#!/usr/bin/env python3
"""
T.A.R. sound effects.

    tar_sfx.py <name> [volume 0..1]
    tar_sfx.py --list

Tones are synthesised, not shipped as files: no assets to install, no licence
questions, and they scale with the rice's theme rather than sounding like a
stock notification. Everything is a short blip -- an assistant that chimes for
half a second gets muted within a day.

Plays to the OUTPUT DEVICE chosen in setup, so a speaker test actually tests
the speaker you picked instead of whichever sink happens to be default.
"""

import array
import json
import math
import os
import shutil
import struct
import subprocess
import sys

RATE = 44100
DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)

# name -> list of (freq_hz, ms, shape). shape: "sine" | "tri" | "noise"
SFX = {
    "ping":    [(880, 60, "sine"), (1320, 70, "sine")],
    "ok":      [(660, 55, "sine"), (990, 85, "sine")],
    "done":    [(523, 60, "sine"), (784, 60, "sine"), (1047, 110, "sine")],
    "error":   [(220, 90, "tri"), (165, 140, "tri")],
    "open":    [(440, 45, "sine"), (660, 45, "sine"), (880, 60, "sine")],
    "close":   [(880, 45, "sine"), (660, 45, "sine"), (440, 60, "sine")],
    "boot":    [(330, 70, "sine"), (494, 70, "sine"), (659, 70, "sine"),
                (988, 150, "sine")],
    "listen":  [(1200, 40, "sine"), (1600, 55, "sine")],
    "act":     [(1046, 35, "sine"), (1568, 45, "sine")],
    "click":   [(1800, 18, "sine")],
    "warn":    [(400, 70, "tri"), (400, 70, "tri")],
    "sweep":   [(300, 220, "sweep")],
}


def sink():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("devices") or {}).get("sink")
    except (OSError, ValueError):
        return None


def render(spec, vol):
    buf = array.array("h")
    for freq, ms, shape in spec:
        n = int(RATE * ms / 1000.0)
        # short attack + exponential decay: a blip, never a beep
        atk = max(1, int(n * 0.12))
        for i in range(n):
            t = i / RATE
            if shape == "sweep":
                f = freq * (1.0 + 3.0 * (i / n))
            else:
                f = freq
            ph = 2 * math.pi * f * t
            if shape == "tri":
                s = 2 / math.pi * math.asin(math.sin(ph))
            else:
                s = math.sin(ph)
            env = (i / atk) if i < atk else math.exp(-4.0 * (i - atk) / n)
            buf.append(int(max(-1.0, min(1.0, s * env * vol)) * 20000))
    return buf.tobytes()


def play(pcm):
    dev = sink()
    # NOT pw-play: that is the libsndfile frontend and rejects raw PCM on
    # stdin ("Format not recognised"). pw-cat --raw is the one that takes it.
    if shutil.which("pw-cat"):
        cmd = ["pw-cat", "--playback", "--raw", "--rate", str(RATE),
               "--format", "s16", "--channels", "1"]
        if dev:
            cmd += ["--target", dev]
        cmd += ["-"]
    elif shutil.which("paplay"):
        cmd = ["paplay", "--raw", "--rate=%d" % RATE, "--format=s16le",
               "--channels=1"]
        if dev:
            cmd += ["--device=" + dev]
    elif shutil.which("aplay"):
        cmd = ["aplay", "-q", "-r", str(RATE), "-f", "S16_LE", "-c", "1",
               "-t", "raw"]
    else:
        return 127
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        p.communicate(pcm, timeout=15)
        return p.returncode or 0
    except (OSError, subprocess.SubprocessError):
        return 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--list", "-l"):
        print(json.dumps({"t": "sfx_list", "names": sorted(SFX)}))
        return 0
    name = sys.argv[1]
    vol = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6
    spec = SFX.get(name)
    if not spec:
        print(json.dumps({"t": "error", "v": "unknown sfx: " + name}))
        return 2
    rc = play(render(spec, max(0.0, min(1.0, vol))))
    print(json.dumps({"t": "sfx", "name": name, "ok": rc == 0}))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
