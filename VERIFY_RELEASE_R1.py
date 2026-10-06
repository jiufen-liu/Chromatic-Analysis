from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED_VERSION = 'v0.14.6.4 Hotfix66 Release Readiness R1 Palette Direct Drop'
EXPECTED_BUILD = 'HF64-RR1-UI-STABILITY-20260927'


def fail(msg: str) -> None:
    raise SystemExit('[FAIL] ' + msg)

version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
if version != EXPECTED_VERSION:
    fail(f'VERSION mismatch: {version!r}')

build_tree = ast.parse((ROOT / 'qtx_app' / 'build_info.py').read_text(encoding='utf-8'))
values = {}
for node in build_tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        try:
            values[node.targets[0].id] = ast.literal_eval(node.value)
        except Exception:
            pass
if values.get('BUILD_ID') != EXPECTED_BUILD:
    fail(f'BUILD_ID mismatch: {values.get("BUILD_ID")!r}')
if values.get('PRODUCT_VERSION') != '0.14.6.4':
    fail(f'PRODUCT_VERSION mismatch: {values.get("PRODUCT_VERSION")!r}')

launcher = (ROOT / 'RUN_CHROMATIC_ANALYSIS.bat').read_text(encoding='utf-8')
if 'START_HF37.bat' in launcher:
    fail('launcher still points to removed START_HF37.bat')
if 'main.py' not in launcher:
    fail('launcher does not start main.py')

required = [
    ROOT / 'qtx_app' / 'qml' / 'Lab3DView.qml',
    ROOT / 'packaging' / 'ChromaticAnalysis.spec',
    ROOT / 'requirements-build.txt',
]
for path in required:
    if not path.exists():
        fail(f'missing release asset: {path.relative_to(ROOT)}')

print('[PASS] Release Readiness R1 static checks passed.')
