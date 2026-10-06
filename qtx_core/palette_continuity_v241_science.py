"""Science-guided appearance-continuity ordering V2.4.1.

This candidate is deliberately built from colour-order / colour-appearance
principles rather than customer, filename or named-colour exceptions.

Key design rules
----------------
1. Perceptual topology first: chromatic samples are assigned to a broad circular
   hue family before any nearest-neighbour path is solved. This follows the
   colour-order idea used by Munsell: hue around a neutral axis, value/lightness
   vertically, chroma radially away from neutral.
2. Near-neutral hue is treated conservatively. A sample inside the neutral core
   stays Neutral unless a same-family chromatic neighbour provides strong local
   evidence. Low chroma never inherits a non-adjacent family merely because
   CIEDE2000 is small.
3. Warm/cool opponent protection: a +b* yellow/warm tint cannot be reassigned to
   Blue, and a -b* blue/cool tint cannot be reassigned to Yellow/Orange-Brown.
4. Boundary migration is local only: a low-chroma sample may cross to an adjacent
   hue family only when its own h° is close to the shared boundary and the anchor
   is genuinely close. Non-adjacent family jumps are forbidden.
5. Once the appearance family is stable, local continuity is solved by the V2.3
   adaptive perceptual graph: CIEDE2000 is primary, reflectance RMS is secondary,
   with lightness/chroma/hue terms used as structural guards.

The algorithm contains no Adidas/khaki/customer special case. Khaki/olive/earth
colours stay away from Blue because the hue topology and opponent axes make that
cross-family move structurally invalid.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence, Any

from .models import Sample
from .palette_arrangement import _de00_lab
from .palette_continuity_v21 import (
    FAMILY_ORDER,
    _hue_distance,
    _lab_props,
    _broad_family,
    _neutral_core,
    _spectral_rms,
    _median_positive,
)

TONE_NAMES = ("Deep", "Dark", "Mid", "Light", "Pale", "VeryLight")
TONE_EDGES = (0.0, 25.0, 42.0, 60.0, 78.0, 90.0, 101.0)


@dataclass
class _Rec:
    idx: int
    sample: Sample
    L: float
    a: float
    b: float
    C: float
    h: float
    family: str = ""
    native_family: str = ""
    family_reason: str = ""
    anchor_family: str = ""


def _tone_name(L: float) -> str:
    for i in range(len(TONE_EDGES) - 1):
        if TONE_EDGES[i] <= L < TONE_EDGES[i + 1]:
            return TONE_NAMES[i]
    return TONE_NAMES[-1]


def _chroma_name(C: float, neutral: bool = False) -> str:
    if neutral:
        return "Neutral"
    if C < 10.0:
        return "Muted"
    if C < 30.0:
        return "Normal"
    return "Vivid"


def _hue_reliability(C: float) -> float:
    """Continuous hue trust instead of another hard C* bucket.

    At very low chroma hue is unstable; as chroma grows, hue becomes useful.
    The smooth weighting avoids creating another arbitrary bin boundary.
    """
    C = max(0.0, float(C))
    return C / (C + 8.0)




# Broad-family topology mirrors FAMILY_ORDER except that Neutral is not on the
# circular hue wheel.  This is a structural guard, not a named-colour rule.
_HUE_FAMILIES = tuple(f for f in FAMILY_ORDER if f != "Neutral")
_FAMILY_EDGES = {
    "Red": ((330.0, 360.0), (0.0, 22.5)),
    "Orange/Brown": ((22.5, 67.5),),
    "Yellow": ((67.5, 110.0),),
    "Green": ((110.0, 190.0),),
    "Blue": ((190.0, 285.0),),
    "Violet": ((285.0, 330.0),),
}


def _smoothstep01(x: float) -> float:
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def _neutral_limit(L: float) -> float:
    """Conservative lightness-aware neutral radius in CIELAB C*.

    It deliberately stays much narrower than PAC V24. Near white the radius
    grows only modestly; in deep colours it does not swallow visibly tinted
    navy/olive samples.  Samples just outside this radius are *ambiguous*, not
    automatically neutralised.
    """
    L = max(0.0, min(100.0, float(L)))
    high = 1.9 * _smoothstep01((L - 88.0) / 10.0)
    low = 0.9 * _smoothstep01((16.0 - L) / 10.0)
    return 3.4 + high + low


def _neutral_confidence(L: float, C: float) -> float:
    limit = _neutral_limit(L)
    # Fade across a narrow transition zone rather than a wide hard bucket.
    return max(0.0, min(1.0, 1.0 - (float(C) - limit) / 2.4))


def _family_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if a not in _HUE_FAMILIES or b not in _HUE_FAMILIES:
        return 99
    ia, ib = _HUE_FAMILIES.index(a), _HUE_FAMILIES.index(b)
    d = abs(ia - ib)
    return min(d, len(_HUE_FAMILIES) - d)


def _boundary_distance(h: float, family: str) -> float:
    """Angular distance to the nearest edge of the sample's native family."""
    h = float(h) % 360.0
    edges = []
    for lo, hi in _FAMILY_EDGES.get(family, ()):
        edges.extend((lo % 360.0, hi % 360.0))
    if not edges:
        return 180.0
    return min(_hue_distance(h, e) for e in edges)


