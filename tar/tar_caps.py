#!/usr/bin/env python3
"""
T.A.R. capability layer.

Answers three questions the UI needs and the brain can't answer on its own:

  1. what can T.A.R. actually DO on this box right now  (`caps`)
  2. what would I have to install to unlock the rest    (`engines <cap>`)
  3. which speaker / mic / camera should it use          (`devices`, `set-device`)

Same one-JSON-object-per-line protocol as tar_brain.py, so tar.qml's existing
handleLine() can read it with no new parser.

Privilege note: pacman/yay need root. We do NOT try to smuggle a password out of
the UI. Anything needing root is handed to a visible terminal window the user
can type into; everything that is a plain download (piper voices, whisper ggml
models, ollama pulls) streams inline with real progress.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINES = os.path.join(HERE, "engines.json")

DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
CONFIG_PATH = os.path.join(DATA, "config.json")
VOICE_DIR = os.path.join(DATA, "voices")
STT_DIR = os.path.join(DATA, "stt")

OLLAMA = os.environ.get("TAR_OLLAMA", "http://127.0.0.1:11434")


# ----------------------------------------------------------------- line protocol

def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def die(msg, **kw):
    emit("error", v=msg, **kw)
    sys.exit(1)


# ----------------------------------------------------------------- storage

def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def registry():
    reg = _load(ENGINES, None)
    if not reg:
        die("engines.json missing or unreadable at " + ENGINES)
    return reg


def config():
    cfg = _load(CONFIG_PATH, {})
    cfg.setdefault("engines", {})     # cap -> engine key the user picked
    cfg.setdefault("devices", {})     # "sink"/"source"/"video" -> id
    cfg.setdefault("voice", None)     # piper voice name
    cfg.setdefault("stt_model", None)  # whisper ggml name
    cfg.setdefault("speak", False)    # talk out loud by default?
    # How T.A.R. should appear on launch:
    #   "window"     - open straight as a normal tiled window (fastest)
    #   "cinematic"  - fullscreen for the intro, then settle into a window
    #   "floating"   - fullscreen for the intro, then a floating window
    cfg.setdefault("start_mode", "cinematic")
    # What T.A.R. should switch on by itself when it launches. Everything here
    # is off by default: an assistant that starts talking, or starts listening,
    # without being asked is the fastest way to get itself disabled.
    cfg.setdefault("autostart", {})
    cfg["autostart"].setdefault("speak", False)       # talk replies out loud
    cfg["autostart"].setdefault("mic", False)         # mic hot on launch
    cfg["autostart"].setdefault("wake", False)        # wake-word listening
    cfg["autostart"].setdefault("warm", True)         # preload the model
    cfg["autostart"].setdefault("sfx", True)          # UI sounds
    cfg["autostart"].setdefault("greet", False)       # say hello on launch
    return cfg


def save_config(cfg):
    _save(CONFIG_PATH, cfg)


# ----------------------------------------------------------------- detection

VENV = os.path.join(DATA, "venv")


def _venv_python():
    return os.path.join(VENV, "bin", "python")


def _venv_has(module):
    """Is `module` importable from T.A.R.'s venv?"""
    py = _venv_python()
    if not os.path.exists(py):
        return False
    try:
        r = subprocess.run([py, "-c", "import %s" % module],
                           capture_output=True, timeout=25)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _has_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        with open(os.path.join(DATA, "keys.json"), encoding="utf-8") as f:
            return bool(json.load(f).get("anthropic"))
    except (OSError, ValueError):
        return False


def _google_key():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tar_cloud
        return tar_cloud.api_key("google")
    except Exception:
        return ""


