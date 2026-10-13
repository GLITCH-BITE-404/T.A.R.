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


LOG = os.path.join(T.DATA, "chess.log")


def log(*parts):
    """One line per decision -- so a bad game can be explained afterwards."""
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in parts) + "\n")
    except OSError:
        pass


def _vision(path, prompt, max_tokens=800, limit=25):
    """T._gemini_image with a hard time limit: a stalled API call once froze
    setup for minutes."""
    import threading
    box = {}
    th = threading.Thread(target=lambda: box.update(r=T._gemini_image(path, prompt, max_tokens=max_tokens)),
                          daemon=True)
    t0 = time.time()
    th.start()
    th.join(limit)
    if th.is_alive():
        log("vision call timed out after %ds" % limit)
        return None, "the AI didn't answer within %ds (slow network or quota) -- try again" % limit
    log("vision call %.1fs" % (time.time() - t0))
    return box.get("r", (None, "vision failed"))


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


_IMG_CACHE = {}


def _pixels(path):
    """Decoded grayscale screenshot, loaded ONCE (snapping scores ~800 boxes;
    re-decoding a 1080p PNG for each one took minutes)."""
    key = (path, os.path.getmtime(path))
    if key not in _IMG_CACHE:
        from PIL import Image
        im = Image.open(path).convert("L")
        _IMG_CACHE.clear()
        _IMG_CACHE[key] = (im.load(), im.size, im)
    return _IMG_CACHE[key][:2]


def _pattern_score(path, box, mon_off, inset=0.12):
    """How well this box lines up with an 8x8 light/dark grid (higher = better).
    Uses square CORNERS (pieces sit in the middle); a small inset makes it
    sensitive to a few pixels of size error."""
    x1, y1, x2, y2 = [v - o for v, o in zip(box, mon_off * 2)]
    side = (x2 - x1) / 8.0
    if side < 20:
        return -1
    px, (W, H) = _pixels(path)
    vals = []
    for r in range(8):
        for f in range(8):
            s = 0.0
            n = 0
            for dx, dy in ((inset, inset), (1 - inset, inset), (inset, 1 - inset), (1 - inset, 1 - inset)):
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


def board_edges(shot, box, mon_off, reach=40):
    """Exact board edges: where the page background meets the squares, found on
    a few scan lines through the middle (the snapped grid alone wandered by
    ~12 px between screenshots, which wrecked piece-shape matching)."""
    import statistics as st
    px, (W, H) = _pixels(shot)
    x1, y1, x2, y2 = [v - o for v, o in zip(box, mon_off * 2)]
    side = (x2 - x1) / 8.0
    rows = [int(y1 + (k + 0.5) * side) for k in (1, 3, 5)]
    cols = [int(x1 + (k + 0.5) * side) for k in (1, 3, 5)]

    def best(line, positions, rising):
        top = None
        for q in positions:
            d = (line(q + 3) - line(q - 3)) * (1 if rising else -1)
            if top is None or d > top[0]:
                top = (d, q)
        # then the exact pixel: the single biggest step near that spot
        q0 = top[1]
        step = max(range(q0 - 4, q0 + 4), key=lambda x: abs(line(x + 1) - line(x)))
        return step + 1
    cx = lambda v: max(0, min(W - 1, v))
    cy = lambda v: max(0, min(H - 1, v))
    L = st.median(best(lambda x, y=y: px[cx(x), y], range(int(x1) - reach, int(x1) + reach), True) for y in rows)
    R = st.median(best(lambda x, y=y: px[cx(x), y], range(int(x2) - reach, int(x2) + reach), False) for y in rows)
    T_ = st.median(best(lambda y, x=x: px[x, cy(y)], range(int(y1) - reach, int(y1) + reach), True) for x in cols)
    B = st.median(best(lambda y, x=x: px[x, cy(y)], range(int(y2) - reach, int(y2) + reach), False) for x in cols)
    side = round(((R - L) + (B - T_)) / 2)
    return (int(L + mon_off[0]), int(T_ + mon_off[1]), int(L + mon_off[0] + side), int(T_ + mon_off[1] + side))


def snap_board(shot, box, mon_off):
    """Find the 8x8 grid near the vision box, then pin it to the board's real
    edges. -> (box, score)"""
    box, score = _snap_grid(shot, box, mon_off)
    try:
        # left/top edges are clean; right/bottom ones get fooled by coordinate
        # labels drawn inside the squares (lichess) -- so the corner comes from
        # the edges and the SIZE from where the light/dark pattern fits best
        e = board_edges(shot, box, mon_off)
        L, T_ = e[0], e[1]
        if abs(L - box[0]) < 30 and abs(T_ - box[1]) < 30:
            side0 = box[2] - box[0]
            best = (-1e9, None)
            for sd in range(int(side0 * 0.94), int(side0 * 1.06) + 1):
                b = (L, T_, L + sd, T_ + sd)
                sc = _pattern_score(shot, b, mon_off, inset=0.035)
                if sc > best[0]:
                    best = (sc, b)
            if best[1] and _pattern_score(shot, best[1], mon_off) >= 8:
                return best[1], _pattern_score(shot, best[1], mon_off)
    except Exception:
        pass
    return box, score


def _snap_grid(shot, box, mon_off):
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


_SF = None


def stockfish():
    """A running Stockfish (kept open for the whole game), or None."""
    global _SF
    if _SF is not None:
        return _SF or None
    import shutil
    exe = shutil.which("stockfish") or next(
        (p for p in (os.path.expanduser("~/.local/bin/stockfish"), "/usr/bin/stockfish")
         if os.access(p, os.X_OK)), None)
    _SF = False
    if exe:
        try:
            import chess.engine
            _SF = chess.engine.SimpleEngine.popen_uci(exe)
            _SF.configure({"Threads": 2, "Hash": 64})
        except Exception:
            _SF = False
    return _SF or None


