# Hotfix67 · Release Readiness R1 · Smooth 3D & Palette Sort Stability

基线：Hotfix66。

## 1. 3D 点大小滑块更细腻
- 保持原有可视点大小范围 2.0–10.0 不变。
- 内部滑块从 9 个整数档改为 0.1 细分，共 81 个位置。
- 拖动时点大小连续变化，手感接近“点透明度 / 表面透明度”。
- 点击滑槽仍为小步进，不再跳到端点。
- 仅改变 UI 控制精度，QML 点云、Lab 坐标、3D 数据与颜色科学不变。

## 2. 色卡方案排序稳定性 / 性能
问题根因有两层：
1. 光谱排列为 O(n²) 的谱距离路径，300+ 色样在 GUI 线程计算会阻塞事件循环；Munsell 精确色相也包含逐样本 C/2° 光谱计算。
2. 排序完成后旧实现 `grid.clear()` 并重新创建全部 QListWidgetItem / ColorCardCell，300+ QWidget 同时销毁重建会引发明显卡顿，并增加 Qt 生命周期崩溃风险。

修复：
- 光谱排列、光谱+感知排列、Munsell 感知色相排序移动到独立后台单线程执行。
- 继续调用现有 `spectral_order()`，光谱排序语义/算法不改。
- Munsell 继续使用原 C/2° -> XYZ -> Munsell renotation 路径；只移动执行线程，不改变排序含义。
- 排序结果回到 GUI 线程后，不再 clear/rebuild 全部格子；改为在原 QListWidgetItem / ColorCardCell 上原位更新 key、文字和色块。
- 未解析的旧色卡引用和空白格位置继续保留。
- 新的排序请求使用 generation token，旧后台结果不会覆盖较新的用户操作。
- 主程序退出时取消/关闭排序 executor，避免后台排序跨越 Qt 生命周期。

## 冻结范围
未修改：
- qtx_core 颜色科学与 spectral_order 实现
- QTX / CPX Parser
- 555 / Shade Sort
- library_store
- Excel 原有交换逻辑
- Lab3DView.qml
- RBAC / Data Governance

仅修改：
- `qtx_app/main_window.py`：色卡排序调度与原位 UI 更新
- `qtx_app/lab3d_quick.py`：点大小滑块控制精度
- build/version/docs
