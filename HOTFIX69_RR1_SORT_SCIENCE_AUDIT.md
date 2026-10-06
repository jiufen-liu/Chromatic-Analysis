# Hotfix69 · Release Readiness R1 · Sort Science Audit

## 目的
在继续做 Munsell 缓存和排序加速之前，先验证“排序定义是否科学、视觉是否符合预期”。本版**不改变正式色卡排序算法**，只增加可重复的科学/逻辑验证工具。

## 新增
- `RUN_DIAGNOSTICS.bat` 菜单新增：`4. 色卡排序科学 / 逻辑验证（生成可视化 Excel）`。
- 新增 `tools/palette_sort_correctness_audit.py`。
- 输出 `diagnostics_reports/PALETTE_SORT_SCIENCE_AUDIT_R1_*.xlsx/.txt/.json`。
- Excel 包含：
  - 总览与结论
  - C* 彩度高→低
  - Munsell HF68 当前序
  - Munsell 审计建议序
  - 光谱相似度路径
  - 光谱+感知路径
  - 异常与重点样本
  - 最大跳跃 TOP50
  - 四种排序位置对比
- 每个排序表包含屏幕色块预览，方便检查“逻辑正确但图像不连续”的情况。
- Munsell 审计会对全部有光谱色样实际尝试 C/2° renotation，而不先被 `C*<3` 短路；因此能查出“当前预判中性色 vs 精确 Munsell”潜在不一致。
- 明确列出 fallback 色样，不再只给一个计数。
- 光谱路径列出相邻 RMS、ΔE00 和最大跳跃，便于定位视觉突变。
- 记录当前 `光谱+感知` 的实际启发式 `spectral RMS + 0.18 × ΔE00`，但本版不修改它。

## 诊断中心修复
Windows Terminal / PowerShell 拖文件时，路径可能成为：
- `'D:\\...\\file.qtx'`
- `"D:\\...\\file.qtx"`
- `& 'D:\\...\\file.qtx'`

Hotfix69 会自动去除这些外层包装，不需要用户手动删除引号。

## 冻结边界
本版不修改：
- `qtx_app/main_window.py` 正式排序行为
- `qtx_core/analysis.py` 光谱/混合排序算法
- `qtx_core/colorimetry.py` Munsell/色差/颜色科学
- QTX/CPX Parser
- 555
- 色库核心
- Excel 原业务交换逻辑
- 3D

## 下一步
用户用实际 `adidas.qtx` 运行诊断中心选项 4，并把生成的 XLSX/TXT 发回。根据真实异常样本、Munsell 中性色差异和最大跳跃，先确定最终排序定义，再进入性能优化。