def _cloud_status():
    """(ready, reason) for the selected cloud model -- Gemini needs no SDK."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tar_cloud
        return tar_cloud.status()
    except Exception as e:
        return False, "cloud backend error: %s" % e


def _pip_install(pkgs, cap):
    """
    Install into a dedicated venv. Arch marks the system python
    externally-managed and pip refuses to touch it, and shipping a distro that
    pip-installs into /usr would be wrong anyway.
    """
    py = _venv_python()
    if not os.path.exists(py):
        emit("install_log", cap=cap, v="creating T.A.R.'s python venv…")
        rc = _stream([sys.executable, "-m", "venv", VENV], cap)
        if rc != 0 or not os.path.exists(py):
            return rc or 1
    emit("install_log", cap=cap, v="installing " + " ".join(pkgs))
    return _stream([py, "-m", "pip", "install", "--upgrade"] + list(pkgs), cap)


def have(binname):
    """
    Accept a list of candidate names. Packages do not reliably name their
    binary after themselves: piper-tts-bin ships /usr/bin/piper-tts, not
    /usr/bin/piper, so looking only for "piper" meant the engine could never be
    detected no matter how many times it was installed.
    """
    if not binname:
        return None
    names = binname if isinstance(binname, (list, tuple)) else [binname]
    for n in names:
        if n and shutil.which(n):
            return n
        # tools built for the user (aur-user installs) live in ~/.local/bin,
        # which isn't always on the PATH T.A.R. is launched with
        if n and os.access(os.path.expanduser("~/.local/bin/" + n), os.X_OK):
            return n
    return None


def ollama_models():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=4) as r:
            d = json.loads(r.read().decode())
        return {m["name"] for m in d.get("models", [])} | \
               {m["name"].split(":")[0] for m in d.get("models", [])}
    except Exception:
        pass
    # ollama isn't running (it no longer starts at boot): read what's on disk
    found = set()
    base = os.path.expanduser("~/.ollama/models/manifests")
    for root, dirs, files in os.walk(base):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), base).split(os.sep)
            if len(rel) >= 3:
                name = rel[-2] if rel[-3] == "library" else "/".join(rel[-3:-1])
                found |= {name, "%s:%s" % (name, rel[-1])}
    return found


def _sys_module(name):
    """Importable by the SYSTEM python (pacman python-* packages land there)."""
    try:
        return subprocess.run(["python3", "-c", "import " + name], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def voice_files():
    """Installed piper voices, by name."""
    out = {}
    if os.path.isdir(VOICE_DIR):
        for f in os.listdir(VOICE_DIR):
            if f.endswith(".onnx"):
                out[f[:-5]] = os.path.join(VOICE_DIR, f)
    return out


def stt_models():
    out = {}
    if os.path.isdir(STT_DIR):
        for f in os.listdir(STT_DIR):
            m = re.match(r"ggml-(.+)\.bin$", f)
            if m:
                out[m.group(1)] = os.path.join(STT_DIR, f)
    return out


def engine_state(cap, key, spec, have_ollama):
    """Is this one engine installed, and is it fully usable?"""
    st = {
        "engine": key,
        "desc": spec.get("desc", ""),
        "quality": spec.get("quality", ""),
        "size_mb": spec.get("size_mb", 0),
        "recommended": bool(spec.get("recommended")),
        "source": spec.get("source", "repo"),
    }
    if spec.get("cloud_vision"):
        st["kind"] = "cloud"
        st["ref"] = "gemini"
        st["pkg"] = None
        st["installed"] = _cloud_status()[0] or bool(_google_key())
        st["ready"] = st["installed"]
        st["missing_asset"] = None if st["ready"] else "gemini key"
        return st

    if spec.get("sys_module"):
        st["kind"] = "python"
        st["ref"] = spec["sys_module"]
        st["pkg"] = spec.get("pkg")
        st["installed"] = _sys_module(spec["sys_module"])
        st["ready"] = st["installed"]
        if st["installed"] and spec.get("needs_wake_models"):
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import tar_wake
            st["ready"] = tar_wake.models_present()
            st["missing_asset"] = None if st["ready"] else "wake model (~3 MB)"
        return st

    if spec.get("python_module"):
        # Not a binary: the cloud SDK lives in T.A.R.'s own venv.
        st["kind"] = "python"
        st["ref"] = spec["python_module"]
        st["pkg"] = spec.get("pkg")
        st["installed"] = _venv_has(spec["python_module"])
        st["ready"] = st["installed"]
        if st["installed"] and spec.get("needs_key"):
            st["ready"] = _has_key()
            st["missing_asset"] = None if st["ready"] else "api key"
        return st

    if spec.get("ollama_model"):
        st["kind"] = "model"
        st["ref"] = spec["ollama_model"]
        st["installed"] = spec["ollama_model"] in have_ollama
        st["ready"] = st["installed"]
        st["pkg"] = None
    else:
        st["kind"] = "package"
        st["ref"] = (spec.get("bin")[0]
                     if isinstance(spec.get("bin"), list) else spec.get("bin"))
        st["pkg"] = spec.get("pkg")
        found = have(spec.get("bin", ""))
        st["installed"] = found is not None
        st["bin"] = found
        st["ready"] = st["installed"]
        # a binary alone isn't enough for these two
        if st["installed"] and spec.get("needs_voice"):
            st["ready"] = bool(voice_files())
            st["missing_asset"] = None if st["ready"] else "voice"
        if st["installed"] and spec.get("needs_model"):
            st["ready"] = bool(stt_models())
            st["missing_asset"] = None if st["ready"] else "model"
    return st


def caps_status():
    reg = registry()
    cfg = config()
    have_ollama = ollama_models()
    caps = {}

    for cap, cspec in reg["capabilities"].items():
        rows = [engine_state(cap, k, s, have_ollama)
                for k, s in cspec["engines"].items()]
        ready = [r for r in rows if r["ready"]]
        installed = [r for r in rows if r["installed"]]

        chosen = cfg["engines"].get(cap)
        if chosen and not any(r["engine"] == chosen and r["ready"] for r in rows):
            chosen = None          # picked engine went away / never finished
        if not chosen and ready:
            # prefer the recommended one, else best quality we have
            order = {"neural": 4, "good": 3, "ok": 2, "basic": 1, "robotic": 0}
            chosen = sorted(
                ready,
                key=lambda r: (r["recommended"], order.get(r["quality"], 0)),
                reverse=True,
            )[0]["engine"]

        # a capability can also be gated behind another one
        unmet = [d for d in cspec.get("requires", [])
                 if not caps.get(d, {}).get("ready")]

        caps[cap] = {
            "label": cspec["label"],
            "blurb": cspec["blurb"],
            "icon": cspec.get("icon", ""),
            "device_kind": cspec.get("device_kind"),
            "engines": rows,
            "ready": bool(ready) and not unmet,
            "locked": not bool(ready) or bool(unmet),
            "any_installed": bool(installed),
            "chosen": chosen,
            "requires_unmet": unmet,
            # what the wall should say
            "wall": (
                ("needs " + ", ".join(caps.get(d, {}).get("label", d)
                                      for d in unmet)
                 if unmet else "no engine installed")
                if (not ready or unmet) else None
            ),
        }

    return {
        "caps": caps,
        "voice": cfg.get("voice"),
        "voices_installed": sorted(voice_files()),
        "stt_model": cfg.get("stt_model"),
        "stt_models_installed": sorted(stt_models()),
        "speak": bool(cfg.get("speak")),
        "devices_chosen": cfg.get("devices", {}),
        "start_mode": cfg.get("start_mode", "cinematic"),
        "backend": cfg.get("backend", "local"),
        # What will ACTUALLY answer. Configured != available: selecting cloud
        # without the SDK or a key silently ran the local model while the UI
        # still said CLOUD BRAIN, which is worse than refusing.
        "backend_effective": (
            "claude" if (cfg.get("backend") == "claude" and _cloud_status()[0])
            else "local"),
        "cloud_blocked": (
            None if cfg.get("backend") != "claude" else _cloud_status()[1]),
        "cloud_model": cfg.get("cloud_model", "claude-opus-5"),
        "autostart": cfg.get("autostart", {}),
    }


# ----------------------------------------------------------------- devices

def _pactl(args):
    try:
        out = subprocess.run(["pactl"] + args, capture_output=True,
                             text=True, timeout=6)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_DESC_CACHE = {}


def _descriptions():
    """Real device descriptions from pactl, keyed by node name."""
    if _DESC_CACHE:
        return _DESC_CACHE
    for kind in ("sinks", "sources"):
        name = None
        for line in _pactl(["list", kind]).splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Description:") and name:
                _DESC_CACHE[name] = line.split(":", 1)[1].strip()
                name = None
    return _DESC_CACHE


def _pretty_sink(name):
    """
    Human name for a node. "Mic1"/"Mic2" told you nothing about which physical
    microphone you were picking, so prefer PulseAudio's own Description
    ("Digital Microphone", "Headphones") and keep the HiFi token as a suffix to
    disambiguate two ports on the same card.
    """
    if not name:
        return ""
    desc = _descriptions().get(name)
    port = re.search(r"HiFi__([A-Za-z0-9]+)__", name)
    if desc:
        if port and port.group(1).lower() not in desc.lower():
            return "%s (%s)" % (desc, port.group(1))
        return desc
    if port:
        return port.group(1)
    m = re.search(r"\.([A-Za-z0-9_-]+)$", name)
    return (m.group(1) if m else name).replace("_", " ")


def devices():
    """
    Real, selectable audio + video devices.

    Monitor sources are flagged rather than dropped: they're the right pick if
    you ever want T.A.R. to listen to what the machine is playing, and the wrong
    pick for a mic -- so the UI can grey them out instead of pretending they
    don't exist.
    """
    cfg = config()
    sinks, sources, cams = [], [], []

    for line in _pactl(["list", "short", "sinks"]).splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            sinks.append({"id": p[1], "name": _pretty_sink(p[1]),
                          "hdmi": "HDMI" in p[1],
                          "state": p[-1].lower() if len(p) > 4 else ""})

    for line in _pactl(["list", "short", "sources"]).splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            is_mon = p[1].endswith(".monitor")
            sources.append({"id": p[1], "name": _pretty_sink(p[1]),
                            "monitor": is_mon,
                            "state": p[-1].lower() if len(p) > 4 else ""})

    info = _pactl(["info"])
    def_sink = def_source = None
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            def_sink = line.split(":", 1)[1].strip()
        elif line.startswith("Default Source:"):
            def_source = line.split(":", 1)[1].strip()

    for dev in sorted(f for f in os.listdir("/dev") if f.startswith("video")):
        path = "/dev/" + dev
        label = path
        try:
            out = subprocess.run(["v4l2-ctl", "-d", path, "--all"],
                                 capture_output=True, text=True, timeout=4)
            m = re.search(r"Card type\s*:\s*(.+)", out.stdout)
            caps_m = re.search(r"Device Caps.*?\n((?:\s+\w+.*\n)+)", out.stdout)
            capture = "Video Capture" in (caps_m.group(1) if caps_m else "")
            if m:
                label = "%s (%s)" % (m.group(1).strip(), dev)
        except (OSError, subprocess.SubprocessError):
            capture = True
        else:
            if not capture:
                continue        # metadata/output nodes aren't cameras
        cams.append({"id": path, "name": label})

    return {
        "sinks": sinks, "sources": sources, "cameras": cams,
        "default_sink": def_sink, "default_source": def_source,
        "chosen": cfg.get("devices", {}),
        # what will actually get used, after falling back to the system default
        "effective": {
            "sink": cfg["devices"].get("sink") or def_sink,
            "source": cfg["devices"].get("source") or def_source,
            "video": cfg["devices"].get("video") or (cams[0]["id"] if cams else None),
        },
    }


# ----------------------------------------------------------------- install

def _terminal_cmd(argv, title, marker=None):
    """
    Wrap a privileged command in a visible terminal so sudo can prompt.

    On success the window closes itself -- you asked for the password, it
    worked, there is nothing to read. On failure it stays open with the error
    still on screen, because that is the one case you need to see. Either way
    the exit code lands in `marker` so the caller can report the real result
    instead of guessing.
    """
    inner = " ".join(_shq(a) for a in argv)
    mk = _shq(marker) if marker else "/dev/null"
    script = (
        "printf '\\033]0;%s\\007'; " % title +
        "echo '── T.A.R. is installing: %s'; echo; " % title +
        inner + "; rc=$?; echo; "
        "printf '%s' \"$rc\" > " + mk + "; "
        "if [ $rc -eq 0 ]; then echo '✓ done'; sleep 1; "
        "else echo \"✗ failed (exit $rc)\"; echo; "
        "echo 'left open so you can read the error — press enter to close'; "
        "read _; fi"
    )
    for term, flag in (("kitty", "-e"), ("foot", "-e"),
                       ("alacritty", "-e"), ("xterm", "-e")):
        if have(term):
            return [term, flag, "bash", "-lc", script]
    return None


def _shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _sudo_is_free():
    """True if sudo would not prompt (NOPASSWD or a warm timestamp)."""
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _stream(argv, tag):
    """Run argv, forwarding each output line to the UI. Returns exit code."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as e:
        emit("install_log", cap=tag, v="cannot run %s: %s" % (argv[0], e))
        return 127
    for line in p.stdout:
        line = line.rstrip()
        if line:
            emit("install_log", cap=tag, v=line[:400])
    return p.wait()


