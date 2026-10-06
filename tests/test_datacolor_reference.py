from pathlib import Path

import pytest

from qtx_core import (
    analyse_pair,
    cie_tint_d65_10,
    cie_whiteness_d65_10,
    parse_qtx_file,
    reflectance_to_xyz_lab,
)


FIXTURE = Path(__file__).parent / "data" / "Col.9 FH0092.qtx"


@pytest.fixture(scope="module")
def samples():
    parsed = parse_qtx_file(FIXTURE)
    return {sample.display_name: sample for sample in parsed}


def test_parser_reads_one_standard_and_two_batches(samples):
    assert set(samples) == {"Col.9 FH0092", "Col.9 FH0051", "Col.9 ZFH0144"}
    assert samples["Col.9 FH0092"].kind == "STD"
    assert all(len(sample.reflectance) == 35 for sample in samples.values())


def test_standard_xyz_to_lab_matches_datacolor(samples):
    lab = samples["Col.9 FH0092"].lab_d65_10
    assert lab == pytest.approx((22.77, -1.53, -1.07), abs=0.01)


@pytest.mark.parametrize(
    "name, expected_wi, expected_tint",
    [
        ("Col.9 FH0092", 17.64, 8.21),
        ("Col.9 FH0051", 10.05, -1.60),
        ("Col.9 ZFH0144", 14.11, 0.79),
    ],
)
def test_whiteness_and_tint_match_datacolor(samples, name, expected_wi, expected_tint):
    xyz = samples[name].xyz_d65_10
    assert cie_whiteness_d65_10(xyz) == pytest.approx(expected_wi, abs=0.011)
    assert cie_tint_d65_10(xyz) == pytest.approx(expected_tint, abs=0.011)


@pytest.mark.parametrize(
    "name, illuminant, expected_xyz, expected_lab",
    [
        ("Col.9 FH0092", "A", (3.93, 3.66, 1.38), (22.51, -1.94, -1.52)),
        ("Col.9 FH0092", "F02", (3.75, 3.70, 2.70), (22.67, -1.08, -1.24)),
        ("Col.9 FH0051", "A", (3.10, 2.76, 0.99), (19.07, 0.43, -0.44)),
        ("Col.9 FH0051", "F02", (2.86, 2.75, 1.96), (19.01, 0.29, -0.64)),
        ("Col.9 ZFH0144", "A", (3.44, 3.11, 1.14), (20.49, -0.23, -0.82)),
        ("Col.9 ZFH0144", "F02", (3.22, 3.12, 2.25), (20.51, 0.01, -0.92)),
    ],
)
def test_multilight_xyz_lab_matches_datacolor(samples, name, illuminant, expected_xyz, expected_lab):
    sample = samples[name]
    xyz, lab = reflectance_to_xyz_lab(sample.reflectance, illuminant, sample.wavelengths)
    assert xyz == pytest.approx(expected_xyz, abs=0.011)
    assert lab == pytest.approx(expected_lab, abs=0.011)


@pytest.mark.parametrize(
    "batch_name, illuminant, expected_mi",
    [
        ("Col.9 FH0051", "A", 0.72),
        ("Col.9 FH0051", "F02", 0.60),
        ("Col.9 ZFH0144", "A", 0.50),
        ("Col.9 ZFH0144", "F02", 0.47),
    ],
)
def test_multiplicative_metamerism_matches_datacolor(samples, batch_name, illuminant, expected_mi):
    result = analyse_pair(samples["Col.9 FH0092"], samples[batch_name], illuminant)
    assert result.metamerism_index == pytest.approx(expected_mi, abs=0.011)


def test_reference_illuminant_has_no_metamerism_value(samples):
    result = analyse_pair(samples["Col.9 FH0092"], samples["Col.9 FH0051"], "D65")
    assert result.metamerism_index is None


@pytest.mark.parametrize(
    "batch_name, illuminant, expected_de00, expected_cmc21, expected_de94",
    [
        ("Col.9 FH0051", "D65", 3.88, 3.91, 2.69),
        ("Col.9 FH0051", "A", 4.26, 4.25, 2.98),
        ("Col.9 FH0051", "F02", 3.29, 3.43, 2.33),
        ("Col.9 ZFH0144", "D65", 2.71, 2.70, 1.87),
        ("Col.9 ZFH0144", "A", 2.84, 2.82, 1.97),
        ("Col.9 ZFH0144", "F02", 2.20, 2.24, 1.53),
    ],
)
def test_pair_colour_differences_match_datacolor(
    samples, batch_name, illuminant, expected_de00, expected_cmc21, expected_de94
):
    result = analyse_pair(samples["Col.9 FH0092"], samples[batch_name], illuminant)
    assert result.delta_e00 == pytest.approx(expected_de00, abs=0.015)
    assert result.cmc21 == pytest.approx(expected_cmc21, abs=0.015)
    assert result.delta_e94 == pytest.approx(expected_de94, abs=0.015)
