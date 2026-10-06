from __future__ import annotations

"""Simple, deterministic visual colour-family sorting.

The goal is intentionally practical rather than mathematically global:
1) put visually similar broad colour families together;
2) keep black/grey/white and navy/deep-blue distinct;
3) sort each family mainly dark -> light, with chroma/hue only as local ties.

The classifier uses only displayed CIELAB L*, a*, b* values.  It does not use
customer names, brand-specific rules, spectra, clustering, graph optimisation,
or a global Delta-E path.
"""

from dataclasses import dataclass
import math
from typing import Iterable, Sequence, Any


FAMILY_ORDER = (
    "Black",
    "Grey",
    "White / Light Grey",
    "Navy / Deep Blue",
    "Blue",
    "Purple",
    "Green",
    "Olive / Yellow-Green",
    "Yellow / Khaki / Beige",
    "Orange / Brown",
    "Red",
    "Pink",
)

FAMILY_LABELS_ZH = {
    "Black": "黑色",
    "Grey": "灰色",
    "White / Light Grey": "白色 / 浅灰",
    "Navy / Deep Blue": "深蓝 / 藏青",
    "Blue": "蓝色",
    "Purple": "紫色",
    "Green": "绿色",
    "Olive / Yellow-Green": "橄榄 / 黄绿",
    "Yellow / Khaki / Beige": "黄色 / 卡其 / 米色",
    "Orange / Brown": "橙色 / 棕色",
    "Red": "红色",
    "Pink": "粉色",
}

_ORDER_INDEX = {name: i for i, name in enumerate(FAMILY_ORDER)}


@dataclass(frozen=True)
class FamilySortRecord:
    key: str
    name: str
    L: float
    a: float
    b: float
    C: float
    h: float
    family: str
    family_index: int
    source_index: int


def _lch(L: float, a: float, b: float) -> tuple[float, float]:
    C = math.hypot(float(a), float(b))
    h = math.degrees(math.atan2(float(b), float(a))) % 360.0
    return C, h


def classify_color_family(L: float, a: float, b: float) -> str:
    """Return one broad visual family.

    Thresholds deliberately remain few and interpretable.  Low chroma is handled
    before hue because h* is unstable close to the neutral axis, while a special
    dark-blue guard preserves navy identity before general grey handling.
    """
    L = float(L); a = float(a); b = float(b)
    C, h = _lch(L, a, b)

    # Achromatic axis.  Black must stay separate from navy/deep-blue.
    if L <= 23.0 and C <= 4.8:
        return "Black"

    # Dark blue keeps its visual identity even when chroma is modest.
    if L <= 40.0 and C > 4.8 and 220.0 <= h < 285.0:
        return "Navy / Deep Blue"

    # Very light near-neutrals are grouped as white/light-grey.
    if L >= 88.0 and C <= 8.0:
        return "White / Light Grey"

    # Near-neutral greys.  The second clause catches tinted greys without letting
    # clearly chromatic colours disappear into Grey.
    if C <= 5.6 or (L >= 35.0 and C <= 7.0):
        return "Grey"

    # Chromatic families on the CIELAB hue circle:
    # red~0, yellow~90, green~180, blue~270, magenta/purple~315.
    if h >= 325.0 or h < 25.0:
        if L >= 60.0:
            return "Pink"
        return "Red"

    if 25.0 <= h < 70.0:
        # Dark/moderate warm colours read more like brown; bright high-L colours
        # are still kept in the same simple warm family rather than fragmented.
        return "Orange / Brown"

    if 70.0 <= h < 115.0:
        return "Yellow / Khaki / Beige"

    if 115.0 <= h < 145.0:
        return "Olive / Yellow-Green"

    if 145.0 <= h < 220.0:
        return "Green"

    if 220.0 <= h < 285.0:
        return "Blue"

    if 285.0 <= h < 325.0:
        return "Purple"

    # Circular tail 325+ was handled above.  This is a deterministic fallback.
    return "Red"


def _record_sort_key(rec: FamilySortRecord) -> tuple:
    # Broad family first.  Inside each family the primary visual reading is
    # dark -> light.  2-L* bands prevent tiny numerical L* noise from breaking
    # visually close neighbours; within a band muted -> vivid is the tie rule.
    lightness_band = round(rec.L / 2.0)
    return (
        rec.family_index,
        lightness_band,
        rec.L,
        rec.C,
        rec.h,
        rec.name.casefold(),
        rec.source_index,
        rec.key,
    )


def sort_lab_rows(rows: Iterable[Sequence[Any]]) -> list[FamilySortRecord]:
    """Sort ``(key, name, (L,a,b), ...)`` rows used by the UI worker."""
    records: list[FamilySortRecord] = []
    for i, row in enumerate(rows):
        key = str(row[0]); name = str(row[1]); lab = row[2]
        L, a, b = (float(lab[0]), float(lab[1]), float(lab[2]))
        C, h = _lch(L, a, b)
        family = classify_color_family(L, a, b)
        records.append(FamilySortRecord(
            key=key, name=name, L=L, a=a, b=b, C=C, h=h,
            family=family, family_index=_ORDER_INDEX[family], source_index=i,
        ))
    records.sort(key=_record_sort_key)
    return records


def family_counts(records: Iterable[FamilySortRecord]) -> dict[str, int]:
    out = {name: 0 for name in FAMILY_ORDER}
    for rec in records:
        out[rec.family] = out.get(rec.family, 0) + 1
    return {k: v for k, v in out.items() if v}
