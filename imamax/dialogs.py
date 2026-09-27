"""Dialogs: paste-to-side, resize, live-preview adjustments, keyboard shortcuts."""

from PyQt6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .icons import icon, swatch
from .utils import ADJUSTMENT_DEFAULTS, paste_layout

PREVIEW_THUMB = 480  # longest side of the thumbnails the paste preview draws


class _SideButton(QToolButton):
    double_clicked = pyqtSignal()

    STYLE = (
        "QToolButton { background: #3a3a3a; border: 1px solid #555; border-radius: 4px;"
        " padding: 4px 10px; }"
        "QToolButton:hover { background: #454545; }"
        "QToolButton:checked { background: #3d6fb8; border-color: #5a8fd8; color: white;"
        " font-weight: bold; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self.STYLE)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()


class _PastePreview(QWidget):
    """Scaled-down picture of the paste result, with the pasted part outlined."""

    def __init__(self, current, pasted, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 200)
        self._current_size = current.size()
        self._pasted_size = pasted.size()
        self._current = current.scaled(PREVIEW_THUMB, PREVIEW_THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        self._pasted = pasted.scaled(PREVIEW_THUMB, PREVIEW_THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._layout = None
        self._bg = QColor(255, 255, 255)

    def set_options(self, side, align, gap, scale_to_match, bg):
        self._layout = paste_layout(self._current_size, self._pasted_size, side, align, gap, scale_to_match)
        self._bg = bg
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        if self._layout is None:
            return
        size, cur, pst = self._layout
        k = min((self.width() - 16) / size.width(), (self.height() - 16) / size.height())
        ox = (self.width() - size.width() * k) / 2
        oy = (self.height() - size.height() * k) / 2

        def scaled(r):
            return QRectF(ox + r.x() * k, oy + r.y() * k, r.width() * k, r.height() * k)

        canvas = scaled(QRect(0, 0, size.width(), size.height()))
        if self._bg.alpha() < 255:  # checkerboard shows transparency
            cell = 6
            p.save()
            p.setClipRect(canvas)
            y = canvas.top()
            while y < canvas.bottom():
                x = canvas.left()
                while x < canvas.right():
                    light = (int((x - canvas.left()) / cell) + int((y - canvas.top()) / cell)) % 2 == 0
                    p.fillRect(QRectF(x, y, cell, cell), QColor(95, 95, 95) if light else QColor(65, 65, 65))
                    x += cell
                y += cell
            p.restore()
        p.fillRect(canvas, self._bg)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(scaled(cur), self._current, QRectF(self._current.rect()))
        p.drawPixmap(scaled(pst), self._pasted, QRectF(self._pasted.rect()))
        p.setPen(QPen(QColor(74, 158, 255), 2, Qt.PenStyle.DashLine))
        p.drawRect(scaled(pst).adjusted(1, 1, -1, -1))
        p.end()


class PasteDialog(QDialog):
    """Choose where and how to paste the clipboard image, with a live preview.

    Settings are remembered between uses, so repeating the last paste is just
    Ctrl+Shift+V, Enter.
    """

    SIDES = ("top", "left", "right", "bottom")
    TO_WALLPAPER = 2  # exec() result: continue in the Wallpaper Maker

    def __init__(self, current, pasted, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paste to Side")
        self._settings = settings
        self._current_size = current.size()
        self._pasted_size = pasted.size()

        layout = QVBoxLayout(self)
        intro = QLabel(f"Clipboard image: {pasted.width()} × {pasted.height()} px. "
                       "Choose a side (or press an arrow key):")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setSpacing(6)
        self._side_group = QButtonGroup(self)
        self._side_group.setExclusive(True)
        positions = {"top": (0, 1), "left": (1, 0), "right": (1, 2), "bottom": (2, 1)}
        for side in self.SIDES:
            btn = _SideButton()
            btn.setText(side.capitalize())
            btn.setIcon(icon(f"arrow_{'up' if side == 'top' else 'down' if side == 'bottom' else side}"))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setCheckable(True)
            btn.setMinimumSize(96, 34)
            btn.setToolTip("Double-click to paste straight away")
            btn.double_clicked.connect(self.accept)
            self._side_group.addButton(btn)
            btn.setProperty("side", side)
            r, c = positions[side]
            grid.addWidget(btn, r, c, alignment=Qt.AlignmentFlag.AlignCenter)
        self._preview = _PastePreview(current, pasted)
        grid.addWidget(self._preview, 1, 1)
        layout.addLayout(grid)

        options = QGroupBox("Options")
        form = QFormLayout(options)
        self._scale_check = QCheckBox()
        form.addRow(self._scale_check)
        self._align_combo = QComboBox()
        for data in ("start", "center", "end"):
            self._align_combo.addItem("", data)
        self._align_combo.setToolTip("Where the smaller image sits when the sizes differ")
        form.addRow("Alignment:", self._align_combo)
        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(0, 1000)
        self._gap_spin.setSuffix(" px")
        self._gap_spin.setToolTip("Space between the two images, filled with the fill colour")
        form.addRow("Spacing:", self._gap_spin)
        self._color_btn = QPushButton()
        self._color_btn.setToolTip("Colour for the spacing and any uncovered area (supports transparency)")
        self._color_btn.clicked.connect(self._pick_color)
        form.addRow("Fill color:", self._color_btn)
        self._result_label = QLabel()
        form.addRow("Result:", self._result_label)
        layout.addWidget(options)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Paste")
        ok.setIcon(icon("paste_side"))
        maker = buttons.addButton("Wallpaper Maker…", QDialogButtonBox.ButtonRole.ActionRole)
        maker.setIcon(icon("wallpaper"))
        maker.setAutoDefault(False)
        maker.setToolTip("Continue in the Wallpaper Maker, where you can add more images")
        maker.clicked.connect(self._to_wallpaper)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Restore the last-used options
        s = settings
        self._bg = QColor(s.value("paste/color", "#ffffffff"))
        if not self._bg.isValid():
            self._bg = QColor(255, 255, 255)
        self._scale_check.setChecked(s.value("paste/scale", True, type=bool))
        self._align_combo.setCurrentIndex(max(0, self._align_combo.findData(s.value("paste/align", "center"))))
        self._gap_spin.setValue(int(s.value("paste/gap", 0)))
        side = s.value("paste/side", "right")
        for btn in self._side_group.buttons():
            btn.setChecked(btn.property("side") == side)
        if self._side_group.checkedButton() is None:
            self._side_group.buttons()[2].setChecked(True)

        self._side_group.buttonToggled.connect(lambda *_: self._refresh())
        self._scale_check.toggled.connect(self._refresh)
        self._align_combo.currentIndexChanged.connect(self._refresh)
        self._gap_spin.valueChanged.connect(self._refresh)
        self._refresh()
        self._side_group.checkedButton().setFocus()

    def side(self):
        return self._side_group.checkedButton().property("side")

    def _refresh(self):
        side = self.side()
        horizontal = side in ("left", "right")
        self._scale_check.setText("Scale pasted image to match the height" if horizontal
                                  else "Scale pasted image to match the width")
        names = ("Top", "Center", "Bottom") if horizontal else ("Left", "Center", "Right")
        for i, name in enumerate(names):
            self._align_combo.setItemText(i, name)
        self._color_btn.setIcon(swatch(self._bg))
        self._color_btn.setText(self._bg.name(QColor.NameFormat.HexArgb if self._bg.alpha() < 255
                                              else QColor.NameFormat.HexRgb).upper())
        opts = self.get_options()
        size, _, _ = paste_layout(self._current_size, self._pasted_size, side, opts["align"],
                                  opts["gap"], opts["scale_to_match"])
        self._result_label.setText(f"{size.width()} × {size.height()} px")
        self._preview.set_options(side, opts["align"], opts["gap"], opts["scale_to_match"], self._bg)

    def _pick_color(self):
        color = QColorDialog.getColor(self._bg, self, "Fill Color",
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self._bg = color
            self._refresh()

    def keyPressEvent(self, event):
        sides = {Qt.Key.Key_Up: "top", Qt.Key.Key_Down: "bottom",
                 Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right"}
        focus = self.focusWidget()
        typing = isinstance(focus, (QAbstractSpinBox, QComboBox, QLineEdit))
        if event.key() in sides and not typing:
            for btn in self._side_group.buttons():
                if btn.property("side") == sides[event.key()]:
                    btn.setChecked(True)
                    btn.setFocus()
            return
        super().keyPressEvent(event)

    def _to_wallpaper(self):
        self._save_settings()
        self.done(self.TO_WALLPAPER)

    def accept(self):
        self._save_settings()
        super().accept()

    def _save_settings(self):
        s = self._settings
        opts = self.get_options()
        s.setValue("paste/side", opts["side"])
        s.setValue("paste/scale", opts["scale_to_match"])
        s.setValue("paste/align", opts["align"])
        s.setValue("paste/gap", opts["gap"])
        s.setValue("paste/color", self._bg.name(QColor.NameFormat.HexArgb))

    def get_options(self):
        return {
            "side": self.side(),
            "scale_to_match": self._scale_check.isChecked(),
            "align": self._align_combo.currentData(),
            "gap": self._gap_spin.value(),
            "bg_color": (self._bg.red(), self._bg.green(), self._bg.blue(), self._bg.alpha()),
        }


class ResizeDialog(QDialog):
    """Resize by pixels or percentage, with a choice of resampling."""

    METHODS = (
        ("lanczos", "High quality (Lanczos)"),
        ("bilinear", "Smooth (bilinear)"),
        ("nearest", "Pixelated (nearest neighbor)"),
    )

    def __init__(self, current_w, current_h, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resize Image")
        self.setMinimumWidth(380)
        self._settings = settings
        self._orig_w = current_w
        self._orig_h = current_h
        self._aspect = current_w / current_h if current_h > 0 else 1.0
        self._updating = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Current size: {current_w} × {current_h} px"))

        form = QFormLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 30000)
        self.width_spin.setValue(current_w)
        self.width_spin.setSuffix(" px")
        form.addRow("Width:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 30000)
        self.height_spin.setValue(current_h)
        self.height_spin.setSuffix(" px")
        form.addRow("Height:", self.height_spin)

        self.constrain_check = QCheckBox("Keep aspect ratio")
        self.constrain_check.setChecked(True)
        form.addRow("", self.constrain_check)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 2000.0)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setValue(100.0)
        form.addRow("Scale:", self.scale_spin)

        presets = QHBoxLayout()
        presets.setSpacing(4)
        for label, factor in (("25%", 0.25), ("50%", 0.5), ("75%", 0.75), ("150%", 1.5), ("200%", 2.0)):
            btn = QPushButton(label)
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _checked, f=factor: self.scale_spin.setValue(f * 100))
            presets.addWidget(btn)
        form.addRow("", presets)

        self.method_combo = QComboBox()
        for key, text in self.METHODS:
            self.method_combo.addItem(text, key)
        self.method_combo.setCurrentIndex(
            max(0, self.method_combo.findData(settings.value("resize/method", "lanczos"))))
        self.method_combo.setToolTip("Nearest neighbor keeps hard pixel edges — best for "
                                     "screenshots and pixel art enlarged by whole numbers")
        form.addRow("Resampling:", self.method_combo)
        layout.addLayout(form)

        self._preview_label = QLabel()
        self._preview_label.setStyleSheet("color: #aaa;")
        layout.addWidget(self._preview_label)
        self._update_preview()

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)
        self.scale_spin.valueChanged.connect(self._on_scale_changed)
        self.constrain_check.toggled.connect(self._on_constrain_toggled)

    def _set_values(self, w=None, h=None, pct=None):
        self._updating = True
        if w is not None:
            self.width_spin.setValue(w)
        if h is not None:
            self.height_spin.setValue(h)
        if pct is not None:
            self.scale_spin.setValue(pct)
        self._updating = False
        self._update_preview()

    def _on_width_changed(self, val):
        if self._updating:
            return
        pct = val / self._orig_w * 100
        if self.constrain_check.isChecked():
            self._set_values(h=max(1, round(val / self._aspect)), pct=pct)
        else:
            self._set_values(pct=pct)

    def _on_height_changed(self, val):
        if self._updating:
            return
        pct = val / self._orig_h * 100
        if self.constrain_check.isChecked():
            self._set_values(w=max(1, round(val * self._aspect)), pct=pct)
        else:
            self._set_values(pct=pct)

    def _on_scale_changed(self, pct):
        if self._updating:
            return
        self._set_values(w=max(1, round(self._orig_w * pct / 100)),
                         h=max(1, round(self._orig_h * pct / 100)))

    def _on_constrain_toggled(self, on):
        if on:
            self._on_width_changed(self.width_spin.value())

    def _update_preview(self):
        w, h = self.width_spin.value(), self.height_spin.value()
        self._preview_label.setText(
            f"New size: {w} × {h} px  ({w / self._orig_w * 100:.0f}% × {h / self._orig_h * 100:.0f}%,"
            f" {w * h / 1e6:.1f} MP)")

    def accept(self):
        self._settings.setValue("resize/method", self.get_method())
        super().accept()

    def get_size(self):
        return self.width_spin.value(), self.height_spin.value()

    def get_method(self):
        return self.method_combo.currentData()


class AdjustmentsDialog(QDialog):
    """Brightness / contrast / saturation / sharpness / blur with live preview."""

    preview_requested = pyqtSignal(dict)

    # (key, label, slider min, slider max, default, to-value divisor)
    SLIDERS = [
        ("brightness", "Brightness", 10, 300, 100, 100.0),
        ("contrast", "Contrast", 10, 300, 100, 100.0),
        ("saturation", "Saturation", 0, 300, 100, 100.0),
        ("sharpness", "Sharpness", 0, 300, 100, 100.0),
        ("blur", "Blur", 0, 100, 0, 10.0),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjustments")
        self.setMinimumWidth(440)

        self._sliders = {}
        self._value_labels = {}

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._emit_preview)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        for key, text, lo, hi, default, _div in self.SLIDERS:
            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
            slider.setToolTip("Double-click to reset")
            slider.installEventFilter(self)
            slider.valueChanged.connect(lambda _v, k=key: self._on_slider_changed(k))
            value_label = QLabel()
            value_label.setMinimumWidth(52)
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value_label.setStyleSheet("font-family: monospace;")
            row.addWidget(slider)
            row.addWidget(value_label)
            form.addRow(text + ":", row)
            self._sliders[key] = slider
            self._value_labels[key] = value_label
            self._update_value_label(key)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._preview_check = QCheckBox("Preview")
        self._preview_check.setChecked(True)
        self._preview_check.setToolTip("Untick to compare with the original")
        self._preview_check.toggled.connect(self._emit_preview)
        btn_row.addWidget(self._preview_check)
        reset_btn = QPushButton("Reset")
        reset_btn.setAutoDefault(False)
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        btn_row.addWidget(btn_box)
        layout.addLayout(btn_row)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and isinstance(obj, QSlider):
            for key, _text, _lo, _hi, default, _div in self.SLIDERS:
                if self._sliders[key] is obj:
                    obj.setValue(default)
            return True
        return super().eventFilter(obj, event)

    def _emit_preview(self):
        self.preview_requested.emit(self.get_values() if self._preview_check.isChecked()
                                    else dict(ADJUSTMENT_DEFAULTS))

    def _on_slider_changed(self, key):
        self._update_value_label(key)
        self._debounce.start()

    def _update_value_label(self, key):
        slider = self._sliders[key]
        if key == "blur":
            self._value_labels[key].setText(f"{slider.value() / 10.0:.1f} px")
        else:
            self._value_labels[key].setText(f"{slider.value()}%")

    def _on_reset(self):
        for key, _text, _lo, _hi, default, _div in self.SLIDERS:
            self._sliders[key].setValue(default)

    def get_values(self):
        values = dict(ADJUSTMENT_DEFAULTS)
        for key, _text, _lo, _hi, _default, div in self.SLIDERS:
            values[key] = self._sliders[key].value() / div
        return values


class ShortcutsDialog(QDialog):
    """Searchable list of every shortcut, generated from the menus."""

    def __init__(self, sections, parent=None):
        """sections: [(title, [(description, shortcut text), ...]), ...]"""
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(520, 600)
        layout = QVBoxLayout(self)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Search shortcuts…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Action", "Shortcut"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setStretchLastSection(False)
        for title, rows in sections:
            group = QTreeWidgetItem([title])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self._tree.addTopLevelItem(group)
            for desc, keys in rows:
                QTreeWidgetItem(group, [desc, keys])
            group.setExpanded(True)
        layout.addWidget(self._tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_filter(self, text):
        text = text.lower().strip()
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            any_visible = False
            for j in range(group.childCount()):
                child = group.child(j)
                match = not text or text in child.text(0).lower() or text in child.text(1).lower()
                child.setHidden(not match)
                any_visible |= match
            group.setHidden(not any_visible)


def action_label(action):
    """Menu text without mnemonics or the trailing ellipsis."""
    return action.text().replace("&", "").rstrip("…").strip()


def shortcut_text(action):
    return ",  ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts())
