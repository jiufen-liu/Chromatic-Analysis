# Hotfix136 — UI Final Consolidation

Base: Hotfix135a UI Responsive P2 Startup Fix.

## Scope
UI/presentation-only consolidation. No colour-science algorithms, QTX/CPX/Excel formats, RBAC rules, palette sorting, 3D calculations, export semantics, tolerance/555 algorithms, or frozen HF124 performance core were intentionally changed.

### Library sample pickers
- “从色库选择查色标准” now opens from the lightweight SQLite sample index and creates only customer/QTX nodes initially.
- Sample leaves are lazy: they are created on QTX expansion; search materialises only matching leaves instead of constructing the whole Coloro/Pantone file.
- Real sample rows show a cached swatch icon. Fluorescent index rows use the existing fluorescent preview rule; the currently focused sample is hydrated alone for an exact spectral preview.
- Internal GUID QTX filenames are replaced by friendly display labels only; the stored path/source is unchanged and remains available as a tooltip.
- First column stretches with the dialog, the type/status column stays compact, and the dialog size adapts to the current screen.
- “确定/取消” and “加入/取消” are explicit Chinese actions.
- Find-standard picker shows selected count plus current colour/Lab/spectrum preview; OK remains disabled until at least one real sample is selected.
- Existing multi-select Ctrl/Shift semantics are preserved.
- The workbench average-standard selector now also shows colour swatches and Lab tooltips; its calculation/selection semantics are unchanged.
- The workbench “从色库加入色样” picker keeps its existing QTX/group check-to-add behavior, but sample leaves now show swatches and friendlier QTX labels.

### Comparison workbench
- Replaced the clipped height-for-width top FlowLayout with a stable single-row responsive command bar. Low-frequency display controls move to “更多” on narrow widths; all original handlers remain reachable.
- Standard/sample operations and output operations are visually separated more clearly; “移除选中” follows sample operations.
- Sort segments are now true checked segments and remain mirrored by the compact “排序” menu.
- Fit-mode table widths keep numeric columns compact and allocate spare width to the frozen Name column, reducing unused right-side whitespace while preserving the frozen Swatch + Name identity columns.

### Compatibility / safety
- No existing top-level function, class, or class method from HF135a was removed (AST inventory checked).
- FindStandardPickerDialog._build is retained as a compatibility entry point.

## Static validation
- Python compileall: PASS
- ZIP integrity: performed during packaging
- Source inventory preservation vs HF135a: PASS
