from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('palette_labch_sort_hf106', ROOT / 'qtx_core' / 'palette_labch_sort.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
professional_hue_sort_key = MOD.professional_hue_sort_key
hue_family_name = MOD.hue_family_name


def test_blue_family_no_five_degree_lightness_reset():
    rows = [
        ('light-241', (64.3, -10.9, -20.2), 0),
        ('dark-251',  (22.9, -1.5, -4.2), 1),
        ('light-255', (57.9, -4.6, -17.7), 2),
        ('deep-259',  (27.4, -2.1, -11.3), 3),
    ]
    ordered = sorted(rows, key=lambda x: professional_hue_sort_key(x[1], x[2]))
    Ls = [x[1][0] for x in ordered]
    assert Ls == sorted(Ls, reverse=True)


def test_broad_hue_family_is_primary_before_lightness():
    red = (30.0, 25.0, 3.0)
    blue = (90.0, -4.0, -25.0)
    assert professional_hue_sort_key(red, 0) < professional_hue_sort_key(blue, 1)


def test_neutral_is_one_coherent_light_to_dark_block():
    labs = [(80.0,1.0,-1.0),(20.0,0.5,-1.0),(55.0,-1.0,1.0)]
    ordered = sorted(enumerate(labs), key=lambda x: professional_hue_sort_key(x[1], x[0]))
    assert [x[1][0] for x in ordered] == [80.0,55.0,20.0]


def test_family_labels_follow_atlas_boundaries():
    assert hue_family_name(0.0) == 'red'
    assert hue_family_name(60.0) == 'orange'
    assert hue_family_name(90.0) == 'yellow'
    assert hue_family_name(180.0) == 'green'
    assert hue_family_name(220.0) == 'cyan'
    assert hue_family_name(260.0) == 'blue'
    assert hue_family_name(300.0) == 'blue_violet'
