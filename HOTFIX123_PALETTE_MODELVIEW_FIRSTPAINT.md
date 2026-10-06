# Hotfix123 · Palette Model/View + Startup First Paint

## Goal
Migrate the proven P3-4/HF50 virtual-view architecture into Palette Studio without rolling back any HF122 business/science functionality, and restore the HF52 first-paint principle for application startup.

## 1. Palette Studio: real Model/View virtualization
The old Palette Studio path still created one `QListWidgetItem` for every palette slot and used a giant fixed `QListWidget` canvas. HF118-HF120 virtualized only the child `ColorCardCell` widgets; Qt still had to maintain thousands of item geometries.

HF123 replaces the palette surface with:

- `PaletteSlotModel(QAbstractTableModel)` — lightweight 1-D slot sequence mapped to fixed palette rows/columns.
- `ColorCardPlanGrid(QTableView)` — owns scrollbars; fixed logical columns are preserved regardless of window size.
- `PaletteCardDelegate(QStyledItemDelegate)` — QPainter-rendered swatch/name/Lab/blank/selection/fluorescent fold.
- No per-colour `QListWidgetItem` allocation.
- No per-colour `ColorCardCell QWidget` allocation.
- No giant fixed-height palette canvas.
- Qt asks only for cells visible in the current viewport.

Existing behaviour is preserved through a small compatibility proxy for palette commands:
Ctrl/Shift selection, Ctrl+A, Delete, Ctrl+C/X/V, drag/drop, blank slots, undo/redo, sorting, search, CPX fixed layout, QTX/Excel import, saving/export, fluorescent marks and sample details.

## 2. Large-import path
`_rebuild_slots_bulk()` now replaces the full palette model in one model reset instead of creating 660/3500 Qt items one by one.

After an import the status bar reports:

`色卡模型 N 位：xx.x ms · 首屏 yy.y ms`

This measures the part that the automatic core benchmark previously missed: model-to-GUI first paint.

## 3. Startup First Paint
The mature heavy tool pages are now lazy-built on first use:

- Formal Library
- Find / Search
- Palette Studio
- Compare Workbench
- Spectrum

The home shell is created first. Saved-state restoration is deferred until after the first event-loop/paint opportunity. Each tool is still created only once and remains persistent after its first open.

The status bar reports `启动首屏：xxx.x ms` on first show.

## 4. UI performance log
Real GUI timings are appended to:

`%APPDATA%/ChromaticAnalysis/ChromaticAnalysis/logs/ui_performance.jsonl`

Events include:

- `startup_first_paint`
- `tool_first_build`
- `palette_first_paint` (includes slot count and model-load time)

## 5. What was not changed
- QTX/CPX/Excel parsing and round-trip semantics
- HF122 vectorized spectral sorting
- CIELAB/XYZ/CMC/CIE94/CIEDE2000/MI/555 formulas
- RBAC / Data Governance
- formal/official library data model
- current Palette Studio UI commands and saved layout semantics

## Validation
Package preparation performs Python syntax compilation for the full source tree. Final PySide6 behaviour/performance must be verified on the user's Windows/Python 3.14 runtime.
