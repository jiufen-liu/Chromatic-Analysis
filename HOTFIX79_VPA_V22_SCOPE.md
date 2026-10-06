# Hotfix79 · Visual Palette Arrangement V2.2 Offline Audit

本版只扩展离线验证，不把 V2.2 接入正式 Palette Studio。

## V2.2

- 保持 12 Hue Family 大骨架。
- 自适应 L* 明度层。
- 同一明度层内以 C* 作为主要横向坐标，低彩度 → 高彩度。
- Hue 作为局部次级坐标；C*<10 标记低 Hue 置信度。
- 距 Hue Family 边界 ≤4° 标记软边界；相邻 Family 且 ΔE00<3 记为“桥接”，不当作错误。
- White / Neutral Grey / Black 改成同一个 Neutral Axis，保持亮 → 暗连续，仅保留语义子标签。
- 仅真正近中性色与感知主导的白/黑进入 Neutral Axis；低彩度彩色尽量留在 Hue Family 的低彩度端。
- 行内 C* 跨度默认控制在 25 以内，避免灰桃 → 高彩橙 → 灰桃式跳跃。

## 诊断

`RUN_DIAGNOSTICS.bat -> [9]`

可拖入：
- 单个 `.qtx`：输出 PNG + XLSX + TXT + JSON。
- 包含多个 QTX 的文件夹：递归批量验证，并生成 Batch Summary Excel/JSON，同时保留每个 QTX 的预览和审计。

## 冻结

- `qtx_app/main_window.py` 与 HF78 二进制内容一致。
- 不替换 Munsell / Spectral RMS / Hybrid / V1 / V2 / V2.1。
- 不修改 QTX/CPX/Excel/555/3D/RBAC/色库业务逻辑。
