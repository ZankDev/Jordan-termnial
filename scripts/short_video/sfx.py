"""Synthesised sound design for the JORDAN shorts (no external samples): whoosh, riser, pop, tick, click, boom, ding, glitch,
plus an ambient music bed (pad chords, sub pulse, soft hats) that ducks under the narration. Everything is numpy at 48 kHz."""
from __future__ import annotations

import wave

import numpy as np

SR = 48000
rng = np.random.default_rng(7)


def _env(n: int, a: float, d: float, curve: float = 2.0) -> np.ndarray:
    t = np.linspace(0, 1, n)
    att = max(1, int(n * a))
    e = np.ones(n)
    e[:att] = np.linspace(0, 1, att) ** 0.6
    rel = t[att:]
    e[att:] = (1 - (rel - rel[0]) / max(1e-9, (1 - rel[0]))) ** (curve * d * 4 + 0.5)
    return e


def _lp(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """One-pole low-pass with a (possibly time-varying) cutoff in Hz."""
    if np.isscalar(cutoff):
        from scipy.signal import lfilter
        a1 = np.exp(-2 * np.pi * float(cutoff) / SR)
        return lfilter([1 - a1], [1, -a1], x)
    c = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    a = np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a[i]) * x[i] + a[i] * acc
        y[i] = acc
    return y


def whoosh(dur: float = 0.55, up: bool = True) -> np.ndarray:
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    sweep = np.geomspace(300, 5200, n) if up else np.geomspace(5200, 300, n)
    x = _lp(noise, sweep) - _lp(noise, sweep * 0.25)
    e = np.sin(np.linspace(0, np.pi, n)) ** 1.6
    return 0.9 * x * e / (np.abs(x).max() + 1e-9)


def riser(dur: float = 1.2) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = np.geomspace(180, 1400, n)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35 + np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR) * 0.15
    noise = _lp(rng.standard_normal(n), np.geomspace(500, 7000, n)) * 0.5
    e = (t / dur) ** 2.2
    return (tone + noise) * e


def pop(freq: float = 900) -> np.ndarray:
    n = int(SR * 0.12)
    t = np.arange(n) / SR
    f = freq * np.exp(-t * 30) + freq * 0.5
    x = np.sin(2 * np.pi * np.cumsum(f) / SR)
    return x * np.exp(-t * 38) * 0.8


def tick() -> np.ndarray:
    n = int(SR * 0.05)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 2400 * t) * 0.6 + rng.standard_normal(n) * 0.25
    return x * np.exp(-t * 120)


def click() -> np.ndarray:
    n = int(SR * 0.03)
    t = np.arange(n) / SR
    return (rng.standard_normal(n) * np.exp(-t * 300)) * 0.7


def boom(dur: float = 1.4) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 95 * np.exp(-t * 3.2) + 38
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6)
    hit = _lp(rng.standard_normal(n), 1800) * np.exp(-t * 22) * 0.9
    return np.tanh((body * 1.3 + hit) * 1.4) * 0.95


def ding() -> np.ndarray:
    n = int(SR * 1.1)
    t = np.arange(n) / SR
    x = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, a, d in ((1318.5, 0.5, 4.5), (1975.5, 0.3, 6), (2637, 0.18, 8), (987.8, 0.25, 3.5)))
    return x * 0.9


def glitch(dur: float = 0.35) -> np.ndarray:
    n = int(SR * dur)
    x = np.zeros(n)
    i = 0
    while i < n:
        seg = int(SR * rng.uniform(0.008, 0.04))
        kind = rng.integers(0, 3)
        t = np.arange(min(seg, n - i)) / SR
        if kind == 0:
            x[i:i + len(t)] = np.sign(np.sin(2 * np.pi * rng.uniform(200, 1800) * t)) * 0.35
        elif kind == 1:
            x[i:i + len(t)] = rng.standard_normal(len(t)) * 0.4
        i += seg
    return x


def swipe() -> np.ndarray:
    return whoosh(0.28, up=True) * 0.7


def music_bed(total: float, bpm: float = 96) -> np.ndarray:
    """Dark ambient bed: Am - F - C - G pad chords, sub pulse on the beat, airy hats on off-beats."""
    n = int(SR * total)
    t = np.arange(n) / SR
    beat = 60 / bpm
    bar = beat * 4
    chords = [(220.0, 261.6, 329.6), (174.6, 220.0, 261.6), (130.8, 196.0, 261.6), (196.0, 246.9, 293.7)]
    pad = np.zeros(n)
    for b in range(int(total / bar) + 1):
        a, z = int(b * bar * SR), min(n, int((b + 1) * bar * SR))
        if a >= n:
            break
        tt = t[a:z] - t[a]
        ch = chords[b % 4]
        seg = sum(np.sin(2 * np.pi * f * tt + 0.3 * np.sin(2 * np.pi * 0.25 * tt)) + 0.4 * np.sin(2 * np.pi * f * 2.003 * tt) for f in ch)
        e = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.6)
        pad[a:z] += seg * e
    pad = _lp(pad, 1100) * 0.11
    sub = np.zeros(n)
    hat = np.zeros(n)
    k = np.arange(int(SR * 0.35)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(60 * np.exp(-k * 9) + 42) / SR) * np.exp(-k * 7)
    hn = int(SR * 0.04)
    for i in range(int(total / beat) + 1):
        s = int(i * beat * SR)
        if s >= n:
            break
        m = min(len(kick), n - s)
        sub[s:s + m] += kick[:m] * 0.55
        h = int((i + 0.5) * beat * SR)
        if h + hn < n:
            hat[h:h + hn] += rng.standard_normal(hn) * np.exp(-np.arange(hn) / SR * 90) * 0.08
    hat = hat - _lp(hat, 6000)
    out = pad + sub * 0.5 + hat
    fade = np.minimum(1, t / 2.5) * np.minimum(1, (total - t) / 2.5)
    return out * fade


SOUNDS = {"whoosh": lambda: whoosh(0.55, True), "whoosh_dn": lambda: whoosh(0.5, False), "riser": riser, "pop": pop, "pop_hi": lambda: pop(1400), "tick": tick, "click": click,
          "boom": boom, "ding": ding, "glitch": glitch, "swipe": swipe}


def mix(total: float, voice: np.ndarray, cues: list[tuple[float, str, float]], music_gain: float = 0.55) -> np.ndarray:
    """voice: mono float at SR (already placed on the timeline). cues: (time, sound, gain). Music ducks under the voice."""
    n = int(SR * total)
    out = np.zeros(n)
    v = np.zeros(n)
    v[:min(n, len(voice))] = voice[:n]
    # duck: smoothed voice envelope
    env = np.abs(v)
    env = np.convolve(env, np.ones(int(SR * 0.08)) / int(SR * 0.08), mode="same")
    env = env / (env.max() + 1e-9)
    duck = 1 - 0.62 * np.clip(env * 3.5, 0, 1)
    out += music_bed(total) * duck * music_gain
    cache: dict[str, np.ndarray] = {}
    for t0, name, g in cues:
        if name not in cache:
            raw = SOUNDS[name]()
            cache[name] = raw / (np.abs(raw).max() + 1e-9)
        s = cache[name]
        a = int(t0 * SR)
        if a >= n or a < 0:
            continue
        m = min(len(s), n - a)
        out[a:a + m] += s[:m] * g * 0.5
    out += v * 1.0
    peak = np.abs(out).max()
    out = np.tanh(out / max(1e-9, peak) * 1.15) * 0.92   # soft limiter
    return out


def write_wav(path: str, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
