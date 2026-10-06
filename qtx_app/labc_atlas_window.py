"""LABC Atlas V6 UI (Hotfix105).

Additive, read-only atlas browser over the current Palette Studio samples.
Hotfix103 keeps the Hotfix102 view-sync fixes and adds Chinese UI copy, explicit multi-sample cell badges, and a fine-grained interactive hue ring for the opponent slice:

    selected hue  <-  Neutral  ->  opposite hue
                     L* vertical

The slice is computed directly from measured CIELAB a*/b* geometry.  It does
not use Munsell conversion and does not mutate palette slots, files, libraries,
permissions, or any existing sort command.
"""
from __future__ import annotations

from math import atan2, cos, degrees, hypot, radians, sin
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from qtx_core.labc_atlas import (
    C_CENTERS, FAMILY_HEX, FAMILY_KEYS, FAMILY_LABELS, HUE_FAMILIES, L_CENTERS, HUE_PLANE_CENTERS,
    SIGNED_C_CENTERS, build_hue_cells, build_opponent_cells, build_lightness_cells, build_chroma_cells, hue_plane_columns, family_center_angle, angular_distance,
    family_counts, family_for_lab, lab_to_lch, nearest_records,
    opposite_family_key, family_key_for_hue_angle, opposite_hue_angle,
    build_validation_report, validation_report_text,
)


# UI-only Chinese labels. Internal family keys and Lab/LCh calculations remain unchanged.
UI_FAMILY_LABELS = {
    "neutral": "中性",
    "red": "红",
    "red_orange": "红橙",
    "orange": "橙",
    "yellow": "黄",
    "yellow_green": "黄绿",
    "green": "绿",
    "cyan": "青",
    "blue": "蓝",
    "blue_violet": "蓝紫",
    "violet": "紫",
    "red_violet": "红紫",
}

def _ui_family_label(key: str) -> str:
    return UI_FAMILY_LABELS.get(str(key), FAMILY_LABELS.get(str(key), str(key)))


def _contrast_text(hex_color: str) -> str:
    color = QColor(hex_color)
    lum = 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
    return "#111827" if lum > 150 else "#FFFFFF"


class AtlasCellButton(QPushButton):
    activated = Signal(object)

    def __init__(self, records: Sequence[Mapping], parent=None, compact: bool = False):
        super().__init__(parent)
        self.records = list(records)
        self._index = 0
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(46 if compact else 62, 44 if compact else 50)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clicked.connect(self._cycle_activate)
        self.refresh()

    def _cycle_activate(self):
        if not self.records:
            return
        record = self.records[self._index % len(self.records)]
        self._index = (self._index + 1) % len(self.records)
        self.activated.emit(record)
        self.refresh()

    def refresh(self):
        if not self.records:
            self.setEnabled(False)
            self.setText("")
            self.setStyleSheet("QPushButton{background:#FBFCFE;border:1px solid #E4EAF1;border-radius:3px;}")
            return
        record = self.records[self._index % len(self.records)]
        bg = str(record.get("hex") or "#8894A5")
        count = len(self.records)
        self.setText("•" if count == 1 else f"×{count}")
        names = "\n".join(str(x.get("name", "")) for x in self.records[:12])
        self.setToolTip(f"此格共有 {count} 个真实色样；单击可逐个查看。\n{names}")
        self.setStyleSheet(
            "QPushButton{"
            f"background:{bg};color:{_contrast_text(bg)};border:1px solid #D5DCE6;"
            "border-radius:3px;font-weight:700;}"
            "QPushButton:hover{border:2px solid #1677FF;}"
        )


class MiniSwatch(QFrame):
    clicked = Signal(object)

    def __init__(self, record: Mapping, compact=False, parent=None):
        super().__init__(parent)
        self.record = record
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(92 if not compact else 72)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(4)
        chip = QFrame(self); chip.setFixedHeight(48 if not compact else 38)
        chip.setStyleSheet(f"QFrame{{background:{record.get('hex','#9AA4B2')};border:1px solid #D5DCE6;border-radius:5px;}}")
        lay.addWidget(chip)
        full_name = str(record.get("name", ""))
        name = QLabel(full_name, self); name.setWordWrap(True); name.setToolTip(full_name)
        name.setMaximumHeight(34 if compact else 40)
        name.setStyleSheet("color:#334155;font-size:10px;")
        lay.addWidget(name)
        if not compact:
            L,C,_ = lab_to_lch(record["lab"])
            meta=QLabel(f"L* {L:.1f}   C* {C:.1f}",self); meta.setStyleSheet("color:#64748B;font-size:9px;")
            lay.addWidget(meta)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.record)
        super().mousePressEvent(event)


