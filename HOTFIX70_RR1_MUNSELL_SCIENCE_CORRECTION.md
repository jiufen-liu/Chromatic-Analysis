# Hotfix70 · Release Readiness R1 · Munsell Science Correction

依据 `PALETTE_SORT_SCIENCE_AUDIT_R1_20260927_102756.xlsx` 的实测结论，本版只修正色卡编排排序语义，不进入性能缓存/并行阶段。

## 审计结论
- 309 色样中，C*<3 近中性预判 54 个；精确 Munsell 近中性 80 个；本数据集潜在预判冲突 0。
- HF69 以前的近中性色存在“两个量纲混排”：C*<3 分支使用 L*(0~100)，精确 Munsell 近中性分支使用 Value(0~10)，会在近中性段产生不自然的亮暗跳变。
- 9 个样本精确 Munsell renotation 失败。旧逻辑以 Lab h° 伪装为 Munsell 顺序并混入彩色主序列，不够严谨。
- C* 彩度排序公式本身正确。
- 光谱排列是 RMS 最近邻路径；光谱+感知为工程启发式，本版不修改其算法，仅改善名称说明。

## HF70 正式规则
1. **Munsell 彩色组**：Illuminant C / 2° 精确 Munsell Hue family → hue position → Value → Chroma。
2. **近中性色组**：包括 C*<3 快速路由及 Munsell Chroma<1.5；统一使用当前 L* 亮→暗，不再混用 L* 与 Munsell Value。
3. **无法映射组**：精确 Munsell renotation 失败时，不再用 Lab h° 冒充 Munsell；单独放在末组，按 L* 亮→暗。
4. Hue 逆序只反转彩色色相方向；近中性和无法映射组仍保持亮→暗，避免视觉跳变。
5. UI 明确标注 `Munsell Hue（C/2°；近中性色按 L*）`。
6. `光谱排列` 改名为 `光谱相似度路径（RMS）`；`光谱 + 感知` 标注为 `实验性 · 光谱 + 感知路径`。算法未改。

## 未修改
- QTX/CPX Parser
- 555
- 色差/颜色科学核心
- 光谱距离算法及 hybrid 0.18 权重
- 色库核心
- Excel 业务交换
- 3D
- RBAC / Data Governance

## 下一步
先用同一 adidas.qtx 做视觉与 Science Audit 复验。确认排序定义后，再进入 Munsell 缓存复用和性能优化。
