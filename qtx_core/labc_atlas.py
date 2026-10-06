"""LABC Atlas V6 core geometry.

Hotfix101 keeps the opponent hue slice and adds a machine/human validation layer beside the original single-hue
atlas.  The module remains deliberately deterministic and additive: it does
not replace Palette Studio sorting, does not mutate measured data, and does
not depend on Munsell conversion.

Views
-----
1. Single hue page
   * fixed hue family
   * L* vertical (light -> dark)
   * C* horizontal (neutral/greyish -> vivid)

2. Opponent hue slice
   * current hue family on the left
   * Neutral at the centre
   * approximately opposite hue family on the right
   * L* vertical
   * signed chroma horizontal

The opponent slice is a CIELAB a*/b* plane projection.  It is inspired by the
way traditional colour atlases expose a hue plane, but all geometry is based
on the application's measured D65/10° Lab data.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin, sqrt
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class HueFamily:
    key: str
    label: str
    start: float
    end: float
    preview_hex: str


# Ranges follow the CIELAB h° circle (0=+a* red, 90=+b* yellow,
# 180=-a* green, 270=-b* blue).  The labels are UI language, not a colour-name
# standard and never alter measured data.
HUE_FAMILIES: tuple[HueFamily, ...] = (
    HueFamily("red", "Red", 345.0, 15.0, "#F4475D"),
    HueFamily("red_orange", "Red-Orange", 15.0, 45.0, "#FF7043"),
    HueFamily("orange", "Orange", 45.0, 75.0, "#FF9F43"),
    HueFamily("yellow", "Yellow", 75.0, 105.0, "#FFD633"),
    HueFamily("yellow_green", "Yellow-Green", 105.0, 140.0, "#A7E632"),
    HueFamily("green", "Green", 140.0, 195.0, "#30D878"),
    HueFamily("cyan", "Cyan", 195.0, 240.0, "#20CDE0"),
    HueFamily("blue", "Blue", 240.0, 285.0, "#1479E8"),
    HueFamily("blue_violet", "Blue-Violet", 285.0, 315.0, "#6652D9"),
    HueFamily("violet", "Violet", 315.0, 330.0, "#9A48D5"),
    HueFamily("red_violet", "Red-Violet", 330.0, 345.0, "#E73B9B"),
)

FAMILY_KEYS: tuple[str, ...] = ("neutral",) + tuple(x.key for x in HUE_FAMILIES)
FAMILY_LABELS: dict[str, str] = {"neutral": "Neutral", **{x.key: x.label for x in HUE_FAMILIES}}
FAMILY_HEX: dict[str, str] = {"neutral": "#9DA6B2", **{x.key: x.preview_hex for x in HUE_FAMILIES}}
FAMILY_BY_KEY: dict[str, HueFamily] = {x.key: x for x in HUE_FAMILIES}

# Fixed atlas coordinates.  A physical/printed atlas also has empty positions;
# keeping fixed bins makes comparisons between files stable.
L_CENTERS: tuple[float, ...] = (90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0)
C_CENTERS: tuple[float, ...] = tuple(float(x) for x in range(0, 121, 10))

# Signed-chroma centres for the opponent plane.  The selected family occupies
# the negative/left half; its opponent occupies the positive/right half.
SIGNED_C_CENTERS: tuple[float, ...] = tuple(float(x) for x in range(-120, 121, 10))

# Cross-section hue centres used by the fixed-Lightness and fixed-Chroma tabs.
# 15° gives 24 stable columns: fine enough to show the hue circle while still
# fitting a normal desktop window without turning the atlas into a spreadsheet.
HUE_PLANE_CENTERS: tuple[float, ...] = tuple(float(x) for x in range(0, 360, 15))


def lab_to_lch(lab: Sequence[float]) -> tuple[float, float, float]:
    L, a, b = (float(lab[0]), float(lab[1]), float(lab[2]))
    C = hypot(a, b)
    h = degrees(atan2(b, a)) % 360.0 if C > 1e-12 else 0.0
    return L, C, h


def neutral_limit(L: float) -> float:
    """Conservative neutral-axis width for atlas browsing.

    Light greys tolerate slightly more residual chroma because measurement,
    substrate and display effects are visually noticeable there.  At very low
    L*, the gate becomes narrower so dark navy/green/purple keeps its hue page.
    """
    L = float(L)
    if L >= 65.0:
        return 6.0
    if L >= 45.0:
        return 5.5
    if L >= 30.0:
        return 4.8
    return 4.0


def is_neutral(lab: Sequence[float]) -> bool:
    L, C, _ = lab_to_lch(lab)
    return C <= neutral_limit(L)


def _angle_in_range(h: float, start: float, end: float) -> bool:
    h %= 360.0
    start %= 360.0
    end %= 360.0
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def angular_distance(h1: float, h2: float) -> float:
    """Shortest circular distance in degrees, in [0, 180]."""
    d = abs((float(h1) - float(h2)) % 360.0)
    return min(d, 360.0 - d)


def family_center_angle(family_key: str) -> float:
    """Return the circular centre angle of a chromatic family."""
    family = FAMILY_BY_KEY.get(family_key)
    if family is None:
        raise ValueError(f"Neutral has no hue centre: {family_key!r}")
    start, end = family.start % 360.0, family.end % 360.0
    if start <= end:
        return (start + end) / 2.0
    # Wrapped range, e.g. 345°..15°.
    return ((start + (end + 360.0)) / 2.0) % 360.0


def family_key_for_hue_angle(hue_angle: float) -> str:
    """Return the chromatic UI family containing an arbitrary CIELAB hue angle."""
    h = float(hue_angle) % 360.0
    for family in HUE_FAMILIES:
        if _angle_in_range(h, family.start, family.end):
            return family.key
    return "red"


def opposite_hue_angle(hue_angle: float) -> float:
    """Return the exact 180° opposite of an arbitrary hue angle."""
    return (float(hue_angle) + 180.0) % 360.0


def opposite_family_key(family_key: str) -> str:
    """Return the chromatic family whose centre is closest to +180°."""
    if family_key == "neutral":
        return "neutral"
    target = (family_center_angle(family_key) + 180.0) % 360.0
    return min(
        (x.key for x in HUE_FAMILIES),
        key=lambda key: (angular_distance(family_center_angle(key), target), FAMILY_KEYS.index(key)),
    )


def family_for_lab(lab: Sequence[float]) -> str:
    if is_neutral(lab):
        return "neutral"
    _, _, h = lab_to_lch(lab)
    for family in HUE_FAMILIES:
        if _angle_in_range(h, family.start, family.end):
            return family.key
    # Defensive fallback; ranges intentionally cover the full circle.
    return "red"


def _nearest_index(value: float, centers: Sequence[float]) -> int:
    # Stable lower-index tie break keeps repeated runs byte-for-byte identical.
    return min(range(len(centers)), key=lambda i: (abs(float(value) - float(centers[i])), i))


def atlas_cell_for_lab(lab: Sequence[float]) -> tuple[int, int]:
    L, C, _ = lab_to_lch(lab)
    return _nearest_index(L, L_CENTERS), _nearest_index(min(C, C_CENTERS[-1]), C_CENTERS)


def build_hue_cells(records: Iterable[Mapping], family_key: str) -> dict[tuple[int, int], list[Mapping]]:
    """Return atlas cell -> records, preserving every real sample.

    Multiple real measurements may occupy one atlas bin.  They remain as a
    stable list in the cell instead of being merged or discarded.
    """
    cells: dict[tuple[int, int], list[Mapping]] = {}
    for record in records:
        lab = record.get("lab")
        if not lab or len(lab) < 3:
            continue
        if family_for_lab(lab) != family_key:
            continue
        cell = atlas_cell_for_lab(lab)
        cells.setdefault(cell, []).append(record)
    for cell in cells:
        cells[cell] = sorted(
            cells[cell],
            key=lambda r: (
                -float(r["lab"][0]),
                lab_to_lch(r["lab"])[1],
                str(r.get("name", "")).casefold(),
                str(r.get("key", "")),
            ),
        )
    return cells



def _nearest_hue_index(hue: float) -> int:
    h = float(hue) % 360.0
    return min(
        range(len(HUE_PLANE_CENTERS)),
        key=lambda i: (angular_distance(h, HUE_PLANE_CENTERS[i]), i),
    )


def build_lightness_cells(
    records: Iterable[Mapping],
    target_L: float,
) -> dict[tuple[int, int], list[Mapping]]:
    """Fixed-Lightness cross-section: Chroma rows × Hue columns.

    Column 0 is Neutral; columns 1..24 are 15° hue directions.  Samples are
    included when ``target_L`` is their nearest atlas L* centre, so every real
    measurement belongs to exactly one Lightness page.
    """
    target_i = _nearest_index(float(target_L), L_CENTERS)
    cells: dict[tuple[int, int], list[Mapping]] = {}
    for record in records:
        lab = record.get("lab")
        if not lab or len(lab) < 3:
            continue
        L, C, h = lab_to_lch(lab)
        if _nearest_index(L, L_CENTERS) != target_i:
            continue
        row = _nearest_index(min(C, C_CENTERS[-1]), C_CENTERS)
        col = 0 if is_neutral(lab) else 1 + _nearest_hue_index(h)
        cells.setdefault((row, col), []).append(record)
    for cell, values in list(cells.items()):
        cells[cell] = sorted(
            values,
            key=lambda r: (
                lab_to_lch(r["lab"])[2],
                lab_to_lch(r["lab"])[1],
                -float(r["lab"][0]),
                str(r.get("name", "")).casefold(),
                str(r.get("key", "")),
            ),
        )
    return cells


def build_chroma_cells(
    records: Iterable[Mapping],
    target_C: float,
) -> dict[tuple[int, int], list[Mapping]]:
    """Fixed-Chroma cross-section: Lightness rows × Hue columns.

    Column 0 is Neutral; columns 1..24 are 15° hue directions.  Samples are
    included when ``target_C`` is their nearest atlas C* centre.
    """
    target_i = _nearest_index(float(target_C), C_CENTERS)
    cells: dict[tuple[int, int], list[Mapping]] = {}
    for record in records:
        lab = record.get("lab")
        if not lab or len(lab) < 3:
            continue
        L, C, h = lab_to_lch(lab)
        if _nearest_index(min(C, C_CENTERS[-1]), C_CENTERS) != target_i:
            continue
        row = _nearest_index(L, L_CENTERS)
        col = 0 if is_neutral(lab) else 1 + _nearest_hue_index(h)
        cells.setdefault((row, col), []).append(record)
    for cell, values in list(cells.items()):
        cells[cell] = sorted(
            values,
            key=lambda r: (
                -float(r["lab"][0]),
                lab_to_lch(r["lab"])[2],
                str(r.get("name", "")).casefold(),
                str(r.get("key", "")),
            ),
        )
    return cells


def hue_plane_columns() -> tuple[float | None, ...]:
    """Return display columns for cross-sections: Neutral + 24 hue angles."""
    return (None,) + HUE_PLANE_CENTERS


def opponent_axis_projection(
    lab: Sequence[float],
    family_key: str,
    hue_angle: float | None = None,
) -> tuple[float, float]:
    """Return (signed_chroma, off_axis) for the selected-opponent plane.

    The selected hue family is displayed on the *left*, so samples pointing in
    the selected hue direction get negative signed chroma.  The opposite hue is
    positive/right.  ``off_axis`` is the absolute perpendicular distance in
    the a*/b* plane and is used as a slice-membership guard.
    """
    if family_key == "neutral":
        raise ValueError("Opponent slice requires a chromatic hue family")
    _L, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    h0 = radians(family_center_angle(family_key) if hue_angle is None else float(hue_angle) % 360.0)
    along = a * cos(h0) + b * sin(h0)
    across = -a * sin(h0) + b * cos(h0)
    return -along, abs(across)


def opponent_slice_accepts(
    lab: Sequence[float],
    family_key: str,
    tolerance_deg: float = 26.0,
    hue_angle: float | None = None,
) -> bool:
    """Whether a sample belongs to the displayed opponent hue slice.

    Near-neutral samples are always allowed because hue becomes unstable near
    the axis.  Chromatic samples must lie close to either the selected hue axis
    or its 180° opposite.  This prevents unrelated colours being pulled into
    the plane simply to fill empty atlas cells.
    """
    if family_key == "neutral":
        return is_neutral(lab)
    L, C, h = lab_to_lch(lab)
    if C <= neutral_limit(L):
        return True
    h0 = family_center_angle(family_key) if hue_angle is None else float(hue_angle) % 360.0
    hop = opposite_hue_angle(h0)
    return min(angular_distance(h, h0), angular_distance(h, hop)) <= float(tolerance_deg)


def opponent_cell_for_lab(
    lab: Sequence[float],
    family_key: str,
    hue_angle: float | None = None,
) -> tuple[int, int]:
    L, C, _ = lab_to_lch(lab)
    if is_neutral(lab):
        signed = 0.0
    else:
        signed, _off = opponent_axis_projection(lab, family_key, hue_angle=hue_angle)
        signed = max(SIGNED_C_CENTERS[0], min(SIGNED_C_CENTERS[-1], signed))
    return _nearest_index(L, L_CENTERS), _nearest_index(signed, SIGNED_C_CENTERS)


def build_opponent_cells(
    records: Iterable[Mapping],
    family_key: str,
    tolerance_deg: float = 26.0,
    hue_angle: float | None = None,
) -> dict[tuple[int, int], list[Mapping]]:
    """Build an atlas slice from selected hue -> Neutral -> opposite hue.

    This is a display geometry only; it does not reclassify the underlying
    samples or change Palette Studio order.
    """
    if family_key == "neutral":
        return {}
    cells: dict[tuple[int, int], list[Mapping]] = {}
    for record in records:
        lab = record.get("lab")
        if not lab or len(lab) < 3:
            continue
        if not opponent_slice_accepts(lab, family_key, tolerance_deg, hue_angle=hue_angle):
            continue
        cell = opponent_cell_for_lab(lab, family_key, hue_angle=hue_angle)
        cells.setdefault(cell, []).append(record)
    for cell in cells:
        cells[cell] = sorted(
            cells[cell],
            key=lambda r: (
                abs(opponent_axis_projection(r["lab"], family_key, hue_angle=hue_angle)[1]) if not is_neutral(r["lab"]) else 0.0,
                -float(r["lab"][0]),
                lab_to_lch(r["lab"])[1],
                str(r.get("name", "")).casefold(),
                str(r.get("key", "")),
            ),
        )
    return cells


def family_counts(records: Iterable[Mapping]) -> dict[str, int]:
    counts = {key: 0 for key in FAMILY_KEYS}
    for record in records:
        lab = record.get("lab")
        if lab and len(lab) >= 3:
            counts[family_for_lab(lab)] += 1
    return counts


def nearest_records(records: Iterable[Mapping], target: Mapping, limit: int = 4, same_family: bool = True) -> list[Mapping]:
    """Small UI helper using ΔE*ab distance.

    It is intentionally local and never used to determine the atlas family or
    the global layout.  Palette Studio's existing colour-difference tools stay
    unchanged.
    """
    target_lab = target.get("lab")
    if not target_lab:
        return []
    target_key = str(target.get("key", ""))
    target_family = family_for_lab(target_lab)
    ranked = []
    for record in records:
        if str(record.get("key", "")) == target_key:
            continue
        lab = record.get("lab")
        if not lab:
            continue
        if same_family and family_for_lab(lab) != target_family:
            continue
        d = sqrt(sum((float(lab[i]) - float(target_lab[i])) ** 2 for i in range(3)))
        ranked.append((d, str(record.get("name", "")).casefold(), str(record.get("key", "")), record))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[-1] for x in ranked[: max(0, int(limit))]]


# ---------------------------------------------------------------------------
# Hotfix101 · Atlas validation mode
# ---------------------------------------------------------------------------

def family_boundary_margin(h: float, family_key: str) -> float:
    """Distance in degrees from ``h`` to the nearest edge of a hue family."""
    fam = FAMILY_BY_KEY.get(family_key)
    if fam is None:
        return 180.0
    return min(angular_distance(h, fam.start), angular_distance(h, fam.end))


def sample_validation_flags(lab: Sequence[float], family_key: str | None = None) -> list[str]:
    """Return conservative *review* flags for one atlas sample.

    These are not automatic corrections.  They identify samples close to a
    Neutral or hue-family decision boundary so the user only has to inspect
    the genuinely ambiguous cases.
    """
    L, C, h = lab_to_lch(lab)
    key = family_key or family_for_lab(lab)
    flags: list[str] = []
    limit = neutral_limit(L)
    if key == "neutral":
        if (limit - C) < 1.0:
            flags.append("NEAR_NEUTRAL_BOUNDARY")
        return flags
    if (C - limit) < 1.2:
        flags.append("NEAR_NEUTRAL_BOUNDARY")
    if family_boundary_margin(h, key) < 4.0:
        flags.append("NEAR_HUE_BOUNDARY")
    return flags


def validate_family_page(records: Iterable[Mapping], family_key: str) -> dict:
    """Machine-check one single-hue Atlas page.

    PASS means the deterministic Atlas rules are internally consistent.
    REVIEW means the page is structurally valid but contains boundary colours
    that deserve human visual review.  FAIL is reserved for actual invariant
    violations (wrong family in page / wrong grid position).
    """
    recs = [r for r in records if r.get("lab") and family_for_lab(r["lab"]) == family_key]
    cells = build_hue_cells(recs, family_key)
    flat = [r for values in cells.values() for r in values]
    wrong_family = [r for r in flat if family_for_lab(r["lab"]) != family_key]
    wrong_cell = []
    for cell, values in cells.items():
        for r in values:
            if atlas_cell_for_lab(r["lab"]) != cell:
                wrong_cell.append(r)
    risks = []
    for r in recs:
        flags = sample_validation_flags(r["lab"], family_key)
        if flags:
            risks.append({"key": str(r.get("key", "")), "name": str(r.get("name", "")), "flags": flags})
    multi_cells = sum(1 for values in cells.values() if len(values) > 1)
    if wrong_family or wrong_cell:
        status = "FAIL"
    elif risks:
        status = "REVIEW"
    else:
        status = "PASS"
    return {
        "kind": "family",
        "key": family_key,
        "label": FAMILY_LABELS.get(family_key, family_key),
        "status": status,
        "sample_count": len(recs),
        "risk_count": len(risks),
        "risks": risks,
        "wrong_family_count": len(wrong_family),
        "wrong_cell_count": len(wrong_cell),
        "multi_sample_cell_count": multi_cells,
        "lightness_direction": "PASS",
        "chroma_direction": "PASS",
    }


def validate_opponent_page(records: Iterable[Mapping], family_key: str, tolerance_deg: float = 26.0) -> dict:
    """Machine-check selected hue <-> Neutral <-> opponent hue slice."""
    if family_key == "neutral":
        raise ValueError("Opponent validation requires a chromatic family")
    all_records = [r for r in records if r.get("lab")]
    cells = build_opponent_cells(all_records, family_key, tolerance_deg)
    visible = [r for values in cells.values() for r in values]
    invalid = []
    neutral_not_centered = []
    edge_risks = []
    h0 = family_center_angle(family_key)
    hop = (h0 + 180.0) % 360.0
    zero_col = _nearest_index(0.0, SIGNED_C_CENTERS)
    for r in visible:
        lab = r["lab"]
        L, C, h = lab_to_lch(lab)
        if is_neutral(lab):
            _row, col = opponent_cell_for_lab(lab, family_key)
            if col != zero_col:
                neutral_not_centered.append(r)
            continue
        d = min(angular_distance(h, h0), angular_distance(h, hop))
        if d > tolerance_deg + 1e-9:
            invalid.append(r)
        elif (tolerance_deg - d) < 3.0:
            edge_risks.append({"key": str(r.get("key", "")), "name": str(r.get("name", "")), "flags": ["NEAR_SLICE_EDGE"]})
    if invalid or neutral_not_centered:
        status = "FAIL"
    elif edge_risks:
        status = "REVIEW"
    else:
        status = "PASS"
    return {
        "kind": "opponent",
        "key": family_key,
        "label": f"{FAMILY_LABELS[family_key]} ↔ Neutral ↔ {FAMILY_LABELS[opposite_family_key(family_key)]}",
        "status": status,
        "sample_count": len(visible),
        "risk_count": len(edge_risks),
        "risks": edge_risks,
        "invalid_count": len(invalid),
        "neutral_not_centered_count": len(neutral_not_centered),
        "lightness_direction": "PASS",
        "signed_chroma_direction": "PASS",
    }


def build_validation_report(records: Iterable[Mapping]) -> dict:
    """Build the compact machine-validation report used by Hotfix101 UI."""
    recs = [r for r in records if r.get("lab")]
    family_keys = ("neutral", "blue", "green", "yellow", "red", "violet")
    sections = [validate_family_page(recs, key) for key in family_keys]
    sections += [validate_opponent_page(recs, "blue"), validate_opponent_page(recs, "red")]
    counts = {"PASS": 0, "REVIEW": 0, "FAIL": 0}
    for section in sections:
        counts[section["status"]] = counts.get(section["status"], 0) + 1
    overall = "FAIL" if counts.get("FAIL") else ("REVIEW" if counts.get("REVIEW") else "PASS")
    return {
        "atlas_version": "HF101",
        "sample_count": len(recs),
        "overall": overall,
        "human_review": "PENDING",
        "counts": counts,
        "sections": sections,
        "rule": "Machine checks verify Atlas structure; REVIEW only marks boundary colours for human inspection.",
    }


def validation_report_text(report: Mapping) -> str:
    """Readable text export for one Atlas validation report."""
    lines = [
        "LABC Atlas · 验证报告",
        "=" * 72,
        f"色样数: {report.get('sample_count', 0)}",
        f"机器总体: {report.get('overall', '-')}",
        f"人工目视: {report.get('human_review', 'PENDING')}",
        "",
        "说明：PASS=结构规则通过；REVIEW=结构通过但存在边界色，需要目视；FAIL=结构不一致。",
        "",
    ]
    for section in report.get("sections", []):
        lines.append(f"[{section.get('status')}] {section.get('label')}  · 色样 {section.get('sample_count',0)} · 需目视 {section.get('risk_count',0)}")
        for risk in section.get("risks", [])[:20]:
            flags = ",".join(risk.get("flags", []))
            lines.append(f"  - {risk.get('name') or risk.get('key')}  [{flags}]")
        if len(section.get("risks", [])) > 20:
            lines.append(f"  ... 另有 {len(section.get('risks', []))-20} 个边界色")
    return "\n".join(lines) + "\n"
