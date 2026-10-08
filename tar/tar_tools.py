#!/usr/bin/env python3
"""
T.A.R. action layer -- the part that makes it an assistant instead of a chatbot.

Two ways in:

  match <text>        deterministic. "fullscreen", "open firefox", "volume 40"
                      hit a regex and run instantly, no model, no latency.
  run <action> [args] direct call, used by the UI's buttons and by the LLM
                      when it decides to invoke a tool.

Everything is an allowlisted named action. There is deliberately no "run this
shell string" action unless TAR_ALLOW_SHELL=1 is set, because a 3B model will
happily hallucinate `rm -rf` if you give it a shell.

Actions tagged ui=True aren't executed here at all -- they're emitted for
tar.qml to animate. That keeps the orb effects in the renderer where they
belong.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get(
    "TAR_DATA",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "bite-os", "tar",
    ),
)
SHOTS = os.path.join(DATA, "shots")
ALLOW_SHELL = os.environ.get("TAR_ALLOW_SHELL") == "1"


def emit(t, **kw):
    kw["t"] = t
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def die(msg, **kw):
    emit("error", v=msg, **kw)
    sys.exit(1)


def sh(argv, timeout=15, detach=False):
    """Run a command. Returns (rc, output)."""
    try:
        if detach:
            _note_spawn()
            subprocess.Popen(argv, start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return 0, ""
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout or r.stderr or "").strip()
    except FileNotFoundError:
        return 127, argv[0] + " not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 1, str(e)


def hypr(*args):
    return sh(["hyprctl", "dispatch"] + [str(a) for a in args])


# ---- what did T.A.R. itself open? ------------------------------------------
# Every launch starts a tiny detached watcher that snapshots the windows that
# existed BEFORE, then claims the first new window that appears. Claimed
# windows go into a permanent list (no expiry, survives restarts), so
# "close it" / "close them" only ever touch things T.A.R. really opened --
# never the terminal you were already working in, and never a window you
# opened yourself afterwards. Closed windows are pruned on read.
MINE = os.path.join(DATA, "mine.json")
CLAIM_TIMEOUT_S = 15


def _load_mine():
    try:
        with open(MINE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save_mine(rows):
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = MINE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(tmp, MINE)
    except OSError:
        pass


def _all_clients_raw():
    try:
        return json.loads(subprocess.run(["hyprctl", "-j", "clients"],
                                         capture_output=True, text=True,
                                         timeout=5).stdout)
    except (ValueError, OSError, subprocess.SubprocessError):
        return []


def _note_spawn():
    """Start the claim watcher for the launch that is about to happen."""
    before = ",".join(w.get("address", "") for w in _all_clients_raw())
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "_claim",
                          before], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=dict(os.environ, TAR_DATA=DATA))
    except OSError:
        pass


def _claim(before):
    """Watcher body: record the first window that wasn't there before."""
    base = set(a for a in before.split(",") if a)
    end = time.time() + CLAIM_TIMEOUT_S
    while time.time() < end:
        time.sleep(0.25)
        for w in _all_clients_raw():
            addr = w.get("address")
            if (addr and addr not in base and w.get("mapped")
                    and w.get("title") != TAR_TITLE):
                rows = _load_mine()
                if any(r.get("address") == addr for r in rows):
                    base.add(addr)          # another watcher got it first
                    continue
                rows.append({"address": addr, "class": w.get("class", ""),
                             "at": time.time()})
                _save_mine(rows)
                return


def tar_opened(wins=None):
    """Live windows T.A.R. opened, newest first. Prunes closed ones."""
    wins = wins if wins is not None else _clients()
    live = {w.get("address"): w for w in wins}
    rows = _load_mine()
    # same address AND same class: guards against address reuse after reboot
    keep = [r for r in rows if r.get("address") in live
            and live[r["address"]].get("class") == r.get("class")]
    if len(keep) != len(rows):
        _save_mine(keep)
    keep.sort(key=lambda r: r.get("at", 0), reverse=True)
    return [live[r["address"]] for r in keep]


def cfg():
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def effective_device(kind):
    c = cfg().get("devices", {})
    if c.get(kind):
        return c[kind]
    if kind == "video":
        for n in ("/dev/video0", "/dev/video1"):
            if os.path.exists(n):
                return n
    return None


# ----------------------------------------------------------------- window

DIRS = {"left": "l", "right": "r", "up": "u", "down": "d",
        "l": "l", "r": "r", "u": "u", "d": "d"}


# T.A.R. always has focus while you talk to it, so "the focused window" was
# always T.A.R. itself -- "close it" after "open kitty" closed T.A.R. Window
# actions now aim at the window the user was last in BEFORE T.A.R., or at one
# named in args ("what"/"q": a class or title fragment). Never at T.A.R.
TAR_TITLE = "T.A.R."
_PRONOUNS = ("", "it", "that", "this", "the window", "window", "this window",
             "that window", "the app", "that app", "this app")


def _clients():
    rc, out = sh(["hyprctl", "-j", "clients"])
    if rc:
        return []
    try:
        return [w for w in json.loads(out)
                if w.get("mapped") and w.get("title") != TAR_TITLE]
    except ValueError:
        return []


# "browser" means whatever browser the user actually uses -- an open one first
BROWSER_CLASSES = ("google-chrome", "chromium", "firefox", "brave", "zen",
                   "librewolf", "vivaldi", "opera", "microsoft-edge", "floorp")
_BROWSER_WORDS = ("browser", "web browser", "my browser", "the browser",
                  "internet", "the internet", "chrome or firefox")


def _is_browser(w):
    c = (w.get("class") or "").lower()
    return any(b in c for b in BROWSER_CLASSES)


def preferred_browser():
    """Binary to launch: config `browser`, else the first installed of
    chrome / chromium / firefox (the same order web searches use)."""
    want = (cfg().get("browser") or "").strip()
    if want and shutil.which(want):
        return want
    for c in ("google-chrome-stable", "chromium", "firefox"):
        if shutil.which(c):
            return c
    return None


def _label(w):
    return (w.get("class") or w.get("title") or "window")[:40]


def target_window(args=None):
    """The window an action means: a named one, else the last one used."""
    wins = _clients()
    if not wins:
        return None
    needle = ((args or {}).get("what") or (args or {}).get("q") or "")
    needle = re.sub(r"^(?:the|a|an|my)\s+", "", needle.strip().lower())
    needle = re.sub(r"\s+(?:window|app)$", "", needle)
    ours = tar_opened(wins)
    if needle in _BROWSER_WORDS:
        hits = [w for w in wins if _is_browser(w)]
        if not hits:
            return False
        mine = [w for w in hits if w in ours]
        wins = mine or hits
    elif needle and needle not in _PRONOUNS:
        alias = {"terminal": "kitty", "term": "kitty",
                 "chrome": "google-chrome", "files": "nautilus",
                 "file manager": "nautilus", "editor": "codium"}.get(needle, needle)
        hits = [w for w in wins
                if alias in (w.get("class", "") + " " + w.get("title", "")).lower()]
        if not hits:
            return False                    # named, but nothing matches
        # "close kitty": prefer the kitty T.A.R. opened over the one you
        # were already typing in
        mine = [w for w in hits if w in ours]
        wins = mine or hits
    elif ours:
        wins = ours                         # "close it" = the thing I opened
    # focusHistoryID 0 = most recently focused
    return min(wins, key=lambda w: w.get("focusHistoryID", 1 << 30))


def _focus_target(args):
    """Focus the target so the active-window dispatchers hit it, not T.A.R."""
    w = target_window(args)
    if w is None:
        return None, "no app window to act on"
    if w is False:
        return None, "no window matching %r" % (args.get("what") or args.get("q"))
    sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
    return w, None


def a_fullscreen(args):
    w, err = _focus_target(args)
    if err:
        return err
    hypr("fullscreen", 0)
    return "fullscreen " + _label(w)


def a_maximize(args):
    w, err = _focus_target(args)
    if err:
        return err
    hypr("fullscreen", 1)
    return "maximized " + _label(w)


def a_float(args):
    w, err = _focus_target(args)
    if err:
        return err
    hypr("togglefloating")
    return "toggled floating on " + _label(w)


def a_center(args):
    w, err = _focus_target(args)
    if err:
        return err
    hypr("centerwindow")
    return "centered " + _label(w)


# "them" = what T.A.R. opened. "everything" = every window except T.A.R.
_PLURAL = ("them", "em", "'em", "those", "these", "both", "both of them", "all of them",
           "everything you opened", "all of those", "what you opened",
           "the ones you opened")
_EVERYTHING = ("everything", "all", "all windows", "all apps", "everything else",
               "all the windows", "all the apps", "every window", "all other windows",
               "everything but tar", "everything except tar", "all but tar")


def a_close_tab(args):
    """Close only the browser TABS whose title matches q -- never the window."""
    q = (args.get("q") or args.get("what") or args.get("title") or "").strip().lower()
    q = re.sub(r"\b(the|tab|tabs|all|of|my)\b", " ", q).strip()
    if not q:
        return "close which tab? (part of its title)"
    if not shutil.which("wtype"):
        return "wtype isn't installed"
    closed = 0
    for w in [w for w in _clients() if _is_browser(w)]:
        addr = w["address"]
        sh(["hyprctl", "dispatch", "focuswindow", "address:" + addr])
        time.sleep(0.25)
        seen = set()
        for _ in range(40):                 # walk the tabs with Ctrl+Tab
            cur = next((c for c in _clients() if c["address"] == addr), None)
            if not cur:
                break                       # the window closed (its last tab went)
            title = (cur.get("title") or "")
            if q in title.lower():
                wkeys("-M", "ctrl", "-k", "w", "-m", "ctrl")
                closed += 1
                time.sleep(0.35)
                seen.clear()
                continue
            if title in seen:
                break                       # full circle: no more matching tabs
            seen.add(title)
            wkeys("-M", "ctrl", "-k", "Tab", "-m", "ctrl")
            time.sleep(0.25)
    left = [c for c in _clients() if _is_browser(c) and q in (c.get("title") or "").lower()]
    if not closed:
        return "no open tab with %r in its title" % q
    return "closed %d tab%s matching %r -- %s" % (
        closed, "" if closed == 1 else "s", q,
        "VERIFIED: none left" if not left else "NOT VERIFIED: one still shows")


_TERMINALS = ("kitty", "foot", "alacritty", "wezterm", "konsole", "gnome-terminal",
              "xterm", "ghostty", "terminator", "tilix", "st")


def _last_action(within_s=300):
    """The last thing T.A.R. did (from actions.jsonl), if recent."""
    try:
        with open(os.path.join(DATA, "actions.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            lines = f.read().decode("utf-8", "ignore").strip().splitlines()
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("action") in ("closewin", "close"):
                continue
            return d if time.time() - d.get("at", 0) < within_s else None
    except OSError:
        pass
    return None


def _own_panels():
    """T.A.R.'s own open side panels (from the UI's ui-state.json)."""
    try:
        with open(os.path.join(DATA, "ui-state.json"), encoding="utf-8") as f:
            ui = json.load(f)
        return [k for k in ("viewer", "test", "console", "tasks", "setup", "chats") if ui.get(k)]
    except (OSError, ValueError):
        return []


def a_closewin(args):
    what = (args.get("what") or "").strip().lower()
    if what in _PLURAL + ("it", "that", "this", "this one", "that one") and not tar_opened():
        panels = _own_panels()
        if panels:
            # nothing T.A.R. opened is open, but its own panels are: those are "them"
            emit("ui", action="panel", what="all", state="close")
            return "closed T.A.R.'s own panels (%s) -- VERIFIED" % ", ".join(panels)
    if what in ("it", "that", "this", "this one", "that one"):
        # "close it" means the last thing T.A.R. showed/opened -- its OWN viewer
        # included. It once closed the user's terminal (with Claude Code in it)
        # right after "show me my camera".
        last = _last_action()
        if last and last.get("action") in ("camera_live", "cam_view", "camera_look",
                                           "cam_snap", "look", "snip"):
            emit("ui", action="camlive", state="off")
            emit("ui", action="viewer", state="off")
            return "closed T.A.R.'s camera/viewer panel -- VERIFIED"
        if not last or not any(w["address"] in {o["address"] for o in tar_opened()} for w in _clients()):
            return ("NOT DONE: 'it' is unclear -- I didn't just open a window. Ask the user "
                    "which window to close (open: %s)" % ", ".join(_label(w) for w in _clients()))
    if what in _EVERYTHING:
        wins = _clients()               # already excludes T.A.R.
        if not wins:
            return "no other windows are open"
        stop = needs_confirm("closewin", args, "close ALL %d windows: %s" % (
            len(wins), ", ".join(_label(w) for w in wins)))
        if stop:
            return stop
        for w in wins:
            sh(["hyprctl", "dispatch", "closewindow", "address:" + w["address"]])
        return "closed %d windows: %s" % (len(wins),
                                         ", ".join(_label(w) for w in wins))
    if what in _PLURAL:
        ours = tar_opened()
        if not ours:
            # Say what IS open, so the model can't claim "nothing is running"
            others = _clients()
            if others:
                return ("nothing I opened is still open. The user's own windows "
                        "are still open: %s -- closewin what=everything closes "
                        "those too" % ", ".join(_label(w) for w in others))
            return "nothing I opened is still open, and no other windows are open"
        for w in ours:
            sh(["hyprctl", "dispatch", "closewindow", "address:" + w["address"]])
        return "closed " + ", ".join(_label(w) for w in ours)
    w = target_window(args)
    if w is None:
        return "no app window to close"
    if w is False:
        return "no window matching %r" % (args.get("what") or args.get("q"))
    cls = (w.get("class") or "").lower()
    if any(t == cls or cls.endswith("." + t) for t in _TERMINALS) and \
            w["address"] not in {o["address"] for o in tar_opened()}:
        # a terminal may be running anything (an editor, a build, Claude Code)
        stop = needs_confirm("closewin", args, "close the %s terminal %r -- whatever is "
                             "running in it stops" % (_label(w), (w.get("title") or "")[:40]))
        if stop:
            return stop
    if _is_browser(w):
        # a browser window holds MANY tabs -- closing it to close one tab is
        # how T.A.R. once wiped the user's whole Chrome
        stop = needs_confirm("closewin", args, "close the WHOLE %s window with all its tabs "
                             "(%r) -- to close just a tab use close_tab" % (_label(w), (w.get("title") or "")[:40]))
        if stop:
            return stop
    sh(["hyprctl", "dispatch", "closewindow", "address:" + w["address"]])
    return "closed " + _label(w)


def a_resize(args):
    w = _int(args.get("w", 0), 0)
    h = _int(args.get("h", 0), 0)
    if not w and not h:
        return "need a size"
    _w, err = _focus_target(args)
    if err:
        return err
    # exact resize only works on floating windows; float first so it always lands
    sh(["hyprctl", "dispatch", "setfloating"])
    hypr("resizeactive", "exact", w or 800, h or 600)
    return "resized to %dx%d" % (w or 800, h or 600)


def a_grow(args):
    d = args.get("dir", "right")
    step = _int(args.get("step", 100), 100)
    dx, dy = {"l": (-step, 0), "r": (step, 0),
              "u": (0, -step), "d": (0, step)}[DIRS.get(d, "r")]
    _w, err = _focus_target(args)
    if err:
        return err
    hypr("resizeactive", dx, dy)
    return "resized %+d %+d" % (dx, dy)


def a_move(args):
    d = DIRS.get(args.get("dir", "right"), "r")
    _w, err = _focus_target(args)
    if err:
        return err
    hypr("movewindow", d)
    return "moved " + args.get("dir", "right")


def a_workspace(args):
    n = args.get("n")
    if n is None:
        return "which workspace?"
    if _int(n, -1) < 1:
        return "which workspace? (a number)"
    hypr("workspace", _int(n, 1))
    return "workspace " + str(n)


def a_sendto(args):
    n = args.get("n")
    if n is None:
        return "which workspace?"
    if _int(n, -1) < 1:
        return "which workspace? (a number)"
    n = _int(n, 1)
    w = target_window(args)
    if not w:
        return "no app window to send"
    # movetoworkspacesilent: the window goes, T.A.R. stays where you are
    hypr("movetoworkspacesilent", "%d,address:%s" % (int(n), w["address"]))
    return "sent %s to workspace %s" % (_label(w), n)


def a_pin(args):
    w, err = _focus_target(args)
    if err:
        return err
    hypr("pin")
    return "pinned " + _label(w)


def a_opacity(args):
    p = max(10, min(100, _int(args.get("pct", 100), 100)))
    w = target_window(args)
    if not w:
        return "no app window to change"
    sh(["hyprctl", "setprop", "address:" + w["address"], "alpha", str(p / 100.0)])
    return "opacity %d%%" % p


def a_winlist(_):
    rc, out = sh(["hyprctl", "-j", "clients"])
    if rc:
        return "could not list windows"
    try:
        wins = json.loads(out)
    except ValueError:
        return "could not parse window list"
    rows = [{"title": w.get("title", "")[:70], "class": w.get("class", ""),
             "ws": (w.get("workspace") or {}).get("name", ""),
             "addr": w.get("address", ""),
             "size": w.get("size", [])}
            for w in wins if w.get("mapped")]
    emit("windows", rows=rows)
    return "%d windows" % len(rows)


def a_focus(args):
    """Focus a window: by name (q=/what=/name=), "browser", or the last used."""
    needle = (args.get("q") or args.get("what") or args.get("name")
              or args.get("app") or "")
    w = target_window({"what": needle})
    if w:
        sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
        return "focused " + (w.get("title") or w.get("class") or "window")[:50]
    others = _clients()
    if not others:
        return "no windows are open"
    # say what IS open so the model can pick instead of guessing
    return ("no window matching %r. open windows: %s"
            % (needle, ", ".join("%s (%s)" % (_label(o), (o.get("title") or "")[:30])
                                 for o in others)))


# ----------------------------------------------------------------- launch

# Friendly name -> what to actually exec. Keeps the model from inventing
# binaries; anything not here falls through to a PATH lookup.
APPS = {
    "firefox": ["firefox"],
    "chrome": ["google-chrome-stable"], "chromium": ["chromium"],
    "terminal": ["kitty"], "kitty": ["kitty"], "term": ["kitty"],
    "files": ["nautilus"], "nautilus": ["nautilus"], "explorer": ["nautilus"],
    "file manager": ["nautilus"], "filemanager": ["nautilus"],
    "thunar": ["thunar"], "dolphin": ["dolphin"],
    "editor": ["codium"], "code": ["codium"], "vscode": ["codium"],
    "zed": ["zed"], "nvim": ["kitty", "-e", "nvim"], "vim": ["kitty", "-e", "nvim"],
    "discord": ["equibop"], "spotify": ["spotify"],
    "music": ["spotify"], "audacious": ["audacious"],
    "settings": ["kitty", "-e", "btop"], "monitor": ["kitty", "-e", "btop"],
    "btop": ["kitty", "-e", "btop"], "top": ["kitty", "-e", "btop"],
    "octopi": ["octopi"], "vlc": ["vlc"], "mpv": ["mpv"],
}


SITES = {
    "youtube": "https://www.youtube.com/", "yt": "https://www.youtube.com/",
    "github": "https://github.com/", "reddit": "https://www.reddit.com/",
    "google": "https://www.google.com/", "gmail": "https://mail.google.com/",
    "twitter": "https://x.com/", "x": "https://x.com/",
    "twitch": "https://www.twitch.tv/", "netflix": "https://www.netflix.com/",
    "wikipedia": "https://en.wikipedia.org/", "wiki": "https://wiki.archlinux.org/",
    "arch wiki": "https://wiki.archlinux.org/", "aur": "https://aur.archlinux.org/",
    "tiktok": "https://www.tiktok.com/", "instagram": "https://www.instagram.com/",
    "chatgpt": "https://chatgpt.com/", "claude": "https://claude.ai/",
    "gemini": "https://gemini.google.com/", "whatsapp": "https://web.whatsapp.com/",
    "discord web": "https://discord.com/app", "spotify web": "https://open.spotify.com/",
    "cookie clicker": "https://orteil.dashnet.org/cookieclicker/",
    "classroom": "https://classroom.google.com/", "google classroom": "https://classroom.google.com/",
    "calendar": "https://calendar.google.com/", "google calendar": "https://calendar.google.com/",
    "2048": "https://play2048.co/", "chess": "https://www.chess.com/play/computer",
    "wordle": "https://www.nytimes.com/games/wordle/", "monkeytype": "https://monkeytype.com/",
    "google docs": "https://docs.google.com/", "google drive": "https://drive.google.com/",
    "maps": "https://maps.google.com/", "google maps": "https://maps.google.com/",
    "translate": "https://translate.google.com/", "google translate": "https://translate.google.com/",
}


def low_name_ok(prog):
    """Programs that ARE browsers/launchers shouldn't hijack a real URL."""
    return prog.lower() not in _BROWSER_WORDS and prog.lower() not in (
        "chrome", "google-chrome", "firefox", "chromium", "xdg-open", "open")


def a_open(args):
    what = (args.get("what") or "").strip()
    if not what:
        return "open what?"
    # "open me a terminal", "open up the browser", "open us a file manager"
    # -> the bare name the table knows. Indirect objects and particles were
    # being treated as part of the app name.
    what = re.sub(r"^(?:me|us|him|her|them)\s+", "", what, flags=re.I).strip()
    what = re.sub(r"^(?:up|out)\s+", "", what, flags=re.I).strip()
    what = re.sub(r"^(?:the|a|an|my|some)\s+", "", what, flags=re.I).strip()
    what = re.sub(r"\s+(?:please|now|for me|on it|in it)$", "", what,
                  flags=re.I).strip()
    # "classroom on my student chrome account" -> that Chrome profile
    prof = resolve_profile(args.get("profile")) if args.get("profile") else None
    if args.get("profile") and not prof:
        return ("no Chrome profile matching %r. profiles: %s" % (
            args.get("profile"), ", ".join(p["name"] + (" (school/managed)" if p["managed"] else "")
                                           for p in chrome_profiles()) or "none found"))
    mp = re.search(r"\s+(?:on|in|with|using|from)\s+(?:my\s+|the\s+)?([\w-]+(?:\s+[\w-]+)?)\s+"
                   r"(?:chrome\s+)?(?:account|profile|user)$", what, flags=re.I)
    if mp:
        prof = resolve_profile(mp.group(1)) or prof
        if not prof:
            return ("no Chrome profile matching %r. profiles: %s" % (
                mp.group(1), ", ".join(p["name"] + (" (school/managed)" if p["managed"] else "")
                                       for p in chrome_profiles()) or "none found"))
        what = what[:mp.start()].strip()
        low = what.lower()
    # "cookie clicker on chrome" -> the site, opened in chrome
    want_browser = None
    mb = re.search(r"\s+(?:on|in|with|using)\s+(?:my\s+|the\s+)?(chrome|google chrome|firefox|"
                   r"the browser|browser|chromium)$", what, flags=re.I)
    if mb:
        want_browser = {"chrome": "google-chrome-stable", "google chrome": "google-chrome-stable",
                        "firefox": "firefox", "chromium": "chromium"}.get(mb.group(1).lower())
        what = what[:mb.start()].strip()
    low = what.lower()

    # "open my browser": use the one that's already open, else the preferred
    if low in _BROWSER_WORDS:
        w = target_window({"what": "browser"})
        if w:
            sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
            return "%s is already open -- focused it" % _label(w)
        b = preferred_browser()
        if not b:
            return "no browser installed"
        sh([b], detach=True)
        return "opening " + b

    if low in APPS:
        argv = APPS[low]
        if shutil.which(argv[0]):
            sh(argv, detach=True)
            return "opening " + low
        web = SITES.get(low + " web")
        if web:                     # app missing, but it has a web version
            sh(["xdg-open", web], detach=True)
            return "%s isn't installed -- opened the web version" % argv[0]
        return "%s isn't installed" % argv[0]

    if re.match(r"^(https?://|www\.)", low):
        # the model sometimes turns a program into a website ("do something
        # cool" -> https://cmatrix.org). If that name is a program installed
        # here and the user didn't ask for a site, run the program.
        host = re.sub(r"^(https?://)?(www\.)?", "", low).split("/")[0]
        prog = host.split(".")[0]
        said = (USER_SAID or "").lower()
        if prog and len(prog) > 2 and shutil.which(prog) and prog not in ("google", "www") \
                and low_name_ok(prog) and host not in said \
                and not re.search(r"\b(site|website|web ?page|url|link|online|\.com|\.org)\b", said):
            return a_open({"what": prog}) + " (the installed program, not its website)"
        url = what if low.startswith("http") else "https://" + what
        return open_url(url, prof, want_browser)

    # "open downloads" -> the (Hebrew-named) XDG folder
    xd = xdg_dir(low)
    if xd:
        sh(["xdg-open", xd], detach=True)
        return "opening " + xd

    # "open youtube" is a website, not a binary
    site = SITES.get(low) or SITES.get(re.sub(r"\.com$", "", low))
    if prof and site:
        return open_url(site, prof, want_browser)
    if prof and not site:
        # unknown site name: jump to it inside that profile
        return open_url("https://duckduckgo.com/?q=" + urlquote("\\" + what), prof, want_browser)
    if site and _already_open(low):
        w = _already_open(low)
        sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
        return "already open (%r) -- switched to it -- VERIFIED" % (w.get("title") or "")[:50]
    if site:
        return open_url(site, None, want_browser)

    path = os.path.expanduser(what)
    if os.path.exists(path):
        sh(["xdg-open", path], detach=True)
        return "opening " + os.path.basename(path)

    if shutil.which(low):
        # A console program launched detached paints nothing and looks like a
        # no-op. If it has no .desktop entry, assume it needs a terminal.
        if _has_desktop_entry(low):
            sh([low], detach=True)
            return "launching " + low
        return a_run({"cmd": what})

    # "a foot terminal" / "chrome browser" -- the extra noun is description,
    # not part of the name. Try the leading word before giving up.
    words = low.split()
    if len(words) > 1:
        head = words[0]
        if head in APPS:
            argv = APPS[head]
            if shutil.which(argv[0]):
                sh(argv, detach=True)
                return "opening " + head
        if shutil.which(head):
            if _has_desktop_entry(head):
                sh([head], detach=True)
                return "launching " + head
            return a_run({"cmd": head})

    # last resort: let the desktop's own matcher try
    if shutil.which("gtk-launch"):
        rc, _ = sh(["gtk-launch", low])
        if rc == 0:
            return "opening " + low
    return "don't know how to open %r" % what


# ----------------------------------------------------------------- code

EDITOR = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "codium"


def a_edit(args):
    p = os.path.expanduser((args.get("path") or "").strip())
    if not p:
        return "edit what?"
    ed = EDITOR if shutil.which(EDITOR) else (
        "codium" if shutil.which("codium") else "nvim")
    if ed == "nvim":
        sh(["kitty", "-e", "nvim", p], detach=True)
    else:
        sh([ed, p], detach=True)
    return "opened %s in %s" % (os.path.basename(p), ed)


def a_read(args):
    p = os.path.expanduser((args.get("path") or "").strip())
    if not p or not os.path.isfile(p):
        return "no such file: " + str(p)
    if os.path.getsize(p) > 400_000:
        return "that file is too big to read inline"
    n = int(args.get("lines", 200))
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            body = "".join(f.readlines()[:n])
    except OSError as e:
        return "cannot read: %s" % e
    emit("file", path=p, body=body)
    return "read %s" % os.path.basename(p)


def a_grep(args):
    q = (args.get("q") or "").strip()
    where = os.path.expanduser(args.get("path") or "~/.config/hypr")
    if not q:
        return "search for what?"
    rc, out = sh(["grep", "-rn", "--max-count=3", "-I",
                  "--exclude-dir=.git", "-e", q, where], timeout=25)
    hits = [l for l in out.splitlines() if l][:40]
    emit("grep", q=q, hits=hits)
    return "%d hits for %r" % (len(hits), q)


def a_patch(args):
    """
    Guarded write. Always snapshots first, into the same store the rest of
    T.A.R. uses, so a bad model suggestion is one `undo-patch` away.
    """
    p = os.path.expanduser((args.get("path") or "").strip())
    body = args.get("body")
    if not p or body is None:
        return "patch needs a path and a body"
    if not os.path.isfile(p):
        return "refusing to create a new file this way: " + p
    bak_dir = os.path.join(DATA, "patches")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, "%s-%s.bak" % (
        os.path.basename(p), time.strftime("%Y%m%d-%H%M%S")))
    try:
        shutil.copy2(p, bak)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError as e:
        return "patch failed: %s" % e
    emit("patched", path=p, backup=bak)
    return "patched %s (backup kept)" % os.path.basename(p)


# ----------------------------------------------------------------- media / system

def _pct_arg(args):
    for k in ("pct", "level", "value", "percent", "volume", "brightness", "to", "amount"):
        v = args.get(k)
        if v not in (None, "") and re.match(r"^\s*\d+(\.\d+)?\s*%?\s*$", str(v)):
            return v
    return None


def a_volume(args):
    if args.get("pct") is None and _pct_arg(args) is not None:
        args = dict(args, pct=_pct_arg(args))
    if args.get("mute"):
        sh(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        return "mute toggled"
    if args.get("pct") is not None:
        p = max(0, min(150, _int(args["pct"], 50)))
        sh(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % p])
        return "volume %d%%" % p
    d = _int(args.get("delta", 5), 5)
    sh(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%+d%%" % d])
    return "volume %+d%%" % d


def a_bright(args):
    if args.get("pct") is None and _pct_arg(args) is not None:
        args = dict(args, pct=_pct_arg(args))
    d = args.get("delta")
    if not shutil.which("brightnessctl"):
        return "brightnessctl isn't installed"
    if args.get("pct") is not None:
        pct = max(1, min(100, _int(args["pct"], 50)))
        sh(["brightnessctl", "set", "%d%%" % pct])
        return "brightness %d%%" % pct
    d = _int(d or 10, 10)
    sh(["brightnessctl", "set", ("%d%%+" % d) if d > 0 else ("%d%%-" % -d)])
    return "brightness %+d%%" % d


def a_media(args):
    what = args.get("what", "play-pause")
    # explicit play/pause instead of toggling: "pause" twice must not resume
    sh(["playerctl", {"play": "play", "resume": "play", "unpause": "play",
                      "pause": "pause", "next": "next", "prev": "previous",
                      "stop": "stop"}.get(what, "play-pause")])
    return what


def a_screenshot(_):
    script = os.path.expanduser("~/.config/hypr/scripts/screenshot.sh")
    if os.path.exists(script):
        sh(["bash", script], detach=True)
        return "screenshot"
    if shutil.which("grim"):
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, time.strftime("shot-%Y%m%d-%H%M%S.png"))
        sh(["grim", p])
        return "saved " + os.path.basename(p)
    return "no screenshot tool found"


def a_lock(_):
    sh(["bash", os.path.expanduser("~/.config/hypr/scripts/bite-lock.sh")],
       detach=True)
    return "locking"


def a_wallpaper(_):
    sh(["bash", os.path.expanduser(
        "~/.config/hypr/scripts/wallpaper-restore.sh")], detach=True)
    return "wallpaper reshuffled"


def a_sysinfo(_):
    rc, out = sh(["sh", "-c",
                  "uptime -p; free -h | awk 'NR==2{print $3\" / \"$2\" RAM\"}'"])
    emit("sysinfo", v=out)
    return out.replace("\n", " · ")


# ----------------------------------------------------------------- camera

def a_cam_view(args):
    # "live view" means inside T.A.R.'s viewer -- never a separate app window.
    # mpv is only used when explicitly asked for a window (window=1).
    if str(args.get("window", "")).lower() not in ("1", "true", "yes"):
        return a_camera_live({"state": "on"})
    dev = effective_device("video")
    if not dev:
        return "no camera found"
    if not shutil.which("mpv"):
        return "mpv isn't installed"
    sh(["mpv", "--profile=low-latency", "--untimed", "--no-osc",
        "--title=T.A.R. camera", "av://v4l2:" + dev], detach=True)
    return "camera up on " + dev


def _camera_holder(dev):
    """Which program has the camera open? e.g. 'Chrome (pid 74841)'."""
    rc, out = sh(["fuser", dev], timeout=5)
    pids = [p for p in re.findall(r"\d+", out or "") if int(p) != os.getpid()]
    names = []
    for pid in pids[:3]:
        try:
            comm = open("/proc/%s/comm" % pid).read().strip()
        except OSError:
            continue
        nice = {"chrome": "Chrome", "firefox": "Firefox", "qs": "T.A.R.'s live view",
                "quickshell": "T.A.R.'s live view", "mpv": "mpv", "obs": "OBS",
                "zoom": "Zoom", "discord": "Discord"}.get(comm.lower(), comm)
        names.append("%s (pid %s)" % (nice, pid))
    return ", ".join(names)


def a_selfie(args):
    """Timed webcam photo: countdown on screen, then snap and show it."""
    delay = max(0, min(30, _int(args.get("delay") or args.get("in") or 0, 0)))
    for n in range(delay, 0, -1):
        emit("ui", action="banner", text=str(n))
        time.sleep(1)
    if delay:
        emit("ui", action="flash")
    out = a_cam_snap({})
    if out.startswith("captured"):
        return "selfie taken%s: %s" % (" after a %d s countdown" % delay if delay else "", out)
    return "NOT DONE (no photo taken): " + out


def a_cam_snap(_):
    # the live view holds the camera -- take the photo FROM it instead
    live = os.path.join(DATA, "live-frame.jpg")
    if os.path.exists(live) and time.time() - os.path.getmtime(live) < 3:
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, time.strftime("cam-%Y%m%d-%H%M%S.jpg"))
        shutil.copyfile(live, p)
        emit("image", path=p, source="camera", caption="photo from the live camera")
        return "captured %s (from the live view) -- VERIFIED. To SEE it: look path=%s" % (p, p)
    dev = effective_device("video")
    if not dev:
        return "no camera found"
    if not shutil.which("ffmpeg"):
        return "ffmpeg isn't installed"
    os.makedirs(SHOTS, exist_ok=True)
    p = os.path.join(SHOTS, time.strftime("cam-%Y%m%d-%H%M%S.jpg"))
    for attempt in range(2):            # a live camera view can hold the device a moment
        rc, out = sh(["ffmpeg", "-y", "-f", "v4l2", "-i", dev,
                      "-frames:v", "1", p], timeout=25)
        if not rc and os.path.exists(p):
            break
        time.sleep(1.0)
    if rc or not os.path.exists(p):
        who = _camera_holder(dev)
        return ("camera grab failed -- %s is using the camera. Close it (or the browser tab "
                "using the camera) and try again." % who) if who else \
               "camera grab failed (the camera didn't answer)"
    emit("image", path=p, source="camera", caption="camera photo")
    return "captured %s -- to SEE what's in it, call look with path=%s" % (p, p)


