from qtx_core.colorimetry import clear_science_cache, reflectance_to_xyz_lab, science_cache_info


def test_spectral_compute_cache_is_result_transparent():
    waves = tuple(range(360, 701, 10))
    refl = tuple(20.0 + (i % 7) * 1.25 for i in range(len(waves)))
    clear_science_cache()
    first = reflectance_to_xyz_lab(refl, "D50", waves, 10)
    mid = science_cache_info()
    second = reflectance_to_xyz_lab(refl, "D50", waves, 10)
    final = science_cache_info()
    assert first == second
    assert mid["misses"] >= 1
    assert final["hits"] >= mid["hits"] + 1
