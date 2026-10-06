from qtx_core.palette_family_sort import classify_color_family, sort_lab_rows


def test_known_neutral_and_navy_boundaries():
    assert classify_color_family(16.67, 0.1, -1.55) == 'Black'
    assert classify_color_family(75.19, 0.27, -2.69) == 'Grey'
    assert classify_color_family(79.60, 4.44, -1.87) == 'Grey'
    assert classify_color_family(20.37, -0.20, -7.56) == 'Navy / Deep Blue'
    assert classify_color_family(22.30, 4.58, -16.57) == 'Purple'


def test_basic_hue_families():
    assert classify_color_family(50, 35, 5) == 'Red'
    assert classify_color_family(75, 30, -2) == 'Pink'
    assert classify_color_family(55, -25, 10) == 'Green'
    assert classify_color_family(55, -5, -35) == 'Blue'


def test_deterministic_family_then_dark_to_light():
    rows=[
        ('b','light blue',(70,-5,-30)),
        ('a','dark blue',(35,-5,-20)),
        ('g','grey',(55,1,-1)),
        ('k','black',(15,0,-1)),
    ]
    one=sort_lab_rows(rows)
    two=sort_lab_rows(rows)
    assert [x.key for x in one] == [x.key for x in two]
    assert [x.family for x in one] == ['Black','Grey','Navy / Deep Blue','Blue']
