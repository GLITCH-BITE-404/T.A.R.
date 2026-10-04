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
_PLURAL = ("them", "those", "these", "both", "both of them", "all of them",
           "everything you opened", "all of those", "what you opened",
           "the ones you opened")
_EVERYTHING = ("everything", "all", "all windows", "all apps", "everything else",
               "all the windows", "all the apps", "every window", "all other windows",
               "everything but tar", "everything except tar", "all but tar")


def a_closewin(args):
    what = (args.get("what") or "").strip().lower()
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
}


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
        url = what if low.startswith("http") else "https://" + what
        sh(["xdg-open", url], detach=True)
        return "opening " + url[:60]

    # "open downloads" -> the (Hebrew-named) XDG folder
    xd = xdg_dir(low)
    if xd:
        sh(["xdg-open", xd], detach=True)
        return "opening " + xd

    # "open youtube" is a website, not a binary
    site = SITES.get(low) or SITES.get(re.sub(r"\.com$", "", low))
    if site:
        sh(["xdg-open", site], detach=True)
        return "opening " + site.split("//")[1].rstrip("/")

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

def a_volume(args):
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

def a_cam_view(_):
    dev = effective_device("video")
    if not dev:
        return "no camera found"
    if not shutil.which("mpv"):
        return "mpv isn't installed"
    sh(["mpv", "--profile=low-latency", "--untimed", "--no-osc",
        "--title=T.A.R. camera", "av://v4l2:" + dev], detach=True)
    return "camera up on " + dev


def a_cam_snap(_):
    dev = effective_device("video")
    if not dev:
        return "no camera found"
    if not shutil.which("ffmpeg"):
        return "ffmpeg isn't installed"
    os.makedirs(SHOTS, exist_ok=True)
    p = os.path.join(SHOTS, time.strftime("cam-%Y%m%d-%H%M%S.jpg"))
    rc, out = sh(["ffmpeg", "-y", "-f", "v4l2", "-i", dev,
                  "-frames:v", "1", p], timeout=25)
    if rc or not os.path.exists(p):
        return "camera grab failed"
    emit("image", path=p, source="camera")
    return "grabbed a frame"


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

def _away_from_tar(args):
    """Typing goes to the focused window -- which is T.A.R. while you talk to
    it. Move focus to the target app first (named via into=/app=, else last)."""
    rc, out = sh(["hyprctl", "-j", "activewindow"])
    try:
        active = json.loads(out).get("title") if rc == 0 else None
    except ValueError:
        active = None
    named = args.get("into") or args.get("app")
    if active != TAR_TITLE and not named:
        return None
    _w, err = _focus_target({"what": named} if named else {})
    if err:
        return err
    time.sleep(0.15)                # let the compositor move keyboard focus
    return None


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
    rc, out = sh(["wtype", txt], timeout=20)
    return "typed %d chars" % len(txt) if rc == 0 else "typing failed: " + out


def a_key(args):
    """Press a key combo, e.g. key combo=ctrl+t"""
    combo = (args.get("combo") or args.get("what") or "").strip()
    if not combo:
        return "which key?"
    if not shutil.which("wtype"):
        return "wtype isn't installed"
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
    rc, out = sh(argv, timeout=15)
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
    # wl-copy stays alive as the clipboard OWNER after reading stdin, so
    # subprocess.run() waits on it forever. Hand it the text and walk away.
    try:
        p_ = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, text=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              start_new_session=True)
        p_.stdin.write(txt)
        p_.stdin.close()
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


def a_web(args):
    """Open a URL, or search the web, in the user's browser."""
    q = (args.get("q") or args.get("what") or "").strip()
    if not q:
        return "open what?"
    if re.match(r"^(https?://|www\.)", q, re.I):
        url = q if q.lower().startswith("http") else "https://" + q
    elif re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", q, re.I):
        url = "https://" + q
    else:
        # lucky: DuckDuckGo's "\" prefix jumps straight to the first result
        if str(args.get("lucky", "")).lower() in ("1", "true", "yes"):
            q = "\\" + q
        url = "https://duckduckgo.com/?q=" + urlquote(q)
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
    return os.path.normpath(p)


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


class _TarHidden:
    """Park T.A.R. on a hidden special workspace while looking/clicking, so it
    never covers the target and never clicks itself; put it back after."""
    def __enter__(self):
        self.w = _tar_window()
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
        return False


def _shot(path):
    rc, out = sh(["grim", "-t", "jpeg", "-q", "80", path], timeout=10)
    return rc == 0 and os.path.exists(path)


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


