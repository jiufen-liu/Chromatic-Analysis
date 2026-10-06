# Hotfix50 · Performance P3-4

This release virtualizes the **temporary/local QTX/CPX workspace**.

The formal/official library was already optimized by P3-1 to P3-3. The remaining large-file path was `WorkspaceDocument`, which still built one `QListWidgetItem` for every parsed sample and made the list widget as tall as all cards combined.

Hotfix50 changes that surface to `QListView + QAbstractListModel`:

- all Sample objects remain in memory exactly as parsed;
- no spectral or Lab data is discarded;
- no sample count is reduced;
- the view asks the model only for rows that need painting;
- the view owns the scrollbar, so the viewport stays screen-sized;
- workspace search is debounced and filters model row indexes rather than hiding thousands of widget items.

This is UI/data-view virtualization only. It does not change color science or file parsing semantics.
