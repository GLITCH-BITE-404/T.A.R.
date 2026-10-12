#!/usr/bin/env python3
"""
T.A.R. plays chess in a browser tab (chess.com / lichess vs a bot or a person).

Asking the vision model "where are the pieces?" every move clicked random
squares. This plays like a program instead:

  * the board is found ONCE: one vision call for its corners, which side we
    are and the moves already played, then the grid is snapped exactly by
    checking the light/dark square pattern;
  * python-chess keeps the real game state; a small alpha-beta engine picks
    our moves; a move is two clicks (from-square, to-square) and is checked by
    looking at exactly those two squares;
  * the opponent's move is read by watching which squares changed and picking
    the ONE legal move that explains them -- no per-move vision, no guessing.

Runs as a background task (loop.json), so the orb card / TASKS tab show it and
STOP / PAUSE work. Usage: tar_chess.py play [--minutes N]
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tar_tools as T          # noqa: E402

VENV = os.path.join(T.DATA, "venv")


def _add_venv():
    lib = os.path.join(VENV, "lib")
    if os.path.isdir(lib):
        for d in sorted(os.listdir(lib)):
            sp = os.path.join(lib, d, "site-packages")
            if os.path.isdir(sp) and sp not in sys.path:
                sys.path.insert(0, sp)


def ensure_chess():
    """python-chess, installed into T.A.R.'s own venv on first use (no sudo)."""
    _add_venv()
    try:
        import chess  # noqa: F401
        return None
    except ImportError:
        pass
    try:
        import tar_caps
        rc = tar_caps._pip_install(["chess"], "chess")
    except Exception as e:
        return "couldn't install the chess library: %s" % e
    _add_venv()
    try:
        import chess  # noqa: F401
        return None
    except ImportError:
        return "couldn't install the chess library (pip exit %s)" % rc


# --------------------------------------------------------------------- screen
def _grab(box, path):
    x1, y1, x2, y2 = box
    rc, _ = T.sh(["grim", "-g", "%d,%d %dx%d" % (x1, y1, x2 - x1, y2 - y1), path], timeout=10)
    return rc == 0


def _gray(path, size):
    from PIL import Image
    return Image.open(path).convert("L").resize((size, size))


def _square_cells(img, n=8):
    """img (8*c x 8*c gray) -> 8x8 list of per-square pixel lists (c x c)."""
    w = img.size[0] // n
    px = img.load()
    cells = []
    for r in range(n):
        row = []
        for f in range(n):
            row.append([px[f * w + i, r * w + j] for j in range(w) for i in range(w)])
        cells.append(row)
    return cells


def _pattern_score(path, box, mon_off):
    """How well this box lines up with an 8x8 light/dark grid (higher = better).
    Uses square CORNERS (pieces sit in the middle)."""
    from PIL import Image
    im = Image.open(path).convert("L")
    x1, y1, x2, y2 = [v - o for v, o in zip(box, mon_off * 2)]
    side = (x2 - x1) / 8.0
    if side < 20:
        return -1
    px = im.load()
    W, H = im.size
    vals = []
    for r in range(8):
        for f in range(8):
            s = 0.0
            n = 0
            for dx, dy in ((0.12, 0.12), (0.88, 0.12), (0.12, 0.88), (0.88, 0.88)):
                X = int(x1 + (f + dx) * side)
                Y = int(y1 + (r + dy) * side)
                if 0 <= X < W and 0 <= Y < H:
                    s += px[X, Y]
                    n += 1
            vals.append((s / n if n else 0, (r + f) % 2))
    light = [v for v, p in vals if p == 0]
    dark = [v for v, p in vals if p == 1]
    if not light or not dark:
        return -1
    # squares of one colour should match each other AND differ from the other colour
    import statistics as st
    return abs(st.mean(light) - st.mean(dark)) - 0.5 * (st.pstdev(light) + st.pstdev(dark))


