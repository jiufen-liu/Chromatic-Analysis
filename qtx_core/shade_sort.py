from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
import math

from .colorimetry import delta_h_cielab_signed


@dataclass(frozen=True)
class Shade555Result:
    code: str
    first: int | None
    second: int | None
    third: int | None
    components: tuple[float, float, float]
    in_range: bool


def _normalize_range(configured) -> tuple[float, float]:
    """Normalize a Datacolor 555 axis tolerance.

    Preferred input is a (min, max) pair, matching Datacolor CHECK II where
    the full acceptability range is divided equally into 3/5/7/9 boxes.  A
    positive scalar is accepted only for backwards compatibility and means a
    symmetric range (-value, +value).
    """
    if isinstance(configured, (tuple, list)) and len(configured) >= 2:
        lo, hi = float(configured[0]), float(configured[1])
    else:
        v = abs(float(configured))
        lo, hi = -v, v
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        raise ValueError("555 每个方向都需要有效的最小值和最大值，且最大值必须大于最小值")
    return lo, hi


def _bin_from_range(value: float, low: float, high: float, boxes: int) -> int | None:
    boxes = int(boxes)
    if boxes not in (3, 5, 7, 9):
        raise ValueError("Datacolor 555 分选箱数仅支持 3 / 5 / 7 / 9")
    value = float(value); low = float(low); high = float(high)
    if value < low or value > high:
        return None
    width = (high - low) / boxes
    if width <= 0:
        return None
    if math.isclose(value, high, rel_tol=0.0, abs_tol=1e-12):
        return boxes
    pos = (value - low) / width
    # Datacolor's published examples use decimal boundaries such as +0.20
    # as the first value of the next box.  Floating-point arithmetic can turn
    # an exact boundary into 3.9999999999999996, so snap values that are
    # numerically on an internal boundary before taking floor().
    nearest = round(pos)
    if 0 < nearest < boxes and math.isclose(pos, nearest, rel_tol=0.0, abs_tol=1e-10):
        # CHECK II's published 9-box table assigns exact decimal boundaries
        # away from the standard: -0.20 belongs to box 4 while +0.20
        # belongs to box 6 (for L ±1.80).  Mirror that convention.
        if value < 0:
            return max(1, min(boxes, int(nearest)))
        return max(1, min(boxes, int(nearest) + 1))
    return max(1, min(boxes, int(math.floor(pos)) + 1))


def shade_555(
    standard_lab: Sequence[float],
    sample_lab: Sequence[float],
    ranges: Mapping[str, tuple[float, float] | float],
    mode: str = "LAB",
    blocks: int = 9,
) -> Shade555Result:
    """Calculate a Datacolor-style 555 shade-sort code.

    The code is a *sorting* result, not a pass/fail result.  Callers should
    decide first whether a batch has passed its acceptability tolerance, then
    run this function for the three configured colorimetric dimensions.

    ``ranges`` contains the low/high tolerance relative to the standard for
    each axis.  For LAB use L/a/b; for LCH use L/C/H.  The complete axis range
    is divided equally into 3, 5, 7 or 9 boxes.  With symmetric low/high limits
    and 9 boxes, the standard (zero delta on all axes) naturally falls in 555.
    """
    L1, a1, b1 = (float(x) for x in standard_lab)
    L2, a2, b2 = (float(x) for x in sample_lab)
    dL = L2 - L1; da = a2 - a1; db = b2 - b1
    C1 = math.hypot(a1, b1); C2 = math.hypot(a2, b2); dC = C2 - C1
    dH = delta_h_cielab_signed((L1, a1, b1), (L2, a2, b2))

    mode = str(mode or "LAB").upper()
    if mode in {"LAB", "L*A*B*"}:
        keys = ("L", "a", "b"); values = (dL, da, db)
    else:
        keys = ("L", "C", "H"); values = (dL, dC, dH)

    bins: list[int | None] = []
    for key, value in zip(keys, values):
        if key not in ranges:
            return Shade555Result("—", None, None, None, values, False)
        low, high = _normalize_range(ranges[key])
        bins.append(_bin_from_range(value, low, high, blocks))

    ok = all(x is not None for x in bins)
    if ok:
        code = "".join(str(x) for x in bins) if blocks <= 9 else "/".join(str(x) for x in bins)
    else:
        code = "超范围"
    return Shade555Result(code, bins[0], bins[1], bins[2], values, ok)


def symmetric_ranges(half_ranges: Mapping[str, float], mode: str = "LAB") -> dict[str, tuple[float, float]]:
    keys = ("L", "a", "b") if str(mode).upper() == "LAB" else ("L", "C", "H")
    out = {}
    for key in keys:
        v = abs(float(half_ranges[key]))
        if v <= 0:
            raise ValueError("555 对称容差必须大于 0")
        out[key] = (-v, v)
    return out
