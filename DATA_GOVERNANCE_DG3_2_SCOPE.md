# Data Governance DG-3.2 · Hotfix62

## Scope

Hotfix62 is intentionally limited to the **Local Data Management** administration surface and audit-query/export helpers.
The main Studio interface is not redesigned or restyled.

### Local Data Management UX
- Responsive dialog sizing based on the available desktop area.
- Scrollable administration content for smaller notebook displays.
- Compact card layout for organization/installation, portable data, backup, private ownership and audit records.
- Existing DG-3.1 organization, installation, package migration, backup, privacy and ownership-transfer behavior is preserved.

### Audit filtering
Administrators can filter local audit records by:
- time window (all / today / 7 / 30 / 90 days)
- user
- action
- target/detail keyword

The table shows up to 1,000 matching records to keep the GUI responsive; Excel export can export the full filtered result.

### Audit Excel export
- Admin-only `导出 Excel…` action.
- Workbook contains `审计记录` and `导出说明` sheets.
- Export metadata includes organization, Installation ID, local instance label, export user, export time, filters and record count.
- The audit-export event itself is recorded as `EXPORT_AUDIT_EXCEL` after a successful export.

## Frozen scope
Hotfix62 does not modify:
- main Studio layout
- qtx_core colour science / QTX parser / MI / 555
- Quick3D / QML
- Excel import/export business formats
- DG-2 ACL semantics
- DG-3.1 privacy and portable-data ownership rules