# ---- strength levels 1-20 ------------------------------------------------------
# Beginner 1-3 · Pro 4-5 · Grandmaster 6-8 · Engine 9-20. Stockfish's own rating
# limiter covers 1320-3190; below that, weakest settings + some random moves.
def level_band(lv):
    return "beginner" if lv <= 3 else "pro" if lv <= 5 else "grandmaster" if lv <= 8 else "engine"


def level_elo(lv):
    """Roughly how strong a level plays (None = no limit at all)."""
    table = {1: 400, 2: 700, 3: 1000, 4: 1400, 5: 1800, 6: 2300, 7: 2500, 8: 2700}
    if lv in table:
        return table[lv]
    return None if lv >= 20 else 2800 + (lv - 9) * 39      # 9 -> 2800 ... 19 -> 3190


_RANDOM = {1: 0.60, 2: 0.35, 3: 0.15}                       # beginner blunders
_SF_LEVEL = [None]


def current_level():
    """The level right now: the running task's (changed live from the orb card),
    else the saved setting, else full strength."""
    for src in (lambda: (T.loop_running() or {}).get("level"),
                lambda: T.cfg().get("chess_level")):
        try:
            lv = src()
            if lv:
                return max(1, min(20, int(lv)))
        except (TypeError, ValueError):
            pass
    return 20


def _configure(sf, lv):
    if _SF_LEVEL[0] == lv:
        return
    elo = level_elo(lv)
    try:
        if lv <= 3:
            sf.configure({"UCI_LimitStrength": False, "Skill Level": 0})
        elif elo:
            sf.configure({"Skill Level": 20, "UCI_LimitStrength": True,
                          "UCI_Elo": max(1320, min(3190, elo))})
        else:
            sf.configure({"UCI_LimitStrength": False, "Skill Level": 20})
        _SF_LEVEL[0] = lv
        log("strength: level %d (%s, ~%s)" % (lv, level_band(lv), elo or "full strength"))
    except Exception as e:
        log("couldn't set the level:", e)


def best_move(board, seconds=2.5, avoid=(), level=None):
    """Stockfish if it's installed (plays far above any bot), else the small
    built-in alpha-beta below -- at the chosen level. avoid = moves that didn't
    work on screen."""
    import random
    lv = level or current_level()
    allowed = [m for m in board.legal_moves if m not in avoid] or list(board.legal_moves)
    if random.random() < _RANDOM.get(lv, 0):
        return random.choice(allowed)
    sf = stockfish()
    if sf:
        try:
            import chess.engine
            _configure(sf, lv)
            return sf.play(board, chess.engine.Limit(time=0.5 if lv >= 20 else 0.3),
                           root_moves=allowed if avoid else None).move
        except Exception:
            pass
    m = _own_best_move(board, min(seconds, 0.3 + lv * 0.11))
    return m if m in allowed else allowed[0]


def _own_best_move(board, seconds=2.5):
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
    def __init__(self, box, white_bottom, board, mon, junk=()):
        self.box = box
        self.white_bottom = white_bottom
        self.board = board
        self.mon = mon
        self.side = (box[2] - box[0]) / 8.0
        self.shot = os.path.join(T.DATA, "chess-board.png")
        self.junk = set(junk)           # squares an overlay covers: never compared
        self.covered = False
        self.tries = {}                 # square -> failed clicks (moves the click point)

    def occ(self):
        """{square: is_white} of what's REALLY on the board right now (pixels)."""
        if not _grab(self.box, self.shot):
            return None
        _IMG_CACHE.clear()
        w = self.box[2] - self.box[0]
        # is that really the board? A terminal dragged over it was once read
        # as "pieces" -> fake moves -> clicks into whatever was on top
        self.covered = _pattern_score(self.shot, (0, 0, w, w), (0, 0)) < 8
        if self.covered:
            return None
        o = colours_for(self.shot, (0, 0, w, w), (0, 0), occupancy(self.shot, (0, 0, w, w), (0, 0)))
        self.raw = o
        self.last_seen = {_sq(r, c, self.white_bottom): is_w for (r, c), is_w in o.items()
                          if _sq(r, c, self.white_bottom) not in self.junk}
        return self.last_seen

    def last_mover(self):
        """Who moved last according to the screen's highlight (from the last occ() grab)."""
        w = self.box[2] - self.box[0]
        try:
            return last_mover(self.shot, (0, 0, w, w), (0, 0), self.raw)
        except Exception:
            return None

    def expect(self, board=None):
        """{square: is_white} our copy of the game says should be there."""
        import chess
        b = board or self.board
        return {sq: p.color == chess.WHITE for sq, p in b.piece_map().items() if sq not in self.junk}

    def find_move(self, seen, plies=1):
        """The legal move (or our move + their instant reply) that turns our copy of
        the game into exactly what's on screen. -> list of moves, or None."""
        b = self.board
        for m in b.legal_moves:
            b.push(m)
            try:
                if self.expect(b) == seen:
                    return [m]
                if plies > 1:
                    for r in b.legal_moves:
                        b.push(r)
                        try:
                            if self.expect(b) == seen:
                                return [m, r]
                        finally:
                            b.pop()
            finally:
                b.pop()
        return None

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
        import chess
        pc = self.board.piece_at(sq)
        log("click", chess.square_name(sq), "| my copy:", pc.symbol() if pc else "empty",
            "| screen:", {True: "white", False: "black", None: "empty"}[self.last_seen.get(sq)]
            if hasattr(self, "last_seen") else "?")
        x, y = self.center(sq)
        # retries click elsewhere in the square: something drawn over the board
        # (the rice's desktop cat at the bottom edge) swallowed centre clicks
        dx, dy = ((0, 0), (0, -0.3), (-0.28, -0.3), (0.28, -0.3))[self.tries.get(sq, 0) % 4]
        x, y = x + dx * self.side, y + dy * self.side
        T.sh(["hyprctl", "dispatch", "movecursor", str(int(x)), str(int(y))])
        time.sleep(0.08)
        T.sh([T._wlrctl(), "pointer", "click", "left"])

    def park(self):
        """Pointer off the board so hover highlights don't look like moves."""
        T.sh(["hyprctl", "dispatch", "movecursor", str(int(self.box[2] + 60)), str(int(self.box[1] + 20))])


