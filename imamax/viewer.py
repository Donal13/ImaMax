"""Main ImaMax window."""

import os
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QRect, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction, QColor, QCursor, QDesktopServices, QGuiApplication, QKeySequence,
    QMouseEvent, QMovie, QPixmap, QTransform,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QDialog, QFileDialog, QInputDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QScrollArea, QSizePolicy, QStatusBar,
    QToolBar, QVBoxLayout, QWidget,
)

from . import __version__
from .canvas import ImageCanvas
from .dialogs import (
    AdjustmentsDialog, PasteDialog, ResizeDialog, ShortcutsDialog, action_label, shortcut_text,
)
from .icons import icon
from .utils import (
    LOSSY_FORMATS, MAX_ZOOM, MIN_ZOOM, SUPPORTED_FORMATS, apply_adjustments,
    compose_side_by_side, is_default_adjustments, load_image, natural_key,
    pil_to_qpixmap, qpixmap_to_pil, resize_pixmap, step_zoom,
)
from .widgets import CropBar, ZoomControl

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

UNDO_LIMIT = 30
MAX_RECENT_FILES = 10
PREVIEW_MAX_DIM = 1600  # adjustments preview works on a downscaled copy
VIEW_BG = QColor(43, 43, 43)
FULLSCREEN_BG = QColor(0, 0, 0)
AUTOSCROLL_MAX = 40     # px per tick when dragging a crop past the view edge

# Enabled only when an image is loaded
IMAGE_ACTIONS = (
    "save", "save_as", "properties", "copy", "zoom_in", "zoom_out", "zoom_fit",
    "zoom_100", "crop", "resize_img", "rot_ccw", "rot_cw", "flip_h", "flip_v",
    "adjust", "grayscale",
)
# Disabled while cropping: they would change the image under the selection
# (and the arrow / Space / Home / End keys are needed by the crop tool)
CROP_BLOCKED = (
    "prev", "next", "first", "last", "undo", "redo", "paste_replace", "paste_side",
    "rot_ccw", "rot_cw", "flip_h", "flip_v", "adjust", "grayscale", "resize_img",
    "delete_file",
)

EXTRA_SHORTCUTS = [
    ("Crop tool", [
        ("Resize the selection", "Drag an edge or corner"),
        ("Move the selection", "Drag inside it"),
        ("Keep proportions (square when drawing)", "Shift + drag"),
        ("Nudge the selection", "Arrow keys (Shift: 10 px)"),
        ("Swap portrait / landscape", "X"),
        ("Reset to the whole image", "Ctrl+A"),
        ("Pan the view", "Space + drag, middle drag"),
        ("Crop", "Enter, double-click inside"),
        ("Cancel", "Esc"),
    ]),
    ("Mouse", [
        ("Zoom at the cursor", "Ctrl + wheel"),
        ("Pan (when zoomed in)", "Drag, middle drag"),
        ("Toggle fit / 100%", "Double-click"),
        ("Previous / next image", "Mouse back / forward buttons"),
        ("More actions", "Right-click"),
        ("Open an image", "Drop a file, or double-click the empty window"),
    ]),
]


