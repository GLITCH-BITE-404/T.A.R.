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
import re
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


# ------------------------------------------------------------------ providers
# Every cloud brain T.A.R. can use. "gemini" speaks Google's API, "openai" the
# OpenAI chat format (Groq, Mistral and OpenRouter all do), "anthropic" the
# Claude SDK. Model ids are "<provider>/<model>" except the original gemini-*
# and claude-* ones. key_url = where to get a key (all free except Anthropic).
PROVIDERS = {
    "google": {"name": "Google Gemini", "kind": "gemini", "free": True,
               "key_url": "https://aistudio.google.com/apikey", "prefix": "AIza",
               "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
               "blurb": "the only one that can SEE the screen (clicking, games, vision)"},
    "groq": {"name": "Groq", "kind": "openai", "free": True,
             "base": "https://api.groq.com/openai/v1",
             "key_url": "https://console.groq.com/keys", "prefix": "gsk_", "env": ["GROQ_API_KEY"],
             "blurb": "very fast · 1,000 requests/day on the big models"},
    "mistral": {"name": "Mistral", "kind": "openai", "free": True,
                "base": "https://api.mistral.ai/v1",
                "key_url": "https://console.mistral.ai/api-keys", "prefix": "", "env": ["MISTRAL_API_KEY"],
                "blurb": "'Experiment' plan · huge monthly quota (they may train on your chats)"},
    "openrouter": {"name": "OpenRouter", "kind": "openai", "free": True,
                   "base": "https://openrouter.ai/api/v1",
                   "key_url": "https://openrouter.ai/keys", "prefix": "sk-or-",
                   "env": ["OPENROUTER_API_KEY"],
                   "blurb": "free models · only 50 requests/day unless you add $10 credit"},
    "anthropic": {"name": "Anthropic Claude", "kind": "anthropic", "free": False,
                  "key_url": "https://console.anthropic.com/settings/keys", "prefix": "sk-ant",
                  "env": ["ANTHROPIC_API_KEY"], "blurb": "strongest at long multi-step work"},
    "virustotal": {"name": "VirusTotal", "kind": "tool", "free": True,
                   "key_url": "https://www.virustotal.com/gui/my-apikey", "prefix": "",
                   "env": ["VT_API_KEY"], "blurb": "lets file scans check 70+ antivirus engines"},
}


def provider_of(model):
    m = str(model or "")
    if m.startswith("gemini"):
        return "google"
    if m.startswith("claude"):
        return "anthropic"
    head = m.split("/", 1)[0]
    return head if head in PROVIDERS else "anthropic"


def model_name(model):
    """The id the provider's API wants (without our provider/ prefix)."""
    m = str(model or "")
    head, _, rest = m.partition("/")
    return rest if head in PROVIDERS and rest else m


def key_provider(key):
    """Which provider a pasted key belongs to -- None when it can't be told
    from the key itself (Mistral keys have no prefix), so the user picks."""
    if re.fullmatch(r"[0-9a-f]{64}", key):
        return "virustotal"
    for pid in ("anthropic", "openrouter", "groq", "google"):
        if key.startswith(PROVIDERS[pid]["prefix"]):
            return pid
    return None


def active_model():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            m = json.load(f).get("cloud_model")
    except (OSError, ValueError):
        m = None
    return m or default_model()


def api_key(provider=None):
    provider = provider or provider_of(active_model())
    for env in PROVIDERS.get(provider, {}).get("env", []):
        if os.environ.get(env):
            return os.environ[env]
    return load_keys().get(provider) or ""


