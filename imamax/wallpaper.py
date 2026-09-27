"""Wallpaper Maker: design multi-image mosaic wallpapers.

The editor shows the whole canvas scaled to fit. Click a cell to select it;
drag to reposition its image and use the wheel to zoom it; drag the gaps
between cells to resize them; Ctrl+drag an image onto another cell to swap
the two, or onto a cell's edge to dock it on that side. Ctrl+Shift+arrow
pastes the clipboard beside the selected cell — paste-to-side, repeatable.
"""

import json
import os
import shutil
import subprocess
import sys
import time

from PyQt6.QtCore import QDateTime, QPointF, QRectF, QSize, QStandardPaths, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QCursor, QGuiApplication, QIcon, QImage, QKeySequence,
    QPainter, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QColorDialog, QComboBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from .icons import icon, swatch
from .mosaic import (
    Cell, Mosaic, Split, cell_piece, cell_placement, clamp_center, draw_piece,
    render, templates,
)
from .utils import SUPPORTED_FORMATS, clipboard_pixmap, load_image, screen_pixel_size

ACCENT = QColor(74, 158, 255)
VIEW_BG = QColor(30, 30, 30)
EDGE_ZONE = 0.25       # fraction of a cell near each edge that docks instead of swapping
ZOOM_STEP = 1.15
MAX_CELL_ZOOM = 8.0
UNDO_LIMIT = 50
SOFT_SCALE = 1.25      # warn when an image is enlarged more than this

COMMON_SIZES = [
    ("Full HD", 1920, 1080), ("WUXGA", 1920, 1200), ("QHD", 2560, 1440),
    ("WQXGA", 2560, 1600), ("4K UHD", 3840, 2160), ("5K", 5120, 2880),
    ("Ultrawide", 2560, 1080), ("Ultrawide QHD", 3440, 1440),
    ("Super ultrawide", 5120, 1440), ("Phone", 1080, 2400),
]
BACKGROUNDS = [("Black", "#000000"), ("Charcoal", "#1c1c1c"), ("Grey", "#7f7f7f"), ("White", "#ffffff")]
SIDE_NAMES = {"left": "Left", "right": "Right", "top": "Above", "bottom": "Below"}
SIDE_KEYS = {"left": "Left", "right": "Right", "top": "Up", "bottom": "Down"}
ARROW_SIDES = {Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
               Qt.Key.Key_Up: "top", Qt.Key.Key_Down: "bottom"}
ZONE_LABELS = {"center": "Swap", "left": "Dock left", "right": "Dock right",
               "top": "Dock above", "bottom": "Dock below"}
HINT = "Drag to reposition · wheel to zoom · Ctrl+drag to swap or dock · drag the gaps to resize"
IMAGE_FILTER = "Images (" + " ".join("*" + ext for ext in SUPPORTED_FORMATS) + ");;All Files (*)"


