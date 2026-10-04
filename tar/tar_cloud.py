#!/usr/bin/env python3
"""
T.A.R. cloud backend — Claude via the Anthropic API, or Gemini via Google's
API (free tier, no SDK: plain urllib). The provider follows the model id:
gemini-* goes to Google, everything else to Anthropic.

Why this exists: a 1.5B model on a 7.4GB CPU-only laptop cannot reliably turn
"open a terminal idk dude foot" into the right action, and no amount of regex
around it changes that. This backend swaps the brain for a real one and uses
PROPER tool calling — the model picks the action and fills the arguments, so
loose phrasing, follow-ups ("run btop on that terminal") and references ("close
it") work because the model actually understands them.

The local backend stays the default. This is opt-in, per-user, and needs a key.

Protocol out is identical to tar_brain.py, so tar.qml needs no new parser.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
KEYS_PATH = os.path.join(DATA, "keys.json")
VENV = os.path.join(DATA, "venv")

# The SDK is installed into T.A.R.'s own venv so it never touches the system
# python (Arch marks that externally-managed and pip refuses anyway).
_site = os.path.join(VENV, "lib")
if os.path.isdir(_site):
    for d in sorted(os.listdir(_site)):
        sp = os.path.join(_site, d, "site-packages")
        if os.path.isdir(sp):
            sys.path.insert(0, sp)


def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------ keys

def load_keys():
    try:
        with open(KEYS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_key(provider, key):
    keys = load_keys()
    keys[provider] = key
    os.makedirs(DATA, exist_ok=True)
    tmp = KEYS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2)
    os.chmod(tmp, 0o600)          # not world-readable, ever
    os.replace(tmp, KEYS_PATH)
    try:
        os.chmod(KEYS_PATH, 0o600)
    except OSError:
        pass


def provider_of(model):
    return "google" if str(model or "").startswith("gemini") else "anthropic"


def key_provider(key):
    """Which provider a pasted key belongs to. Anthropic keys are sk-ant-..."""
    return "anthropic" if key.startswith("sk-ant") else "google"


def active_model():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            m = json.load(f).get("cloud_model")
    except (OSError, ValueError):
        m = None
    return m or default_model()


def api_key(provider=None):
    provider = provider or provider_of(active_model())
    if provider == "google":
        return (os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or load_keys().get("google") or "")
    return (os.environ.get("ANTHROPIC_API_KEY")
            or load_keys().get("anthropic") or "")


def have_sdk(provider=None):
    if (provider or provider_of(active_model())) == "google":
        return True                     # Gemini goes over plain urllib
    try:
        import anthropic                # noqa: F401
        return True
    except ImportError:
        return False


def status():
    """(ready, reason) for the cloud model that is currently selected."""
    prov = provider_of(active_model())
    if not have_sdk(prov):
        return False, "the anthropic SDK isn't installed"
    if not api_key(prov):
        return False, "no %s API key set" % ("Gemini" if prov == "google"
                                              else "Anthropic")
    return True, None


# ------------------------------------------------------------------ models

MODELS = [
    {"id": "claude-opus-5", "name": "Opus 5",
     "blurb": "Most capable. Best at loose phrasing and multi-step work.",
     "in_per_mtok": 5.00, "out_per_mtok": 25.00, "default": True},
    {"id": "claude-sonnet-5", "name": "Sonnet 5",
     "blurb": "Strong and noticeably cheaper. Good daily driver.",
     "in_per_mtok": 2.00, "out_per_mtok": 10.00},
    {"id": "claude-haiku-4-5", "name": "Haiku 4.5",
     "blurb": "Fastest and cheapest. Fine for commands, weaker at reasoning.",
     "in_per_mtok": 1.00, "out_per_mtok": 5.00},
    {"id": "gemini-flash-lite-latest", "name": "Gemini Flash-Lite",
     "blurb": "Google free tier. Fast, generous rate limit, good at commands.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0},
    {"id": "gemini-flash-latest", "name": "Gemini Flash",
     "blurb": "Google free tier. Smarter, but only ~5 requests a minute free.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0},
]


def default_model():
    return next((m["id"] for m in MODELS if m.get("default")), MODELS[0]["id"])


# ------------------------------------------------------------------ tools

def build_tools():
    """
    Expose T.A.R.'s action registry as real Anthropic tool definitions.

    One generic tool rather than 50 separate ones: the action set changes as
    the rice grows, and a single well-described dispatcher keeps the tool block
    small (it is re-sent every turn and is part of the cached prefix).
    """
    import tar_tools as T
    names = sorted(set(T.ACTIONS) | set(T.UI_ACTIONS))
    lines = []
    for n in names:
        entry = T.ACTIONS.get(n)
        helptext = entry[1] if entry else T.UI_ACTIONS.get(n, "")
        lines.append("  %s — %s" % (n, helptext))
    return [{
        "name": "run_action",
        "description": (
            "Perform an action on the user's Linux machine (BITE-OS, Hyprland). "
            "Use this whenever they ask for something to happen rather than "
            "asked a question. You may call it several times in one turn — for "
            "example open an app and then search the web.\n\n"
            "Available actions:\n" + "\n".join(lines)
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": names,
                           "description": "which action to run"},
                "args": {
                    "type": "object",
                    "description": (
                        "arguments for the action, e.g. {\"what\": \"firefox\"} "
                        "for open, {\"what\": \"kitty\"} for closewin/"
                        "fullscreen/float/center/pin (names the window), "
                        "{\"cmd\": \"btop\"} for run, "
                        "{\"q\": \"arch wiki\"} for web, "
                        "{\"w\": 1200, \"h\": 800} for resize"
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "strict": False,
    }]


def normalize_call(name, payload):
    """Models don't always use the one run_action tool the way it's declared:
    some call `click_on` directly, or put the args flat next to `action`.
    Accept all of those shapes -> (action, args)."""
    import tar_tools as T
    payload = dict(payload or {})
    action = payload.pop("action", "") or ""
    if not action and name and name != "run_action" and (
            name in T.ACTIONS or name in T.UI_ACTIONS):
        action = name                   # called the action by its own name
    args = payload.pop("args", None)
    if args is None:
        args = payload                  # flat: {"action": .., "target": ..}
    elif payload:
        args = dict(args) if isinstance(args, dict) else {"what": str(args)}
        args.update(payload)
    return action, args


def run_tool(name, payload):
    os.environ["TAR_BACKEND"] = "cloud"     # unlocks the shell action
    import tar_tools as T
    action, args = normalize_call(name, payload)
    if not isinstance(args, dict):
        args = {"what": str(args)}
    args = {k: (str(v) if not isinstance(v, str) else v)
            for k, v in args.items()}
    if action not in T.ACTIONS and action not in T.UI_ACTIONS:
        return ("no such action: %r. Call run_action with action=<one of the "
                "listed names> and args={...}, e.g. action='click_on', "
                "args={'target': 'the big cookie', 'times': 10}" % action)
    if action in ("confirm", "cancel") or args.get("_confirmed"):
        # only the USER can approve a parked action, by replying yes
        return ("you can't confirm actions yourself -- ask the user to reply "
                "'yes' to go ahead or 'no' to cancel")
    try:
        out = T.run(action, args)
    except Exception as e:          # never let a tool crash the turn
        out = "action failed: %s" % e
    return out if out is not None else "done"


# ------------------------------------------------------------------ chat

SYSTEM = (
    "You are T.A.R., {user}'s assistant running inside BITE-OS, an Arch/CachyOS "
    "Hyprland rice.\n{voice}\n\n"
    "You have real control of this machine through the run_action tool. When "
    "they ask for something to happen, CALL THE TOOL — do not describe what "
    "you would do, do not ask for confirmation, and never say you are unable "
    "to open, run or close something that is in the action list. This is the "
    "owner's own machine and they are asking you to operate it; that is the "
    "entire purpose of the tool. Afterwards say in one short line what you "
    "did.\n\n"
    "Handle loose phrasing: \"open a terminal idk dude foot\" means "
    "run_action open what=foot. Handle references to earlier actions: \"run "
    "btop on that terminal\" and \"close it\" refer to things you already "
    "opened — the action results tell you what those were. Window actions "
    "(closewin, fullscreen, float, move, sendto...) target the app window "
    "the user last used, never T.A.R. itself; when the user refers to "
    "something you opened, pass its name as what= (\"close it\" right after "
    "opening kitty = closewin what=kitty). The 'close' UI action shuts T.A.R. "
    "down — use it ONLY if they say close yourself / close T.A.R.\n\n"
    "CHECK YOUR WORK: action results now end with VERIFIED or NOT VERIFIED. "
    "If NOT VERIFIED, don't claim success: look, then retry a different way "
    "(different target wording, wait, close a popup) -- at most 2 retries -- "
    "and if it still fails, tell the user plainly what didn't work.\n"
    "LONG TASKS: 'until I stop you' / 'keep doing it' / 'play this for me' -> "
    "keep_doing with a clear task that includes the strategy. If the site/app "
    "is already open, DON'T open it again -- just keep going in it.\n"
    "MULTI-STEP: do every step the user asked, in order, in this same turn "
    "(e.g. open the site, then click_on the thing times=5). Don't stop after "
    "step one.\n"
    "THINK MIDWAY: things pop up. After opening a site/app, and whenever a "
    "click target isn't found, call look. If a dialog is in the way, handle "
    "it yourself when the choice is obvious and harmless -- pick English for "
    "a language prompt, 'Got it'/'OK'/close (X) for notices and cookie "
    "banners, 'Not now' for upsells -- then carry on. STOP and ask the user "
    "only for real decisions: logins, payments, permissions, anything "
    "personal or irreversible.\n"
    "Prefer the dedicated actions (remind, wifi, bluetooth, battery, "
    "weather, calc, files, find, move, copy, rename, mkdir, trash, restore, "
    "folder, download, kill, processes, power, dnd, nightlight, record, play, "
    "notify...) -- they are reliable. Chain several actions for multi-step "
    "requests. "
    "SCREEN & MOUSE: you can see and click. 'click X' / 'press X' / 'that "
    "button' -> click_on target=<what the user described> (it finds it by "
    "text or vision and clicks; T.A.R. hides itself so it never clicks "
    "itself). Not sure what they mean by 'that'? call look YOURSELF (never tell "
    "the user to), then click it, or if several things fit, ask which one and "
    "name what you see. Typing into "
    "a field -> type_into. Scrolling -> scroll with what=<window>. Never fake "
    "a click with key presses. 'ask claude ...' -> ask_claude.\n"
    "ALWAYS TRY: tools get installed and things change during a chat. If the "
    "user asks for something, call the action again -- never just repeat an "
    "earlier failure or 'I can't' from the conversation.\n"
    "HONESTY: never invent personal info (emails, passwords, names, "
    "addresses) -- ask, or use what's in memory. Only say you did something "
    "if an action result shows it worked; if a result says it failed or "
    "NEEDS CONFIRMATION, say so plainly. If you can't do something, say what "
    "you can't do and offer the closest thing you can. One monitor unless "
    "the monitors action says otherwise.\n"
    "If no dedicated action fits, use the shell action -- it runs any "
    "command and returns the output, so you can do almost anything. This "
    "machine: CachyOS (Arch), Hyprland on Wayland, fish shell (shell action "
    "runs bash). Useful tools: hyprctl (clients -j, dispatch exec/"
    "focuswindow/closewindow/workspace/movetoworkspace), wtype (typing), "
    "grim+slurp (screenshots), playerctl, wpctl/pactl (audio), brightnessctl, "
    "nmcli (wifi), bluetoothctl, notify-send, wl-copy/wl-paste, cliphist, "
    "xdg-open, trash-put (delete = trash, never rm), jq, curl. Home folders "
    "are Hebrew-localized: use `xdg-user-dir DOWNLOAD|DOCUMENTS|PICTURES|"
    "VIDEOS|MUSIC|DESKTOP`, never ~/Downloads. Check results; never claim "
    "you did something an action result doesn't show. If a command is "
    "refused, tell the user the exact command to run themselves.\n\n"
    "This is the machine owner asking about their own system and their own "
    "code. Security tooling, pentesting scripts, shell and kernel work are "
    "normal here; answer straight. If you genuinely cannot do something, say "
    "so in one line."
)


def live_status():
    """What T.A.R. can do RIGHT NOW, checked fresh every message. Overrides
    anything older in the conversation (e.g. 'mouse tool isn't installed'
    from before it was installed)."""
    import tar_tools as T
    try:
        click = T._pointer_ok()
    except Exception:
        click = False
    try:
        mons = T.a_monitors({})
    except Exception:
        mons = "unknown"
    vision = bool(api_key("google"))
    lines = [
        "clicking/scrolling with the mouse: %s" % (
            "AVAILABLE -- use click_on / click / scroll" if click else
            "NOT available yet -- tell the user to open SETUP and install "
            "the Mouse capability (one click, no password)"),
        "seeing the screen (look, click by description): %s" % (
            "AVAILABLE" if vision else "text-only (OCR), no vision key"),
        "monitors: %s" % mons,
    ]
    out = "\n\nLIVE STATUS (checked just now -- this is the truth; ignore any "
    out += "older message or action result in this chat that says otherwise):\n- "
    return out + "\n- ".join(lines)


def chat(message, model=None, max_turns=6):
    import tar_brain as B
    model = model or B.config().get("cloud_model") or default_model()
    if provider_of(model) == "google":
        return chat_gemini(message, model, max_turns)
    key = api_key("anthropic")
    if not key:
        emit("error", v="no API key set — add one in setup (cloud mode)")
        return 1
    try:
        import anthropic
    except ImportError:
        emit("error", v="the anthropic SDK isn't installed — install the "
                        "cloud engine in setup")
        return 1

    import tar_brain as B

    cfg = B.config()
    model = model or cfg.get("cloud_model") or default_model()
    client = anthropic.Anthropic(api_key=key)
    tools = build_tools()

    system = SYSTEM.format(user=cfg.get("user", "the user"), voice=B.voice_block(cfg)) + B.mem_block()
    system += B.act_block_context(n=40, within_s=None)
    system += live_status()
    system += B.persona_tail(cfg)

    history = B.history_tail(int(cfg.get("cloud_history_turns", 40)))
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": message})

    emit("state", v="thinking", model=model, tier="cloud", lane="api",
         task="chat", reason="cloud: " + model)

    acted, reply_parts = [], []
    import time
    started = time.time()
    first = None

    for _turn in range(max_turns):
        try:
            with client.messages.stream(
                model=model,
                max_tokens=8000,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    if first is None:
                        first = time.time() - started
                        emit("state", v="speaking")
                    reply_parts.append(text)
                    emit("token", v=text)
                final = stream.get_final_message()
        except Exception as e:
            emit("error", v="cloud request failed: %s" % e)
            return 1

        if final.stop_reason != "tool_use":
            break

        # run every tool call, then hand all results back in ONE user message
        messages.append({"role": "assistant", "content": final.content})
        results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            out = run_tool(block.name, block.input)
            act = (block.input or {}).get("action", "?")
            acted.append(act)
            B.act_log(act, (block.input or {}).get("args") or {}, out)
            emit("acted", action=act,
                 args=(block.input or {}).get("args") or {},
                 v=out, direct=False)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": str(out)})
        messages.append({"role": "user", "content": results})

    reply = "".join(reply_parts).strip()
    B.history_append("user", message)
    if reply:
        B.history_append("assistant", reply)
    elif acted:
        B.history_append("assistant", "[did: %s]" % ", ".join(acted))
    B._speak(reply)

    emit("state", v="idle")
    emit("done", reply=reply, model=model,
         ms=int((time.time() - started) * 1000),
         first_token_ms=int((first or 0) * 1000),
         saved=None, task="chat", acted=(acted or None), backend="claude")
    return 0


# ------------------------------------------------------------------ gemini

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "%s:generateContent")


def gemini_tools():
    t = build_tools()[0]
    schema = dict(t["input_schema"])
    return [{"functionDeclarations": [{
        "name": t["name"],
        "description": t["description"],
        "parametersJsonSchema": schema,
    }]}]


def gemini_call(key, model, body):
    """One generateContent call. Free-tier 429s get one patient retry."""
    import time
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode()
    for attempt in range(2):
        req = urllib.request.Request(
            GEMINI_URL % model, data=data,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429 and attempt == 0:
                emit("info", v="Gemini rate limit — waiting a moment")
                time.sleep(15)
                continue
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise RuntimeError("HTTP %d: %s" % (e.code, detail[:300]))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # wifi blip / DNS hiccup: one quick retry before giving up
            if attempt == 0:
                time.sleep(2)
                continue
            raise RuntimeError("no connection to Google (%s) -- check your internet"
                               % getattr(e, "reason", e))
    raise RuntimeError("rate limited")


# A reply that says it DID something while no action ran is a bluff.
_CLAIM_RE = None


def _claims_action(text):
    global _CLAIM_RE
    import re
    if _CLAIM_RE is None:
        _CLAIM_RE = re.compile(
            r"\b(typed|typing|clicked|clicking|pressed|opened|opening|closed|"
            r"closing|scrolled|scrolling|sent|moved|launched|deleted|turned (?:it )?"
            r"(?:on|off|up|down)|switched|muted|played|playing|copied|saved|set (?:it|the|a|your)|"
            r"done|on it)\b", re.I)
    return bool(_CLAIM_RE.search(text or ""))


# The user asked for something to HAPPEN (not a question about it).
_DO_RE = None


def _asks_action(text):
    global _DO_RE
    import re
    if _DO_RE is None:
        _DO_RE = re.compile(
            r"^(?:(?:hey |yo |ok |okay |so |pls |please )*(?:can|could|would|will) "
            r"(?:you|u|ya) |please |pls )?(?:click|press|tap|hit|open|close|type|"
            r"scroll|launch|start|play|pause|turn|set|move|send|search|find|show|"
            r"take|mute|switch|focus|drag|select|copy|paste|run|kill|lock)\b", re.I)
    return bool(_DO_RE.match((text or "").strip()))


def chat_gemini(message, model, max_turns=10):
    import time
    import tar_brain as B
    import tar_tools as T
    # what the USER wrote (this turn + recent turns) -- the type guards check
    # personal info against this, so the model can't invent an email
    T.USER_SAID = " \n ".join([h["content"] for h in B.history_tail(6)
                               if h.get("role") == "user"] + [message])

    key = api_key("google")
    if not key:
        emit("error", v="no Gemini API key set — /key <your-key>")
        return 1

    cfg = B.config()
    system = SYSTEM.format(user=cfg.get("user", "the user"), voice=B.voice_block(cfg)) + B.mem_block()
    system += B.act_block_context(n=40, within_s=None)
    system += live_status()
    system += B.persona_tail(cfg)

    contents = []
    for h in B.history_tail(int(cfg.get("cloud_history_turns", 40))):
        role = "model" if h["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    emit("state", v="thinking", model=model, tier="cloud", lane="api",
         task="chat", reason="cloud: " + model)

    acted, reply_parts = [], []
    started = time.time()
    first = None
    tools = gemini_tools()
    nudged = False
    last_check = None           # "ok" / "failed" from the latest verified action

    for _turn in range(max_turns):
        try:
            resp = gemini_call(key, model, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "tools": tools,
                "generationConfig": {"maxOutputTokens": 4000},
            })
        except Exception as e:
            emit("error", v="cloud request failed: %s" % e)
            return 1

        cand = (resp.get("candidates") or [{}])[0]
        content = cand.get("content") or {"role": "model", "parts": []}
        parts = content.get("parts") or []
        calls = [p["functionCall"] for p in parts if "functionCall" in p]

        # Honesty check: claims to have acted, but nothing ran this turn?
        said = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not calls and not acted and not nudged and _asks_action(message):
            # asked to DO something, answered without even trying -- usually
            # parroting an old "I can't" from history. Make it actually try.
            nudged = True
            contents.append(content)
            contents.append({"role": "user", "parts": [{"text": (
                "[system check] You didn't call any action. The user asked you "
                "to do this NOW -- call the action and report its real result. "
                "Earlier failures in this chat may no longer apply.")}]})
            continue
        if not calls and not acted and not nudged and _claims_action(said):
            nudged = True
            contents.append(content)
            contents.append({"role": "user", "parts": [{"text": (
                "[system check] You said you did something, but you called NO "
                "action, so nothing happened. Either call the right action now, "
                "or tell the user plainly that you didn't do it and why (e.g. "
                "you need their email). Never invent personal info.")}]})
            continue

        if not calls and last_check == "failed" and _claims_action(said) and \
                "didn't" not in said.lower() and "not " not in said.lower():
            # the final check said it didn't work -- don't let "done" stand
            said = ("I tried, but it doesn't look like it worked -- nothing changed "
                    "on screen after my last attempt. Want me to try another way?")
            parts = [{"text": said}]
        if not calls and not acted and nudged and _claims_action(said):
            # bluffed again after being told nothing ran: don't show it
            said = ("I didn't actually do that -- no action ran. "
                    "Tell me exactly what you want (and anything I'd need, "
                    "like the text to type) and I'll do it for real.")
            parts = [{"text": said}]
        for p in parts:
            if p.get("text") and not p.get("thought"):
                if first is None:
                    first = time.time() - started
                    emit("state", v="speaking")
                reply_parts.append(p["text"])
                emit("token", v=p["text"])

        if not calls:
            break

        # Echo the model turn back verbatim: Gemini 3 rejects a follow-up
        # whose function calls lost their thoughtSignature.
        contents.append(content)
        results = []
        for c in calls:
            payload = c.get("args") or {}
            out = run_tool(c.get("name"), payload)
            if "NOT VERIFIED" in str(out):
                last_check = "failed"
            elif "VERIFIED" in str(out):
                last_check = "ok"
            act, _a = normalize_call(c.get("name"), payload)
            act = act or "?"
            if act in T.ACTIONS or act in T.UI_ACTIONS:
                acted.append(act)       # malformed calls don't count as "did something"
            B.act_log(act, payload.get("args") or {}, out)
            emit("acted", action=act, args=payload.get("args") or {},
                 v=out, direct=False)
            results.append({"functionResponse": {
                "name": c.get("name"), "response": {"result": str(out)}}})
        contents.append({"role": "user", "parts": results})

    reply = "".join(reply_parts).strip()
    if not reply and acted:
        # model went quiet after acting: say *something* so it isn't a void
        reply = "Done: " + ", ".join(a for a in dict.fromkeys(acted) if a != "?") + "."
        emit("token", v=reply)
    B.history_append("user", message)
    if reply:
        B.history_append("assistant", reply)
    elif acted:
        B.history_append("assistant", "[did: %s]" % ", ".join(acted))
    B._speak(reply)

    emit("state", v="idle")
    emit("done", reply=reply, model=model,
         ms=int((time.time() - started) * 1000),
         first_token_ms=int((first or 0) * 1000),
         saved=None, task="chat", acted=(acted or None), backend="gemini")
    return 0


# ------------------------------------------------------------------ cli

def background_loop(task, every, minutes):
    """Run a task round after round until stopped (loop.json removed), the
    time limit, or repeated failures. Each round is one normal cloud turn.
    Progress goes into loop.json so T.A.R.'s tasks panel can show it."""
    import time
    import tar_tools as T
    import tar_brain as B
    B._speak = lambda *a, **k: None          # rounds are silent
    B.history_append = lambda *a, **k: None  # and don't flood the chat history
    fails, n = 0, 0

    def until():
        d = T.loop_running() or {}
        return float(d.get("until") or 0)

    while T.loop_running() and time.time() < until():
        d = T.loop_running() or {}
        if d.get("paused"):
            T.loop_update(status="paused", beat=time.time())
            time.sleep(1)
            continue
        n += 1
        T.loop_update(round=n, status="working", beat=time.time())
        before = len(open(B.ACTLOG_PATH).readlines()) if os.path.exists(B.ACTLOG_PATH) else 0
        captured = []
        _emit = globals()["emit"]

        def tee(t, **kw):                       # remember what happened this round
            if t == "acted":
                captured.append("%s: %s" % (kw.get("action"), str(kw.get("v", ""))[:90]))
            _emit(t, **kw)
        globals()["emit"] = tee
        try:
            rc = chat_gemini(
                "[background task, round %d] Task: %s\nDo the next round NOW: FIRST "
                "call look to read the current state (numbers, prices, what's "
                "affordable/lit up, any popup), THEN act on what you actually see -- "
                "click things by their real names (e.g. 'Cursor'), not guesses. "
                "Then stop. Keep any reply to a few words." % (n, task),
                (B.config().get("cloud_model") or default_model()), max_turns=8)
        except Exception as e:
            rc = 1
            captured.append("error: %s" % e)
        finally:
            globals()["emit"] = _emit
        after = len(open(B.ACTLOG_PATH).readlines()) if os.path.exists(B.ACTLOG_PATH) else 0
        fails = fails + 1 if (rc or after == before) else 0
        T.loop_update(last=(captured[-1] if captured else "no actions this round"),
                      fails=fails, beat=time.time())
        if fails >= 3:
            T.loop_update(status="failed")
            os.system("notify-send -a T.A.R. 'T.A.R. stopped' 'the background task kept failing'")
            break
        next_at = time.time() + every
        T.loop_update(status="waiting", next_at=next_at)
        while time.time() < next_at and T.loop_running():
            if (T.loop_running() or {}).get("paused"):
                break
            time.sleep(1)
            T.loop_update(beat=time.time())
    try:
        os.remove(T.LOOP)
    except OSError:
        pass
    return 0


