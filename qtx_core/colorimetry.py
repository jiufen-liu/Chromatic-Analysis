from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Sequence
import warnings

import colour
import numpy as np
from colour.utilities import ColourRuntimeWarning

from .models import Triplet


OBSERVER = "CIE 1964 10 Degree Standard Observer"
OBSERVER_2 = "CIE 1931 2 Degree Standard Observer"


def observer_name(observer_degrees: int | str = 10) -> str:
    """Return the colour-science observer identifier for the selected field."""
    value = int(observer_degrees)
    if value == 2:
        return OBSERVER_2
    if value == 10:
        return OBSERVER
    raise ValueError("观察者仅支持 2° 或 10°")

# Public/UI names.  The UI deliberately merges well-known aliases so the
# same physical/reference illuminant is not shown twice (for example CWF/F02).
# CIE LED illuminants are available in colour-science 0.4.7.  U30/U35,
# Horizon and LEDT8G are commercial viewing conditions rather than CIE
# standard illuminants; see ``illuminant_sd`` for the compatibility spectra
# used when an exact vendor SPD is not bundled with the source file.
SUPPORTED_ILLUMINANTS = (
    "A", "C", "D50", "D55", "D60", "D65", "D75", "E",
    "F01", "F02 / CWF", "F03", "F04", "F05", "F06",
    "F07", "F08", "F09", "F10", "F11 / TL84", "F12 / TL83",
    "U30", "U35", "Horizon", "LEDT8G",
    "LED-B1", "LED-B2", "LED-B3", "LED-B4", "LED-B5",
    "LED-BH1", "LED-RGB1", "LED-V1", "LED-V2",
)

_ALIASES = {
    "CWF": "FL2",
    "F02/CWF": "FL2",
    "F2/CWF": "FL2",
    "TL84": "FL11",
    "F11/TL84": "FL11",
    "TL83": "FL12",
    "F12/TL83": "FL12",
    "U3000": "U30",
    "ULTRALUME3000": "U30",
    "U3500": "U35",
    "HOR": "HORIZON",
    "HORIZON": "HORIZON",
    "LEDT8G": "LEDT8G",
    **{f"F{i}": f"FL{i}" for i in range(1, 13)},
    **{f"F{i:02d}": f"FL{i}" for i in range(1, 13)},
    **{f"FL{i}": f"FL{i}" for i in range(1, 13)},
}

# Commercial illuminants below are present in current Datacolor instruments,
# but their exact vendor SPDs are not published as CIE standards.  To avoid
# pretending that colour-science contains a proprietary spectrum, the code
# uses a documented compatibility spectrum and keeps that fact queryable.
COMMERCIAL_ILLUMINANT_NOTES = {
    "U30": "兼容光谱：3000 K 三基色荧光灯（接近 Ultralume/U3000；如有客户指定 SPD 应以客户 SPD 为准）",
    "U35": "兼容光谱：3500 K 三基色荧光灯（接近 U3500；如有客户指定 SPD 应以客户 SPD 为准）",
    "HORIZON": "兼容光谱：2300 K 普朗克辐射体（Horizon 约 2300 K）",
    "LEDT8G": "兼容光谱：CIE LED-B3（约 4100 K，接近 Datacolor LEDT8G/L840 类；客户指定 SPD 优先）",
}


def _illuminant_key(name: str) -> str:
    key = str(name or "").strip().upper().replace(" ", "")
    return _ALIASES.get(key, key)


def normalize_illuminant(name: str) -> str:
    canonical = _illuminant_key(name)
    if canonical in {"U30", "U35", "HORIZON", "LEDT8G"}:
        return canonical
    if canonical not in colour.SDS_ILLUMINANTS:
        raise ValueError(f"不支持的光源：{name}")
    return canonical


def display_illuminant(name: str) -> str:
    canonical = normalize_illuminant(name)
    aliases = {"FL2": "F02 / CWF", "FL11": "F11 / TL84", "FL12": "F12 / TL83"}
    if canonical in aliases:
        return aliases[canonical]
    if canonical.startswith("FL"):
        return f"F{int(canonical[2:]):02d}"
    if canonical == "HORIZON":
        return "Horizon"
    return canonical


