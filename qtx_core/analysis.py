from __future__ import annotations

from collections.abc import Sequence

from .colorimetry import delta_e, display_illuminant, reflectance_to_xyz_lab, illuminant_note, observer_name
from .metamerism import metamerism_index_multiplicative
from .models import ColorResult, PairAnalysis, Sample


def _common_average_grid(samples: Sequence[Sample]) -> tuple[int, ...]:
    """Return a stable common wavelength grid for spectral averaging.

    Real textile QTX/CPX files are not always sampled on byte-for-byte identical
    wavelength tuples (for example 360-700/10 nm vs a subset or an imported
    Excel series).  Averaging should therefore use the *overlapping* spectral
    domain and linear interpolation instead of rejecting otherwise compatible
    samples.
    """
    if not samples:
        raise ValueError("至少选择一个样本")
    if any(not sample.has_spectrum() for sample in samples):
        raise ValueError("所有样本都必须包含完整光谱")
    lo=max(min(int(x) for x in s.wavelengths) for s in samples)
    hi=min(max(int(x) for x in s.wavelengths) for s in samples)
    if lo > hi:
        raise ValueError("参与平均的样本没有共同波长范围")
    points=sorted({int(w) for s in samples for w in s.wavelengths if lo <= int(w) <= hi})
    if len(points) < 2:
        raise ValueError("共同波长范围不足，无法建立光谱平均标准")
    return tuple(points)


def _interp_spectrum(sample: Sample, wavelengths: tuple[int, ...]) -> tuple[float, ...]:
    xs=[float(x) for x in sample.wavelengths]; ys=[float(x) for x in sample.reflectance]
    out=[]
    for w in wavelengths:
        x=float(w)
        if x <= xs[0]:
            out.append(ys[0]); continue
        if x >= xs[-1]:
            out.append(ys[-1]); continue
        j=1
        while j < len(xs) and xs[j] < x:
            j += 1
        x0,x1=xs[j-1],xs[j]; y0,y1=ys[j-1],ys[j]
        f=(x-x0)/(x1-x0) if x1 != x0 else 0.0
        out.append(y0+(y1-y0)*f)
    return tuple(out)


def average_reflectance(samples: Sequence[Sample]) -> tuple[float, ...]:
    wavelengths=_common_average_grid(samples)
    spectra=[_interp_spectrum(sample,wavelengths) for sample in samples]
    count=float(len(spectra))
    return tuple(sum(spec[i] for spec in spectra)/count for i in range(len(wavelengths)))


def average_spectral_standard(samples: Sequence[Sample], name: str = "模拟标准") -> Sample:
    wavelengths=_common_average_grid(samples)
    spectra=[_interp_spectrum(sample,wavelengths) for sample in samples]
    count=float(len(spectra))
    spectrum=tuple(sum(spec[i] for spec in spectra)/count for i in range(len(wavelengths)))
    xyz, lab = reflectance_to_xyz_lab(spectrum, "D65", wavelengths)
    return Sample(
        sample_id="__SPECTRAL_AVERAGE__",
        display_name=name,
        kind="AVERAGE",
        xyz_d65_10=xyz,
        lab_d65_10=lab,
        reflectance=spectrum,
        wavelengths=wavelengths,
        source_file="; ".join(sorted({s.source_file for s in samples if s.source_file})),
        viewing="spectral average",
    )


def color_result(sample: Sample, illuminant: str, observer_degrees: int | str = 10) -> ColorResult:
    observer_name(observer_degrees)
    # Datacolor stores the authoritative D65/10° XYZ and (for BAT) Lab in
    # the QTX. Preserve those values so the default view matches the source
    # exactly; alternate illuminants are recomputed from the spectrum.
    if display_illuminant(illuminant) == "D65" and sample.kind != "AVERAGE" and int(observer_degrees) == 10:
        return ColorResult("D65", sample.xyz_d65_10, sample.lab_d65_10,
                           int(observer_degrees), "stored_d65_10", "")
    if not sample.has_spectrum():
        raise ValueError(f"样本 {sample.display_name} 没有完整光谱")
    xyz, lab = reflectance_to_xyz_lab(sample.reflectance, illuminant, sample.wavelengths, observer_degrees)
    return ColorResult(display_illuminant(illuminant), xyz, lab,
                       int(observer_degrees), "spectrum", illuminant_note(illuminant))


