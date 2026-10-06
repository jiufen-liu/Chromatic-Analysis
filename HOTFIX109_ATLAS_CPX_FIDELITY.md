# Hotfix109 · 色彩空间图谱 UI + CPX 原版式保真

## 1. 色彩空间图谱 UI
- “综合色图谱 / LABC Atlas”统一命名为“色彩空间图谱”。
- 色相族按钮与对向色相环改为中文：中性、红、红橙、橙、黄、黄绿、绿、青、蓝、蓝紫、紫、红紫。
- 删除开发阶段“验证”按钮。
- “在原色卡定位”改名为“定位到色卡”，含明确 Tooltip：只选中/滚动定位，不改变版位。
- 右侧详情区改为可垂直滚动，色相环与色样详情不再因窗口高度不足互相遮挡。
- 已选色样/相近色区域增高，原始色样名称支持换行与 Tooltip。
- 原始样品英文名称保持源文件原文，不做翻译或改写，以保证色号追溯与 CPX/QTX 一致性。

## 2. CPX 打开逻辑
CPX 不再被当成普通“色样列表”压缩成 4/6 列文档。

- 从左侧“CPX 文件”、菜单“打开 CPX”、拖入主工作区：按 CPX 自己的 Fixed Layout 打开到“色卡编排”内部子窗口。
- 从色卡编排“添加色样”选择 CPX：CPX 也按原版式打开为独立色卡子窗口，不打乱当前色卡；QTX / Excel 仍追加到当前色卡。
- 保留 CPX 的 TileCount、TileSize、TileGap、空白 Sample 槽位、ViewingConditions、原始 Sample XML 与未知字段。
- 打开的源 CPX 不会自动写入“已有色卡方案”；修改后只有用户主动保存，才保存为软件内色卡方案。

## 3. 源 CPX 保护
- 禁止导出 CPX 时直接覆盖当前源 CPX 路径。
- 未修改版式、名称与色样时另存 CPX：直接写出原始 CPX 字节快照，做到 byte-for-byte 一致。
- 修改版式后另存：基于原始 CPX XML 模板，只更新必要的 Layout / Sample 顺序 / Rank；未知根节点、Sample 附加节点与原始 Attributes 尽量保留。
- 原始空白 Sample 节点也优先复用，不再无条件重建通用空白节点。

## 4. 用户提供 CPX 参考文件验证
文件：`2025 Smart Color Yarn  排版 MPC.cpx`

读取到：
- PaletteName: 2025 Smart Color Yarn  排版 MPC
- Layout: Fixed
- TileCount: 14 × 67
- TileSize: 84 × 34
- TileGap: 20 × 22
- 总槽位: 938
- 实际色样: 80
- 空位: 858

验证：
- 解析后的 14×67 固定版式与空位完整保留：PASS
- 未修改另存 CPX 与源文件逐字节一致：PASS
- 修改一个版位后导出，原 Ingredient / Attributes 保留，Rank 同步新位置：PASS

