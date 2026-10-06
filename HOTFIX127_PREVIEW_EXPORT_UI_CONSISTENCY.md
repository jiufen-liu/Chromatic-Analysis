# Hotfix127 · Preview / Export / UI Consistency

Base: Hotfix126.  This is a targeted regression/UX patch; frozen HF124 library/palette performance paths are unchanged.

## 1. Find QTX-name filter
- Restores an explicit preset dropdown for `全部 / DTY / FDY / YARN / SOCK / FABRIC`.
- The combo remains editable, so arbitrary text can still be typed.
- An attached down-arrow button is rendered explicitly because some Windows Qt styles hide the arrow on editable QComboBox controls.

## 2. Fluorescent / out-of-gamut preview consistency
- Find result cards now hydrate only the final bounded result set (for example top 5/10) and cache those full Samples per standard.
- Display therefore uses the same measured spectrum -> D65/2° preview path as QTX import, sample details and palette cards.
- Scientific Lab/XYZ/reflectance, ΔE and sorting are unchanged.

## 3. Large-QTX picker swatches
- Keeps the virtual `QTableView + QAbstractTableModel` used for Coloro 3500.
- Adds a dedicated `QStyledItemDelegate` that paints only visible swatches.
- No 3,500 QPixmap/icon allocations are introduced.
- Fluorescent folded-corner marker is painted consistently.

## 4. Visual export redesign
- PNG/JPG now use fixed page-shaped canvases instead of very tall strips.
- Orientation is available for PDF/PNG/JPG.
- Large palettes are split into numbered page images; order is preserved.
- Default image page density is 8 rows/page; user can choose up to 12.
- Very large image jobs (>30 pages) display a confirmation and recommend PDF for whole-library delivery.
- Fluorescent folded-corner marker is included in visual exports.

## 5. Export completion feedback
Color-card exports now use explicit completion dialogs for:
- PDF
- PNG/JPG
- Excel
- QTX
- CPX

The dialog reports the output file or file count and save location. Existing audit/status-bar logging remains.

## Validation performed in build environment
- `python -m compileall -q qtx_app qtx_core tests`: PASS
- Static regression checks confirm the editable dropdown, virtual swatch delegate, full-result preview hydration, fixed page image renderer and success notifications are present.
- The build environment does not include PySide6, so final Windows GUI rendering must be verified on the user's machine.