def analyse_pair(
    standard: Sample,
    batch: Sample,
    illuminant: str,
    reference_illuminant: str = "D65",
    observer_degrees: int | str = 10,
) -> PairAnalysis:
    standard_test = color_result(standard, illuminant, observer_degrees)
    batch_test = color_result(batch, illuminant, observer_degrees)
    canonical_display = display_illuminant(illuminant)

    mi: float | None = None
    if display_illuminant(illuminant) != display_illuminant(reference_illuminant):
        standard_ref = color_result(standard, reference_illuminant, observer_degrees)
        batch_ref = color_result(batch, reference_illuminant, observer_degrees)
        mi = metamerism_index_multiplicative(
            standard_ref.xyz,
            batch_ref.xyz,
            standard_test.xyz,
            batch_test.xyz,
            illuminant,
            observer_degrees,
        )

    return PairAnalysis(
        illuminant=canonical_display,
        standard=standard_test,
        batch=batch_test,
        delta_e76=delta_e(standard_test.lab, batch_test.lab, "CIE 1976"),
        # Datacolor textile workflow uses the CIE94 textile parameter set.
        delta_e94=delta_e(standard_test.lab, batch_test.lab, "CIE 1994", textiles=True),
        delta_e00=delta_e(standard_test.lab, batch_test.lab, "CIE 2000"),
        cmc21=delta_e(standard_test.lab, batch_test.lab, "CMC", l=2, c=1),
        cmc11=delta_e(standard_test.lab, batch_test.lab, "CMC", l=1, c=1),
        metamerism_index=mi,
        observer_degrees=int(observer_degrees),
        reference_illuminant=display_illuminant(reference_illuminant),
    )


def _spectrum_on_union(sample: Sample, wavelengths: tuple[int, ...]) -> tuple[float, ...]:
    """Linearly interpolate one sample onto a common wavelength grid."""
    if not sample.has_spectrum():
        raise ValueError(f"样本 {sample.display_name} 没有完整光谱")
    if tuple(sample.wavelengths) == wavelengths:
        return tuple(float(x) for x in sample.reflectance)
    xs=[float(x) for x in sample.wavelengths]; ys=[float(x) for x in sample.reflectance]
    out=[]
    for w in wavelengths:
        x=float(w)
        if x <= xs[0]: out.append(ys[0]); continue
        if x >= xs[-1]: out.append(ys[-1]); continue
        j=1
        while j < len(xs) and xs[j] < x: j+=1
        x0,x1=xs[j-1],xs[j]; y0,y1=ys[j-1],ys[j]
        f=(x-x0)/(x1-x0) if x1!=x0 else 0.0
        out.append(y0+(y1-y0)*f)
    return tuple(out)


def spectral_rms_distance(a: Sample, b: Sample) -> float:
    """RMS difference in reflectance percentage points across the shared spectrum."""
    if not a.has_spectrum() or not b.has_spectrum():
        raise ValueError("光谱距离需要两条完整反射率曲线")
    lo=max(min(a.wavelengths),min(b.wavelengths)); hi=min(max(a.wavelengths),max(b.wavelengths))
    if lo >= hi:
        raise ValueError("两条光谱没有共同波长范围")
    # Use all 10-nm-ish sample points from both spectra in the overlap.
    grid=tuple(sorted({int(w) for w in (*a.wavelengths,*b.wavelengths) if lo<=w<=hi}))
    av=_spectrum_on_union(a,grid); bv=_spectrum_on_union(b,grid)
    return (sum((x-y)**2 for x,y in zip(av,bv))/len(grid))**0.5


def spectral_feature_summary(sample: Sample) -> dict[str, float]:
    if not sample.has_spectrum():
        return {"max_r": float('nan'), "max_nm": float('nan'), "min_r": float('nan'), "min_nm": float('nan'), "mean_r": float('nan')}
    vals=[float(x) for x in sample.reflectance]; waves=[float(x) for x in sample.wavelengths]
    imax=max(range(len(vals)),key=vals.__getitem__); imin=min(range(len(vals)),key=vals.__getitem__)
    return {"max_r":vals[imax],"max_nm":waves[imax],"min_r":vals[imin],"min_nm":waves[imin],"mean_r":sum(vals)/len(vals)}


