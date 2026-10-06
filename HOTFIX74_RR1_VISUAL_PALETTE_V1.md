# Hotfix74 · Visual Palette Arrangement V1

## Scope
Experimental Palette Studio arrangement only. Existing Munsell, spectral RMS, spectral+perceptual, 555, QTX/CPX parser, colour-difference, library, 3D, RBAC and export science are not replaced.

## Visual Palette Arrangement V1
- Adds `实验性 · 视觉色貌编排 V1` under Palette Studio → 整理方案.
- Uses 8 broad CIELAB hue families as a colour-wheel skeleton.
- Within each hue family, L* is monotonic to avoid dark→pale→dark oscillation.
- Very light low-chroma whites, very dark low-chroma blacks and C*<=8 near-neutrals are removed from hue-angle sorting because hue is unstable near the neutral axis.
- The direction of each hue family is chosen from the two legal monotonic-L* directions by the smaller boundary ΔE*ab, reducing large family-boundary jumps without a greedy nearest-neighbour path.
- O(n log n); intended to be effectively instant for hundreds of swatches.
- It is a layout heuristic, not a standard colour-difference formula and not a replacement for Munsell.

## Diagnostics
The integrated sort science audit now includes a `视觉色貌编排_V1` worksheet so it can be compared against Munsell, spectral RMS and spectral+perceptual paths on the same QTX.

## Safety / integrity
- Every input palette key must appear exactly once in the V1 result; otherwise the operation aborts.
- A newly requested visual arrangement invalidates any still-running heavy sort result, so an older Munsell/spectral job cannot overwrite it later.
- Ctrl+Z restores the pre-arrangement layout.
