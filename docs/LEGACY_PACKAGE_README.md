# Chromatic Analysis v0.14.6.4 · Hotfix72 · Release Readiness R1 · Build Info Compatibility Fix

修复 HF70/HF71 中 `qtx_app/build_info.py` 版本字段回归：恢复 `PRODUCT_NAME` / `PRODUCT_VERSION`，并保留 `APP_VERSION` 兼容别名。此修复只解决启动 ImportError，不改变 HF71 的 Munsell 缓存复用与任何颜色业务逻辑。

开发运行：`START_RELEASE_R1_HF72.bat`。诊断：`RUN_DIAGNOSTICS.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix71 · Release Readiness R1 · Munsell Cache Reuse

本版在 HF70 科学排序修正基础上，只增加当前会话内的精确 Munsell 结果缓存复用：首次排序仍做完整 C/2° Munsell 计算；同一批未变化色样再次正/逆序时直接复用结果，避免重复 renotation。

开发运行：`START_RELEASE_R1_HF71.bat`。诊断：`RUN_DIAGNOSTICS.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix70 · Release Readiness R1 · Munsell Science Correction

本版依据 HF69 排序科学审计结果，修正 Munsell 近中性色混合量纲与 fallback 混入主色相序列的问题；不进入性能优化阶段。

开发运行：`START_RELEASE_R1_HF70.bat`。诊断：`RUN_DIAGNOSTICS.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix69 · Release Readiness R1 · Sort Science Audit

本版按“先确认排序科学/逻辑定义，再做性能优化”的顺序推进。正式色卡排序算法暂不改变；诊断中心新增“色卡排序科学 / 逻辑验证”，会生成带色块的 Excel，逐样本检查 C*、Munsell、光谱相似度和光谱+感知路径，同时标出 Munsell fallback、中性色预判差异和最大路径跳跃。

诊断：`RUN_DIAGNOSTICS.bat` → `4. 色卡排序科学 / 逻辑验证`。开发运行：`START_RELEASE_R1_HF69.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix68 · Release Readiness R1 · Integrated Diagnostics

本版不改颜色业务逻辑。把 Performance P3 阶段的诊断方式正式内置到项目包：新增 `RUN_DIAGNOSTICS.bat`、系统诊断、色卡排序专项基准以及诊断资料一键打包。以后遇到性能、崩溃或机器差异，不再临时另发测试程序。

开发运行仍使用 `START_RELEASE_R1_HF67.bat`；诊断使用 `RUN_DIAGNOSTICS.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix67 · Release Readiness R1

- 3D 点大小滑块 0.1 细分，保持原 2–10 视觉范围。
- 色卡 300+ 色样的光谱/Munsell 重排序移出 GUI 线程，并复用现有格子控件，降低卡顿与 Qt 生命周期崩溃风险。
- 颜色科学、QTX/CPX、555、色库核心均未改动。

# Chromatic Analysis v0.14.6.4 · Hotfix66 · Release Readiness R1 · Palette Direct Drop

本版继续以 Hotfix62/65 冻结基线为基础，只处理色卡编排交互：去除重复的“方案管理 / 搜索”入口，并支持将 QTX / CPX / Excel 直接拖入色卡编排或当前色卡方案。新建草稿通过拖入文件后仍保持“未保存”，只有 Ctrl+S 才进入已有方案。

开发运行：`START_RELEASE_R1_HF66.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix65 · Release Readiness R1 · Qt Lifecycle Guard

基于已验证的 Hotfix63/R1，只修复三项实机交互问题，不改颜色科学、QTX Parser、色库核心、Excel 业务交换逻辑：

- 正式色库与比色工作台同时打开时，不再自动压成 70/30；恢复为可自由移动、重叠、最大化/最小化的独立 MDI 窗口，并给成熟页面保留可用最小尺寸。
- Qt Quick 3D 的“点大小”滑块将 pageStep 从默认 10 修正为 1；点击滑槽不再直接跳到最小/最大，拖动行为保持不变。
- 新建色卡方案改为“未保存草稿”：只有 Ctrl+S/保存后才进入已有方案；直接关闭不会留下空方案。已有方案菜单只显示最近 6 个，并在方案管理器增加搜索。

开发运行：`START_RELEASE_R1_HF65.bat`。Windows EXE 构建仍使用：`BUILD_WINDOWS_EXE.bat`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix63 · Release Readiness R1

本版不新增颜色业务功能。目标是把已验证的 Hotfix62 推进到 Windows 独立 EXE 的发布工程阶段：修正启动入口、统一版本信息、增加崩溃日志、提供 PyInstaller 构建脚本，并建立干净电脑测试门禁。

开发运行：`START_RELEASE_R1_HF63.bat`。Windows EXE 构建：`BUILD_WINDOWS_EXE.bat`。详细范围见 `RELEASE_READINESS_R1.md`。

