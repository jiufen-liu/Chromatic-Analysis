# Hotfix120 — Palette virtualization visibility

## Symptom
Large QTX imports (for example 660 or 3500 samples) finish successfully and the palette count is correct, but the palette canvas is blank.

## Root cause
HF118/HF119 virtualized palettes above 240 slots by creating `ColorCardCell` widgets only for visible rows. The same optimization also skipped `QListWidgetItem.setSizeHint()` for large palettes. In `QListWidget.IconMode` with empty-text items on Windows/PySide6, the item geometry can therefore be empty/invalid even though `gridSize` is set. The virtualized widgets are then attached to slots that have no usable visual rectangle.

## Fix
- Keep virtualization: only visible colour-card QWidget objects are created.
- Always assign the lightweight slot size hint to every `QListWidgetItem`.
- Reset materialized-row bookkeeping whenever the list model is cleared/rebuilt.
- Schedule one post-layout visible-row refresh after Qt propagates the fixed canvas geometry.

## Expected result
- 660/3500 samples still use virtualized card widgets.
- The first visible colour cards appear immediately after import.
- Scrolling materializes only nearby rows.
- Search, selection, sorting, export, and saved slot coordinates are unchanged.
