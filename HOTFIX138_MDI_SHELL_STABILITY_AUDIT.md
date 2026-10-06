# Hotfix138 — MDI Shell Stability Audit

Scope: UI shell only. Business/science/data paths are unchanged.

## Root cause found
HF135-HF137 installed the mature tool page directly as `QMdiSubWindow.widget()` and also manually called `page.setGeometry(...)` to reserve a custom title bar. Qt's own `QMdiSubWindow` layout was still managing the same page. On Windows the two geometry owners raced, causing:

- top rows/title content clipped or overlapped;
- windows appearing incomplete and correcting themselves later;
- the same symptom on Formal Library, Find, Palette, Compare and Spectrum;
- fragile state transitions around minimize/restore.

## HF138 correction
- `QMdiSubWindow` owns one private shell widget only.
- custom title bar + mature page are children of a normal `QVBoxLayout` in that shell.
- no manual geometry is applied to the mature page.
- window flags are set before the shell is installed.
- delayed first-open geometry callbacks are not scheduled for tool/document windows.
- taskbar visibility is driven by an explicit minimized-window registry, not by button lifetime.
- Studio taskbar has one consistent 40 px height in code and stylesheet.
- Python-level `setWidget()/widget()` compatibility is preserved for existing callers.

## Explicit non-goals
No changes to QTX/CPX/Excel formats, colorimetry, Delta-E, 555, tolerance logic, library data model, RBAC, palette sorting, 3D, spectrum calculations, exports, or frozen performance paths.

## Static validation
- project `compileall` passed;
- no top-level function/class method removed relative to HF137;
- no delayed first-open geometry call remains for tool/document windows;
- no manual `page.setGeometry(...)` remains in StudioToolSubWindow;
- ZIP integrity checked after packaging.
