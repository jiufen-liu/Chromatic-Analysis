from __future__ import annotations

import math

from qtx_core.analysis import spectral_order, spectral_rms_distance
from qtx_core.colorimetry import delta_e
from qtx_core.models import Sample


def _sample(i: int) -> Sample:
    # Deterministic same-grid spectra with enough variation to exercise the
    # nearest-neighbour path without relying on project QTX fixtures.
    waves=tuple(range(360,701,10))
    refl=tuple(8.0 + ((i*13 + j*7) % 71) * 0.83 + math.sin((i+1)*(j+2))*0.15 for j,_ in enumerate(waves))
    L=18.0 + (i*17 % 73)
    a=-42.0 + (i*19 % 84)
    b=-38.0 + (i*23 % 76)
    return Sample(
        sample_id=f'S{i:03d}', display_name=f'Sample {i:03d}', kind='BAT',
        xyz_d65_10=(10.0+i,20.0+i,30.0+i), lab_d65_10=(L,a,b),
        reflectance=refl, wavelengths=waves, source_file='same_grid.qtx'
    )


def _legacy(samples, hybrid=False):
    items=list(samples)
    spectral=[s for s in items if s.has_spectrum()]
    missing=[s for s in items if not s.has_spectrum()]
    def hue_key(s):
        L,a,b=s.lab_d65_10; c=(a*a+b*b)**0.5; h=(math.degrees(math.atan2(b,a))%360.0)
        return (h,L,c,s.display_name.casefold())
    remaining=sorted(spectral,key=hue_key)
    ordered=[remaining.pop(0)]
    while remaining:
        prev=ordered[-1]
        def score(s):
            spec=spectral_rms_distance(prev,s)
            if not hybrid:return (spec,hue_key(s))
            de=delta_e(prev.lab_d65_10,s.lab_d65_10,'CIE 2000')
            return (spec+0.18*de,spec,hue_key(s))
        nxt=min(remaining,key=score); remaining.remove(nxt); ordered.append(nxt)
    return ordered+missing


def test_hf122_same_grid_spectral_order_matches_legacy():
    samples=[_sample(i) for i in range(48)]
    expected=[s.sample_id for s in _legacy(samples,False)]
    actual=[s.sample_id for s in spectral_order(samples,False)]
    assert actual==expected


def test_hf122_same_grid_hybrid_order_matches_legacy():
    samples=[_sample(i) for i in range(36)]
    expected=[s.sample_id for s in _legacy(samples,True)]
    actual=[s.sample_id for s in spectral_order(samples,True)]
    assert actual==expected
