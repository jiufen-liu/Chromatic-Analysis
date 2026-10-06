"""LABCH palette-sort helpers (Hotfix106).

Design goals
------------
* Keep LABCH, one-reference Color Difference and Running ΔE as separate tools.
* Make Hue sorting visually usable on palette cards instead of a raw h° number list.
* Keep greys/blacks together, keep broad hue families together, then organise each
  hue family from light to dark.  This avoids the former 5°-bucket reset that
  produced a light-blue -> near-black -> mid-blue sawtooth.
* Never modify measured Lab values.
"""
from __future__ import annotations

from math import atan2, degrees, hypot
from typing import Sequence

# Same human-readable hue-family boundaries used by LABC Atlas.
# The ranges cover the full CIELAB h° circle and are intentionally broad: Hue
# sorting is first a family organisation task; fine h° remains a local tie-break.
_HUE_FAMILIES: tuple[tuple[str, float, float], ...] = (
    ("red", 345.0, 15.0),
    ("red_orange", 15.0, 45.0),
    ("orange", 45.0, 75.0),
    ("yellow", 75.0, 105.0),
    ("yellow_green", 105.0, 140.0),
    ("green", 140.0, 195.0),
    ("cyan", 195.0, 240.0),
    ("blue", 240.0, 285.0),
    ("blue_violet", 285.0, 315.0),
    ("violet", 315.0, 330.0),
    ("red_violet", 330.0, 345.0),
)


def neutral_limit(L: float) -> float:
    """Conservative achromatic gate shared with the Atlas browsing logic."""
    L = float(L)
    if L >= 65.0:
        return 6.0
    if L >= 45.0:
        return 5.5
    if L >= 30.0:
        return 4.8
    return 4.0


def lab_to_lch(lab: Sequence[float]) -> tuple[float, float, float]:
    L, a, b = (float(lab[0]), float(lab[1]), float(lab[2]))
    C = hypot(a, b)
    h = degrees(atan2(b, a)) % 360.0 if C > 1e-12 else 0.0
    return L, C, h


def _angle_in_range(h: float, start: float, end: float) -> bool:
    h %= 360.0
    start %= 360.0
    end %= 360.0
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def hue_family_index(h: float) -> int:
    h = float(h) % 360.0
    for i, (_name, start, end) in enumerate(_HUE_FAMILIES):
        if _angle_in_range(h, start, end):
            return i
    return 0


def hue_family_name(h: float) -> str:
    return _HUE_FAMILIES[hue_family_index(h)][0]


def _family_local_hue(h: float, family_index: int) -> float:
    """Hue position measured from the beginning of its broad family."""
    start = _HUE_FAMILIES[int(family_index)][1]
    return (float(h) - start) % 360.0


def professional_hue_sort_key(
    lab: Sequence[float],
    stable_index: int = 0,
    *,
    lightness_step: float = 10.0,
) -> tuple:
    """Deterministic palette-friendly Hue ordering.

    Why HF105 looked wrong
    ----------------------
    HF105 made 5° hue the primary local bucket.  Every new 5° bucket restarted
    the L* order, so a Blue region could read light blue -> near black -> light
    blue -> navy.  The hue numbers were legal, but the palette was visually bad.

    HF106 hierarchy
    ---------------
    1. Achromatic/near-neutral colours are one coherent block, light -> dark.
    2. Chromatic colours are grouped by broad hue family in circular order:
       Red -> Red-Orange -> ... -> Blue -> Violet -> Red-Violet.
    3. Inside one family, L* is the main visual organiser (light -> dark).
    4. C* then local h° only break nearby ties.

    This keeps "blue with blue" and, inside Blue, puts pale blue -> blue ->
    deep blue/navy instead of repeatedly resetting depth for each tiny h° slice.
    """
    L, C, h = lab_to_lch(lab)
    idx = int(stable_index)

    if C <= neutral_limit(L):
        # One achromatic block.  Keeping L* primary makes white/grey/black read
        # as one clean ramp rather than being scattered by unstable h° values.
        return (0, -L, C, idx)

    family = hue_family_index(h)
    local_h = _family_local_hue(h, family)

    # Exact L* is intentionally ahead of local hue.  This is the key HF106
    # change: a family no longer restarts lightness every few hue degrees.
    return (1, family, -L, C, local_h, idx)


def strict_reference_distance_key(distance: float, stable_index: int = 0, stable_key: str = "") -> tuple:
    """Deterministic key for one-reference Color Difference ordering."""
    return (float(distance), int(stable_index), str(stable_key))


def circular_hue_cut(hues: Sequence[float]) -> float:
    """Choose the midpoint of the largest empty arc on the hue circle.

    A linear 0..360 sort incorrectly separates e.g. 359° red from 1° red.
    Cutting the circle at the largest data gap keeps dense hue clusters intact.
    """
    values = sorted(float(h) % 360.0 for h in hues)
    if not values:
        return 0.0
    if len(values) == 1:
        return (values[0] + 180.0) % 360.0
    best_gap = -1.0
    best_start = values[0]
    for i, h in enumerate(values):
        nxt = values[(i + 1) % len(values)] + (360.0 if i == len(values) - 1 else 0.0)
        gap = nxt - h
        # Deterministic tie break: smaller starting hue wins.
        if gap > best_gap + 1e-12 or (abs(gap - best_gap) <= 1e-12 and h < best_start):
            best_gap = gap
            best_start = h
    return (best_start + best_gap * 0.5) % 360.0


def circular_hue_position(h: float, cut_angle: float) -> float:
    """Linear position after cutting the hue circle at ``cut_angle``."""
    return (float(h) - float(cut_angle)) % 360.0


def reliable_hues_for_cut(labs: Sequence[Sequence[float]], minimum_chroma: float = 8.0) -> list[float]:
    """Return hue angles reliable enough to choose a circular cut.

    Low-chroma/near-neutral samples still participate in the final neutral block,
    but they do not decide where the hue circle should be cut.
    """
    out: list[float] = []
    for lab in labs:
        L, C, h = lab_to_lch(lab)
        if C > max(neutral_limit(L), float(minimum_chroma)):
            out.append(h)
    if out:
        return out
    # Degenerate fallback: use any chromatic sample above the neutral gate.
    for lab in labs:
        L, C, h = lab_to_lch(lab)
        if C > neutral_limit(L):
            out.append(h)
    return out
