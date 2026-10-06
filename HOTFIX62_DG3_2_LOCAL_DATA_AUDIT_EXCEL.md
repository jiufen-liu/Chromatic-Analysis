# Hotfix62 · DG-3.2 · Local Data Console / Audit Excel

This hotfix follows Hotfix61 and implements the approved local-data-management UI refinement without changing the main application workspace.

Key additions:
1. Responsive local-data-management window suitable for smaller notebook screens.
2. Audit filters for period, user, action and keyword.
3. Administrator audit export to Excel using the current filter.
4. Audit export creates two sheets: audit records and export metadata.
5. Successful audit export is itself appended to the audit log.
