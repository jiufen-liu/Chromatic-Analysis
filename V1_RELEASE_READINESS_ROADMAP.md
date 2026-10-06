# Chromatic Analysis v1.0 落地路线（HF140 后）

HF140 完成后冻结 DG4 目录与存储架构，进入 v1.0 Release Readiness。

## Gate 1 · Clean Machine

- Windows 11 干净电脑安装 / 启动；
- 用户无需安装 Python / PyCharm；
- 依赖、字体、Qt 插件、3D 运行时完整；
- 首次启动初始化、管理员创建、数据目录权限正确。

## Gate 2 · Upgrade / Migration

- HF138 / HF139 数据升级到 DG4；
- 升级不覆盖用户数据；
- 数据库 schema 有版本与可回滚迁移；
- 旧 Customers / 旧工作区兼容路径有明确退役策略。

## Gate 3 · Core Regression

- QTX / CPX / Excel 导入导出；
- 正式 / 官方色库；
- 查色；
- 比色工作台；
- 色卡编排；
- 555 / MI / 色差；
- 3D / 光谱；
- RBAC；
- MDI 多窗口、拖放、最小化恢复。

## Gate 4 · Performance Red Lines

保持 HF124 / P3 冻结基线，不允许新版本明显回退：

- Coloro 3500 打开与分页；
- QTX 3500 导入；
- 色卡虚拟化；
- 光源转换缓存；
- 查色索引 / 最终 top-N 光谱加载。

## Gate 5 · Backup / Recovery

分别验证：

- 色彩数据备份 / 恢复；
- 用户与权限备份 / 恢复；
- 审计归档；
- 完整系统备份 / 恢复；
- 数据包跨电脑迁移；
- 恢复色彩数据不得回滚账号权限，反之亦然。

## Gate 6 · Release Package

- 正式安装包；
- 管理员手册；
- 用户快速指南；
- 数据目录 / 备份说明；
- 升级说明；
- 故障恢复说明；
- Release Candidate 全量验收。

v1.0 Stable 后再进入 Color Analysis Dashboard，以及 Color Journey / Matching / Performance / Harmony。
