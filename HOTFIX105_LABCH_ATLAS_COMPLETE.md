# Hotfix105 · LABCH Hue 修正 + Color Difference 验证 + LABC Atlas 完整横截面

## 本次目标

只处理用户本轮明确指出的三件事，不改动已验证业务模块：

1. LABCH 中的 `h° 色相` 视觉排序不合理；
2. `Color Difference` 需要更容易验证，并确保选中的基准色一定排第一；
3. LABC Atlas 的 `明度` / `彩度` 两个页签从占位状态补齐为可交互横截面。

`Running ΔE` 用户反馈当前效果可以，本版不改算法。

## 1. h° 色相排序

旧版直接按 raw h° 0–360 排序。低彩度灰/黑虽然数学上存在 h°，但色相不稳定，会被散落到红/绿/蓝/紫之间；同时 0°/360° 会把 Red 家族切开。

Hotfix105：

- Neutral / 近 Neutral 先形成一个连续块；
- Neutral 内按 L* 浅→深；
- 色相环从 Red 家族边界 345° 起步，避免红色在首尾被拆开；
- chromatic 颜色按 5° hue slice 前进；
- 同一 5° slice 内再用 L*、C* 做局部整理；
- 相同输入完全确定。

Adidas 309 色验证：同一 Neutral 判定下，旧 raw h° 会把 67 个 Neutral 分散成 34 个区段；新排序为 1 个连续区段。

## 2. Color Difference

Color Difference 继续保持它应有的语义：**相对于一个明确基准色的 ΔE 由近到远**。

修正：

- 用户明确选中的 reference 无条件排在第一个 occupied slot；
- 即使有另一个重复测量也恰好 ΔE=0，也不能抢到 reference 前面；
- 其余颜色严格按当前色差公式的 reference ΔE 升序；
- 排序后每张卡 Tooltip 增加 `基准色差 = ...` 和 `基准色：...`，可以直接验证数值是否单调；
- 不把 Running ΔE 的局部路径逻辑混进 Color Difference。

## 3. Running ΔE

算法保持 Hotfix104：

- 选中 1 个起点；
- 每一步从剩余颜色中找当前 ΔE 最近邻；
- 后台线程执行；
- >1500 色继续提示先筛选/拆分。

## 4. LABC Atlas · 明度页

参考 Digital Munsell Atlas 的 Value cross-section 思想，但底层仍是 D65/10° CIELAB：

- 固定一个 L* 页面；
- 横轴：h° 色相，使用 `N + 24 个 15° hue columns`；
- 纵轴：C*，0→120；
- 每个真实色样只进入离它最近的一个 L* 页面；
- cell 仍显示 `• / ×N`，不展开重复占位；
- 点击 cell 可逐个查看真实色样。

## 5. LABC Atlas · 彩度页

参考 Munsell Chroma cross-section 思想：

- 固定一个 C* 页面；
- 横轴：h° 色相，`N + 24 个 15° hue columns`；
- 纵轴：L*，90→10；
- 每个真实色样只进入离它最近的一个 C* 页面；
- cell 保留 `• / ×N`；
- 点击后右侧仍显示真实 Lab/LCh、相似色、原色卡定位。

## 6. 非破坏性范围

本版没有主动改动：

- QTX / CPX / Excel 导入导出；
- 查色、比色、555；
- 3D、光谱；
- 色库、RBAC、个人工作区 / 正式色库；
- 色卡拖拽、复制、剪切、空卡位、保存、另存为；
- Running ΔE 算法；
- L* / a* / b* / C* 基础排序。
