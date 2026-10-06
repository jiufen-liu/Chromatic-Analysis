# Hotfix84 · Palette Workspace UX P1

Scope: productise the already validated Visual Palette V2.4.2 Core and simplify the Palette Workspace UI without changing measured colour data or existing file semantics.

## Product changes
- Add visible Palette Workspace command bar: Import, Smart Appearance Arrangement, Sort, Restore Original, Undo/Redo, Save, Export.
- Wire `visual_palette_layout_v242` into the real palette editor as a one-shot 2-D layout generator.
- Smart arrangement never changes Lab/reflectance/sample identity and never re-runs automatically when a saved scheme is reopened.
- Users may freely drag cards after automatic arrangement, save the edited layout, undo, redo, and restore the original session slots.
- Primary sort menu is reduced to Name / L* / C*.
- a* / b* / Munsell Hue move to Advanced / Colour Data.
- Spectral RMS and Spectral+Perceptual are removed from the normal Palette Workspace UI; their implementation remains available for diagnostics/research and future Color Performance work.
- Experimental Visual Palette V1 is removed from the normal Palette Workspace UI; code remains for historical comparison.
- Restore Original now restores original slot geometry (including blanks), which is required after a true 2-D smart layout.

## Explicitly unchanged
- V2.4.2 algorithm implementation in `qtx_core/palette_arrangement.py`.
- QTX / CPX parsing and export science/data.
- 555 implementation.
- Colourimetry and ΔE calculations.
- 2D/3D analysis.
- Formal colour-library / RBAC behaviour.
- Existing cross-window drag/copy semantics.
