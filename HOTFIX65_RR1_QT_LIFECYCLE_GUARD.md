# Hotfix65 · Release Readiness R1 · Qt Lifecycle Guard

## 修复

修复 `QMdiSubWindow.destroyed` 回调在 Qt 页面/主窗口销毁阶段继续刷新色卡工作区，导致：

`RuntimeError: libshiboken: Internal C++ object (PySide6.QtWidgets.QLabel) already deleted.`

## 根因

Python wrapper 仍存在不代表底层 C++ QObject 仍有效。旧代码仅使用 `hasattr()`，在 Qt 销毁顺序中会误判控件可用。

## 处理

- 主窗口增加 `_app_closing` 生命周期标志；
- 应用退出阶段不再刷新色卡 UI；
- `destroyed` 回调只清理字典，并延迟刷新；
- 色卡刷新对 QLabel/QList/QCombo/QMdiArea 做底层 QObject 存活检查；
- 不修改色彩科学、QTX Parser、555、3D 算法、色库数据核心与 Excel 业务交换。
