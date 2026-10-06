# Hotfix107 · LABCH Semantic Reset

## Why this reset
Hotfix106 tried to make h° visually pretty by silently changing it into:
hue family -> exact L* -> C*. That made the menu label “h°” misleading and still did not satisfy visual continuity.

## LABCH h° now
- LABCH means coordinate sorting.
- Chromatic samples are sorted strictly by CIELAB h° from 0° to 360°.
- Near-neutral samples are removed from h° because hue angle is unstable near the neutral axis.
- Neutral samples are appended as one white/grey/black block, ordered by L*.
- No hidden ΔE optimisation is applied to LABCH h°.

## Color Difference
- UI label is “基准色差距离”.
- It is a strict radial distance sort from one selected reference colour.
- Equal/similar ΔE values may belong to very different hue directions; visual irregularity is expected.

## Running ΔE
- Unchanged.
- This is the dedicated visual-continuity tool because each next colour is chosen from the current colour.

## LABC Atlas
- Unchanged.
- It remains the professional hue/lightness/chroma organisation system inspired by atlas-style colour organisation.
