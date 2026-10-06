# Hotfix46 - 3D Navigator / Surface Restore / Performance

## Scope
Only the 3D viewer UI/rendering layer is changed. Color science, saved data and library database semantics are unchanged.

## Changes
1. Left 3D sample navigator is pageable (24 cards/page) with Previous/Next and page indicator. Search results paginate too.
2. Removed the center floating selected-sample callout. Selection remains visible as the 3D highlight; numeric Lab/LCh data stays in the right information panel.
3. Restored reliable `表面+点` and `色域表面` by prebuilding the lightweight gamut geometry before QML binds to it. Surface rendering uses unlit vertex color for cross-RHI reliability.
4. Surface default opacity raised to 0.22 so it remains visible on the light background.
5. Dense point clouds use lower-poly smooth sphere geometry; 3000+ samples use 8x5 segments, 1400-2999 use 10x6. All samples remain rendered.
6. 3000+ sample scenes disable MSAA; smaller scenes use low MSAA. This reduces GPU cost without changing Lab coordinates.
7. Axis contrast is increased and grid contrast reduced so all three analytical axes remain easier to track.

## Protected files
`qtx_core/*`, `qtx_app/library_store.py`, `qtx_app/auth_store.py`, and `qtx_app/excel_exchange.py` are unchanged from Hotfix45.
