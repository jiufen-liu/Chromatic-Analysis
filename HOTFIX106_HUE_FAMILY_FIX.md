# Hotfix106 · Hue Family Fix

## 修正原因
Hotfix105 的 h° 排序使用 5° 小色相桶，并在每个小桶里重新按 L* 排序。
这会造成同一 Blue 家族中反复出现：
浅蓝 → 深色/近黑 → 浅蓝 → Navy
的锯齿式视觉跳跃。

## Hotfix106
- Neutral 仍保持单独连续块。
- 综合色相先按与 LABC Atlas 相同的综合色相族组织。
- 同一综合色相族内部改为 L* 由浅到深。
- C* 和局部 h° 只作为后续 tie-break，不再让每个 5° 小桶重新开始明度。
- Color Difference 菜单明确改名为“基准色差”。
- Color Difference 增加单调性自检；排序结果若不是相对基准色 ΔE 单调递增，会中止并报警。
- Color Difference 排序后，色卡底部数据行显示当前基准 ΔE，便于直接核对。
- Running ΔE 逻辑保持不变。
- LABC Atlas Hue / 明度 / 彩度视图继续保留，不修改原文件与原卡位。
