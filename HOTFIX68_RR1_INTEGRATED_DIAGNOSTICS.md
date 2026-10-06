# Hotfix68 · Release Readiness R1 · Integrated Diagnostics Foundation

范围：仅增加长期诊断/性能测试工具，不修改颜色业务、UI业务行为或数据结构。

新增：
- `RUN_DIAGNOSTICS.bat`
- `tools/diagnostics_center.py`
- `tools/palette_sort_benchmark.py`
- `RUN_PALETTE_SORT_BENCHMARK.bat`
- `DIAGNOSTICS_README.txt`

沿用：
- `tools/performance_baseline.py`（Performance P3-5/P3-6 基线）
- AppData `crash_*.log`
- `performance_reports/`

目标：以后用户只需运行一个诊断入口，即可把性能报告、崩溃日志、系统信息汇总为 `DIAGNOSTIC_BUNDLE_*.zip`。
