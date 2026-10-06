# Hotfix61 — DG-3.1 Organization / Installation / Portable Data / Privacy

Hotfix61 separates four identities that were previously implicit:
- **Vendor/developer**: builds the software; has no hidden customer admin account.
- **Organisation**: the customer/company owning the deployment and business data.
- **User/admin account**: belongs to that organisation and is created by the organisation.
- **Installation**: one physical/local app instance (home DEV PC, office DEV PC, customer PC, etc.).

### First customer administrator
A clean database now asks for organisation name and the first admin. Existing admins may create additional admins through User Management.

### Recovery without vendor backdoor
A first-run recovery key is generated and shown once. The DB stores only its salted PBKDF2 hash. It may reset an admin password from the login dialog. A logged-in admin may rotate it.

### Home vs office development PCs
Source code should be synchronised separately (for example through version control). Each PC keeps its own Installation ID and AppData runtime. When the same managed runtime state is needed on another PC, export/import a `.cadata` package rather than copying AppData/SQLite manually.

### Administrator privacy model
Administrator means **system administration**, not automatic surveillance. Normal workbench/color-card queries now use the same owner boundary for admins and operators. Admins can inspect aggregate counts and execute audited ownership transfer, but normal UI does not open another user's private payload.
