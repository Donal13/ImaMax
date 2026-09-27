"""Tool widgets: the crop options bar and the status-bar zoom control."""

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QSpinBox, QToolButton, QWidget,
)

from .icons import icon
from .utils import screen_pixel_size

# (label, ratio spec, description). Spec: None = free, "original" = the
# image's own ratio, "screen" = the monitor's, (a, b) = a:b in landscape
# orientation. A None entry is a separator.
RATIO_PRESETS = [
    ("Free", None, "Any shape"),
    ("Original", "original", "Keep the image's own proportions"),
    ("Screen", "screen", "Match this monitor, for a wallpaper that fills it exactly"),
    None,
    ("Square", (1, 1), "Profile pictures, social posts"),
    ("5:4", (5, 4), "8×10 prints, older monitors"),
    ("4:3", (4, 3), "Compact cameras, iPad, classic TV"),
    ("3:2", (3, 2), "35 mm photos, 4×6 prints, Surface"),
    ("16:10", (16, 10), "Laptops and WUXGA / WQXGA monitors"),
    ("16:9", (16, 9), "HD / 4K monitors and TVs, video"),
    None,
    ("2:1", (2, 1), "Univisium, wide phone wallpapers"),
    ("21:9", (21, 9), "Ultrawide monitors (2560×1080, 3440×1440)"),
    ("32:9", (32, 9), "Super-ultrawide monitors (5120×1440)"),
    None,
    ("19.5:9", (19.5, 9), "Modern phones — press X for portrait"),
]

_SPEC_ROLE = Qt.ItemDataRole.UserRole
_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1


CROP_HELP = (
    "<b>Crop tool</b><table cellpadding='2'>"
    "<tr><td>Drag an edge or corner</td><td>Resize the selection</td></tr>"
    "<tr><td>Drag inside</td><td>Move the selection</td></tr>"
    "<tr><td>Drag outside</td><td>Draw a new selection</td></tr>"
    "<tr><td>Shift + drag</td><td>Keep the proportions (square when drawing)</td></tr>"
    "<tr><td>Arrow keys</td><td>Nudge 1 px (Shift: 10 px)</td></tr>"
    "<tr><td>X</td><td>Swap portrait / landscape</td></tr>"
    "<tr><td>Space + drag, middle drag</td><td>Pan the view</td></tr>"
    "<tr><td>Ctrl + A</td><td>Reset to the whole image</td></tr>"
    "<tr><td>Enter, double-click</td><td>Crop</td></tr>"
    "<tr><td>Esc</td><td>Cancel</td></tr></table>"
)


