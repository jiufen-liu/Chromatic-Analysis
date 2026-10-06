from __future__ import annotations

import py_compile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
errors = []

for p in ROOT.rglob("*.py"):
    if any(part in {".venv", "venv", "__pycache__"} for part in p.parts):
        continue
    try:
        py_compile.compile(str(p), doraise=True)
    except Exception as exc:
        errors.append(f"compile {p.relative_to(ROOT)}: {exc}")

# Static contracts that do not require colour-science/PySide6 imports.
v23 = (ROOT / "qtx_core/palette_continuity_v23.py").read_text(encoding="utf-8")
audit = (ROOT / "tools/palette_continuity_v23_audit.py").read_text(encoding="utf-8")
utils = (ROOT / "tools/pac_batch_utils.py").read_text(encoding="utf-8")
diag = (ROOT / "tools/diagnostics_center.py").read_text(encoding="utf-8")
for token, where in [
    ('"break_threshold": de_soft', "V23 threshold metadata"),
    ('choose_batch_dir(OUT, "P23_1"', "short batch directory"),
    ('MANIFEST.csv', "manifest support"),
    ('choice == "19"', "PAC V24 diagnostics menu"),
]:
    hay = v23 if "threshold" in where else audit if where == "short batch directory" else utils if where == "manifest support" else diag
    if token not in hay:
        errors.append(f"missing {where}: {token}")

v24 = ROOT / "qtx_core/palette_continuity_v24.py"
if not v24.exists():
    errors.append("missing PAC V24 core")

if errors:
    print("HOTFIX94 VERIFY FAIL")
    for e in errors:
        print(" -", e)
    raise SystemExit(1)

print("HOTFIX94 VERIFY PASS")
print("Python syntax: PASS")
print("V23 adaptive threshold schema: PASS")
print("P23.1 short-path + manifest contract: PASS")
print("PAC V24 diagnostic candidate/menu: PASS")
print("Note: full QTX runtime validation still requires project dependencies (colour-science/PySide6).")
