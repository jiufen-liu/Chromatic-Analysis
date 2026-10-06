# Hotfix117 · 全程序流畅度与交互性能验证

## 目的
在回归后续开发计划前，建立一个可重复的“整机真实操作”性能门槛，而不仅仅测试 QTX 解析或色卡排序算法。

## 新增入口
- `RUN_DIAGNOSTICS.bat` -> `[22] 全程序流畅度 / 交互性能回归测试`
- 或直接双击 `RUN_FULL_PERFORMANCE_TEST.bat`

## 采集指标
1. 主窗口显示/首次绘制时间。
2. Qt 事件循环延迟与 >100 / 250 / 500 / 1000 ms 阻塞次数。
3. 用户输入到下一次 Paint 的近似响应 P50/P95/P99/Max。
4. Windows 进程工作集、Private Memory、CPU 平均/P95/峰值（无第三方依赖）。
5. 现有 `perf_monitor` 埋点的 count / average / max / slow_count。
6. 正常退出后的 `performance_summary.json` / `performance.log` 自动收集。

## 测试原则
同一台电脑、同一组 QTX/CPX/Excel、同一路线重复测试，才可用于版本对比。

## 输出
`performance_reports/FULL_APP_SESSION_YYYYMMDD_HHMMSS/`
- `FULL_APP_PERFORMANCE.txt`
- `FULL_APP_PERFORMANCE.json`
- `ui_probe.json`
- `performance_summary.json`（如生成）
- `performance.log`（如存在）

诊断中心 `[5]` 会把最近 3 次完整会话一并打包。

## 兼容性补丁
为保证完整回归路线可执行，同时修复 `WorkbenchIlluminantDialog` 的 PySide6 API 拼写错误：
`setFirstItemColumnSpanned` -> `QTreeWidgetItem.setFirstColumnSpanned(True)`。
不改变光源选择业务逻辑。
