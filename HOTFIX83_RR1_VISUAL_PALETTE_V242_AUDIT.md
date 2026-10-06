# Hotfix83 · Visual Palette V2.4.2 Offline Audit

Scope: audit-only. Formal Palette Studio behaviour remains frozen.

## Reason
V2.4.1 left one validated near-white semantic split in the Chongming dataset:
`CTS-007 WHITE` remained Blue-Violet while `CTS-007 WHITE swatch` was promoted to Neutral Field.
Their ΔE00 is ~1.598. The swatch is ΔE00 ~2.002 from an original Tinted White seed, while CTS-007 WHITE is ~3.212 from that original seed.

## V2.4.2 rule
Keep V2.4.1 Stage-1 exactly unchanged. Add one and only one Stage-2 continuity hop:
- candidate L* >= 90 and C* <= 18
- candidate to a Stage-1 promoted bridge: ΔE00 < 3
- candidate to the original Tinted White seed: ΔE00 <= 4
- candidate hue direction within 15° of the original seed
- Stage-2 bridges cannot recruit any further sample

This prevents unrestricted chain propagation while allowing a visually continuous near-white cluster to remain together.

## Validation target
Use RUN_DIAGNOSTICS.bat option [13] on the same multi-QTX batch used for V2.4.1.
Review Stage-1 / Stage-2 promotion counts and verify semantic conflicts remain zero without broad new absorption.
