# Hotfix39 — 3D Interaction & Visual Spec

This release treats the user-approved HF39 mockup as an implementation specification.

## Implemented

- Qt Quick 3D / RHI remains the primary 3D renderer. No legacy black 3D window is reintroduced.
- Softer display-only 3D colour rendering. Measured Lab coordinates and original sample colours remain untouched.
- Smaller, semi-transparent, studio-lit point cloud to reduce the “solid lump” appearance.
- Functional top coordinate tabs:
  - CIELAB 空间: interactive 3D colour space.
  - L*a*b*: real Cartesian a*b* scatter plus L* position scale.
  - L*C*h°: real hue/chroma polar plot plus L* position scale.
- Unified selection model across left cards, 3D points, Lab view and LCh view.
- Clicking a left colour card highlights the exact same Lab point in 3D with a blue halo and floating data callout.
- Clicking a 3D instance updates the left selection and right information panel.
- Right information panel shows Lab and LCh simultaneously.
- Controls implemented: point size, point opacity, surface opacity, axes, a*b* reference grid, labels, perspective/front/side/top/bottom, auto rotate, reset.
- 3D viewport mini toolbar: rotate step, zoom in, zoom out, home/reset.
- Search scans the full 3D sample set and shows the first 24 matching cards without creating thousands of QWidget cards.

## Data integrity

The following remain unchanged:
- qtx_core colour science algorithms
- QTX / CPX / Excel parsing
- Lab / XYZ / CMC / DE2000 / MI / 555 calculations
- SQLite library paging and lightweight index

The 3D softening is display-only and never modifies measured sample Lab values.
