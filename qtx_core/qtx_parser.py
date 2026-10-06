from __future__ import annotations

import os
import math
import colour
import numpy as np
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from .colorimetry import (
    BatchColorimetryMismatch,
    reflectance_to_xyz_lab,
    reflectances_to_xyz_lab,
    xyz_to_lab,
)
from .models import Sample, Triplet


_SECTION_RE = re.compile(r"^\[([A-Za-z_][\w]*)(?:\s+[^\]]+)?\]\s*,?$")
_BATCH_MIN_SAMPLES = 16
_LAST_PARSE_DIAGNOSTICS: dict[str, object] = {}


def _float(value: str | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    value = str(value).strip().rstrip(",")
    if not value:
        return default
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except ValueError:
        return default


def _floats(value: str | None) -> tuple[float, ...]:
    """Never drop a spectral token: doing so changes every following wavelength."""
    if not value or not value.strip():
        return ()
    tokens = value.strip().rstrip(",").split(",")
    try:
        result = tuple(float(item.strip()) for item in tokens)
    except ValueError as exc:
        raise ValueError("光谱反射率包含空值或非数值，不能自动跳过波长点") from exc
    if not all(math.isfinite(v) for v in result):
        raise ValueError("光谱反射率必须是有限数值")
    return result


def _parse_blocks(text: str) -> list[tuple[str, dict[str, str]]]:
    """Parse QTX sections while preserving all raw metadata.

    P3-6 keeps the established permissive grammar but precompiles the section
    expression and avoids extra ``partition`` objects on every assignment.
    """
    blocks: list[tuple[str, dict[str, str]]] = []
    block_type: str | None = None
    data: dict[str, str] = {}
    append_block = blocks.append
    section_match = _SECTION_RE.match

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = section_match(line)
        if match:
            if block_type is not None:
                append_block((block_type, data))
            block_type, data = match.group(1).upper(), {}
        elif block_type is not None:
            split_at = line.find("=")
            if split_at >= 0:
                key = line[:split_at].strip()
                value = line[split_at + 1 :].strip().rstrip(",").strip()
                data[key] = value
    if block_type is not None:
        append_block((block_type, data))
    return blocks


@dataclass(slots=True)
class _PendingSpectrum:
    order: int
    data: dict[str, str]
    prefix: str
    kind: str
    reflectance: tuple[float, ...]
    wavelengths: tuple[int, ...]
    source_file: str


def _make_sample(
    data: dict[str, str],
    prefix: str,
    kind: str,
    reflectance: tuple[float, ...],
    sample_wavelengths: tuple[int, ...],
    source_file: str,
    xyz: Triplet,
    lab: Triplet,
) -> Sample:
    return Sample(
        sample_id=(data.get(f"{prefix}_GUID") or str(uuid.uuid4())).strip(),
        display_name=(data.get(f"{prefix}_NAME") or source_file or kind).strip(),
        kind=kind,
        xyz_d65_10=(float(xyz[0]), float(xyz[1]), float(xyz[2])),
        lab_d65_10=(float(lab[0]), float(lab[1]), float(lab[2])),
        reflectance=reflectance,
        wavelengths=sample_wavelengths,
        source_file=source_file,
        viewing=(data.get(f"{prefix}_VIEWING") or "").strip(),
        raw=dict(data),
    )


def _embedded_datacolor_lab(xyz: Triplet) -> Triplet:
    """Use the tabulated D65/10° white for embedded Datacolor coordinates.

    Datacolor reference fixtures use Xn=94.811, Yn=100, Zn=107.304. Colour's
    more precise chromaticity-derived white differs slightly; use this explicit
    compatibility convention only for QTX caches, never spectral integration.
    """
    xy = colour.XYZ_to_xy(np.asarray((94.811, 100.0, 107.304)))
    lab = colour.XYZ_to_Lab(np.asarray(xyz, dtype=float) / 100.0, xy)
    return tuple(float(v) for v in lab)


def _embedded_xyz_lab(
    section: str, data: dict[str, str], prefix: str
) -> tuple[Triplet | None, Triplet | None]:
    X = _float(data.get(f"{prefix}_X"))
    Y = _float(data.get(f"{prefix}_Y"))
    Z = _float(data.get(f"{prefix}_Z"))
    L = _float(data.get(f"{prefix}_CIEL"))
    a = _float(data.get(f"{prefix}_CIEa"))
    b = _float(data.get(f"{prefix}_CIEb"))

    # Preserve the Datacolor production precedence rules from Hotfix24+.
    if section == "STANDARD_DATA":
        TX = _float(data.get("STD_TX"))
        TY = _float(data.get("STD_TY"))
        TZ = _float(data.get("STD_TZ"))
        if None not in (TX, TY, TZ):
            X, Y, Z = TX, TY, TZ
            L = a = b = None
        elif None in (X, Y, Z):
            illname = str(data.get("ILLNAME") or "").upper().replace("°", " DEG ")
            is_d65_10 = "D65" in illname and ("10" in illname or "TEN" in illname)
            bX = _float(data.get("BAT_X"))
            bY = _float(data.get("BAT_Y"))
            bZ = _float(data.get("BAT_Z"))
            bL = _float(data.get("BAT_CIEL"))
            ba = _float(data.get("BAT_CIEa"))
            bb = _float(data.get("BAT_CIEb"))
            if is_d65_10 and None not in (bX, bY, bZ):
                X, Y, Z = bX, bY, bZ
                if None not in (bL, ba, bb):
                    L, a, b = bL, ba, bb
                else:
                    L = a = b = None

    if None in (X, Y, Z):
        return None, None
    xyz: Triplet = (float(X), float(Y), float(Z))
    if None not in (L, a, b):
        lab: Triplet = (float(L), float(a), float(b))
    else:
        lab = _embedded_datacolor_lab(xyz)
    return xyz, lab


def qtx_parse_diagnostics() -> dict[str, object]:
    """Return diagnostics for the most recent parse in the current process."""
    return dict(_LAST_PARSE_DIAGNOSTICS)


def _batch_disabled() -> bool:
    value = os.environ.get("CHROMATIC_DISABLE_QTX_BATCH", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def parse_qtx_text(text: str, source_file: str = "") -> list[Sample]:
    started = perf_counter()
    blocks = _parse_blocks(text)
    block_done = perf_counter()
    default_wavelengths = tuple(range(360, 701, 10))

    slots: list[Sample | _PendingSpectrum | None] = []
    pending_groups: dict[tuple[int, ...], list[_PendingSpectrum]] = {}
    embedded_count = 0
    spectral_pending = 0
    skipped_count = 0

    for section, data in blocks:
        if section == "STANDARD_DATA":
            prefix, kind = "STD", "STD"
        elif section == "BATCH_DATA":
            prefix, kind = "BAT", "BAT"
        else:
            continue

        reflectance = _floats(data.get(f"{prefix}_R"))
        refl_low = int(_float(data.get(f"{prefix}_REFLLOW"), 360) or 360)
        refl_interval = int(_float(data.get(f"{prefix}_REFLINTERVAL"), 10) or 10)
        refl_points = int(
            _float(data.get(f"{prefix}_REFLPOINTS"), len(reflectance))
            or len(reflectance)
        )
        if reflectance:
            if len(reflectance) != refl_points:
                raise ValueError(f"光谱反射率数量与声明不一致：{len(reflectance)} / {refl_points}")
            if refl_interval <= 0:
                raise ValueError("光谱波长间隔必须大于零")
            sample_wavelengths = tuple(refl_low + i * refl_interval for i in range(refl_points))
        else:
            sample_wavelengths = default_wavelengths

        xyz, lab = _embedded_xyz_lab(section, data, prefix)
        if xyz is not None and lab is not None:
            slots.append(
                _make_sample(
                    data,
                    prefix,
                    kind,
                    reflectance,
                    sample_wavelengths,
                    source_file,
                    xyz,
                    lab,
                )
            )
            embedded_count += 1
            continue

        if reflectance:
            pending = _PendingSpectrum(
                order=len(slots),
                data=data,
                prefix=prefix,
                kind=kind,
                reflectance=reflectance,
                wavelengths=sample_wavelengths,
                source_file=source_file,
            )
            slots.append(pending)
            pending_groups.setdefault(sample_wavelengths, []).append(pending)
            spectral_pending += 1
            continue

        # Lab-only records remain intentionally unsupported because downstream
        # colour-space functions require XYZ, matching the pre-P3-6 behaviour.
        skipped_count += 1

    science_started = perf_counter()
    batch_groups = 0
    batch_samples = 0
    scalar_samples = 0
    fallback_groups = 0
    verify_failures = 0
    batch_enabled = not _batch_disabled()

    for wavelengths, group in pending_groups.items():
        computed: tuple[tuple[Triplet, Triplet], ...] | None = None
        use_batch = batch_enabled and len(group) >= _BATCH_MIN_SAMPLES
        if use_batch:
            try:
                computed = reflectances_to_xyz_lab(
                    [item.reflectance for item in group],
                    "D65",
                    wavelengths,
                    10,
                    verify_scalar_reference=True,
                )
                batch_groups += 1
                batch_samples += len(group)
            except BatchColorimetryMismatch:
                verify_failures += 1
                fallback_groups += 1
                computed = None
            except Exception:
                # Unusual grids or Colour-version differences must never make a
                # formerly valid QTX unreadable.  The scalar reference remains
                # the authoritative safety net.
                fallback_groups += 1
                computed = None

        if computed is None:
            scalar_results: list[tuple[Triplet, Triplet]] = []
            append_result = scalar_results.append
            for item in group:
                try:
                    append_result(
                        reflectance_to_xyz_lab(
                            item.reflectance, "D65", item.wavelengths, 10
                        )
                    )
                except Exception:
                    append_result((None, None))  # type: ignore[arg-type]
            computed = tuple(scalar_results)
            scalar_samples += len(group)

        for item, pair in zip(group, computed):
            xyz, lab = pair
            if xyz is None or lab is None:
                slots[item.order] = None
                skipped_count += 1
                continue
            slots[item.order] = _make_sample(
                item.data,
                item.prefix,
                item.kind,
                item.reflectance,
                item.wavelengths,
                item.source_file,
                xyz,
                lab,
            )

    samples = [item for item in slots if isinstance(item, Sample)]
    ended = perf_counter()
    _LAST_PARSE_DIAGNOSTICS.clear()
    _LAST_PARSE_DIAGNOSTICS.update(
        {
            "source_file": source_file,
            "blocks": len(blocks),
            "samples": len(samples),
            "embedded_xyz_samples": embedded_count,
            "spectral_samples": spectral_pending,
            "batch_enabled": batch_enabled,
            "batch_groups": batch_groups,
            "batch_samples": batch_samples,
            "scalar_samples": scalar_samples,
            "fallback_groups": fallback_groups,
            "verification_failures": verify_failures,
            "skipped": skipped_count,
            "block_parse_ms": round((block_done - started) * 1000.0, 3),
            "sample_prepare_ms": round((science_started - block_done) * 1000.0, 3),
            "science_ms": round((ended - science_started) * 1000.0, 3),
            "total_ms": round((ended - started) * 1000.0, 3),
        }
    )
    return samples


def parse_qtx_file(path: str | Path) -> list[Sample]:
    source = Path(path)
    started = perf_counter()
    text = source.read_text(encoding="utf-8-sig", errors="ignore")
    read_done = perf_counter()
    samples = parse_qtx_text(text, source.name)
    ended = perf_counter()
    # parse_qtx_text() owns the authoritative parser breakdown.  Add file-I/O
    # timing afterwards so performance reports can distinguish network/disk
    # latency from parser/colour-science CPU time.
    _LAST_PARSE_DIAGNOSTICS.update({
        "file_read_ms": round((read_done - started) * 1000.0, 3),
        "file_total_ms": round((ended - started) * 1000.0, 3),
    })
    return samples


def export_qtx_file(path: str | Path, samples: list[Sample] | tuple[Sample, ...]) -> None:
    """Export a compact Datacolor-style QTX containing the supplied samples.

    Existing QTX metadata is preserved where it belongs to the sample kind. Core
    colour/spectral fields are regenerated from the current Sample object so edits
    to name/attributes round-trip through this application. Custom ``ATTR_*``
    fields are written as ordinary records; Datacolor may ignore unknown records,
    while this application will preserve them on re-import.
    """
    target = Path(path)
    records: list[str] = []
    std_i = bat_i = 0
    for sample in samples:
        kind = 'STD' if str(sample.kind).upper() == 'STD' else 'BAT'
        idx = std_i if kind == 'STD' else bat_i
        if kind == 'STD':
            std_i += 1
            section = f'[STANDARD_DATA {idx}]'
        else:
            bat_i += 1
            section = f'[BATCH_DATA {idx}]'
        prefix = kind
        raw = dict(sample.raw or {})
        lines = [section]

        core = {
            f'{prefix}_NAME': sample.display_name,
            f'{prefix}_GUID': sample.sample_id,
            f'{prefix}_REFLPOINTS': str(len(sample.reflectance)),
            f'{prefix}_REFLINTERVAL': str((sample.wavelengths[1]-sample.wavelengths[0]) if len(sample.wavelengths) > 1 else 10),
            f'{prefix}_REFLLOW': str(sample.wavelengths[0] if sample.wavelengths else 360),
            f'{prefix}_VIEWING': sample.viewing or raw.get(f'{prefix}_VIEWING',''),
            f'{prefix}_R': ','.join(f'{float(v):.6f}' for v in sample.reflectance),
            f'{prefix}_X': f'{float(sample.xyz_d65_10[0]):.6f}',
            f'{prefix}_Y': f'{float(sample.xyz_d65_10[1]):.6f}',
            f'{prefix}_Z': f'{float(sample.xyz_d65_10[2]):.6f}',
            f'{prefix}_CIEL': f'{float(sample.lab_d65_10[0]):.6f}',
            f'{prefix}_CIEa': f'{float(sample.lab_d65_10[1]):.6f}',
            f'{prefix}_CIEb': f'{float(sample.lab_d65_10[2]):.6f}',
        }
        # STD_TX/TY/TZ take precedence on import. Preserve the transformed-target
        # marker but refresh its values, otherwise old raw data undoes an edit.
        if kind == 'STD' and any(f'STD_T{axis}' in raw for axis in ('X', 'Y', 'Z')):
            core.update({f'STD_T{axis}': f'{float(value):.6f}'
                         for axis, value in zip(('X', 'Y', 'Z'), sample.xyz_d65_10)})
        # Preserve same-kind raw metadata and all generic/custom attributes, but
        # never allow stale raw values to overwrite regenerated core fields.
        for key, value in raw.items():
            key = str(key)
            if key in core:
                continue
            if key.startswith(('STD_','BAT_')) and not key.startswith(prefix + '_'):
                continue
            if key.startswith('__'):
                continue
            lines.append(f'{key}={value}')
        for key, value in core.items():
            lines.append(f'{key}={value}')
        records.append('\n'.join(lines))
    target.write_text('\n'.join(records) + ('\n' if records else ''), encoding='utf-8-sig')
