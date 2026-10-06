# HF141 Validation

## 静态验证
- `python -m compileall -q .`：通过
- HF140 → HF141 AST 对比：修改文件中原有函数/类/类方法删除数 = 0

## 数据域自动化验证（隔离临时目录）
已用临时 SQLite / 临时 DG4 目录执行：

1. Administrator 新建方案 → owner=Administrator / visibility=private / status=draft
2. 其他用户不可见私人方案
3. 所有者共享 → owner 不变 / visibility=organization
4. 其他用户可见共享方案，但修改原方案被拒绝
5. 其他用户“复制到我的方案” → 新 owner / private / draft
6. 管理员发布 → owner 不变 / organization / published
7. 转移所有者 → 内容、共享和发布状态保留，owner 改变
8. 可读镜像随状态移动到“用户方案 / 共享方案 / 已发布方案”对应目录
9. 正式色库新发布 → customer 保持业务归属，created_by/steward 写入当前管理员
10. 更换正式色库数据管理员 → steward 改变，customer 不变

## HF140 → HF141 迁移验证
模拟 HF140 `color_cards` 旧表：

- owner_user_id=0 → `visibility=legacy_unassigned`
- owner_user_id>0 → `visibility=private`
- 旧方案内容、ID、布局不改变

## 仍需 Windows 实机验证
当前生成环境没有 Windows + PySide6 GUI 运行时，因此以下项目请在用户机器验证：

- 色卡方案管理右键菜单与显示列
- 共享后另一账号的可见性
- 发布/取消发布与本地目录镜像
- 历史未归属方案“转移所有者”
- 本地 QTX/CPX/Excel 打开后 Ctrl+S 是否进入个人工作文件，而非自动进入正式色库
- 已有正式色库 QTX Ctrl+S 是否仍更新原正式资产
- 客户 QTX 管理 → 数据管理员显示与更换
