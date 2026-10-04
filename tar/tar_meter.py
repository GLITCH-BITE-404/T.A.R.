#!/usr/bin/env python3
"""
T.A.R. live microphone meter.

Streams the input level so the mic test can show real moving bars instead of
claiming "ready" and hoping. Emits one JSON line per frame:

    {"t":"level","v":0.0-1.0,"peak":0.0-1.0,"db":-60..0}

Records from the device chosen in setup (config.devices.source), because
"whatever is default" is exactly the bug this whole capability layer exists to
avoid. Reads raw s16 mono from pw-record/parecord/arecord, whichever exists.

Usage: tar_meter.py [--seconds N] [--fps N]
"""

import array
import json
import math
import os
import shutil
import subprocess
import sys
import time

DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
RATE = 16000


def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def source():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("devices") or {}).get("source")
    except (OSError, ValueError):
        return None


def recorder(dev):
    """Raw s16le mono @RATE on stdout, from the chosen device."""
    if shutil.which("pw-record"):
        cmd = ["pw-record", "--rate", str(RATE), "--channels", "1",
               "--format", "s16", "--latency", "20ms"]
        if dev:
            cmd += ["--target", dev]
        return cmd + ["-"]
    if shutil.which("parecord"):
        cmd = ["parecord", "--rate=%d" % RATE, "--channels=1",
               "--format=s16le", "--raw"]
        if dev:
            cmd += ["--device=" + dev]
        return cmd
    if shutil.which("arecord"):
        return ["arecord", "-q", "-r", str(RATE), "-c", "1", "-f", "S16_LE",
                "-t", "raw"]
    return None


def main():
    secs = 30.0
    fps = 24
    if "--seconds" in sys.argv:
        secs = float(sys.argv[sys.argv.index("--seconds") + 1])
    if "--fps" in sys.argv:
        fps = int(sys.argv[sys.argv.index("--fps") + 1])

    dev = source()
    cmd = recorder(dev)
    if not cmd:
        emit("error", v="no recorder found (need pw-record, parecord or arecord)")
        return 1

    chunk = max(160, int(RATE / fps))          # samples per frame
    emit("meter_start", device=dev or "default", rate=RATE)

    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    except OSError as e:
        emit("error", v="cannot start recorder: %s" % e)
        return 1

    started = time.time()
    peak_hold = 0.0
    heard = False
    try:
        while time.time() - started < secs:
            raw = p.stdout.read(chunk * 2)
            if not raw or len(raw) < 2:
                break
            a = array.array("h")
            a.frombytes(raw[: (len(raw) // 2) * 2])
            if not a:
                continue
            # RMS for the bar, absolute peak for the clip indicator
            acc = 0
            pk = 0
            for v in a:
                acc += v * v
                av = abs(v)
                if av > pk:
                    pk = av
            rms = math.sqrt(acc / len(a)) / 32768.0
            peak = pk / 32768.0
            peak_hold = max(peak_hold * 0.92, peak)
            db = 20 * math.log10(rms) if rms > 1e-6 else -60.0
            if peak > 0.04:
                heard = True
            emit("level", v=round(min(1.0, rms * 6), 4),
                 peak=round(min(1.0, peak_hold), 4),
                 db=round(max(-60.0, db), 1))
    except (OSError, KeyboardInterrupt):
        pass
    finally:
        try:
            p.terminate()
        except OSError:
            pass

    emit("meter_done", heard=heard,
         v=("heard you — mic works" if heard
            else "no sound detected — check the input device"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
