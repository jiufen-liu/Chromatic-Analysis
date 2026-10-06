import math

from qtx_core.models import Sample
from qtx_core.palette_continuity_v241_science import appearance_continuity_layout_v241_science


def _sample(name: str, L: float, a: float, b: float) -> Sample:
    # Spectrum is intentionally omitted: these regression cases exercise the
    # appearance-family topology; real QTX validation additionally uses spectra.
    return Sample(
        sample_id=name,
        display_name=name,
        kind="test",
        xyz_d65_10=(0.0, 0.0, 0.0),
        lab_d65_10=(float(L), float(a), float(b)),
        reflectance=(),
    )


def _families(result):
    return {s.display_name: result["info"][id(s)]["family"] for s in result["order"]}


def test_khaki_and_blue_gray_cannot_swap_hue_sides():
    samples = [
        _sample("khaki", 55, 2.0, 7.0),          # warm yellow / earth direction
        _sample("olive khaki", 48, -1.0, 8.0),  # yellow-green earth direction
        _sample("blue gray", 55, -0.5, -7.0),   # cool blue direction
        _sample("blue anchor", 52, -2.0, -16.0),
        _sample("yellow anchor", 57, 3.0, 18.0),
    ]
    result = appearance_continuity_layout_v241_science(samples, columns=6)
    fam = _families(result)

    assert fam["khaki"] in {"Orange/Brown", "Yellow", "Green"}
    assert fam["khaki"] != "Blue"
    assert fam["olive khaki"] != "Blue"
    assert fam["blue gray"] == "Blue"
    assert result["cross_family_anchor_risk"] == 0
    assert result["warm_cool_inversion_risk"] == 0


def test_pale_blue_is_not_neutralised_and_near_white_is_neutral():
    samples = [
        _sample("pale blue", 92, -0.8, -8.0),
        _sample("pale blue anchor", 86, -2.0, -12.0),
        _sample("near white", 96, 1.0, 2.0),
        _sample("light neutral", 88, 0.7, -1.1),
    ]
    result = appearance_continuity_layout_v241_science(samples, columns=6)
    fam = _families(result)

    assert fam["pale blue"] == "Blue"
    assert fam["near white"] == "Neutral"
    assert fam["light neutral"] == "Neutral"


def test_deep_navy_is_preserved_while_near_black_is_neutral():
    samples = [
        _sample("navy", 18, -1.0, -8.0),
        _sample("deep blue anchor", 23, -2.0, -13.0),
        _sample("near black", 16, 1.0, -2.0),
    ]
    result = appearance_continuity_layout_v241_science(samples, columns=6)
    fam = _families(result)

    assert fam["navy"] == "Blue"
    assert fam["near black"] == "Neutral"


def test_integrity_every_sample_appears_once():
    samples = [
        _sample("red", 50, 22, 6),
        _sample("orange", 55, 16, 18),
        _sample("yellow", 70, 2, 25),
        _sample("green", 55, -20, 10),
        _sample("blue", 45, -8, -22),
        _sample("violet", 42, 10, -18),
        _sample("neutral", 52, 0.5, 0.5),
    ]
    result = appearance_continuity_layout_v241_science(samples, columns=3)

    assert len(result["order"]) == len(samples)
    assert {id(s) for s in result["order"]} == {id(s) for s in samples}
    assert result["cross_family_anchor_risk"] == 0
    assert result["warm_cool_inversion_risk"] == 0
