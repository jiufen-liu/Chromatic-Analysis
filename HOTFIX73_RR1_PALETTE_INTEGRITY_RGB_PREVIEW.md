# Hotfix73 · Palette Integrity & RGB Preview

## Scope
Release Readiness R1 bug-fix only. No 555, colour-difference, QTX parser, library core, 3D, RBAC, or existing sort science definitions are changed.

## Fixes
1. **Palette import duplicate-measurement preservation**
   - QTX/CPX/Excel records that repeat the same source GUID/sample_id are no longer silently collapsed in Palette Studio.
   - Occurrence 1 keeps the historical key; occurrence 2+ receive an internal `__palette_instance` marker.
   - Internal markers are excluded from QTX export metadata, so source GUID/sample_id values remain unchanged.
2. **Export integrity gate**
   - QTX/CPX/Excel palette export now verifies that every occupied visible slot resolves to a real Sample.
   - Export is cancelled with a warning instead of silently writing fewer colours than the palette shows.
3. **WorkspaceCardList drag-hover crash**
   - QListView now uses `indexAt()/visualRect()`; QListWidget keeps `itemAt()/visualItemRect()`.
4. **RGB swatch preview v2**
   - For samples with a spectrum, display Lab is recalculated from reflectance at D65/2° before mapping to sRGB.
   - This aligns the preview conversion with the sRGB reference condition and retains the existing constant-hue/lightness gamut compression.
   - Display only: measured spectra/Lab, ΔE, sorting, exports and stored data are untouched.

## Important appearance limitation
A flat sRGB swatch cannot reproduce textile/plastic surface texture, gloss, SCI/SCE appearance, fluorescence energy or colours outside the monitor gamut. It is a colorimetric screen preview, not a digital twin of the physical sample.
