# Hotfix36 — Qt Quick 3D / RHI 统一 3D 架构

## 目的

Hotfix34/35 的新 3D 使用 `QOpenGLWidget`。当机器的原生 OpenGL 上下文或 shader 初始化失败时，程序会先显示白色新窗口，再关闭并重新打开旧的黑色 QPainter 3D，导致 UI 与设计稿不一致，并且旧渲染在 1950/3500 色样旋转时仍然掉帧。

Hotfix36 改为统一的 Qt Quick 3D / RHI 渲染架构：

- Windows 默认由 Qt RHI 使用 Direct3D 11；不再硬依赖原生 OpenGL。
- 使用 `QQuickView + QWidget.createWindowContainer()`，保留 Qt Quick 的 threaded render loop，避免 `QQuickWidget` 的额外离屏渲染和主线程渲染限制。
- 色样球使用 `FileInstancing`，1950/3500 个真实 Lab 色样一次上传 GPU，旋转时 Python 不再逐样重新投影和绘制。
- UI 始终是同一套浅色专业界面；如果 RHI 场景无法在启动时生成有效帧，只在同一窗口中央切换到轻量 CPU 兼容视图，不再弹出旧黑色窗口。
- 真实 Lab 坐标、QTX/CPX/Excel、SQLite、CMC、DE2000、MI、555 均未修改。

## 3D 显示模式

1. **色彩点云**（默认）——真实色样使用柔和球体材质、灰色中性背景和清晰 CIELAB 坐标轴。
2. **表面+点**——真实点云叠加由当前 Lab 数据推导出的显示包络面。
3. **色域表面**——仅显示推导的包络面。包络面是显示辅助，不改变任何测量数据。

## 兼容策略

默认不指定 `QSG_RHI_BACKEND`，让 Qt 根据平台选择图形 API。Windows 正常情况下为 D3D11。若机器没有可用硬件图形设备，可使用 `RUN_3D_SOFTWARE_FALLBACK.bat` 请求 Qt 的软件图形后端。

## 验收

- 3D 打开后不再出现“白色新窗口 → 旧黑色窗口”的跳转。
- NCS TOTAL 1950、Pantone 2331、Coloro 3500 均应在同一 UI 内显示。
- 连续拖动旋转时，点云由 Qt Quick 3D/RHI 负责，不在 Python `paintEvent` 中逐球绘制。
- 若 GPU/RHI 初始化失败，中央画布切换到“兼容渲染”，左右 UI 不变化。
