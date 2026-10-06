import pytest

from qtx_core import (
    SUPPORTED_ILLUMINANTS,
    average_reflectance,
    normalize_illuminant,
    reflectance_to_xyz_lab,
)


def test_required_illuminants_are_available():
    assert {normalize_illuminant(x) for x in ("A", "D65", "F01", "F02", "F11")}.issubset(
        {normalize_illuminant(x) for x in SUPPORTED_ILLUMINANTS})


def test_datacolor_illuminant_aliases():
    assert normalize_illuminant("F02") == "FL2"
    assert normalize_illuminant("CWF") == "FL2"
    assert normalize_illuminant("F11") == "FL11"
    assert normalize_illuminant("TL84") == "FL11"


def test_empty_spectral_average_is_rejected():
    with pytest.raises(ValueError, match="至少选择一个样本"):
        average_reflectance([])


@pytest.mark.parametrize("illuminant", SUPPORTED_ILLUMINANTS)
def test_every_advertised_illuminant_can_be_calculated(illuminant):
    spectrum = [50.0] * 35
    xyz, lab = reflectance_to_xyz_lab(spectrum, illuminant)
    # A spectrally neutral 50% reflector has Y=50; X and Z follow the
    # selected illuminant's white chromaticity rather than both equalling 50.
    assert xyz[1] == pytest.approx(50.0, abs=0.06)
    assert lab[0] == pytest.approx(76.07, abs=0.03)
    # The QTX wavelength range stops at 700 nm, so narrow fluorescent
    # illuminants can leave a small numerical residual around neutral.
    assert lab[1] == pytest.approx(0.0, abs=0.15)
    assert lab[2] == pytest.approx(0.0, abs=0.15)


def test_average_spectrum_accepts_compatible_different_wavelength_grids():
    from qtx_core import Sample, average_spectral_standard
    s1=Sample('a','A','STD',(0,0,0),(50,0,0),(10.0,20.0,30.0),(360,370,380))
    s2=Sample('b','B','STD',(0,0,0),(50,0,0),(20.0,30.0,40.0),(370,380,390))
    avg=average_spectral_standard([s1,s2],'avg')
    assert avg.wavelengths == (370,380)
    assert avg.reflectance == pytest.approx((20.0,30.0))