def download(url, dest, tag, label):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    emit("install_log", cap=tag, v="fetching " + label)
    try:
        with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            step = max(1, total // 20) if total else 1 << 21
            nxt = step
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got >= nxt:
                    nxt += step
                    if total:
                        emit("install_progress", cap=tag,
                             pct=int(got * 100 / total), label=label)
                    else:
                        emit("install_log", cap=tag,
                             v="%.1f MB" % (got / 1e6))
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        emit("install_log", cap=tag, v="download failed: %s" % e)
        return False
    os.replace(tmp, dest)
    emit("install_progress", cap=tag, pct=100, label=label)
    return True


def pull_ollama(model, tag):
    """Stream an ollama pull's real percentage."""
    req = urllib.request.Request(
        OLLAMA + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"})
    last = -1
    try:
        with urllib.request.urlopen(req, timeout=None) as r:
            for raw in r:
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw.decode())
                except ValueError:
                    continue
                if d.get("error"):
                    emit("install_log", cap=tag, v=str(d["error"]))
                    return False
                tot, done = d.get("total"), d.get("completed")
                if tot and done:
                    pct = int(done * 100 / tot)
                    if pct != last:
                        last = pct
                        emit("install_progress", cap=tag, pct=pct,
                             label=d.get("status", model))
                elif d.get("status"):
                    emit("install_log", cap=tag, v=d["status"])
    except Exception as e:
        emit("install_log", cap=tag, v="pull failed: %s" % e)
        return False
    return True


def install(cap, engine, want_voice=None, want_model=None):
    reg = registry()
    cspec = reg["capabilities"].get(cap) or die("unknown capability: " + cap)
    spec = cspec["engines"].get(engine) or die(
        "unknown engine '%s' for %s" % (engine, cap))

    emit("install_start", cap=cap, engine=engine,
         label=spec.get("desc", engine), size_mb=spec.get("size_mb", 0))

    # ---- vision engines are just ollama models
    if spec.get("ollama_model"):
        if not pull_ollama(spec["ollama_model"], cap):
            emit("install_done", cap=cap, engine=engine, ok=False,
                 v="could not pull " + spec["ollama_model"])
            return 1
        _choose(cap, engine)
        emit("install_done", cap=cap, engine=engine, ok=True,
             v=spec["ollama_model"] + " ready")
        return 0

    # ---- python (venv) engines
    if spec.get("python_module"):
        if not _venv_has(spec["python_module"]):
            rc = _pip_install(spec["pkg"].split(), cap)
            if rc != 0:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="pip install failed (exit %d)" % rc)
                return 1
        _choose(cap, engine)
        if spec.get("needs_key") and not _has_key():
            emit("install_done", cap=cap, engine=engine, ok=True,
                 v="SDK installed — now add your API key to finish")
        else:
            emit("install_done", cap=cap, engine=engine, ok=True,
                 v=cspec["label"] + " ready")
        return 0

    # ---- wake word: package present -> fetch its model files (no sudo)
    if spec.get("sys_module") and _sys_module(spec["sys_module"]):
        if spec.get("needs_wake_models"):
            rc = _stream([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "tar_wake.py"), "--setup"], cap)
            if rc != 0:
                emit("install_done", cap=cap, engine=engine, ok=False, v="model download failed")
                return 1
        _choose(cap, engine)
        emit("install_done", cap=cap, engine=engine, ok=True, v=cspec["label"] + " ready")
        return 0

    # ---- cloud vision needs nothing installed
    if spec.get("cloud_vision"):
        _choose(cap, engine)
        emit("install_done", cap=cap, engine=engine, ok=True, v="Gemini vision ready")
        return 0

    # ---- user-built AUR tools: no sudo, lands in ~/.local/bin
    if spec.get("source") == "aur-user":
        if not have(spec["bin"]):
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "bin", "aur-user-install")
            b = spec["bin"] if isinstance(spec["bin"], str) else spec["bin"][0]
            rc = _stream([script, spec["pkg"], b], cap)
            if rc != 0 or not have(spec["bin"]):
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="building %s failed (exit %s) -- see the log above" % (spec["pkg"], rc))
                return 1
        _choose(cap, engine)
        emit("install_done", cap=cap, engine=engine, ok=True, v=cspec["label"] + " ready")
        return 0

    # ---- package engines
    if not have(spec["bin"]):
        pkgs = spec["pkg"].split()
        if spec.get("source") == "aur":
            helper = "yay" if have("yay") else ("paru" if have("paru") else None)
            if not helper:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="%s is an AUR package and no yay/paru found" % spec["pkg"])
                return 1
            # -Syu, not -S: a stale sync DB makes pacman ask the mirror for a
            # package version that has already been replaced, and the .sig 404s
            # ("failed retrieving ... .pkg.tar.zst.sig"). Refreshing without
            # upgrading would be a partial upgrade, which Arch explicitly does
            # not support, so we do the full thing.
            argv = [helper, "-Syu", "--needed", "--noconfirm"] + pkgs
        else:
            argv = ["sudo", "pacman", "-Syu", "--needed"] + pkgs

        if _sudo_is_free():
            rc = _stream(argv, cap)
        else:
            marker = os.path.join(DATA, "install-%s.rc" % cap)
            try:
                os.makedirs(DATA, exist_ok=True)
                if os.path.exists(marker):
                    os.unlink(marker)
            except OSError:
                pass
            term = _terminal_cmd(argv, spec["pkg"], marker)
            if not term:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="need root and found no terminal to ask in; run: "
                       + " ".join(argv))
                return 1
            emit("install_log", cap=cap,
                 v="opening a terminal for the password prompt…")
            emit("install_needs_terminal", cap=cap, cmd=" ".join(argv))
            try:
                subprocess.Popen(term, start_new_session=True)
            except OSError as e:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="could not open a terminal: %s" % e)
                return 1
            # Wait for the terminal to finish rather than shrugging. The UI
            # shows this capability as "installing" for the whole wait, so it
            # is obvious something is running and there is nothing to spam.
            rc = None
            waited = 0.0
            while waited < 900:          # 15 min ceiling
                if os.path.exists(marker):
                    try:
                        with open(marker, encoding="utf-8") as f:
                            rc = int((f.read() or "1").strip() or 1)
                        os.unlink(marker)
                    except (OSError, ValueError):
                        rc = 1
                    break
                time.sleep(0.5)
                waited += 0.5
                if int(waited) % 5 == 0:
                    emit("install_log", cap=cap, v="waiting for the installer…")
            if rc is None:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="installer timed out")
                return 1
            if rc != 0:
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="%s failed (exit %d) — the terminal has the error"
                       % (spec["pkg"], rc))
                return 1
        if rc != 0:
            emit("install_done", cap=cap, engine=engine, ok=False,
                 v="package install failed (exit %d)" % rc)
            return 1

    # ---- second-stage assets
    if spec.get("needs_voice"):
        name = want_voice or _recommended(reg["voices"])
        v = reg["voices"].get(name) or die("unknown voice: " + str(name))
        onnx = os.path.join(VOICE_DIR, name + ".onnx")
        if not os.path.exists(onnx):
            if not download(v["url"], onnx, cap, name):
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="voice download failed")
                return 1
            download(v["config_url"], onnx + ".json", cap, name + " config")
        cfg = config()
        cfg["voice"] = name
        save_config(cfg)

    if spec.get("needs_model"):
        name = want_model or _recommended(reg["stt_models"])
        m = reg["stt_models"].get(name) or die("unknown stt model: " + str(name))
        dest = os.path.join(STT_DIR, "ggml-%s.bin" % name)
        if not os.path.exists(dest):
            if not download(m["url"], dest, cap, "whisper " + name):
                emit("install_done", cap=cap, engine=engine, ok=False,
                     v="model download failed")
                return 1
        cfg = config()
        cfg["stt_model"] = name
        save_config(cfg)

    _choose(cap, engine)
    emit("install_done", cap=cap, engine=engine, ok=True,
         v=cspec["label"] + " ready")
    return 0


