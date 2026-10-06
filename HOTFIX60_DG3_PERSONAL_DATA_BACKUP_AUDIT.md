# Hotfix60 · Data Governance DG-3

## Scope

1. Workbench and color-card ownership
   - `workbenches.owner_user_id`
   - `color_cards.owner_user_id`
   - New records are owned by the signed-in user.
   - Operators see their own records plus legacy shared (`owner_user_id=0`) records for migration compatibility.
   - Administrators can inspect all records.

2. Personal work-file default location
   - `.chromatic` save/open dialogs now start in an account-specific managed directory under AppData.
   - Users can still explicitly choose another destination; this preserves existing workflows.

3. Managed local data directory
   - App-generated formal/official library QTX mirrors move from `Documents/Chromatic Analysis Data/Customers` to AppData managed storage.
   - Only recorded app-generated `mirror_path` files are migrated. Original imported QTX source files are never moved.
   - Migration is administrator-only and occurs after an automatic safety backup.

4. Backup and recovery
   - At most one automatic SQLite backup per 24 hours.
   - Backup includes authentication/permissions and library/workbench/color-card databases.
   - Administrator account menu adds `本地数据管理…` for manual backup, opening managed folders, audit viewing, and explicit restore.
   - Restore makes a safety backup first and requires an application restart.

5. Audit expansion
   - Personal work-file save/open and high-value export paths now create audit entries in addition to existing administrator library operations.

6. Official/formal library state fix
   - Official-only users no longer open a library window whose content header says `正式色库`.
   - Initial scope detection treats the `官方色库` root as official, not formal.
   - Main title, source label and empty-state counts use the actual current scope.

## Security note

This is an offline single-PC application. App-level user ownership and AppData placement prevent accidental cross-user access through the application, but they are not a substitute for separate Windows accounts, NTFS ACLs, disk encryption, or a server-side database when hostile local-machine access is in scope.
