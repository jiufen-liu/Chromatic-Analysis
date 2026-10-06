# Hotfix122 · Performance Final

目标：结束这一轮 Performance / Release Readiness 性能收口，不改色彩公式、不改业务流程，只修复已经被 HF121 自动报告明确定位的性能瓶颈。

## 1. 色卡导入：源路径只规范化一次

HF121 中同一个 QTX 的数千个色样会在 `sample_key()` / 重复 GUID 处理过程中反复执行 `Path.resolve()`。对于 UNC / 网络共享路径，这会把本来只是生成键值的工作放大成数千次路径规范化。

HF122 新增独立的源路径缓存：

- 每个不同的 `source_file` 只规范化一次；
- 每个色样仍然保持原来的 `source | sample_id` 键语义；
- 重复 GUID 的 `~@N` 规则不变；
- 不改变 QTX / CPX / Excel 数据本身。

## 2. 光谱 / 光谱+感知排序：同波长网格向量化

历史 `spectral_order()` 每走一步都会对剩余候选逐个调用光谱插值与 RMS 计算。1000 色约需 26~28 秒。

HF122 保持原排序定义：

- 起点仍使用原来的 hue key；
- 光谱排序仍是最近邻 spectral RMS；
- 光谱+感知仍是 `spectral RMS + 0.18 × CIEDE2000`；
- 同分时仍以原 hue key 做确定性 tie-break；
- 缺失光谱仍放到末尾。

仅在所有样本已经位于完全相同的波长网格时启用 NumPy 向量快路径；混合网格 / 不规则波长自动回退历史实现。因此这是一条性能快路径，不是新排序算法。

## 3. 一键性能测试不再用 tracemalloc 干扰计时

HF121 在整个性能回归期间开启 `tracemalloc`，会显著放大 Python 对象创建/解析耗时。HF122 改为：

- 不在关键计时段启用 tracemalloc；
- Windows 使用系统进程 Working Set / Peak Working Set；
- 文件读取、QTX 核心解析、源路径绑定分开计时；
- QTX 核心解析继续显示 block / sample prepare / colour science 三段；
- 色卡准备拆成 key/path、完整测量快照、版位准备三段。

这样网络共享读取慢不会再被误判成 QTX 算法本身慢。

## 4. P3-6 基线回归

当输入为 Coloro 3500（3500 色）时，[23] 会额外显示项目历史 P3-6 参考：

- 671.73 ms / 1000 色

并输出当前 / 基线倍率、变化百分比和 PASS / WARN / FAIL。

## 5. 验证方式

日常只需要：

`RUN_DIAGNOSTICS.bat -> [23] -> Coloro 3500.qtx`

无需逐个打开软件功能。

最终阶段冻结前，再人工执行一次 [22] 全程序交互回归即可。