def illuminant_note(name: str) -> str:
    """Return a short provenance note for non-CIE commercial conditions."""
    return COMMERCIAL_ILLUMINANT_NOTES.get(normalize_illuminant(name), "")


def illuminant_sd(name: str):
    """Return the spectral distribution used for the selected illuminant.

    Standard CIE illuminants come directly from colour-science.  Datacolor's
    commercial names are represented by the closest reproducible public
    spectrum available in colour-science; the UI can surface ``illuminant_note``
    so production users know when a customer/vendor SPD should replace it.
    """
    canonical = normalize_illuminant(name)
    if canonical in colour.SDS_ILLUMINANTS:
        return colour.SDS_ILLUMINANTS[canonical]

    light_sources = getattr(colour, "SDS_LIGHT_SOURCES", {})
    if canonical == "U30":
        for candidate in ("F32T8/TL830 (Triphosphor)", "T8 Polylux 3000"):
            try:
                return light_sources[candidate]
            except Exception:
                pass
        # CIE FL12 is a 3000 K tri-band fluorescent and is the safest fallback.
        return colour.SDS_ILLUMINANTS["FL12"]
    if canonical == "U35":
        for candidate in ("F32T8/TL835 (Triphosphor)",):
            try:
                return light_sources[candidate]
            except Exception:
                pass
        # If the optional light-source table is unavailable, use a 3500 K
        # blackbody only as a deterministic compatibility fallback.
        fn = getattr(colour, "sd_blackbody", None)
        if fn is not None:
            return fn(3500)
        return colour.SDS_ILLUMINANTS["A"]
    if canonical == "HORIZON":
        fn = getattr(colour, "sd_blackbody", None)
        if fn is not None:
            return fn(2300)
        return colour.SDS_ILLUMINANTS["A"]
    if canonical == "LEDT8G":
        return colour.SDS_ILLUMINANTS["LED-B3"]
    raise ValueError(f"不支持的光源：{name}")


def _illuminant_xy(illuminant: str, observer_degrees: int | str = 10):
    canonical = normalize_illuminant(illuminant)
    observer = observer_name(observer_degrees)
    table = colour.CCS_ILLUMINANTS.get(observer, {})
    if canonical in table:
        return table[canonical]
    # Commercial compatibility spectra do not have an entry in
    # CCS_ILLUMINANTS, so derive the chromaticity directly from the SPD.
    sd = illuminant_sd(canonical)
    cmfs = colour.MSDS_CMFS[observer]
    try:
        xyz = colour.sd_to_XYZ(sd, cmfs=cmfs, method="Integration")
    except TypeError:
        xyz = colour.sd_to_XYZ(sd, cmfs=cmfs)
    return colour.XYZ_to_xy(np.asarray(xyz, dtype=float))


def _triplet(values: Iterable[float]) -> Triplet:
    a = np.asarray(tuple(values), dtype=float)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise ValueError("XYZ/Lab必须是3个有限数值")
    return float(a[0]), float(a[1]), float(a[2])


@lru_cache(maxsize=None)
def reference_white_xyz(illuminant: str, observer_degrees: int | str = 10) -> Triplet:
    canonical = normalize_illuminant(illuminant)
    xy = _illuminant_xy(canonical, observer_degrees)
    xyz = colour.xy_to_XYZ(xy) * 100.0
    return _triplet(xyz)


def xyz_to_lab(xyz: Sequence[float], illuminant: str = "D65", observer_degrees: int | str = 10) -> Triplet:
    """Convert XYZ on a Y=100 scale to CIELAB for the selected illuminant/observer."""
    canonical = normalize_illuminant(illuminant)
    xy = _illuminant_xy(canonical, observer_degrees)
    lab = colour.XYZ_to_Lab(np.asarray(xyz, dtype=float) / 100.0, xy)
    return _triplet(lab)


