"""PAC V25 — HVC Surface Path / Neutral Axis.

Purpose
-------
Flatten a three-dimensional colour-order structure into a stable 1-D / row-wise
palette without repeating the two failure modes seen in earlier candidates:

* near-neutral greys being scattered into arbitrary hue families because h° is
  ill-conditioned close to the neutral axis;
* family blocks being concatenated with incompatible endpoints (for example a
  very-light blue immediately followed by a deep violet, or a deep violet
  immediately followed by black while light neutrals live elsewhere).

Scientific structure
--------------------
The implementation keeps the application's D65 / 10° CIELAB measurement basis
and CIEDE2000 pair distance.  It uses colour-order concepts analogous to an H-V-C
surface: broad hue around a neutral axis, lightness/value as a vertical axis and
chroma as distance from neutral.  CIEDE2000 is used as a *local distance* after
appearance topology has constrained which transitions are plausible.

V25 changes the final flattening step substantially:

1. Neutral-axis routing is based on distance to the same-L* neutral point,
   not raw h°.  A narrow "Tinted Neutral" shell catches unstable low-chroma
   greys while preserving visibly blue/khaki/pink samples.
2. Chromatic family assignment keeps the V24.1 hue-topology / warm-cool guards.
3. Each hue family is solved internally as appearance clusters, then exposed in
   two legal orientations (forward / reverse).
4. A small dynamic program chooses the orientation of ALL populated hue-family
   paths plus the Neutral axis together.  This creates a serpentine HVC surface
   path: family endpoints meet at similar lightness/chroma instead of being
   mechanically concatenated.
5. Boundary guardrails explicitly penalise catastrophic ΔE00 / ΔL* jumps.
6. Diagnostics expose neutral splits, family-boundary maxima, catastrophic
   boundaries, lightness reversals and isolated chromatic islands.

The algorithm is dataset-agnostic: no customer, filename or named-colour rules.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from .models import Sample
from .palette_arrangement import _de00_lab
from .palette_continuity_v21 import FAMILY_ORDER, _broad_family, _lab_props
from .palette_continuity_v241_science import (
    _adaptive_nn_scale,
    _boundary_distance,
    _build_family_clusters,
    _chroma_name,
    _cluster_label,
    _cluster_path,
    _family_distance,
    _hue_reliability,
    _opponent_compatible,
    _opponent_side,
    _order_clusters,
    _pair_matrices,
    _spectral_rms,
    _structural_thresholds,
    _tone_name,
    _true_structural_jump,
)


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
    neutral_distance: float = 0.0
    neutral_state: str = "Chromatic"


_HUE_FAMILIES = tuple(f for f in FAMILY_ORDER if f != "Neutral")


def _neutral_axis_distance(r: _Rec) -> float:
    """Perceptual distance to the same-lightness neutral point.

    Using (L*, 0, 0) means the neutral decision does not depend on unstable hue
    angle.  CIEDE2000 is used here only as a local perceptual radial distance.
    """
    return float(_de00_lab((r.L, r.a, r.b), (r.L, 0.0, 0.0)))


def _neutral_state(r: _Rec) -> str:
    """Return Core Neutral / Tinted Neutral / Chromatic.

    The shell is intentionally narrow.  It is wide enough to capture the
    visually grey C*≈3–4 samples whose h° can rotate through Red/Violet/Blue,
    but not wide enough to swallow blue-grey, khaki or pale-blue samples with a
    real chromatic signal.
    """
    d = r.neutral_distance
    C = r.C
    L = r.L

    # Strongly neutral anywhere in the value scale.
    if C <= 2.8 or d <= 3.25:
        return "Core Neutral"

    # Near black / white the visual neutral axis tolerates a little more radial
    # noise, but remains conservative so navy / tinted whites stay chromatic.
    if L <= 17.0 and C <= 4.4 and d <= 4.9:
        return "Core Neutral"
    if L >= 96.0 and C <= 4.8 and d <= 5.2:
        return "Core Neutral"

    # Tinted neutral shell: this is the key fix for low-chroma greys with wildly
    # different numerical h°.  Requiring BOTH low C* and low ΔE00-to-neutral
    # prevents warm earth or blue-grey samples from being over-neutralised.
    if C <= 4.2 and d <= 5.05:
        return "Tinted Neutral"

    return "Chromatic"


def _strong_anchor(r: _Rec) -> bool:
    return r.family != "Neutral" and r.C >= 8.0 and r.neutral_state == "Chromatic"


def _assign_families(recs: list[_Rec], de: list[list[float]]) -> None:
    """Neutral-axis first, then guarded hue topology.

    Low-chroma samples outside the neutral shell keep their own native hue
    direction by default.  CIEDE2000 can only move them to an *adjacent* family
    very close to a shared hue boundary, and only with opponent-axis agreement.
    """
    for r in recs:
        r.native_family = _broad_family(r.h)
        r.neutral_distance = _neutral_axis_distance(r)
        r.neutral_state = _neutral_state(r)
        if r.neutral_state != "Chromatic":
            r.family = "Neutral"
            r.family_reason = "neutral_axis"
        else:
            r.family = r.native_family
            r.family_reason = "native_hue"

    anchors = [r.idx for r in recs if _strong_anchor(r)]

    # Only ambiguous chromatic samples need anchor validation.  Unlike the old
    # low-C* inheritance, an anchor can never steal a sample across the wheel.
    for r in recs:
        if r.family == "Neutral" or r.C >= 8.0:
            continue

        cands = [
            j for j in anchors
            if abs(recs[j].L - r.L) <= 18.0
            and _family_distance(r.native_family, recs[j].family) <= 1
            and _opponent_compatible(r, recs[j])
        ]
        if not cands:
            r.family_reason = "native_hue_no_anchor"
            continue

        j = min(cands, key=lambda x: (de[r.idx][x], abs(recs[x].L - r.L), x))
        anchor = recs[j]
        r.anchor_family = anchor.family

        if anchor.family == r.native_family:
            r.family_reason = "native_hue_supported"
            continue

        # Cross only a real local hue boundary.  The rails are intentionally
        # tighter than V24.1 because V25 no longer needs anchors to rescue greys.
        if _boundary_distance(r.h, r.native_family) <= 5.5 and de[r.idx][j] <= 4.8:
            r.family = anchor.family
            r.family_reason = "adjacent_boundary_bridge"
        else:
            r.family_reason = "native_hue_guarded"



def _connected_components_by_de(indices: list[int], de, limit: float) -> list[list[int]]:
    """Small deterministic components for boundary-island analysis."""
    ids = list(indices)
    if not ids:
        return []
    adj = {i: [] for i in ids}
    for pos, a in enumerate(ids):
        for b in ids[pos + 1:]:
            if de[a][b] <= limit:
                adj[a].append(b)
                adj[b].append(a)
    out: list[list[int]] = []
    seen: set[int] = set()
    for st in sorted(ids):
        if st in seen:
            continue
        stack = [st]
        seen.add(st)
        comp: list[int] = []
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in sorted(adj[x], reverse=True):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        out.append(sorted(comp))
    return out


def _adjacent_hue_families(family: str) -> tuple[str, str]:
    pos = _HUE_FAMILIES.index(family)
    return (
        _HUE_FAMILIES[(pos - 1) % len(_HUE_FAMILIES)],
        _HUE_FAMILIES[(pos + 1) % len(_HUE_FAMILIES)],
    )


def _median_nearest_support(component: list[int], support: list[int], de) -> float:
    if not component or not support:
        return float("inf")
    vals = [min(float(de[i][j]) for j in support if j != i) if any(j != i for j in support) else float("inf") for i in component]
    vals = [x for x in vals if math.isfinite(x)]
    return statistics.median(vals) if vals else float("inf")


def _assimilate_pale_boundary_islands(recs: list[_Rec], de) -> int:
    """Move only *isolated pale boundary islands* to a supported adjacent hue.

    Why this exists
    ---------------
    A hue family may contain a tiny very-light island near a shared hue boundary
    and a completely separate dark body.  Keeping both as one rigid family can
    force a huge within-family jump (for example a tinted near-white immediately
    followed by a deep shade with similar h°).  In a visual colour-order system
    the pale boundary island is better represented by the adjacent family when:

    * it is near-white but still chromatic (so it is NOT a neutral-axis case),
    * it lies close to a hue-family boundary,
    * the same family has a distinct non-pale body,
    * an adjacent family's pale samples give strong perceptual support, and
    * that adjacent support is decisively better than the native body's support.

    The rule is intentionally symmetric around the hue wheel and has no
    customer/name-specific branches.
    """
    changes = 0
    # Snapshot current assignments so a move in one family cannot cascade into
    # a second move during the same pass.
    original_family = {r.idx: r.family for r in recs}

    for fam in _HUE_FAMILIES:
        fam_ids = [r.idx for r in recs if original_family[r.idx] == fam]
        if len(fam_ids) < 3:
            continue

        candidates = [
            i for i in fam_ids
            if recs[i].neutral_state == "Chromatic"
            and recs[i].L >= 88.0
            and recs[i].C <= 14.5
            and _boundary_distance(recs[i].h, fam) <= 9.0
        ]
        if not candidates:
            continue

        for comp in _connected_components_by_de(candidates, de, limit=5.5):
            if not comp:
                continue
            # This is an *island* correction, never a wholesale family swap.
            # A dense pale-blue run, for example, remains Blue even if a tiny
            # Violet tint cluster sits nearby.
            if len(comp) > 6:
                continue

            # Native support must come from the *body*, not another member of
            # this pale island.  Requiring a body prevents an all-pale Violet
            # palette from being arbitrarily relabelled Blue (or vice versa).
            native_body = [
                j for j in fam_ids
                if j not in comp
                and (
                    recs[j].L < 84.0
                    or recs[j].C > 16.0
                    or _boundary_distance(recs[j].h, fam) > 12.0
                )
            ]
            if not native_body:
                continue

            native_support = _median_nearest_support(comp, native_body, de)
            best: tuple[float, str, list[int]] | None = None
            for af in _adjacent_hue_families(fam):
                support = [
                    r.idx for r in recs
                    if original_family[r.idx] == af
                    and r.neutral_state == "Chromatic"
                    and r.L >= 80.0
                    and r.C <= 18.0
                ]
                if len(support) < 2:
                    continue
                adj_support = _median_nearest_support(comp, support, de)
                item = (adj_support, af, support)
                if best is None or item[:2] < best[:2]:
                    best = item

            if best is None:
                continue
            adj_support, target, support = best

            # Strong visual evidence is required.  The native body must be
            # genuinely separated while the adjacent pale family is close.
            if not (adj_support <= 5.0 and native_support >= 9.0 and native_support >= adj_support + 4.0):
                continue

            # Opponent-axis guard: even at low chroma, do not turn a warm pale
            # island into Blue or a cool blueish island into Yellow/Brown.
            if not all(any(_opponent_compatible(recs[i], recs[j]) for j in support) for i in comp):
                continue

            for i in comp:
                recs[i].family = target
                recs[i].family_reason = "pale_boundary_island"
                recs[i].anchor_family = target
                changes += 1

    return changes


def _pair_boundary_cost(a: int, b: int, recs: list[_Rec], de, sp, *, cross_family: bool) -> float:
    """Cost used only for segment / family endpoints.

    Large pair differences are deliberately super-linear so a path cannot hide
    a huge lightness jump behind a small hue difference.
    """
    d = float(de[a][b])
    dL = abs(recs[a].L - recs[b].L)
    dC = abs(recs[a].C - recs[b].C)
    sr = sp[a][b]

    # CIEDE2000 is primary. Reflectance is only a gentle tie-break because the
    # application already represents appearance in D65/10° Lab.
    cost = d + 0.035 * dL + 0.018 * dC
    if math.isfinite(sr):
        cost += 0.012 * min(float(sr), 40.0)

    # Explicit disaster rails. These are penalties, not reclassification rules.
    if d > 12.0:
        cost += 0.28 * (d - 12.0) ** 2
    if dL > 24.0:
        cost += 0.20 * (dL - 24.0) ** 2
    if cross_family and (d >= 18.0 or dL >= 35.0):
        cost += 45.0
    if cross_family and dL >= 55.0:
        cost += 90.0
    return cost


def _significant_lightness_reversals(path: list[int], recs: list[_Rec], rail: float = 4.0) -> int:
    signs: list[int] = []
    for a, b in zip(path, path[1:]):
        d = recs[b].L - recs[a].L
        if abs(d) < rail:
            continue
        signs.append(1 if d > 0 else -1)
    return sum(1 for x, y in zip(signs, signs[1:]) if x != y)


def _path_pair_cost(path: list[int], recs: list[_Rec], de, sp) -> float:
    if len(path) <= 1:
        return 0.0
    cost = sum(_pair_boundary_cost(a, b, recs, de, sp, cross_family=False) for a, b in zip(path, path[1:]))
    # HVC surface should not bounce dark -> light -> dark repeatedly inside one
    # broad family.  A soft reversal penalty keeps L* visually coherent without
    # replacing local ΔE00 continuity.
    cost += 1.8 * _significant_lightness_reversals(path, recs)
    return cost


def _orient_cluster_paths(paths: list[list[int]], recs: list[_Rec], de, sp) -> list[int]:
    """Choose each cluster direction with a 2-state dynamic program."""
    paths = [list(p) for p in paths if p]
    if not paths:
        return []
    if len(paths) == 1:
        p = paths[0]
        # Choose the lower of the graph path and a stable L* path if the latter
        # materially reduces reversals without paying a large ΔE00 price.
        by_l = sorted(p, key=lambda i: (recs[i].L, recs[i].C, recs[i].h, recs[i].sample.display_name.casefold()))
        candidates = [p, list(reversed(p)), by_l, list(reversed(by_l))]
        return min(candidates, key=lambda q: (_path_pair_cost(q, recs, de, sp), tuple(q)))

    cands = [(p, list(reversed(p))) for p in paths]
    dp: list[dict[int, tuple[float, int | None]]] = []
    first = {}
    for o in (0, 1):
        p = cands[0][o]
        first[o] = (_path_pair_cost(p, recs, de, sp), None)
    dp.append(first)

    for k in range(1, len(cands)):
        layer: dict[int, tuple[float, int | None]] = {}
        for o in (0, 1):
            p = cands[k][o]
            own = _path_pair_cost(p, recs, de, sp)
            best = (float("inf"), None)
            for po in (0, 1):
                pp = cands[k - 1][po]
                prev_cost = dp[k - 1][po][0]
                bridge = _pair_boundary_cost(pp[-1], p[0], recs, de, sp, cross_family=False)
                cand = (prev_cost + own + bridge, po)
                if cand[0] < best[0] - 1e-12 or (abs(cand[0] - best[0]) <= 1e-12 and po < (best[1] if best[1] is not None else 9)):
                    best = cand
            layer[o] = best
        dp.append(layer)

    last_o = min((0, 1), key=lambda o: (dp[-1][o][0], o))
    orientations = [0] * len(cands)
    orientations[-1] = last_o
    for k in range(len(cands) - 1, 0, -1):
        prev = dp[k][orientations[k]][1]
        orientations[k - 1] = 0 if prev is None else int(prev)

    out: list[int] = []
    for k, o in enumerate(orientations):
        out.extend(cands[k][o])
    return out


def _family_base_path(indices: list[int], family: str, recs: list[_Rec], de, sp) -> list[int]:
    if not indices:
        return []
    if family == "Neutral":
        # Neutral axis: h° is deliberately absent.  Start from black and move to
        # white; the global DP may reverse the whole axis to connect smoothly.
        return sorted(indices, key=lambda i: (recs[i].L, recs[i].neutral_distance, recs[i].C, recs[i].sample.display_name.casefold()))

    clusters = _build_family_clusters(indices, family, recs, de, sp)
    clusters = _order_clusters(clusters, family, recs, de)
    raw_paths = [_cluster_path(c, family, recs, de, sp) for c in clusters if c]
    graph_path = _orient_cluster_paths(raw_paths, recs, de, sp)

    # A graph path is excellent inside dense colour clouds, but a sparse
    # hue-family can contain separated value islands.  In that case a single
    # graph bridge can be visually disastrous even though most local edges are
    # tiny.  Compare it against a strict Value/L* sweep using the same robust
    # super-linear edge cost.  This is not a fixed preference for L*: it only
    # wins when it materially removes a catastrophic bridge.
    value_path = sorted(
        indices,
        key=lambda i: (recs[i].L, recs[i].C, recs[i].h, recs[i].sample.display_name.casefold()),
    )
    candidates = [graph_path, list(reversed(graph_path)), value_path, list(reversed(value_path))]
    candidates = [p for p in candidates if p]
    return min(candidates, key=lambda p: (_path_pair_cost(p, recs, de, sp), tuple(p)))


def _top_cross_endpoints(indices: list[int], support: list[int], de, k: int = 2) -> list[int]:
    if not indices or not support:
        return []
    return sorted(
        indices,
        key=lambda i: (min(float(de[i][j]) for j in support), i),
    )[: max(1, int(k))]


def _greedy_fixed_endpoint_path(
    indices: list[int], start: int, end: int, recs: list[_Rec], de, sp
) -> list[int]:
    """O(n²) endpoint-constrained path used for large families."""
    if len(indices) <= 1:
        return list(indices)
    if len(indices) == 2:
        return [start, end]
    rem = set(indices)
    rem.discard(start)
    rem.discard(end)
    out = [start]
    while rem:
        cur = out[-1]
        nxt = min(
            rem,
            key=lambda j: (
                _pair_boundary_cost(cur, j, recs, de, sp, cross_family=False)
                + 0.03 * _pair_boundary_cost(j, end, recs, de, sp, cross_family=False),
                de[cur][j],
                abs(recs[cur].L - recs[j].L),
                j,
            ),
        )
        out.append(nxt)
        rem.remove(nxt)
    out.append(end)
    return out


def _cheapest_insertion_fixed_endpoints(
    indices: list[int], start: int, end: int, recs: list[_Rec], de, sp
) -> list[int]:
    """Endpoint-constrained cheapest insertion for small/medium families.

    Endpoints are fixed because they are selected from the best perceptual
    bridges to the adjacent hue families.  Interior samples are inserted where
    they cause the smallest increase in the robust local appearance cost.
    """
    if len(indices) <= 1:
        return list(indices)
    if len(indices) == 2:
        return [start, end]
    path = [start, end]
    remaining = set(indices)
    remaining.discard(start)
    remaining.discard(end)
    while remaining:
        best = None
        for x in remaining:
            for pos in range(len(path) - 1):
                a, b = path[pos], path[pos + 1]
                ab = _pair_boundary_cost(a, b, recs, de, sp, cross_family=False)
                ax = _pair_boundary_cost(a, x, recs, de, sp, cross_family=False)
                xb = _pair_boundary_cost(x, b, recs, de, sp, cross_family=False)
                delta = ax + xb - ab
                cand = (
                    delta,
                    de[a][x] + de[x][b],
                    abs(recs[x].L - (recs[a].L + recs[b].L) * 0.5),
                    x,
                    pos,
                )
                if best is None or cand < best:
                    best = cand
        assert best is not None
        _, _, _, x, pos = best
        path.insert(pos + 1, x)
        remaining.remove(x)
    return path


def _family_candidate_paths(
    family: str,
    family_pos: int,
    populated_families: list[str],
    families: dict[str, list[int]],
    base_paths: dict[str, list[int]],
    recs: list[_Rec],
    de,
    sp,
) -> list[tuple[str, list[int]]]:
    ids = list(families.get(family) or [])
    base = list(base_paths.get(family) or [])
    if not ids or not base:
        return []

    out: list[tuple[str, list[int]]] = []
    seen: set[tuple[int, ...]] = set()

    def add(name: str, path: list[int]) -> None:
        t = tuple(path)
        if path and t not in seen:
            seen.add(t)
            out.append((name, list(path)))

    # First family has no scientific 'previous hue' endpoint.  Preserve the
    # established light-opening convention instead of allowing a dark-red start
    # just to shave a later bridge score.
    if family_pos == 0:
        if recs[base[0]].L >= recs[base[-1]].L:
            add("base", base)
        else:
            add("base_reverse", list(reversed(base)))
    else:
        add("base", base)
        add("base_reverse", list(reversed(base)))

    # Refine the established family endpoints without changing where this
    # family enters/exits.  This often removes a single graph-path outlier while
    # preserving the familiar broad appearance progression.
    if family != "Neutral" and len(ids) <= 130 and len(base) > 2:
        refined = _cheapest_insertion_fixed_endpoints(
            ids, base[0], base[-1], recs, de, sp
        )
        add("base_endpoint_refined", refined)

    if family_pos == 0:
        return out

    # Neutral is a true value axis.  Do not distort it with graph insertion;
    # forward/reverse monotonic value order is the complete candidate set.
    if family == "Neutral":
        return out

    prev_ids = (
        families[populated_families[family_pos - 1]] if family_pos > 0 else []
    )
    next_ids = (
        families[populated_families[family_pos + 1]]
        if family_pos + 1 < len(populated_families)
        else []
    )

    endpoint_k = 4 if len(ids) <= 60 else 2
    if prev_ids:
        left = _top_cross_endpoints(ids, prev_ids, de, endpoint_k)
    else:
        left = sorted(ids, key=lambda i: (-recs[i].L, recs[i].C, i))[:endpoint_k]
    if next_ids:
        right = _top_cross_endpoints(ids, next_ids, de, endpoint_k)
    else:
        right = sorted(ids, key=lambda i: (recs[i].L, recs[i].C, i))[:endpoint_k]

    if not left or not right:
        return out

    # Full cheapest insertion is intentionally limited: O(n³) is useful for the
    # office-size 100-ish sample families where endpoint quality matters most,
    # while large libraries use the O(n²) endpoint-constrained greedy path.
    use_insertion = len(ids) <= 130
    endpoint_pairs = [(a, b) for a in left for b in right if a != b or len(ids) == 1]
    if len(ids) > 220:
        endpoint_pairs = endpoint_pairs[:1]

    for pi, (st, en) in enumerate(endpoint_pairs):
        if use_insertion:
            p = _cheapest_insertion_fixed_endpoints(ids, st, en, recs, de, sp)
            add(f"endpoint_insert_{pi}", p)
        else:
            p = _greedy_fixed_endpoint_path(ids, st, en, recs, de, sp)
            add(f"endpoint_greedy_{pi}", p)

    return out


def _cross_family_minima(populated: list[str], families: dict[str, list[int]], recs, de):
    minima: dict[tuple[str, str], dict[str, float]] = {}
    for a_f, b_f in zip(populated, populated[1:]):
        a_ids, b_ids = families[a_f], families[b_f]
        best_de = float("inf")
        best_dL = float("inf")
        for a in a_ids:
            for b in b_ids:
                d = float(de[a][b])
                dl = abs(recs[a].L - recs[b].L)
                if d < best_de:
                    best_de = d
                if dl < best_dL:
                    best_dL = dl
        minima[(a_f, b_f)] = {"de00": best_de, "dL": best_dL}
    return minima


def _global_surface_paths(
    base_paths: dict[str, list[int]],
    families: dict[str, list[int]],
    recs: list[_Rec],
    de,
    sp,
):
    """Global HVC-surface dynamic program over endpoint-aware family paths.

    The critical quantity is *boundary regret*: how much worse the chosen
    family-to-family bridge is than the best bridge actually available in this
    dataset.  This prevents an avoidable Orange→Yellow or Blue→Violet jump while
    not punishing a genuinely sparse Yellow→Green dataset whose best possible
    bridge is already large.
    """
    populated = [f for f in FAMILY_ORDER if base_paths.get(f)]
    if not populated:
        return {}, {}, {}, {}

    minima = _cross_family_minima(populated, families, recs, de)
    variants = {
        f: _family_candidate_paths(
            f, k, populated, families, base_paths, recs, de, sp
        )
        for k, f in enumerate(populated)
    }

    def bridge_cost(a_f: str, b_f: str, a: int, b: int) -> float:
        raw = _pair_boundary_cost(a, b, recs, de, sp, cross_family=True)
        min_de = minima[(a_f, b_f)]["de00"]
        regret = max(0.0, float(de[a][b]) - min_de)
        # Quadratic regret only addresses avoidable endpoint mismatch.  An
        # unavoidable colour-family gap has regret≈0 and is left alone.
        return raw + 3.0 * regret + 0.20 * regret * regret

    dp: list[list[tuple[float, int | None]]] = []
    first = populated[0]
    first_layer: list[tuple[float, int | None]] = []
    for _, path in variants[first]:
        # A small display convention: start the hue sweep from a light-ish red
        # endpoint when all else is comparable.  This does not affect family
        # assignment or any later endpoint.
        start_penalty = 0.10 * max(0.0, 65.0 - recs[path[0]].L)
        first_layer.append((_path_pair_cost(path, recs, de, sp) + start_penalty, None))
    dp.append(first_layer)

    for k in range(1, len(populated)):
        f = populated[k]
        pf = populated[k - 1]
        layer: list[tuple[float, int | None]] = []
        for _, path in variants[f]:
            own = _path_pair_cost(path, recs, de, sp)
            best = (float("inf"), None)
            for pi, (_, prev_path) in enumerate(variants[pf]):
                c = (
                    dp[k - 1][pi][0]
                    + own
                    + bridge_cost(pf, f, prev_path[-1], path[0])
                )
                cand = (c, pi)
                if cand[0] < best[0] - 1e-12 or (
                    abs(cand[0] - best[0]) <= 1e-12
                    and (best[1] is None or pi < best[1])
                ):
                    best = cand
            layer.append(best)
        dp.append(layer)

    last = min(range(len(dp[-1])), key=lambda i: (dp[-1][i][0], i))
    choices = [0] * len(populated)
    choices[-1] = last
    for k in range(len(populated) - 1, 0, -1):
        prev = dp[k][choices[k]][1]
        choices[k - 1] = 0 if prev is None else int(prev)

    chosen: dict[str, list[int]] = {}
    choice_names: dict[str, str] = {}
    boundary_minima: dict[tuple[str, str], dict[str, float]] = minima
    for f, ci in zip(populated, choices):
        name, path = variants[f][ci]
        chosen[f] = path
        choice_names[f] = name

    return chosen, choice_names, boundary_minima, variants

def _boundary_record(prev_f: str, f: str, a: int, b: int, recs: list[_Rec], de) -> dict[str, Any]:
    d = float(de[a][b])
    dL = abs(recs[a].L - recs[b].L)
    dC = abs(recs[a].C - recs[b].C)
    catastrophic = bool(d >= 18.0 or dL >= 35.0)
    return {
        "from_family": prev_f,
        "to_family": f,
        "from": a,
        "to": b,
        "de00": d,
        "dL": dL,
        "dC": dC,
        "catastrophic": catastrophic,
    }


def _isolated_chromatic_islands(families: dict[str, list[int]], recs: list[_Rec], de) -> int:
    count = 0
    for fam in _HUE_FAMILIES:
        ids = families.get(fam, [])
        if len(ids) <= 1:
            continue
        clusters = _build_family_clusters(ids, fam, recs, de, [[float("nan")]*len(recs) for _ in recs])
        for c in clusters:
            if len(c) != 1:
                continue
            i = c[0]
            nearest = min((de[i][j] for j in ids if j != i), default=0.0)
            if nearest >= 7.5:
                count += 1
    return count


def appearance_continuity_layout_v25(samples: Sequence[Sample], columns: int = 6) -> dict[str, Any]:
    samples = list(samples)
    columns = max(1, int(columns))
    recs: list[_Rec] = []
    for i, sm in enumerate(samples):
        L, a, b, C, h = _lab_props(sm)
        recs.append(_Rec(i, sm, L, a, b, C, h))

    if not recs:
        return {
            "order": [], "grid": [], "groups": [], "breaks": [], "info": {},
            "columns": columns, "family_boundaries": [],
        }

    de, sp = _pair_matrices(recs)
    _assign_families(recs, de)
    pale_boundary_island_changes = _assimilate_pale_boundary_islands(recs, de)

    families = {f: [] for f in FAMILY_ORDER}
    for r in recs:
        families[r.family].append(r.idx)

    base_paths = {f: _family_base_path(families[f], f, recs, de, sp) for f in FAMILY_ORDER}
    chosen_paths, orientations, boundary_minima, _surface_variants = _global_surface_paths(
        base_paths, families, recs, de, sp
    )

    grid: list[list[Sample | None]] = []
    row: list[Sample | None] = []
    order: list[Sample] = []
    groups: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    family_boundaries: list[dict[str, Any]] = []

    prev_idx: int | None = None
    prev_family: str | None = None
    linear_indices: list[int] = []

    for fam in FAMILY_ORDER:
        path = chosen_paths.get(fam) or []
        if not path:
            continue

        local_nn = _adaptive_nn_scale(families[fam], de) if len(families[fam]) > 1 else 3.0
        de_soft, de_hard = _structural_thresholds(local_nn)

        if row:
            row += [None] * (columns - len(row))
            grid.append(row)
            row = []

        if prev_idx is not None and prev_family is not None:
            br = _boundary_record(prev_family, fam, prev_idx, path[0], recs, de)
            _minb = boundary_minima.get((prev_family, fam), {})
            br["min_possible_de00"] = float(_minb.get("de00", br["de00"]))
            br["boundary_regret_de00"] = max(0.0, br["de00"] - br["min_possible_de00"])
            br["avoidable_catastrophic"] = bool(
                br["catastrophic"] and br["boundary_regret_de00"] >= 6.0
            )
            family_boundaries.append(br)
            breaks.append({
                "from": prev_idx,
                "to": path[0],
                "de00": br["de00"],
                "reason": "family_boundary_catastrophic" if br["catastrophic"] else "family_boundary",
                "dL": br["dL"],
            })

        tone, chroma = _cluster_label(path, fam, recs)
        groups.append({
            "family": fam,
            "tone": tone,
            "chroma": chroma,
            "size": len(path),
            "orientation": orientations.get(fam, "forward"),
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
                    breaks.append({
                        "from": a, "to": b, "de00": de[a][b],
                        "reason": "within_family_structural_jump",
                        "dL": abs(recs[a].L - recs[b].L),
                    })

            row.append(recs[idx].sample)
            order.append(recs[idx].sample)
            linear_indices.append(idx)
            prev_idx = idx
            prev_family = fam
            if len(row) == columns:
                grid.append(row)
                row = []

    if row:
        row += [None] * (columns - len(row))
        grid.append(row)

    if len(order) != len(samples) or len({id(x) for x in order}) != len(samples):
        raise ValueError("Appearance continuity V25 integrity failure")

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
            "neutral_distance": r.neutral_distance,
            "neutral_state": r.neutral_state,
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
    neutral_split_count = sum(
        1 for r in recs if r.neutral_state in {"Core Neutral", "Tinted Neutral"} and r.family != "Neutral"
    )
    tinted_neutral_count = sum(1 for r in recs if r.neutral_state == "Tinted Neutral")
    core_neutral_count = sum(1 for r in recs if r.neutral_state == "Core Neutral")
    family_boundary_max_de = max((x["de00"] for x in family_boundaries), default=0.0)
    family_boundary_max_dL = max((x["dL"] for x in family_boundaries), default=0.0)
    catastrophic_boundary_count = sum(1 for x in family_boundaries if x["catastrophic"])
    avoidable_catastrophic_boundary_count = sum(
        1 for x in family_boundaries if x.get("avoidable_catastrophic")
    )
    family_boundary_max_regret = max(
        (float(x.get("boundary_regret_de00", 0.0)) for x in family_boundaries),
        default=0.0,
    )

    within_family_max_de00 = 0.0
    for fam in FAMILY_ORDER:
        p = chosen_paths.get(fam) or []
        if len(p) > 1:
            within_family_max_de00 = max(
                within_family_max_de00,
                max(float(de[a][b]) for a, b in zip(p, p[1:])),
            )

    lightness_reversal_count = 0
    for fam in FAMILY_ORDER:
        p = chosen_paths.get(fam) or []
        lightness_reversal_count += _significant_lightness_reversals(p, recs)

    # Isolated island metric uses the already-computed spectral matrix through a
    # lightweight direct cluster scan to avoid changing the production order.
    isolated_chromatic_island = 0
    for fam in _HUE_FAMILIES:
        ids = families[fam]
        if len(ids) <= 1:
            continue
        cls = _build_family_clusters(ids, fam, recs, de, sp)
        for c in cls:
            if len(c) == 1:
                i = c[0]
                nearest = min((de[i][j] for j in ids if j != i), default=0.0)
                if nearest >= 7.5:
                    isolated_chromatic_island += 1

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
        "family_boundaries": family_boundaries,
        "surface_orientation": orientations,
        "cross_family_anchor_risk": cross_family_anchor_risk,
        "warm_cool_inversion_risk": warm_cool_inversion_risk,
        "neutral_split_count": neutral_split_count,
        "core_neutral_count": core_neutral_count,
        "tinted_neutral_count": tinted_neutral_count,
        "family_boundary_max_de": family_boundary_max_de,
        "family_boundary_max_dL": family_boundary_max_dL,
        "catastrophic_boundary_count": catastrophic_boundary_count,
        "avoidable_catastrophic_boundary_count": avoidable_catastrophic_boundary_count,
        "family_boundary_max_regret": family_boundary_max_regret,
        "within_family_max_de00": within_family_max_de00,
        "lightness_reversal_count": lightness_reversal_count,
        "isolated_chromatic_island": isolated_chromatic_island,
        "pale_boundary_island_changes": pale_boundary_island_changes,
    }
