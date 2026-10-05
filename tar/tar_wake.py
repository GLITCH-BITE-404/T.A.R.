#!/usr/bin/env python3
"""
T.A.R. wake word -- runs locally (openWakeWord, ~1-2% CPU).

Two kinds of wake word:
  * built-in   "hey jarvis" / "alexa" / "hey mycroft" / "hey rhasspy"
  * my voice   ANY phrase, any language: you say it 3-4 times (--enroll) and
               T.A.R. matches how *you* sound saying it (few-shot templates on
               openWakeWord's speech embeddings, compared with DTW).

Quiet mics are boosted automatically (AGC) -- a mic at -19 dB never reached
the built-in model's threshold before.

  tar_wake.py                   listen until killed; prints {"t":"wake"}
  tar_wake.py --test            same, plus live level / score / "heard" text
  tar_wake.py --enroll [N]      record N samples of your phrase (default 4)
  tar_wake.py --setup [word]    download model files
  tar_wake.py --check           status JSON
  tar_wake.py --devices         microphones JSON

Config (config.json "wake"): {"engine": "builtin"|"voice", "word": "hey_jarvis",
  "phrase": "<what you say, for display>", "sensitivity": 0.5}
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

DATA = os.environ.get("TAR_DATA", os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "bite-os", "tar"))
WAKE_DIR = os.path.join(DATA, "wakeword")
VOICE_FILE = os.path.join(WAKE_DIR, "my-voice.json")
BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
BUILTIN = {"hey_jarvis": "hey_jarvis_v0.1.onnx", "alexa": "alexa_v0.1.onnx",
           "hey_mycroft": "hey_mycroft_v0.1.onnx", "hey_rhasspy": "hey_rhasspy_v0.1.onnx"}
FEATURES = {"melspec": "melspectrogram.onnx", "embedding": "embedding_model.onnx"}
RATE = 16000
CHUNK = 1280                    # 80 ms per step (one embedding frame)
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


def save_cfg(c):
    p = os.path.join(DATA, "config.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def wake_cfg():
    w = dict(cfg().get("wake") or {})
    w.setdefault("engine", "builtin")
    w.setdefault("word", "hey_jarvis")
    w.setdefault("sensitivity", 0.5)
    return w


def module_present():
    try:
        import openwakeword  # noqa: F401
        return True
    except Exception:
        return False


def _path(name):
    return os.path.join(WAKE_DIR, name)


def models_present(word=None):
    word = word or wake_cfg()["word"]
    need = list(FEATURES.values()) + ([BUILTIN[word]] if word in BUILTIN else [])
    return all(os.path.exists(_path(f)) for f in need)


def setup(word=None):
    os.makedirs(WAKE_DIR, exist_ok=True)
    word = word or wake_cfg()["word"]
    for name in list(FEATURES.values()) + ([BUILTIN[word]] if word in BUILTIN else []):
        if os.path.exists(_path(name)):
            continue
        emit("install_log", v="downloading " + name)
        for attempt in range(3):
            try:
                urllib.request.urlretrieve(BASE + name, _path(name) + ".part")
                os.replace(_path(name) + ".part", _path(name))
                break
            except Exception as e:
                if attempt == 2:
                    emit("error", v="couldn't download %s: %s" % (name, e))
                    return 1
                time.sleep(2)
    emit("ok", v="wake word files ready")
    return 0


def devices():
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                             capture_output=True, text=True, timeout=10).stdout
        rows = [s for s in json.loads(out) if ".monitor" not in s.get("name", "")]
    except Exception:
        rows = []
    cur = (cfg().get("devices") or {}).get("source")
    emit("wake_devices", rows=[{"id": s["name"], "name": s.get("description", s["name"]),
                                "current": s["name"] == cur} for s in rows])


def recorder():
    dev = (cfg().get("devices") or {}).get("source")
    cmd = ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16",
           "--latency", "40ms"] + (["--target", dev] if dev else []) + ["-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL), dev


class AGC:
    """Boost quiet mics so speech lands where the models expect it."""
    def __init__(self, target=3000.0, max_gain=12.0):
        self.target, self.max_gain, self.level = target, max_gain, 300.0

    def __call__(self, x):
        import numpy as np
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)) + 1e-6)
        if rms > 150:                       # only learn from actual sound
            self.level = 0.9 * self.level + 0.1 * rms
        gain = max(1.0, min(self.max_gain, self.target / max(self.level, 1.0)))
        y = np.clip(x.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
        return y, rms


def features():
    from openwakeword.utils import AudioFeatures
    return AudioFeatures(inference_framework="onnx",
                         melspec_model_path=_path(FEATURES["melspec"]),
                         embedding_model_path=_path(FEATURES["embedding"]))


# ---- "my voice": few-shot templates + DTW --------------------------------
def _dtw(a, b):
    """Normalized DTW distance between two (frames, 96) embedding sequences,
    using cosine distance per frame."""
    import numpy as np
    an = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    cost = 1.0 - an @ bn.T
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    return float(acc[n, m] / (n + m))


def load_voice():
    try:
        with open(VOICE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        import numpy as np
        d["templates"] = [np.array(t, dtype=np.float32) for t in d["templates"]]
        return d
    except Exception:
        return None


class Segmenter:
    """Energy VAD: yields complete speech segments (int16 arrays)."""
    def __init__(self, start_rms=500, end_ms=600, max_s=4.0):
        self.start_rms, self.end_n = start_rms, int(end_ms / 80)
        self.max_n = int(max_s * 1000 / 80)
        self.buf, self.quiet, self.active, self.pre = [], 0, False, []

    def push(self, chunk, boosted_rms):
        import numpy as np
        if not self.active:
            self.pre = (self.pre + [chunk])[-3:]        # keep a little lead-in
            if boosted_rms > self.start_rms:
                self.active, self.buf, self.quiet = True, list(self.pre), 0
            return None
        self.buf.append(chunk)
        self.quiet = self.quiet + 1 if boosted_rms < self.start_rms * 0.6 else 0
        if self.quiet >= self.end_n or len(self.buf) >= self.max_n:
            seg = np.concatenate(self.buf[:len(self.buf) - self.quiet + 2])
            self.active, self.buf, self.pre = False, [], []
            return seg if len(seg) >= RATE * 0.3 else None
        return None


def embed_segment(af, seg):
    """(frames, 96) embeddings for one speech segment."""
    import numpy as np
    pad = np.concatenate([np.zeros(RATE // 2, np.int16), seg, np.zeros(RATE // 4, np.int16)])
    emb = af._get_embeddings(pad)
    return np.asarray(emb, dtype=np.float32)


def enroll(n=4):
    if not module_present():
        emit("error", v="openwakeword isn't installed")
        return 1
    if setup("none") != 0:
        return 1
    import numpy as np
    af = features()
    agc = AGC()
    rec, dev = recorder()
    seg = Segmenter()
    templates, i = [], 0
    emit("enroll_prompt", n=1, of=n, v="say your wake phrase now (1/%d)" % n)
    t_end = time.time() + 25 * n
    try:
        while len(templates) < n and time.time() < t_end:
            buf = rec.stdout.read(CHUNK * 2)
            if not buf or len(buf) < CHUNK * 2:
                break
            x, rms = AGC.__call__(agc, np.frombuffer(buf, dtype=np.int16))
            brms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
            emit("level", v=min(1.0, brms / 8000.0))
            s = seg.push(x, brms)
            if s is not None:
                e = embed_segment(af, s)
                if len(e) < 3:
                    continue
                templates.append(e)
                wavp = _path("my-voice-%d.wav" % len(templates))
                with wave.open(wavp, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(s.tobytes())
                emit("enroll_got", n=len(templates), of=n, secs=round(len(s) / RATE, 1))
                if len(templates) < n:
                    emit("enroll_prompt", n=len(templates) + 1, of=n,
                         v="again (%d/%d)" % (len(templates) + 1, n))
    finally:
        rec.terminate()
    if len(templates) < 2:
        emit("error", v="didn't catch enough samples -- speak a bit louder and try again")
        return 1
    # threshold from how alike your own samples are to each other
    d = [_dtw(templates[a], templates[b]) for a in range(len(templates))
         for b in range(len(templates)) if a < b]
    thr = float(np.mean(d) + 1.5 * np.std(d) + 0.02)
    os.makedirs(WAKE_DIR, exist_ok=True)
    with open(VOICE_FILE, "w", encoding="utf-8") as f:
        json.dump({"templates": [t.tolist() for t in templates], "threshold": thr,
                   "made": time.time()}, f)
    c = cfg()
    w = dict(c.get("wake") or {})
    w["engine"] = "voice"
    c["wake"] = w
    save_cfg(c)
    emit("ok", v="learned your wake phrase from %d samples -- it's on" % len(templates),
         threshold=round(thr, 3))
    return 0


# ---- the listener ------------------------------------------------------------
def listen(test=False):
    if not module_present():
        emit("error", v="openwakeword isn't installed (python-openwakeword)")
        return 1
    w = wake_cfg()
    engine = w["engine"]
    voice = load_voice() if engine == "voice" else None
    if engine == "voice" and not voice:
        emit("error", v="no voice samples yet -- use TRAIN MY VOICE first")
        return 1
    if not models_present(w["word"] if engine == "builtin" else "none") and \
            setup(w["word"] if engine == "builtin" else "none") != 0:
        return 1
    import numpy as np
    sens = float(w.get("sensitivity") or 0.5)          # 0.1 strict .. 0.9 eager
    model = None
    if engine == "builtin":
        from openwakeword.model import Model
        model = Model(wakeword_models=[_path(BUILTIN.get(w["word"], BUILTIN["hey_jarvis"]))],
                      inference_framework="onnx",
                      melspec_model_path=_path(FEATURES["melspec"]),
                      embedding_model_path=_path(FEATURES["embedding"]))
        threshold = max(0.15, 1.0 - sens)              # sens .5 -> .5, .8 -> .2
    else:
        af = features()
        threshold = voice["threshold"] * (0.7 + 0.6 * sens)   # eager -> looser match
    agc = AGC()
    seg = Segmenter()
    rec, dev = recorder()
    emit("wake_ready", v="listening for %s" % (
        ("\"%s\"" % w["word"].replace("_", " ")) if engine == "builtin"
        else ("your phrase" + (" (\"%s\")" % w["phrase"] if w.get("phrase") else ""))),
        device=dev or "default", engine=engine)
    last, last_level = 0.0, 0.0
    try:
        while True:
            buf = rec.stdout.read(CHUNK * 2)
            if not buf or len(buf) < CHUNK * 2:
                emit("error", v="microphone stream ended")
                return 1
            x, raw_rms = agc(np.frombuffer(buf, dtype=np.int16))
            brms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
            hit, score = False, 0.0
            if model is not None:
                scores = model.predict(x)
                score = float(max(scores.values())) if scores else 0.0
                hit = score >= threshold
            s = seg.push(x, brms)
            if s is not None:
                if engine == "voice":
                    e = embed_segment(af, s)
                    dist = min(_dtw(e, t) for t in voice["templates"]) if len(e) >= 3 else 9
                    score = max(0.0, 1.0 - dist / max(threshold, 1e-6) * 0.5)
                    hit = dist <= threshold
                    if test:
                        emit("wake_score", v=round(score, 2), dist=round(dist, 3),
                             need=round(threshold, 3))
                if test:
                    _transcribe_async(s)
            if test:
                now = time.time()
                if now - last_level > 0.08:
                    last_level = now
                    emit("level", v=min(1.0, brms / 8000.0), raw=round(raw_rms),
                         score=round(score, 2) if engine == "builtin" else None)
            if hit and time.time() - last > COOLDOWN_S:
                last = time.time()
                if model is not None:
                    model.reset()
                emit("wake", score=round(score, 2))
    finally:
        rec.terminate()


def _transcribe_async(seg):
    """Test mode: show what was heard (cloud if there's a key, else whisper)."""
    import threading

    def run():
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                p = tf.name
            with wave.open(p, "wb") as wv:
                wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(RATE); wv.writeframes(seg.tobytes())
            text = ""
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import tar_cloud
                tar_cloud.emit = lambda *a, **k: None
                if tar_cloud.api_key("google"):
                    text = tar_cloud.transcribe_wav(p)
            except Exception:
                text = ""
            emit("heard", v=text or "(couldn't make out words)")
            os.unlink(p)
        except Exception as e:
            emit("heard", v="(transcription failed: %s)" % e)
    threading.Thread(target=run, daemon=True).start()


def main():
    a = sys.argv[1:]
    if "--setup" in a:
        i = a.index("--setup")
        return setup(a[i + 1] if len(a) > i + 1 else None)
    if "--check" in a:
        w = wake_cfg()
        emit("wake_status", module=module_present(), models=models_present(),
             engine=w["engine"], word=w["word"], voice=os.path.exists(VOICE_FILE),
             sensitivity=w["sensitivity"])
        return 0
    if "--set" in a:
        # --set word=alexa  sensitivity=0.7  engine=builtin|voice  phrase="..."
        c = cfg()
        w = dict(c.get("wake") or {})
        msgs = []
        for kv in a[a.index("--set") + 1:]:
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            if k == "word" and v in BUILTIN:
                w["word"], w["engine"] = v, "builtin"
                msgs.append("wake word: \"%s\"" % v.replace("_", " "))
            elif k == "sensitivity":
                try:
                    w["sensitivity"] = max(0.1, min(0.9, float(v)))
                    msgs.append("sensitivity %.1f" % w["sensitivity"])
                except ValueError:
                    pass
            elif k == "engine" and v in ("builtin", "voice"):
                if v == "voice" and not os.path.exists(VOICE_FILE):
                    emit("error", v="train your voice first")
                    return 1
                w["engine"] = v
                msgs.append("using " + ("your trained voice" if v == "voice" else "the built-in word"))
            elif k == "phrase":
                w["phrase"] = v
        c["wake"] = w
        save_cfg(c)
        if w.get("engine") == "builtin":
            setup(w.get("word"))
        emit("ok", v=", ".join(msgs) or "saved", wake=w)
        return 0
    if "--devices" in a:
        devices()
        return 0
    if "--enroll" in a:
        i = a.index("--enroll")
        n = int(a[i + 1]) if len(a) > i + 1 and a[i + 1].isdigit() else 4
        return enroll(n)
    return listen(test="--test" in a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
