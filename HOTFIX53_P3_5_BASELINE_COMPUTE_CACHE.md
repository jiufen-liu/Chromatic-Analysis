# Hotfix53 — Performance P3-5 Baseline & Compute Cache

## Scope

Performance-only closeout of the P3 programme. No UI redesign, no 3D changes, no permission/RBAC changes and no formula changes.

## Changes

1. Shared bounded spectral compute cache around the existing ASTM E308 `reflectance_to_xyz_lab()` path.
2. Cache key includes the complete reflectance curve, wavelength grid, canonical illuminant and observer; stale results cannot survive a measurement change.
3. Aggregate performance statistics are collected for existing profiled hot paths and written to `performance_summary.json` at normal exit.
4. Existing `performance.log` remains available for individual slow calls.
5. Background import / maintenance executors cancel queued work on application shutdown; the currently executing Python parse is allowed to finish safely rather than being force-killed.
6. Added reproducible `RUN_P3_5_BENCHMARK.bat` and `tools/performance_baseline.py`.

## Explicitly unchanged

- QTX/CPX parsing formulas and supported production variants.
- CIELAB / XYZ / CMC / CIE94 / CIEDE2000 / MI / 555 formulas.
- P3-1 SQLite pagination.
- P3-2 lightweight library index.
- P3-3 lazy spectrum hydration.
- P3-4 virtual workspace.
- Quick3D/QML renderer and all 3D interaction.
- RBAC and UI layout.

## Validation note

The build environment used to prepare this package does not include the project runtime dependencies (`colour-science`, `PySide6`), so full numerical/GUI tests cannot be executed here. Python source is syntax-compiled and the new cache test is included for execution in the project `.venv` / user Windows environment.
