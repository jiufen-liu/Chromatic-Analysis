# Hotfix139 · DG4 Data Storage & Identity Separation

## Goal

Implement DG4 without changing the HF138 MDI/window shell or colour-science/business algorithms.
The storage model is split into four domains:

1. **Colour business data** — formal libraries, colour-card readable mirrors, personal work files.
2. **Identity & permissions** — users, password hashes, roles, feature permissions, data-scope grants.
3. **Audit** — independent audit database and archives.
4. **Runtime/system resources** — official-library mirrors, performance/cache/log files.

## Windows data layout

### Human-readable business data
Default location:

`Documents\Chromatic Analysis Data`

Structure:

- `01 正式色库` — formal/customer QTX mirrors.
- `02 色卡编排` — `.chromaticcard.json` readable mirrors, grouped by logical user/customer.
- `03 个人工作区` — workbench readable mirrors and `.chromatic` personal work files.
- `04 数据交换` — import/export classification folders.
- `90 业务备份` — colour-domain backups.

The business root can be migrated from **数据管理中心**. Migration copies and verifies files, switches the configured root, keeps the old directory for rollback, rebases QTX mirror paths, then requires restart.

### Program-managed system data
Preferred location on Windows:

`%PROGRAMDATA%\Chromatic Analysis`

If the current account cannot write there, the program automatically falls back to its per-user application-data area. The actual path is always shown in **数据管理中心**.

Structure:

- `Security\security.sqlite3` — users / credentials / roles / permissions / data grants.
- `Database\color_data.sqlite3` — colour/business authoritative SQLite database.
- `Audit\audit.sqlite3` — audit history only.
- `OfficialLibraries` — official-library QTX mirrors; kept out of the user-facing formal-library directory.
- `Runtime` — performance cache/log/runtime data.
- `Backup\Security` — identity/permission backups.
- `Backup\Audit` — audit archives.
- `Backup\System` — full-system safety backups.

## Automatic migration from HF138 / DG3

On first HF139 startup:

- `chromatic_auth.sqlite3` is copied to `Security\security.sqlite3`.
- Embedded legacy `audit_log` rows are copied to `Audit\audit.sqlite3`; the audit table is removed only from the new DG4 security copy after verification.
- `chromatic_library.sqlite3` is copied to `Database\color_data.sqlite3`.
- Existing managed QTX mirrors are copied into the new readable formal-library tree.
- Official-library mirrors are copied into `OfficialLibraries`.
- Existing managed personal work files are copied into `03 个人工作区`.
- Existing colour cards and workbenches are converted to readable JSON mirrors without hydrating the whole spectral library.
- Legacy source databases/files are **not deleted**. They remain a rollback source.

## Readable mirrors

SQLite remains authoritative for normal operation and performance.

- Saving a colour-card plan also updates a `.chromaticcard.json` mirror.
- Saving a workbench also updates a `.chromaticworkbench.json` mirror.
- Deleting a plan/workbench removes its generated mirror.
- **重建可读镜像** can explicitly rebuild QTX, colour-card and workbench mirrors from SQLite.

This does not change QTX/CPX/Excel parsing, colour matching, sorting, spectra, 3D, 555, tolerances or RBAC enforcement.

## Backup isolation

Data Management now provides:

- **完整系统备份** — security + colour + audit + readable business/system mirrors.
- **仅备份色彩数据** — colour database + formal/card/personal/official mirrors, no accounts or permissions.
- **仅备份用户与权限** — security database only, no colour data.
- **归档审计** — audit database snapshot only.
- **恢复色彩数据** — does not modify users/roles/permissions/audit.
- **恢复用户与权限** — does not modify colour data/audit.
- **完整恢复** — full-domain restore.

Legacy DG3 backup folders and v1 `.cadata` packages remain import-compatible.

## Portable package

`.cadata` package format is upgraded to v2 and contains separated security, colour and audit databases plus readable business/system resource mirrors. v1 packages are still accepted; their embedded legacy audit history is migrated on the next launch.

## Data integrity

**检查数据完整性** runs SQLite `PRAGMA quick_check` independently on security, colour and audit DBs and reports readable mirror counts. It also detects colour-card/workbench owner IDs that no longer correspond to a current user.

## HF138 stability freeze

HF138 MDI/window shell is intentionally frozen. `main_window.py` changes in HF139 are limited to:

- passing the signed-in username into the data store for readable mirror naming;
- moving Munsell cache into the runtime data domain;
- renaming the admin menu item to “数据管理中心”.

No MDI geometry, title-bar, taskbar, minimization, responsive-window or layout-shell logic is changed.
