"""Screen tiling model: a cols x rows grid where each player owns a rectangle of cells."""

from __future__ import annotations

from dataclasses import dataclass, field

# Grid presets (cols, rows) per player count; the first entry is the default.
PRESETS: dict[int, list[tuple[int, int]]] = {
    1: [(1, 1)],
    2: [(2, 1), (1, 2)],
    3: [(2, 2)],
    4: [(2, 2)],
    5: [(3, 2), (2, 3)],
    6: [(3, 2), (2, 3)],
    7: [(4, 2), (3, 3)],
    8: [(4, 2), (3, 3)],
}


@dataclass(frozen=True)
class Rect:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1

    def cells(self) -> set[tuple[int, int]]:
        return {(r, c) for r in range(self.row, self.row + self.rowspan)
                for c in range(self.col, self.col + self.colspan)}

    def contains(self, row: int, col: int) -> bool:
        return (self.row <= row < self.row + self.rowspan
                and self.col <= col < self.col + self.colspan)


@dataclass
class Layout:
    cols: int
    rows: int
    rects: list[Rect] = field(default_factory=list)  # index = player

    @property
    def count(self) -> int:
        return len(self.rects)

    # ---- construction ----------------------------------------------------
    @classmethod
    def default(cls, count: int, grid: tuple[int, int] | None = None) -> "Layout":
        """One cell per player in reading order; spare cells stay blank (shown as "add player")."""
        cols, rows = grid or PRESETS[count][0]
        cells = [(r, c) for r in range(rows) for c in range(cols)]
        return cls(cols, rows, [Rect(r, c) for r, c in cells[:count]])

    # ---- queries ---------------------------------------------------------
    def occupied(self, exclude: int | None = None) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for i, r in enumerate(self.rects):
            if i != exclude:
                out |= r.cells()
        return out

    def free_cells(self) -> list[tuple[int, int]]:
        occ = self.occupied()
        return [(r, c) for r in range(self.rows) for c in range(self.cols) if (r, c) not in occ]

    def player_at(self, row: int, col: int) -> int | None:
        for i, r in enumerate(self.rects):
            if r.contains(row, col):
                return i
        return None

    def in_bounds(self, rect: Rect) -> bool:
        return (rect.row >= 0 and rect.col >= 0 and rect.rowspan >= 1 and rect.colspan >= 1
                and rect.row + rect.rowspan <= self.rows and rect.col + rect.colspan <= self.cols)

    def can_place(self, player: int, rect: Rect) -> bool:
        return self.in_bounds(rect) and not (rect.cells() & self.occupied(exclude=player))

    def is_valid(self) -> bool:
        seen: set[tuple[int, int]] = set()
        for r in self.rects:
            if not self.in_bounds(r) or r.cells() & seen:
                return False
            seen |= r.cells()
        return True

    # ---- edits -----------------------------------------------------------
    def swap(self, a: int, b: int) -> None:
        self.rects[a], self.rects[b] = self.rects[b], self.rects[a]

    def move_to_cell(self, player: int, row: int, col: int) -> bool:
        """Drop `player` on a cell: swap with its owner, or move (as a single cell) to a blank."""
        other = self.player_at(row, col)
        if other is not None:
            if other != player:
                self.swap(player, other)
            return True
        rect = self.rects[player]
        moved = Rect(row, col, rect.rowspan, rect.colspan)
        if not self.can_place(player, moved):
            moved = Rect(row, col)
        self.rects[player] = moved
        return True

    def grow(self, player: int, direction: str) -> bool:
        """Extend a player one cell in 'up' | 'down' | 'left' | 'right' if that space is blank."""
        r = self.rects[player]
        cand = {
            "up": Rect(r.row - 1, r.col, r.rowspan + 1, r.colspan),
            "down": Rect(r.row, r.col, r.rowspan + 1, r.colspan),
            "left": Rect(r.row, r.col - 1, r.rowspan, r.colspan + 1),
            "right": Rect(r.row, r.col, r.rowspan, r.colspan + 1),
        }[direction]
        if self.can_place(player, cand):
            self.rects[player] = cand
            return True
        return False

    def can_grow(self, player: int, direction: str) -> bool:
        saved = self.rects[player]
        ok = self.grow(player, direction)
        self.rects[player] = saved
        return ok

    def shrink(self, player: int) -> None:
        r = self.rects[player]
        self.rects[player] = Rect(r.row, r.col)

    # ---- geometry --------------------------------------------------------
    def pixel_rects(self, width: int, height: int, gap: int = 0) -> list[tuple[int, int, int, int]]:
        """(x, y, w, h) for each player inside a width x height area."""
        xs = [round(c * width / self.cols) for c in range(self.cols + 1)]
        ys = [round(r * height / self.rows) for r in range(self.rows + 1)]
        half = gap // 2
        out = []
        for r in self.rects:
            x0, x1 = xs[r.col], xs[r.col + r.colspan]
            y0, y1 = ys[r.row], ys[r.row + r.rowspan]
            l = half if r.col > 0 else 0
            t = half if r.row > 0 else 0
            rr = half if r.col + r.colspan < self.cols else 0
            b = half if r.row + r.rowspan < self.rows else 0
            out.append((x0 + l, y0 + t, x1 - x0 - l - rr, y1 - y0 - t - b))
        return out

    # ---- adding / removing players --------------------------------------
    def fits_in(self, cols: int, rows: int) -> bool:
        return all(r.row + r.rowspan <= rows and r.col + r.colspan <= cols for r in self.rects)

    def add_player(self, cell: tuple[int, int] | None = None) -> bool:
        """Add a player on a blank `cell`, or (None) on a grid enlarged to the next preset."""
        if cell is not None:
            if cell not in self.free_cells():
                return False
            self.rects.append(Rect(*cell))
            return True
        n = self.count + 1
        if n > max(PRESETS):
            return False
        # Prefer the smallest preset that keeps every tile where it is and gains a column.
        for count in range(n, max(PRESETS) + 1):
            grids = sorted(PRESETS[count], key=lambda g: (g[0] * g[1], g[0] <= self.cols))
            for cols, rows in grids:
                if cols * rows > self.cols * self.rows and self.fits_in(cols, rows):
                    self.cols, self.rows = cols, rows
                    self.rects.append(Rect(*self.free_cells()[0]))
                    return True
        self.__init__(*PRESETS[n][0], Layout.default(n).rects)
        return True

    def remove_player(self, index: int) -> None:
        """Drop a player, then shrink the grid if the remaining tiles allow it."""
        del self.rects[index]
        n = self.count
        if n == 0:
            return
        fitting = [g for g in sorted(PRESETS[n], key=lambda g: g[0] * g[1])
                   if g[0] * g[1] < self.cols * self.rows and self.fits_in(*g)]
        if fitting:
            self.cols, self.rows = fitting[0]
            return
        if any(r.rowspan > 1 or r.colspan > 1 for r in self.rects):
            return  # keep a hand-made arrangement
        grids = PRESETS[n]
        landscape = self.cols >= self.rows
        cols, rows = next((g for g in grids if (g[0] >= g[1]) == landscape), grids[0])
        if cols * rows >= self.cols * self.rows:
            return
        order = sorted(range(n), key=lambda i: (self.rects[i].row, self.rects[i].col))
        cells = [(r, c) for r in range(rows) for c in range(cols)]
        rects = list(self.rects)
        for cell, i in zip(cells, order):
            rects[i] = Rect(*cell)
        self.cols, self.rows, self.rects = cols, rows, rects

    def cell_rects(self, width: int, height: int, gap: int = 0) -> dict[tuple[int, int], tuple[int, int, int, int]]:
        """(x, y, w, h) of every single cell, laid out like pixel_rects."""
        probe = Layout(self.cols, self.rows,
                       [Rect(r, c) for r in range(self.rows) for c in range(self.cols)])
        cells = [(r.row, r.col) for r in probe.rects]
        return dict(zip(cells, probe.pixel_rects(width, height, gap)))

    # ---- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"cols": self.cols, "rows": self.rows,
                "rects": [[r.row, r.col, r.rowspan, r.colspan] for r in self.rects]}

    @classmethod
    def from_dict(cls, raw: dict, count: int) -> "Layout":
        try:
            layout = cls(int(raw["cols"]), int(raw["rows"]),
                         [Rect(*map(int, r)) for r in raw["rects"]])
            grids = {g for gs in PRESETS.values() for g in gs}
            if layout.count == count and (layout.cols, layout.rows) in grids and layout.is_valid():
                return layout
        except (KeyError, TypeError, ValueError):
            pass
        return cls.default(count)
