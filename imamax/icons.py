"""Built-in line icons, rendered from inline SVG and tinted from the palette.

Bundling the icons (rather than relying on the desktop icon theme) keeps the
toolbar identical on every platform and guarantees contrast against the dark
application palette. Icons render lazily at whatever size and device pixel
ratio Qt asks for, so they stay crisp on HiDPI screens.
"""

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QImage, QPainter, QPalette, QPixmap

try:
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:  # QtSvg missing: actions fall back to text labels
    QSvgRenderer = None

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.7" stroke-linecap="round" '
        'stroke-linejoin="round">{body}</svg>')

# 24×24 line drawings; "{c}" is replaced by the tint colour.
_ICONS = {
    "open": '<path d="M4 19V6a1 1 0 0 1 1-1h4.5l2 2H18a1 1 0 0 1 1 1v2"/>'
            '<path d="M4 19l2.6-7.3a1 1 0 0 1 .9-.7H21l-2.7 7.3a1 1 0 0 1-.9.7z"/>',
    "save": '<path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z"/>'
            '<path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "folder": '<path d="M4 18V6a1 1 0 0 1 1-1h4.5l2 2H19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5'
              'a1 1 0 0 1-1-1z"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.01"/>',
    "trash": '<path d="M4 7h16M10 3.5h4M6.5 7l.9 12.1a1 1 0 0 0 1 .9h7.2a1 1 0 0 0 1-.9'
             'L17.5 7M10 11v5.5M14 11v5.5"/>',
    "quit": '<path d="M9 20H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3M15.5 16l4-4-4-4M19.5 12H10"/>',
    "undo": '<path d="M9 13.5L4.5 9 9 4.5"/><path d="M4.5 9H15a4.5 4.5 0 0 1 0 9h-4"/>',
    "redo": '<path d="M15 13.5L19.5 9 15 4.5"/><path d="M19.5 9H9a4.5 4.5 0 0 0 0 9h4"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="1.5"/>'
            '<path d="M15.5 8.5V6A1.5 1.5 0 0 0 14 4.5H6A1.5 1.5 0 0 0 4.5 6v8'
            'A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    "paste": '<path d="M9 4.5H6.5a1 1 0 0 0-1 1V19.5a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V5.5'
             'a1 1 0 0 0-1-1H15"/><rect x="9" y="3" width="6" height="3.5" rx="1"/>',
    "paste_side": '<rect x="2.5" y="5" width="10" height="14" rx="1"/>'
                  '<path d="M15.5 5.5h4a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-4" '
                  'stroke-dasharray="2.2 2.2"/><path d="M18 9.5v5M15.5 12h5"/>',
    "zoom_in": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.9-4.9M10.5 8v5M8 10.5h5"/>',
    "zoom_out": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.9-4.9M8 10.5h5"/>',
    "zoom_fit": '<path d="M4 8.5V5a1 1 0 0 1 1-1h3.5M15.5 4H19a1 1 0 0 1 1 1v3.5M20 15.5V19'
                'a1 1 0 0 1-1 1h-3.5M8.5 20H5a1 1 0 0 1-1-1v-3.5"/>'
                '<rect x="8" y="8.5" width="8" height="7" rx="1"/>',
    "zoom_100": '<path d="M6.5 8.5L8.5 7v10M15.5 8.5L17.5 7v10M12 10.5v.01M12 14v.01"/>',
    "fullscreen": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1'
                  'h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
    "prev": '<path d="M14.5 18l-6-6 6-6"/>',
    "next": '<path d="M9.5 18l6-6-6-6"/>',
    "first": '<path d="M17 18l-6-6 6-6M7 6v12"/>',
    "last": '<path d="M7 18l6-6-6-6M17 6v12"/>',
    "rotate_left": '<path d="M5 13A7 7 0 1 0 12 6"/><path d="M12 6H6.5M9 3.2L6.2 6 9 8.8"/>',
    "rotate_right": '<path d="M19 13A7 7 0 1 1 12 6"/><path d="M12 6h5.5M15 3.2L17.8 6 15 8.8"/>',
    "flip_h": '<path d="M12 3v18" stroke-dasharray="1.6 2.4"/><path d="M9 6.5v11H3.5z"/>'
              '<path d="M15 6.5v11h5.5z" fill="{c}"/>',
    "flip_v": '<path d="M3 12h18" stroke-dasharray="1.6 2.4"/><path d="M6.5 9h11V3.5z"/>'
              '<path d="M6.5 15h11v5.5z" fill="{c}"/>',
    "crop": '<path d="M7 3v13a1 1 0 0 0 1 1h13"/><path d="M17 21V8a1 1 0 0 0-1-1H3"/>',
    "resize": '<rect x="3.5" y="12.5" width="8" height="8" rx="1"/>'
              '<path d="M3.5 9V4.5a1 1 0 0 1 1-1h15a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H15"/>'
              '<path d="M14 10l4-4M14 6h4v4"/>',
    "adjust": '<path d="M4 6h8M16 6h4M4 12h2M10 12h10M4 18h10M18 18h2"/>'
              '<circle cx="14" cy="6" r="2"/><circle cx="8" cy="12" r="2"/>'
              '<circle cx="16" cy="18" r="2"/>',
    "grayscale": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="{c}"/>',
    "swap": '<path d="M16.5 3.5L20 7l-3.5 3.5M20 7H8M7.5 13.5L4 17l3.5 3.5M4 17h12"/>',
    "reset": '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.6"/><path d="M4 4v4.6h4.6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="1.5"/>'
                '<path d="M6 9.8v.01M9.3 9.8v.01M12.6 9.8v.01M15.9 9.8v.01M18 9.8v.01M8 14.2h8"/>',
    "arrow_up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "arrow_down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "arrow_left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "arrow_right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def _tint(mode):
    pal = QGuiApplication.palette()
    if mode == QIcon.Mode.Disabled:
        base = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        bg = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
        # Halfway between text and background reads as "unavailable" on any theme
        return QColor((base.red() + bg.red()) // 2, (base.green() + bg.green()) // 2,
                      (base.blue() + bg.blue()) // 2)
    if mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
        return pal.color(QPalette.ColorRole.HighlightedText)
    return pal.color(QPalette.ColorRole.WindowText)


class _SvgIconEngine(QIconEngine):

    def __init__(self, body):
        super().__init__()
        self._body = body
        self._cache = {}

    def _render(self, size, mode, scale):
        color = _tint(mode)
        key = (size.width(), size.height(), mode, scale, color.rgba())
        pm = self._cache.get(key)
        if pm is None:
            svg = _SVG.format(c=color.name(), body=self._body.replace("{c}", color.name()))
            renderer = QSvgRenderer(QByteArray(svg.encode()))
            img = QImage(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)),
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, img.width(), img.height()))
            painter.end()
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(scale)
            self._cache[key] = pm
        return pm

    def paint(self, painter, rect, mode, state):
        scale = painter.device().devicePixelRatioF() if painter.device() else 1.0
        painter.drawPixmap(rect, self._render(rect.size(), mode, scale))

    def pixmap(self, size, mode, state):
        return self._render(size, mode, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        return self._render(size, mode, scale)

    def clone(self):
        return _SvgIconEngine(self._body)


_icon_cache = {}


def icon(name):
    """Return the named built-in icon (a null QIcon if unavailable)."""
    if name not in _icon_cache:
        body = _ICONS.get(name)
        if body is None or QSvgRenderer is None:
            _icon_cache[name] = QIcon()
        else:
            _icon_cache[name] = QIcon(_SvgIconEngine(body))
    return _icon_cache[name]


def swatch(color, size=QSize(16, 16)):
    """Small colour-sample icon, with a checkerboard showing any transparency."""
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    cell = max(2, size.width() // 4)
    for y in range(0, size.height(), cell):
        for x in range(0, size.width(), cell):
            light = ((x // cell) + (y // cell)) % 2 == 0
            p.fillRect(x, y, cell, cell, QColor(200, 200, 200) if light else QColor(140, 140, 140))
    p.fillRect(pm.rect(), color)
    p.setPen(QColor(0, 0, 0, 160))
    p.drawRect(pm.rect().adjusted(0, 0, -1, -1))
    p.end()
    return QIcon(pm)
