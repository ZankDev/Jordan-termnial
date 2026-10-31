"""One-minute vertical (1080x1920) motion-graphics explainer on gamma levels, using META's live levels.
Narration: Microsoft neural TTS (edge-tts, en-US-AndrewNeural) with word timings -> word-by-word caption.
Visuals: gamma_short.html (GSAP timeline), rendered frame by frame with headless Chromium, encoded with ffmpeg.
Usage: .venv\\Scripts\\python.exe scripts\\short_video\\make_short.py [OUT_MP4]
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import edge_tts  # noqa: E402

from app.invest import gamma  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "reports", time.strftime("%Y-%m-%d"), "gamma_short_META.mp4")
WORK = os.path.join(os.path.dirname(OUT), "_short")
FR = os.path.join(WORK, "frames")
os.makedirs(FR, exist_ok=True)
VOICE, RATE, FPS, GAP = "en-US-AndrewNeural", "+4%", 30, 0.35

G = gamma.compute("META")
px, cw, pw, zg = round(G["spot"]), int(G["call_wall"]), int(G["put_wall"]), round(G["zero_gamma"])
above = G["spot"] > G["zero_gamma"]
SCENES = [
    ("s1", "Why does a stock keep stopping at the exact same price?"),
    ("s2", "When you buy an option, a market maker sells it to you. They don't want to bet on direction, so they hedge, by buying and selling the stock itself."),
    ("s3", "Gamma tells them how many shares to trade, every time the price moves. And it's strongest right near the current price."),
    ("s4", "When gamma is positive, they sell into rallies and buy the dips. Moves get absorbed, and the market stays calm."),
    ("s5", "When gamma is negative, they're forced to chase the price. Every move gets bigger."),
    ("s6", "That creates the levels. The call wall, where price tends to stall. The put wall, which supports it from below. And zero gamma, where the behaviour flips."),
    ("s7", f"Meta today trades at {px} dollars, {'above' if above else 'below'} zero gamma at {zg}. The put wall is {pw}, and the call wall is {cw}."),
    ("s8", "Gamma levels. Now in your terminal, for any stock."),
]


async def tts(text: str, mp3: str) -> list[dict]:
    words = []
    c = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"s": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "w": ch["text"]})
    return words


def dur(path: str) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).decode().strip())


print("narration ...", flush=True)
scenes, words, t = [], [], 0.25
for sid, text in SCENES:
    mp3 = os.path.join(WORK, f"{sid}.mp3")
    ws = asyncio.run(tts(text, mp3))
    d = dur(mp3)
    scenes.append({"id": sid, "start": round(t, 3), "dur": round(d + GAP, 3), "mp3": mp3, "words": ws})
    for i, w in enumerate(ws):
        end = ws[i + 1]["s"] if i + 1 < len(ws) else w["s"] + w["d"] + 0.25
        words.append({"s": round(t + w["s"], 3), "e": round(t + end, 3), "w": w["w"]})
    t += d + GAP
total = round(t + 0.6, 3)


def frac(sid: str, key: str) -> float:
    s = next(x for x in scenes if x["id"] == sid)
    hit = next((w for w in s["words"] if w["w"].lower().startswith(key)), None)
    return round(max(0.05, (hit["s"] - 0.15) / s["dur"]), 3) if hit else 0.3


data = {"scenes": [{k: v for k, v in s.items() if k in ("id", "start", "dur")} for s in scenes], "words": words, "total": total,
        "price": G["spot"], "callWall": cw, "putWall": pw, "zero": zg, "regimeText": ("Above zero gamma → calmer moves" if above else "Below zero gamma → bigger moves"),
        "s6": {"call": frac("s6", "call"), "put": frac("s6", "put"), "zero": frac("s6", "zero")}}
html = open(os.path.join(HERE, "gamma_short.html"), encoding="utf-8").read().replace("__DATA__", json.dumps(data))
page = os.path.join(WORK, "page.html")
open(page, "w", encoding="utf-8").write(html)
print(f"total {total}s, {len(words)} caption words", flush=True)

print("audio mix ...", flush=True)
inputs, filt = [], []
for i, s in enumerate(scenes):
    inputs += ["-i", s["mp3"]]
    ms = int(s["start"] * 1000)
    filt.append(f"[{i}]adelay={ms}|{ms},volume=1.6[a{i}]")
n = len(scenes)
# soft ambient pad under the voice (two low sines, slow tremolo, faded in and out)
inputs += ["-f", "lavfi", "-t", str(total), "-i", "sine=frequency=110:sample_rate=48000", "-f", "lavfi", "-t", str(total), "-i", "sine=frequency=164.8:sample_rate=48000"]
filt.append(f"[{n}][{n + 1}]amix=inputs=2,tremolo=f=0.15:d=0.5,lowpass=f=600,volume=0.05,afade=t=in:d=2,afade=t=out:st={total - 2}:d=2[pad]")
filt.append("".join(f"[a{i}]" for i in range(n)) + f"[pad]amix=inputs={n + 1}:normalize=0,atrim=0:{total}[out]")
audio = os.path.join(WORK, "audio.m4a")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", audio], check=True)

print("rendering frames ...", flush=True)
from playwright.sync_api import sync_playwright  # noqa: E402

nframes = int(total * FPS)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1208\chrome-win64\chrome.exe"))
    pg = b.new_page(viewport={"width": 720, "height": 1280}, device_scale_factor=1.5)
    pg.goto("file:///" + page.replace("\\", "/"))
    pg.wait_for_function("window.ready !== undefined")
    pg.evaluate("window.ready")
    pg.wait_for_timeout(1500)
    if os.environ.get("PREVIEW"):  # a handful of frames for a design check, then stop
        for sc in scenes:
            for k, f in (("a", 0.35), ("b", 0.8)):
                pg.evaluate(f"window.seek({sc['start'] + sc['dur'] * f:.3f})")
                pg.screenshot(path=os.path.join(WORK, f"prev_{sc['id']}{k}.jpg"), type="jpeg", quality=80)
        b.close()
        sys.exit(0)
    for i in range(nframes):
        pg.evaluate(f"window.seek({i / FPS:.4f})")
        pg.screenshot(path=os.path.join(FR, f"f{i:05d}.jpg"), type="jpeg", quality=90)
        if i % 300 == 0:
            print(f"  frame {i}/{nframes}", flush=True)
    b.close()

print("encoding ...", flush=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(FR, "f%05d.jpg"), "-i", audio, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", OUT], check=True)
print("DONE", OUT, flush=True)