# P3-5: spectral integration is one of the most expensive repeated science
# operations in the application.  A single immutable reflectance curve is often
# requested repeatedly by the library, details, workbench, MI/555 and analysis
# views under the same illuminant/observer.  Cache the *science result*, not any
# UI object.  The key contains the entire measurement curve and condition, so
# edited/replaced data cannot reuse a stale value.
_SPECTRAL_XYZ_LAB_CACHE_SIZE = 8192


@lru_cache(maxsize=_SPECTRAL_XYZ_LAB_CACHE_SIZE)
def _reflectance_to_xyz_lab_cached(
    reflectance_percent: tuple[float, ...],
    canonical_illuminant: str,
    wavelengths: tuple[float, ...],
    observer_degrees: int,
) -> tuple[Triplet, Triplet]:
    spectral_data = {
        float(w): float(r) / 100.0
        for w, r in zip(wavelengths, reflectance_percent)
    }
    sd = colour.SpectralDistribution(spectral_data, name="QTX reflectance")
    # QTX spectra end at 700 nm whereas the source CIE tables can extend
    # farther. ASTM E308 performs the intended selected-wavelength treatment;
    # colour-science emits informational shape warnings for this valid case.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ColourRuntimeWarning)
        xyz = colour.sd_to_XYZ(
            sd,
            cmfs=colour.MSDS_CMFS[observer_name(observer_degrees)],
            illuminant=illuminant_sd(canonical_illuminant),
            method="ASTM E308",
        )
    xyz_out = _triplet(xyz)
    return xyz_out, xyz_to_lab(xyz_out, canonical_illuminant, observer_degrees)


def _validate_wavelengths(wavelengths: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(w) for w in wavelengths)
    if len(values) < 2:
        raise ValueError("至少需要两个光谱点")
    if not all(np.isfinite(w) for w in values):
        raise ValueError("波长必须是有限数值")
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError("波长必须严格递增且不能重复")
    return values


def reflectance_to_xyz_lab(
    reflectance_percent: Sequence[float],
    illuminant: str = "D65",
    wavelengths: Sequence[int] = tuple(range(360, 701, 10)),
    observer_degrees: int | str = 10,
) -> tuple[Triplet, Triplet]:
    """Calculate XYZ/Lab from reflectance percentages using ASTM E308.

    P3-5 keeps the public API and numerical path unchanged while sharing a
    bounded process-level cache across UI modules.  The first call for a unique
    spectrum/condition performs the authoritative ASTM E308 integration; later
    identical calls return the immutable XYZ/Lab tuple directly.
    """
    if len(reflectance_percent) != len(wavelengths):
        raise ValueError("反射率数量与波长数量不一致")
    if len(reflectance_percent) < 2:
        raise ValueError("至少需要两个光谱点")
    if any(not np.isfinite(v) for v in reflectance_percent):
        raise ValueError("反射率包含无效数值")

    canonical = normalize_illuminant(illuminant)
    observer = int(observer_degrees)
    # Keep validation semantics explicit before entering the cached function.
    observer_name(observer)
    reflectance_key = tuple(float(v) for v in reflectance_percent)
    wavelength_key = _validate_wavelengths(wavelengths)
    return _reflectance_to_xyz_lab_cached(
        reflectance_key, canonical, wavelength_key, observer
    )




class BatchColorimetryMismatch(RuntimeError):
    """Raised when the vectorised ASTM E308 fast path disagrees with scalar reference."""


def _uniform_spectral_shape(wavelengths: Sequence[float]):
    """Return a Colour SpectralShape for a regular wavelength grid, else ``None``."""
    values = tuple(float(w) for w in wavelengths)
    if len(values) < 2:
        return None
    interval = values[1] - values[0]
    if interval <= 0:
        return None
    tolerance = max(1e-9, abs(interval) * 1e-9)
    if any(abs((values[i] - values[i - 1]) - interval) > tolerance for i in range(2, len(values))):
        return None
    return colour.SpectralShape(values[0], values[-1], interval)


