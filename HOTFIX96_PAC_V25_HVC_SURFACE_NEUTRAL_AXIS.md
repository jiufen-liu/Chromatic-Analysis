# Hotfix96 · PAC V25 HVC Surface Path / Neutral Axis

本版只重构“色貌连续排序”，不删除或替换其它业务功能。V23、V24.1 仍保留为诊断/回归基线。

## 目标

解决 Hotfix95 仍暴露的三类结构问题：

1. 低彩度浅灰因 h° 数值不稳定而被拆到 Red / Violet / Neutral 不同位置；
2. Near-White 边界小岛与同一广义色相的深色主体被强行连成一个巨大跳跃；
3. 各 Hue Family 独立排完后机械首尾拼接，导致“上一家族终点”与“下一家族起点”不匹配。

## 科学结构

- 测量基础不变：D65 / 10° CIELAB；
- CIEDE2000只作为局部感知距离，不直接决定跨 Hue Family 的归属；
- 采用 H–V–C 色序思想：Hue 拓扑 + Value/Lightness + Chroma/Neutral-axis distance；
- 中性色判定使用同明度中性点 `(L*,0,0)` 的 CIEDE2000 距离，避免靠近中性轴时 h° 角度乱跳；
- 暖/冷 opponent guard 与相邻 Hue Family guard 延续 Hotfix95；
- 新增 Near-White boundary-island assimilation：只允许“小而孤立、靠近色相边界、与邻接家族的浅色群有强支持、与自身深色主体明显分离”的浅色小岛迁移；
- 新增 endpoint-aware family path：候选路径同时考虑家族内部连续性与相邻家族的可用端点；
- 新增 boundary regret：用“实际家族边界 ΔE00 - 此数据集中该两家族可达到的最小 ΔE00”判断边界是否属于算法造成的可避免错误，而不是把本来就缺少过渡样本的 Yellow→Green 等自然空档误判为算法失败。

## 新增诊断指标

- `neutral_split_count`
- `pale_boundary_island_changes`
- `family_boundary_max_de`
- `family_boundary_max_regret`
- `avoidable_catastrophic_boundary_count`
- `within_family_max_de00`
- `lightness_reversal_count`
- `isolated_chromatic_island`
- `CROSS_FAMILY_ANCHOR_RISK`
- `WARM_COOL_INVERSION_RISK`

## Adidas 已知回归点

开发期用用户提供的 309 色 Excel 做了独立回归：

- 原先首列/末尾被拆开的低彩度浅灰均进入同一 Neutral Axis；
- 卡其/黄褐继续保持在 Yellow / Orange-Brown / 相邻 Green，不允许进入 Blue；
- 原先浅蓝白 → 深紫的巨大跳跃通过 Near-White boundary-island 处理消除；
- Blue 末端转入 Deep Violet，再进入 Black / Neutral Axis；
- 不使用 adidas、客户名、色号或文件名特例。

## 诊断入口

运行 `RUN_DIAGNOSTICS.bat`，选择：

`21 - 色貌连续排序 PAC V25 HVC Surface A/B`

可拖入单个 QTX 或包含多个 QTX 的文件夹。每个 case 会生成：

- `001_V23.png`
- `001_V241.png`
- `001_V25.png`（与软件主按钮一致的线性顺序）
- `001_V25_STRUCTURAL.png`
- `001_AUDIT.txt`
- `001_AUDIT.json`

批量汇总：`PAC_V25_HVC_AB_SUMMARY.csv`。
