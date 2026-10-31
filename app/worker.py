"""BACKGROUND WORKER — the CPU-heavy loops in their own process, so the web server stays fast.

Runs: the futures trainer (app.optimizer.trainer_service.scheduler_loop), the Polymarket trainer + history backfill
(app.polymarket.trainer_service.scheduler_loop / backfill_loop). Before this they ran as threads inside the server and,
being pure-Python CPU work, held the GIL most of the time: every page request waited behind them (a 401 took 0.3 s,
the HOME numbers ~12 s).

Started and stopped by the server (app/main.py) with BELOW_NORMAL priority on Windows. Both processes use the same
SQLite database (WAL mode), so champions, promotions and Polymarket state written here are read by the server as before.
The futures trainer's in-memory stage is mirrored to data_cache/worker_state.json every 2 s for /api/trainer/status;
the Polymarket trainer already persists its state to meta 'pm_trainer_state'.

    .venv\\Scripts\\python.exe -m app.worker          (normally launched by the server, not by hand)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data_cache" / "worker_state.json"
PID_FILE = ROOT / "data_cache" / "worker.pid"
logger = logging.getLogger("worker")


def _below_normal_priority() -> None:
    if os.name != "nt":
        try:
            os.nice(10)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        import ctypes

        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:  # noqa: BLE001
        pass


async def _mirror_state() -> None:
    from app.optimizer import trainer_service

    while True:
        try:
            st = dict(trainer_service._state)
            st["training_now"] = trainer_service._lock.locked()
            st["heartbeat"] = time.time()
            st["pid"] = os.getpid()
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, default=str), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except Exception:  # noqa: BLE001 - the file is a convenience for the status page
            pass
        await asyncio.sleep(2)


async def _watch_parent(parent_pid: int) -> None:
    """Exit when the server that started us is gone (no orphan worker training on after a crash)."""
    while True:
        await asyncio.sleep(10)
        if parent_pid and not _alive(parent_pid):
            logger.info("server %s is gone, worker exiting", parent_pid)
            os._exit(0)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def main() -> None:
    from app import db
    from app.optimizer import trainer_service
    from app.polymarket import trainer_service as pm_trainer_service

    db.init_db()
    loops = {s.strip() for s in os.environ.get("NOSTRO_WORKER_LOOPS", "trainer,polymarket").split(",") if s.strip()}
    tasks = [asyncio.create_task(_mirror_state())]
    parent = int(os.environ.get("NOSTRO_WORKER_PARENT", "0") or 0)
    if parent:
        tasks.append(asyncio.create_task(_watch_parent(parent)))
    if "trainer" in loops:
        tasks.append(asyncio.create_task(trainer_service.scheduler_loop()))
    if "polymarket" in loops:
        tasks.append(asyncio.create_task(pm_trainer_service.scheduler_loop()))
        tasks.append(asyncio.create_task(pm_trainer_service.backfill_loop()))
    logger.info("worker %s running: %s", os.getpid(), ", ".join(sorted(loops)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(name)s: %(message)s")
    _below_normal_priority()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        asyncio.run(main())
    finally:
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except Exception:  # noqa: BLE001
            pass
