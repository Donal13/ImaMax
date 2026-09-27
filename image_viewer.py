#!/usr/bin/env python3
"""
ImaMax - A feature-rich image viewer for Linux with clipboard paste support.
Supports pasting clipboard images to the top, bottom, left, or right of the
current image.

Requirements: PyQt6, Pillow, Send2Trash
Install: pip install -r requirements.txt
Run: python3 image_viewer.py [optional_image_path]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from imamax.main import main

if __name__ == "__main__":
    main()
