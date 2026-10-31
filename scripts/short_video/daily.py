"""Daily JORDAN short: every morning (Windows Task Scheduler, 06:00 Israel time) render the next topic from the queue and send it to Telegram.
Queue + history: data_cache/video_queue.json. Log: reports/topics/daily.log.
Usage: pythonw scripts\\short_video\\daily.py            next topic
       python  scripts\\short_video\\daily.py --topic cpi   a specific topic (does not change the queue order)
       python  scripts\\short_video\\daily.py --status       show the queue
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from topics import ORDER, TITLES  # noqa: E402

STATE = os.path.join(ROOT, "data_cache", "video_queue.json")
OUTDIR = os.path.join(ROOT, "reports", "topics")
LOG = os.path.join(OUTDIR, "daily.log")
LOCK = os.path.join(OUTDIR, "daily.lock")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
NOWIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: no console flashes at 6 am
os.makedirs(OUTDIR, exist_ok=True)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001  (pythonw has no console)
        pass


def state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"order": ORDER, "done": [], "history": []}


def save(st: dict) -> None:
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1)


def telegram() -> tuple[str, str] | None:
    env = dict(re.findall(r'set "(TELEGRAM_[A-Z_]+)=([^"]*)"', open(os.path.join(ROOT, "run.bat"), encoding="utf-8", errors="ignore").read()))
    t, c = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    return (t, c) if t and c else None


def tg_text(text: str) -> None:
    import requests
    cfg = telegram()
    if cfg:
        requests.post(f"https://api.telegram.org/bot{cfg[0]}/sendMessage", data={"chat_id": cfg[1], "text": text}, timeout=30)


def tg_video(path: str, caption: str) -> bool:
    import requests
    cfg = telegram()
    if not cfg:
        log("telegram not configured in run.bat")
        return False
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{cfg[0]}/sendVideo", data={"chat_id": cfg[1], "caption": caption[:1000], "supports_streaming": "true", "width": 1080, "height": 1920},
                          files={"video": (os.path.basename(path), fh, "video/mp4")}, timeout=900)
    ok = r.ok and r.json().get("ok")
    if not ok:
        log(f"telegram sendVideo failed: {r.status_code} {r.text[:200]}")
    return bool(ok)


def render(topic: str) -> str | None:
    out = os.path.join(OUTDIR, f"{topic}.mp4")
    env = {**os.environ, "TTS": "piper", "PYTHONIOENCODING": "utf-8"}
    for attempt in range(3):
        if attempt:
            env["RESUME"] = "1"
            log(f"retry {attempt} (resume)")
        with open(os.path.join(OUTDIR, f"{topic}.render.log"), "a", encoding="utf-8") as lf:
            rc = subprocess.run([PY, os.path.join(HERE, "jordan_short.py"), topic, out], cwd=ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT, creationflags=NOWIN).returncode
        if rc == 0 and os.path.exists(out):
            return out
        log(f"render exit code {rc}")
        time.sleep(20)
    return None


def compress(src: str) -> str:
    dst = src[:-4] + "_tg.mp4"
    for crf in (20, 23, 26, 29):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst],
                       check=True, creationflags=NOWIN)
        if os.path.getsize(dst) < 48 * 1024 * 1024:
            return dst
    return dst


def main() -> None:
    args = sys.argv[1:]
    st = state()
    if "--status" in args:
        left = [t for t in st["order"] if t not in st["done"]]
        print(json.dumps({"next": left[0] if left else None, "left": left, "done": st["done"]}, indent=1))
        return
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 3 * 3600:
        log("another run is in progress; skipping")
        return
    open(LOCK, "w").write(str(os.getpid()))
    try:
        forced = args[args.index("--topic") + 1] if "--topic" in args else None
        left = [t for t in st["order"] if t not in st["done"]]
        topic = forced or (left[0] if left else None)
        if not topic:
            if not st.get("empty_notified"):
                tg_text("🎬 JORDAN daily videos: the topic list is finished. Send me new topics and I'll queue them.")
                st["empty_notified"] = True
                save(st)
            log("queue empty")
            return
        log(f"start {topic}: {TITLES.get(topic, topic)}")
        t0 = time.time()
        out = render(topic)
        if not out:
            tg_text(f"⚠️ JORDAN daily video '{TITLES.get(topic, topic)}' failed to render this morning. It will be retried tomorrow.")
            log("render failed")
            return
        small = compress(out)
        n = len(st["done"]) + (0 if forced else 1)
        caption = f"🎬 JORDAN daily #{n} · {TITLES.get(topic, topic)}\n\n◆ JORDAN TERMINAL"
        if tg_video(small, caption):
            if not forced:
                st["done"].append(topic)
            st.setdefault("history", []).append({"topic": topic, "at": time.strftime("%Y-%m-%d %H:%M"), "secs": round(time.time() - t0), "file": out})
            save(st)
            log(f"sent {topic} in {round(time.time() - t0)}s")
        else:
            log("send failed; will retry tomorrow")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