def occupancy(png, box, mon_off):
    """{square: True(white)/False(black)} for every occupied square, from pixels.
    A piece shows up as many pixels far from the square's own corner colour;
    black pieces are mostly dark pixels, white ones mostly outline."""
    import statistics as st
    from PIL import Image
    im = Image.open(png).convert("L")
    px = im.load()
    x1, y1 = box[0] - mon_off[0], box[1] - mon_off[1]
    side = (box[2] - box[0]) / 8.0
    out = {}
    for row in range(8):
        for col in range(8):
            X0, Y0 = x1 + col * side, y1 + row * side
            base = st.mean(px[int(X0 + dx * side), int(Y0 + dy * side)]
                           for dx in (0.08, 0.92) for dy in (0.08, 0.92))
            pts = [px[int(X0 + (0.3 + 0.4 * i / 9) * side), int(Y0 + (0.25 + 0.6 * j / 9) * side)]
                   for i in range(10) for j in range(10)]
            light = sum(1 for v in pts if v > base + 45)
            dark = sum(1 for v in pts if v < base - 45)
            if light + dark >= 10:
                out[(row, col)] = dark < 45            # True = white piece
    return out


def last_mover(png, box, mon_off, occ):
    """Who moved last, read from the last-move highlight: (True=white / False=black
    / None if no clear highlight). The highlight tints the from- and to-square the
    same way; exactly one of them holds a piece -- the one that just moved."""
    import statistics as st
    from PIL import Image
    im = Image.open(png).convert("RGB")
    px = im.load()
    x1, y1 = box[0] - mon_off[0], box[1] - mon_off[1]
    side = (box[2] - box[0]) / 8.0
    cols = {}
    for row in range(8):
        for col in range(8):
            X0, Y0 = x1 + col * side, y1 + row * side
            # top-right + bottom-left corners: clear of the rank/file labels
            pts = [px[int(X0 + 0.86 * side), int(Y0 + 0.14 * side)],
                   px[int(X0 + 0.14 * side), int(Y0 + 0.86 * side)]]
            cols[(row, col)] = tuple((pts[0][i] + pts[1][i]) / 2 for i in range(3))
    delta = {}
    for parity in (0, 1):
        sq = [k for k in cols if (k[0] + k[1]) % 2 == parity]
        med = tuple(st.median(cols[k][i] for k in sq) for i in range(3))
        for k in sq:
            d = tuple(cols[k][i] - med[i] for i in range(3))
            mag = sum(abs(v) for v in d)
            red_check = d[0] > 25 and d[0] > d[1] + 25          # the red "in check" mark
            if mag > 45 and not red_check:
                delta[k] = d
    best = None
    keys = sorted(delta)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            occ_a, occ_b = a in occ, b in occ
            if occ_a == occ_b:
                continue                 # one square empty (from), one occupied (to)
            da, db = delta[a], delta[b]
            # the two highlight squares are tinted the SAME way (a check marker
            # darkens a square differently): compare tint directions
            dot = sum(x * y for x, y in zip(da, db))
            na = sum(x * x for x in da) ** 0.5
            nb = sum(y * y for y in db) ** 0.5
            cos = dot / (na * nb) if na and nb else 0
            if cos < 0.8:
                continue
            score = (sum(abs(v) for v in da) + sum(abs(v) for v in db)) * cos
            if not best or score > best[0]:
                best = (score, b if occ_b else a)
    if not best or best[0] < 120:
        return None
    return occ[best[1]]


TEMPLATES = os.path.join(T.DATA, "chess-pieces.json")


def colour_feats(png, box, mon_off, squares):
    """Per square: (parity, (dark, light, delta, p70)) -- how a piece changes its
    square. No single rule works for every piece set (classic white pieces have
    thick black outlines), so these are LEARNED per theme at the start."""
    import statistics as st
    px, _ = _pixels(png)
    x1, y1 = box[0] - mon_off[0], box[1] - mon_off[1]
    side = (box[2] - box[0]) / 8.0
    out = {}
    for (r, c) in squares:
        X0, Y0 = x1 + c * side, y1 + r * side
        base = st.median(px[int(X0 + dx * side), int(Y0 + dy * side)]
                         for dx in (0.07, 0.93) for dy in (0.07, 0.93))
        pts = [px[int(X0 + (0.2 + 0.6 * i / 11) * side), int(Y0 + (0.15 + 0.7 * j / 11) * side)]
               for i in range(12) for j in range(12)]
        n = len(pts)
        dark = sum(v < base - 45 for v in pts) / n
        light = sum(v > base + 25 for v in pts) / n
        delta = (st.mean(pts) - base) / 100
        pp = sorted(v for v in pts if abs(v - base) > 25)
        p70 = ((pp[int(len(pp) * 0.7)] if pp else base) - base) / 100
        out[(r, c)] = ((r + c) % 2, (dark, light, delta, p70))
    return out


