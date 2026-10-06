# Chromatic Analysis

Python / PySide6 色彩分析桌面软件，当前为开发源码版本（第二轮整改）。源码已展开在仓库根目录，入口为 `main.py`。

## 下载

GitHub 仓库选择 **main → Code → Download ZIP**，解压后在包含 `main.py` 的目录运行。仓库中的 `Chromatic_Analysis_development_r2.zip` 也是第二轮整改源码快照，不是安装包。

## Windows 开发运行

建议 Python 3.12。在 PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

首次启动按正常流程创建管理员/登录。首次测试请使用测试数据，并备份已有业务数据。

## 回归验证

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Linux 无桌面环境可使用 `QT_QPA_PLATFORM=offscreen` 执行 UI 测试。`requirements-review-lock.txt` 记录 Python 3.12/Linux 验证版本；跨平台开发按 `requirements.txt` 安装。

## 本次整改

- 修复权限异常放开访问、客户 Excel 内部属性泄露、QTX/CPX 数据完整性及条件误标。
- 修复更新测量后缓存仍返回旧值、切换光源时轻量色样误回退。
- 增加原子 JSON 写入，保护工作文件与可读镜像。
- 统一预览转换，显示光源、观察者、兼容光谱及回退提示。
- 保留计算来源和条件信息；完善测试与依赖。

当前自动回归：173 项通过；Linux/offscreen 实际窗口、D65/U30 切换及数据库读写已验证。Windows 显示缩放、真实仪器、ICC 和生产数据迁移尚需专项验证。

详见 [第二轮开发审查](DEVELOPMENT_REVIEW_ZH.md)、[第一轮项目审查](PROJECT_REVIEW_ZH.md) 和 [验证证据](review_evidence/round2/)。报告中的 `review_working/` 是整改期间的历史工作目录；GitHub 下载后的当前源码位置为仓库根目录。

原始 HF142 ZIP 保留。历史包 README 与启动说明见 [历史文档](docs/LEGACY_PACKAGE_README.md)；当前开发运行以本 README 为准。
