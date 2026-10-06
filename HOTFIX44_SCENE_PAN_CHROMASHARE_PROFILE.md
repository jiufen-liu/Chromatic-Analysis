# Hotfix44 · Scene Pan / ChromaShare Rendering Profile

## 1. Right-button scene pan
- Right drag now translates the full 3D scene instead of moving the camera.
- Data points, gamut surface, grid, axis lines, selected marker and six axis labels move as one analytical coordinate system.
- Axis labels are no longer clamped/pinned to viewport edges; they leave/enter the viewport with their real 3D endpoints.
- Left drag remains orbit, wheel remains zoom, double-click remains reset.

## 2. ChromaShare Rendering Profile v2
- Reference: user-supplied ChromaShare Lab Space Graph video.
- The largest visual gap was marker brightness, not saturation.
- 3D-only Lab-aware display mapping now raises medium/dark marker value while preserving hue family and measured L* ordering.
- Point material changed from darker PBR PrincipledMaterial to soft FragmentLighting DefaultMaterial, closer to classic professional colour-space renderers.
- Three broad fill lights were rebalanced to keep back-facing spheres coloured instead of muddy/black.
- Neutral warm-gray background retained for colour contrast.
- No change to measured Lab/spectrum, library swatch colour, CMC/DE2000/MI/555 or saved payloads.
