# v0.13.0 架构说明

## 1. 三层概念

### 工作区（Workspace）
日常临时数据层。打开 QTX/CPX/Excel、测色/比色、排序、555、色卡编排都先发生在工作区。普通打开文件不会改变正式色库。

### 正式色库（Formal Library）
企业正式颜色数据层。只有 Administrator 能新增、修改、删除。Operator 可以读取用于查色/比色，但不能写入。

### 客户文件（Customer Files）
正式保存时同步建立 Windows 可浏览的客户数据镜像。用户不需要只依赖数据库才能找到自己的正式数据。

## 2. 权限模型

角色：
- operator
- admin

权限原则：
- 读取正式色库：operator/admin
- 工作区临时操作：operator/admin
- 导出副本：operator/admin
- 正式色库新增/修改/删除：admin only
- 客户与用户管理：admin only

UI 隐藏不是唯一防线；正式写动作执行前仍再次做管理员校验。

## 3. 文档模型

统一工作台可以同时存在：
- QTX document
- CPX document
- Excel document
- Workspace folder
- Legacy tool tab（查色 / 比色 / 色卡 / 光谱 / 正式色库管理）

各文档不因另一个文档切换标准、排序或布局而自动互相修改。

## 4. 电子色卡

电子卡片遵循：
- 约 70% 大色块；
- 下方白色信息区；
- 正面只显示名称和少量 Lab；
- 详细数据通过测色明细查看；
- 固定逻辑行列；
- 应用窗口变化不触发 reflow。

## 5. 数据完整性

视觉优化不改变：
- 原始反射率；
- XYZ / Lab / LCh 数值；
- 仪器条件；
- Attributes；
- QTX/CPX 源字段。

显示色块只作为屏幕预览。
