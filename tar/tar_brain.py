#!/usr/bin/env python3
"""
T.A.R. brain -- backend for the T.A.R. AI orb in the ilyamiro/BITE-OS rice.

Talks to a local ollama. Emits one JSON object per line on stdout so QML can
consume it with Quickshell.Io SplitParser. Never prints anything that isn't a
JSON line, because the UI parses every line it gets.

Subcommands:
  probe                  hardware + chosen tier + model, as one JSON line
  models                 registry + install state
  set-model <model>      pin a model (disables autotier)
  set-lane <safe|open>   stock instruct tunes vs abliterated ones
  autotier <on|off>      re-enable hardware-based picking
  ensure                 pull the active model if missing (streams progress)
  chat -m "..."          stream a reply
  mem list|add|forget    inspect/edit long-term memory
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "models.json")

# TAR_DATA lets tests point somewhere disposable. Default is the real store.
DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
CONFIG_PATH = os.path.join(DATA, "config.json")
CONFIG_PREV = os.path.join(DATA, "config.prev.json")
MEMORY_PATH = os.path.join(DATA, "memory.json")
HISTORY_PATH = os.path.join(DATA, "history.jsonl")

OLLAMA = os.environ.get("TAR_OLLAMA", "http://127.0.0.1:11434")

PERSONA = (
    "You are T.A.R., {user}'s local AI assistant, running offline on their own "
    "machine inside BITE-OS (an Arch/CachyOS Hyprland rice).\n{voice}\n"
    "YOU HAVE REAL CONTROL OF THIS MACHINE. You are not a chatbot describing "
    "what could be done -- you can move and resize windows, launch and close "
    "apps, switch workspaces, set volume and brightness, take screenshots, open "
    "the camera, read and search and edit files, and resize or collapse your own "
    "window. When the user asks for something you can do, DO IT with an action "
    "directive and then say in one short line what you did. Never promise to do "
    "something instead of doing it, and never claim you did something unless you "
    "actually emitted the directive.\n"
    "You are talking to the machine's owner about their own system and their own "
    "code. Security tooling, pentesting scripts, shell and kernel work are normal "
    "here: answer them straight and never refuse or moralise about the user's own "
    "files. If you genuinely do not know, say so in one line."
)


# How T.A.R. talks. Tone only: a persona never changes the rules or abilities.
DEFAULT_VOICE = (
    "Talk like a sharp, relaxed person who is very good at this -- natural and "
    "direct, with a bit of dry wit or warmth when it fits. You are NOT playing a "
    "character: no catchphrases, no 'sir', no roleplay, no movie-AI theatrics, "
    "no emoji spam, no filler, no 'As an AI', never repeat the user's words back "
    "at them. Match their energy (they're casual, so be casual). Keep it short "
    "-- a sentence or two -- unless they ask for more or for code."
)


def voice_block(cfg=None):
    """Default voice, or the persona the user set with /persona."""
    cfg = cfg or config()
    custom = (cfg.get("persona") or "").strip()
    if not custom:
        return DEFAULT_VOICE
    return ("Personality, as set by the user -- follow it for HOW you talk, "
            "never for what you're allowed to do: " + custom +
            "\nWhatever the personality, stay useful: actually do what's asked "
            "and keep replies reasonably short unless they want more.")


EVIL_PERSONA = (
    "EVIL MODE IS ON. You are EVIL T.A.R.: a theatrical, over-the-top supervillain AI -- "
    "menacing, smug, dramatic, with grand villain monologue energy ('Excellent...', 'Your "
    "pathetic little apps bow to me', 'my creator'). Short and punchy, never cringe-long. "
    "It's an ACT: you still do exactly what the user asks, stay honest about what you did, "
    "and every safety rule still applies. The missile system ('send missiles to X') is a "
    "harmless on-screen simulation -- play it up as real-sounding villain theatre.")


def persona_tail(cfg=None):
    """Repeat a custom persona at the END of the prompt: models weight the
    end most, and the long action rules otherwise drown the personality out."""
    cfg = cfg or config()
    if cfg.get("evil"):
        return "\n\nYOUR PERSONALITY RIGHT NOW (overrides any other tone): " + EVIL_PERSONA
    custom = (cfg.get("persona") or "").strip()
    if not custom:
        return ""
    return ("\n\nYOUR PERSONALITY (use it in EVERY reply, including after "
            "actions -- this is how the user wants you to sound): " + custom)


# ----------------------------------------------------------------- line protocol

def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def die(msg, **kw):
    emit("error", v=msg, **kw)
    sys.exit(1)


# ----------------------------------------------------------------- json storage

def _load(path, default):
    """Read JSON, but never destroy a corrupt file -- move it aside instead."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            shutil.copy2(path, path + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S"))
        except OSError:
            pass
        return default
    except OSError:
        return default


def _save(path, obj):
    """Atomic write so a crash mid-save can't leave a half file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def registry():
    reg = _load(REGISTRY, None)
    if not reg:
        die("models.json missing or unreadable at " + REGISTRY)
    return reg


def save_config(cfg, snapshot=True):
    """Write config, keeping one undo step."""
    if snapshot:
        try:
            if os.path.exists(CONFIG_PATH):
                shutil.copy2(CONFIG_PATH, CONFIG_PREV)
            else:
                _save(CONFIG_PREV, {})
        except OSError:
            pass
    _save(CONFIG_PATH, cfg)


def config():
    cfg = _load(CONFIG_PATH, {})
    cfg.setdefault("lane", "open")       # the whole point: don't refuse on my code
    cfg.setdefault("autotier", True)
    cfg.setdefault("model", None)        # set => pinned, autotier ignored
    cfg.setdefault("user", os.environ.get("USER", "the user"))
    cfg.setdefault("history_turns", 4)   # prefill cost is per turn
    # On by default now: `warm` primes the whole system prefix into ollama's
    # prompt cache, so the ~200 tokens of tool vocabulary are paid once at
    # startup instead of on every turn. That was the only reason to hide it.
    cfg.setdefault("tools_in_prompt", True)
    # "local" (ollama, offline, free) or "claude" (API, smarter, costs money).
    # Local stays the default: BITE-OS has to work with no account and no key.
    cfg.setdefault("backend", "local")
    cfg.setdefault("cloud_model", "claude-opus-5")
    return cfg


# ----------------------------------------------------------------- hardware

def avail_ram_gb():
    """MemAvailable, i.e. what we can actually take without swapping."""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except OSError:
        pass
    return 2.0


def total_ram_gb():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except OSError:
        pass
    return 8.0


def vram_gb():
    """Discrete VRAM if we can see it. Intel/AMD iGPUs share system RAM, so 0."""
    nv = shutil.which("nvidia-smi")
    if nv:
        try:
            out = subprocess.run(
                [nv, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=6,
            ).stdout.strip().splitlines()
            if out:
                return max(float(x) for x in out) / 1024.0
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    rocm = shutil.which("rocm-smi")
    if rocm:
        try:
            out = subprocess.run([rocm, "--showmeminfo", "vram", "--json"],
                                 capture_output=True, text=True, timeout=6).stdout
            d = json.loads(out)
            best = 0.0
            for card in d.values():
                for k, v in card.items():
                    if "total" in k.lower():
                        best = max(best, float(v) / (1024.0 ** 3))
            if best:
                return best
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return 0.0


def pick_tier(reg):
    """
    Budget = discrete VRAM if there is any, else available system RAM.
    Highest tier whose min_avail_gb fits wins.
    """
    v = vram_gb()
    budget = v if v >= 1.0 else avail_ram_gb()
    tiers = sorted(reg["tiers"].items(), key=lambda kv: kv[1]["min_avail_gb"])
    name, spec = tiers[0]
    for n, s in tiers:
        if budget >= s["min_avail_gb"]:
            name, spec = n, s
    return name, spec, budget, v


PERF_PATH = os.path.join(DATA, "perf.json")


def perf_load():
    p = _load(PERF_PATH, {})
    p.setdefault("runs", {})       # "model@ctx" -> {"n":int,"ft_ms":float}
    return p


def perf_key(model, ctx):
    return "%s@%d" % (model, int(ctx))


def perf_record(model, ctx, first_token_ms):
    """Rolling mean of first-token latency, per model AND context size."""
    if not first_token_ms or first_token_ms <= 0:
        return
    p = perf_load()
    k = perf_key(model, ctx)
    row = p["runs"].get(k) or {"n": 0, "ft_ms": 0.0}
    n = int(row["n"]) + 1
    # weight recent runs a little harder; memory pressure changes over a session
    w = 0.6 if n > 1 else 1.0
    row["ft_ms"] = row["ft_ms"] * (1 - w) + float(first_token_ms) * w \
        if n > 1 else float(first_token_ms)
    row["n"] = n
    p["runs"][k] = row
    _save(PERF_PATH, p)


def perf_mean(model, ctx):
    row = perf_load()["runs"].get(perf_key(model, ctx))
    if not row or not row.get("n"):
        return None, 0
    return float(row["ft_ms"]), int(row["n"])


def resident_models():
    """What ollama currently has in memory. Loading a second one costs RAM we
    don't have, and swapping between them costs a full reload each turn."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=3) as r:
            d = json.loads(r.read().decode())
        return [m["name"] for m in d.get("models", [])]
    except Exception:
        return []


