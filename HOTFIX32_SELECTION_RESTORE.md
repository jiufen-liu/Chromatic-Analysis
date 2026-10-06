# Hotfix32 — 色库选择行为恢复

基线：Hotfix31。

修复：Hotfix31 在右键菜单懒加载重构时误删了一组相邻的 MainWindow 方法，导致正式/官方色库 ColorTile 单击调用 `handle_library_tile_click()` 时目标方法不存在，因此单击、Ctrl/Shift 多选及相关操作失效，而双击明细仍可使用。

本修复从 Hotfix30 恢复以下 9 个既有方法，不改变其业务行为：
- visible_library_tiles
- _describe_3d_source
- open_lab2d_window
- open_lab3d_window
- set_library_tile_checked
- clear_library_tile_selection
- handle_library_tile_click
- select_all_library_tiles
- copy_selected_library_tiles

保留 Hotfix31 的两项改动：
- 右键菜单构建阶段不再提前加载全部完整 Sample payload；需要完整数据时才按操作懒加载。
- 未保存/色库文件的重复“保存到色库 / 存入色库”入口已合并。

验证：
- `main_window.py` AST / `compileall` 通过。
- Hotfix30 对外方法集合已完全恢复；Hotfix31 新增的右键懒加载局部函数仍保留。
- 当前 Linux 验证容器未安装 PySide6 与 colour-science，因此无法执行 Windows Qt GUI 自动化/颜色依赖测试；未将这一限制误报为功能测试通过。
