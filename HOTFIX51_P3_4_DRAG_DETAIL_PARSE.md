# Hotfix51 — P3-4 Drag / Detail / Parse Polish

基线：Hotfix50 / P3-4 大型工作区虚拟化。

## 本轮修复

1. 虚拟 QListView 恢复可靠跨窗口拖拽：左键按下并移动超过 Qt drag threshold 后显式进入现有 CARD_MIME 拖拽链路，保留 Ctrl=复制 / 默认跨窗口移动语义。
2. 色样详情 Attributes 表格恢复“项目 / 值”双列：空表也不会把“项目”列压没；项目列默认 190px，可拖动分隔线调整，值列自适应填满。
3. 大文件导入增加解析/界面分段计时，状态栏可看到真实耗时。
4. 增加同一会话内的外部文件解析缓存：QTX/CPX/Excel 文件 size + mtime 未变化时，再次打开复用不可变 Sample 对象；冷解析仍使用原权威 parser，不改变颜色算法。

## 不变范围

- qtx_core 颜色算法与解析规则不修改。
- P3-1 / P3-2 / P3-3 不回退。
- Quick3D/QML、RBAC、正式/官方色库结构不修改。
