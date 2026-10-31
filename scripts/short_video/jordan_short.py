"""JORDAN shorts: vertical 1080x1920 motion-graphics videos in the style of premium finance explainers
(black stage, glossy objects, single-word captions, kinetic type, flash cuts) with neural TTS, a music bed and synthesised SFX.
Usage: .venv\\Scripts\\python.exe scripts\\short_video\\jordan_short.py what|trade [OUT_MP4]   (PREVIEW=1 renders a few stills)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import edge_tts  # noqa: E402
import sfx  # noqa: E402

VIDEO = sys.argv[1] if len(sys.argv) > 1 else "what"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "reports", time.strftime("%Y-%m-%d"), f"gamma_{VIDEO}.mp4")
WORK = os.path.join(os.path.dirname(OUT), f"_{VIDEO}")
FR = os.path.join(WORK, "frames")
os.makedirs(FR, exist_ok=True)
VOICE, RATE, FPS, GAP = "en-US-AndrewNeural", "+6%", 30, 0.32
SHOTS = r"C:\Users\yarde\.claude\jobs\66b87be5\tmp"


# ---------------------------------------------------------------- candle stories
def candles(way: list[tuple[int, float]], n: int, seed: int, vol: float = 0.9, wick: float = 0.7) -> list[list[float]]:
    rng = np.random.default_rng(seed)
    xs, ys = zip(*way)
    path = np.interp(np.arange(n), xs, ys)
    out, prev = [], path[0]
    for i in range(n):
        c = path[i] + rng.normal(0, vol * 0.6)
        o = prev
        h = max(o, c) + abs(rng.normal(0, vol * wick))
        lo = min(o, c) - abs(rng.normal(0, vol * wick))
        out.append([round(o, 2), round(h, 2), round(lo, 2), round(c, 2)])
        prev = c
    return out


def clamp_level(cs: list[list[float]], level: float, side: int, upto: int | None = None) -> None:
    """Make a level hold: highs never above a ceiling (side=+1) / lows never below a floor (side=-1)."""
    for c in cs[: upto or len(cs)]:
        if side > 0 and c[1] > level:
            c[1] = level + 0.15; c[0] = min(c[0], level); c[3] = min(c[3], level - 0.2)
        if side < 0 and c[2] < level:
            c[2] = level - 0.15; c[0] = max(c[0], level); c[3] = max(c[3], level + 0.2)


def rng_of(cs: list[list[float]], extra: list[float] = ()) -> tuple[float, float]:
    lo = min(min(c[2] for c in cs), *extra) if extra else min(c[2] for c in cs)
    hi = max(max(c[1] for c in cs), *extra) if extra else max(c[1] for c in cs)
    pad = (hi - lo) * 0.12
    return lo - pad, hi + pad


CALL, PUT, ZERO = 110.0, 90.0, 100.0
LV = lambda cue_call="call", cue_put="put", cue_zero="zero": [  # noqa: E731
    {"v": CALL, "label": "CALL WALL", "color": "#22c08f", "cue": cue_call},
    {"v": PUT, "label": "PUT WALL", "color": "#ff4d5e", "cue": cue_put},
    {"v": ZERO, "label": "ZERO γ", "color": "#ffa028", "dash": True, "cue": cue_zero, "left": True}]

# hook (video 1): three rejections at an invisible ceiling
h1 = candles([(0, 96), (6, 109.6), (9, 103), (14, 109.8), (17, 102), (22, 109.7), (26, 100), (30, 99)], 30, 3, 0.8)
clamp_level(h1, CALL, +1)
# levels chart: chop between the walls
lv1 = candles([(0, 99), (5, 108.5), (9, 101), (13, 92), (17, 98), (21, 108), (25, 103), (29, 91.5), (33, 99), (36, 104)], 36, 5, 1.0)
clamp_level(lv1, CALL, +1); clamp_level(lv1, PUT, -1)
# hook (video 2): rally into the call wall, rejection
h2 = candles([(0, 98), (10, 109.5), (12, 108), (14, 109.8), (17, 104), (22, 97), (26, 94)], 26, 11, 0.9)
clamp_level(h2, CALL, +1)
# trade story: drop into the put wall, absorption, reclaim, run to zero gamma
tr = candles([(0, 101), (6, 96), (10, 91.2), (13, 90.6), (16, 91), (18, 90.5), (21, 93.5), (25, 97), (29, 100.5), (31, 100.2)], 32, 21, 0.75)
clamp_level(tr, PUT, -1)
# negative gamma: break of the put wall, retest from below, run
ng = candles([(0, 99), (5, 95), (9, 91), (11, 90.4), (13, 87.5), (15, 89.6), (16, 89.9), (18, 86), (22, 81), (26, 78)], 26, 31, 0.9)
for c in ng[14:17]:
    c[1] = min(c[1], PUT + 0.2)

IMG = {"gamma": "img_gamma.png", "zone": "img_zone.png", "sim": "img_sim.png", "qnt_mc": "img_qnt_mc.png", "qnt_top": "img_qnt_top.png", "hist": "img_hist.png"}


def chart_spec(cs, levels=None, boxes=None, arrows=None, bubbles=None, top=330, h=740, extra=(), p0=0, p1=1):
    lo, hi = rng_of(cs, list(extra) + [L["v"] for L in (levels or [])])
    return {"candles": cs, "lo": lo, "hi": hi, "levels": levels or [], "boxes": boxes or [], "arrows": arrows or [], "bubbles": bubbles, "top": top, "h": h, "p0": p0, "p1": p1}


VIDEOS = {
    "what": [
        dict(id="s1", kind="hook", flash=True, text="This invisible line decides where the market stops. And most traders can't even see it.",
             p=dict(line1="This invisible line decides", line2="where price stops", key2="where", hits=["line", "stops", "see"],
                    chart={**chart_spec(h1, levels=[{"v": CALL, "label": "?", "color": "#ffa028", "cue": None}], top=470, h=700)}),
             sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("line", "tick", 0.9), ("stops", "tick", 0.9), ("see", "glitch", 0.6)], em=["invisible", "stops"]),
        dict(id="s2", kind="dealer", text="Every time you buy an option, a market maker sells it to you. They don't want to gamble on direction. So they hedge, by trading the stock itself.",
             p=dict(dealerCue="market", hedgeCue="hedge"), sfx=[("start", "swipe", 0.9), ("market", "pop", 0.9), ("hedge", "whoosh", 0.9), ("hedge", "pop_hi", 0.7)], em=["hedge", "market"]),
        dict(id="s3", kind="bars", text="Gamma tells them how much to hedge. And it's biggest at the strikes with huge open interest.",
             p=dict(title="Open interest by strike", vals=[22, 30, 41, 58, 100, 64, 47, 35, 26], labels=["5600", "5620", "5640", "5660", "5680", "5700", "5720", "5740", "5760"], pill="γ is strongest here", pillCue="biggest"),
             sfx=[("start", "whoosh", 0.7), (0.35, "tick", 0.5), (0.6, "tick", 0.5), (0.85, "tick", 0.5), ("biggest", "ding", 0.8)], em=["gamma", "biggest"]),
        dict(id="s4", kind="regime", flash=True, text="When gamma is positive, dealers sell the rips and buy the dips. Price gets pinned and calm. When it's negative, they chase the move, and every move gets bigger.",
             p=dict(posTitle="Dealers fight the move", posText="Sell rips, buy dips: price is pinned and calm.", negTitle="Dealers chase the move", negText="Buy highs, sell lows: every move gets bigger.", negCue="negative"),
             sfx=[("start", "whoosh", 0.9), ("negative", "whoosh_dn", 0.9), ("negative", "glitch", 0.5), ("bigger", "boom", 0.6)], em=["positive", "negative", "pinned", "bigger"]),
        dict(id="s5", kind="chart", text="That creates three levels. The call wall: a ceiling where rallies stall. The put wall: a floor where dips get bought. And zero gamma: the line where calm turns into chaos.",
             p=dict(title="The 3 gamma levels", chart=chart_spec(lv1, levels=LV()), reveal=[0, 0.95], shake=["chaos"]),
             sfx=[("start", "swipe", 0.8), ("call", "pop", 1.0), ("put", "pop", 1.0), ("zero", "pop_hi", 1.0), ("chaos", "boom", 0.9)], em=["call", "put", "zero", "chaos"]),
        dict(id="s6", kind="shot", flash=True, text="And you can see them live, for any stock, inside the Jordan Terminal.",
             p=dict(title="Live gamma levels", img=IMG["gamma"], zoom=1.45, panX=-8, top=380, pill="◆ JORDAN TERMINAL"),
             sfx=[("start", "whoosh", 0.9), ("jordan", "ding", 0.8)], em=["live", "jordan"]),
        dict(id="s7", kind="outro", text="Follow for part two: how to trade them.", p=dict(cta="Follow for part 2"),
             sfx=[("start", "boom", 0.8), (2.3, "click", 1.0), (2.35, "pop_hi", 0.8)], em=["follow"]),
    ],
    "trade": [
        dict(id="s1", kind="hook", flash=True, text="Stop buying right under this line. Here's how pros actually trade gamma levels.",
             p=dict(line1="Stop buying", line2="under the call wall", key2="under", hits=["line"],
                    chart={**chart_spec(h2, levels=[{"v": CALL, "label": "CALL WALL", "color": "#22c08f", "cue": None}], arrows=[{"i": 14, "v": 109.8, "up": False, "label": "TRAP", "cue": "line"}], top=470, h=700)}),
             sfx=[("start", "boom", 1.0), ("start", "whoosh", 0.8), ("line", "glitch", 0.8), ("pros", "riser", 0.5)], em=["stop", "pros"]),
        dict(id="s2", kind="statement", text="Step one. Check the regime.", p=dict(line1="STEP 1", line2="Check the regime", key2="check"), sfx=[("start", "swipe", 0.9), ("check", "pop", 0.8)], em=["regime"]),
        dict(id="s3", kind="regime", text="Above zero gamma, dealers absorb moves, so you fade the edges. Below zero gamma, they chase, so you trade the breakouts.",
             p=dict(posTitle="Above zero γ: fade the edges", posText="Sell the call wall, buy the put wall.", negTitle="Below zero γ: trade breakouts", negText="Dealers chase, so moves extend.", negCue="below"),
             sfx=[("start", "whoosh", 0.9), ("below", "whoosh_dn", 0.9)], em=["above", "below", "fade", "breakouts"]),
        dict(id="s4", kind="chart", flash=True, text="Step two. Mark the walls. The call wall above, the put wall below, and zero gamma in between.",
             p=dict(title="STEP 2 · Mark the walls", chart=chart_spec(lv1, levels=LV()), reveal=[0, 0.9]),
             sfx=[("start", "swipe", 0.8), ("call", "pop", 1.0), ("put", "pop", 1.0), ("zero", "pop_hi", 1.0)], em=["walls", "call", "put", "zero"]),
        dict(id="s5", kind="chart", text="Step three. Price drops into the put wall. Don't buy blindly. Wait for absorption: heavy selling, but price refuses to go lower.",
             p=dict(title="STEP 3 · Wait for absorption", chart=chart_spec(tr, levels=[LV()[1] | {"cue": "put"}], bubbles=[{"i": 11, "v": 90.6, "r": 22, "side": -1}, {"i": 13, "v": 90.4, "r": 30, "side": -1}, {"i": 15, "v": 90.5, "r": 26, "side": -1}, {"i": 17, "v": 90.4, "r": 34, "side": -1}], p0=0, p1=0.62),
                    reveal=[0, 0.85], bubbleCue="absorption", badge={"text": "ABSORPTION", "cls": "g", "cue": "refuses", "top": 1090}),
             sfx=[("start", "swipe", 0.8), ("put", "pop", 0.9), ("absorption", "tick", 0.8), ("heavy", "tick", 0.8), ("refuses", "ding", 0.7)], em=["absorption", "refuses", "put"]),
        dict(id="s6", kind="chart", text="Step four. Enter long when price reclaims the level. Stop just below the wall. Target zero gamma. That's a three to one trade.",
             p=dict(title="STEP 4 · Enter on the reclaim", chart=chart_spec(tr, levels=[LV()[1] | {"cue": None, "left": True}, LV()[2] | {"cue": "target"}], boxes=[{"i0": 19, "i1": 31, "entry": 92.6, "stop": 89.4, "target": 100.0, "cue": "enter", "tStop": "STOP 89.4", "tTarget": "TARGET 100.0", "tEntry": "LONG 92.6"}],
                                                                           arrows=[{"i": 19, "v": 91.5, "up": True, "cue": "enter"}], p0=0.62, p1=1), reveal=[0.05, 0.95], badge={"text": "R:R  1 : 3", "cls": "g", "cue": "three", "top": 1090}),
             sfx=[("start", "swipe", 0.8), ("enter", "pop", 1.0), ("stop", "tick", 0.8), ("target", "pop_hi", 0.9), ("three", "ding", 1.0)], em=["long", "stop", "target", "three"]),
        dict(id="s7", kind="chart", flash=True, text="But below zero gamma, flip it. When the put wall breaks, short the retest. Dealers sell with you, and the move runs.",
             p=dict(title="Negative γ · short the retest", chart=chart_spec(ng, levels=[LV()[1] | {"cue": "put", "left": True}], boxes=[{"i0": 15, "i1": 25, "entry": 89.6, "stop": 92.2, "target": 79.5, "cue": "short", "tStop": "STOP", "tTarget": "TARGET", "tEntry": "SHORT"}],
                                                                           arrows=[{"i": 15, "v": 89.9, "up": False, "cue": "short"}]), reveal=[0, 0.95], shake=["breaks", "runs"]),
             sfx=[("start", "whoosh_dn", 0.9), ("breaks", "glitch", 0.9), ("short", "pop", 1.0), ("runs", "boom", 0.9)], em=["flip", "breaks", "short", "runs"]),
        dict(id="s8", kind="checklist", text="Three rules. Never short the put wall in positive gamma. Never fade a breakout in negative gamma. And always size from your stop.",
             p=dict(title="3 rules", items=[{"text": "Short the put wall in positive γ", "ok": False, "cue": "never"}, {"text": "Fade a breakout in negative γ", "ok": False, "cue": "never#2"}, {"text": "Size every trade from your stop", "ok": True, "cue": "always"}]),
             sfx=[("start", "swipe", 0.8), ("never", "pop", 0.9), ("never#2", "pop", 0.9), ("always", "ding", 0.8)], em=["never", "always"]),
        dict(id="s9", kind="shot", flash=True, text="Practice it risk free in the Jordan Sim. Drag the gamma zone tool, and see exactly where dealers hedge.",
             p=dict(title="Practice in the Jordan Sim", img=IMG["zone"], zoom=1.5, panX=-6, panY=-4, top=380, pill="γ GAMMA ZONE · 3D"),
             sfx=[("start", "whoosh", 0.9), ("drag", "click", 1.0), ("dealers", "ding", 0.8)], em=["free", "sim", "gamma"]),
        dict(id="s10", kind="outro", text="Follow, and trade the levels.", p=dict(cta="Follow for more"), sfx=[("start", "boom", 0.8), (2.3, "click", 1.0), (2.35, "pop_hi", 0.8)], em=["follow"]),
    ],
}


PIPER = os.path.join(ROOT, "data_cache", "piper", os.environ.get("PIPER_VOICE", "en_US-ryan-high") + ".onnx")


def tts_any(text: str, path: str) -> tuple[list[dict], str]:
    """Narration audio + word timings. Microsoft neural TTS when it answers; otherwise Piper (local neural voice) with
    word timings from Whisper on Groq. Returns (words, audio path)."""
    if os.environ.get("TTS", "auto") != "piper":
        try:
            return asyncio.run(tts(text, path)), path
        except Exception as e:  # noqa: BLE001
            print(f"  edge-tts unavailable ({type(e).__name__}); using Piper", flush=True)
    wav = path[:-4] + ".wav"
    subprocess.run([sys.executable, "-m", "piper", "-m", PIPER, "-f", wav, "--length-scale", os.environ.get("PIPER_SPEED", "0.98"), "--sentence-silence", "0.18"], input=text.encode(), check=True, capture_output=True)
    import requests
    sys.path.insert(0, ROOT)
    from app.intel import llm
    key = llm.config()["key"]
    for attempt in range(3):
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": "Bearer " + key}, files={"file": ("a.wav", open(wav, "rb"), "audio/wav")},
                          data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "timestamp_granularities[]": "word", "language": "en", "prompt": text}, timeout=120)
        if r.ok:
            break
        time.sleep(3 * (attempt + 1))
    r.raise_for_status()
    ws = [{"s": w["start"], "d": max(0.05, w["end"] - w["start"]), "w": w["word"].strip()} for w in r.json().get("words", [])]
    return ws, wav


def _add_topics() -> None:
    from topics import topics
    VIDEOS.update(topics(chart_spec, IMG))


_add_topics()


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


def load_pcm(src: str) -> np.ndarray:
    wav = src[:-4] + ".48k.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(sfx.SR), "-af", "highpass=f=70,acompressor=threshold=-18dB:ratio=3:attack=5:release=80,loudnorm=I=-16:TP=-1.5", wav], check=True)
    with wave.open(wav) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float) / 32768
    return x


def prep_images() -> None:
    from PIL import Image
    src = {"gamma": (os.path.join(SHOTS, "shots_gz", "3_gamma_window.png"), (130, 230, 1410, 970)), "zone": (os.path.join(SHOTS, "shots_gz", "6_zone_jordan.png"), (0, 0, 1150, 690)) if os.path.exists(os.path.join(SHOTS, "shots_gz", "6_zone_jordan.png")) else (os.path.join(SHOTS, "shots_gz", "5_zone_on_chart.png"), (0, 70, 1180, 700)),
           "sim": (os.path.join(SHOTS, "shots_ui", "sim_jordan.png"), (10, 105, 1530, 900)),
           "qnt_mc": (os.path.join(SHOTS, "shots_qnt", "2_qnt_full.png"), (10, 940, 770, 1420)), "qnt_top": (os.path.join(SHOTS, "shots_qnt", "2_qnt_full.png"), (10, 380, 1530, 930)),
           "hist": (os.path.join(SHOTS, "shots_hist", "1_history_panel.png"), (0, 0, 1515, 485))}
    for k, (path, box) in src.items():
        if os.path.exists(path):
            Image.open(path).crop(box).save(os.path.join(WORK, IMG[k]))


def render(page: str, total: float) -> None:
    """Frames (resumable: existing frames are kept when RESUME=1) + encode."""
    from playwright.sync_api import sync_playwright
    audio = os.path.join(WORK, "audio.m4a")
    nframes = int(total * FPS)
    if not os.environ.get("RESUME"):
        shutil.rmtree(FR, ignore_errors=True)
    os.makedirs(FR, exist_ok=True)
    todo = [i for i in range(nframes) if not os.path.exists(os.path.join(FR, f"f{i:05d}.jpg"))]
    print(f"rendering {len(todo)} of {nframes} frames", flush=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1208\chrome-win64\chrome.exe"))
        pg = b.new_page(viewport={"width": 720, "height": 1280}, device_scale_factor=1.5)
        pg.goto("file:///" + page.replace("\\", "/"))
        pg.wait_for_function("window.ready !== undefined"); pg.evaluate("window.ready"); pg.wait_for_timeout(1500)
        for n, i in enumerate(todo):
            pg.evaluate(f"window.seek({i / FPS:.4f})")
            pg.screenshot(path=os.path.join(FR, f"f{i:05d}.jpg"), type="jpeg", quality=90)
            if n % 300 == 0: print(f"  frame {i}/{nframes}", flush=True)
        b.close()
    print("encoding ...", flush=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(FR, "f%05d.jpg"), "-i", audio, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", OUT], check=True)
    print("DONE", OUT, flush=True)


def main() -> None:
    spec = VIDEOS[VIDEO]
    page, meta = os.path.join(WORK, "page.html"), os.path.join(WORK, "meta.json")
    if os.environ.get("RESUME") and os.path.exists(page) and os.path.exists(os.path.join(WORK, "audio.m4a")):
        total = json.load(open(meta))["total"] if os.path.exists(meta) else float(re.search(r'"total": ([0-9.]+)', open(page, encoding="utf-8").read()).group(1))
        render(page, total); return
    prep_images()
    print("narration ...", flush=True)
    scenes, words, t, voice = [], [], 0.35, []
    for sc in spec:
        mp3 = os.path.join(WORK, f"{sc['id']}.mp3")
        ws, mp3 = tts_any(sc["text"], mp3)
        d = dur(mp3)
        extra = 2.8 if sc["kind"] == "outro" else 0.0
        sdur = round(d + GAP + extra, 3)
        cues, seen = {}, {}
        for w in ws:
            key = re.sub(r"[^a-z0-9']", "", w["w"].lower())
            seen[key] = seen.get(key, 0) + 1
            for k in [key] + ([f"{key}#{seen[key]}"] if seen[key] > 1 else []):
                cues.setdefault(k, round(max(0.05, w["s"] - 0.1), 3))
        # prefix matching for cue keys (e.g. "absorption" from "absorption:")
        def cue(k: str) -> float | None:
            if k in cues:
                return cues[k]
            base, _, nth = k.partition("#")
            hits = [w for w in ws if re.sub(r"[^a-z0-9']", "", w["w"].lower()).startswith(base)]
            idx = int(nth) - 1 if nth else 0
            return round(max(0.05, hits[idx]["s"] - 0.1), 3) if len(hits) > idx else None
        keys = set()
        for v in json.dumps(sc["p"]).split('"'):
            pass
        def walk(o):
            if isinstance(o, dict):
                for k2, v2 in o.items():
                    if k2.lower().endswith("cue") and isinstance(v2, str): keys.add(v2)
                    if k2 in ("hits", "shake") and isinstance(v2, list): keys.update(v2)
                    if k2 == "key2" and isinstance(v2, str): keys.add(v2)
                    walk(v2)
            elif isinstance(o, list):
                for v2 in o: walk(v2)
        walk(sc["p"])
        keys.update(k for k, _, _ in sc.get("sfx", []) if isinstance(k, str) and k != "start")
        sc_cues = {k: cue(k) for k in keys if cue(k) is not None}
        missing = [k for k in keys if cue(k) is None]
        if missing:
            print(f"  {sc['id']}: cue words not spoken: {missing}", flush=True)
        scenes.append({"id": sc["id"], "kind": sc["kind"], "start": round(t, 3), "dur": sdur, "p": sc["p"], "cues": sc_cues, "flash": sc.get("flash", False)})
        em = {e.lower() for e in sc.get("em", [])}
        for i, w in enumerate(ws):
            end = ws[i + 1]["s"] if i + 1 < len(ws) else w["s"] + w["d"] + 0.25
            words.append({"s": round(t + w["s"], 3), "e": round(t + end, 3), "w": w["w"], "em": re.sub(r"[^a-z0-9']", "", w["w"].lower()) in em})
        voice.append((t, load_pcm(mp3)))
        sc["_t"], sc["_cue"] = t, sc_cues
        t += sdur
    total = round(t + 0.4, 3)
    # audio: voice on the timeline + SFX cues + music bed
    n = int(total * sfx.SR)
    vtrack = np.zeros(n)
    for t0, pcm in voice:
        a = int(t0 * sfx.SR); m = min(len(pcm), n - a); vtrack[a:a + m] += pcm[:m] * 0.95
    cues = []
    for sc in spec:
        for k, snd, g in sc.get("sfx", []):
            if k == "start": tt = sc["_t"] - 0.05
            elif isinstance(k, (int, float)): tt = sc["_t"] + k
            else:
                c0 = sc["_cue"].get(k)
                if c0 is None: continue
                tt = sc["_t"] + c0
            cues.append((max(0, tt), snd, g))
    print("audio mix ...", flush=True)
    mixed = sfx.mix(total, vtrack, cues, music_gain=0.5)
    wav = os.path.join(WORK, "mix.wav"); sfx.write_wav(wav, mixed)
    audio = os.path.join(WORK, "audio.m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "aac", "-b:a", "192k", audio], check=True)
    data = {"scenes": scenes, "words": words, "total": total}
    html = open(os.path.join(HERE, "jordan_short.html"), encoding="utf-8").read().replace("__DATA__", json.dumps(data))
    open(page, "w", encoding="utf-8").write(html); json.dump({"total": total}, open(meta, "w"))
    print(f"total {total}s, {len(scenes)} scenes, {len(cues)} sfx", flush=True)
    from playwright.sync_api import sync_playwright
    nframes = int(total * FPS)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1208\chrome-win64\chrome.exe"))
        pg = b.new_page(viewport={"width": 720, "height": 1280}, device_scale_factor=1.5)
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("file:///" + page.replace("\\", "/"))
        pg.wait_for_function("window.ready !== undefined"); pg.evaluate("window.ready"); pg.wait_for_timeout(1500)
        if errs: print("PAGE ERRORS:", errs[:3], flush=True)
        if os.environ.get("PREVIEW"):
            for sc in scenes:
                for k, f in (("a", 0.4), ("b", 0.85)):
                    pg.evaluate(f"window.seek({sc['start'] + sc['dur'] * f:.3f})")
                    pg.screenshot(path=os.path.join(WORK, f"prev_{sc['id']}{k}.jpg"), type="jpeg", quality=82)
            b.close(); print("PREVIEW DONE", WORK, flush=True); return
        shutil.rmtree(FR, ignore_errors=True); os.makedirs(FR, exist_ok=True)
        for i in range(nframes):
            pg.evaluate(f"window.seek({i / FPS:.4f})")
            pg.screenshot(path=os.path.join(FR, f"f{i:05d}.jpg"), type="jpeg", quality=90)
            if i % 300 == 0: print(f"  frame {i}/{nframes}", flush=True)
        b.close()
    print("encoding ...", flush=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(FR, "f%05d.jpg"), "-i", audio, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", OUT], check=True)
    print("DONE", OUT, flush=True)


if __name__ == "__main__":
    main()
