#!/usr/bin/env python3
"""
T.A.R. dictation: type what the user SAYS into one app window.

    tar_dictate.py --win <hyprland address> [--minutes N]

Started by the `dictate` action as a background worker (so "stop" kills it
like any other). One utterance at a time: tar_listen.py records until a
pause and transcribes (Gemini, so Hebrew works), then the text is pasted with
put_text -- keybinds paused, clipboard restored.

Only ever types into the window it was started for. If the user switches
away, what they said is NOT typed anywhere else (a dictated sentence landing
in a terminal could run as a command); it's shown in a notification instead.

Spoken commands (the whole utterance):
    stop / stop dictation / done / עצור / די      -> end
    new line / שורה חדשה                          -> Enter
    delete that / scratch that / מחק את זה         -> remove the last chunk
"""

import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tar_tools as T          # noqa: E402

IDLE_END = 90                  # this long with nothing said -> stop by itself

_STOP = re.compile(r"^(?:ok(?:ay)? )?(?:stop|stop dictat\w*|end dictat\w*|stop typing|done|"
                   r"that'?s it|i'?m done|עצור|תעצור|די|סיים|סיימתי|סוף הכתבה)$")
_NEWLINE = re.compile(r"^(?:new line|next line|enter|new paragraph|שורה חדשה|ירידת שורה|אנטר)$")
_UNDO = re.compile(r"^(?:delete that|scratch that|undo that|erase that|מחק את זה|תמחק את זה|מחק)$")


def _norm(t):
    return " ".join(re.sub(r"[.!?,،…\"']", " ", t.lower()).split())


def hear():
    """One utterance -> text ('' = nothing understood, None = the mic failed)."""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "tar_listen.py"),
                            "--seconds", "30", "--silence", "1.3"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    text, failed = "", False
    for line in (r.stdout or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("t") == "transcript" and d.get("ok"):
            text = (d.get("v") or "").strip()
        elif d.get("t") == "error":
            failed = True
    return None if failed and not text else text


def notify(msg):
    T.sh(["notify-send", "-a", "T.A.R.", "-t", "4000", "T.A.R. dictation", msg])


def main():
    win = sys.argv[sys.argv.index("--win") + 1] if "--win" in sys.argv else ""
    minutes = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 20
    if not win:
        return 2
    end_at = time.time() + minutes * 60
    last_heard = time.time()
    last_len, at_line_start, typed, fails = 0, True, 0, 0
    was_start = True                    # at_line_start before the last chunk, for an undo
    while time.time() < end_at:
        text = hear()
        if text is None:
            fails += 1
            if fails >= 3:
                notify("stopped -- the microphone isn't working")
                break
            continue
        fails = 0
        if not text:
            if time.time() - last_heard > IDLE_END:
                notify("stopped -- nothing said for %d s" % IDLE_END)
                break
            continue
        last_heard = time.time()
        low = _norm(text)
        if _STOP.match(low):
            break
        act = T._active_window() or {}
        if act.get("address") != win:
            notify("not typed (you switched windows): " + text[:120])
            continue
        if _NEWLINE.match(low):
            T.wkeys("-k", "Return")
            last_len, at_line_start = 0, True
            continue
        if _UNDO.match(low):
            if last_len:
                T.wkeys(*(["-k", "BackSpace"] * last_len))
                typed -= last_len
                last_len, at_line_start = 0, was_start
            continue
        chunk = text if at_line_start else " " + text
        rc, _o = T.put_text(chunk)
        if rc == 0:
            was_start = at_line_start
            last_len, at_line_start, typed = len(chunk), False, typed + len(chunk)
    notify("dictation ended (%d characters typed)" % typed)
    return 0


if __name__ == "__main__":
    # "stop" from T.A.R. kills the whole group; unwind so tar_listen restores the mic
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