def _recommended(table):
    for k, v in table.items():
        if k.startswith("_"):
            continue
        if v.get("recommended"):
            return k
    for k in table:
        if not k.startswith("_"):
            return k
    return None


def _choose(cap, engine):
    cfg = config()
    cfg["engines"][cap] = engine
    if cap == "tts":
        cfg["speak"] = True     # you installed a voice; you want it used
    save_config(cfg)


# ----------------------------------------------------------------- connect

def connect(cap):
    """
    The 'Connect' button. Don't install anything -- find what's ALREADY on the
    box that can serve this capability, wire the best of it up, and say what it
    picked. Also auto-selects a sane device, which is the whole point of not
    grabbing a random HDMI sink.
    """
    st = caps_status()["caps"].get(cap) or die("unknown capability: " + cap)
    ready = [e for e in st["engines"] if e["ready"]]
    part = [e for e in st["engines"] if e["installed"] and not e["ready"]]

    if not ready and not part:
        emit("connect_done", cap=cap, ok=False,
             v="nothing on this machine can do %s yet — install an engine"
               % st["label"], engines=st["engines"])
        return 1

    if not ready and part:
        e = part[0]
        emit("connect_done", cap=cap, ok=False,
             v="%s is installed but missing its %s — install that to finish"
               % (e["engine"], e.get("missing_asset") or "data"),
             engine=e["engine"], engines=st["engines"])
        return 1

    order = {"neural": 4, "good": 3, "ok": 2, "basic": 1, "robotic": 0}
    best = sorted(ready, key=lambda r: (r["recommended"],
                                        order.get(r["quality"], 0)),
                  reverse=True)[0]
    _choose(cap, best["engine"])

    picked_dev = None
    kind = st.get("device_kind")
    if kind:
        d = devices()
        cfg = config()
        if kind == "sink":
            # never default to HDMI: on this box those 3 are silent
            cand = [s for s in d["sinks"] if not s["hdmi"]] or d["sinks"]
            pref = d["default_sink"]
            picked_dev = next((c["id"] for c in cand if c["id"] == pref),
                              cand[0]["id"] if cand else None)
        elif kind == "source":
            cand = [s for s in d["sources"] if not s["monitor"]]
            pref = d["default_source"]
            picked_dev = next((c["id"] for c in cand if c["id"] == pref),
                              cand[0]["id"] if cand else None)
        elif kind == "video":
            picked_dev = d["cameras"][0]["id"] if d["cameras"] else None
        if picked_dev:
            cfg["devices"][kind] = picked_dev
            save_config(cfg)

    emit("connect_done", cap=cap, ok=True, engine=best["engine"],
         device=picked_dev,
         v="wired %s to %s%s" % (st["label"], best["engine"],
                                 " on " + _pretty_sink(picked_dev)
                                 if picked_dev else ""))
    return 0


