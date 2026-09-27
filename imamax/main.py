"""Application entry point: theme, settings identity, argument handling."""

import sys

from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication

from .viewer import ImageViewer


def _dark_palette():
    """Dark theme that fits well on KDE/Kubuntu."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 50))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(60, 60, 60))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button, QColor(55, 55, 55))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link, QColor(100, 160, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(80, 120, 200))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(130, 130, 130))
    # Without distinct disabled colours, unavailable menu items look clickable
    disabled = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(disabled, role, QColor(120, 120, 120))
    palette.setColor(disabled, QPalette.ColorRole.Light, QColor(43, 43, 43))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor(70, 70, 70))
    return palette


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ImaMax")
    app.setOrganizationName("ImaMax")
    # Lets KDE/Wayland associate the window with the .desktop launcher
    QGuiApplication.setDesktopFileName("ImaMax")
    app.setPalette(_dark_palette())

    viewer = ImageViewer(filepath=sys.argv[1] if len(sys.argv) > 1 else None)
    viewer.show()
    sys.exit(app.exec())