def _opponent_side(r: _Rec) -> str:
    """Coarse CIELAB yellow-blue opponent sign used only as a veto guard."""
    # Do not claim a warm/cool direction when the b* signal is tiny.
    rail = max(1.8, 0.22 * max(0.0, r.C))
    if r.b >= rail:
        return "warm"
    if r.b <= -rail:
        return "cool"
    return "weak"


def _opponent_compatible(a: _Rec, b: _Rec) -> bool:
    sa, sb = _opponent_side(a), _opponent_side(b)
    return not ({sa, sb} == {"warm", "cool"})


def _is_strong_anchor(r: _Rec) -> bool:
    return r.family != "Neutral" and r.C >= max(8.0, _neutral_limit(r.L) + 3.0)


def _same_family_rescue(r: _Rec, anchors: list[int], recs: list[_Rec], de) -> int | None:
    """Find strong same-native-family support for an edge-of-neutral tint."""
    cands = [
        j for j in anchors
        if recs[j].native_family == r.native_family
        and abs(recs[j].L - r.L) <= 13.0
        and _opponent_compatible(r, recs[j])
    ]
    if not cands:
        return None
    j = min(cands, key=lambda x: (de[r.idx][x], abs(recs[x].L-r.L), x))
    return j if de[r.idx][j] <= 4.8 else None