class ImageViewer(QMainWindow):
    """Main image viewer window."""

    def __init__(self, filepath=None):
        super().__init__()
        self.setMinimumSize(800, 600)
        self.setAcceptDrops(True)

        self._settings = QSettings("ImaMax", "ImaMax")
        self._current_file = None
        self._file_size = None
        self._pixmap = None       # full-resolution image (first frame if animated)
        self._movie = None        # QMovie when displaying an animation
        self._zoom_factor = 1.0
        self._fit_mode = True
        self._saved_key = None    # cacheKey of the pixmap as last loaded/saved
        self._dir_files = []
        self._dir_index = -1
        self._undo_stack = []
        self._redo_stack = []
        self._crop_mode = False
        self._adjust_preview_pil = None
        self._wheel_accum = 0
        self._forwarding_mouse = False
        self._chrome_state = None  # bar visibility saved while full screen

        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(30)
        self._autoscroll.timeout.connect(self._on_autoscroll)

        self._create_actions()
        self._setup_ui()
        self._create_toolbar()
        self._create_menus()
        self._restore_view_settings()
        self._update_actions()
        self._update_title()

        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 800)

        if filepath and os.path.isfile(filepath):
            self.open_file(filepath)

    # ── UI Setup ─────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self._crop_bar = CropBar()
        self._crop_bar.setVisible(False)
        central_layout.addWidget(self._crop_bar)

        # The canvas is sized manually so zooming past the viewport
        # produces scrollbars instead of clipping.
        self._scroll_area = QScrollArea()
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._scroll_area.customContextMenuRequested.connect(self._show_context_menu)
        self._scroll_area.installEventFilter(self)
        self._scroll_area.viewport().installEventFilter(self)

        self._canvas = ImageCanvas()
        self._canvas.set_placeholder("Open an image",
                                     "Double-click, press Ctrl+O, or drop a file here")
        self._canvas.crop_changed.connect(self._on_crop_selection_changed)
        self._canvas.crop_drag_active.connect(self._on_crop_drag_active)
        self._canvas.crop_apply_requested.connect(self._on_crop_apply)
        self._canvas.hover_changed.connect(self._on_hover)
        self._canvas.pan_delta.connect(self._on_pan)
        self._canvas.double_clicked.connect(self._on_double_click)
        self._scroll_area.setWidget(self._canvas)
        self._set_view_background(VIEW_BG)
        central_layout.addWidget(self._scroll_area)
        self.setCentralWidget(central)

        bar = self._crop_bar
        bar.ratio_changed.connect(self._canvas.set_crop_ratio)
        bar.swap_requested.connect(self._canvas.swap_crop)
        bar.field_edited.connect(self._canvas.set_crop_field)
        bar.reset_requested.connect(self._canvas.reset_crop)
        bar.apply_requested.connect(self._on_crop_apply)
        bar.cancel_requested.connect(self._on_crop_cancel)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._pos_label = QLabel()
        self._pos_label.setMinimumWidth(90)
        self._pos_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pos_label.setToolTip("Pixel under the cursor")
        self._pos_label.setStyleSheet("color: #999;")
        self._status_bar.addPermanentWidget(self._pos_label)
        self._info_label = QLabel("No image loaded")
        self._status_bar.addPermanentWidget(self._info_label)
        self._zoom_control = ZoomControl(self._actions)
        self._zoom_control.zoom_requested.connect(self._apply_zoom)
        self._zoom_control.finished.connect(self._on_zoom_edit_finished)
        self._status_bar.addPermanentWidget(self._zoom_control)

    def _set_view_background(self, color):
        self._scroll_area.setStyleSheet(f"QScrollArea {{ background-color: {color.name()}; }}")
        self._canvas.set_background(color)

    def _create_actions(self):
        self._actions = {}

        def act(name, text, shortcut=None, slot=None, tip=None, icon_name=None,
                checkable=False, icon_text=None):
            a = QAction(text, self)
            if icon_name:
                a.setIcon(icon(icon_name))
            if icon_text:
                a.setIconText(icon_text)
            a.setCheckable(checkable)
            if shortcut:
                keys = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
                a.setShortcuts([QKeySequence(k) for k in keys])
            if slot:
                a.triggered.connect(slot)
            if tip:
                a.setStatusTip(tip)
            label = action_label(a)
            keys_text = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            a.setToolTip(f"{label} ({keys_text})" if keys_text else label)
            # Registering on the window keeps shortcuts alive when the menu
            # bar and toolbar are hidden (full screen)
            self.addAction(a)
            self._actions[name] = a
            return a

        # File
        act("open", "&Open…", "Ctrl+O", self._on_open, "Open an image file", "open")
        act("save", "&Save", "Ctrl+S", self._on_save, "Save the image", "save")
        act("save_as", "Save &As…", "Ctrl+Shift+S", self._on_save_as, "Save under a new name or format")
        act("open_folder", "Open Containing &Folder", None, self._on_open_folder,
            "Show the image's folder in the file manager", "folder")
        act("properties", "P&roperties…", ["Alt+Return", "I"], self._on_info,
            "Dimensions, file size and camera details", "info")
        act("delete_file", "Move to &Trash", "Delete", self._on_delete, "Move the file to the trash", "trash")
        act("quit", "&Quit", "Ctrl+Q", self.close, icon_name="quit")

        # Edit
        act("undo", "&Undo", "Ctrl+Z", self._on_undo, icon_name="undo")
        act("redo", "&Redo", ["Ctrl+Shift+Z", "Ctrl+Y"], self._on_redo, icon_name="redo")
        act("copy", "&Copy", "Ctrl+C", self._on_copy,
            "Copy the image (or the crop selection) to the clipboard", "copy")
        act("paste_replace", "&Paste", "Ctrl+V", self._on_paste_replace,
            "Replace the image with the clipboard image", "paste")
        act("paste_side", "Paste to &Side…", "Ctrl+Shift+V", self._on_paste_side,
            "Attach the clipboard image to a side of this image", "paste_side")

        # View
        act("zoom_in", "Zoom &In", ["Ctrl++", "Ctrl+=", "+"], self._on_zoom_in, icon_name="zoom_in")
        act("zoom_out", "Zoom &Out", ["Ctrl+-", "-"], self._on_zoom_out, icon_name="zoom_out")
        act("zoom_100", "&Actual Size", "Ctrl+1", self._on_zoom_100, "Show at 100% (one image pixel per screen pixel)",
            "zoom_100", icon_text="100%")
        act("zoom_fit", "&Fit to Window", "Ctrl+0", self._on_zoom_fit, "Show the whole image",
            "zoom_fit", checkable=True, icon_text="Fit")
        act("enlarge_small", "&Enlarge Small Images to Fit", None, self._on_enlarge_toggled,
            "Let Fit to Window zoom past 100% for images smaller than the window", checkable=True)
        act("fullscreen", "F&ull Screen", ["F11", "Ctrl+Shift+F"], self._on_fullscreen,
            "Show only the image", "fullscreen", checkable=True)
        act("show_statusbar", "Show Status &Bar", None, self._on_statusbar_toggled, checkable=True)

        # Image
        act("crop", "&Crop", "C", self._on_crop_toggle, "Select an area to keep", "crop", checkable=True)
        act("resize_img", "Re&size…", "Ctrl+Alt+I", self._on_resize, "Change the pixel dimensions", "resize")
        act("rot_ccw", "Rotate &Left", ["Ctrl+L", "Ctrl+Shift+R"], self._on_rotate_ccw,
            "Rotate 90° counter-clockwise", "rotate_left")
        act("rot_cw", "Rotate &Right", "Ctrl+R", self._on_rotate_cw, "Rotate 90° clockwise", "rotate_right")
        act("flip_h", "Flip &Horizontal", "H", self._on_flip_h, "Mirror left to right", "flip_h")
        act("flip_v", "Flip &Vertical", "V", self._on_flip_v, "Mirror top to bottom", "flip_v")
        act("adjust", "&Adjustments…", "A", self._on_adjustments,
            "Brightness, contrast, saturation, sharpness, blur", "adjust")
        act("grayscale", "&Grayscale", None, self._on_grayscale, "Remove all color", "grayscale")

        # Go
        act("prev", "&Previous Image", ["Left", "Backspace"], self._on_prev, "Previous image in the folder", "prev")
        act("next", "&Next Image", ["Right", "Space"], self._on_next, "Next image in the folder", "next")
        act("first", "&First Image", "Home", self._on_first, "First image in the folder", "first")
        act("last", "&Last Image", "End", self._on_last, "Last image in the folder", "last")

        # Help
        act("shortcuts", "&Keyboard Shortcuts", "F1", self._on_shortcuts, icon_name="keyboard")
        act("about", "&About ImaMax", None, self._on_about, icon_name="info")
        act("about_qt", "About &Qt", None, QApplication.aboutQt)

    def _create_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(tb)

        groups = (("open", "save"), ("undo", "redo"), ("prev", "next"),
                  ("rot_ccw", "rot_cw"), ("crop", "resize_img", "adjust"), ("copy", "paste_side"))
        for i, group in enumerate(groups):
            if i:
                tb.addSeparator()
            for k in group:
                tb.addAction(self._actions[k])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        for k in ("properties", "delete_file", "fullscreen"):
            tb.addAction(self._actions[k])

        # The signature feature gets a text label so it's easy to find
        tb.widgetForAction(self._actions["paste_side"]).setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle = tb.toggleViewAction()
        toggle.setText("Show &Toolbar")
        toggle.toggled.connect(self._on_toolbar_toggled)
        self._actions["show_toolbar"] = toggle
        self._toolbar = tb

    def _create_menus(self):
        mb = self.menuBar()
        self._menus = []

        def menu(title, keys):
            m = mb.addMenu(title)
            for k in keys:
                if k is None:
                    m.addSeparator()
                elif k == "recent":
                    self._recent_menu = m.addMenu("Open &Recent")
                    self._rebuild_recent_menu()
                else:
                    m.addAction(self._actions[k])
            self._menus.append(m)

        menu("&File", ["open", "recent", None, "save", "save_as", None,
                       "open_folder", "properties", None, "delete_file", None, "quit"])
        menu("&Edit", ["undo", "redo", None, "copy", "paste_replace", "paste_side"])
        menu("&View", ["zoom_in", "zoom_out", "zoom_100", "zoom_fit", None, "enlarge_small", None,
                       "fullscreen", None, "show_toolbar", "show_statusbar"])
        menu("&Image", ["crop", "resize_img", None, "rot_ccw", "rot_cw", "flip_h", "flip_v", None,
                        "adjust", "grayscale"])
        menu("&Go", ["prev", "next", None, "first", "last"])
        menu("&Help", ["shortcuts", None, "about", "about_qt"])

    def _restore_view_settings(self):
        s = self._settings
        self._toolbar.setVisible(s.value("view/toolbar", True, type=bool))
        status_on = s.value("view/statusbar", True, type=bool)
        self._status_bar.setVisible(status_on)
        self._actions["show_statusbar"].setChecked(status_on)
        self._actions["enlarge_small"].setChecked(s.value("view/enlarge_small", False, type=bool))

    def _update_actions(self):
        has_img = self._pixmap is not None
        for k in IMAGE_ACTIONS:
            self._actions[k].setEnabled(has_img)
        for k in ("open_folder", "delete_file"):
            self._actions[k].setEnabled(self._current_file is not None)
        self._actions["paste_replace"].setEnabled(True)
        self._actions["paste_side"].setEnabled(True)
        self._actions["undo"].setEnabled(bool(self._undo_stack))
        self._actions["redo"].setEnabled(bool(self._redo_stack))
        has_nav = len(self._dir_files) > 1
        for k in ("prev", "next", "first", "last"):
            self._actions[k].setEnabled(has_nav)
        if self._crop_mode:
            for k in CROP_BLOCKED:
                self._actions[k].setEnabled(False)
        self._actions["crop"].setChecked(self._crop_mode)
        self._actions["zoom_fit"].setChecked(has_img and self._fit_mode)
        self._zoom_control.setEnabled(has_img)

    def _update_title(self):
        if self._pixmap is None:
            self.setWindowTitle("ImaMax")
            self.setWindowModified(False)
            return
        name = os.path.basename(self._current_file) if self._current_file else "Untitled"
        self.setWindowTitle(f"{name}[*] — ImaMax")
        self.setWindowModified(self._is_modified())

    def _is_modified(self):
        """Unsaved changes? Undoing back to the saved image counts as clean."""
        return self._pixmap is not None and self._pixmap.cacheKey() != self._saved_key

    def _update_status(self):
        if self._pixmap is None:
            self._info_label.setText("No image loaded")
            self._zoom_control.set_zoom(None)
            return
        parts = []
        if self._current_file and self._dir_files:
            parts.append(f"{self._dir_index + 1} / {len(self._dir_files)}")
        parts.append(f"{self._pixmap.width()} × {self._pixmap.height()} px")
        if self._file_size is not None and not self._is_modified():
            size = self._file_size
            parts.append(f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB")
        self._info_label.setText("    ".join(parts))
        self._zoom_control.set_zoom(self._zoom_factor)

    # ── Recent files ─────────────────────────────────────────────────

    def _recent_files(self):
        files = self._settings.value("recent_files", []) or []
        if isinstance(files, str):
            files = [files]
        return list(files)

    def _add_recent(self, filepath):
        files = self._recent_files()
        if filepath in files:
            files.remove(filepath)
        files.insert(0, filepath)
        self._settings.setValue("recent_files", files[:MAX_RECENT_FILES])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        files = [f for f in self._recent_files() if os.path.isfile(f)]
        for f in files:
            action = QAction(os.path.basename(f), self)
            action.setStatusTip(f)
            action.triggered.connect(lambda checked, p=f: self.open_file(p))
            self._recent_menu.addAction(action)
        self._recent_menu.setEnabled(bool(files))
        if files:
            self._recent_menu.addSeparator()
            clear = QAction("Clear Recent", self)
            clear.triggered.connect(self._clear_recent)
            self._recent_menu.addAction(clear)

    def _clear_recent(self):
        self._settings.setValue("recent_files", [])
        self._rebuild_recent_menu()

    # ── File Operations ──────────────────────────────────────────────

    def open_file(self, filepath, confirm=True):
        if confirm and not self._confirm_discard():
            return
        filepath = os.path.abspath(filepath)
        pixmap, is_animated = load_image(filepath)
        if pixmap.isNull():
            QMessageBox.warning(self, "Error", f"Could not load image:\n{filepath}")
            return
        self._current_file = filepath
        try:
            self._file_size = os.path.getsize(filepath)
        except OSError:
            self._file_size = None
        self._set_image(pixmap, clear_undo=True)
        if is_animated:
            movie = QMovie(filepath)
            if movie.isValid():
                self._movie = movie
                movie.frameChanged.connect(self._on_movie_frame)
                movie.start()
        self._scan_directory(filepath)
        self._add_recent(filepath)
        self._settings.setValue("last_dir", os.path.dirname(filepath))
        self._update_title()
        self._update_status()

    def _scan_directory(self, filepath):
        d = os.path.dirname(filepath)
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        files = sorted(
            [os.path.join(d, f) for f in names
             if os.path.splitext(f)[1].lower() in SUPPORTED_FORMATS
             and os.path.isfile(os.path.join(d, f))],
            key=natural_key,
        )
        self._dir_files = files
        try:
            self._dir_index = files.index(os.path.abspath(filepath))
        except ValueError:
            self._dir_index = 0
        self._update_actions()

    def _confirm_discard(self):
        """Return True if it is OK to replace the current image."""
        if not self._is_modified():
            return True
        ret = QMessageBox.warning(
            self, "Unsaved Changes",
            "The current image has unsaved changes.\nSave them first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if ret == QMessageBox.StandardButton.Save:
            return self._do_save()
        return ret == QMessageBox.StandardButton.Discard

    def _on_movie_frame(self, _frame):
        if self._movie is not None:
            self._canvas.set_frame(self._movie.currentPixmap())

    def _stop_movie(self):
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
            if self._pixmap is not None:
                self._canvas.set_frame(self._pixmap)

    def _set_image(self, pixmap, clear_undo=False, record_undo=True):
        if self._crop_mode:
            self._exit_crop_mode()
        old = self._pixmap
        is_edit = record_undo and not clear_undo and old is not None
        if is_edit:
            self._undo_stack.append(old)
            if len(self._undo_stack) > UNDO_LIMIT:
                self._undo_stack.pop(0)
            self._redo_stack.clear()
        if clear_undo:
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._saved_key = pixmap.cacheKey()

        if self._movie is not None:
            self._stop_movie()
            if is_edit:
                self._status_bar.showMessage(
                    "Animation flattened to a single frame for editing.", 4000)

        # Same-size edits (adjustments, flips…) keep the current zoom and scroll
        self._show_pixmap(pixmap, keep_view=not clear_undo and old is not None
                          and old.size() == pixmap.size())

    def _show_pixmap(self, pixmap, keep_view=False):
        self._pixmap = pixmap
        if not keep_view:
            self._fit_mode = True
        self._canvas.set_pixmap(pixmap)
        self._display_image()
        self._update_actions()
        self._update_title()

    def _show_placeholder(self):
        self._canvas.set_pixmap(None)
        self._canvas.resize(self._scroll_area.viewport().size())

    def _fit_zoom(self):
        vp = self._scroll_area.viewport().size()
        margin = 2 * self._canvas.pad() + 2
        z = min((vp.width() - margin) / self._pixmap.width(),
                (vp.height() - margin) / self._pixmap.height())
        if not self._actions["enlarge_small"].isChecked():
            z = min(z, 1.0)
        return max(z, 0.001)

    def _display_image(self):
        if self._pixmap is None:
            self._show_placeholder()
            return
        policy = (Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self._fit_mode
                  else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setHorizontalScrollBarPolicy(policy)
        self._scroll_area.setVerticalScrollBarPolicy(policy)
        if self._fit_mode:
            self._zoom_factor = self._fit_zoom()
        self._canvas.set_zoom(self._zoom_factor)
        self._update_pannable()
        self._update_status()
        self._actions["zoom_fit"].setChecked(self._fit_mode)

    def _update_pannable(self):
        vp = self._scroll_area.viewport()
        self._canvas.set_pannable(
            self._pixmap is not None
            and (self._canvas.width() > vp.width() or self._canvas.height() > vp.height()))

    # ── Slots: file ──────────────────────────────────────────────────

    def _on_open(self):
        if self._current_file:
            start_dir = os.path.dirname(self._current_file)
        else:
            start_dir = self._settings.value("last_dir") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", start_dir,
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.ico *.ppm *.svg);;All Files (*)"
        )
        if path:
            self.open_file(path)

    def _save_quality_for(self, path):
        """Encoder quality: stored preference for lossy formats, default otherwise."""
        if os.path.splitext(path)[1].lower() in LOSSY_FORMATS:
            return int(self._settings.value("save_quality", 90))
        return -1

    def _do_save(self, path=None):
        """Save to path (or the current file). Returns True on success."""
        if self._pixmap is None:
            return False
        path = path or self._current_file
        if not path:
            return self._on_save_as()
        quality = self._save_quality_for(path)
        if not self._pixmap.save(path, None, quality):
            QMessageBox.warning(self, "Error", "Could not save the file.")
            return False
        self._saved_key = self._pixmap.cacheKey()
        try:
            self._file_size = os.path.getsize(path)
        except OSError:
            self._file_size = None
        self._update_title()
        self._update_status()
        if quality >= 0:
            self._status_bar.showMessage(f"Saved (quality {quality}).", 3000)
        else:
            self._status_bar.showMessage("Saved.", 3000)
        return True

    def _on_save(self):
        self._do_save()

    def _on_save_as(self):
        if self._pixmap is None:
            return False
        start = self._current_file or os.path.join(
            self._settings.value("last_dir") or str(Path.home()), "image.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image As", start,
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp);;TIFF (*.tiff);;WebP (*.webp);;All Files (*)"
        )
        if not path:
            return False
        if os.path.splitext(path)[1].lower() in LOSSY_FORMATS:
            quality, ok = QInputDialog.getInt(
                self, "Save Quality", "Encoder quality (1–100):",
                int(self._settings.value("save_quality", 90)), 1, 100)
            if not ok:
                return False
            self._settings.setValue("save_quality", quality)
        if not self._do_save(path):
            return False
        self._current_file = path
        self._scan_directory(path)
        self._add_recent(path)
        self._settings.setValue("last_dir", os.path.dirname(path))
        self._update_title()
        self._update_status()
        return True

    def _on_open_folder(self):
        if self._current_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._current_file)))

    def _on_next(self):
        self._step_directory(1)

    def _on_prev(self):
        self._step_directory(-1)

    def _on_first(self):
        self._go_to_index(0)

    def _on_last(self):
        self._go_to_index(len(self._dir_files) - 1)

    def _step_directory(self, step):
        if self._dir_files:
            self._go_to_index((self._dir_index + step) % len(self._dir_files))

    def _go_to_index(self, index):
        if not self._dir_files or index == self._dir_index or not self._confirm_discard():
            return
        self._dir_index = index
        self.open_file(self._dir_files[index], confirm=False)

    def _on_delete(self):
        if not self._current_file:
            return
        name = os.path.basename(self._current_file)
        if send2trash is not None:
            reply = QMessageBox.question(
                self, "Confirm Delete", f"Move to trash?\n{name}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.warning(
                self, "Confirm Delete",
                f"The Send2Trash package is not installed, so this will "
                f"PERMANENTLY delete the file — it cannot be undone.\n\n{name}\n\n"
                "Delete anyway?  (pip install Send2Trash to get trash support)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            if send2trash is not None:
                send2trash(self._current_file)
            else:
                os.remove(self._current_file)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Could not delete the file:\n{e}")
            return

        deleted = self._current_file
        self._saved_key = self._pixmap.cacheKey() if self._pixmap else None
        if deleted in self._dir_files:
            self._dir_files.remove(deleted)
        if self._dir_files:
            self._dir_index = min(self._dir_index, len(self._dir_files) - 1)
            self.open_file(self._dir_files[self._dir_index], confirm=False)
        else:
            self._stop_movie()
            self._pixmap = None
            self._current_file = None
            self._file_size = None
            self._show_placeholder()
            self._update_status()
            self._update_actions()
            self._update_title()

    # ── Undo / Redo ──────────────────────────────────────────────────

    def _on_undo(self):
        if not self._undo_stack or self._crop_mode:
            return
        self._redo_stack.append(self._pixmap)
        self._restore(self._undo_stack.pop())

    def _on_redo(self):
        if not self._redo_stack or self._crop_mode:
            return
        self._undo_stack.append(self._pixmap)
        self._restore(self._redo_stack.pop())

    def _restore(self, pixmap):
        self._show_pixmap(pixmap, keep_view=pixmap.size() == self._pixmap.size())

    # ── Clipboard ────────────────────────────────────────────────────

    def _clipboard_pixmap(self):
        clipboard = QApplication.clipboard()
        pixmap = clipboard.pixmap()
        if pixmap.isNull():
            img = clipboard.image()
            if not img.isNull():
                pixmap = QPixmap.fromImage(img)
        return pixmap

    def _on_copy(self):
        if self._pixmap is None:
            return
        if self._crop_mode:
            r = self._canvas.crop_selection()
            QApplication.clipboard().setPixmap(self._pixmap.copy(r))
            self._status_bar.showMessage(
                f"Selection ({r.width()} × {r.height()} px) copied to clipboard.", 3000)
        else:
            QApplication.clipboard().setPixmap(self._pixmap)
            self._status_bar.showMessage("Image copied to clipboard.", 3000)

    def _on_paste_replace(self):
        pixmap = self._clipboard_pixmap()
        if pixmap.isNull():
            QMessageBox.information(self, "Paste", "No image found on clipboard.")
            return
        self._current_file = None
        self._file_size = None
        self._set_image(pixmap)
        self._update_title()

    def _on_paste_side(self):
        """The star feature: paste clipboard image to a chosen side of the current image."""
        if self._pixmap is None:
            self._on_paste_replace()
            return

        clip_pixmap = self._clipboard_pixmap()
        if clip_pixmap.isNull():
            QMessageBox.information(self, "Paste to Side", "No image found on clipboard.")
            return

        dlg = PasteDialog(self._pixmap, clip_pixmap, self._settings, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.get_options()
        result = compose_side_by_side(self._pixmap, clip_pixmap, opts["side"], opts["align"],
                                      opts["gap"], opts["bg_color"], opts["scale_to_match"])
        self._set_image(result)
        self._status_bar.showMessage(f"Pasted image to the {opts['side']}.", 3000)

    # ── Zoom / Pan ───────────────────────────────────────────────────

    def _apply_zoom(self, factor, global_pos=None):
        """Zoom keeping the image point under global_pos (default: view center) fixed."""
        if self._pixmap is None:
            return
        factor = max(MIN_ZOOM, min(factor, MAX_ZOOM))
        vp = self._scroll_area.viewport()
        if global_pos is None:
            global_pos = vp.mapToGlobal(vp.rect().center())
        anchor = self._canvas.map_to_image(QPointF(self._canvas.mapFromGlobal(global_pos)))
        vp_pos = vp.mapFromGlobal(global_pos)

        self._fit_mode = False
        self._zoom_factor = factor
        self._display_image()

        target = self._canvas.map_from_image(anchor)
        self._scroll_area.horizontalScrollBar().setValue(round(target.x() - vp_pos.x()))
        self._scroll_area.verticalScrollBar().setValue(round(target.y() - vp_pos.y()))

    def _on_zoom_in(self):
        self._apply_zoom(step_zoom(self._zoom_factor, 1))

    def _on_zoom_out(self):
        self._apply_zoom(step_zoom(self._zoom_factor, -1))

    def _on_zoom_fit(self):
        self._fit_mode = True
        self._display_image()  # also re-checks the action if it was toggled off

    def _on_zoom_100(self):
        self._apply_zoom(1.0)

    def _on_zoom_edit_finished(self):
        self._scroll_area.setFocus()
        self._update_status()

    def _on_enlarge_toggled(self, on):
        self._settings.setValue("view/enlarge_small", on)
        if self._fit_mode:
            self._display_image()

    def _on_pan(self, delta):
        hbar = self._scroll_area.horizontalScrollBar()
        vbar = self._scroll_area.verticalScrollBar()
        hbar.setValue(hbar.value() - delta.x())
        vbar.setValue(vbar.value() - delta.y())

    def _on_hover(self, px):
        self._pos_label.setText(f"{px.x()}, {px.y()}" if px is not None else "")

    def _on_double_click(self):
        if self._pixmap is None:
            self._on_open()
        elif self._fit_mode:
            self._on_zoom_100()
        else:
            self._on_zoom_fit()

    def _on_toolbar_toggled(self, on):
        if self._chrome_state is None:  # not a temporary full-screen hide
            self._settings.setValue("view/toolbar", on)

    def _on_statusbar_toggled(self, on):
        self._status_bar.setVisible(on)
        if self._chrome_state is None:
            self._settings.setValue("view/statusbar", on)

    def _on_fullscreen(self):
        if self.isFullScreen():
            self._leave_fullscreen()
        else:
            self._chrome_state = (self.menuBar().isVisible(), self._toolbar.isVisible(),
                                  self._status_bar.isVisible(), self.isMaximized())
            for w in (self.menuBar(), self._toolbar, self._status_bar):
                w.hide()
            self._set_view_background(FULLSCREEN_BG)
            self.showFullScreen()
        self._actions["fullscreen"].setChecked(self.isFullScreen())

    def _leave_fullscreen(self):
        if self.isFullScreen():
            if self._chrome_state and self._chrome_state[3]:
                self.showMaximized()
            else:
                self.showNormal()
        self._restore_chrome()

    def _restore_chrome(self):
        if self._chrome_state is not None:
            menu_on, tool_on, status_on, _ = self._chrome_state
            self.menuBar().setVisible(menu_on)
            self._toolbar.setVisible(tool_on)
            self._status_bar.setVisible(status_on)
            self._chrome_state = None
            self._set_view_background(VIEW_BG)
        self._actions["fullscreen"].setChecked(False)

    # ── Transforms ───────────────────────────────────────────────────

    def _on_rotate_cw(self):
        if self._pixmap:
            self._set_image(self._pixmap.transformed(QTransform().rotate(90)))

    def _on_rotate_ccw(self):
        if self._pixmap:
            self._set_image(self._pixmap.transformed(QTransform().rotate(-90)))

    def _on_flip_h(self):
        if self._pixmap:
            self._set_image(self._pixmap.transformed(QTransform().scale(-1, 1)))

    def _on_flip_v(self):
        if self._pixmap:
            self._set_image(self._pixmap.transformed(QTransform().scale(1, -1)))

    # ── Image Adjustments (via Pillow) ───────────────────────────────

    def _on_adjustments(self):
        if not self._pixmap:
            return
        if self._movie is not None:
            self._stop_movie()
            self._status_bar.showMessage(
                "Animation flattened to a single frame for editing.", 4000)

        base = self._pixmap
        preview_src = base
        if max(base.width(), base.height()) > PREVIEW_MAX_DIM:
            preview_src = base.scaled(
                PREVIEW_MAX_DIM, PREVIEW_MAX_DIM,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        self._adjust_preview_pil = qpixmap_to_pil(preview_src)

        dlg = AdjustmentsDialog(self)
        dlg.preview_requested.connect(self._on_adjust_preview)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        self._adjust_preview_pil = None
        self._canvas.set_preview(None)

        values = dlg.get_values()
        if accepted and not is_default_adjustments(values):
            full = apply_adjustments(qpixmap_to_pil(base), values)
            self._set_image(pil_to_qpixmap(full))
            self._status_bar.showMessage("Adjustments applied.", 3000)

    def _on_adjust_preview(self, values):
        if self._adjust_preview_pil is None:
            return
        if is_default_adjustments(values):
            self._canvas.set_preview(None)
        else:
            self._canvas.set_preview(pil_to_qpixmap(apply_adjustments(self._adjust_preview_pil, values)))

    def _on_grayscale(self):
        if not self._pixmap:
            return
        pil = qpixmap_to_pil(self._pixmap)
        alpha = pil.getchannel("A")
        gray = pil.convert("L").convert("RGBA")
        gray.putalpha(alpha)
        self._set_image(pil_to_qpixmap(gray))

    def _on_resize(self):
        if not self._pixmap:
            return
        dlg = ResizeDialog(self._pixmap.width(), self._pixmap.height(), self._settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_w, new_h = dlg.get_size()
            if (new_w, new_h) != (self._pixmap.width(), self._pixmap.height()):
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    resized = resize_pixmap(self._pixmap, new_w, new_h, dlg.get_method())
                finally:
                    QApplication.restoreOverrideCursor()
                self._set_image(resized)
                self._status_bar.showMessage(f"Resized to {new_w} × {new_h} px.", 3000)

    # ── Crop ─────────────────────────────────────────────────────────

    def _on_crop_toggle(self):
        if self._crop_mode:
            self._on_crop_cancel()
        else:
            self._enter_crop_mode()

    def _enter_crop_mode(self):
        if self._pixmap is None or self._crop_mode:
            self._update_actions()
            return
        if self._movie is not None:
            self._stop_movie()
            self._status_bar.showMessage(
                "Animation paused on its first frame for cropping.", 4000)
        self._crop_mode = True
        self._crop_bar.start(self._pixmap.width(), self._pixmap.height())
        self._canvas.set_crop_ratio(self._crop_bar.current_ratio())
        self._canvas.set_crop_mode(True)
        self._shift_scroll(self._canvas.pad())
        self._crop_bar.setVisible(True)
        self._display_image()
        self._scroll_area.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._scroll_area.setFocus()
        self._update_actions()
        self._status_bar.showMessage(
            "Drag an edge or corner to crop, or drag inside to move · Enter to crop, Esc to cancel")

    def _exit_crop_mode(self):
        if not self._crop_mode:
            return
        pad = self._canvas.pad()
        self._crop_mode = False
        self._autoscroll.stop()
        self._crop_bar.setVisible(False)
        self._canvas.set_space_pan(False)
        self._canvas.set_crop_mode(False)
        self._shift_scroll(-pad)
        self._scroll_area.viewport().unsetCursor()
        self._status_bar.clearMessage()
        self._display_image()
        self._update_actions()

    def _shift_scroll(self, delta):
        """Keep the view steady when the crop margin appears or disappears."""
        if not self._fit_mode:
            for bar in (self._scroll_area.horizontalScrollBar(), self._scroll_area.verticalScrollBar()):
                bar.setValue(bar.value() + delta)

    def _on_crop_selection_changed(self, rect):
        self._crop_bar.set_selection(rect, not self._canvas.is_crop_full())

    def _on_crop_apply(self):
        if not self._crop_mode:
            return
        r = self._canvas.crop_selection().intersected(
            QRect(0, 0, self._pixmap.width(), self._pixmap.height()))
        if r.isEmpty() or self._canvas.is_crop_full():
            self._exit_crop_mode()
            return
        cropped = self._pixmap.copy(r)
        self._set_image(cropped)  # leaves crop mode
        self._status_bar.showMessage(f"Cropped to {cropped.width()} × {cropped.height()} px.", 3000)

    def _on_crop_cancel(self):
        self._exit_crop_mode()

    def _on_crop_drag_active(self, active):
        if active:
            self._scroll_area.setFocus()  # so arrow keys nudge after using the fields
            self._autoscroll.start()
        else:
            self._autoscroll.stop()

    def _on_autoscroll(self):
        """While dragging a crop past the edge of the view, scroll towards the cursor."""
        if not self._canvas.is_crop_dragging():
            self._autoscroll.stop()
            return
        vp = self._scroll_area.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        r = vp.rect()

        def overshoot(v, lo, hi):
            return v - lo if v < lo else (v - hi if v > hi else 0)

        moved = False
        for bar, over in ((self._scroll_area.horizontalScrollBar(), overshoot(pos.x(), r.left(), r.right())),
                          (self._scroll_area.verticalScrollBar(), overshoot(pos.y(), r.top(), r.bottom()))):
            if over:
                before = bar.value()
                bar.setValue(before + max(-AUTOSCROLL_MAX, min(AUTOSCROLL_MAX, over)))
                moved |= bar.value() != before
        if moved:
            self._canvas.continue_crop_drag(QCursor.pos(), QGuiApplication.keyboardModifiers())

    def _handle_crop_key(self, event):
        """Crop-tool keys while the image has focus. Returns True if handled."""
        key, mods = event.key(), event.modifiers()
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            step = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
            dx, dy = arrows[key]
            self._canvas.nudge_crop(dx * step, dy * step)
            return True
        if key == Qt.Key.Key_X and not mods & (Qt.KeyboardModifier.ControlModifier
                                               | Qt.KeyboardModifier.AltModifier):
            self._crop_bar.toggle_orientation()
            return True
        if key == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            self._canvas.reset_crop()
            return True
        if key == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self._canvas.set_space_pan(True)
            return True
        return False

    def _forward_mouse(self, event):
        """Relay a viewport mouse event to the canvas (crops can start off-image)."""
        vp = self._scroll_area.viewport()
        local = self._canvas.mapFrom(vp, event.position())
        relay = QMouseEvent(event.type(), local, event.globalPosition(), event.button(),
                            event.buttons(), event.modifiers())
        handler = {QEvent.Type.MouseButtonPress: self._canvas.mousePressEvent,
                   QEvent.Type.MouseMove: self._canvas.mouseMoveEvent,
                   QEvent.Type.MouseButtonRelease: self._canvas.mouseReleaseEvent}[event.type()]
        handler(relay)

    # ── Context menu ─────────────────────────────────────────────────

    def _show_context_menu(self, _pos):
        m = QMenu(self)
        a = self._actions
        if self._crop_mode:
            apply = m.addAction(icon("check"), "Crop")
            apply.setEnabled(not self._canvas.is_crop_full())
            apply.triggered.connect(self._on_crop_apply)
            m.addAction(icon("close"), "Cancel").triggered.connect(self._on_crop_cancel)
            m.addSeparator()
            m.addAction(icon("copy"), "Copy Selection").triggered.connect(self._on_copy)
            m.addAction(icon("reset"), "Reset Selection").triggered.connect(self._canvas.reset_crop)
            m.addAction(icon("swap"), "Swap Orientation").triggered.connect(
                self._crop_bar.toggle_orientation)
        elif self._pixmap is None:
            for k in ("open", "paste_replace"):
                m.addAction(a[k])
        else:
            groups = (("copy", "paste_replace", "paste_side"), ("rot_ccw", "rot_cw", "crop"),
                      ("zoom_fit", "zoom_100", "fullscreen"), ("open_folder", "properties"),
                      ("delete_file",))
            for i, group in enumerate(groups):
                if i:
                    m.addSeparator()
                for k in group:
                    m.addAction(a[k])
        m.exec(QCursor.pos())

    # ── Info / Help ──────────────────────────────────────────────────

    def _on_info(self):
        if self._pixmap is None:
            return
        rows = []
        if self._current_file:
            rows.append(("File", os.path.basename(self._current_file)))
            rows.append(("Folder", os.path.dirname(self._current_file)))
            try:
                size = os.path.getsize(self._current_file)
                if size >= 1024 * 1024:
                    rows.append(("File size", f"{size / (1024 * 1024):.2f} MB"))
                else:
                    rows.append(("File size", f"{size / 1024:.1f} KB"))
            except OSError:
                pass
        w, h = self._pixmap.width(), self._pixmap.height()
        rows.append(("Dimensions", f"{w} × {h} px"))
        rows.append(("Megapixels", f"{w * h / 1e6:.2f} MP"))
        if self._movie is not None:
            rows.append(("Animation", f"{self._movie.frameCount()} frames"))

        if self._current_file:
            rows.extend(self._exif_rows(self._current_file))

        html = "<table cellpadding='4'>"
        for k, v in rows:
            html += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
        html += "</table>"
        QMessageBox.information(self, "Properties", html)

    @staticmethod
    def _exif_rows(filepath):
        """A few common EXIF fields, if the file has them."""
        rows = []
        try:
            from PIL import Image
            with Image.open(filepath) as im:
                rows.append(("Format", f"{im.format} ({im.mode})"))
                exif = im.getexif()
                for tag, label in ((271, "Camera make"), (272, "Camera model"),
                                   (306, "Date taken")):
                    value = exif.get(tag)
                    if value:
                        rows.append((label, str(value).strip()))
        except Exception:
            pass
        return rows

    def _on_about(self):
        QMessageBox.about(
            self, "About ImaMax",
            f"<h2>ImaMax {__version__}</h2>"
            "<p>A lightweight image viewer for Linux with IrfanView-style "
            "clipboard paste-to-side support.</p>"
            "<p><b>Key Feature:</b> Ctrl+Shift+V to paste a clipboard image "
            "to the top, bottom, left, or right of the current image.</p>"
            "<p>Built with PyQt6 &amp; Pillow.</p>"
        )

    def _on_shortcuts(self):
        sections = []
        for m in self._menus:
            rows = [(action_label(act), shortcut_text(act)) for act in m.actions()
                    if not act.isSeparator() and act.shortcuts()]
            if rows:
                sections.append((m.title().replace("&", ""), rows))
        ShortcutsDialog(sections + EXTRA_SHORTCUTS, self).exec()

    # ── Events ───────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        vp = self._scroll_area.viewport()
        t = event.type()
        if obj is vp:
            if t == QEvent.Type.Resize:
                if self._pixmap is None:
                    self._canvas.resize(vp.size())
                elif self._fit_mode:
                    self._display_image()
                else:
                    self._update_pannable()
            elif t == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._pixmap is not None:
                    # Accumulate so high-resolution wheels/touchpads step once per notch
                    self._wheel_accum += event.angleDelta().y()
                    while abs(self._wheel_accum) >= 120:
                        direction = 1 if self._wheel_accum > 0 else -1
                        self._wheel_accum -= 120 * direction
                        self._apply_zoom(step_zoom(self._zoom_factor, direction),
                                         event.globalPosition().toPoint())
                    return True
            elif self._crop_mode and self._pixmap is not None and t in (
                    QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
                # Presses beside the image still start / continue a crop drag
                if (t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
                        and not self._canvas.geometry().contains(event.position().toPoint())):
                    self._forwarding_mouse = True
                if self._forwarding_mouse:
                    self._forward_mouse(event)
                    if t == QEvent.Type.MouseButtonRelease:
                        self._forwarding_mouse = False
                    return True
        elif obj is self._scroll_area and self._crop_mode:
            if t == QEvent.Type.KeyPress and self._handle_crop_key(event):
                return True
            if (t == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Space
                    and not event.isAutoRepeat()):
                self._canvas.set_space_pan(False)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._crop_mode:
                self._on_crop_cancel()
            elif self.isFullScreen():
                self._leave_fullscreen()
            else:
                super().keyPressEvent(event)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._crop_mode:
            focus = QApplication.focusWidget()
            if isinstance(focus, QAbstractSpinBox) and self._crop_bar.isAncestorOf(focus):
                self._scroll_area.setFocus()  # value committed; next Enter crops
            else:
                self._on_crop_apply()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.BackButton and self._actions["prev"].isEnabled():
            self._on_prev()
        elif event.button() == Qt.MouseButton.ForwardButton and self._actions["next"].isEnabled():
            self._on_next()
        else:
            super().mousePressEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and not self.isFullScreen()
                and self._chrome_state is not None):
            self._restore_chrome()  # left full screen via the window manager

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self.open_file(path)

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        if self.isFullScreen():
            self._leave_fullscreen()
        self._settings.setValue("geometry", self.saveGeometry())
        event.accept()
