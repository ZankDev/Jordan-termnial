"""Real-balance tracker that does not need the Claude Chrome extension.

It drives its OWN Chrome window (a separate profile in data_cache/tp_profile, so your normal Chrome is never touched), opens the TradersPost dashboard, reads the
paper-account balance and records it exactly like the old Chrome cron did (connections.add_balance + sync_account_to_real + a Telegram alert on a change >= $1).
It is read-only: it never clicks anything on TradersPost and never types a password.

usage:
  python scripts/tp_balance_tracker.py login   # ONCE: a Chrome window opens; log in to TradersPost yourself, then close the window. The login is kept in the profile.
  python scripts/tp_balance_tracker.py once    # read + record one time (headless)
  python scripts/tp_balance_tracker.py loop    # read every 5 minutes, all day (Ctrl+C to stop); use --minutes N to change the interval
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PROFILE = ROOT / "data_cache" / "tp_profile"
URL = "https://app.traderspost.io/app/dashboard"
MONEY = re.compile(r"\$[\d,]+\.\d{2}")


def _launch(p, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless, viewport={"width": 1280, "height": 900})


def login() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL)
        print("Log in to TradersPost in the window that opened. When the dashboard shows your balance, CLOSE the window. Waiting...")
        try:
            page.wait_for_event("close", timeout=15 * 60 * 1000)
        except Exception:  # noqa: BLE001
            pass
        ctx.close()
    print("Login saved in", PROFILE)


def read_balance() -> float | None:
    """Open the dashboard in a fresh page, wait for the figures to render, return the first dollar amount (the paper-account balance), or None."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=True)
        try:
            page = ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            if "login" in page.url or "sign" in page.url:
                print("not logged in - run: python scripts/tp_balance_tracker.py login", flush=True)
                return None
            for _ in range(6):  # ~30 s for the balance to render
                page.wait_for_timeout(5000)
                found = MONEY.findall(page.inner_text("body"))
                if found:
                    return float(found[0].replace("$", "").replace(",", ""))
        finally:
            ctx.close()
    return None


def record(new: float) -> None:
    from app.notify import connections, ops_alerts

    connections.load_into_env()
    previous = (connections.balance_summary().get("typed") or {}).get("balance")
    connections.add_balance(new, "read from TradersPost dashboard (python tracker)")
    connections.sync_account_to_real(new)
    if previous is not None and abs(new - previous) >= 1:
        ops_alerts.alert("real_bal", f"Real balance ${new:,.2f} ({new - previous:+,.2f} since the last check)", cooldown=0)
    print(f"{time.strftime('%H:%M:%S')}  balance ${new:,.2f}  change {0 if previous is None else new - previous:+,.2f}", flush=True)


def once() -> bool:
    bal = read_balance()
    if bal is None or bal <= 0:
        print(f"{time.strftime('%H:%M:%S')}  could not read the balance", flush=True)
        return False
    record(bal)
    return True


def loop(minutes: float) -> None:
    while True:
        try:
            once()
        except Exception as e:  # noqa: BLE001 - keep tracking through a bad page load
            print("error:", type(e).__name__, e, flush=True)
        time.sleep(minutes * 60)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "login":
        login()
    elif mode == "loop":
        loop(float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 5)
    else:
        sys.exit(0 if once() else 1)
