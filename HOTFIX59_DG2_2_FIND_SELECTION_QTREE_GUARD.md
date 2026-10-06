# Hotfix59 · DG-2.2 · Find Selection / Permission Tree Guard

## Fix 1 — 色库选中色样进入查色
- 色库工具栏“查色 / 找色”现在先读取当前选中的色样。
- 1 个或多个选中色样会建立一个多标准查色任务，然后再进入查色界面。
- 只解析/补全被选中的色样，不扫描整库光谱。
- 未选择色样时保留旧行为：仅进入查色界面。
- 色库右键“查色 / 找色”同步支持多选标准。

## Fix 2 — 权限树悬空 QTreeWidgetItem
- 资源树 rebuild 前先清空 Python item 引用，再调用 `QTreeWidget.clear()`。
- rebuild 全程 blockSignals，避免清理阶段触发 `itemChanged`/摘要刷新。
- `_refresh_effective_summary()` 在 `_updating` 期间不执行。
- 对可能排队到达的旧 Qt 信号增加 RuntimeError 安全保护。

## Scope protection
- 不修改 qtx_core / 色差 / MI / 555 / QTX Parser / Quick3D / QML / Excel。
- 不改变 DG-2.2 权限规则与查色范围 ACL。
