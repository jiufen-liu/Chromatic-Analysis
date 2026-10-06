from pathlib import Path
p=Path(__file__).resolve().parents[1]/'qtx_app'/'main_window.py'
s=p.read_text(encoding='utf-8')
section=s[s.index('class AddSamplesDialog'):s.index('class WorkbenchTargetDialog')]
checks={
    'workbench_clicked_bool_guard': 'new_btn.clicked.connect(lambda _checked=False: self.create_workbench())' in s and 'if isinstance(name, bool):' in s,
    'picker_no_eager_full_library_hydration': 'load_index_samples_by_keys' not in section,
    'picker_lazy_sample_leaves': 'def _ensure_file_children' in section,
    'palette_image_not_single_giant_canvas': 'rows_all=max(1,int(math.ceil(len(slots)/cols)))' not in s,
    'palette_image_paged_output': 'image_pages=max(1,int(math.ceil(len(slots)/image_per_page)))' in s,
}
for name,ok in checks.items(): print(('PASS' if ok else 'FAIL'), name)
raise SystemExit(0 if all(checks.values()) else 1)