def learn_colours(feats, white_rc):
    """Centroids of white/black pieces on light/dark squares, from the start."""
    import statistics as st
    occ = list(feats)
    cent = {}
    for par in (0, 1):
        for w in (True, False):
            vs = [feats[rc][1] for rc in occ if feats[rc][0] == par and (rc in white_rc) == w]
            if not vs:
                return None
            cent["%d%d" % (par, w)] = [st.mean(v[i] for v in vs) for i in range(4)]
    sd = [st.pstdev([feats[rc][1][i] for rc in occ]) or 1 for i in range(4)]
    # every learned piece kept too: a detailed piece (the classic queen) sits
    # between the two averages but right next to its own learned example
    samples = [[feats[rc][0], list(feats[rc][1]), rc in white_rc] for rc in occ]
    return {"cent": cent, "sd": sd, "samples": samples}


def classify_colours(feats, model):
    """{(r, c): is_white} by nearest learned centroid."""
    cent, sd = model["cent"], model["sd"]
    samples = model.get("samples")
    out = {}
    for rc, (par, v) in feats.items():
        if samples:
            # nearest learned piece on the same square colour decides
            same = [smp for smp in samples if smp[0] == par] or samples
            near = min(same, key=lambda smp: sum(((v[i] - smp[1][i]) / sd[i]) ** 2 for i in range(4)))
            out[rc] = bool(near[2])
            continue
        dw = sum(((v[i] - cent["%d1" % par][i]) / sd[i]) ** 2 for i in range(4))
        db = sum(((v[i] - cent["%d0" % par][i]) / sd[i]) ** 2 for i in range(4))
        out[rc] = dw < db
    return out


def is_start(occ_rc):
    """Fresh-game layout from WHERE the pieces are (no colours needed)."""
    return len(occ_rc) == 32 and all((r, c) in occ_rc for r in (0, 1, 6, 7) for c in range(8))


def start_side(feats):
    """Which side's 16 pieces are white at the start: the brighter group."""
    import statistics as st
    top = st.mean(feats[(r, c)][1][2] for r in (0, 1) for c in range(8))
    bot = st.mean(feats[(r, c)][1][2] for r in (6, 7) for c in range(8))
    return bot > top                    # True = white at the bottom


def _theme(box):
    """Learned shapes + colours for a board this size, or None."""
    try:
        t = json.load(open(TEMPLATES))
    except (OSError, ValueError):
        return None
    sizes = t.get("sizes") or ({str(t["side"]): {"pieces": t["pieces"]}} if "side" in t else {})
    side = box[2] - box[0]
    near = [k for k in sizes if abs(int(k) - side) <= 6]
    if not near:
        return None
    th = sizes[min(near, key=lambda k: abs(int(k) - side))]
    return th if isinstance(th, dict) else {"pieces": th}
_MASK = 20                              # piece silhouette grid (20x20)


def piece_masks(png, box, mon_off):
    """{(row, col): silhouette bits} -- pixels that differ from the square's own
    colour, so the same piece looks the same on light, dark or highlighted squares."""
    import statistics as st
    px, (W, H) = _pixels(png)
    x1, y1 = box[0] - mon_off[0], box[1] - mon_off[1]
    side = (box[2] - box[0]) / 8.0
    out = {}
    for row in range(8):
        for col in range(8):
            X0, Y0 = x1 + col * side, y1 + row * side
            base = st.mean(px[int(X0 + dx * side), int(Y0 + dy * side)]
                           for dx in (0.08, 0.92) for dy in (0.08, 0.92))
            bits = []
            for j in range(_MASK):
                for i in range(_MASK):
                    # middle 70% only: corner labels (a-h, 1-8) and things lying
                    # on the board's edge stay out of the silhouette
                    v = px[int(X0 + (0.15 + 0.7 * (i + 0.5) / _MASK) * side),
                           int(Y0 + (0.12 + 0.7 * (j + 0.5) / _MASK) * side)]
                    bits.append(1 if abs(v - base) > 40 else 0)
            out[(row, col)] = bits
    return out


def learn_pieces(png, box, mon_off, wb):
    """Fresh game = every piece on a known square: remember what each looks like
    (shape) and what each side's colour looks like in this theme."""
    import chess
    masks = piece_masks(png, box, mon_off)
    start = chess.Board()
    tpl = []
    for (row, col), bits in masks.items():
        pc = start.piece_at(_sq(row, col, wb))
        if pc:
            tpl.append([pc.symbol(), bits])
    occ_rc = [(r, c) for r in (0, 1, 6, 7) for c in range(8)]
    white_rc = {(r, c) for r in ((6, 7) if wb else (0, 1)) for c in range(8)}
    colours = learn_colours(colour_feats(png, box, mon_off, occ_rc), white_rc)
    try:
        try:
            allsz = json.load(open(TEMPLATES)).get("sizes", {})
        except (OSError, ValueError):
            allsz = {}
        allsz[str(int(box[2] - box[0]))] = {"pieces": tpl, "colours": colours}   # per board size
        with open(TEMPLATES, "w") as f:
            json.dump({"sizes": allsz}, f)
        log("learned", len(tpl), "piece shapes for a %d px board" % int(box[2] - box[0]))
    except OSError:
        pass


def recognise(png, box, mon_off, want, wb):
    """{square: symbol} for every occupied square, from the learned shapes --
    None if there are no shapes for this board size."""
    t = _theme(box)
    if not t or not t.get("pieces"):
        return None                     # never seen a board this size: shapes don't apply
    masks = piece_masks(png, box, mon_off)
    ranked = {}                         # square -> [(score, symbol)] best first
    for (row, col), bits in masks.items():
        sq = _sq(row, col, wb)
        if sq not in want:
            continue
        best = {}
        for sym, tb in t["pieces"]:
            if sym.isupper() != want[sq]:
                continue                # pixels already know the colour
            inter = sum(a & b for a, b in zip(bits, tb))
            union = sum(a | b for a, b in zip(bits, tb)) or 1
            best[sym] = max(best.get(sym, 0), inter / union)
        ranked[sq] = sorted(((v, k) for k, v in best.items()), reverse=True)
    return _resolve(ranked)


