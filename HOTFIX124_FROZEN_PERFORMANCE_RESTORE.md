# Hotfix124 · Frozen Performance Restore

This build deliberately stops inventing new startup/palette architecture.

Baseline: HF122 (validated core-performance baseline).

Restored/migrated mature performance paths:
- Formal library keeps the Hotfix26 page-sized recycled ColorTile pool and P3-1 SQLite paging already present in HF122.
- HF123 first-open lazy tool-page construction is removed; the library page again exists before the user clicks “色库”, matching the frozen mature navigation path.
- Palette Studio now uses the same HF50/HF51 virtual surface pattern as WorkspaceDocument: QListView + QAbstractListModel + IconMode + uniform item sizes + batched layout + the view's own scrollbar.
- No outer document-height QScrollArea, no QListWidgetItem-per-slot, no QTableView row/column geometry, and no ColorCardCell QWidget materialisation.
- Existing palette data/order/search/selection/drag/drop/sort/save/export semantics are retained through the compatibility proxy.

Smoke test only:
1. Launch and click Formal Library once.
2. Open Palette Studio, import Coloro 3500, verify first screen and continuous scroll.