def reflectances_to_xyz_lab(
    reflectance_sets: Sequence[Sequence[float]],
    illuminant: str = "D65",
    wavelengths: Sequence[int] = tuple(range(360, 701, 10)),
    observer_degrees: int | str = 10,
    *,
    verify_scalar_reference: bool = True,
) -> tuple[tuple[Triplet, Triplet], ...]:
    """Batch-convert many reflectance curves using Colour's ASTM E308 path.

    P3-6 fast path.  All curves must share one regular wavelength grid.  The
    calculation intentionally uses :class:`colour.MultiSpectralDistributions`
    (rather than the faster raw-array integration shortcut) so Colour aligns the
    measured spectra with the CMFs using its precision-oriented ASTM E308 path.

    A few representative curves are checked against the existing scalar
    ``reflectance_to_xyz_lab`` implementation the first time a caller asks for
    verification.  Any disagreement raises ``BatchColorimetryMismatch`` so the
    parser can safely fall back to the scalar reference algorithm.
    """
    rows = tuple(tuple(float(v) for v in row) for row in reflectance_sets)
    if not rows:
        return ()

    wavelength_key = _validate_wavelengths(wavelengths)
    if len(wavelength_key) < 2:
        raise ValueError("至少需要两个光谱点")
    width = len(wavelength_key)
    if any(len(row) != width for row in rows):
        raise ValueError("批量反射率数量与波长数量不一致")

    values = np.asarray(rows, dtype=float)
    if values.ndim != 2 or not np.all(np.isfinite(values)):
        raise ValueError("批量反射率包含无效数值")

    shape = _uniform_spectral_shape(wavelength_key)
    if shape is None:
        raise ValueError("批量 ASTM E308 快速路径仅支持等间隔波长")

    canonical = normalize_illuminant(illuminant)
    observer = int(observer_degrees)
    observer_id = observer_name(observer)

    # MultiSpectralDistributions expects wavelength rows and sample columns.
    msds = colour.MultiSpectralDistributions((values / 100.0).T, shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ColourRuntimeWarning)
        xyz_values = colour.msds_to_XYZ(
            msds,
            cmfs=colour.MSDS_CMFS[observer_id],
            illuminant=illuminant_sd(canonical),
            method="ASTM E308",
        )
    xyz_array = np.asarray(xyz_values, dtype=float)
    if xyz_array.ndim == 1:
        xyz_array = xyz_array.reshape(1, 3)
    if xyz_array.shape != (len(rows), 3) or not np.all(np.isfinite(xyz_array)):
        raise ValueError("批量 ASTM E308 返回了无效 XYZ")

    xy = _illuminant_xy(canonical, observer)
    lab_array = np.asarray(colour.XYZ_to_Lab(xyz_array / 100.0, xy), dtype=float)
    if lab_array.shape != (len(rows), 3) or not np.all(np.isfinite(lab_array)):
        raise ValueError("批量 ASTM E308 返回了无效 Lab")

    result = tuple(
        (_triplet(xyz_array[i]), _triplet(lab_array[i]))
        for i in range(len(rows))
    )

    if verify_scalar_reference and len(rows) >= 2:
        # Three probes are enough to detect construction/alignment differences
        # while keeping the validation overhead tiny even for 3500+ samples.
        probe_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
        for index in probe_indexes:
            xyz_ref, lab_ref = reflectance_to_xyz_lab(
                rows[index], canonical, wavelength_key, observer
            )
            xyz_fast, lab_fast = result[index]
            # This tolerance is several orders tighter than displayed Lab data;
            # it only allows harmless floating-point vectorisation noise.
            if not (
                np.allclose(xyz_fast, xyz_ref, rtol=1e-9, atol=1e-8)
                and np.allclose(lab_fast, lab_ref, rtol=1e-9, atol=1e-8)
            ):
                raise BatchColorimetryMismatch(
                    "批量 ASTM E308 与逐条参考算法不一致，已要求回退逐条计算"
                )

    return result