class CropBar(QFrame):
    """Tool options shown above the image while cropping."""

    ratio_changed = pyqtSignal(object)       # width/height float, or None for free
    swap_requested = pyqtSignal()            # free ratio: swap the selection's sides
    field_edited = pyqtSignal(QRect, str)    # numeric edit + field name ('x','y','w','h')
    reset_requested = pyqtSignal()
    apply_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CropBar")
        self.setStyleSheet(
            "#CropBar { background: #333333; border-bottom: 1px solid #1c1c1c; }"
            "QPushButton#CropApply { background: #3d6fb8; color: white; border: 1px solid #4b82d0;"
            " border-radius: 3px; padding: 4px 18px; font-weight: bold; }"
            "QPushButton#CropApply:hover { background: #4a80cc; }"
            "QPushButton#CropApply:disabled { background: #3a3a3a; color: #7a7a7a; border-color: #454545; }"
        )
        self._image_size = QSize(1, 1)
        self._portrait = False

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(6)

        title_icon = QLabel()
        title_icon.setPixmap(icon("crop").pixmap(QSize(18, 18)))
        row.addWidget(title_icon)
        title = QLabel("Crop")
        title.setStyleSheet("font-weight: bold;")
        row.addWidget(title)
        row.addSpacing(10)

        row.addWidget(QLabel("Aspect ratio"))
        self._ratio_combo = QComboBox()
        self._ratio_combo.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._ratio_combo.setToolTip("Lock the selection to a shape")
        for preset in RATIO_PRESETS:
            if preset is None:
                self._ratio_combo.insertSeparator(self._ratio_combo.count())
                continue
            label, spec, description = preset
            self._ratio_combo.addItem(label, spec)
            i = self._ratio_combo.count() - 1
            self._ratio_combo.setItemData(i, label, _LABEL_ROLE)
            self._ratio_combo.setItemData(i, description, Qt.ItemDataRole.ToolTipRole)
        self._ratio_combo.currentIndexChanged.connect(self._on_ratio_selected)
        row.addWidget(self._ratio_combo)

        self._swap_btn = QToolButton()
        self._swap_btn.setIcon(icon("swap"))
        self._swap_btn.setAutoRaise(True)
        self._swap_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._swap_btn.setToolTip("Swap portrait / landscape (X)")
        self._swap_btn.clicked.connect(self.toggle_orientation)
        row.addWidget(self._swap_btn)
        row.addSpacing(12)

        self._spins = {}
        for key, label, tip in (("x", "Position", "Left edge, in pixels"),
                                ("y", None, "Top edge, in pixels"),
                                ("w", "Size", "Width, in pixels"),
                                ("h", "×", "Height, in pixels")):
            if label:
                if key == "w":
                    row.addSpacing(8)
                row.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setKeyboardTracking(False)  # apply on Enter / Tab, not per keystroke
            spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            spin.setFixedWidth(62)
            spin.setToolTip(tip)
            spin.valueChanged.connect(lambda _v, k=key: self._on_spin(k))
            row.addWidget(spin)
            self._spins[key] = spin

        reset_btn = QToolButton()
        reset_btn.setIcon(icon("reset"))
        reset_btn.setAutoRaise(True)
        reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset_btn.setToolTip("Reset to the whole image (Ctrl+A)")
        reset_btn.clicked.connect(self.reset_requested)
        row.addSpacing(4)
        row.addWidget(reset_btn)

        help_label = QLabel()
        help_label.setPixmap(icon("info").pixmap(QSize(16, 16)))
        help_label.setToolTip(CROP_HELP)
        row.addWidget(help_label)

        row.addStretch()

        buttons = QDialogButtonBox()
        self._apply_btn = buttons.addButton("Crop", QDialogButtonBox.ButtonRole.AcceptRole)
        self._apply_btn.setObjectName("CropApply")
        self._apply_btn.setIcon(icon("check"))
        self._apply_btn.setToolTip("Crop to the selection (Enter)")
        cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setToolTip("Leave the image as it is (Esc)")
        for b in (self._apply_btn, cancel_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoDefault(False)
        buttons.accepted.connect(self.apply_requested)
        buttons.rejected.connect(self.cancel_requested)
        row.addWidget(buttons)

    # ── Driven by the viewer ─────────────────────────────────────────

    def start(self, image_w, image_h):
        """Prepare for a new crop session on an image of the given size."""
        self._image_size = QSize(image_w, image_h)
        self._portrait = image_h > image_w
        for key, hi in (("x", image_w - 1), ("y", image_h - 1), ("w", image_w), ("h", image_h)):
            spin = self._spins[key]
            spin.blockSignals(True)
            spin.setRange(0 if key in ("x", "y") else 1, max(1, hi))
            spin.blockSignals(False)
        self._refresh_labels()

    def set_selection(self, rect, can_apply):
        for key, value in (("x", rect.x()), ("y", rect.y()),
                           ("w", rect.width()), ("h", rect.height())):
            spin = self._spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._apply_btn.setEnabled(can_apply)

    def current_ratio(self):
        spec = self._ratio_combo.currentData()
        if spec is None:
            return None
        a, b = self._landscape_sides(spec)
        return b / a if self._portrait else a / b

    def _landscape_sides(self, spec):
        """(long, short) sides for a ratio spec."""
        if spec == "original":
            w, h = self._image_size.width(), self._image_size.height()
        elif spec == "screen":
            size = screen_pixel_size(self.screen())
            w, h = size.width(), size.height()
        else:
            return spec
        return max(w, h), max(1, min(w, h))

    def toggle_orientation(self):
        if self._ratio_combo.currentData() is None:
            self.swap_requested.emit()
            return
        self._portrait = not self._portrait
        self._refresh_labels()
        self.ratio_changed.emit(self.current_ratio())

    # ── Internal ─────────────────────────────────────────────────────

    def _refresh_labels(self):
        combo = self._ratio_combo
        for i in range(combo.count()):
            label, spec = combo.itemData(i, _LABEL_ROLE), combo.itemData(i)
            if label is None:
                continue  # separator
            if isinstance(spec, tuple) and spec[0] != spec[1]:
                a, b = spec
                label = f"{b:g}:{a:g}" if self._portrait else f"{a:g}:{b:g}"
            elif spec == "screen":
                size = screen_pixel_size(self.screen())
                label = f"Screen ({size.width()} × {size.height()})"
            combo.setItemText(i, label)
        self._swap_btn.setEnabled(combo.currentData() != (1, 1))

    def _on_ratio_selected(self, _index):
        if self._ratio_combo.currentData() == "screen":
            size = screen_pixel_size(self.screen())
            self._portrait = size.height() > size.width()  # follow the monitor, not the photo
        self._refresh_labels()
        self.ratio_changed.emit(self.current_ratio())

    def _on_spin(self, key):
        rect = QRect(self._spins["x"].value(), self._spins["y"].value(),
                     self._spins["w"].value(), self._spins["h"].value())
        self.field_edited.emit(rect, key)


ZOOM_PRESETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


def format_zoom(zoom):
    pct = zoom * 100
    return f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"


class ZoomControl(QWidget):
    """Status-bar zoom: Fit / 100% buttons, zoom out, editable level, zoom in."""

    zoom_requested = pyqtSignal(float)
    finished = pyqtSignal()   # user is done typing; focus can go back to the image

    def __init__(self, actions, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(1)

        for name in ("zoom_fit", "zoom_100"):
            btn = QToolButton()
            btn.setDefaultAction(actions[name])
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setAutoRaise(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(btn)
        row.addSpacing(6)

        out_btn = QToolButton()
        out_btn.setDefaultAction(actions["zoom_out"])
        out_btn.setAutoRaise(True)
        out_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(out_btn)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.setFixedWidth(78)
        self._combo.setToolTip("Zoom level — pick a preset or type a percentage")
        for z in ZOOM_PRESETS:
            self._combo.addItem(format_zoom(z), z)
        self._combo.activated.connect(self._on_preset)
        self._combo.lineEdit().returnPressed.connect(self._on_typed)
        row.addWidget(self._combo)

        in_btn = QToolButton()
        in_btn.setDefaultAction(actions["zoom_in"])
        in_btn.setAutoRaise(True)
        in_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(in_btn)

    def set_zoom(self, zoom):
        if not self._combo.lineEdit().hasFocus():
            self._combo.setEditText(format_zoom(zoom) if zoom else "")

    def _on_preset(self, index):
        self.zoom_requested.emit(self._combo.itemData(index))
        self.finished.emit()

    def _on_typed(self):
        text = self._combo.currentText().strip().rstrip("%").strip()
        try:
            value = float(text) / 100
        except ValueError:
            value = 0
        if value > 0:
            self.zoom_requested.emit(value)
        self.finished.emit()
