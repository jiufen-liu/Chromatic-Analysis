from __future__ import annotations

from typing import Sequence

import numpy as np

from .colorimetry import delta_e, xyz_to_lab


def multiplicative_correct_xyz(
    standard_reference_xyz: Sequence[float],
    sample_reference_xyz: Sequence[float],
    sample_test_xyz: Sequence[float],
) -> tuple[float, float, float]:
    std_ref = np.asarray(standard_reference_xyz, dtype=float)
    spl_ref = np.asarray(sample_reference_xyz, dtype=float)
    spl_test = np.asarray(sample_test_xyz, dtype=float)
    if std_ref.shape != (3,) or spl_ref.shape != (3,) or spl_test.shape != (3,):
        raise ValueError("XYZ必须是3个数值")
    if np.any(np.isclose(spl_ref, 0.0)):
        raise ValueError("参考光源下的样本XYZ不能包含0")
    corrected = spl_test * std_ref / spl_ref
    return float(corrected[0]), float(corrected[1]), float(corrected[2])


def metamerism_index_multiplicative(
    standard_reference_xyz: Sequence[float],
    sample_reference_xyz: Sequence[float],
    standard_test_xyz: Sequence[float],
    sample_test_xyz: Sequence[float],
    test_illuminant: str,
    observer_degrees: int | str = 10,
) -> float:
    """ISO/CIE illuminant metamerism index with multiplicative correction.

    The resulting index is the CIELAB ΔE*ab between the test-illuminant
    standard and the virtually corrected sample.
    """
    corrected_xyz = multiplicative_correct_xyz(
        standard_reference_xyz,
        sample_reference_xyz,
        sample_test_xyz,
    )
    corrected_lab = xyz_to_lab(corrected_xyz, test_illuminant, observer_degrees)
    standard_test_lab = xyz_to_lab(standard_test_xyz, test_illuminant, observer_degrees)
    return delta_e(standard_test_lab, corrected_lab, "CIE 1976")
