# Hotfix101 · LABC Atlas 验证模式

## 目标
让用户不需要理解公式，只需看少量需要目视确认的颜色。

## 新增
- LABC Atlas 顶部新增 `✓ 验证模式`。
- 自动检查 8 个固定验收页：
  - Neutral
  - Blue
  - Green
  - Yellow
  - Red
  - Violet
  - Blue ↔ Neutral ↔ Yellow
  - Red ↔ Neutral ↔ Green
- 机器结果只有三种：
  - PASS：图谱结构一致，当前页没有边界色。
  - REVIEW：结构一致，但存在 Hue/Neutral/剖面边界色，只需目视这些颜色。
  - FAIL：真正的结构不一致，例如错误 family 进入页面或 Neutral 没落在对置剖面中心。
- 可导出 TXT 验证报告。

## 验收哲学
`REVIEW` 不等于算法错误。它只表示该色样位于数学边界附近，机器不替用户做主观颜色命名决定。

用户只需看：
1. Neutral 是否主要是黑/灰/白，Navy 不应误入。
2. Blue 是否聚集蓝、深蓝、Navy。
3. Green 是否聚集绿色。
4. Yellow / Khaki 邻域是否自然。
5. Red / Pink 邻域是否自然。
6. Violet 是否聚集紫色。
7. 对置剖面中间是否趋向 Neutral，左右是否是对应 hue 邻域。

## 非破坏性保证
本 Hotfix 只修改 LABC Atlas 模块与其测试：
- 不改 QTX / CPX / Excel 读写。
- 不改查色、比色、555、3D、光谱、色库、RBAC。
- 不改色卡已有卡位、保存、导出和 L*/a*/b*/C* 排序。
- 验证模式只读，不自动重排或修改色样。
