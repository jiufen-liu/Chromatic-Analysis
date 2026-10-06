# Hotfix98 · 色卡编排 LABC Reset Baseline

## 本版目的

色卡编排停止叠加旧排序算法，重新建立干净基线。

用户可见的排序仅保留：

- L* 明度
- a* 红绿轴
- b* 黄蓝轴
- C* 彩度

从色卡编排界面删除：

- Munsell H 色相排序
- 色貌连续排序
- 经典光谱+感知连续排序
- 色类分组排序
- 名称排序

注意：Munsell/光谱等底层能力在软件其他业务（例如详情、色库兼容）中未强行物理删除，以避免破坏现有非色卡功能；本版只切断色卡编排系统的这些排序入口和命令调用。

## 外部色序系统调研结论

### Digital Munsell Color Atlas

不是把全部颜色压成一条“最短路径”。其 Hue 视图先固定一个 Hue，再把 Value 放在行、Chroma 放在列；另有固定 Value 或固定 Chroma 的横截面视图。Neutral 单独以 N 表示。

### Coloro

Coloro 的编码以 Hue、Lightness、Chroma 三个维度构成；官方实体 Codebook/Workbook 页面按 hue 组织，并提供 pastels / brights / darks 等分段分析。

### Pantone

Pantone 官方描述其多种指南为 chromatically arranged，数字产品也以 chromatically organized grid 连续浏览 color families。官方公开资料没有给出可复刻的精确排序算法，因此本项目不假造“Pantone 算法”。

## 对下一代色卡编排的启发

三者共同的产品思路更接近：

1. 先建立颜色家族 / hue page；
2. 再在家族内部用明度和综合色强度组织二维位置；
3. Neutral / gray 需要单独、稳定地处理；
4. 不需要为了数学距离把不同颜色家族强行串成一条路径。

下一版新算法应基于现有 D65/10° Lab 数据重新设计，不再依赖 Munsell renotation 作为排序核心。
