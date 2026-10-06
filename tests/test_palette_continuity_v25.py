from __future__ import annotations

import math

from qtx_core.models import Sample
from qtx_core.palette_continuity_v25 import appearance_continuity_layout_v25


def sm(name: str, L: float, C: float, h: float) -> Sample:
    a = C * math.cos(math.radians(h))
    b = C * math.sin(math.radians(h))
    return Sample(name, name, "STD", (0.0, 0.0, 0.0), (L, a, b), (), ())


def family(result, sample):
    return result["info"][id(sample)]["family"]


def state(result, sample):
    return result["info"][id(sample)]["neutral_state"]


def test_low_chroma_hue_instability_routes_to_one_neutral_axis():
    samples = [
        sm("grey-red-angle", 71, 3.55, 2),
        sm("grey-violet-angle", 74, 3.60, 314),
        sm("grey-blue-angle", 76, 2.70, 275),
        sm("grey-warm-angle", 79, 3.35, 314),
        sm("red", 55, 45, 25),
        sm("blue", 48, 32, 255),
    ]
    r = appearance_continuity_layout_v25(samples, 6)
    for x in samples[:4]:
        assert family(r, x) == "Neutral"
        assert state(r, x) in {"Core Neutral", "Tinted Neutral"}
    assert r["neutral_split_count"] == 0


def test_khaki_is_not_blue_and_blue_grey_is_not_neutralised():
    khaki = sm("khaki", 58, 8.0, 78)
    olive = sm("olive-khaki", 46, 10.0, 92)
    blue_grey = sm("blue-grey", 46, 6.5, 250)
    navy = sm("navy", 22, 14.0, 275)
    r = appearance_continuity_layout_v25([khaki, olive, blue_grey, navy], 6)
    assert family(r, khaki) in {"Yellow", "Green", "Orange/Brown"}
    assert family(r, khaki) != "Blue"
    assert family(r, olive) != "Blue"
    assert family(r, blue_grey) == "Blue"
    assert family(r, navy) in {"Blue", "Violet"}
    assert r["warm_cool_inversion_risk"] == 0
    assert r["cross_family_anchor_risk"] == 0


def test_pale_boundary_island_can_join_supported_adjacent_family_without_moving_deep_body():
    # Dense pale Blue support close to the Blue/Violet boundary.
    blue = [
        sm("blue-pale-1", 92.0, 10.0, 283.0),
        sm("blue-pale-2", 94.0, 10.5, 284.0),
        sm("blue-pale-3", 96.0, 9.0, 285.0),
        sm("blue-mid", 55.0, 20.0, 265.0),
        sm("blue-deep", 23.0, 16.0, 275.0),
    ]
    # A tiny near-white boundary tint island plus a separated deep Violet body.
    pale_v = [
        sm("violet-pale-1", 92.0, 10.5, 287.0),
        sm("violet-pale-2", 94.0, 10.8, 288.0),
    ]
    deep_v = [
        sm("violet-deep-1", 22.0, 17.0, 286.0),
        sm("violet-deep-2", 19.0, 15.0, 291.0),
    ]
    r = appearance_continuity_layout_v25(blue + pale_v + deep_v, 6)
    assert all(family(r, x) == "Blue" for x in pale_v)
    assert all(family(r, x) == "Violet" for x in deep_v)
    assert r["pale_boundary_island_changes"] >= 1


def test_neutral_axis_is_monotonic_in_whichever_direction_global_surface_chooses():
    samples = [
        sm("black", 15, 1.0, 250),
        sm("dark-grey", 30, 1.5, 20),
        sm("mid-grey", 50, 2.0, 170),
        sm("light-grey", 75, 3.2, 315),
        sm("red", 55, 40, 25),
        sm("yellow", 75, 60, 90),
        sm("blue", 30, 25, 270),
        sm("violet", 24, 20, 290),
    ]
    r = appearance_continuity_layout_v25(samples, 6)
    neutral_in_order = [x for x in r["order"] if family(r, x) == "Neutral"]
    Ls = [x.lab_d65_10[0] for x in neutral_in_order]
    assert Ls == sorted(Ls) or Ls == sorted(Ls, reverse=True)


def test_order_integrity_no_duplicates_no_loss():
    samples = [sm(f"x{i}", 15 + i * 6, 5 + (i % 5) * 7, (i * 37) % 360) for i in range(14)]
    r = appearance_continuity_layout_v25(samples, 6)
    assert len(r["order"]) == len(samples)
    assert len({id(x) for x in r["order"]}) == len(samples)
