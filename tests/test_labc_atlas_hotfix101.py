"""Hotfix101 LABC Atlas validation-mode regression tests."""
import importlib.util
import pathlib
import sys

MODULE = pathlib.Path(__file__).resolve().parents[1] / "qtx_core" / "labc_atlas.py"
spec = importlib.util.spec_from_file_location("labc_atlas_hf101", MODULE)
atlas = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atlas
spec.loader.exec_module(atlas)


def test_validation_report_has_expected_pages():
    records = [
        {"key":"n","name":"grey","lab":(60.0,1.0,1.0)},
        {"key":"b","name":"blue","lab":(40.0,0.0,-40.0)},
        {"key":"g","name":"green","lab":(50.0,-35.0,10.0)},
        {"key":"y","name":"yellow","lab":(70.0,0.0,50.0)},
        {"key":"r","name":"red","lab":(50.0,45.0,5.0)},
        {"key":"v","name":"violet","lab":(45.0,25.0,-20.0)},
    ]
    report = atlas.build_validation_report(records)
    assert report["sample_count"] == 6
    assert len(report["sections"]) == 8
    labels = [x["label"] for x in report["sections"]]
    assert "Neutral" in labels
    assert any("Blue" in x and "Yellow" in x for x in labels)
    assert report["overall"] in {"PASS","REVIEW"}


def test_family_page_never_contains_wrong_family():
    records = [
        {"key":"b","name":"blue","lab":(40.0,0.0,-40.0)},
        {"key":"r","name":"red","lab":(40.0,40.0,0.0)},
    ]
    result = atlas.validate_family_page(records, "blue")
    assert result["sample_count"] == 1
    assert result["wrong_family_count"] == 0
    assert result["wrong_cell_count"] == 0


def test_boundary_colour_is_review_not_fail():
    # h° close to Blue family upper boundary (285°) but still inside Blue.
    import math
    C = 20.0
    h = math.radians(284.0)
    lab = (50.0, C*math.cos(h), C*math.sin(h))
    records = [{"key":"edge","name":"edge blue","lab":lab}]
    result = atlas.validate_family_page(records, "blue")
    assert result["status"] == "REVIEW"
    assert result["risk_count"] == 1
    assert result["wrong_family_count"] == 0


def test_opponent_neutral_stays_centered():
    records = [
        {"key":"n","name":"grey","lab":(50.0,1.0,1.0)},
        {"key":"b","name":"blue","lab":(50.0,0.0,-40.0)},
        {"key":"y","name":"yellow","lab":(50.0,0.0,40.0)},
    ]
    result = atlas.validate_opponent_page(records, "blue")
    assert result["neutral_not_centered_count"] == 0
    assert result["invalid_count"] == 0
