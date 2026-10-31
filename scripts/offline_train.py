"""Run the same optimizer/trainer the live server runs 24/7, standalone - no
need to start uvicorn or open the dashboard. Calls the exact same
app.optimizer.trainer_service.run_now() the always-on background loop calls,
so it writes to the same data_cache/app.db the web app reads from: any
champion found here shows up in the Trainer tab next time the server runs,
and vice versa.

Safe to run while the live server is also running (SQLite's own busy-timeout
handles the concurrent writes, see app/db.py) - but running both at once just
means two training cycles competing for the same CPU. This is meant for the
opposite case: keeping training going while the server (and the market
session it trades) is OFF - overnight, on a headless machine, or from
Windows Task Scheduler.

Usage:
    .venv\\Scripts\\python.exe scripts\\offline_train.py                    # one cycle each of NQ, ES
    .venv\\Scripts\\python.exe scripts\\offline_train.py --symbol NQ         # one NQ cycle only
    .venv\\Scripts\\python.exe scripts\\offline_train.py --cycles 0          # run forever, alternating (Ctrl+C to stop)
    .venv\\Scripts\\python.exe scripts\\offline_train.py --cycles 6 --iterations 150
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.optimizer import trainer_service  # noqa: E402


def _run_one_cycle(symbol: str, iterations: int) -> None:
    print(f"[offline_train] training {symbol} ({iterations} iterations/strategy)...", flush=True)
    started = time.time()
    result = asyncio.run(trainer_service.run_now(symbol, iterations))
    elapsed = time.time() - started
    winner = result["results"][0] if result["results"] else None
    if winner:
        tag = " -- NEW CHAMPION" if result["champion_updated"] else ""
        print(
            f"[offline_train] {symbol} done in {elapsed:.0f}s - "
            f"best: {winner['strategy']} (score {winner['score']:.3f}){tag}",
            flush=True,
        )
    else:
        print(f"[offline_train] {symbol} done in {elapsed:.0f}s - no candidate cleared the gates this cycle", flush=True)


def main() -> None:
    db.init_db()  # idempotent; guarantees the schema is current even if the server has not started yet
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbol", choices=[*trainer_service.SYMBOL_CYCLE, "ALL"], default="ALL",
        help="Symbol to train (default: ALL, alternating through the same cycle the live trainer uses)",
    )
    parser.add_argument(
        "--iterations", type=int, default=trainer_service.N_ITERATIONS_PER_STRATEGY,
        help=f"Parameter combinations tried per strategy per cycle (default: {trainer_service.N_ITERATIONS_PER_STRATEGY})",
    )
    parser.add_argument(
        "--cycles", type=int, default=1,
        help="How many training cycles to run (one cycle = one symbol). 0 = run forever until Ctrl+C (default: 1)",
    )
    parser.add_argument(
        "--interval", type=float, default=0.0,
        help="Seconds to sleep between cycles (default: 0, back-to-back - matches the live trainer)",
    )
    args = parser.parse_args()

    symbols = trainer_service.SYMBOL_CYCLE if args.symbol == "ALL" else [args.symbol]

    cycle = 0
    i = 0
    while args.cycles <= 0 or cycle < args.cycles:
        symbol = symbols[i % len(symbols)]
        i += 1
        try:
            _run_one_cycle(symbol, args.iterations)
        except Exception as e:
            print(f"[offline_train] {symbol} cycle failed: {e}", flush=True)
        cycle += 1
        if (args.cycles <= 0 or cycle < args.cycles) and args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