def snap_board(shot, box, mon_off):
    """Nudge the vision box until the 8x8 pattern fits best."""
    best = (_pattern_score(shot, box, mon_off), box)
    x1, y1, x2, y2 = box
    side = x2 - x1
    for scale in (0.97, 0.985, 1.0, 1.015, 1.03):
        s = side * scale
        for dx in range(-12, 13, 3):
            for dy in range(-12, 13, 3):
                cx, cy = (x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy
                b = (int(cx - s / 2), int(cy - s / 2), int(cx + s / 2), int(cy + s / 2))
                sc = _pattern_score(shot, b, mon_off)
                if sc > best[0]:
                    best = (sc, b)
    return best[1], best[0]


# --------------------------------------------------------------------- engine
_VAL = {1: 100, 2: 320, 3: 330, 4: 500, 5: 900, 6: 0}
# piece-square tables (white's view, a8..h8 first row); kept small on purpose
_PST = {
    1: [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    2: [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    3: [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    4: [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5] + [-5, 0, 0, 0, 0, 0, 0, -5] * 5
       + [0, 0, 0, 5, 5, 0, 0, 0],
    5: [0] * 64,
    6: [-30, -40, -40, -50, -50, -40, -40, -30] * 4 + [-20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10, 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
}


def _evaluate(board):
    import chess
    if board.is_checkmate():
        return -99999
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    s = 0
    for sq, p in board.piece_map().items():
        idx = (7 - chess.square_rank(sq)) * 8 + chess.square_file(sq) if p.color else \
            chess.square_rank(sq) * 8 + chess.square_file(sq)
        v = _VAL[p.piece_type] + _PST[p.piece_type][idx]
        s += v if p.color == board.turn else -v
    return s


def _ordered(board):
    caps, quiet = [], []
    for m in board.legal_moves:
        if board.is_capture(m):
            victim = board.piece_at(m.to_square)
            caps.append((-(_VAL[victim.piece_type] if victim else 100), m))
        else:
            quiet.append(m)
    caps.sort(key=lambda x: x[0])
    return [m for _s, m in caps] + quiet


def _search(board, depth, alpha, beta, deadline):
    if time.time() > deadline:
        raise TimeoutError
    if depth == 0 or board.is_game_over():
        return _evaluate(board)
    best = -10 ** 9
    for m in _ordered(board):
        board.push(m)
        try:
            v = -_search(board, depth - 1, -beta, -alpha, deadline)
        finally:
            board.pop()                 # a timeout must never leave a trial move on the board
        if v > best:
            best = v
        alpha = max(alpha, v)
        if alpha >= beta:
            break
    return best


def best_move(board, seconds=2.5):
    """Iterative deepening alpha-beta; returns the best move found in time."""
    deadline = time.time() + seconds
    moves = _ordered(board)
    best = moves[0]
    for depth in range(1, 6):
        try:
            scored = []
            for m in moves:
                board.push(m)
                try:
                    v = -_search(board, depth - 1, -10 ** 9, 10 ** 9, deadline)
                finally:
                    board.pop()
                scored.append((v, m))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1]
            moves = [m for _v, m in scored]          # best-first next depth
        except TimeoutError:
            break
    return best


# --------------------------------------------------------------------- player
class Player:
    def __init__(self, box, white_bottom, board, mon):
        self.box = box
        self.white_bottom = white_bottom
        self.board = board
        self.mon = mon
        self.side = (box[2] - box[0]) / 8.0
        self.shot = os.path.join(T.DATA, "chess-board.png")

    def center(self, sq):
        import chess
        f, r = chess.square_file(sq), chess.square_rank(sq)
        col = f if self.white_bottom else 7 - f
        row = 7 - r if self.white_bottom else r
        return (self.box[0] + (col + 0.5) * self.side, self.box[1] + (row + 0.5) * self.side)

    def cells(self):
        """Per-square grayscale samples of the board right now (row, col screen order)."""
        if not _grab(self.box, self.shot):
            return None
        return _square_cells(_gray(self.shot, 8 * 16))

    def changed(self, a, b, thresh=10.0):
        """Board squares (python-chess numbering) whose look changed between two grabs."""
        import chess
        out = set()
        for row in range(8):
            for col in range(8):
                d = sum(abs(x - y) for x, y in zip(a[row][col], b[row][col])) / len(a[row][col])
                if d > thresh:
                    f = col if self.white_bottom else 7 - col
                    r = 7 - row if self.white_bottom else row
                    out.add(chess.square(f, r))
        return out

    @staticmethod
    def touched(board, m):
        """Squares a move changes on screen (castling moves the rook too)."""
        import chess
        s = {m.from_square, m.to_square}
        if board.is_castling(m):
            rank = chess.square_rank(m.from_square)
            if chess.square_file(m.to_square) == 6:
                s |= {chess.square(7, rank), chess.square(5, rank)}
            else:
                s |= {chess.square(0, rank), chess.square(3, rank)}
        if board.is_en_passant(m):
            s.add(chess.square(chess.square_file(m.to_square), chess.square_rank(m.from_square)))
        return s

    def explain(self, changed, ignore):
        """The one legal move whose squares best match what changed."""
        board = self.board
        ch = changed - ignore
        best, best_sc = None, -99
        for m in board.legal_moves:
            t = self.touched(board, m)
            sc = len(t & changed) * 2 - len(t - changed) * 3 - len(ch - t)
            if sc > best_sc:
                best, best_sc = m, sc
        if best and self.touched(board, best) <= changed:
            return best
        return None

    def click_sq(self, sq):
        x, y = self.center(sq)
        T.sh(["hyprctl", "dispatch", "movecursor", str(int(x)), str(int(y))])
        time.sleep(0.08)
        T.sh([T._wlrctl(), "pointer", "click", "left"])

    def park(self):
        """Pointer off the board so hover highlights don't look like moves."""
        T.sh(["hyprctl", "dispatch", "movecursor", str(int(self.box[2] + 60)), str(int(self.box[1] + 20))])


def setup():
    """Find the board + game state. -> (Player, None) or (None, why)."""
    import chess
    w = None
    for c in T._clients():
        if T._is_browser(c) and re.search(r"chess|lichess", c.get("title") or "", re.I):
            w = c
            break
    if not w:
        return None, "no chess game open in the browser -- open one (chess.com / lichess) first"
    T.sh(["hyprctl", "dispatch", "focuswindow", "address:" + w["address"]])
    time.sleep(0.5)
    mon = T._monitor()
    mx, my = mon.get("x", 0), mon.get("y", 0)
    shot = os.path.join(T.DATA, "chess-setup.jpg")
    with T._TarHidden():
        if not T._shot(shot):
            return None, "couldn't take a screenshot"
    txt, err = T._gemini_image(shot, (
        "This screenshot shows a chess game in a browser. Reply with ONLY JSON: "
        "{\"board\": [x1, y1, x2, y2], \"white_at_bottom\": true, \"moves\": \"1. e4 e5 2. Nf3\"} "
        "board = the 8x8 squares only (NOT the coordinate letters around it), normalized 0-1000 "
        "of the screenshot width/height. white_at_bottom = are the WHITE pieces' home ranks at "
        "the bottom. moves = the move list shown next to the board in standard algebraic "
        "notation (write piece letters K Q R B N even if icons are shown), or \"\" if no moves "
        "were played yet. If there is no chessboard: {\"board\": null}."), max_tokens=600)
    if err:
        return None, err
    try:
        d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except (AttributeError, ValueError):
        return None, "couldn't read the board"
    if not d.get("board"):
        return None, "no chessboard visible on screen"
    W, H = mon.get("width", 1920), mon.get("height", 1080)
    x1, y1, x2, y2 = d["board"]
    box = [mx + x1 / 1000 * W, my + y1 / 1000 * H, mx + x2 / 1000 * W, my + y2 / 1000 * H]
    side = ((box[2] - box[0]) + (box[3] - box[1])) / 2        # it's a square
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    from PIL import Image
    Image.open(shot).save(shot[:-4] + ".png")
    box, score = snap_board(shot[:-4] + ".png", box, (mx, my))
    if score < 8:
        return None, "couldn't line up the board's squares (score %.1f) -- is the whole board visible?" % score
    board = chess.Board()
    sans = [t for t in re.split(r"\s+", re.sub(r"\d+\.(\.\.)?", " ", d.get("moves") or "")) if t
            and not re.match(r"^(1-0|0-1|1/2-1/2|\*)$", t)]
    for san in sans:
        try:
            board.push_san(san.replace("0-0-0", "O-O-O").replace("0-0", "O-O"))
        except ValueError:
            return None, ("couldn't follow the move list at %r -- start a new game and say "
                          "'play chess' right away" % san)
    return Player(box, bool(d.get("white_at_bottom", True)), board, mon), None


def play(minutes=30):
    import chess
    err = ensure_chess()
    if err:
        T.loop_update(status="failed", last=err)
        return 1
    p, err = setup()
    if not p:
        T.loop_update(status="failed", last=err)
        T.mini_say("chess: " + T.short_say(err, 8))
        return 1
    me = chess.WHITE if p.white_bottom else chess.BLACK
    T.loop_update(status="playing", last="found the board, playing %s, %d moves in" % (
        "white" if me else "black", len(p.board.move_stack)))
    p.park()
    time.sleep(0.3)
    base = p.cells()
    ignore = set()                      # our last move's squares (their highlight fades)
    rnd = 0
    until = time.time() + minutes * 60
    while T.loop_running() and time.time() < until:
        d = T.loop_running() or {}
        if d.get("paused"):
            time.sleep(1)
            continue
        ok, why = T.task_window_ok()
        if not ok:
            T.loop_update(status="waiting for the window", last="paused: " + why)
            T.mini_say("chess: waiting, " + why)
            time.sleep(2)
            continue
        if p.board.is_game_over():
            res = p.board.result()
            won = (res == "1-0" and me) or (res == "0-1" and not me)
            T.loop_update(status="game over", last="game over: %s (%s)" % (
                res, "T.A.R. won" if won else "draw" if res == "1/2-1/2" else "T.A.R. lost"))
            T.mini_say("checkmate! " + ("I won" if won else "draw" if res == "1/2-1/2" else "I lost"))
            break
        if p.board.turn == me:
            rnd += 1
            T.mini_say("thinking…")
            m = best_move(p.board)
            san = p.board.san(m)
            T.mini_say("playing " + san)
            before = p.cells()
            p.click_sq(m.from_square)
            time.sleep(0.25)
            p.click_sq(m.to_square)
            if m.promotion:
                time.sleep(0.5)
                p.click_sq(m.to_square)          # the queen sits on the target square
            p.park()
            time.sleep(0.9)
            after = p.cells()
            moved = p.changed(before, after) if before and after else set()
            if not (Player.touched(p.board, m) <= moved):
                # didn't take: maybe a popup, maybe it's not our turn on the page
                T.loop_update(last="tried %s but the board didn't change -- retrying" % san)
                time.sleep(1.5)
                continue
            ignore = Player.touched(p.board, m)
            p.board.push(m)
            base = after
            T.loop_update(round=rnd, clicks=rnd, last="played %s" % san, status="waiting for them")
        else:
            # wait for the opponent: a stable change that one legal move explains
            time.sleep(0.7)
            now = p.cells()
            if not now or not base:
                continue
            ch = p.changed(base, now)
            if len(ch - ignore) < 2:
                continue
            time.sleep(0.5)                       # let the animation finish
            now2 = p.cells()
            ch = p.changed(base, now2)
            m = p.explain(ch, ignore)
            if not m:
                T.loop_update(last="couldn't tell their move (changed %d squares) -- watching" % len(ch))
                continue
            san = p.board.san(m)
            p.board.push(m)
            base = now2
            ignore = set()
            T.mini_say("they played " + san)
            T.loop_update(last="they played %s" % san, status="playing")
    try:
        os.remove(T.LOOP)
    except OSError:
        pass
    T.mini(on=False, say="")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "play":
        mins = int(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 30
        T.MINI_HOLD = 90
        sys.exit(play(mins))
    print(__doc__)
