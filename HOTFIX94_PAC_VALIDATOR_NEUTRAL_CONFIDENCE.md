# Hotfix94 · PAC V23.1 Validator Hardening + PAC V24 Neutral/Hue Confidence Audit

本包直接基于用户上传的 Hotfix93 完整工程修改，正式 Palette Studio 业务行为保持冻结。

## A. 已修复：PAC V23 `break_threshold` 报错

Hotfix93 的 V2.3 核心已经改成“自适应 soft/hard 结构断点”，但审计脚本仍按 V2.1/V2.2 的旧 schema 读取 `g['break_threshold']`，因此 7 个品牌 QTX 会在报告阶段统一 KeyError。

Hotfix94 不引入一个新的静态默认阈值，而是把 V2.3 实际正在使用的 `_structural_thresholds(local_nn)` 作为唯一来源，同时在 group metadata 输出：

- `break_threshold`（兼容旧审计，等于 soft rail）
- `break_soft`
- `break_hard`
- `local_nn`

所以排序逻辑与审计不会再各维护一套阈值。

## B. 已修复：84/91 QTX 长输出路径失败

批量产物不再拼接完整 QTX 文件名。P23.1 使用：

- `001_PREVIEW.png`
- `001_AUDIT.txt`
- `001_AUDIT.json`
- `MANIFEST.csv` 保存原始文件名和完整路径
- `SUMMARY.csv` 保存真实 PASS/FAIL

批量目录改为短目录：`diagnostics_reports/P23_1_时间戳/`。

如果 Windows 安装路径本身已经过长，验证器会自动回退到 LOCALAPPDATA/TEMP 下的短诊断目录，而不是依赖系统 Long Paths 设置。

## C. 已修复：失败却提示“每个QTX都有 PREVIEW + AUDIT”

现在只有 PREVIEW、AUDIT.txt、AUDIT.json 都真实存在并且 case 无异常时才记为 PASS；否则 MANIFEST 记录 `failed_stage/error`，结束语明确显示 FAIL 数量。

## D. 已加入：PAC V24 Neutral Confidence + Hue Confidence（诊断候选，不替换正式排序）

V23 继续作为冻结 A/B 基线。V24 在 V23 Graph/MST 结构上新增：

1. lightness-aware Neutral Field：高 L* 近白区域允许更宽的 neutral envelope；中间调保持保守；极深色只小幅放宽。
2. Hue Confidence：低 C* 的 h° 权重连续下降，不再让几乎白/灰的数值 h° 强制决定 Green/Blue/Orange-Brown。
3. transitional tint anchor：处于 neutral/chromatic 过渡带的颜色只有在附近存在真正的 chromatic anchor 时才继承彩色 family。
4. 新诊断指标：`LOW_CHROMA_HUE_RISK`、`DEEP_HUE_RISK`、`NEAR_WHITE_HUE_RISK`。
5. A/B 输出：每个 case 同时生成 `001_V23.png` 和 `001_V24.png`，便于肉眼比较，避免只看单一 ΔE 指标。

## 诊断菜单

运行 `RUN_DIAGNOSTICS.bat`：

- [18] PAC V23.1：先验证基础设施与冻结基线
- [19] PAC V24 A/B：比较 V23 与 Neutral/Hue Confidence 候选

推荐顺序：先让 91 个 QTX 的 [18] 达到 91/91，再用 [19] 做 A/B；V24 未验证通过前不进入正式色卡编排。
