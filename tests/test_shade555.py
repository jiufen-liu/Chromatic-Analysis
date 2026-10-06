from qtx_core.shade_sort import shade_555


RANGES_LAB = {'L': (-1.80, 1.80), 'a': (-0.90, 0.90), 'b': (-0.90, 0.90)}


def test_standard_is_555_with_datacolor_symmetric_ranges():
    r = shade_555((50, 10, 5), (50, 10, 5), RANGES_LAB, mode='LAB', blocks=9)
    assert r.code == '555'


def test_datacolor_manual_box_widths_for_9_boxes():
    standard = (50.0, 0.0, 0.0)
    # Manual example: L span 3.60 => 0.40 per box; a/b span 1.80 => 0.20.
    assert shade_555(standard, (50.19, 0.09, 0.09), RANGES_LAB, mode='LAB', blocks=9).code == '555'
    assert shade_555(standard, (50.20, 0.10, 0.10), RANGES_LAB, mode='LAB', blocks=9).code == '666'
    assert shade_555(standard, (49.80, -0.10, -0.10), RANGES_LAB, mode='LAB', blocks=9).code == '444'


def test_supported_box_counts_keep_standard_in_middle_when_symmetric():
    standard = (50.0, 20.0, 5.0)
    expected = {3: '222', 5: '333', 7: '444', 9: '555'}
    for boxes, code in expected.items():
        assert shade_555(standard, standard, RANGES_LAB, mode='LAB', blocks=boxes).code == code


def test_outside_configured_range_is_not_sortable():
    standard = (50.0, 0.0, 0.0)
    result = shade_555(standard, (51.81, 0.0, 0.0), RANGES_LAB, mode='LAB', blocks=9)
    assert result.code == '超范围'
    assert not result.in_range


def test_lch_mode_uses_explicit_low_high_ranges():
    standard = (50.0, 20.0, 0.0)
    ranges = {'L': (-1.8, 1.8), 'C': (-1.8, 1.8), 'H': (-1.8, 1.8)}
    assert shade_555(standard, standard, ranges, mode='LCH', blocks=9).code == '555'
