# Hotfix91 · Palette Continuity V2.1 Structural Audit

This hotfix is an **offline validation gate**. It does not replace the formal Palette Studio sort yet.

## Candidate architecture
1. Broad appearance families (Red / Orange-Brown / Yellow / Green / Blue / Violet / Neutral).
2. Low-chroma hue confidence guard: weak tints inherit a nearby chromatic family only when ΔE00 supports it; otherwise Neutral.
3. Family-internal subgroups by L* tone and C* level.
4. Tiny-island rehoming only within a compatible family and ΔE00 threshold.
5. Subgroup path: CIEDE2000 85% + normalized spectral RMS 15%, with a hard perceptual penalty above ΔE00 10.
6. Bounded 2-opt removes greedy end traps.
7. Adaptive local jump detection; large jumps start a new row instead of forcing adjacency.
8. Six-column layout treats columns as a maximum; blanks are intentional editable slots.

No customer names, colour names, or specific QTX IDs are encoded in the algorithm.

## Test
RUN_DIAGNOSTICS.bat -> 16. Test either one QTX or a folder of QTX files.
Review the batch CSV plus representative PNG/TXT reports. Focus on sky/light blue, navy/deep blue, black/grey, pink, and khaki/beige grouping across different files.
