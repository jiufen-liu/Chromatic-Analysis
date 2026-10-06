# Hotfix100 · LABC Atlas V2 · Opponent Hue Slice

## 本次新增

在 Hotfix99 的 LABC Atlas `Hue` 页中增加第二种浏览模式：

- **单 Hue 页**：保留原 V1，纵向 L*，横向 C*。
- **对置 Hue 剖面**：当前 Hue 在左，Neutral 在中间，对向 Hue 在右；纵向仍为 L*。

例如：

- Blue ← Neutral → Yellow
- Red ← Neutral → Green
- Green ← Neutral → Red-Violet（按当前 LABC Hue family 中心自动选择最接近 180° 的 family）

## 色彩逻辑

对置剖面直接使用 D65/10° CIELAB `a* / b*` 平面，不做 Munsell 换算：

- 选中 Hue family 的中心角作为轴 `h0`；
- 样品投影到该 Hue ↔ Opposite Hue 直径；
- 当前 Hue 方向显示在左侧，对向 Hue 在右侧；
- Neutral 样品固定在中心；
- 与该轴偏离过大的综合色相不强行塞入剖面，因此允许真实空位。

## UI

- Hue 页新增两个小视图按钮：`单 Hue 页` / `对置 Hue 剖面`。
- 右侧新增综合色相圆盘，显示当前 Hue ↔ 对向 Hue 的直径，可点击综合色相圆切换剖面。
- 对置模式顶部改为 `Selected Hue ← Neutral → Opposite Hue`。
- 当前色样、相似色、已选色样、定位原色卡等 Hotfix99 功能全部保留。

## 兼容性

本次仅修改：

- `qtx_core/labc_atlas.py`
- `qtx_app/labc_atlas_window.py`
- LABC Atlas 按钮提示文本

没有修改 QTX/CPX/Excel、色库、比色、查色、555、3D、光谱、RBAC、保存/导出、原色卡卡位及 L*/a*/b*/C* 排序逻辑。