def a_mouse_test(_):
    """Setup TEST: move the pointer in a small square and back -- no clicks."""
    if not _pointer_ok():
        return _NO_POINTER
    try:
        x, y = (int(v) for v in sh(["hyprctl", "cursorpos"])[1].replace(",", " ").split()[:2])
    except ValueError:
        return "couldn't read the cursor position"
    seen = []
    for dx, dy in ((40, 0), (40, 40), (0, 40), (0, 0)):
        sh(["hyprctl", "dispatch", "movecursor", str(x + dx), str(y + dy)])
        time.sleep(0.15)
        seen.append(sh(["hyprctl", "cursorpos"])[1])
    moved = len(set(seen)) > 1
    return ("VERIFIED: the pointer moved and came back -- clicking works (wlrctl at %s)" % _wlrctl()
            if moved else "NOT VERIFIED: the pointer didn't move")


def _set_cfg(key, value):
    p = os.path.join(DATA, "config.json")
    c = cfg()
    c[key] = value
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def a_evil(args):
    """EVIL MODE: red UI, reboot animation, villain persona, missiles tab."""
    st = str(args.get("state") or "on").lower()
    on = st not in ("off", "stop", "disable", "false", "0", "normal", "turn off",
                    "deactivate", "exit", "leave")
    _set_cfg("evil", on)
    emit("ui", action="evil", state="on" if on else "off")
    return ("EVIL MODE ENGAGED -- UI red, villain persona on, missile system unlocked "
            "(say 'send missiles to <city>') -- VERIFIED" if on else
            "evil mode off -- back to normal T.A.R. -- VERIFIED")


def _nominatim(params):
    import urllib.parse
    import urllib.request
    q = dict(params, format="json", **{"accept-language": "en"})
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "T.A.R.-assistant/1.0 (BITE-OS)"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def _geocode(place):
    """Place -> {lat, lon, name, country, kind} via OpenStreetMap, cached.
    A COUNTRY (or region) is resolved to one of its real cities."""
    cache_p = os.path.join(DATA, "geo-cache.json")
    try:
        cache = json.load(open(cache_p))
    except (OSError, ValueError):
        cache = {}
    key = "v2:" + place.strip().lower()
    hit = cache.get(key)
    if hit is None:
        res = _nominatim({"q": place, "limit": 1, "addressdetails": 1})
        if not res:
            return None
        r0 = res[0]
        addr = r0.get("address") or {}
        hit = {"lat": float(r0["lat"]), "lon": float(r0["lon"]),
               "name": r0.get("display_name", place).split(",")[0],
               "country": addr.get("country", ""), "code": addr.get("country_code", ""),
               "kind": r0.get("addresstype") or r0.get("type") or "", "cities": []}
        if hit["kind"] in ("country", "state", "region", "province") and hit["code"]:
            # a country/region: its biggest real cities (bundled Natural Earth data)
            try:
                with open(os.path.join(HERE, "assets", "cities.json"), encoding="utf-8") as f:
                    cs = json.load(f).get(hit["code"].lower(), {}).get("cities", [])
                hit["cities"] = [[c[0], c[1], c[2], c[4]] for c in cs]
            except (OSError, ValueError):
                pass
        cache[key] = hit
        try:
            json.dump(cache, open(cache_p, "w"))
        except OSError:
            pass
    return hit


# proxy hops for the fake trace route (public city coordinates; IPs are made up)
_PROXIES = [("Reykjavik", 64.15, -21.94), ("Sao Paulo", -23.55, -46.63), ("Lagos", 6.52, 3.38),
            ("Singapore", 1.35, 103.82), ("Moscow", 55.76, 37.62), ("Tallinn", 59.44, 24.75),
            ("Panama City", 8.98, -79.52), ("Cape Town", -33.92, 18.42), ("Seoul", 37.57, 126.98),
            ("Bucharest", 44.43, 26.10), ("Anchorage", 61.22, -149.90), ("Perth", -31.95, 115.86),
            ("Nairobi", -1.29, 36.82), ("Mumbai", 19.08, 72.88), ("Vancouver", 49.28, -123.12),
            ("Ulaanbaatar", 47.89, 106.91)]


def a_missiles(args):
    """Evil-mode easter egg: a SIMULATED sci-fi missile strike on a map."""
    import random
    if not cfg().get("evil"):
        return ("the missile system only exists in EVIL MODE -- say 'turn on evil mode' first. "
                "(nothing happened)")
    target = (args.get("target") or args.get("city") or args.get("what") or "").strip()
    target = re.sub(r"^(?:the )", "", target, flags=re.I).strip(" .!?")
    if not target:
        emit("ui", action="missiles")
        return "missile command tab open -- awaiting a target -- VERIFIED"
    try:
        hit = _geocode(target)
    except Exception as e:
        return "targeting failed: couldn't look up %r (%s)" % (target, str(e)[:80])
    if not hit:
        return "no such place as %r on the map" % target
    name, lat, lon = hit["name"], hit["lat"], hit["lon"]
    country = hit.get("country") or ""
    if hit.get("cities"):
        # a country was named: T.A.R. picks a real city inside it -- the
        # capital and the biggest cities most often
        cs = hit["cities"][:6]
        weights = [(4 if c[3] else 1) * (6 - i) for i, c in enumerate(cs)]
        name, lat, lon = random.choices(cs, weights=weights)[0][:3]
    label = name if not country or country.lower() == name.lower() else "%s, %s" % (name, country)
    hops = random.sample(_PROXIES, 4)
    route = [[h[0], h[1], h[2], "%d.%d.%d.%d" % (random.randint(23, 223), random.randint(0, 255),
                                                  random.randint(0, 255), random.randint(1, 254))]
             for h in hops]
    origin = {}
    try:                            # trace starts at the user's own location, if known
        lc = json.load(open(os.path.join(DATA, "loc-cache.json")))
        if "lat" in lc and "lon" in lc:
            origin = {"olat": float(lc["lat"]), "olon": float(lc["lon"])}
    except (OSError, ValueError, TypeError):
        pass
    emit("ui", action="missiles", target=label, city=name, country=country, lat=lat, lon=lon,
         route=json.dumps(route), **origin)
    return ("SIMULATED strike on %s (%.2f, %.2f) playing on screen with narration: trace route "
            "through %s, breach, launch, impact in 20 s, narrated out loud by the UI. Animation only "
            "-- VERIFIED. Reply with ONE word or two in villain voice (e.g. 'Excellent.') -- the "
            "UI does the talking." % (label, lat, lon,
                                                                  ", ".join(h[0] for h in hops)))


def a_camera_live(args):
    """Stream the webcam live inside T.A.R.'s viewer (no app, no photo)."""
    st = str(args.get("state") or args.get("what") or "on").lower()
    st = "off" if st in ("off", "stop", "close", "false", "0") else "on"
    emit("ui", action="camlive", state=st)
    return ("live camera %s in T.A.R.'s viewer -- VERIFIED" % ("on" if st == "on" else "off"))


def a_camera_look(args):
    """Take a photo with the webcam and answer a question about it."""
    q = args.get("q") or args.get("question") or "Describe what you see."
    live = os.path.join(DATA, "live-frame.jpg")
    if os.path.exists(live) and time.time() - os.path.getmtime(live) < 3:
        # the live view is running: answer from its current frame
        txt, err = _gemini_image(live, "This is a frame from the user's webcam (the user "
                                 "is usually the person in it). " + q + " Answer directly and briefly.")
        return (err or txt or "couldn't make sense of the frame") + " -- VERIFIED: from the live camera"
    snap = a_cam_snap({})
    m = re.search(r"captured (\S+\.jpg)", snap)
    if not m:
        return snap
    txt, err = _gemini_image(m.group(1), "This is a photo from the user's webcam (the "
                             "user is usually the person in it). " + q +
                             " Answer directly and briefly.")
    return (err or txt or "couldn't make sense of the photo") + " -- VERIFIED: from a fresh camera photo"


def a_show(args):
    """
    Show the most recent capture (or a named file) in the UI panel.
    "show me" used to be parsed as reading a FILE named "me".
    """
    p_ = (args.get("path") or args.get("what") or "").strip()
    if p_ and p_.lower() not in ("me", "it", "that", "this", "them"):
        path = os.path.expanduser(p_)
        if not os.path.isfile(path):
            return "no such file: " + p_
    else:
        if not os.path.isdir(SHOTS):
            return "nothing captured yet"
        files = [os.path.join(SHOTS, f) for f in os.listdir(SHOTS)
                 if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not files:
            return "nothing captured yet"
        path = max(files, key=os.path.getmtime)
    emit("image", path=path, source="show")
    return "showing " + os.path.basename(path)


def a_cam_off(_):
    sh(["pkill", "-f", "T.A.R. camera"])
    return "camera closed"


# ----------------------------------------------------------------- real work
# The "it should actually do things" set. Everything here is a real binary that
# exists on this machine -- checked, not assumed. Notable gap: there is no
# ydotool/dotool, and wtype cannot synthesise mouse clicks on Wayland, so
# clicking at coordinates is NOT offered rather than silently failing.

def _active_window():
    rc, out = sh(["hyprctl", "-j", "activewindow"])
    try:
        d = json.loads(out) if rc == 0 else {}
    except ValueError:
        d = {}
    return d if d.get("address") else None


def _away_from_tar(args):
    """Typing goes to the focused window -- which is T.A.R. while you talk to
    it. Move focus to the target app first (named via into=/app=, else last).
    Then REFUSE unless a real app window has focus: with nothing focused, keys
    land on the desktop shell, and that once swapped the user's whole rice."""
    act = _active_window()
    named = args.get("into") or args.get("app")
    if not act or act.get("title") == TAR_TITLE or named:
        _w, err = _focus_target({"what": named} if named else {})
        if err:
            return err
        time.sleep(0.15)                # let the compositor move keyboard focus
        act = _active_window()
    if not act or act.get("title") == TAR_TITLE:
        return ("NOT DONE: no app window has keyboard focus, so the keys would hit the "
                "desktop shell. Open/focus the app first (or name it with into=).")
    cls = (act.get("class") or "").lower()
    if any(t == cls or cls.endswith("." + t) for t in _TERMINALS) and \
            act.get("address") not in {o["address"] for o in tar_opened()}:
        # the user's own terminal may be running anything (it typed a URL into
        # the terminal running Claude Code) -- keys go there only with a yes
        return needs_confirm("type", args, "type/press keys into YOUR %s terminal %r" % (
            _label(act), (act.get("title") or "")[:40]))
    return None


# combos that drive the desktop itself (rice/shell switch, logout, kill...) --
# only when the user typed the request themselves
_SYSTEM_COMBO = re.compile(r"(super|logo|win|meta|mod4)|ctrl\+alt\+(backspace|delete|del)", re.I)


# What the user actually wrote recently -- set by the cloud backend before it
# runs tools. Personal info may only be typed if the user supplied it.
USER_SAID = ""
_SENSITIVE_FIELD = re.compile(
    r"\b(e-?mail|password|passcode|pass ?word|pin|login|log in|sign ?in|user ?name|"
    r"account|phone|address|card|cvv|cvc|iban|ssn|social security|otp|2fa|code)\b", re.I)
_PERSONAL_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s-]{7,}\d")


def _personal_guard(text, target=""):
    """Refuse to type personal info the user didn't give us."""
    supplied = text.strip() and text.strip().lower() in (USER_SAID or "").lower()
    if supplied:
        return None
    if _SENSITIVE_FIELD.search(target or "") or _PERSONAL_TEXT.search(text or ""):
        return ("refused: that's personal info (%s) and the user never told you "
                "what it is. Ask them for the exact text -- never guess emails, "
                "passwords, usernames or numbers." % (target or "email/phone"))
    return None


# -- typing safety -------------------------------------------------------------
# Synthetic keys go through Hyprland's keybind handling like real ones, and
# wtype typing has twice coincided with the rice-switch bind firing. So while
# T.A.R. types, Hyprland sits in an EMPTY submap (no binds but SUPER+Escape =
# leave it), and text goes in by paste instead of key-by-key. The user's own
# binds are never touched: the submap is registered at runtime and a watchdog
# drops back to the normal map if this process dies mid-type.
TYPE_SUBMAP = "tar_typing"
_paused = 0


def _submap_now():
    rc, out = sh(["hyprctl", "submap"], timeout=5)
    return out.strip() if rc == 0 else ""


class keys_paused:
    """Context: user keybinds can't fire while synthetic keys are sent. Nests."""
    def __enter__(self):
        global _paused
        _paused += 1
        if _paused == 1 and shutil.which("hyprctl"):
            # registered at runtime, so a config reload drops it -- check each time
            rc, out = sh(["hyprctl", "binds", "-j"], timeout=5)
            if '"submap": "%s"' % TYPE_SUBMAP not in out:
                sh(["hyprctl", "--batch", "keyword submap %s ; keyword bind SUPER,escape,submap,reset"
                    " ; keyword submap reset" % TYPE_SUBMAP], timeout=5)
            sh(["hyprctl", "dispatch", "submap", TYPE_SUBMAP], timeout=5)
            sh(["sh", "-c", 'for i in $(seq 180); do kill -0 %d 2>/dev/null || break; sleep 1; done; '
                '[ "$(hyprctl submap)" = %s ] && hyprctl dispatch submap reset' % (os.getpid(), TYPE_SUBMAP)],
               detach=True)
        return self

    def __exit__(self, *exc):
        global _paused
        _paused -= 1
        if _paused == 0 and _submap_now() == TYPE_SUBMAP:
            sh(["hyprctl", "dispatch", "submap", "reset"], timeout=5)
        return False


def wkeys(*args, timeout=15):
    """wtype with the keybinds paused -- every key press goes through here."""
    with keys_paused():
        return sh(["wtype"] + list(args), timeout=timeout)


def _clip_put(txt, mime=None):
    # wl-copy stays alive as the clipboard OWNER after reading stdin, so
    # subprocess.run() waits on it forever. Hand it the data and walk away.
    p_ = subprocess.Popen(["wl-copy"] + (["-t", mime] if mime else []), stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          start_new_session=True)
    p_.stdin.write(txt if isinstance(txt, bytes) else txt.encode())
    p_.stdin.close()


def _clip_save():
    """(mime, bytes) of the current clipboard, so a paste can put it back."""
    try:
        types = subprocess.run(["wl-paste", "-l"], capture_output=True, text=True, timeout=3).stdout.split()
        if not types:
            return None
        mime = next((t for t in types if t.startswith("text/plain")), types[0])
        data = subprocess.run(["wl-paste", "-n", "-t", mime], capture_output=True, timeout=3).stdout
        return (mime, data) if len(data) < 20_000_000 else None
    except (OSError, subprocess.SubprocessError):
        return None


def put_text(text, timeout=20):
    """Insert text into the focused window: clipboard + Ctrl+V (Ctrl+Shift+V in
    a terminal), keybinds paused. The user's clipboard is restored after."""
    if not shutil.which("wl-copy") or not shutil.which("wtype"):
        return 127, "need wl-copy and wtype"
    act = _active_window() or {}
    cls = (act.get("class") or "").lower()
    term = any(t == cls or cls.endswith("." + t) for t in _TERMINALS)
    mini_say("typing: " + text[:60] + ("…" if len(text) > 60 else ""))
    old = _clip_save()
    try:
        _clip_put(text)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "copy failed: %s" % e
    time.sleep(0.12)                    # let wl-copy take the selection
    with keys_paused():
        rc, out = sh(["wtype", "-M", "ctrl"] + (["-M", "shift"] if term else []) + ["-k", "v"]
                     + (["-m", "shift"] if term else []) + ["-m", "ctrl"], timeout=timeout)
    time.sleep(0.5)                     # the app reads the clipboard asynchronously
    if old:
        try:
            _clip_put(old[1], old[0])
        except (OSError, subprocess.SubprocessError):
            pass
    return rc, out


def a_type(args):
    """Type text into whatever window has focus."""
    txt = args.get("text") or args.get("what") or ""
    if not txt:
        return "type what?"
    if not shutil.which("wtype"):
        return "wtype isn't installed"
    stop = _personal_guard(txt, args.get("into") or args.get("field") or "")
    if stop:
        return stop
    err = _away_from_tar(args)
    if err:
        return err
    rc, out = put_text(txt)
    return "typed %d chars" % len(txt) if rc == 0 else "typing failed: " + out


def a_key(args):
    """Press a key combo, e.g. key combo=ctrl+t"""
    combo = (args.get("combo") or args.get("what") or "").strip()
    if not combo:
        return "which key?"
    if not shutil.which("wtype"):
        return "wtype isn't installed"
    if _SYSTEM_COMBO.search(combo.replace(" ", "")):
        stop = needs_confirm("key", args, "press %s -- a desktop shortcut (can switch your "
                             "rice/shell, log out or close things)" % combo)
        if stop:
            return stop
    err = _away_from_tar(args)
    if err:
        return err
    argv = ["wtype"]
    parts = [p.strip() for p in combo.replace("-", "+").split("+") if p.strip()]
    # wtype wants keysym names: "enter" is Return, "esc" is Escape
    keysyms = {"enter": "Return", "return": "Return", "esc": "Escape",
               "escape": "Escape", "tab": "Tab", "space": "space",
               "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
               "up": "Up", "down": "Down", "left": "Left", "right": "Right",
               "home": "Home", "end": "End", "pageup": "Prior",
               "pagedown": "Next"}
    if parts:
        parts[-1] = keysyms.get(parts[-1].lower(), parts[-1])
    mods = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
            "super": "logo", "win": "logo", "meta": "logo"}
    for p in parts[:-1]:
        argv += ["-M", mods.get(p.lower(), p.lower())]
    argv += ["-k", parts[-1]]
    for p in reversed(parts[:-1]):
        argv += ["-m", mods.get(p.lower(), p.lower())]
    if _SYSTEM_COMBO.search(combo.replace(" ", "")):
        rc, out = sh(argv, timeout=15)  # a confirmed desktop shortcut must reach Hyprland
    else:
        rc, out = wkeys(*argv[1:])
    return "pressed " + combo if rc == 0 else "key failed: " + out


def a_screen_read(args):
    """
    OCR the screen (or a region) so T.A.R. can answer questions about what is
    actually on it. grim grabs, tesseract reads.
    """
    if not shutil.which("grim") or not shutil.which("tesseract"):
        return "need grim and tesseract for screen reading"
    os.makedirs(SHOTS, exist_ok=True)
    shot = os.path.join(SHOTS, "screen-ocr.png")
    if args.get("region") in ("1", "true", "yes"):
        if not shutil.which("slurp"):
            return "slurp isn't installed"
        rc, geo = sh(["slurp"], timeout=60)
        if rc or not geo:
            return "selection cancelled"
        rc, _ = sh(["grim", "-g", geo, shot], timeout=20)
    else:
        rc, _ = sh(["grim", shot], timeout=20)
    if rc:
        return "screen grab failed"
    rc, out = sh(["tesseract", shot, "stdout", "--psm", "6"], timeout=90)
    text = "\n".join(l for l in (out or "").splitlines() if l.strip())
    if not text:
        return "nothing readable on screen"
    emit("screen_text", text=text[:4000], path=shot)
    return "read %d lines off the screen" % len(text.splitlines())


def a_clip_get(_):
    if not shutil.which("wl-paste"):
        return "wl-paste isn't installed"
    rc, out = sh(["wl-paste", "--no-newline"], timeout=10)
    if rc:
        return "clipboard is empty"
    emit("clipboard", text=out[:4000])
    return "clipboard: " + (out[:70] + ("…" if len(out) > 70 else ""))


def a_clip_set(args):
    txt = args.get("text") or args.get("what") or ""
    if not txt:
        return "copy what?"
    if not shutil.which("wl-copy"):
        return "wl-copy isn't installed"
    try:
        _clip_put(txt)
    except (OSError, subprocess.SubprocessError) as e:
        return "copy failed: %s" % e
    return "copied %d chars" % len(txt)


def a_dns(args):
    host = (args.get("host") or args.get("what") or "").strip()
    if not host:
        return "look up what?"
    if not shutil.which("dig"):
        return "dig isn't installed"
    rc, out = sh(["dig", "+short", host], timeout=20)
    ips = [l for l in (out or "").splitlines() if l.strip()]
    emit("dns", host=host, records=ips)
    return "%s -> %s" % (host, ", ".join(ips[:4]) if ips else "no records")


# ---- Chrome profiles: "open classroom on my student account" -----------------
def chrome_profiles():
    """[{dir, name, given, managed}] from Chrome's Local State (names only)."""
    out = []
    for base in ("~/.config/google-chrome", "~/.config/chromium"):
        p = os.path.expanduser(base + "/Local State")
        try:
            with open(p, encoding="utf-8") as f:
                ic = json.load(f).get("profile", {}).get("info_cache", {})
        except (OSError, ValueError):
            continue
        for d, v in ic.items():
            out.append({"dir": d, "name": (v.get("name") or "").replace("\u200f", "").strip(),
                        "given": v.get("gaia_given_name") or "",
                        "managed": bool(v.get("is_managed")),
                        "browser": "chromium" if "chromium" in base else "google-chrome-stable"})
        if out:
            break
    return out


def resolve_profile(want):
    """'student' / 'school' -> the managed profile, 'main' -> Default, or a name."""
    raw = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", (want or "")).strip().lower()
    profs = chrome_profiles()
    for p in profs:                         # a profile's own NAME / folder
        if raw and raw in (p["name"].lower(), p["dir"].lower()):
            return p
    # a PERSON's name ("Rephael" / "רפאל") is on several profiles (the school
    # account belongs to the same person) -> it means the everyday account,
    # unless they also said school/student/work
    if raw and not re.search(r"\b(student|school|work|class|edu|learning)\b", raw):
        hits = [p for p in profs if p["given"] and p["given"].lower() in raw]
        if hits:
            return next((p for p in hits if not p["managed"]), None) or main_profile() or hits[0]
    want = re.sub(r"\b(my|the|chrome|google|account|profile|user)\b", " ", raw).strip()
    if not want:
        return None
    for p in profs:                         # an exact name wins
        if want in (p["name"].lower(), p["given"].lower(), p["dir"].lower()):
            return p
    if re.search(r"\b(student|school|work|managed|class|edu|learning)\b", want):
        m = [p for p in profs if p["managed"]]
        if m:
            return m[0]
    if re.search(r"\b(main|personal|default|normal|regular|own)\b", want):
        return next((p for p in profs if p["dir"] == "Default"), None)
    return next((p for p in profs if want in p["name"].lower() or want in p["given"].lower()), None)


def _profile_argv(prof, url):
    return [prof["browser"], "--profile-directory=" + prof["dir"], url]


def main_profile():
    """The user's everyday account: config `chrome_profile`, else Default."""
    want = (cfg().get("chrome_profile") or "").strip()
    profs = chrome_profiles()
    return (next((p for p in profs if p["dir"] == want or p["name"] == want), None) if want
            else None) or next((p for p in profs if p["dir"] == "Default"), None)


USER_PROFILE = None          # the ONE account the user named this turn, if any


def profile_from_message(text):
    """The single Chrome account a message names ('rephael', 'learning',
    'student', 'my main account'...), or None if none / more than one."""
    low = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", (text or "").lower())
    found = {}
    for p in chrome_profiles():
        names = {p["name"].lower(), p["given"].lower()} - {""}
        if any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(n), low) for n in names):
            q = resolve_profile(p["given"] or p["name"]) if not p["managed"] or \
                p["name"].lower() in low else p
            if q:
                found[q["dir"]] = q
    if re.search(r"\b(student|school|class(?:room)? account|learning)\b", low):
        q = resolve_profile("school")
        if q:
            found[q["dir"]] = q
    if re.search(r"\b(main|normal|personal|regular|own) (?:chrome )?(?:account|profile)\b", low):
        q = main_profile()
        if q:
            found[q["dir"]] = q
    return next(iter(found.values())) if len(found) == 1 else None


def open_url(url, prof=None, want_browser=None):
    """Open a URL. In Chrome it ALWAYS goes to an explicit profile -- the one
    asked for, else the main account -- so a fresh Chrome start can't land in
    whatever profile happened to be used last."""
    b = (want_browser if want_browser and shutil.which(want_browser) else preferred_browser())
    if b and ("chrome" in b or "chromium" in b):
        if USER_PROFILE:                # the account the USER named beats the model's guess
            prof = USER_PROFILE
        prof = prof or main_profile()
        if prof:
            sh(_profile_argv(prof, url), detach=True)
            return "opened %s in the %r Chrome profile%s" % (
                url[:70], prof["name"],
                " (school/learning account)" if prof["managed"]
                else (" (main -- the user's %r account)" % prof["given"] if prof["given"] else " (main)")
                if prof["dir"] == "Default" else "")
    sh([b, url] if b else ["xdg-open", url], detach=True)
    return "opened %s in %s" % (url[:70], b or "the browser")


def _already_open(q):
    """A browser window whose title already shows this site/game -> it, else None."""
    words = re.sub(r"https?://|www\.|\.com|\.org|\.net|/.*$|\b(game|site|website|online|play)\b",
                   " ", q.lower().lstrip("\\"))
    key = " ".join(w for w in words.split() if len(w) > 2)
    if not key:
        return None
    for w in _clients():
        if _is_browser(w) and key in (w.get("title") or "").lower():
            return w
    return None


def a_web(args):
    """Open a URL, or search the web, in the user's browser."""
    q = (args.get("q") or args.get("what") or "").strip()
    if not q:
        return "open what?"
    prof = resolve_profile(args.get("profile")) if args.get("profile") else None
    if args.get("profile") and not prof:
        return ("no Chrome profile matching %r. profiles: %s" % (
            args.get("profile"), ", ".join(p["name"] + (" (school/managed)" if p["managed"] else "")
                                           for p in chrome_profiles()) or "none found"))
    if not str(args.get("new", "")).lower() in ("1", "true", "yes"):
        # also when a profile is named: "play cookie clicker" opened a second
        # copy of the game because the model passed profile=main
        w = _already_open(q)
        if w:
            sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
            return ("already open in %s (%r) -- switched to it instead of opening another tab "
                    "-- VERIFIED" % (_label(w), (w.get("title") or "")[:50]))
    if re.match(r"^(https?://|www\.)", q, re.I):
        url = q if q.lower().startswith("http") else "https://" + q
    elif re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", q, re.I):
        url = "https://" + q
    else:
        # lucky: DuckDuckGo's "\" prefix jumps straight to the first result
        if str(args.get("lucky", "")).lower() in ("1", "true", "yes"):
            q = "\\" + q
        url = "https://duckduckgo.com/?q=" + urlquote(q)
    return open_url(url, prof)
    browser = preferred_browser()
    if browser:
        sh([browser, url], detach=True)
    else:
        sh(["xdg-open", url], detach=True)
    return "opened " + url[:70]


def _has_desktop_entry(name):
    """Does this binary ship a .desktop file? If so it is a GUI app."""
    roots = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications",
        "/usr/local/share/applications",
    ]
    for r in roots:
        if not os.path.isdir(r):
            continue
        for f in os.listdir(r):
            if f.lower() == name.lower() + ".desktop":
                return True
    return False


# ---- everyday actions ------------------------------------------------------
# Dedicated, reliable versions of the things people ask for most. The shell
# action can do all of these too, but a small/cheap model gets a named action
# with plain arguments right far more often than a hand-written command line.

_XDG = {"downloads": "DOWNLOAD", "download": "DOWNLOAD", "documents": "DOCUMENTS",
        "docs": "DOCUMENTS", "desktop": "DESKTOP", "pictures": "PICTURES",
        "photos": "PICTURES", "images": "PICTURES", "videos": "VIDEOS",
        "music": "MUSIC", "templates": "TEMPLATES", "public": "PUBLICSHARE"}
_HOME = os.path.expanduser("~")
_PROTECTED = (os.path.join(_HOME, ".local/share/bite-os"),)


def xdg_dir(name):
    key = _XDG.get((name or "").strip().lower().strip("/"))
    if not key:
        return None
    rc, out = sh(["xdg-user-dir", key])
    return out if rc == 0 and out else None


def resolve_path(p):
    """'downloads/x.zip', '~/a', 'my documents' -> absolute path in $HOME."""
    p = (p or "").strip().strip('"').strip("'")
    p = re.sub(r"^(?:my|the)\s+", "", p, flags=re.I)
    if not p:
        return _HOME
    head, _, rest = p.partition("/")
    base = xdg_dir(head) if head.lower() not in ("~", "home") else _HOME
    if base:
        p = os.path.join(base, rest) if rest else base
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(_HOME, p)
    p = os.path.normpath(p)
    # the model says ~/Downloads, but the real folders are Hebrew (הורדות...)
    if p.startswith(_HOME + os.sep):
        first, _, rest = p[len(_HOME) + 1:].partition(os.sep)
        real = xdg_dir(first)
        eng = os.path.join(_HOME, first)
        # use the real (Hebrew) folder when the English-named one is missing or
        # just an empty leftover (there's an empty ~/Downloads next to הורדות)
        if real and os.path.isdir(real) and os.path.normpath(real) != eng and (
                not os.path.exists(eng) or (os.path.isdir(eng) and not os.listdir(eng))):
            p = os.path.join(real, rest) if rest else real
    return p


def _safe_path(p):
    """Only touch the user's own files, and never T.A.R./rice data."""
    real = os.path.realpath(p)
    if not (real == _HOME or real.startswith(_HOME + os.sep)):
        return "refusing to touch %s -- outside your home folder" % p
    if real == _HOME:
        return "refusing to touch the whole home folder"
    if any(real.startswith(x) for x in _PROTECTED):
        return "refusing -- that's T.A.R./rice data"
    return None


def a_notify(args):
    txt = args.get("text") or args.get("what") or ""
    sh(["notify-send", "-a", "T.A.R.", args.get("title") or "T.A.R.", txt])
    return "notified"