def science_cache_info() -> dict[str, int]:
    """Return lightweight diagnostics for the shared spectral compute cache."""
    info = _reflectance_to_xyz_lab_cached.cache_info()
    return {
        "hits": int(info.hits),
        "misses": int(info.misses),
        "maxsize": int(info.maxsize or 0),
        "currsize": int(info.currsize),
    }


def clear_science_cache() -> None:
    """Clear only performance caches; never changes measurement data/results."""
    _reflectance_to_xyz_lab_cached.cache_clear()



def cam16ucs_from_xyz(
    xyz: Sequence[float],
    illuminant: str = "D65",
    observer_degrees: int | str = 10,
) -> Triplet:
    """Return CAM16-UCS J', a', b' for XYZ on a Y=100 scale.

    Average-surround viewing conditions are used for colour-order visualisation.
    The function intentionally lives in the colour core so UI sorters use one
    consistent conversion path.
    """
    XYZ = np.asarray(_triplet(xyz), dtype=float)
    XYZ_w = np.asarray(reference_white_xyz(illuminant, observer_degrees), dtype=float)
    fn = getattr(colour, "XYZ_to_CAM16UCS", None)
    if fn is None:
        try:
            from colour.models import XYZ_to_CAM16UCS as fn  # type: ignore
        except Exception as exc:  # pragma: no cover - only old colour-science
            raise RuntimeError("当前 colour-science 不支持 CAM16-UCS") from exc

    surround = None
    vc = getattr(colour, "VIEWING_CONDITIONS_CAM16", None)
    if vc is not None:
        try:
            surround = vc["Average"]
        except Exception:
            pass
    kwargs = {"XYZ_w": XYZ_w, "L_A": 64.0, "Y_b": 20.0}
    if surround is not None:
        kwargs["surround"] = surround
    try:
        jab = fn(XYZ, **kwargs)
    except TypeError:
        # Some colour-science wrappers accept only XYZ and use reference defaults.
        jab = fn(XYZ)
    return _triplet(jab)


def munsell_hue_order_from_xyz(xyz: Sequence[float]) -> tuple[float, str, float, float]:
    """Return a sortable Munsell hue position from XYZ.

    Result is ``(hue_index, notation, value, chroma)``.  Hue index follows the
    conventional R→YR→Y→GY→G→BG→B→PB→P→RP sequence.  Neutral colours return
    ``inf`` and are therefore handled separately by the UI.
    """
    XYZ = np.asarray(_triplet(xyz), dtype=float) / 100.0
    xyY = colour.XYZ_to_xyY(XYZ)
    fn = getattr(colour, "xyY_to_munsell_colour", None)
    if fn is None:
        try:
            from colour.notation import xyY_to_munsell_colour as fn  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("当前 colour-science 不支持 Munsell renotation") from exc
    # colour-science 的 Munsell renotation 会对超出 MacAdam 插值边界的 xyY
    # 连续发 ColourUsageWarning。这里仍保留返回/异常语义，但避免同一批色样在 UI
    # 排序、重绘时把控制台刷满并拖慢程序。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        notation = str(fn(xyY)).strip()
    import re
    # Examples: ``5YR 6/8`` or decimal variants.
    m = re.match(r"^([0-9.]+)\s*([A-Z]+)\s+([0-9.]+)\s*/\s*([0-9.]+)", notation, re.I)
    if not m:
        return float("inf"), notation, float(xyY[2] * 10.0), 0.0
    number = float(m.group(1)); family = m.group(2).upper(); value = float(m.group(3)); chroma = float(m.group(4))
    families = ["R", "YR", "Y", "GY", "G", "BG", "B", "PB", "P", "RP"]
    if family not in families:
        return float("inf"), notation, value, chroma
    return families.index(family) * 10.0 + number, notation, value, chroma


