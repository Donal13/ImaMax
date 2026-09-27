"""Image canvas: zoomed rendering, the crop tool, and drag-to-pan.

The canvas is sized to the displayed image plus a margin (``pad``) that is
only non-zero in crop mode, so crop handles on the image border are never
clipped. Downscaled views use a cached high-quality scale; magnified views
paint just the exposed area straight from the source, with sharp
(nearest-neighbour) pixels from 200% up for precise work.

The crop selection is kept in image pixel coordinates, which keeps it anchored
to the same pixels whatever the zoom or window size.
"""

import math

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

SHARP_ZOOM = 2.0     # at and above this zoom, pixels are drawn as crisp squares
HANDLE_REACH = 8     # px either side of a selection edge that grabs it
CORNER_REACH = 16    # px along an edge, from a corner, that still grabs the corner
DRAG_THRESHOLD = 3   # px of movement before a press becomes a new selection

ACCENT = QColor(74, 158, 255)

_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


class ImageCanvas(QWidget):

    crop_changed = pyqtSignal(QRect)      # selection, in image pixels
    crop_drag_active = pyqtSignal(bool)   # a crop drag began / ended
    crop_apply_requested = pyqtSignal()   # double-click inside the selection
    hover_changed = pyqtSignal(object)    # image pixel under the cursor (QPoint) or None
    pan_delta = pyqtSignal(QPoint)        # mouse movement while panning
    double_clicked = pyqtSignal()

    CROP_PAD = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._pixmap = None        # image (or current animation frame) being shown
        self._preview = None       # temporary stand-in, e.g. adjustments preview
        self._scaled = None        # cached smooth downscale of _pixmap
        self._scaled_key = None
        self._zoom = 1.0
        self._pad = 0
        self._bg = QColor(43, 43, 43)
        self._placeholder = ("", "")
        self._hover_px = None

        self._pannable = False
        self._panning = False
        self._space_pan = False
        self._last_pan = QPoint()

        self._crop_mode = False
        self._sel = QRect()
        self._ratio = None         # locked width/height ratio, or None for free
        self._drag = None          # active crop drag state
        self._hover_hit = None     # crop handle under the cursor

    # ── Content and view ─────────────────────────────────────────────

    def set_pixmap(self, pixmap):
        """Show `pixmap` (None shows the placeholder). Keeps zoom and selection."""
        old_size = self._pixmap.size() if self._pixmap is not None else None
        self._pixmap = pixmap
        self._scaled = None
        if pixmap is None:
            self._sel = QRect()
        elif self._crop_mode and pixmap.size() != old_size:
            self._set_sel(self._full_rect(), force=True)
        self.resize(self.sizeHint())
        self.update()

    def set_frame(self, pixmap):
        """Swap in the next animation frame (same size, no relayout)."""
        self._pixmap = pixmap
        self.update()

    def set_preview(self, pixmap):
        self._preview = pixmap
        self.update()

    def set_zoom(self, zoom):
        if zoom != self._zoom:
            self._zoom = zoom
            self._scaled = None
        self.resize(self.sizeHint())
        self.update()

    def zoom(self):
        return self._zoom

    def pad(self):
        return self._pad

    def set_background(self, color):
        self._bg = QColor(color)
        self.update()

    def set_placeholder(self, title, subtitle):
        self._placeholder = (title, subtitle)
        self.update()

    def set_pannable(self, pannable):
        self._pannable = pannable
        self._update_cursor()

    def set_space_pan(self, held):
        """Space held in crop mode: left-drag pans instead of editing."""
        if held != self._space_pan:
            self._space_pan = held
            self._update_cursor()

    def sizeHint(self):
        d = self._display_size()
        return QSize(d.width() + 2 * self._pad, d.height() + 2 * self._pad)

    def _display_size(self):
        if self._pixmap is None:
            return QSize(0, 0)
        return QSize(max(1, round(self._pixmap.width() * self._zoom)),
                     max(1, round(self._pixmap.height() * self._zoom)))

    def _scale_xy(self):
        d = self._display_size()
        return d.width() / self._pixmap.width(), d.height() / self._pixmap.height()

    def image_rect(self):
        """Where the image is drawn, in widget coordinates."""
        d = self._display_size()
        return QRectF(self._pad, self._pad, d.width(), d.height())

    def map_to_image(self, pos):
        """Widget point -> image point (float, unclamped)."""
        sx, sy = self._scale_xy()
        return QPointF((pos.x() - self._pad) / sx, (pos.y() - self._pad) / sy)

    def map_from_image(self, pt):
        sx, sy = self._scale_xy()
        return QPointF(self._pad + pt.x() * sx, self._pad + pt.y() * sy)

    # ── Crop API ─────────────────────────────────────────────────────

    def set_crop_mode(self, enabled):
        self._crop_mode = enabled
        self._pad = self.CROP_PAD if enabled else 0
        self._drag = None
        self._hover_hit = None
        self._sel = QRect()
        if enabled and self._pixmap is not None:
            self._sel = self._conform(self._full_rect(), self._ratio) if self._ratio else self._full_rect()
            self.crop_changed.emit(QRect(self._sel))
        self.resize(self.sizeHint())
        self._update_cursor()
        self.update()

    def crop_selection(self):
        return QRect(self._sel)

    def is_crop_full(self):
        return self._sel == self._full_rect()

    def is_crop_dragging(self):
        return self._drag is not None

    def set_crop_ratio(self, ratio):
        """Lock the selection to width/height `ratio` (None = free)."""
        self._ratio = ratio
        if self._crop_mode and ratio:
            self._set_sel(self._conform(self._sel, ratio), force=True)

    def reset_crop(self):
        full = self._full_rect()
        self._set_sel(self._conform(full, self._ratio) if self._ratio else full, force=True)

    def swap_crop(self):
        """Swap the selection's width and height about its centre."""
        r = self._sel
        iw, ih = self._image_wh()
        w, h = r.height(), r.width()
        k = min(1.0, iw / w, ih / h)
        w, h = max(1, round(w * k)), max(1, round(h * k))
        c = QPointF(r.x() + r.width() / 2, r.y() + r.height() / 2)
        self._set_sel(QRect(round(c.x() - w / 2), round(c.y() - h / 2), w, h), force=True)

    def nudge_crop(self, dx, dy):
        self._set_sel(self._moved(self._sel, QPointF(dx, dy)))

    def set_crop_field(self, rect, field):
        """Apply a numeric edit of one field ('x', 'y', 'w' or 'h')."""
        iw, ih = self._image_wh()
        r = QRect(self._sel)
        if field == "x":
            r.moveLeft(rect.x())
        elif field == "y":
            r.moveTop(rect.y())
        elif field == "w":
            w = min(max(1, rect.width()), iw)
            h = r.height()
            if self._ratio:
                h = max(1, round(w / self._ratio))
                if h > ih:
                    h, w = ih, max(1, round(ih * self._ratio))
            r = QRect(min(r.x(), iw - w), min(r.y(), ih - h), w, h)
        elif field == "h":
            h = min(max(1, rect.height()), ih)
            w = r.width()
            if self._ratio:
                w = max(1, round(h * self._ratio))
                if w > iw:
                    w, h = iw, max(1, round(iw / self._ratio))
            r = QRect(min(r.x(), iw - w), min(r.y(), ih - h), w, h)
        self._set_sel(r, force=True)

    def continue_crop_drag(self, global_pos, modifiers):
        """Re-run the active drag at `global_pos` (used while auto-scrolling)."""
        if self._drag is not None:
            self._crop_drag_to(QPointF(self.mapFromGlobal(global_pos)), modifiers)

    # ── Crop geometry (image pixel space) ────────────────────────────

    def _image_wh(self):
        return self._pixmap.width(), self._pixmap.height()

    def _full_rect(self):
        if self._pixmap is None:
            return QRect()
        return QRect(0, 0, self._pixmap.width(), self._pixmap.height())

    def _snap(self, pt):
        """Nearest pixel edge to `pt`, clamped to the image."""
        iw, ih = self._image_wh()
        return min(max(round(pt.x()), 0), iw), min(max(round(pt.y()), 0), ih)

    def _clamp_rect(self, r):
        iw, ih = self._image_wh()
        w = min(max(1, r.width()), iw)
        h = min(max(1, r.height()), ih)
        return QRect(min(max(r.x(), 0), iw - w), min(max(r.y(), 0), ih - h), w, h)

    def _moved(self, start, delta):
        return self._clamp_rect(start.translated(round(delta.x()), round(delta.y())))

    def _conform(self, r, ratio):
        """Largest rect of `ratio` with about the same area and centre as `r`."""
        iw, ih = self._image_wh()
        w = math.sqrt(max(1, r.width() * r.height()) * ratio)
        h = w / ratio
        if w > iw:
            w, h = iw, iw / ratio
        if h > ih:
            h, w = ih, ih * ratio
        w, h = max(1, round(w)), max(1, round(h))
        cx, cy = r.x() + r.width() / 2, r.y() + r.height() / 2
        return self._clamp_rect(QRect(round(cx - w / 2), round(cy - h / 2), w, h))

    def _span(self, anchor, pt, ratio):
        """Rect from the fixed `anchor` edge point towards `pt`, optionally ratio-locked."""
        iw, ih = self._image_wh()
        ax, ay = anchor
        if ratio is None:
            cx, cy = self._snap(pt)
            x0, x1 = sorted((ax, cx))
            y0, y1 = sorted((ay, cy))
            return self._clamp_rect(QRect(x0, y0, x1 - x0, y1 - y0))
        dx, dy = pt.x() - ax, pt.y() - ay
        sx = -1 if dx < 0 else 1
        sy = -1 if dy < 0 else 1
        w, h = abs(dx), abs(dy)
        if w < h * ratio:
            w = h * ratio
        else:
            h = w / ratio
        avail_w = ax if sx < 0 else iw - ax
        avail_h = ay if sy < 0 else ih - ay
        if w > avail_w:
            w, h = avail_w, avail_w / ratio
        if h > avail_h:
            h, w = avail_h, avail_h * ratio
        w, h = max(1, round(w)), max(1, round(h))
        return self._clamp_rect(QRect(ax - w if sx < 0 else ax, ay - h if sy < 0 else ay, w, h))

    def _edge_drag(self, edge, start, pt, ratio):
        iw, ih = self._image_wh()
        left, top = start.x(), start.y()
        right, bottom = left + start.width(), top + start.height()
        if ratio is None:
            px, py = self._snap(pt)
            if edge == "l":
                left = px
            elif edge == "r":
                right = px
            elif edge == "t":
                top = py
            else:
                bottom = py
            x0, x1 = sorted((left, right))
            y0, y1 = sorted((top, bottom))
            return self._clamp_rect(QRect(x0, y0, x1 - x0, y1 - y0))
        # Ratio locked: the dragged edge sets one side; the other follows, centred
        if edge in ("l", "r"):
            ax = right if edge == "l" else left
            d = pt.x() - ax
            avail = ax if d < 0 else iw - ax
            w = min(abs(d), avail)
            h = w / ratio
            if h > ih:
                h, w = ih, ih * ratio
            w, h = max(1, round(w)), max(1, round(h))
            y = min(max(round((top + bottom) / 2 - h / 2), 0), ih - h)
            return self._clamp_rect(QRect(ax - w if d < 0 else ax, y, w, h))
        ay = bottom if edge == "t" else top
        d = pt.y() - ay
        avail = ay if d < 0 else ih - ay
        h = min(abs(d), avail)
        w = h * ratio
        if w > iw:
            w, h = iw, iw / ratio
        w, h = max(1, round(w)), max(1, round(h))
        x = min(max(round((left + right) / 2 - w / 2), 0), iw - w)
        return self._clamp_rect(QRect(x, ay - h if d < 0 else ay, w, h))

    def _set_sel(self, rect, force=False):
        rect = self._clamp_rect(rect)
        if rect != self._sel or force:
            self._sel = rect
            self.update()
            self.crop_changed.emit(QRect(rect))

    # ── Crop interaction ─────────────────────────────────────────────

    def _sel_widget_rect(self):
        """Selection in widget pixels, snapped to whole pixels."""
        sx, sy = self._scale_xy()
        r = self._sel
        x0 = round(self._pad + r.x() * sx)
        y0 = round(self._pad + r.y() * sy)
        x1 = round(self._pad + (r.x() + r.width()) * sx)
        y1 = round(self._pad + (r.y() + r.height()) * sy)
        return QRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    def _hit_test(self, pos):
        if not self._crop_mode or self._sel.isEmpty() or self._pixmap is None:
            return None
        s = QRectF(self._sel_widget_rect())
        x, y = pos.x(), pos.y()
        reach = HANDLE_REACH
        if not (s.left() - reach <= x <= s.right() + reach
                and s.top() - reach <= y <= s.bottom() + reach):
            return None
        # The grab band shrinks inside small selections so they stay movable
        in_x = min(reach, s.width() / 4)
        in_y = min(reach, s.height() / 4)
        v = "t" if y <= s.top() + in_y else ("b" if y >= s.bottom() - in_y else "")
        h = "l" if x <= s.left() + in_x else ("r" if x >= s.right() - in_x else "")
        corner_x = min(CORNER_REACH, s.width() / 3)
        corner_y = min(CORNER_REACH, s.height() / 3)
        if v and not h:
            h = "l" if x <= s.left() + corner_x else ("r" if x >= s.right() - corner_x else "")
        elif h and not v:
            v = "t" if y <= s.top() + corner_y else ("b" if y >= s.bottom() - corner_y else "")
        return (v + h) or "move"

    def _drag_ratio(self, modifiers, start):
        if self._ratio:
            return self._ratio
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            if start is None or start.height() == 0:
                return 1.0  # Shift while drawing: square
            return start.width() / start.height()  # Shift on a handle: keep shape
        return None

    def _crop_press(self, pos, modifiers):
        hit = self._hit_test(pos)
        mode = hit
        if hit is None or (hit == "move" and self.is_crop_full()):
            mode = "new"  # nothing to move when the whole image is selected
        self._drag = {
            "mode": mode,
            "press": QPointF(pos),
            "origin": self.map_to_image(pos),
            "start": QRect(self._sel),
            "active": mode != "new",
        }
        self._hover_hit = None if mode == "new" else mode
        self.crop_drag_active.emit(True)
        self._update_cursor()
        self.update()

    def _crop_drag_to(self, pos, modifiers):
        d = self._drag
        if not d["active"]:
            delta = pos - d["press"]
            if abs(delta.x()) + abs(delta.y()) < DRAG_THRESHOLD:
                return
            d["active"] = True
        pt = self.map_to_image(pos)
        mode, start = d["mode"], d["start"]
        if mode == "move":
            rect = self._moved(start, pt - d["origin"])
        elif mode == "new":
            rect = self._span(self._snap(d["origin"]), pt, self._drag_ratio(modifiers, None))
        elif len(mode) == 2:
            anchor = (start.x() + (start.width() if "l" in mode else 0),
                      start.y() + (start.height() if "t" in mode else 0))
            rect = self._span(anchor, pt, self._drag_ratio(modifiers, start))
        else:
            rect = self._edge_drag(mode, start, pt, self._drag_ratio(modifiers, start))
        self._set_sel(rect)

    def _crop_release(self, pos, modifiers):
        if self._drag is not None:
            if self._drag["active"]:
                self._crop_drag_to(pos, modifiers)
            self._drag = None
            self.crop_drag_active.emit(False)
        self._hover_hit = self._hit_test(pos)
        self._update_cursor()
        self.update()

    # ── Mouse ────────────────────────────────────────────────────────

    def _update_cursor(self):
        if self._panning:
            shape = Qt.CursorShape.ClosedHandCursor
        elif self._crop_mode and self._space_pan and self._pannable:
            shape = Qt.CursorShape.OpenHandCursor
        elif self._crop_mode:
            hit = self._drag["mode"] if self._drag and self._drag["mode"] != "new" else self._hover_hit
            if hit == "move" and self.is_crop_full():
                hit = None
            shape = _CURSORS.get(hit, Qt.CursorShape.CrossCursor)
        elif self._pannable:
            shape = Qt.CursorShape.OpenHandCursor
        else:
            shape = Qt.CursorShape.ArrowCursor
        self.setCursor(shape)

    def _wants_pan(self, button):
        if not self._pannable:
            return False
        if button == Qt.MouseButton.MiddleButton:
            return True
        return button == Qt.MouseButton.LeftButton and (not self._crop_mode or self._space_pan)

    def mousePressEvent(self, event):
        button = event.button()
        if self._wants_pan(button):
            self._panning = True
            self._last_pan = event.globalPosition().toPoint()
            self._update_cursor()
        elif button == Qt.MouseButton.LeftButton and self._crop_mode and self._pixmap is not None:
            self._crop_press(event.position(), event.modifiers())
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        self._emit_hover(pos)
        if self._panning:
            gp = event.globalPosition().toPoint()
            self.pan_delta.emit(gp - self._last_pan)
            self._last_pan = gp
        elif self._drag is not None:
            self._crop_drag_to(pos, event.modifiers())
        elif self._crop_mode:
            hit = self._hit_test(pos)
            if hit != self._hover_hit:
                self._hover_hit = hit
                self.update()
            self._update_cursor()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (Qt.MouseButton.LeftButton,
                                                Qt.MouseButton.MiddleButton):
            self._panning = False
            self._update_cursor()
        elif self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._crop_release(event.position(), event.modifiers())
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
        elif self._crop_mode:
            if self._hit_test(event.position()) == "move" and not self.is_crop_full():
                self.crop_apply_requested.emit()
        else:
            self.double_clicked.emit()

    def leaveEvent(self, event):
        self._emit_hover(None)
        if self._hover_hit is not None and self._drag is None:
            self._hover_hit = None
            self.update()
        super().leaveEvent(event)

    def _emit_hover(self, pos):
        px = None
        if pos is not None and self._pixmap is not None:
            pt = self.map_to_image(pos)
            x, y = math.floor(pt.x()), math.floor(pt.y())
            if 0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height():
                px = QPoint(x, y)
        if px != self._hover_px:
            self._hover_px = px
            self.hover_changed.emit(px)

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), self._bg)
        if self._pixmap is None:
            self._paint_placeholder(p)
        else:
            self._paint_image(p, event.rect())
            if self._crop_mode and not self._sel.isEmpty():
                self._paint_crop(p)
        p.end()

    def _paint_placeholder(self, p):
        title, subtitle = self._placeholder
        r = self.rect()
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.5)
        p.setFont(font)
        p.setPen(QColor(170, 170, 170))
        p.drawText(r.adjusted(0, 0, 0, -26), Qt.AlignmentFlag.AlignCenter, title)
        p.setFont(self.font())
        p.setPen(QColor(120, 120, 120))
        p.drawText(r.adjusted(0, 30, 0, 0), Qt.AlignmentFlag.AlignCenter, subtitle)

    def _paint_image(self, p, exposed):
        target = self.image_rect()
        if self._preview is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(target, self._preview, QRectF(self._preview.rect()))
            return
        src = self._pixmap
        disp = self._display_size()
        if disp == src.size():
            p.drawPixmap(self._pad, self._pad, src)
            return
        dpr = self.devicePixelRatioF()
        phys = QSize(round(disp.width() * dpr), round(disp.height() * dpr))
        if phys.width() < src.width():
            # Downscaling: one high-quality (area-averaged) scale at device
            # resolution, so HiDPI screens get every available pixel
            key = (src.cacheKey(), phys.width(), phys.height())
            if self._scaled is None or self._scaled_key != key:
                self._scaled = src.scaled(phys, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
                self._scaled.setDevicePixelRatio(dpr)
                self._scaled_key = key
            p.drawPixmap(target, self._scaled, QRectF(self._scaled.rect()))
            return
        # Magnifying: draw only the exposed source pixels (plus a margin so
        # smooth filtering has neighbours and no seams show while scrolling)
        area = QRectF(exposed).intersected(target)
        if area.isEmpty():
            return
        sx, sy = self._scale_xy()
        margin = 2
        x0 = max(0, math.floor((area.left() - self._pad) / sx) - margin)
        y0 = max(0, math.floor((area.top() - self._pad) / sy) - margin)
        x1 = min(src.width(), math.ceil((area.right() - self._pad) / sx) + margin)
        y1 = min(src.height(), math.ceil((area.bottom() - self._pad) / sy) + margin)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < SHARP_ZOOM)
        p.drawPixmap(QRectF(self._pad + x0 * sx, self._pad + y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy),
                     src, QRectF(x0, y0, x1 - x0, y1 - y0))

    def _paint_crop(self, p):
        s = self._sel_widget_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Dim everything outside the selection
        shade = QPainterPath()
        shade.setFillRule(Qt.FillRule.OddEvenFill)
        shade.addRect(self.image_rect())
        shade.addRect(QRectF(s))
        p.fillPath(shade, QColor(0, 0, 0, 150))

        # Rule-of-thirds guides
        if s.width() > 45 and s.height() > 45:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            for i in (1, 2):
                x = s.left() + round(s.width() * i / 3)
                y = s.top() + round(s.height() * i / 3)
                p.drawLine(x, s.top(), x, s.bottom())
                p.drawLine(s.left(), y, s.right(), y)

        # Border just outside the kept pixels: white with a dark halo
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 140), 1))
        p.drawRect(s.adjusted(-2, -2, 1, 1))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1))
        p.drawRect(s.adjusted(-1, -1, 0, 0))

        # Handles: L-brackets on the corners, bars on the edge midpoints
        active = self._drag["mode"] if self._drag and self._drag["mode"] != "new" else self._hover_hit
        for name, rects in self._handle_rects(s).items():
            color = ACCENT if active == name else QColor(255, 255, 255)
            for r in rects:
                p.fillRect(r.adjusted(-1, -1, 1, 1), QColor(0, 0, 0, 110))
            for r in rects:
                p.fillRect(r, color)
        if active in ("l", "r", "t", "b"):
            # Light up the whole edge being resized, just outside the kept pixels
            edge = {"l": QRect(s.left() - 3, s.top(), 3, s.height()),
                    "r": QRect(s.right() + 1, s.top(), 3, s.height()),
                    "t": QRect(s.left(), s.top() - 3, s.width(), 3),
                    "b": QRect(s.left(), s.bottom() + 1, s.width(), 3)}[active]
            p.fillRect(edge, ACCENT)

        self._paint_size_badge(p, s)

    def _handle_rects(self, s):
        t = 3  # bar thickness, drawn outside the selection
        length = max(6, min(20, s.width() // 2, s.height() // 2))
        left, top = s.left() - t, s.top() - t
        right, bottom = s.right() + 1, s.bottom() + 1  # first pixel outside
        span = length + t
        rects = {
            "tl": [QRect(left, top, span, t), QRect(left, top, t, span)],
            "tr": [QRect(right - length, top, span, t), QRect(right, top, t, span)],
            "bl": [QRect(left, bottom, span, t), QRect(left, bottom - length, t, span)],
            "br": [QRect(right - length, bottom, span, t), QRect(right, bottom - length, t, span)],
        }
        bar = min(24, s.width() // 4)
        if bar >= 8:
            cx = s.left() + s.width() // 2
            rects["t"] = [QRect(cx - bar // 2, top, bar, t)]
            rects["b"] = [QRect(cx - bar // 2, bottom, bar, t)]
        bar = min(24, s.height() // 4)
        if bar >= 8:
            cy = s.top() + s.height() // 2
            rects["l"] = [QRect(left, cy - bar // 2, t, bar)]
            rects["r"] = [QRect(right, cy - bar // 2, t, bar)]
        return rects

    def _paint_size_badge(self, p, s):
        text = f"{self._sel.width()} × {self._sel.height()}"
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 6
        x = s.left() + (s.width() - w) // 2
        y = s.bottom() + 10
        if y + h > self.height() - 2:
            y = s.bottom() - h - 8  # no room below: tuck it inside
        x = min(max(2, x), self.width() - w - 2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 200))
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
