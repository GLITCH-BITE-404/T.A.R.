#!/usr/bin/env python3
"""
T.A.R. wake word -- listens to the mic for "hey jarvis" (openWakeWord, runs
locally, ~1-2% CPU) and prints {"t": "wake"} when it hears it, so the UI can
start listening for the actual request.

  tar_wake.py            listen until killed (one JSON object per line)
  tar_wake.py --setup    download the model files (once, ~3 MB)
  tar_wake.py --check    {"t": "wake_status", "module": .., "models": ..}

The package ships no model files and its own folder is read-only (/usr/lib),
so the models live in T.A.R.'s data dir instead.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

DATA = os.environ.get("TAR_DATA", os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "bite-os", "tar"))
WAKE_DIR = os.path.join(DATA, "wakeword")
BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
FILES = {"wake": "hey_jarvis_v0.1.onnx", "melspec": "melspectrogram.onnx",
         "embedding": "embedding_model.onnx"}
RATE = 16000
CHUNK = 1280                    # 80 ms -- what openWakeWord expects per step
THRESHOLD = 0.5
COOLDOWN_S = 2.5


def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cfg():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def models_present():
    return all(os.path.exists(os.path.join(WAKE_DIR, f)) for f in FILES.values())


def module_present():
    try:
        import openwakeword  # noqa: F401
        return True
    except Exception:
        return False


def setup():
    os.makedirs(WAKE_DIR, exist_ok=True)
    for name in FILES.values():
        dest = os.path.join(WAKE_DIR, name)
        if os.path.exists(dest):
            continue
        emit("install_log", v="downloading " + name)
        for attempt in range(3):
            try:
                tmp = dest + ".part"
                urllib.request.urlretrieve(BASE + name, tmp)
                os.replace(tmp, dest)
                break
            except Exception as e:
                if attempt == 2:
                    emit("error", v="couldn't download %s: %s" % (name, e))
                    return 1
                time.sleep(2)
    emit("ok", v="wake word ready -- say \"hey jarvis\"")
    return 0


def listen():
    if not module_present():
        emit("error", v="openwakeword isn't installed (python-openwakeword)")
        return 1
    if not models_present() and setup() != 0:
        return 1
    import numpy as np
    from openwakeword.model import Model
    model = Model(wakeword_models=[os.path.join(WAKE_DIR, FILES["wake"])],
                  inference_framework="onnx",
                  melspec_model_path=os.path.join(WAKE_DIR, FILES["melspec"]),
                  embedding_model_path=os.path.join(WAKE_DIR, FILES["embedding"]))
    dev = (cfg().get("devices") or {}).get("source")
    cmd = ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16",
           "--latency", "40ms"] + (["--target", dev] if dev else []) + ["-"]
    rec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    emit("wake_ready", v="listening for \"hey jarvis\"")
    last = 0.0
    try:
        while True:
            buf = rec.stdout.read(CHUNK * 2)
            if not buf or len(buf) < CHUNK * 2:
                emit("error", v="microphone stream ended")
                return 1
            scores = model.predict(np.frombuffer(buf, dtype=np.int16))
            score = max(scores.values()) if scores else 0.0
            if score >= THRESHOLD and time.time() - last > COOLDOWN_S:
                last = time.time()
                model.reset()
                emit("wake", score=round(float(score), 2))
    finally:
        rec.terminate()


def main():
    if "--setup" in sys.argv:
        return setup()
    if "--check" in sys.argv:
        emit("wake_status", module=module_present(), models=models_present())
        return 0
    return listen()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
