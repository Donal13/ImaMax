# ImaMax

A lightweight image viewer for Linux with IrfanView-style clipboard
**paste-to-side** support — press `Ctrl+Shift+V` to paste a clipboard image to
the top, bottom, left, or right of the current image — and a **Wallpaper
Maker** that grows the same idea into multi-image mosaic wallpapers sized for
your screen. Built with PyQt6 and Pillow.

## Features

- Paste-to-side compositing (the star feature) with a live preview and
  remembered settings — repeating the last paste is just `Ctrl+Shift+V`, `Enter`
- **Wallpaper Maker** (below) for mosaic wallpapers from many images
- Crop tool with edge and corner handles, drag-to-move, numeric position/size
  fields, arrow-key nudging, and auto-scroll when dragging past the edge of a
  zoomed view. Aspect-ratio presets cover photo, monitor and phone shapes —
  including **Screen** (your monitor's own ratio), 16:9, 16:10, 21:9 and
  32:9 — with a portrait/landscape swap
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

## Wallpaper Maker

Open it with **Tools ▸ Wallpaper Maker** (`Ctrl+Shift+M`). The canvas starts
at your screen's resolution; presets cover common monitors and phones.

- **Collect images** by pressing `W` in the viewer as you browse (while
  cropping, `W` adds just the selection), with *Add Images*, by pasting, or by
  dropping files onto the canvas. From *Paste to Side*, the **Wallpaper
  Maker…** button carries the pair across.
- **Auto layout** arranges the images in rows or columns that crop them as
  little as possible, and keeps re-flowing as you add images or change the
  canvas shape — until you arrange things by hand.
- **Arrange by hand**: pick a layout template, drag the gaps between images
  to resize them, `Ctrl`+drag an image onto another to swap them or onto its
  edge to dock it there, and `Ctrl+Shift`+arrow to paste the clipboard beside
  the selected image (paste-to-side, as often as you like).
- **Frame each image**: drag to reposition it, wheel to zoom, `F` to fit the
  whole image instead of filling the cell, `R` to rotate. A warning appears
  if an image would be enlarged enough to look soft.
- **Style**: gap, outer margin, rounded corners and background colour.
- **Finish**: *Set as Wallpaper* saves to `~/Pictures/Wallpapers` and applies
  it (KDE Plasma, GNOME/Cinnamon or Windows); *Save As* writes PNG, JPEG or
  WebP; *Open in Viewer* hands it to the main window for further editing.

Everything is undoable (`Ctrl+Z`), and the design stays put if you close the
window, until you quit.

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
| `Ctrl+Shift+M` | Wallpaper Maker |
| `W` | Add the image (or crop selection) to the wallpaper |
| `←` `→`, `Backspace` `Space` | Previous / Next image |
| `Home` / `End` | First / Last image |
| `F1` | Keyboard shortcuts |

**While cropping:** drag an edge or corner to resize, drag inside to move,
drag outside to start over; `Shift` keeps proportions; arrow keys nudge
(`Shift`: 10 px); `X` swaps portrait/landscape; `Ctrl+A` resets; `Space`+drag
or middle-drag pans; `Enter` or double-click crops; `Esc` cancels.

**In the Wallpaper Maker:** arrow keys select a neighbouring cell;
`Ctrl+Shift`+arrow pastes beside it; `F` fit/fill, `R` rotate, `0` reset,
`+`/`-` zoom the image; `Delete` removes the cell.

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
  wallpaper.py         Wallpaper Maker window and its interactive canvas
  mosaic.py            mosaic model: split-tree layout, templates, rendering
  icons.py             built-in toolbar/menu icons (tinted to the theme)
  utils.py             image loading, PIL/Qt conversion, compositing, zoom stops
```
