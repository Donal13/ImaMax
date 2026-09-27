# ImaMax

A lightweight image viewer for Linux with IrfanView-style clipboard
**paste-to-side** support: press `Ctrl+Shift+V` to paste a clipboard image to
the top, bottom, left, or right of the current image — with alignment,
spacing, fill-color options and a live preview. Built with PyQt6 and Pillow.

## Features

- Paste-to-side compositing (the star feature) with a live preview and
  remembered settings — repeating the last paste is just `Ctrl+Shift+V`, `Enter`
- Crop tool with edge and corner handles, drag-to-move, aspect-ratio presets
  (with portrait/landscape swap), numeric position/size fields, arrow-key
  nudging, and auto-scroll when dragging past the edge of a zoomed view
- Zoom at the mouse cursor (Ctrl+wheel) through standard zoom stops, sharp
  pixels from 200% up, drag-to-pan, double-click to toggle fit/100%, and a
  zoom box in the status bar
- Adjustments dialog with live preview and before/after toggle: brightness,
  contrast, saturation, sharpness, blur
- Rotate, flip, resize (pixels or %, Lanczos / bilinear / nearest-neighbor),
  grayscale
- Folder navigation in natural order (`img2` before `img10`), animated GIF
  playback, EXIF-aware orientation
- Undo/redo, unsaved-changes protection, safe move-to-trash
- Full screen that hides everything but the image; right-click context menu
- Remembers window geometry, last folder, recent files, and view options

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python3 image_viewer.py [image]
```

To add it to your application menu, copy `ImaMax.desktop` to
`~/.local/share/applications/` and edit its `Exec=` line to point at wherever
`image_viewer.py` lives.

## Keyboard shortcuts

Press `F1` in the app for a searchable list.

| Key | Action |
| --- | --- |
| `Ctrl+O` | Open image |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / Save As |
| `Alt+Enter` or `I` | Properties |
| `Delete` | Move to trash |
| `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`) | Undo / Redo |
| `Ctrl+C` | Copy (the crop selection, while cropping) |
| `Ctrl+V` | Paste (replace) |
| `Ctrl+Shift+V` | **Paste to side** |
| `Ctrl++` / `Ctrl+-` (or `+` / `-`) | Zoom in / out |
| `Ctrl+1` / `Ctrl+0` | Actual size / Fit to window |
| `F11` | Full screen |
| `C` | Crop |
| `Ctrl+Alt+I` | Resize |
| `Ctrl+L` / `Ctrl+R` | Rotate left / right |
| `H` / `V` | Flip horizontal / vertical |
| `A` | Adjustments |
| `←` `→`, `Backspace` `Space` | Previous / Next image |
| `Home` / `End` | First / Last image |
| `F1` | Keyboard shortcuts |

**While cropping:** drag an edge or corner to resize, drag inside to move,
drag outside to start over; `Shift` keeps proportions; arrow keys nudge
(`Shift`: 10 px); `X` swaps portrait/landscape; `Ctrl+A` resets; `Space`+drag
or middle-drag pans; `Enter` or double-click crops; `Esc` cancels.

**Mouse:** `Ctrl`+wheel zooms at the cursor, drag or middle-drag pans,
double-click toggles fit/100%, back/forward buttons change image.

## Code layout

```
image_viewer.py        thin launcher (kept for existing shortcuts/.desktop files)
imamax/
  main.py              QApplication setup, dark theme
  viewer.py            main window: actions, menus, toolbar, file handling
  canvas.py            image display: zoomed rendering, crop tool, panning
  widgets.py           crop options bar, status-bar zoom control
  dialogs.py           paste-to-side, resize, adjustments, shortcuts dialogs
  icons.py             built-in toolbar/menu icons (tinted to the theme)
  utils.py             image loading, PIL/Qt conversion, compositing, zoom stops
```