class MosaicView(QWidget):
    """Interactive preview of the mosaic, scaled to fit."""

    about_to_change = pyqtSignal(object)          # undo key (None: always a new step)
    changed = pyqtSignal()                        # the design was edited
    selection_changed = pyqtSignal()
    command = pyqtSignal(str, object)             # requests handled by the window
    sources_dropped = pyqtSignal(list, object, str)  # paths/pixmaps, target cell, zone

    MARGIN = 28

    def __init__(self, mosaic, parent=None):
        super().__init__(parent)
        self.mosaic = mosaic
        self.selected = None
        self._hover = None    # ("cell", cell) or ("divider", split, index)
        self._drag = None
        self._drop = None     # (cell or None, zone) while dragging something over the view
        self._ghost = None    # cursor position while Ctrl+dragging a cell
        self._cache = {}
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(420, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ── Geometry ─────────────────────────────────────────────────────

    def _scale(self):
        m = self.mosaic
        s = max(0.01, min((self.width() - 2 * self.MARGIN) / m.width,
                          (self.height() - 2 * self.MARGIN) / m.height))
        return s, QPointF((self.width() - m.width * s) / 2, (self.height() - m.height * s) / 2)

    def to_view(self, r):
        s, o = self._scale()
        return QRectF(o.x() + r.x() * s, o.y() + r.y() * s, r.width() * s, r.height() * s)

    def to_output(self, p):
        s, o = self._scale()
        return QPointF((p.x() - o.x()) / s, (p.y() - o.y()) / s)

    def canvas_rect(self):
        return self.to_view(QRectF(0, 0, self.mosaic.width, self.mosaic.height))

    def _divider_hit_rect(self, divider):
        r = self.to_view(divider.rect)
        if divider.split.horizontal:
            grow = max(0.0, (8 - r.width()) / 2)
            return r.adjusted(-grow, 0, grow, 0)
        grow = max(0.0, (8 - r.height()) / 2)
        return r.adjusted(0, -grow, 0, grow)

    def _hit(self, pos):
        cells, dividers = self.mosaic.layout()
        for d in dividers:
            if self._divider_hit_rect(d).contains(pos):
                return ("divider", d)
        for cell, rect in cells:
            if self.to_view(rect).contains(pos):
                return ("cell", cell, rect)
        return None

    def _find_divider(self, split, index):
        return next((d for d in self.mosaic.layout()[1]
                     if d.split is split and d.index == index), None)

    def _drop_target(self, pos, exclude=None):
        """(cell, zone) under `pos`: zone is 'center' or the side it's near."""
        hit = self._hit(pos)
        if hit is None or hit[0] != "cell" or hit[1] is exclude:
            return None
        vr = self.to_view(hit[2])
        fx = (pos.x() - vr.left()) / max(1.0, vr.width())
        fy = (pos.y() - vr.top()) / max(1.0, vr.height())
        dist = {"left": fx, "right": 1 - fx, "top": fy, "bottom": 1 - fy}
        side = min(dist, key=dist.get)
        return hit[1], (side if dist[side] < EDGE_ZONE else "center")

    # ── Selection and edits ──────────────────────────────────────────

    def select(self, cell):
        if cell is not self.selected:
            self.selected = cell
            self.update()
            self.selection_changed.emit()

    def select_neighbor(self, side):
        cells = self.mosaic.layout()[0]
        if not cells:
            return
        if self.selected is None:
            self.select(cells[0][0])
            return
        here = self.mosaic.cell_rect(self.selected).center()
        best = None
        for cell, rect in cells:
            if cell is self.selected:
                continue
            dx, dy = rect.center().x() - here.x(), rect.center().y() - here.y()
            ahead = {"left": -dx, "right": dx, "top": -dy, "bottom": dy}[side]
            if ahead <= 1:
                continue
            score = ahead + 2 * (abs(dy) if side in ("left", "right") else abs(dx))
            if best is None or score < best[0]:
                best = (score, cell)
        if best:
            self.select(best[1])

    def zoom_cell(self, cell, factor, anchor=None):
        """Zoom a cell's image, keeping the output point `anchor` fixed."""
        rect = self.mosaic.cell_rect(cell)
        placed, _ = cell_placement(cell, rect)
        anchor = anchor or rect.center()
        u = (anchor.x() - placed.left()) / placed.width()
        v = (anchor.y() - placed.top()) / placed.height()
        cell.zoom = min(max(cell.zoom * factor, 1.0), MAX_CELL_ZOOM)
        zoomed, _ = cell_placement(cell, rect)
        cell.center = ((rect.center().x() - anchor.x()) / zoomed.width() + u,
                       (rect.center().y() - anchor.y()) / zoomed.height() + v)
        cell.center = clamp_center(cell, rect)
        cell.touch()
        self.update()
        self.changed.emit()

    # ── Mouse ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hit = self._hit(pos)
        if hit and hit[0] == "divider":
            d = hit[1]
            self._drag = {"kind": "divider", "split": d.split, "index": d.index,
                          "press": pos, "active": False}
        elif hit:
            cell = hit[1]
            self.select(cell)
            if cell.pixmap is not None:
                ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
                self._drag = {"kind": "move" if ctrl else "pan", "cell": cell,
                              "press": pos, "last": pos, "active": False}
        self._update_cursor(pos, event.modifiers())

    def mouseMoveEvent(self, event):
        pos = event.position()
        d = self._drag
        if d is None:
            hit = self._hit(pos)
            hover = None
            if hit and hit[0] == "divider":
                hover = ("divider", hit[1].split, hit[1].index)
            elif hit:
                hover = ("cell", hit[1])
            if hover != self._hover:
                self._hover = hover
                self.update()
            self._update_cursor(pos, event.modifiers())
            return
        if not d["active"]:
            delta = pos - d["press"]
            if abs(delta.x()) + abs(delta.y()) < 3:
                return
            d["active"] = True
            if d["kind"] != "move":
                self.about_to_change.emit(None)
        if d["kind"] == "divider":
            div = self._find_divider(d["split"], d["index"])
            if div is not None:
                out = self.to_output(pos)
                self.mosaic.drag_divider(div, out.x() if div.split.horizontal else out.y())
                self.update()
                self.changed.emit()
        elif d["kind"] == "pan":
            cell = d["cell"]
            rect = self.mosaic.cell_rect(cell)
            placed, _ = cell_placement(cell, rect)
            s, _o = self._scale()
            step = pos - d["last"]
            d["last"] = pos
            cell.center = (cell.center[0] - step.x() / (placed.width() * s),
                           cell.center[1] - step.y() / (placed.height() * s))
            cell.center = clamp_center(cell, rect)
            cell.touch()
            self.update()
            self.changed.emit()
        else:  # move
            self._drop = self._drop_target(pos, exclude=d["cell"])
            self._ghost = pos
            self.update()

    def mouseReleaseEvent(self, event):
        d, self._drag = self._drag, None
        if d and d["kind"] == "move" and d["active"] and self._drop:
            target, zone = self._drop
            self.about_to_change.emit(None)
            self.select(self.mosaic.move(d["cell"], target, zone))
            self.changed.emit()
        self._drop = self._ghost = None
        self.update()
        self._update_cursor(event.position(), event.modifiers())

    def mouseDoubleClickEvent(self, event):
        hit = self._hit(event.position())
        if event.button() != Qt.MouseButton.LeftButton or hit is None:
            return
        if hit[0] == "divider":  # even out the two neighbours
            d = hit[1]
            self.about_to_change.emit(None)
            r = d.split.ratios
            r[d.index] = r[d.index + 1] = (r[d.index] + r[d.index + 1]) / 2
            self.mosaic.auto = False
            self.update()
            self.changed.emit()
        elif hit[1].pixmap is None:
            self.command.emit("browse", hit[1])
        else:
            self.about_to_change.emit(None)
            hit[1].reset_view()
            self.update()
            self.changed.emit()

    def wheelEvent(self, event):
        hit = self._hit(event.position())
        steps = event.angleDelta().y() / 120
        if hit is None or hit[0] != "cell" or hit[1].pixmap is None or not steps:
            super().wheelEvent(event)
            return
        self.about_to_change.emit(("zoom", id(hit[1])))
        self.select(hit[1])
        self.zoom_cell(hit[1], ZOOM_STEP ** steps, self.to_output(event.position()))

    def contextMenuEvent(self, event):
        hit = self._hit(QPointF(event.pos()))
        cell = hit[1] if hit and hit[0] == "cell" else None
        if cell is not None:
            self.select(cell)
        self.command.emit("context", (cell, event.globalPos()))

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def _update_cursor(self, pos, modifiers):
        d = self._drag
        shape = Qt.CursorShape.ArrowCursor
        if d is not None:
            if d["kind"] == "divider":
                shape = (Qt.CursorShape.SplitHCursor if d["split"].horizontal
                         else Qt.CursorShape.SplitVCursor)
            else:
                shape = (Qt.CursorShape.ClosedHandCursor if d["kind"] == "pan"
                         else Qt.CursorShape.DragMoveCursor)
        else:
            hit = self._hit(pos)
            if hit and hit[0] == "divider":
                shape = (Qt.CursorShape.SplitHCursor if hit[1].split.horizontal
                         else Qt.CursorShape.SplitVCursor)
            elif hit and hit[1].pixmap is not None:
                shape = (Qt.CursorShape.SizeAllCursor if modifiers & Qt.KeyboardModifier.ControlModifier
                         else Qt.CursorShape.OpenHandCursor)
        self.setCursor(shape)

    # ── Keyboard ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        plain = mods == Qt.KeyboardModifier.NoModifier
        if key in ARROW_SIDES and mods == ctrl_shift:
            self.command.emit("paste_beside", ARROW_SIDES[key])
        elif key in ARROW_SIDES and plain:
            self.select_neighbor(ARROW_SIDES[key])
        elif key == Qt.Key.Key_Delete and plain:
            self.command.emit("remove", None)
        elif key == Qt.Key.Key_F and plain:
            self.command.emit("toggle_fit", None)
        elif key == Qt.Key.Key_R and plain:
            self.command.emit("rotate", None)
        elif key in (Qt.Key.Key_0, Qt.Key.Key_Home) and plain:
            self.command.emit("reset", None)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.command.emit("zoom", ZOOM_STEP)
        elif key == Qt.Key.Key_Minus:
            self.command.emit("zoom", 1 / ZOOM_STEP)
        else:
            if key == Qt.Key.Key_Control:
                self._update_cursor(QPointF(self.mapFromGlobal(QCursor.pos())), event.modifiers())
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Control:
            self._update_cursor(QPointF(self.mapFromGlobal(QCursor.pos())), event.modifiers())
        super().keyReleaseEvent(event)

    # ── Drops from outside (files, images) ───────────────────────────

    @staticmethod
    def _sources(mime):
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.splitext(p)[1].lower() in SUPPORTED_FORMATS]
        if paths:
            return paths
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                return [QPixmap.fromImage(image)]
        return []

    def dragEnterEvent(self, event):
        if self._sources(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self._drop = self._drop_target(event.position()) or (None, "add")
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop = None
        self.update()

    def dropEvent(self, event):
        sources = self._sources(event.mimeData())
        target = self._drop_target(event.position()) or (None, "add")
        self._drop = None
        self.update()
        if sources:
            event.acceptProposedAction()
            self.sources_dropped.emit(sources, target[0], target[1])

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), VIEW_BG)
        m = self.mosaic
        s, _o = self._scale()
        canvas = self.canvas_rect()
        for i, alpha in enumerate((60, 35, 18)):  # soft shadow
            p.fillRect(canvas.adjusted(-i, -i + 2, i + 1, i + 3), QColor(0, 0, 0, alpha))
        p.fillRect(canvas, m.background)

        dpr = self.devicePixelRatioF()
        radius = m.radius * s
        cells, dividers = m.layout()
        for cell, rect in cells:
            vr = self.to_view(rect)
            if cell.pixmap is None:
                self._paint_empty(p, vr, radius)
            else:
                self._paint_image(p, cell, vr, radius, dpr)

        # Divider under the mouse / being dragged
        active = None
        if self._drag and self._drag["kind"] == "divider":
            active = (self._drag["split"], self._drag["index"])
        elif self._hover and self._hover[0] == "divider":
            active = self._hover[1:]
        if active:
            d = self._find_divider(*active)
            if d is not None:
                r = self.to_view(d.rect)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(QPen(ACCENT, 3, cap=Qt.PenCapStyle.RoundCap))
                c = r.center()
                if d.split.horizontal:
                    p.drawLine(QPointF(c.x(), r.top() + 2), QPointF(c.x(), r.bottom() - 2))
                else:
                    p.drawLine(QPointF(r.left() + 2, c.y()), QPointF(r.right() - 2, c.y()))

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._hover and self._hover[0] == "cell" and self._hover[1] is not self.selected \
                and self._drag is None:
            p.setPen(QPen(QColor(255, 255, 255, 110), 1))
            p.drawRoundedRect(self._cell_view_rect(self._hover[1]).adjusted(0.5, 0.5, -0.5, -0.5),
                              radius, radius)
        if self.selected is not None:
            vr = self._cell_view_rect(self.selected)
            if not vr.isNull():
                p.setPen(QPen(ACCENT, 2.5))
                p.drawRoundedRect(vr.adjusted(1.25, 1.25, -1.25, -1.25), radius, radius)

        self._paint_drop(p)
        if self._ghost is not None and self._drag:
            cell = self._drag["cell"]
            thumb = cell.proxy.scaled(110, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            p.setOpacity(0.75)
            p.drawPixmap(self._ghost + QPointF(14, 14), thumb)
            p.setOpacity(1.0)
        p.end()

    def _cell_view_rect(self, cell):
        rect = self.mosaic.cell_rect(cell)
        return self.to_view(rect) if not rect.isNull() else QRectF()

    def _paint_image(self, p, cell, vr, radius, dpr):
        key = (cell.proxy.cacheKey(), cell.fit, cell.zoom, cell.center,
               round(vr.x() * dpr), round(vr.y() * dpr), round(vr.width() * dpr), round(vr.height() * dpr))
        if key not in self._cache:
            if len(self._cache) > 300:
                self._cache.clear()
            device_rect = QRectF(vr.x() * dpr, vr.y() * dpr, vr.width() * dpr, vr.height() * dpr)
            self._cache[key] = cell_piece(cell, device_rect, cell.proxy,
                                          cell.proxy.width() / cell.pixmap.width())
        entry = self._cache[key]
        if entry:
            piece, pos, visible = entry
            draw_piece(p, piece, QPointF(pos.x() / dpr, pos.y() / dpr),
                       QRectF(visible.x() / dpr, visible.y() / dpr,
                              visible.width() / dpr, visible.height() / dpr),
                       radius, 1 / dpr)

    def _paint_empty(self, p, vr, radius):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 80), 1, Qt.PenStyle.DashLine))
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(vr.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if vr.width() > 50 and vr.height() > 40:
            c = vr.center()
            p.setPen(QPen(QColor(255, 255, 255, 150), 2, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() - 9, c.y()), QPointF(c.x() + 9, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - 9), QPointF(c.x(), c.y() + 9))
            if vr.width() > 170 and vr.height() > 90:
                p.setPen(QColor(255, 255, 255, 140))
                p.drawText(QRectF(vr.left() + 6, c.y() + 18, vr.width() - 12, vr.bottom() - c.y() - 18),
                           Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                           "Drop, paste or double-click\nto add an image")
        p.restore()

    def _paint_drop(self, p):
        if not self._drop:
            return
        cell, zone = self._drop
        if cell is None:
            zr, label = self.canvas_rect(), "Add"
        else:
            vr = self._cell_view_rect(cell)
            w, h = vr.width() / 2, vr.height() / 2
            zr = {"center": vr, "left": vr.adjusted(0, 0, -w, 0), "right": vr.adjusted(w, 0, 0, 0),
                  "top": vr.adjusted(0, 0, 0, -h), "bottom": vr.adjusted(0, h, 0, 0)}[zone]
            label = ZONE_LABELS[zone] if self._drag else ("Replace" if zone == "center" else
                                                           ZONE_LABELS[zone].replace("Dock", "Add"))
        p.save()
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(QColor(74, 158, 255, 70))
        p.drawRect(zr.adjusted(1, 1, -1, -1))
        fm = p.fontMetrics()
        tw, th = fm.horizontalAdvance(label) + 18, fm.height() + 8
        badge = QRectF(zr.center().x() - tw / 2, zr.center().y() - th / 2, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 210))
        p.drawRoundedRect(badge, th / 2, th / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        p.restore()


class _SliderSpin(QWidget):
    """Slider and spin box kept in step."""

    valueChanged = pyqtSignal(int)

    def __init__(self, lo, hi, suffix=" px", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(lo, hi)
        self._spin = QSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setSuffix(suffix)
        self._spin.setFixedWidth(76)
        row.addWidget(self._slider)
        row.addWidget(self._spin)
        self._slider.valueChanged.connect(self._on_change)
        self._spin.valueChanged.connect(self._on_change)

    def _on_change(self, value):
        self.setValue(value)
        self.valueChanged.emit(value)

    def setValue(self, value):
        for w in (self._slider, self._spin):
            if w.value() != value:
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)

    def value(self):
        return self._spin.value()


class WallpaperMaker(QMainWindow):
    """Separate window for designing multi-image mosaic wallpapers."""

    def __init__(self, viewer, settings):
        super().__init__(viewer, Qt.WindowType.Window)
        self._viewer = viewer
        self._settings = settings
        self.setWindowTitle("Wallpaper Maker — ImaMax")
        self.setMinimumSize(980, 620)

        s = settings
        screen = screen_pixel_size(viewer.screen())
        self.mosaic = Mosaic(int(s.value("wallpaper/width", screen.width())),
                             int(s.value("wallpaper/height", screen.height())))
        self.mosaic.spacing = int(s.value("wallpaper/spacing", 12))
        self.mosaic.margin = int(s.value("wallpaper/margin", 0))
        self.mosaic.radius = int(s.value("wallpaper/radius", 0))
        bg = QColor(s.value("wallpaper/background", "#141414"))
        self.mosaic.background = bg if bg.isValid() else QColor(20, 20, 20)

        self._undo, self._redo = [], []
        self._undo_key, self._undo_time = None, 0.0
        self._dirty = False
        self._template_sig = None
        self._geometry_restored = False

        self._create_actions()
        self._create_toolbar()

        self.view = MosaicView(self.mosaic)
        self.view.about_to_change.connect(self._push_undo)
        self.view.changed.connect(self._changed)
        self.view.selection_changed.connect(self._on_selection_changed)
        self.view.command.connect(self._on_command)
        self.view.sources_dropped.connect(self._on_sources_dropped)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.view, 1)
        row.addWidget(self._create_panel())
        self.setCentralWidget(central)

        self._hint = QLabel(HINT)
        self._hint.setStyleSheet("color: #999;")
        self.statusBar().addWidget(self._hint, 1)
        self._info = QLabel()
        self.statusBar().addPermanentWidget(self._info)

        self.view.select(self.mosaic.root)
        self._changed(dirty=False)

    # ── Public API (used by the viewer) ──────────────────────────────

    def show_and_raise(self):
        if not self._geometry_restored:
            self._geometry_restored = True
            geometry = self._settings.value("wallpaper/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)
            else:
                self.resize(1280, 820)
        self.show()
        self.raise_()
        self.activateWindow()
        self.view.setFocus()

    def add_images(self, items):
        """Add [(pixmap, name)]; returns how many were added."""
        items = [(pm, name) for pm, name in items if pm is not None and not pm.isNull()]
        if not items:
            return 0
        self._push_undo()
        last = None
        for pixmap, name in items:
            last = self.mosaic.add_image(pixmap, name)
        self.view.select(last)
        self._changed()
        noun = "image" if len(items) == 1 else "images"
        self.statusBar().showMessage(f"Added {len(items)} {noun}.", 3000)
        return len(items)

    def load_pair(self, first, first_name, second, second_name, side):
        """Start from a paste-to-side pair: `second` goes on `side` of `first`."""
        had_work = not self.mosaic.is_empty()
        self._push_undo()
        a, b = Cell(), Cell()
        a.set_image(first, first_name)
        b.set_image(second, second_name)
        after = side in ("right", "bottom")
        self.mosaic.root = Split(side in ("left", "right"), [a, b] if after else [b, a])
        self.mosaic.auto = False
        self.mosaic.balance()
        self.view.select(b)
        self._changed()
        if had_work:
            self.statusBar().showMessage("Loaded the pasted pair — Undo brings back your previous design.", 6000)

    def has_unsaved_work(self):
        return self._dirty and not self.mosaic.is_empty()

    # ── Actions and toolbar ──────────────────────────────────────────

    def _create_actions(self):
        self._actions = {}

        def act(name, text, shortcut, slot, tip=None, icon_name=None):
            a = QAction(text, self)
            if icon_name:
                a.setIcon(icon(icon_name))
            if shortcut:
                keys = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
                a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(slot)
            if tip:
                a.setStatusTip(tip)
            label = text.replace("&", "").rstrip("…")
            keys_text = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            a.setToolTip(f"{label} ({keys_text})" if keys_text else label)
            self.addAction(a)
            self._actions[name] = a

        act("new", "&New", "Ctrl+N", self._on_new, "Start over with an empty canvas", "new")
        act("add", "&Add Images…", "Ctrl+O", self._on_add_files, "Add image files", "open")
        act("add_viewer", "Add &Viewer Image", "Ctrl+I", self._on_add_from_viewer,
            "Add the image (or crop selection) open in the main window", "add_to_wallpaper")
        act("paste", "&Paste", "Ctrl+V", self._on_paste,
            "Paste the clipboard image (Ctrl+Shift+arrow pastes beside the selected cell)", "paste")
        act("undo", "&Undo", "Ctrl+Z", self._on_undo, icon_name="undo")
        act("redo", "&Redo", ["Ctrl+Shift+Z", "Ctrl+Y"], self._on_redo, icon_name="redo")
        act("to_viewer", "Open in &Viewer", None, self._on_to_viewer,
            "Send the finished wallpaper to the main window", "image")
        act("save", "&Save As…", ["Ctrl+S", "Ctrl+Shift+S"], self._on_save_as,
            "Save the wallpaper as an image file", "save")
        act("set_wallpaper", "Set as &Wallpaper", None, self._on_set_wallpaper,
            "Save the wallpaper and make it your desktop background", "wallpaper")
        act("close", "&Close", "Ctrl+W", self.close)

    def _create_toolbar(self):
        tb = QToolBar("Wallpaper Toolbar")
        tb.setObjectName("WallpaperToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        for k in ("new", "add", "add_viewer", "paste"):
            tb.addAction(self._actions[k])
        tb.addSeparator()
        for k in ("undo", "redo"):
            tb.addAction(self._actions[k])
            tb.widgetForAction(self._actions[k]).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        for k in ("to_viewer", "save", "set_wallpaper"):
            tb.addAction(self._actions[k])
        tb.widgetForAction(self._actions["set_wallpaper"]).setStyleSheet(
            "QToolButton { background: #3d6fb8; color: white; border: 1px solid #4b82d0;"
            " border-radius: 3px; padding: 3px 8px; font-weight: bold; }"
            "QToolButton:hover { background: #4a80cc; }")

    # ── Side panel ───────────────────────────────────────────────────

    def _create_panel(self):
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(6)

        # Canvas
        box = QGroupBox("Canvas")
        v = QVBoxLayout(box)
        self._size_combo = QComboBox()
        self._size_combo.setToolTip("Wallpaper size in pixels")
        self._size_combo.activated.connect(self._on_size_preset)
        v.addWidget(self._size_combo)
        row = QHBoxLayout()
        self._w_spin, self._h_spin = QSpinBox(), QSpinBox()
        for spin, tip in ((self._w_spin, "Width"), (self._h_spin, "Height")):
            spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            spin.setRange(64, 16384)
            spin.setSuffix(" px")
            spin.setKeyboardTracking(False)
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._on_size_edited)
        swap = QToolButton()
        swap.setIcon(icon("swap"))
        swap.setAutoRaise(True)
        swap.setToolTip("Swap portrait / landscape")
        swap.clicked.connect(lambda: self._set_canvas_size(self.mosaic.height, self.mosaic.width))
        row.addWidget(self._w_spin)
        row.addWidget(QLabel("×"))
        row.addWidget(self._h_spin)
        row.addWidget(swap)
        v.addLayout(row)
        col.addWidget(box)

        # Layout
        box = QGroupBox("Layout")
        v = QVBoxLayout(box)
        self._layout_mode = QLabel()
        self._layout_mode.setStyleSheet("color: #aaa;")
        self._layout_mode.setToolTip("In auto layout the mosaic re-arranges itself (in order) whenever "
                                     "images are added or the canvas changes shape. Dragging a gap, "
                                     "docking an image or picking a template switches to a custom layout.")
        v.addWidget(self._layout_mode)
        tiles = QWidget()
        self._tiles = QGridLayout(tiles)
        self._tiles.setContentsMargins(0, 0, 0, 0)
        self._tiles.setSpacing(4)
        v.addWidget(tiles)
        grid = QGridLayout()
        grid.setSpacing(4)
        for i, (text, tip, slot) in enumerate((
                ("Auto arrange", "Rows or columns that crop the least; keeps re-flowing as you add images",
                 self._on_auto_arrange),
                ("Fit to images", "Keep this layout but resize cells to match their images' shapes",
                 self._on_balance),
                ("Even sizes", "Make every cell in each row / column the same size", self._on_equalize),
                ("Shuffle", "Mix the images up", self._on_shuffle))):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setAutoDefault(False)
            if text == "Shuffle":
                btn.setIcon(icon("shuffle"))
            btn.clicked.connect(slot)
            grid.addWidget(btn, i // 2, i % 2)
        v.addLayout(grid)
        col.addWidget(box)

        # Style
        box = QGroupBox("Spacing and style")
        form = QGridLayout(box)
        self._style = {}
        for r, (attr, label, hi, tip) in enumerate((
                ("spacing", "Gap", 200, "Space between images"),
                ("margin", "Margin", 400, "Space around the edge of the wallpaper"),
                ("radius", "Corners", 200, "Rounded image corners"))):
            form.addWidget(QLabel(label), r, 0)
            ctrl = _SliderSpin(0, hi)
            ctrl.setToolTip(tip)
            ctrl.valueChanged.connect(lambda value, a=attr: self._on_style(a, value))
            form.addWidget(ctrl, r, 1)
            self._style[attr] = ctrl
        form.addWidget(QLabel("Background"), 3, 0)
        swatches = QHBoxLayout()
        swatches.setSpacing(3)
        for name, hex_color in BACKGROUNDS:
            b = QToolButton()
            b.setIcon(swatch(QColor(hex_color), QSize(18, 18)))
            b.setIconSize(QSize(18, 18))
            b.setAutoRaise(True)
            b.setToolTip(name)
            b.clicked.connect(lambda _c=False, h=hex_color: self._on_background(QColor(h)))
            swatches.addWidget(b)
        self._bg_btn = QToolButton()
        self._bg_btn.setIconSize(QSize(18, 18))
        self._bg_btn.setText("Custom…")
        self._bg_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._bg_btn.setToolTip("Choose any background colour")
        self._bg_btn.clicked.connect(self._pick_background)
        swatches.addWidget(self._bg_btn)
        swatches.addStretch()
        form.addLayout(swatches, 3, 1)
        col.addWidget(box)

        # Selected cell
        self._cell_box = QGroupBox("Selected image")
        v = QVBoxLayout(self._cell_box)
        self._cell_name = QLabel()
        self._cell_name.setStyleSheet("font-weight: bold;")
        self._cell_info = QLabel()
        self._cell_info.setWordWrap(True)
        v.addWidget(self._cell_name)
        v.addWidget(self._cell_info)

        row = QHBoxLayout()
        self._fill_group = QButtonGroup(self)
        for i, (text, tip) in enumerate((("Fill", "Cover the whole cell (crops the image)"),
                                         ("Fit", "Show the whole image (F toggles)"))):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setStyleSheet(
                "QToolButton { background: #3a3a3a; border: 1px solid #555; border-radius: 3px;"
                " padding: 3px 10px; }"
                "QToolButton:hover { background: #454545; }"
                "QToolButton:checked { background: #3d6fb8; border-color: #5a8fd8; color: white; }"
                "QToolButton:disabled { background: #333; border-color: #444; color: #777; }")
            b.setToolTip(tip)
            b.setMinimumWidth(52)
            self._fill_group.addButton(b, i)
            row.addWidget(b)
        self._fill_group.idClicked.connect(lambda i: self._set_fit(i == 1))
        row.addSpacing(8)
        for icon_name, tip, slot in (("rotate_right", "Rotate 90° (R)", self._on_rotate),
                                     ("reset", "Reset zoom and position (0)", self._on_reset_cell)):
            b = QToolButton()
            b.setIcon(icon(icon_name))
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Zoom"))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(100, int(MAX_CELL_ZOOM * 100))
        self._zoom_slider.setToolTip("Zoom the image inside its cell (mouse wheel)")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)
        row.addWidget(self._zoom_slider)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Add cell"))
        for side in ("left", "top", "bottom", "right"):
            b = QToolButton()
            b.setIcon(icon({"left": "arrow_left", "right": "arrow_right",
                            "top": "arrow_up", "bottom": "arrow_down"}[side]))
            b.setAutoRaise(True)
            b.setToolTip(f"Add an empty cell {SIDE_NAMES[side].lower()} "
                         f"(Ctrl+Shift+{SIDE_KEYS[side]} pastes the clipboard there)")
            b.clicked.connect(lambda _c=False, sd=side: self._add_empty(sd))
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)

        row = QHBoxLayout()
        for text, tip, slot in (("Replace…", "Choose a different image for this cell", self._on_replace),
                                ("Clear", "Empty this cell", self._on_clear),
                                ("Remove", "Remove this cell (Delete)", self._on_remove)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        self._image_controls = [self._zoom_slider, *self._fill_group.buttons()]
        col.addWidget(self._cell_box)
        col.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(330)
        self._populate_sizes()
        return scroll

    def _populate_sizes(self):
        combo = self._size_combo
        combo.blockSignals(True)
        combo.clear()
        here = self._viewer.screen()
        for i, screen in enumerate(QGuiApplication.screens()):
            size = screen_pixel_size(screen)
            label = "This screen" if screen is here else f"Screen {i + 1} ({screen.name()})"
            combo.addItem(icon("wallpaper"), f"{label} — {size.width()} × {size.height()}",
                          (size.width(), size.height()))
        combo.insertSeparator(combo.count())
        for name, w, h in COMMON_SIZES:
            combo.addItem(f"{name} — {w} × {h}", (w, h))
        combo.addItem("Custom size", None)
        combo.blockSignals(False)

    # ── Change plumbing ──────────────────────────────────────────────

    def _push_undo(self, key=None):
        now = time.monotonic()
        if key is not None and key == self._undo_key and now - self._undo_time < 1.5:
            self._undo_time = now  # coalesce a burst of the same edit
            return
        self._undo.append((self.mosaic.snapshot(), self._selected_index()))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._undo_key, self._undo_time = key, now
        self._update_actions()

    def _selected_index(self):
        leaves = self.mosaic.leaves()
        return next((i for i, c in enumerate(leaves) if c is self.view.selected), -1)

    def _restore(self, state):
        snap, index = state
        self.mosaic.restore(snap)
        leaves = self.mosaic.leaves()
        self.view.select(leaves[min(index, len(leaves) - 1)] if index >= 0 else leaves[0])
        self._undo_key = None
        self._changed()

    def _on_undo(self):
        if self._undo:
            self._redo.append((self.mosaic.snapshot(), self._selected_index()))
            self._restore(self._undo.pop())

    def _on_redo(self):
        if self._redo:
            self._undo.append((self.mosaic.snapshot(), self._selected_index()))
            self._restore(self._redo.pop())

    def _changed(self, dirty=True):
        """Bring every view of the design up to date after an edit."""
        if dirty:
            self._dirty = True
        leaves = self.mosaic.leaves()
        sel = self.view.selected
        if not any(c is sel for c in leaves):  # rebuilt: follow the image, else pick one
            match = next((c for c in leaves if sel is not None and sel.pixmap is not None
                          and c.pixmap is sel.pixmap), None)
            self.view.selected = None
            self.view.select(match or leaves[0])
        self.view.update()
        self._sync_controls()
        self._refresh_templates()
        self._refresh_cell_panel()
        self._update_actions()
        m = self.mosaic
        images, empty = m.image_count(), sum(1 for c in leaves if c.pixmap is None)
        info = f"{m.width} × {m.height} px   ·   {images} image{'s' if images != 1 else ''}"
        if empty and images:
            info += f"   ·   {empty} empty"
        self._info.setText(info)
        self._layout_mode.setText(
            "<b>Auto layout</b> — re-flows as you add images" if m.auto else
            "<b>Custom layout</b> — Auto arrange to re-flow")

    def _sync_controls(self):
        m = self.mosaic
        for spin, value in ((self._w_spin, m.width), (self._h_spin, m.height)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        combo = self._size_combo
        index = next((i for i in range(combo.count()) if combo.itemData(i) == (m.width, m.height)),
                     combo.count() - 1)
        combo.setCurrentIndex(index)
        for attr, ctrl in self._style.items():
            ctrl.setValue(getattr(m, attr))
        self._bg_btn.setIcon(swatch(m.background, QSize(18, 18)))

    def _update_actions(self):
        a = self._actions
        a["undo"].setEnabled(bool(self._undo))
        a["redo"].setEnabled(bool(self._redo))
        has = not self.mosaic.is_empty()
        for k in ("save", "set_wallpaper", "to_viewer"):
            a[k].setEnabled(has)

    def _refresh_templates(self):
        m = self.mosaic
        n = len(m.leaves())
        aspect = max(1, m.width - 2 * m.margin) / max(1, m.height - 2 * m.margin)
        sig = (n, round(aspect, 3))
        if sig == self._template_sig:
            return
        self._template_sig = sig
        while self._tiles.count():
            item = self._tiles.takeAt(0)
            item.widget().deleteLater()
        for i, (name, tree) in enumerate(templates(n, aspect)):
            b = QToolButton()
            b.setIcon(self._template_icon(tree, aspect))
            b.setIconSize(QSize(52, 32))
            b.setAutoRaise(True)
            b.setToolTip(f"{name} — {n} cell{'s' if n != 1 else ''}")
            b.clicked.connect(lambda _c=False, t=tree: self._apply_template(t))
            self._tiles.addWidget(b, i // 4, i % 4)

    def _template_icon(self, tree, aspect):
        w, h = 52, 32
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(w * dpr), round(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        tmp = Mosaic(1000, max(1, round(1000 / aspect)))
        tmp.root = tree.clone()
        s = min((w - 4) / tmp.width, (h - 4) / tmp.height)
        tmp.spacing = 2 / s
        ox, oy = (w - tmp.width * s) / 2, (h - tmp.height * s) / 2
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (_cell, r) in enumerate(tmp.layout()[0]):
            p.setBrush(QColor(120, 170, 235) if i == 0 else QColor(200, 200, 200))
            p.drawRoundedRect(QRectF(ox + r.x() * s, oy + r.y() * s, r.width() * s, r.height() * s), 1.5, 1.5)
        p.end()
        return QIcon(pm)

    def _refresh_cell_panel(self):
        cell = self.view.selected
        box = self._cell_box
        box.setEnabled(cell is not None)
        has_image = cell is not None and cell.pixmap is not None
        for w in self._image_controls:
            w.setEnabled(has_image)
        if cell is None:
            self._cell_name.setText("Nothing selected")
            self._cell_info.setText("Click a cell to select it.")
            return
        if not has_image:
            self._cell_name.setText("Empty cell")
            self._cell_info.setText("Drop an image here, paste one (Ctrl+V), or double-click to choose a file.")
            return
        rect = self.mosaic.cell_rect(cell)
        placed, scale = cell_placement(cell, rect)
        visible = placed.intersected(rect)
        shown = visible.width() * visible.height() / max(1.0, placed.width() * placed.height())
        name = self._cell_name.fontMetrics().elidedText(cell.name or "Untitled",
                                                        Qt.TextElideMode.ElideMiddle, 290)
        self._cell_name.setText(name)
        info = (f"{cell.pixmap.width()} × {cell.pixmap.height()} px · shown at {scale * 100:.0f}%"
                f" · {shown * 100:.0f}% visible")
        if scale > SOFT_SCALE:
            info += "<br><span style='color:#e8a33d'>Enlarged — may look soft at this size.</span>"
        self._cell_info.setText(info)
        self._fill_group.button(1 if cell.fit else 0).setChecked(True)
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(round(cell.zoom * 100))
        self._zoom_slider.blockSignals(False)

    def _on_selection_changed(self):
        self._refresh_cell_panel()

    # ── Edits ────────────────────────────────────────────────────────

    def _selected_or_first(self):
        return self.view.selected or self.mosaic.leaves()[0]

    def _on_new(self):
        if self.mosaic.is_empty() and len(self.mosaic.leaves()) == 1:
            return
        self._push_undo()
        self.mosaic.root = Cell()
        self.mosaic.auto = True
        self.view.select(self.mosaic.root)
        self._dirty = False
        self._changed(dirty=False)
        self.statusBar().showMessage("Started a new design — Undo brings the previous one back.", 5000)

    def _load_sources(self, sources):
        items = []
        for src in sources:
            if isinstance(src, QPixmap):
                items.append((src, "Dropped image"))
                continue
            pixmap, _animated = load_image(src)
            if pixmap.isNull():
                self.statusBar().showMessage(f"Couldn't load {os.path.basename(src)}", 4000)
            else:
                items.append((pixmap, os.path.basename(src)))
        return items

    def _choose_files(self, multiple=True):
        start = self._settings.value("wallpaper/last_dir") or self._settings.value("last_dir") or ""
        if multiple:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add Images", start, IMAGE_FILTER)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose Image", start, IMAGE_FILTER)
            paths = [path] if path else []
        if paths:
            self._settings.setValue("wallpaper/last_dir", os.path.dirname(paths[0]))
        return paths

    def _on_add_files(self):
        paths = self._choose_files()
        if paths:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                items = self._load_sources(paths)
            finally:
                QApplication.restoreOverrideCursor()
            self.add_images(items)

    def _on_add_from_viewer(self):
        item = self._viewer.current_image()
        if item is None:
            self.statusBar().showMessage("Open an image in the main window first.", 4000)
        else:
            self.add_images([item])

    def _clipboard_or_warn(self):
        pixmap = clipboard_pixmap()
        if pixmap.isNull():
            self.statusBar().showMessage("There's no image on the clipboard.", 4000)
            return None
        return pixmap

    def _on_paste(self):
        pixmap = self._clipboard_or_warn()
        if pixmap is None:
            return
        cell = self.view.selected
        if cell is not None and cell.pixmap is None:
            self._set_cell_image(cell, pixmap, "Clipboard image")
        else:
            self.add_images([(pixmap, "Clipboard image")])

    def _paste_beside(self, side):
        pixmap = self._clipboard_or_warn()
        if pixmap is None:
            return
        self._push_undo()
        cell = self.mosaic.insert_beside(self._selected_or_first(), side)
        cell.set_image(pixmap, "Clipboard image")
        self.view.select(cell)
        self._changed()

    def _add_empty(self, side):
        self._push_undo()
        self.view.select(self.mosaic.insert_beside(self._selected_or_first(), side))
        self._changed()

    def _set_cell_image(self, cell, pixmap, name):
        self._push_undo()
        cell.set_image(pixmap, name)
        if self.mosaic.auto and self.mosaic.image_count() > 1:
            self.mosaic.auto_arrange()
        self._changed()

    def _browse_into(self, cell):
        paths = self._choose_files(multiple=False)
        items = self._load_sources(paths)
        if items:
            self._set_cell_image(cell, *items[0])

    def _on_replace(self):
        if self.view.selected is not None:
            self._browse_into(self.view.selected)

    def _on_clear(self):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None:
            self._push_undo()
            cell.clear()
            self.mosaic.auto = False
            self._changed()

    def _on_remove(self):
        cell = self.view.selected
        if cell is None:
            return
        self._push_undo()
        index = self._selected_index()
        self.mosaic.remove(cell)
        leaves = self.mosaic.leaves()
        self.view.select(leaves[min(index, len(leaves) - 1)])
        self._changed()

    def _set_fit(self, fit):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None and cell.fit != fit:
            self._push_undo()
            cell.fit = fit
            cell.reset_view()
            self._changed()

    def _on_rotate(self):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None:
            self._push_undo()
            cell.rotate(90)
            if self.mosaic.auto and self.mosaic.image_count() > 1:
                self.mosaic.auto_arrange()  # the new shape may fit elsewhere better
            self._changed()

    def _on_reset_cell(self):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None:
            self._push_undo()
            cell.reset_view()
            self._changed()

    def _on_zoom_slider(self, value):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None:
            self._push_undo(("zoom", id(cell)))
            self.view.zoom_cell(cell, value / 100 / cell.zoom)

    def _zoom_selected(self, factor):
        cell = self.view.selected
        if cell is not None and cell.pixmap is not None:
            self._push_undo(("zoom", id(cell)))
            self.view.zoom_cell(cell, factor)

    def _apply_template(self, tree):
        self._push_undo()
        self.mosaic.apply_structure(tree)
        self._changed()

    def _on_auto_arrange(self):
        if self.mosaic.image_count():
            self._push_undo()
            self.mosaic.auto_arrange()
            self._changed()

    def _on_balance(self):
        self._push_undo()
        self.mosaic.balance()
        self._changed()

    def _on_equalize(self):
        self._push_undo()
        self.mosaic.equalize()
        self._changed()

    def _on_shuffle(self):
        if self.mosaic.image_count() > 1:
            self._push_undo()
            self.mosaic.shuffle()
            self._changed()

    def _on_style(self, attr, value):
        if getattr(self.mosaic, attr) != value:
            self._push_undo(("style", attr))
            setattr(self.mosaic, attr, value)
            self._settings.setValue(f"wallpaper/{attr}", value)
            self._changed()

    def _on_background(self, color):
        if color.isValid() and color != self.mosaic.background:
            self._push_undo()
            self.mosaic.background = QColor(color)
            self._settings.setValue("wallpaper/background", color.name())
            self._changed()

    def _pick_background(self):
        self._on_background(QColorDialog.getColor(self.mosaic.background, self, "Background Colour"))

    def _on_size_preset(self, index):
        size = self._size_combo.itemData(index)
        if size:
            self._set_canvas_size(*size)
        else:
            self._w_spin.setFocus()
            self._w_spin.selectAll()

    def _on_size_edited(self):
        self._set_canvas_size(self._w_spin.value(), self._h_spin.value())

    def _set_canvas_size(self, w, h):
        if (w, h) != (self.mosaic.width, self.mosaic.height):
            self._push_undo()
            self.mosaic.set_size(w, h)
            self._settings.setValue("wallpaper/width", w)
            self._settings.setValue("wallpaper/height", h)
            self._changed()

    def _on_sources_dropped(self, sources, cell, zone):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            items = self._load_sources(sources)
        finally:
            QApplication.restoreOverrideCursor()
        if not items:
            return
        if cell is None:
            self.add_images(items)
            return
        self._push_undo()
        target = cell
        for i, (pixmap, name) in enumerate(items):
            if i == 0 and zone == "center":
                cell.set_image(pixmap, name)
            else:
                target = self.mosaic.insert_beside(target, zone if zone != "center" else "right")
                target.set_image(pixmap, name)
        if self.mosaic.auto and self.mosaic.image_count() > 1:
            self.mosaic.auto_arrange()
        self.view.select(target)
        self._changed()

    def _on_command(self, name, arg):
        handlers = {
            "context": lambda: self._show_context_menu(*arg),
            "browse": lambda: self._browse_into(arg),
            "paste_beside": lambda: self._paste_beside(arg),
            "remove": self._on_remove,
            "toggle_fit": lambda: self.view.selected and self._set_fit(not self.view.selected.fit),
            "rotate": self._on_rotate,
            "reset": self._on_reset_cell,
            "zoom": lambda: self._zoom_selected(arg),
        }
        handlers[name]()

    def _show_context_menu(self, cell, global_pos):
        m = QMenu(self)
        if cell is None:
            m.addAction(self._actions["add"])
            m.addAction(self._actions["paste"])
            m.exec(global_pos)
            return
        clip = not clipboard_pixmap().isNull()
        if cell.pixmap is None:
            a = m.addAction(icon("paste"), "Paste Here")
            a.setEnabled(clip)
            a.triggered.connect(lambda: self._set_cell_image(cell, clipboard_pixmap(), "Clipboard image"))
            m.addAction(icon("open"), "Choose Image…").triggered.connect(lambda: self._browse_into(cell))
        else:
            a = m.addAction(icon("paste"), "Replace with Clipboard")
            a.setEnabled(clip)
            a.triggered.connect(lambda: self._set_cell_image(cell, clipboard_pixmap(), "Clipboard image"))
            m.addAction(icon("open"), "Replace with File…").triggered.connect(lambda: self._browse_into(cell))
        beside = m.addMenu(icon("paste_side"), "Paste Beside")
        beside.setEnabled(clip)
        empty = m.addMenu("Add Empty Cell")
        for side in ("left", "right", "top", "bottom"):
            a = beside.addAction(SIDE_NAMES[side])
            a.setShortcut(QKeySequence(f"Ctrl+Shift+{SIDE_KEYS[side]}"))
            a.triggered.connect(lambda _c=False, sd=side: self._paste_beside(sd))
            empty.addAction(SIDE_NAMES[side]).triggered.connect(
                lambda _c=False, sd=side: self._add_empty(sd))
        if cell.pixmap is not None:
            m.addSeparator()
            a = m.addAction("Fit Whole Image")
            a.setCheckable(True)
            a.setChecked(cell.fit)
            a.setShortcut(QKeySequence("F"))
            a.triggered.connect(lambda on: self._set_fit(on))
            a = m.addAction(icon("rotate_right"), "Rotate")
            a.setShortcut(QKeySequence("R"))
            a.triggered.connect(self._on_rotate)
            a = m.addAction(icon("reset"), "Reset Position")
            a.setShortcut(QKeySequence("0"))
            a.triggered.connect(self._on_reset_cell)
            m.addSeparator()
            m.addAction("Clear Image").triggered.connect(self._on_clear)
        a = m.addAction(icon("trash"), "Remove Cell")
        a.setShortcut(QKeySequence("Delete"))
        a.triggered.connect(self._on_remove)
        m.exec(global_pos)

    # ── Output ───────────────────────────────────────────────────────

    def _render_checked(self):
        if self.mosaic.is_empty():
            QMessageBox.information(self, "Wallpaper Maker", "Add some images first.")
            return None
        empty = sum(1 for c in self.mosaic.leaves() if c.pixmap is None)
        if empty:
            reply = QMessageBox.question(
                self, "Wallpaper Maker",
                f"{empty} cell{' is' if empty == 1 else 's are'} empty and will show the "
                "background colour. Continue anyway?")
            if reply != QMessageBox.StandardButton.Yes:
                return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return render(self.mosaic)
        finally:
            QApplication.restoreOverrideCursor()

    def _on_save_as(self):
        image = self._render_checked()
        if image is None:
            return
        folder = (self._settings.value("wallpaper/save_dir")
                  or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation))
        start = os.path.join(folder, f"wallpaper-{self.mosaic.width}x{self.mosaic.height}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save Wallpaper", start,
                                              "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp)")
        if not path:
            return
        lossy = os.path.splitext(path)[1].lower() in (".jpg", ".jpeg", ".webp")
        if image.save(path, None, 95 if lossy else -1):
            self._dirty = False
            self._settings.setValue("wallpaper/save_dir", os.path.dirname(path))
            self.statusBar().showMessage(f"Saved {path}", 5000)
        else:
            QMessageBox.warning(self, "Save Wallpaper", f"Couldn't save the file:\n{path}")

    def _on_set_wallpaper(self):
        image = self._render_checked()
        if image is None:
            return
        pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        folder = os.path.join(pictures or os.path.expanduser("~"), "Wallpapers")
        stamp = QDateTime.currentDateTime().toString("yyyyMMdd-HHmmss")
        path = os.path.join(folder, f"imamax-{stamp}.png")
        try:
            os.makedirs(folder, exist_ok=True)
            saved = image.save(path)
        except OSError:
            saved = False
        if not saved:
            QMessageBox.warning(self, "Set as Wallpaper", f"Couldn't save the wallpaper to\n{folder}")
            return
        self._dirty = False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, detail = apply_wallpaper(path)
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            self.statusBar().showMessage(f"Wallpaper set ({detail}). Saved as {path}", 8000)
        else:
            QMessageBox.information(
                self, "Set as Wallpaper",
                f"The wallpaper was saved as\n{path}\n\nbut couldn't be applied automatically "
                f"({detail}). You can pick it in your desktop's wallpaper settings.")

    def _on_to_viewer(self):
        image = self._render_checked()
        if image is not None:
            self._viewer.receive_image(QPixmap.fromImage(image))
            self._dirty = False

    def closeEvent(self, event):
        self._settings.setValue("wallpaper/geometry", self.saveGeometry())
        super().closeEvent(event)  # just hides; the design is kept for next time


_PLASMA_SCRIPT = """
var all = desktops();
for (var i = 0; i < all.length; i++) {
    var d = all[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", %s);
}
"""


def apply_wallpaper(path):
    """Make `path` the desktop background. Returns (ok, detail)."""
    path = os.path.abspath(path)
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ok = ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)  # SPI_SETDESKWALLPAPER
            return bool(ok), "Windows"
        desktop = ":".join(os.environ.get(k, "") for k in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION")).lower()
        detail = "unrecognised desktop"
        if "kde" in desktop or "plasma" in desktop:
            tool = shutil.which("plasma-apply-wallpaperimage")  # Plasma 5.24+
            if tool:
                r = subprocess.run([tool, path], capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    return True, "KDE Plasma"
                detail = (r.stderr or r.stdout).strip() or "plasma-apply-wallpaperimage failed"
            url = "file://" + path
            for exe in ("qdbus6", "qdbus", "qdbus-qt6", "qdbus-qt5"):
                qdbus = shutil.which(exe)
                if qdbus:
                    r = subprocess.run([qdbus, "org.kde.plasmashell", "/PlasmaShell",
                                        "org.kde.PlasmaShell.evaluateScript",
                                        _PLASMA_SCRIPT % json.dumps(url)],
                                       capture_output=True, text=True, timeout=30)
                    if r.returncode == 0:
                        return True, "KDE Plasma"
                    detail = (r.stderr or r.stdout).strip() or detail
                    break
            return False, detail
        if shutil.which("gsettings") and any(d in desktop for d in (
                "gnome", "ubuntu", "unity", "budgie", "pantheon", "cinnamon")):
            schema = ("org.cinnamon.desktop.background" if "cinnamon" in desktop
                      else "org.gnome.desktop.background")
            uri = "file://" + path
            r = subprocess.run(["gsettings", "set", schema, "picture-uri", uri],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return False, r.stderr.strip() or "gsettings failed"
            subprocess.run(["gsettings", "set", schema, "picture-uri-dark", uri],
                           capture_output=True, text=True, timeout=30)  # absent on older GNOME
            return True, "GNOME"
        return False, detail
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
