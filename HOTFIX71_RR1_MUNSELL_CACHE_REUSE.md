# Hotfix71 · Release Readiness R1 · Munsell Cache Reuse

## Scope
- Preserve HF70 Munsell science correction unchanged.
- Reuse exact Munsell results already present in `MainWindow._hue_order_cache` when the MDI color-card plan is sorted again.
- First exact Munsell sort still performs the scientific computation; subsequent forward/reverse sorts in the same application session should avoid repeating renotation for unchanged samples.
- Diagnostic menu numbering changed to padded `[1]` style to remain visible when a terminal viewport is slightly horizontally shifted.
- Palette benchmark text no longer says `HF67` for current algorithms.

## Not in scope
- No persistent disk cache yet.
- No change to Munsell C/2° science, neutral/fallback grouping, spectral path, hybrid scoring, 555, QTX parser, Excel exchange, library business data, RBAC, or 3D science.
