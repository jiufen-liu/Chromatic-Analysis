# Hotfix63 · Release Readiness R1

## 目标
把 Hotfix62 从“开发目录可运行”推进到“可以开始生成独立 Windows EXE”的发布工程状态。R1 不新增颜色业务功能。

## 本次修改
- 修复通用启动脚本仍引用已不存在 `START_HF37.bat` 的问题。
- 统一发布版本信息到 Hotfix63 / R1，并增加运行时产品版本。
- 增加未处理异常日志：写入应用 AppData `logs`，不写程序安装目录。
- 增加独立的 PyInstaller 构建依赖和 spec；显式包含 3D QML 资源。
- 增加构建前静态检查、pytest 门禁和干净电脑测试清单。

## 冻结边界
未修改：
- `qtx_app/main_window.py`
- `qtx_core/qtx_parser.py`
- `qtx_core/colorimetry.py`
- `qtx_core/shade_sort.py`
- `qtx_app/library_store.py`
- `qtx_app/excel_exchange.py`
- 3D 渲染实现与 QML 内容
- DG/RBAC/备份恢复/审计业务逻辑

## 重要说明
R1 只生成 one-folder EXE 候选。通过干净电脑测试后，再进入 R2：正式安装器（安装路径、桌面快捷方式、开始菜单、卸载、升级与数据保留策略）。
