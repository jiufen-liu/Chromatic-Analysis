"""Appearance-continuity layout V2.4 (diagnostic candidate: Neutral Confidence + Hue Confidence).

Goal
----
Preserve the structural gains of V2.2 (broad hue families, no customer/name
special-cases, perception first, spectrum secondary) while fixing two issues
found by multi-QTX validation:

* V2.1 over-segmented because of fixed L*/C* buckets.
* V2.2 sometimes over-broke rows because every locally large ΔE00 jump became a
  segment boundary, even when the hue/tone transition was visually coherent.

V2.4 keeps the V2.3 graph/MST structure and adds a lightness-aware Neutral Confidence gate.

V2.3 therefore uses a *soft, adaptive perceptual graph* inside each broad hue
family.  A minimum-spanning tree finds the natural colour neighbourhoods; only
structurally implausible edges are cut.  Tiny orphan clusters are re-attached
when they are genuinely close.  Rows break only on true appearance discontinuity
or broad-family boundaries.  There are no brand, file, customer, product-name,
or colour-name rules.
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


def _smoothstep01(x: float) -> float:
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def _neutral_limit(L: float) -> float:
    """Lightness-aware chroma below which hue is not trusted as a family key.

    Near-white samples need a wider neutral envelope because tiny a*/b* noise
    creates unstable h°.  Mid-tones keep a conservative envelope so real muted
    colours are not erased.  Very dark samples get only a small extra allowance.
    """
    L = max(0.0, min(100.0, float(L)))
    high = 5.0 * _smoothstep01((L - 72.0) / 23.0)
    low = 1.5 * _smoothstep01((28.0 - L) / 18.0)
    return 5.0 + high + low


def _hue_reliability(C: float, L: float = 50.0) -> float:
    """Continuous hue confidence relative to the lightness-aware neutral field."""
    C = max(0.0, float(C))
    limit = _neutral_limit(L)
    effective = max(0.0, C - 0.60 * limit)
    return effective / (effective + 6.0) if effective > 0.0 else 0.0


def _neutral_confidence(L: float, C: float) -> float:
    limit = _neutral_limit(L)
    if C <= 0.0:
        return 1.0
    # 1.0 inside the core field, fading smoothly over the next 4 C* units.
    return max(0.0, min(1.0, 1.0 - (float(C) - limit) / 4.0))


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
    """Assign broad family only when hue has enough perceptual support.

    This is the PAC V24 change.  It is dataset-agnostic and contains no brand,
    customer, filename or colour-name rules.
    """
    anchors: list[int] = []
    for r in recs:
        limit = _neutral_limit(r.L)
        if _neutral_core(r.L, r.C) or r.C <= limit:
            r.family = "Neutral"
            continue
        r.family = _broad_family(r.h)
        # Strong chromatic anchors are safely outside the neutral transition.
        if r.C >= max(9.0, limit + 4.0):
            anchors.append(r.idx)

    # Transitional tints are chromatic only when a genuinely close anchor
    # supports that appearance.  Otherwise numerical h° does not get to decide.
    for r in recs:
        if r.family == "Neutral":
            continue
        limit = _neutral_limit(r.L)
        if r.C >= max(9.0, limit + 4.0):
            continue
        cands = [j for j in anchors if abs(recs[j].L - r.L) <= 16.0]
        if not cands:
            r.family = "Neutral"
            continue
        j = min(cands, key=lambda x: (de[r.idx][x], abs(recs[x].L - r.L)))
        if de[r.idx][j] <= 6.5:
            r.family = recs[j].family
        else:
            r.family = "Neutral"


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
            rel = min(_hue_reliability(recs[a].C, recs[a].L), _hue_reliability(recs[b].C, recs[b].L))
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
    rel = min(_hue_reliability(recs[a].C, recs[a].L), _hue_reliability(recs[b].C, recs[b].L))
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
    rel = min(_hue_reliability(recs[a].C, recs[a].L), _hue_reliability(recs[b].C, recs[b].L))
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
    rel = min(_hue_reliability(s1["C"], s1["L"]), _hue_reliability(s2["C"], s2["L"]))
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
                rel = min(_hue_reliability(recs[a].C, recs[a].L), _hue_reliability(recs[b].C, recs[b].L))
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


def appearance_continuity_layout_v24(samples: Sequence[Sample], columns: int = 6) -> dict[str, Any]:
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
        raise ValueError("Appearance continuity V2.4 integrity failure")

    info = {
        id(r.sample): {
            "family": r.family,
            "tone": _tone_name(r.L),
            "chroma": _chroma_name(r.C, r.family == "Neutral"),
            "L": r.L,
            "C": r.C,
            "h": r.h,
            "neutral_limit": _neutral_limit(r.L),
            "neutral_confidence": _neutral_confidence(r.L, r.C),
            "hue_confidence": _hue_reliability(r.C, r.L),
        }
        for r in recs
    }
    low_chroma_hue_risk = sum(
        1 for r in recs if r.family != "Neutral" and r.C <= _neutral_limit(r.L) + 1.5
    )
    deep_hue_risk = sum(1 for r in recs if r.family != "Neutral" and r.L < 25.0 and r.C < 10.0)
    near_white_hue_risk = sum(1 for r in recs if r.family != "Neutral" and r.L >= 85.0 and r.C < 12.0)
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
        "low_chroma_hue_risk": low_chroma_hue_risk,
        "deep_hue_risk": deep_hue_risk,
        "near_white_hue_risk": near_white_hue_risk,
    }
