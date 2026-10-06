Chromatic Analysis · 诊断与性能测试中心
============================================================

目的
----
从 Hotfix68 开始，把诊断工具永久放在主程序包里，不再另发一个临时测试包。
以后如果出现“卡、慢、崩溃、某台电脑异常”，优先运行：

  RUN_DIAGNOSTICS.bat

功能
----
1. 系统 / 版本 / Python / PySide6 / NumPy / colour-science 诊断
2. QTX 解析与颜色计算性能基线（沿用 Performance P3 的 tools/performance_baseline.py）
3. 色卡排序专项性能测试（Munsell / 光谱 / 光谱+感知）
4. 自动收集：
   - AppData 下最近的 crash_*.log
   - performance_reports 下最近的 TXT / JSON
   - 当前版本、CPU、Windows、Python 与依赖信息
5. 一键打包为 diagnostics_reports/DIAGNOSTIC_BUNDLE_*.zip

以后怎么反馈问题
--------------
- 如果只是性能慢：运行对应性能测试，把最新 TXT 发给 ChatGPT。
- 如果程序崩溃或原因不明：运行 RUN_DIAGNOSTICS.bat -> 4，
  把生成的 DIAGNOSTIC_BUNDLE_*.zip 发给 ChatGPT。

原则
----
诊断工具只读取数据并计时，不修改色库、不修改 QTX、不保存色卡方案，
也不改变颜色科学结果。


Hotfix69 新增排序科学/逻辑验证：
RUN_DIAGNOSTICS.bat -> 4
输出 diagnostics_reports/PALETTE_SORT_SCIENCE_AUDIT_R1_*.xlsx/.txt/.json
建议用实际色卡编排 QTX 运行；先看“异常与重点样本”“最大跳跃_TOP50”和两张 Munsell 对照表。
拖入路径现在兼容单引号/双引号/PowerShell & 前缀。

HF77 新增：
[7] 视觉色卡编排 V2 离线验证
- 不修改正式色卡，不写入色库；
- 输出完整 PNG 预览 + Excel/JSON/TXT；
- 用于先验证“色相 × 明度 × 彩度”的二维色卡结构，再决定是否进入正式功能。
