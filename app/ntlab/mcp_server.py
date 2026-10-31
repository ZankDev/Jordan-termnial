"""NT LAB MCP server (Model Context Protocol over stdio, JSON-RPC 2.0, no SDK needed).

Lets an agent drive the backtest lab as tools:
  nt_status          NinjaTrader installed? bridge up? data connections? NinjaScript installed? current search
  nt_install         copy NostroLab + NostroBridge into NinjaTrader's Custom folder (then compile once with F5)
  nt_search_start    start the search loop in the background (provider local | ninjatrader)
  nt_search_status   progress log of the running search
  nt_leaderboard     best candidates of a run (or all runs), champions first
  nt_candidate       one candidate: spec, plain-English rules, IS/OOS/folds/holdout/Apex metrics, NinjaTrader params
  nt_backtest        backtest one spec (or a stored candidate) now and return its metrics
  nt_bars            historical bars straight from NinjaTrader's data provider (through the bridge)
  nt_compare         the same candidate on NinjaTrader vs the reference engine, trade by trade
Registered for Claude Code in the repo's .mcp.json. Logs go to stderr; stdout carries only protocol messages.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.ntlab import metrics, providers, search, spec as sp, store  # noqa: E402

PROTOCOL = "2024-11-05"


def _log(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ tools
def t_status(_: dict) -> dict:
    return {"ninjatrader": providers.NinjaTraderProvider.status(), "local": providers.LocalProvider.status(), "search": search.status(),
            "gates": metrics.GATES}


def t_install(_: dict) -> dict:
    nt = providers.nt_user_dir()
    if not (nt / "bin" / "Custom").exists():
        return {"ok": False, "error": f"NinjaTrader 8 user folder not found at {nt}. Install NinjaTrader 8 and start it once."}
    copied = []
    for sub, name in (("Strategies", "NostroLab.cs"), ("AddOns", "NostroBridge.cs")):
        dst = nt / "bin" / "Custom" / sub
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "ninjatrader" / sub / name, dst / name)
        copied.append(str(dst / name))
    (nt / "nostro" / "jobs").mkdir(parents=True, exist_ok=True)
    (nt / "nostro" / "results").mkdir(parents=True, exist_ok=True)
    return {"ok": True, "copied": copied, "next": "In NinjaTrader: New > NinjaScript Editor, press F5 to compile (once). The bridge starts with NinjaTrader; restart NinjaTrader once after the first compile."}


def t_search_start(a: dict) -> dict:
    cur = search.status()
    if cur.get("state") == "running" and cur.get("pid") and _alive(cur["pid"]):
        return {"ok": False, "error": f"a search is already running (run #{cur.get('run_id')})", "status": cur}
    args = [sys.executable, "-m", "app.ntlab.search", "--provider", a.get("provider", "local"), "--instrument", a.get("instrument", "ES"),
            "--generations", str(a.get("generations", 8)), "--population", str(a.get("population", 120)),
            "--target-champions", str(a.get("target_champions", 3)), "--start", a.get("start", "2020-05-01"), "--end", a.get("end", "2026-10-20"),
            "--holdout-from", a.get("holdout_from", "2025-10-01"), "--commission", str(a.get("commission_rt", 4.5)), "--slippage", str(a.get("slippage_ticks", 1))]
    if a.get("seed") is not None:
        args += ["--seed", str(a["seed"])]
    if a.get("ml") is False:
        args += ["--no-ml"]
    log = ROOT / "data_cache" / "ntlab" / "search.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(args, cwd=ROOT, stdout=open(log, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
                         creationflags=0x00004000 | 0x08000000 if os.name == "nt" else 0)  # below-normal priority, no window
    search._status(state="running", pid=p.pid, started_args=args[3:], log=[])
    return {"ok": True, "pid": p.pid, "log": str(log), "note": "poll nt_search_status; with provider=ninjatrader each generation waits for a Strategy Analyzer run (instructions in the log)"}


def _alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=10).stdout
        return str(pid) in out
    except Exception:  # noqa: BLE001
        return False


def t_search_status(_: dict) -> dict:
    s = search.status()
    if s.get("pid"):
        s["alive"] = _alive(s["pid"])
    import time as _t
    if s.get("state") == "running" and s.get("updated") and _t.time() - s["updated"] > 1800 and s.get("provider") != "ninjatrader":
        s["state"] = "stalled"   # no progress for 30 min on the local engine (NinjaTrader batches legitimately wait for a click)
        s["stalled_minutes"] = round((_t.time() - s["updated"]) / 60)
    return s


def t_leaderboard(a: dict) -> dict:
    rows = store.leaderboard(a.get("run_id"), int(a.get("n", 15)), bool(a.get("champions_only", False)))
    return {"runs": store.runs(5), "rows": [{"run_id": r["run_id"], "spec_id": r["spec_id"], "champion": bool(r["champion"]), "score": r["score"],
                                             "stability": r["stability"], "rules": sp.describe(r["spec"]), "is": r["metrics"]["is"], "oos": r["metrics"]["oos"],
                                             "folds": r["metrics"]["folds"], "holdout": r["holdout"], "apex": r["metrics"]["apex"], "why_not": r["why_not"]} for r in rows]}


def t_candidate(a: dict) -> dict:
    c = store.candidate(int(a["run_id"]), a["spec_id"])
    if not c:
        return {"error": "not found"}
    c["rules"] = sp.describe(c["spec"])
    c["ninjatrader_params"] = sp.to_ninjatrader_params(c["spec"])
    c["metrics"]["equity"] = c["metrics"].get("equity", [])[-120:]
    return c


def t_backtest(a: dict) -> dict:
    if a.get("spec_id") and a.get("run_id"):
        s = store.candidate(int(a["run_id"]), a["spec_id"])["spec"]
    else:
        s = sp.finalize(a["spec"])
    start, end, hold = a.get("start", "2020-05-01"), a.get("end", "2026-10-20"), a.get("holdout_from", "2025-10-01")
    prov = providers.make(a.get("provider", "local"), a.get("instrument", "ES"))
    try:
        res = prov.run_batch(providers.new_batch_id("single_"), [s], {"start": start, "end": end, "commission_rt": a.get("commission_rt", 4.5),
                                                                       "slippage_ticks": a.get("slippage_ticks", 1)}, progress=_log)
    finally:
        if hasattr(prov, "close"):
            prov.close()
    trades = res.get(0, [])
    m = metrics.evaluate([t for t in trades if t["et"] < hold], start, hold)
    m["equity"] = m["equity"][-60:]
    ok, why = metrics.is_champion(m)
    return {"rules": sp.describe(s), "spec": s, "metrics": m, "holdout": metrics._stats(metrics.frame([t for t in trades if t["et"] >= hold])),
            "passes_gates_without_stability": ok, "why_not": why}


def t_bars(a: dict) -> dict:
    import requests
    q = {"instrument": a.get("instrument", "ES ##-##"), "type": a.get("type", "Minute"), "value": a.get("value", 5),
         "from": a.get("from"), "to": a.get("to")}
    r = requests.get(f"{providers.BRIDGE}/bars", params={k: v for k, v in q.items() if v is not None}, timeout=180)
    d = r.json()
    if "bars" in d:
        d["n"] = len(d["bars"]); d["first"] = d["bars"][:3]; d["last"] = d["bars"][-3:]; del d["bars"]
    return d


def t_compare(a: dict) -> dict:
    """NinjaTrader's trades for a candidate (from its NT batch result file) vs the reference engine on the same dates."""
    c = store.candidate(int(a["run_id"]), a["spec_id"])
    if not c:
        return {"error": "not found"}
    folder = providers.nt_user_dir() / "nostro" / "results" / c["batch"]
    nt = None
    if folder.exists():
        for f in folder.glob("*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("spec_id") == c["spec_id"]:
                nt = d
    if not nt:
        return {"error": "no NinjaTrader result for this candidate (it was run on the local provider)"}
    ref = t_backtest({"run_id": a["run_id"], "spec_id": a["spec_id"], "provider": "local", "start": nt["first"][:10], "end": nt["last"][:10]})
    ntm = metrics._stats(metrics.frame(nt["trades"]))
    return {"ninjatrader": ntm, "reference": ref["metrics"]["all"], "nt_trades": len(nt["trades"]),
            "note": "differences come from data (NinjaTrader futures vs Dukascopy index CFD) and fill details; NinjaTrader is the truth"}


def t_ml_filter(a: dict) -> dict:
    """Meta-label one candidate: walk-forward LightGBM decides which of its trades to take (Jansen ch. 7)."""
    from app.ntlab.ml import meta
    c = store.candidate(int(a["run_id"]), a["spec_id"])
    if not c:
        return {"error": "not found"}
    runs = {r["id"]: r for r in store.runs(200)}
    run = runs.get(int(a["run_id"]), {})
    prov = providers.make(run.get("provider", "local"), run.get("instrument", "ES"))
    trades = providers.load_trades(prov, c["batch"], c["spec_id"])
    if not trades:
        return {"error": "no stored trades for this candidate"}
    r = meta.filter_trades(trades, run.get("instrument", "ES"), run.get("start", "2020-05-01"), run.get("holdout_from", "2025-10-01"), run.get("end"))
    r.pop("taken_trades", None)
    return {"rules": sp.describe(c["spec"]), **r}


def t_validate(a: dict) -> dict:
    """Send stored candidates to NinjaTrader as one batch (non-blocking): writes jobs\\<batch>.json and returns the exact
    Strategy Analyzer settings. Default candidates: every champion + the top of each finished run."""
    picks: list[dict] = []
    if a.get("candidates"):
        for c in a["candidates"]:
            r = store.candidate(int(c["run_id"]), c["spec_id"])
            if r:
                picks.append(r)
    else:
        for run in store.runs(20):
            board = store.leaderboard(run["id"], n=int(a.get("per_run", 8)))
            picks += [store.candidate(run["id"], r["spec_id"]) for r in board if r["champion"]] + [store.candidate(run["id"], r["spec_id"]) for r in board[:int(a.get("per_run", 8))]]
    run_inst = {r["id"]: r["instrument"] for r in store.runs(500)}
    groups: dict[str, dict[str, dict]] = {}
    for p in picks:
        if p:
            sym = a.get("instrument") or run_inst.get(p["run_id"], "ES")
            groups.setdefault(sym, {})[p["spec_id"]] = p["spec"]   # one entry per strategy
    out = {}
    for sym, specs in sorted(groups.items()):
        group = list(specs.values())
        prov = providers.NinjaTraderProvider({"ES": "ES ##-##", "NQ": "NQ ##-##"}.get(sym, sym))
        batch = providers.new_batch_id(f"val{sym}_")
        cfg = {"start": a.get("start", "2020-05-01"), "end": a.get("end", "2026-10-30"), "commission_rt": 4.5, "slippage_ticks": 1, "instrument": sym}
        providers._write_jobs(prov.folder, batch, group, {**cfg, "nt_instrument": prov.nt_instrument, "period_min": prov.period_min})
        store.save_batch(batch, 0, 0, len(group), "ninjatrader", "waiting")
        out[sym] = {"batch": batch, "n": len(group), "jobs": str(prov.folder / "jobs" / f"{batch}.json"), "instructions": prov.instructions(batch, len(group), cfg)}
    return {"batches": out, "next": "Run each batch in Strategy Analyzer as shown, then call nt_collect with the batch id."}


def t_collect(a: dict) -> dict:
    """Read a NinjaTrader batch's results and score every strategy (IS / OOS / holdout) — NinjaTrader's real fills."""
    batch, folder = a["batch"], providers.nt_user_dir() / "nostro"
    job = json.loads((folder / "jobs" / f"{batch}.json").read_text(encoding="utf-8"))
    res = providers._collect(folder, batch, len(job["specs"]))
    hold = a.get("holdout_from", "2025-10-01")
    rows = []
    for i, s in enumerate(job["specs"]):
        tr = res.get(i)
        if tr is None:
            rows.append({"index": i, "spec_id": s["id"], "status": "not run yet"}); continue
        m = metrics.evaluate([t for t in tr if t["et"] < hold], job["cfg"]["start"], hold)
        h = metrics._stats(metrics.frame([t for t in tr if t["et"] >= hold]))
        ok, why = metrics.is_champion(m)
        rows.append({"index": i, "spec_id": s["id"], "rules": sp.describe(s), "trades": len(tr), "is": m["is"], "oos": m["oos"], "folds": m["folds"],
                     "holdout": h, "apex": m["apex"], "passes_gates": ok and h.get("net", 0) > 0 and h.get("pf", 0) >= metrics.GATES["holdout_pf"], "why_not": why})
    done = sum(1 for r in rows if r.get("trades") is not None)
    return {"batch": batch, "done": f"{done}/{len(rows)}", "rows": rows}


def t_leak_check(a: dict) -> dict:
    from app.ntlab.ml import features
    return features.leak_check(a.get("instrument", "ES"))


TOOLS: dict[str, tuple[Callable[[dict], dict], str, dict]] = {
    "nt_status": (t_status, "NinjaTrader / bridge / NinjaScript install status, the local engine, and the current search.", {"type": "object", "properties": {}}),
    "nt_install": (t_install, "Copy the NostroLab strategy and NostroBridge add-on into NinjaTrader 8's Custom folder.", {"type": "object", "properties": {}}),
    "nt_search_start": (t_search_start, "Start the strategy search loop in the background.", {"type": "object", "properties": {
        "provider": {"type": "string", "enum": ["local", "ninjatrader"]}, "instrument": {"type": "string", "description": "ES or NQ"},
        "generations": {"type": "integer"}, "population": {"type": "integer"}, "target_champions": {"type": "integer"},
        "start": {"type": "string"}, "end": {"type": "string"}, "holdout_from": {"type": "string"}, "commission_rt": {"type": "number"},
        "slippage_ticks": {"type": "number"}, "seed": {"type": "integer"}, "ml": {"type": "boolean", "description": "ML-guided search + meta-labeling (default true)"}}}),
    "nt_ml_filter": (t_ml_filter, "Meta-labeling: train a walk-forward LightGBM filter on a candidate's trades; report taken vs skipped, holdout, AUC, top features.", {"type": "object", "properties": {
        "run_id": {"type": "integer"}, "spec_id": {"type": "string"}}, "required": ["run_id", "spec_id"]}),
    "nt_validate": (t_validate, "Send stored candidates (default: champions + top of each run) to NinjaTrader as a batch; returns Strategy Analyzer settings.", {"type": "object", "properties": {
        "candidates": {"type": "array", "items": {"type": "object"}}, "per_run": {"type": "integer"}, "instrument": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}}),
    "nt_collect": (t_collect, "Score a NinjaTrader batch's results (real fills): IS / OOS / walk-forward / holdout / Apex per strategy.", {"type": "object", "properties": {
        "batch": {"type": "string"}, "holdout_from": {"type": "string"}}, "required": ["batch"]}),
    "nt_leak_check": (t_leak_check, "Look-ahead guard: verify no ML feature uses future data.", {"type": "object", "properties": {"instrument": {"type": "string"}}}),
    "nt_search_status": (t_search_status, "Progress of the running search (log, generation, champions).", {"type": "object", "properties": {}}),
    "nt_leaderboard": (t_leaderboard, "Best candidates (champions first) with IS/OOS/fold/holdout/Apex metrics.", {"type": "object", "properties": {
        "run_id": {"type": "integer"}, "n": {"type": "integer"}, "champions_only": {"type": "boolean"}}}),
    "nt_candidate": (t_candidate, "Full detail of one candidate, including NostroLab parameters to run it in NinjaTrader.", {"type": "object", "properties": {
        "run_id": {"type": "integer"}, "spec_id": {"type": "string"}}, "required": ["run_id", "spec_id"]}),
    "nt_backtest": (t_backtest, "Backtest one spec (JSON per ninjatrader/SPEC.md) or a stored candidate now.", {"type": "object", "properties": {
        "spec": {"type": "object"}, "run_id": {"type": "integer"}, "spec_id": {"type": "string"}, "provider": {"type": "string", "enum": ["local", "ninjatrader"]},
        "instrument": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "holdout_from": {"type": "string"}}}),
    "nt_bars": (t_bars, "Historical bars from NinjaTrader's connected data provider (via NostroBridge).", {"type": "object", "properties": {
        "instrument": {"type": "string"}, "type": {"type": "string"}, "value": {"type": "integer"}, "from": {"type": "string"}, "to": {"type": "string"}}}),
    "nt_compare": (t_compare, "Compare a candidate's NinjaTrader trades with the reference engine.", {"type": "object", "properties": {
        "run_id": {"type": "integer"}, "spec_id": {"type": "string"}}, "required": ["run_id", "spec_id"]}),
}


# ------------------------------------------------------------------ protocol
def _reply(mid: Any, result: Any = None, error: dict | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, default=str) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        _reply(mid, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                     "serverInfo": {"name": "ntlab", "version": "1.0.0"},
                     "instructions": "NT LAB: search for robust futures strategies with NinjaTrader 8 as the backtest engine. Start with nt_status."})
    elif method == "tools/list":
        _reply(mid, {"tools": [{"name": k, "description": d, "inputSchema": s} for k, (_f, d, s) in TOOLS.items()]})
    elif method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            _reply(mid, error={"code": -32602, "message": f"unknown tool {name}"})
            return
        try:
            out = TOOLS[name][0](args)
            _reply(mid, {"content": [{"type": "text", "text": json.dumps(out, default=str, indent=1)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            _reply(mid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
    elif method == "ping":
        _reply(mid, {})
    elif mid is not None:
        _reply(mid, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    _log("ntlab MCP server ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception:  # noqa: BLE001
            _log(traceback.format_exc())


if __name__ == "__main__":
    main()
