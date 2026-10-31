"""Backtest providers. Both take a batch of specs and return {index: trades} in the same result-file format.

NinjaTraderProvider — the real one. Writes <Documents>\\NinjaTrader 8\\nostro\\jobs\\<batch>.json, then waits for
NinjaTrader's Strategy Analyzer to run NostroLab over JobIndex 0..N-1 (one Optimize run = the whole batch, on
NinjaTrader's data and fill engine, all cores) and collects results\\<batch>\\<i>.json as they land. `instructions()`
says exactly what to set. The NostroBridge add-on (127.0.0.1:8099) reports whether NinjaTrader is up and connected.

LocalProvider — the reference engine (app/ntlab/engine.py) on local Dukascopy data, in parallel; the same files are
written under data_cache/ntlab/results, so everything downstream is identical. For testing the loop and cross-checks.
"""
from __future__ import annotations

import concurrent.futures as cf
import ctypes
import json
import os
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

import requests

BRIDGE = "http://127.0.0.1:8099"


def documents_dir() -> Path:
    """The real Documents folder (it may be redirected to OneDrive)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (wintypes.BYTE * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(p)) == 0:
            out = Path(p.value)
            ctypes.windll.ole32.CoTaskMemFree(p)
            return out
    except Exception:  # noqa: BLE001
        pass
    return Path(os.path.expanduser("~")) / "Documents"


def nt_user_dir() -> Path:
    return documents_dir() / "NinjaTrader 8"


def new_batch_id(prefix: str = "b") -> str:
    return f"{prefix}{time.strftime('%m%d%H%M%S')}{uuid.uuid4().hex[:4]}"


def _write_jobs(folder: Path, batch: str, specs: list[dict[str, Any]], cfg: dict[str, Any]) -> Path:
    jobs = folder / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    path = jobs / f"{batch}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"batch": batch, "cfg": cfg, "specs": [{k: v for k, v in s.items() if k not in ("gen", "parent")} for s in specs]}), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _collect(folder: Path, batch: str, n: int) -> dict[int, list[dict[str, Any]]]:
    d = folder / "results" / batch
    out: dict[int, list[dict[str, Any]]] = {}
    if d.exists():
        for f in d.glob("*.json"):
            try:
                out[int(f.stem)] = json.loads(f.read_text(encoding="utf-8"))["trades"]
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
    return out


class NinjaTraderProvider:
    name = "ninjatrader"

    def __init__(self, nt_instrument: str = "ES ##-##", period_min: int = 5, wait_hours: float = 12):
        self.folder = nt_user_dir() / "nostro"
        self.nt_instrument, self.period_min, self.wait = nt_instrument, period_min, wait_hours * 3600

    @staticmethod
    def status() -> dict[str, Any]:
        installed = (nt_user_dir() / "bin" / "Custom").exists()  # created by NinjaTrader itself (our jobs folder does not count)
        try:
            ping = requests.get(f"{BRIDGE}/ping", timeout=3).json()
        except Exception:  # noqa: BLE001
            ping = None
        return {"installed": installed, "user_dir": str(nt_user_dir()), "bridge": bool(ping), "ping": ping,
                "strategy_installed": (nt_user_dir() / "bin" / "Custom" / "Strategies" / "NostroLab.cs").exists(),
                "addon_installed": (nt_user_dir() / "bin" / "Custom" / "AddOns" / "NostroBridge.cs").exists()}

    def instructions(self, batch: str, n: int, cfg: dict[str, Any]) -> str:
        return (f"NinjaTrader > New > Strategy Analyzer:\n"
                f"  Strategy: NostroLab   Instrument: {self.nt_instrument}   Type: Minute, Value: {self.period_min}\n"
                f"  Trading hours: CME US Index Futures ETH   Start: {cfg['start']}   End: {cfg['end']}\n"
                f"  Backtest type: Optimization   Optimizer: Default   Keep best # of results: 10\n"
                f"  BatchId = {batch}    JobIndex: tick 'optimize', Min 0, Max {n - 1}, Increment 1\n"
                f"  Commission: your template (e.g. NinjaTrader Brokerage Lifetime)   Slippage: {cfg.get('slippage_ticks', 1)}\n"
                f"  Order fill resolution: Standard   then Run. Results land in {self.folder / 'results' / batch}")

    def run_batch(self, batch: str, specs: list[dict[str, Any]], cfg: dict[str, Any], progress: Callable[[str], None] = print) -> dict[int, list[dict[str, Any]]]:
        _write_jobs(self.folder, batch, specs, {**cfg, "nt_instrument": self.nt_instrument, "period_min": self.period_min})
        progress("WAITING FOR NINJATRADER — " + self.instructions(batch, len(specs), cfg))
        t0, last = time.time(), -1
        while time.time() - t0 < self.wait:
            got = _collect(self.folder, batch, len(specs))
            if len(got) != last:
                last = len(got)
                progress(f"ninjatrader: {len(got)}/{len(specs)} backtests done")
            if len(got) >= len(specs):
                return got
            time.sleep(5)
        return _collect(self.folder, batch, len(specs))


def _local_one(args: tuple) -> tuple[int, list[dict[str, Any]]]:
    i, spec, symbol, start, end, comm, slip = args
    from app.ntlab import data, engine
    b = data.window(data.bars(symbol), start, end)
    return i, engine.run(spec, b, comm, slip)


class LocalProvider:
    name = "local"

    def __init__(self, symbol: str = "ES", workers: int | None = None):
        from app.ntlab.data import ROOT
        self.folder = ROOT / "ntlab"
        self.symbol, self.workers = symbol, workers or max(1, (os.cpu_count() or 4) - 2)
        self._pool: cf.ProcessPoolExecutor | None = None

    @staticmethod
    def status() -> dict[str, Any]:
        return {"available": True, "engine": "reference (NostroLab rules in Python) on Dukascopy 5-min"}

    def run_batch(self, batch: str, specs: list[dict[str, Any]], cfg: dict[str, Any], progress: Callable[[str], None] = print) -> dict[int, list[dict[str, Any]]]:
        _write_jobs(self.folder, batch, specs, cfg)
        if self._pool is None:
            self._pool = cf.ProcessPoolExecutor(max_workers=self.workers)
        res_dir = self.folder / "results" / batch
        res_dir.mkdir(parents=True, exist_ok=True)
        out: dict[int, list[dict[str, Any]]] = {}
        jobs = [(i, s, self.symbol, cfg["start"], cfg["end"], cfg.get("commission_rt", 4.5), cfg.get("slippage_ticks", 1)) for i, s in enumerate(specs)]
        for k, (i, trades) in enumerate(self._pool.map(_local_one, jobs, chunksize=2)):
            out[i] = trades
            (res_dir / f"{i}.json").write_text(json.dumps({"batch": batch, "index": i, "spec_id": specs[i]["id"], "engine": "reference", "trades": trades}), encoding="utf-8")
            if (k + 1) % 25 == 0:
                progress(f"local: {k + 1}/{len(specs)} backtests done")
        return out

    def close(self) -> None:
        if self._pool:
            self._pool.shutdown(cancel_futures=True)
            self._pool = None


def load_trades(prov: Any, batch: str, spec_id: str) -> list[dict[str, Any]] | None:
    """A candidate's trades from its batch result files (whichever provider wrote them)."""
    d = prov.folder / "results" / batch
    if not d.exists():
        return None
    for f in d.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("spec_id") == spec_id:
            return r["trades"]
    return None


def make(provider: str, instrument: str = "ES", nt_instrument: str | None = None) -> Any:
    if provider == "ninjatrader":
        return NinjaTraderProvider(nt_instrument or {"ES": "ES ##-##", "NQ": "NQ ##-##"}.get(instrument.upper(), instrument))
    return LocalProvider(instrument)
