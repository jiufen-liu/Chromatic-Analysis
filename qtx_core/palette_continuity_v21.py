"""Structural colour-appearance continuity layout for Palette Studio.

This module is deliberately dataset-agnostic.  It does not contain product- or
customer-specific colour names (khaki, Adidas, etc.) and it does not special-case
individual QTX files.  The layout is built from reusable appearance principles:

1. hue is trusted only when chroma is high enough to make hue meaningful;
2. broad hue families keep obvious colours together;
3. each family is subdivided by tone (L*) and chroma level before path finding;
4. tiny islands are re-homed only when they are genuinely close to a compatible
   group;
5. inside a subgroup, CIEDE2000 is primary and spectrum is a secondary tie-break;
6. visually large local jumps create a row break instead of being forced next to
   one another.

The function returns ordinary Sample objects and ``None`` slots, making it usable
by both the diagnostics tools and the GUI without depending on Qt.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence, Any

try:
    import numpy as np
except Exception:  # packaging fallback; the application normally ships NumPy
    np = None

from .models import Sample
from .palette_arrangement import _de00_lab

FAMILY_ORDER = ("Red", "Orange/Brown", "Yellow", "Green", "Blue", "Violet", "Neutral")
TONE_NAMES = ("Deep", "Dark", "Mid", "Light", "Pale", "VeryLight")
TONE_EDGES = (0.0, 25.0, 42.0, 60.0, 78.0, 90.0, 101.0)
CHROMA_NAMES = ("Muted", "Normal", "Vivid")


def _hue_distance(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def _lab_props(sample: Sample) -> tuple[float, float, float, float, float]:
    L, a, b = (float(x) for x in sample.lab_d65_10)
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0 if C > 1e-12 else 0.0
    return L, a, b, C, h


def _broad_family(h: float) -> str:
    """Broad, user-facing hue skeleton.

    Cyan-blue is intentionally kept inside a broad Blue family.  The earlier
    narrow Cyan/Blue boundary was a major source of visually identical sky-blue
    swatches being split into separate islands.
    """
    h = float(h) % 360.0
    if h >= 330.0 or h < 22.5:
        return "Red"
    if h < 67.5:
        return "Orange/Brown"
    if h < 110.0:
        return "Yellow"
    if h < 190.0:
        return "Green"
    if h < 285.0:
        return "Blue"
    return "Violet"


def _tone_index(L: float) -> int:
    L = float(L)
    for i in range(len(TONE_EDGES) - 1):
        if TONE_EDGES[i] <= L < TONE_EDGES[i + 1]:
            return i
    return len(TONE_NAMES) - 1


def _chroma_index(C: float) -> int:
    C = float(C)
    if C < 10.0:
        return 0
    if C < 30.0:
        return 1
    return 2


def _neutral_core(L: float, C: float) -> bool:
    """Only remove hue where hue is genuinely unreliable.

    The rule is intentionally conservative.  Pale blue/grey-blue swatches with
    a real tint stay eligible for a hue family; true black/grey/white cores do
    not get scattered merely because their numerical h° is unstable.
    """
    if C < 3.5:
        return True
    if L < 22.0 and C < 7.0:
        return True
    if L > 94.0 and C < 5.5:
        return True
    return False


def _spectral_rms(a: Sample, b: Sample) -> float:
    """RMS on the common spectral domain without importing colour-science."""
    if not a.has_spectrum() or not b.has_spectrum():
        return float("nan")
    wa = [float(x) for x in a.wavelengths]; wb = [float(x) for x in b.wavelengths]
    ra = [float(x) for x in a.reflectance]; rb = [float(x) for x in b.reflectance]
    lo = max(min(wa), min(wb)); hi = min(max(wa), max(wb))
    if lo >= hi:
        return float("nan")
    grid = sorted({x for x in wa + wb if lo <= x <= hi})
    if not grid:
        return float("nan")
    if np is not None:
        av = np.interp(grid, wa, ra); bv = np.interp(grid, wb, rb)
        return float(np.sqrt(np.mean((av - bv) ** 2)))
    # linear interpolation fallback
    def interp(xs, ys, x):
        if x <= xs[0]: return ys[0]
        if x >= xs[-1]: return ys[-1]
        for i in range(1, len(xs)):
            if xs[i] >= x:
                x0, x1 = xs[i-1], xs[i]; y0, y1 = ys[i-1], ys[i]
                t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
                return y0 + (y1 - y0) * t
        return ys[-1]
    vals = [(interp(wa, ra, x) - interp(wb, rb, x)) ** 2 for x in grid]
    return math.sqrt(sum(vals) / len(vals))


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
    tone: int = 0
    chroma: int = -1


@dataclass
class _Group:
    family: str
    tone: int
    chroma: int
    members: list[int]
    path: list[int] | None = None


def _centroid(group: _Group, recs: list[_Rec]) -> tuple[float, float, float]:
    n = max(1, len(group.members))
    return (
        sum(recs[i].L for i in group.members) / n,
        sum(recs[i].a for i in group.members) / n,
        sum(recs[i].b for i in group.members) / n,
    )


def _median_positive(vals: Sequence[float], fallback: float = 1.0) -> float:
    clean = [float(x) for x in vals if math.isfinite(float(x)) and float(x) > 1e-12]
    return statistics.median(clean) if clean else fallback


def _pair_matrices(recs: list[_Rec]) -> tuple[list[list[float]], list[list[float]]]:
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
    # First pass: strong chromatic anchors and true neutrals.
    anchors: list[int] = []
    for r in recs:
        r.tone = _tone_index(r.L)
        if _neutral_core(r.L, r.C):
            r.family = "Neutral"; r.chroma = -1
        else:
            r.family = _broad_family(r.h); r.chroma = _chroma_index(r.C)
            if r.C >= 8.0:
                anchors.append(r.idx)

    # Hue is unstable for low-chroma tints.  Instead of trusting h° blindly,
    # inherit the family of a perceptually close chromatic anchor at a similar
    # lightness.  If no such anchor exists, keep the colour in Neutral.
    for r in recs:
        if r.family == "Neutral" or r.C >= 8.0:
            continue
        candidates = [j for j in anchors if abs(recs[j].L - r.L) <= 18.0]
        if not candidates:
            r.family = "Neutral"; r.chroma = -1; continue
        j = min(candidates, key=lambda x: (de[r.idx][x], abs(recs[x].L-r.L)))
        if de[r.idx][j] <= 8.5:
            r.family = recs[j].family
            r.chroma = _chroma_index(r.C)
        else:
            r.family = "Neutral"; r.chroma = -1


def _build_groups(recs: list[_Rec], de: list[list[float]]) -> list[_Group]:
    buckets: dict[tuple[str, int, int], list[int]] = {}
    for r in recs:
        key = (r.family, r.tone, -1 if r.family == "Neutral" else r.chroma)
        buckets.setdefault(key, []).append(r.idx)
    groups = [_Group(f, t, c, list(m)) for (f, t, c), m in buckets.items()]

    # Re-home only tiny islands, and only to a compatible subgroup in the same
    # broad family.  This prevents one black/blue/pink swatch becoming an
    # isolated island without creating dataset-specific exceptions.
    changed = True
    while changed:
        changed = False
        for g in sorted(list(groups), key=lambda x: len(x.members)):
            if g not in groups or len(g.members) > 2:
                continue
            cg = _centroid(g, recs)
            candidates: list[tuple[float, _Group]] = []
            for h in groups:
                if h is g or h.family != g.family or abs(h.tone - g.tone) > 1:
                    continue
                d = float(_de00_lab(cg, _centroid(h, recs)))
                candidates.append((d, h))
            if candidates:
                d, target = min(candidates, key=lambda x: (x[0], -len(x[1].members)))
                if d <= 9.0:
                    target.members.extend(g.members)
                    groups.remove(g)
                    changed = True
                    break
    return groups


def _score_matrix(indices: list[int], de: list[list[float]], sp: list[list[float]]) -> dict[tuple[int,int], float]:
    de_vals=[]; sp_vals=[]
    for pos,a in enumerate(indices):
        for b in indices[pos+1:]:
            de_vals.append(de[a][b])
            if math.isfinite(sp[a][b]): sp_vals.append(sp[a][b])
    de_med=_median_positive(de_vals,1.0); sp_med=_median_positive(sp_vals,1.0)
    score={}
    for a in indices:
        for b in indices:
            if a==b: score[(a,b)]=0.0; continue
            dn=de[a][b]/de_med
            sn=(sp[a][b]/sp_med) if math.isfinite(sp[a][b]) else dn
            # Perception is primary; spectrum is a secondary tie-break.
            val=0.85*dn + 0.15*sn
            # A strong penalty prevents a small spectral RMS from legitimising a
            # visibly wrong neighbour (the historical Night-Sky -> Dark-Green bug).
            if de[a][b] > 10.0:
                val += ((de[a][b]-10.0)/5.0) ** 2
            score[(a,b)] = val
    return score


def _path_for_group(group: _Group, recs: list[_Rec], de: list[list[float]], sp: list[list[float]]) -> list[int]:
    inds=list(group.members)
    if len(inds)<=1: return inds
    score=_score_matrix(inds,de,sp)

    def greedy(start:int)->list[int]:
        rem=set(inds); rem.remove(start); out=[start]
        while rem:
            cur=out[-1]
            nxt=min(rem,key=lambda j:(score[(cur,j)],de[cur][j],recs[j].L,recs[j].C,recs[j].sample.display_name.casefold()))
            out.append(nxt); rem.remove(nxt)
        return out
    def cost(path:list[int])->float:
        return sum(score[(a,b)] for a,b in zip(path,path[1:]))
    def improve(path:list[int])->list[int]:
        p=list(path)
        # Two bounded 2-opt passes remove the worst greedy end traps while
        # keeping hundreds-of-swatches performance predictable.
        for _ in range(2):
            improved=False
            for i in range(len(p)-3):
                a,b=p[i],p[i+1]
                for k in range(i+2,len(p)-1):
                    c,d=p[k],p[k+1]
                    if score[(a,c)] + score[(b,d)] + 1e-12 < score[(a,b)] + score[(c,d)]:
                        p[i+1:k+1]=reversed(p[i+1:k+1]); improved=True
            if not improved: break
        return p

    candidates={inds[0],min(inds,key=lambda i:recs[i].L),max(inds,key=lambda i:recs[i].L),
                min(inds,key=lambda i:recs[i].C),max(inds,key=lambda i:recs[i].C)}
    # add endpoints of the largest perceptual diameter
    far=None; far_d=-1.0
    for pos,a in enumerate(inds):
        for b in inds[pos+1:]:
            if de[a][b]>far_d: far=(a,b); far_d=de[a][b]
    if far: candidates.update(far)
    best=None; best_cost=float("inf")
    for start in list(candidates)[:8]:
        p=improve(greedy(start)); c=cost(p)
        if c<best_cost: best,best_cost=p,c
    return best or inds


def _adaptive_path_segments(path:list[int], de:list[list[float]]) -> tuple[list[list[int]], float]:
    if len(path)<=1: return [path],10.0
    jumps=[de[a][b] for a,b in zip(path,path[1:])]
    med=statistics.median(jumps)
    mad=statistics.median(abs(x-med) for x in jumps)
    # Relative outlier test plus an absolute ceiling.  A dataset with generally
    # tiny local differences gets a lower break threshold; a rough industrial
    # dataset is allowed more tolerance, but never a >10 ΔE00 forced neighbour.
    threshold=min(10.0,max(6.5,med + 3.0*1.4826*mad))
    parts=[]; start=0
    for i,d in enumerate(jumps):
        if d>threshold:
            parts.append(path[start:i+1]); start=i+1
    parts.append(path[start:])
    return [p for p in parts if p],threshold


def appearance_continuity_layout_v21(samples: Sequence[Sample], columns: int = 6) -> dict[str, Any]:
    """Return a structural, user-editable colour-card layout.

    ``columns`` is a maximum row width, not a demand that every row be filled.
    Large perceptual transitions intentionally leave ``None`` slots and begin a
    new row.
    """
    samples=list(samples); columns=max(1,int(columns))
    recs=[]
    for i,sm in enumerate(samples):
        L,a,b,C,h=_lab_props(sm)
        recs.append(_Rec(i,sm,L,a,b,C,h))
    if not recs:
        return {"order":[],"grid":[],"groups":[],"breaks":[],"info":{}}
    de,sp=_pair_matrices(recs)
    _assign_families(recs,de)
    groups=_build_groups(recs,de)
    for g in groups:
        g.path=_path_for_group(g,recs,de,sp)

    groups.sort(key=lambda g:(FAMILY_ORDER.index(g.family),g.tone,g.chroma,
                              _centroid(g,recs)[0],_centroid(g,recs)[1]))

    # Split any residual large jump inside a subgroup into a new segment.
    segments=[]; split_breaks=[]
    for g in groups:
        parts,thr=_adaptive_path_segments(g.path or [],de)
        for part_no,part in enumerate(parts):
            segments.append((g,part,part_no,thr))
        if len(parts)>1:
            for left,right in zip(parts,parts[1:]):
                split_breaks.append({"from":left[-1],"to":right[0],"de00":de[left[-1]][right[0]],"reason":"subgroup_jump"})

    grid=[]; row=[]; order=[]; breaks=[]; previous=None; previous_group=None
    for g,path,part_no,thr in segments:
        if not path: continue
        # Deterministic orientation.  If the segment can legally continue on
        # the current row, orient its closer endpoint toward the previous card.
        p=list(path)
        if previous is not None and de[previous][p[-1]] < de[previous][p[0]]:
            p.reverse()
        elif previous is None and recs[p[0]].L > recs[p[-1]].L:
            p.reverse()

        start_new=False; boundary_de=None
        if row:
            boundary_de=de[previous][p[0]] if previous is not None else None
            if previous_group is None or g.family != previous_group.family:
                start_new=True
            elif g.tone != previous_group.tone:
                start_new=True
            elif boundary_de is not None and boundary_de>8.0:
                start_new=True
        if start_new:
            row += [None]*(columns-len(row)); grid.append(row); row=[]
            breaks.append({"from":previous,"to":p[0],"de00":boundary_de,"reason":"family_or_tone_boundary"})

        for idx in p:
            row.append(recs[idx].sample); order.append(recs[idx].sample)
            previous=idx
            if len(row)==columns:
                grid.append(row); row=[]
        previous_group=g
    if row:
        row += [None]*(columns-len(row)); grid.append(row)

    # Strong integrity contract: every input Sample object appears once.
    if len(order)!=len(samples) or len({id(x) for x in order})!=len(samples):
        raise ValueError("Appearance continuity V2.1 integrity failure")

    info={id(r.sample):{"family":r.family,"tone":TONE_NAMES[r.tone],
                        "chroma":("Neutral" if r.family=="Neutral" else CHROMA_NAMES[r.chroma]),
                        "L":r.L,"C":r.C,"h":r.h} for r in recs}
    group_report=[]
    for g,path,part_no,thr in segments:
        group_report.append({"family":g.family,"tone":TONE_NAMES[g.tone],
                             "chroma":("Neutral" if g.family=="Neutral" else CHROMA_NAMES[g.chroma]),
                             "size":len(path),"break_threshold":thr})
    return {"order":order,"grid":grid,"groups":group_report,
            "breaks":breaks+split_breaks,"info":info,"columns":columns}
