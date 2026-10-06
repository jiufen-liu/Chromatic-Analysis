# Hotfix93 · Palette Continuity V2.3 Graph Cluster Audit

Diagnostic-only candidate. Formal Palette Studio behaviour remains frozen.

Why V2.3 exists
---------------
Multi-QTX validation of V2.2 confirmed that removing hard L*/C* buckets reduced
fragmentation, but it also exposed two remaining structural issues:

1. Some datasets still generated too many row breaks / empty slots because a
   moderately large ΔE00 jump alone could split an otherwise coherent tone/hue
   transition.
2. Sparse datasets still contained a few singleton/micro-islands, while broader
   datasets needed light-blue, navy, black/grey, pink and khaki families to stay
   together without brand- or file-specific rules.

V2.3 is dataset-agnostic:

* broad hue families remain the only hard skeleton;
* hue trust is continuous with chroma, not another hard C* bin;
* inside each family an MST over a perceptual graph finds natural neighbourhoods;
* MST edges are cut only when ΔE00 is large AND the hue/tone/chroma structure is
  also inconsistent (or when an absolute safety ceiling is exceeded);
* 1–2 item islands are re-homed only when genuinely close to a compatible cluster;
* cluster order preserves broad hue progression, Neutral follows L*;
* spectrum is secondary to appearance;
* row breaks occur only at broad-family boundaries or true structural jumps.

No customer, brand, colour name, QTX filename or product-specific special case is
present.  This hotfix only adds diagnostic candidate code + menu item [18].
