from __future__ import annotations

import importlib.util
import sys
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('hf110_labch', ROOT / 'qtx_core' / 'palette_labch_sort.py')
labch = importlib.util.module_from_spec(spec); sys.modules[spec.name]=labch; spec.loader.exec_module(labch)

spec2 = importlib.util.spec_from_file_location('hf110_global', ROOT / 'qtx_core' / 'palette_global_delta.py')
global_path = importlib.util.module_from_spec(spec2); sys.modules[spec2.name]=global_path; spec2.loader.exec_module(global_path)


def de76(a, b):
    return math.sqrt(sum((float(x)-float(y))**2 for x, y in zip(a, b)))


def test_red_wrap_359_and_1_stay_adjacent():
    hues = [359.0, 1.0, 5.0, 120.0, 130.0]
    cut = labch.circular_hue_cut(hues)
    order = sorted(hues, key=lambda h: labch.circular_hue_position(h, cut))
    assert abs(order.index(359.0) - order.index(1.0)) == 1


def test_cut_is_inside_largest_empty_arc():
    hues = [350.0, 355.0, 2.0, 8.0, 90.0]
    cut = labch.circular_hue_cut(hues)
    # Largest empty arc is 90 -> 350, midpoint 220.
    assert abs(cut - 220.0) < 1e-9


def test_global_path_never_worse_than_its_selected_initial_candidate():
    labs = [
        (5, 0, 0), (12, 1, 0), (18, 2, 0), (28, 6, 2),
        (40, 8, 4), (52, 2, 9), (60, -8, 3), (44, -11, -3),
        (30, -4, -10), (18, 1, -8),
    ]
    result = global_path.optimise_global_open_path(
        labs, de76, max_starts=8, optimise_candidates=3, two_opt_passes=12
    )
    assert result.final_metrics.score() <= result.initial_metrics.score()
    assert sorted(result.order) == list(range(len(labs)))


def test_same_input_is_deterministic():
    labs = [(0,0,0),(8,1,0),(15,3,2),(25,8,5),(40,0,12),(35,-7,5),(20,-8,-5),(10,0,-8)]
    a = global_path.optimise_global_open_path(labs, de76, max_starts=8, two_opt_passes=10)
    b = global_path.optimise_global_open_path(labs, de76, max_starts=8, two_opt_passes=10)
    assert a.order == b.order
    assert a.final_metrics.score() == b.final_metrics.score()
