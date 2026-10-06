# Data Governance DG-3.1 Scope — Hotfix61

## Goal
Turn the standalone build into a single-organisation deployable product instead of a developer-owned local database.

## Implemented
1. **Organisation identity**
   - First clean launch asks the customer for organisation/company name and first administrator.
   - Existing installations are migrated to a local organisation profile and can rename it from Local Data Management.
   - No hidden developer administrator is created.
2. **Organisation recovery key**
   - Clean first launch generates a recovery key shown once to the customer administrator.
   - The key is stored only as a PBKDF2 hash; the application/developer cannot recover the plaintext key later.
   - Login screen exposes explicit "管理员恢复…" to reset an administrator password with the recovery key.
   - Logged-in administrators can rotate the recovery key; the old key becomes invalid.
3. **Installation identity**
   - Each Windows installation receives a persistent UUID (`installation.json`) plus a human-readable machine label.
   - Importing/copying code does not clone this identity.
4. **Portable `.cadata` package**
   - Exports authentication/ACL DB, library DB, application-managed library mirrors and managed personal workfiles.
   - Import creates DB backup + full pre-import safety package, verifies SHA-256 manifest, replaces managed data and rebuilds mirror paths for the target PC.
   - The target Installation ID is preserved.
   - Current package is deliberately marked **not encrypted** and UI warns it is sensitive business data.
5. **Private work data boundary**
   - Administrators no longer automatically list/open other users' private workbenches or color-card schemes.
   - Admin management UI can see only per-user counts.
   - Ownership transfer changes metadata and moves managed personal files without opening private payloads.
   - Legacy owner `0` remains shared temporarily for backward compatibility and will be handled by the historical-data migration phase.
6. **Hotfix59/DG-2.2 behaviour retained**
   - Unified permission center, ACL query filtering, find-source multiselect and library-selection-to-find flow remain unchanged.

## Security boundary
This remains an offline single-PC application. App RBAC protects actions inside Chromatic Analysis. A Windows user with unrestricted filesystem/SQLite access can still inspect local files. Portable `.cadata` is not encrypted in Hotfix61; treat it as sensitive data. Server/API isolation and encrypted transfer are future stages.
