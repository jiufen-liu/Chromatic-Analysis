# Hotfix125 · Targeted Regression Fixes

基线：Hotfix124 Frozen Performance Restore。只修复已复现回归，不更换冻结性能架构。

## 修复

1. 新建比色工作台名称变成 `False`：Qt `clicked(bool)` 被错误当作 `name` 参数。按钮连接改为显式丢弃 checked，并在 `create_workbench()` 增加 bool 防护。
2. 比色工作台 / 色卡编排【添加色样】打开迟钝：恢复 P3 轻量索引原则。选择器打开时只读取 `sample_index()` 标量元数据，只创建客户/QTX节点；色样叶节点展开时才创建，完整 Sample/光谱仅在用户确认后按所选 key 批量读取。取消启动时全库 `load_index_samples_by_keys()`、逐源 `Path.resolve()`。
3. 色卡 PNG/JPG 导出失败：取消 3500 色单张超高图片。PNG/JPG 与 PDF 一样尊重“每页行数”；大色卡自动输出 `*_01.png`, `*_02.png`…，并限制单页像素数，避免 Qt 图像编码器内存/尺寸失败。

## 不变

- Hotfix124 色库首次打开与色卡 Model/View 冻结性能路径不回退。
- QTX/CPX/Excel、色彩科学公式、排序算法、RBAC、正式色库权限不修改。
