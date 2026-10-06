from __future__ import annotations

import math

import numpy as np
import pytest

from qtx_core import qtx_parser
from qtx_core.colorimetry import (
    BatchColorimetryMismatch,
    reflectance_to_xyz_lab,
    reflectances_to_xyz_lab,
)


def _spectral_only_qtx(count: int = 24) -> str:
    wavelengths = list(range(360, 701, 10))
    sections = []
    for i in range(count):
        curve = [20.0 + i * 0.05 + j * 0.2 for j in range(len(wavelengths))]
        sections.append(
            "\n".join(
                [
                    f"[STANDARD_DATA {i}]",
                    f"STD_NAME=Sample {i}",
                    f"STD_GUID=guid-{i}",
                    "STD_REFLPOINTS=35",
                    "STD_REFLINTERVAL=10",
                    "STD_REFLLOW=360",
                    "STD_R=" + ",".join(f"{v:.6f}" for v in curve),
                ]
            )
        )
    return "\n".join(sections)


def test_large_same_grid_qtx_uses_one_batch_call(monkeypatch):
    calls = []

    def fake_batch(rows, illuminant, wavelengths, observer, verify_scalar_reference=True):
        calls.append((len(rows), illuminant, tuple(wavelengths), observer, verify_scalar_reference))
        return tuple(
            (((10.0 + i, 20.0 + i, 30.0 + i), (40.0 + i, 1.0, 2.0)))
            for i in range(len(rows))
        )

    def scalar_must_not_run(*args, **kwargs):
        raise AssertionError("scalar path should not run when the batch path succeeds")

    monkeypatch.setattr(qtx_parser, "reflectances_to_xyz_lab", fake_batch)
    monkeypatch.setattr(qtx_parser, "reflectance_to_xyz_lab", scalar_must_not_run)
    monkeypatch.delenv("CHROMATIC_DISABLE_QTX_BATCH", raising=False)

    samples = qtx_parser.parse_qtx_text(_spectral_only_qtx(24), "large.qtx")
    assert len(samples) == 24
    assert len(calls) == 1
    assert calls[0][0] == 24
    assert calls[0][1] == "D65"
    assert calls[0][3] == 10
    assert samples[0].display_name == "Sample 0"
    assert samples[-1].display_name == "Sample 23"
    diag = qtx_parser.qtx_parse_diagnostics()
    assert diag["batch_samples"] == 24
    assert diag["scalar_samples"] == 0
    assert diag["fallback_groups"] == 0


def test_batch_validation_failure_falls_back_to_scalar(monkeypatch):
    def mismatching_batch(*args, **kwargs):
        raise BatchColorimetryMismatch("test")

    scalar_calls = []

    def fake_scalar(reflectance, illuminant, wavelengths, observer):
        scalar_calls.append(reflectance)
        i = len(scalar_calls)
        return (10.0 + i, 20.0, 30.0), (50.0, 1.0 + i, 2.0)

    monkeypatch.setattr(qtx_parser, "reflectances_to_xyz_lab", mismatching_batch)
    monkeypatch.setattr(qtx_parser, "reflectance_to_xyz_lab", fake_scalar)
    monkeypatch.delenv("CHROMATIC_DISABLE_QTX_BATCH", raising=False)

    samples = qtx_parser.parse_qtx_text(_spectral_only_qtx(20), "fallback.qtx")
    assert len(samples) == 20
    assert len(scalar_calls) == 20
    diag = qtx_parser.qtx_parse_diagnostics()
    assert diag["batch_samples"] == 0
    assert diag["scalar_samples"] == 20
    assert diag["fallback_groups"] == 1
    assert diag["verification_failures"] == 1


def test_environment_switch_can_force_reference_scalar_path(monkeypatch):
    batch_calls = []
    scalar_calls = []

    monkeypatch.setenv("CHROMATIC_DISABLE_QTX_BATCH", "1")
    monkeypatch.setattr(
        qtx_parser,
        "reflectances_to_xyz_lab",
        lambda *a, **k: batch_calls.append(True),
    )

    def fake_scalar(reflectance, illuminant, wavelengths, observer):
        scalar_calls.append(1)
        return (1.0, 2.0, 3.0), (4.0, 5.0, 6.0)

    monkeypatch.setattr(qtx_parser, "reflectance_to_xyz_lab", fake_scalar)
    samples = qtx_parser.parse_qtx_text(_spectral_only_qtx(18), "scalar.qtx")
    assert len(samples) == 18
    assert batch_calls == []
    assert len(scalar_calls) == 18
    assert qtx_parser.qtx_parse_diagnostics()["batch_enabled"] is False


def test_batch_colourimetry_matches_scalar_reference_for_all_rows():
    wavelengths = tuple(range(360, 701, 10))
    curves = []
    for i in range(18):
        curve = []
        for j, wavelength in enumerate(wavelengths):
            # Smooth, bounded, unique synthetic reflectance curves.
            value = 35.0 + 20.0 * math.sin((wavelength - 330) / 55.0 + i * 0.17)
            value += 0.7 * i + 0.08 * j
            curve.append(max(0.2, min(96.0, value)))
        curves.append(tuple(curve))

    batch = reflectances_to_xyz_lab(
        curves, "D65", wavelengths, 10, verify_scalar_reference=True
    )
    scalar = tuple(
        reflectance_to_xyz_lab(curve, "D65", wavelengths, 10) for curve in curves
    )

    assert len(batch) == len(scalar)
    for (xyz_batch, lab_batch), (xyz_scalar, lab_scalar) in zip(batch, scalar):
        np.testing.assert_allclose(xyz_batch, xyz_scalar, rtol=1e-9, atol=1e-8)
        np.testing.assert_allclose(lab_batch, lab_scalar, rtol=1e-9, atol=1e-8)
