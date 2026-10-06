"""Dependency-free guard for the studio taskbar/data-browser UI update."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


ROOT=Path(__file__).resolve().parent
PROTECTED={
    'qtx_core/__init__.py':'573b5a35680e344fb70055b72fdb0cf3c31b2539ffcb62d7f5c04368190a7e4c',
    'qtx_core/analysis.py':'b7666543645c0f4f5a97f9cece60556a3e1ab3b095166acdf764805b8f35f16f',
    'qtx_core/colorimetry.py':'62dc09af1807315717ddbfafaf98976ff06e07883ec64073f3fd6a528781380d',
    'qtx_core/cpx_io.py':'e649151df5c7eb963102a771f378422f02c7d1988c040fabd943fb4d8c8fb4e0',
    'qtx_core/metamerism.py':'fab72879eaaeeb951288a0a20a2417b87a3a14b6daec69ecd014094a70de7a75',
    'qtx_core/models.py':'ca75a90acd2741953dde0c1e12e3f7d66135ec0dccb84badca76d69c510243fb',
    'qtx_core/qtx_parser.py':'ee2633680f832d3f32fd8ae0747308ec9668e25e40de2917543a572a87b47bb8',
    'qtx_core/shade_sort.py':'35691a8c53c18f93c8173401441ed23b7c78caa8dcc7d5c9162590132597845c',
    'qtx_app/auth_store.py':'7f951fec12a09497948e32f8a979a0581f39a39b656a4fd1b4137d2f80ec3cf1',
    'qtx_app/library_store.py':'0261ed784ad22753ab61c2cab91a8c6c1213e072935237e82b4454d4da7bb4f9',
    'qtx_app/excel_exchange.py':'2bf13cdc4a896ed459c08432bda1b865cb8ec549c8a77eddff819f76170bfdc1',
}
REQUIRED={
    'MainWindow':{'new_workspace_window','open_tool_window','create_workbench','drop_sample_keys_to_workbench','require_admin','open_lab2d_window','open_lab3d_window','arrange_primary_tool_windows','_library_grid_metrics','_library_effective_page_size','add_workspace_document','_minimize_to_studio_taskbar','_restore_from_studio_taskbar','save_dropped_samples_to_formal_library'},
    'SampleDetailsDialog':{'_build_colour_tab','_build_measurement_tab','_build_spectrum_tab','_build_attributes_tab','save_attributes','open_2d','open_3d'},
    'ColorTile':{'mousePressEvent','mouseMoveEvent','mouseDoubleClickEvent','set_card_width'},
    'WorkbenchTableView':{'startDrag','dragEnterEvent','dragMoveEvent','dropEvent'},
    'WorkspaceCardList':{'startDrag','dragEnterEvent','dragMoveEvent','dropEvent'},
    'WorkspaceDocument':{'receive_sample_keys','open_2d','open_3d','show_details'},
    'StudioToolSubWindow':{'changeEvent','_minimize_to_taskbar','_hide_minimized'},
}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    failures=[f'protected file changed: {p}' for p,h in PROTECTED.items() if digest(ROOT/p)!=h]
    tree=ast.parse((ROOT/'qtx_app/main_window.py').read_text(encoding='utf-8'))
    classes={n.name:{x.name for x in n.body if isinstance(x,ast.FunctionDef)} for n in tree.body if isinstance(n,ast.ClassDef)}
    for cls,methods in REQUIRED.items():
        missing=methods-classes.get(cls,set())
        if missing:failures.append(f'{cls} missing: '+', '.join(sorted(missing)))
    if failures:
        print('SCOPED UPDATE GUARD FAILED'); [print('-',x) for x in failures]; return 1
    print('SCOPED UPDATE GUARD PASSED'); print('protected files:',len(PROTECTED)); return 0


if __name__=='__main__':raise SystemExit(main())
