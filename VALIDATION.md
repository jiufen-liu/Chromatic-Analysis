# Datacolor 对照验证

## 对照文件

- `Col.9 FH0092.qtx`：35点反射率，360–700 nm，10 nm间隔
- Datacolor导出：D65、A、F02，10°观察者

## 采用方法

- 观察者：CIE 1964 10°
- 光谱积分：ASTM E308 selected-wavelength method
- 白度：CIE Whiteness，D65/10°
- Tint：CIE Tint，D65/10°，x项系数900
- 同色异谱：D65参考光源，XYZ Multiplicative Correction，最终使用 CIELAB ΔE*ab

## 关键结果

| 项目 | Datacolor | 本核心 | 判定 |
|---|---:|---:|---|
| FH0092 D65 L* | 22.77 | 22.767 | 通过 |
| FH0092 D65 a* | -1.53 | -1.530 | 通过 |
| FH0092 D65 b* | -1.07 | -1.072 | 通过 |
| FH0092 CIE WI | 17.64 | 17.633 | 通过 |
| FH0092 CIE Tint | 8.21 | 8.201 | 通过 |
| FH0051 MI A | 0.72 | 0.719 | 通过 |
| FH0051 MI F02 | 0.60 | 0.596 | 通过 |
| ZFH0144 MI A | 0.50 | 0.495 | 通过 |
| ZFH0144 MI F02 | 0.47 | 0.468 | 通过 |

测试还覆盖：

- 三个样本的D65白度/Tint
- 三个样本在A、F02下的XYZ/Lab
- D65、A、F02下的CIEDE2000与CMC(2:1)
- A、D50、D55、D65、D75及F01–F12全部可计算
- 光源别名 `CWF→F02`、`TL84→F11`
- 无光谱、空平均及参考光源MI规则

自动测试共42项，全部通过。

## v0.6 MPC数据交换验证

- 实际MPC数据Excel识别80个具有完整35点光谱的色样。
- 识别到 `4PMOR195NR-2` 仅有部分表记录、缺少反射率，并明确提示。
- 识别到Rank `4-2`被两条色样占用，两条均保留待人工处理。
- 英文排版Excel识别80个排版条目。
- 客户Excel导出支持空位，且不会输出配方、纱线成本或配方成本字段。
- 新增自动测试覆盖MPC三表关联和客户导出内部字段隔离。

当前执行环境未安装PySide6/colour-science，因此本轮完成了Python语法检查和Excel读写实测；
Windows界面启动、工作台收起重启与数据库恢复需要按README清单在目标电脑完成验收。

## PySide6界面验证

已完成离屏启动测试，并实际载入测试QTX验证：

- 自动识别3条样本及QTX标准
- D65下FH0051显示 L*=19.05、WI=10.05、CIE94=2.69
- 切换A光源后显示 L*=19.07、MI=0.72
- 多光源窗口显示FH0051的A MI=0.72、F02 MI=0.60
- 主窗口、表格模型、标准卡片和二维图均可创建并刷新

## 尚待后续实测确认

F01、F11等光源已使用标准光源数据实现并通过通用计算测试，但当前用户对照Excel只包含A和F02。因此F01、F11与Datacolor的逐项显示舍入差异，应在获得对应Datacolor导出后继续加入回归测试。

## v0.9.2 validation
- `python -m compileall -q .` passed.
- Full pytest suite could not run in the build container because the optional runtime dependency `colour-science` is not installed here.
- Recommended manual smoke tests: customer deletion guard, find-page empty state, find illuminant re-query, 3D shortcuts/auto-rotate, library->card transfer, workbench alternate illuminants and MI rows.
