# Hotfix135 · UI Responsive P2 Complete

Base: **Hotfix133 Combined Display Fit** (intentionally does NOT include the HF134 tolerance/555 shortcut additions).

## Scope
Presentation/interaction shell only. Existing colour science, QTX/CPX/Excel semantics, sorting algorithms, exports, RBAC, 3D and frozen performance paths are not redesigned.

## Changes

1. **Modern MDI subwindow chrome**
   - Replaces the old native Qt/Windows-looking internal title strip with a light Studio title bar.
   - Keeps move, edge resize, minimize-to-taskbar, maximize/restore and close.
   - Keeps first-open true MDI viewport fitting from HF131.

2. **Responsive command surfaces**
   - Adds a reusable FlowLayout for controls that must wrap on narrow windows.
   - Workbench top controls and Find conditions wrap instead of forcing the page wider.
   - Workbench low-frequency actions collapse into `更多` while primary operations remain visible.
   - Palette Studio progressively folds Atlas / restore / undo / redo into `更多` on narrow widths.
   - Library keeps search as the dominant row and wraps classification/sort filters below it.

3. **Comparison table density redesign**
   - `色块 + 名称` are frozen identity columns and remain visible while horizontal data columns scroll.
   - Same model and selection model are shared; no sample data duplication.
   - Presets: 基础查看 / 色度分析 / 色差分析 / 555 分色 / 完整数据 / 自定义.
   - Density remains separately selectable: 适应窗口 / 标准宽度 / 紧凑显示.

4. **Column settings redesign**
   - Replaces the very long flat menu with a compact task preset menu plus a categorized `列与视图` dialog.
   - Categories: 基础信息, CIELAB/LCh, 色差分量, 三刺激值, 白度/色调, 色差公式, 质量判定.
   - Search is available inside the dialog.
   - `色块 + 名称` are treated as permanent identity columns.

5. **Secondary navigation**
   - Formal Library internal browser/navigation pane can be collapsed independently.
   - In compact mode it auto-collapses when the live MDI viewport is narrow, unless the user has explicitly chosen a state.

6. **Unified visual hierarchy**
   - Modern MDI active/inactive states.
   - Segmented sort controls on wide workbench layouts, compact sort menu on narrow layouts.
   - Unified panel toggle, inline-field, toolbar and frozen-column styling.

## Recommended Windows visual validation matrix

- 1366×768 @ 100%
- 1920×1080 @ 100%
- 1920×1080 @ 125%
- 1920×1080 @ 150%
- 2560×1440 @ 125%

At each size verify: global sidebar open/closed, first-open MDI fit, title-bar drag/resize/minimize, Find wrapping, Library secondary pane, Workbench toolbar wrapping, frozen columns, presets, Palette Studio `更多`, and no clipped buttons/text.

## Validation in build environment

- Python AST parse: PASS
- Whole-tree `compileall`: PASS
- Source diff against HF133: only `qtx_app/main_window.py`, `qtx_app/build_info.py`, plus this note.
- Runtime PySide6 validation is not possible in this Linux build container because PySide6 is not installed.
- Core pytest suite could not run here because the container does not have the project `colour` dependency; core source files were not modified.