# ----------------------------------------------------------------- test

def test(cap):
    """Prove a capability actually works, rather than just being installed."""
    st = caps_status()
    c = st["caps"].get(cap) or die("unknown capability: " + cap)
    if not c["ready"]:
        emit("test_done", cap=cap, ok=False, v=c["wall"] or "not ready")
        return 1
    cfg = config()
    eff = devices()["effective"]

    if cap == "tts":
        rc = subprocess.run(
            [os.path.join(HERE, "tar_say.sh"), "T.A.R. online. Audio path confirmed."],
            capture_output=True, text=True, timeout=45)
        ok = rc.returncode == 0 and "NO_TTS" not in rc.stdout
        emit("test_done", cap=cap, ok=ok,
             v="spoke through " + _pretty_sink(eff["sink"] or "default")
               if ok else "TTS produced no audio")
        return 0 if ok else 1

    if cap == "stt":
        emit("test_done", cap=cap, ok=True,
             v="mic %s ready (%s, model %s)" % (
                 _pretty_sink(eff["source"] or "default"),
                 cfg["engines"].get("stt"), cfg.get("stt_model")))
        return 0

    if cap == "camera":
        dev = eff["video"]
        if not dev:
            emit("test_done", cap=cap, ok=False, v="no camera node found")
            return 1
        shot = os.path.join(DATA, "cam-test.jpg")
        rc = subprocess.run(
            ["ffmpeg", "-y", "-f", "v4l2", "-i", dev, "-frames:v", "1", shot],
            capture_output=True, timeout=25)
        ok = rc.returncode == 0 and os.path.exists(shot)
        emit("test_done", cap=cap, ok=ok,
             v="grabbed a frame from " + dev if ok
               else "could not read " + dev, path=shot if ok else None)
        return 0 if ok else 1

    emit("test_done", cap=cap, ok=True, v=c["label"] + " looks wired")
    return 0