_MAX = {"k": 1, "q": 1, "r": 2, "b": 2, "n": 2, "p": 8}


def _resolve(ranked):
    """Best shape per square, then chess sense: while a side has more of a piece
    than a real game allows (4 knights...), the least sure square takes its
    next-best shape."""
    pick = {sq: 0 for sq in ranked}
    for _ in range(64):
        counts = {}
        for sq, i in pick.items():
            sym = ranked[sq][i][1]
            counts.setdefault(sym, []).append(sq)
        over = [(sym, sqs) for sym, sqs in counts.items() if len(sqs) > _MAX[sym.lower()]]
        if not over:
            break
        sym, sqs = over[0]
        # the square whose best guess is least ahead of its next option gives way
        movable = [sq for sq in sqs if pick[sq] + 1 < len(ranked[sq])]
        if not movable:
            break
        sq = min(movable, key=lambda q: ranked[q][pick[q]][0] - ranked[q][pick[q] + 1][0])
        pick[sq] += 1
    return {sq: ranked[sq][i][1] if ranked[sq] else None for sq, i in pick.items()}


def plausible(board):
    """Could this position come from a real game? (rejects '3 white bishops')"""
    import chess
    for color in (chess.WHITE, chess.BLACK):
        n = {pt: len(board.pieces(pt, color)) for pt in range(1, 7)}
        extra = max(0, n[2] - 2) + max(0, n[3] - 2) + max(0, n[4] - 2) + max(0, n[5] - 1)
        if n[6] != 1 or n[1] + extra > 8:
            return False
    return board.is_valid()


def _sq(row, col, white_bottom):
    import chess
    f = col if white_bottom else 7 - col
    r = 7 - row if white_bottom else row
    return chess.square(f, r)


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
    # wait until the game is REALLY in front: a fixed 0.5 s once screenshotted
    # the terminal workspace and the AI (rightly) saw no chessboard
    for _ in range(30):
        time.sleep(0.1)
        act = T._active_window() or {}
        cur = next((c for c in T._clients() if c.get("address") == w["address"]), None)
        if act.get("address") == w["address"] and cur and T.window_on_screen(cur)[0]:
            w = cur
            break
    else:
        return None, "couldn't bring the chess window to the front"
    time.sleep(0.25)                    # let the workspace animation finish
    mon = T._monitor()
    mx, my = mon.get("x", 0), mon.get("y", 0)
    shot = os.path.join(T.DATA, "chess-setup.jpg")
    with T._TarHidden():
        if not T._shot(shot):
            return None, "couldn't take a screenshot"
    png = shot[:-4] + ".png"
    from PIL import Image
    Image.open(shot).save(png)
    c = _cached_box(w)
    if c and _pattern_score(png, tuple(c["box"]), (mx, my)) >= 8:
        r = _position(png, tuple(c["box"]), mon, c.get("white_bottom", True), "", guess_side=True)
        if r[0]:
            return r
    T.mini_say("finding the board…")
    # only the browser window goes to the AI (no terminal / T.A.R. to confuse it);
    # its answer is mapped back to screen coordinates below
    wx, wy = (w.get("at") or [mx, my])
    ww, wh = (w.get("size") or [mon.get("width", 1920), mon.get("height", 1080)])
    wshot = os.path.join(T.DATA, "chess-window.jpg")
    Image.open(png).crop((wx - mx, wy - my, wx - mx + ww, wy - my + wh)).convert("RGB").save(wshot)
    txt, err = _vision(wshot, (
        "This screenshot shows a chess game in a browser. Reply with ONLY JSON: "
        "{\"box_2d\": [ymin, xmin, ymax, xmax], \"white_at_bottom\": true, "
        "\"moves\": \"1. e4 e5 2. Nf3\"} -- box_2d = the 8x8 squares only (NOT the coordinate "
        "letters around them), normalized 0-1000. white_at_bottom = are the white pieces' home "
        "ranks at the bottom (rank 1 at the bottom). moves = the FULL move list if it is all "
        "visible, in algebraic notation (write K Q R B N even if icons are shown); \"\" if no moves "
        "were played or the list is cut off. If there is no chessboard: {\"box_2d\": null}."),
        max_tokens=800)
    if err:
        return None, err
    try:
        d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except (AttributeError, ValueError):
        return None, "couldn't read the board"
    bx = d.get("box_2d") or d.get("board")
    if not bx or len(bx) != 4:
        return None, "no chessboard visible on screen"
    def to_box(x1, y1, x2, y2):
        # 0-1000 of the WINDOW crop -> screen pixels
        b = [wx + x1 / 1000 * ww, wy + y1 / 1000 * wh, wx + x2 / 1000 * ww, wy + y2 / 1000 * wh]
        side = ((b[2] - b[0]) + (b[3] - b[1])) / 2              # it's a square
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    # models disagree on [y,x] vs [x,y] order: keep whichever fits the 8x8 pattern
    best = max(((snap_board(png, to_box(*o), (mx, my))) for o in
                ((bx[1], bx[0], bx[3], bx[2]), (bx[0], bx[1], bx[2], bx[3]))), key=lambda r: r[1])
    box, score = best
    if score < 8:
        return None, "couldn't line up the board's squares (score %.1f) -- is the whole board visible?" % score
    wb = bool(d.get("white_at_bottom", True))
    _save_cache(w, box, wb)
    return _position(png, box, mon, wb, d.get("moves") or "", guess_side=False)


CACHE = os.path.join(T.DATA, "chess-board.json")


