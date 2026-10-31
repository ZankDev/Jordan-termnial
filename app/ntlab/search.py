"""The NT LAB search loop: generate -> backtest (provider) -> score -> keep the robust -> breed -> repeat.

Per generation ONE batch goes to the provider (with NinjaTrader that is one Strategy Analyzer Optimize run):
  * generation 0: `population` random strategies, spread evenly over the six families
  * later: mutations of the elites (60%), crossovers (15%), fresh random ones (25%, so the search never collapses)
  * plus 6 close NEIGHBOURS of every new finalist: if small parameter changes break it, it was curve-fit (stability)
Each batch covers the whole period, but only [start, holdout_from) is ever scored. The holdout (the most recent
months) is looked at for finalists only, after the fact — a true forward test the search never optimised on.
A finalist becomes a CHAMPION when it passes every gate in metrics.GATES (out-of-sample PF/Sharpe, 3+/4 walk-forward
folds, monthly consistency, drawdown, neighbour stability) AND makes money in the holdout.
The loop stops at `target_champions` champions or after `generations`.

Run:  python -m app.ntlab.search --provider local --instrument ES --generations 8 --population 120
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from app.ntlab import metrics, providers, spec as sp, store

STATUS = Path(__file__).resolve().parents[2] / "data_cache" / "ntlab" / "status.json"


def _status(**kw: Any) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cur = {}
    cur.update(kw, updated=time.time())
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur), encoding="utf-8")
    tmp.replace(STATUS)


def status() -> dict[str, Any]:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"state": "idle"}


def _dsr(f: dict[str, Any], board: list[dict[str, Any]], n_trials: int) -> dict[str, Any]:
    """Deflated Sharpe of finalist f; the luck benchmark uses the spread of Sharpe ratios over everything tried."""
    """Variance of Sharpe over tries with enough trades to mean something (tiny samples have wild Sharpes and would inflate
    the luck benchmark); number of tries = EFFECTIVE independent tries (near-copies are correlated, López de Prado)."""
    import numpy as np
    from app.ntlab.ml import dsr
    ok = [r for r in board if r["metrics"]["is"]["n"] + r["metrics"]["oos"]["n"] >= metrics.GATES["min_trades_is"] + metrics.GATES["min_trades_oos"]] or board
    srs = np.array([r["metrics"]["all"]["sharpe"] for r in ok]) / np.sqrt(252)
    full = [store.candidate(r["run_id"], r["spec_id"]) for r in ok[:150]]
    n_eff, rho = dsr.effective_trials([dsr.daily_frame(c["metrics"].get("equity", [])) for c in full if c], max(2, n_trials))
    out = dsr.deflated_sharpe(dsr.daily_series(f["metrics"].get("equity", [])), max(2, int(round(n_eff))), float(srs.var()) if len(srs) > 2 else 0.0)
    out["n_eff"], out["rho"] = round(n_eff, 1), round(rho, 3)
    return out


ML_GATES = {"auc": 0.55, "wf_pf": 1.3, "wf_n": 40, "hold_n": 10}


def _meta(prov: Any, f: dict[str, Any], instrument: str, start: str, holdout_from: str, end: str) -> dict[str, Any] | None:
    """Meta-label a finalist's own trades (walk-forward LightGBM). It is an ML champion only if the filter is predictive
    (AUC), the trades it takes are clearly profitable walk-forward, AND they make money in the never-searched holdout."""
    from app.ntlab.ml import meta
    trades = providers.load_trades(prov, f["batch"], f["spec_id"])
    if not trades:
        return None
    try:
        r = meta.filter_trades(trades, instrument, start, holdout_from, end)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "verdict": f"error {type(e).__name__}"}
    if not r.get("ok"):
        return {"ok": False, "verdict": r.get("reason")}
    wt, ht = r["walk_forward"]["taken"], r["holdout"]["taken"]
    wa, ha = r["walk_forward"]["all"], r["holdout"]["all"]
    fails = []
    if (r["auc_wf"] or 0) < ML_GATES["auc"]: fails.append(f"AUC {r['auc_wf']} < {ML_GATES['auc']}")
    if wt["pf"] < ML_GATES["wf_pf"] or wt["n"] < ML_GATES["wf_n"]: fails.append(f"taken trades PF {wt['pf']} n={wt['n']}")
    if ht["n"] < ML_GATES["hold_n"] or ht["net"] <= 0: fails.append(f"holdout taken {ht['n']} trades ${ht['net']:,.0f}")
    # the filter must ADD something: clearly better than taking every trade, walk-forward AND in the holdout
    if wt["pf"] < wa["pf"] + 0.15: fails.append(f"no lift walk-forward (taken PF {wt['pf']} vs all {wa['pf']})")
    if ha["n"] and ht["pf"] < ha["pf"]: fails.append(f"no lift in holdout (taken PF {ht['pf']} vs all {ha['pf']})")
    r.pop("taken_trades", None)
    r["champion"] = not fails
    r["verdict"] = "passed" if not fails else "; ".join(fails)
    return r


def _diverse_elites(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Top by score, but no family takes more than 40% of the elite slots."""
    cap, per, out = max(2, int(k * 0.4)), {}, []
    for r in rows:
        f = r["spec"]["family"]
        if per.get(f, 0) < cap:
            out.append(r); per[f] = per.get(f, 0) + 1
        if len(out) >= k:
            break
    return out


