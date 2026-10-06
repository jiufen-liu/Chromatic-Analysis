# Hotfix104 · LABCH + Color Difference + Running ΔE + LABC Atlas

## 本次目标

按照已确认的四块结构重新整理色卡编排的专业排序入口：

1. **LABCH 排序**：L* / a* / b* / C* / h° 放在同一组。
2. **Color Difference 色差**：以当前唯一选中的色样为基准，按当前色差公式由近到远。
3. **Running ΔE 连续色差**：以当前唯一选中的色样为起点，每一步寻找剩余色样中 ΔE 最小的颜色。
4. **LABC Atlas**：继续作为独立的专业图谱 / 色卡组织系统，不新增另一套 Munsell 排序。

## UI

色卡顶部“排序”菜单：

- LABCH 排序
  - L* 明度
  - a* 红绿轴
  - b* 黄蓝轴
  - C* 彩度
  - h° 色相角
- Color Difference · 色差
- Running ΔE · 连续色差
- 反向当前顺序

独立按钮：

- LABC Atlas · 专业图谱

右键“整理方案”同步提供相同入口，并可直接打开 LABC Atlas。

## Color Difference

- 必须先且只选中 1 张色样。
- 该色作为参考色。
- 使用主程序当前色差公式：CIEDE2000 / CIE94 / ΔE*ab / CMC(2:1)。
- 参考色自身 ΔE=0，因此自然排在第一位。
- 同 ΔE 时使用稳定 sample key 作为确定性 tie-break。

## Running ΔE

- 必须先且只选中 1 张色样作为起始色。
- 每一步以“当前色”为参考，在剩余色样里寻找当前色差公式下 ΔE 最小者。
- 同 ΔE 时优先保持原色卡顺序，再以 sample key 决定，保证确定性。
- 使用后台 card-sort executor，避免正常色卡计算时阻塞 UI。
- 第一阶段对超过 1500 色的单次 Running ΔE 给出明确提示，避免 O(N²) 最近邻计算拖垮界面；LABCH 与 LABC Atlas 无此限制。

## LABCH h°

- 使用标准 CIELAB 极坐标色相角：`atan2(b*, a*) mod 360°`。
- h° 是纯坐标排序，不做 Neutral / 颜色家族 / 人工色貌修正。
- 第一次点击 h° 默认 0° → 360°；再次点击反向。

## 非破坏性约束

本 Hotfix 不修改：

- QTX / CPX / Excel 导入导出
- 查色 / 比色 / 555
- 3D / 光谱
- 色库 / RBAC
- 原有保存、另存为、撤销、重做、空卡位、固定列数、拖拽和复制移动
- LABC Atlas 已有单 Hue / 对置剖面 / 细分色相环 / 验证模式

## 设计原则

- LABCH = 坐标排序。
- Color Difference = 相对某一个基准色的远近。
- Running ΔE = 相邻颜色之间的连续路径。
- LABC Atlas = 借鉴专业色彩图谱组织方式的浏览与色卡组织系统。

四者职责明确，互不混用。
