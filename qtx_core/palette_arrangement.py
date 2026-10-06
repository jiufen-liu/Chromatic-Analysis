"""Fast, deterministic colour-appearance ordering for Palette Studio.

Visual Palette Arrangement V1 is intentionally *not* a replacement for a
standard colour-difference formula or for Munsell renotation.  It is an
industrial colour-card layout heuristic built from the three appearance axes
users actually see in a swatch book: hue family, lightness and chroma.

Design goals for V1:
- keep obvious hue families together so a red cannot drift into a green block;
- do not trust hue angle near the neutral axis;
- keep each hue family monotonic in L* (no dark -> pale -> dark oscillation);
- choose the direction of each family using only a cheap Lab boundary distance,
  avoiding the greedy-path end traps seen in the experimental spectral paths;
- remain O(n log n) and therefore effectively instant for hundreds of swatches.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


HUE_FAMILY_NAMES = (
    "Red",
    "Red-Orange",
    "Yellow",
    "Yellow-Green",
    "Green-Cyan",
    "Cyan-Blue",
    "Blue-Violet",
    "Violet-Magenta",
)


def _lab3(lab: Sequence[float]) -> tuple[float, float, float]:
    if len(lab) < 3:
        raise ValueError("Lab needs three values")
    return float(lab[0]), float(lab[1]), float(lab[2])


def _de76(a: Sequence[float], b: Sequence[float]) -> float:
    L1, a1, b1 = _lab3(a)
    L2, a2, b2 = _lab3(b)
    return math.sqrt((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)




def _de00_lab(lab1: Sequence[float], lab2: Sequence[float]) -> float:
    """Pure-math CIEDE2000 for lightweight palette heuristics.

    Kept local so Palette Studio ordering does not need to import the heavier
    colour-science stack merely to decide whether two near-white swatches are
    perceptually indistinguishable enough to share the same transition field.
    """
    L1, a1, b1 = _lab3(lab1)
    L2, a2, b2 = _lab3(lab2)
    C1 = math.hypot(a1, b1); C2 = math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - math.sqrt((Cbar ** 7) / (Cbar ** 7 + 25.0 ** 7))) if Cbar else 0.5
    a1p = (1.0 + G) * a1; a2p = (1.0 + G) * a2
    C1p = math.hypot(a1p, b1); C2p = math.hypot(a2p, b2)

    def _hp(ap: float, bb: float) -> float:
        h = math.degrees(math.atan2(bb, ap))
        return h + 360.0 if h < 0.0 else h

    h1p = _hp(a1p, b1) if C1p > 1e-12 else 0.0
    h2p = _hp(a2p, b2) if C2p > 1e-12 else 0.0
    dLp = L2 - L1; dCp = C2p - C1p
    if C1p * C2p == 0.0:
        dhp = 0.0
    else:
        dh = h2p - h1p
        if abs(dh) <= 180.0: dhp = dh
        elif dh > 180.0: dhp = dh - 360.0
        else: dhp = dh + 360.0
    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))
    Lbarp = (L1 + L2) / 2.0; Cbarp = (C1p + C2p) / 2.0
    if C1p * C2p == 0.0:
        hbarp = h1p + h2p
    else:
        dh = abs(h1p - h2p)
        if dh <= 180.0: hbarp = (h1p + h2p) / 2.0
        elif h1p + h2p < 360.0: hbarp = (h1p + h2p + 360.0) / 2.0
        else: hbarp = (h1p + h2p - 360.0) / 2.0
    T = (1.0 - 0.17 * math.cos(math.radians(hbarp - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * hbarp))
         + 0.32 * math.cos(math.radians(3.0 * hbarp + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * hbarp - 63.0)))
    dtheta = 30.0 * math.exp(-((hbarp - 275.0) / 25.0) ** 2)
    Rc = 2.0 * math.sqrt((Cbarp ** 7) / (Cbarp ** 7 + 25.0 ** 7)) if Cbarp else 0.0
    Sl = 1.0 + (0.015 * (Lbarp - 50.0) ** 2) / math.sqrt(20.0 + (Lbarp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbarp
    Sh = 1.0 + 0.015 * Cbarp * T
    Rt = -math.sin(math.radians(2.0 * dtheta)) * Rc
    xL = dLp / Sl; xC = dCp / Sc; xH = dHp / Sh
    return math.sqrt(xL * xL + xC * xC + xH * xH + Rt * xC * xH)

def _appearance_record(key: str, name: str, lab: Sequence[float]) -> dict[str, Any]:
    L, a, b = _lab3(lab)
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0 if C > 1e-12 else 0.0

    # Hue becomes unstable close to the neutral axis.  Very light low-chroma
    # samples are perceived primarily as whites; very dark low-chroma samples
    # are perceived primarily as blacks.  Keep these out of the hue wheel.
    if L >= 88.0 and C <= 14.0:
        bucket = "neutral"
        neutral_kind = "white"
    elif L <= 22.0 and C <= 10.0:
        bucket = "neutral"
        neutral_kind = "black"
    elif C <= 8.0:
        bucket = "neutral"
        neutral_kind = "neutral"
    else:
        bucket = "chromatic"
        neutral_kind = ""

    family = int(((h + 22.5) % 360.0) // 45.0) if bucket == "chromatic" else -1
    return {
        "key": str(key),
        "name": str(name),
        "lab": (L, a, b),
        "L": L,
        "a": a,
        "b": b,
        "C": C,
        "h": h,
        "bucket": bucket,
        "neutral_kind": neutral_kind,
        "family": family,
        "family_name": HUE_FAMILY_NAMES[family] if family >= 0 else "Neutral",
    }


def visual_palette_order_v1(
    rows: Sequence[tuple[str, str, Sequence[float]]],
) -> dict[str, Any]:
    """Return a deterministic visual colour-card order.

    ``rows`` contains ``(stable_key, display_name, Lab)``.  The returned order
    contains every input key exactly once; no colour is filtered or duplicated.

    The eight broad hue families are a *layout device*, not a claim that CIELAB
    hue angle is a standard named-hue classification.  Within each family the
    sequence is monotonic in L*.  Family direction is chosen from its two
    monotonic possibilities by the smaller boundary ΔE*ab to the previous
    family.  Near-neutrals form one separate monotonic-L* group whose direction
    is chosen the same way.
    """

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    families: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_NAMES]
    neutral: list[dict[str, Any]] = []
    for rec in records:
        if rec["bucket"] == "chromatic":
            families[int(rec["family"])].append(rec)
        else:
            neutral.append(rec)

    ordered: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    previous: dict[str, Any] | None = None

    for family_index, family_rows in enumerate(families):
        if not family_rows:
            continue
        # Two legal appearances for a family: dark->light or light->dark.
        # C* and hue are stable tie-breakers only; L* remains monotonic.
        dark_to_light = sorted(
            family_rows,
            key=lambda r: (r["L"], r["C"], r["h"], r["name"].casefold(), r["key"]),
        )
        light_to_dark = list(reversed(dark_to_light))
        if previous is None:
            # Start the colour wheel from the lighter reds: friendlier for a
            # physical/electronic colour-card opening page.
            chosen = light_to_dark
        else:
            d_dark = _de76(previous["lab"], dark_to_light[0]["lab"])
            d_light = _de76(previous["lab"], light_to_dark[0]["lab"])
            chosen = dark_to_light if d_dark <= d_light else light_to_dark
        ordered.extend(chosen)
        previous = chosen[-1]
        family_counts[HUE_FAMILY_NAMES[family_index]] = len(chosen)

    # Neutral hue is not reliable enough for ordering.  Keep it monotonic in L*
    # and select the direction which produces the smaller family-boundary jump.
    neutral.sort(key=lambda r: (-r["L"], r["C"], r["name"].casefold(), r["key"]))
    if neutral and previous is not None:
        reversed_neutral = list(reversed(neutral))
        d_forward = _de76(previous["lab"], neutral[0]["lab"])
        d_reverse = _de76(previous["lab"], reversed_neutral[0]["lab"])
        if d_reverse < d_forward:
            neutral = reversed_neutral
    ordered.extend(neutral)

    keys = [rec["key"] for rec in ordered]
    if len(keys) != len(rows) or len(set(keys)) != len(keys):
        # Stable keys are required by Palette Studio.  Failing loudly is safer
        # than silently dropping colour measurements.
        raise ValueError("Visual Palette V1 integrity check failed: keys are not one-to-one")

    info_by_key = {
        rec["key"]: {
            "family": rec["family_name"],
            "bucket": rec["bucket"],
            "neutral_kind": rec["neutral_kind"],
            "L": rec["L"],
            "C": rec["C"],
            "h": rec["h"],
        }
        for rec in records
    }
    return {
        "keys": keys,
        "info": info_by_key,
        "family_counts": family_counts,
        "neutral_count": len(neutral),
    }


# ---------------------------------------------------------------------------
# HF77 / Visual Palette Arrangement V2 (offline validation only)
# ---------------------------------------------------------------------------

HUE_FAMILY_12 = (
    "Red",
    "Red-Orange",
    "Orange",
    "Yellow",
    "Yellow-Green",
    "Green",
    "Green-Cyan",
    "Cyan-Blue",
    "Blue",
    "Blue-Violet",
    "Violet",
    "Magenta-Red",
)


def _v2_neutral_kind(L: float, C: float) -> str:
    """Conservative appearance routing used by the HF77 V2 audit.

    Hue angle becomes poorly conditioned near the neutral axis.  V2 therefore
    removes only clearly neutral samples from hue-page construction: tinted
    whites may use a slightly wider chroma allowance, while mid-value samples
    require much lower chroma before they are treated as neutral.
    """
    if L >= 90.0 and C <= 14.0:
        return "White"
    if L <= 20.0 and C <= 7.0:
        return "Black"
    if C <= 5.0:
        return "Neutral"
    return ""


def visual_palette_layout_v2(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_band: float = 10.0,
    separator_rows: int = 1,
) -> dict[str, Any]:
    """Build an audit-only 2-D colour-card layout.

    V2 deliberately changes the *problem definition* from a one-dimensional
    travelling path to a colour-atlas layout.  The grid is built as:

    - 12 hue pages/sections around CIELCh hue, Red -> ... -> Magenta-Red;
    - within each hue section, high-lightness rows precede low-lightness rows;
    - within a lightness row, two 15-degree sub-hue lanes are kept together and
      chroma grows from low to high;
    - clearly near-neutral whites/greys/blacks are moved to a separate final
      block where unstable hue angle is not used;
    - optional empty separator rows preserve semantic section boundaries.

    This is a deterministic layout heuristic for visual colour-card review. It
    is not a CIE/ISO colour-difference formula and it does not modify the
    production Palette Studio ordering in HF77.
    """
    columns = max(1, int(columns))
    lightness_band = max(1.0, float(lightness_band))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral: list[dict[str, Any]] = []

    for rec in records:
        kind = _v2_neutral_kind(float(rec["L"]), float(rec["C"]))
        rec["v2_neutral_kind"] = kind
        if kind:
            rec["v2_family"] = -1
            rec["v2_family_name"] = "Neutral / White / Black"
            neutral.append(rec)
            continue

        # Twelve 30-degree sectors, with Red centred on 0 degrees.  The modulo
        # keeps red-violet values near 360 adjacent to red values near 0.
        family = int(((float(rec["h"]) + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        rec["v2_family"] = family
        rec["v2_family_name"] = HUE_FAMILY_12[family]
        chromatic[family].append(rec)

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def push_records(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        # A row is a lightness slice.  This is intentionally closer to the
        # organisation of a colour atlas than to a nearest-neighbour path.
        bands: dict[int, list[dict[str, Any]]] = {}
        for rec in block:
            band = int(math.floor(float(rec["L"]) / lightness_band))
            band = max(0, min(int(math.ceil(100.0 / lightness_band)), band))
            bands.setdefault(band, []).append(rec)

        for band in sorted(bands, reverse=True):
            band_rows = bands[band]
            centre = family_index * 30.0

            def row_key(rec: dict[str, Any]):
                h = float(rec["h"])
                # Signed shortest angular displacement from the family centre.
                rel = ((h - centre + 180.0) % 360.0) - 180.0
                sublane = 0 if rel < 0.0 else 1
                return (
                    sublane,
                    float(rec["C"]),
                    abs(rel),
                    -float(rec["L"]),
                    rec["name"].casefold(),
                    rec["key"],
                )

            ordered_band = sorted(band_rows, key=row_key)
            for offset in range(0, len(ordered_band), columns):
                chunk = ordered_band[offset:offset + columns]
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": family_name,
                        "family_index": family_index,
                        "lightness_band": band,
                        "neutral_kind": "",
                        "L": rec["L"],
                        "C": rec["C"],
                        "h": rec["h"],
                    }

        end_row = len(grid) - 1
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, end_row)
        for _ in range(separator_rows):
            grid.append([None] * columns)

    for family_index, block in enumerate(chromatic):
        push_records(block, HUE_FAMILY_12[family_index], family_index)

    # Neutrals use lightness, not hue.  Within each lightness slice, chroma is
    # kept low -> high so tinted whites / greys progress gently.
    if neutral:
        start_row = len(grid)
        bands: dict[int, list[dict[str, Any]]] = {}
        for rec in neutral:
            band = int(math.floor(float(rec["L"]) / lightness_band))
            bands.setdefault(band, []).append(rec)
        for band in sorted(bands, reverse=True):
            band_rows = sorted(
                bands[band],
                key=lambda r: (float(r["C"]), float(r["h"]), -float(r["L"]), r["name"].casefold(), r["key"]),
            )
            for offset in range(0, len(band_rows), columns):
                chunk = band_rows[offset:offset + columns]
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": "Neutral / White / Black",
                        "family_index": 99,
                        "lightness_band": band,
                        "neutral_kind": rec["v2_neutral_kind"],
                        "L": rec["L"],
                        "C": rec["C"],
                        "h": rec["h"],
                    }
        family_counts["Neutral / White / Black"] = len(neutral)
        family_row_ranges["Neutral / White / Black"] = (start_row, len(grid) - 1)

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid,
        "keys": flat_keys,
        "slot_info": slot_info,
        "family_counts": family_counts,
        "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral),
        "columns": columns,
        "lightness_band": lightness_band,
        "separator_rows": separator_rows,
    }


# HF78 / V2.1 ---------------------------------------------------------------
def _v21_neutral_kind(L: float, C: float) -> str:
    """Appearance routing for the HF78 V2.1 offline audit.

    V2.1 keeps the conservative HF77 white rule, but adds a soft shoulder for
    slightly darker off-whites.  This removes a hard L*=90 discontinuity where
    two visually near-identical yarn/sock measurements can otherwise be sent
    to different semantic sections.  The general neutral and black rules stay
    intentionally conservative so dark navy / brown samples are not erased as
    chromatic colours merely because their C* is modest.
    """
    if (L >= 90.0 and C <= 14.0) or (L >= 87.0 and C <= 7.0):
        return "White"
    if L <= 20.0 and C <= 7.0:
        return "Black"
    if C <= 5.0:
        return "Neutral"
    return ""


def _v21_lightness_shelves(block: list[dict[str, Any]], max_span: float) -> list[list[dict[str, Any]]]:
    """Create data-adaptive lightness shelves without fixed 10-L* boundaries."""
    if not block:
        return []
    ordered = sorted(block, key=lambda r: (-float(r["L"]), r["name"].casefold(), r["key"]))
    shelves: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    top_L = 0.0
    for rec in ordered:
        L = float(rec["L"])
        if not current:
            current = [rec]
            top_L = L
        elif top_L - L <= max_span:
            current.append(rec)
        else:
            shelves.append(current)
            current = [rec]
            top_L = L
    if current:
        shelves.append(current)
    return shelves


def visual_palette_layout_v21(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 7.5,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF78 audit-only Visual Palette V2.1 layout.

    Changes from V2 are deliberately narrow and data-driven:
    - keep the successful 12-family hue skeleton;
    - replace fixed 10-L* bins with adaptive shelves (avoids hard bin edges);
    - sort hue monotonically inside a shelf before C* (fewer local hue jumps);
    - split White / Neutral Grey / Black into explicit semantic sections;
    - soften only the white boundary around L*=90 to avoid near-identical
      yarn/sock measurements being split by a single hard threshold.

    This remains a deterministic review heuristic, not a CIE/ISO formula and
    not a production ordering in HF78.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_groups: dict[str, list[dict[str, Any]]] = {"White": [], "Neutral Grey": [], "Black": []}

    for rec in records:
        kind = _v21_neutral_kind(float(rec["L"]), float(rec["C"]))
        rec["v21_neutral_kind"] = kind
        if kind:
            group_name = "Neutral Grey" if kind == "Neutral" else kind
            rec["v21_family"] = -1
            rec["v21_family_name"] = group_name
            neutral_groups[group_name].append(rec)
            continue
        family = int(((float(rec["h"]) + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        rec["v21_family"] = family
        rec["v21_family_name"] = HUE_FAMILY_12[family]
        chromatic[family].append(rec)

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            def signed_rel(rec: dict[str, Any]) -> float:
                return ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0
            # Hue moves monotonically from the low-angle side to the high-angle
            # side of the family. C* is only the secondary coordinate.
            ordered = sorted(
                shelf,
                key=lambda rec: (
                    signed_rel(rec),
                    float(rec["C"]),
                    -float(rec["L"]),
                    rec["name"].casefold(),
                    rec["key"],
                ),
            )
            for offset in range(0, len(ordered), columns):
                chunk = ordered[offset:offset + columns]
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": family_name,
                        "family_index": family_index,
                        "lightness_band": shelf_index,
                        "neutral_kind": "",
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": "LOW" if float(rec["C"]) < 10.0 else "NORMAL",
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # White: arrange by L* first, then tint from warm (positive b*) toward cool
    # (negative b*). Near-neutral hue angle is deliberately not the primary key.
    for group_name in ("White", "Neutral Grey", "Black"):
        block = neutral_groups[group_name]
        if not block:
            continue
        start_row = len(grid)
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            if group_name == "White":
                ordered = sorted(shelf, key=lambda r: (-float(r["b"]), float(r["a"]), float(r["C"]), -float(r["L"]), r["name"].casefold(), r["key"]))
            else:
                ordered = sorted(shelf, key=lambda r: (float(r["C"]), -float(r["L"]), float(r["a"]), float(r["b"]), r["name"].casefold(), r["key"]))
            for offset in range(0, len(ordered), columns):
                chunk = ordered[offset:offset + columns]
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": group_name,
                        "family_index": 90 if group_name == "White" else 91 if group_name == "Neutral Grey" else 92,
                        "lightness_band": shelf_index,
                        "neutral_kind": rec["v21_neutral_kind"],
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": "N/A",
                    }
        family_counts[group_name] = len(block)
        family_row_ranges[group_name] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2.1 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid,
        "keys": flat_keys,
        "slot_info": slot_info,
        "family_counts": family_counts,
        "family_row_ranges": family_row_ranges,
        "neutral_count": sum(len(v) for v in neutral_groups.values()),
        "columns": columns,
        "lightness_span": lightness_span,
        "version": "V2.1",
    }


# HF79 / V2.2 ---------------------------------------------------------------

def _v22_neutral_axis(L: float, C: float) -> bool:
    """Route only perceptually dominant whites/blacks and true neutrals to a neutral axis.

    V2.2 intentionally removes the broad C*<=5 hard wall used by V2/V2.1.
    Muted chromatic samples remain in their hue family and are placed at the
    low-chroma edge of that family.  This reduces the common failure where two
    visually near-identical samples straddling C*=5 are sent to unrelated
    sections.
    """
    if C <= 3.0:
        return True
    # Very light colours are perceived primarily as whites/off-whites even
    # when a weak warm/cool tint is measurable.
    if L >= 94.0 and C <= 10.0:
        return True
    if L >= 90.0 and C <= 6.0:
        return True
    # Reserve the neutral axis for only very dark, nearly achromatic blacks;
    # dark navy/brown colours with meaningful chroma remain chromatic.
    if L <= 16.0 and C <= 4.5:
        return True
    return False


def _v22_neutral_kind(L: float) -> str:
    if L >= 85.0:
        return "White"
    if L <= 25.0:
        return "Black"
    return "Neutral Grey"


def _v22_chroma_rows(
    shelf: list[dict[str, Any]],
    *,
    columns: int,
    max_chroma_span: float,
    family_centre: float,
) -> list[list[dict[str, Any]]]:
    """Split one lightness shelf into low->high chroma rows.

    A colour atlas should make a large chroma change understandable rather than
    hide it inside one row.  V2.2 therefore treats chroma as a real layout axis:
    each row is monotonic in C* and is cut when either the display column count
    or a maximum C* span is reached. Hue is only a tie-breaker inside the local
    chroma neighbourhood.
    """
    if not shelf:
        return []

    def signed_rel(rec: dict[str, Any]) -> float:
        return ((float(rec["h"]) - family_centre + 180.0) % 360.0) - 180.0

    ordered = sorted(
        shelf,
        key=lambda rec: (
            float(rec["C"]),
            signed_rel(rec),
            -float(rec["L"]),
            rec["name"].casefold(),
            rec["key"],
        ),
    )
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    row_start_c = 0.0
    for rec in ordered:
        c = float(rec["C"])
        if not current:
            current = [rec]
            row_start_c = c
            continue
        # Start a new row *before* adding the record which would exceed the
        # chroma span.  This keeps every multi-item row directionally legible.
        if len(current) >= columns or c - row_start_c > max_chroma_span:
            out.append(current)
            current = [rec]
            row_start_c = c
        else:
            current.append(rec)
    if current:
        out.append(current)
    return out


def visual_palette_layout_v22(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 7.5,
    chroma_span: float = 25.0,
    soft_boundary_deg: float = 4.0,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF79 audit-only Visual Palette V2.2 layout.

    V2.2 keeps the successful 12-family hue skeleton but changes the internal
    colour-atlas geometry:

    * Hue family = large semantic section only.
    * Lightness = vertical shelf axis (adaptive, not fixed integer bins).
    * Chroma = primary horizontal axis inside a shelf, low -> high.
    * Hue = secondary/tie-break coordinate inside the local chroma range.
    * Adjacent hue-family boundaries are explicitly marked as a soft boundary;
      close colours across neighbouring families are considered bridges, not
      classification failures.
    * White/grey/black share one continuous Neutral Axis instead of three hard
      boxes.  Only true neutrals and perceptually dominant whites/blacks enter
      that axis; muted colours stay in their hue family with LOW hue confidence.

    It remains an offline review heuristic, not a CIE/ISO formula and not a
    production Palette Studio ordering in HF79.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    chroma_span = max(5.0, float(chroma_span))
    soft_boundary_deg = max(0.0, min(15.0, float(soft_boundary_deg)))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_axis: list[dict[str, Any]] = []

    for rec in records:
        L, C, h = float(rec["L"]), float(rec["C"]), float(rec["h"])
        if _v22_neutral_axis(L, C):
            rec["v22_family"] = 99
            rec["v22_family_name"] = "Neutral Axis"
            rec["v22_neutral_kind"] = _v22_neutral_kind(L)
            rec["v22_boundary_zone"] = False
            rec["v22_adjacent_family"] = ""
            neutral_axis.append(rec)
            continue

        family = int(((h + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        centre = family * 30.0
        rel = ((h - centre + 180.0) % 360.0) - 180.0
        edge_distance = max(0.0, 15.0 - abs(rel))
        is_boundary = edge_distance <= soft_boundary_deg
        adjacent_idx = (family + (1 if rel >= 0 else -1)) % 12 if is_boundary else -1
        rec["v22_family"] = family
        rec["v22_family_name"] = HUE_FAMILY_12[family]
        rec["v22_neutral_kind"] = ""
        rec["v22_boundary_zone"] = is_boundary
        rec["v22_boundary_distance"] = edge_distance
        rec["v22_adjacent_family"] = HUE_FAMILY_12[adjacent_idx] if adjacent_idx >= 0 else ""
        chromatic[family].append(rec)

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            chroma_rows = _v22_chroma_rows(
                shelf,
                columns=columns,
                max_chroma_span=chroma_span,
                family_centre=centre,
            )
            for chroma_row_index, chunk in enumerate(chroma_rows):
                # Chroma stays monotonic across the visible row.  Within nearly
                # equal chroma, follow local hue direction for a smoother tint.
                def row_key(rec: dict[str, Any]):
                    rel = ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0
                    return (float(rec["C"]), rel, -float(rec["L"]), rec["name"].casefold(), rec["key"])
                chunk = sorted(chunk, key=row_key)
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    c = float(rec["C"])
                    confidence = "VERY_LOW" if c < 5.0 else "LOW" if c < 10.0 else "NORMAL"
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": family_name,
                        "family_index": family_index,
                        "lightness_band": shelf_index,
                        "chroma_row": chroma_row_index,
                        "neutral_kind": "",
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": confidence,
                        "boundary_zone": bool(rec.get("v22_boundary_zone", False)),
                        "boundary_distance_deg": float(rec.get("v22_boundary_distance", 99.0)),
                        "adjacent_family": rec.get("v22_adjacent_family", ""),
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # One continuous neutral axis: light -> dark. White/Grey/Black remain useful
    # semantic labels in metadata, but do not create hard layout walls.
    if neutral_axis:
        start_row = len(grid)
        shelves = _v21_lightness_shelves(neutral_axis, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            # Warm tint -> cool tint for approximately equal lightness, while
            # keeping chroma weak and visually interpretable.
            ordered = sorted(
                shelf,
                key=lambda r: (
                    float(r["C"]),
                    -float(r["b"]),
                    float(r["a"]),
                    -float(r["L"]),
                    r["name"].casefold(),
                    r["key"],
                ),
            )
            for offset in range(0, len(ordered), columns):
                chunk = ordered[offset:offset + columns]
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index,
                        "column": col_index,
                        "family": "Neutral Axis",
                        "family_index": 99,
                        "lightness_band": shelf_index,
                        "chroma_row": 0,
                        "neutral_kind": rec["v22_neutral_kind"],
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": "N/A",
                        "boundary_zone": False,
                        "boundary_distance_deg": 99.0,
                        "adjacent_family": "",
                    }
        family_counts["Neutral Axis"] = len(neutral_axis)
        family_row_ranges["Neutral Axis"] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2.2 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid,
        "keys": flat_keys,
        "slot_info": slot_info,
        "family_counts": family_counts,
        "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral_axis),
        "columns": columns,
        "lightness_span": lightness_span,
        "chroma_span": chroma_span,
        "soft_boundary_deg": soft_boundary_deg,
        "version": "V2.2",
    }

# ---------------------------------------------------------------------------
# HF80 / Visual Palette Arrangement V2.3 (offline validation only)
# ---------------------------------------------------------------------------

def _v23_neutral_field(L: float, C: float) -> tuple[bool, str]:
    """Route visually near-neutral colours into one continuous neutral field.

    V2.2 exposed a hard C*=3 wall in dark navy/black datasets and a similar
    white/off-white split at high L*.  V2.3 therefore treats neutrality as a
    *field* rather than an axis.  The thresholds are deliberately conservative
    and L*-dependent: weak tints remain visible in the neutral field instead of
    being forced into a 30-degree hue family where hue angle is poorly
    conditioned.

    This is an engineering colour-appearance heuristic for offline validation,
    not a CIE/ISO named-colour classification.
    """
    L = float(L); C = float(C)
    if C <= 2.5:
        return True, "Neutral Core"
    # Off-whites / tinted whites: colour is perceived primarily as white.
    if L >= 88.0 and C <= 12.0:
        return True, "Tinted White"
    # Blue-black / brown-black / tinted black transition.  The HF79 multi-file
    # audit showed that C*=3..6 at L*~14..25 is frequently visually adjacent to
    # nominal blacks, so do not create a hard hue-family wall here.
    if L <= 25.0 and C <= 6.0:
        return True, "Tinted Black"
    # Mid-value weak tints are better represented next to neutral greys than as
    # fully chromatic family members.  Stronger muted colours remain chromatic.
    if C <= 5.0:
        return True, "Tinted Grey"
    return False, ""


def _v23_chroma_rows(
    shelf: list[dict[str, Any]],
    *,
    columns: int,
    family_centre: float,
) -> list[list[dict[str, Any]]]:
    """Create low->high chroma rows with adaptive span *and* step guards.

    V2.2 allowed a single row to span, for example, C*=4 -> 27 or C*=50 -> 74.
    Direction was monotonic but the visual jump was still large.  V2.3 keeps
    chroma directional while cutting a row when either the total span or the
    next local C* step becomes too large.  Low-chroma rows are intentionally
    tighter because a small absolute C* change near the neutral field changes
    appearance category more strongly than the same change at high chroma.
    """
    if not shelf:
        return []

    def signed_rel(rec: dict[str, Any]) -> float:
        return ((float(rec["h"]) - family_centre + 180.0) % 360.0) - 180.0

    ordered = sorted(
        shelf,
        key=lambda rec: (
            float(rec["C"]),
            signed_rel(rec),
            -float(rec["L"]),
            rec["name"].casefold(),
            rec["key"],
        ),
    )

    def span_limit(start_c: float) -> float:
        if start_c < 10.0:
            return 8.0
        if start_c < 30.0:
            return 14.0
        return 20.0

    def step_limit(prev_c: float) -> float:
        if prev_c < 10.0:
            return 6.0
        if prev_c < 30.0:
            return 10.0
        return 15.0

    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    row_start_c = 0.0
    prev_c = 0.0
    for rec in ordered:
        c = float(rec["C"])
        if not current:
            current = [rec]
            row_start_c = prev_c = c
            continue
        split = (
            len(current) >= columns
            or c - row_start_c > span_limit(row_start_c)
            or c - prev_c > step_limit(prev_c)
        )
        if split:
            out.append(current)
            current = [rec]
            row_start_c = c
        else:
            current.append(rec)
        prev_c = c
    if current:
        out.append(current)
    return out


def visual_palette_layout_v23(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 6.0,
    soft_boundary_deg: float = 4.0,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF80 audit-only Visual Palette V2.3 layout.

    V2.3 is driven by the HF79 multi-file audit (adidas, Apple, FIGS, Nike,
    dark-grey/black sets and Smart Color):

    * 12 hue families remain the *strong-chromatic* semantic skeleton;
    * L* shelves are slightly tighter (default 6) for smoother vertical levels;
    * C* rows use adaptive span/step guards rather than one fixed 25-C* span;
    * one continuous Neutral Field absorbs true neutrals plus weakly tinted
      whites, greys and blacks, eliminating the C*=3 dark navy/black hard wall;
    * low-chroma hue remains metadata, not a hard visual family boundary;
    * input/output integrity remains one-to-one.

    This is still an offline review heuristic, not a CIE/ISO formula and not a
    production Palette Studio ordering in HF80.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    soft_boundary_deg = max(0.0, min(15.0, float(soft_boundary_deg)))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_field: list[dict[str, Any]] = []

    for rec in records:
        L, C, h = float(rec["L"]), float(rec["C"]), float(rec["h"])
        is_neutral, neutral_kind = _v23_neutral_field(L, C)
        if is_neutral:
            rec["v23_family"] = 99
            rec["v23_family_name"] = "Neutral Field"
            rec["v23_neutral_kind"] = neutral_kind
            rec["v23_boundary_zone"] = False
            rec["v23_adjacent_family"] = ""
            neutral_field.append(rec)
            continue

        family = int(((h + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        centre = family * 30.0
        rel = ((h - centre + 180.0) % 360.0) - 180.0
        edge_distance = max(0.0, 15.0 - abs(rel))
        is_boundary = edge_distance <= soft_boundary_deg
        adjacent_idx = (family + (1 if rel >= 0 else -1)) % 12 if is_boundary else -1
        rec["v23_family"] = family
        rec["v23_family_name"] = HUE_FAMILY_12[family]
        rec["v23_neutral_kind"] = ""
        rec["v23_boundary_zone"] = is_boundary
        rec["v23_boundary_distance"] = edge_distance
        rec["v23_adjacent_family"] = HUE_FAMILY_12[adjacent_idx] if adjacent_idx >= 0 else ""
        chromatic[family].append(rec)

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            chroma_rows = _v23_chroma_rows(shelf, columns=columns, family_centre=centre)
            for chroma_row_index, chunk in enumerate(chroma_rows):
                chunk = sorted(
                    chunk,
                    key=lambda rec: (
                        float(rec["C"]),
                        ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0,
                        -float(rec["L"]),
                        rec["name"].casefold(), rec["key"],
                    ),
                )
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid)
                grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    c = float(rec["C"])
                    confidence = "VERY_LOW" if c < 8.0 else "LOW" if c < 12.0 else "NORMAL"
                    slot_info[rec["key"]] = {
                        "row": row_index, "column": col_index,
                        "family": family_name, "family_index": family_index,
                        "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                        "neutral_kind": "", "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": confidence,
                        "boundary_zone": bool(rec.get("v23_boundary_zone", False)),
                        "boundary_distance_deg": float(rec.get("v23_boundary_distance", 99.0)),
                        "adjacent_family": rec.get("v23_adjacent_family", ""),
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # Neutral Field: light -> dark shelves.  C* is still a horizontal strength
    # axis, while hue angle is only a tie-breaker for weak tints.
    if neutral_field:
        start_row = len(grid)
        shelves = _v21_lightness_shelves(neutral_field, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            ordered = sorted(
                shelf,
                key=lambda r: (
                    float(r["C"]),
                    float(r["h"]) if float(r["C"]) > 2.5 else 0.0,
                    -float(r["L"]),
                    r["name"].casefold(), r["key"],
                ),
            )
            # Neutral-field rows use a tighter C* span; weak tint should grow
            # smoothly away from the neutral core rather than jump abruptly.
            chunks: list[list[dict[str, Any]]] = []
            cur: list[dict[str, Any]] = []
            start_c = prev_c = 0.0
            for rec in ordered:
                c = float(rec["C"])
                if not cur:
                    cur = [rec]; start_c = prev_c = c; continue
                if len(cur) >= columns or c - start_c > 5.0 or c - prev_c > 3.5:
                    chunks.append(cur); cur = [rec]; start_c = c
                else:
                    cur.append(rec)
                prev_c = c
            if cur: chunks.append(cur)

            for chroma_row_index, chunk in enumerate(chunks):
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid); grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    slot_info[rec["key"]] = {
                        "row": row_index, "column": col_index,
                        "family": "Neutral Field", "family_index": 99,
                        "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                        "neutral_kind": rec["v23_neutral_kind"],
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": "N/A",
                        "boundary_zone": False, "boundary_distance_deg": 99.0,
                        "adjacent_family": "",
                    }
        family_counts["Neutral Field"] = len(neutral_field)
        family_row_ranges["Neutral Field"] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2.3 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid, "keys": flat_keys, "slot_info": slot_info,
        "family_counts": family_counts, "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral_field), "columns": columns,
        "lightness_span": lightness_span, "soft_boundary_deg": soft_boundary_deg,
        "version": "V2.3",
    }


# ---------------------------------------------------------------------------
# HF81 / Visual Palette Arrangement V2.4 (offline validation only)
# ---------------------------------------------------------------------------

def _v24_neutral_field(L: float, C: float) -> tuple[bool, str]:
    """Perceptual neutral-field envelope refined from the V2.3 multi-file audit.

    V2.3 left a very narrow high-L* boundary: adidas blue-whites around
    L*=92..94 and C*=12.1..12.4 were treated as Blue-Violet while visually
    near-identical whites at C*=11.8 were placed in Neutral Field.  V2.4 uses
    a smooth high-lightness envelope rather than one C*=12 hard wall.

    Dark and mid-value rules intentionally stay conservative because V2.3
    already resolved the Smart Color / Air Canada dark neutral failures.
    This remains an engineering colour-appearance heuristic, not a CIE/ISO
    named-colour classification.
    """
    L = float(L); C = float(C)
    if C <= 2.5:
        return True, "Neutral Core"

    # Tinted whites: allow slightly more measured chroma as L* approaches
    # paper/optical-white territory.  The envelope is continuous and capped,
    # avoiding both the old C*=12 cliff and uncontrolled pastel absorption.
    if L >= 88.0:
        white_limit = min(14.5, 12.0 + 0.50 * (L - 88.0))
        if C <= white_limit:
            return True, "Tinted White"

    # Keep the already validated dark-neutral transition from V2.3.
    if L <= 25.0 and C <= 6.0:
        return True, "Tinted Black"

    if C <= 5.0:
        return True, "Tinted Grey"
    return False, ""


def _v24_neutral_tint_sector(rec: dict[str, Any]) -> tuple[int, str]:
    """Broad tint direction for weak-chroma neutrals.

    Hue angle is unstable close to the neutral axis, so V2.4 does not use it
    as a fine ordering coordinate.  Above the neutral core it is only used to
    create four broad tint lanes.  This prevents opposite weak tints (for
    example green-grey next to red-violet grey) from sharing one row simply
    because their C* values are similar.
    """
    C = float(rec["C"])
    if C <= 2.5:
        return 0, "Neutral Core"
    h = float(rec["h"]) % 360.0
    if h >= 315.0 or h < 45.0:
        return 1, "Warm / Red Tint"
    if h < 135.0:
        return 2, "Yellow / Green Tint"
    if h < 225.0:
        return 3, "Green / Cyan Tint"
    return 4, "Blue / Violet Tint"


def visual_palette_layout_v24(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 6.0,
    soft_boundary_deg: float = 4.0,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF81 audit-only Visual Palette V2.4 layout.

    Targeted refinements only; the successful V2.3 architecture is preserved:
    - same 12 strong-chromatic hue-family skeleton;
    - same 6-L* adaptive shelves and adaptive C* row guards;
    - smooth high-L* neutral envelope for blue-/warm-whites;
    - Neutral Field uses broad tint lanes inside each lightness shelf so
      opposite weak tints are not forced into the same row by C* alone.

    V2.4 remains offline validation and is not wired into Palette Studio.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    soft_boundary_deg = max(0.0, min(15.0, float(soft_boundary_deg)))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_field: list[dict[str, Any]] = []

    for rec in records:
        L, C, h = float(rec["L"]), float(rec["C"]), float(rec["h"])
        is_neutral, neutral_kind = _v24_neutral_field(L, C)
        if is_neutral:
            rec["v24_family"] = 99
            rec["v24_family_name"] = "Neutral Field"
            rec["v24_neutral_kind"] = neutral_kind
            rec["v24_boundary_zone"] = False
            rec["v24_adjacent_family"] = ""
            sector_idx, sector_name = _v24_neutral_tint_sector(rec)
            rec["v24_neutral_tint_sector"] = sector_idx
            rec["v24_neutral_tint_name"] = sector_name
            neutral_field.append(rec)
            continue

        family = int(((h + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        centre = family * 30.0
        rel = ((h - centre + 180.0) % 360.0) - 180.0
        edge_distance = max(0.0, 15.0 - abs(rel))
        is_boundary = edge_distance <= soft_boundary_deg
        adjacent_idx = (family + (1 if rel >= 0 else -1)) % 12 if is_boundary else -1
        rec["v24_family"] = family
        rec["v24_family_name"] = HUE_FAMILY_12[family]
        rec["v24_neutral_kind"] = ""
        rec["v24_neutral_tint_sector"] = -1
        rec["v24_neutral_tint_name"] = ""
        rec["v24_boundary_zone"] = is_boundary
        rec["v24_boundary_distance"] = edge_distance
        rec["v24_adjacent_family"] = HUE_FAMILY_12[adjacent_idx] if adjacent_idx >= 0 else ""
        chromatic[family].append(rec)

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            chroma_rows = _v23_chroma_rows(shelf, columns=columns, family_centre=centre)
            for chroma_row_index, chunk in enumerate(chroma_rows):
                chunk = sorted(
                    chunk,
                    key=lambda rec: (
                        float(rec["C"]),
                        ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0,
                        -float(rec["L"]), rec["name"].casefold(), rec["key"],
                    ),
                )
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid); grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    c = float(rec["C"])
                    confidence = "VERY_LOW" if c < 8.0 else "LOW" if c < 12.0 else "NORMAL"
                    slot_info[rec["key"]] = {
                        "row": row_index, "column": col_index,
                        "family": family_name, "family_index": family_index,
                        "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                        "neutral_kind": "", "neutral_tint": "",
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": confidence,
                        "boundary_zone": bool(rec.get("v24_boundary_zone", False)),
                        "boundary_distance_deg": float(rec.get("v24_boundary_distance", 99.0)),
                        "adjacent_family": rec.get("v24_adjacent_family", ""),
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # Neutral Field: L* remains the vertical skeleton.  Within each L* shelf,
    # use broad tint lanes first and C* strength second.  This is deliberately
    # coarse: near-neutral hue is not treated as a precise colour coordinate.
    if neutral_field:
        start_row = len(grid)
        shelves = _v21_lightness_shelves(neutral_field, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            by_sector: dict[int, list[dict[str, Any]]] = {}
            for rec in shelf:
                sec = int(rec.get("v24_neutral_tint_sector", 0))
                by_sector.setdefault(sec, []).append(rec)

            chroma_row_index = 0
            for sec in (0, 1, 2, 3, 4):
                sector = by_sector.get(sec, [])
                if not sector:
                    continue
                sector = sorted(
                    sector,
                    key=lambda r: (
                        float(r["C"]),
                        float(r["h"]) if float(r["C"]) > 2.5 else 0.0,
                        -float(r["L"]), r["name"].casefold(), r["key"],
                    ),
                )
                cur: list[dict[str, Any]] = []
                start_c = prev_c = 0.0
                chunks: list[list[dict[str, Any]]] = []
                for rec in sector:
                    c = float(rec["C"])
                    if not cur:
                        cur = [rec]; start_c = prev_c = c; continue
                    # Core neutrals can stay compact.  Tinted lanes use the
                    # same conservative C* guards as V2.3.
                    max_span = 4.0 if sec == 0 else 5.0
                    max_step = 3.0 if sec == 0 else 3.5
                    if len(cur) >= columns or c - start_c > max_span or c - prev_c > max_step:
                        chunks.append(cur); cur = [rec]; start_c = c
                    else:
                        cur.append(rec)
                    prev_c = c
                if cur:
                    chunks.append(cur)

                for chunk in chunks:
                    row_keys: list[str | None] = [rec["key"] for rec in chunk]
                    row_keys.extend([None] * (columns - len(row_keys)))
                    row_index = len(grid); grid.append(row_keys)
                    for col_index, rec in enumerate(chunk):
                        slot_info[rec["key"]] = {
                            "row": row_index, "column": col_index,
                            "family": "Neutral Field", "family_index": 99,
                            "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                            "neutral_kind": rec["v24_neutral_kind"],
                            "neutral_tint": rec.get("v24_neutral_tint_name", ""),
                            "L": rec["L"], "C": rec["C"], "h": rec["h"],
                            "hue_confidence": "N/A",
                            "boundary_zone": False, "boundary_distance_deg": 99.0,
                            "adjacent_family": "",
                        }
                    chroma_row_index += 1

        family_counts["Neutral Field"] = len(neutral_field)
        family_row_ranges["Neutral Field"] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2.4 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid, "keys": flat_keys, "slot_info": slot_info,
        "family_counts": family_counts, "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral_field), "columns": columns,
        "lightness_span": lightness_span, "soft_boundary_deg": soft_boundary_deg,
        "version": "V2.4",
    }


# ---------------------------------------------------------------------------
# HF82 / Visual Palette Arrangement V2.4.1 (offline validation only)
# ---------------------------------------------------------------------------

def visual_palette_layout_v241(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 6.0,
    soft_boundary_deg: float = 4.0,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF82 audit-only Visual Palette V2.4.1 layout.

    Targeted refinements only; the successful V2.3 architecture is preserved:
    - same 12 strong-chromatic hue-family skeleton;
    - same 6-L* adaptive shelves and adaptive C* row guards;
    - keep the V2.4 smooth high-L* neutral envelope;
    - additionally absorb only data-supported near-white bridges (L*>=90, C*<=18, ΔE00<3 to an initial Tinted White);
    - Neutral Field uses broad tint lanes inside each lightness shelf so
      opposite weak tints are not forced into the same row by C* alone.

    V2.4.1 remains offline validation and is not wired into Palette Studio.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    soft_boundary_deg = max(0.0, min(15.0, float(soft_boundary_deg)))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_field: list[dict[str, Any]] = []

    for rec in records:
        L, C, h = float(rec["L"]), float(rec["C"]), float(rec["h"])
        is_neutral, neutral_kind = _v24_neutral_field(L, C)
        if is_neutral:
            rec["v241_family"] = 99
            rec["v241_family_name"] = "Neutral Field"
            rec["v241_neutral_kind"] = neutral_kind
            rec["v241_boundary_zone"] = False
            rec["v241_adjacent_family"] = ""
            sector_idx, sector_name = _v24_neutral_tint_sector(rec)
            rec["v241_neutral_tint_sector"] = sector_idx
            rec["v241_neutral_tint_name"] = sector_name
            neutral_field.append(rec)
            continue

        family = int(((h + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        centre = family * 30.0
        rel = ((h - centre + 180.0) % 360.0) - 180.0
        edge_distance = max(0.0, 15.0 - abs(rel))
        is_boundary = edge_distance <= soft_boundary_deg
        adjacent_idx = (family + (1 if rel >= 0 else -1)) % 12 if is_boundary else -1
        rec["v241_family"] = family
        rec["v241_family_name"] = HUE_FAMILY_12[family]
        rec["v241_neutral_kind"] = ""
        rec["v241_neutral_tint_sector"] = -1
        rec["v241_neutral_tint_name"] = ""
        rec["v241_boundary_zone"] = is_boundary
        rec["v241_boundary_distance"] = edge_distance
        rec["v241_adjacent_family"] = HUE_FAMILY_12[adjacent_idx] if adjacent_idx >= 0 else ""
        rec["v241_near_white_bridge"] = False
        chromatic[family].append(rec)

    # V2.4.1: data-adaptive near-white bridge absorption.
    # Do not globally widen the white threshold.  Instead, only promote a
    # high-L* pastel into Neutral Field when this *same dataset* already
    # contains an initial Tinted White within ΔE00 < 3.  This removes the
    # YKK/Walmart blue-white cliff without swallowing unrelated pale colours.
    seed_whites = [rec for rec in neutral_field if rec.get("v241_neutral_kind") == "Tinted White"]
    near_white_promotions: list[dict[str, Any]] = []
    if seed_whites:
        for family_index in range(len(chromatic)):
            kept: list[dict[str, Any]] = []
            for rec in chromatic[family_index]:
                L = float(rec["L"]); C = float(rec["C"])
                if L >= 90.0 and C <= 18.0:
                    nearest = min(seed_whites, key=lambda w: _de00_lab((rec["L"], rec["a"], rec["b"]), (w["L"], w["a"], w["b"])))
                    de00 = _de00_lab((rec["L"], rec["a"], rec["b"]), (nearest["L"], nearest["a"], nearest["b"]))
                    if de00 < 3.0:
                        old_family = rec.get("v241_family_name", "")
                        rec["v241_family"] = 99
                        rec["v241_family_name"] = "Neutral Field"
                        rec["v241_neutral_kind"] = "Tinted White Bridge"
                        rec["v241_boundary_zone"] = False
                        rec["v241_adjacent_family"] = ""
                        sector_idx, sector_name = _v24_neutral_tint_sector(rec)
                        rec["v241_neutral_tint_sector"] = sector_idx
                        rec["v241_neutral_tint_name"] = sector_name
                        rec["v241_near_white_bridge"] = True
                        rec["v241_bridge_de00"] = float(de00)
                        rec["v241_bridge_seed"] = nearest.get("name", nearest.get("key", ""))
                        rec["v241_bridge_from_family"] = old_family
                        neutral_field.append(rec)
                        near_white_promotions.append(rec)
                        continue
                kept.append(rec)
            chromatic[family_index] = kept

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            chroma_rows = _v23_chroma_rows(shelf, columns=columns, family_centre=centre)
            for chroma_row_index, chunk in enumerate(chroma_rows):
                chunk = sorted(
                    chunk,
                    key=lambda rec: (
                        float(rec["C"]),
                        ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0,
                        -float(rec["L"]), rec["name"].casefold(), rec["key"],
                    ),
                )
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid); grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    c = float(rec["C"])
                    confidence = "VERY_LOW" if c < 8.0 else "LOW" if c < 12.0 else "NORMAL"
                    slot_info[rec["key"]] = {
                        "row": row_index, "column": col_index,
                        "family": family_name, "family_index": family_index,
                        "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                        "neutral_kind": "", "neutral_tint": "",
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": confidence,
                        "boundary_zone": bool(rec.get("v241_boundary_zone", False)),
                        "boundary_distance_deg": float(rec.get("v241_boundary_distance", 99.0)),
                        "adjacent_family": rec.get("v241_adjacent_family", ""),
                        "near_white_bridge": False, "bridge_de00": 0.0, "bridge_seed": "", "bridge_from_family": "",
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # Neutral Field: L* remains the vertical skeleton.  Within each L* shelf,
    # use broad tint lanes first and C* strength second.  This is deliberately
    # coarse: near-neutral hue is not treated as a precise colour coordinate.
    if neutral_field:
        start_row = len(grid)
        shelves = _v21_lightness_shelves(neutral_field, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            by_sector: dict[int, list[dict[str, Any]]] = {}
            for rec in shelf:
                sec = int(rec.get("v241_neutral_tint_sector", 0))
                by_sector.setdefault(sec, []).append(rec)

            chroma_row_index = 0
            for sec in (0, 1, 2, 3, 4):
                sector = by_sector.get(sec, [])
                if not sector:
                    continue
                sector = sorted(
                    sector,
                    key=lambda r: (
                        float(r["C"]),
                        float(r["h"]) if float(r["C"]) > 2.5 else 0.0,
                        -float(r["L"]), r["name"].casefold(), r["key"],
                    ),
                )
                cur: list[dict[str, Any]] = []
                start_c = prev_c = 0.0
                chunks: list[list[dict[str, Any]]] = []
                for rec in sector:
                    c = float(rec["C"])
                    if not cur:
                        cur = [rec]; start_c = prev_c = c; continue
                    # Core neutrals can stay compact.  Tinted lanes use the
                    # same conservative C* guards as V2.3.
                    max_span = 4.0 if sec == 0 else 5.0
                    max_step = 3.0 if sec == 0 else 3.5
                    if len(cur) >= columns or c - start_c > max_span or c - prev_c > max_step:
                        chunks.append(cur); cur = [rec]; start_c = c
                    else:
                        cur.append(rec)
                    prev_c = c
                if cur:
                    chunks.append(cur)

                for chunk in chunks:
                    row_keys: list[str | None] = [rec["key"] for rec in chunk]
                    row_keys.extend([None] * (columns - len(row_keys)))
                    row_index = len(grid); grid.append(row_keys)
                    for col_index, rec in enumerate(chunk):
                        slot_info[rec["key"]] = {
                            "row": row_index, "column": col_index,
                            "family": "Neutral Field", "family_index": 99,
                            "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                            "neutral_kind": rec["v241_neutral_kind"],
                            "neutral_tint": rec.get("v241_neutral_tint_name", ""),
                            "L": rec["L"], "C": rec["C"], "h": rec["h"],
                            "hue_confidence": "N/A",
                            "boundary_zone": False, "boundary_distance_deg": 99.0,
                            "adjacent_family": "",
                            "near_white_bridge": bool(rec.get("v241_near_white_bridge", False)),
                            "bridge_de00": float(rec.get("v241_bridge_de00", 0.0)),
                            "bridge_seed": rec.get("v241_bridge_seed", ""),
                            "bridge_from_family": rec.get("v241_bridge_from_family", ""),
                        }
                    chroma_row_index += 1

        family_counts["Neutral Field"] = len(neutral_field)
        family_row_ranges["Neutral Field"] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette V2.4.1 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid, "keys": flat_keys, "slot_info": slot_info,
        "family_counts": family_counts, "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral_field), "columns": columns,
        "lightness_span": lightness_span, "soft_boundary_deg": soft_boundary_deg,
        "near_white_bridge_count": len(near_white_promotions),
        "near_white_bridge_keys": [rec["key"] for rec in near_white_promotions],
        "version": "V2.4.1",
    }



# HF83 / Visual Palette Arrangement V2.4.2 (offline validation only)
# ---------------------------------------------------------------------------

def visual_palette_layout_v242(
    rows: Sequence[tuple[str, str, Sequence[float]]],
    *,
    columns: int = 6,
    lightness_span: float = 6.0,
    soft_boundary_deg: float = 4.0,
    separator_rows: int = 0,
) -> dict[str, Any]:
    """HF83 audit-only Visual Palette V2.4.2 layout.

    Targeted refinements only; the successful V2.3 architecture is preserved:
    - same 12 strong-chromatic hue-family skeleton;
    - same 6-L* adaptive shelves and adaptive C* row guards;
    - keep the V2.4 smooth high-L* neutral envelope;
    - keep V2.4.1 direct near-white absorption, then allow one tightly bounded second-hop continuity bridge for the same near-white cluster;
    - Neutral Field uses broad tint lanes inside each lightness shelf so
      opposite weak tints are not forced into the same row by C* alone.

    V2.4.2 remains offline validation and is not wired into Palette Studio.
    """
    columns = max(1, int(columns))
    lightness_span = max(2.0, float(lightness_span))
    soft_boundary_deg = max(0.0, min(15.0, float(soft_boundary_deg)))
    separator_rows = max(0, int(separator_rows))

    records = [_appearance_record(key, name, lab) for key, name, lab in rows]
    chromatic: list[list[dict[str, Any]]] = [[] for _ in HUE_FAMILY_12]
    neutral_field: list[dict[str, Any]] = []

    for rec in records:
        L, C, h = float(rec["L"]), float(rec["C"]), float(rec["h"])
        is_neutral, neutral_kind = _v24_neutral_field(L, C)
        if is_neutral:
            rec["v242_family"] = 99
            rec["v242_family_name"] = "Neutral Field"
            rec["v242_neutral_kind"] = neutral_kind
            rec["v242_boundary_zone"] = False
            rec["v242_adjacent_family"] = ""
            sector_idx, sector_name = _v24_neutral_tint_sector(rec)
            rec["v242_neutral_tint_sector"] = sector_idx
            rec["v242_neutral_tint_name"] = sector_name
            neutral_field.append(rec)
            continue

        family = int(((h + 15.0) % 360.0) // 30.0)
        family = min(11, max(0, family))
        centre = family * 30.0
        rel = ((h - centre + 180.0) % 360.0) - 180.0
        edge_distance = max(0.0, 15.0 - abs(rel))
        is_boundary = edge_distance <= soft_boundary_deg
        adjacent_idx = (family + (1 if rel >= 0 else -1)) % 12 if is_boundary else -1
        rec["v242_family"] = family
        rec["v242_family_name"] = HUE_FAMILY_12[family]
        rec["v242_neutral_kind"] = ""
        rec["v242_neutral_tint_sector"] = -1
        rec["v242_neutral_tint_name"] = ""
        rec["v242_boundary_zone"] = is_boundary
        rec["v242_boundary_distance"] = edge_distance
        rec["v242_adjacent_family"] = HUE_FAMILY_12[adjacent_idx] if adjacent_idx >= 0 else ""
        rec["v242_near_white_bridge"] = False
        rec["v242_bridge_generation"] = 0
        rec["v242_bridge_seed_de00"] = 0.0
        rec["v242_bridge_parent"] = ""
        rec["v242_bridge_seed_key"] = ""
        chromatic[family].append(rec)

    # V2.4.2: controlled near-white continuity closure.
    #
    # Stage 1 is identical to V2.4.1: a high-L* pastel can join Neutral Field
    # only when it is within ΔE00 < 3 of an *original* Tinted White seed.
    #
    # Stage 2 addresses the one remaining validated cliff (e.g. CTS-007 WHITE
    # versus CTS-007 WHITE swatch).  It permits exactly one extra hop through a
    # Stage-1 bridge, but prevents runaway chaining by requiring the candidate
    # to remain close to the original seed as well:
    #   - L* >= 90, C* <= 18
    #   - ΔE00(candidate, promoted bridge) < 3
    #   - ΔE00(candidate, original seed) <= 4
    #   - hue direction within 15° of the original seed
    # No Stage-2 result may recruit another colour.
    seed_whites = [rec for rec in neutral_field if rec.get("v242_neutral_kind") == "Tinted White"]
    near_white_promotions: list[dict[str, Any]] = []
    stage1_promotions: list[dict[str, Any]] = []

    def _promote_near_white(rec: dict[str, Any], *, old_family: str, parent: dict[str, Any], seed: dict[str, Any], generation: int, parent_de00: float, seed_de00: float) -> None:
        rec["v242_family"] = 99
        rec["v242_family_name"] = "Neutral Field"
        rec["v242_neutral_kind"] = "Tinted White Bridge" if generation == 1 else "Tinted White Continuity Bridge"
        rec["v242_boundary_zone"] = False
        rec["v242_adjacent_family"] = ""
        sector_idx, sector_name = _v24_neutral_tint_sector(rec)
        rec["v242_neutral_tint_sector"] = sector_idx
        rec["v242_neutral_tint_name"] = sector_name
        rec["v242_near_white_bridge"] = True
        rec["v242_bridge_generation"] = int(generation)
        rec["v242_bridge_de00"] = float(parent_de00)
        rec["v242_bridge_seed_de00"] = float(seed_de00)
        rec["v242_bridge_parent"] = parent.get("name", parent.get("key", ""))
        rec["v242_bridge_seed"] = seed.get("name", seed.get("key", ""))
        rec["v242_bridge_seed_key"] = seed.get("key", "")
        rec["v242_bridge_from_family"] = old_family
        neutral_field.append(rec)
        near_white_promotions.append(rec)

    if seed_whites:
        # Stage 1: direct seed absorption (same rule as V2.4.1).
        for family_index in range(len(chromatic)):
            kept: list[dict[str, Any]] = []
            for rec in chromatic[family_index]:
                L = float(rec["L"]); C = float(rec["C"])
                if L >= 90.0 and C <= 18.0:
                    nearest = min(seed_whites, key=lambda w: _de00_lab((rec["L"], rec["a"], rec["b"]), (w["L"], w["a"], w["b"])))
                    de00 = _de00_lab((rec["L"], rec["a"], rec["b"]), (nearest["L"], nearest["a"], nearest["b"]))
                    if de00 < 3.0:
                        old_family = rec.get("v242_family_name", "")
                        _promote_near_white(rec, old_family=old_family, parent=nearest, seed=nearest, generation=1, parent_de00=de00, seed_de00=de00)
                        stage1_promotions.append(rec)
                        continue
                kept.append(rec)
            chromatic[family_index] = kept

        # Stage 2: one bounded continuity hop through Stage-1 bridges only.
        if stage1_promotions:
            seed_by_key = {str(s.get("key", "")): s for s in seed_whites}
            for family_index in range(len(chromatic)):
                kept: list[dict[str, Any]] = []
                for rec in chromatic[family_index]:
                    L = float(rec["L"]); C = float(rec["C"])
                    promoted = False
                    if L >= 90.0 and C <= 18.0:
                        for parent in sorted(stage1_promotions, key=lambda p: _de00_lab((rec["L"], rec["a"], rec["b"]), (p["L"], p["a"], p["b"]))):
                            parent_de = _de00_lab((rec["L"], rec["a"], rec["b"]), (parent["L"], parent["a"], parent["b"]))
                            if parent_de >= 3.0:
                                break
                            seed = seed_by_key.get(str(parent.get("v242_bridge_seed_key", "")))
                            if seed is None:
                                continue
                            seed_de = _de00_lab((rec["L"], rec["a"], rec["b"]), (seed["L"], seed["a"], seed["b"]))
                            hue_delta = abs((float(rec["h"]) - float(seed["h"]) + 180.0) % 360.0 - 180.0)
                            if seed_de <= 4.0 and hue_delta <= 15.0:
                                old_family = rec.get("v242_family_name", "")
                                _promote_near_white(rec, old_family=old_family, parent=parent, seed=seed, generation=2, parent_de00=parent_de, seed_de00=seed_de)
                                promoted = True
                                break
                    if not promoted:
                        kept.append(rec)
                chromatic[family_index] = kept

    grid: list[list[str | None]] = []
    slot_info: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, int] = {}
    family_row_ranges: dict[str, tuple[int, int]] = {}

    def add_separator():
        for _ in range(separator_rows):
            grid.append([None] * columns)

    def push_chromatic(block: list[dict[str, Any]], family_name: str, family_index: int):
        if not block:
            return
        start_row = len(grid)
        centre = family_index * 30.0
        shelves = _v21_lightness_shelves(block, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            chroma_rows = _v23_chroma_rows(shelf, columns=columns, family_centre=centre)
            for chroma_row_index, chunk in enumerate(chroma_rows):
                chunk = sorted(
                    chunk,
                    key=lambda rec: (
                        float(rec["C"]),
                        ((float(rec["h"]) - centre + 180.0) % 360.0) - 180.0,
                        -float(rec["L"]), rec["name"].casefold(), rec["key"],
                    ),
                )
                row_keys: list[str | None] = [rec["key"] for rec in chunk]
                row_keys.extend([None] * (columns - len(row_keys)))
                row_index = len(grid); grid.append(row_keys)
                for col_index, rec in enumerate(chunk):
                    c = float(rec["C"])
                    confidence = "VERY_LOW" if c < 8.0 else "LOW" if c < 12.0 else "NORMAL"
                    slot_info[rec["key"]] = {
                        "row": row_index, "column": col_index,
                        "family": family_name, "family_index": family_index,
                        "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                        "neutral_kind": "", "neutral_tint": "",
                        "L": rec["L"], "C": rec["C"], "h": rec["h"],
                        "hue_confidence": confidence,
                        "boundary_zone": bool(rec.get("v242_boundary_zone", False)),
                        "boundary_distance_deg": float(rec.get("v242_boundary_distance", 99.0)),
                        "adjacent_family": rec.get("v242_adjacent_family", ""),
                        "near_white_bridge": False, "bridge_generation": 0, "bridge_de00": 0.0, "bridge_seed_de00": 0.0, "bridge_parent": "", "bridge_seed": "", "bridge_from_family": "",
                    }
        family_counts[family_name] = len(block)
        family_row_ranges[family_name] = (start_row, len(grid) - 1)
        add_separator()

    for family_index, block in enumerate(chromatic):
        push_chromatic(block, HUE_FAMILY_12[family_index], family_index)

    # Neutral Field: L* remains the vertical skeleton.  Within each L* shelf,
    # use broad tint lanes first and C* strength second.  This is deliberately
    # coarse: near-neutral hue is not treated as a precise colour coordinate.
    if neutral_field:
        start_row = len(grid)
        shelves = _v21_lightness_shelves(neutral_field, lightness_span)
        for shelf_index, shelf in enumerate(shelves):
            by_sector: dict[int, list[dict[str, Any]]] = {}
            for rec in shelf:
                sec = int(rec.get("v242_neutral_tint_sector", 0))
                by_sector.setdefault(sec, []).append(rec)

            chroma_row_index = 0
            for sec in (0, 1, 2, 3, 4):
                sector = by_sector.get(sec, [])
                if not sector:
                    continue
                sector = sorted(
                    sector,
                    key=lambda r: (
                        float(r["C"]),
                        float(r["h"]) if float(r["C"]) > 2.5 else 0.0,
                        -float(r["L"]), r["name"].casefold(), r["key"],
                    ),
                )
                cur: list[dict[str, Any]] = []
                start_c = prev_c = 0.0
                chunks: list[list[dict[str, Any]]] = []
                for rec in sector:
                    c = float(rec["C"])
                    if not cur:
                        cur = [rec]; start_c = prev_c = c; continue
                    # Core neutrals can stay compact.  Tinted lanes use the
                    # same conservative C* guards as V2.3.
                    max_span = 4.0 if sec == 0 else 5.0
                    max_step = 3.0 if sec == 0 else 3.5
                    if len(cur) >= columns or c - start_c > max_span or c - prev_c > max_step:
                        chunks.append(cur); cur = [rec]; start_c = c
                    else:
                        cur.append(rec)
                    prev_c = c
                if cur:
                    chunks.append(cur)

                for chunk in chunks:
                    row_keys: list[str | None] = [rec["key"] for rec in chunk]
                    row_keys.extend([None] * (columns - len(row_keys)))
                    row_index = len(grid); grid.append(row_keys)
                    for col_index, rec in enumerate(chunk):
                        slot_info[rec["key"]] = {
                            "row": row_index, "column": col_index,
                            "family": "Neutral Field", "family_index": 99,
                            "lightness_band": shelf_index, "chroma_row": chroma_row_index,
                            "neutral_kind": rec["v242_neutral_kind"],
                            "neutral_tint": rec.get("v242_neutral_tint_name", ""),
                            "L": rec["L"], "C": rec["C"], "h": rec["h"],
                            "hue_confidence": "N/A",
                            "boundary_zone": False, "boundary_distance_deg": 99.0,
                            "adjacent_family": "",
                            "near_white_bridge": bool(rec.get("v242_near_white_bridge", False)),
                            "bridge_generation": int(rec.get("v242_bridge_generation", 0)),
                            "bridge_de00": float(rec.get("v242_bridge_de00", 0.0)),
                            "bridge_seed_de00": float(rec.get("v242_bridge_seed_de00", 0.0)),
                            "bridge_parent": rec.get("v242_bridge_parent", ""),
                            "bridge_seed": rec.get("v242_bridge_seed", ""),
                            "bridge_from_family": rec.get("v242_bridge_from_family", ""),
                        }
                    chroma_row_index += 1

        family_counts["Neutral Field"] = len(neutral_field)
        family_row_ranges["Neutral Field"] = (start_row, len(grid) - 1)
        add_separator()

    while grid and all(k is None for k in grid[-1]):
        grid.pop()

    flat_keys = [key for row in grid for key in row if key is not None]
    input_keys = [str(key) for key, _name, _lab in rows]
    if len(flat_keys) != len(input_keys) or len(set(flat_keys)) != len(flat_keys) or set(flat_keys) != set(input_keys):
        raise ValueError("Visual Palette Visual Palette V2.4.2 integrity check failed: input/output keys are not one-to-one")

    return {
        "grid": grid, "keys": flat_keys, "slot_info": slot_info,
        "family_counts": family_counts, "family_row_ranges": family_row_ranges,
        "neutral_count": len(neutral_field), "columns": columns,
        "lightness_span": lightness_span, "soft_boundary_deg": soft_boundary_deg,
        "near_white_bridge_count": len(near_white_promotions),
        "near_white_bridge_stage1_count": sum(1 for rec in near_white_promotions if int(rec.get("v242_bridge_generation",0)) == 1),
        "near_white_bridge_stage2_count": sum(1 for rec in near_white_promotions if int(rec.get("v242_bridge_generation",0)) == 2),
        "near_white_bridge_keys": [rec["key"] for rec in near_white_promotions],
        "version": "V2.4.2",
    }