def delta_h_cielab_signed(standard_lab: Sequence[float], sample_lab: Sequence[float]) -> float:
    """Signed CIELAB hue-difference component ΔH*.

    Datacolor-style ΔH* is a colour-difference component, not the direct hue-angle
    difference Δh°.  Magnitude follows sqrt(Δa² + Δb² - ΔC²); sign follows the
    shortest hue-angle rotation from standard to sample.
    """
    L1,a1,b1=_triplet(standard_lab); L2,a2,b2=_triplet(sample_lab)
    c1=float(np.hypot(a1,b1)); c2=float(np.hypot(a2,b2))
    da=a2-a1; db=b2-b1; dc=c2-c1
    magnitude=float(np.sqrt(max(0.0, da*da + db*db - dc*dc)))
    if magnitude <= 1e-12:
        return 0.0
    h1=float(np.degrees(np.arctan2(b1,a1)) % 360.0)
    h2=float(np.degrees(np.arctan2(b2,a2)) % 360.0)
    angle_delta=((h2-h1+180.0)%360.0)-180.0
    return float(np.copysign(magnitude, angle_delta))

def delta_e(standard_lab: Sequence[float], sample_lab: Sequence[float], method: str = "CIE 2000", **kwargs: float) -> float:
    methods = {
        "DE76": "CIE 1976",
        "CIE1976": "CIE 1976",
        "DE94": "CIE 1994",
        "CIE1994": "CIE 1994",
        "DE00": "CIE 2000",
        "CIEDE2000": "CIE 2000",
        "CMC": "CMC",
    }
    normalized = method.strip().upper().replace(" ", "")
    selected = methods.get(normalized, method)
    value = colour.delta_E(
        np.asarray(standard_lab, dtype=float),
        np.asarray(sample_lab, dtype=float),
        method=selected,
        **kwargs,
    )
    return float(value)


def delta_e_many(standard_lab: Sequence[float], sample_labs: Sequence[Sequence[float]], method: str = "CIE 2000", **kwargs: float) -> np.ndarray:
    """Vectorised colour difference from one standard to many Lab samples.

    Find/lookup can rank thousands of D65/10° index rows without constructing
    full Sample/spectrum payloads or calling ``analyse_pair`` thousands of times.
    The calculation still delegates to colour-science's same ``delta_E``
    implementation used by :func:`delta_e`; only the batching is different.
    """
    methods = {
        "DE76": "CIE 1976",
        "CIE1976": "CIE 1976",
        "DE94": "CIE 1994",
        "CIE1994": "CIE 1994",
        "DE00": "CIE 2000",
        "CIEDE2000": "CIE 2000",
        "CMC": "CMC",
    }
    normalized = method.strip().upper().replace(" ", "")
    selected = methods.get(normalized, method)
    labs = np.asarray(sample_labs, dtype=float)
    if labs.size == 0:
        return np.empty((0,), dtype=float)
    labs = labs.reshape((-1, 3))
    standard = np.asarray(standard_lab, dtype=float).reshape((1, 3))
    standard = np.broadcast_to(standard, labs.shape)
    values = colour.delta_E(standard, labs, method=selected, **kwargs)
    return np.asarray(values, dtype=float).reshape((-1,))


def cie_whiteness_d65_10(xyz: Sequence[float]) -> float:
    X, Y, Z = _triplet(xyz)
    total = X + Y + Z
    if total <= 0:
        raise ValueError("XYZ之和必须大于0")
    x, y = X / total, Y / total
    xn, yn = colour.CCS_ILLUMINANTS[OBSERVER]["D65"]
    return float(Y + 800.0 * (xn - x) + 1700.0 * (yn - y))


def cie_tint_d65_10(xyz: Sequence[float]) -> float:
    X, Y, Z = _triplet(xyz)
    total = X + Y + Z
    if total <= 0:
        raise ValueError("XYZ之和必须大于0")
    x, y = X / total, Y / total
    xn, yn = colour.CCS_ILLUMINANTS[OBSERVER]["D65"]
    return float(900.0 * (xn - x) - 650.0 * (yn - y))
