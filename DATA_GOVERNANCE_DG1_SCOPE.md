# Hotfix55 · Data Governance DG-1

Scope: audit + schema foundation only.

Changed:
- Added non-enforcing `data_scope_grants` ACL table/API to `AuthStore`.
- Added common data-governance classification helpers.
- Added PyCharm-runnable `tools/data_governance_audit.py`.
- Added current-state audit documentation.

Intentionally unchanged:
- Existing library visibility and business behavior.
- QTX/CPX/Excel parsing/export behavior.
- Colorimetry, CMC, CIEDE2000, MI, 555.
- 2D/3D rendering.
- Formal-library administrator save/move/delete rules.
- P3 performance paths.

DG-2 will activate customer-level visibility after an administrator assignment UI exists.
