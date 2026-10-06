# Hotfix75 · Sort Baseline Reset & Experiment Guard

## Purpose
HF74 field validation showed two product issues: hue-only Munsell is not a satisfactory one-dimensional palette arrangement for all samples, and Visual Palette V1 is not yet suitable as a production-default arrangement. HF75 therefore does **not** change either scientific/experimental algorithm.

## Changes
- Adds a real session-level **Restore Original Order** action. A draft imported from QTX/CPX/Excel can now return to the order captured immediately after import, even before Ctrl+S.
- Restore is placed directly under `整理方案`, not buried inside a sort submenu.
- Basic scalar sorts (Name/L*/a*/b*/C*) remain under `基础排序`.
- Munsell, spectral RMS, spectral+perceptual, and Visual Palette V1 are moved to `高级 / 对照排序`; V1 is explicitly marked comparison-only.
- Any restore request invalidates a pending background sort, so an old Munsell/spectral result cannot overwrite the restored layout later.
- No change to QTX/CPX parsers, colourimetry, 555, library core, 3D, Excel business exchange, RBAC, or the mathematical definitions of the four comparison sorts.

## Validation
1. Import adidas.qtx into a new draft; confirm 309 cards.
2. Apply any comparison sort.
3. Choose `整理方案 -> 恢复原始顺序`; the exact imported sequence should return.
4. Start Munsell, then immediately choose Restore Original Order; when Munsell eventually finishes it must not repaint the palette.
