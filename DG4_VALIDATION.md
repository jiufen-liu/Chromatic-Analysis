# DG4 Validation

Validation performed for HF139:

- Full Python `compileall`: PASS.
- AST compatibility check against HF138: no pre-existing top-level function/class removed from `main_window.py`, `library_store.py`, `auth_store.py`, `data_protection.py`, or `data_management.py`; no pre-existing class method removed.
- Simulated DG3 -> DG4 migration with isolated fake Windows-style QStandardPaths:
  - legacy auth DB preserved;
  - legacy colour DB preserved;
  - security DB copied;
  - audit history separated and security-copy audit table removed after verified transfer;
  - formal QTX mirrors copied;
  - official QTX mirrors moved to system-managed domain;
  - personal work files copied to standardized user workspace;
  - colour-card/workbench readable mirrors generated.
- LibraryStore domain test with dependency stubs:
  - colour-card save/remove mirror: PASS;
  - workbench save/remove mirror: PASS;
  - formal QTX mirror routing: PASS;
  - official QTX mirror routing: PASS.
- Backup/package simulation:
  - full backup contains security/colour/audit DBs: PASS;
  - colour-only/security-only/audit archive: PASS;
  - official resources included in full/colour/package payloads: PASS;
  - `.cadata` v2 manifest and payload creation: PASS.
- ZIP integrity check: performed after packaging.

Environment limitation: this build environment does not have PySide6, so final Windows GUI/runtime behavior must still be confirmed on the user's Windows/PySide6 machine. Static compilation and storage-layer simulations do not substitute for that GUI test.
