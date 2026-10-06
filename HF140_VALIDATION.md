# HF140 Validation

## Static validation

- `python -m compileall -q qtx_app qtx_core main.py`: PASS
- AST compatibility check against HF139:
  - removed top-level functions: 0
  - removed classes: 0
  - removed existing class methods: 0
- Changed application source files only:
  - `qtx_app/storage_v2.py`
  - `qtx_app/library_store.py`
  - `qtx_app/data_management.py`
  - `qtx_app/main_window.py`
  - `qtx_app/build_info.py`

## DG4 v2 storage simulation

A temporary isolated storage tree was created with a stubbed `QStandardPaths` and real SQLite databases.

Validated:

- 2 colour-card records rebuilt into human-readable mirrors;
- shared scheme path -> `02 色卡编排/共享方案/...`;
- user scheme path -> `02 色卡编排/用户方案/admin/...`;
- HF139 `0000_共享` / `0001_admin` mirrors moved only after new mirror count matched DB count;
- old card folders archived under `99 旧版迁移源/色卡编排_旧结构`;
- old `Customers` copied to `99 旧版迁移源/Customers_旧版迁移源_首次归档`;
- original `Customers` retained as compatibility source;
- active `mirror_path` references to old Customers = 0;
- historical logical source path reference is reported separately and allowed;
- second finalisation run is idempotent.

## Windows GUI boundary

The current execution environment does not contain PySide6/Windows GUI runtime. Therefore the following still require the user's real Windows test:

- Windows hidden attribute on legacy `Customers`;
- colour-card scheme manager table appearance / context menu / keyboard shortcuts;
- Data Management Center buttons and health-dialog layout.

No HF138 MDI-shell code was modified.
