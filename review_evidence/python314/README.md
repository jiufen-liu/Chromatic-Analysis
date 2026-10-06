# Python 3.14 验证

环境：CPython 3.14.7，Linux x86_64，Qt offscreen，标准 GIL 构建。

- 安装：`uv pip install --python /workspace/chromatic-venv314/bin/python -r requirements.txt`，安装 14 个包。
- 依赖检查：`uv pip check --python /workspace/chromatic-venv314/bin/python`，无冲突。
- 完整测试：`python -m pytest --tb=short`，173 passed，6 warnings，exit 0。
- 实际 UI：`python tools/review_smoke.py`，主窗口、QTX 解析、SQLite 保存/加载、正式库切换、D65/U30 条件提示和三库完整性检查通过，exit 0。

测试警告是光源光谱采样对齐提示。启动提示 Matplotlib 未安装（colour-science 可选绘图接口不可用）及 offscreen 插件不支持 propagateSizeHints；均未影响本次验证。

截图与原始输出见本目录。Windows Python 3.14 自动测试已配置，当前云端未执行 Windows 实机检查；EXE 打包及自由线程 Python 未验证。
