# Hotfix78 · Visual Palette V2.1 Offline Audit

Scope is diagnostic-only. Production Palette Studio ordering remains unchanged from HF76/HF77.

V2.1 changes:
- preserve 12-family CIELCh hue skeleton;
- adaptive lightness shelves instead of fixed 10-L* bins;
- monotonic hue ordering inside each shelf;
- soften off-white routing around L*=90;
- split White / Neutral Grey / Black;
- add cross-family conflict audit for pairs with ΔE00 < 3;
- ignore neutral hue-angle jumps as a quality metric.

Use RUN_DIAGNOSTICS.bat -> [8].