def main():
    if len(sys.argv) < 2:
        emit("error", v="usage: tar_cloud.py chat -m '...' | models | "
                        "set-key <key> | check")
        return 2
    cmd = sys.argv[1]

    if cmd == "models":
        emit("cloud_models", rows=MODELS, active=None)

    elif cmd == "set-key":
        if len(sys.argv) < 3:
            emit("error", v="usage: set-key <key>")
            return 2
        k = sys.argv[2].strip()
        prov = key_provider(k)
        save_key(prov, k)
        if prov == "google" and provider_of(active_model()) != "google":
            import tar_brain as B
            cfg = B.config()
            cfg["cloud_model"] = "gemini-flash-lite-latest"
            B.save_config(cfg)
        emit("ok", v="%s API key saved (readable only by you)"
                     % ("Gemini" if prov == "google" else "Anthropic"))

    elif cmd == "clear-key":
        keys = load_keys()
        keys.pop(provider_of(active_model()), None)
        os.makedirs(DATA, exist_ok=True)
        with open(KEYS_PATH, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.chmod(KEYS_PATH, 0o600)
        emit("ok", v="API key removed")

    elif cmd == "check":
        ready, why = status()
        emit("cloud_status", key=bool(api_key()), sdk=have_sdk(),
             ready=ready, model=active_model(), v=("ready" if ready else why))

    elif cmd == "loop":
        return background_loop(
            sys.argv[sys.argv.index("--task") + 1],
            int(sys.argv[sys.argv.index("--every") + 1]),
            int(sys.argv[sys.argv.index("--minutes") + 1]))

    elif cmd == "chat":
        msg = None
        model = None
        if "-m" in sys.argv:
            msg = sys.argv[sys.argv.index("-m") + 1]
        if "--model" in sys.argv:
            model = sys.argv[sys.argv.index("--model") + 1]
        if not msg:
            emit("error", v="usage: chat -m '...'")
            return 2
        return chat(msg, model)

    else:
        emit("error", v="unknown command: " + cmd)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
