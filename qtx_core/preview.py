"""Shared display approximation for Lab swatches; never modifies measurements.

This preserves the existing application's sRGB preview convention. It does not
provide ICC display calibration or convert measured 10° coordinates to 2°.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


@dataclass(frozen=True)
class PreviewColor:
    rgb: tuple[float, float, float]
    gamut_mapped: bool
    lightness_clamped: bool
    strategy: str

    @property
    def rgb8(self) -> tuple[int, int, int]:
        return tuple(round(c * 255) for c in self.rgb)

    @property
    def hex(self) -> str:
        return '#' + ''.join(f'{v:02X}' for v in self.rgb8)


def lab_to_srgb_preview(lab: Sequence[float], *, strategy: str = 'compress') -> PreviewColor:
    values = tuple(float(v) for v in lab)
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        raise ValueError('预览 Lab 必须是三个有限数值')
    if strategy not in ('compress', 'clip'):
        raise ValueError('未知预览策略')
    measured_L, a, b = values
    L = max(0.0, min(100.0, measured_L))
    d = 6 / 29

    def linear(scale):
        fy = (L + 16) / 116
        fx, fz = fy + a * scale / 500, fy - b * scale / 200
        def inv(t): return t**3 if t > d else 3*d*d*(t - 4/29)
        x, y, z = inv(fx)*.95047, inv(fy), inv(fz)*1.08883
        return (3.2406*x - 1.5372*y - .4986*z,
                -.9689*x + 1.8758*y + .0415*z,
                .0557*x - .204*y + 1.057*z)

    rgb = linear(1.0)
    mapped = not all(0 <= c <= 1 for c in rgb)
    if mapped and strategy == 'compress':
        lo, hi = 0.0, 1.0
        for _ in range(22):
            mid = (lo + hi) / 2
            if all(0 <= c <= 1 for c in linear(mid)): lo = mid
            else: hi = mid
        rgb = linear(lo * .97)
    def gamma(c): return 12.92*c if c <= .0031308 else 1.055*max(c, 0)**(1/2.4) - .055
    display = tuple(max(0.0, min(1.0, gamma(c))) for c in rgb)
    return PreviewColor(display, mapped, L != measured_L, strategy)