def _save_cache(w, box, wb):
    try:
        with open(CACHE, "w") as f:
            json.dump({"addr": w["address"], "at": w.get("at"), "size": w.get("size"),
                       "box": list(box), "white_bottom": wb, "t": time.time()}, f)
    except OSError:
        pass


def _cached_box(w):
    """The board box from last time, if the window hasn't moved or resized."""
    try:
        c = json.load(open(CACHE))
    except (OSError, ValueError):
        return None
    if c.get("addr") == w["address"] and c.get("at") == w.get("at") and c.get("size") == w.get("size"):
        return c
    return None


def colours_for(png, box, mon_off, occ):
    """Replace the rule-of-thumb colours with the learned ones (when learned)."""
    th = _theme(box)
    if not th or not th.get("colours"):
        return occ
    return classify_colours(colour_feats(png, box, mon_off, list(occ)), th["colours"])


def _position(png, box, mon, wb, moves, guess_side):
    """Board state from the pixels (+ the move list, + one labelling call only
    when joining a game that's already going)."""
    import chess
    from PIL import Image
    mx, my = mon.get("x", 0), mon.get("y", 0)
    occ = occupancy(png, box, (mx, my))              # (row, col) -> is_white (rule of thumb)
    # a fresh game is recognised from WHERE the pieces are -- no colours, no AI;
    # the brighter group of 16 is white, for any piece set
    if is_start(set(occ)):
        bottom_white = start_side(colour_feats(png, box, (mx, my), list(occ)))
        learn_pieces(png, box, (mx, my), bottom_white)
        return Player(box, bottom_white, chess.Board(), mon), None
    occ = colours_for(png, box, (mx, my), occ)       # learned colours, if this theme was seen
    if guess_side:
        # cached board, game in progress: which side is ours? white's pieces
        # are mostly in the half nearest the white player
        low = sum(1 for (r, c), w_ in occ.items() if w_ and r >= 4) - \
            sum(1 for (r, c), w_ in occ.items() if w_ and r < 4)
        wb = low >= 0
    want = {_sq(r, c, wb): is_w for (r, c), is_w in occ.items()}

    def matches(b):
        have = {sq: p.color == chess.WHITE for sq, p in b.piece_map().items()}
        return have == want

    # 1) the move list, if it replays to exactly what's on the board (keeps castling/turn)
    board = chess.Board()
    ok = True
    for san in [t for t in re.split(r"\s+", re.sub(r"\d+\.(\.\.)?", " ", moves))
                if t and not re.match(r"^(1-0|0-1|1/2-1/2|\*)$", t)]:
        try:
            board.push_san(san.replace("0-0-0", "O-O-O").replace("0-0", "O-O"))
        except ValueError:
            ok = False
            break
    if ok and matches(board):
        return Player(box, wb, board, mon), None
    shapes = recognise(png, box, (mx, my), want, wb)
    # 2) learned piece shapes (no AI): accepted only if the position is plausible
    if shapes:
        b2 = chess.Board(None)
        for sq, sym in shapes.items():
            if sym:
                b2.set_piece_at(sq, chess.Piece.from_symbol(sym))
        b2.turn = chess.WHITE if wb else chess.BLACK
        lm0 = last_mover(png, box, (mx, my), occ)
        if lm0 is not None:
            b2.turn = not lm0
        _castling_from_board(b2)
        if plausible(b2):
            log("read the position from learned piece shapes:", b2.fen())
            return Player(box, wb, b2, mon), None
        log("learned shapes gave an impossible position -- asking the AI")
    # 3) read the position off the board: pixels say WHERE and which colour,
    #    the model only names the piece type on each occupied square
    crop = os.path.join(T.DATA, "chess-crop.jpg")
    Image.open(png).crop((box[0] - mx, box[1] - my, box[2] - mx, box[3] - my)).convert("RGB").save(crop)
    names = {sq: chess.square_name(sq) for sq in want}
    T.mini_say("reading the position…")
    txt, err = _vision(crop, (
        "This is a chessboard%s. These squares have pieces: %s. For EACH of them say which "
        "piece it is. Reply with ONLY JSON like {\"e1\": \"K\", \"d8\": \"q\"} -- uppercase = "
        "white, lowercase = black, letters K Q R B N P. If a listed square has NO chess piece "
        "(something else covers it, e.g. a bar or popup), answer \"-\" for it." % (
            " (white at the bottom: a1 bottom-left)" if wb else " (black at the bottom: h1 bottom-left)",
            ", ".join("%s %s" % (names[sq], "white" if is_w else "black") for sq, is_w in sorted(want.items()))
        )), max_tokens=600)
    if err:
        return None, err
    try:
        labels = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except (AttributeError, ValueError):
        return None, "couldn't read which pieces are where"
    board = chess.Board(None)
    junk = set()
    for sq, is_w in want.items():
        t = str(labels.get(names[sq], "P")).strip()[:1].upper()
        if t in ("-", ".", "", "0", "N/A"[:1]) and t != "N":
            junk.add(sq)
            continue                    # an overlay (T.A.R.'s own voice bar once), not a piece
        pt = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP,
              "N": chess.KNIGHT}.get(t, chess.PAWN)
        if pt == chess.PAWN and chess.square_rank(sq) in (0, 7):
            junk.add(sq)
            continue                    # pawns never stand there: screen junk
        board.set_piece_at(sq, chess.Piece(pt, chess.WHITE if is_w else chess.BLACK))
    me = chess.WHITE if wb else chess.BLACK
    lm = last_mover(png, box, (mx, my), occ)
    board.turn = me if lm is None else (not lm)   # whoever didn't move last
    _castling_from_board(board)
    if not plausible(board):
        return None, ("couldn't read the pieces reliably (got an impossible position) -- start a "
                      "new game and say 'play chess' at the start, so I can learn the pieces")
    return Player(box, wb, board, mon, junk), None


