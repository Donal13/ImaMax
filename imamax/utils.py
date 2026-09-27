"""Shared helpers: image loading, PIL/Qt conversion, sorting, adjustments,
paste-to-side compositing, resizing and zoom steps."""

import re
from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QImageReader, QPainter, QPixmap
from PIL import Image, ImageEnhance, ImageFilter

SUPPORTED_FORMATS = (
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif",
    ".webp", ".ico", ".ppm", ".pgm", ".pbm", ".xbm", ".xpm", ".svg",
)

LOSSY_FORMATS = (".jpg", ".jpeg", ".webp")


def natural_key(path):
    """Sort key that orders img2.png before img10.png."""
    name = Path(path).name.lower()
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def load_image(filepath):
    """Load an image respecting EXIF orientation.

    Returns (pixmap, is_animated); pixmap is null on failure.
    """
    reader = QImageReader(filepath)
    reader.setAutoTransform(True)
    is_animated = reader.supportsAnimation() and reader.imageCount() > 1
    image = reader.read()
    if image.isNull():
        return QPixmap(), False
    return QPixmap.fromImage(image), is_animated


def clipboard_pixmap():
    """Image on the clipboard as a QPixmap (null if there is none)."""
    clipboard = QGuiApplication.clipboard()
    pixmap = clipboard.pixmap()
    if pixmap.isNull():
        img = clipboard.image()
        if not img.isNull():
            pixmap = QPixmap.fromImage(img)
    return pixmap


def screen_pixel_size(screen=None):
    """Physical pixel size of `screen` (default: the primary screen)."""
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return QSize(1920, 1080)
    dpr = screen.devicePixelRatio()
    size = screen.size()
    return QSize(round(size.width() * dpr), round(size.height() * dpr))


def qpixmap_to_pil(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    ptr = image.bits()
    ptr.setsize(height * width * 4)
    return Image.frombytes("RGBA", (width, height), bytes(ptr))


def pil_to_qpixmap(pil_image):
    if pil_image.mode != "RGBA":
        pil_image = pil_image.convert("RGBA")
    data = pil_image.tobytes("raw", "RGBA")
    qimage = QImage(data, pil_image.width, pil_image.height,
                    QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


ADJUSTMENT_DEFAULTS = {
    "brightness": 1.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 1.0,
    "blur": 0.0,
}


def apply_adjustments(pil_image, values):
    """Apply enhancement factors (1.0 = unchanged) and a Gaussian blur radius."""
    img = pil_image
    if abs(values.get("brightness", 1.0) - 1.0) > 1e-3:
        img = ImageEnhance.Brightness(img).enhance(values["brightness"])
    if abs(values.get("contrast", 1.0) - 1.0) > 1e-3:
        img = ImageEnhance.Contrast(img).enhance(values["contrast"])
    if abs(values.get("saturation", 1.0) - 1.0) > 1e-3:
        img = ImageEnhance.Color(img).enhance(values["saturation"])
    if abs(values.get("sharpness", 1.0) - 1.0) > 1e-3:
        img = ImageEnhance.Sharpness(img).enhance(values["sharpness"])
    if values.get("blur", 0.0) > 1e-3:
        img = img.filter(ImageFilter.GaussianBlur(radius=values["blur"]))
    return img


def is_default_adjustments(values):
    return all(abs(values.get(k, d) - d) < 1e-3 for k, d in ADJUSTMENT_DEFAULTS.items())


def paste_layout(current_size, pasted_size, side, align, gap, scale_to_match):
    """Geometry of a paste-to-side result.

    Returns (canvas QSize, current QRect, pasted QRect). With scale_to_match
    the pasted image is scaled to the current image's height (left/right) or
    width (top/bottom).
    align: 'start' | 'center' | 'end' — cross-axis placement of the smaller image
    """
    cw, ch = current_size.width(), current_size.height()
    pw, ph = pasted_size.width(), pasted_size.height()
    horizontal = side in ("left", "right")
    if scale_to_match and pw > 0 and ph > 0:
        if horizontal:
            pw, ph = max(1, round(pw * ch / ph)), ch
        else:
            pw, ph = cw, max(1, round(ph * cw / pw))

    if horizontal:
        total_w, total_h = cw + pw + gap, max(ch, ph)
    else:
        total_w, total_h = max(cw, pw), ch + ph + gap

    def offset(size, total):
        if align == "start":
            return 0
        if align == "end":
            return total - size
        return (total - size) // 2

    if side == "left":
        pasted = QRect(0, offset(ph, total_h), pw, ph)
        current = QRect(pw + gap, offset(ch, total_h), cw, ch)
    elif side == "right":
        current = QRect(0, offset(ch, total_h), cw, ch)
        pasted = QRect(cw + gap, offset(ph, total_h), pw, ph)
    elif side == "top":
        pasted = QRect(offset(pw, total_w), 0, pw, ph)
        current = QRect(offset(cw, total_w), ph + gap, cw, ch)
    else:  # bottom
        current = QRect(offset(cw, total_w), 0, cw, ch)
        pasted = QRect(offset(pw, total_w), ch + gap, pw, ph)
    return QSize(total_w, total_h), current, pasted


def compose_side_by_side(current, pasted, side, align, gap, bg_color, scale_to_match=False):
    """Join `pasted` onto the given side of `current` (both QPixmaps).

    bg_color: (r, g, b[, a]) fill for the gap and any unmatched area
    """
    size, cur_rect, paste_rect = paste_layout(
        current.size(), pasted.size(), side, align, gap, scale_to_match)
    if paste_rect.size() != pasted.size():
        pasted = pasted.scaled(paste_rect.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    result = QPixmap(size)
    result.fill(QColor(*bg_color))
    painter = QPainter(result)
    painter.drawPixmap(cur_rect.topLeft(), current)
    painter.drawPixmap(paste_rect.topLeft(), pasted)
    painter.end()
    return result


RESAMPLING = {
    "lanczos": Image.Resampling.LANCZOS,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
}


def resize_pixmap(pixmap, width, height, method="lanczos"):
    """Resize with Pillow's resamplers (Lanczos is sharper than Qt's smooth scale)."""
    pil = qpixmap_to_pil(pixmap)
    return pil_to_qpixmap(pil.resize((width, height), RESAMPLING.get(method, Image.Resampling.LANCZOS)))


# Standard zoom stops (like most editors) so stepping lands on 100%, 200%…
ZOOM_LEVELS = (
    0.02, 0.03, 0.05, 0.0625, 0.0833, 0.125, 0.1667, 0.25, 0.3333, 0.5, 0.6667,
    1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0,
)
MIN_ZOOM, MAX_ZOOM = ZOOM_LEVELS[0], ZOOM_LEVELS[-1]


def step_zoom(zoom, direction):
    """Next zoom stop above (direction > 0) or below (< 0) the current zoom."""
    if direction > 0:
        return next((z for z in ZOOM_LEVELS if z > zoom * 1.001), MAX_ZOOM)
    return next((z for z in reversed(ZOOM_LEVELS) if z < zoom / 1.001), MIN_ZOOM)