---

# Chromatic Analysis v0.14.6.4 · Hotfix47 · Performance P3-3

本版继续 Performance P3 主线：正式/官方色库默认 D65/10° 浏览只读取 SQLite 轻量索引；光谱、完整 raw/XYZ 仅在查看明细、查色、比色、导出或非 D65/10° 计算真正需要时按 sample_key 加载。完整 payload 保持不变，是唯一完整数据源。

启动建议：`START_P3_3_HF47.bat`。详细范围见 `PERFORMANCE_P3_3_HOTFIX47_SCOPE.txt`。

---

# Chromatic Analysis v0.14.0 · Stage 1 Hotfix 1

这是 Stage 1 的实机修复包。针对首次测试中发现的 `QFontMetrics` 报错、左上角 10° 漂浮控件、工作区文件夹只显示文件名、色库入口不明显等问题进行修复。没有进入 Stage 2。

请优先按 `TEST_CHECKLIST_STAGE1_HOTFIX1.md` 回归测试；通过后再继续第一阶段视觉打磨或进入 Stage 2。

---

# Chromatic Analysis v0.14.0 · Stage 1 UI Foundation

本版基于 v0.13.1，只进行第一阶段 UI 基础优化：登录账号下拉、主工作台轻量化、隐藏传统常驻菜单、统一高级中性 Design System、底部角色/时间状态栏。颜色计算、QTX/CPX/Excel、555、3D、正式色库、比色、查色、Attributes 等业务逻辑保持原有实现。

请先阅读 `TEST_CHECKLIST_STAGE1.md` 并完成回归测试；全部通过后再进入 Stage 2。

---

# Chromatic Analysis v0.13.1 · Color Studio Polish

这是 v0.13.0 Unified Workspace 的视觉与交互精修版。底层 QTX / CPX / Excel、测色、色差、正式色库、权限、Attributes、Datacolor 555 等逻辑继续沿用；本版重点把界面进一步做轻，改成更像专业色彩软件的“画布 + 文档 + 键鼠操作”方式。

## 本版重点

- 主工具栏只保留品牌、一个“＋打开”入口和当前角色；光源/观察者不再常驻显示。
- 查色、比色、色卡、光谱通过菜单和 `Ctrl+1/2/3/4` 调用，减少按钮堆叠。
- 电子色卡重新设计：更大的主色块、简洁白色信息区、轻边框、柔和层次、统一留白。
- 屏幕色块与 Excel 色卡使用一致的 sRGB 预览映射；超出 sRGB 色域时按固定明度/色相压缩色度，避免原先通道硬裁切导致的“荧光、发脏、死黑/死亮”观感。原始光谱和 Lab 数据不会被修改。
- 色卡编排完整支持 `Ctrl+A` 全选、`Ctrl+C/X/V`、`Ctrl+Z/Y`、`Delete`、`Esc`、`Ctrl+S`。
- 色卡编排和普通 QTX/CPX 文档均可 `Ctrl+Alt+3` 或右键打开 3D CIELAB 图。
- 3D 的参考点云只按“已保存客户”选择，不再弹出“全部色库 + 每个 QTX”的超长列表；选中某客户后才加载该客户数据。
- 色卡编排背景和工作区统一为中性色，不再出现右侧突兀的大块白色空区；已有格子位置仍保持固定，不因窗口缩放重新排版。

## 权限

首次启动创建管理者账号，以后统一登录。

- 使用者：查色、比色、打开文件、排序、555、查看明细、临时编排、3D 和导出；不能新增、修改或删除正式色库数据。
- 管理者：在使用者能力基础上维护正式色库、客户、用户账号与系统数据。

## 555

继续严格按 Datacolor CHECK II 555 Shade Sorting：555 仅对已经通过 acceptability tolerance 的 Batch 做分阶，不用于 Pass/Fail；支持 Lab 或 LCh 三维，每轴设置最小/最大容差，支持 3/5/7/9 箱。

## Windows 快捷键

- `Ctrl+O`：打开 QTX
- `Ctrl+Shift+P`：打开 CPX
- `Ctrl+Shift+O`：打开 Excel
- `Ctrl+1`：查色 / 找色
- `Ctrl+2`：比色工作台
- `Ctrl+3`：色卡编排
- `Ctrl+4`：光谱分析
- 文档/色卡内：`Ctrl+A/C/X/V/Z/Y/S`、`Delete`、`Esc`
- `Ctrl+Alt+3`：查看当前选择（无选择则当前文档/色卡）的 3D CIELAB 图

## 安装

建议解压到较短路径，例如 `F:\ChromaticAnalysis_v0131`，安装 `requirements.txt` 后运行 `main.py`。也可以直接用 PyCharm 打开目录并运行 `main.py`。

详细验证步骤见 `VALIDATION_v0.13.1.md`。