def _pair_matrices(recs: list[_Rec]):
    n = len(recs)
    de = [[0.0] * n for _ in range(n)]
    sp = [[float("nan")] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = float(_de00_lab(recs[i].sample.lab_d65_10, recs[j].sample.lab_d65_10))
            s = _spectral_rms(recs[i].sample, recs[j].sample)
            de[i][j] = de[j][i] = d
            sp[i][j] = sp[j][i] = s
    return de, sp


def _assign_families(recs: list[_Rec], de: list[list[float]]) -> None:
    """Science-guided broad family assignment with topology guardrails.

    Important: CIEDE2000 chooses *within plausible appearance neighbourhoods*;
    it is never allowed to make a low-chroma sample jump across unrelated hue
    families. That is the failure mode that can place a khaki/yellow-brown tint
    among blue-grey samples in a global nearest-neighbour path.
    """
    # Pass 1: establish each sample's own hue-direction prior and a conservative
    # neutral core.  No dataset neighbour can overwrite the native family here.
    for r in recs:
        r.native_family = _broad_family(r.h)
        limit = _neutral_limit(r.L)
        if r.C <= limit:
            r.family = "Neutral"
            r.family_reason = "neutral_core"
        else:
            r.family = r.native_family
            r.family_reason = "native_hue"

    anchors = [r.idx for r in recs if _is_strong_anchor(r)]

    # Pass 2: edge-of-neutral rescue.  Only SAME native family can rescue a tint
    # out of Neutral. This preserves true pale/deep tints without allowing a
    # numerically close opposite-hue anchor to steal the sample.
    for r in recs:
        if r.family != "Neutral":
            continue
        limit = _neutral_limit(r.L)
        if r.C < max(3.2, limit - 0.75):
            continue
        j = _same_family_rescue(r, anchors, recs, de)
        if j is not None:
            r.family = r.native_family
            r.anchor_family = recs[j].family
            r.family_reason = "same_family_rescue"

    # Pass 3: validate ambiguous chromatic samples.  Anchors are restricted to
    # the same or an adjacent family on the circular hue topology and must also
    # agree on the CIELAB yellow-blue opponent direction. Non-adjacent migration
    # is structurally forbidden.
    for r in recs:
        if r.family == "Neutral" or _is_strong_anchor(r):
            continue
        limit = _neutral_limit(r.L)
        cands = [
            j for j in anchors
            if abs(recs[j].L - r.L) <= 18.0
            and _family_distance(r.native_family, recs[j].family) <= 1
            and _opponent_compatible(r, recs[j])
        ]
        if not cands:
            if r.C <= limit + 1.1:
                r.family = "Neutral"
                r.family_reason = "unsupported_neutral_edge"
            else:
                r.family_reason = "native_hue_no_anchor"
            continue

        j = min(cands, key=lambda x: (de[r.idx][x], abs(recs[x].L-r.L), x))
        r.anchor_family = recs[j].family
        anchor = recs[j]
        if anchor.family == r.native_family:
            r.family_reason = "native_hue_supported"
            continue

        # Adjacent-family migration is only allowed very near the shared family
        # boundary. Away from a boundary, own hue direction wins even if ΔE00
        # to an adjacent anchor is slightly smaller.
        if _boundary_distance(r.h, r.native_family) <= 7.0 and de[r.idx][j] <= 5.5:
            r.family = anchor.family
            r.family_reason = "adjacent_boundary_bridge"
        elif r.C <= limit + 0.7 and de[r.idx][j] > 7.0:
            r.family = "Neutral"
            r.family_reason = "weak_transition_neutral"
        else:
            r.family_reason = "native_hue_guarded"

def _family_scales(indices: list[int], recs: list[_Rec], de, sp):
    de_vals: list[float] = []
    sp_vals: list[float] = []
    dL_vals: list[float] = []
    dC_vals: list[float] = []
    dh_vals: list[float] = []
    for p, a in enumerate(indices):
        for b in indices[p + 1 :]:
            de_vals.append(de[a][b])
            if math.isfinite(sp[a][b]):
                sp_vals.append(sp[a][b])
            dL_vals.append(abs(recs[a].L - recs[b].L))
            dC_vals.append(abs(recs[a].C - recs[b].C))
            rel = min(_hue_reliability(recs[a].C), _hue_reliability(recs[b].C))
            dh_vals.append(_hue_distance(recs[a].h, recs[b].h) * rel)
    return {
        "de": _median_positive(de_vals, 1.0),
        "sp": _median_positive(sp_vals, 1.0),
        "dL": _median_positive(dL_vals, 12.0),
        "dC": _median_positive(dC_vals, 10.0),
        "dh": _median_positive(dh_vals, 20.0),
    }


def _graph_score(a: int, b: int, family: str, recs: list[_Rec], de, sp, scales) -> float:
    dn = de[a][b] / max(scales["de"], 1e-9)
    sn = (sp[a][b] / max(scales["sp"], 1e-9)) if math.isfinite(sp[a][b]) else dn
    dL = abs(recs[a].L - recs[b].L) / max(scales["dL"], 1e-9)
    dC = abs(recs[a].C - recs[b].C) / max(scales["dC"], 1e-9)
    if family == "Neutral":
        # Neutral is fundamentally a lightness axis. Hue is deliberately absent.
        return 0.70 * dn + 0.08 * sn + 0.18 * dL + 0.04 * dC
    rel = min(_hue_reliability(recs[a].C), _hue_reliability(recs[b].C))
    dh = (_hue_distance(recs[a].h, recs[b].h) * rel) / max(scales["dh"], 1e-9)
    return 0.66 * dn + 0.08 * sn + 0.12 * dL + 0.06 * dC + 0.08 * dh


def _mst(indices: list[int], family: str, recs: list[_Rec], de, sp):
    """Deterministic O(n²) Prim MST; dataset sizes here are small enough."""
    if len(indices) <= 1:
        return []
    scales = _family_scales(indices, recs, de, sp)
    start = min(indices, key=lambda i: (recs[i].L, recs[i].C, recs[i].sample.display_name.casefold()))
    tree = {start}
    best: dict[int, tuple[float, int]] = {}
    for j in indices:
        if j != start:
            best[j] = (_graph_score(start, j, family, recs, de, sp, scales), start)
    edges = []
    while len(tree) < len(indices):
        v, (score, u) = min(best.items(), key=lambda kv: (kv[1][0], de[kv[1][1]][kv[0]], kv[0]))
        edges.append((u, v, score))
        tree.add(v)
        del best[v]
        for j in list(best):
            s = _graph_score(v, j, family, recs, de, sp, scales)
            old_s, old_u = best[j]
            if (s, de[v][j], v) < (old_s, de[old_u][j], old_u):
                best[j] = (s, v)
    return edges


def _adaptive_nn_scale(indices: list[int], de) -> float:
    nn = []
    for a in indices:
        vals = [de[a][b] for b in indices if b != a]
        if vals:
            nn.append(min(vals))
    return statistics.median(nn) if nn else 3.0


def _structural_thresholds(local_nn: float) -> tuple[float, float]:
    """Return the exact adaptive soft/hard ΔE00 rails used by V2.3.

    Kept in one function so the sorter and the diagnostic report cannot drift.
    """
    de_soft = min(10.5, max(7.0, float(local_nn) * 3.2))
    de_hard = 11.5
    return de_soft, de_hard


def _true_structural_jump(a: int, b: int, family: str, recs: list[_Rec], de_val: float, local_nn: float) -> bool:
    """A row/cluster break needs both perceptual size and structural evidence.

    This is the main V2.3 change: a ΔE00 of ~7-8 by itself no longer forces a
    break when two colours still form a coherent hue/tone transition.
    """
    dL = abs(recs[a].L - recs[b].L)
    dC = abs(recs[a].C - recs[b].C)
    de_soft, de_hard = _structural_thresholds(local_nn)
    if de_val >= de_hard:
        return True
    if de_val < de_soft:
        return False
    if family == "Neutral":
        return dL > 17.0 or dC > 10.0
    rel = min(_hue_reliability(recs[a].C), _hue_reliability(recs[b].C))
    hue_jump = _hue_distance(recs[a].h, recs[b].h) * rel
    return hue_jump > 28.0 or dL > 24.0 or dC > 20.0


def _components(indices: list[int], kept_edges: list[tuple[int, int, float]]) -> list[list[int]]:
    adj = {i: [] for i in indices}
    for a, b, _ in kept_edges:
        adj[a].append(b)
        adj[b].append(a)
    out = []
    seen = set()
    for s in indices:
        if s in seen:
            continue
        stack = [s]
        seen.add(s)
        comp = []
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        out.append(comp)
    return out


def _cluster_medoid(cluster: list[int], de) -> int:
    if len(cluster) == 1:
        return cluster[0]
    return min(cluster, key=lambda a: (sum(de[a][b] for b in cluster), a))


def _cluster_stats(cluster: list[int], recs: list[_Rec]):
    return {
        "L": statistics.median(recs[i].L for i in cluster),
        "C": statistics.median(recs[i].C for i in cluster),
        "h": statistics.median(recs[i].h for i in cluster),
    }


def _hue_compatible(c1: list[int], c2: list[int], family: str, recs: list[_Rec]) -> bool:
    if family == "Neutral":
        s1, s2 = _cluster_stats(c1, recs), _cluster_stats(c2, recs)
        return abs(s1["L"] - s2["L"]) <= 22.0
    s1, s2 = _cluster_stats(c1, recs), _cluster_stats(c2, recs)
    rel = min(_hue_reliability(s1["C"]), _hue_reliability(s2["C"]))
    return _hue_distance(s1["h"], s2["h"]) * rel <= 35.0


def _rescue_orphans(clusters: list[list[int]], family: str, recs: list[_Rec], de, local_nn: float):
    clusters = [list(c) for c in clusters]
    # Merge only genuine 1-2 item islands; never force a distant merge.
    merge_limit = min(8.5, max(5.5, local_nn * 2.7))
    changed = True
    while changed:
        changed = False
        for c in sorted(list(clusters), key=lambda x: (len(x), min(x))):
            if c not in clusters or len(c) > 2 or len(clusters) <= 1:
                continue
            candidates = []
            for t in clusters:
                if t is c or not _hue_compatible(c, t, family, recs):
                    continue
                md = min(de[a][b] for a in c for b in t)
                candidates.append((md, -len(t), t))
            if candidates:
                md, _, target = min(candidates, key=lambda x: (x[0], x[1], min(x[2])))
                if md <= merge_limit:
                    target.extend(c)
                    clusters.remove(c)
                    changed = True
                    break
    return clusters


def _build_family_clusters(indices: list[int], family: str, recs: list[_Rec], de, sp):
    if not indices:
        return []
    if len(indices) == 1:
        return [list(indices)]
    local_nn = _adaptive_nn_scale(indices, de)
    mst_edges = _mst(indices, family, recs, de, sp)
    kept = []
    for a, b, s in mst_edges:
        if not _true_structural_jump(a, b, family, recs, de[a][b], local_nn):
            kept.append((a, b, s))
    clusters = _components(indices, kept)
    clusters = _rescue_orphans(clusters, family, recs, de, local_nn)
    return clusters


def _path_score(indices: list[int], family: str, recs: list[_Rec], de, sp):
    de_vals = []
    sp_vals = []
    for p, a in enumerate(indices):
        for b in indices[p + 1 :]:
            de_vals.append(de[a][b])
            if math.isfinite(sp[a][b]):
                sp_vals.append(sp[a][b])
    de_med = _median_positive(de_vals, 1.0)
    sp_med = _median_positive(sp_vals, 1.0)
    score = {}
    for a in indices:
        for b in indices:
            if a == b:
                score[(a, b)] = 0.0
                continue
            dn = de[a][b] / de_med
            sn = (sp[a][b] / sp_med) if math.isfinite(sp[a][b]) else dn
            dL = abs(recs[a].L - recs[b].L)
            dC = abs(recs[a].C - recs[b].C)
            if family == "Neutral":
                val = 0.80 * dn + 0.05 * sn + 0.13 * (dL / 15.0) + 0.02 * (dC / 10.0)
            else:
                rel = min(_hue_reliability(recs[a].C), _hue_reliability(recs[b].C))
                dh = _hue_distance(recs[a].h, recs[b].h) * rel
                val = 0.78 * dn + 0.07 * sn + 0.07 * (dL / 18.0) + 0.03 * (dC / 18.0) + 0.05 * (dh / 35.0)
            if de[a][b] > 10.0:
                val += ((de[a][b] - 10.0) / 4.5) ** 2
            score[(a, b)] = val
    return score


def _two_opt(path, score, passes=2):
    p = list(path)
    for _ in range(passes):
        changed = False
        for i in range(len(p) - 3):
            a, b = p[i], p[i + 1]
            for k in range(i + 2, len(p) - 1):
                c, d = p[k], p[k + 1]
                if score[(a, c)] + score[(b, d)] + 1e-12 < score[(a, b)] + score[(c, d)]:
                    p[i + 1 : k + 1] = reversed(p[i + 1 : k + 1])
                    changed = True
        if not changed:
            break
    return p


def _cluster_path(cluster: list[int], family: str, recs: list[_Rec], de, sp) -> list[int]:
    if len(cluster) <= 1:
        return list(cluster)
    if family == "Neutral":
        # Stable neutral axis; tiny local reversals are not useful visually.
        return sorted(cluster, key=lambda i: (recs[i].L, recs[i].C, recs[i].sample.display_name.casefold()))
    score = _path_score(cluster, family, recs, de, sp)

    def greedy(start):
        rem = set(cluster)
        rem.remove(start)
        out = [start]
        while rem:
            cur = out[-1]
            nxt = min(
                rem,
                key=lambda j: (
                    score[(cur, j)],
                    de[cur][j],
                    abs(recs[cur].L - recs[j].L),
                    recs[j].sample.display_name.casefold(),
                ),
            )
            out.append(nxt)
            rem.remove(nxt)
        return out

    starts = {
        min(cluster, key=lambda i: recs[i].L),
        max(cluster, key=lambda i: recs[i].L),
        min(cluster, key=lambda i: recs[i].C),
        max(cluster, key=lambda i: recs[i].C),
        _cluster_medoid(cluster, de),
    }
    best = None
    best_cost = float("inf")
    for st in list(starts):
        p = _two_opt(greedy(st), score, 2)
        c = sum(score[(a, b)] for a, b in zip(p, p[1:]))
        if c < best_cost:
            best, best_cost = p, c
    return best or list(cluster)


def _unwrap_family_h(h: float, family: str) -> float:
    h = float(h) % 360.0
    if family == "Red" and h >= 330.0:
        return h - 360.0
    return h


def _order_clusters(clusters: list[list[int]], family: str, recs: list[_Rec], de) -> list[list[int]]:
    if len(clusters) <= 1:
        return clusters
    if family == "Neutral":
        return sorted(clusters, key=lambda c: (_cluster_stats(c, recs)["L"], _cluster_stats(c, recs)["C"], min(c)))

    # Preserve broad hue progression, then tone.  This prevents an otherwise
    # low-cost graph path from bouncing from sky-blue to navy and back again.
    def key(c):
        st = _cluster_stats(c, recs)
        return (_unwrap_family_h(st["h"], family), st["L"], st["C"], min(c))

    return sorted(clusters, key=key)


def _cluster_label(cluster: list[int], family: str, recs: list[_Rec]):
    L = statistics.median(recs[i].L for i in cluster)
    C = statistics.median(recs[i].C for i in cluster)
    return _tone_name(L), _chroma_name(C, family == "Neutral")


def appearance_continuity_layout_v241_science(samples: Sequence[Sample], columns: int = 6) -> dict[str, Any]:
    samples = list(samples)
    columns = max(1, int(columns))
    recs: list[_Rec] = []
    for i, sm in enumerate(samples):
        L, a, b, C, h = _lab_props(sm)
        recs.append(_Rec(i, sm, L, a, b, C, h))
    if not recs:
        return {"order": [], "grid": [], "groups": [], "breaks": [], "info": {}, "columns": columns}

    de, sp = _pair_matrices(recs)
    _assign_families(recs, de)
    families = {f: [] for f in FAMILY_ORDER}
    for r in recs:
        families[r.family].append(r.idx)

    family_clusters: dict[str, list[list[int]]] = {}
    for fam in FAMILY_ORDER:
        cls = _build_family_clusters(families[fam], fam, recs, de, sp)
        family_clusters[fam] = _order_clusters(cls, fam, recs, de)

    grid: list[list[Sample | None]] = []
    row: list[Sample | None] = []
    order: list[Sample] = []
    groups = []
    breaks = []
    prev_idx: int | None = None
    prev_family: str | None = None

    for fam in FAMILY_ORDER:
        clusters = family_clusters[fam]
        if not clusters:
            continue
        local_nn = _adaptive_nn_scale(families[fam], de) if len(families[fam]) > 1 else 3.0
        for ci, cluster in enumerate(clusters):
            path = _cluster_path(cluster, fam, recs, de, sp)
            if not path:
                continue

            # Family boundary always starts a fresh row.  Cluster boundaries do
            # not: only a true appearance discontinuity gets a blank remainder.
            if row and prev_family is not None and fam != prev_family:
                row += [None] * (columns - len(row))
                grid.append(row)
                row = []
                if prev_idx is not None:
                    breaks.append({
                        "from": prev_idx,
                        "to": path[0],
                        "de00": de[prev_idx][path[0]],
                        "reason": "family_boundary",
                    })
            elif row and prev_idx is not None:
                # Reverse a cluster if its other endpoint is a materially better
                # bridge to the current row; this keeps the cluster intact.
                if len(path) > 1 and de[prev_idx][path[-1]] + 0.35 < de[prev_idx][path[0]]:
                    path = list(reversed(path))
                if _true_structural_jump(prev_idx, path[0], fam, recs, de[prev_idx][path[0]], local_nn):
                    row += [None] * (columns - len(row))
                    grid.append(row)
                    row = []
                    breaks.append({
                        "from": prev_idx,
                        "to": path[0],
                        "de00": de[prev_idx][path[0]],
                        "reason": "cluster_boundary_jump",
                    })

            tone, chroma = _cluster_label(path, fam, recs)
            de_soft, de_hard = _structural_thresholds(local_nn)
            groups.append({
                "family": fam,
                "tone": tone,
                "chroma": chroma,
                "size": len(path),
                "cluster": ci,
                # Backward-compatible key used by older audit code.  In V2.3
                # it means the adaptive SOFT rail; hard rail is also exposed.
                "break_threshold": de_soft,
                "break_soft": de_soft,
                "break_hard": de_hard,
                "local_nn": local_nn,
            })

            for k, idx in enumerate(path):
                if k > 0:
                    a, b = path[k - 1], idx
                    if row and _true_structural_jump(a, b, fam, recs, de[a][b], local_nn):
                        row += [None] * (columns - len(row))
                        grid.append(row)
                        row = []
                        breaks.append({"from": a, "to": b, "de00": de[a][b], "reason": "within_cluster_jump"})
                row.append(recs[idx].sample)
                order.append(recs[idx].sample)
                prev_idx = idx
                prev_family = fam
                if len(row) == columns:
                    grid.append(row)
                    row = []

    if row:
        row += [None] * (columns - len(row))
        grid.append(row)

    if len(order) != len(samples) or len({id(x) for x in order}) != len(samples):
        raise ValueError("Appearance continuity V2.4.1 science integrity failure")

    info = {
        id(r.sample): {
            "family": r.family,
            "native_family": r.native_family,
            "family_reason": r.family_reason,
            "anchor_family": r.anchor_family,
            "opponent_side": _opponent_side(r),
            "tone": _tone_name(r.L),
            "chroma": _chroma_name(r.C, r.family == "Neutral"),
            "L": r.L,
            "C": r.C,
            "h": r.h,
            "neutral_limit": _neutral_limit(r.L),
            "neutral_confidence": _neutral_confidence(r.L, r.C),
            "hue_confidence": _hue_reliability(r.C),
        }
        for r in recs
    }
    cross_family_anchor_risk = sum(
        1 for r in recs
        if r.anchor_family and _family_distance(r.native_family, r.anchor_family) > 1
    )
    warm_cool_inversion_risk = sum(
        1 for r in recs
        if (r.b >= 2.0 and r.family == "Blue")
        or (r.b <= -2.0 and r.family in {"Yellow", "Orange/Brown"})
    )
    guarded_family_changes = sum(
        1 for r in recs if r.family not in {r.native_family, "Neutral"}
    )
    slots = max(1, len(grid) * columns)
    return {
        "order": order,
        "grid": grid,
        "groups": groups,
        "breaks": breaks,
        "info": info,
        "columns": columns,
        "compactness": len(samples) / slots,
        "extra_slots": slots - len(samples),
        "cross_family_anchor_risk": cross_family_anchor_risk,
        "warm_cool_inversion_risk": warm_cool_inversion_risk,
        "guarded_family_changes": guarded_family_changes,
    }
