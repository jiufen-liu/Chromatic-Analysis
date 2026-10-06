# Hotfix95 — Science-Guided Appearance Continuity

## 1. 本次不再用“针对某个 QTX 调参数”的方式

Hotfix95 把色卡连续排序改成 **Topology first, local distance second**：

1. 先确定色貌拓扑：色相家族 / 中性色轴 / 明度 / 彩度；
2. 再在合理的色貌邻域内，用 CIEDE2000 + 光谱 RMS 优化局部连续性；
3. 低彩度样本禁止仅因为 ΔE00 较小而跨越不相关的色相家族；
4. 暖黄/卡其方向（CIELAB +b*）与蓝方向（-b*）之间设置 opponent veto；
5. 只有位于相邻色相家族边界附近的样本，才允许有限度跨边界；
6. 极浅近白与极深近黑使用保守的中性色判定，不再像 PAC V24 那样扩大 Neutral Field；
7. 深蓝 / Navy 与浅蓝只要仍有可靠色相信号，就保留在 Blue，不被 Neutral 吞并。

这套规则不读取客户名、颜色名或 adidas / Nike 等品牌字段。

## 2. 色彩科学依据

### Munsell Color Order System

Munsell 的核心结构是 Hue / Value / Chroma：色相环绕中性色轴，Value 表示明暗，Chroma 表示离中性色轴的距离。RIT Munsell Color Science Laboratory 公开的 Munsell Renotation 数据来自大规模视觉实验后的修正，用于提高色序系统的视觉均匀性。

工程含义：**色相拓扑不能被“最近一个 ΔE00 邻居”随意破坏。** 一个卡其/黄褐样本即使与某个蓝灰的 ΔE00 不算极大，也不应因此跨越色相拓扑进入 Blue。

### CIEDE2000 — ISO/CIE 11664-6:2022

CIEDE2000 是针对两色刺激之间感知色差的 colour-difference formula，并对 lightness / chroma / hue 以及 chroma-hue interaction 做修正。它非常适合评价局部相邻色差，但标准本身并不是一个“全局颜色排序拓扑”。

工程含义：Hotfix95 继续使用 CIEDE2000，但将它放到正确的位置：**先限定合理色貌邻域，再用 ΔE00 做局部优化。**

### CIECAM16 / CAM16-UCS

CIE 248:2022 指出，colour appearance model 是在特定 viewing conditions 下把 XYZ 与感知属性相关量互相转换的方法。CIE 当前亦有 TC 1-100 研究将 CAM16-UCS 推荐为 CIE Uniform Colour Space。

工程含义：色貌排序应该尊重 lightness / chroma / hue 与观察条件，而不是只追求一个全局最短路径。

Hotfix95 **没有**在这一版把现有 D65/10° 生产数据链强行替换成 CAM16-UCS。原因是这会扩大变更范围，并需要独立验证 viewing-condition 参数。当前仍保留项目已验证的 D65/10° CIELAB、CIEDE2000 和反射率数据，只借鉴 colour-appearance / colour-order 的结构原则。

## 3. 解决三类已知失败

| 已知问题 | Hotfix95 机制 |
|---|---|
| 卡其 / 米色 / 黄褐跑进 Blue | Hue topology + CIELAB b* opponent veto + 非相邻家族禁止迁移 |
| 深蓝 / Navy 被 Neutral 吞并 | conservative neutral radius + deep hue preservation |
| 近白 / 浅灰被微小 h° 强行染色 | neutral-axis handling + same-family rescue only |

## 4. 保留回退路径

软件主按钮“色貌连续排序”使用 Hotfix95 Science 核心。

排序菜单保留：

- **色貌连续排序**：Hotfix95 Science；
- **经典连续排序（历史光谱+感知）**：原 `spectral_hybrid`，作为回退基线；
- Munsell 排序：保持原有功能。

原历史算法没有删除。

## 5. 验证门槛

诊断中心选项 20 会同时生成 V23 与 Science 预览。

必须重点检查：

- `CROSS_FAMILY_ANCHOR_RISK = 0`；
- `WARM_COOL_INVERSION_RISK = 0`；
- adidas：卡其 / 橄榄卡其 / 米色不得混入 Blue；
- adidas：浅蓝应与浅蓝连续，深蓝 / Navy 不应被拆到不相关区域；
- FIGS / Nike：不能重现 PAC V24 的深色过度 Neutral 化；
- Apple：近白/浅灰应稳定，但不能产生新的大跳跃；
- 91 个 QTX 批量必须全部 PASS。

## 6. 参考资料

- ISO/CIE 11664-6:2022, *Colorimetry — Part 6: CIEDE2000 Colour-Difference Formula*.
- CIE 248:2022, *The CIE 2016 Colour Appearance Model for Colour Management Systems: CIECAM16*, DOI 10.25039/TR.248.2022.
- CIE TC 1-100, *To recommend CAM16-UCS as the CIE Uniform Colour Space*.
- RIT Munsell Color Science Laboratory, *Munsell Renotation Data / Educational Resources*.

> 注意：Hotfix95 的 family 名称是工业色卡编排的宽色相骨架，并不宣称这些边界是 CIE 标准命名边界。标准/文献提供的是结构原则；边界是本软件为保持连续色卡浏览而使用的工程划分，并通过回归测试约束。
