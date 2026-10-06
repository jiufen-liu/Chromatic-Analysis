from pathlib import Path

import pytest

from qtx_core.qtx_parser import parse_qtx_file


FIXTURE = Path(__file__).parent / "data" / "Sun Way.qtx"


def test_sun_way_standard_prefers_datacolor_transformed_target_xyz():
    samples = parse_qtx_file(FIXTURE)
    standard = samples[0]

    # Datacolor uses STD_TX/TY/TZ for the comparison target in this QTX.
    assert standard.display_name == "Rich Black 21067PW-STD"
    assert standard.xyz_d65_10 == pytest.approx((2.162688, 2.251873, 2.530830), abs=1e-6)
    assert standard.lab_d65_10 == pytest.approx((16.75714, 0.60723, -0.87820), abs=0.0002)


@pytest.mark.parametrize(
    "index, expected_name, expected_xyz, expected_lab",
    [
        (1, "4PMBK368R-5 HCSD 50-72 10% DTY YARN", (2.685828, 2.821970, 3.159992), (19.316322, 0.194895, -0.871604)),
        (2, "4PMBK368R-5 HCSD 50-72 10% DTY SOCK", (1.961426, 2.057320, 2.296131), (15.785226, 0.253805, -0.723102)),
        (3, "4PMBK368R-5 SXSD 50-72 10% DTY YARN", (2.788594, 2.926962, 3.272077), (19.748983, 0.249690, -0.847393)),
        (4, "4PMBK368R-5 SXSD 50-72 10% DTY SOCK", (2.129105, 2.229101, 2.485307), (16.646347, 0.346955, -0.723239)),
        (5, "4PMBK368R-5 YYSXSD 50-72 10% DTY YARN", (2.672725, 2.807767, 3.126456), (19.256973, 0.202448, -0.754662)),
        (6, "4PMBK368R-5 YYSXSD 50-72 10% DTY SOCK", (1.890882, 1.981647, 2.199463), (15.390636, 0.288939, -0.613020)),
    ],
)
def test_sun_way_embedded_batch_d65_payload_is_authoritative(index, expected_name, expected_xyz, expected_lab):
    samples = parse_qtx_file(FIXTURE)
    sample = samples[index]

    assert sample.display_name == expected_name
    assert sample.xyz_d65_10 == pytest.approx(expected_xyz, abs=1e-6)
    assert sample.lab_d65_10 == pytest.approx(expected_lab, abs=1e-6)


def test_sun_way_parser_scope_does_not_reclassify_sections():
    samples = parse_qtx_file(FIXTURE)

    # Hotfix24 intentionally fixes only D65 value selection.  It does not alter
    # section/type classification or any UI/business behaviour.
    assert all(sample.kind == "STD" for sample in samples)


def test_sun_way_d65_deltas_reproduce_datacolor_embedded_results():
    samples = parse_qtx_file(FIXTURE)
    standard = samples[0]

    for sample in samples[1:]:
        dL = sample.lab_d65_10[0] - standard.lab_d65_10[0]
        da = sample.lab_d65_10[1] - standard.lab_d65_10[1]
        db = sample.lab_d65_10[2] - standard.lab_d65_10[2]
        de76 = (dL * dL + da * da + db * db) ** 0.5

        assert dL == pytest.approx(float(sample.raw["CIE_DL"]), abs=2e-5)
        assert da == pytest.approx(float(sample.raw["CIE_Da"]), abs=2e-5)
        assert db == pytest.approx(float(sample.raw["CIE_Db"]), abs=2e-5)
        assert de76 == pytest.approx(float(sample.raw["CIE_DE"]), abs=2e-5)
