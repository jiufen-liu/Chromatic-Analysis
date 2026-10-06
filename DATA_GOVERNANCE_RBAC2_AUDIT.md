# Data Governance / RBAC 2.0 · DG-1 Audit

## Current state

- Authentication is local/offline SQLite (`chromatic_auth.sqlite3`) with PBKDF2-SHA256 passwords.
- The current roles are `admin` and `operator`.
- Feature permissions exist (`library_view`, `find`, `compare`, `cards`, `spectrum`, `export`, `workfile`).
- Operators retain all feature permissions by default unless the administrator changes them.
- Formal-library destructive operations (save/move/delete/customer maintenance) already call `require_admin()`.
- Library visibility has no data-scope ACL today: an operator with `library_view` can browse all official/formal customers.
- Official and formal data share `chromatic_library.sqlite3`; official data is identified by the `官方色库/...` customer prefix.
- Customer QTX mirrors are written to `Documents/Chromatic Analysis Data/Customers`, outside application RBAC.
- Color-card plans and comparison workbenches are global records; they have no owner user id.
- Personal `.chromatic` files are user-selected filesystem paths rather than a managed per-user private store.
- Favorites/navigation are already separated with `navigation/user_<user_id>/...` and can remain.
- Existing audit log covers login/user administration and major formal-library changes, but not all read/copy/export operations.

## Main gaps

1. Feature permission != data permission. There is no answer to “which customer may this operator see?”.
2. Local mirrored QTX files can bypass application visibility rules when the Windows account can browse Documents.
3. Workbenches/cards are shared across application users.
4. Export permission does not yet uniformly cover clipboard/copy/drag/data extraction paths.
5. Audit records are incomplete for data governance.

## DG-1 foundation added in Hotfix55

A non-enforcing `data_scope_grants` table is created in the auth database:

- `user_id`
- `scope_type`: `official`, `formal`, `customer`
- `scope_key`
- `can_view`
- `can_export`
- grant metadata/timestamps

Hotfix55 does **not** filter existing data yet. This prevents a migration from accidentally hiding existing libraries before the administrator configures grants.

`qtx_app/data_governance.py` defines the common data-class vocabulary.

`tools/data_governance_audit.py` reports the actual Windows paths, users, feature grants and current data counts.

## DG-2 enforcement target

- Administrator: all data, all management operations.
- Operator:
  - official library: view-only by policy;
  - formal library: only explicitly granted customer trees;
  - no formal-library save/move/delete/overwrite;
  - export/copy controlled separately.
- Personal workspace/card/workbench: owner-only by default, with future sharing as an explicit action.

Existing data will be migrated conservatively: nothing is deleted, and pre-DG records remain recoverable by administrators.

## DG-2 status · Hotfix56

Customer-level application ACL enforcement is now active after an administrator saves a policy. Library navigation, SQL paging/search, lightweight indexes, key hydration, palette sources and favorites all share the same `LibraryStore` access context. Export is additionally checked against per-customer `can_export` grants. Existing pre-DG accounts remain in compatibility mode until explicitly configured; new operator accounts start official-only.

Remaining DG-3 work is local-file/ownership governance: protected customer mirror storage, per-user card/workbench ownership, personal workspace roots, backup/restore and expanded audit events.