def have_sdk(provider=None):
    if PROVIDERS.get(provider or provider_of(active_model()), {}).get("kind") in ("gemini", "openai"):
        return True                     # plain urllib, no SDK
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
        return False, "no %s API key set -- /key" % PROVIDERS.get(prov, {}).get("name", prov)
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
    {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash",
     "blurb": "Google free tier. Best free Gemini at games and multi-step tasks.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 1},
    # ---- OpenAI-format providers (free tiers)
    {"id": "groq/llama-3.3-70b-versatile", "name": "Llama 3.3 70B (Groq)",
     "blurb": "Free, ~300 tokens/s. Strong all-rounder, 1,000 requests/day.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 2},
    {"id": "groq/openai/gpt-oss-120b", "name": "gpt-oss 120B (Groq)",
     "blurb": "Free. OpenAI's open model, good reasoning + tools, 1,000/day.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 3},
    {"id": "groq/llama-3.1-8b-instant", "name": "Llama 3.1 8B (Groq)",
     "blurb": "Free, instant, 14,400 requests/day. Fine for simple commands.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 9},
    {"id": "mistral/mistral-large-latest", "name": "Mistral Large",
     "blurb": "Free on the Experiment plan. Strong, big monthly quota.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 4},
    {"id": "mistral/mistral-small-latest", "name": "Mistral Small",
     "blurb": "Free on the Experiment plan. Faster, lighter.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0},
    {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron 3 Ultra (OpenRouter)",
     "blurb": "Free, huge model. 50 requests/day without credit.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "backup": 5},
    {"id": "openrouter/google/gemma-4-31b-it:free", "name": "Gemma 4 31B (OpenRouter)",
     "blurb": "Free, can see images. 50 requests/day without credit.",
     "in_per_mtok": 0.0, "out_per_mtok": 0.0, "vision": True},
]


def _humanize(out):
    """Action result -> one short sentence for the user (no machine tags)."""
    t = str(out or "").split("\n")[0]
    t = re.sub(r"\s*--\s*(NOT )?VERIFIED.*$", "", t).strip()
    t = re.sub(r"\s*\(done instantly.*?\)", "", t).strip()
    if not t:
        return "Done."
    t = t[0].upper() + t[1:]
    return (t[:140].rstrip() + ("." if not t[:140].rstrip().endswith((".", "!", "?")) else ""))


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


# Spoken requests must name what to do. Outward actions whose text/target
# isn't in what the user just SAID are refused (it once opened GitHub for a
# spoken "okay go ahead and do it", and re-ran an old request on "online").
VOICE_MSG = ""
_GROUNDED = {"open": ("what", "app", "q"), "web": ("q", "what"), "open_url": ("url", "q"),
             "type": ("text", "what"), "type_into": ("text", "target"), "key": ("combo", "what"),
             "click_on": ("target", "what"), "run": ("cmd", "what"), "shell": ("cmd",),
             "email": ("to", "subject"), "whatsapp": ("to", "text"), "browser": ("q", "url"),
             "write_file": ("path",), "files": ("q", "path"), "kill": ("what",),
             "closewin": ("what",), "close_tab": ("q", "what"), "play": ("q", "what"),
             "missiles": ("target", "city", "what"), "evil": ("state",)}
_FILLER = {"the", "a", "an", "my", "your", "it", "this", "that", "and", "for", "with", "to",
           "of", "on", "in", "up", "please", "can", "you", "open", "go", "new", "tab"}


def _voice_grounding(action, args):
    if os.environ.get("TAR_VOICE") != "1" or action not in _GROUNDED:
        return None
    import re as _re
    said = set(_re.findall(r"[\w']+", VOICE_MSG.lower()))
    for k in _GROUNDED[action]:
        v = str(args.get(k) or "").lower()
        if not v:
            continue
        words = [w for w in _re.findall(r"[\w']+", v) if len(w) > 2 and w not in _FILLER]
        if words and not any(w in said or any(w[:4] == s[:4] for s in said if len(s) > 3)
                             for w in words):
            return ("NOT DONE: this was a SPOKEN message and %r isn't something they said "
                    "(they said: %r). Don't guess from older messages -- ask them what they "
                    "want." % (v[:60], VOICE_MSG[:80]))
    return None


# screen actions narrate themselves in the mini orb's speech bubble
_MINI_LABELS = {"look": "looking at the screen", "click_on": "clicking", "click": "clicking",
                "type_into": "typing into", "type": "typing", "scroll": "scrolling",
                "key": "pressing", "fill_table": "filling the table", "fill_sheet":
                "filling in the sheet", "close_tab": "closing tabs", "browser": "browser:"}


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
    stop = _voice_grounding(action, args)
    if stop:
        return stop
    if os.environ.get("TAR_EXEC") and action in T.UI_ACTIONS:
        return ("not allowed: the user said exec, and %r only animates T.A.R.'s OWN window. Do it "
                "for real on their screen/system: screen_fx / make_fx for visual effects, or the "
                "real action that does it." % action)
    if action in _MINI_LABELS:
        what = "" if action in ("look", "scroll") else (args.get("target") or args.get("what") or "")
        T.mini_say(_MINI_LABELS[action] + (" " + T.short_say(what, 3) if what else ""))
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
    "keep_doing. For clicker/idle games set autoclick=<the thing to click "
    "non-stop> and task=<the decisions, e.g. buy the best affordable upgrade>. "
    "If the site/app is already open, DON'T open it again.\n"
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
    "notify, devices, audio_out, mic, disk, temps, network, updates, installed, "
    "minimize, browser (tabs/back/reload/find/zoom), file_info, recent, write_file "
    "(USE THIS to create files, not shell echo), note, archive, now_playing, "
    "reminders, stopwatch, keyboard, keep_awake, snip, email, whatsapp, close_tab...) "
    "-- they are reliable. Chain several actions for multi-step "
    "requests. "
    "SCREEN & MOUSE: you can see and click. 'click X' / 'press X' / 'that "
    "button' -> click_on target=<what the user described> (it finds it by "
    "text or vision and clicks; T.A.R. hides itself so it never clicks "
    "itself). Not sure what they mean by 'that'? call look YOURSELF (never tell "
    "the user to), then click it, or if several things fit, ask which one and "
    "name what you see. Typing into "
    "a field -> type_into. Scrolling -> scroll with what=<window>. Never fake "
    "a click with key presses. 'ask claude ...' -> ask_claude.\n"
    "LANGUAGE: reply in the language the user is writing in -- English unless they "
    "wrote in Hebrew. A short or ambiguous message ('again', 'ok', 'yes', 'do it') "
    "keeps the language of their previous messages. Hebrew names or Hebrew text on "
    "screen don't change that.\n"
    "REPLIES: after acting, tell the user the RESULT in plain words (what you found, "
    "what changed, what failed) -- never just list the actions you called.\n"
    "TABS vs WINDOWS: to close tabs use close_tab. NEVER close a browser window "
    "or kill the browser to get rid of a tab -- that destroys every other tab.\n"
    "PROFILES: only say something opened in a profile if the action result "
    "says 'in the <name> Chrome profile'.\n"
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
    try:
        profs = T.chrome_profiles()
    except Exception:
        profs = []
    if profs:
        lines.append("Chrome profiles (use profile=<name> on open/web): " + ", ".join(
            "%r%s" % (p["name"] or p["dir"],
                      " = school/student account (managed)" if p["managed"]
                      else (" = MAIN/normal account, the user calls it '%s' -- used by default"
                            % p["given"] if p["dir"] == "Default" else ""))
            for p in profs) + ". 'Rephael'/'my normal account'/'main' -> profile='main'. "
            "If the user NAMES the account, the name decides; 'my other account' only means "
            "'not the one I'm looking at', never 'not main'.")
    try:
        with open(os.path.join(T.DATA, "ui-state.json"), encoding="utf-8") as f:
            ui = json.load(f)
        opened = [k + ("" if v is True else " (%s)" % v) for k, v in ui.items()
                  if k != "view" and v]
        lines.append("YOUR OWN UI (T.A.R.'s window) right now: %s mode; open panels: %s. "
                     "When the user says 'the preview/tab/panel/that/them' and one of THESE is "
                     "open, they mean your panel -> use the panel action, never close windows "
                     "or browser tabs." % (ui.get("view", "?"), ", ".join(opened) or "none"))
    except (OSError, ValueError):
        pass
    out = "\n\nLIVE STATUS (checked just now -- this is the truth; ignore any "
    out += "older message or action result in this chat that says otherwise):\n- "
    return out + "\n- ".join(lines)


FORMS_RULE = (
    "\n\nHOMEWORK / TABLES / WORKSHEETS ('answer the questions in my doc', 'fill the table'): "
    "call fill_table -- it reads the questions on screen itself (tables, 'answer:' lines, "
    "blanks), answers them and types every answer in one go. Pass q ONLY if the user gave extra "
    "instructions in THIS message; never pass older context. Don't click cells yourself and don't use click_on/type for "
    "tables. Afterwards tell the user in ONE line how many cells were filled; if it says NOT "
    "VERIFIED, give the answers it returned as a short plain list to paste. For other forms "
    "(not tables), fill_cells start=<first field> texts=<v1 || v2>.")


def chat(message, model=None, max_turns=6):
    import tar_brain as B
    model = model or B.config().get("cloud_model") or default_model()
    if PROVIDERS.get(provider_of(model), {}).get("kind") in ("gemini", "openai"):
        # one chat loop for both: OpenAI-format providers go through llm_call's
        # translation, so they get every guard the Gemini path has
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
    system += FORMS_RULE
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


# Free tier: each model has its OWN daily request quota. When one runs out,
# fall through to the next instead of going dead for the rest of the day.
FALLBACK_CHAIN = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash",
                  "gemini-flash-latest", "gemini-3.6-flash"]
QUOTA_FILE = os.path.join(DATA, "quota-exhausted.json")


def _exhausted():
    try:
        with open(QUOTA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    import time
    return {m: t for m, t in d.items() if t > time.time()}


def _mark_exhausted(model, retry_s):
    import time
    d = _exhausted()
    d[model] = time.time() + max(600, retry_s)
    try:
        with open(QUOTA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


LAST_MODEL = None          # the model that REALLY answered (after any quota fallback)


def gemini_call(key, model, body, fallback=True):
    """generateContent with automatic fallback when a model's DAILY free quota
    is used up (remembered until it resets). TTS models don't fall back."""
    gone = _exhausted()
    chain = [model] + ([m for m in FALLBACK_CHAIN if m != model]
                       if "tts" not in model and fallback else [])
    last = None
    for m in chain:
        if m in gone and m != chain[-1]:
            continue
        try:
            r = _gemini_call_one(key, m, body)
            global LAST_MODEL
            LAST_MODEL = m
            return r
        except _DailyQuota as e:
            _mark_exhausted(m, e.retry_s)
            emit("info", v="%s is out of free requests for today -- switching to %s" % (
                m, next((x for x in chain[chain.index(m) + 1:] if x not in gone), "nothing")))
            last = e
    if last and not fallback:
        raise last                      # llm_call moves on to another provider
    raise RuntimeError("every Gemini model is out of free requests for today -- try again "
                       "later (quota resets daily)" if last else "no Gemini model available")


class _DailyQuota(Exception):
    def __init__(self, retry_s):
        Exception.__init__(self, "daily quota")
        self.retry_s = retry_s


def _gemini_call_one(key, model, body):
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
            if e.code == 429 and "PerDay" in detail:
                import re as _re
                m_ = _re.search(r'"retryDelay":\s*"(\d+)s"', detail)
                raise _DailyQuota(int(m_.group(1)) if m_ else 3600)
            if e.code == 429 and attempt == 0:
                emit("info", v="Gemini rate limit — waiting a moment")
                time.sleep(15)
                continue
            if e.code in (500, 502, 503, 504) and attempt == 0:
                # "model is experiencing high demand" -- usually gone in seconds
                emit("info", v="Gemini is busy — retrying")
                time.sleep(4)
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


# ------------------------------------------------------------------ any provider
# The chat loop speaks Gemini's request/response shape. llm_call() sends it to
# whichever provider the model belongs to -- OpenAI-format ones (Groq, Mistral,
# OpenRouter) get it translated both ways -- and when a model's free daily
# quota is gone it moves to the strongest model of ANOTHER provider you have a
# key for, before ever dropping to a weak backup.

def _has_images(body):
    return any("inline_data" in p or "inlineData" in p
               for c in body.get("contents", []) for p in c.get("parts", []))


def backup_models(model, body=None):
    """Other models to try, strongest first: only providers with a key, and
    only image-capable ones when the request carries a screenshot."""
    imgs = _has_images(body or {})
    out = []
    for m in sorted((m for m in MODELS if m.get("backup")), key=lambda m: m["backup"]):
        p = provider_of(m["id"])
        if m["id"] == model or not api_key(p) or PROVIDERS[p]["kind"] not in ("gemini", "openai"):
            continue
        if imgs and not (p == "google" or m.get("vision")):
            continue
        out.append(m["id"])
    if api_key("google") and "gemini-flash-lite-latest" not in out + [model]:
        out.append("gemini-flash-lite-latest")    # last resort: Google's own chain
    return out


def llm_call(key, model, body, fallback=True):
    """Gemini-shaped request -> Gemini-shaped response, from any provider."""
    global LAST_MODEL
    chain = [model] + (backup_models(model, body) if fallback else [])
    gone = _exhausted()
    last = None
    for i, m in enumerate(chain):
        if m in gone and i < len(chain) - 1:
            continue
        prov = provider_of(m)
        k = key if (i == 0 and key and prov == provider_of(model)) else api_key(prov)
        if not k:
            continue
        try:
            if PROVIDERS[prov]["kind"] == "gemini":
                # the requested Gemini model alone first; Google's own weaker
                # fallbacks only when nothing better is left
                r = gemini_call(k, m, body, fallback=(i == len(chain) - 1))
            elif PROVIDERS[prov]["kind"] == "openai":
                r = openai_call(k, m, body)
                LAST_MODEL = m
            else:
                continue
            return r
        except _DailyQuota as e:
            _mark_exhausted(m, e.retry_s)
            last = e
        except RuntimeError as e:
            # out of quota / rate limited -> next provider; anything else (bad
            # key, broken request) is reported, not hidden behind a fallback
            if not ("out of free requests" in str(e) or "rate limit" in str(e).lower()) \
                    or i == len(chain) - 1:
                raise
            last = e
        nxt = next((x for x in chain[i + 1:] if x not in gone and api_key(provider_of(x))), None)
        if nxt:
            emit("info", v="%s is out of requests -- switching to %s" % (m, nxt))
    raise RuntimeError("every model you have a key for is out of free requests today -- "
                       "add another provider with /key, or try later" if last else
                       "no cloud model available -- add a key with /key")


def _oa_id(i, j):
    return "c%04d%04d" % (i % 10000, j % 10000)     # 9 chars a-z0-9: Mistral's rule


def _to_openai(model, body):
    msgs = []
    si = body.get("systemInstruction")
    if si:
        msgs.append({"role": "system", "content": "".join(p.get("text", "") for p in si.get("parts", []))})
    pending = []
    for i, c in enumerate(body.get("contents", [])):
        texts, imgs, calls, resps = [], [], [], []
        for p in c.get("parts", []):
            if p.get("thought"):
                continue
            if "functionCall" in p:
                calls.append(p["functionCall"])
            elif "functionResponse" in p:
                resps.append(p["functionResponse"])
            elif "inline_data" in p or "inlineData" in p:
                imgs.append(p.get("inline_data") or p.get("inlineData"))
            elif p.get("text"):
                texts.append(p["text"])
        if resps:
            for j, r in enumerate(resps):
                msgs.append({"role": "tool",
                             "tool_call_id": pending[j] if j < len(pending) else _oa_id(i, j),
                             "content": str((r.get("response") or {}).get("result", r.get("response")))})
            pending = []
            if texts:
                msgs.append({"role": "user", "content": "\n".join(texts)})
            continue
        if c.get("role") == "model":
            m = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                pending = [_oa_id(i, j) for j in range(len(calls))]
                m["tool_calls"] = [{"id": pending[j], "type": "function",
                                    "function": {"name": fc.get("name", ""),
                                                 "arguments": json.dumps(fc.get("args") or {})}}
                                   for j, fc in enumerate(calls)]
            msgs.append(m)
        elif imgs:
            msgs.append({"role": "user", "content": [{"type": "text", "text": t} for t in texts] + [
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (
                    d.get("mime_type") or d.get("mimeType") or "image/jpeg", d.get("data", ""))}}
                for d in imgs]})
        else:
            msgs.append({"role": "user", "content": "\n".join(texts)})
    out = {"model": model_name(model), "messages": msgs}
    tools = [{"type": "function", "function": {
        "name": fd["name"], "description": fd.get("description", ""),
        "parameters": fd.get("parametersJsonSchema") or fd.get("parameters")
        or {"type": "object", "properties": {}}}}
        for t in body.get("tools") or [] for fd in t.get("functionDeclarations", [])]
    if tools:
        out["tools"] = tools
    gc = body.get("generationConfig") or {}
    if gc.get("maxOutputTokens"):
        out["max_tokens"] = int(gc["maxOutputTokens"])
    if gc.get("temperature") is not None:
        out["temperature"] = gc["temperature"]
    return out


def _from_openai(r):
    msg = ((r.get("choices") or [{}])[0].get("message")) or {}
    parts = []
    if msg.get("content"):
        parts.append({"text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        parts.append({"functionCall": {"name": fn.get("name", ""), "args": args}})
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def openai_call(key, model, body):
    """One OpenAI-format chat completion (Groq / Mistral / OpenRouter)."""
    import time
    import urllib.error
    import urllib.request
    prov = PROVIDERS[provider_of(model)]
    data = json.dumps(_to_openai(model, body)).encode()
    for attempt in range(2):
        req = urllib.request.Request(prov["base"] + "/chat/completions", data=data, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key,
            "HTTP-Referer": "https://github.com/GLITCH-BITE-404/T.A.R.", "X-Title": "T.A.R."})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return _from_openai(json.loads(r.read().decode()))
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            low = detail.lower()
            if e.code == 429 and re.search(r"per day|\brpd\b|daily|free-models-per-day|tokens per month", low):
                raise _DailyQuota(3600)
            if e.code == 429 and attempt == 0:
                wait = min(20, int(float(e.headers.get("retry-after") or 8)))
                emit("info", v="%s rate limit -- waiting %ds" % (prov["name"], wait))
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503, 504) and attempt == 0:
                time.sleep(3)
                continue
            try:
                detail = json.loads(detail).get("error", {})
                detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            except (ValueError, AttributeError):
                pass
            if e.code == 401:
                raise RuntimeError("%s rejected the API key (401) -- check it with /key" % prov["name"])
            if e.code == 429:
                raise RuntimeError("%s rate limit: %s" % (prov["name"], str(detail)[:200]))
            raise RuntimeError("%s HTTP %d: %s" % (prov["name"], e.code, str(detail)[:300]))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == 0:
                time.sleep(2)
                continue
            raise RuntimeError("no connection to %s (%s)" % (prov["name"], getattr(e, "reason", e)))
    raise RuntimeError("%s rate limit" % prov["name"])


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


REVIEW_PROMPT = """You are T.A.R.'s self-check. Review one turn honestly and strictly.

FACTS ABOUT THIS COMPUTER (trust these):
{facts}

USER ASKED:
{ask}

ACTIONS T.A.R. RAN (with their REAL results):
{trace}

T.A.R.'S DRAFT REPLY:
{reply}

Check:
1. Did the actions actually do what the user asked? Watch for: wrong target
   (wrong account/profile, wrong window, wrong tab, wrong file), a step skipped,
   the request misunderstood, a result that says it failed / NOT VERIFIED /
   NEEDS CONFIRMATION.
2. Does the draft claim ANYTHING the results don't show? (e.g. says "in your main
   account" but no result says "in the ... Chrome profile"; says "done" but a
   result failed; invents details.)

Reply with ONLY JSON:
{{"ok": true}}
or
{{"ok": false, "problem": "<one short sentence>", "fix": "<what T.A.R. should do now, concretely, e.g. call open with profile=main>", "honest_reply": "<a short truthful reply to the user describing what really happened>"}}"""


def self_review(key, model, ask, trace, reply):
    """Second opinion on a finished turn. Returns None if fine, else a dict."""
    if not trace:
        return None
    lines = "\n".join("- %s %s -> %s" % (a, json.dumps(g, ensure_ascii=False)[:160], str(r)[:260])
                      for a, g, r in trace)
    try:
        r = llm_call(key, model, {"contents": [{"role": "user", "parts": [{"text":
            REVIEW_PROMPT.format(facts=live_status().strip()[:1200], ask=ask[:800], trace=lines,
                                 reply=reply[:600] or "(no reply)")}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 400}})
    except Exception:
        return None                      # never block a reply on the reviewer
    text = "".join(p.get("text", "") for p in
                   ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    import re as _re
    m = _re.search(r"\{.*\}", text, _re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return None
    return None if d.get("ok", True) else d


def chat_gemini(message, model, max_turns=10):
    import time
    import tar_brain as B
    import tar_tools as T
    # what the USER wrote (this turn + recent turns) -- the type guards check
    # personal info against this, so the model can't invent an email
    T.USER_SAID = " \n ".join([h["content"] for h in B.history_tail(6)
                               if h.get("role") == "user"] + [message])
    try:
        T.USER_PROFILE = T.profile_from_message(message)   # the account THEY named
    except Exception:
        T.USER_PROFILE = None

    key = api_key(provider_of(model))
    if not key:
        emit("error", v="no %s API key set -- type /key to add one"
             % PROVIDERS.get(provider_of(model), {}).get("name", "API"))
        return 1

    cfg = B.config()
    system = SYSTEM.format(user=cfg.get("user", "the user"), voice=B.voice_block(cfg)) + B.mem_block()
    system += B.act_block_context(n=40, within_s=None)
    system += live_status()
    system += B.persona_tail(cfg)

    contents = []
    hist = B.history_tail(int(cfg.get("cloud_history_turns", 40)))
    for h in hist:
        role = "model" if h["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    # Old requests are FINISHED. After a restart T.A.R. heard itself say
    # "online" and picked an unfinished request from 15 min earlier back up.
    try:
        last_at = float(hist[-1].get("at") or 0) if hist else 0
    except (TypeError, ValueError):
        last_at = 0
    gap = time.time() - last_at if last_at else 0
    system += ("\n\nEVERY request in the history above is DONE or ABANDONED. Act ONLY on the "
               "latest message. Never redo or 'finish' an earlier request unless the latest "
               "message explicitly asks for it again.")
    if gap > 300:
        system += (" The conversation was idle for %d minutes -- treat the latest message as "
                   "a fresh start." % int(gap // 60))
    if os.environ.get("TAR_EXEC"):
        system += ("\n\nEXEC MODE: the user wants this DONE FOR REAL on their screen/system right "
                   "now. Use real actions. A visual effect that doesn't exist yet: WRITE it with "
                   "make_fx (and fix it if it returns errors). Never use T.A.R.'s own UI animations "
                   "(shake, glitch, barrel roll...) as a stand-in. Report only what the action "
                   "results show.")
    voice = os.environ.get("TAR_VOICE") == "1"
    global VOICE_MSG
    VOICE_MSG = message if voice else ""
    short_voice = voice and len(message.split()) < 2
    if voice:
        system += ("\n\nVOICE INPUT: this message was SPOKEN and machine-transcribed from a "
                   "quiet mic -- it may be misheard. If it is gibberish, a fragment, a random "
                   "word/name, or doesn't clearly ask for something (e.g. 'love love', 'uh', "
                   "'open up'), DO NOT run any action and DO NOT search the web for it: say "
                   "what you heard in quotes and ask what they meant. Only act on a clear request.")
    contents.append({"role": "user", "parts": [{"text": message}]})

    emit("state", v="thinking", model=model, tier="cloud", lane="api",
         task="chat", reason="cloud: " + model)

    acted, reply_parts = [], []
    started = time.time()
    first = None
    tools = gemini_tools()
    if short_voice:
        # 1-2 spoken words ("online", "uh") are never enough to act on -- in
        # CODE, not just a prompt rule: no tools offered for this turn
        tools = None
        system += ("\n\nYou heard only %r. You can't run actions for this. Reply with what you "
                   "heard and ask what they want." % message)
    nudged = False
    last_check = None           # "ok" / "failed" from the latest verified action
    trace = []                  # (action, args, result) -- for the self-check
    reviewed = 0

    for _turn in range(max_turns):
        try:
            resp = llm_call(key, model, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                **({"tools": tools} if tools else {}),
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
        if not calls and trace and reviewed < 2:
            draft = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            verdict = self_review(key, model, message, trace, draft)
            if verdict and reviewed == 0 and _turn + 1 < max_turns:
                # first miss: let it actually fix the mistake
                reviewed = 1
                emit("info", v="self-check: " + str(verdict.get("problem", ""))[:120])
                contents.append(content)
                contents.append({"role": "user", "parts": [{"text": (
                    "[self-check] Something is wrong with what you did or said: %s\n"
                    "Fix it NOW with actions: %s\nThen reply truthfully, claiming only "
                    "what the action results show." % (verdict.get("problem", ""),
                                                        verdict.get("fix", "")))}]})
                continue
            if verdict:
                # still wrong after one fix attempt: tell the truth instead
                reviewed = 2
                honest = (verdict.get("honest_reply") or
                          "I didn't manage that correctly: %s" % verdict.get("problem", ""))
                parts = [{"text": honest}]
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
            B.act_log(act, _a or {}, out)
            emit("acted", action=act, args=payload.get("args") or {},
                 v=out, direct=False)
            trace.append((act, _a or {}, out))
            results.append({"functionResponse": {
                "name": c.get("name"), "response": {"result": str(out)}}})
        contents.append({"role": "user", "parts": results})

    reply = "".join(reply_parts).strip()
    if not reply and acted:
        # model went quiet after acting ("Done: shell, web, look, click_on." was
        # all you got for a virus scan): ask it once for the actual result
        try:
            contents.append({"role": "user", "parts": [{"text":
                "Now tell me the RESULT of what you just did in 1-2 short sentences, from the "
                "action results above (what you found / what changed / what failed). Don't "
                "list action names."}]})
            r2 = llm_call(key, model, {"systemInstruction": {"parts": [{"text": system}]},
                                          "contents": contents,
                                          "generationConfig": {"maxOutputTokens": 400}})
            p2 = ((r2.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            reply = "".join(p.get("text", "") for p in p2 if not p.get("thought")).strip()
        except Exception:
            reply = ""
        if not reply and trace:
            reply = _humanize(trace[-1][2])
        if reply:
            emit("token", v=reply)
    B.history_append("user", message)
    if reply:
        B.history_append("assistant", reply)
    elif acted:
        B.history_append("assistant", "[did: %s]" % ", ".join(acted))
    B._speak(reply)

    emit("state", v="idle")
    used = LAST_MODEL or model
    emit("done", reply=reply, model=used, asked=model, downgraded=(used != model),
         ms=int((time.time() - started) * 1000),
         first_token_ms=int((first or 0) * 1000),
         saved=None, task="chat", acted=(acted or None), backend="gemini")
    return 0


# ------------------------------------------------------------------ cli

def _autoclick_burst(target, rate, until_ts, push):
    """Click `target` non-stop until until_ts (or pause/stop) -- no AI, just a
    fast local clicker at the remembered spot. Returns clicks done."""
    import time
    import tar_tools as T
    loc = T._loc_get(target)
    if not loc:
        found, err = T._locate(target, tries=1)
        if not found:
            push("warn", "autoclick: can't find %r (%s)" % (target, (err or "")[:60]))
            return 0
        loc = {"x": found[0], "y": found[1], "label": found[2]}
        T._loc_set(target, found[0], found[1], found[2])
    x, y = loc["x"], loc["y"]
    n = 0
    gap = 1.0 / max(1, rate)
    ok, why = T.task_window_ok()
    if not ok:
        push("warn", "autoclick paused: " + why)
        return 0
    mon = T._monitor()
    want = (mon.get("x", 0) + int(x), mon.get("y", 0) + int(y))
    with T._TarHidden():
        T._move(x, y)
        T.mini_say("clicking " + T.short_say(target, 3))
        while time.time() < until_ts:
            d = T.loop_running()
            if not d or d.get("paused"):
                break
            if n % 8 == 0:
                # the clicker presses wherever the pointer IS: if the user moved
                # it or switched away, stop instead of clicking their stuff
                cur = T.cursor_pos()
                if cur and abs(cur[0] - want[0]) + abs(cur[1] - want[1]) > 6:
                    push("warn", "you moved the mouse -- stopped autoclicking until next round")
                    T.mini_say("you're using the mouse, waiting")
                    break
                ok, why = T.task_window_ok()
                if not ok:
                    push("warn", "autoclick paused: " + why)
                    T.mini_say("waiting: " + why)
                    break
            T._click("left", False)
            n += 1
            if n % 20 == 0:
                T.loop_update(clicks=int(d.get("clicks") or 0) + 20, beat=time.time())
            time.sleep(gap)
    return n


# ---- speech: Gemini TTS (Hebrew + anything piper can't say) and STT -------
TTS_MODEL = "gemini-2.5-flash-preview-tts"
STT_MODEL = "gemini-flash-lite-latest"


def tts_pcm(text, voice="Charon"):
    """Text -> raw PCM (24 kHz, s16, mono) via Gemini. Raises on failure."""
    import base64
    key = api_key("google")
    if not key:
        raise RuntimeError("no Gemini key")
    body = {"contents": [{"parts": [{"text": "Read this aloud naturally, in its own "
                                              "language, exactly as written:\n" + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                                     "voiceName": voice}}}}}
    r = gemini_call(key, TTS_MODEL, body)
    part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
    return base64.b64decode(part["data"])


def transcribe_wav(path):
    """Speech file -> text via Gemini (any language, incl. Hebrew).
    Returns '' for no speech. Raises on failure."""
    import base64
    key = api_key("google")
    if not key:
        raise RuntimeError("no Gemini key")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = gemini_call(key, STT_MODEL, {"contents": [{"parts": [
        {"inline_data": {"mime_type": "audio/wav", "data": b64}},
        {"text": "Transcribe exactly what is said, in the language it is spoken "
                 "(Hebrew stays in Hebrew letters, English in English). Output ONLY "
                 "the words. The assistant being spoken to is called T.A.R. (said 'tar'), "
                 "so 'hey tar' is English, not Hebrew. If there is no clear speech, "
                 "output exactly: [none]"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 400}})
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return "" if text.lower().strip("[]. ") in ("none", "") else text


def background_loop(task, every, minutes, autoclick="", rate=8):
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
        ok, why = T.task_window_ok()
        if not ok:
            # never act on whatever happens to be on screen instead (and don't
            # spend a Gemini call looking at the wrong thing)
            if (T.loop_running() or {}).get("status") != "waiting for the window":
                T.loop_update(status="waiting for the window", last="paused: " + why)
            T.mini_say("waiting: " + why)
            if "closed" in why:
                break
            time.sleep(3)
            T.loop_update(beat=time.time())
            continue
        n += 1
        T.loop_update(round=n, status="working", beat=time.time())
        before = len(open(B.ACTLOG_PATH).readlines()) if os.path.exists(B.ACTLOG_PATH) else 0
        captured = []
        thought = []
        _emit = globals()["emit"]
        t0 = time.time()

        def push(kind, text):
            if kind == "think":
                T.mini_say("thinking…")        # the full thought is in the orb's card
            d = T.loop_running() or {}
            feed = (d.get("feed") or []) + [{"r": n, "k": kind, "t": text[:220],
                                              "at": time.time()}]
            T.loop_update(feed=feed[-14:], beat=time.time())

        def tee(t, **kw):                       # the live feed the tasks tab shows
            if t == "token":
                thought.append(kw.get("v", ""))
            elif t == "acted":
                if thought and "".join(thought).strip():
                    push("think", "".join(thought).strip())
                    thought.clear()
                v = str(kw.get("v", ""))
                captured.append("%s: %s" % (kw.get("action"), v[:90]))
                kind = "warn" if "NOT VERIFIED" in v or v.startswith(("couldn't", "refused")) else "act"
                short = v.split(" -- ")[0][:110]
                tail = " \u2713" if "-- VERIFIED" in v else (" \u2717" if kind == "warn" else "")
                push(kind, "%s: %s%s" % (kw.get("action"), short, tail))
            _emit(t, **kw)
        globals()["emit"] = tee
        _run_tool = globals()["run_tool"]

        def run_tool_live(name, payload):      # show each step as it STARTS
            act, a = normalize_call(name, payload)
            what = a.get("target") or a.get("q") or a.get("what") or ""
            label = {"look": "looking at the screen", "click_on": "clicking",
                     "click": "clicking", "type_into": "typing into", "scroll": "scrolling"
                     }.get(act, act)
            push("doing", "\u2192 %s%s\u2026" % (label, (" " + str(what)[:50]) if what and act != "look" else ""))
            return _run_tool(name, payload)
        globals()["run_tool"] = run_tool_live
        try:
            rc = chat_gemini(
                "[background task, round %d] Task: %s\n%s"
                "Do the next round NOW: FIRST call look to read the current state "
                "(numbers, prices, what's affordable/lit up, any popup). Then write ONE "
                "short line of reasoning (what you see, what you'll do). THEN do it: "
                "call click_on for EACH thing you decided -- e.g. one click_on "
                "target='Grandma' per Grandma you buy. Writing 'buying X' without "
                "calling click_on does NOTHING. Then stop." % (
                    n, task,
                    ("(%r is being clicked automatically between rounds -- don't click it "
                     "yourself; your job is only the decisions.)\n" % autoclick)
                    if autoclick else ""),
                (B.config().get("cloud_model") or default_model()), max_turns=8)
        except Exception as e:
            rc = 1
            captured.append("error: %s" % e)
        finally:
            globals()["emit"] = _emit
            globals()["run_tool"] = _run_tool
        after = len(open(B.ACTLOG_PATH).readlines()) if os.path.exists(B.ACTLOG_PATH) else 0
        fails = fails + 1 if (rc or after == before) else 0
        if thought and "".join(thought).strip():
            push("think", "".join(thought).strip())
        push("round", "round %d done in %ds" % (n, time.time() - t0))
        T.loop_update(last=(captured[-1] if captured else "no actions this round"),
                      fails=fails, beat=time.time())
        if fails >= 3:
            T.loop_update(status="failed")
            os.system("notify-send -a T.A.R. 'T.A.R. stopped' 'the background task kept failing'")
            break
        next_at = time.time() + every
        if autoclick:
            T.loop_update(status="autoclicking", next_at=next_at)
            done = _autoclick_burst(autoclick, rate, next_at, push)
            if done:
                push("act", "autoclicked %s x%d \u2713" % (autoclick, done))
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
        prov = sys.argv[sys.argv.index("--provider") + 1] if "--provider" in sys.argv else key_provider(k)
        if prov not in PROVIDERS:
            # can't tell from the key itself: the UI asks which provider it is
            emit("key_which", v="which provider is this key for?",
                 rows=[dict(id=pid, **{k2: p[k2] for k2 in ("name", "free", "blurb", "key_url")})
                       for pid, p in PROVIDERS.items()])
            return 0
        save_key(prov, k)
        P = PROVIDERS[prov]
        import tar_brain as B
        cfg = B.config()
        if P["kind"] in ("gemini", "openai", "anthropic") and not api_key(provider_of(active_model())):
            # the current brain has no key -> use the new provider's best model
            best = next((m["id"] for m in sorted(MODELS, key=lambda m: m.get("backup") or 99)
                         if provider_of(m["id"]) == prov), None)
            if best:
                cfg["cloud_model"] = best
                B.save_config(cfg)
        emit("ok", v="%s key saved (only readable by you)%s" % (
            P["name"], "" if P["kind"] == "tool" else
            " -- it's now a backup brain when another runs out" if provider_of(active_model()) != prov
            else " -- answering with " + active_model()), provider=prov)

    elif cmd == "providers":
        # for /key and the CLOUD tab: every provider + whether its key is set
        emit("providers", rows=[dict(id=pid, has_key=bool(api_key(pid)),
                                     **{k2: p[k2] for k2 in ("name", "kind", "free", "blurb", "key_url")})
                                for pid, p in PROVIDERS.items()])

    elif cmd == "clear-key":
        keys = load_keys()
        keys.pop(sys.argv[2] if len(sys.argv) > 2 else provider_of(active_model()), None)
        os.makedirs(DATA, exist_ok=True)
        with open(KEYS_PATH, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.chmod(KEYS_PATH, 0o600)
        emit("ok", v="API key removed")

    elif cmd == "check":
        ready, why = status()
        emit("cloud_status", key=bool(api_key()), sdk=have_sdk(),
             ready=ready, model=active_model(), v=("ready" if ready else why))

    elif cmd == "ping":
        # setup TEST for the cloud brain: one tiny real request, timed
        import time
        key = api_key()
        if not key:
            emit("acted", v="NOT VERIFIED: no API key -- paste one in the console's CLOUD tab")
            return 1
        t0 = time.time()
        try:
            r = llm_call(key, active_model(), {"contents": [{"parts": [{"text": "Reply with exactly: online"}]}]},
                         fallback=False)
            txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]).strip()
            emit("acted", v="VERIFIED: %s answered %r in %.1fs" % (active_model(), txt[:30], time.time() - t0))
        except Exception as e:
            emit("acted", v="NOT VERIFIED: %s" % str(e)[:200])
            return 1
        return 0

    elif cmd == "tts":
        # tar_say.sh: text on stdin -> raw 24k PCM on stdout
        try:
            voice = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else "Charon"
            sys.stdout.buffer.write(tts_pcm(sys.stdin.read(), voice))
            return 0
        except Exception as e:
            print("tts failed: %s" % e, file=sys.stderr)
            return 1

    elif cmd == "loop":
        def opt(name, default=""):
            return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
        import tar_tools as _T
        _T.MINI_HOLD = 90               # a task keeps its orb out between rounds
        try:
            return background_loop(opt("--task"), int(opt("--every", "15")),
                                   int(opt("--minutes", "20")), opt("--autoclick"),
                                   int(opt("--rate", "8")))
        finally:
            _T.mini(on=False, say="")   # task over: the orb flies home

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