def _secs(args):
    """minutes= / seconds= / hours= / in='10m' -> seconds"""
    total = 0
    for k, mul in (("hours", 3600), ("minutes", 60), ("seconds", 1)):
        try:
            total += int(float(args.get(k) or 0) * mul)
        except ValueError:
            pass
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(h|hr|hours?|m|min|minutes?|s|sec|seconds?)?\s*$",
                 str(args.get("in") or ""), re.I)
    if m:
        n = float(m.group(1)); u = (m.group(2) or "m").lower()
        total += int(n * (3600 if u.startswith("h") else 1 if u.startswith("s") else 60))
    return total


def a_remind(args):
    secs = _secs(args)
    at = str(args.get("at") or "").strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", at, re.I)
    if m and not secs:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) and m.group(3).lower() == "pm" and h < 12:
            h += 12
        if m.group(3) and m.group(3).lower() == "am" and h == 12:
            h = 0
        now = time.localtime()
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, mi, 0, 0, 0, -1))
        if target <= time.time():
            target += 86400                  # already passed today -> tomorrow
        secs = int(target - time.time())
    if secs <= 0:
        return "when? give minutes/seconds/hours"
    txt = args.get("text") or args.get("what") or "time's up"
    rc, out = sh(["systemd-run", "--user", "--collect", "--quiet",
                  "--on-active=%ds" % secs, "--unit=tar-remind-%d" % int(time.time()),
                  "notify-send", "-u", "critical", "-a", "T.A.R.", "T.A.R. reminder", txt])
    if rc:
        return "could not set the reminder: " + out
    when = ("%d min" % (secs // 60)) if secs >= 60 else ("%d s" % secs)
    return "reminder set for %s from now: %s" % (when, txt)


def a_wifi(args):
    what = (args.get("state") or args.get("what") or "status").lower()
    if what in ("on", "off"):
        rc, out = sh(["nmcli", "radio", "wifi", what])
        return "wifi " + what if rc == 0 else out
    if what in ("list", "scan", "networks"):
        rc, out = sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi",
                      "list", "--rescan", "auto"], timeout=20)
        rows = [l for l in out.splitlines() if l.split(":")[1:2] != [""]][:12]
        return "networks (in-use:ssid:signal:security):\n" + "\n".join(rows)
    if what == "connect":
        ssid = args.get("ssid") or args.get("name") or ""
        if not ssid:
            return "connect to which network?"
        argv = ["nmcli", "dev", "wifi", "connect", ssid]
        if args.get("password"):
            argv += ["password", args["password"]]
        rc, out = sh(argv, timeout=40)
        return out[:200]
    rc, out = sh(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"])
    cur = [l for l in out.splitlines() if l.startswith("yes:")]
    radio = sh(["nmcli", "radio", "wifi"])[1]
    return "wifi %s, connected to %s" % (radio, cur[0].split(":")[1] + " (" +
            cur[0].split(":")[2] + "%)" if cur else "nothing")


def a_bluetooth(args):
    what = (args.get("state") or args.get("what") or "status").lower()
    if what in ("on", "off"):
        if what == "on":
            sh(["rfkill", "unblock", "bluetooth"])
        rc, out = sh(["bluetoothctl", "power", what])
        return "bluetooth " + what if rc == 0 else out
    if what in ("devices", "list", "paired"):
        rc, out = sh(["bluetoothctl", "devices", "Paired"])
        rc2, con = sh(["bluetoothctl", "devices", "Connected"])
        return "paired:\n%s\nconnected:\n%s" % (out or "(none)", con or "(none)")
    if what in ("connect", "disconnect"):
        name = (args.get("device") or args.get("name") or "").lower()
        rc, out = sh(["bluetoothctl", "devices", "Paired"])
        hit = [l.split(" ", 2) for l in out.splitlines()
               if name and name in l.lower()]
        if not hit:
            return "no paired device matching %r" % name
        rc, out = sh(["bluetoothctl", what, hit[0][1]], timeout=25)
        return "%sed %s" % (what, hit[0][2]) if rc == 0 else out[:200]
    rc, out = sh(["bluetoothctl", "show"])
    on = "Powered: yes" in out
    con = sh(["bluetoothctl", "devices", "Connected"])[1]
    return "bluetooth %s; connected: %s" % ("on" if on else "off", con or "nothing")


def a_battery(_):
    rc, devs = sh(["upower", "-e"])
    bat = next((d for d in devs.splitlines() if "BAT" in d), None)
    if not bat:
        return "no battery found"
    rc, out = sh(["upower", "-i", bat])
    keep = [l.strip() for l in out.splitlines()
            if l.strip().split(":")[0] in ("state", "percentage", "time to empty",
                                           "time to full", "energy-rate")]
    return "; ".join(keep)


# -- research ------------------------------------------------------------------
# "look up X": search, read the top pages, answer briefly WITH the links. One
# Gemini call per question (search is DuckDuckGo's key-less HTML page), and
# answers are cached for a few hours so a repeat costs nothing.
REPLY_TEXT = None                  # set by an action whose result IS the reply
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _http_get(url, timeout=5, limit=1_500_000):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en,he;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read(limit)
    cs = re.search(r"charset=([\w-]+)", ctype)
    return ctype, raw.decode(cs.group(1) if cs else "utf-8", errors="replace")


def web_search(q, n=5):
    """[(title, url)] from DuckDuckGo's HTML endpoint (no key, no JS)."""
    import html as _html
    import urllib.parse
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(q)
    try:
        _ct, page = _http_get(url, timeout=6)
    except Exception:
        _ct, page = _http_get(url, timeout=6)    # it stalls now and then; once more
    out = []
    for href, title in re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href = _html.unescape(href)
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if not u.startswith("http") or "duckduckgo.com/y.js" in u or u in [x[1] for x in out]:
            continue                    # ads and duplicates
        out.append((_html.unescape(re.sub(r"<[^>]+>", "", title)).strip(), u))
        if len(out) >= n:
            break
    return out


def page_text(url, limit=7000):
    """Readable text of a web page (scripts, menus and footers dropped)."""
    from html.parser import HTMLParser
    ctype, page = _http_get(url)
    if "html" not in ctype and "text" not in ctype:
        return ""

    class _P(HTMLParser):
        skip_tags = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"}

        def __init__(self):
            HTMLParser.__init__(self)
            self.depth, self.bits = 0, []

        def handle_starttag(self, tag, _a):
            if tag in self.skip_tags:
                self.depth += 1

        def handle_endtag(self, tag):
            if tag in self.skip_tags and self.depth:
                self.depth -= 1

        def handle_data(self, d):
            if not self.depth and d.strip():
                self.bits.append(d.strip())

    p = _P()
    try:
        p.feed(page)
    except Exception:
        pass
    text = re.sub(r"\s+", " ", " ".join(p.bits))     # short bits too: table cells hold the facts
    return text[:limit]


def a_research(args):
    """Search the web, read the top pages, answer in a few sentences + links."""
    global REPLY_TEXT
    q = (args.get("q") or args.get("what") or "").strip().rstrip("?")
    if not q:
        return "look up what?"
    cache_p = os.path.join(DATA, "research-cache.json")
    try:
        with open(cache_p, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    key_q = re.sub(r"\s+", " ", q.lower())
    hit = cache.get(key_q)
    if hit and time.time() - hit.get("at", 0) < 6 * 3600 and not args.get("fresh"):
        REPLY_TEXT = hit["reply"]
        return "researched %r (cached from earlier) -- VERIFIED: answer + %d sources shown" % (
            q, hit.get("n", 0))
    try:
        results = web_search(q)
    except Exception:
        results = []
    if not results:
        # DuckDuckGo stalled or found nothing: Gemini's own Google search
        return _research_grounded(q, cache, cache_p, key_q)
    emit("info", v="reading %d pages about %s…" % (min(3, len(results)), q[:60]))
    from concurrent.futures import ThreadPoolExecutor, wait

    def grab(r):
        try:
            return r, page_text(r[1])
        except Exception:
            return r, ""                # 403 / timeout / not html
    ex = ThreadPoolExecutor(5)
    futs = [ex.submit(grab, r) for r in results[:5]]
    wait(futs, timeout=6)               # slow sites don't hold the answer up
    ex.shutdown(wait=False, cancel_futures=True)
    read = [f.result() for f in futs if f.done() and len(f.result()[1]) > 200][:3]
    if not read:
        read = [(r, "") for r in results[:3]]   # pages blocked us: titles only
    try:
        import tar_cloud as C
    except ImportError:
        return "NOT DONE: research needs the cloud backend"
    model = cfg().get("cloud_model") or "gemini-flash-lite-latest"
    key = C.api_key(C.provider_of(model)) or C.api_key("google")
    if not key:
        return "NOT DONE: research needs a cloud key -- type /key"
    if not C.api_key(C.provider_of(model)):
        model = "gemini-flash-lite-latest"
    src = "\n\n".join("[%d] %s (%s)\n%s" % (i + 1, t, u, txt or "(page could not be read)")
                      for i, ((t, u), txt) in enumerate(read))
    prompt = _research_prompt(q) + "\n\nSources:\n" + src
    try:
        r = C.llm_call(key, model, {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                       "generationConfig": {"maxOutputTokens": 3000, "temperature": 0.2}})
    except Exception as e:
        return "NOT DONE: couldn't summarise (%s). Top links: %s" % (
            e, " ".join(u for (_t, u), _x in read))
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    answer = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not answer:
        return "NOT DONE: the model returned nothing. Top links: " + " ".join(u for (_t, u), _x in read)
    return _research_done(q, answer, [tu for tu, _x in read], cache, cache_p, key_q)


def _research_prompt(q):
    return ("Today is %s.\nQuestion: %s\n\nAnswer in 2-5 short sentences, in the language of "
            "the question, using ONLY the sources. Lead with the specific fact (number, name, "
            "date, version). Cite every claim like [1]. If sources disagree, say which says "
            "what. If they don't answer it, say so plainly. No intro like 'based on the "
            "sources', no markdown." % (time.strftime("%d %B %Y"), q))


def _research_done(q, answer, sources, cache, cache_p, key_q):
    global REPLY_TEXT
    import urllib.parse
    # unquoted so Hebrew URLs are readable; spaces stay escaped so the link holds
    reply = answer + "\n\nSources:\n" + "\n".join(
        "[%d] %s\n    %s" % (i + 1, t[:70], urllib.parse.unquote(u).replace(" ", "%20"))
        for i, (t, u) in enumerate(sources))
    REPLY_TEXT = reply
    cache[key_q] = {"at": time.time(), "reply": reply, "n": len(sources)}
    try:
        cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("at", 0))[-60:])
        with open(cache_p, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return "researched %r, read %d pages -- VERIFIED: answer shown with sources:\n%s" % (
        q, len(sources), reply)


def _research_grounded(q, cache, cache_p, key_q):
    """Fallback: one Gemini call with Google Search grounding."""
    try:
        import tar_cloud as C
    except ImportError:
        return "NOT DONE: web search failed and research needs the cloud backend"
    key = C.api_key("google")
    if not key:
        return "NOT DONE: web search failed (no internet?) and there's no Gemini key"
    emit("info", v="searching with Google for %s…" % q[:60])
    try:
        gm = cfg().get("cloud_model") or ""
        r = C.gemini_call(key, gm if gm.startswith("gemini") else "gemini-flash-lite-latest", {
            "contents": [{"role": "user", "parts": [{"text": _research_prompt(q).replace(
                "using ONLY the sources", "searching the web first")}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 3000, "temperature": 0.2}})
    except Exception as e:
        return "NOT DONE: web search failed and so did Google search (%s)" % e
    cand = (r.get("candidates") or [{}])[0]
    answer = "".join(p.get("text", "") for p in ((cand.get("content") or {}).get("parts") or [])
                     if not p.get("thought")).strip()
    chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
    sources = [((c.get("web") or {}).get("title") or "source", (c.get("web") or {}).get("uri"))
               for c in chunks if (c.get("web") or {}).get("uri")][:5]
    if not answer or not sources:
        return "NOT DONE: couldn't find sources for %r -- try rewording it" % q
    return _research_done(q, answer, sources, cache, cache_p, key_q)


# -- file safety check -----------------------------------------------------------
_USB_WORDS = re.compile(r"^(?:usb|usbs|usb drive|usb stick|flash ?drive|thumb ?drive|stick|"
                        r"drive|disk on key|disk-on-key|disk|external drive|it'?s files)$", re.I)


def _usb_mounts(mount=True):
    """Mount points of attached removable drives; mounts them READ-ONLY if needed."""
    rc, out = sh(["lsblk", "-J", "-o", "NAME,RM,TRAN,TYPE,MOUNTPOINT,FSTYPE"], timeout=15)
    try:
        tree = json.loads(out)
    except ValueError:
        return []
    found = []

    def walk(nodes, usb=False):
        for n in nodes:
            u = usb or n.get("rm") or n.get("tran") == "usb"
            if u and n.get("type") == "part" or (u and n.get("type") == "disk" and n.get("fstype")):
                if n.get("mountpoint"):
                    found.append(n["mountpoint"])
                elif mount and n.get("fstype") and shutil.which("udisksctl"):
                    rc2, o2 = sh(["udisksctl", "mount", "-b", "/dev/" + n["name"], "-o", "ro"], timeout=30)
                    m = re.search(r" at (/\S+)", o2 or "")
                    if rc2 == 0 and m:
                        found.append(m.group(1).rstrip("."))
            walk(n.get("children") or [], u)
    walk(tree.get("blockdevices") or [])
    return found


def _find_named(name):
    """A bare file name -> where it is (USB drives first, then Downloads, then home)."""
    roots = _usb_mounts(mount=False) + [xdg_dir("downloads") or "", xdg_dir("desktop") or "", _HOME]
    for r in [x for x in roots if x and os.path.isdir(x)]:
        rc, out = sh(["find", r, "-maxdepth", "5", "-iname", name, "-not", "-path", "*/.*",
                      "-print", "-quit"], timeout=20)
        if out.strip():
            return out.strip().splitlines()[0]
    return None


def a_scan(args):
    """Static malware check of a file/folder/USB: what it's BUILT to do, never its name."""
    global REPLY_TEXT
    what = (args.get("path") or args.get("what") or args.get("q") or "usb").strip().rstrip("?")
    what = re.sub(r"^(?:the|my|this|that)\s+", "", what, flags=re.I)
    if what.lower() in ("it", "this", "that", "them", "these", "those", "this file", "that file"):
        return "no such file named -- which file or folder? (give its name or path)"
    if _USB_WORDS.match(what):
        roots = _usb_mounts()
        if not roots:
            return "no such drive: no USB drive is attached (or it couldn't be mounted)"
    else:
        p = resolve_path(what)
        if not os.path.exists(p):
            p = _find_named(os.path.basename(what)) or p
        if not os.path.exists(p):
            return "no such file or folder: %s" % what
        roots = [p]
    try:
        import tar_scan as S
    except ImportError:
        return "could not load the scanner (tar_scan.py missing)"
    try:
        import tar_cloud as C
        vt_key = C.api_key("virustotal")
    except Exception:
        vt_key = ""
    emit("info", v="checking %s… (static analysis, nothing gets run)" % ", ".join(
        os.path.basename(r) or r for r in roots))
    lines, n, flagged, clean, clam = [], 0, 0, [], False
    mark = {"likely malicious": "⛔", "suspicious": "⚠", "worth a look": "•", "test file": "🧪"}
    for root in roots:
        res = S.scan(root, vt_key)
        clam = clam or res["clamav"]
        n += len(res["files"])
        for r in res["files"]:
            vt = r.get("vt") or {}
            vt_txt = ("VirusTotal: %d/%d engines flag it%s" % (
                vt["malicious"], vt["total"], (" (%s)" % vt["label"]) if vt.get("label") else "")
                if vt.get("known") else "VirusTotal: never seen this file" if "known" in vt else "")
            if r["level"] == "nothing dangerous found" and not vt.get("malicious"):
                clean.append(r["name"])
                continue
            flagged += r["level"] in ("likely malicious", "suspicious")
            lines.append("%s %s -- %s (%s)" % (mark.get(r["level"], "•"), r["name"],
                                                r["level"].upper(), r.get("type", "")))
            for t in r.get("techniques", []):
                lines.append("   %s%s: %s%s" % (t["tactic"], (" · " + t["id"]) if t["id"] else "",
                                                t["what"], ("  [" + t["evidence"] + "]") if t["evidence"] else ""))
            for why, ev in r.get("findings", []):
                lines.append("   %s%s" % (why, ("  [" + ev + "]") if ev else ""))
            if vt_txt:
                lines.append("   " + vt_txt)
            if r.get("sha256") and r["level"] in ("likely malicious", "suspicious"):
                lines.append("   https://www.virustotal.com/gui/file/" + r["sha256"])
    if clean:
        lines.append("✓ nothing dangerous found in %d file%s: %s" % (
            len(clean), "" if len(clean) == 1 else "s",
            ", ".join(clean[:12]) + (" …" if len(clean) > 12 else "")))
    head = ("Checked %d file%s in %s -- static analysis (nothing was run). Verdicts come from "
            "what each file is built to do (imports, commands, ATT&CK techniques), not its name."
            % (n, "" if n == 1 else "s", ", ".join(roots)))
    tail = []
    if not vt_key:
        tail.append("Tip: a free VirusTotal key (/key <key>) adds a 70+ engine hash check -- "
                    "files are never uploaded.")
    if not clam:
        tail.append("ClamAV isn't installed, so there was no signature scan.")
    REPLY_TEXT = "\n".join([head, ""] + lines + ([""] + tail if tail else []))
    return ("scanned %d files: %d flagged -- VERIFIED: report shown\n%s" % (n, flagged, REPLY_TEXT))


def a_weather(args):
    place = (args.get("place") or args.get("where") or "").strip()
    rc, out = sh(["curl", "-s", "--max-time", "10",
                  "https://wttr.in/%s?format=%%l:+%%C+%%t+(feels+%%f),+wind+%%w,+humidity+%%h"
                  % urlquote(place)], timeout=15)
    return out if rc == 0 and out and "Unknown" not in out else "couldn't get the weather"


def a_datetime(_):
    return time.strftime("%A %d %B %Y, %H:%M")


def a_calc(args):
    expr = args.get("expr") or args.get("what") or ""
    if not expr:
        return "calculate what?"
    # qalc reads "15% of 80" as a modulo/byte expression
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", expr, flags=re.I)
    rc, out = sh(["qalc", "-t", expr]) if shutil.which("qalc") else \
        sh(["bash", "-c", "echo %s | bc -l" % shlex.quote(expr)])
    return out or "couldn't calculate that"


def a_files(args):
    p = resolve_path(args.get("path") or args.get("where") or "")
    if not os.path.isdir(p):
        return "no such folder: " + p
    try:
        items = sorted(os.listdir(p), key=str.lower)
    except OSError as e:
        return str(e)
    if not args.get("hidden"):
        items = [i for i in items if not i.startswith(".")]
    rows = [(i + "/" if os.path.isdir(os.path.join(p, i)) else i) for i in items[:80]]
    more = "" if len(items) <= 80 else "\n...and %d more" % (len(items) - 80)
    return "%s (%d items):\n%s%s" % (p, len(items), "\n".join(rows), more)


def a_find(args):
    q = args.get("q") or args.get("name") or args.get("what") or ""
    if not q:
        return "find what?"
    where = resolve_path(args.get("where") or "")
    tool = "fd" if shutil.which("fd") else None
    if tool:
        rc, out = sh(["fd", "-i", "--max-results", "30", q, where], timeout=20)
    else:
        rc, out = sh(["find", where, "-iname", "*%s*" % q, "-not", "-path", "*/.*"],
                     timeout=20)
        out = "\n".join(out.splitlines()[:30])
    return out or "nothing matching %r under %s" % (q, where)


def a_move(args):
    src, dst = resolve_path(args.get("src")), resolve_path(args.get("dst"))
    for p in (src, dst):
        err = _safe_path(p)
        if err:
            return err
    if not os.path.exists(src):
        return "no such file: " + src
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.exists(dst):
        return "refusing to overwrite " + dst
    shutil.move(src, dst)
    return "moved to " + dst


def a_copy(args):
    src, dst = resolve_path(args.get("src")), resolve_path(args.get("dst"))
    err = _safe_path(dst)
    if err:
        return err
    if not os.path.exists(src):
        return "no such file: " + src
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.exists(dst):
        return "refusing to overwrite " + dst
    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
    return "copied to " + dst


def a_rename(args):
    src = resolve_path(args.get("path") or args.get("src"))
    new = (args.get("to") or args.get("name") or "").strip()
    if not new or "/" in new:
        return "rename to what? (just a name)"
    return a_move({"src": src, "dst": os.path.join(os.path.dirname(src), new)})


def a_mkdir(args):
    p = resolve_path(args.get("path") or args.get("what"))
    err = _safe_path(p)
    if err:
        return err
    try:
        os.makedirs(p, exist_ok=True)
    except OSError as e:
        return "couldn't create it: %s" % e.strerror
    return "created " + p


def a_trash(args):
    p = resolve_path(args.get("path") or args.get("what"))
    err = _safe_path(p)
    if err:
        return err
    if not os.path.lexists(p):
        return "no such file: " + p
    if os.path.isdir(p) and not os.path.islink(p):
        n = sum(len(f) for _, _, f in os.walk(p))
        stop = needs_confirm("trash", args, "move the folder %s (%d files) to the trash" % (p, n))
        if stop:
            return stop
    rc, out = sh(["trash-put", p]) if shutil.which("trash-put") else sh(["gio", "trash", p])
    return ("moved %s to the trash (restorable)" % p) if rc == 0 else out


def a_restore(args):
    name = args.get("name") or args.get("what") or ""
    rc, out = sh(["trash-list"])
    hits = [l for l in out.splitlines() if name and name in l]
    if not hits:
        return "nothing in the trash matching %r" % name
    path = hits[-1].split(" ", 2)[2]
    rc, out = sh(["bash", "-c", "echo 0 | trash-restore %s" % shlex.quote(path)])
    return "restored " + path if os.path.exists(path) else "restore failed: " + out[:150]


def a_folder(args):
    p = resolve_path(args.get("path") or args.get("what") or "")
    if not os.path.exists(p):
        return "no such folder: " + p
    sh(["xdg-open", p], detach=True)
    return "opened " + p


def a_download(args):
    url = args.get("url") or args.get("what") or ""
    if not re.match(r"^https?://", url):
        return "need an http(s) url"
    where = resolve_path(args.get("where") or "downloads")
    err = _safe_path(where)
    if err:
        return err
    rc, out = sh(["curl", "-sSL", "--max-time", "120", "-OJ", "--output-dir", where, url],
                 timeout=130)
    return ("downloaded into " + where) if rc == 0 else "download failed: " + out[:150]


_NEVER_KILL = {"hyprland", "qs", "quickshell", "sddm", "systemd", "pipewire",
               "wireplumber", "networkmanager", "dbus-daemon", "xdg-desktop-portal",
               "xdg-desktop-portal-hyprland", "python3", "bash", "fish", "sh",
               "claude", "kitty", "init"}


def a_kill(args):
    name = (args.get("name") or args.get("what") or "").strip()
    if not name:
        return "kill what?"
    if name.lower() in _NEVER_KILL or name.lower() in ("me", "myself", "yourself",
                                                       "you", "it", "tar", "t.a.r."):
        return "refusing to kill %s" % name
    stop = needs_confirm("kill", args, "force-quit %s (unsaved work in it is lost)" % name)
    if stop:
        return stop
    # windows first: closing by window is gentler than a signal
    w = target_window({"what": name})
    if w:
        sh(["hyprctl", "dispatch", "closewindow", "address:" + w["address"]])
        return "closed " + _label(w)
    rc, _ = sh(["pkill", "-x", name])
    if rc != 0:
        rc, _ = sh(["pkill", "-i", "-f", "^[^ ]*%s" % re.escape(name)])
    return ("stopped %s" % name) if rc == 0 else "no running process called %r" % name


def a_processes(args):
    by = "-%mem" if "mem" in str(args.get("by", "")).lower() else "-%cpu"
    rc, out = sh(["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=" + by])
    return "\n".join(out.splitlines()[:12])


def a_power(args):
    what = (args.get("action") or args.get("what") or "").lower()
    cmds = {"suspend": ["systemctl", "suspend"], "sleep": ["systemctl", "suspend"],
            "lock": ["loginctl", "lock-session"],
            "logout": ["hyprctl", "dispatch", "exit"],
            "reboot": ["systemctl", "reboot"], "restart": ["systemctl", "reboot"],
            "shutdown": ["systemctl", "poweroff"], "poweroff": ["systemctl", "poweroff"]}
    if what not in cmds:
        return "power what? suspend|lock|logout|reboot|shutdown"
    if what in ("reboot", "restart", "shutdown", "poweroff", "logout"):
        stop = needs_confirm("power", args, what + " the computer")
        if stop:
            return stop
        sh(["notify-send", "-u", "critical", "-a", "T.A.R.", "T.A.R.",
            "%s in 5 seconds" % what])
        sh(["bash", "-c", "sleep 5; " + " ".join(cmds[what])], detach=True)
        return "%s in 5 seconds" % what
    sh(cmds[what], detach=True)
    return what


def a_dnd(args):
    what = (args.get("state") or args.get("what") or "toggle").lower()
    if not shutil.which("swaync-client"):
        return "no notification daemon control found"
    flag = {"on": "-dn", "off": "-df"}.get(what, "-d")
    sh(["swaync-client", flag])
    return "do not disturb " + {"on": "on", "off": "off"}.get(what, "toggled")


def a_nightlight(args):
    what = (args.get("state") or args.get("what") or "on").lower()
    sh(["pkill", "-x", "hyprsunset"])
    if what == "off":
        return "night light off"
    temp = str(args.get("temp") or 4000)
    sh(["hyprsunset", "-t", temp], detach=True)
    return "night light on (%sK)" % temp


def a_colorpick(_):
    sh(["hyprpicker", "-a"], detach=True)
    return "click anywhere to pick a colour -- it's copied to the clipboard"


def a_record(args):
    what = (args.get("state") or args.get("what") or "start").lower()
    if what == "stop":
        rc, _ = sh(["pkill", "-INT", "-x", "wf-recorder"])
        return "recording stopped and saved" if rc == 0 else "nothing was recording"
    vids = xdg_dir("videos") or _HOME
    out = os.path.join(vids, time.strftime("tar-rec-%Y%m%d-%H%M%S.mp4"))
    sh(["wf-recorder", "-f", out], detach=True)
    return "recording the screen to " + out + " -- say stop recording to finish"


_SECRET_RE = re.compile(r"\b(AIza[\w-]{20,}|AQ\.[\w-]{20,}|sk-[\w-]{20,}|gh[pousr]_\w{20,}|"
                        r"xox[abp]-[\w-]+|eyJ[\w-]{20,}\.[\w-]+|[A-Za-z0-9_\-]{40,})")


def a_cliphist(_):
    """Recent clipboard entries -- with anything key/token-shaped masked, so
    a copied password or API key never gets sent to the cloud model."""
    rc, out = sh(["cliphist", "list"])
    rows = [_SECRET_RE.sub("[hidden secret]", l.split("\t", 1)[-1][:80])
            for l in out.splitlines()[:10]]
    return "\n".join(rows) or "empty"


# ---- background tasks: "keep doing X until I say stop" ----------------------
LOOP = os.path.join(DATA, "loop.json")


def loop_running():
    try:
        with open(LOOP, encoding="utf-8") as f:
            d = json.load(f)
        os.kill(int(d["pid"]), 0)           # still alive?
        return d
    except (OSError, ValueError, KeyError, TypeError):
        return None


def loop_update(**fields):
    """Merge fields into loop.json (the tasks panel reads it)."""
    try:
        with open(LOOP, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    d.update(fields)
    tmp = LOOP + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, LOOP)
    except OSError:
        pass
    return d


def a_pause_task(_):
    d = loop_running()
    if not d:
        return "nothing is running in the background"
    if d.get("paused"):
        return "already paused"
    loop_update(paused=True, paused_at=time.time(), status="paused")
    return "paused %r -- say resume to continue -- VERIFIED" % d.get("task", "")[:60]


def a_resume_task(_):
    d = loop_running()
    if not d:
        return "nothing is running in the background"
    if not d.get("paused"):
        return "it isn't paused"
    # the paused time doesn't count against the time limit
    extra = time.time() - float(d.get("paused_at") or time.time())
    loop_update(paused=False, paused_at=None, status="running",
                until=float(d.get("until") or time.time()) + extra)
    return "resumed %r -- VERIFIED" % d.get("task", "")[:60]


def a_keep_doing(args):
    task = (args.get("task") or args.get("what") or "").strip()
    if not task:
        return "keep doing what?"
    old = loop_running()
    if old:
        a_stop_task({})
    autoclick = (args.get("autoclick") or "").strip()
    rate = max(1, min(15, _int(args.get("rate"), 8)))
    # with an autoclicker doing the repetitive part, the AI only needs to
    # check in now and then to make decisions
    every = max(3, min(300, _int(args.get("every"), 15 if autoclick else 3)))
    minutes = max(1, min(120, _int(args.get("minutes"), 20)))
    win = args.get("_win") or target_window({"what": args.get("app") or args.get("window")} if (
        args.get("app") or args.get("window")) else {})
    if win and not window_on_screen(win)[0]:
        sh(["hyprctl", "dispatch", "focuswindow", "address:" + win["address"]])
        time.sleep(0.3)                 # starting a task = show its window
    here = os.path.dirname(os.path.abspath(__file__))
    log = open(os.path.join(DATA, "loop.log"), "a")
    p = subprocess.Popen([sys.executable, os.path.join(here, "tar_cloud.py"), "loop",
                          "--task", task, "--every", str(every), "--minutes", str(minutes),
                          "--autoclick", autoclick, "--rate", str(rate)],
                         stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, TAR_DATA=DATA))
    with open(LOOP, "w", encoding="utf-8") as f:
        json.dump({"pid": p.pid, "task": task, "started": time.time(),
                   "until": time.time() + minutes * 60, "every": every,
                   "round": 0, "status": "starting", "last": "", "paused": False, "feed": [],
                   "autoclick": autoclick, "rate": rate, "clicks": 0,
                   "win": (win or {}).get("address", ""),
                   "win_title": (win or {}).get("title", ""), "beat": time.time()}, f)
    sh(["notify-send", "-a", "T.A.R.", "T.A.R. is working", task[:120] + "  (say stop to end)"])
    return ("started in the background: %r%s -- an AI round every %ds for up to %d min. "
            "Say 'stop' to end it. -- VERIFIED" % (
                task[:80], (" + autoclicking %r %d/s between rounds" % (autoclick, rate))
                if autoclick else "", every, minutes))


def window_on_screen(w):
    """(True, "") if window w is on a workspace that's showing right now."""
    if not w:
        return False, "the window is gone"
    rc, out = sh(["hyprctl", "-j", "monitors"])
    try:
        shown = {m["activeWorkspace"]["id"] for m in json.loads(out)}
        shown |= {m["specialWorkspace"]["id"] for m in json.loads(out)
                  if (m.get("specialWorkspace") or {}).get("id")}
    except (ValueError, KeyError, TypeError):
        return True, ""
    ws = (w.get("workspace") or {})
    if ws.get("id") in shown:
        return True, ""
    return False, "it's on workspace %s" % (ws.get("name") or ws.get("id"))


def task_window_ok():
    """Is the background task's window still there AND on screen?"""
    d = loop_running() or {}
    addr = d.get("win")
    if not addr:
        return True, ""                 # task wasn't tied to a window
    w = next((c for c in _clients() if c.get("address") == addr), None)
    if not w:
        return False, "its window was closed"
    return window_on_screen(w)


def cursor_pos():
    rc, out = sh(["hyprctl", "cursorpos"], timeout=3)
    try:
        x, y = out.replace(" ", "").split(",")
        return int(float(x)), int(float(y))
    except ValueError:
        return None


# Web games T.A.R. knows how to play without asking the model what to do first.
GAMES = {
    "cookie clicker": {"url": "https://orteil.dashnet.org/cookieclicker/",
                       "title": "cookie", "autoclick": "the big cookie", "every": 15,
                       "task": "Play Cookie Clicker: buy the best building or upgrade you can "
                               "afford right now (the lit-up ones on the right). Close popups."},
}
_GAME_ALIASES = {"cookieclicker": "cookie clicker", "cookie clicker game": "cookie clicker",
                 "cookies": "cookie clicker", "the cookie game": "cookie clicker"}


def _game_window(g):
    for w in _clients():
        if _is_browser(w) and GAMES[g]["title"] in (w.get("title") or "").lower():
            return w
    return None


def a_play_game(args):
    """Open (or find) a known web game and start playing it in the background."""
    name = (args.get("game") or args.get("what") or "").strip().lower()
    name = _GAME_ALIASES.get(name, name)
    if name in ("", "it", "this", "that", "the game"):
        open_games = [g for g in GAMES if _game_window(g)]
        if len(open_games) != 1:
            return "don't know how to play that -- which game?"
        name = open_games[0]
    if name not in GAMES:
        return "don't know how to play %r on my own yet" % name
    g = GAMES[name]
    w = _game_window(name)
    if not w:
        open_url(g["url"], None)
        for _ in range(20):             # wait for the page to load
            time.sleep(0.5)
            w = _game_window(name)
            if w:
                break
        if not w:
            return "could not open %s -- the page didn't load" % name
    sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
    time.sleep(0.4)
    minutes = _int(args.get("minutes"), 20)
    out = a_keep_doing({"task": g["task"], "autoclick": g.get("autoclick", ""),
                        "every": g.get("every", 15), "minutes": minutes, "_win": w})
    return "playing %s in %r for %d min (autoclicking %s, buying every %ds) -- %s" % (
        name, (w.get("title") or "")[:40], minutes, g.get("autoclick") or "nothing",
        g.get("every", 15), "VERIFIED" if "started" in out else out)


# -- screen effects: real overlays (tar/fx/<name>.qml + AI-written ones) ------------
# Built-ins live in tar/fx/, effects the model writes (make_fx) in
# $TAR_DATA/fx-custom/. Every launch is checked: the overlay host prints
# FX_READY / FX_FAIL and QML errors land in fx.log, which goes back to the
# model so it fixes its code instead of claiming it worked.
FX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fx")
FX_HOST = os.path.join(FX_DIR, "host.qml")
FX_CUSTOM = os.path.join(DATA, "fx-custom")
FX_LOG = os.path.join(DATA, "fx.log")
_FX_ALIASES = {"matrix": "matrix", "matrix rain": "matrix", "code rain": "matrix", "the matrix": "matrix",
               "snow": "snow", "snowfall": "snow", "it snow": "snow", "snowing": "snow",
               "glitch": "glitch", "glitches": "glitch", "screen glitch": "glitch", "glitchy": "glitch",
               "shake": "shake", "screen shake": "shake", "earthquake": "shake", "shaking": "shake",
               # effects T.A.R. wrote itself, now built in
               "black hole": "black_hole_orbs", "blackhole": "black_hole_orbs",
               "black hole orbs": "black_hole_orbs", "fireworks": "fireworks", "firework": "fireworks",
               "orbs": "four_orbs", "four orbs": "four_orbs", "spinning orb": "spinning_orb",
               "spin": "spinning_orb", "orb": "spinning_orb"}
_FX_INSTANT = {"shake": 2}           # built from a screenshot: no fade, short


def _fx_file(name):
    for d, ext in ((FX_DIR, ".qml"), (FX_CUSTOM, ".qml")):
        p = os.path.join(d, name + ext)
        if name != "host" and os.path.exists(p):
            return p
    return None


def fx_list():
    names = [f[:-4] for f in os.listdir(FX_DIR) if f.endswith(".qml") and f != "host.qml"]
    if os.path.isdir(FX_CUSTOM):
        names += [f[:-4] for f in os.listdir(FX_CUSTOM) if f.endswith(".qml")]
    return sorted(set(names))


def _fx_pids():
    """Every running effect host (argv match, not pkill -f on a pattern)."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            argv = open("/proc/%s/cmdline" % d, "rb").read().split(b"\0")
        except OSError:
            continue
        if FX_HOST.encode() in argv:
            out.append(int(d))
    return out


def fx_running():
    return bool(_fx_pids())


def a_stop_fx(_):
    pids = _fx_pids()
    if not pids:
        return "no screen effect is running"
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    time.sleep(0.4)
    left = _fx_pids()
    for pid in left:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    return "stopped the screen effect -- VERIFIED: no effect overlay left"


def _fx_shots():
    """A screenshot of every monitor, for effects that distort the screen."""
    rc, out = sh(["hyprctl", "-j", "monitors"])
    try:
        mons = [m["name"] for m in json.loads(out)]
    except (ValueError, KeyError, TypeError):
        mons = []
    for m in mons:
        sh(["grim", "-o", m, os.path.join(DATA, "fx-%s.png" % m)], timeout=10)


def _fx_launch(src, secs, fade=600, shot=False):
    """Start the overlay host; wait for FX_READY. -> (ok, message)"""
    a_stop_fx({})                        # one effect at a time
    qs = shutil.which("qs") or shutil.which("quickshell")
    if not qs:
        return False, "quickshell isn't installed"
    if shot:
        _fx_shots()
    log = open(FX_LOG, "w")
    p = subprocess.Popen([qs, "-p", FX_HOST], start_new_session=True, stdout=log, stderr=log,
                         env=dict(os.environ, TAR_FX=src, TAR_FX_SECS=str(secs),
                                  TAR_FX_FADE=str(fade), TAR_FX_SHOTS=DATA,
                                  QT_FORCE_STDERR_LOGGING="1"))
    for _ in range(24):
        time.sleep(0.25)
        try:
            text = re.sub(r"\x1b\[[0-9;]*m", "", open(FX_LOG, errors="replace").read())
        except OSError:
            text = ""
        text = text.replace("file://" + FX_CUSTOM + "/", "")     # short file names for the model
        errs = [l for l in text.splitlines() if re.search(
            r"ERROR|Error:|TypeError|ReferenceError|SyntaxError|is not a type|not installed|"
            r"Cannot assign|Invalid property|Expected token|Unexpected token", l)]
        if "FX_FAIL" in text or p.poll() is not None or (errs and "FX_READY" not in text):
            a_stop_fx({})
            return False, "\n".join(e.strip()[-240:] for e in errs[:8]) or "it exited right away"
        if "FX_READY" in text and "tar-fx" in sh(["hyprctl", "layers"])[1]:
            # runtime errors right after start (bad bindings) count too
            time.sleep(0.6)
            text = re.sub(r"\x1b\[[0-9;]*m", "", open(FX_LOG, errors="replace").read())
            text = text.replace("file://" + FX_CUSTOM + "/", "")
            errs = [l for l in text.splitlines() if re.search(
                r"TypeError|ReferenceError|Error:|Cannot assign", l)]
            if errs:
                a_stop_fx({})
                return False, "\n".join(e.strip()[-240:] for e in errs[:8])
            return True, "overlay is up"
    a_stop_fx({})
    return False, "the overlay never appeared"


def a_screen_fx(args):
    """Show a full-screen effect (built-in or one T.A.R. made earlier)."""
    raw = (args.get("effect") or args.get("what") or "").strip().lower()
    raw = re.sub(r"^(?:a|an|the|some)\s+|\s+(?:effect|on (?:my|the) screen|on screen)$", "", raw).strip()
    name = _FX_ALIASES.get(raw, re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-"))
    path = _fx_file(name)
    if not path:
        return ("don't know how to show %r yet -- effects I have: %s. To make a new one, "
                "write it with make_fx" % (raw, ", ".join(fx_list())))
    secs = _FX_INSTANT.get(name) or max(3, min(600, _int(args.get("seconds"), 30)))
    code = open(path, errors="replace").read()
    ok, msg = _fx_launch(path, secs, fade=0 if name in _FX_INSTANT else 600,
                         shot="property string shot" in code)
    if not ok:
        return "NOT DONE: the %s effect failed -- %s" % (name, msg)
    return ("showing %s on the screen for %ds (say 'stop' to end it) -- VERIFIED: %s"
            % (name, secs, msg))


_FX_OK_IMPORTS = {"QtQuick", "QtQuick.Shapes", "QtQuick.Particles", "QtQuick.Effects",
                  "QtQuick.Window"}
_FX_BANNED = re.compile(r"XMLHttpRequest|openUrlExternally|createQmlObject|Quickshell|"
                        r"\bProcess\b|FileView|Qt\.labs|LocalStorage|https?://|file://|"
                        r"\bLoader\b|\bsource\s*:\s*[\"'](?!file:)", re.I)


def a_make_fx(args):
    """The model writes a visual effect (QML) -> checked, run, errors reported back."""
    name = re.sub(r"[^a-z0-9_-]+", "-", (args.get("name") or "custom").lower()).strip("-")[:40] or "custom"
    code = args.get("qml") or args.get("code") or ""
    if not code.strip():
        return "make_fx needs qml= (a complete QML file) and name="
    if len(code) > 24000:
        return "NOT DONE: effect code too long (keep it under 24 KB)"
    if name in [f[:-4] for f in os.listdir(FX_DIR)]:
        name += "-custom"
    # visuals only: plain QtQuick modules, nothing that reaches files/network/programs
    bad_imp = [m for m in re.findall(r"^\s*import\s+([\w.]+)", code, re.M) if m not in _FX_OK_IMPORTS]
    if bad_imp:
        return ("NOT DONE: effects may only import %s (not %s) -- rewrite it with those"
                % (", ".join(sorted(_FX_OK_IMPORTS)), ", ".join(bad_imp)))
    hit = _FX_BANNED.search(code)
    if hit:
        return ("NOT DONE: effects are visuals only -- %r isn't allowed in them. Rewrite without it"
                % hit.group(0))
    os.makedirs(FX_CUSTOM, exist_ok=True)
    path = os.path.join(FX_CUSTOM, name + ".qml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    secs = max(2, min(600, _int(args.get("seconds"), 20)))
    ok, msg = _fx_launch(path, secs, fade=0 if args.get("instant") in ("1", "true", True) else 400,
                         shot="property string shot" in code)
    if not ok:
        os.remove(path)
        return ("NOT DONE: your effect code failed to run. Errors:\n%s\nFix the code and call "
                "make_fx again." % msg)
    return ("made and showing effect %r for %ds -- VERIFIED: %s. Saved: say 'show %s' to "
            "play it again." % (name, secs, msg, name))


RAW_MESSAGE = ""                    # the user's message as typed (set by the brain)


# -- memory: facts T.A.R. keeps in its prompt (memory.json), NOT a notes file ------
_VAGUE_FACT = re.compile(r"^(?:my|your|the|this|that|it|me|him|her|them|what i said)(?: \w+)?$", re.I)


def a_remember(args):
    """Save a fact to T.A.R.'s memory (shown to it on every turn)."""
    fact = (args.get("fact") or args.get("text") or args.get("what") or "").strip().rstrip(".")
    if not fact or _VAGUE_FACT.match(fact):
        # "remember my name" -- which name? the model fills it in from the chat
        return "don't know how to remember %r on its own -- say the actual fact" % fact
    # the matcher lowercased it -- take the user's own spelling back ("Dana")
    i = RAW_MESSAGE.lower().find(fact.lower())
    if i >= 0:
        fact = RAW_MESSAGE[i:i + len(fact)]
    # memory is read as facts ABOUT the user: "i hate firefox" -> "The user hates firefox"
    for a, b in ((r"\bi am\b|\bi'?m\b", "the user is"), (r"\bmy\b", "the user's"),
                 (r"\bme\b", "the user"), (r"\bmine\b", "the user's"), (r"\bmyself\b", "the user")):
        fact = re.sub(a, b, fact, flags=re.I)
    fact = re.sub(r"\bi (\w+?)(s?)\b", lambda m: "the user " + m.group(1) + (
        "" if m.group(1) in ("was", "can", "will", "should", "must", "do", "did", "had", "could",
                             "would") else "es" if m.group(1).endswith(("sh", "ch", "x", "o"))
        else "s"), fact, flags=re.I)
    fact = fact[0].upper() + fact[1:]
    import tar_brain as B
    saved = B.mem_add(fact)
    if not saved:
        return "already remembered: %s -- VERIFIED" % fact
    emit("mem", v=saved["text"])
    return "remembered: %s -- VERIFIED (saved to T.A.R.'s memory)" % saved["text"]


def a_forget(args):
    what = (args.get("fact") or args.get("text") or args.get("what") or "").strip().rstrip(".")
    if not what:
        return "forget what?"
    import tar_brain as B
    n = B.mem_forget(what)
    return ("forgot %d thing%s matching %r -- VERIFIED" % (n, "" if n == 1 else "s", what)
            if n else "no remembered fact matches %r" % what)


def a_stop_task(_):
    d = loop_running()
    if not d:
        try:
            os.remove(LOOP)
        except OSError:
            pass
        return "nothing is running in the background"
    try:
        os.killpg(int(d["pid"]), 15)
    except OSError:
        try:
            os.kill(int(d["pid"]), 15)
        except OSError:
            pass
    try:
        os.remove(LOOP)
    except OSError:
        pass
    mins = int((time.time() - d.get("started", time.time())) / 60)
    return "stopped %r after %d min -- VERIFIED" % (d.get("task", "")[:60], mins)


def a_coin(_):
    side = "heads" if os.urandom(1)[0] % 2 else "tails"
    emit("ui", action="banner", text=side.upper())
    return side


def a_dice(args):
    try:
        sides = max(2, min(1000, int(args.get("sides") or 6)))
    except ValueError:
        sides = 6
    n = 1 + int.from_bytes(os.urandom(2), "big") % sides
    emit("ui", action="banner", text=str(n))
    return "rolled a %d (d%d)" % (n, sides)


def a_play(args):
    q = args.get("q") or args.get("what") or ""
    if not q:
        return "play what?"
    # DuckDuckGo's "\" jumps to the first result; scoping to youtube makes
    # that the top video
    return a_web({"q": q + " site:youtube.com", "lucky": "true"})


# ---- confirmation gate -----------------------------------------------------
# Risky things the MODEL decides to do are parked here and only run when the
# USER's next message says yes. The model cannot confirm for itself: "yes" is
# matched against the user's own message in the fast path, never a tool call.
# Commands the user typed directly (DIRECT=True, set by the brain's fast path)
# skip the gate -- typing "reboot" yourself shouldn't ask twice.
PENDING = os.path.join(DATA, "pending.json")
PENDING_TTL_S = 180
DIRECT = False


def needs_confirm(name, args, summary):
    """Return None if allowed to run now, else park it and say why."""
    if DIRECT or (args or {}).get("_confirmed"):
        return None
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(PENDING, "w", encoding="utf-8") as f:
            json.dump({"name": name, "args": args or {}, "summary": summary,
                       "at": time.time()}, f)
    except OSError:
        return "refused (could not store the confirmation)"
    emit("confirm", v=summary)
    return ("NEEDS CONFIRMATION -- nothing was done yet. Tell the user exactly "
            "this will happen: %s. It only runs if THEY reply yes." % summary)


def pending_action():
    try:
        with open(PENDING, encoding="utf-8") as f:
            p = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - p.get("at", 0) > PENDING_TTL_S:
        clear_pending()
        return None
    return p


def clear_pending():
    try:
        os.remove(PENDING)
    except OSError:
        pass


def a_confirm(_):
    p = pending_action()
    if not p:
        return "nothing is waiting for confirmation"
    clear_pending()
    args = dict(p.get("args") or {})
    args["_confirmed"] = True
    out = run(p["name"], args)
    return "confirmed: %s -> %s" % (p.get("summary", p["name"]), out)


def a_cancel(_):
    p = pending_action()
    clear_pending()
    return ("cancelled: " + p.get("summary", "")) if p else "nothing to cancel"


def _int(v, default):
    try:
        return int(float(str(v).strip().rstrip("%")))
    except (ValueError, TypeError):
        return default


# ---- eyes and hands: screen, OCR, vision, pointer ---------------------------
# power controls: never pressed by clicking -- the power action asks first
_POWER_CLICK = re.compile(r"\b(restart|reboot|shut ?down|power ?off|power button|power icon|"
                          r"turn off|sleep|suspend|hibernate|log ?out|sign ?out)\b", re.I)
# T.A.R.'s own buttons: hidden while it looks, so press them directly
_SELF_UI = [
    (re.compile(r"\b(setup|set up|settings|config(?:uration)?)\b", re.I), "setup"),
    (re.compile(r"\bconsole\b", re.I), "console"),
    (re.compile(r"\b(chats|history|past chats)\b", re.I), "chats"),
    (re.compile(r"\bchat (?:mode|button|view)\b", re.I), "expand"),
    (re.compile(r"\borb(?: mode| button| view)?\b", re.I), "collapse"),
]
_RISKY_CLICK = re.compile(
    r"\b(delete|remove|erase|wipe|uninstall|format|buy|purchase|pay|checkout|"
    r"order|subscribe|unsubscribe|send|post|publish|submit|confirm|transfer|"
    r"sign ?out|log ?out|deactivate|close account|reset|factory)\b", re.I)


def _tar_window():
    rc, out = sh(["hyprctl", "-j", "clients"])
    try:
        return next((w for w in json.loads(out) if w.get("title") == TAR_TITLE), None)
    except ValueError:
        return None


# -- mini orb ------------------------------------------------------------------------
# While T.A.R. works on the screen, a small click-through orb (TarMiniOrb.qml)
# rests in a corner of the window being worked on, glides to just BESIDE each
# spot before it's clicked, and says what it's doing in a speech bubble. Python
# decides the whole layout and writes it to $TAR_DATA/mini.json (so background
# tasks drive it too); screenshots black out exactly that layout, so T.A.R.
# never sees -- or clicks -- its own orb.
MINI_PATH = os.path.join(DATA, "mini.json")
MINI_DOCK = os.path.join(DATA, "mini-dock.json")     # where the home orb sits (QML writes it)
MINI_HOLD = 6                     # seconds the working orb stays out after an update
MINI_R = 26                       # orb radius incl. glow (px)
BUBBLE_W, BUBBLE_H = 264, 72


def _mini_read():
    try:
        with open(MINI_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def mini(**kw):
    """Merge kw into the orb state and publish it. Never raises."""
    try:
        d = _mini_read()
        if time.time() - d.get("at", 0) > d.get("hold", 6):
            d = {"click": d.get("click", 0)}          # stale: start fresh
        d.update(kw)
        d["hold"] = MINI_HOLD
        d["at"] = time.time()
        d["seq"] = d.get("seq", 0) + 1
        if "x" in d and "bx" not in kw:
            d.update(_mini_bubble(d["x"], d["y"], d.get("mw", 1920), d.get("mh", 1080),
                                  d.get("tx"), d.get("ty")))
        os.makedirs(DATA, exist_ok=True)
        tmp = MINI_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, MINI_PATH)
        return d
    except Exception:
        return {}


def _mini_bubble(x, y, mw, mh=1080, tx=None, ty=None):
    """Bubble box on the side of the orb AWAY from the target (it must never
    cover what's about to be clicked), flipped at screen edges."""
    tx = x if tx is None else tx
    ty = y + 1 if ty is None else ty
    up = ty > y                                   # target below the orb -> bubble above
    if up and y - MINI_R - 8 - BUBBLE_H < 8:
        up = False
    if not up and y + MINI_R + 8 + BUBBLE_H > mh - 8:
        up = True
    bx = x - 20 if tx < x else x + 20 - BUBBLE_W  # extend away from the target sideways
    bx = int(min(max(8, bx), mw - BUBBLE_W - 8))
    by = int(y - MINI_R - 8 - BUBBLE_H) if up else int(y + MINI_R + 8)
    return {"bx": bx, "by": by}


def short_say(text, words=6):
    """Orb bubbles stay tiny: first clause, a few words."""
    t = re.sub(r"[*_`#>]+", "", str(text or "")).strip()
    t = re.split(r"(?<=[.!?:;])\s|\n|, ", t)[0]
    w = t.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def mini_say(text):
    d = _mini_read()
    if "x" not in d or time.time() - d.get("at", 0) > d.get("hold", 6):
        mini_rest()                     # give it a place before it talks
    mini(on=True, say=str(text)[:110])


def _mini_glide(x, y, mon):
    """Move the orb to monitor-local (x, y); wait for the glide if it's far."""
    d = _mini_read()
    live = d.get("on") and time.time() - d.get("at", 0) < d.get("hold", 6)
    far = not live or \
        abs(d.get("x", -999) - x) + abs(d.get("y", -999) - y) > 40
    mini(on=True, x=int(x), y=int(y), mon=mon.get("name", ""),
         mw=mon.get("width", 1920), mh=mon.get("height", 1080))
    if far:
        time.sleep(0.32)                # glide (or fly out from home): QML takes 0.3 s


def mini_beside(tx, ty, mon=None):
    """Park the orb just beside a target (never on it), flipping at screen edges."""
    mon = mon or _monitor()
    off = MINI_R + 22
    x = tx + off if tx + off + MINI_R < mon.get("width", 1920) else tx - off
    y = ty + off if ty + off + MINI_R < mon.get("height", 1080) else ty - off
    mini(tx=int(tx), ty=int(ty))
    _mini_glide(x, y, mon)


def mini_rest(mon=None):
    """Orb back to the lower-right corner of the window being worked on."""
    mon = mon or _monitor()
    w = target_window({}) if "target_window" in globals() else None
    if w and w.get("at") and w.get("size"):
        x = w["at"][0] - mon.get("x", 0) + w["size"][0] - 48
        y = w["at"][1] - mon.get("y", 0) + w["size"][1] - 48
    else:
        x, y = mon.get("width", 1920) - 70, mon.get("height", 1080) - 70
    _mini_glide(min(max(40, x), mon.get("width", 1920) - 40),
                min(max(40, y), mon.get("height", 1080) - 40), mon)


def _mini_mask(path):
    """Black out the orb + its bubble in a screenshot (monitor-local pixels)."""
    d = _mini_read()
    try:
        with open(MINI_DOCK, encoding="utf-8") as f:
            dock = json.load(f)
    except (OSError, ValueError):
        dock = {}
    working = d.get("on") and time.time() - d.get("at", 0) < d.get("hold", 6) and "x" in d
    # the home orb may still be on screen for a moment after it hides
    home = dock.get("w") and (dock.get("visible") or time.time() - dock.get("at", 0) < 1.5)
    if not working and not home:
        return
    try:
        from PIL import Image, ImageDraw
        im = Image.open(path)
        dr = ImageDraw.Draw(im)
        if home:
            dr.rectangle([dock["x"], dock["y"], dock["x"] + dock["w"], dock["y"] + dock["h"]],
                         fill=(0, 0, 0))
        if not working:
            im.save(path, quality=85)
            return
        dr.rectangle([d["x"] - MINI_R - 4, d["y"] - MINI_R - 4, d["x"] + MINI_R + 4,
                      d["y"] + MINI_R + 4], fill=(0, 0, 0))
        if d.get("say"):
            dr.rectangle([d["bx"] - 2, d["by"] - 2, d["bx"] + BUBBLE_W + 2,
                          d["by"] + BUBBLE_H + 2], fill=(0, 0, 0))
        im.save(path, quality=85)
    except Exception:
        pass


class _TarHidden:
    """Get T.A.R. out of the way while looking/clicking.

    FLOATING T.A.R. sits on top of things: park it on a hidden special
    workspace and put it back after. TILED T.A.R. covers nothing -- hiding it
    would make Hyprland re-tile, every other window jumps mid-animation and
    the click lands on the wrong spot. So a tiled T.A.R. stays put and its
    rectangle (self.rect) is masked out / refused instead."""
    def __enter__(self):
        # T.A.R.'s pop-out side tabs (viewer, tasks, console) sit OVER the
        # neighbouring app -- hide them for the shot or they block the text
        emit("ui", action="capture", state="on")
        md = _mini_read()
        if not md.get("on") or "x" not in md or time.time() - md.get("at", 0) > md.get("hold", 6):
            mini_rest()                 # the mini orb appears while T.A.R. works
        time.sleep(0.2)
        self.w = _tar_window()
        self.rect = None
        if self.w:
            # only matters if T.A.R. is on the workspace that's actually showing
            rc, out = sh(["hyprctl", "-j", "monitors"])
            try:
                shown = {m["activeWorkspace"]["id"] for m in json.loads(out)}
            except (ValueError, KeyError, TypeError):
                shown = None
            if shown is not None and (self.w.get("workspace") or {}).get("id") not in shown:
                self.w = None
        if self.w and not self.w.get("floating"):
            (x, y), (w, h) = self.w.get("at", [0, 0]), self.w.get("size", [0, 0])
            self.rect = (x, y, x + w, y + h)
            self.w = None                   # nothing to restore
        if self.w:
            self.ws = (self.w.get("workspace") or {}).get("name") or "1"
            sh(["hyprctl", "dispatch", "movetoworkspacesilent",
                "special:tarhide,address:" + self.w["address"]])
            time.sleep(0.25)            # let the compositor redraw without us
        return self

    def __exit__(self, *exc):
        if self.w:
            sh(["hyprctl", "dispatch", "movetoworkspacesilent",
                "%s,address:%s" % (self.ws, self.w["address"])])
        emit("ui", action="capture", state="off")
        return False


def _shot(path):
    rc, out = sh(["grim", "-t", "jpeg", "-q", "80", path], timeout=10)
    ok = rc == 0 and os.path.exists(path)
    if ok:
        _mini_mask(path)                # T.A.R. must not see its own mini orb
    return ok


def _monitor():
    rc, out = sh(["hyprctl", "-j", "monitors"])
    try:
        m = next((m for m in json.loads(out) if m.get("focused")), None) or json.loads(out)[0]
        return m
    except (ValueError, IndexError):
        return {"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}


def _ocr_boxes(path):
    """Words on screen with boxes, grouped into lines: [(text, x, y, w, h)]"""
    if not shutil.which("tesseract"):
        return []
    rc, out = sh(["tesseract", path, "-", "--psm", "11", "tsv"], timeout=40)
    lines = {}
    for row in out.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or not c[11].strip() or _int(c[10], -1) < 40:
            continue
        key = (c[2], c[3], c[4])            # block, paragraph, line
        x, y, w, h = (_int(c[i], 0) for i in (6, 7, 8, 9))
        cur = lines.setdefault(key, [[], x, y, x + w, y + h])
        cur[0].append(c[11])
        cur[1] = min(cur[1], x); cur[2] = min(cur[2], y)
        cur[3] = max(cur[3], x + w); cur[4] = max(cur[4], y + h)
    return [(" ".join(v[0]), v[1], v[2], v[3] - v[1], v[4] - v[2]) for v in lines.values()]


def _find_text(boxes, target):
    """Best on-screen text match for target -> (text, cx, cy) or None"""
    t = re.sub(r"\b(the|a|an|button|link|tab|icon|on|that|this)\b", " ", target.lower())
    t = " ".join(t.split())
    if not t:
        return None
    best, score = None, 0.0
    for text, x, y, w, h in boxes:
        low = text.lower()
        if t == low:
            s_ = 3.0
        elif t in low:
            s_ = 2.0 + len(t) / max(len(low), 1)
        else:
            words = set(t.split())
            s_ = len(words & set(low.split())) / max(len(words), 1)
        if s_ > score:
            # centre of the matched words, not the whole line, when possible
            best, score = (text, x + w // 2, y + h // 2), s_
    return best if score >= 0.6 else None


def _gemini_image(path, prompt, model=None, max_tokens=800):
    """Ask the cloud vision model about a screenshot. Google key only."""
    try:
        import base64
        import tar_cloud as C
    except ImportError:
        return None, "vision needs the cloud backend"
    key = C.api_key("google")
    if not key:
        return None, "vision needs a Gemini key (/key <key>)"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    model = model or cfg().get("vision_model") or "gemini-flash-lite-latest"
    try:
        r = C.gemini_call(key, model, {"contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1}})
    except Exception as e:
        return None, "vision request failed: %s" % e
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip(), None


def _gemini_point(path, target, mon):
    txt, err = _gemini_image(path, (
        "This is a screenshot. Find: %s\nReply with ONLY JSON: "
        "{\"found\": true, \"point\": [y, x], \"label\": \"...\"} with y and x "
        "normalized 0-1000 to the CENTER of it. label = what is ACTUALLY at that "
        "spot in your own words (its visible text, or the icon, e.g. 'restart "
        "icon', 'gear icon'), NOT a copy of the request. If only something "
        "different-but-similar is there, return it with its real label. Or "
        "{\"found\": false, \"why\": \"...\"} if nothing fits." % target))
    if err:
        return None, err
    m = re.search(r"\{.*\}", txt or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    if not d.get("found") or not isinstance(d.get("point"), list):
        return None, "couldn't see %r on screen%s" % (
            target, (": " + d["why"]) if d.get("why") else "")
    y, x = d["point"][:2]
    return (int(x / 1000 * mon["width"]), int(y / 1000 * mon["height"]),
            d.get("label") or target), None


def _locate(target, tries=3):
    """Find something on screen, retrying while a page/app is still loading."""
    loc, err = None, None
    for i in range(max(1, tries)):
        loc, err = _locate_once(target)
        if loc or (err and "vision" in err and "failed" in err):
            break
        if i < tries - 1:
            time.sleep(2.0)
    return loc, err


def _mask(path, rect, mon):
    """Black out T.A.R.'s own (tiled) window in a screenshot."""
    if not rect:
        return
    x1, y1, x2, y2 = (rect[0] - mon.get("x", 0), rect[1] - mon.get("y", 0),
                      rect[2] - mon.get("x", 0), rect[3] - mon.get("y", 0))
    try:
        from PIL import Image, ImageDraw
        im = Image.open(path)
        ImageDraw.Draw(im).rectangle([x1, y1, x2, y2], fill=(0, 0, 0))
        im.save(path, quality=85)
    except Exception:
        if shutil.which("magick"):
            sh(["magick", path, "-fill", "black", "-draw",
                "rectangle %d,%d %d,%d" % (x1, y1, x2, y2), path])


def _inside(rect, x, y, mon=None):
    if not rect:
        return False
    mon = mon or {}
    ax, ay = x + mon.get("x", 0), y + mon.get("y", 0)
    return rect[0] <= ax <= rect[2] and rect[1] <= ay <= rect[3]


def _locate_once(target):
    """Find something on screen -> ((x, y, label, how), None) or (None, why).
    OCR first (exact for text buttons), vision second (icons, images)."""
    path = os.path.join(DATA, "screen-look.jpg")
    mon = _monitor()
    mini_rest(mon)                      # out of the way of what it's looking for
    with _TarHidden() as hid:
        if not _shot(path):
            return None, "couldn't take a screenshot"
        rect = hid.rect
    _mask(path, rect, mon)                  # tiled T.A.R. can't see/click itself
    # OCR (text buttons) and vision (icons, pictures) run AT THE SAME TIME:
    # a lookup costs the slower of the two instead of both added up
    import threading
    res = {}
    vt = threading.Thread(target=lambda: res.__setitem__("v", _gemini_point(path, target, mon)))
    vt.start()
    hit = _find_text(_ocr_boxes(path), target)
    if hit and not _inside(rect, hit[1], hit[2], mon):
        return (hit[1], hit[2], hit[0], "text"), None   # vision thread just finishes on its own
    vt.join(timeout=30)
    p, err = res.get("v") or (None, "vision timed out")
    if p and _inside(rect, p[0], p[1], mon):
        return None, "that spot is inside T.A.R.'s own window -- not clicking myself"
    if p:
        return (p[0], p[1], p[2], "vision"), None
    return None, (err or "not found") + (" -- call look to see what's on screen; a popup "
                                         "or dialog may be in the way")


def _wlrctl():
    """wlrctl from PATH, or ~/.local/bin (a user-built copy works without
    sudo, and T.A.R. may be launched with a PATH that lacks ~/.local/bin)."""
    return shutil.which("wlrctl") or next(
        (p for p in (os.path.expanduser("~/.local/bin/wlrctl"), "/usr/bin/wlrctl")
         if os.access(p, os.X_OK)), None)


def _pointer_ok():
    return bool(_wlrctl())


_NO_POINTER = ("can't click yet: the mouse tool isn't installed. Tell the user to "
               "open SETUP and install the Mouse capability (one click, no "
               "password), then try again")


def _move(x, y, mon=None):
    mon = mon or _monitor()
    mini_beside(x, y, mon)              # the orb flies over first, then the pointer goes
    sh(["hyprctl", "dispatch", "movecursor", str(int(mon.get("x", 0) + x)),
        str(int(mon.get("y", 0) + y))])


def _click(button="left", double=False):
    mini(click=_mini_read().get("click", 0) + 1)    # flash ring at the target
    for _ in range(2 if double else 1):
        sh([_wlrctl(), "pointer", "click", button])
        if double:
            time.sleep(0.08)


def show_seen(src_path, source, caption, mark=None):
    """Copy what T.A.R. just looked at into shots/ (unique name, so the UI
    reloads it), optionally draw a crosshair where it clicked, and tell the UI
    to open the viewer -- you always see what it saw."""
    try:
        os.makedirs(SHOTS, exist_ok=True)
        out = os.path.join(SHOTS, "%s-%s.jpg" % (source, time.strftime("%Y%m%d-%H%M%S")))
        from PIL import Image, ImageDraw
        im = Image.open(src_path).convert("RGB")
        if mark:
            x, y = mark
            d = ImageDraw.Draw(im)
            for r, w in ((26, 5), (26, 2)):
                d.ellipse([x - r, y - r, x + r, y + r], outline=(0, 0, 0) if w == 5 else (255, 70, 90), width=w)
            d.line([x - 40, y, x - 10, y], fill=(255, 70, 90), width=3)
            d.line([x + 10, y, x + 40, y], fill=(255, 70, 90), width=3)
            d.line([x, y - 40, x, y - 10], fill=(255, 70, 90), width=3)
            d.line([x, y + 10, x, y + 40], fill=(255, 70, 90), width=3)
        im.save(out, quality=82)
        # keep the folder from growing forever: last 40 screen/click shots
        old = sorted(f for f in os.listdir(SHOTS) if f.startswith(("screen-", "click-")))
        for f in old[:-40]:
            try:
                os.remove(os.path.join(SHOTS, f))
            except OSError:
                pass
        emit("image", path=out, source=source, caption=caption[:120])
    except Exception:
        pass


def a_look(args):
    """Answer a question about what's on screen (T.A.R. hides itself first)."""
    q = args.get("q") or args.get("question") or "Describe what is on the screen."
    img = (args.get("path") or args.get("image") or "").strip()
    if img:                                 # a specific picture (e.g. a camera photo)
        img = resolve_path(img) if not img.startswith("/") else img
        if not os.path.isfile(img):
            return "no such image: " + img
        txt, err = _gemini_image(img, q + " Answer directly and briefly.")
        show_seen(img, "image", "looked at " + os.path.basename(img))
        return err or txt or "couldn't make sense of the image"
    path = os.path.join(DATA, "screen-look.jpg")
    with _TarHidden() as hid:
        if not _shot(path):
            return "couldn't take a screenshot"
        _mask(path, hid.rect, _monitor())
    show_seen(path, "screen", "what T.A.R. saw on your screen")
    txt, err = _gemini_image(path, "Screenshot of the user's desktop. " + q +
                             " Be specific and brief; mention exact button/label text.")
    if err:
        # no vision: fall back to OCR text so the model has *something*
        words = [b[0] for b in _ocr_boxes(path)][:60]
        return "(%s) text visible on screen: %s" % (err, " | ".join(words) or "none")
    return txt or "couldn't make sense of the screen"


# ---- verification: did that actually do anything? --------------------------
def _screen_change(before, after, x, y, radius=160):
    """Fraction of pixels that visibly changed: (around the click, whole screen).
    None if it can't be measured (no PIL / missing shots)."""
    try:
        from PIL import Image, ImageChops
        a = Image.open(before).convert("L")
        b = Image.open(after).convert("L")
        if a.size != b.size:
            return None
        diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 28 else 0)

        def frac(img):
            small = img.resize((max(1, img.width // 4), max(1, img.height // 4)))
            hist = small.histogram()
            total = sum(hist) or 1
            return hist[255] / total
        box = (max(0, x - radius), max(0, y - radius),
               min(a.width, x + radius), min(a.height, y + radius))
        return frac(diff.crop(box)), frac(diff)
    except Exception:
        return None


def _verdict(change):
    if change is None:
        return "(couldn't verify)"
    local, overall = change
    if local > 0.015 or overall > 0.02:
        return "VERIFIED: the screen changed (%d%% around the click, %d%% overall)" % (
            round(local * 100), round(overall * 100))
    return ("NOT VERIFIED: nothing on screen changed after the click -- it probably "
            "missed or the thing isn't clickable. look, then try again (or tell the user)")


def _click_and_check(x, y, button, double, times=1, fast=False):
    """Hover, snapshot, click, snapshot, compare. Returns the verdict text."""
    mon = _monitor()
    b_path = os.path.join(DATA, "click-before.jpg")
    a_path = os.path.join(DATA, "click-after.jpg")
    with _TarHidden() as hid:
        _move(x, y, mon)
        time.sleep(0.15)                    # let hover effects settle first
        have_before = _shot(b_path)
        for i in range(times):
            _click(button, double)
            if times > 1:
                time.sleep(0.04 if fast else 0.12)
        time.sleep(0.6)                     # give the page time to react
        have_after = _shot(a_path)
        rect = hid.rect
    if not (have_before and have_after):
        return "(couldn't verify)"
    _mask(b_path, rect, mon)
    _mask(a_path, rect, mon)
    return _verdict(_screen_change(b_path, a_path, x, y))


# ---- remembered positions: don't re-find the same button every click ---------
LOC_CACHE = os.path.join(DATA, "loc-cache.json")
LOC_TTL_S = 900


def _layout_sig():
    """Changes whenever any window moves/resizes or another workspace shows."""
    try:
        ws = json.loads(sh(["hyprctl", "-j", "activeworkspace"])[1]).get("id")
    except (ValueError, TypeError):
        ws = None
    geo = sorted((w.get("address"), tuple(w.get("at") or ()), tuple(w.get("size") or ()))
                 for w in _clients())
    return "%s|%s" % (ws, hash(str(geo)))


def _loc_key(target):
    return " ".join(re.sub(r"\b(the|a|an|that|this|button|on)\b", " ", target.lower()).split())


def _loc_get(target):
    try:
        with open(LOC_CACHE, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    e = c.get(_loc_key(target))
    if e and time.time() - e.get("at", 0) < LOC_TTL_S and e.get("sig") == _layout_sig():
        return e
    return None


def _loc_set(target, x, y, label, drop=False):
    try:
        with open(LOC_CACHE, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    k = _loc_key(target)
    if drop:
        c.pop(k, None)
    else:
        c[k] = {"x": x, "y": y, "label": label, "at": time.time(), "sig": _layout_sig()}
    try:
        with open(LOC_CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f)
    except OSError:
        pass


_FIELD_WORDS = re.compile(r"\b(cell|field|box|input|text ?area|search ?bar|address bar|"
                          r"textbox|empty|blank|column|row|form|line)\b", re.I)


def a_click_on(args):
    """click_on + an honest verdict for text fields: clicking into a field or
    table cell only moves the caret, which barely changes the screen -- that
    used to read as NOT VERIFIED and made T.A.R. give up on working clicks."""
    out = _click_on_raw(args)
    target = str(args.get("target") or args.get("what") or "")
    if isinstance(out, str) and "NOT VERIFIED: nothing on screen changed" in out \
            and _FIELD_WORDS.search(target):
        out = out.split(" -- NOT VERIFIED")[0] + (
            " -- PROBABLY OK: clicking into a text field/cell only moves the cursor, which "
            "can't be seen. Type next, then check that the text appeared.")
    return out


def _click_on_raw(args):
    """Click something on screen by description: 'the subscribe button'."""
    target = (args.get("target") or args.get("what") or "").strip()
    if not target:
        return "click on what?"
    # "that thing in the top right" names nothing -- clicking a guess is how
    # you hit the wrong button. Make the model look first.
    vague = re.sub(r"\b(the|a|an|that|this|it|thing|stuff|one|button|icon|spot|"
                   r"in|on|at|of|top|bottom|left|right|middle|center|centre|corner|"
                   r"upper|lower|side|there|here)\b", " ", target.lower()).strip()
    if not vague:
        return ("too vague to click safely (%r). Use look first to see what's "
                "there, then click_on its actual label." % target)
    # "your setup button": T.A.R. can't see itself, so press its own UI directly
    if re.search(r"\b(your|tar'?s|t\.a\.r\.?'?s)\b", target, re.I) or \
            re.fullmatch(r"(?:the )?(setup|settings|console|chats|history)(?: button| tab| panel)?",
                         target.strip(), re.I):
        for pat, ui in _SELF_UI:
            if pat.search(target):
                emit("ui", action=ui)
                return "opened T.A.R.'s own %s (pressed directly -- it isn't on the screenshot)" % ui
    if _POWER_CLICK.search(target):
        return ("not clicking power controls -- use the power action instead "
                "(action=restart/shutdown/suspend/logout); it asks the user to confirm")
    if not _pointer_ok():
        return _NO_POINTER
    if _RISKY_CLICK.search(target):
        stop = needs_confirm("click_on", args, "click %r" % target)
        if stop:
            return stop
    cached = _loc_get(target)
    if cached:
        loc, err = (cached["x"], cached["y"], cached["label"], "memory"), None
    else:
        loc, err = _locate(target)
    if not loc:
        return err
    x, y, label, how = loc
    if _POWER_CLICK.search(label):
        return ("that turned out to be a power control (%r) -- not clicking it. "
                "Use the power action if the user wants that; it asks to confirm." % label)
    if _RISKY_CLICK.search(label) and not args.get("_confirmed") and not DIRECT:
        stop = needs_confirm("click_on", args, "click %r (found %r)" % (target, label))
        if stop:
            return stop
    button = (args.get("button") or "left").lower()
    times = max(1, min(50, _int(args.get("times"), 1)))
    shot = os.path.join(DATA, "screen-look.jpg")
    if how != "memory" and os.path.exists(shot):
        show_seen(shot, "click", "clicking %r" % label[:60], mark=(x, y))
    btn = button if button in ("left", "right", "middle") else "left"
    dbl = str(args.get("double", "")).lower() in ("1", "true", "yes")
    fast = str(args.get("fast", "")).lower() in ("1", "true", "yes")
    check = _click_and_check(x, y, btn, dbl, times, fast)
    if check.startswith("NOT VERIFIED") and how == "memory":
        # the remembered spot is stale (page scrolled/changed): find it fresh
        _loc_set(target, 0, 0, "", drop=True)
        loc, err = _locate(target)
        if not loc:
            return err
        x, y, label, how = loc
        check = _click_and_check(x, y, btn, dbl, times, fast)
    if check.startswith("VERIFIED"):
        _loc_set(target, x, y, label)       # next time: straight there
    return "clicked %r%s at (%d, %d) [found by %s] -- %s" % (
        label[:60], (" %d times" % times) if times > 1 else "", x, y, how, check)


def a_click(args):
    """Click at screen coordinates (pixels from the top-left of the screen)."""
    if not _pointer_ok():
        return _NO_POINTER
    x, y = _int(args.get("x"), -1), _int(args.get("y"), -1)
    mon = _monitor()
    if not (0 <= x < mon["width"] and 0 <= y < mon["height"]):
        return "x/y must be on screen (0-%d, 0-%d)" % (mon["width"] - 1, mon["height"] - 1)
    button = (args.get("button") or "left").lower()
    check = _click_and_check(
        x, y, button if button in ("left", "right", "middle") else "left",
        str(args.get("double", "")).lower() in ("1", "true", "yes"))
    return "clicked %s at (%d, %d) -- %s" % (button, x, y, check)


def a_scroll(args):
    """Scroll a window (named via what=, else the last one used)."""
    if not _pointer_ok():
        return _NO_POINTER
    w = target_window({"what": args.get("what") or args.get("window") or ""})
    if not w:
        return "no window to scroll"
    d = (args.get("dir") or args.get("direction") or "down").lower()
    n = max(1, min(40, _int(args.get("amount"), 5)))
    (x, y), (ww, hh) = w.get("at", [0, 0]), w.get("size", [800, 600])
    sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
    with _TarHidden():
        sh(["hyprctl", "dispatch", "movecursor", str(x + ww // 2), str(y + hh // 2)])
        dy = {"down": 1, "up": -1}.get(d, 0) * 15 * n
        dx = {"right": 1, "left": -1}.get(d, 0) * 15 * n
        sh([_wlrctl(), "pointer", "scroll", str(dy), str(dx)])
    return "scrolled %s %d in %s" % (d, n, _label(w))


_SHEET_PROMPT = (
    "This is a screenshot of the user's screen. Find what the user must ANSWER on it: questions in "
    "a document, worksheet or text editor -- empty table cells, 'answer:' lines, blanks after a "
    "question.{extra}\n"
    "{context}"
    "Use ONLY what is visible in this screenshot (plus the earlier text above, for reading "
    "comprehension). Never invent questions or tables that aren't there.\n"
    "Answer each one correctly and concisely, like a strong student, in the language the question "
    "is written in. Reply with ONLY JSON:\n"
    "{{\"found\": true, \"slots\": [{{\"question\": \"<the question as written>\", "
    "\"answer\": \"<your answer>\", \"box\": [ymin, xmin, ymax, xmax], "
    "\"kind\": \"cell|line|inline|below\"}}]}}\n"
    "- box = WHERE THE ANSWER GOES, normalized 0-1000 on the whole screenshot.\n"
    "- kind: 'cell' = an empty table cell (box = the cell); 'line' = an 'answer:' line (box = the "
    "rest of that line after 'answer:'); 'inline' = a blank like ___ INSIDE a sentence (box = "
    "exactly the ___); 'below' = the empty line under the question (box = that line).\n"
    "- Skip anything already answered.\n"
    "ALWAYS also include \"page_text\": a faithful transcription of the readable text on this screen "
    "(the story / passage / instructions, up to ~2000 characters) -- it is passed on as context "
    "when the document is scrolled and the questions about it come later.\n"
    "If nothing on screen needs answering: {{\"found\": false, \"why\": \"<what you see instead>\", "
    "\"page_text\": \"...\"}}")


def _ocr_langs():
    """eng+heb only if Hebrew data is installed -- asking for a missing
    language made every OCR run fail once and run again (2x slower)."""
    if not hasattr(_ocr_langs, "v"):
        rc, out = sh(["tesseract", "--list-langs"], timeout=10)
        _ocr_langs.v = "eng+heb" if "heb" in (out or "") else "eng"
    return _ocr_langs.v


def _vshift(prev_path, cur_path, max_shift=400):
    """How far the page moved vertically between two screenshots (pixels,
    + = content moved UP). Row-brightness cross-correlation: milliseconds,
    instead of a full OCR before every answer."""
    try:
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(prev_path).convert("L"), dtype=np.float32).mean(axis=1)
        b = np.asarray(Image.open(cur_path).convert("L"), dtype=np.float32).mean(axis=1)
        if a.shape != b.shape:
            return None
        a -= a.mean(); b -= b.mean()
        n = len(a)
        best, best_err = 0, None
        for d in range(-max_shift, max_shift + 1, 2):
            if d >= 0:
                x, y = a[d:], b[:n - d]
            else:
                x, y = a[:n + d], b[-d:]
            err = float(np.mean(np.abs(x - y)))
            if best_err is None or err < best_err:
                best, best_err = d, err
        return best
    except Exception:
        return None


def _ocr_lines(img_path, sx, sy):
    """OCR the screenshot into text lines: [(text_lower, center_y, right_x)]."""
    if not shutil.which("tesseract"):
        return []
    rc, out = sh(["tesseract", img_path, "-", "-l", _ocr_langs(), "--psm", "4", "tsv"], timeout=40)
    lines = {}
    for ln in (out or "").splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        try:
            key = (int(f[2]), int(f[3]), int(f[4]))
            left, top, w, h = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        except ValueError:
            continue
        L = lines.setdefault(key, {"words": [], "ys": [], "right": 0})
        L["words"].append(f[11].strip().lower())
        L["ys"].append(top + h / 2)
        L["right"] = max(L["right"], left + w)
    out_l = [(" ".join(L["words"]), sum(L["ys"]) / len(L["ys"]) * sy, L["right"] * sx) for L in lines.values()]
    return sorted(out_l, key=lambda r: r[1])


_ANSWER_LINE = re.compile(r"^(?:answer|answers|ans|a|תשובה|תשובות)\s*[:.]")


def _answer_spot_below(question, lines):
    """The 'answer:' line directly BELOW the question's own line (matched by its
    words). Picking the label NEAREST the vision model's guess put every answer
    one line too low on a tight sheet."""
    qw = set(re.findall(r"\w+", str(question).lower())) - {"the", "a", "an", "of", "is", "in"}
    if not qw:
        return None
    best, best_score = None, 0.0
    for i, (text, y, _r) in enumerate(lines):
        tw = set(re.findall(r"\w+", text))
        score = len(qw & tw) / len(qw)
        if score > best_score:
            best, best_score = i, score
    if best is None or best_score < 0.5:
        return None
    for text, y, right in lines[best + 1:best + 4]:
        if _ANSWER_LINE.match(text):
            return (int(right) + 12, int(y))
    return None


def _answer_labels(img_path, sx, sy):
    """Exact screen positions of 'answer:' / 'תשובה:' labels via tesseract --
    the vision model's boxes can be a line off (it typed '600' after the
    question instead of on the answer line). Returns [(right_x, center_y)]."""
    if not shutil.which("tesseract"):
        return []
    rc, out = sh(["tesseract", img_path, "-", "-l", "eng+heb", "--psm", "11", "tsv"], timeout=40)
    if rc != 0:
        rc, out = sh(["tesseract", img_path, "-", "--psm", "11", "tsv"], timeout=40)
    found = []
    for ln in (out or "").splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12:
            continue
        word = f[11].strip().lower()
        if re.match(r"^(answer|answers|ans|a|תשובה|תשובות)[:.]$", word) or word in ("answer", "תשובה"):
            try:
                left, top, w, h = (int(f[6]), int(f[7]), int(f[8]), int(f[9]))
            except ValueError:
                continue
            found.append((int((left + w) * sx), int((top + h / 2) * sy)))
    return found


def _qkey(q):
    return " ".join(re.findall(r"\w+", str(q).lower()))[:80]


def _sheet_page(extra, mon, done, context=""):
    """One screen: find the questions still to answer (not in `done`), with
    exact spots (OCR-snapped 'answer:' lines). Returns (slots, why, shot)."""
    shot = os.path.join(DATA, "sheet-look.jpg")
    with _TarHidden() as hid:
        ok = _shot(shot)
        rect = hid.rect
    if not ok:
        return None, "couldn't take a screenshot", None
    _mask(shot, rect, mon)
    ctx = ("Text from EARLIER in this document (scrolled past -- use it to answer questions "
           "about the passage):\n\"\"\"\n%s\n\"\"\"\n" % context[-6000:]) if context else ""
    txt, err = _gemini_image(shot, _SHEET_PROMPT.format(extra=(" The user says: " + extra) if extra else "",
                                                        context=ctx),
                             model=cfg().get("homework_model") or "gemini-3.5-flash", max_tokens=6000)
    if err:
        return None, err, shot
    m = re.search(r"\{.*\}", txt or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    slots = [x for x in (d.get("slots") or []) if isinstance(x, dict) and isinstance(x.get("box"), list)
             and len(x["box"]) == 4 and str(x.get("answer") or "").strip()
             and _qkey(x.get("question", "")) not in done]
    try:
        from PIL import Image
        iw, ih = Image.open(shot).size
    except Exception:
        iw, ih = mon["width"], mon["height"]
    olines = _ocr_lines(shot, mon["width"] / iw, mon["height"] / ih)
    for x in slots:
        if x.get("kind") == "line":
            spot = _answer_spot_below(x.get("question", ""), olines)
            if spot:
                x["_at"] = spot
    labels = _answer_labels(shot, mon["width"] / iw, mon["height"] / ih)
    used = set()
    for x in sorted(slots, key=lambda x: x["box"][0]):
        if x.get("kind") != "line" or not labels or x.get("_at"):
            continue
        y = (x["box"][0] + x["box"][2]) / 2 / 1000 * mon["height"]
        best = min((i for i in range(len(labels)) if i not in used),
                   key=lambda i: abs(labels[i][1] - y), default=None)
        if best is not None and abs(labels[best][1] - y) < mon["height"] * 0.08:
            used.add(best)
            x["_at"] = (labels[best][0] + 12, labels[best][1])
    _sheet_page.page_text = str(d.get("page_text") or "")
    return slots, d.get("why") or "", shot


def _page_moved(before, after):
    """Did the document scroll? Full resolution, small threshold: a dark editor
    with thin text changes well under 1% of the screen when it scrolls -- the
    click check (quarter size, >1%) said "nothing moved" and T.A.R. stopped."""
    try:
        from PIL import Image, ImageChops
        a = Image.open(before).convert("L")
        b = Image.open(after).convert("L")
        if a.size != b.size:
            return True, 1.0
        diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0)
        frac = diff.histogram()[255] / float(a.width * a.height)
        return frac > 0.0004, frac
    except Exception:
        return False, 0.0


def _sheet_log(**kw):
    try:
        kw["at"] = time.strftime("%H:%M:%S")
        with open(os.path.join(DATA, "sheet.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _type_slots(slots, mon):
    """Type each answer TOP-DOWN. The answer lines are found by OCR ONCE; before
    each answer a quick screenshot measures whether the page shifted (typing
    near the bottom scrolls the editor) and the saved spots move with it."""
    doc_x = mon["width"] // 2
    base = os.path.join(DATA, "sheet-base.jpg")
    live = os.path.join(DATA, "sheet-live.jpg")
    olines = None
    if _shot(base):
        try:
            from PIL import Image
            iw, ih = Image.open(base).size
        except Exception:
            iw, ih = mon["width"], mon["height"]
        olines = _ocr_lines(base, mon["width"] / iw, mon["height"] / ih)
    for x in sorted(slots, key=lambda x: x["box"][0]):
        y0, x0, y1, x1 = x["box"]
        cy = int((y0 + y1) / 2 / 1000 * mon["height"])
        if x.get("kind") == "line":
            cx = int((x0 + (x1 - x0) * 0.15) / 1000 * mon["width"])
            spot = _answer_spot_below(x.get("question", ""), olines) if olines else None
            spot = spot or x.get("_at")
            if spot:
                cx, cy = spot
                if _shot(live):
                    dy = _vshift(base, live)
                    if dy:
                        cy -= dy                      # the page moved up by dy
            x["_typed_at"] = [cx, cy]
        else:
            cx = int((x0 + x1) / 2 / 1000 * mon["width"])
        doc_x = cx
        _move(cx, cy, mon)
        time.sleep(0.08)
        ans = str(x["answer"])
        if x.get("kind") == "inline":
            # a ___ blank inside a sentence: double-click selects the blank,
            # typing replaces it (End would put the answer after the full stop)
            _click("left", True)
            time.sleep(0.15)
            put_text(ans, timeout=30)
        else:
            _click("left", False)
            time.sleep(0.15)
            wkeys("-k", "End")
            put_text((" " + ans) if x.get("kind") == "line" else ans, timeout=30)
        time.sleep(0.2)
    _sheet_log(typed=[[str(x.get("question", ""))[:30], x.get("_typed_at")] for x in slots])
    return doc_x


def _empty_answer_lines(mon):
    """OCR the screen: are there 'answer:' lines with nothing after them?"""
    p = os.path.join(DATA, "sheet-sweep.jpg")
    if not _shot(p):
        return 0
    try:
        from PIL import Image
        iw, ih = Image.open(p).size
    except Exception:
        iw, ih = mon["width"], mon["height"]
    return sum(1 for t, _y, _r in _ocr_lines(p, mon["width"] / iw, mon["height"] / ih)
               if re.match(r"^(?:answer|answers|ans|תשובה)\s*[:.]?\s*$", t))


def a_fill_table(args):
    """Homework / question sheets / tables, even LONGER than the screen:
    answer what's visible, scroll down, repeat (skipping questions already
    answered) until the page stops moving -- at most 8 screens."""
    if not shutil.which("wtype"):
        return "wtype isn't installed"
    extra = (args.get("q") or args.get("instructions") or "").strip()
    # only pass instructions that came from the user, never the brain's own
    # summary of an older task (it once fed in a previous doc's topic)
    if extra and extra.lower() not in (USER_SAID or "").lower() and not DIRECT:
        extra = ""
    mon = _monitor()
    done, answered, pages, why, context = set(), [], 0, "", ""
    doc_x = mon["width"] // 2
    # start from the TOP: the passage the questions are about is usually above them
    for _ in range(6):
        b0 = os.path.join(DATA, "scroll-before.jpg")
        a0 = os.path.join(DATA, "scroll-after.jpg")
        with _TarHidden() as hid:
            _shot(b0)
            _move(doc_x, int(mon["height"] * 0.55), mon)
            sh([_wlrctl(), "pointer", "scroll", str(-int(mon["height"] * 1.5)), "0"])
            time.sleep(0.5)
            _shot(a0)
            rect = hid.rect
        _mask(b0, rect, mon)
        _mask(a0, rect, mon)
        if not _page_moved(b0, a0)[0]:
            with _TarHidden():
                wkeys("-M", "ctrl", "-k", "Home", "-m", "ctrl")   # top of the document
                time.sleep(0.4)
            break
    for page in range(8):
        _sheet_page.page_text = ""
        slots, why, shot = _sheet_page(extra, mon, done, context)
        if _sheet_page.page_text:
            context += "\n" + _sheet_page.page_text
        if slots is None:
            if answered:
                break
            return "NOT DONE: " + why
        for x in slots:
            stop = _personal_guard(str(x["answer"]), "answer")
            if stop:
                return stop
        _sheet_log(page=page + 1, found=[str(x.get("question", ""))[:50] for x in (slots or [])],
                   spots=[x.get("_at") or "vision" for x in (slots or [])], why=why)
        if slots:
            pages += 1
            show_seen(shot, "screen", "page %d: %d question%s" % (page + 1, len(slots), "" if len(slots) == 1 else "s"))
            with _TarHidden():
                doc_x = _type_slots(slots, mon)
            for x in slots:
                done.add(_qkey(x.get("question", "")))
                answered.append(x)
        # scroll on: if the page doesn't move, we've reached the end
        before = os.path.join(DATA, "scroll-before.jpg")
        after = os.path.join(DATA, "scroll-after.jpg")
        with _TarHidden() as hid:
            _shot(before)
            _move(doc_x, int(mon["height"] * 0.55), mon)
            sh([_wlrctl(), "pointer", "scroll", str(int(mon["height"] * 0.55)), "0"])
            time.sleep(0.6)
            _shot(after)
            rect = hid.rect
        _mask(before, rect, mon)
        _mask(after, rect, mon)
        moved, frac = _page_moved(before, after)
        how = "wheel"
        if not moved:
            # some editors ignore the mouse wheel (it read "scrolled 0.0" and
            # stopped after 8 of 11 questions): Page Down in the document
            with _TarHidden() as hid:
                wkeys("-k", "Next")
                time.sleep(0.15)
                wkeys("-k", "Next")
                time.sleep(0.6)
                _shot(after)
                rect = hid.rect
            _mask(after, rect, mon)
            moved, frac = _page_moved(before, after)
            how = "pagedown"
        _sheet_log(page=page + 1, scrolled=round(frac, 4), moved=moved, how=how)
        if not moved:
            break                                   # really the bottom of the document
    # reached the end: sweep back UP to make sure nothing was missed. OCR finds
    # 'answer:' lines still empty; only those screens go back to the AI.
    for sweep in range(8):
        with _TarHidden() as hid:
            empty = _empty_answer_lines(mon)
            rect = hid.rect
        _sheet_log(sweep=sweep + 1, empty_answer_lines=empty)
        if empty:
            slots, why2, shot = _sheet_page(extra, mon, done, context)
            slots = [x for x in (slots or []) if _personal_guard(str(x["answer"]), "answer") is None]
            if slots:
                show_seen(shot, "screen", "missed on the way down: %d" % len(slots))
                with _TarHidden():
                    doc_x = _type_slots(slots, mon)
                for x in slots:
                    done.add(_qkey(x.get("question", "")))
                    answered.append(x)
        before = os.path.join(DATA, "scroll-before.jpg")
        after = os.path.join(DATA, "scroll-after.jpg")
        with _TarHidden() as hid:
            _shot(before)
            _move(doc_x, int(mon["height"] * 0.55), mon)
            sh([_wlrctl(), "pointer", "scroll", str(-int(mon["height"] * 0.55)), "0"])
            time.sleep(0.5)
            _shot(after)
            rect = hid.rect
        _mask(before, rect, mon)
        _mask(after, rect, mon)
        moved, _f = _page_moved(before, after)
        if not moved:
            with _TarHidden() as hid:
                wkeys("-k", "Prior")
                time.sleep(0.15)
                wkeys("-k", "Prior")
                time.sleep(0.5)
                _shot(after)
                rect = hid.rect
            _mask(after, rect, mon)
            moved, _f = _page_moved(before, after)
        if not moved:
            break                                   # back at the top
    if not answered:
        return "NOT DONE: nothing to answer on screen -- %s" % (why or "no questions found")
    final = os.path.join(DATA, "fill-after.jpg")
    with _TarHidden() as hid:
        _shot(final)
        rect = hid.rect
    _mask(final, rect, mon)
    show_seen(final, "screen", "after answering %d" % len(answered))
    lines = "\n".join("- %s -> %s" % (str(x.get("question", ""))[:60], x["answer"]) for x in answered)
    return ("answered %d question(s) over %d screen(s), scrolled to the end -- VERIFIED by "
            "screenshots:\n%s" % (len(answered), max(1, pages), lines))


def a_fill_cells(args):
    """Fill a table / form the reliable way: click the FIRST cell once, then
    type each value and press Tab to move to the next cell (Google Docs and
    Sheets tables, web forms). An empty value skips a cell. Verifies by
    comparing the screen before and after."""
    start = (args.get("start") or args.get("target") or args.get("first") or "").strip()
    raw = args.get("texts") or args.get("values") or args.get("text") or ""
    if isinstance(raw, list):
        texts = [str(t) for t in raw]
    else:
        raw = str(raw)
        try:
            v = json.loads(raw)
            texts = [str(t) for t in v] if isinstance(v, list) else [raw]
        except ValueError:
            texts = [t.strip() for t in raw.split("||")]
    if not start or not any(t.strip() for t in texts):
        return "need start=<the first cell to fill> and texts=<value1 || value2 || ...>"
    if not shutil.which("wtype"):
        return "wtype isn't installed"
    for t in texts:
        stop = _personal_guard(t, start)
        if stop:
            return stop
    nav = {"tab": "Tab", "down": "Down", "enter": "Return"}.get(str(args.get("nav") or "tab").lower(), "Tab")
    clicked = _click_on_raw({"target": start, "_confirmed": args.get("_confirmed")})
    if not str(clicked).startswith("clicked"):
        return clicked
    time.sleep(0.25)
    mon = _monitor()
    b_path = os.path.join(DATA, "fill-before.jpg")
    a_path = os.path.join(DATA, "fill-after.jpg")
    with _TarHidden() as hid, keys_paused():
        have_b = _shot(b_path)
        typed = 0
        for i, t in enumerate(texts):
            if t.strip():
                put_text(t, timeout=30)
                typed += 1
            if i < len(texts) - 1:
                wkeys("-k", nav)
                time.sleep(0.12)
        time.sleep(0.5)
        have_a = _shot(a_path)
        rect = hid.rect
    verdict = "(couldn't verify)"
    if have_b and have_a:
        _mask(b_path, rect, mon)
        _mask(a_path, rect, mon)
        ch = _screen_change(b_path, a_path, 0, 0)
        if ch is not None:
            verdict = ("VERIFIED: new text appeared on screen" if ch[1] > 0.002 else
                       "NOT VERIFIED: the screen didn't change -- the first click probably wasn't "
                       "inside the table. look, then retry with a clearer start=")
        show_seen(a_path, "screen", "after filling %d cell%s" % (typed, "" if typed == 1 else "s"))
    return "%s -- filled %d cell(s), moving with %s -- %s" % (clicked.split(" -- ")[0], typed, nav, verdict)


def a_type_into(args):
    """Click a field by description, then type into it."""
    text = args.get("text") or ""
    if not text:
        return "type what?"
    stop = _personal_guard(text, args.get("target") or args.get("field") or "")
    if stop:
        return stop
    clicked = a_click_on({"target": args.get("target") or args.get("field") or "",
                          "_confirmed": args.get("_confirmed")})
    if not clicked.startswith("clicked"):
        return clicked
    time.sleep(0.15)
    rc, out = put_text(text)
    return clicked + (" -- typed %d chars" % len(text) if rc == 0 else " -- typing failed")


def a_ask_claude(args):
    """Ask Claude (Claude Code CLI) a question. Tools disabled: answer-only."""
    q = (args.get("q") or args.get("question") or args.get("what") or "").strip()
    if not q:
        return "ask Claude what?"
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(exe):
        return "Claude Code isn't installed"
    rc, out = sh([exe, "-p", q, "--tools", "", "--no-session-persistence",
                  "--output-format", "text"], timeout=180)
    if rc != 0:
        return "Claude didn't answer: " + (out or "exit %d" % rc)[:200]
    return "Claude says:\n" + out[:3500]


def a_monitors(_):
    rc, out = sh(["hyprctl", "-j", "monitors"])
    try:
        ms = json.loads(out)
    except ValueError:
        return "couldn't read monitors"
    return "%d monitor(s): " % len(ms) + ", ".join(
        "%s %dx%d%s" % (m["name"], m["width"], m["height"],
                        " (focused)" if m.get("focused") else "") for m in ms)


# ---- full coverage: devices, system, windows, browser, files, media, time ---

# -- devices & hardware --------------------------------------------------------
def _pactl_list(kind):
    """[(name, description, is_default)] for sinks/sources (no monitors)."""
    rc, out = sh(["pactl", "-f", "json", "list", kind])
    rc2, dflt = sh(["pactl", "get-default-" + kind[:-1]])
    try:
        rows = json.loads(out)
    except ValueError:
        return []
    return [(r.get("name", ""), r.get("description", ""), r.get("name") == dflt.strip())
            for r in rows if ".monitor" not in r.get("name", "")]


def a_devices(_):
    """Everything connected: audio, cameras, input, USB, drives, screens, bluetooth."""
    parts = []
    outs = _pactl_list("sinks")
    ins = _pactl_list("sources")
    if outs:
        parts.append("audio outputs: " + "; ".join(d + (" (in use)" if df else "") for _, d, df in outs))
    if ins:
        parts.append("microphones: " + "; ".join(d + (" (in use)" if df else "") for _, d, df in ins))
    cams = sorted(f for f in os.listdir("/dev") if f.startswith("video"))
    if cams:
        parts.append("cameras: " + ", ".join("/dev/" + c for c in cams))
    rc, o = sh(["hyprctl", "-j", "devices"])
    try:
        d = json.loads(o)
        kb = sorted({k["name"] for k in d.get("keyboards", []) if "virtual" not in k["name"]})
        mice = sorted({m["name"] for m in d.get("mice", []) if "virtual" not in m["name"]})
        if kb:
            parts.append("keyboards: " + ", ".join(kb[:6]))
        if mice:
            parts.append("mice/touchpads: " + ", ".join(mice[:6]))
    except (ValueError, KeyError, TypeError):
        pass
    rc, o = sh(["lsusb"])
    usb = [l.split(" ", 6)[-1] for l in o.splitlines()
           if "root hub" not in l.lower() and len(l.split(" ", 6)) > 6]
    if usb:
        parts.append("usb: " + "; ".join(usb[:10]))
    parts.append(a_usb({}))
    parts.append(a_monitors({}))
    rc, o = sh(["bluetoothctl", "devices", "Connected"])
    parts.append("bluetooth connected: " + (", ".join(l.split(" ", 2)[-1] for l in o.splitlines()) or "nothing"))
    return "\n".join(p for p in parts if p)


def _pick_device(kind, want):
    want = (want or "").lower()
    rows = _pactl_list(kind)
    hits = [r for r in rows if want and (want in r[1].lower() or want in r[0].lower())]
    return hits[0] if hits else None


def a_audio_out(args):
    """List outputs, or switch to one: to='headphones' / 'speakers' / 'hdmi'."""
    want = args.get("to") or args.get("what") or ""
    if not want:
        return "outputs: " + "; ".join(d + (" (in use)" if df else "") for _, d, df in _pactl_list("sinks"))
    dev = _pick_device("sinks", want)
    if not dev:
        return "no output matching %r. outputs: %s" % (
            want, "; ".join(d for _, d, _ in _pactl_list("sinks")))
    sh(["pactl", "set-default-sink", dev[0]])
    # move what's already playing too
    rc, o = sh(["pactl", "list", "short", "sink-inputs"])
    for l in o.splitlines():
        sh(["pactl", "move-sink-input", l.split()[0], dev[0]])
    rc, cur = sh(["pactl", "get-default-sink"])
    return ("switched sound to %s -- VERIFIED" % dev[1]) if cur.strip() == dev[0] \
        else "NOT VERIFIED: output is still %s" % cur


def a_mic(args):
    """Microphone: state=mute|unmute|toggle, or to=<name> to switch, or list."""
    st = (args.get("state") or "").lower()
    want = args.get("to") or ""
    if want:
        dev = _pick_device("sources", want)
        if not dev:
            return "no microphone matching %r" % want
        sh(["pactl", "set-default-source", dev[0]])
        return "switched microphone to %s" % dev[1]
    if st in ("mute", "unmute", "toggle", "on", "off"):
        val = {"mute": "1", "off": "1", "unmute": "0", "on": "0"}.get(st, "toggle")
        sh(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", val])
        rc, o = sh(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
        return "microphone is now %s -- VERIFIED" % ("muted" if "yes" in o else "on")
    rc, o = sh(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
    return "microphones: %s; default is %s" % (
        "; ".join(d + (" (in use)" if df else "") for _, d, df in _pactl_list("sources")),
        "muted" if "yes" in o else "on")


# -- system ----------------------------------------------------------------------
def a_disk(args):
    p = args.get("path") or args.get("what")
    if p:
        path = resolve_path(p)
        rc, o = sh(["du", "-sh", path], timeout=60)
        return ("%s is %s" % (path, o.split()[0])) if rc == 0 and o else "couldn't measure " + path
    rc, o = sh(["df", "-h", "--output=target,size,used,avail,pcent", "/", os.path.expanduser("~")])
    return "disk space:\n" + o


def a_temps(_):
    rc, o = sh(["sensors"])
    keep = [l.strip() for l in o.splitlines()
            if re.search(r"(Package|Core \d|Tctl|temp1|fan\d|Composite|edge)", l)]
    return "\n".join(keep[:12]) or "no temperature sensors found"


def a_network(args):
    """IP addresses, wifi, and whether the internet actually works."""
    rc, o = sh(["ip", "-brief", "-4", "addr"])
    ips = [l for l in o.splitlines() if not l.startswith("lo")]
    rc, gw = sh(["ip", "route", "show", "default"])
    rc, p = sh(["ping", "-c", "3", "-W", "2", "1.1.1.1"], timeout=15)
    m = re.search(r"= [\d.]+/([\d.]+)/", p or "")
    rc2, d = sh(["ping", "-c", "1", "-W", "2", "google.com"], timeout=10)
    return "\n".join([
        "addresses: " + ("; ".join(ips) or "none"),
        "router: " + (gw.split()[2] if gw.split()[2:3] else "none"),
        "internet: " + (("working, %s ms" % m.group(1)) if m else "NOT reachable"),
        "dns: " + ("working" if rc2 == 0 else "NOT resolving names"),
        a_wifi({})])


def a_updates(_):
    rc, o = sh(["checkupdates"], timeout=90)
    n = len([l for l in o.splitlines() if l.strip()]) if rc in (0, 2) else None
    rc2, a = sh(["yay", "-Qua"], timeout=60) if shutil.which("yay") else (1, "")
    na = len([l for l in a.splitlines() if l.strip()]) if rc2 == 0 else 0
    if n is None:
        return "couldn't check for updates (offline?)"
    return ("%d system update(s) and %d AUR update(s) available. To install, the user "
            "runs: sudo pacman -Syu  (or press Super+U for rice updates)" % (n, na)) \
        if (n or na) else "everything is up to date"


def a_installed(args):
    """Is <app> installed? (or list installed apps)"""
    q = (args.get("q") or args.get("what") or "").strip().lower()
    names = set()
    for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".desktop"):
                    try:
                        with open(os.path.join(d, f), encoding="utf-8", errors="ignore") as fh:
                            m = re.search(r"^Name=(.+)$", fh.read(), re.M)
                        if m:
                            names.add(m.group(1).strip())
                    except OSError:
                        pass
    if not q:
        return "%d apps: %s" % (len(names), ", ".join(sorted(names, key=str.lower)[:120]))
    hit = sorted(n for n in names if q in n.lower())
    binp = shutil.which(q)
    rc, pk = sh(["pacman", "-Qq", q])
    if hit or binp or rc == 0:
        return "yes: %s" % (", ".join(hit[:8]) or binp or q)
    rc, s_ = sh(["pacman", "-Ss", "^%s$|^%s-" % (re.escape(q), re.escape(q))], timeout=30)
    found = s_.splitlines()[:2]
    return ("not installed. available in the repos: %s -- the user installs it with: "
            "sudo pacman -S %s" % (" / ".join(found), found[0].split("/")[-1].split()[0])) \
        if found else "not installed (not in the official repos -- maybe the AUR: yay -S %s)" % q


# -- windows ---------------------------------------------------------------------
def a_minimize(args):
    w = target_window(args)
    if not w:
        return "no app window to minimize"
    hypr("movetoworkspacesilent", "special:minimized,address:" + w["address"])
    return "minimized %s (say restore to bring it back)" % _label(w)


def a_unminimize(args):
    rc, o = sh(["hyprctl", "-j", "clients"])
    try:
        mins = [w for w in json.loads(o)
                if (w.get("workspace") or {}).get("name") == "special:minimized"]
    except ValueError:
        mins = []
    q = (args.get("what") or "").lower()
    if q:
        mins = [w for w in mins if q in (w.get("class", "") + w.get("title", "")).lower()]
    if not mins:
        return "nothing is minimized"
    cur = json.loads(sh(["hyprctl", "-j", "activeworkspace"])[1]).get("id", 1)
    for w in mins:
        hypr("movetoworkspacesilent", "%s,address:%s" % (cur, w["address"]))
    return "restored " + ", ".join(_label(w) for w in mins)


def a_layout(args):
    what = (args.get("what") or "split").lower()
    if "monitor" in what or "screen" in what:
        w, err = _focus_target(args)
        if err:
            return err
        hypr("movewindow", "mon:+1")
        return "moved %s to the next monitor" % _label(w)
    w, err = _focus_target(args)
    if err:
        return err
    hypr("togglesplit")
    return "toggled split direction"


# -- browser (keyboard on the browser window) ---------------------------------
_BROWSER_KEYS = {
    "new_tab": ("ctrl", "t"), "reopen_tab": ("ctrl+shift", "t"), "reload": ("ctrl", "r"),
    "back": ("alt", "Left"), "forward": ("alt", "Right"), "next_tab": ("ctrl", "Tab"),
    "prev_tab": ("ctrl+shift", "Tab"), "zoom_in": ("ctrl", "plus"), "zoom_out": ("ctrl", "minus"),
    "zoom_reset": ("ctrl", "0"), "private": ("ctrl+shift", "n"), "bookmark": ("ctrl", "d"),
    "find": ("ctrl", "f"), "new_window": ("ctrl", "n"), "address": ("ctrl", "l"),
}


def _browser_window(args):
    w = target_window({"what": args.get("browser") or "browser"})
    if not w:
        return None, "no browser window is open"
    sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
    time.sleep(0.2)
    return w, None


def _press(mods, key):
    argv = []
    for m_ in mods.split("+"):
        argv += ["-M", m_]
    argv += ["-k", key]
    for m_ in reversed(mods.split("+")):
        argv += ["-m", m_]
    wkeys(*argv)


def a_browser(args):
    """Browser controls: action=new_tab|reopen_tab|reload|back|forward|next_tab|
    prev_tab|zoom_in|zoom_out|zoom_reset|private|bookmark|find (text=)|tab (q=)|tabs"""
    act = (args.get("action") or args.get("what") or "").lower().replace(" ", "_")
    if args.get("profile") or USER_PROFILE or act in ("new_window", "private"):
        # a tab in a SPECIFIC account can't be a Ctrl+T in whatever window is in
        # front -- launch it in that profile instead
        prof = resolve_profile(args.get("profile")) if args.get("profile") else None
        if args.get("profile") and not prof:
            return "no Chrome profile matching %r. profiles: %s" % (
                args.get("profile"), ", ".join(p["name"] for p in chrome_profiles()))
        if act in ("new_tab", "new_window", "private", "") or prof:
            if act == "private":
                b = preferred_browser() or "google-chrome-stable"
                sh([b, "--incognito"] if "chrom" in b else [b, "--private-window"], detach=True)
                return "opened a private window"
            return open_url("chrome://newtab/", prof)
    w, err = _browser_window(args)
    if err:
        return err
    if act in ("tabs", "list_tabs", "list"):
        seen, order = set(), []
        for _ in range(40):
            t = next((c.get("title") for c in _clients() if c["address"] == w["address"]), "")
            if t in seen:
                break
            seen.add(t); order.append(re.sub(r"\s*-\s*Google Chrome.*$|\s*—\s*Mozilla Firefox$", "", t))
            _press("ctrl", "Tab"); time.sleep(0.2)
        return "%d tab(s): %s" % (len(order), " | ".join(order))
    if act in ("tab", "switch_tab", "go_to_tab"):
        q = (args.get("q") or args.get("title") or "").lower()
        seen = set()
        for _ in range(40):
            t = next((c.get("title") for c in _clients() if c["address"] == w["address"]), "")
            if q and q in t.lower():
                return "switched to the tab %r -- VERIFIED" % t[:60]
            if t in seen:
                break
            seen.add(t)
            _press("ctrl", "Tab"); time.sleep(0.2)
        return "no tab with %r in its title" % q
    if act not in _BROWSER_KEYS:
        return "browser action? one of: " + ", ".join(sorted(_BROWSER_KEYS) + ["tab", "tabs"])
    mods, key = _BROWSER_KEYS[act]
    _press(mods, key)
    if act == "find" and args.get("text"):
        time.sleep(0.2)
        put_text(args["text"])
    return "%s in %s" % (act.replace("_", " "), _label(w))


# -- files -------------------------------------------------------------------------
def a_file_info(args):
    p = resolve_path(args.get("path") or args.get("what"))
    if not os.path.exists(p):
        return "no such file: " + p
    st = os.stat(p)
    kind = "folder" if os.path.isdir(p) else (sh(["file", "-b", p])[1] or "file")
    size = sh(["du", "-sh", p], timeout=30)[1].split()[0] if os.path.isdir(p) else "%d bytes" % st.st_size
    return "%s: %s, %s, modified %s" % (p, kind[:60], size,
                                        time.strftime("%d %b %Y %H:%M", time.localtime(st.st_mtime)))


def a_recent(args):
    days = max(1, min(30, _int(args.get("days"), 1)))
    where = resolve_path(args.get("where") or "")
    if shutil.which("fd"):
        rc, o = sh(["fd", "--type", "f", "--changed-within", "%dd" % days, "--max-results", "25",
                    "--exclude", ".cache", "--exclude", ".local", ".", where], timeout=30)
    else:
        rc, o = sh(["find", where, "-type", "f", "-mtime", "-%d" % days, "-not", "-path", "*/.*"], timeout=30)
        o = "\n".join(o.splitlines()[:25])
    return ("files changed in the last %d day(s):\n%s" % (days, o)) if o else "nothing changed recently"


def a_write_file(args):
    """Create a text file (path=, text=). Overwriting an existing file asks first."""
    p = resolve_path(args.get("path") or "")
    err = _safe_path(p)
    if err:
        return err
    text = args.get("text") or ""
    if os.path.exists(p) and not str(args.get("append", "")).lower() in ("1", "true", "yes"):
        stop = needs_confirm("write_file", args, "overwrite %s" % p)
        if stop:
            return stop
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a" if str(args.get("append", "")).lower() in ("1", "true", "yes") else "w",
              encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return "wrote %d chars to %s -- VERIFIED" % (len(text), p)


def _notes_path():
    return os.path.join(xdg_dir("documents") or os.path.expanduser("~"), "tar-notes.md")


def a_note(args):
    text = (args.get("text") or args.get("what") or "").strip()
    if not text:
        return "note what?"
    p = _notes_path()
    with open(p, "a", encoding="utf-8") as f:
        f.write("- %s  _(%s)_\n" % (text, time.strftime("%d %b %H:%M")))
    return "noted -- saved to %s" % p


def a_notes(_):
    try:
        with open(_notes_path(), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
    except OSError:
        return "no notes yet"
    return "\n".join(lines[-20:]) or "no notes yet"


def a_archive(args):
    """zip (path=folder/file, to=name.zip) or unzip (path=archive, to=folder)."""
    act = (args.get("action") or "").lower()
    src = resolve_path(args.get("path") or "")
    if not os.path.exists(src):
        return "no such file: " + src
    if act in ("unzip", "extract") or re.search(r"\.(zip|tar|tgz|gz|xz|zst|7z|rar)$", src, re.I) and act != "zip":
        dest = resolve_path(args.get("to") or re.sub(r"\.(zip|tar(\.\w+)?|tgz|7z|rar)$", "", src, flags=re.I))
        err = _safe_path(dest)
        if err:
            return err
        os.makedirs(dest, exist_ok=True)
        rc, o = sh(["bsdtar", "-xf", src, "-C", dest], timeout=300)
        return ("extracted into %s -- VERIFIED" % dest) if rc == 0 else "extract failed: " + o[:150]
    dest = resolve_path(args.get("to") or (src.rstrip("/") + ".zip"))
    err = _safe_path(dest)
    if err:
        return err
    if os.path.exists(dest):
        return "refusing to overwrite " + dest
    rc, o = sh(["bsdtar", "-a", "-cf", dest, "-C", os.path.dirname(src), os.path.basename(src)], timeout=300)
    return ("created %s -- VERIFIED" % dest) if rc == 0 and os.path.exists(dest) else "zip failed: " + o[:150]


def a_empty_trash(args):
    stop = needs_confirm("empty_trash", args, "PERMANENTLY delete everything in the trash")
    if stop:
        return stop
    rc, o = sh(["trash-empty", "-f"]) if shutil.which("trash-empty") else (1, "trash-empty missing")
    return "trash emptied -- VERIFIED" if rc == 0 else "couldn't empty the trash: " + o[:100]


def a_open_with(args):
    p = resolve_path(args.get("path") or "")
    app = (args.get("app") or "").strip()
    if not os.path.exists(p):
        return "no such file: " + p
    exe = (APPS.get(app.lower()) or [app])[0]
    if not shutil.which(exe):
        return "%s isn't installed" % app
    sh([exe, p], detach=True)
    return "opened %s with %s" % (os.path.basename(p), app)


# -- media ---------------------------------------------------------------------------
def a_now_playing(_):
    rc, o = sh(["playerctl", "metadata", "--format",
                "{{status}}: {{artist}} - {{title}} ({{duration(position)}}/{{duration(mpris:length)}}) on {{playerName}}"])
    return o if rc == 0 and o else "nothing is playing"


def a_seek(args):
    s_ = str(args.get("by") or args.get("to") or "10").strip()
    if args.get("to"):
        sh(["playerctl", "position", s_])
    else:
        sh(["playerctl", "position", (s_ if s_[0] in "+-" else "+" + s_)])
    return "now: " + a_now_playing({})


# -- time ---------------------------------------------------------------------------
def a_reminders(args):
    act = (args.get("action") or "list").lower()
    rc, o = sh(["systemctl", "--user", "list-timers", "tar-remind-*", "--no-legend", "--no-pager"])
    rows = [l for l in o.splitlines() if "tar-remind" in l]
    if act in ("cancel", "clear", "delete"):
        for l in rows:
            unit = next((t for t in l.split() if t.startswith("tar-remind") and t.endswith(".timer")), None)
            if unit:
                sh(["systemctl", "--user", "stop", unit])
        return "cancelled %d reminder(s)" % len(rows)
    return ("%d reminder(s):\n" % len(rows) + "\n".join(" ".join(l.split()[:4]) for l in rows)) \
        if rows else "no reminders set"


STOPWATCH = os.path.join(DATA, "stopwatch.json")


def a_stopwatch(args):
    act = (args.get("action") or "status").lower()
    try:
        with open(STOPWATCH, encoding="utf-8") as f:
            sw = json.load(f)
    except (OSError, ValueError):
        sw = {}
    if act == "start":
        json.dump({"start": time.time()}, open(STOPWATCH, "w"))
        return "stopwatch started"
    if not sw.get("start"):
        return "the stopwatch isn't running"
    el = time.time() - sw["start"]
    txt = "%d:%02d" % (el // 60, el % 60)
    if act in ("stop", "reset"):
        os.remove(STOPWATCH)
        return "stopwatch stopped at " + txt
    return "stopwatch: " + txt


# -- desktop ------------------------------------------------------------------------
def a_keyboard(args):
    act = (args.get("action") or "next").lower()
    if act in ("next", "switch", "change", "toggle"):
        sh(["hyprctl", "switchxkblayout", "all", "next"])
    rc, o = sh(["hyprctl", "-j", "devices"])
    try:
        kb = next(k for k in json.loads(o)["keyboards"] if k.get("main"))
        return "keyboard layout: " + kb.get("active_keymap", "?")
    except (ValueError, KeyError, StopIteration, TypeError):
        return "switched keyboard layout"


def a_keep_awake(args):
    mins = max(1, min(600, _int(args.get("minutes"), 60)))
    sh(["systemd-inhibit", "--what=idle:sleep", "--who=T.A.R.", "--why=user asked",
        "sleep", str(mins * 60)], detach=True)
    return "keeping the screen awake for %d min" % mins


def a_snip(args):
    """Screenshot of a region (drag to select) or the focused window, to Pictures."""
    what = (args.get("what") or "region").lower()
    pics = xdg_dir("pictures") or os.path.expanduser("~")
    out = os.path.join(pics, time.strftime("tar-shot-%Y%m%d-%H%M%S.png"))
    if "window" in what:
        w = target_window(args)
        if not w:
            return "no window to capture"
        (x, y), (ww, hh) = w["at"], w["size"]
        rc, o = sh(["grim", "-g", "%d,%d %dx%d" % (x, y, ww, hh), out])
    else:
        rc, g = sh(["slurp"], timeout=60)
        if rc != 0 or not g:
            return "cancelled"
        rc, o = sh(["grim", "-g", g, out])
    if rc == 0 and os.path.exists(out):
        return "saved %s -- VERIFIED" % out
    return "screenshot failed"


# -- communication ------------------------------------------------------------------
def a_email(args):
    """Open a Gmail draft (to=, subject=, body=) -- the user presses send."""
    to = (args.get("to") or "").strip()
    if to and "@" not in to:
        return ("need %s's actual email address -- ask the user for it (never guess "
                "one). Or leave to= empty and they fill it in." % to)
    url = "https://mail.google.com/mail/?view=cm&fs=1&to=%s&su=%s&body=%s" % (
        urlquote(args.get("to") or ""), urlquote(args.get("subject") or ""),
        urlquote(args.get("body") or ""))
    prof = resolve_profile(args.get("profile")) if args.get("profile") else None
    return open_url(url, prof) + " -- draft ready, the user reviews and presses Send"


def a_whatsapp(args):
    """Open a WhatsApp Web chat (phone= with country code, text=) -- user presses send."""
    phone = re.sub(r"[^\d]", "", args.get("phone") or "")
    url = "https://web.whatsapp.com/send?%stext=%s" % (
        ("phone=%s&" % phone) if phone else "", urlquote(args.get("text") or ""))
    return open_url(url) + " -- message filled in, the user presses Send"


# ---- shell: the cloud brain's general-purpose hands -----------------------
# Runs a command and RETURNS its output, so the model can look things up and
# act on anything there is no dedicated action for. Cloud-only (the small local
# model can't be trusted with it) and fenced off from the irreversible stuff.
_SHELL_DENY = [
    (r"(^|[;&|]\s*|\s)(sudo|doas|pkexec|su)\b", "needs root"),
    (r"(?<![\w.-])\\?rm\b|\bunlink\b|-delete\b|shutil\.rmtree|os\.(remove|unlink|rmdir)"
     r"|\brmdir\b|\bsrm\b",
     "deleting is off-limits here -- use the trash action (recoverable)"),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba|z|fi)?sh\b", "piping a download into a shell"),
    (r"(^|[\s/])\.?ssh/|\.gnupg|id_rsa|id_ed25519", "touches SSH/GPG keys"),
    (r"\bpass\s+(show|ls|find|grep|otp|-c)|\bpass\s*$|\.password-store|\bgpg\b.*(-d|--decrypt)|"
     r"secret-tool|kwallet|gnome-keyring|keyring\s+get|Login Data|logins\.json|key4\.db|"
     r"\bbw\s+(get|list)|\bop\s+(item|read)|keepassxc-cli",
     "reads saved passwords/secrets -- never allowed"),
    (r"\b(dd|mkfs|wipefs|fdisk|parted|sgdisk|cryptsetup|mount|umount|swapoff)\b",
     "disk/mount tool"),
    (r"\b(shred|mkfs[\w.]*|wipefs|fdisk|parted|sgdisk|dd)\b", "disk-destroying tool"),
    # system paths: reading is fine (cat /etc/os-release), writing is not
    (r"(?=.*(^|\s|=|[\'\"])/(boot|efi|etc|usr|bin|lib|lib64|sbin|var|root|"
     r"dev(?!/(?:null|stdout|stderr|tty)\b)|sys|proc)\b)"
     r"(?=.*(>(?!>?\s*/dev/(?:null|stdout|stderr)\b)|\btee\b|"
     r"\b(mv|cp|ln|touch|mkdir|rmdir|install|truncate|chmod)\b|sed\s+-i))",
     "writes to a system path"),
    (r"(^|\s)/boot\b|loader/entries", "touches the bootloader"),
    (r"\b(loader\.conf|bootctl|grub|limine|refind|efibootmgr|mkinitcpio|dracut)\b",
     "touches the bootloader"),
    (r"pacman\.d/hooks|zz-[\w-]+\.hook", "touches pacman hooks"),
    (r"\b(pacman|yay|paru)\s+-[A-Za-z]*[SRU]", "installs/removes packages"),
    (r"\bchmod\s+-R|\bchown\b", "recursive permission change"),
    (r":\(\)\s*\{", "fork bomb"),
    (r">\s*/dev/sd|>\s*/dev/nvme", "writes to a raw disk"),
    (r"systemctl\s+(--system\s+)?(disable|mask|stop)\s+(sddm|NetworkManager|systemd)",
     "would break the session"),
    (r"\.local/share/bite-os/(rices|tar)\b", "T.A.R./rice data -- hands off"),
]


# allowed, but only after the user says yes: changes that are hard to undo
_SHELL_CONFIRM = [
    (r"(^|[\s;&|])(mv|cp\s+-[a-z]*f|truncate|shred)\b", "moves/overwrites files"),
    (r"(^|[^>&0-9])>(?!>|&|\s*/dev/null)", "overwrites a file"),
    (r"\bsed\s+(-[a-z]*\s+)*-i", "edits a file in place"),
    (r"\b(kill|pkill|killall)\b", "kills processes"),
    (r"\bgit\s+(reset\s+--hard|clean|push\s+.*(-f|--force)|checkout\s+--|restore|stash\s+drop|branch\s+-D)",
     "discards git work"),
    (r"\bsystemctl\s+(--user\s+)?(stop|disable|mask|restart)", "stops a service"),
    (r"\b(pip|pipx|npm|cargo|go)\s+(install|uninstall|remove)", "installs/removes software"),
    (r"\.config/(hypr|quickshell|caelestia|serpantinum|glitch)|bite-os-distro|BITE-OS",
     "touches the rice / BITE-OS files"),
    (r"\bchmod\b", "changes permissions"),
]


def a_shell(args):
    cmd = (args.get("cmd") or args.get("what") or "").strip()
    if not cmd:
        return "shell what?"
    if os.environ.get("TAR_BACKEND") != "cloud":
        return "shell is only available to the cloud brain"
    for pat, why in _SHELL_DENY:
        if re.search(pat, cmd):
            return ("refused (%s). If the user really wants this, tell them the "
                    "exact command to run themselves: %s" % (why, cmd))
    for pat, why in _SHELL_CONFIRM:
        if re.search(pat, cmd):
            stop = needs_confirm("shell", args, "run `%s` (%s)" % (cmd[:200], why))
            if stop:
                return stop
            break
    try:
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                           timeout=int(args.get("timeout", 30)),
                           cwd=os.path.expanduser("~"),
                           stdin=subprocess.DEVNULL, start_new_session=True)
    except subprocess.TimeoutExpired:
        return ("timed out after 30s -- for GUI apps or anything long-running "
                "use open/run or `hyprctl dispatch exec <cmd>`")
    out = ((r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr else "")).strip()
    emit("ran", cmd=cmd, rc=r.returncode, output=out[:3000])
    if len(out) > 4000:
        out = out[:4000] + "\n...[truncated]"
    return "[exit %d]\n%s" % (r.returncode, out or "(no output)")


def a_run(args):
    """
    Run a command and let the user watch it.

    Distinct from `open`: `open btop` launches the app, `run <anything>` runs a
    command line in a visible terminal so its output is readable and it can be
    killed. Still allowlist-free BUT non-interactive and never as root -- the
    terminal is the sandbox, and you can see exactly what it did.
    """
    cmd = (args.get("cmd") or args.get("what") or "").strip()
    # "run btop inside it" / "in the terminal" -- where, not part of the command
    cmd = re.sub(r"\s+(?:inside|in|on|into)\s+(?:it|there|that|this|the terminal|"
                 r"that terminal|the new terminal|a terminal|kitty)$", "", cmd,
                 flags=re.I).strip()
    if not cmd:
        return "run what?"
    first = cmd.split()[0]
    if first in ("sudo", "doas", "pkexec"):
        return "refusing to run that as root — do it yourself"
    if not shutil.which(first):
        return "%s isn't installed" % first
    term = None
    for t_, flag in (("kitty", "-e"), ("foot", "-e"),
                     ("alacritty", "-e"), ("xterm", "-e")):
        if shutil.which(t_):
            term = [t_, flag]
            break
    if not term:
        rc, out = sh(["bash", "-lc", cmd], timeout=30)
        emit("ran", cmd=cmd, rc=rc, output=out[:3000])
        return out[:200] if out else ("exit %d" % rc)
    sh(term + ["bash", "-lc", cmd + '; echo; echo "[exit $?] press enter"; read _'],
       detach=True)
    return "running: " + cmd


def a_usb(_):
    """Removable drives currently attached."""
    rc, out = sh(["lsblk", "-J", "-o",
                  "NAME,SIZE,TYPE,RM,MOUNTPOINT,LABEL,TRAN"], timeout=15)
    try:
        tree = json.loads(out)
    except ValueError:
        return "could not read block devices"
    found = []

    def walk(nodes):
        for n in nodes:
            if n.get("rm") or n.get("tran") == "usb":
                if n.get("type") in ("disk", "part"):
                    found.append({
                        "name": "/dev/" + n["name"],
                        "size": n.get("size"),
                        "label": n.get("label") or "",
                        "mount": n.get("mountpoint") or "",
                    })
            walk(n.get("children") or [])

    walk(tree.get("blockdevices") or [])
    emit("usb", drives=found)
    if not found:
        return "no removable drives attached"
    return " · ".join("%s %s%s" % (d["label"] or d["name"], d["size"],
                                   " (mounted)" if d["mount"] else "")
                      for d in found)


def a_open_usb(args):
    """Open a removable drive in the file manager, mounting it if needed."""
    rc, out = sh(["lsblk", "-J", "-o", "NAME,RM,MOUNTPOINT,LABEL,TYPE,TRAN"],
                 timeout=15)
    try:
        tree = json.loads(out)
    except ValueError:
        return "could not read block devices"
    want = (args.get("what") or "").strip().lower()
    target = None

    def walk(nodes):
        nonlocal target
        for n in nodes:
            if n.get("type") == "part" and (n.get("rm") or n.get("tran") == "usb"):
                if not want or want in (n.get("label") or "").lower() \
                        or want in n["name"]:
                    target = target or n
            walk(n.get("children") or [])

    walk(tree.get("blockdevices") or [])
    if not target:
        return "no removable partition found"
    mount = target.get("mountpoint")
    if not mount:
        rc, out = sh(["udisksctl", "mount", "-b", "/dev/" + target["name"]],
                     timeout=40)
        m = re.search(r"at (\S+)", out or "")
        mount = m.group(1).rstrip(".") if m else None
        if not mount:
            return "could not mount /dev/%s: %s" % (target["name"], out[:120])
    sh(["xdg-open", mount], detach=True)
    return "opened " + mount


def urlquote(s_):
    import urllib.parse
    return urllib.parse.quote_plus(s_)


# ----------------------------------------------------------------- capabilities

def a_install(args):
    """
    Let the model finish what the settings wall started: once the user has
    picked an engine, actually install it. Delegates to tar_caps.py so there is
    exactly one installer, and streams its lines straight through.
    """
    cap = (args.get("cap") or "").strip()
    engine = (args.get("engine") or "").strip()
    if not cap or not engine:
        return "install needs cap= and engine="
    argv = [sys.executable, os.path.join(HERE, "tar_caps.py"),
            "install", cap, engine]
    for k, flag in (("voice", "--voice"), ("model", "--model")):
        if args.get(k):
            argv += [flag, args[k]]
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except OSError as e:
        return "could not start the installer: %s" % e
    for line in p.stdout:          # already JSON lines; pass them upward
        line = line.strip()
        if line.startswith("{"):
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    p.wait()
    return "install of %s (%s) finished" % (engine, cap)


def a_connect(args):
    cap = (args.get("cap") or "").strip()
    if not cap:
        return "connect needs cap="
    rc, out = sh([sys.executable, os.path.join(HERE, "tar_caps.py"),
                  "connect", cap], timeout=60)
    for line in out.splitlines():
        if line.strip().startswith("{"):
            sys.stdout.write(line.strip() + "\n")
            sys.stdout.flush()
    return "connect %s done" % cap


# --------------------------------------------------------------- self-config
# T.A.R. changing its OWN settings. "set it up so it listens to my mic when it
# opens" should just work, rather than being a thing only a human can do by
# hunting through a pane.

_AUTOSTART_KEYS = {
    "mic": ("mic", ("mic", "microphone", "listening", "listen", "voice input",
                    "hearing")),
    "speak": ("speak", ("speak", "speaking", "talk", "talking", "voice",
                        "speech", "read replies", "reads")),
    "wake": ("wake", ("wake", "wake word", "wakeword", "hey tar")),
    "warm": ("warm", ("warm", "preload", "pre-load", "load the model")),
    "sfx": ("sfx", ("sfx", "sounds", "sound effects", "ui sounds")),
    "greet": ("greet", ("greet", "greeting", "say hello", "hello")),
}


def _caps_cli(args, timeout=30):
    rc, out = sh([sys.executable, os.path.join(HERE, "tar_caps.py")] + args,
                 timeout=timeout)
    line = ""
    for l_ in (out or "").splitlines():
        if l_.strip().startswith("{"):
            line = l_.strip()
    try:
        return rc, json.loads(line).get("v", "")
    except (ValueError, AttributeError):
        return rc, (out or "").strip()[:200]


def a_autostart(args):
    """Enable/disable something T.A.R. does on launch."""
    what = (args.get("what") or args.get("key") or "").strip().lower()
    on = args.get("on")
    if isinstance(on, str):
        on = on.lower() not in ("off", "false", "no", "0", "disable")
    if on is None:
        on = not bool(re.search(r"\b(off|stop|disable|don'?t|never)\b",
                                what, re.I))
    key = None
    for k, (canon, words) in _AUTOSTART_KEYS.items():
        if any(w in what for w in words):
            key = canon
            break
    if not key:
        return ("which startup setting? one of: "
                + ", ".join(sorted(_AUTOSTART_KEYS)))
    rc, msg = _caps_cli(["autostart", key, "on" if on else "off"])
    if rc:
        return "could not change it: " + msg
    emit("settings_changed", key="autostart." + key, value=bool(on))
    return "on launch, %s is now %s" % (key, "on" if on else "off")


def a_start_mode(args):
    mode = (args.get("mode") or args.get("what") or "").strip().lower()
    for m in ("window", "cinematic", "floating"):
        if m in mode:
            rc, msg = _caps_cli(["start-mode", m])
            if rc:
                return "could not change it: " + msg
            emit("settings_changed", key="start_mode", value=m)
            return "window mode set to " + m
    return "pick one: window, cinematic or floating"


def a_speak_toggle(args):
    on = args.get("on")
    if isinstance(on, str):
        on = on.lower() not in ("off", "false", "no", "0")
    if on is None:
        on = True
    rc, msg = _caps_cli(["speak", "on" if on else "off"])
    if rc:
        return "could not change it: " + msg
    emit("settings_changed", key="speak", value=bool(on))
    return "speaking replies " + ("on" if on else "off")


def a_speed(args):
    mode = (args.get("mode") or args.get("what") or "").strip().lower()
    for m in ("fast", "auto", "quality"):
        if m in mode:
            rc, out = sh([sys.executable, os.path.join(HERE, "tar_brain.py"),
                          "speed", m], timeout=20)
            emit("settings_changed", key="speed", value=m)
            return "speed policy: " + m
    return "pick one: fast, auto or quality"


def a_settings(_):
    """Report the current settings in one line each."""
    rc, out = sh([sys.executable, os.path.join(HERE, "tar_caps.py"), "caps"],
                 timeout=30)
    try:
        d = json.loads((out or "").splitlines()[0])
    except (ValueError, IndexError):
        return "could not read settings"
    a = d.get("autostart") or {}
    bits = ["start: " + str(d.get("start_mode")),
            "speak: " + str(d.get("speak")),
            "on launch: " + ", ".join(k for k, v in a.items() if v) or "nothing"]
    emit("settings", autostart=a, start_mode=d.get("start_mode"),
         speak=d.get("speak"))
    return " · ".join(bits)


def a_caps(_):
    rc, out = sh([sys.executable, os.path.join(HERE, "tar_caps.py"), "caps"],
                 timeout=30)
    try:
        d = json.loads(out.splitlines()[0])
    except (ValueError, IndexError):
        return "could not read capabilities"
    bits = []
    for name, c in (d.get("caps") or {}).items():
        bits.append("%s: %s" % (name, "ready" if c.get("ready")
                                else (c.get("wall") or "locked")))
    return " · ".join(bits)


# ----------------------------------------------------------------- ui-only

UI_ACTIONS = {
    # T.A.R. controlling its own window/panel
    "compact":   "shrink T.A.R. to just the orb",
    "fullpanel": "grow T.A.R. to the full chat panel",
    "selfsize":  "resize T.A.R.'s own window (w=, h=)",
    "anim":      "play a named orb animation",
    "glitch":    "fire the glitch burst",
    "rain":      "toggle the matrix rain layer",
    "scan":      "sweep a scanline over the panel",
    "alert":     "flash the orb red",
    "calm":      "settle the orb to idle",
    "expand":    "grow the panel to console view",
    "collapse":  "shrink the panel back to the orb",
    "close":     "close T.A.R. ITSELF -- only when the user says close yourself/tar",
    "newchat":   "start a new, empty chat (old ones stay in CHATS)",
    "setup":     "open T.A.R.'s own SETUP pane",
    "console":   "toggle T.A.R.'s console (models, voice, memory)",
    "chats":     "open T.A.R.'s past chats",
    "selffloat": "make T.A.R.'s OWN window floating (state=on) or a tiled window (state=off)",
    "panel":     "open/close one of T.A.R.'s OWN panels: what=viewer (the preview/'sees' tab "
                 "with photos/camera) | test (setup test bay) | console | tasks | setup | chats "
                 "| all, state=open|close. 'close the preview tab / that panel / em' = THIS.",
    # visual effects (TarFx layer) -- fine to use for fun or to celebrate
    "shock":     "fx: shockwave ring",
    "flash":     "fx: white flash",
    "shake":     "fx: shake the panel",
    "heartbeat": "fx: double pulse",
    "matrix":    "fx: green matrix takeover (text= optional line)",
    "hack":      "fx: hacker hex cascade + ACCESS GRANTED",
    "party":     "fx: colour-cycling party mode",
    "barrelroll": "fx: spin the whole panel 360",
    "selfdestruct": "fx: fake self-destruct countdown (harmless joke)",
    "banner":    "fx: big glitch text across the panel (text=)",
}


# ----------------------------------------------------------------- registry

# name -> (handler, help, pattern list). Patterns are tried in order and the
# FIRST match wins, so put the specific ones first.
ACTIONS = {
    # window
    "fullscreen": (a_fullscreen, "fullscreen the user's last-used app window (what= to name one)",
                   [r"^(?:go )?full ?screen$", r"^maximi[sz]e fully$"]),
    "maximize":   (a_maximize, "maximize but keep the bar",
                   [r"^maximi[sz]e$", r"^fill the screen$"]),
    "float":      (a_float, "toggle floating", [r"^(?:toggle )?float(?:ing)?$"]),
    "center":     (a_center, "center the window", [r"^cent(?:er|re)(?: it)?$"]),
    "close_tab":  (a_close_tab, "close browser TABS whose title contains q= (e.g. q='cookie "
                                "clicker'). Use this for tabs -- NEVER close a browser window "
                                "to get rid of a tab.",
                   [r"^close (?:all )?(?:of )?(?:the |my )?(?P<q>(?!(?:those|these|them|it|that|this|"
                    r"the|my|all|both|new|other|extra)\b).+?) tabs?$"]),
    "closewin":   (a_closewin, "close an app window (a browser window = ALL its tabs; use "
                               "close_tab for tabs): what=kitty/firefox/... or "
                               "what=them for everything YOU opened, "
                               "what=everything for EVERY window except T.A.R. "
                               "(\"close everything but tar\"), or "
                               "omit for the last one you opened. NEVER "
                               "closes T.A.R. (that is the 'close' UI action)",
                   [r"^(?:close|kill|quit) (?:the )?window$",
                    r"^(?:close|kill|quit) (?P<what>it|that|this(?: window)?)$",
                    r"^(?:close|kill|quit) (?P<what>them|those|these|both(?: of them)?|"
                    r"all of them|all of those|everything you opened|what you opened)$",
                    r"^(?:close|kill|quit) (?P<what>everything|all(?: the)? (?:windows|apps)|"
                    r"every window|everything else|all other windows)"
                    r"(?: (?:but|except|besides|other than) (?:you|yourself|tar))?$",
                    r"^(?:close|kill|quit) (?:the |my )?(?P<what>(?!yourself\b|tar\b|"
                    r"the panel\b|panel\b)[\w.-]+(?: [\w.-]+)?)$"]),
    "resize":     (a_resize, "resize to an exact size",
                   [r"^resize (?:it |this |the window )?(?:to )?(?P<w>\d{2,5})\s*[x× ]\s*(?P<h>\d{2,5})$"]),
    "grow":       (a_grow, "grow/shrink by a step",
                   [r"^(?:grow|widen) (?P<dir>left|right|up|down)$"]),
    "move":       (a_move, "move the window",
                   [r"^move (?:it )?(?P<dir>left|right|up|down)$"]),
    "workspace":  (a_workspace, "switch workspace",
                   [r"^(?:go to )?workspace (?P<n>\d{1,2})$"]),
    "sendto":     (a_sendto, "send window to a workspace",
                   [r"^send (?:it |this )?to workspace (?P<n>\d{1,2})$",
                    # "put it on workspace 2" arrives here as "on workspace 2"
                    r"^(?:on|onto|to) workspace (?P<n>\d{1,2})$",
                    r"^move (?:it |this )?to workspace (?P<n>\d{1,2})$"]),
    "pin":        (a_pin, "pin the window", [r"^pin(?: it)?$"]),
    "opacity":    (a_opacity, "set window opacity",
                   [r"^opacity (?P<pct>\d{1,3})%?$"]),
    # NOTE: this sits before "open" on purpose -- "list open windows" must not
    # be read as the open action with what="windows".
    "winlist":    (a_winlist, "list open windows",
                   [r"^(?:list|show|which|what)?\s*(?:open\s+)?windows$",
                    r"^what'?s open$", r"^windows list$"]),
    "focus":      (a_focus, "focus/switch to a window: q=kitty, q=browser (any open "
                            "browser), or omit for the last one used",
                   [r"^(?:go to|press on|click on|switch to|bring up|show me|jump to) "
                    r"(?:my |the )?(?P<q>browser|chrome|firefox|kitty|terminal|files)$",
                    r"^focus (?P<q>.+)$", r"^switch to (?P<q>.+)$"]),

    # --- self-configuration
    "autostart":  (a_autostart,
                   "turn something on/off at launch (what=mic|speak|wake|"
                   "warm|sfx|greet on=true/false)",
                   [r"^(?:when (?:you|it|tar) (?:opens?|starts?|launch(?:es)?)"
                    r"|on (?:launch|startup|start)|at startup)[,\s]+"
                    r"(?:auto(?:matically)?\s+)?(?P<what>.+)$",
                    r"^(?:always|automatically) (?P<what>listen|speak|talk)"
                    r"(?: on (?:launch|startup))?$",
                    r"^start (?:up )?with (?:the )?(?P<what>.+?)"
                    r"(?: (?:on|enabled))?$",
                    r"^(?:add|set) (?:a )?startup .*?(?P<what>mic|speak|wake|"
                    r"warm|sfx|greet|listen\w*|talk\w*).*$"]),
    "start_mode": (a_start_mode, "how T.A.R. opens (window/cinematic/floating)",
                   [r"^(?:set )?(?:window|start) mode (?:to )?(?P<mode>\w+)$",
                    r"^(?:open|start) (?:as|in) (?P<mode>window|floating|"
                    r"cinematic)(?: mode)?$"]),
    "speak_set":  (a_speak_toggle, "turn speaking replies on or off",
                   [r"^(?:stop|don'?t) (?:speak|talk)\w*$",
                    r"^(?:speak|talk|read) (?:your )?replies$"]),
    "speed":      (a_speed, "speed policy: fast | auto | quality",
                   [r"^(?:be|go|use) (?P<mode>fast|quality)(?:er)?$",
                    r"^speed (?:mode )?(?P<mode>fast|auto|quality)$"]),
    "settings":   (a_settings, "report current settings",
                   [r"^(?:what are your |show )?settings$",
                    r"^what'?s (?:your )?(?:setup|config)$"]),

    # removable media -- BEFORE the generic "open", or "open the usb" is read
    # as an app named "the usb"
    "open_usb":   (a_open_usb, "mount and open a removable drive",
                   [r"^open (?:the )?usb(?:\s+(?P<what>\S+))?$",
                    r"^open (?:the )?(?:flash |thumb )?drive$"]),

    # launch
    "open":       (a_open, "open an app, file, url or known site (profile=<chrome profile> "
                          "for a specific Chrome account)",
                   [r"^(?:open|launch|start|run) (?P<what>.+)$"]),

    # code
    "edit":       (a_edit, "open a file in the editor",
                   [r"^edit (?P<path>\S+)$"]),
    "read":       (a_read, "read a file into the console",
                   # NOT bare pronouns: "show me" is the show action, and a
                   # file called "me" does not exist.
                   [r"^(?:read|cat) (?P<path>\S+)$",
                    r"^show (?!me$|it$|that$|this$|them$)(?P<path>\S+)$"]),
    "grep":       (a_grep, "search the config tree",
                   [r"^(?:grep|search) (?:for )?(?P<q>.+?)(?: in (?P<path>\S+))?$"]),
    "patch":      (a_patch, "overwrite a file, keeping a backup", []),

    # media / system
    "volume":     (a_volume, "set or nudge volume",
                   [r"^volume (?P<pct>\d{1,3})%?$",
                    r"^(?:set )?(?:the )?volume (?:to |at )(?P<pct>\d{1,3})%?$",
                    r"^(?:volume|turn it) (?P<_up>up)$",
                    r"^(?:volume|turn it) (?P<_down>down)$",
                    r"^turn (?:the )?(?:volume|sound|music) (?P<_up>up)$",
                    r"^turn (?:the )?(?:volume|sound|music) (?P<_down>down)$",
                    r"^(?:louder)$(?P<_up>)", r"^(?:quieter|softer)$(?P<_down>)",
                    r"^(?P<mute>mute|unmute)$"]),
    "brightness": (a_bright, "set or nudge brightness",
                   [r"^brightness (?P<pct>\d{1,3})%?$",
                    r"^(?:screen )?(?P<_up>brighter)$",
                    r"^(?:screen )?(?P<_down>dimmer)$"]),
    "media":      (a_media, "control the player",
                   [r"^(?P<what>play|pause|next|prev|stop)$",
                    r"^skip$",
                    r"^(?P<what>next|prev)(?:ious)? (?:song|track)$",
                    r"^skip (?:this |the )?(?:song|track)$",
                    r"^(?P<what>resume|unpause)(?: (?:the )?(?:music|song))?$",
                    r"^(?P<what>pause|play) (?:the )?(?:music|song)$"]),
    "screenshot": (a_screenshot, "take a screenshot",
                   [r"^(?:take |grab )?(?:a )?screen ?shot$", r"^grab the screen$"]),
    "lock":       (a_lock, "lock the session", [r"^lock(?: (?:it|the screen))?$"]),
    "wallpaper":  (a_wallpaper, "reshuffle the wallpaper",
                   [r"^(?:new |change )?wallpaper$"]),
    "sysinfo":    (a_sysinfo, "uptime and memory",
                   [r"^(?:sys(?:tem)? ?info|status|uptime)$"]),

    # camera
    "cam_view":   (a_cam_view, "live camera inside T.A.R.'s viewer (same as camera_live; window=1 "
                               "only if the user asks for a separate app window)",
                   [r"^(?:open |show )?(?:the )?cam(?:era)?$",
                    r"^let me see$", r"^eyes on$"]),
    "cam_snap":   (a_cam_snap, "grab a still from the camera",
                   [r"^(?:cam(?:era)? )?snap(?:shot)?(?: a (?:photo|pic(?:ture)?|shot))?$",
                    r"^(?:take|grab) a (?:photo|pic(?:ture)?|shot|selfie)$"]),
    "show":       (a_show, "display the last capture, or a given image",
                   [r"^show(?: me| it| that| this)?$",
                    r"^show me (?P<path>\S+\.(?:jpe?g|png))$",
                    r"^(?:display|view) (?:it|that|the (?:photo|picture|shot))$",
                    r"^let me see (?:it|that|the (?:photo|picture))$"]),
    "cam_off":    (a_cam_off, "close the camera window",
                   [r"^cam(?:era)? off$", r"^eyes off$"]),

    # capabilities -- how T.A.R. unlocks its own missing engines
    "install":    (a_install, "install an engine for a capability "
                              "(cap=tts|stt|vision|camera|wakeword engine=NAME)",
                   []),
    "connect":    (a_connect, "wire up an already-installed engine (cap=NAME)",
                   []),
    # --- real work
    "notify":     (a_notify, "desktop notification (text=, title=)", []),
    "remind":     (a_remind, "reminder/timer/alarm: minutes=/seconds=/hours= or in='10m' or "
                           "at='18:30', text=",
                   [r"^remind me in (?P<in>\d+\s*(?:m|min|minutes?|s|sec|seconds?|h|hours?)) (?:to |that |about )?(?P<text>.+)$",
                    r"^(?:set )?(?:a )?timer (?:for )?(?P<in>\d+\s*(?:m|min|minutes?|s|sec|seconds?|h|hours?))$"]),
    "wifi":       (a_wifi, "wifi: state=on|off|status|list, or state=connect ssid= password=",
                   [r"^(?:turn )?wi-?fi (?P<state>on|off)$", r"^turn (?P<state>on|off) (?:the )?wi-?fi$",
                    r"^(?:what wi-?fi am i on|wi-?fi status)$"]),
    "bluetooth":  (a_bluetooth, "bluetooth: state=on|off|status|devices|connect|disconnect, device=",
                   [r"^(?:turn )?bluetooth (?P<state>on|off)$", r"^turn (?P<state>on|off) (?:the )?bluetooth$"]),
    "battery":    (a_battery, "battery level and time left",
                   [r"^(?:battery|how much battery(?: do i have)?(?: left)?|battery (?:level|status))$"]),
    "research":   (a_research, "look something up properly: searches the web, reads the top "
                               "pages and returns a short answer with source links (q=question). "
                               "Use this for facts/questions; the user already sees the answer, "
                               "so just relay it briefly with the links.",
                   [r"^(?:please |can you |could you )?(?:look up|research|find out(?: about)?|"
                    r"look into|what does the (?:web|internet) say about) (?!online )(?P<q>.+)$"]),
    "virus_scan": (a_scan, "check files/folders/a USB drive for malware (path=file, folder, or "
                           "'usb'). Static analysis of what each file is BUILT to do (imports, "
                           "commands, MITRE ATT&CK techniques) -- nothing is run. NEVER judge a "
                           "file as safe or malware by its NAME; call this and relay its report.",
                   [r"^(?:scan|virus ?scan|malware ?scan|check) (?:the |my |this |that )?(?P<path>.+?) "
                    r"for (?:any )?(?:viruses|virus|malware|ransomware|anything bad|threats)\??$",
                    r"^(?:virus ?scan|malware ?scan|scan) (?!(?:(?:the|my|this|that) )?(?:room|area|"
                    r"face|qr|code on|screen|page|document|paper|homework|me\b|camera)\b)"
                    r"(?:the |my |this |that )?(?P<path>.+?)\??$",
                    r"^(?:is|are) (?:the |my |this |that )?(?P<path>.+?) (?:safe|a virus|malware|"
                    r"ransomware|dangerous|infected|malicious)\??$",
                    r"^(?:does|do) (?:the |my |this |that )?(?P<path>.+?) (?:contain|have) (?:any )?"
                    r"(?:viruses|a virus|virus|malware|ransomware)\??$"]),
    "play_game":  (a_play_game, "play a web game T.A.R. knows (cookie clicker) on its own in the "
                                "background: opens/finds it, autoclicks and buys upgrades. game=, "
                                "minutes= (default 20). Use this instead of keep_doing for these.",
                   [r"^(?:(?:can|could) you |please |go |now )?(?:play|keep playing|start playing|"
                    r"continue playing|go play) (?:the game |game )?(?P<game>cookie ?clicker(?: game)?|"
                    r"the cookie game|it|that|the game)(?: for (?P<minutes>\d+) ?min(?:ute)?s?)?"
                    r"(?: for me)?(?: please)?$",
                    r"^(?:keep playing|continue playing|play for me)$"]),
    "weather":    (a_weather, "current weather (place= optional)",
                   [r"^(?:what'?s the )?weather(?: like)?(?: in (?P<place>.+))?$"]),
    "datetime":   (a_datetime, "current date and time",
                   [r"^what(?:'s| is)? the (?:time|date)$", r"^what time is it$", r"^what day is it$"]),
    "calc":       (a_calc, "calculate/convert: expr= (e.g. '15% of 80', '5 usd to ils')",
                   [r"^(?:calc|calculate) (?P<expr>.+)$"]),
    "files":      (a_files, "list a folder: path= (downloads, documents, desktop, ~/x...)", []),
    "find":       (a_find, "find files by name: q=, where=", []),
    "move":       (a_move, "move a file/folder: src=, dst=", []),
    "copy":       (a_copy, "copy a file/folder: src=, dst=", []),
    "rename":     (a_rename, "rename: path=, to=", []),
    "mkdir":      (a_mkdir, "create a folder: path=", []),
    "trash":      (a_trash, "delete a file/folder (to the trash, restorable): path=", []),
    "restore":    (a_restore, "restore from the trash: name=", []),
    "folder":     (a_folder, "open a folder in the file manager: path=", []),
    "download":   (a_download, "download a url: url=, where= (default downloads)", []),
    "kill":       (a_kill, "force-quit an app/process by name: name=",
                   [r"^(?:kill|force quit|force-quit) (?P<name>[\w.-]+)$"]),
    "processes":  (a_processes, "top processes by cpu (by=mem for memory)",
                   [r"^(?:what'?s using (?:my )?(?:cpu|ram|memory)|top processes)$"]),
    "power":      (a_power, "action=suspend|lock|logout|reboot|shutdown",
                   [r"^(?P<action>suspend|reboot|shutdown|log ?out)(?: the (?:pc|computer))?$"]),
    "dnd":        (a_dnd, "do not disturb: state=on|off|toggle",
                   [r"^(?:turn )?(?:do not disturb|dnd) (?P<state>on|off)$"]),
    "nightlight": (a_nightlight, "night light: state=on|off, temp= (kelvin)",
                   [r"^(?:turn )?night ?light (?P<state>on|off)$", r"^turn (?P<state>on|off) (?:the )?night ?light$"]),
    "colorpick":  (a_colorpick, "pick a colour from the screen", [r"^(?:pick a colou?r|colou?r picker)$"]),
    "record":     (a_record, "screen recording: state=start|stop",
                   [r"^(?P<state>start|stop) recording(?: the screen)?$", r"^record (?:my |the )?screen$"]),
    "cliphist":   (a_cliphist, "recent clipboard history", [r"^clipboard history$"]),
    "keep_doing": (a_keep_doing, "run a task in the BACKGROUND, round after round, until the "
                                 "user says stop. task=the DECISIONS to make each round (e.g. "
                                 "'buy the best upgrade/building you can afford in the store'). "
                                 "autoclick=<thing to click NON-STOP between rounds, e.g. 'the big "
                                 "cookie'> -- for clicker/idle games ALWAYS set it: a fast local "
                                 "clicker (rate= clicks/sec, default 8) does the repetitive "
                                 "clicking, the AI round only decides. every=seconds between AI "
                                 "rounds, minutes=time limit (default 20). Use for 'keep doing', "
                                 "'until I stop you', 'play this for me'.", []),
    "stop_task":  (a_stop_task, "stop the background task", []),
    "pause_task": (a_pause_task, "pause the background task (resume_task continues it)", []),
    "resume_task": (a_resume_task, "resume a paused background task", []),
    "coin":       (a_coin, "flip a coin", [r"^(?:flip a coin|coin ?flip|heads or tails)$"]),
    "dice":       (a_dice, "roll a die: sides= (default 6)",
                   [r"^roll (?:a )?(?:die|dice)$", r"^roll (?:a )?d(?P<sides>\d{1,4})$"]),
    "play":       (a_play, "play MUSIC or a VIDEO to watch on youtube: q=. ONLY for "
                         "songs/videos -- websites and web games (e.g. cookie clicker) "
                         "go through open/web instead. ALREADY starts the top video -- "
                         "don't click afterwards.",
                   [r"^play (?P<q>.+) on youtube$"]),
    "mouse_test": (a_mouse_test, "(setup test) wiggle the pointer to prove mouse control works", []),
    "evil":       (a_evil, "EVIL MODE on/off (state=on|off): red UI, villain persona, missile easter egg",
                   [r"^(?:turn on |activate |enable |engage |go into |switch to )?evil mode(?: on)?$",
                    r"^go evil$", r"^(?:turn |switch )?evil mode (?P<state>off)$",
                    r"^(?P<state>turn off|deactivate|disable|exit|leave) (?:the )?evil mode$",
                    r"^(?:be )?(?P<state>normal) (?:again|mode)$"]),
    "missiles":   (a_missiles, "EVIL MODE easter egg: SIMULATED missile strike animation on a "
                               "world map (target=<city>); no target = open the missile tab",
                   [r"^(?:send|launch|fire) (?:the |some |a )?(?:missiles?|missels?|nukes?|a nuke|warheads?) "
                    r"(?:to|at|on) (?P<target>.+)$",
                    r"^nuke (?P<target>.+)$",
                    r"^(?:open |show )?(?:the )?(?:missiles?|missels?|nukes?|launch) (?:tab|panel|control|command)$"]),
    "fill_table": (a_fill_table, "HOMEWORK / QUESTION SHEETS / TABLES: reads the questions on screen "
                                 "(table cells, 'answer:' lines, blanks), answers them in their language "
                                 "and types each answer in the right spot. q= ONLY the user's own extra "
                                 "instructions (or nothing). USE THIS for 'answer the questions'.",
                   [r"^(?:fill in|fill out|fill|answer|complete|do|solve) (?:the |my )?(?:table|questions|question sheet|questions sheet|homework|worksheet|sheet)(?: (?:in|on) (?:the |my )?(?:doc|docs|document|screen|page))?(?P<q> in \w+)?$"]),
    "fill_cells": (a_fill_cells, "fill a TABLE or FORM: start=<first cell to fill> texts=<v1 || v2 || ...> "
                                 "(nav=tab default; Tab goes left->right then next row; '' skips a cell "
                                 "that already has text). Use this instead of clicking each cell.", []),
    "selfie":     (a_selfie, "take a webcam photo of the user, optionally after a countdown (delay=seconds) "
                             "-- for 'take a selfie (in 5 seconds)'. Never answer a photo request with an "
                             "old screenshot.",
                   [r"^(?:take|snap|grab) (?:a |me a |my )?(?:selfie|photo|picture|pic)(?: of me)?"
                    r"(?: in (?P<delay>\d+) ?(?:s|sec|secs|seconds?))?$",
                    r"^selfie(?: in (?P<delay>\d+) ?(?:s|sec|secs|seconds?))?$"]),
    "camera_live": (a_camera_live, "show the webcam LIVE inside T.A.R. (state=on|off) -- for "
                                   "'show me the camera', 'watch me', 'can you see me'. No app opens.",
                    [r"^(?:show (?:me )?(?:the )?(?:camera|webcam)|watch me|camera on|turn on the camera)$",
                     r"^(?:camera|webcam) (?P<state>off)$",
                     r"^(?P<state>close|stop|turn off|hide) (?:the |my )?(?:live )?(?:camera|webcam|cam)(?: feed| view| panel| stream)?$",
                     r"^(?:show (?:me )?)?(?:my |the )?(?:live )?(?:camera|webcam) (?:feed|view|stream)$"]),
    "camera_look": (a_camera_look, "take a WEBCAM photo and answer about it (q=) -- for anything "
                                   "about the user or the room: 'am I smiling', 'what am I "
                                   "holding', 'how do I look'. NOT look (that's the screen).",
                   [r"^(?:what do you see|look at me|how do i look)$"]),
    "look":       (a_look, "LOOK at the SCREEN (or an image file with path=) and answer a "
                           "question about it (q=). "
                           "Use before clicking if unsure what's there.", []),
    "click_on":   (a_click_on, "CLICK (single/double/right -- it CANNOT press-and-hold) "
                               "something visible on screen by description: "
                               "target='the subscribe button' (button=right, double=true, "
                               "times=N to click it N times, fast=true for rapid clicks, all "
                               "optional). Waits a few "
                               "seconds for a page that's still loading. "
                               "Finds it by text, else by vision.", []),
    "click":      (a_click, "click at exact screen pixels x=, y= (button=, double=)", []),
    "scroll":     (a_scroll, "scroll a window: what=chrome, dir=down|up|left|right, "
                             "amount=notches 1-15 (default 5 = about half a page)",
                   [r"^scroll (?P<dir>down|up)(?: (?:on|in) (?P<what>\w+))?$"]),
    "type_into":  (a_type_into, "click a field by description then type: target='the search box', text=", []),
    "ask_claude": (a_ask_claude, "ask Claude (Anthropic's AI, via Claude Code) a question: q=. "
                                 "Use when the user says 'ask claude'.",
                   [r"^ask claude (?:about |to |that |this )?(?P<q>.+)$"]),
    "monitors":   (a_monitors, "list connected monitors", [r"^(?:what|which|how many) monitors?.*$"]),
    "confirm":    (a_confirm, "(user-only) run the action waiting for confirmation", []),
    "cancel":     (a_cancel, "(user-only) drop the action waiting for confirmation", []),
    "devices":    (a_devices, "everything connected: sound outputs, mics, cameras, keyboards, mice, "
                            "usb, drives, screens, bluetooth",
                   [r"^(?:connected )?devices$", r"^what'?s (?:connected|plugged in)$",
                    r"^what devices are (?:connected|plugged in)$"]),
    "audio_out":  (a_audio_out, "list sound outputs, or switch with to=headphones/speakers/hdmi", []),
    "mic":        (a_mic, "microphone: state=mute|unmute|toggle, to=<name> to switch, or list",
                   [r"^(?P<state>mute|unmute) (?:my |the )?mic(?:rophone)?$"]),
    "disk":       (a_disk, "disk space left, or path= to measure a folder's size",
                   [r"^disk space$", r"^how much (?:disk |storage )?space (?:do i have|is left)$"]),
    "temps":      (a_temps, "CPU / drive temperatures and fans",
                   [r"^(?:cpu )?temps?$", r"^(?:cpu )?temperatures?$"]),
    "network":    (a_network, "IP address, router, whether internet and DNS work, wifi", []),
    "updates":    (a_updates, "check for system/AUR updates (installing needs the user)",
                   [r"^(?:any )?updates\??$", r"^check (?:for )?updates$"]),
    "installed":  (a_installed, "is an app installed (q=), or list installed apps", []),
    "minimize":   (a_minimize, "minimize a window (what=, else the last used)",
                   [r"^minimi[sz]e (?P<what>it|that|this|[\w-]+)$"]),
    "unminimize": (a_unminimize, "bring minimized windows back (what= optional)", []),
    "layout":     (a_layout, "what=split toggles split direction; what=monitor moves the window "
                             "to the next monitor", []),
    "browser":    (a_browser, "browser controls (profile=<account> opens new_tab/new_window IN that "
                              "Chrome account): action=tab q=<title> (switch "
                              "to a tab), tabs (list them), new_tab, reopen_tab, reload, back, "
                              "forward, next_tab, prev_tab, find text=, zoom_in, zoom_out, "
                              "zoom_reset, private, bookmark", []),
    "file_info":  (a_file_info, "size / type / modified date of a file or folder: path=", []),
    "recent":     (a_recent, "files changed recently: days= (default 1), where=", []),
    "write_file": (a_write_file, "create a text file: path=, text= (append=true to add; "
                                 "overwriting asks the user)", []),
    "screen_fx":  (a_screen_fx, "show a full-screen visual effect over the WHOLE screen "
                                "(click-through): effect=<name> -- built-in matrix, snow, glitch, "
                                "shake (shakes the real screen), fireworks, black hole, orbs, "
                                "spinning orb, or any effect you made with "
                                "make_fx. seconds= (default 30). For an effect that doesn't exist "
                                "yet, write it with make_fx.",
                   [r"^(?:(?:can|could) you |please )?(?:make|put|show|start|do|give me|turn on)? ?"
                    r"(?:a |an |the |some )?(?P<effect>matrix(?: rain)?|code rain|snow(?:fall)?|"
                    r"fireworks?|black ?hole(?: orbs)?|spinning orb|"
                    r"(?:screen )?glitch(?:es)?)(?: effect)?(?: on (?:my|the) screen)?"
                    r"(?: for (?P<seconds>\d+) ?(?:s|sec|secs|seconds))?$",
                    r"^make it (?P<effect>snow)(?: on (?:my|the) screen)?$",
                    r"^(?:make |let )?(?:my |the )?screen (?P<effect>shake)$",
                    r"^(?P<effect>shake) (?:my|the) screen$",
                    r"^make (?:my|the) screen (?P<effect>shake)$",
                    r"^(?:show|play|run|do|replay) (?:the |my |that )?(?P<effect>[a-z0-9 _-]+?) "
                    r"effect(?: again)?$",
                    r"^(?:do an? |make an? )?(?P<effect>earthquake)(?: on (?:my|the) screen)?$",
                    r"^make (?:my|the) screen (?P<effect>glitch)(?:y)?$"]),
    "make_fx":    (a_make_fx, "WRITE a new full-screen visual effect and run it: name=, qml= a "
                              "complete QML file: `import QtQuick` (also allowed: QtQuick.Shapes, "
                              "QtQuick.Particles, QtQuick.Effects) and ONE root Item; the host makes "
                              "it fill the screen, so use the root's width/height (they start at 0 "
                              "-- start motion only once width > 0). Keep it light: <= 300 items, "
                              "move things with ONE Timer at ~30 fps, no Canvas full-screen redraws. "
                              "Visuals only: no network, files or programs. To distort the REAL "
                              "screen, declare `property string shot` on the root -- it gets set to "
                              "a screenshot path of that screen (show it with Image, source: "
                              "\"file://\" + shot). instant=true for no fade-in. seconds= (default "
                              "20). If it returns errors, fix the code and call make_fx again.",
                   []),
    "stop_fx":    (a_stop_fx, "stop the screen effect",
                   [r"^(?:stop|end|kill|remove|turn off) (?:the |that )?(?:screen )?(?:effect|matrix|"
                    r"rain|snow|glitch|matrix rain)$"]),
    "remember":   (a_remember, "REMEMBER a fact about the user or how to behave (fact= one clear "
                               "sentence, e.g. 'The user's name is Rephael', 'Always greet the user "
                               "by name'). Goes into your memory, seen every turn. Use this -- NOT "
                               "note/write_file -- whenever they say remember / don't forget / keep "
                               "in mind.",
                   [r"^(?:please |can you |could you )?(?:remember|don'?t forget|keep in mind)"
                    r"(?: that)?[,:]? (?P<fact>.+)$"]),
    "forget":     (a_forget, "forget remembered facts matching fact=",
                   [r"^(?:please )?forget (?:that |about )?(?P<fact>(?!it$|that$|this$|everything$).+)$"]),
    "note":       (a_note, "write a line into the user's notes FILE (Documents/tar-notes.md) -- only "
                           "when they say 'take a note' / 'write down'; facts to remember go to "
                           "remember: text=",
                   [r"^(?:take a )?note[: ]+(?P<text>.+)$"]),
    "notes":      (a_notes, "read back saved notes", [r"^(?:my |show (?:my )?|read (?:my )?)notes$"]),
    "archive":    (a_archive, "zip (action=zip path=, to=) or unzip/extract (path=archive, to=)", []),
    "empty_trash": (a_empty_trash, "permanently empty the trash (asks the user first)", []),
    "open_with":  (a_open_with, "open a file with a specific app: path=, app=", []),
    "now_playing": (a_now_playing, "what song/video is playing",
                    [r"^what'?s playing$", r"^what song is this$", r"^now playing$"]),
    "seek":       (a_seek, "media: by=+10 / -10 seconds, or to=<seconds>", []),
    "reminders":  (a_reminders, "list reminders, or action=cancel to cancel them all",
                   [r"^(?:my |list |show )?reminders$"]),
    "stopwatch":  (a_stopwatch, "stopwatch: action=start|status|stop",
                   [r"^(?P<action>start|stop) (?:the |a )?stopwatch$"]),
    "keyboard":   (a_keyboard, "switch keyboard layout (action=next) or show it (action=show)",
                   [r"^(?:switch|change) (?:the )?(?:keyboard|language|layout)$"]),
    "keep_awake": (a_keep_awake, "stop the screen sleeping for minutes= (default 60)", []),
    "snip":       (a_snip, "screenshot what=region (drag to pick) or what=window, saved to Pictures", []),
    "email":      (a_email, "open a Gmail draft: to=, subject=, body= (user presses Send)", []),
    "whatsapp":   (a_whatsapp, "open a WhatsApp Web chat: phone= (with country code), text= "
                               "(user presses Send)", []),
    "shell":      (a_shell, "run ANY shell command (bash, as the user, in ~) and get its "
                            "output back -- for anything no other action covers: "
                            "files, settings, network, bluetooth, notifications, "
                            "info lookups. Non-interactive, 30s limit. Launch GUI "
                            "apps with open or `hyprctl dispatch exec`. Delete with "
                            "trash-put, never rm. No sudo.", []),
    "run":        (a_run, "run a command in a visible terminal",
                   [r"^(?:run|exec(?:ute)?)\s+(?P<cmd>.+)$"]),
    "type":       (a_type, "type text into the focused window",
                   [r"^type (?P<text>.+)$"]),
    "key":        (a_key, "press a key combo",
                   [r"^press (?P<combo>[\w+\-]+)$",
                    r"^(?:hit|send) (?P<combo>[\w+\-]+)$"]),
    "screen_read": (a_screen_read, "OCR what is on screen",
                   [r"^(?:read|what'?s on) (?:my |the )?screen$",
                    r"^what do you see$", r"^read this screen$"]),
    "clip_get":   (a_clip_get, "read the clipboard",
                   [r"^(?:what'?s|read) (?:in )?(?:the )?clipboard$",
                    r"^paste$"]),
    "clip_set":   (a_clip_set, "put text on the clipboard",
                   [r"^copy (?P<text>.+)$"]),
    "dns":        (a_dns, "DNS lookup",
                   [r"^(?:dns|nslookup|lookup|resolve) (?P<host>\S+)$"]),
    "web":        (a_web, "open a site or search the web (lucky=true opens the first "
                         "result -- use it to open a named site/web game you don't know the URL of; "
                         "profile=<chrome profile name, or 'student'/'school'/'main'> to use "
                         "that Chrome account -- never tell the user to log in instead)",
                   [r"^(?:google|search the web for|look up online)(?: up)? (?P<q>.+)$",
                    r"^(?:go to|browse|visit) (?P<q>\S+)$"]),
    "usb":        (a_usb, "list attached removable drives",
                   [r"^(?:what )?usb(?: drives)?$",
                    r"^(?:any )?drives(?: attached)?$"]),
    "caps":       (a_caps, "report which capabilities are ready or locked",
                   [r"^(?:what can you do|capabilities|caps)$"]),
}

# Phrasings for the UI-only actions. They run in the renderer, but the user
# still says them in plain words, so they need matching too.
UI_PATTERNS = {
    "compact":   [r"^(?:compact|minimi[sz]e|shrink)(?: yourself)?$",
                  r"^(?:go |get )?small(?:er)?$", r"^orb mode$"],
    "fullpanel": [r"^(?:expand|maximi[sz]e yourself|full ?panel)$",
                  r"^(?:go |get )?big(?:ger)?$", r"^chat mode$"],
    "selfsize":  [r"^resize yourself (?:to )?(?P<w>\d{3,5})\s*[x\u00d7 ]\s*(?P<h>\d{3,5})$"],
    "glitch":    [r"^glitch(?: out)?$"],
    "rain":      [r"^(?:toggle )?rain$", r"^matrix$"],
    "scan":      [r"^scan$"],
    "alert":     [r"^alert$"],
    "calm":      [r"^calm(?: down)?$", r"^settle$"],
    "selffloat": [r"^(?P<state>float) yourself$", r"^go (?P<state>floating)$",
                  r"^(?:switch to )?(?P<state>floating) mode$", r"^(?:make yourself|be) (?P<state>floating)$",
                  r"^(?P<state>tile) yourself$", r"^go (?P<state>tiled)$",
                  r"^(?:go |switch to )?(?P<state>window) mode$", r"^(?:make yourself|be) (?:a )?(?P<state>window|tiled)$"],
    "close":     [r"^(?:close|quit|exit|kill|shut ?down|turn off|stop) (?:yourself|tar|t\.?a\.?r\.?|the panel)$",
                  r"^(?:quit|exit|go away|bye tar|goodbye tar)$"],
    # ---- easter eggs (no model needed) ----
    "barrelroll": [r"^do a barrel roll$", r"^barrel roll$", r"^spin$"],
    "selfdestruct": [r"^(?:initiate |activate |start )?self[ -]?destruct(?: sequence)?$"],
    "hack":      [r"^hack (?:the )?(?:mainframe|planet|pentagon|nasa|gibson)$", r"^i'?m in$",
                  r"^hacker ?mode$", r"^enhance$"],
    "matrix":    [r"^(?:enter the matrix|wake up,? neo|follow the white rabbit|red pill)$"],
    "party":     [r"^(?:party|rave|disco)(?: mode| time)?$", r"^let'?s party$"],
    "shake":     [r"^shake(?: it| yourself)?$"],
    "heartbeat": [r"^are you alive$", r"^heartbeat$"],
    "flash":     [r"^lumos$", r"^flashbang$"],
    "shock":     [r"^shockwave$", r"^boom$", r"^kamehameha$"],
    "banner":    [r"^(?P<_hal>open the pod bay doors(?:,? (?:hal|tar))?)$",
                  r"^(?P<_humor>what'?s your humou?r setting)$",
                  r"^(?P<_life>what'?s the meaning of life(?:,? the universe and everything)?)$",
                  r"^(?P<_sandwich>sudo make me a sandwich)$",
                  r"^(?P<_hello>hello there)$",
                  r"^(?P<_ily>i love you)$"],
    "newchat":   [r"^(?:start |open |make )?(?:a )?(?:new|fresh|clean) (?:chat|conversation|session)$",
                  r"^new chat please$", r"^clear (?:the )?chat$"],
}


# Words that never change which action is meant, only how politely it's asked.
# Stripped on the second matching pass so "list my open windows for me" lands on
# the same action as "windows" without needing a pattern per phrasing.
FILLER = re.compile(
    r"\b(?:my|all|of|currently|right now|real quick|rq|just|kindly|"
    r"go ahead and|for me|thanks|pls)\b", re.I)


def _normalize(text):
    t = " ".join((text or "").strip().split())
    t = t.rstrip(".!?").strip()
    low = t.lower()
    # strip a leading wake/politeness prefix
    low = re.sub(r"^(?:hey |ok(?:ay)? )?tar[,:]?\s*", "", low)
    # Leading conversational filler. "thanks can you run btop" used to miss
    # entirely and get handed to the model, which then invented an action.
    low = re.sub(r"^(?:thanks?|thank you|ok(?:ay)?|alright|aight|yo|hey|hi|"
                 r"so|um+|uh+|well|dude|bro)\b[,\s]+", "", low)
    low = re.sub(r"^(?:please\s+|pls\s+|plz\s+|(?:can|could|would|will) (?:you|u|ya)\s+|"
                 r"i want you to\s+|i need you to\s+|i want u to\s+)+", "", low)
    # "run on it btop" / "open it up for me" -- dangling prepositional filler
    low = re.sub(r"^(run|open|launch|start)\s+(?:on|in)\s+it\s+", r"\1 ", low)
    low = re.sub(r"^(run|open|launch|start)\s+up\s+", r"\1 ", low)
    low = re.sub(r"^let'?s\s+", "", low)
    # only strip a leading "go " when it is NOT "go to <somewhere>"
    low = re.sub(r"^go\s+(?!to\b)", "", low)
    # "make this fullscreen" / "set it to fullscreen" -> "fullscreen"
    low = re.sub(r"^(?:make|set|put)\s+(?:this|it|the window)\s+(?:to\s+)?",
                 "", low)
    low = re.sub(r"\s+(?:please|now|for me|thanks|thank you)$", "", low)
    return low.strip()


def _loosen(low):
    """Second pass: drop filler words and a possessive, then re-space."""
    out = FILLER.sub(" ", low)
    out = re.sub(r"\s+", " ", out).strip()
    return out


# Splitters for compound requests. "open chrome and look up X" is TWO actions;
# the matcher used to swallow the whole sentence as one app name and fail.
_SPLIT_RE = re.compile(
    r"\s+(?:and then|then|and also|and|,\s*then|;)\s+", re.I)


WINDOW_ACTIONS = ("closewin", "fullscreen", "maximize", "float", "center",
                  "resize", "grow", "move", "sendto", "pin", "opacity")


_LUCKY_RE = re.compile(
    r"^(?:google|search(?: the web)?(?: for)?|look ?up|find)(?: up)? (?P<q>.+?)"
    r",? and (?:then )?(?:press|click|open|go to|hit)(?: on)? (?:the )?"
    r"(?:first|top|1st) (?:link|result|one)$")
def _is_terminal(what):
    words = re.sub(r"^(?:me|us)\s+", "", (what or "").lower())
    words = set(re.sub(r"^(?:a|an|the|my|new)\s+", "", words).split())
    return bool(words) and words <= {"kitty", "terminal", "term", "foot",
                                     "new", "window"}


def _lucky(norm):
    m = _LUCKY_RE.match(norm)
    return ("web", {"q": m.group("q").strip(), "lucky": "true"}) if m else None


def match_all(text):
    """
    Split a compound request into the actions it actually contains.
    Returns a list of (name, args). Falls back to a single match.
    Returns None when the request must go to the model untouched.
    """
    norm = _normalize(text)
    lucky = _lucky(norm)
    if lucky:
        return [lucky]
    parts = [p for p in _SPLIT_RE.split(norm) if p.strip()]

    if len(parts) < 2:
        whole = match(text)
        return [whole] if whole[0] else []

    # There IS a conjunction. A greedy single match would swallow it whole
    # ("open chrome and lookup test" -> open an app literally named
    # "chrome and lookup test"), so split first and only fall back to the
    # whole-string match if splitting finds nothing.

    BROWSERS = ("chrome", "chromium", "firefox", "browser", "web browser")
    out = []
    carry = None                # the app named earlier in the sentence
    for part in parts:
        part = part.strip()
        n, a = match(part)

        # Context: once a BROWSER is open, "look up X" / "search X" means the
        # web -- not a DNS record and not grep over the config tree.
        if carry in BROWSERS:
            m_ = re.match(r"^(?:look ?up|search(?: for)?|google|find)\s+(.+)$",
                          part, re.I)
            if m_:
                n, a = "web", {"q": m_.group(1).strip().strip('"')}

        if not n and not (carry in BROWSERS):
            # A step the shortcut can't do ("...and then click the cookie 5
            # times"). Doing only the first half is worse than not starting:
            # hand the whole request to the model, which can do every step.
            return None
        if not n and carry in BROWSERS:
            # The tail has no verb of its own ("... and arch wiki"). Send it to
            # the web action DIRECTLY -- routing it back through match() as
            # "search the web for X" just hit the grep pattern instead.
            n, a = "web", {"q": part}
        # "open kitty and then close it": the window doesn't exist yet when the
        # second action runs, so "it" would hit whatever was used before.
        # Don't guess -- hand the whole request to the model.
        if carry and n in WINDOW_ACTIONS and (a or {}).get("what", "it") in _PRONOUNS:
            return None             # None = "ask the model", not "no match"
        # "open a terminal and run btop" is ONE terminal running btop
        if (carry and _is_terminal(carry) and n in ("open", "run")
                and out and out[-1][0] == "open"):
            cmd = (a or {}).get("cmd") or (a or {}).get("what") or ""
            if cmd:
                out[-1] = ("run", {"cmd": cmd})
                carry = None
                continue
        if n:
            if n == "open" and a and a.get("what"):
                carry = a["what"]
            out.append((n, a))

    if not out:
        whole = match(text)
        return [whole] if whole[0] else []
    return out


_SELF_CLOSE = re.compile(r"^(?:quit|exit|go away|(?:bye|goodbye) tar)[.!]?$")
# anywhere in a SHORT message: "great now kill yourself", "pkill tar", "run pkill -9 tar"
_SELF_CLOSE_ANY = re.compile(r"\b(?:close|quit|exit|kill|pkill|killall|shut ?down|turn off)"
                             r"(?: -9| -15)? (?:yourself|tar|t\.?\s?a\.?\s?r\.?)(?:[.!?]|$|\s)")


_SHEET_ASK = re.compile(r"\b(?:answer|do|solve|complete|fill(?: in| out)?)\b.{0,40}\b(?:questions?|exercises?|"
                        r"homework|worksheet|table)\b")
_SHEET_WHERE = re.compile(r"\b(?:doc|docs|document|sheet|worksheet|page|screen|homework|table|file|"
                          r"under it|below|in it|on it|the questions|these questions|those questions)\b")


def match(text):
    """Deterministic intent match. Returns (name, args) or (None, None)."""
    strict = _normalize(text)
    # "read the doc and answer the questions under it" -> the sheet answerer
    if len(strict.split()) <= 20 and _SHEET_ASK.search(strict) and _SHEET_WHERE.search(strict) \
            and not strict.startswith(("what ", "how ", "why ", "who ")):
        return "fill_table", {}
    # "kill tar" used to mean the `tar` archiver process, "kill t.a.r" a window
    # called t.a.r -- closing T.A.R. itself wins over both
    if _SELF_CLOSE.match(strict) or (len(strict.split()) <= 7 and _SELF_CLOSE_ANY.search(strict)):
        return "close", {}
    lucky = _lucky(strict)
    if lucky:
        return lucky
    passes = [strict]
    loose = _loosen(strict)
    if loose and loose != strict:
        passes.append(loose)

    for low in passes:
        hit = _match_one(low)
        if hit[0]:
            return hit
    return None, None


_PRIORITY = ("screen_fx", "stop_fx", "remember", "forget", "play_game", "research", "virus_scan", "selfie", "evil", "missiles", "camera_live", "devices", "now_playing", "note", "stopwatch", "focus", "coin", "dice", "record", "remind", "wifi", "bluetooth", "dnd", "nightlight", "power",
             "play", "weather", "datetime", "battery", "calc", "processes",
             "colorpick", "cliphist")


_YES_RE = re.compile(r"^(?:yes|yeah|yep|yup|y|sure|ok|okay|do it|go ahead|confirm|"
                     r"confirmed|go|yes do it|yes please|send it|proceed)$")
_NO_RE = re.compile(r"^(?:no|nope|nah|n|cancel|stop|don'?t|abort|never ?mind|wait)$")
# words after open/close/kill that are not apps or windows
_NOT_TARGETS = re.compile(r"^(?:me|myself|yourself|you|your \w+|my (?:eyes|mind|heart|mouth)|"
                          r"source|up|now|the door|eyes|mind|heart)(?:\b|$)")


_STOP_RE = re.compile(r"^(?:stop|stop it|stop now|stop playing|stop that|ok stop|enough|"
                      r"that'?s enough|you can stop|quit it|cancel (?:it|that|the task))$")


def _match_one(low):
    if fx_running() and _STOP_RE.match(low):
        return "stop_fx", {}            # "stop" ends the effect on screen, not the music
    if loop_running():
        if _STOP_RE.match(low):
            return "stop_task", {}
        # only while a task exists -- otherwise "keep going" is just chat
        if re.match(r"^(?:pause|pause (?:the |that |your )?(?:task|playing|game|background task)|"
                    r"hold on|wait a sec|take a break)$", low):
            return "pause_task", {}
        if re.match(r"^(?:resume|continue|keep going|carry on|unpause|go on)"
                    r"(?: (?:the |that )?(?:task|playing|game))?$", low):
            return "resume_task", {}
    if pending_action():
        if _YES_RE.match(low):
            return "confirm", {}
        if _NO_RE.match(low):
            return "cancel", {}
    for name, pats in UI_PATTERNS.items():
        for pat in pats:
            m = re.match(pat, low, re.I)
            if m:
                return name, {k: v for k, v in (m.groupdict() or {}).items()
                              if v is not None}
    # specific phrasings first, so the generic "start/open <x>" pattern can't
    # swallow "start recording" or "turn wifi off"
    order = [n for n in _PRIORITY if n in ACTIONS] + \
            [n for n in ACTIONS if n not in _PRIORITY]
    for name in order:
        _fn, _help, pats = ACTIONS[name]
        for pat in pats:
            m = re.match(pat, low, re.I)
            if not m:
                continue
            args = {k: v for k, v in (m.groupdict() or {}).items()
                    if v is not None}
            tgt = (args.get("what") or args.get("name") or "").lower()
            if name in ("open", "closewin", "kill") and _NOT_TARGETS.match(tgt):
                continue                # "kill me", "open your heart": talk, not a command
            if name in ("closewin", "kill") and re.search(r"\btabs?$", tgt):
                continue                # tabs are never windows -- the AI picks which tabs
            # sugar groups: _up / _down become a delta
            if "_up" in args:
                args["delta"] = 5 if name == "volume" else 10
                args.pop("_up")
            if "_down" in args:
                args["delta"] = -5 if name == "volume" else -10
                args.pop("_down")
            if name == "media" and "what" not in args:
                args["what"] = "next"
            if args.get("mute"):
                args["mute"] = True
            return name, args
    return None, None


# What T.A.R. says back when an easter egg fires (instead of the fx label).
EGG_REPLY = {
    "barrelroll": "wheee.",
    "selfdestruct": "self-destruct sequence engaged. stand by.",
    "hack": "I'm in.",
    "matrix": "follow the white rabbit.",
    "party": "party mode. don't tell anyone.",
    "shake": "brace yourself.",
    "heartbeat": "still ticking.",
    "flash": "lumos.",
    "shock": "boom.",
}
# banner eggs: the matched phrase picks the line (and the banner text)
BANNER_EGG = {
    "_hal": ("I'm sorry. I'm afraid I can't do that.", "alert"),
    "_humor": ("75%. want me to turn it down?", "banner"),
    "_life": ("42.", "banner"),
    "_sandwich": ("okay. (I can't make sandwiches.)", "banner"),
    "_hello": ("general kenobi.", "banner"),
    "_ily": ("I know.", "banner"),
}


# ---- post-checks: report whether an action REALLY worked ---------------------
_WINDOW_OPENERS = ("open", "run", "web", "play", "folder")
_SKIP_VERIFY = ("refused", "NEEDS CONFIRMATION", "no ", "couldn't", "can't", "don't know",
                "not clicking", "too vague", "which ", "need ", "nothing ", "rolled",
                "opened T.A.R.", "cancelled", "confirmed")


def _snapshot_windows():
    return {w["address"]: (w.get("title") or "", (w.get("workspace") or {}).get("id"),
                           w.get("class") or "") for w in _clients()}


def _pre_verify(name, args):
    if name in _WINDOW_OPENERS or name in ("closewin", "kill", "sendto", "focus"):
        return _snapshot_windows()
    return None


def _post_verify(name, args, pre, out):
    """Append a real check of the result. Never raises."""
    if str(out).startswith(("NOT DONE", "NEEDS CONFIRMATION")) or "VERIFIED" in str(out):
        return ""
    try:
        if name in _WINDOW_OPENERS and pre is not None:
            for _ in range(10):             # up to ~5s for the app/page to show up
                time.sleep(0.5)
                now = _snapshot_windows()
                new = [now[a] for a in now if a not in pre]
                if new:
                    return "VERIFIED: new window appeared (%s)" % (new[0][2] or new[0][0][:40])
                moved = [now[a] for a in now if a in pre and now[a][0] != pre[a][0]
                         and _is_browser({"class": now[a][2]})]
                if moved:
                    return "VERIFIED: browser now shows %r" % moved[0][0][:60]
            return "NOT VERIFIED: no new window or page appeared within 5s"
        if name in ("closewin", "kill") and pre is not None:
            time.sleep(1.2)
            now = _snapshot_windows()
            gone = [pre[a][2] or pre[a][0][:30] for a in pre if a not in now]
            if gone:
                return "VERIFIED: closed %s" % ", ".join(gone)
            return ("NOT VERIFIED: the window is still open -- it may be showing a "
                    "'save changes?' dialog. look to check")
        if name == "sendto" and pre is not None:
            now = _snapshot_windows()
            moved = [a for a in now if a in pre and now[a][1] != pre[a][1]]
            return "VERIFIED: window moved" if moved else "NOT VERIFIED: no window changed workspace"
        if name == "focus":
            rc, o = sh(["hyprctl", "-j", "activewindow"])
            t = (json.loads(o).get("title") or "") if rc == 0 and o.strip().startswith("{") else ""
            return ("VERIFIED: %r is now in front" % t[:50]) if t and t != TAR_TITLE \
                else "NOT VERIFIED: focus didn't move"
        if name == "volume":
            rc, o = sh(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            m = re.search(r"(\d+)%", o or "")
            rc2, mute = sh(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            return "now: volume %s%s" % (m.group(1) + "%" if m else "?",
                                         ", MUTED" if "yes" in (mute or "") else "")
        if name == "wifi" and str(args.get("state", "")).lower() in ("on", "off"):
            rc, o = sh(["nmcli", "radio", "wifi"])
            want = "enabled" if args["state"].lower() == "on" else "disabled"
            return ("VERIFIED: wifi is %s" % o) if o.strip() == want else ("NOT VERIFIED: wifi is %s" % o)
        if name == "bluetooth" and str(args.get("state", "")).lower() in ("on", "off"):
            rc, o = sh(["bluetoothctl", "show"])
            on = "Powered: yes" in (o or "")
            ok = on == (args["state"].lower() == "on")
            return ("VERIFIED" if ok else "NOT VERIFIED") + ": bluetooth is %s" % ("on" if on else "off")
        if name == "media":
            rc, o = sh(["playerctl", "status"])
            return "now: %s" % (o or "no player")
        if name == "brightness":
            rc, cur = sh(["brightnessctl", "get"])
            rc2, mx = sh(["brightnessctl", "max"])
            if cur.isdigit() and mx.isdigit() and int(mx):
                return "now: brightness %d%%" % round(int(cur) * 100 / int(mx))
    except Exception as e:
        return "(couldn't verify: %s)" % e
    return None


def run(name, args):
    global REPLY_TEXT
    REPLY_TEXT = None                   # only the action that sets it may use it
    if name in UI_ACTIONS:
        args = dict(args or {})
        egg = next((k for k in BANNER_EGG if k in args), None)
        if egg:
            line, kind = BANNER_EGG[egg]
            args.pop(egg, None)
            if kind == "alert":
                emit("ui", action="alert")
            emit("ui", action="banner", text=line if len(line) < 28 else line.split(".")[0] + ".")
            return line
        if name == "close" and not DIRECT and not re.search(
                r"\b(close|quit|exit|kill|pkill|killall|shut ?down|turn off|stop)\b.{0,20}\b(yourself|tar|t\.?\s?a\.?\s?r\.?|you)\b"
                r"|\b(go away|bye tar|goodbye)\b", (USER_SAID or "").split(" \n ")[-1].lower()):
            # it once closed ITSELF when asked to "close the camera feed"
            return ("NOT DONE: 'close' shuts T.A.R. itself down -- the user didn't ask for that. "
                    "For the camera/preview use camera_live state=off or panel what=viewer state=close.")
        emit("ui", action=name, **args)
        if name == "panel":
            return "%s T.A.R.'s %s panel%s -- VERIFIED (done instantly, don't repeat it)" % (
                "opened" if str(args.get("state", "close")) == "open" else "closed",
                args.get("what", "all"), "s" if args.get("what", "all") == "all" else "")
        return EGG_REPLY.get(name) or ("done: %s -- VERIFIED (T.A.R.'s own UI, don't repeat it)" % name)
    entry = ACTIONS.get(name)
    if not entry:
        return None
    fn = entry[0]
    pre = _pre_verify(name, args or {})
    out = fn(args or {})
    if isinstance(out, str) and not out.startswith(_SKIP_VERIFY) and "VERIFIED" not in out:
        check = _post_verify(name, args or {}, pre, out)
        if check:
            out = "%s -- %s" % (out, check)
    return out


def schema():
    """Compact tool list for the model's system prompt."""
    rows = []
    for name, (_fn, helptext, pats) in ACTIONS.items():
        ex = None
        for p in pats:
            ex = re.sub(r"[\^\$\\]|\(\?:|\(\?P<\w+>|[\[\]\(\)\?\|]|\{[\d,]+\}",
                        "", p).strip()
            if ex:
                break
        rows.append({"action": name, "help": helptext, "example": ex or ""})
    for name, helptext in UI_ACTIONS.items():
        rows.append({"action": name, "help": helptext, "ui": True})
    return rows


# ----------------------------------------------------------------- cli

def main():
    if len(sys.argv) < 2:
        die("usage: tar_tools.py match <text> | run <action> [k=v ...] | "
            "list | schema")
    cmd = sys.argv[1]

    if cmd == "_claim":
        _claim(sys.argv[2] if len(sys.argv) > 2 else "")
        return

    if cmd == "match":
        text = " ".join(sys.argv[2:])
        name, args = match(text)
        if not name:
            emit("nomatch", v=text)
            return
        emit("matched", action=name, args=args)
        out = run(name, args)
        emit("acted", action=name, v=out)

    elif cmd == "run":
        if len(sys.argv) < 3:
            die("usage: run <action> [k=v ...]")
        name = sys.argv[2]
        args = {}
        for kv in sys.argv[3:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                args[k] = v
        if name not in ACTIONS and name not in UI_ACTIONS:
            die("unknown action: " + name)
        out = run(name, args)
        if out is None:
            die("action produced nothing: " + name)
        emit("acted", action=name, v=out)

    elif cmd in ("list", "schema"):
        emit("actions", rows=schema())

    elif cmd == "shell":
        if not ALLOW_SHELL:
            die("shell actions are off; export TAR_ALLOW_SHELL=1 to enable")
        rc, out = sh(["bash", "-lc", " ".join(sys.argv[2:])], timeout=30)
        emit("acted", action="shell", rc=rc, v=out[:2000])

    else:
        die("unknown command: " + cmd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