def spectral_order(samples: Sequence[Sample], hybrid: bool=False) -> list[Sample]:
    """Order samples by the validated deterministic spectral/perceptual path.

    The ordering semantics stay the same as the historical implementation:
    start from the lowest hue-key sample and repeatedly choose the nearest
    remaining sample by spectral RMS, optionally plus ``0.18 * CIEDE2000``.

    Hotfix122 adds a *same-wavelength-grid* vector fast path.  Production QTX
    files such as Coloro/Adidas normally place every spectrum on one common
    360-700/10 nm grid.  In that case all reflectances are converted to one
    NumPy matrix once and each nearest-neighbour step evaluates the remaining
    RMS distances in a vector operation.  Mixed-grid/irregular data falls back
    to the historical interpolation code, preserving compatibility.
    """
    import math

    items=list(samples)
    spectral=[s for s in items if s.has_spectrum()]
    missing=[s for s in items if not s.has_spectrum()]
    if len(spectral) <= 1:
        return spectral + missing

    def hue_key(s: Sample):
        L,a,b=s.lab_d65_10; c=(a*a+b*b)**0.5; h=(math.degrees(math.atan2(b,a))%360.0)
        return (h, L, c, s.display_name.casefold())

    hue_keys=[hue_key(s) for s in spectral]
    index_by_object={id(s):i for i,s in enumerate(spectral)}

    # Fast path only when every spectrum already shares the exact wavelength
    # tuple.  This is deliberately conservative: irregular/mixed-grid imports
    # retain the byte-for-byte historical interpolation semantics below.
    try:
        import numpy as _np
        first_grid=tuple(spectral[0].wavelengths)
        same_grid=bool(first_grid) and all(
            tuple(s.wavelengths)==first_grid and len(s.reflectance)==len(first_grid)
            for s in spectral
        )
        if same_grid:
            matrix=_np.asarray([s.reflectance for s in spectral],dtype=float)
            if matrix.ndim==2 and matrix.shape==(len(spectral),len(first_grid)) and _np.isfinite(matrix).all():
                labs=_np.asarray([s.lab_d65_10 for s in spectral],dtype=float)
                de_matrix=None
                de_vector_fn=None
                if hybrid:
                    try:
                        import colour as _colour
                        # 1000-colour validation benefits from one matrix build.
                        # For very large palettes avoid ~N² memory and evaluate
                        # only the current row against remaining candidates.
                        if len(spectral) <= 1600:
                            de_matrix=_np.asarray(
                                _colour.delta_E(labs[:,None,:],labs[None,:,:],method='CIE 2000'),
                                dtype=float,
                            )
                            if de_matrix.shape != (len(spectral),len(spectral)):
                                de_matrix=None
                        if de_matrix is None:
                            def de_vector_fn(prev_i, rem_idx):
                                return _np.asarray(
                                    _colour.delta_E(labs[prev_i],labs[rem_idx],method='CIE 2000'),
                                    dtype=float,
                                ).reshape(-1)
                    except Exception:
                        de_matrix=None
                        de_vector_fn=None

                remaining=sorted(range(len(spectral)),key=lambda i:hue_keys[i])
                ordered_idx=[remaining.pop(0)]
                while remaining:
                    prev_i=ordered_idx[-1]
                    rem_idx=_np.asarray(remaining,dtype=_np.intp)
                    diff=matrix[rem_idx]-matrix[prev_i]
                    spec=_np.sqrt(_np.mean(diff*diff,axis=1))
                    if not hybrid:
                        # Keep the same deterministic secondary hue key used by
                        # the historical min(tuple) implementation.
                        best_pos=min(
                            range(len(remaining)),
                            key=lambda j:(float(spec[j]),hue_keys[remaining[j]])
                        )
                    else:
                        if de_matrix is not None:
                            de=de_matrix[prev_i,rem_idx]
                        elif de_vector_fn is not None:
                            try: de=de_vector_fn(prev_i,rem_idx)
                            except Exception: de=None
                        else:
                            de=None
                        if de is None or len(de)!=len(remaining):
                            de=_np.asarray([
                                float(delta_e(spectral[prev_i].lab_d65_10,spectral[i].lab_d65_10,'CIE 2000'))
                                for i in remaining
                            ],dtype=float)
                        score=spec + 0.18*de
                        best_pos=min(
                            range(len(remaining)),
                            key=lambda j:(float(score[j]),float(spec[j]),hue_keys[remaining[j]])
                        )
                    ordered_idx.append(remaining.pop(best_pos))
                return [spectral[i] for i in ordered_idx] + missing
    except Exception:
        # NumPy/colour availability must never change whether sorting works.
        pass

    # Historical mixed-grid / compatibility path.
    de_matrix=None
    if hybrid:
        try:
            import numpy as _np
            import colour as _colour
            labs=_np.asarray([s.lab_d65_10 for s in spectral],dtype=float)
            de_matrix=_np.asarray(
                _colour.delta_E(labs[:,None,:],labs[None,:,:],method='CIE 2000'),
                dtype=float,
            )
            if de_matrix.shape != (len(spectral),len(spectral)):
                de_matrix=None
        except Exception:
            de_matrix=None

    remaining=sorted(spectral,key=hue_key)
    ordered=[remaining.pop(0)]
    while remaining:
        prev=ordered[-1]
        prev_idx=index_by_object.get(id(prev),-1)
        def score(s: Sample):
            try: spec=spectral_rms_distance(prev,s)
            except Exception: spec=1e9
            if not hybrid:
                return (spec,hue_key(s))
            try:
                if de_matrix is not None:
                    de=float(de_matrix[prev_idx,index_by_object[id(s)]])
                else:
                    de=delta_e(prev.lab_d65_10,s.lab_d65_10,'CIE 2000')
            except Exception:
                de=0.0
            return (spec + 0.18*de, spec, hue_key(s))
        nxt=min(remaining,key=score); remaining.remove(nxt); ordered.append(nxt)

    return ordered + missing