def _gemini_image(path, prompt):
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
    model = cfg().get("vision_model") or "gemini-flash-lite-latest"
    try:
        r = C.gemini_call(key, model, {"contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800, "temperature": 0.1}})
    except Exception as e:
        return None, "vision request failed: %s" % e
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip(), None


def _gemini_point(path, target, mon):
    txt, err = _gemini_image(path, (
        "This is a screenshot. Find: %s\nReply with ONLY JSON: "
        "{\"found\": true, \"point\": [y, x], \"label\": \"what it is\"} with y and x "
        "normalized 0-1000 to the CENTER of it, or {\"found\": false, \"why\": "
        "\"...\"} if it is not visible." % target))
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


def _locate(target):
    """Find something on screen -> ((x, y, label, how), None) or (None, why).
    OCR first (exact for text buttons), vision second (icons, images)."""
    path = os.path.join(DATA, "screen-look.jpg")
    mon = _monitor()
    with _TarHidden():
        if not _shot(path):
            return None, "couldn't take a screenshot"
    hit = _find_text(_ocr_boxes(path), target)
    if hit:
        return (hit[1], hit[2], hit[0], "text"), None
    p, err = _gemini_point(path, target, mon)
    if p:
        return (p[0], p[1], p[2], "vision"), None
    return None, err


def _wlrctl():
    """wlrctl from PATH, or ~/.local/bin (a user-built copy works without
    sudo, and T.A.R. may be launched with a PATH that lacks ~/.local/bin)."""
    return shutil.which("wlrctl") or next(
        (p for p in (os.path.expanduser("~/.local/bin/wlrctl"), "/usr/bin/wlrctl")
         if os.access(p, os.X_OK)), None)


def _pointer_ok():
    return bool(_wlrctl())


_NO_POINTER = ("can't click yet: the mouse tool isn't installed. Tell the user to "
               "run:  yay -S wlrctl   (then try again)")


def _move(x, y, mon=None):
    mon = mon or _monitor()
    sh(["hyprctl", "dispatch", "movecursor", str(int(mon.get("x", 0) + x)),
        str(int(mon.get("y", 0) + y))])


def _click(button="left", double=False):
    for _ in range(2 if double else 1):
        sh([_wlrctl(), "pointer", "click", button])
        if double:
            time.sleep(0.08)


def a_look(args):
    """Answer a question about what's on screen (T.A.R. hides itself first)."""
    q = args.get("q") or args.get("question") or "Describe what is on the screen."
    path = os.path.join(DATA, "screen-look.jpg")
    with _TarHidden():
        if not _shot(path):
            return "couldn't take a screenshot"
    txt, err = _gemini_image(path, "Screenshot of the user's desktop. " + q +
                             " Be specific and brief; mention exact button/label text.")
    if err:
        # no vision: fall back to OCR text so the model has *something*
        words = [b[0] for b in _ocr_boxes(path)][:60]
        return "(%s) text visible on screen: %s" % (err, " | ".join(words) or "none")
    return txt or "couldn't make sense of the screen"


def a_click_on(args):
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
    if not _pointer_ok():
        return _NO_POINTER
    if _RISKY_CLICK.search(target):
        stop = needs_confirm("click_on", args, "click %r" % target)
        if stop:
            return stop
    loc, err = _locate(target)
    if not loc:
        return err
    x, y, label, how = loc
    if _RISKY_CLICK.search(label) and not args.get("_confirmed") and not DIRECT:
        stop = needs_confirm("click_on", args, "click %r (found %r)" % (target, label))
        if stop:
            return stop
    button = (args.get("button") or "left").lower()
    with _TarHidden():
        _move(x, y)
        time.sleep(0.05)
        _click(button if button in ("left", "right", "middle") else "left",
               str(args.get("double", "")).lower() in ("1", "true", "yes"))
    return "clicked %r at (%d, %d) [found by %s]" % (label[:60], x, y, how)


def a_click(args):
    """Click at screen coordinates (pixels from the top-left of the screen)."""
    if not _pointer_ok():
        return _NO_POINTER
    x, y = _int(args.get("x"), -1), _int(args.get("y"), -1)
    mon = _monitor()
    if not (0 <= x < mon["width"] and 0 <= y < mon["height"]):
        return "x/y must be on screen (0-%d, 0-%d)" % (mon["width"] - 1, mon["height"] - 1)
    button = (args.get("button") or "left").lower()
    with _TarHidden():
        _move(x, y, mon)
        time.sleep(0.05)
        _click(button if button in ("left", "right", "middle") else "left",
               str(args.get("double", "")).lower() in ("1", "true", "yes"))
    return "clicked %s at (%d, %d)" % (button, x, y)


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
    rc, out = sh(["wtype", text], timeout=20)
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
    "closewin":   (a_closewin, "close an app window: what=kitty/firefox/... or "
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
    "open":       (a_open, "open an app, file or url",
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
    "cam_view":   (a_cam_view, "open a live camera window",
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
    "remind":     (a_remind, "reminder/timer: minutes=/seconds=/hours= or in='10m', text=",
                   [r"^remind me in (?P<in>\d+\s*(?:m|min|minutes?|s|sec|seconds?|h|hours?)) (?:to |that |about )?(?P<text>.+)$",
                    r"^(?:set )?(?:a )?timer (?:for )?(?P<in>\d+\s*(?:m|min|minutes?|s|sec|seconds?|h|hours?))$"]),
    "wifi":       (a_wifi, "wifi: state=on|off|status|list, or state=connect ssid= password=",
                   [r"^(?:turn )?wi-?fi (?P<state>on|off)$", r"^turn (?P<state>on|off) (?:the )?wi-?fi$",
                    r"^(?:what wi-?fi am i on|wi-?fi status)$"]),
    "bluetooth":  (a_bluetooth, "bluetooth: state=on|off|status|devices|connect|disconnect, device=",
                   [r"^(?:turn )?bluetooth (?P<state>on|off)$", r"^turn (?P<state>on|off) (?:the )?bluetooth$"]),
    "battery":    (a_battery, "battery level and time left",
                   [r"^(?:battery|how much battery(?: do i have)?(?: left)?|battery (?:level|status))$"]),
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
    "coin":       (a_coin, "flip a coin", [r"^(?:flip a coin|coin ?flip|heads or tails)$"]),
    "dice":       (a_dice, "roll a die: sides= (default 6)",
                   [r"^roll (?:a )?(?:die|dice)$", r"^roll (?:a )?d(?P<sides>\d{1,4})$"]),
    "play":       (a_play, "play a song/video on youtube: q=. This ALREADY opens and "
                         "starts the top video -- do NOT click anything afterwards.",
                   [r"^play (?P<q>.+) on youtube$"]),
    "look":       (a_look, "LOOK at the screen and answer a question about it (q=). "
                           "Use before clicking if unsure what's there.", []),
    "click_on":   (a_click_on, "CLICK something visible on screen by description: "
                               "target='the subscribe button' (button=right, double=true optional). "
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
    "web":        (a_web, "open a site or search the web (lucky=true opens the first result)",
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
    "close":     [r"^(?:close|quit|exit) (?:yourself|tar|the panel)$"],
    # ---- easter eggs (no model needed) ----
    "barrelroll": [r"^do a barrel roll$", r"^barrel roll$", r"^spin$"],
    "selfdestruct": [r"^(?:initiate |activate |start )?self[ -]?destruct(?: sequence)?$"],
    "hack":      [r"^hack (?:the )?(?:mainframe|planet|pentagon|nasa|gibson)$", r"^i'?m in$",
                  r"^hacker ?mode$", r"^enhance$"],
    "matrix":    [r"^(?:enter the matrix|wake up,? neo|follow the white rabbit|red pill)$"],
    "party":     [r"^(?:party|rave|disco)(?: mode| time)?$", r"^let'?s party$"],
    "shake":     [r"^(?:earthquake|shake(?: it)?)$"],
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


def match(text):
    """Deterministic intent match. Returns (name, args) or (None, None)."""
    strict = _normalize(text)
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


_PRIORITY = ("focus", "coin", "dice", "record", "remind", "wifi", "bluetooth", "dnd", "nightlight", "power",
             "play", "weather", "datetime", "battery", "calc", "processes",
             "colorpick", "cliphist")


_YES_RE = re.compile(r"^(?:yes|yeah|yep|yup|y|sure|ok|okay|do it|go ahead|confirm|"
                     r"confirmed|go|yes do it|yes please|send it|proceed)$")
_NO_RE = re.compile(r"^(?:no|nope|nah|n|cancel|stop|don'?t|abort|never ?mind|wait)$")
# words after open/close/kill that are not apps or windows
_NOT_TARGETS = re.compile(r"^(?:me|myself|yourself|you|your \w+|my (?:eyes|mind|heart|mouth)|"
                          r"source|up|now|the door|eyes|mind|heart)(?:\b|$)")


def _match_one(low):
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


def run(name, args):
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
        emit("ui", action=name, **args)
        return EGG_REPLY.get(name) or UI_ACTIONS[name]
    entry = ACTIONS.get(name)
    if not entry:
        return None
    fn = entry[0]
    return fn(args or {})


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
