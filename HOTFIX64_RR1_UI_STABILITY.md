# Hotfix64 · Release Readiness R1 · UI Stability

## 仅修复的 3 个实机问题

1. Studio MDI：正式色库 + 比色工作台同时打开时，旧逻辑自动执行 70/30 平铺，导致比色窗口被压到不可用宽度，看起来像“界面卡住”。HF64 不再自动平铺；窗口保持独立可移动/重叠，并设置合理最小尺寸。
2. Qt Quick 3D：点大小范围为 2..10，但 QSlider 默认 pageStep=10，点击滑槽会一步跳到端点。HF64 对点大小设 singleStep=1/pageStep=1。
3. 色卡编排：新建方案原来创建瞬间就写入数据库。HF64 改为未保存草稿；Ctrl+S 后才入库。已有方案菜单仅显示最近 6 个；方案管理器增加名称搜索。

## 冻结边界

未主动修改：
- qtx_core/colorimetry.py
- qtx_core/qtx_parser.py
- qtx_core/shade_sort.py
- qtx_app/library_store.py
- qtx_app/excel_exchange.py
- qtx_app/qml/Lab3DView.qml

## 实机回归

- 同时打开正式色库与比色工作台，两个窗口都可点击、移动、最大化、最小化；比色工作台不再被压成窄条。
- 3D 点大小：拖动正常；点击滑槽每次只改变 1 级，不跳端点。
- 新建色卡方案后直接关闭：方案管理/打开已有方案中不出现。
- 新建方案后 Ctrl+S：方案进入已保存列表，关闭再打开仍存在。
- 保存方案超过 6 个：右键“打开已有方案”只显示最近 6 个，可通过“查看全部方案/方案管理搜索”找到其余方案。
