# Hotfix56 · Data Governance DG-2

## Goal

Activate real customer-level data-scope enforcement for the offline/single-PC application without changing color science, QTX parsing, P3 performance, 3D, or administrator library-management behavior.

## Added

- `用户与权限 → 数据范围…` administrator UI.
- Official-library visibility/export toggles.
- Formal-library customer/category grants with separate `可查看` and `可导出` flags.
- Secure default for newly created operator accounts: official libraries visible, formal/customer libraries hidden until explicitly granted.
- Conservative migration for existing operator accounts: if no DG grants exist, the account remains in compatibility mode (existing full visibility) until an administrator saves a data-scope policy.
- LibraryStore SQL-level access context. Paging, search, index pickers, key hydration, customer counts, favorites and card-source reads all use the same ACL filter.
- Per-customer export checks for detail export, workspace export, palette export, comparison-workbench export and find-result export.
- Legacy embedded palette/workbench samples are filtered in memory so a pre-DG cached sample cannot bypass the current customer's visibility policy.
- Audit records for data-scope policy changes.

## Permission semantics

- Administrator: unrestricted view/export/manage.
- Operator feature permission still answers **what modules can be used**.
- Data scope answers **which official/formal customer data can be seen/exported**.
- A granted customer path also grants its descendants, e.g. `客户A` includes `客户A/2026`.
- If only one child is required, grant that child directly rather than its parent.

## Migration behavior

Existing operators with zero `data_scope_grants` are deliberately not locked out during upgrade. Their first saved `数据范围` policy activates enforcement. New operator accounts start in the safer official-only state.

## Still intentionally deferred to DG-3

- Move customer mirror QTX files out of the user-browsable Documents tree / harden local storage against direct Explorer access.
- Per-user ownership/isolation for color-card plans and comparison workbenches.
- Managed per-user private workspace root and backup/restore policy.
- Full read/copy/clipboard/drag audit trail.

These are filesystem/ownership concerns and are intentionally separated from DG-2 query ACL enforcement.