def _castling_from_board(board):
    import chess
    rights = ""
    if board.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE):
        if board.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE): rights += "K"
        if board.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE): rights += "Q"
    if board.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK):
        if board.piece_at(chess.H8) == chess.Piece(chess.ROOK, chess.BLACK): rights += "k"
        if board.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK): rights += "q"
    board.set_castling_fen(rights or "-")


def _running(name):
    """Is a process with this exact name running (lock screens)?"""
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if open("/proc/%s/comm" % d).read().strip() == name:
                    return True
            except OSError:
                pass
    return False


def play(minutes=30):
    err = ensure_chess()                # BEFORE importing it: first run installs it
    if err:
        T.loop_update(status="failed", last=err)
        return 1
    import chess
    log("=== new chess task")
    p, err = setup()
    if not p:
        log("setup failed:", err)
        T.loop_update(status="failed", last=err)
        T.mini_say("chess: " + T.short_say(err, 8))
        return 1
    me = chess.WHITE if p.white_bottom else chess.BLACK
    log("setup ok: me", "white" if me else "black", "| turn", "white" if p.board.turn else "black",
        "| fen", p.board.fen(), "| box", p.box)
    _upd = T.loop_update

    def _logged_update(**k):
        if k.get("last") or k.get("status"):
            log("status:", k.get("status", ""), "|", k.get("last", ""))
        return _upd(**k)
    T.loop_update = _logged_update
    lv0 = current_level()
    T.loop_update(status="playing", level=lv0, last="found the board, playing %s with %s at level %d (%s)" % (
        "white" if me else "black", "Stockfish" if stockfish() else "the built-in engine", lv0, level_band(lv0)))
    p.park()
    rnd, misses, lost, not_ours, stuck, quiet, checked_at = 0, 0, 0, 0, 0, 0, -1
    failed, fails_total = {}, 0          # moves that didn't work on screen
    resyncs_in_row, just_resynced = 0, False
    until = time.time() + minutes * 60

    def resync(why):
        # our copy of the game disagrees with the screen: read the board again
        nonlocal p
        T.mini_say("re-reading the board…")
        T.loop_update(last="re-reading the board (%s)" % why)
        log("resync because:", why)
        q, e2 = setup()
        if q:
            q.tries = dict(p.tries)                 # keep where clicks failed...
            q.junk |= p.junk                        # ...and which squares are covered
            q.white_bottom = (me == chess.WHITE)     # our colour never changes mid-game
            q.occ()
            lm2 = q.last_mover()
            q.board.turn = me if lm2 is None else (not lm2)
            p = q
            p.park()
            return True
        T.loop_update(last="couldn't re-read the board: %s" % e2)
        return False

    def apply(moves, who):
        for m in moves:
            san = p.board.san(m)
            log("read move from screen:", san, "| fen before", p.board.fen())
            p.board.push(m)
            T.mini_say(("they played " if p.board.turn == me else "played ") + san)
            T.loop_update(last="%s %s" % ("they played" if p.board.turn == me else "played", san))

    last_tick = time.time()
    while T.loop_mine() and time.time() < until:
        d = T.loop_mine() or {}
        now = time.time()
        gap, last_tick = now - last_tick, now
        T.loop_update(beat=now)          # heartbeat: the orb can tell "frozen" from "waiting"
        if gap > 20:
            # the machine slept (lid closed / screen off): the board may have moved,
            # a lock screen may still be up -- wait for a usable screen, then find
            # the board fresh instead of trusting what we knew before
            log("woke up after %ds -- re-finding the board" % gap)
            T.loop_update(status="waking up", last="the screen was off for %d s -- finding the board again" % gap)
            T.mini_say("chess: back, finding the board…")
            until += gap                 # the time asleep doesn't count
            for _ in range(60):          # up to ~1 min for the lock screen to go away
                if not any(_running(n) for n in ("hyprlock", "swaylock", "gtklock")):
                    break
                time.sleep(1)
            resync("the system was asleep")
            continue
        if d.get("paused"):
            time.sleep(1)
            continue
        ok, why = T.task_window_ok()
        if not ok:
            if "closed" in why:
                # the game's tab/window is gone: nothing to wait for
                log("stopping:", why)
                T.loop_update(status="failed", last="stopped: the chess window was closed")
                T.mini_say("chess: the game window is gone, stopped")
                break
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
        seen = p.occ()
        if seen is None:
            if p.covered:
                T.loop_update(status="board covered", last="the board is covered by another window -- waiting")
                T.mini_say("chess: board is covered, waiting")
            time.sleep(1)
            continue
        if seen != p.expect():
            # the screen moved on without us knowing: work out the move(s)
            time.sleep(0.35)
            seen2 = p.occ()
            if seen2 != seen:
                continue                 # still animating
            found = p.find_move(seen2, plies=2)
            if found:
                apply(found, "they")
                lost = quiet = resyncs_in_row = 0
                just_resynced = False
                continue
            lost += 1
            if lost >= 3:
                diff = {sq for sq in set(seen2) | set(p.expect()) if seen2.get(sq) != p.expect().get(sq)}
                if just_resynced and diff and len(diff) <= 4:
                    # a FRESH read still disagrees on the same few squares: something
                    # is drawn over them (the desktop cat once) -- ignore those squares
                    p.junk |= diff
                    log("ignoring covered squares:", ", ".join(sorted(chess.square_name(q) for q in diff)))
                    T.loop_update(last="ignoring squares something covers: %s" % ", ".join(
                        sorted(chess.square_name(q) for q in diff)))
                    just_resynced = False
                elif resyncs_in_row >= 3:
                    why = "the board keeps disagreeing with every legal move -- stopped (is something covering the board?)"
                    log("giving up:", why)
                    T.loop_update(status="failed", last=why)
                    T.mini_say("chess: stopped, can't read the board")
                    break
                else:
                    resync("the board doesn't match any legal move")
                    resyncs_in_row += 1
                    just_resynced = True
                lost = 0
            time.sleep(0.6)
            continue
        lost = 0
        if p.board.turn != me:
            # waiting for them -- unless the screen says it's really OUR move
            # (they already moved last, or a fresh game where we're white):
            # waiting would hang both sides forever
            lm = p.last_mover()
            ours = (lm is not None and lm != me) or (
                lm is None and me == chess.WHITE and p.board.board_fen() == chess.STARTING_BOARD_FEN)
            if ours:
                stuck += 1
                if stuck >= 8:           # ~5 s: our copy has the turn wrong
                    resync("it's my move on the screen")
                    stuck = 0
            else:
                stuck = 0
                quiet += 1
                # no clue on screen and nothing moved for ~30 s: check ONCE per
                # position (a person may think for minutes -- don't keep calling the AI)
                if quiet >= 50 and lm is None and checked_at != len(p.board.move_stack):
                    checked_at = len(p.board.move_stack)
                    resync("nothing happened for a while")
                    quiet = 0
            T.loop_update(status="waiting for them")
            time.sleep(0.6)
            continue
        # the screen must agree it's our turn: the piece on the last-move
        # highlight is the side that just moved
        lm = p.last_mover()
        if lm is not None and lm == me:
            not_ours += 1
            T.loop_update(status="waiting for them")
            if not_ours >= 10:           # ~6 s of disagreement: our copy is off
                resync("the screen says I moved last")
                not_ours = 0
            time.sleep(0.6)
            continue
        not_ours = 0
        # our move
        rnd += 1
        T.loop_update(status="thinking", round=rnd)
        T.mini_say("thinking…")
        avoid = {mv for (fen, mv), n in failed.items() if fen == p.board.board_fen() and n >= 4}
        m = best_move(p.board, avoid=avoid)
        san = p.board.san(m)
        if m.from_square not in p.junk and seen.get(m.from_square) != (me == chess.WHITE):
            # never touch a piece that isn't ours on the REAL board
            # (a covered square can't be checked: there we trust our copy)
            T.loop_update(last="the screen doesn't show my piece on %s -- re-reading"
                          % chess.square_name(m.from_square))
            if resyncs_in_row >= 3:
                why = "my copy of the game keeps disagreeing with the board -- stopped"
                log("giving up:", why)
                T.loop_update(status="failed", last=why)
                T.mini_say("chess: stopped, can't read the board")
                break
            resync("my piece isn't where I think")
            resyncs_in_row += 1
            continue
        T.mini_say("playing " + san)
        p.click_sq(m.from_square)
        time.sleep(0.25)
        p.click_sq(m.to_square)
        if m.promotion:
            time.sleep(0.5)
            p.click_sq(m.to_square)      # chess.com: the queen sits on the target square
        p.park()
        time.sleep(0.9)
        after = p.occ()
        if after is None and p.covered:
            # covered right after our clicks: once the board shows again, the
            # normal check works out whether our move (and a reply) happened
            time.sleep(1)
            continue
        p.board.push(m)
        if after == p.expect():
            T.loop_update(round=rnd, clicks=rnd, last="played %s" % san, status="waiting for them")
            misses = fails_total = resyncs_in_row = 0
            p.tries.clear()
            continue
        reply = p.find_move(after) if after else None
        if reply:                        # the bot answered instantly
            T.loop_update(round=rnd, clicks=rnd, last="played %s" % san)
            apply(reply, "they")
            misses = 0
            continue
        p.board.pop()                    # the move didn't happen on screen
        # Was it really our turn? Only then is it the MOVE's fault. If the
        # screen says we moved last (or can't tell), it's a turn mix-up: wait
        # / re-read -- never mark a good move as unplayable or give up over it.
        lm_now = p.last_mover() if after else None
        if lm_now is None or lm_now == me:
            log("move %s didn't land but the screen %s -- not blaming the move" % (
                san, "says I moved last" if lm_now == me else "doesn't show whose turn it is"))
            misses += 1
            if misses >= 2:
                resync("not sure whose turn it is")
                misses = 0
            time.sleep(1.0)
            continue
        key = (p.board.board_fen(), m)
        failed[key] = failed.get(key, 0) + 1
        fails_total += 1
        for sq_ in (m.from_square, m.to_square):
            p.tries[sq_] = p.tries.get(sq_, 0) + 1
        log("move failed:", san, "x%d" % failed[key], "| total fails", fails_total,
            "| screen confirms it was my turn")
        if fails_total >= 6:
            # never drag the user's mouse around forever
            why = "my moves aren't landing on the board (tried %s) -- stopped. Is the game over or the board covered?" % san
            log("giving up:", why)
            T.loop_update(status="failed", last=why)
            T.mini_say("chess: stopped, my moves weren't landing")
            break
        misses += 1
        T.loop_update(last="tried %s but the board didn't change -- retrying" % san)
        if misses >= 2:
            # never guess whose turn it is: read the board (and the turn) again
            resync("my move didn't register")
            misses = 0
        time.sleep(1.0)
    try:
        os.remove(T.LOOP)
    except OSError:
        pass
    if _SF:
        try:
            _SF.quit()
        except Exception:
            pass
    T.mini(on=False, say="")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "play":
        mins = int(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 30
        T.MINI_HOLD = 90
        try:
            rc = play(mins)
        except Exception as e:
            # never die silently: the card / chat must say what broke
            import traceback
            traceback.print_exc()
            T.loop_update(status="failed", last="chess crashed: %s" % e)
            T.mini_say("chess crashed: " + T.short_say(str(e), 8))
            rc = 1
        sys.exit(rc)
    print(__doc__)
