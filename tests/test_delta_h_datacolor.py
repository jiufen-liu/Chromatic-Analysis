import math

from qtx_core.colorimetry import delta_h_cielab_signed


def test_datacolor_delta_h_reference_rows():
    # Reference values from the user's Datacolor comparison screenshot.
    standard = (19.40, 0.73, 0.08)
    rows = [
        ((19.14, 0.60, 0.10), +0.04, 0.03),
        ((13.86, 0.62, 0.39), +0.32, 0.03),
        ((27.91, 0.04, -1.88), -1.73, 0.04),
    ]
    for sample, expected, tol in rows:
        value = delta_h_cielab_signed(standard, sample)
        assert math.isclose(value, expected, abs_tol=tol), (value, expected)