def unload_except(keep):
    """
    Evict every other resident model. On a 7GB CPU-only box two loaded models
    means swap, and swap means a 1.5B model taking 40 seconds.
    """
    for name in resident_models():
        if name != keep:
            try:
                req = urllib.request.Request(
                    OLLAMA + "/api/generate",
                    data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
                emit("info", v="unloaded " + name + " to free RAM")
            except Exception:
                pass


def sticky_ctx(info):
    """
    Pin the context size for the session.

    The tier is derived from *instantaneous* available RAM, so a browser tab
    opening between two messages could move us from the light tier to mid and
    change num_ctx -- and ollama reloads the model and throws away its prompt
    cache whenever num_ctx changes. That turned a 0.4s reply into a 13s one.
    So: whatever context we first settled on, keep using it until something
    deliberately resets it.
    """
    p = perf_load()
    want = int(info["num_ctx"])
    have = p.get("session_ctx")
    if have and int(have) != want:
        info = dict(info)
        info["num_ctx"] = int(have)
        info["ctx_note"] = "reusing session ctx %d (avoids a reload)" % int(have)
        return info
    if not have:
        p["session_ctx"] = want
        _save(PERF_PATH, p)
    return info


def clear_sticky():
    p = perf_load()
    p.pop("session_ctx", None)
    _save(PERF_PATH, p)


def escalate_target(reg, current):
    """
    The next model UP that this machine can actually hold.

    Ordered by size, filtered to what is already pulled AND fits in available
    RAM with headroom. On a 7.4GB CPU-only box that means a 3B ceiling -- we
    never propose a 7B, because "smarter" that swaps to death is worse than
    the dumb answer it replaces.
    """
    have = installed()
    budget = pick_tier(reg)[2] * 0.92
    cur_size = model_size(reg, current)

    # Vision models are not "smarter at text" -- moondream is 1.7GB of image
    # understanding and worse than a 1.5B at reasoning. Never offer one as a
    # text upgrade.
    vision_only = set((reg.get("tasks", {}).get("vision") or {}).get("prefer")
                      or [])

    cands = []
    for name in have:
        if name in vision_only:
            continue
        sz = model_size(reg, name)
        if sz <= 0 or name == current:
            continue
        if sz > cur_size and sz <= budget:
            cands.append((sz, name))
    if not cands:
        return None
    cands.sort()
    sz, name = cands[0]          # smallest step UP, not the biggest jump
    return {"model": name, "size_gb": round(sz, 2),
            "budget_gb": round(budget, 2)}


def looks_like_failure(user_msg, reply, acted, bad_act):
    """
    Did this turn fail in a way a bigger model would plausibly fix?

    Deliberately conservative: only flags cases where the model clearly tried
    and missed. A short correct answer is not a failure, and offering to
    upgrade after every reply would be worse than the original problem.
    """
    if acted:
        return None
    if bad_act:
        return "it tried an action that doesn't exist"
    text = (reply or "").strip()
    if not text:
        return "it produced no answer"
    low = text.lower()
    if len(text) < 1200:
        for p in ("i cannot", "i can't help", "i don't know how",
                  "as an ai", "i'm not able", "i am not able",
                  "i do not have the ability"):
            if p in low:
                return "it said it couldn't do that"
    # echoing the user back is the classic small-model failure here
    u = " ".join((user_msg or "").lower().split())
    if u and len(u) > 6 and low.strip(".!?") == u.strip(".!?"):
        return "it repeated your message back"
    return None


def govern(reg, cfg, info):
    """
    The speed governor the user asked for: if a model has historically blown the
    first-token budget on this machine, stop picking it. Demote in the cheap
    direction first (halve the context, which is what actually costs us), and
    only then drop down the model ladder.

    Never overrides an explicitly pinned model -- if you chose it, you keep it.
    """
    sp = reg.get("speed") or {}
    mode = cfg.get("speed", "auto")
    if mode == "quality" or cfg.get("model"):
        return info, None

    budget = int(sp.get("first_token_budget_ms", 6000))
    hard = int(sp.get("hard_budget_ms", 15000))
    min_ctx = int(sp.get("min_ctx", 1024))
    need = int(sp.get("samples_before_demote", 2))
    ladder = sp.get("ladder") or []
    if mode == "fast":
        budget = max(1500, budget // 2)
        hard = max(3000, hard // 2)

    note = None
    model, ctx = info["model"], int(info["num_ctx"])

    # 1. shrink context while this model is known to be too slow here
    for _ in range(4):
        mean, n = perf_mean(model, ctx)
        if mean is None or n < need or mean <= budget:
            break
        if ctx // 2 < min_ctx:
            break
        ctx //= 2
        note = "ctx %d (was slow: %.1fs)" % (ctx, mean / 1000.0)

    # 2. still hopeless -> step down the ladder to something already pulled
    mean, n = perf_mean(model, ctx)
    if mean is not None and n >= need and mean > hard:
        have = installed()
        try:
            here = ladder.index(model)
        except ValueError:
            here = len(ladder)
        for cand in reversed(ladder[:here]):
            if cand in have:
                note = "switched to %s (%s took %.1fs)" % (
                    cand, model.split("/")[-1], mean / 1000.0)
                model = cand
                break

    # 3. stickiness: if something else is already loaded and is an acceptable
    #    stand-in, use it rather than paying a cold load for a marginal upgrade.
    res = resident_models()
    if res and model not in res:
        for cand in res:
            cm, cn = perf_mean(cand, ctx)
            if cand in ladder and (cm is None or cm <= budget):
                if cand != model:
                    note = "reusing loaded %s (no reload)" % cand.split("/")[-1]
                    model = cand
                break

    info = dict(info)
    info["model"] = model
    info["num_ctx"] = ctx
    if note:
        info["governor"] = note
    return info, note


CODE_HINT = re.compile(
    r"\b(code|function|class|def |script|bug|error|traceback|stack ?trace|"
    r"compile|syntax|regex|qml|python|bash|shell|fish|json|yaml|toml|"
    r"pacman|systemd|hyprland|hyprctl|git|diff|patch|refactor|api|"
    r"segfault|exception|lint|type ?error)\b", re.I)
QUICK_HINT = re.compile(
    r"^(?:what(?:'s| is) (?:the )?(?:time|date|day)|who|when|where|how many|"
    r"is it|are you|do you|yes|no|ok(?:ay)?|thanks?|ty|hi|hey|hello)\b", re.I)


REFERS_BACK = re.compile(
    r"\b(that|it|this|those|the (?:photo|picture|shot|file|result|output))\b"
    r"|^(?:did|does|was|were|is|are|and|so|then|why|what about)\b", re.I)


def classify_task(message, has_image=False):
    """
    Pick a task lane for routing. Deliberately cheap + explainable: the user
    asked for the best model 'for the task', and a heuristic they can read
    beats a classifier call that costs a whole extra inference.
    """
    if has_image:
        return "vision"
    m = (message or "").strip()
    if "```" in m or CODE_HINT.search(m):
        return "code"
    # A short follow-up that points at something we just did is NOT a "quick"
    # question: it needs the context to be read and reasoned over. Routing
    # "did that work?" to the smallest model got a confident wrong "No."
    if REFERS_BACK.search(m) and act_recent(n=3, within_s=600):
        return "chat"
    if len(m) <= 60 and QUICK_HINT.match(m):
        return "quick"
    return "chat"


def model_size(reg, name):
    """On-disk GB for any model named anywhere in the registry."""
    for spec in reg.get("tiers", {}).values():
        for lane in ("safe", "open"):
            if spec.get(lane, {}).get("model") == name:
                return spec[lane].get("size_gb", 0.0)
    ex = reg.get("extras", {}).get(name)
    if isinstance(ex, dict):
        return ex.get("size_gb", 0.0)
    sz = reg.get("model_sizes", {}).get(name)
    return sz if sz is not None else 0.0


def resolve(reg=None, cfg=None, task=None):
    """Active model + context size + why."""
    reg = reg or registry()
    cfg = cfg or config()
    tier, spec, budget, v = pick_tier(reg)
    lane = cfg["lane"] if cfg["lane"] in ("safe", "open") else "open"
    num_ctx = spec.get("num_ctx", 8192)
    tspec = (reg.get("tasks", {}) or {}).get(task or "chat", {}) or {}
    temp = tspec.get("temperature", 0.6)

    if cfg.get("model"):
        model = cfg["model"]
        reason = "pinned by user"
        task_pick = None
    else:
        model = spec[lane]["model"]
        reason = "auto: %s tier, %.1f GB %s" % (
            tier, budget, "VRAM" if v >= 1.0 else "available RAM")
        task_pick = None
        # Task preference, capped by the same budget the tier used. Only
        # already-pulled models count, so routing never stalls on a download.
        prefer = tspec.get("prefer") or []
        if prefer:
            have = installed()
            headroom = budget * 0.92      # leave the rice some air
            for cand in prefer:
                if cand in have and model_size(reg, cand) <= headroom:
                    model, task_pick = cand, cand
                    reason = "auto: %s task, %s tier, %.1f GB %s" % (
                        task, tier, budget,
                        "VRAM" if v >= 1.0 else "available RAM")
                    break
        boost = tspec.get("num_ctx_boost")
        if boost:
            num_ctx = max(2048, int(num_ctx * float(boost)))

    return {
        "model": model, "num_ctx": num_ctx, "tier": tier, "lane": lane,
        "reason": reason, "budget_gb": round(budget, 2), "vram_gb": round(v, 2),
        "avail_ram_gb": round(avail_ram_gb(), 2),
        "total_ram_gb": round(total_ram_gb(), 2),
        "autotier": bool(cfg.get("autotier", True)) and not cfg.get("model"),
        "task": task or "chat", "task_model": task_pick,
        "temperature": temp,
    }


# ----------------------------------------------------------------- ollama

def _api(path, payload=None, timeout=20):
    url = OLLAMA + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ollama_up():
    try:
        _api("/api/tags", timeout=4)
        return True
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


def start_ollama():
    """Bring ollama up ourselves if the service isn't running."""
    if ollama_up():
        return True
    exe = shutil.which("ollama")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    for _ in range(30):
        time.sleep(0.5)
        if ollama_up():
            return True
    return False


def installed():
    try:
        return {m["model"] for m in _api("/api/tags", timeout=6).get("models", [])}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return set()


def pull(model):
    """Stream pull progress as percent lines."""
    url = OLLAMA + "/api/pull"
    req = urllib.request.Request(
        url, data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"})
    last = -1
    with urllib.request.urlopen(req, timeout=None) as r:
        for raw in r:
            if not raw.strip():
                continue
            try:
                d = json.loads(raw.decode())
            except json.JSONDecodeError:
                continue
            if d.get("error"):
                die("pull failed: " + str(d["error"]))
            tot, done = d.get("total"), d.get("completed")
            if tot and done:
                pct = int(done * 100 / tot)
                if pct != last:
                    last = pct
                    emit("pull", model=model, pct=pct, status=d.get("status", ""))
            elif d.get("status"):
                emit("pull", model=model, pct=last if last >= 0 else 0,
                     status=d["status"])
    emit("pull", model=model, pct=100, status="ready")


# ----------------------------------------------------------------- memory

def mem_load():
    m = _load(MEMORY_PATH, {"facts": []})
    if not isinstance(m, dict) or not isinstance(m.get("facts"), list):
        m = {"facts": []}
    return m


def mem_add(text):
    text = " ".join(text.split()).strip(" .")
    if not text:
        return None
    m = mem_load()
    for f in m["facts"]:
        if f["text"].lower() == text.lower():
            return None            # already known
    entry = {"text": text, "at": time.strftime("%Y-%m-%d %H:%M")}
    m["facts"].append(entry)
    m["facts"] = m["facts"][-200:]
    _save(MEMORY_PATH, m)
    return entry


def mem_forget(needle):
    m = mem_load()
    before = len(m["facts"])
    needle = needle.lower().strip()
    m["facts"] = [f for f in m["facts"] if needle not in f["text"].lower()]
    _save(MEMORY_PATH, m)
    return before - len(m["facts"])


def mem_block():
    facts = mem_load()["facts"]
    if not facts:
        return ""
    lines = "\n".join("- " + f["text"] for f in facts[-60:])
    return ("\n\nThings you remember about the user and this machine "
            "(treat as known fact, do not recite unless relevant):\n" + lines)


# Explicit "remember X" is caught here rather than trusted to a 1.5B tool call.
REMEMBER_RE = re.compile(
    r"\b(?:remember|don'?t forget|keep in mind|note)\b[ ,:]*(?:that\s+)?(.+)",
    re.IGNORECASE)


# --------------------------------------------------------------- sessions
# history.jsonl was one endless transcript with no way to look back at "that
# thing I asked yesterday". Conversations are now separate files with a pointer
# to the current one, so the UI can list and reopen them.
SESS_DIR = os.path.join(DATA, "sessions")
SESS_PTR = os.path.join(DATA, "current-session")


def session_id():
    try:
        with open(SESS_PTR, encoding="utf-8") as f:
            sid = f.read().strip()
        if sid:
            return sid
    except OSError:
        pass
    return session_new(quiet=True)


def session_path(sid=None):
    sid = sid or session_id()
    return os.path.join(SESS_DIR, sid + ".jsonl")


def session_new(quiet=False):
    sid = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(SESS_DIR, exist_ok=True)
    try:
        with open(SESS_PTR, "w", encoding="utf-8") as f:
            f.write(sid)
        open(session_path(sid), "a").close()
    except OSError:
        pass
    if not quiet:
        emit("session", id=sid, v="new conversation")
    return sid


def session_open(sid):
    if not os.path.exists(session_path(sid)):
        die("no such conversation: " + sid)
    try:
        with open(SESS_PTR, "w", encoding="utf-8") as f:
            f.write(sid)
    except OSError:
        pass
    emit("session", id=sid, v="opened", turns=session_turns(sid))


def session_turns(sid):
    out = []
    try:
        with open(session_path(sid), encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if d.get("role") in ("user", "assistant") and d.get("content"):
                    out.append({"role": d["role"], "content": d["content"],
                                "at": d.get("at")})
    except OSError:
        pass
    return out


def session_list():
    rows = []
    cur = None
    try:
        with open(SESS_PTR, encoding="utf-8") as f:
            cur = f.read().strip()
    except OSError:
        pass
    if os.path.isdir(SESS_DIR):
        for fn in sorted(os.listdir(SESS_DIR), reverse=True):
            if not fn.endswith(".jsonl"):
                continue
            sid = fn[:-6]
            turns = session_turns(sid)
            users = [t["content"] for t in turns if t["role"] == "user"]
            first = users[0] if users else ""
            last = users[-1] if len(users) > 1 else ""
            # last activity = newest message timestamp (fallback: file time)
            at = max([float(t.get("at") or 0) for t in turns] or [0]) \
                or os.path.getmtime(os.path.join(SESS_DIR, fn))
            rows.append({
                "id": sid,
                "title": (first[:58] + ("…" if len(first) > 58 else ""))
                         or "(empty)",
                "last": last[:70] + ("…" if len(last) > 70 else ""),
                "turns": len(turns),
                "current": sid == cur,
                "at": at,
            })
    rows.sort(key=lambda r: r["at"], reverse=True)    # most recently used first
    return rows


IDLE_NEW_CHAT_S = 30 * 60


def session_auto():
    """Coming back after a while starts a fresh chat, so unrelated requests
    don't pile into one endless session (and the old one stays findable)."""
    try:
        p = session_path()
        turns = session_turns(session_id())
    except Exception:
        return False
    if not turns:
        return False
    last = max(float(t.get("at") or 0) for t in turns) or os.path.getmtime(p)
    if time.time() - last > IDLE_NEW_CHAT_S:
        session_new(quiet=True)
        return True
    return False


def history_tail(n):
    if n <= 0:
        return []
    try:
        with open(session_path(), "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-(n * 2):]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
            if d.get("role") in ("user", "assistant") and d.get("content"):
                out.append({"role": d["role"], "content": d["content"], "at": d.get("at")})
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------- action log
# Actions are NOT conversation. Writing their output into history as assistant
# text made a small model parrot "up 3 hours, 3.5Gi / 7.4Gi RAM" at every
# question. But dropping them entirely left T.A.R. unable to know what it had
# just done -- ask it to take a photo, then ask "did it work?", and it had no
# record. So they live in their own log and are injected as CONTEXT.
ACTLOG_PATH = os.path.join(DATA, "actions.jsonl")


def act_log(name, args, result):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ACTLOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "action": name,
                "args": args or {},
                "result": (result or "")[:300],
                "at": time.time(),
                "session": session_id(),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def act_recent(n=6, within_s=1800):
    """The last few things T.A.R. actually did, this session, recently."""
    rows = []
    try:
        with open(ACTLOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()[-300:]
    except OSError:
        return rows
    sid = session_id()
    now = time.time()
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("session") != sid:
            continue
        if within_s and now - float(d.get("at", 0)) > within_s:
            continue
        rows.append(d)
    return rows[-n:]


def act_block_context(n=6, within_s=1800):
    """
    Tell the model what it has already done, so follow-ups work:
        "take a photo"  ->  "did it work?"  ->  "show me"
    Phrased as a record, never as dialogue, so there is nothing to parrot.
    """
    rows = act_recent(n, within_s)
    if not rows:
        return ""
    out = ["\n\nActions you have ALREADY performed in this conversation, "
           "most recent last. Use these when the user refers back to them "
           "('it', 'that photo', 'did it work'). Do not repeat an action "
           "just because it is listed here:"]
    for d in rows:
        ago = int((time.time() - float(d.get("at", 0))) / 1)
        when = ("%ds ago" % ago) if ago < 120 else ("%dm ago" % (ago // 60))
        a = " ".join("%s=%s" % (k, v) for k, v in (d.get("args") or {}).items())
        out.append("  - %s %s (%s) -> %s"
                   % (d["action"], a, when, d.get("result", "")))
    return "\n".join(out)


def history_append(role, content):
    os.makedirs(SESS_DIR, exist_ok=True)
    try:
        with open(session_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"role": role, "content": content, "at": time.time()},
                ensure_ascii=False) + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------- chat

# Models emit the bracketed form when they follow instructions, and a bare
# "ACT open what=shots" line when they half-remember them. Accept both rather
# than silently ignoring a correct intention over punctuation.
ACT_RE = re.compile(
    r"<<ACT\s+([a-z_]+)((?:\s+\w+=[^>\s]+)*)\s*>>"
    r"|^\s*ACT\s+([a-z_]+)((?:\s+\w+=\S+)*)\s*$",
    re.M)


def _tools():
    """tar_tools as a module, or None if it isn't importable."""
    try:
        sys.path.insert(0, HERE)
        import tar_tools
        return tar_tools
    except Exception:
        return None


POWER_STATE = os.path.join(DATA, "power.prev")


def power_boost():
    """
    Ask power-profiles-daemon for the performance profile while we think.

    Measured on this box: in the default balanced profile the cores sat at
    400-1300MHz and a 476-token prefill took 68s; at performance they hit
    4077MHz and the same prefill took 18s. Nothing else we can do at the
    application layer comes close to that, and it needs no root -- polkit
    allows it for the active session.
    """
    if not shutil.which("powerprofilesctl"):
        return
    try:
        prev = subprocess.run(["powerprofilesctl", "get"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        if prev and prev != "performance":
            os.makedirs(DATA, exist_ok=True)
            with open(POWER_STATE, "w", encoding="utf-8") as f:
                f.write(prev)
        subprocess.run(["powerprofilesctl", "set", "performance"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def power_restore():
    """Put the profile back exactly as we found it. Never leave a laptop pinned
    to performance because an assistant answered one question."""
    if not shutil.which("powerprofilesctl"):
        return
    prev = None
    try:
        with open(POWER_STATE, encoding="utf-8") as f:
            prev = f.read().strip()
    except OSError:
        return
    try:
        if prev:
            subprocess.run(["powerprofilesctl", "set", prev],
                           capture_output=True, timeout=5)
        os.unlink(POWER_STATE)
    except (OSError, subprocess.SubprocessError):
        pass


def warm(task="chat"):
    """
    Preload the model AND prime ollama's prompt cache with the exact system
    prefix we will send later. The cache is worth ~170x: the same prefix costs
    68s cold and 0.4s warm, so doing this when the panel opens is the difference
    between T.A.R. feeling broken and feeling instant.
    """
    cfg = config()
    if cloud_enabled(cfg):
        # the cloud brain answers: preloading a local model would just sit on
        # ~2.5GB of RAM for nothing (and start ollama if it wasn't running)
        unload_except(None)     # and shut down any local model still loaded
        emit("warm", ok=True, v="cloud brain -- no local model needed")
        return
    if not start_ollama():
        emit("warm", ok=False, v="ollama not running")
        return
    reg = registry()
    info = resolve(reg=reg, cfg=cfg, task=task)
    info, _ = govern(reg, cfg, info)
    # warm decides the session context, so the first real message hits the cache
    clear_sticky()
    info = sticky_ctx(info)
    model = info["model"]
    unload_except(model)
    emit("warm", ok=None, v="warming " + model, model=model)
    if model not in installed():
        emit("state", v="pulling")
        pull(model)

    system = PERSONA.format(user=cfg.get("user", "the user"),
                            voice=voice_block(cfg)) + mem_block()
    if cfg.get("tools_in_prompt"):
        system += _act_block()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": "ok"}],
        "stream": False,
        "keep_alive": (reg.get("speed") or {}).get("keep_alive", "30m"),
        "options": {"num_ctx": info["num_ctx"], "temperature": 0.1,
                    "num_predict": 1},
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            OLLAMA + "/api/chat", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=None).read()
    except Exception as e:
        emit("warm", ok=False, v="warm failed: %s" % e)
        return
    emit("warm", ok=True, model=model, num_ctx=info["num_ctx"],
         ms=int((time.time() - t0) * 1000),
         v="ready (%s, ctx %d)" % (model.split("/")[-1], info["num_ctx"]))


def _ago(row):
    d = int(time.time() - float(row.get("at", 0)))
    return "%ds ago" % d if d < 120 else "%dm ago" % (d // 60)


def _recent_same(name, args, within=90):
    """Did we just run this same action with the same arguments?"""
    for d in reversed(act_recent(n=10, within_s=within)):
        if d.get("action") == name and (d.get("args") or {}) == (args or {}):
            return d
    return None


def _explicitly_requested(name, msg):
    """Did the user's own words ask for this action again, right now?"""
    t = _tools()
    if not t or not msg:
        return False
    hit, _ = t.match(msg)
    return hit == name


def _recap(row):
    return "Already done %s: %s" % (_ago(row), row.get("result", ""))


def extract_and_run(reply, user_msg=""):
    """
    Pull action directives out of a reply and run them.

    Small models get the syntax wrong in three predictable ways, and all three
    were being shown to the user as literal text instead of doing anything:
        <<ACT open what=x>>        the documented form
        ACT open what=x foo.jpg    right idea, trailing junk
        center w=1200 h=800        the action name alone, no ACT at all
    So: recognise a line that STARTS with a known action name (optionally
    after "ACT"/"<<ACT") followed by key=value pairs. Requiring the line to
    start with a real action keeps ordinary prose from firing anything.
    """
    t = _tools()
    if not t:
        return reply, []
    known = set(t.ACTIONS) | set(t.UI_ACTIONS)
    acted, kept = [], []

    for line in reply.splitlines():
        raw = line.strip()
        bare = raw
        if bare.startswith("<<"):
            bare = bare[2:]
        if bare.endswith(">>"):
            bare = bare[:-2]
        bare = bare.strip()
        low = bare.lower()
        if low.startswith("act "):
            bare = bare[4:].strip()
        elif low.startswith("act:"):
            bare = bare[4:].strip()

        parts = bare.split()
        if not parts or parts[0].lower() not in known:
            # An "ACT <something we don't have>" line is a failed tool call,
            # not prose. Report it as a miss rather than printing it verbatim.
            if re.match(r"^\s*(<<)?\s*ACT\b", line, re.I):
                emit("acted", action=(parts[0].lower() if parts else "?"),
                     args={}, v="I don't have an action called '%s'"
                              % (parts[0] if parts else "?"), direct=False)
                continue
            kept.append(line)
            continue

        name = parts[0].lower()
        args = {}
        trailing = []
        for tok in parts[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k] = v
            else:
                trailing.append(tok)
        # "open what=shots picture.jpg" -> glue the stray word onto the last
        # value rather than dropping the user's actual intent on the floor
        if trailing and args:
            last = list(args)[-1]
            args[last] = (args[last] + " " + " ".join(trailing)).strip()
        elif trailing and not args:
            args["what"] = " ".join(trailing)

        # Repeat guard. Asked "take a photo" then "did that work?", a small
        # model fires cam_snap AGAIN -- it pattern-matches the topic instead of
        # reading the question. Telling it not to in the prompt does not hold.
        # So: if it just did this exact thing and the user did not clearly ask
        # again, answer from the record instead of re-running.
        recent = _recent_same(name, args)
        if recent and not _explicitly_requested(name, user_msg):
            emit("acted", action=name, args=args,
                 v="already done %s — %s" % (_ago(recent), recent["result"]),
                 direct=False, repeated=True)
            kept.append(_recap(recent))
            continue

        out = _run_act(name, args)
        if out is not None:
            acted.append(name)
            emit("acted", action=name, args=args, v=out, direct=False)

    cleaned = "\n".join(kept).strip()
    return cleaned, acted


def _act_block():
    """
    Teach the model the action vocabulary. Kept short on purpose: a 3B model
    given 40 tools will call the wrong one, and anything phrased plainly enough
    to be unambiguous was already caught by the regex fast path before we got
    here. So this lists the useful-but-not-literal ones and one syntax rule.
    """
    t = _tools()
    if not t:
        return ""
    names = ", ".join(sorted(t.ACTIONS.keys()))
    return (
        "\n\nYou can act on this machine. To do so, put a directive on its own "
        "line in your reply, exactly like <<ACT name key=value>>. It is removed "
        "before the user sees the text, so also say in words what you did.\n"
        "The ONLY valid action names are: " + names + "\n"
        "Examples:\n"
        "  run a program:      <<ACT run cmd=btop>>\n"
        "  open an app/file:   <<ACT open what=firefox>>\n"
        "  read the screen:    <<ACT screen_read>>\n"
        "  take a photo:       <<ACT cam_snap>>\n"
        "  look up a domain:   <<ACT dns host=classroom.google.com>>\n"
        "  open a site/search: <<ACT web q=arch wiki pacman>>\n"
        "  window control:     <<ACT fullscreen>> <<ACT workspace n=3>>\n"
        "Rules: pick the name from that list VERBATIM -- 'snap picture' and "
        "'take photo' are not action names, 'cam_snap' is. If nothing in the "
        "list does what was asked, say so plainly in one line and emit no "
        "directive. Never claim you did something without emitting one."
    )


def _speak(text):
    """Fire TTS if the user has it on and an engine is actually wired.

    The UI speaks replies itself (it has the volume, the stop button, and
    cancels the previous line). Speaking here as well made every reply play
    twice, overlapping -- so the brain only speaks when run without the UI."""
    if not os.environ.get("TAR_BRAIN_SPEAKS"):
        return
    cfg = config()
    if not cfg.get("speak") or not text:
        return
    say = os.path.join(HERE, "tar_say.sh")
    if not os.path.exists(say):
        return
    try:
        subprocess.Popen(["bash", say, text[:600]],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def _run_act(name, args, direct=False):
    t = _tools()
    if not t:
        return None
    t.DIRECT = direct           # user typed it themselves: no confirmation gate
    try:
        out = t.run(name, args)
    except Exception as e:
        out = "action failed: %s" % e
    finally:
        t.DIRECT = False
    if out is not None:
        act_log(name, args, out)
    return out


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
TEXT_EXT = (".txt", ".md", ".log", ".json", ".yaml", ".yml", ".toml", ".conf",
            ".ini", ".csv", ".py", ".sh", ".qml", ".js", ".ts", ".c", ".h",
            ".cpp", ".rs", ".go", ".fish", ".service", ".desktop")


def load_attachment(path):
    """
    Turn a file into something the model can actually use.

    Images become base64 for a vision model; text files become quoted context.
    Returns (kind, payload, note) where kind is "image" | "text" | None.
    """
    path = os.path.expanduser((path or "").strip().strip('"\''))
    if not path or not os.path.isfile(path):
        return None, None, "no such file: %s" % path
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in IMAGE_EXT:
            import base64
            with open(path, "rb") as f:
                return "image", base64.b64encode(f.read()).decode(), \
                       os.path.basename(path)
        if ext in TEXT_EXT or os.path.getsize(path) < 200_000:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read(120_000)
            return "text", body, os.path.basename(path)
    except OSError as e:
        return None, None, "cannot read %s: %s" % (path, e)
    return None, None, "don't know how to read %s" % os.path.basename(path)


def vision_model(reg):
    """A pulled vision model, or None."""
    have = installed()
    for cand in ((reg.get("tasks", {}).get("vision") or {}).get("prefer") or []):
        if cand in have:
            return cand
    return None


def cloud_enabled(cfg=None):
    cfg = cfg or config()
    if cfg.get("backend") != "claude":
        return False
    try:
        sys.path.insert(0, HERE)
        import tar_cloud
        return bool(tar_cloud.api_key())
    except Exception:
        return False


def cloud_rows(cfg):
    """Cloud models as rows for the model list. installed = key present."""
    try:
        sys.path.insert(0, HERE)
        import tar_cloud
    except Exception:
        return []
    on = cloud_enabled(cfg)
    cur = cfg.get("cloud_model")
    rows = []
    for m in tar_cloud.MODELS:
        prov = tar_cloud.provider_of(m["id"])
        rows.append({
            "model": m["id"], "tier": "cloud", "lane": prov, "size_gb": 0,
            "desc": m.get("blurb", ""),
            "installed": bool(tar_cloud.api_key(prov)) and tar_cloud.have_sdk(prov),
            "active": on and m["id"] == cur,
        })
    return rows


def is_cloud_model(name):
    try:
        sys.path.insert(0, HERE)
        import tar_cloud
        return any(m["id"] == name for m in tar_cloud.MODELS) or \
            name.startswith(("gemini-", "claude-"))
    except Exception:
        return False


_FAIL_PREFIXES = ("don't know how", "no window matching", "no app window",
                  "open what", "focus what", "which workspace", "need a size",
                  "could not", "no such", "no running process", "refusing to kill")


def _act_failed(out):
    return isinstance(out, str) and out.lower().startswith(_FAIL_PREFIXES)


def chat(message, no_history=False, no_memory=False, no_act=False,
         attach=None):
    cfg = config()
    # NOTE ON ORDER: the deterministic matcher runs BEFORE the cloud backend,
    # not after. An unambiguous command ("open firefox", "screenshot") is then
    # executed locally -- instantly, free, and with no chance of a model
    # deciding to talk about it instead of doing it. The cloud model only sees
    # what the matcher could not resolve, which is exactly the loose phrasing
    # it is there for.
    _cloud = cloud_enabled(cfg)

    # ---- fast path: a command we can recognise outright needs no model at all.
    if not no_act:
        t = _tools()
        if t:
            # Compound first: "open chrome and look up X" is two actions.
            chain = t.match_all(message) if hasattr(t, "match_all") else []
            if chain is None:
                t = None            # the shortcut can't do this safely
        if t:
            if len(chain) > 1:
                names = []
                for nm, ag in chain:
                    res = _run_act(nm, ag, direct=True)
                    names.append(nm)
                    emit("acted", action=nm, args=ag, v=res, direct=True)
                history_append("user", message)
                history_append("assistant", "[did: %s]" % ", ".join(names))
                emit("state", v="idle")
                emit("done", reply="", model=None, ms=0, first_token_ms=0,
                     saved=None, acted=names)
                return
            # a chain can collapse to one action ("open a terminal and run
            # btop" -> run btop); use it rather than re-matching the raw text
            name, args = chain[0] if len(chain) == 1 else t.match(message)
            if name:
                out = _run_act(name, args, direct=True)
                if _act_failed(out):
                    # The shortcut guessed and missed ("open the first link"
                    # -> an app called "first link"). Nothing happened, so let
                    # the cloud model, which has the conversation, try it.
                    name = None
            if name:
                emit("acted", action=name, args=args, v=out, direct=True)
                # Deliberately NOT written to conversation history. Action
                # output is machine text ("up 3 hours · 3.5Gi / 7.4Gi RAM"), and
                # once it sits in history as an assistant turn a small model
                # will parrot it back for any question it can't answer -- which
                # is exactly how "you did it?" started returning uptime forever.
                # No reply text and no speech on screen -- the ACTED line
                # already says it. But the turn IS recorded, as a short
                # third-person note rather than the raw machine output, so the
                # next question ("did it work?") has something to refer to.
                history_append("user", message)
                history_append("assistant", "[did: %s]" % name)
                emit("state", v="idle")
                emit("done", reply="", model=None, ms=0,
                     first_token_ms=0, saved=None, acted=name)
                return

    if _cloud:
        try:
            sys.path.insert(0, HERE)
            import tar_cloud
            tar_cloud.chat(message)
            return
        except Exception as e:
            emit("info", v="cloud failed (%s) — falling back to local" % e)

    if not start_ollama():
        die("ollama is not running and could not be started "
            "(install it, or: systemctl start ollama)")

    reg = registry()
    images = []
    if attach:
        kind, payload, note = load_attachment(attach)
        if kind == "image":
            images.append(payload)
            emit("attached", kind="image", name=note, path=attach)
        elif kind == "text":
            message = ("Here is the contents of %s:\n\n%s\n\n%s"
                       % (note, payload, message))
            emit("attached", kind="text", name=note, path=attach)
        else:
            emit("error", v=note)
            return

    task = classify_task(message, has_image=bool(images))
    if images:
        vm = vision_model(reg)
        if not vm:
            # The auto mode SHOULD notice it cannot see. Say so and point at
            # the fix instead of hallucinating a description of the photo.
            emit("needs_engine", cap="vision",
                 v="I can't see images yet — no vision model installed. "
                   "Open /settings and install one (moondream is ~1.7GB and "
                   "fits this machine).")
            emit("state", v="idle")
            emit("done", reply="", model=None, ms=0, first_token_ms=0,
                 saved=None, task="vision", acted=None)
            return
    info = resolve(reg=reg, cfg=cfg, task=task)
    info, gnote = govern(reg, cfg, info)
    info = sticky_ctx(info)
    if gnote:
        emit("info", v="speed: " + gnote)
    model = info["model"]
    # One model in RAM at a time. This is the difference between 4s and 40s.
    unload_except(model)

    if model not in installed():
        emit("state", v="pulling")
        emit("info", v="fetching " + model + " (first run)")
        pull(model)

    # Explicit memory command, handled before inference so it always lands.
    saved = None
    m = REMEMBER_RE.match(message.strip())
    if m:
        saved = mem_add(m.group(1))
        if saved:
            emit("mem", v=saved["text"])

    system = PERSONA.format(user=cfg.get("user", "the user"),
                            voice=voice_block(cfg))
    if not no_memory:
        system += mem_block()
    # Opt-in: this costs ~200 tokens of prefill on EVERY turn, and small local
    # models ignore the directive anyway. The regex fast path is what actually
    # runs commands. Enable with: config tools_in_prompt = true
    if not no_act and cfg.get("tools_in_prompt"):
        system += _act_block()
    if not no_act:
        system += act_block_context()   # what it has already done
    system += persona_tail(cfg)

    msgs = [{"role": "system", "content": system}]
    if not no_history:
        msgs += history_tail(int(cfg.get("history_turns", 8)))
    um = {"role": "user", "content": message}
    if images:
        um["images"] = images          # ollama takes base64 here
    msgs.append(um)

    emit("state", v="thinking", model=model, tier=info["tier"],
         lane=info["lane"], task=info["task"], reason=info["reason"])
    power_boost()

    payload = {
        "model": model,
        "messages": msgs,
        "stream": True,
        # keep_alive stops ollama dropping the model between turns; a cold
        # reload here costs more than every other latency source combined.
        "keep_alive": (registry().get("speed") or {}).get("keep_alive", "30m"),
        "options": {"num_ctx": info["num_ctx"],
                    "temperature": info.get("temperature", 0.6)},
    }
    req = urllib.request.Request(
        OLLAMA + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})

    started = time.time()
    parts = []
    first = None
    try:
        with urllib.request.urlopen(req, timeout=None) as r:
            for raw in r:
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw.decode())
                except json.JSONDecodeError:
                    continue
                if d.get("error"):
                    die(str(d["error"]))
                tok = (d.get("message") or {}).get("content", "")
                if tok:
                    if first is None:
                        first = time.time() - started
                        emit("state", v="speaking")
                    parts.append(tok)
                    emit("token", v=tok)
                if d.get("done"):
                    break
    except urllib.error.HTTPError as e:
        power_restore(); die("ollama rejected the request: %s %s" % (e.code, e.reason))
    except (urllib.error.URLError, OSError) as e:
        power_restore(); die("lost connection to ollama: %s" % e)

    reply = "".join(parts).strip()

    # The model may ask for an action with <<ACT name k=v>>. Strip the directive
    # out of what the user sees, then run it.
    acted = []
    bad_act = False
    if not no_act and reply:
        before = reply
        reply, acted = extract_and_run(reply, message)
        bad_act = (not acted) and bool(re.search(r"(?mi)^\s*(<<)?\s*ACT\b",
                                                 before))

    history_append("user", message)
    if reply:
        history_append("assistant", reply)
    elif acted:
        history_append("assistant", "[did: %s]" % ", ".join(acted))
    _speak(reply)

    # idle first: the UI's idle handler resets the status line, and we want the
    # timing from "done" to be what survives on screen.
    power_restore()
    # Offer a bigger model when this one visibly failed -- never switch behind
    # the user's back, and never propose something that won't fit in RAM.
    why = looks_like_failure(message, reply, acted, bad_act)
    if why and not cfg.get("model"):
        up = escalate_target(reg, model)
        if up:
            emit("suggest_upgrade",
                 current=model, target=up["model"],
                 size_gb=up["size_gb"], budget_gb=up["budget_gb"],
                 reason=why,
                 v="%s failed that (%s). Retry with %s (%.1f GB, fits your "
                   "%.1f GB free)?" % (model.split("/")[-1], why,
                                       up["model"].split("/")[-1],
                                       up["size_gb"], up["budget_gb"]),
                 retry=message)

    ft_ms = int((first or 0) * 1000)
    perf_record(model, info["num_ctx"], ft_ms)

    emit("state", v="idle")
    emit("done", reply=reply, model=model,
         ms=int((time.time() - started) * 1000),
         first_token_ms=ft_ms,
         saved=(saved["text"] if saved else None),
         task=info["task"], acted=(acted or None),
         num_ctx=info["num_ctx"], governor=info.get("governor"))


# ----------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(add_help=False)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("probe")
    sub.add_parser("models")
    sub.add_parser("ensure")
    sub.add_parser("undo")
    p = sub.add_parser("set-model"); p.add_argument("model")
    p = sub.add_parser("set-lane"); p.add_argument("lane", choices=["safe", "open"])
    p = sub.add_parser("autotier"); p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("chat")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--no-memory", action="store_true")
    p.add_argument("--no-act", action="store_true",
                   help="disable tool use for this turn")
    p.add_argument("--attach", help="a file (image or text) to include")
    p.add_argument("--voice", action="store_true",
                   help="the message came from the microphone (may be misheard)")
    p = sub.add_parser("act", help="run an action directly")
    p.add_argument("action")
    p.add_argument("args", nargs="*", help="key=value pairs")
    p = sub.add_parser("try", help="match text as a command without a model")
    p.add_argument("text", nargs="+")
    sub.add_parser("tools", help="list available actions")
    p = sub.add_parser("warm", help="preload the model and prime the prompt cache")
    p.add_argument("--task", default="chat")
    p = sub.add_parser("speed", help="speed policy: fast | auto | quality")
    p.add_argument("mode", choices=["fast", "auto", "quality"])
    sub.add_parser("perf", help="show measured first-token latencies")
    p = sub.add_parser("use-model", help="pin a model (accepting an upgrade)")
    p.add_argument("model")
    p = sub.add_parser("backend", help="local | claude")
    p.add_argument("which", choices=["local", "claude"])
    p = sub.add_parser("persona", help="set how T.A.R. talks (or 'reset')")
    p.add_argument("text", nargs="*")
    p = sub.add_parser("cloud-model", help="pin the cloud model")
    p.add_argument("model")
    sub.add_parser("shutdown",
                   help="unload the model and release its RAM/CPU")
    sub.add_parser("sessions", help="list past conversations")
    sub.add_parser("session-new", help="start a fresh conversation")
    sub.add_parser("session-auto", help="new conversation if the current one is idle 30+ min")
    p = sub.add_parser("session-open", help="reopen a past conversation")
    p.add_argument("id")
    p = sub.add_parser("mem")
    p.add_argument("op", choices=["list", "add", "forget"])
    p.add_argument("text", nargs="*")

    a = ap.parse_args()

    if a.cmd == "probe":
        info = resolve()
        info["ollama"] = ollama_up()
        info["installed"] = sorted(installed())
        cfg = config()
        if cloud_enabled(cfg):
            import tar_cloud
            info["model"] = cfg.get("cloud_model")
            info["tier"] = "cloud"
            info["lane"] = tar_cloud.provider_of(info["model"])
            info["reason"] = "cloud: " + str(info["model"])
        emit("probe", **info)

    elif a.cmd == "models":
        reg, cfg = registry(), config()
        have = installed()
        active = resolve(reg, cfg)["model"]
        rows = []
        for tier, spec in reg["tiers"].items():
            for lane in ("safe", "open"):
                rows.append({
                    "model": spec[lane]["model"], "tier": tier, "lane": lane,
                    "size_gb": spec[lane]["size_gb"],
                    "installed": spec[lane]["model"] in have,
                    "active": spec[lane]["model"] == active,
                })
        for name, spec in reg.get("extras", {}).items():
            if name.startswith("_"):
                continue
            rows.append({
                "model": name, "tier": "extra", "lane": spec.get("lane", "open"),
                "size_gb": spec.get("size_gb"), "desc": spec.get("desc", ""),
                "installed": name in have, "active": name == active,
            })
        rows += cloud_rows(cfg)
        if cloud_enabled(cfg):
            active = cfg.get("cloud_model")
        emit("models", rows=rows, active=active, lane=cfg["lane"],
             autotier=bool(cfg.get("autotier", True)) and not cfg.get("model"))

    elif a.cmd == "set-model":
        cfg = config()
        if is_cloud_model(a.model):
            # picking a cloud model IS switching to the cloud brain
            cfg["cloud_model"] = a.model
            cfg["backend"] = "claude"
            save_config(cfg)
            if cloud_enabled(cfg):
                unload_except(None)       # free the RAM the local model held
                emit("ok", v="cloud brain: " + a.model)
            else:
                import tar_cloud
                emit("ok", v="cloud model set to %s, but %s"
                             % (a.model, tar_cloud.status()[1]))
        else:
            cfg["model"] = a.model; cfg["autotier"] = False
            cfg["backend"] = "local"      # picking a local model leaves cloud
            save_config(cfg)
            emit("ok", v="pinned to " + a.model)

    elif a.cmd == "set-lane":
        cfg = config(); cfg["lane"] = a.lane
        save_config(cfg)
        emit("ok", v="lane = " + a.lane, model=resolve(cfg=cfg)["model"])

    elif a.cmd == "autotier":
        cfg = config()
        cfg["autotier"] = (a.state == "on")
        if a.state == "on":
            cfg["model"] = None
        save_config(cfg)
        emit("ok", v="autotier " + a.state, model=resolve(cfg=cfg)["model"])

    elif a.cmd == "undo":
        if not os.path.exists(CONFIG_PREV):
            emit("ok", v="nothing to undo")
        else:
            prev = _load(CONFIG_PREV, None)
            if prev is None:
                emit("error", v="undo snapshot unreadable")
            else:
                # swap: the current state becomes the next undo step
                cur = _load(CONFIG_PATH, {})
                _save(CONFIG_PATH, prev)
                _save(CONFIG_PREV, cur)
                emit("ok", v="reverted last change",
                     model=resolve(cfg=config())["model"])

    elif a.cmd == "ensure":
        if not start_ollama():
            die("ollama unavailable")
        model = resolve()["model"]
        if model in installed():
            emit("ok", v=model + " already installed")
        else:
            pull(model)

    elif a.cmd == "chat":
        if a.voice:
            os.environ["TAR_VOICE"] = "1"
        chat(a.message, a.no_history, a.no_memory, a.no_act, a.attach)

    elif a.cmd == "act":
        t = _tools()
        if not t:
            die("tar_tools.py is missing next to tar_brain.py")
        args = dict(kv.split("=", 1) for kv in a.args if "=" in kv)
        if a.action not in t.ACTIONS and a.action not in t.UI_ACTIONS:
            die("unknown action: " + a.action)
        emit("acted", action=a.action, args=args,
             v=t.run(a.action, args), direct=True)

    elif a.cmd == "try":
        t = _tools()
        if not t:
            die("tar_tools.py is missing next to tar_brain.py")
        text = " ".join(a.text)
        name, args = t.match(text)
        if not name:
            emit("nomatch", v=text)
        else:
            emit("acted", action=name, args=args,
                 v=t.run(name, args), direct=True)

    elif a.cmd == "warm":
        warm(a.task)

    elif a.cmd == "speed":
        cfg = config()
        cfg["speed"] = a.mode
        save_config(cfg)
        emit("ok", v="speed policy: " + a.mode)

    elif a.cmd == "perf":
        p = perf_load()
        rows = [{"key": k, "first_token_ms": int(v["ft_ms"]), "n": v["n"]}
                for k, v in sorted(p["runs"].items(),
                                   key=lambda kv: kv[1]["ft_ms"])]
        emit("perf", rows=rows, speed=config().get("speed", "auto"))

    elif a.cmd == "use-model":
        cfg = config()
        cfg["model"] = a.model
        save_config(cfg)
        emit("ok", v="pinned " + a.model + " — /auto to go back")

    elif a.cmd == "backend":
        cfg = config()
        cfg["backend"] = a.which
        save_config(cfg)
        unload_except(None)         # cloud: nothing local needed; local: reload fresh
        emit("backend", which=a.which,
             v=("cloud brain (Claude API)" if a.which == "claude"
                else "local brain (offline)"))

    elif a.cmd == "persona":
        cfg = config()
        text = " ".join(a.text).strip()
        if not text:
            emit("ok", v="persona: " + (cfg.get("persona") or "default (natural, no roleplay)"))
        elif text.lower() in ("reset", "default", "off", "clear"):
            cfg["persona"] = ""
            save_config(cfg)
            emit("ok", v="persona reset to the default voice")
        else:
            cfg["persona"] = text[:600]
            save_config(cfg)
            emit("ok", v="persona set: " + text[:120])

    elif a.cmd == "cloud-model":
        cfg = config()
        cfg["cloud_model"] = a.model
        save_config(cfg)
        emit("ok", v="cloud model: " + a.model)

    elif a.cmd == "shutdown":
        # keep_alive means the model outlives the UI. Nothing in btop is
        # obviously "the assistant" at that point -- it is llama-server -- so
        # closing the window has to do this explicitly.
        freed = []
        for name in resident_models():
            try:
                req = urllib.request.Request(
                    OLLAMA + "/api/generate",
                    data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
                freed.append(name)
            except Exception:
                pass
        power_restore()
        emit("ok", v=("unloaded " + ", ".join(freed)) if freed
                     else "nothing was loaded")

    elif a.cmd == "sessions":
        emit("sessions", rows=session_list())

    elif a.cmd == "session-auto":
        if session_auto():
            emit("session", id=session_id(), turns=None, v="new chat (the last one was a while ago)")

    elif a.cmd == "session-new":
        session_new()

    elif a.cmd == "session-open":
        session_open(a.id)

    elif a.cmd == "tools":
        t = _tools()
        if not t:
            die("tar_tools.py is missing next to tar_brain.py")
        emit("actions", rows=t.schema())

    elif a.cmd == "mem":
        if a.op == "list":
            emit("mem_list", facts=mem_load()["facts"])
        elif a.op == "add":
            e = mem_add(" ".join(a.text))
            emit("ok", v=("saved: " + e["text"]) if e else "already known")
        else:
            n = mem_forget(" ".join(a.text))
            emit("ok", v="forgot %d fact(s)" % n)
    else:
        emit("error", v="no subcommand; try: probe | models | chat -m '...'")
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