def run(provider: str = "local", instrument: str = "ES", start: str = "2020-05-01", end: str = "2026-10-20", holdout_from: str = "2025-10-01",
        generations: int = 8, population: int = 120, elite: int = 16, target_champions: int = 3, seed: int | None = None,
        commission_rt: float = 4.5, slippage_ticks: float = 1.0, nt_instrument: str | None = None, ml: bool = True) -> dict[str, Any]:
    rng = random.Random(seed if seed is not None else int(time.time()))
    if ml:
        from app.ntlab.ml import features
        lc = features.leak_check(instrument)
        if not lc["ok"]:
            raise RuntimeError(f"ML features use future data — refusing to search: {lc['leaking']}")
    prov = providers.make(provider, instrument, nt_instrument)
    cfg = {"start": start, "end": end, "commission_rt": commission_rt, "slippage_ticks": slippage_ticks, "instrument": instrument}
    run_id = store.new_run(provider, instrument, start, end, holdout_from,
                           {**cfg, "holdout_from": holdout_from, "generations": generations, "population": population, "elite": elite, "target_champions": target_champions})
    log: list[str] = []

    def say(msg: str) -> None:
        log.append(time.strftime("%H:%M:%S ") + msg)
        print(msg, flush=True)
        _status(state="running", run_id=run_id, log=log[-30:])

    say(f"run #{run_id}: {provider} · {instrument} · {start}..{holdout_from} searched, {holdout_from}..{end} held out")
    seen = set()
    pool = [sp.random_spec(rng, sp.FAMILIES[i % len(sp.FAMILIES)]) for i in range(population)]
    for s in pool:
        s["role"] = "explore"
    champions: list[dict[str, Any]] = []
    tested_stability: set[str] = set()
    neighbours: dict[str, list[str]] = {}   # finalist id -> neighbour ids
    try:
        for gen in range(generations):
            batch_specs = [s for s in pool if s["id"] not in seen]
            if not batch_specs:
                break
            batch = providers.new_batch_id(f"r{run_id}g{gen}_")
            store.save_batch(batch, run_id, gen, len(batch_specs), provider, "running")
            store.update_run(run_id, generation=gen)
            _status(state="running", run_id=run_id, generation=gen, batch=batch, batch_size=len(batch_specs), champions=len(champions))
            say(f"generation {gen}: {len(batch_specs)} backtests -> {provider}")
            res = prov.run_batch(batch, batch_specs, cfg, progress=say)
            store.save_batch(batch, run_id, gen, len(batch_specs), provider, "done")
            for i, s in enumerate(batch_specs):
                seen.add(s["id"])
                trades = res.get(i)
                if trades is None:
                    continue
                m = metrics.evaluate([t for t in trades if t["et"] < holdout_from], start, holdout_from)
                hold = metrics._stats(metrics.frame([t for t in trades if t["et"] >= holdout_from]))
                store.save_candidate(run_id, gen, batch, s, s.get("role", "explore"), m, hold)
            board = store.leaderboard(run_id, n=400)
            # stability of finalists whose neighbours are now all evaluated
            by_id = {r["spec_id"]: r for r in board}
            for fid, nids in list(neighbours.items()):
                if fid in tested_stability or not all(n in seen for n in nids):
                    continue
                tested_stability.add(fid)
                rows = [by_id[n] for n in nids if n in by_id]
                stab = sum(1 for r in rows if r["metrics"]["oos"]["net"] > 0 and r["metrics"]["oos"]["pf"] > 1.0) / max(1, len(rows))
                f = store.candidate(run_id, fid)
                if not f:
                    continue
                ok, why = metrics.is_champion(f["metrics"], stab)
                hold = f["holdout"] or {}
                if ok and not (hold.get("net", 0) > 0 and hold.get("n", 0) >= 5):
                    ok, why = False, [f"holdout (never searched) {hold.get('n', 0)} trades, net ${hold.get('net', 0):,.0f}"]
                # the holdout must hold for the FAMILY, not just this member: with many relatives tried, one of them
                # will look fine in the holdout by luck — its close neighbours must mostly make money there too
                hstab = sum(1 for r in rows if (r.get("holdout") or {}).get("net", 0) > 0) / max(1, len(rows))
                if hstab < metrics.GATES["holdout_stability"]:
                    ok = False
                    why.append(f"only {hstab:.0%} of its neighbours profitable in the holdout (family luck)")
                if hold.get("n", 0) >= 5 and hold.get("pf", 0) < metrics.GATES["holdout_pf"]:
                    ok = False
                    why.append(f"holdout profit factor {hold.get('pf')} < {metrics.GATES['holdout_pf']}")
                # Deflated Sharpe: is it better than the best of N lucky tries? (N = everything tested in this run)
                d = _dsr(f, board, len(seen))
                if d["dsr"] < metrics.GATES["dsr"]:
                    why.append(f"Deflated Sharpe {d['dsr']:.2f} < {metrics.GATES['dsr']} (Sharpe {d['sr']} vs {d['sr0']} expected from luck over {len(seen)} tries)")
                    ok = False
                ml_res = None
                if ml:   # meta-labeling: can a model trained walk-forward pick which of its trades to take?
                    ml_res = _meta(prov, f, instrument, start, holdout_from, end)
                store.set_verdict(run_id, fid, stab, ok, why, dsr=d["dsr"], ml=ml_res)
                if ok:
                    champions.append(f)
                    say(f"CHAMPION {fid}: {sp.describe(f['spec'])} | OOS PF {f['metrics']['oos']['pf']} Sharpe {f['metrics']['oos']['sharpe']} | holdout ${hold.get('net', 0):,.0f} | stability {stab:.0%} | DSR {d['dsr']:.2f}")
                elif ml_res and ml_res.get("champion"):
                    champions.append(f)
                    store.set_ml_champion(run_id, fid)
                    say(f"ML CHAMPION {fid}: {sp.describe(f['spec'])} + LightGBM filter | walk-forward taken PF {ml_res['walk_forward']['taken']['pf']} | holdout taken ${ml_res['holdout']['taken']['net']:,.0f} | AUC {ml_res['auc_wf']}")
                else:
                    say(f"finalist {fid} rejected: {'; '.join(why[:3])}" + (f" | ML filter: {ml_res.get('verdict')}" if ml_res else ""))
            best = board[0] if board else None
            if best:
                bm = best["metrics"]
                say(f"gen {gen} best: score {best['score']} · {sp.describe(best['spec'])} · OOS PF {bm['oos']['pf']} Sharpe {bm['oos']['sharpe']} n={bm['oos']['n']}")
            if len(champions) >= target_champions:
                say(f"{len(champions)} champions found — stopping")
                break
            # ---- breed the next generation
            elites = _diverse_elites([r for r in board if r["role"] != "neighbour"], elite)
            nxt: list[dict[str, Any]] = []
            for r in elites[:5]:   # new finalists get their neighbourhood tested
                fid = r["spec_id"]
                if r["score"] > 0 and fid not in neighbours:
                    nb = [sp.mutate(r["spec"], rng, 0.1) for _ in range(6)]
                    for x in nb:
                        x["role"] = "neighbour"
                    neighbours[fid] = [x["id"] for x in nb]
                    nxt += nb
            if ml:   # ML-guided: a surrogate trained on every backtest so far picks 40% of the next batch
                from app.ntlab.ml import surrogate
                every = store.leaderboard(run_id, n=20000)
                picks = surrogate.propose([(r["spec"], r["score"]) for r in every if r["role"] != "neighbour"], int(population * 0.4), rng, seen)
                if picks:
                    say(f"surrogate proposed {len(picks)} (predicted score up to {max(p['pred'] for p in picks):.2f})")
                nxt += picks
            n_mut, n_cross = int(population * (0.35 if ml else 0.6)), int(population * 0.15)
            for _ in range(n_mut):
                x = sp.mutate(rng.choice(elites)["spec"], rng, rng.choice([0.1, 0.2, 0.35])); x["role"] = "mutant"; nxt.append(x)
            for _ in range(n_cross):
                a, b = rng.sample(elites, 2) if len(elites) > 1 else (elites[0], elites[0])
                x = sp.crossover(a["spec"], b["spec"], rng); x["role"] = "cross"; nxt.append(x)
            while len(nxt) < population + 6 * 5:
                x = sp.random_spec(rng); x["role"] = "explore"; nxt.append(x)
            uniq: dict[str, dict[str, Any]] = {}
            for x in nxt:
                if x["id"] not in seen:
                    uniq[x["id"]] = x
            pool = list(uniq.values())
        store.update_run(run_id, status="done", note=f"{len(champions)} champions")
        _status(state="done", run_id=run_id, champions=len(champions), log=log[-30:])
    except Exception as e:  # noqa: BLE001
        store.update_run(run_id, status="error", note=str(e)[:300])
        _status(state="error", run_id=run_id, error=str(e)[:300], log=log[-30:])
        raise
    finally:
        if hasattr(prov, "close"):
            prov.close()
    return {"run_id": run_id, "champions": [{"spec_id": c["spec_id"], "describe": sp.describe(c["spec"])} for c in champions]}


def main() -> None:
    ap = argparse.ArgumentParser(description="NT LAB strategy search")
    ap.add_argument("--provider", default="local", choices=["local", "ninjatrader"])
    ap.add_argument("--instrument", default="ES")
    ap.add_argument("--nt-instrument", default=None)
    ap.add_argument("--start", default="2020-05-01")
    ap.add_argument("--end", default="2026-10-20")
    ap.add_argument("--holdout-from", default="2025-10-01")
    ap.add_argument("--generations", type=int, default=8)
    ap.add_argument("--population", type=int, default=120)
    ap.add_argument("--target-champions", type=int, default=3)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--commission", type=float, default=4.5)
    ap.add_argument("--slippage", type=float, default=1.0)
    ap.add_argument("--no-ml", action="store_true", help="plain genetic search (no surrogate, meta-labeling or leak check)")
    a = ap.parse_args()
    out = run(a.provider, a.instrument, a.start, a.end, a.holdout_from, a.generations, a.population, target_champions=a.target_champions,
              seed=a.seed, commission_rt=a.commission, slippage_ticks=a.slippage, nt_instrument=a.nt_instrument, ml=not a.no_ml)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
