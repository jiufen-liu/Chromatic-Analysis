# Clean Machine Test · Release Readiness R1

R1 只是“可执行程序打包基础”，不是最终安装包。Windows 构建完成后必须在一台没有 Python / PyCharm 的电脑上验证。

## 必测
1. `dist\ChromaticAnalysis\ChromaticAnalysis.exe` 可直接启动。
2. 首次启动可创建组织、管理员和恢复密钥。
3. AppData 中自动创建权限库、色库、managed_data、日志目录；程序目录保持只读也能运行。
4. QTX / CPX / Excel：打开、拖入、导出各一次。
5. 比色工作台、查色、色库、色卡编排各完成一次核心流程。
6. 3D：打开、旋转、平移、缩放；确认 QML / QtQuick3D 未缺失。
7. 管理员：本地数据管理、备份、恢复入口、`.cadata` 导出/导入、审计 Excel。
8. 普通使用者：权限、正式色库只读、个人工作区隔离。
9. DPI：1366×768 @100%、1920×1080 @125% 至少各测一次。
10. 人为制造一个测试异常，确认 AppData `logs` 下生成 crash 日志。

## 通过标准
- 无缺 DLL / Qt plugin / QML module 报错。
- 不要求安装 Python、PyCharm 或手动 pip。
- 重启后数据和权限保持一致。
- 旧 Hotfix62 核心业务行为无回归。
