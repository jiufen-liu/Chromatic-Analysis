# Hotfix48 · P3-3 Stability

基线：Hotfix47 / Performance P3-3。

## 1. Munsell 排序不再整库现算
- 旧路径：选择 Munsell 后退出 SQLite 真分页，反序列化整个色库，并逐色样执行 C/2° 光谱→XYZ→Munsell renotation；3500+ 色样会长时间占用 GUI/内存。
- 新路径：首次对当前客户建立可重建的持久 Munsell 排序索引；计算在维护线程逐批执行，UI 保持响应。
- 建立完成后 Munsell 排序直接使用 SQLite ORDER BY + LIMIT/OFFSET，不加载光谱/JSON。
- 低彩度中性色、异常样本的排序语义保持原逻辑。

## 2. 增加 a* / b* 排序
- a* 低→高 / 高→低
- b* 低→高 / 高→低
- 均使用 P3-2 的 lab_a_idx/lab_b_idx，保持真分页与轻量读取。

## 3. 加入比色工作台后立即显示
- 从色库菜单选择目标工作台后，数据加入完成即切换到“比色工作台”。
- 自动激活刚加入的工作台标签与 MDI 工具窗口。

## 范围保护
- qtx_core、3D Quick3D/QML、颜色算法、QTX/CPX/Excel 格式未修改。
- 完整 payload 仍是唯一完整数据源；Munsell 字段只是可重建排序索引。
