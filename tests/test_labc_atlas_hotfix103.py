"""Hotfix103 exact hue-wheel navigation regression tests."""
import importlib.util
import pathlib
import sys
import math

MODULE = pathlib.Path(__file__).resolve().parents[1] / "qtx_core" / "labc_atlas.py"
spec = importlib.util.spec_from_file_location("labc_atlas_hf103", MODULE)
atlas = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atlas
spec.loader.exec_module(atlas)


def lab_at_hue(L, C, h):
    r = math.radians(h)
    return (L, C*math.cos(r), C*math.sin(r))


def test_arbitrary_hue_angle_family_mapping():
    assert atlas.family_key_for_hue_angle(262.5) == "blue"
    assert atlas.family_key_for_hue_angle(82.5) == "yellow"
    assert atlas.opposite_hue_angle(262.5) == 82.5


def test_exact_hue_angle_changes_slice_membership():
    records = [
        {"key":"b255", "name":"blue255", "lab":lab_at_hue(50, 40, 255)},
        {"key":"b280", "name":"blue280", "lab":lab_at_hue(50, 40, 280)},
        {"key":"y75", "name":"yellow75", "lab":lab_at_hue(50, 40, 75)},
        {"key":"n", "name":"neutral", "lab":(50,1,1)},
    ]
    cells = atlas.build_opponent_cells(records, "blue", tolerance_deg=7.5, hue_angle=257.5)
    visible = {r["key"] for rr in cells.values() for r in rr}
    assert "b255" in visible
    assert "b280" not in visible
    assert "n" in visible


def test_old_family_api_remains_compatible():
    blue = {"key":"b", "name":"blue", "lab":(50,0,-40)}
    yellow = {"key":"y", "name":"yellow", "lab":(50,0,40)}
    cells = atlas.build_opponent_cells([blue,yellow], "blue")
    visible = {r["key"] for rr in cells.values() for r in rr}
    assert visible == {"b","y"}
