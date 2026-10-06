# Hotfix54 · P3-6 · QTX Batch Parser Fast Path

## Why this exists
The Hotfix53 real-world baseline exposed the remaining P3 bottleneck: Coloro
3500 (1.6 MiB / 3500 samples) required 12.66868 seconds to parse on the user's
Windows/Python 3.14.5 environment. UI virtualization was no longer the dominant
cost; spectrum-only QTX records were being converted one-by-one through ASTM
E308.

## New fast path
Spectrum-only records are first parsed normally and grouped by their exact
wavelength tuple. A group of 16 or more curves is passed to colour-science as a
MultiSpectralDistributions object and converted with method="ASTM E308" in one
batch. XYZ-to-Lab is then applied to the whole XYZ array.

The raw-array shortcut is deliberately not used. Hotfix54 uses the
MultiSpectralDistributions path so the spectral distributions retain the
precision-oriented alignment behaviour of colour-science.

## Numerical safety
The previous scalar reflectance_to_xyz_lab() implementation remains the
reference algorithm. For every batch group, first/middle/last outputs are
checked against the scalar reference. If XYZ or Lab differs beyond tight
floating-point tolerance, that group is discarded and all of its samples are
recomputed through the old scalar path.

Therefore Hotfix54 is a fast path with automatic safe fallback, not a new colour
formula.

## Diagnostics
`tools/performance_baseline.py` now prints parser internals:
- block parse
- sample prepare
- colour science + sample build
- batch sample/group count
- scalar sample count
- fallback groups
- verification failures

For troubleshooting only, set environment variable:
`CHROMATIC_DISABLE_QTX_BATCH=1`
to force the Hotfix53-style scalar path.
