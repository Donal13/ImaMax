"""Wallpaper mosaic: a split-tree layout of image cells, templates, rendering.

A mosaic is a tree. Leaves are Cells (one image each, or empty); inner nodes
are Splits that share their rectangle between children side by side
(horizontal) or stacked (vertical), according to `ratios`. Putting an image
beside a cell is paste-to-side, generalised: the cell's row/column gains a
sibling, or the cell's slot is split in two.

Everything here works in output pixels and has no widget code, so the editor
and the final export draw exactly the same thing.
"""

import math
import random
from collections import namedtuple

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QTransform

PROXY_MAX = 1600    # longest side of the downscaled copy used while editing
MIN_SHARE = 0.04    # smallest share of a split a divider drag may leave a cell
SIDES = ("left", "right", "top", "bottom")

# A draggable gap between children `index` and `index + 1` of `split`.
# `start` / `total` / `gap` describe the split's main axis in output pixels.
Divider = namedtuple("Divider", "split index rect start total gap")


def _proxy(pixmap):
    if max(pixmap.width(), pixmap.height()) <= PROXY_MAX:
        return pixmap
    return pixmap.scaled(PROXY_MAX, PROXY_MAX, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


class Cell:
    """A leaf: one image (or none) and how it sits in its slot."""

    def __init__(self):
        self.pixmap = None        # full resolution
        self.proxy = None         # downscaled copy for on-screen editing
        self.name = ""
        self.fit = False          # False: fill the slot (crops); True: fit inside it
        self.zoom = 1.0           # on top of fill / fit
        self.center = (0.5, 0.5)  # image point (0–1) shown at the slot centre
        self.version = 0          # bumped on every change; keys the editor's cache

    def set_image(self, pixmap, name=""):
        self.pixmap = pixmap
        self.proxy = _proxy(pixmap) if pixmap is not None else None
        self.name = name
        self.fit = False
        self.reset_view()

    def clear(self):
        self.set_image(None)

    def reset_view(self):
        self.zoom = 1.0
        self.center = (0.5, 0.5)
        self.touch()

    def touch(self):
        self.version += 1

    def rotate(self, degrees=90):
        if self.pixmap is not None:
            self.set_image(self.pixmap.transformed(QTransform().rotate(degrees),
                                                   Qt.TransformationMode.SmoothTransformation),
                           self.name)

    def aspect(self):
        if self.pixmap is None or self.pixmap.height() == 0:
            return None
        return self.pixmap.width() / self.pixmap.height()

    def contents(self):
        return (self.pixmap, self.proxy, self.name, self.fit, self.zoom, self.center)

    def set_contents(self, contents):
        self.pixmap, self.proxy, self.name, self.fit, self.zoom, self.center = contents
        self.touch()

    def clone(self):
        c = Cell()
        c.set_contents(self.contents())
        return c


class Split:
    """An inner node: children side by side (horizontal) or stacked."""

    def __init__(self, horizontal, children, ratios=None):
        self.horizontal = horizontal
        self.children = list(children)
        self.ratios = list(ratios) if ratios else [1 / len(self.children)] * len(self.children)

    def clone(self):
        return Split(self.horizontal, [c.clone() for c in self.children], self.ratios)


def _leaves(node):
    if isinstance(node, Cell):
        return [node]
    return [leaf for child in node.children for leaf in _leaves(child)]


def _normalize(node):
    """Collapse one-child splits and merge nested same-direction splits."""
    if isinstance(node, Cell):
        return node
    kids, ratios = [], []
    for child, share in zip(node.children, node.ratios):
        child = _normalize(child)
        if isinstance(child, Split) and child.horizontal == node.horizontal:
            kids += child.children
            ratios += [share * r for r in child.ratios]
        else:
            kids.append(child)
            ratios.append(share)
    if len(kids) == 1:
        return kids[0]
    total = sum(ratios) or 1.0
    node.children, node.ratios = kids, [r / total for r in ratios]
    return node


def _natural_aspect(node):
    """Set split ratios so every cell matches its image's shape; return the
    width/height the subtree would then have (gaps ignored)."""
    if isinstance(node, Cell):
        return node.aspect() or 1.0
    aspects = [_natural_aspect(c) for c in node.children]
    if node.horizontal:  # equal heights: widths add up
        total = sum(aspects)
        node.ratios = [a / total for a in aspects]
        return total
    inverse = [1 / a for a in aspects]  # equal widths: heights add up
    total = sum(inverse)
    node.ratios = [i / total for i in inverse]
    return 1 / total


class Mosaic:
    """The whole design: canvas size, style, and the layout tree."""

    def __init__(self, width=1920, height=1080):
        self.width = width
        self.height = height
        self.root = Cell()
        self.spacing = 12
        self.margin = 0
        self.radius = 0
        self.background = QColor(20, 20, 20)
        # While True the layout re-flows itself (in reading order) as images
        # are added or the canvas changes shape; hand edits switch it off.
        self.auto = True

    # ── Queries ──────────────────────────────────────────────────────

    def leaves(self):
        return _leaves(self.root)

    def image_count(self):
        return sum(1 for c in self.leaves() if c.pixmap is not None)

    def is_empty(self):
        return self.image_count() == 0

    def find_parent(self, node):
        """(parent split, index) of `node`, or (None, -1) for the root."""
        stack = [self.root]
        while stack:
            current = stack.pop()
            if isinstance(current, Split):
                for i, child in enumerate(current.children):
                    if child is node:
                        return current, i
                    stack.append(child)
        return None, -1

    def layout(self):
        """([(cell, QRectF)], [Divider]) in output pixels."""
        cells, dividers = [], []
        m = self.margin
        self._layout(self.root, QRectF(m, m, max(1, self.width - 2 * m), max(1, self.height - 2 * m)),
                     cells, dividers)
        return cells, dividers

    def _layout(self, node, rect, cells, dividers):
        if isinstance(node, Cell):
            cells.append((node, rect))
            return
        n = len(node.children)
        gap = self.spacing
        start = rect.left() if node.horizontal else rect.top()
        length = rect.width() if node.horizontal else rect.height()
        total = max(0.0, length - gap * (n - 1))
        pos = start
        for i, (child, share) in enumerate(zip(node.children, node.ratios)):
            size = total * share
            if node.horizontal:
                child_rect = QRectF(pos, rect.top(), size, rect.height())
            else:
                child_rect = QRectF(rect.left(), pos, rect.width(), size)
            self._layout(child, child_rect, cells, dividers)
            pos += size
            if i < n - 1:
                strip = (QRectF(pos, rect.top(), gap, rect.height()) if node.horizontal
                         else QRectF(rect.left(), pos, rect.width(), gap))
                dividers.append(Divider(node, i, strip, start, total, gap))
                pos += gap

    def cell_rect(self, cell):
        for c, rect in self.layout()[0]:
            if c is cell:
                return rect
        return QRectF()

    # ── Structure edits ──────────────────────────────────────────────

    def _replace(self, old, new):
        parent, i = self.find_parent(old)
        if parent is None:
            self.root = new
        else:
            parent.children[i] = new

    def normalize(self):
        self.root = _normalize(self.root)

    def insert_beside(self, target, side, cell=None):
        """Put a new (or given) cell on `side` of `target`; returns it."""
        self.auto = False
        cell = cell or Cell()
        horizontal = side in ("left", "right")
        after = side in ("right", "bottom")
        parent, i = self.find_parent(target)
        if parent is not None and parent.horizontal == horizontal:
            half = parent.ratios[i] / 2
            parent.ratios[i] = half
            pos = i + 1 if after else i
            parent.children.insert(pos, cell)
            parent.ratios.insert(pos, half)
        else:
            self._replace(target, Split(horizontal, [target, cell] if after else [cell, target]))
        return cell

    def remove(self, cell):
        """Remove a cell, letting its neighbours take the space."""
        parent, i = self.find_parent(cell)
        if parent is None:
            cell.clear()  # the last cell can only be emptied
            return
        parent.children.pop(i)
        parent.ratios.pop(i)
        self.normalize()
        if self.auto:
            self.auto_arrange()

    def swap(self, a, b):
        ca, cb = a.contents(), b.contents()
        a.set_contents(cb)
        b.set_contents(ca)

    def move(self, src, target, zone):
        """Drop `src` onto `target`: 'center' swaps, a side docks it there.
        Returns the cell now holding src's image."""
        if src is target:
            return src
        if zone == "center":
            self.swap(src, target)
            return target
        contents = src.contents()
        self.remove(src)
        moved = self.insert_beside(target, zone)
        moved.set_contents(contents)
        self.normalize()
        return moved

    def split_largest(self):
        """Add an empty cell by halving the biggest cell along its long side."""
        cell, rect = max(self.layout()[0], key=lambda cr: cr[1].width() * cr[1].height())
        return self.insert_beside(cell, "right" if rect.width() >= rect.height() else "bottom")

    def add_image(self, pixmap, name=""):
        """Add an image: re-flow in auto mode, else fill an empty cell or make room."""
        if self.auto:
            cell = Cell()
            cell.set_image(pixmap, name)
            images = [c for c in self.leaves() if c.pixmap is not None] + [cell]
            self.root = images[0] if len(images) == 1 else Split(True, images)
            self.auto_arrange()
            return next(c for c in self.leaves() if c.pixmap is pixmap)
        target = next((c for c in self.leaves() if c.pixmap is None), None)
        if target is None:
            target = self.split_largest()
            self.auto = False
        target.set_image(pixmap, name)
        return target

    def apply_structure(self, template, keep_empty=True):
        """Rebuild with `template`'s layout, keeping the cells' contents in order."""
        self.auto = False
        contents = [c.contents() for c in self.leaves() if keep_empty or c.pixmap is not None]
        root = template.clone()
        for cell, content in zip(_leaves(root), contents):
            cell.set_contents(content)
        self.root = _normalize(root)

    def auto_arrange(self):
        """Lay the images out in rows or columns chosen to crop the least."""
        cells = [c for c in self.leaves() if c.pixmap is not None]
        if not cells:
            return
        m = self.margin
        aspect = max(1, self.width - 2 * m) / max(1, self.height - 2 * m)
        self.apply_structure(best_fit_structure([c.aspect() for c in cells], aspect), keep_empty=False)
        self.balance()
        self.auto = True

    def balance(self):
        """Resize cells to match their images' shapes as closely as the canvas allows."""
        _natural_aspect(self.root)
        for cell in self.leaves():
            cell.touch()

    def equalize(self):
        self.auto = False
        stack = [self.root]
        while stack:
            node = stack.pop()
            if isinstance(node, Split):
                node.ratios = [1 / len(node.children)] * len(node.children)
                stack.extend(node.children)

    def shuffle(self):
        leaves = self.leaves()
        contents = [c.contents() for c in leaves]
        if len(contents) < 2:
            return
        order = list(range(len(contents)))
        while order == sorted(order):
            random.shuffle(order)
        for cell, j in zip(leaves, order):
            cell.set_contents(contents[j])
        if self.auto:
            self.auto_arrange()  # new order, maybe a better-fitting layout

    def drag_divider(self, divider, pos):
        """Move a divider so its centre sits at `pos` along the split's axis."""
        self.auto = False
        split, i = divider.split, divider.index
        r = split.ratios
        left = divider.start + divider.total * sum(r[:i]) + i * divider.gap
        pair = r[i] + r[i + 1]
        share = (pos - divider.gap / 2 - left) / divider.total if divider.total > 0 else r[i]
        r[i] = min(max(share, MIN_SHARE), pair - MIN_SHARE)
        r[i + 1] = pair - r[i]
        for cell in _leaves(split):
            cell.touch()

    # ── Undo support ─────────────────────────────────────────────────

    def snapshot(self):
        return (self.root.clone(), self.width, self.height, self.spacing,
                self.margin, self.radius, QColor(self.background), self.auto)

    def restore(self, snap):
        root, self.width, self.height, self.spacing, self.margin, self.radius, bg, self.auto = snap
        self.root = root.clone()
        self.background = QColor(bg)

    def set_size(self, width, height):
        self.width, self.height = width, height
        if self.auto and self.image_count() > 1:
            self.auto_arrange()  # a different shape may suit another arrangement


# ── Templates ────────────────────────────────────────────────────────

def _strip(n, horizontal):
    return Cell() if n == 1 else Split(horizontal, [Cell() for _ in range(n)])


def _stack(nodes, horizontal):
    return nodes[0] if len(nodes) == 1 else Split(horizontal, nodes)


def _grid(n, aspect):
    cols = max(1, min(n, round(math.sqrt(n * aspect))))
    rows = math.ceil(n / cols)
    counts = [n // rows + (1 if i < n % rows else 0) for i in range(rows)]
    return _stack([_strip(c, True) for c in counts], False)


def _feature(n, side, aspect):
    """One large cell with the rest beside it."""
    horizontal = side in ("left", "right")
    rest_aspect = aspect * 0.4 if horizontal else aspect / 0.4
    rest = _grid(n - 1, rest_aspect) if n > 4 else _strip(n - 1, not horizontal)
    if side in ("left", "top"):
        return Split(horizontal, [Cell(), rest], [0.6, 0.4])
    return Split(horizontal, [rest, Cell()], [0.4, 0.6])


def _spiral(n, depth=0):
    if n == 1:
        return Cell()
    rest = _spiral(n - 1, depth + 1)
    kids = [Cell(), rest] if depth % 4 < 2 else [rest, Cell()]
    return Split(depth % 2 == 0, kids)


def _halves(n, horizontal):
    first = math.ceil(n / 2)
    return Split(horizontal, [_strip(first, not horizontal), _strip(n - first, not horizontal)])


def _signature(node):
    if isinstance(node, Cell):
        return "C"
    return ("H(" if node.horizontal else "V(") + ",".join(_signature(c) for c in node.children) + ")"


def templates(n, aspect):
    """[(name, tree)] of layouts with exactly `n` cells for a canvas of `aspect`."""
    if n <= 1:
        return [("Single", Cell())]
    options = [("Columns", _strip(n, True)), ("Rows", _strip(n, False)), ("Grid", _grid(n, aspect))]
    if n >= 3:
        for side in ("left", "top", "right", "bottom"):
            options.append((f"Large {side}", _feature(n, side, aspect)))
        options.append(("Spiral", _spiral(n)))
    if n >= 4:
        options += [("Two rows", _halves(n, False)), ("Two columns", _halves(n, True))]
    seen, unique = set(), []
    for name, tree in options:
        tree = _normalize(tree)
        sig = _signature(tree)
        if sig not in seen:
            seen.add(sig)
            unique.append((name, tree))
    return unique


def _partition(values, k):
    """Split `values` (in order) into k runs with sums as even as possible."""
    n = len(values)
    target = sum(values) / k
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    inf = float("inf")
    cost = [[inf] * (k + 1) for _ in range(n + 1)]
    cut = [[0] * (k + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(j, n + 1):
            for m in range(j - 1, i):
                c = cost[m][j - 1] + (prefix[i] - prefix[m] - target) ** 2
                if c < cost[i][j]:
                    cost[i][j], cut[i][j] = c, m
    groups, i = [], n
    for j in range(k, 0, -1):
        m = cut[i][j]
        groups.append(values[m:i])
        i = m
    return groups[::-1]


def best_fit_structure(aspects, canvas_aspect):
    """Rows (or columns) of cells whose natural shape best matches the canvas."""
    n = len(aspects)
    best = None
    for rows in (True, False):
        # Rows: images share a height, so a row's aspect is the sum of its images'.
        # Columns are the same problem with every aspect inverted.
        values = aspects if rows else [1 / a for a in aspects]
        target = canvas_aspect if rows else 1 / canvas_aspect
        for k in range(1, n + 1):
            runs = _partition(values, k)
            natural = 1 / sum(1 / sum(run) for run in runs)
            score = abs(math.log(natural / target))
            if best is None or score < best[0] - 1e-9:
                best = (score, rows, [len(run) for run in runs])
    _, rows, counts = best
    return _normalize(_stack([_strip(c, rows) for c in counts], not rows))


# ── Rendering ────────────────────────────────────────────────────────

def _clamp_axis(c, shown, slot):
    if shown <= slot:
        return 0.5
    half = slot / (2 * shown)
    return min(max(c, half), 1 - half)


def cell_placement(cell, rect):
    """Where the cell's image lands (QRectF) and its scale, for a slot `rect`."""
    iw, ih = cell.pixmap.width(), cell.pixmap.height()
    base = (min if cell.fit else max)(rect.width() / iw, rect.height() / ih)
    s = base * cell.zoom
    w, h = iw * s, ih * s
    cx = _clamp_axis(cell.center[0], w, rect.width())
    cy = _clamp_axis(cell.center[1], h, rect.height())
    return QRectF(rect.center().x() - cx * w, rect.center().y() - cy * h, w, h), s


def clamp_center(cell, rect):
    """The cell's centre after keeping the slot covered (for pan / zoom)."""
    placed, _ = cell_placement(cell, rect)
    return (_clamp_axis(cell.center[0], placed.width(), rect.width()),
            _clamp_axis(cell.center[1], placed.height(), rect.height()))


def cell_piece(cell, rect, source, source_scale=1.0):
    """The visible part of the cell's image, resampled for `rect`.

    source: the pixmap to sample (full image or its proxy); source_scale is
    its size relative to the full image. Returns (piece, pos, visible) —
    `piece` should be drawn with its top-left at `pos`, clipped to `visible` —
    or None if nothing shows.
    """
    placed, s = cell_placement(cell, rect)
    visible = placed.intersected(rect)
    if visible.width() < 0.5 or visible.height() < 0.5:
        return None
    k = s / source_scale  # output pixels per source pixel
    x0 = max(0, math.floor((visible.left() - placed.left()) / k))
    y0 = max(0, math.floor((visible.top() - placed.top()) / k))
    x1 = min(source.width(), math.ceil((visible.right() - placed.left()) / k))
    y1 = min(source.height(), math.ceil((visible.bottom() - placed.top()) / k))
    if x1 <= x0 or y1 <= y0:
        return None
    piece = source.copy(QRect(x0, y0, x1 - x0, y1 - y0)).scaled(
        max(1, round((x1 - x0) * k)), max(1, round((y1 - y0) * k)),
        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return piece, QPointF(placed.left() + x0 * k, placed.top() + y0 * k), visible


def draw_piece(painter, piece, pos, visible, radius, texture_scale=1.0):
    """Fill `visible` with `piece` (anti-aliased, optionally rounded)."""
    brush = QBrush(piece)
    brush.setTransform(QTransform().translate(pos.x(), pos.y()).scale(texture_scale, texture_scale))
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    r = min(radius, visible.width() / 2, visible.height() / 2)
    if r > 0:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawRoundedRect(visible, r, r)
    else:
        painter.drawRect(visible)
    painter.restore()


def render(mosaic):
    """The finished wallpaper as a QImage at full resolution."""
    img = QImage(mosaic.width, mosaic.height, QImage.Format.Format_RGB32)
    img.fill(mosaic.background)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for cell, rect in mosaic.layout()[0]:
        if cell.pixmap is not None:
            piece = cell_piece(cell, rect, cell.pixmap)
            if piece:
                draw_piece(painter, *piece, mosaic.radius)
    painter.end()
    return img