# ----------------------------------------------------------------- cli

def main():
    if len(sys.argv) < 2:
        die("usage: tar_caps.py caps|engines <cap>|install <cap> <engine>|"
            "connect <cap>|devices|set-device <kind> <id>|set-engine <cap> <e>|"
            "test <cap>|speak on|off")
    cmd = sys.argv[1]
    a = sys.argv[2:]

    if cmd == "caps":
        emit("caps", **caps_status())

    elif cmd == "engines":
        if not a:
            die("usage: engines <cap>")
        st = caps_status()["caps"].get(a[0]) or die("unknown capability: " + a[0])
        emit("engine_list", cap=a[0], label=st["label"], blurb=st["blurb"],
             locked=st["locked"], wall=st["wall"], engines=st["engines"],
             chosen=st["chosen"])

    elif cmd == "install":
        if len(a) < 2:
            die("usage: install <cap> <engine> [--voice N] [--model N]")
        voice = model = None
        if "--voice" in a:
            voice = a[a.index("--voice") + 1]
        if "--model" in a:
            model = a[a.index("--model") + 1]
        sys.exit(install(a[0], a[1], voice, model))

    elif cmd == "connect":
        if not a:
            die("usage: connect <cap>")
        sys.exit(connect(a[0]))

    elif cmd == "devices":
        emit("devices", **devices())

    elif cmd == "set-device":
        if len(a) < 2:
            die("usage: set-device <sink|source|video> <id>")
        kind, dev = a[0], a[1]
        if kind not in ("sink", "source", "video"):
            die("kind must be sink, source or video")
        cfg = config()
        cfg["devices"][kind] = dev
        save_config(cfg)
        # make it the system default too, so non-T.A.R. audio follows
        if kind == "sink":
            _pactl(["set-default-sink", dev])
        elif kind == "source":
            _pactl(["set-default-source", dev])
        emit("ok", v="%s -> %s" % (kind, _pretty_sink(dev)))

    elif cmd == "set-engine":
        if len(a) < 2:
            die("usage: set-engine <cap> <engine>")
        st = caps_status()["caps"].get(a[0]) or die("unknown capability: " + a[0])
        row = next((e for e in st["engines"] if e["engine"] == a[1]), None)
        if not row:
            die("unknown engine '%s' for %s" % (a[1], a[0]))
        if not row["ready"]:
            die("%s is not installed yet — install it first" % a[1])
        _choose(a[0], a[1])
        emit("ok", v="%s -> %s" % (st["label"], a[1]))

    elif cmd == "set-voice":
        if not a:
            die("usage: set-voice <name>")
        cfg = config()
        cfg["voice"] = a[0]
        save_config(cfg)
        emit("ok", v="voice -> " + a[0])

    elif cmd == "autostart":
        if len(a) < 2 or a[1] not in ("on", "off"):
            die("usage: autostart <speak|mic|wake|warm|sfx|greet> on|off")
        key = a[0]
        if key not in ("speak", "mic", "wake", "warm", "sfx", "greet"):
            die("unknown autostart key: " + key)
        cfg = config()
        cfg["autostart"][key] = (a[1] == "on")
        save_config(cfg)
        emit("ok", v="autostart %s: %s" % (key, a[1]))

    elif cmd == "backend":
        if not a or a[0] not in ("local", "claude"):
            die("usage: backend local|claude")
        cfg = config()
        cfg["backend"] = a[0]
        save_config(cfg)
        if a[0] == "claude":
            # cloud brain answers now: free the RAM a local model is holding
            try:
                import tar_brain
                tar_brain.unload_except(None)
            except Exception:
                pass
        emit("ok", v="brain: " + a[0])

    elif cmd == "start-mode":
        if not a or a[0] not in ("window", "cinematic", "floating"):
            die("usage: start-mode window|cinematic|floating")
        cfg = config()
        cfg["start_mode"] = a[0]
        save_config(cfg)
        emit("ok", v="start mode: " + a[0])

    elif cmd == "speak":
        if not a or a[0] not in ("on", "off"):
            die("usage: speak on|off")
        cfg = config()
        cfg["speak"] = (a[0] == "on")
        save_config(cfg)
        emit("ok", v="speech " + a[0])

    elif cmd == "test":
        if not a:
            die("usage: test <cap>")
        sys.exit(test(a[0]))

    else:
        die("unknown command: " + cmd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
