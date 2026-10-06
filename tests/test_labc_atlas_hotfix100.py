"""Hotfix100 LABC Atlas opponent-slice geometry regression tests."""
import importlib.util
import pathlib
import sys

MODULE = pathlib.Path(__file__).resolve().parents[1] / "qtx_core" / "labc_atlas.py"
spec = importlib.util.spec_from_file_location("labc_atlas_hf100", MODULE)
atlas = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atlas
spec.loader.exec_module(atlas)


def test_opposites_are_deterministic():
    assert atlas.opposite_family_key("blue") == "yellow"
    assert atlas.opposite_family_key("red") == "green"
    assert atlas.opposite_family_key("neutral") == "neutral"


def test_blue_yellow_slice_keeps_neutral_in_center():
    records = [
        {"key":"b","name":"blue","lab":(50.0,0.0,-40.0)},
        {"key":"y","name":"yellow","lab":(50.0,0.0,40.0)},
        {"key":"n","name":"neutral","lab":(50.0,1.0,1.0)},
        {"key":"r","name":"red","lab":(50.0,40.0,0.0)},
    ]
    cells = atlas.build_opponent_cells(records, "blue")
    visible = {r["key"] for recs in cells.values() for r in recs}
    assert visible == {"b","y","n"}
    _, blue_col = atlas.opponent_cell_for_lab(records[0]["lab"], "blue")
    _, neutral_col = atlas.opponent_cell_for_lab(records[2]["lab"], "blue")
    _, yellow_col = atlas.opponent_cell_for_lab(records[1]["lab"], "blue")
    assert blue_col < neutral_col < yellow_col
    assert atlas.SIGNED_C_CENTERS[neutral_col] == 0.0


def test_slice_does_not_reclassify_samples():
    navy=(20.0,2.0,-12.0)
    black=(12.0,1.0,1.0)
    assert atlas.family_for_lab(navy) == "blue"
    assert atlas.family_for_lab(black) == "neutral"
    # Display slice membership must not alter underlying family identity.
    atlas.opponent_cell_for_lab(navy,"blue")
    atlas.opponent_cell_for_lab(black,"blue")
    assert atlas.family_for_lab(navy) == "blue"
    assert atlas.family_for_lab(black) == "neutral"