class HueOppositionWheel(QWidget):
    """Interactive LABC hue-direction wheel used only in opponent-slice mode.

    The visual language follows the atlas reference supplied by the user:
    a fine segmented hue ring, outer family labels, inner tick marks, a neutral
    centre, and a selected<->opponent diameter.  It is a navigator/indicator,
    not a Munsell conversion.
    """
    familySelected = Signal(str)  # compatibility only
    hueAngleSelected = Signal(float)

    _LABELS = {
        "red": "红",
        "red_orange": "红橙",
        "orange": "橙",
        "yellow": "黄",
        "yellow_green": "黄绿",
        "green": "绿",
        "cyan": "青",
        "blue": "蓝",
        "blue_violet": "蓝紫",
        "violet": "紫",
        "red_violet": "红紫",
    }

    def __init__(self, family_key: str = "blue", parent=None):
        super().__init__(parent)
        self.family_key = family_key if family_key != "neutral" else "blue"
        self.hue_angle = family_center_angle(self.family_key)
        self.setMinimumSize(245, 235)
        self.setMinimumHeight(235)
        self.setMaximumHeight(250)
        self.setCursor(Qt.PointingHandCursor)

    def setFamily(self, key: str):
        if key != "neutral" and key in FAMILY_KEYS:
            self.family_key = key
            self.hue_angle = family_center_angle(key)
            self.update()

    def setHueAngle(self, hue_angle: float):
        self.hue_angle = float(hue_angle) % 360.0
        self.family_key = family_key_for_hue_angle(self.hue_angle)
        self.update()

    @staticmethod
    def _family_for_angle(angle: float):
        angle %= 360.0
        for fam in HUE_FAMILIES:
            start, end = fam.start % 360.0, fam.end % 360.0
            inside = (start <= angle < end) if start <= end else (angle >= start or angle < end)
            if inside:
                return fam
        return HUE_FAMILIES[0]

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Keep room around the ring for external family labels.
        side = min(self.width(), self.height()) - 74
        side = max(130.0, float(side))
        cx, cy = self.width()/2.0, self.height()/2.0 + 4.0
        rect = QRectF(cx-side/2, cy-side/2, side, side)

        # 72 interactive 5° sectors.  Every segment is clickable and changes
        # the exact CIELAB hue plane used by the opponent-slice view.
        sector_deg = 5.0
        selected_idx = int((self.hue_angle % 360.0) // sector_deg)
        for i in range(72):
            angle = i * sector_deg
            fam = self._family_for_angle(angle + sector_deg/2.0)
            p.setPen(QPen(QColor("#0F172A"), 2) if i == selected_idx else QPen(QColor("#FFFFFF"), 1))
            p.setBrush(QColor(fam.preview_hex))
            p.drawPie(rect, int(angle*16), int(sector_deg*16))

        # White inner disc.
        inner = rect.adjusted(side*0.245, side*0.245, -side*0.245, -side*0.245)
        p.setPen(QPen(QColor("#D8E0EA"), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(inner)

        # Inner radial tick marks.
        r_outer = side * 0.245
        for i in range(72):
            a = radians(i*5.0)
            r1 = r_outer - (8 if i % 6 == 0 else 4)
            r2 = r_outer - 1
            x1, y1 = cx + cos(a)*r1, cy - sin(a)*r1
            x2, y2 = cx + cos(a)*r2, cy - sin(a)*r2
            p.setPen(QPen(QColor("#334155"), 1))
            p.drawLine(QPointF(x1,y1), QPointF(x2,y2))

        # Selected <-> opponent diameter.
        h = radians(self.hue_angle)
        r = side * 0.41
        x1, y1 = cx + cos(h)*r, cy - sin(h)*r
        x2, y2 = cx - cos(h)*r, cy + sin(h)*r
        p.setPen(QPen(QColor("#172033"), 2))
        p.drawLine(QPointF(x1,y1), QPointF(x2,y2))

        # Endpoint markers.
        op_angle = opposite_hue_angle(self.hue_angle)
        op = family_key_for_hue_angle(op_angle)
        selected_family = family_key_for_hue_angle(self.hue_angle)
        for x,y,color in ((x1,y1,FAMILY_HEX[selected_family]),(x2,y2,FAMILY_HEX[op])):
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(x,y),7,7)

        # Family labels outside the ring, matching the reference-wheel role.
        label_r = side*0.62
        p.setPen(QColor("#24364F"))
        for fam in HUE_FAMILIES:
            a = radians(family_center_angle(fam.key))
            lx, ly = cx + cos(a)*label_r, cy - sin(a)*label_r
            text = self._LABELS.get(fam.key, fam.label[:2])
            box = QRectF(lx-18, ly-11, 36, 22)
            if fam.key in (self.family_key, op):
                p.setBrush(QColor(fam.preview_hex))
                p.setPen(QPen(QColor("#FFFFFF"), 1))
                p.drawEllipse(box)
                p.setPen(QColor("#102033"))
            else:
                p.setBrush(Qt.NoBrush)
                p.setPen(QColor("#526174"))
            p.drawText(box, Qt.AlignCenter, text)

        p.end()

    def mousePressEvent(self, event):
        side = min(self.width(), self.height()) - 74
        side = max(130.0, float(side))
        cx, cy = self.width()/2.0, self.height()/2.0 + 4.0
        dx, dy = event.position().x()-cx, -(event.position().y()-cy)
        radius = hypot(dx, dy)
        # Only the coloured ring is interactive; the white centre remains a neutral indicator.
        if radius < side*0.25 or radius > side*0.53:
            return
        angle = degrees(atan2(dy, dx)) % 360.0
        sector_deg = 5.0
        selected = (int(angle // sector_deg) * sector_deg + sector_deg/2.0) % 360.0
        self.setHueAngle(selected)
        self.hueAngleSelected.emit(selected)
        super().mousePressEvent(event)




class AtlasValidationDialog(QDialog):
    """Compact validation dashboard: machine structure + human-review queue."""

    STATUS_COLORS = {
        "PASS": ("#EAF8EF", "#197A3A"),
        "REVIEW": ("#FFF7E6", "#9A6700"),
        "FAIL": ("#FDECEC", "#B42318"),
    }

    def __init__(self, records: Sequence[Mapping], parent=None, context_label: str = "D65 / 10°"):
        super().__init__(parent)
        self.records = list(records)
        self.context_label = context_label
        self.distance_callback=distance_callback; self.distance_label=distance_label
        self.report = build_validation_report(self.records)
        self.setWindowTitle("综合色图谱 · 验证")
        self.resize(900, 700)
        self.setMinimumSize(760, 580)
        self.setStyleSheet("QDialog{background:#F6F8FB;color:#142033;} QFrame#card{background:#FFFFFF;border:1px solid #E4EAF1;border-radius:9px;} QPushButton{padding:7px 12px;}")

        root = QVBoxLayout(self); root.setContentsMargins(14,14,14,14); root.setSpacing(10)
        head = QFrame(self); head.setObjectName("card")
        hl = QHBoxLayout(head); hl.setContentsMargins(16,12,16,12)
        title = QLabel("综合色图谱 · 自动验收", head); title.setStyleSheet("font-size:22px;font-weight:700;color:#13223A;")
        hl.addWidget(title); hl.addStretch(1)
        overall = self.report.get("overall", "-")
        bg, fg = self.STATUS_COLORS.get(overall, ("#EEF2F6","#334155"))
        badge = QLabel(overall, head); badge.setStyleSheet(f"background:{bg};color:{fg};padding:6px 12px;border-radius:12px;font-weight:700;")
        hl.addWidget(badge)
        root.addWidget(head)

        note = QLabel(
            f"{context_label} · {len(self.records)} 色样。机器只检查图谱结构；REVIEW 不是错误，只表示该页存在接近 Hue/Neutral 边界的颜色，需要你目视确认。",
            self,
        )
        note.setWordWrap(True); note.setStyleSheet("color:#64748B;"); root.addWidget(note)

        card = QFrame(self); card.setObjectName("card")
        grid = QGridLayout(card); grid.setContentsMargins(12,10,12,10); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        headers = ("验收页", "机器结果", "色样", "需目视", "你需要看什么")
        for c, text in enumerate(headers):
            lab = QLabel(text); lab.setStyleSheet("font-weight:700;color:#475569;"); grid.addWidget(lab,0,c)
        human_help = {
            "neutral": "是否都是黑/灰/白；Navy 不应混入",
            "blue": "蓝、深蓝、Navy 是否聚在一起",
            "green": "绿色是否聚在一起",
            "yellow": "黄/卡其邻域是否自然",
            "red": "红/粉邻域是否自然",
            "violet": "紫色是否聚在一起",
        }
        for row, section in enumerate(self.report.get("sections", []), start=1):
            label = QLabel(str(section.get("label", ""))); grid.addWidget(label,row,0)
            st = str(section.get("status", "-")); bg, fg = self.STATUS_COLORS.get(st, ("#EEF2F6","#334155"))
            stlab = QLabel(st); stlab.setAlignment(Qt.AlignCenter); stlab.setStyleSheet(f"background:{bg};color:{fg};padding:4px 8px;border-radius:8px;font-weight:700;"); grid.addWidget(stlab,row,1)
            grid.addWidget(QLabel(str(section.get("sample_count",0))),row,2)
            grid.addWidget(QLabel(str(section.get("risk_count",0))),row,3)
            if section.get("kind") == "opponent":
                help_text = "中间应趋向 Neutral；左右只出现当前 Hue 与对向邻域"
            else:
                help_text = human_help.get(str(section.get("key","")), "同类颜色是否聚在一起")
            h = QLabel(help_text); h.setWordWrap(True); h.setStyleSheet("color:#64748B;"); grid.addWidget(h,row,4)
        grid.setColumnStretch(4,1)
        root.addWidget(card)

        root.addWidget(QLabel("只需重点查看下面这些边界色（机器没有自动改它们）："))
        self.details = QPlainTextEdit(self); self.details.setReadOnly(True); self.details.setMaximumBlockCount(5000)
        lines=[]
        for section in self.report.get("sections", []):
            risks=section.get("risks", [])
            if not risks:
                continue
            lines.append(f"[{section.get('label')}] {len(risks)} 个需目视")
            for risk in risks[:30]:
                lines.append(f"  • {risk.get('name') or risk.get('key')}  ({'/'.join(risk.get('flags', []))})")
            if len(risks) > 30:
                lines.append(f"  … 另有 {len(risks)-30} 个")
        self.details.setPlainText("\n".join(lines) if lines else "没有发现边界色；机器结构检查全部清晰。")
        root.addWidget(self.details,1)

        foot=QHBoxLayout()
        human=QLabel("人工目视：PENDING"); human.setStyleSheet("font-weight:700;color:#475569;"); foot.addWidget(human)
        foot.addStretch(1)
        export_btn=QPushButton("导出验证报告…"); export_btn.clicked.connect(self._export_report); foot.addWidget(export_btn)
        close_btn=QPushButton("关闭"); close_btn.clicked.connect(self.accept); foot.addWidget(close_btn)
        root.addLayout(foot)

    def _export_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出综合色图谱验证报告", "Color_Atlas_Validation.txt", "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(validation_report_text(self.report))
        except Exception as exc:
            self.details.appendPlainText(f"\n导出失败：{exc}")


class LabcAtlasDialog(QDialog):
    """Read-only LABC atlas over one palette's real measured samples."""

    def __init__(
        self,
        records: Sequence[Mapping],
        parent=None,
        locate_callback: Callable[[str], None] | None = None,
        context_label: str = "D65 / 10°",
        distance_callback: Callable[[Sequence[float],Sequence[float]],float] | None = None,
        distance_label: str = "ΔE*ab",
    ):
        super().__init__(parent)
        self.records = [dict(x) for x in records if x.get("lab")]
        self.locate_callback = locate_callback
        self.context_label = context_label
        self.distance_callback=distance_callback; self.distance_label=distance_label
        self.current_family = "blue" if any(family_for_lab(x["lab"]) == "blue" for x in self.records) else "neutral"
        self.current_record: Mapping | None = None
        self.picked_keys: list[str] = []
        self.family_buttons: dict[str, QToolButton] = {}
        self.view_mode = "single"  # single | opponent
        self.current_hue_angle = family_center_angle(self.current_family) if self.current_family != "neutral" else 270.0

        self.setWindowTitle("色彩空间图谱 · Lab/LCh")
        self.resize(1540, 900)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet("""
            QDialog{background:#F6F8FB;color:#142033;}
            QLabel#atlasTitle{font-size:28px;font-weight:700;color:#13223A;}
            QLabel#atlasFamilyTitle{font-size:25px;font-weight:700;color:#16253D;}
            QLabel#muted{color:#64748B;}
            QFrame#atlasPanel{background:#FFFFFF;border:1px solid #E4EAF1;border-radius:10px;}
            QPushButton#primary{background:#1677FF;color:white;border:0;border-radius:6px;padding:8px 14px;font-weight:600;}
            QPushButton#secondary{background:#FFFFFF;color:#24364F;border:1px solid #D7DEE8;border-radius:6px;padding:7px 12px;}
            QLineEdit{background:#FFFFFF;border:1px solid #D7DEE8;border-radius:6px;padding:7px 10px;}
            QTabBar::tab{padding:8px 18px;color:#475569;border:0;}
            QTabBar::tab:selected{color:#1677FF;font-weight:600;border-bottom:2px solid #1677FF;}
        """)

        root = QVBoxLayout(self); root.setContentsMargins(12,12,12,12); root.setSpacing(8)
        body = QHBoxLayout(); body.setSpacing(8); root.addLayout(body, 1)

        # Main atlas panel ---------------------------------------------------
        left = QFrame(self); left.setObjectName("atlasPanel")
        left_lay = QVBoxLayout(left); left_lay.setContentsMargins(22,16,22,14); left_lay.setSpacing(8)
        title_row=QHBoxLayout()
        title=QLabel("色彩空间图谱",left); title.setObjectName("atlasTitle"); title_row.addWidget(title)
        title_row.addStretch(1)
        self.search=QLineEdit(left); self.search.setPlaceholderText("搜索当前图谱中的色号 / 名称…"); self.search.setClearButtonEnabled(True); self.search.setFixedWidth(280)
        self.search.textChanged.connect(self._rebuild_active_view); title_row.addWidget(self.search)
        left_lay.addLayout(title_row)

        self.tabs=QTabWidget(left)
        hue_page=QWidget(); self.tabs.addTab(hue_page,"色相")
        light_page=QWidget(); self.tabs.addTab(light_page,"明度")
        chroma_page=QWidget(); self.tabs.addTab(chroma_page,"彩度")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        left_lay.addWidget(self.tabs,1)

        hue_lay=QVBoxLayout(hue_page); hue_lay.setContentsMargins(0,6,0,0); hue_lay.setSpacing(8)
        self.family_strip=QHBoxLayout(); self.family_strip.setSpacing(2); hue_lay.addLayout(self.family_strip)
        self._build_family_strip()

        # View mode switch: original single-hue page + new opponent slice.
        mode_row=QHBoxLayout(); mode_row.addWidget(QLabel("显示模式："))
        self.single_btn=QToolButton(); self.single_btn.setText("▦  单色相"); self.single_btn.setCheckable(True); self.single_btn.setChecked(True)
        self.opp_btn=QToolButton(); self.opp_btn.setText("◫  对向色相"); self.opp_btn.setCheckable(True)
        for btn in (self.single_btn,self.opp_btn):
            btn.setStyleSheet("QToolButton{padding:6px 10px;border:1px solid #D7DEE8;border-radius:5px;background:#FFFFFF;} QToolButton:checked{background:#E8F2FF;color:#1268D3;border:2px solid #1677FF;font-weight:600;}")
        self.single_btn.clicked.connect(lambda:self._set_view_mode("single")); self.opp_btn.clicked.connect(lambda:self._set_view_mode("opponent"))
        mode_row.addWidget(self.single_btn); mode_row.addWidget(self.opp_btn)
        # Validation was a development-only tool. It is intentionally not exposed
        # in the production palette browser.
        mode_row.addStretch(1); hue_lay.addLayout(mode_row)

        axis_top=QHBoxLayout(); axis_top.addSpacing(116)
        self.axis_low=QLabel("低彩度\n（更灰）"); self.axis_low.setObjectName("muted"); axis_top.addWidget(self.axis_low)
        axis_top.addStretch(1)
        self.axis_center=QLabel("彩度（C*）  →"); self.axis_center.setStyleSheet("font-weight:600;color:#25324A;"); axis_top.addWidget(self.axis_center)
        axis_top.addStretch(1)
        self.axis_high=QLabel("高彩度\n（更鲜艳）"); self.axis_high.setObjectName("muted"); self.axis_high.setAlignment(Qt.AlignRight); axis_top.addWidget(self.axis_high)
        hue_lay.addLayout(axis_top)

        self.grid_host=QWidget(); self.grid_layout=QGridLayout(self.grid_host); self.grid_layout.setContentsMargins(0,0,0,0); self.grid_layout.setSpacing(3)
        scroll=QScrollArea(); scroll.setFrameShape(QFrame.NoFrame); scroll.setWidgetResizable(True); scroll.setWidget(self.grid_host); hue_lay.addWidget(scroll,1)
        self.note=QLabel("",hue_page); self.note.setObjectName("muted"); self.note.setAlignment(Qt.AlignRight); hue_lay.addWidget(self.note)

        # Fixed-Lightness cross-section ------------------------------------
        light_lay=QVBoxLayout(light_page); light_lay.setContentsMargins(0,6,0,0); light_lay.setSpacing(8)
        light_ctrl=QHBoxLayout()
        light_ctrl.addWidget(QLabel("固定明度 L*："))
        self.light_L_spin=QSpinBox(light_page); self.light_L_spin.setRange(10,90); self.light_L_spin.setSingleStep(10); self.light_L_spin.setValue(50); self.light_L_spin.setSuffix("  ")
        self.light_L_spin.valueChanged.connect(self._lightness_target_changed); light_ctrl.addWidget(self.light_L_spin)
        light_ctrl.addSpacing(14)
        help_l=QLabel("固定一个明度层，查看所有综合色相与彩度分布",light_page); help_l.setObjectName("muted"); light_ctrl.addWidget(help_l); light_ctrl.addStretch(1)
        light_lay.addLayout(light_ctrl)
        self.light_axis=QLabel("横轴：h° 色相环位置   ·   纵轴：C* 彩度（低 → 高）",light_page); self.light_axis.setStyleSheet("font-weight:600;color:#25324A;"); light_lay.addWidget(self.light_axis)
        self.light_grid_host=QWidget(light_page); self.light_grid_layout=QGridLayout(self.light_grid_host); self.light_grid_layout.setContentsMargins(0,0,0,0); self.light_grid_layout.setSpacing(2)
        light_scroll=QScrollArea(light_page); light_scroll.setFrameShape(QFrame.NoFrame); light_scroll.setWidgetResizable(True); light_scroll.setWidget(self.light_grid_host); light_lay.addWidget(light_scroll,1)
        self.light_note=QLabel("",light_page); self.light_note.setObjectName("muted"); self.light_note.setAlignment(Qt.AlignRight); light_lay.addWidget(self.light_note)

        # Fixed-Chroma cross-section ---------------------------------------
        chroma_lay=QVBoxLayout(chroma_page); chroma_lay.setContentsMargins(0,6,0,0); chroma_lay.setSpacing(8)
        chroma_ctrl=QHBoxLayout()
        chroma_ctrl.addWidget(QLabel("固定彩度 C*："))
        self.chroma_C_spin=QSpinBox(chroma_page); self.chroma_C_spin.setRange(0,120); self.chroma_C_spin.setSingleStep(10); self.chroma_C_spin.setValue(20); self.chroma_C_spin.setSuffix("  ")
        self.chroma_C_spin.valueChanged.connect(self._chroma_target_changed); chroma_ctrl.addWidget(self.chroma_C_spin)
        chroma_ctrl.addSpacing(14)
        help_c=QLabel("固定一个彩度层，查看所有综合色相与明度分布",chroma_page); help_c.setObjectName("muted"); chroma_ctrl.addWidget(help_c); chroma_ctrl.addStretch(1)
        chroma_lay.addLayout(chroma_ctrl)
        self.chroma_axis=QLabel("横轴：h° 色相环位置   ·   纵轴：L* 明度（浅 → 深）",chroma_page); self.chroma_axis.setStyleSheet("font-weight:600;color:#25324A;"); chroma_lay.addWidget(self.chroma_axis)
        self.chroma_grid_host=QWidget(chroma_page); self.chroma_grid_layout=QGridLayout(self.chroma_grid_host); self.chroma_grid_layout.setContentsMargins(0,0,0,0); self.chroma_grid_layout.setSpacing(2)
        chroma_scroll=QScrollArea(chroma_page); chroma_scroll.setFrameShape(QFrame.NoFrame); chroma_scroll.setWidgetResizable(True); chroma_scroll.setWidget(self.chroma_grid_host); chroma_lay.addWidget(chroma_scroll,1)
        self.chroma_note=QLabel("",chroma_page); self.chroma_note.setObjectName("muted"); self.chroma_note.setAlignment(Qt.AlignRight); chroma_lay.addWidget(self.chroma_note)

        body.addWidget(left, 3)

        # Right detail panel ------------------------------------------------
        right_scroll=QScrollArea(self); right_scroll.setFrameShape(QFrame.NoFrame); right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); right_scroll.setMinimumWidth(390); right_scroll.setMaximumWidth(470)
        right=QFrame(); right.setObjectName("atlasPanel"); right.setMinimumWidth(360)
        r=QVBoxLayout(right); r.setContentsMargins(18,16,18,16); r.setSpacing(8)
        right_scroll.setWidget(right)
        family_head=QHBoxLayout(); prev=QToolButton(); prev.setText("‹"); prev.clicked.connect(lambda:self._step_context(-1)); family_head.addWidget(prev)
        self.family_title=QLabel("Blue"); self.family_title.setObjectName("atlasFamilyTitle"); family_head.addWidget(self.family_title); family_head.addStretch(1)
        nxt=QToolButton(); nxt.setText("›"); nxt.clicked.connect(lambda:self._step_context(1)); family_head.addWidget(nxt); r.addLayout(family_head)

        # The reference atlas shows the hue wheel only for the opponent-slice
        # view.  A normal single-Hue page goes directly from the Hue title to
        # the selected swatch card.
        self.wheel_host=QWidget(self)
        wheel_lay=QVBoxLayout(self.wheel_host); wheel_lay.setContentsMargins(0,0,0,0); wheel_lay.setSpacing(2)
        self.hue_wheel=HueOppositionWheel(self.current_family if self.current_family != "neutral" else "blue", self.wheel_host)
        self.hue_wheel.hueAngleSelected.connect(self._activate_hue_angle)
        self.hue_wheel.setMinimumHeight(235); wheel_lay.addWidget(self.hue_wheel)
        self.axis_caption=QLabel("",self.wheel_host); self.axis_caption.setObjectName("muted"); self.axis_caption.setAlignment(Qt.AlignCenter); self.axis_caption.setWordWrap(True); self.axis_caption.setMinimumHeight(34); self.axis_caption.setContentsMargins(4,4,4,4); wheel_lay.addWidget(self.axis_caption)
        self.wheel_host.setMinimumHeight(278)
        r.addWidget(self.wheel_host)

        sample_row=QHBoxLayout(); self.big_chip=QFrame(); self.big_chip.setFixedSize(104,104); self.big_chip.setStyleSheet("background:#E9EEF5;border:1px solid #D6DEE8;border-radius:6px;"); sample_row.addWidget(self.big_chip)
        text_col=QVBoxLayout(); self.sample_name=QLabel("点击图谱中的色样"); self.sample_name.setWordWrap(True); self.sample_name.setStyleSheet("font-size:15px;font-weight:700;"); text_col.addWidget(self.sample_name)
        self.sample_family=QLabel("-"); self.sample_family.setObjectName("muted"); text_col.addWidget(self.sample_family); text_col.addStretch(1); sample_row.addLayout(text_col,1); r.addLayout(sample_row)

        values=QGridLayout(); self.value_labels={}
        for col,key in enumerate(("L*","a*","b*","C*","h°")):
            h=QLabel(key); h.setAlignment(Qt.AlignCenter); h.setStyleSheet("font-weight:600;color:#475569;"); values.addWidget(h,0,col)
            v=QLabel("-"); v.setAlignment(Qt.AlignCenter); v.setStyleSheet("background:#F8FAFC;border:1px solid #E5EAF0;padding:5px;"); values.addWidget(v,1,col); self.value_labels[key]=v
        r.addLayout(values)

        action=QHBoxLayout(); self.pick_btn=QPushButton("+ 加入已选色样"); self.pick_btn.setObjectName("primary"); self.pick_btn.clicked.connect(self._pick_current); action.addWidget(self.pick_btn)
        locate=QPushButton("定位到色卡"); locate.setObjectName("secondary"); locate.setToolTip("回到打开本图谱的色卡编排窗口，选中并滚动到当前色样；不会改变色样位置。")
        locate.clicked.connect(self._locate_current); action.addWidget(locate); r.addLayout(action)

        picked_label=QLabel("已选色样（名称保持源文件原文）"); picked_label.setToolTip("下方英文/字母数字是 CPX/QTX 中原始色样名称，软件不会翻译或改写，以免破坏色号与追溯。")
        r.addWidget(picked_label)
        self.picked_host=QWidget(); self.picked_lay=QHBoxLayout(self.picked_host); self.picked_lay.setContentsMargins(0,0,0,0); self.picked_lay.setSpacing(6)
        picked_scroll=QScrollArea(); picked_scroll.setFrameShape(QFrame.NoFrame); picked_scroll.setWidgetResizable(True); picked_scroll.setFixedHeight(124); picked_scroll.setWidget(self.picked_host); r.addWidget(picked_scroll)

        self.similar_title=QLabel("相近色 · 同色相族")
        r.addWidget(self.similar_title)
        self.sim_host=QWidget(); self.sim_lay=QHBoxLayout(self.sim_host); self.sim_lay.setContentsMargins(0,0,0,0); self.sim_lay.setSpacing(6)
        sim_scroll=QScrollArea(); sim_scroll.setFrameShape(QFrame.NoFrame); sim_scroll.setWidgetResizable(True); sim_scroll.setFixedHeight(132); sim_scroll.setWidget(self.sim_host); r.addWidget(sim_scroll)
        r.addStretch(1)
        self.stats=QLabel(f"当前方案：{len(self.records)} 色样 · 图谱只读，不修改原文件/原卡位"); self.stats.setObjectName("muted"); self.stats.setWordWrap(True); r.addWidget(self.stats)
        body.addWidget(right_scroll,1)

        # Bottom planning tray ---------------------------------------------
        bottom=QFrame(self); bottom.setObjectName("atlasPanel"); b=QHBoxLayout(bottom); b.setContentsMargins(14,8,14,8)
        b.addWidget(QLabel("已选色样"))
        self.bottom_host=QWidget(); self.bottom_lay=QHBoxLayout(self.bottom_host); self.bottom_lay.setContentsMargins(8,0,0,0); self.bottom_lay.setSpacing(6); b.addWidget(self.bottom_host,1)
        clear=QPushButton("清空选择"); clear.setObjectName("secondary"); clear.clicked.connect(self._clear_picks); b.addWidget(clear)
        root.addWidget(bottom)

        self._activate_family(self.current_family)


    def _on_tab_changed(self, _index: int):
        self.current_record=None
        self._clear_current_detail()
        self._update_titles()
        self._rebuild_active_view()

    def _rebuild_active_view(self):
        idx=self.tabs.currentIndex() if hasattr(self,"tabs") else 0
        if idx==1:
            self._rebuild_lightness_grid()
        elif idx==2:
            self._rebuild_chroma_grid()
        else:
            self._rebuild_grid()

    def _lightness_target_changed(self, _value: int):
        if self.tabs.currentIndex()!=1:return
        self.current_record=None; self._clear_current_detail(); self._update_titles(); self._rebuild_lightness_grid()

    def _chroma_target_changed(self, _value: int):
        if self.tabs.currentIndex()!=2:return
        self.current_record=None; self._clear_current_detail(); self._update_titles(); self._rebuild_chroma_grid()

    def _step_context(self, delta: int):
        idx=self.tabs.currentIndex()
        if idx==1:
            self.light_L_spin.setValue(max(10,min(90,self.light_L_spin.value()+int(delta)*10))); return
        if idx==2:
            self.chroma_C_spin.setValue(max(0,min(120,self.chroma_C_spin.value()+int(delta)*10))); return
        self._step_family(delta)

    def _cross_header_label(self, angle):
        if angle is None:return "N"
        a=int(round(float(angle)))%360
        return f"{a}°" if a%30==0 else "·"

    def _cross_header_style(self, angle):
        if angle is None:return "background:#9DA6B2;color:#172033;border-radius:3px;font-size:8px;font-weight:700;"
        fam=family_key_for_hue_angle(float(angle)); bg=FAMILY_HEX.get(fam,"#E8EDF3")
        return f"background:{bg};color:#172033;border-radius:3px;font-size:8px;font-weight:600;"

    def _rebuild_lightness_grid(self):
        self._clear_layout(self.light_grid_layout)
        source=self._filtered_records(); target=float(self.light_L_spin.value()); cells=build_lightness_cells(source,target); columns=hue_plane_columns()
        corner=QLabel("彩度\n(C*)"); corner.setAlignment(Qt.AlignCenter); corner.setStyleSheet("font-weight:600;color:#334155;"); self.light_grid_layout.addWidget(corner,0,0)
        for c,angle in enumerate(columns,1):
            lab=QLabel(self._cross_header_label(angle)); lab.setAlignment(Qt.AlignCenter); lab.setStyleSheet(self._cross_header_style(angle)); self.light_grid_layout.addWidget(lab,0,c)
        for row,C in enumerate(C_CENTERS,1):
            y=QLabel(str(int(C))); y.setAlignment(Qt.AlignCenter); y.setStyleSheet("color:#475569;font-weight:600;"); self.light_grid_layout.addWidget(y,row,0)
            for col,_angle in enumerate(columns,1):
                recs=cells.get((row-1,col-1),[])
                btn=AtlasCellButton(recs,self.light_grid_host,compact=True); btn.activated.connect(self._select_record); self.light_grid_layout.addWidget(btn,row,col)
        self.light_grid_layout.setColumnStretch(0,0)
        for c in range(1,len(columns)+1): self.light_grid_layout.setColumnStretch(c,1)
        for r in range(1,len(C_CENTERS)+1): self.light_grid_layout.setRowStretch(r,1)
        visible=sum(len(v) for v in cells.values())
        self.light_note.setText(f"明度层 L*≈{int(target)}（约 {max(0,int(target)-5)}–{min(100,int(target)+5)}）· 每个真实色样只进入最近明度层 · N=中性 · ×N=该格真实色样数")
        self.stats.setText(f"明度横截面 L*={int(target)}：{visible} 色样 · 当前方案 {len(self.records)} 色样 · 图谱只读")
        if visible and self.current_record is None:self._select_record(next(iter(cells.values()))[0])

    def _rebuild_chroma_grid(self):
        self._clear_layout(self.chroma_grid_layout)
        source=self._filtered_records(); target=float(self.chroma_C_spin.value()); cells=build_chroma_cells(source,target); columns=hue_plane_columns()
        corner=QLabel("明度\n(L*)"); corner.setAlignment(Qt.AlignCenter); corner.setStyleSheet("font-weight:600;color:#334155;"); self.chroma_grid_layout.addWidget(corner,0,0)
        for c,angle in enumerate(columns,1):
            lab=QLabel(self._cross_header_label(angle)); lab.setAlignment(Qt.AlignCenter); lab.setStyleSheet(self._cross_header_style(angle)); self.chroma_grid_layout.addWidget(lab,0,c)
        for row,L in enumerate(L_CENTERS,1):
            y=QLabel(str(int(L))); y.setAlignment(Qt.AlignCenter); y.setStyleSheet("color:#475569;font-weight:600;"); self.chroma_grid_layout.addWidget(y,row,0)
            for col,_angle in enumerate(columns,1):
                recs=cells.get((row-1,col-1),[])
                btn=AtlasCellButton(recs,self.chroma_grid_host,compact=True); btn.activated.connect(self._select_record); self.chroma_grid_layout.addWidget(btn,row,col)
        self.chroma_grid_layout.setColumnStretch(0,0)
        for c in range(1,len(columns)+1): self.chroma_grid_layout.setColumnStretch(c,1)
        for r in range(1,len(L_CENTERS)+1): self.chroma_grid_layout.setRowStretch(r,1)
        visible=sum(len(v) for v in cells.values())
        self.chroma_note.setText(f"彩度层 C*≈{int(target)}（约 {max(0,int(target)-5)}–{int(target)+5}）· 每个真实色样只进入最近彩度层 · N=中性 · ×N=该格真实色样数")
        self.stats.setText(f"彩度横截面 C*={int(target)}：{visible} 色样 · 当前方案 {len(self.records)} 色样 · 图谱只读")
        if visible and self.current_record is None:self._select_record(next(iter(cells.values()))[0])

    def _open_validation(self):
        dlg = AtlasValidationDialog(self.records, self, self.context_label)
        dlg.exec()

    def _build_family_strip(self):
        specs=[("neutral",_ui_family_label("neutral"),FAMILY_HEX["neutral"])] + [(x.key,_ui_family_label(x.key),x.preview_hex) for x in HUE_FAMILIES]
        for key,label,color in specs:
            btn=QToolButton(self); btn.setText(label); btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon); btn.setCheckable(True)
            btn.setMinimumWidth(72); btn.setMinimumHeight(42)
            btn.setStyleSheet(
                "QToolButton{border:1px solid #E4E8EF;border-radius:4px;padding:3px;color:#334155;"
                f"background:{color};}}"
                "QToolButton:checked{border:3px solid #1677FF;font-weight:700;}"
            )
            btn.clicked.connect(lambda _=False,k=key:self._activate_family(k)); self.family_strip.addWidget(btn); self.family_buttons[key]=btn

    def _set_view_mode(self, mode: str):
        mode = "opponent" if mode == "opponent" else "single"
        if mode == "opponent" and self.current_family == "neutral":
            # Opponent planes require a hue axis.  Choose Blue when available,
            # otherwise the first populated chromatic family.
            counts=family_counts(self.records)
            key="blue" if counts.get("blue",0) else next((k for k in FAMILY_KEYS if k!="neutral" and counts.get(k,0)),"red")
            self.current_family=key
        self.view_mode=mode
        self.single_btn.setChecked(mode=="single"); self.opp_btn.setChecked(mode=="opponent")
        self._activate_family(self.current_family)

    def _activate_family(self, key: str):
        new_family = key if key in FAMILY_KEYS else "neutral"
        family_changed = new_family != self.current_family
        self.current_family = new_family
        if self.current_family != "neutral":
            self.current_hue_angle = family_center_angle(self.current_family)
        if self.view_mode == "opponent" and self.current_family == "neutral":
            self.view_mode="single"; self.single_btn.setChecked(True); self.opp_btn.setChecked(False)
        for k,btn in self.family_buttons.items(): btn.setChecked(k == self.current_family)
        if self.current_family != "neutral": self.hue_wheel.setFamily(self.current_family)
        # Never keep a Blue/other-page detail card after switching to Orange,
        # Green, etc.  This was the Hotfix101 state-sync defect reported by the
        # user.  The rebuilt page will select its own first real sample.
        if family_changed:
            self.current_record = None
            self._clear_current_detail()
        self._update_titles()
        self._rebuild_grid()

    def _activate_hue_angle(self, hue_angle: float):
        """Activate one interactive 5° hue-ring sector."""
        angle = float(hue_angle) % 360.0
        new_family = family_key_for_hue_angle(angle)
        angle_changed = angular_distance(angle, self.current_hue_angle) > 1e-9
        self.current_hue_angle = angle
        self.current_family = new_family
        for k, btn in self.family_buttons.items():
            btn.setChecked(k == self.current_family)
        self.hue_wheel.setHueAngle(angle)
        if angle_changed:
            self.current_record = None
            self._clear_current_detail()
        self._update_titles()
        self._rebuild_grid()

    def _update_titles(self):
        tab=self.tabs.currentIndex() if hasattr(self,"tabs") else 0
        if tab==1:
            self.wheel_host.setVisible(False)
            self.family_title.setText(f"明度层 L*≈{self.light_L_spin.value()}")
            return
        if tab==2:
            self.wheel_host.setVisible(False)
            self.family_title.setText(f"彩度层 C*≈{self.chroma_C_spin.value()}")
            return

        show_wheel = self.view_mode == "opponent" and self.current_family != "neutral"
        self.wheel_host.setVisible(show_wheel)
        if show_wheel:
            op_angle = opposite_hue_angle(self.current_hue_angle)
            op = family_key_for_hue_angle(op_angle)
            self.family_title.setText(f"{_ui_family_label(self.current_family)} · {self.current_hue_angle:.1f}°")
            self.axis_caption.setText(
                f"{_ui_family_label(self.current_family)} {self.current_hue_angle:.1f}°  ←  中性  →  "
                f"{_ui_family_label(op)} {op_angle:.1f}°"
            )
            self.axis_low.setText(f"当前色相\n{self.current_hue_angle:.1f}°")
            self.axis_center.setText("对置彩度轴 · 中性 = 0")
            self.axis_high.setText(f"对向色相\n{op_angle:.1f}°")
            self.note.setText(
                f"LABC 图谱 · {self.context_label} · 对置色相剖面 · 纵轴 L* · 点击右侧 5° 色相环可连续切换剖面 · 仅显示真实色样"
            )
        else:
            self.family_title.setText(_ui_family_label(self.current_family))
            self.axis_caption.setText("单色相页面 · L* × C*")
            self.axis_low.setText("低彩度\n（更灰）")
            self.axis_center.setText("彩度（C*）  →")
            self.axis_high.setText("高彩度\n（更鲜艳）")
            self.note.setText(
                f"LABC 图谱 · {self.context_label} · 纵轴 L* · 横轴 C* · 固定色相族 · 空格表示无真实色样 · ×N 表示该格有 N 个色样，单击逐个查看"
            )

    def _step_family(self, delta: int):
        keys=[k for k in FAMILY_KEYS if not (self.view_mode=="opponent" and k=="neutral")]
        idx=keys.index(self.current_family) if self.current_family in keys else 0
        self._activate_family(keys[(idx+delta)%len(keys)])

    def _clear_layout(self, layout):
        while layout.count():
            item=layout.takeAt(0); w=item.widget()
            if w is not None: w.deleteLater()

    def _filtered_records(self):
        query=self.search.text().strip().casefold() if hasattr(self,"search") else ""
        if not query: return self.records
        return [r for r in self.records if query in f"{r.get('name','')} {r.get('sample_id','')}".casefold()]

    def _rebuild_grid(self):
        self._clear_layout(self.grid_layout)
        source=self._filtered_records()
        if self.view_mode == "opponent" and self.current_family != "neutral":
            centers=SIGNED_C_CENTERS
            cells=build_opponent_cells(source, self.current_family, tolerance_deg=7.5, hue_angle=self.current_hue_angle)
            compact=True
            op=family_key_for_hue_angle(opposite_hue_angle(self.current_hue_angle))
            corner_text="明度\n(L*)"
        else:
            centers=C_CENTERS
            cells=build_hue_cells(source,self.current_family)
            compact=False
            op=None
            corner_text="明度\n(L*)"

        corner=QLabel(corner_text); corner.setAlignment(Qt.AlignCenter); corner.setStyleSheet("font-weight:600;color:#334155;"); self.grid_layout.addWidget(corner,0,0)
        for c,center in enumerate(centers,1):
            # Opponent mode displays magnitude on both sides with a central N.
            if self.view_mode=="opponent":
                text="N" if abs(center)<1e-9 else str(int(abs(center)))
            else:
                text=str(int(center))
            lab=QLabel(text); lab.setAlignment(Qt.AlignCenter); lab.setStyleSheet("color:#64748B;font-size:9px;"); self.grid_layout.addWidget(lab,0,c)
        for row,L in enumerate(L_CENTERS,1):
            y=QLabel(str(int(L))); y.setAlignment(Qt.AlignCenter); y.setStyleSheet("color:#475569;font-weight:600;"); self.grid_layout.addWidget(y,row,0)
            for col,_C in enumerate(centers,1):
                recs=cells.get((row-1,col-1),[])
                btn=AtlasCellButton(recs,self.grid_host,compact=compact); btn.activated.connect(self._select_record); self.grid_layout.addWidget(btn,row,col)
        self.grid_layout.setColumnStretch(0,0)
        for c in range(1,len(centers)+1): self.grid_layout.setColumnStretch(c,1)
        for row in range(1,len(L_CENTERS)+1): self.grid_layout.setRowStretch(row,1)
        visible_n=sum(len(v) for v in cells.values())
        if self.view_mode=="opponent" and op:
            op_angle = opposite_hue_angle(self.current_hue_angle)
            op = family_key_for_hue_angle(op_angle)
            self.stats.setText(
                f"对置剖面 {_ui_family_label(self.current_family)} {self.current_hue_angle:.1f}° ↔ "
                f"{_ui_family_label(op)} {op_angle:.1f}°：{visible_n} 色样 · 当前方案 {len(self.records)} 色样 · 只读"
            )
        else:
            self.stats.setText(f"{_ui_family_label(self.current_family)}：{visible_n} 色样 · 当前方案 {len(self.records)} 色样 · 图谱只读，不修改原文件/原卡位")
        if visible_n and self.current_record is None:
            first=next(iter(cells.values()))[0]; self._select_record(first)

    def _clear_current_detail(self):
        self.big_chip.setStyleSheet("QFrame{background:#E9EEF5;border:1px solid #D6DEE8;border-radius:6px;}")
        self.sample_name.setText("点击图谱中的色样")
        self.sample_family.setText("-")
        for v in self.value_labels.values():
            v.setText("-")
        self._clear_layout(self.sim_lay)
        self.sim_lay.addStretch(1)

    def _select_record(self, record: Mapping):
        self.current_record=record
        bg=str(record.get("hex") or "#8894A5"); self.big_chip.setStyleSheet(f"QFrame{{background:{bg};border:1px solid #D6DEE8;border-radius:6px;}}")
        self.sample_name.setText(str(record.get("name") or record.get("sample_id") or "未命名"))
        family=family_for_lab(record["lab"]); self.sample_family.setText(f"{_ui_family_label(family)} · {self.context_label}")
        L,a,b=(float(x) for x in record["lab"]); C=hypot(a,b); _L,_C,h=lab_to_lch(record["lab"])
        vals={"L*":L,"a*":a,"b*":b,"C*":C,"h°":h}
        for k,v in vals.items(): self.value_labels[k].setText(f"{v:.2f}")
        self._rebuild_similar()

    def _rebuild_similar(self):
        self._clear_layout(self.sim_lay)
        if hasattr(self,'similar_title'):self.similar_title.setText(f'相近色 · 同色相族 · {self.distance_label}')
        if not self.current_record:return
        if self.distance_callback is None:
            ranked=nearest_records(self.records,self.current_record,4,True)
        else:
            target=self.current_record; fam=family_for_lab(target['lab']); tmp=[]
            for rec in self.records:
                if str(rec.get('key',''))==str(target.get('key','')):continue
                if family_for_lab(rec['lab'])!=fam:continue
                try:d=float(self.distance_callback(target['lab'],rec['lab']))
                except Exception:continue
                tmp.append((d,str(rec.get('name','')).casefold(),str(rec.get('key','')),rec))
            tmp.sort(key=lambda x:(x[0],x[1],x[2])); ranked=[x[-1] for x in tmp[:4]]
        for rec in ranked:
            w=MiniSwatch(rec,compact=True); w.clicked.connect(self._select_record); self.sim_lay.addWidget(w)
        self.sim_lay.addStretch(1)

    def _pick_current(self):
        if not self.current_record:return
        key=str(self.current_record.get("key", ""))
        if key and key not in self.picked_keys:self.picked_keys.append(key)
        self._refresh_picks()

    def _refresh_picks(self):
        mapping={str(x.get("key","")):x for x in self.records}
        for layout in (self.picked_lay,self.bottom_lay):
            self._clear_layout(layout)
            for key in self.picked_keys:
                rec=mapping.get(key)
                if not rec:continue
                w=MiniSwatch(rec,compact=True); w.clicked.connect(self._select_record); layout.addWidget(w)
            layout.addStretch(1)

    def _clear_picks(self):
        self.picked_keys.clear(); self._refresh_picks()

    def _locate_current(self):
        if self.current_record and self.locate_callback:
            self.locate_callback(str(self.current_record.get("key", "")))
