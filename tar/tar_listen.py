#!/usr/bin/env python3
"""
T.A.R. speech-to-text.

    tar_listen.py [--seconds N] [--silence N]

Records from the microphone chosen in setup, stops on silence (or the ceiling),
transcribes with whisper.cpp, and emits the text. This is the piece that was
missing: the capability layer could detect and install an STT engine, but
nothing ever recorded or transcribed, so the mic was "ready" and still could
not understand a word.

Protocol, one JSON object per line:
    {"t":"listen","v":"recording"}
    {"t":"level","v":0.0-1.0}           live, so the UI can draw the waveform
    {"t":"listen","v":"transcribing"}
    {"t":"transcript","v":"...","ok":true}
"""

import array
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

RATE = 16000                 # whisper wants 16k mono
DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
STT_DIR = os.path.join(DATA, "stt")


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


def model_path():
    c = cfg()
    name = c.get("stt_model")
    if name:
        p = os.path.join(STT_DIR, "ggml-%s.bin" % name)
        if os.path.exists(p):
            return p
    if os.path.isdir(STT_DIR):
        for f in sorted(os.listdir(STT_DIR)):
            if f.startswith("ggml-") and f.endswith(".bin"):
                return os.path.join(STT_DIR, f)
    return None


def recorder(dev):
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


def _vol(dev):
    """Current volume percent for a source, or None."""
    try:
        out = subprocess.run(["pactl", "get-source-volume", dev],
                             capture_output=True, text=True, timeout=5).stdout
        import re as _re
        m = _re.search(r"(\d+)%", out)
        return int(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def ensure_gain(dev, target=100):
    """
    Make sure the chosen mic is actually loud enough to transcribe.

    This box ships its Digital Microphone at 48% / -19dB while the other mic
    sits at 100%. At that level whisper hears "(crickets chirping)" no matter
    what you say -- so the capability looked broken when it was only quiet.
    Returns the previous level so it can be put back.
    """
    if not dev:
        return None
    prev = _vol(dev)
    try:
        subprocess.run(["pactl", "set-source-mute", dev, "0"],
                       capture_output=True, timeout=5)
        if prev is not None and prev < 90:
            subprocess.run(["pactl", "set-source-volume", dev, "%d%%" % target],
                           capture_output=True, timeout=5)
            emit("listen", v="raised mic gain %d%% -> %d%%" % (prev, target))
            return prev
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def restore_gain(dev, prev):
    if dev and prev is not None:
        try:
            subprocess.run(["pactl", "set-source-volume", dev, "%d%%" % prev],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def record(max_s, silence_s):
    """Record until `silence_s` of quiet after speech, or `max_s` total."""
    c = cfg()
    dev = (c.get("devices") or {}).get("source")
    cmd = recorder(dev)
    if not cmd:
        emit("error", v="no recorder (need pw-record, parecord or arecord)")
        return None

    prev_gain = ensure_gain(dev)
    emit("listen", v="recording", device=dev or "default")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)

    frames = bytearray()
    chunk = RATE // 20                     # 50ms
    started = time.time()
    last_loud = None
    spoke = False
    try:
        while time.time() - started < max_s:
            raw = p.stdout.read(chunk * 2)
            if not raw:
                break
            frames += raw
            a = array.array("h")
            a.frombytes(raw[: (len(raw) // 2) * 2])
            if not a:
                continue
            rms = math.sqrt(sum(v * v for v in a) / len(a)) / 32768.0
            emit("level", v=round(min(1.0, rms * 6), 4))
            now = time.time()
            if rms > 0.02:
                spoke = True
                last_loud = now
            # stop once they've clearly stopped talking
            if spoke and last_loud and (now - last_loud) > silence_s:
                break
    except (OSError, KeyboardInterrupt):
        pass
    finally:
        try:
            p.terminate()
        except OSError:
            pass
        restore_gain(dev, prev_gain)

    if not spoke:
        emit("transcript", v="", ok=False, v_reason="no speech detected")
        return None
    return bytes(frames)


def transcribe(pcm):
    model = model_path()
    if not model:
        emit("error", v="no whisper model — install the mic engine in setup")
        return
    binary = None
    for c in ("whisper-cli", "whisper-cpp", "main"):
        if shutil.which(c):
            binary = c
            break
    if not binary:
        emit("error", v="whisper is not installed")
        return

    emit("listen", v="transcribing")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        path = tf.name
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        r = subprocess.run(
            [binary, "-m", model, "-f", path, "-nt", "--no-prints",
             "-t", str(min(8, (os.cpu_count() or 4)))],
            capture_output=True, text=True, timeout=180)
        text = " ".join((r.stdout or "").split()).strip()
        # Whisper narrates non-speech audio in brackets -- "[MUSIC PLAYING]",
        # "(crickets chirping)", "[BLANK_AUDIO]". Those are descriptions of the
        # room, not something the user said, so never treat them as a command.
        import re as _re
        stripped = _re.sub(r"^[\[\(].*[\]\)]$", "", text).strip()
        if not stripped or text.lower() in (
                "you", "thank you.", "thanks for watching!", "bye."):
            emit("transcript", v="", ok=False,
                 v_reason=("only picked up background audio (%s)" % text
                           if text else "could not make out any words"))
            return
        text = stripped
        if text:
            emit("transcript", v=text, ok=True)
        else:
            emit("transcript", v="", ok=False,
                 v_reason="could not make out any words")
    except subprocess.TimeoutExpired:
        emit("error", v="transcription timed out")
    except OSError as e:
        emit("error", v="transcription failed: %s" % e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    max_s = 15.0
    sil = 1.2
    if "--seconds" in sys.argv:
        max_s = float(sys.argv[sys.argv.index("--seconds") + 1])
    if "--silence" in sys.argv:
        sil = float(sys.argv[sys.argv.index("--silence") + 1])
    pcm = record(max_s, sil)
    if pcm:
        transcribe(pcm)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
