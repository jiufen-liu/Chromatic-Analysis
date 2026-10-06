# Chromatic Analysis v0.11.0 · Textile Spectral Studio

这一版把“反射率”提升为色样的原始数据层，并在现有桌面工作流中整合：

- ChromaShare CPX 打开 / 编辑 / 导出；
- CPX 原布局与灰色空位保留；
- 荧光折角由反射率 > 100% 自动判断；
- 色卡支持光谱排列与光谱 + 感知排列；
- 测色明细改为 颜色信息 / 反射率 / 测量信息 / Attributes；
- Attributes 可编辑并随 CPX / Excel 导出；
- 比色工作台增加 textile 555 shade sorting；
- 555 范围由用户定义，不用整体 ΔE 自动猜测分量容差；
- 555 作为可选列，不增加主界面常驻按钮。

## CPX

`文件 -> 打开 CPX 色卡方案…`，或在色卡工作区空白处右键打开。

真实 CPX 中的 `TileCount / TileSize / TileGap / ViewingConditions / Spectrum / Attributes / XYZ / Lab / RGB` 会被读取。空位以 CPX 的空 `Sample` 槽位保留。色卡窗口 `方案 -> 导出 CPX…` 可写回相同结构。

## 555

在比色工作台：

- 表头右键 -> `555 色阶分选设置…`
- 或数据行右键 -> `分析 -> 555 色阶分选设置…`

输入 L/C/H 或 L/a/b 三个维度的 `±半范围`，软件自动显示：

`单格宽度 = 2 × 半范围 ÷ 9`

范围完整后启用 555；标准为 `555`。可选择只对当前判定“合格”的批次生成代码。
