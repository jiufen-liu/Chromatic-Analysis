from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'qtx_app' / 'main_window.py').read_text(encoding='utf-8')


def test_labch_group_contains_all_five_coordinates():
    assert "('L* 明度','L')" in SRC
    assert "('a* 红绿轴','a')" in SRC
    assert "('b* 黄蓝轴','b')" in SRC
    assert "('C* 彩度','C')" in SRC
    assert "('h° 色相（环形排序）','h')" in SRC
    assert "mode not in {'L','a','b','C','h'}" in SRC


def test_reference_difference_is_separate_command():
    assert "基准色差" in SRC
    assert 'def sort_by_color_difference' in SRC
    assert '_selected_sort_reference' in SRC
    assert 'active_formula' in SRC


def test_running_delta_uses_global_optimizer_and_background_worker():
    assert "连续色差" in SRC
    assert 'def sort_by_running_delta' in SRC
    assert 'optimise_global_open_path' in SRC
    assert '多起点 + 2-opt' in SRC
    assert 'self.main._card_sort_executor.submit(worker)' in SRC


def test_colour_space_atlas_remains_independent_view():
    assert 'def open_labc_atlas' in SRC
    assert 'active_win.open_labc_atlas()' in SRC
