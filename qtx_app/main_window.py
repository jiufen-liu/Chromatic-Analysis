from __future__ import annotations

import math
import time
import uuid
import shutil
import datetime
import re
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QEvent, QEventLoop, QModelIndex, QItemSelection, QItemSelectionModel, QPoint, QPointF, QRect, QRectF, QSettings, QStandardPaths, QSize, Qt, QTimer, Signal, QMimeData
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap, QImage, QPdfWriter, QPageSize, QPageLayout, QRadialGradient, QLinearGradient, QKeySequence, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QListView, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QDoubleSpinBox, QStackedWidget,
    QStatusBar, QStyle, QStyledItemDelegate, QTableView, QTabWidget, QTabBar, QTextEdit, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QSplitter, QRadioButton, QApplication, QCheckBox, QMdiArea, QMdiSubWindow,
    QTableWidget, QTableWidgetItem, QFormLayout, QGroupBox, QToolTip, QToolButton,
    QProgressBar, QProgressDialog, QLayout,
)

from qtx_core import (
    SUPPORTED_ILLUMINANTS, Sample, analyse_pair, average_spectral_standard,
    cie_tint_d65_10, cie_whiteness_d65_10, parse_qtx_file, reflectance_to_xyz_lab, reflectances_to_xyz_lab,
    cam16ucs_from_xyz, munsell_hue_order_from_xyz, delta_h_cielab_signed,
    parse_cpx_file, export_cpx_file, export_qtx_file, shade_555, spectral_feature_summary,
    display_illuminant, illuminant_note,
)
from .library_store import LibraryStore
from .auth_store import AuthStore, AuthUser, UserManagementDialog
from .excel_exchange import export_client_card, export_workbench_table, import_layout_workbook, import_mpc_data_workbook, import_excel_workbook
from .workfile import read_workfile, write_workfile, SUFFIX as WORKFILE_SUFFIX
from .perf_monitor import profiled, write_performance_summary
from .data_protection import user_workfile_root, maybe_daily_backup
from .data_management import DataManagementDialog
from .storage_v2 import runtime_root

try:
    # Hotfix37: unified Qt Quick 3D / RHI renderer.
    from .lab3d_quick import Lab3DDialog as QuickLab3DDialog
    QUICK3D_IMPORT_ERROR = ""
except Exception as _quick3d_exc:
    QuickLab3DDialog = None
    QUICK3D_IMPORT_ERROR = repr(_quick3d_exc)
from .build_info import BUILD_ID, BUILD_LABEL
from qtx_core.colorimetry import delta_e, delta_e_many
from qtx_core.palette_labch_sort import (
    lab_to_lch, neutral_limit, strict_reference_distance_key,
    circular_hue_cut, circular_hue_position, reliable_hues_for_cut,
)
from qtx_core.palette_global_delta import optimise_global_open_path
from qtx_core.cpx_layout import active_geometry as cpx_active_geometry, project_active_slots as cpx_project_active_slots, expand_to_source_grid as cpx_expand_to_source_grid, crop_trailing_empty_edges as cpx_crop_trailing_empty_edges

FORMULA_KEYS = ["delta_e00", "delta_e94", "delta_e76", "cmc21"]
FORMULA_LABELS = {
    "delta_e00": "CIEDE2000",
    "delta_e94": "CIE94",
    "delta_e76": "ΔE*ab",
    "cmc21": "CMC(2:1)",
}
DEFAULT_THRESHOLDS = {
    "delta_e00": 2.0,
    "delta_e94": 2.0,
    "delta_e76": 2.0,
    "cmc21": 2.0,
}
DEFAULT_VISIBLE_KEYS = {"name", "illuminant", "observer", "role", "L", "a", "b", "C", "h",
                        "dL", "da", "db", "dC", "dh", "de00", "verdict"}

# UI Responsive P2: task-oriented table presets.  These are presentation-only;
# they never change measured data, formulas, exports or stored samples.
WORKBENCH_VIEW_PRESETS = {
    "basic": {
        "label": "基础查看",
        "keys": {"name", "illuminant", "observer", "role", "L", "a", "b", "C", "h", "de00", "verdict"},
    },
    "chromaticity": {
        "label": "色度分析",
        "keys": {"name", "illuminant", "observer", "role", "L", "a", "b", "C", "h", "X", "Y", "Z"},
    },
    "difference": {
        "label": "色差分析",
        "keys": {"name", "illuminant", "observer", "role", "L", "a", "b", "C", "h",
                 "dL", "da", "db", "dC", "dh", "de76", "cmc", "de94", "de00", "verdict"},
    },
    "shade555": {
        "label": "555 分色",
        "keys": {"name", "illuminant", "observer", "role", "L", "a", "b",
                 "dL", "da", "db", "de00", "shade555", "verdict"},
    },
    "full": {
        "label": "完整数据",
        "keys": None,
    },
}


@lru_cache(maxsize=4096)
def _canonical_source_path_cached(source_file: str) -> str:
    """Canonicalise one source path once per distinct file.

    Palette imports can contain thousands of samples from the same QTX.  The
    historical key helper called ``Path.resolve()`` once per *sample*, which is
    especially expensive for UNC/network paths.  The canonical source is a
    property of the file, not of each sample, so cache it independently.
    """
    try:
        return str(Path(source_file).resolve())
    except Exception:
        return str(source_file)


@lru_cache(maxsize=None)
def _sample_key_cached(source_file: str, sample_id: str) -> str:
    return f"{_canonical_source_path_cached(source_file)}|{sample_id}"


def sample_key(sample: Sample) -> str:
    """Stable application key for one physical measurement instance.

    Most samples are identified by ``source_file | sample_id`` exactly as in
    every earlier release.  Palette-local imports may contain repeated GUIDs /
    sample IDs inside one QTX/CPX/Excel source.  In that special case an
    internal ``__palette_instance`` marker is appended to the *key only*.
    Exported sample IDs and measured colour data stay unchanged.
    """
    raw = sample.raw or {}
    instance = str(raw.get('__palette_instance', '') or '').strip()
    sample_id = str(sample.sample_id)
    if instance:
        sample_id = f"{sample_id}~@{instance}"
    return _sample_key_cached(sample.source_file, sample_id)


def _palette_disambiguate_duplicate_samples(samples: list[Sample]) -> list[Sample]:
    """Preserve repeated measurement records in palette-local imports.

    Datacolor exports can legally contain two records with the same GUID/name.
    The palette editor is a sequence editor, so silently collapsing those
    records loses slots and later causes exports such as 309 -> 305 colours.
    Only duplicated base keys receive an internal occurrence marker.
    """
    counts: dict[tuple[str, str], int] = {}
    totals: dict[tuple[str, str], int] = {}
    for sm in samples:
        base = (_canonical_source_path_cached(sm.source_file), str(sm.sample_id))
        totals[base] = totals.get(base, 0) + 1
    out: list[Sample] = []
    for sm in samples:
        base = (_canonical_source_path_cached(sm.source_file), str(sm.sample_id))
        if totals.get(base, 0) <= 1:
            out.append(sm); continue
        counts[base] = counts.get(base, 0) + 1
        occurrence=counts[base]
        # Keep occurrence 1 on the historical key for backward compatibility;
        # only the additional repeated measurements need instance suffixes.
        if occurrence == 1:
            out.append(sm); continue
        raw = dict(sm.raw or {})
        raw['__palette_instance'] = str(occurrence)
        out.append(replace(sm, raw=raw))
    return out


def lab_to_qcolor(lab: tuple[float, float, float]) -> QColor:
    from qtx_core.preview import lab_to_srgb_preview
    return QColor.fromRgbF(*lab_to_srgb_preview(lab).rgb)


def fluorescent_lab_to_qcolor(lab: tuple[float, float, float]) -> QColor:
    from qtx_core.preview import lab_to_srgb_preview
    return QColor.fromRgbF(*lab_to_srgb_preview(lab, strategy='clip').rgb)


def fluorescence_assessment(sample: Sample) -> dict:
    """Assess fluorescence from the measured reflectance curve only.

    ChromaShare CPX files exported by the user place the folded-corner samples
    in the fluorescent column; those samples have apparent reflectance above
    100%.  That is a strong spectral signature of fluorescent contribution.
    Metadata such as ``UV Cal`` or a colour name is deliberately *not* used.
    Manual palette overrides remain available for exceptional production cases.
    """
    if sample is None or not sample.has_spectrum():
        return {"fluorescent": False, "max_r": None, "max_nm": None, "reason": "无完整反射率"}
    vals=[float(v) for v in sample.reflectance]; waves=[int(w) for w in sample.wavelengths]
    if not vals:
        return {"fluorescent": False, "max_r": None, "max_nm": None, "reason": "无完整反射率"}
    idx=max(range(len(vals)), key=vals.__getitem__); mx=vals[idx]; nm=waves[idx]
    fluorescent=mx > 100.0
    reason=(f"最大表观反射率 {mx:.2f}% @ {nm} nm，超过 100%" if fluorescent
            else f"最大表观反射率 {mx:.2f}% @ {nm} nm，未超过 100%")
    return {"fluorescent": fluorescent, "max_r": mx, "max_nm": nm, "reason": reason}


def sample_is_fluorescent(sample: Sample) -> bool:
    # P3-3 library cards may intentionally carry only the scalar index.  Preserve
    # the existing folded-corner/fluorescent preview without loading the spectrum.
    try:
        flag=str((sample.raw or {}).get('__FLUORESCENT_INDEX',''))
        if flag in {'0','1'}:
            return flag=='1'
    except Exception:
        pass
    return bool(fluorescence_assessment(sample).get("fluorescent"))

@lru_cache(maxsize=8192)
def _display_lab_d65_2_cached(reflectance: tuple[float, ...], wavelengths: tuple[int, ...]):
    """Display-only Lab under the sRGB reference condition (D65 / 2°)."""
    return reflectance_to_xyz_lab(reflectance, 'D65', wavelengths, 2)[1]


def sample_display_qcolor(sample: Sample, lab: tuple[float, float, float]) -> QColor:
    """Colour-managed *preview* for QTX/CPX palette cards.

    sRGB itself is defined against a D65/2° reference.  Earlier builds fed the
    stored D65/10° Lab directly into a D65/2° Lab->sRGB transform.  When a real
    reflectance curve is available, HF73 first recalculates display Lab under
    D65/2° and then applies the existing constant-hue/lightness gamut mapping.
    This changes screen pixels only: original spectra, stored Lab, ΔE, sorting,
    QTX/CPX/Excel exports and all scientific calculations remain untouched.

    A flat monitor swatch still cannot reproduce yarn/knit texture, gloss, SCE
    versus SCI appearance, fluorescence energy, or colours outside the monitor
    gamut; those remain measurement/appearance properties, not RGB failures.
    """
    source_lab = tuple(map(float, lab))
    if not all(math.isfinite(v) for v in source_lab):
        return QColor('#DDE3EC')
    display_lab = source_lab
    if sample is not None and sample.has_spectrum():
        try:
            display_lab = _display_lab_d65_2_cached(
                tuple(float(v) for v in sample.reflectance),
                tuple(int(w) for w in sample.wavelengths),
            )
        except Exception:
            display_lab = source_lab
    L, a, b = map(float, display_lab)
    if sample_is_fluorescent(sample) or (L >= 94 and math.hypot(a, b) >= 35):
        return fluorescent_lab_to_qcolor(display_lab)
    return lab_to_qcolor(display_lab)



def _index_preview_qcolor(row: dict | None) -> QColor:
    """Fast swatch preview from the scalar sample index only.

    This deliberately does not hydrate a full Sample/spectrum.  It preserves the
    frozen large-library performance path while keeping picker previews consistent
    with the existing fluorescent-index rule.  The currently selected sample may
    still be hydrated later for an exact spectral preview.
    """
    row = row or {}
    lab = row.get('lab_d65_10')
    try:
        if lab is None or len(lab) < 3:
            return QColor('#DDE3EC')
        lab = tuple(float(v) for v in lab[:3])
        return fluorescent_lab_to_qcolor(lab) if bool(row.get('fluorescent')) else lab_to_qcolor(lab)
    except Exception:
        return QColor('#DDE3EC')


@lru_cache(maxsize=8192)
def _picker_swatch_icon(color_name: str, width: int = 26, height: int = 16) -> QIcon:
    """Small cached colour chip used by tree pickers; no QWidget-per-sample cost."""
    pix = QPixmap(max(12, int(width)), max(10, int(height)))
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(0.5, 0.5, pix.width()-1.0, pix.height()-1.0)
    painter.setPen(QPen(QColor('#CBD5E1'), 1))
    painter.setBrush(QColor(str(color_name or '#DDE3EC')))
    painter.drawRoundedRect(rect, 2.5, 2.5)
    painter.end()
    return QIcon(pix)


def _index_row_swatch_icon(row: dict | None) -> QIcon:
    return _picker_swatch_icon(_index_preview_qcolor(row).name())


def _friendly_qtx_picker_name(path: str, customer: str = '') -> str:
    """Hide storage GUID filenames from UI while leaving the backing path untouched."""
    raw = Path(str(path or '')).name or str(path or '')
    stem = Path(raw).stem
    compact = re.sub(r'[-_]', '', stem)
    if len(compact) >= 24 and re.fullmatch(r'[0-9a-fA-F]+', compact or ''):
        label = (str(customer or '').replace('\\','/').rstrip('/').split('/')[-1] or '官方')
        return f'{label} 色库数据'
    return raw


CARD_MIME = "application/x-chromatic-sample-key"



class CardSourceListWidget(QListWidget):
    """左侧色库：支持单个或批量色样拖到右侧方案。"""
    def startDrag(self, supportedActions):
        items = [it for it in self.selectedItems() if it.data(Qt.UserRole)]
        if not items:
            item=self.currentItem(); items=[item] if item and item.data(Qt.UserRole) else []
        if not items:return
        keys=[str(it.data(Qt.UserRole)) for it in items]
        mime=QMimeData(); mime.setData(CARD_MIME, "\n".join(keys).encode("utf-8"))
        drag=QDrag(self); drag.setMimeData(mime); drag.setPixmap(items[0].icon().pixmap(QSize(84,56))); drag.exec(Qt.CopyAction)


class CardGridListWidget(QListWidget):
    """固定槽位色卡网格。外部拖入替换目标空位；内部拖动交换两个槽位。"""
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.owner:
            return
        # 只对控件本身的真实尺寸变化做一次延迟适配。
        # 不再跟随 viewport/滚动条的细小变化反复计算，否则会形成
        # “格子尺寸 -> 滚动条出现/消失 -> viewport 改变 -> 再算格子”的反馈循环。
        new_size = event.size()
        old = getattr(self, "_last_outer_size", QSize())
        self._last_outer_size = new_size
        if old.isValid() and abs(new_size.width()-old.width()) < 3 and abs(new_size.height()-old.height()) < 3:
            return
        if hasattr(self.owner, "_card_resize_timer"):
            self.owner._card_resize_timer.start(160)
        else:
            QTimer.singleShot(160, self.owner.update_adaptive_card_grid)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME) or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME) or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        self._drag_row = self.row(item)
        mime = QMimeData()
        mime.setData("application/x-chromatic-grid-row", str(self._drag_row).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(self.iconSize()))
        drag.exec(Qt.MoveAction)

    def dropEvent(self, event):
        target = self.indexAt(event.position().toPoint()).row()
        if target < 0:
            target = self.count() - 1
        if event.mimeData().hasFormat(CARD_MIME):
            key = bytes(event.mimeData().data(CARD_MIME)).decode("utf-8")
            self.owner.place_sample_in_card_slot(key, target)
            event.acceptProposedAction()
            return
        if event.source() is self and event.mimeData().hasFormat("application/x-chromatic-grid-row"):
            source = int(bytes(event.mimeData().data("application/x-chromatic-grid-row")).decode())
            self.owner.swap_card_slots(source, target)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class QtxWorkspaceList(QListWidget):
    """Windows-like QTX workspace: file drop, multi-select, blank-area context menu."""
    filesDropped = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setToolTip("拖入 QTX / Excel 或整个文件夹；Ctrl/Shift 多选，右键可打开 QTX 文件夹")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragEnterEvent(event)
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragMoveEvent(event)
    def dropEvent(self, event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths); event.acceptProposedAction(); return
        super().dropEvent(event)

class ColorTile(QPushButton):
    toggledForSample = Signal(str, bool)

    def __init__(self, sample: Sample, owner: "MainWindow"):
        super().__init__()
        self.sample, self.owner = sample, owner
        self._cached_color = None
        self._cached_lab = None
        self._drag_start = None
        self.setCheckable(True)
        self.setFixedSize(136, 134)
        self._card_width = 136
        self.setCursor(Qt.PointingHandCursor)
        # HF113a: use the widget's native context-menu event instead of the
        # CustomContextMenu signal.  On some Windows/MDI combinations the
        # signal was not delivered reliably for recycled library colour tiles,
        # making the formal-library right-click menu appear to be missing.
        # This change is intentionally scoped to ColorTile only.
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.toggled.connect(lambda checked: self.toggledForSample.emit(sample_key(self.sample), checked))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
            self.owner.handle_library_tile_click(self, event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """Open the existing library/find-result menu reliably on right click.

        The menu implementation itself is unchanged; only event delivery is
        made explicit so no other library behaviour or permissions are altered.
        """
        self.owner.show_tile_menu(self.sample, event.globalPos())
        event.accept()

    def mouseMoveEvent(self, event):
        """Copy selected formal-library colour blocks into an open workbench.

        The MIME format already powers the palette editor; reusing it keeps the
        source sample immutable and makes a cross-window drag a copy operation.
        """
        if (event.buttons() & Qt.LeftButton) and self._drag_start is not None:
            distance=(event.position().toPoint()-self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                current=sample_key(self.sample)
                selected=list(getattr(self.owner,'library_selected_keys',set()) or {current})
                if current not in selected:selected=[current]
                mime=QMimeData(); mime.setData(CARD_MIME,"\n".join(selected).encode('utf-8'))
                drag=QDrag(self); drag.setMimeData(mime)
                drag.setPixmap(self.grab().scaled(112,112,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                self._drag_start=None
                drag.exec(Qt.CopyAction)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start=None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.owner.show_sample_details_dialog(self.sample)
            event.accept(); return
        super().mouseDoubleClickEvent(event)

    def refresh_color(self):
        self._cached_color = None
        self._cached_lab = None
        self.update()

    def set_card_width(self, width: int):
        """Resize only when geometry really changes; repeated setFixedSize invalidates layouts."""
        width=max(124,min(176,int(width)))
        if getattr(self, '_card_width', None) == width and self.height() == 138:
            return
        self._card_width = width
        self.setFixedSize(width, 138)

    def sizeHint(self):
        return self.size()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(3, 3, -3, -3); path = QPainterPath(); path.addRoundedRect(box, 8, 8)
        p.fillPath(path, QColor("#FFFFFF")); p.setClipPath(path)
        if self._cached_color is None:
            self._cached_lab = self.owner.sample_lab(self.sample)
            self._cached_color = sample_display_qcolor(self.sample, self._cached_lab)
        lab = self._cached_lab
        color = self._cached_color
        is_find_result=bool(self.property('findResultCard'))
        # HF132: 查询结果卡片在紧凑/小屏模式下也必须完整显示 ΔE 与 LAB。
        # 旧的 68px 色块 + 34px 双行名称会把最后一行 LAB 挤出 136px 卡片。
        # 这里仅压缩查询结果卡片内部的色块/文字垂直占用，不改变结果数据、排序或交互。
        swatch_h=(60.0 if is_find_result else max(78.0,min(88.0,box.height()*0.62)))
        p.fillRect(QRectF(box.left(), box.top(), box.width(), swatch_h), color)
        # CPX / QTX fluorescence visual marker only. It does not alter the parsed
        # sample or CPX slot layout: fluorescent samples keep the traditional
        # folded upper-right corner that users rely on visually.
        if sample_is_fluorescent(self.sample):
            fold=16.0
            tri=QPainterPath(); tri.moveTo(box.right()-fold,box.top()); tri.lineTo(box.right(),box.top()); tri.lineTo(box.right(),box.top()+fold); tri.closeSubpath()
            p.fillPath(tri,QColor('#F8FAFD')); p.setPen(QPen(QColor('#D4DCE8'),1)); p.drawLine(QPointF(box.right()-fold,box.top()),QPointF(box.right(),box.top()+fold))
        p.setClipping(False)
        border = QColor("#3B82F6") if self.isChecked() else QColor("#E5E7EB")
        p.setPen(QPen(border, 2 if self.isChecked() else 1)); p.drawPath(path)
        p.setPen(QColor("#1F2937")); f=p.font(); f.setBold(True); f.setPointSizeF(9.2 if is_find_result else 9.0); p.setFont(f); y = box.top()+swatch_h+7
        if is_find_result:
            # HF132: 名称仍保留两行，但把名称区压到 30px，给 ΔE + LAB 两行留足空间。
            p.drawText(QRectF(box.left()+10, y, box.width()-20, 30), Qt.AlignLeft|Qt.AlignTop|Qt.TextWordWrap, self.sample.display_name)
            info_y=y+31
        else:
            fm=QFontMetrics(f); name=fm.elidedText(self.sample.display_name,Qt.ElideRight,max(20,int(box.width()-20)))
            p.drawText(QRectF(box.left()+10, y, box.width()-20, 20), Qt.AlignLeft|Qt.AlignVCenter, name)
            info_y=y+21
        f.setBold(False); f.setPointSizeF(8.5); p.setFont(f); p.setPen(QColor("#6B7280"))
        de = self.owner.find_scores.get(sample_key(self.sample)) if self.owner.find_mode_active else None
        if de is not None:
            # HF132: 15px 行高可以在 136px 紧凑卡片中完整容纳两行数据。
            p.drawText(QRectF(box.left()+10, info_y, box.width()-20, 15), Qt.AlignLeft|Qt.AlignVCenter, f"ΔE {de:.3f}")
            p.drawText(QRectF(box.left()+10, info_y+15, box.width()-20, 15), Qt.AlignLeft|Qt.AlignVCenter, f"L* {lab[0]:.2f}  a* {lab[1]:.2f}  b* {lab[2]:.2f}")
        else:
            p.drawText(QRectF(box.left()+10, info_y, box.width()-20, 17), Qt.AlignLeft|Qt.AlignVCenter, f"L* {lab[0]:.2f}  a* {lab[1]:.2f}  b* {lab[2]:.2f}")



class InteractiveAnalysisWidget(QWidget):
    """Workbench analysis surface for exactly one standard/current pair.

    The data table owns selection.  Analysis widgets never choose an arbitrary
    batch behind the user's back: no current batch means an explicit empty
    state.  This keeps multi-selection useful for batch actions while the
    *current row* remains the single analysis target.
    """
    sampleActivated = Signal(str)

    def __init__(self, mode, owner, wb_id, parent=None):
        super().__init__(parent)
        self.mode=mode; self.owner=owner; self.wb_id=wb_id; self.hover_key=None
        self.setMouseTracking(True); self.setToolTipDuration(12000)
        self._contour_cache={}; self._points=[]; self._curves=[]
        mins={"compare":QSize(220,300),"spectrum":QSize(420,260),"delta_r":QSize(420,120),"delta":QSize(330,360),"wheel":QSize(300,300)}
        self.setMinimumSize(mins.get(mode,QSize(220,175)))

    def _data(self):
        wb=self.owner._find_workbench(self.wb_id); samples=self.owner._workbench_samples(wb) if wb else []
        ref=None
        if wb:
            if wb.get('standard_key')=='__AVERAGE__' and wb.get('average_standard'):
                try: ref=self.owner._deserialize_sample(wb['average_standard'])
                except Exception: ref=None
            elif wb.get('standard_key'):
                ref=next((x for x in samples if sample_key(x)==wb['standard_key']),None)
        return wb,samples,ref

    def _focus_sample(self, samples, ref=None):
        """Return only the explicit current analysis row; never auto-pick one."""
        focus_key=getattr(self.owner,'_workbench_focus_keys',{}).get(self.wb_id)
        if not focus_key:return None
        found=next((x for x in samples if sample_key(x)==focus_key),None)
        if found is None:return None
        if ref is not None and sample_key(found)==sample_key(ref):return None
        return found

    def _tooltip_text(self, x, extra=''):
        L,a,b=self.owner.sample_lab(x); C=math.hypot(a,b); h=math.degrees(math.atan2(b,a))%360
        src=Path(x.source_file).name if x.source_file else '—'
        base=f'{x.display_name}\nQTX：{src}\nL* {L:.3f}   a* {a:.3f}   b* {b:.3f}\nC* {C:.3f}   h° {h:.1f}'
        return base + ("\n"+extra if extra else '')

    @staticmethod
    def _direction_name(da, db):
        if abs(da)<1e-9 and abs(db)<1e-9:return '接近标准中心'
        deg=(math.degrees(math.atan2(db,da))+360)%360
        names=['偏红','偏黄红','偏黄','偏黄绿','偏绿','偏蓝绿','偏蓝','偏蓝红/紫']
        return names[int(((deg+22.5)%360)//45)]

    def _tolerance_points(self, ref_lab, threshold):
        """Numerically solve the active formula's equal-difference contour at ΔL*=0."""
        formula=getattr(self.owner,'active_formula','delta_e00')
        cache_key=(tuple(ref_lab),float(threshold),formula)
        if cache_key in self._contour_cache:return self._contour_cache[cache_key]
        method={'delta_e00':'CIE 2000','delta_e94':'CIE 1994','delta_e76':'CIE 1976','cmc21':'CMC'}.get(formula,'CIE 2000')
        kwargs={'textiles':True} if formula=='delta_e94' else ({'l':2,'c':1} if formula=='cmc21' else {})
        pts=[]
        for deg in range(0,360,5):
            ang=math.radians(deg); lo,hi=0.0,max(1.0,threshold*4.0)
            def de_at(rad):
                lab=(ref_lab[0],ref_lab[1]+math.cos(ang)*rad,ref_lab[2]+math.sin(ang)*rad)
                try:return delta_e(ref_lab,lab,method,**kwargs)
                except Exception:return float('inf')
            while de_at(hi)<threshold and hi<200:hi*=1.7
            for _ in range(18):
                mid=(lo+hi)/2
                if de_at(mid)<threshold:lo=mid
                else:hi=mid
            rad=(lo+hi)/2; pts.append((math.cos(ang)*rad,math.sin(ang)*rad))
        self._contour_cache={cache_key:pts}
        return pts

    @staticmethod
    def _nice_step(target):
        target=max(1e-9,float(target)); exp=10**math.floor(math.log10(target)); q=target/exp
        if q<=1:return 1*exp
        if q<=2:return 2*exp
        if q<=5:return 5*exp
        return 10*exp

    def _pair_de(self, wb, ref, current):
        if ref is None or current is None:return float('nan')
        conditions=(wb.get('illuminants') or ['D65']) if wb else ['D65']
        illum=display_illuminant(conditions[0]) if conditions else 'D65'; obs=int((wb or {}).get('observer',10))
        try:
            pair=self.owner._workbench_pair_analysis(ref,current,illum,obs)
            return float({'delta_e00':pair.delta_e00,'delta_e94':pair.delta_e94,'delta_e76':pair.delta_e76,'cmc21':pair.cmc21}.get(self.owner.active_formula,pair.delta_e00))
        except Exception:return float('nan')

    def _pair_pen_colors(self, wb, ref, current):
        """Use true swatch colours unless near-identical curves need guaranteed contrast."""
        if ref is None or current is None:return QColor('#F59E0B'),QColor('#2563EB')
        de=self._pair_de(wb,ref,current)
        if math.isfinite(de) and de<1.0:return QColor('#F59E0B'),QColor('#2563EB')
        return sample_display_qcolor(ref,self.owner.sample_lab(ref)),sample_display_qcolor(current,self.owner.sample_lab(current))

    def _empty(self,p,rect,text):
        p.setPen(QColor('#94A3B8')); f=p.font(); f.setPointSizeF(9.0); f.setBold(False); p.setFont(f)
        p.drawText(rect.toRect(),Qt.AlignCenter,text)

    def _draw_pair_compare(self,p,wb,ref,current):
        p.fillRect(self.rect(),QColor('#F8FAFC')); p.setPen(QColor('#0F172A'))
        f=p.font(); f.setPointSizeF(10.5); f.setBold(True); p.setFont(f); p.drawText(14,25,'色样对照')
        inner=QRectF(self.rect()).adjusted(12,38,-12,-12)
        if ref is None:
            self._empty(p,inner,'请先设置标准样'); return
        gap=14; h=(inner.height()-gap)/2
        cards=[('标准样',ref,QRectF(inner.left(),inner.top(),inner.width(),h),'#475569')]
        cards.append(('对比样',current,QRectF(inner.left(),inner.top()+h+gap,inner.width(),h),'#2563EB'))
        for label,sm,rect,accent in cards:
            p.setPen(QPen(QColor('#DCE4EF'),1)); p.setBrush(QColor('#FFFFFF')); p.drawRoundedRect(rect,10,10)
            f.setPointSizeF(8.2); f.setBold(True); p.setFont(f); p.setPen(QColor(accent)); p.drawText(QRectF(rect.left()+12,rect.top()+10,rect.width()-24,18),Qt.AlignLeft|Qt.AlignVCenter,label)
            if sm is None:
                p.setPen(QPen(QColor('#CBD5E1'),1,Qt.DashLine)); p.setBrush(QColor('#F8FAFC'))
                ph=QRectF(rect.left()+12,rect.top()+34,rect.width()-24,max(52,rect.height()-66)); p.drawRoundedRect(ph,8,8)
                p.setPen(QColor('#94A3B8')); p.drawText(ph,Qt.AlignCenter,'请在数据表选择一个批次样')
                continue
            lab=self.owner.sample_lab(sm); col=sample_display_qcolor(sm,lab)
            sw=QRectF(rect.left()+12,rect.top()+34,rect.width()-24,max(54,rect.height()-70))
            p.setPen(QPen(QColor('#E2E8F0'),1)); p.setBrush(col); p.drawRoundedRect(sw,8,8)
            f.setPointSizeF(8.5); f.setBold(True); p.setFont(f); p.setPen(QColor('#1E293B'))
            name=QFontMetrics(f).elidedText(sm.display_name,Qt.ElideRight,max(60,int(rect.width()-24)))
            p.drawText(QRectF(rect.left()+12,rect.bottom()-31,rect.width()-24,20),Qt.AlignLeft|Qt.AlignVCenter,name)

    def _draw_delta(self,p,wb,ref,current):
        p.fillRect(self.rect(),QColor('#FFFFFF'))
        f=p.font(); f.setPointSizeF(10.5); f.setBold(True); p.setFont(f); p.setPen(QColor('#0F172A'))
        formula=FORMULA_LABELS.get(self.owner.active_formula,self.owner.active_formula)
        p.drawText(14,25,f'Δa*/Δb* 容差分布')
        r=QRectF(self.rect()).adjusted(58,48,-24,-58)
        if ref is None:
            self._empty(p,r,'请先设置标准样'); return
        lr=self.owner.sample_lab(ref); current_lab=self.owner.sample_lab(current) if current is not None else None
        da=(current_lab[1]-lr[1]) if current_lab else 0.0; db=(current_lab[2]-lr[2]) if current_lab else 0.0; dL=(current_lab[0]-lr[0]) if current_lab else 0.0
        threshold=float(self.owner.thresholds.get(self.owner.active_formula,2.0)); contour=self._tolerance_points(lr,threshold)
        contour_extent=max([max(abs(a),abs(b)) for a,b in contour] or [0.2]); point_extent=max(abs(da),abs(db)) if current_lab else 0.0
        raw=max(0.18,contour_extent*1.8,point_extent*1.35)
        step=self._nice_step(raw/4.0); limit=max(step,math.ceil(raw/step)*step)
        def mappt(x,y):return QPointF(r.center().x()+x/limit*r.width()/2,r.center().y()-y/limit*r.height()/2)
        # grid + numeric ticks
        p.setPen(QPen(QColor('#E7EDF5'),1)); f.setPointSizeF(7.6); f.setBold(False); p.setFont(f)
        n=int(math.floor(limit/step+1e-9))
        for i in range(-n,n+1):
            v=i*step; pt_x=mappt(v,0).x(); pt_y=mappt(0,v).y()
            p.drawLine(QPointF(pt_x,r.top()),QPointF(pt_x,r.bottom())); p.drawLine(QPointF(r.left(),pt_y),QPointF(r.right(),pt_y))
            p.setPen(QColor('#64748B')); txt=f'{v:g}'
            p.drawText(QRectF(pt_x-24,r.bottom()+5,48,16),Qt.AlignCenter,txt)
            if abs(v)>1e-12:p.drawText(QRectF(r.left()-47,pt_y-8,40,16),Qt.AlignRight|Qt.AlignVCenter,txt)
            p.setPen(QPen(QColor('#E7EDF5'),1))
        # zero axes
        p.setPen(QPen(QColor('#AAB5C3'),1.2)); p.drawLine(QPointF(r.center().x(),r.top()),QPointF(r.center().x(),r.bottom())); p.drawLine(QPointF(r.left(),r.center().y()),QPointF(r.right(),r.center().y()))
        p.setPen(QColor('#475569')); p.drawText(QRectF(r.right()-34,r.center().y()+4,32,18),Qt.AlignRight,'+a*'); p.drawText(QRectF(r.left()+4,r.top()+2,34,18),Qt.AlignLeft,'+b*')
        # tolerance boundary deliberately occupies only about half of the plotting field
        if contour:
            path=QPainterPath()
            for i,(a0,b0) in enumerate(contour):
                q=mappt(a0,b0); path.moveTo(q) if i==0 else path.lineTo(q)
            path.closeSubpath(); p.setPen(QPen(QColor('#D6A12E'),1.5,Qt.DashLine)); p.setBrush(QColor(249,220,144,35)); p.drawPath(path)
        # standard at origin, current hollow circle; no labels/connector line to avoid clutter
        q0=mappt(0,0); p.setBrush(sample_display_qcolor(ref,lr)); p.setPen(QPen(QColor('#1F2937'),2)); p.drawEllipse(q0,7,7)
        self._points.append((q0,ref,(0.0,0.0,0.0)))
        if current is not None:
            q1=mappt(da,db); p.setBrush(QColor('#FFFFFF')); p.setPen(QPen(QColor('#2563EB'),2.2)); p.drawEllipse(q1,7,7); self._points.append((q1,current,(da,db,dL)))
        else:
            self._empty(p,QRectF(r.left()+20,r.top()+20,r.width()-40,26),'请选择一个批次样进行分析')
        # compact legend bottom right
        box=QRectF(r.right()-150,r.bottom()-65,140,55); p.setPen(QPen(QColor('#E2E8F0'),1)); p.setBrush(QColor(255,255,255,235)); p.drawRoundedRect(box,7,7)
        p.setBrush(sample_display_qcolor(ref,lr)); p.setPen(QPen(QColor('#1F2937'),1.5)); p.drawEllipse(QPointF(box.left()+14,box.top()+15),5,5)
        p.setPen(QColor('#475569')); p.drawText(QRectF(box.left()+26,box.top()+7,105,17),Qt.AlignLeft|Qt.AlignVCenter,'标准样')
        p.setBrush(QColor('#FFFFFF')); p.setPen(QPen(QColor('#2563EB'),1.8)); p.drawEllipse(QPointF(box.left()+14,box.top()+35),5,5); p.setPen(QColor('#475569')); p.drawText(QRectF(box.left()+26,box.top()+27,105,17),Qt.AlignLeft|Qt.AlignVCenter,'对比样')
        p.setPen(QColor('#A16207')); p.drawText(QRectF(14,self.height()-31,self.width()-28,20),Qt.AlignLeft|Qt.AlignVCenter,f'容差边界：{formula} ≤ {threshold:g}')

    def _common_spectral_pair(self,ref,current):
        if ref is None or current is None or not ref.has_spectrum() or not current.has_spectrum():return []
        a={int(w):float(v) for w,v in zip(ref.wavelengths,ref.reflectance)}; b={int(w):float(v) for w,v in zip(current.wavelengths,current.reflectance)}
        return [(w,a[w],b[w]) for w in sorted(set(a)&set(b))]

    def _draw_spectrum(self,p,wb,ref,current):
        p.fillRect(self.rect(),QColor('#FFFFFF')); p.setPen(QColor('#0F172A')); f=p.font(); f.setPointSizeF(10.5); f.setBold(True); p.setFont(f); p.drawText(14,25,'反射率曲线')
        r=QRectF(self.rect()).adjusted(66,48,-24,-55)
        if ref is None or current is None:
            self._empty(p,r,'请在数据表选择一个批次样'); return
        pair=self._common_spectral_pair(ref,current)
        if not pair:
            self._empty(p,r,'标准样或对比样没有可比较的原始反射率数据'); return
        lo,hi=360.0,700.0; ymax=max(100.0,math.ceil(max(max(a,b) for _,a,b in pair)/20.0)*20.0)
        def mp(w,v):return QPointF(r.left()+(w-lo)/(hi-lo)*r.width(),r.bottom()-max(0.0,min(ymax,v))/ymax*r.height())
        # grid and Y ticks 0...100 (and beyond for fluorescent values)
        p.setPen(QPen(QColor('#E7EDF5'),1)); f.setPointSizeF(7.7); f.setBold(False); p.setFont(f)
        yt=0
        while yt<=ymax+1e-9:
            y=mp(lo,yt).y(); p.drawLine(QPointF(r.left(),y),QPointF(r.right(),y)); p.setPen(QColor('#64748B')); p.drawText(QRectF(r.left()-42,y-8,34,16),Qt.AlignRight|Qt.AlignVCenter,f'{int(yt)}'); p.setPen(QPen(QColor('#E7EDF5'),1)); yt+=20
        for wl in (360,400,450,500,550,600,650,700):
            x=mp(wl,0).x(); p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom())); p.setPen(QColor('#64748B')); p.drawText(QRectF(x-25,r.bottom()+7,50,17),Qt.AlignCenter,str(wl)); p.setPen(QPen(QColor('#E7EDF5'),1))
        # axis labels
        p.setPen(QColor('#475569')); p.drawText(QRectF(r.left(),r.bottom()+27,r.width(),18),Qt.AlignCenter,'波长 (nm)')
        p.save(); p.translate(17,r.center().y()); p.rotate(-90); p.drawText(QRectF(-70,-10,140,20),Qt.AlignCenter,'Reflectance (%)'); p.restore()
        ref_col,cur_col=self._pair_pen_colors(wb,ref,current)
        for sm,which,col,style in ((ref,1,ref_col,Qt.SolidLine),(current,2,cur_col,Qt.DashLine)):
            path=QPainterPath(); pts=[]
            for w,a,b in pair:
                v=a if which==1 else b; q=mp(w,v); pts.append(q); path.moveTo(q) if len(pts)==1 else path.lineTo(q)
            pen=QPen(col,2.2,style); p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(path)
            # every measurement point is visible; typical QTX interval is 10 nm
            p.setBrush(col); p.setPen(QPen(col,1))
            for q in pts:p.drawEllipse(q,2.2,2.2)
            self._curves.append((pts,sm))
        # internal legend: line style + full sample name whenever the current plot width allows it.
        # HF133: do not use a fixed 210 px name width; that truncated long production sample names
        # even when the chart had plenty of horizontal space.
        legend_y=r.top()+10; legend_x=r.left()+12
        legend_text_x=legend_x+36
        legend_text_w=max(96.0, r.right()-legend_text_x-12.0)
        legend_font=QFont(f); legend_font.setBold(False); legend_font.setPointSizeF(8.0)
        for label,sm,col,style in (('标准样',ref,ref_col,Qt.SolidLine),('对比样',current,cur_col,Qt.DashLine)):
            p.setPen(QPen(col,2.4,style)); p.drawLine(QPointF(legend_x,legend_y+6),QPointF(legend_x+28,legend_y+6)); p.setPen(QColor('#334155'))
            full_text=f'{label}  {sm.display_name}'
            draw_font=QFont(legend_font)
            # Prefer the complete name.  On genuinely narrow windows, reduce the legend font
            # slightly before falling back to a middle ellipsis, preserving both ends of codes.
            for pt in (8.0,7.8,7.6,7.4,7.2,7.0):
                draw_font.setPointSizeF(pt)
                if QFontMetrics(draw_font).horizontalAdvance(full_text) <= int(legend_text_w):
                    break
            fm=QFontMetrics(draw_font)
            text=full_text if fm.horizontalAdvance(full_text) <= int(legend_text_w) else fm.elidedText(full_text,Qt.ElideMiddle,int(legend_text_w))
            p.setFont(draw_font)
            p.drawText(QRectF(legend_text_x,legend_y-3,legend_text_w,18),Qt.AlignLeft|Qt.AlignVCenter,text)
            legend_y+=22
        p.setFont(f)
        # ΔR summary
        max_row=max(pair,key=lambda t:abs(t[2]-t[1])); max_w,max_a,max_b=max_row; d=max_b-max_a
        p.setPen(QColor('#475569')); p.drawText(QRectF(14,self.height()-28,self.width()-28,19),Qt.AlignLeft|Qt.AlignVCenter,f'ΔR 最大 {abs(d):.2f}% @ {max_w} nm')

    def _draw_delta_r(self,p,wb,ref,current):
        p.fillRect(self.rect(),QColor('#FFFFFF')); f=p.font(); f.setPointSizeF(9.3); f.setBold(True); p.setFont(f); p.setPen(QColor('#0F172A')); p.drawText(14,22,'反射率差值（对比样 − 标准样）')
        r=QRectF(self.rect()).adjusted(58,38,-24,-38)
        pair=self._common_spectral_pair(ref,current)
        if not pair:
            self._empty(p,r,'选择有光谱数据的批次样后显示 ΔR'); return
        vals=[b-a for _,a,b in pair]; bound=max(1.0,max(abs(v) for v in vals)*1.2); bound=math.ceil(bound*2)/2.0
        def mp(w,v):return QPointF(r.left()+(w-360)/340*r.width(),r.center().y()-v/bound*r.height()/2)
        p.setPen(QPen(QColor('#E7EDF5'),1));
        for wl in (360,400,450,500,550,600,650,700):
            x=mp(wl,0).x(); p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom()))
        p.setPen(QPen(QColor('#94A3B8'),1)); p.drawLine(QPointF(r.left(),r.center().y()),QPointF(r.right(),r.center().y()))
        barw=max(1.5,r.width()/max(1,len(pair))*0.55)
        for w,a,b in pair:
            v=b-a; q=mp(w,v); zero=mp(w,0); col=QColor('#EF4444') if v>=0 else QColor('#2563EB'); p.setPen(QPen(col,barw)); p.drawLine(QPointF(q.x(),zero.y()),q)
        p.setPen(QColor('#64748B')); f.setPointSizeF(7.4); f.setBold(False); p.setFont(f); p.drawText(QRectF(r.left()-42,r.top()-7,36,16),Qt.AlignRight,f'+{bound:g}'); p.drawText(QRectF(r.left()-42,r.center().y()-8,36,16),Qt.AlignRight,'0'); p.drawText(QRectF(r.left()-42,r.bottom()-8,36,16),Qt.AlignRight,f'-{bound:g}')

    def _draw_wheel(self,p,wb,samples,ref):
        # Legacy analytical wheel kept for compatibility although it is not in the HF116 default layout.
        p.fillRect(self.rect(),QColor('#FFFFFF')); r=QRectF(self.rect()).adjusted(48,40,-32,-42); c=r.center(); outer=min(r.width(),r.height())*.40; ring_w=max(18.0,outer*.18); inner=outer-ring_w
        p.setPen(QColor('#202936')); p.drawText(12,20,'色相环'); wheel_rect=QRectF(c.x()-outer,c.y()-outer,outer*2,outer*2); p.setPen(Qt.NoPen)
        for i in range(72):p.setBrush(QColor.fromHsv((i*5)%360,205,238,255)); p.drawPie(wheel_rect,int((i*5-2.5)*16),int(5.2*16))
        p.setBrush(QColor('#FFFFFF')); p.drawEllipse(c,inner,inner)
        if ref is None:return
        lr=self.owner.sample_lab(ref); focus=self._focus_sample(samples,ref)
        p.setPen(QPen(QColor('#202936'),1)); p.setBrush(lab_to_qcolor(lr)); p.drawEllipse(c,6,6)
        if focus is not None:
            l=self.owner.sample_lab(focus); da,db=l[1]-lr[1],l[2]-lr[2]; ang=math.atan2(db,da); mag=min(1.0,math.hypot(da,db)/max(1.0,float(self.owner.thresholds.get(self.owner.active_formula,2.0))*2)); q=QPointF(c.x()+math.cos(ang)*inner*.75*mag,c.y()-math.sin(ang)*inner*.75*mag); p.setBrush(sample_display_qcolor(focus,l)); p.setPen(QPen(QColor('#2563EB'),2)); p.drawEllipse(q,7,7)

    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); self._points=[]; self._curves=[]
        wb,samples,ref=self._data(); current=self._focus_sample(samples,ref)
        if self.mode=='compare':self._draw_pair_compare(p,wb,ref,current)
        elif self.mode=='delta':self._draw_delta(p,wb,ref,current)
        elif self.mode=='spectrum':self._draw_spectrum(p,wb,ref,current)
        elif self.mode=='delta_r':self._draw_delta_r(p,wb,ref,current)
        elif self.mode=='wheel':self._draw_wheel(p,wb,samples,ref)
        else:p.fillRect(self.rect(),QColor('#FFFFFF'))

    def mouseMoveEvent(self,e):
        pos=e.position(); best=None; dist=1e18
        if self.mode in ('delta','wheel'):
            for pt,x,v in self._points:
                d=(pt.x()-pos.x())**2+(pt.y()-pos.y())**2
                if d<dist:best=(x,v);dist=d
            if best and dist<400:
                x,v=best; self.hover_key=sample_key(x); extra=f'Δa* {v[0]:+.3f}   Δb* {v[1]:+.3f}   ΔL* {v[2]:+.3f}'; self.setToolTip(self._tooltip_text(x,extra)); self.update(); return
        elif self.mode=='spectrum':
            for pts,x in self._curves:
                for i,pt in enumerate(pts):
                    d=(pt.x()-pos.x())**2+(pt.y()-pos.y())**2
                    if d<dist:best=(x,i);dist=d
            if best and dist<225:
                x,i=best; self.hover_key=sample_key(x); self.setToolTip(self._tooltip_text(x,f'波长 {x.wavelengths[i]} nm\n反射率 {x.reflectance[i]:.4f}')); self.update(); return
        if self.hover_key:self.hover_key=None; self.setToolTip(''); self.update()

    def mousePressEvent(self,e):
        if self.hover_key:self.sampleActivated.emit(self.hover_key)

    def mouseDoubleClickEvent(self,e):
        titles={'delta':'Δa*/Δb* 容差分布','wheel':'色相环','compare':'色样对照','spectrum':'反射率曲线','delta_r':'反射率差值'}
        dlg=QDialog(self); dlg.setWindowTitle(titles.get(self.mode,'综合分析')); _fit_dialog_to_screen(dlg,1180,760)
        l=QVBoxLayout(dlg); w=InteractiveAnalysisWidget(self.mode,self.owner,self.wb_id); l.addWidget(w); dlg.exec()


class ResponsiveAnalysisDashboard(QWidget):
    """HF116 professional pair-analysis layout.

    Left: visual pair only.  Center: spectral evidence + ΔR.  Right: tolerance
    geometry.  The layout mirrors how QC work is read: what am I comparing,
    what happened spectrally, and is the colour-difference vector in tolerance.
    """
    def __init__(self, charts, parent=None):
        super().__init__(parent); self.charts=list(charts); self.setObjectName('analysisDashboard')
        self.setStyleSheet("QWidget#analysisDashboard{background:#F5F7FA;border:1px solid #E2E8F0;border-radius:12px;} QSplitter::handle{background:#E4EAF2;} QSplitter::handle:horizontal{width:5px;} QSplitter::handle:vertical{height:5px;}")
        root=QVBoxLayout(self); root.setContentsMargins(8,8,8,8); root.setSpacing(0)
        self.outer=QSplitter(Qt.Horizontal,self); self.outer.setChildrenCollapsible(False)
        if len(self.charts)>0:self.outer.addWidget(self.charts[0])
        center=QSplitter(Qt.Vertical,self.outer); center.setChildrenCollapsible(False)
        if len(self.charts)>1:center.addWidget(self.charts[1])
        if len(self.charts)>2:center.addWidget(self.charts[2])
        center.setStretchFactor(0,4); center.setStretchFactor(1,1)
        if len(self.charts)>3:self.outer.addWidget(self.charts[3])
        self.outer.setStretchFactor(0,1); self.outer.setStretchFactor(1,3); self.outer.setStretchFactor(2,2)
        root.addWidget(self.outer); self.setMinimumHeight(500)
        QTimer.singleShot(0,lambda:self.outer.setSizes([250,650,430]))
        QTimer.singleShot(0,lambda:center.setSizes([330,130]))

def _fit_dialog_to_screen(dialog,width,height):
    """Bound independent dialogs to the current monitor's available logical area."""
    screen=dialog.screen() or QApplication.primaryScreen()
    if screen is None:dialog.resize(width,height);return
    available=screen.availableGeometry()
    dialog.resize(min(width,max(300,available.width()-32)),min(height,max(260,available.height()-48)))


class Lab2DDialog(QDialog):
    """CIELAB a*b* 二维分布图：颜色点 + 十字坐标；悬停显示 LABCH。"""
    def __init__(self, samples, owner):
        super().__init__(owner); self.samples=list(samples); self.owner=owner; self.setWindowTitle('2D CIELAB · a*b* 色彩空间'); _fit_dialog_to_screen(self,920,720)
        self.view=_Lab2DView(self.samples,owner,self); lay=QVBoxLayout(self); lay.setContentsMargins(10,10,10,10); lay.addWidget(self.view)

class _Lab2DView(QWidget):
    def __init__(self,samples,owner,parent=None):
        super().__init__(parent); self.owner=owner; self.data=[]; self.points=[]; self.lpoints=[]; self.setMouseTracking(True)
        for sm in samples:
            lab=owner.sample_lab(sm); L,a,b=lab; C=math.hypot(a,b); h=(math.degrees(math.atan2(b,a))+360)%360; self.data.append((sm,lab,C,h,sample_display_qcolor(sm,lab)))
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.fillRect(self.rect(),QColor('#FFFFFF'))
        whole=self.rect().adjusted(42,28,-28,-52); gap=28; lwidth=max(86,int(whole.width()*.13))
        r=QRectF(whole.left(),whole.top(),whole.width()-lwidth-gap,whole.height())
        lr=QRectF(r.right()+gap,whole.top(),lwidth,whole.height())
        p.setPen(QPen(QColor('#D6DCE4'),1)); cx=r.center().x(); cy=r.center().y(); p.drawLine(r.left(),cy,r.right(),cy); p.drawLine(cx,r.top(),cx,r.bottom())
        vals=[max(abs(x[1][1]),abs(x[1][2])) for x in self.data] or [1]; lim=max(20,math.ceil(max(vals)/10)*10); scale=min(r.width(),r.height())/(2*lim); self.points=[]; self.lpoints=[]
        p.setPen(QColor('#657080')); p.drawText(int(r.right()-38),int(cy-8),'+a*'); p.drawText(int(r.left()+4),int(cy-8),'-a*'); p.drawText(int(cx+8),int(r.top()+15),'+b*'); p.drawText(int(cx+8),int(r.bottom()-4),'-b*')
        for sm,lab,C,h,col in self.data:
            q=QPointF(cx+lab[1]*scale,cy-lab[2]*scale); self.points.append((q,sm,lab,C,h)); p.setBrush(col); p.setPen(QPen(QColor('#26313D'),1)); p.drawEllipse(q,7,7)
        # 右侧 L* 竖轴：与 a*b* 平面共享同一批色样，便于同时看色相/彩度和明度关系。
        x=lr.center().x(); p.setPen(QPen(QColor('#D6DCE4'),1)); p.drawLine(QPointF(x,lr.top()),QPointF(x,lr.bottom()))
        p.setPen(QColor('#657080')); p.drawText(int(x+8),int(lr.top()+10),'+L* 100'); p.drawText(int(x+8),int(lr.bottom()),'-L* 0')
        for sm,lab,C,h,col in self.data:
            ly=lr.bottom()-max(0,min(100,lab[0]))/100.0*lr.height(); q=QPointF(x,ly); self.lpoints.append((q,sm,lab,C,h)); p.setBrush(col); p.setPen(QPen(QColor('#26313D'),1)); p.drawEllipse(q,7,7)
        p.setPen(QColor('#657080')); p.drawText(10,self.height()-15,f'a*b* 范围 ±{lim} · 右侧为 L* 0–100 · 悬停查看 LABCH')
    def mouseMoveEvent(self,e):
        best=None; bd=400
        for q,sm,lab,C,h in self.points+self.lpoints:
            d=(q.x()-e.position().x())**2+(q.y()-e.position().y())**2
            if d<bd:best=(sm,lab,C,h);bd=d
        if best:
            sm,lab,C,h=best; self.setToolTip(f'{sm.display_name}\nL* {lab[0]:.2f}   a* {lab[1]:.2f}   b* {lab[2]:.2f}\nC* {C:.2f}   h {h:.2f}°')
        else:self.setToolTip('')


class LegacyLab3DDialog_DISABLED(QDialog):
    """Legacy CIELAB 3D colour solid (disabled in Hotfix37): keyboard + mouse first, with optional library/sRGB reference cloud."""
    def __init__(self, samples, owner, context_label=''):
        super().__init__(owner)
        self.samples=list(samples); self.owner=owner; self.context_label=(context_label or '').strip(); self.yaw=-0.72; self.pitch=0.20; self.last=None
        self.zoom=1.12; self.pan=QPointF(0,12); self.hover_sample=None; self._projected=[]; self.sample_scale=1.0
        self.auto_rotate=False; self.rotation_speed=0.55; self.reference_visible=False; self.reference_mode='library'; self.reference_label='未选择'; self._reference_data=[]
        self._render_data=[]
        self._lab_by_identity={}
        for sm in self.samples:
            lab=owner.sample_lab(sm)
            self._render_data.append((sm,lab,sample_display_qcolor(sm,lab)))
            self._lab_by_identity[id(sm)]=lab
        # Hotfix33: large 3D scenes use an adaptive painter path.  The Lab
        # coordinates and sample count remain exact; only presentation quality
        # changes while the scene is dense/interactive.
        self._large_scene=len(self.samples)>=900
        self._very_large_scene=len(self.samples)>=2200
        self._drag_active=False
        self._hover_grid={}
        self._hover_cell=32.0
        self._projection_cache_key=None
        self._projection_cache=[]
        self._srgb_gamut_data=self._build_srgb_gamut_cloud()
        # 参考点云按用户选择后再加载；打开 3D 时不再预先把整个色库作为参考云。
        self._library_gamut_data=[]
        self.rotate_timer=QTimer(self); self.rotate_timer.setInterval(40 if self._large_scene else 33); self.rotate_timer.timeout.connect(self._auto_rotate_tick)
        title_context=f' · {self.context_label}' if self.context_label else ''
        self.setWindowTitle(f'3D CIELAB 色彩空间{title_context} · {len(self.samples)} 色样'); _fit_dialog_to_screen(self,1180,860); self.setMinimumSize(400,300); self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet('QDialog{background:#25272B;} QMenu{background:#FFFFFF;color:#20242A;}')

    @staticmethod
    def _srgb_to_lab(r,g,b):
        def lin(v): return v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
        r,g,b=lin(r),lin(g),lin(b); X=(0.4124564*r+0.3575761*g+0.1804375*b)/0.95047; Y=(0.2126729*r+0.7151522*g+0.0721750*b); Z=(0.0193339*r+0.1191920*g+0.9503041*b)/1.08883
        d=6/29
        def f(t): return t**(1/3) if t>d**3 else t/(3*d*d)+4/29
        fx,fy,fz=f(X),f(Y),f(Z); return (116*fy-16,500*(fx-fy),200*(fy-fz))

    def _build_srgb_gamut_cloud(self):
        pts=[]; n=14; vals=[i/n for i in range(n+1)]
        for ri,r in enumerate(vals):
            for gi,g in enumerate(vals):
                for bi,b in enumerate(vals):
                    if not (ri in (0,n) or gi in (0,n) or bi in (0,n)): continue
                    pts.append((self._srgb_to_lab(r,g,b),QColor.fromRgbF(r,g,b)))
        return pts

    def _build_library_gamut_cloud(self):
        try: all_samples=list(self.owner._all_library_samples())
        except Exception: all_samples=[]
        return self._cloud_from_samples(all_samples)

    def _cloud_from_samples(self, samples):
        selected={sample_key(x) for x in self.samples}
        items=[x for x in samples if sample_key(x) not in selected]
        if len(items)>900:
            step=max(1,len(items)//900); items=items[::step]
        out=[]
        for sm in items:
            try:
                lab=self.owner.sample_lab(sm); out.append((lab,sample_display_qcolor(sm,lab)))
            except Exception:
                pass
        return out

    def choose_reference_cloud(self):
        """Choose one saved customer as the reference cloud.

        The menu intentionally shows customers only. Individual QTX files and
        an "all library" entry are not listed, which keeps both the UI and large
        libraries manageable.
        """
        try:
            customers=[str(x) for x in self.owner.store.list_customer_groups() if str(x).strip()]
        except Exception:
            customers=[]
        if not customers:
            try:
                customers=[str(x.customer) for x in self.owner.store.list_files() if str(x.customer).strip()]
            except Exception:
                customers=[]
        customers=sorted(dict.fromkeys(customers),key=str.casefold)
        if not customers:
            QMessageBox.information(self,'客户参考点云','正式色库中还没有已保存的客户数据。')
            return False
        options=[f'客户 · {c}' for c in customers]
        current=self.reference_label if self.reference_label in options else options[0]
        choice,ok=QInputDialog.getItem(self,'选择客户参考点云','客户：',options,options.index(current),False)
        if not ok or not choice:return False
        customer=choice.split('客户 · ',1)[-1]
        try:
            contents=list(self.owner.store.library_contents(customer))
            samples=[sm for _row,items in contents for sm in items]
        except Exception:
            samples=[]
        if not samples:
            QMessageBox.information(self,'客户参考点云',f'客户【{customer}】没有可用于 3D 参考的色样。')
            return False
        self.reference_label=choice
        self._reference_data=self._cloud_from_samples(samples)
        self.reference_mode='library'; self.reference_visible=True; self.update(); return True

    def _auto_rotate_tick(self):
        # 多轴轨道旋转：yaw 持续变化，pitch 缓慢往返，不再锁定 L* 轴。
        t=time.monotonic()*max(.15,self.rotation_speed)
        self.yaw += self.rotation_speed*0.014
        self.pitch = 0.18 + 0.42*math.sin(t*0.36)
        self.update()
    def _toggle_rotate(self):
        self.auto_rotate=not self.auto_rotate
        self.rotate_timer.start() if self.auto_rotate else self.rotate_timer.stop(); self.update()
    def _zoom_by(self,factor): self.zoom=max(.35,min(6.0,self.zoom*factor)); self.update()
    def _scale_spheres(self,factor): self.sample_scale=max(.45,min(3.5,self.sample_scale*factor)); self.update()
    def _reset_view(self): self.yaw=-0.72; self.pitch=0.20; self.zoom=1.12; self.pan=QPointF(0,12); self.sample_scale=1.0; self.update()
    def _view_ab(self): self.yaw=0.0; self.pitch=0.0; self.pan=QPointF(0,12); self.update()
    def _view_top(self): self.yaw=-0.72; self.pitch=1.02; self.pan=QPointF(0,12); self.update()

    def _project(self,a,b,L):
        c=QPointF(self.width()/2+self.pan.x(),self.height()/2+self.pan.y()+15); scale=min(self.width(),self.height())/230*self.zoom; x,y,z=a,b,L-50.0
        cy,sy=math.cos(self.yaw),math.sin(self.yaw); xr=x*cy-y*sy; yr=x*sy+y*cy; cp,sp=math.cos(self.pitch),math.sin(self.pitch); sx=xr; sy2=z*cp-yr*sp; depth=yr*cp+z*sp
        return QPointF(c.x()+sx*scale,c.y()-sy2*scale),depth

    def mousePressEvent(self,e):
        self.last=e.position(); self.setFocus(); self._drag_active=True
        if self.auto_rotate and e.button()==Qt.LeftButton:
            self.auto_rotate=False; self.rotate_timer.stop(); self.update()
    def mouseReleaseEvent(self,e):
        self.last=None; self._drag_active=False; self.update()
    def mouseMoveEvent(self,e):
        if e.buttons() & Qt.LeftButton and self.last:
            d=e.position()-self.last
            if e.modifiers() & Qt.ShiftModifier:self.pan+=d
            else:self.yaw+=d.x()*.007; self.pitch=max(-1.05,min(1.05,self.pitch-d.y()*.0055))
            self.last=e.position(); self.update(); return
        if e.buttons() & (Qt.MiddleButton|Qt.RightButton) and self.last:
            d=e.position()-self.last; self.pan+=d; self.last=e.position(); self.update(); return
        pos=e.position(); best=None; dist=1e18
        # Hotfix33: hit-test only nearby screen-space buckets instead of all
        # 3,500+ projected samples for every mouse move.
        cell=max(1.0,float(self._hover_cell)); cx=int(pos.x()//cell); cy=int(pos.y()//cell)
        candidates=[]
        if self._hover_grid:
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    candidates.extend(self._hover_grid.get((cx+dx,cy+dy),()))
        else:
            candidates=self._projected
        for q,sm in candidates:
            d=(q.x()-pos.x())**2+(q.y()-pos.y())**2
            if d<dist: best=sm; dist=d
        hover_limit=max(22.0,20.0*self.sample_scale)
        hover=best if best is not None and dist<hover_limit**2 else None
        if hover is not self.hover_sample:self.hover_sample=hover; self.update()
    def wheelEvent(self,e): self._zoom_by(1.13**(e.angleDelta().y()/120)); e.accept()
    def mouseDoubleClickEvent(self,e): self._reset_view()
    def keyPressEvent(self,e):
        mod=e.modifiers()
        if (mod & Qt.ControlModifier) and e.key()==Qt.Key_Up:self._scale_spheres(1.14); return
        if (mod & Qt.ControlModifier) and e.key()==Qt.Key_Down:self._scale_spheres(1/1.14); return
        if (mod & Qt.ControlModifier) and e.key()==Qt.Key_Right:self.rotation_speed=min(4.0,self.rotation_speed+0.15); return
        if (mod & Qt.ControlModifier) and e.key()==Qt.Key_Left:self.rotation_speed=max(0.05,self.rotation_speed-0.15); return
        if e.key()==Qt.Key_Space:self._toggle_rotate(); return
        if e.key()==Qt.Key_G:self.choose_reference_cloud(); return
        if e.key() in (Qt.Key_Plus,Qt.Key_Equal):self._zoom_by(1.18); return
        if e.key()==Qt.Key_Minus:self._zoom_by(1/1.18); return
        if e.key() in (Qt.Key_0,Qt.Key_R):self._reset_view(); return
        if e.key()==Qt.Key_1:self._reset_view(); return
        if e.key()==Qt.Key_2:self._view_ab(); return
        if e.key()==Qt.Key_3:self._view_top(); return
        super().keyPressEvent(e)

    def contextMenuEvent(self,e):
        m=QMenu(self); rot=m.addAction('暂停自动旋转' if self.auto_rotate else '开始自动旋转    Space'); faster=m.addAction('旋转更快    Ctrl+→'); slower=m.addAction('旋转更慢    Ctrl+←'); m.addSeparator()
        bigger=m.addAction('放大数据球    Ctrl+↑'); smaller=m.addAction('缩小数据球    Ctrl+↓'); m.addSeparator()
        choose_ref=m.addAction('选择客户参考点云…    G'); hide_ref=m.addAction('隐藏参考点云') if self.reference_visible else None; m.addSeparator(); reset=m.addAction('复位视角    R')
        a=m.exec(e.globalPos())
        if a==rot:self._toggle_rotate()
        elif a==faster:self.rotation_speed=min(4.0,self.rotation_speed+0.15)
        elif a==slower:self.rotation_speed=max(.05,self.rotation_speed-.15)
        elif a==bigger:self._scale_spheres(1.14)
        elif a==smaller:self._scale_spheres(1/1.14)
        elif a==choose_ref:self.choose_reference_cloud()
        elif hide_ref is not None and a==hide_ref:self.reference_visible=False; self.update()
        elif a==reset:self._reset_view()

    @staticmethod
    def _sphere_brush(color,q,radius,selected=False):
        hi=QColor(color).lighter(160 if selected else 138); mid=QColor(color); shadow=QColor(color).darker(150); mid.setAlpha(255 if selected else 215); shadow.setAlpha(255 if selected else 205)
        g=QRadialGradient(QPointF(q.x()-radius*.38,q.y()-radius*.42),radius*1.35,QPointF(q.x()-radius*.38,q.y()-radius*.42)); g.setColorAt(0,QColor(255,255,255,235 if selected else 165)); g.setColorAt(.17,hi); g.setColorAt(.66,mid); g.setColorAt(1,shadow); return g

    def _sample_base_radius(self):
        n=len(self._render_data)
        if n>=3000:return 9.5
        if n>=1600:return 11.0
        if n>=900:return 13.0
        return 15.0

    def _projected_scene(self):
        """Return depth-sorted projected samples, cached while the camera is static."""
        key=(self.width(),self.height(),round(self.yaw,7),round(self.pitch,7),round(self.zoom,7),round(self.pan.x(),2),round(self.pan.y(),2),len(self._render_data))
        if key==self._projection_cache_key:
            return self._projection_cache
        pts=[]
        for sm,lab,color in self._render_data:
            L,a,b=lab; q,z=self._project(a,b,L); pts.append((z,q,sm,lab,color))
        pts.sort(key=lambda x:x[0])
        self._projection_cache_key=key
        self._projection_cache=pts
        return pts

    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing,True); bg=QLinearGradient(0,0,0,self.height()); bg.setColorAt(0,QColor('#31343A')); bg.setColorAt(1,QColor('#202226')); p.fillRect(self.rect(),bg)
        center=QPointF(self.width()*.5,self.height()*.50); rg=QRadialGradient(center,min(self.width(),self.height())*.52); rg.setColorAt(0,QColor(255,255,255,24)); rg.setColorAt(1,QColor(0,0,0,0)); p.fillRect(self.rect(),rg)
        if self.reference_visible:
            source=self._reference_data or (self._srgb_gamut_data if self.reference_mode=='srgb' else self._library_gamut_data)
            cloud=[]
            for lab,color in source:
                L,a,b=lab; q,z=self._project(a,b,L); cloud.append((z,q,color))
            ref_radius=6.4*min(1.15,max(.82,self.zoom))
            for z,q,color in sorted(cloud,key=lambda x:x[0]):
                p.setPen(Qt.NoPen); p.setBrush(self._sphere_brush(color,q,ref_radius,False)); p.drawEllipse(q,ref_radius,ref_radius)
        o,_=self._project(0,0,50); p.setPen(QPen(QColor(18,18,20,170),1.0)); axes=[((0,0,105),'+L'),((0,0,-5),'-L'),((-88,0,50),'-a'),((88,0,50),'+a'),((0,-88,50),'-b'),((0,88,50),'+b')]
        f=p.font(); f.setBold(True); f.setPointSize(15); p.setFont(f)
        for (a,b,L),label in axes:
            q,_=self._project(a,b,L); p.drawLine(o,q); off=QPointF(8,-7) if label.startswith('+') else QPointF(-31,6); off=QPointF(8,-10) if label=='+L' else QPointF(8,22) if label=='-L' else off; p.drawText(q+off,label)

        # Adaptive 3D rendering.  All real samples are still drawn at their exact
        # Lab coordinates.  Dense scenes avoid thousands of radial-gradient
        # allocations per frame; small scenes retain the original glossy spheres.
        interactive=self.auto_rotate or self._drag_active
        fast_mode=self._large_scene or interactive
        base_radius=self._sample_base_radius()*self.sample_scale
        if fast_mode and (interactive or self._very_large_scene):
            p.setRenderHint(QPainter.Antialiasing,False)
        else:
            p.setRenderHint(QPainter.Antialiasing,True)
        self._projected=[]; grid={}; cell=max(24.0,base_radius*2.25); self._hover_cell=cell
        pts=self._projected_scene()
        for z,q,sm,lab,color in pts:
            hover=sm is self.hover_sample
            radius=(base_radius*1.30 if hover else base_radius)
            if hover:
                p.setPen(Qt.NoPen); p.setBrush(QColor(255,255,255,62)); p.drawEllipse(q,radius+7,radius+7)
            if fast_mode and not hover:
                fill=QColor(color); fill.setAlpha(248)
                if interactive or self._very_large_scene:p.setPen(Qt.NoPen)
                else:p.setPen(QPen(QColor(20,20,22,105),0.8))
                p.setBrush(fill); p.drawEllipse(q,radius,radius)
            else:
                p.setPen(QPen(QColor(20,20,22,130),1.0)); p.setBrush(self._sphere_brush(color,q,radius,True)); p.drawEllipse(q,radius,radius)
            self._projected.append((q,sm))
            k=(int(q.x()//cell),int(q.y()//cell)); grid.setdefault(k,[]).append((q,sm))
        self._hover_grid=grid

        p.setRenderHint(QPainter.Antialiasing,True)
        p.setPen(QColor(255,255,255,220)); f=p.font(); f.setPointSize(9); f.setBold(False); p.setFont(f)
        refname=self.reference_label if self.reference_visible else '关闭'
        state='旋转中' if self.auto_rotate else '静止'; render_state='流畅模式' if fast_mode else '高质量球体'
        p.drawText(20,self.height()-31,'拖动旋转 · Shift/中键平移 · 滚轮缩放 · Space 自动旋转 · G 客户参考云 · R 复位')
        source_text=f' · 来源：{self.context_label}' if self.context_label else ''
        p.drawText(20,self.height()-12,f'{len(self.samples)} 个真实色样{source_text} · 参考：{refname} · 球体 {self.sample_scale:.2f}× · {state} {self.rotation_speed:.2f} · 渲染：{render_state}')
        if self.hover_sample is not None:
            sm=self.hover_sample; lab=self._lab_by_identity.get(id(sm)) or self.owner.sample_lab(sm); L,a,b=lab; C=math.hypot(a,b); hh=math.degrees(math.atan2(b,a))%360; box=QRectF(self.width()-405,28,375,104)
            p.setPen(QPen(QColor(255,255,255,70),1)); p.setBrush(QColor(28,28,30,220)); p.drawRoundedRect(box,14,14); p.setPen(QColor('white')); f=p.font(); f.setPointSize(10); f.setBold(True); p.setFont(f); p.drawText(QRectF(box.left()+18,box.top()+16,box.width()-36,24),Qt.AlignLeft,sm.display_name); f.setBold(False); f.setPointSize(9); p.setFont(f); p.drawText(QRectF(box.left()+18,box.top()+48,box.width()-36,20),Qt.AlignLeft,f'L* {L:.2f}    a* {a:.2f}    b* {b:.2f}'); p.drawText(QRectF(box.left()+18,box.top()+72,box.width()-36,20),Qt.AlignLeft,f'C* {C:.2f}    h° {hh:.1f}')


class SwatchDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        color = index.data(Qt.DecorationRole)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(1, 1, -1, -1)
        if isinstance(color, QColor):
            painter.fillRect(rect, color)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.setPen(QPen(QColor("#3B82F6"), 2))
        else:
            painter.setPen(QPen(QColor("#DDE3EA"), 1))
        painter.drawRect(rect)
        painter.restore()


class FindStandardPickerDialog(QDialog):
    """Fast formal-library picker used by Find Color.

    HF136 keeps the persisted library on the frozen lightweight-index path:
    opening the dialog creates only customer/QTX nodes.  Sample leaves and
    swatches are created only when a QTX is expanded or when a search actually
    matches samples in that file.  Full spectral payloads are hydrated only for
    the currently previewed sample and for the final confirmed selection.
    """
    SAMPLE_KIND='sample'; FILE_KIND='file'; GROUP_KIND='group'; PLACEHOLDER_KIND='placeholder'

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner=owner
        self.setWindowTitle('从色库选择查色标准')
        self.setMinimumSize(700,520)
        try:
            screen=(parent.screen() if parent is not None and hasattr(parent,'screen') else self.screen())
            avail=screen.availableGeometry()
            self.resize(min(960,max(720,int(avail.width()*0.70))),min(760,max(560,int(avail.height()*0.78))))
        except Exception:
            self.resize(860,650)

        self._rows_by_file: dict[str,list[dict]]={}
        self._row_by_key: dict[str,dict]={}
        self._file_items: dict[str,QTreeWidgetItem]={}
        self._group_items: list[QTreeWidgetItem]=[]
        try:index_rows=list(owner.store.sample_index())
        except Exception:index_rows=[]
        for raw in index_rows:
            row=dict(raw or {})
            key=str(row.get('sample_key') or '')
            if not key:continue
            path=str(row.get('qtx_path') or '')
            customer=str(row.get('customer') or '未分类')
            row['sample_key']=key; row['qtx_path']=path; row['customer']=customer
            self._rows_by_file.setdefault(path,[]).append(row); self._row_by_key[key]=row
        for rows in self._rows_by_file.values():
            rows.sort(key=lambda r:str(r.get('display_name') or r.get('sample_id') or '').casefold())

        lay=QVBoxLayout(self); lay.setContentsMargins(14,12,14,12); lay.setSpacing(8)
        note=QLabel('默认只显示客户与 QTX；展开后查看色样。色样前显示颜色预览，搜索不会自动确认选择。')
        note.setWordWrap(True); note.setObjectName('muted'); lay.addWidget(note)
        self.search=QLineEdit(); self.search.setPlaceholderText('搜索客户 / QTX 文件 / 色样名称…'); lay.addWidget(self.search)

        self.tree=QTreeWidget(); self.tree.setHeaderLabels(['色样 / 客户 / QTX','类型'])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True); self.tree.setIconSize(QSize(26,16))
        header=self.tree.header(); header.setSectionResizeMode(0,QHeaderView.Stretch); header.setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.tree.setMinimumHeight(300); lay.addWidget(self.tree,1)
        self._build_tree(); self.tree.collapseAll()

        preview=QFrame(); preview.setObjectName('pickerPreview')
        pv=QHBoxLayout(preview); pv.setContentsMargins(10,8,10,8); pv.setSpacing(10)
        self.preview_swatch=QFrame(); self.preview_swatch.setFixedSize(58,40); self.preview_swatch.setStyleSheet('background:#EEF2F7;border:1px solid #CBD5E1;border-radius:7px;'); pv.addWidget(self.preview_swatch)
        info=QVBoxLayout(); info.setSpacing(2)
        self.preview_name=QLabel('选择一个色样可查看预览'); self.preview_name.setObjectName('pickerPreviewName')
        self.preview_lab=QLabel(''); self.preview_lab.setObjectName('muted'); info.addWidget(self.preview_name); info.addWidget(self.preview_lab); pv.addLayout(info,1)
        self.selected_label=QLabel('已选 0 个色样'); self.selected_label.setObjectName('muted'); pv.addWidget(self.selected_label)
        lay.addWidget(preview)

        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        self.ok_button=buttons.button(QDialogButtonBox.Ok); self.cancel_button=buttons.button(QDialogButtonBox.Cancel)
        self.ok_button.setText('确定'); self.cancel_button.setText('取消')
        self.ok_button.setEnabled(False); self.ok_button.setAutoDefault(False); self.cancel_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); lay.addWidget(buttons)

        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(lambda:self._filter(self.search.text()))
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.currentItemChanged.connect(lambda *_:self._update_preview())
        self.tree.itemDoubleClicked.connect(self._double_clicked)

    @staticmethod
    def _make_placeholder():
        item=QTreeWidgetItem(['','']); item.setData(0,Qt.UserRole+1,FindStandardPickerDialog.PLACEHOLDER_KIND)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
        return item

    def _build(self,index_rows=None):
        """Backward-compatible private entry retained for older call sites/tests."""
        if self.tree.topLevelItemCount()==0:
            self._build_tree()

    def _build_tree(self):
        self.tree.setUpdatesEnabled(False)
        try:
            grouped: dict[str,list[str]]={}
            for path,rows in self._rows_by_file.items():
                customer=str((rows[0].get('customer') if rows else None) or '未分类')
                grouped.setdefault(customer,[]).append(path)
            nodes={}
            folder_icon=self.style().standardIcon(QStyle.SP_DirIcon)
            file_icon=self.style().standardIcon(QStyle.SP_FileIcon)
            for customer in sorted(grouped,key=str.casefold):
                parts=[x.strip() for x in customer.replace('\\','/').split('/') if x.strip()] or ['未分类']
                parent=None; acc=[]
                for depth,part in enumerate(parts):
                    acc.append(part); full='/'.join(acc); key=('g',full)
                    node=nodes.get(key)
                    if node is None:
                        node=QTreeWidgetItem([part,'客户' if depth==0 else '子客户'])
                        node.setIcon(0,folder_icon); node.setData(0,Qt.UserRole+1,self.GROUP_KIND); node.setData(0,Qt.UserRole+2,full)
                        if parent is None:self.tree.addTopLevelItem(node)
                        else:parent.addChild(node)
                        nodes[key]=node; self._group_items.append(node)
                    parent=node
                for path in sorted(grouped[customer],key=lambda p:_friendly_qtx_picker_name(p,customer).casefold()):
                    rows=self._rows_by_file.get(path,[])
                    display=_friendly_qtx_picker_name(path,customer)
                    file_item=QTreeWidgetItem([display,'QTX'])
                    file_item.setIcon(0,file_icon); file_item.setToolTip(0,Path(path).name or str(path))
                    file_item.setData(0,Qt.UserRole+1,self.FILE_KIND); file_item.setData(0,Qt.UserRole+2,path)
                    file_item.setData(0,Qt.UserRole+3,'')  # population marker: '', '__all__', or 'q:...'
                    file_item.setData(0,Qt.UserRole+4,'\n'.join((str(r.get('display_name') or '')+' '+str(r.get('sample_id') or '')).casefold() for r in rows))
                    file_item.setData(0,Qt.UserRole+5,customer)
                    file_item.addChild(self._make_placeholder())
                    parent.addChild(file_item); self._file_items[path]=file_item
        finally:
            self.tree.setUpdatesEnabled(True)

    def _populate_file(self,item:QTreeWidgetItem,query:str=''):
        if item is None or item.data(0,Qt.UserRole+1)!=self.FILE_KIND:return
        q=(query or '').strip().casefold(); marker=('q:'+q) if q else '__all__'
        if str(item.data(0,Qt.UserRole+3) or '')==marker:return
        path=str(item.data(0,Qt.UserRole+2) or ''); rows=self._rows_by_file.get(path,[])
        if q:
            rows=[r for r in rows if q in (str(r.get('display_name') or '')+' '+str(r.get('sample_id') or '')).casefold()]
        self.tree.setUpdatesEnabled(False)
        try:
            item.takeChildren()
            for row in rows:
                key=str(row.get('sample_key') or '')
                if not key:continue
                name=str(row.get('display_name') or row.get('sample_id') or key)
                leaf=QTreeWidgetItem([name,'色样']); leaf.setData(0,Qt.UserRole+1,self.SAMPLE_KIND); leaf.setData(0,Qt.UserRole,key)
                leaf.setData(0,Qt.UserRole+2,path); leaf.setIcon(0,_index_row_swatch_icon(row))
                lab=row.get('lab_d65_10')
                if lab is not None:
                    try:leaf.setToolTip(0,f"{name}\nL* {float(lab[0]):.2f}   a* {float(lab[1]):+.2f}   b* {float(lab[2]):+.2f}")
                    except Exception:pass
                item.addChild(leaf)
            item.setData(0,Qt.UserRole+3,marker)
        finally:
            self.tree.setUpdatesEnabled(True)

    def _reset_query_children(self):
        for item in self._file_items.values():
            marker=str(item.data(0,Qt.UserRole+3) or '')
            if marker.startswith('q:'):
                item.takeChildren(); item.addChild(self._make_placeholder()); item.setData(0,Qt.UserRole+3,'')

    def _on_item_expanded(self,item):
        if item is not None and item.data(0,Qt.UserRole+1)==self.FILE_KIND:
            q=self.search.text().strip().casefold()
            self._populate_file(item,q if q else '')

    def _double_clicked(self,item,col):
        if item is None:return
        kind=item.data(0,Qt.UserRole+1)
        if kind==self.SAMPLE_KIND:
            if len(self._selected_keys())==1:self.accept()
        elif kind in {self.GROUP_KIND,self.FILE_KIND}:
            item.setExpanded(not item.isExpanded())

    def _filter(self,text):
        q=(text or '').strip().casefold()
        self.tree.setUpdatesEnabled(False)
        try:
            if not q:
                self._reset_query_children()
                for item in self._file_items.values():item.setHidden(False); item.setExpanded(False)
                for item in self._group_items:item.setHidden(False); item.setExpanded(False)
                return
            # First decide file visibility from scalar metadata; only matching sample
            # leaves are materialised, never the whole 3500-row file for a search.
            for path,item in self._file_items.items():
                customer=str(item.data(0,Qt.UserRole+5) or '').casefold()
                file_text=(item.text(0)+' '+str(Path(path).name)+' '+customer).casefold()
                blob=str(item.data(0,Qt.UserRole+4) or '')
                file_match=q in file_text; sample_match=q in blob
                visible=file_match or sample_match
                item.setHidden(not visible)
                if sample_match:
                    self._populate_file(item,q); item.setExpanded(True)
                elif visible:
                    if str(item.data(0,Qt.UserRole+3) or '').startswith('q:'):
                        item.takeChildren(); item.addChild(self._make_placeholder()); item.setData(0,Qt.UserRole+3,'')
                    item.setExpanded(False)
            def update_group(item):
                own=q in item.text(0).casefold() or q in str(item.data(0,Qt.UserRole+2) or '').casefold()
                child_visible=False
                for i in range(item.childCount()):
                    child=item.child(i)
                    if child.data(0,Qt.UserRole+1)==self.GROUP_KIND:
                        child_visible=update_group(child) or child_visible
                    elif not child.isHidden():
                        child_visible=True
                visible=own or child_visible; item.setHidden(not visible)
                if visible:item.setExpanded(True)
                return visible
            for i in range(self.tree.topLevelItemCount()):update_group(self.tree.topLevelItem(i))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _selected_keys(self):
        out=[]
        for item in self.tree.selectedItems():
            if item.data(0,Qt.UserRole+1)==self.SAMPLE_KIND:
                key=str(item.data(0,Qt.UserRole) or '')
                if key and key not in out:out.append(key)
        return out

    def _selection_changed(self):
        keys=self._selected_keys(); self.selected_label.setText(f'已选 {len(keys)} 个色样')
        self.ok_button.setEnabled(bool(keys)); self._update_preview()

    def _update_preview(self):
        item=self.tree.currentItem(); key=''
        if item is not None and item.data(0,Qt.UserRole+1)==self.SAMPLE_KIND:key=str(item.data(0,Qt.UserRole) or '')
        if not key:
            keys=self._selected_keys(); key=keys[-1] if keys else ''
        row=self._row_by_key.get(key)
        if not row:
            self.preview_name.setText('选择一个色样可查看预览'); self.preview_lab.setText('')
            self.preview_swatch.setStyleSheet('background:#EEF2F7;border:1px solid #CBD5E1;border-radius:7px;'); return
        color=_index_preview_qcolor(row); exact=None
        try:exact=self.owner.store.load_sample_by_key(key)
        except Exception:exact=None
        if exact is not None:
            try:color=sample_display_qcolor(exact,exact.lab_d65_10)
            except Exception:pass
        self.preview_swatch.setStyleSheet(f'background:{color.name()};border:1px solid #CBD5E1;border-radius:7px;')
        name=str(row.get('display_name') or row.get('sample_id') or key); self.preview_name.setText(name)
        lab=row.get('lab_d65_10'); bits=[]
        if lab is not None:
            try:bits.append(f'L* {float(lab[0]):.2f}   a* {float(lab[1]):+.2f}   b* {float(lab[2]):+.2f}')
            except Exception:pass
        bits.append('有光谱' if bool(row.get('has_spectrum')) else '无光谱')
        if bool(row.get('fluorescent')):bits.append('荧光预览')
        self.preview_lab.setText(' · '.join(bits))

    def chosen(self):
        keys=self._selected_keys()
        if not keys:return []
        try:
            # Batch hydration preserves the fast path and avoids N independent DB opens.
            return list(self.owner.store.load_samples_by_keys(keys))
        except Exception:
            return [s for s in (self.owner.store.load_sample_by_key(k) for k in keys) if s is not None]


class AddSamplesDialog(QDialog):
    """从色库按「客户 → QTX → 色样」选择加入当前目标。

    HF125 restores the frozen P3 rule: opening a picker must use only the
    lightweight SQLite index.  Full Sample/spectrum payloads are hydrated only
    for the keys the user actually confirms.  File/sample tree leaves are also
    created lazily, so clicking【添加色样】does not construct thousands of Qt
    items or resolve thousands of source paths before the dialog can appear.
    """

    def __init__(self, owner: "MainWindow", already: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从色库加入色样")
        self.resize(760, 620)
        self.setMinimumSize(660,520)
        try:
            screen=(parent.screen() if parent is not None and hasattr(parent,'screen') else self.screen())
            avail=screen.availableGeometry()
            self.resize(min(920,max(720,int(avail.width()*0.68))),min(740,max(560,int(avail.height()*0.76))))
        except Exception:
            pass
        self.owner = owner
        self.already = {str(x) for x in (already or set()) if x}
        self._changing = False
        self._rows_by_file: dict[str, list[dict]] = {}
        self._file_items: dict[str, QTreeWidgetItem] = {}
        self._workspace_by_key: dict[str, Sample] = {}
        self._saved_keys: set[str] = set()

        # Lightweight persisted-library metadata only: no reflectance arrays,
        # no Sample construction and no Path.resolve()/network source probing.
        try:
            index_rows = list(owner.store.sample_index())
        except Exception:
            index_rows = []
        for row in index_rows:
            key = str(row.get('sample_key') or '')
            if not key:
                continue
            path = str(row.get('qtx_path') or '')
            customer = str(row.get('customer') or '未分类')
            clean = dict(row)
            clean['sample_key'] = key
            clean['qtx_path'] = path
            clean['customer'] = customer
            self._rows_by_file.setdefault(path, []).append(clean)
            self._saved_keys.add(key)

        # Current unsaved workspace samples already exist in memory.  Add only
        # entries that are not represented by the persisted index.
        for sm in owner.samples:
            try:
                key = sample_key(sm)
            except Exception:
                continue
            self._workspace_by_key[key] = sm
            if key in self._saved_keys:
                continue
            path = str(sm.source_file or '临时工作区')
            self._rows_by_file.setdefault(path, []).append({
                'sample_key': key,
                'qtx_path': path,
                'customer': '临时导入',
                'display_name': str(sm.display_name or sm.sample_id or key),
                'lab_d65_10': tuple(sm.lab_d65_10),
                'has_spectrum': bool(sm.has_spectrum()),
                'fluorescent': bool(sample_is_fluorescent(sm)),
                '_workspace': True,
            })

        # Stable name order without touching the source file system.
        for path, rows in self._rows_by_file.items():
            rows.sort(key=lambda r: str(r.get('display_name') or '').casefold())

        lay = QVBoxLayout(self)
        note = QLabel(
            "按层级选择：勾选一个 QTX 文件会加入其中全部色样；也可以展开后只勾选某几个色样。\n"
            "列表使用轻量索引，只有点击【加入】后才读取所选色样的完整光谱。"
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("搜索客户 / QTX 文件名 / 色样名称…")
        self.filter_edit.textChanged.connect(self._filter_tree)
        self.filter_edit.returnPressed.connect(lambda: self._filter_tree(self.filter_edit.text()))
        lay.addWidget(self.filter_edit)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["色样 / 客户 / QTX", "状态"])
        self.tree.setIconSize(QSize(26,16))
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0,QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        lay.addWidget(self.tree, 1)
        self.tree.setUpdatesEnabled(False)
        self._build_tree()
        self.tree.collapseAll()
        self.tree.setUpdatesEnabled(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("加入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.Ok).setAutoDefault(False)
        buttons.button(QDialogButtonBox.Cancel).setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _checkable(item: QTreeWidgetItem, state=Qt.Unchecked):
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, state)

    def _build_tree(self):
        """Build only customer/file nodes. Sample leaves are lazy."""
        grouped: dict[str, list[tuple[str, list[dict]]]] = {}
        for file_path, rows in self._rows_by_file.items():
            customer = str((rows[0].get('customer') if rows else None) or '未分类')
            grouped.setdefault(customer, []).append((file_path, rows))

        self.tree.blockSignals(True)
        try:
            nodes = {}
            for customer in sorted(grouped, key=lambda x: (x == '临时导入', x.casefold())):
                parts=[p.strip() for p in str(customer).replace('\\','/').split('/') if p.strip()] or ['未分类']
                parent=None; acc=[]
                for depth, part in enumerate(parts):
                    acc.append(part); full='/'.join(acc); nk=('group',full)
                    node=nodes.get(nk)
                    if node is None:
                        node=QTreeWidgetItem([part, '客户' if depth==0 else '子客户'])
                        node.setData(0,Qt.UserRole+1,'group'); node.setData(0,Qt.UserRole+2,full)
                        self._checkable(node)
                        if parent is None:self.tree.addTopLevelItem(node)
                        else:parent.addChild(node)
                        nodes[nk]=node
                    parent=node
                for file_path, rows in sorted(grouped[customer], key=lambda x: Path(x[0]).name.casefold()):
                    keys=[str(r.get('sample_key') or '') for r in rows]
                    eligible=sum(1 for k in keys if k and k not in self.already)
                    already_count=len(keys)-eligible
                    status=(f'{len(rows)} 个色样' if not already_count else
                            ('已全部加入' if eligible==0 else f'{len(rows)} 个色样 · 已有 {already_count}'))
                    file_item=QTreeWidgetItem([_friendly_qtx_picker_name(file_path,customer),status])
                    file_item.setToolTip(0,Path(file_path).name or str(file_path))
                    file_item.setData(0,Qt.UserRole+1,'file')
                    file_item.setData(0,Qt.UserRole+2,file_path)
                    file_item.setData(0,Qt.UserRole+3,False)  # sample leaves populated?
                    # Store a lowercase search blob only; this remains scalar metadata.
                    file_item.setData(0,Qt.UserRole+4,'\n'.join(str(r.get('display_name') or '').casefold() for r in rows))
                    self._checkable(file_item)
                    if eligible<=0:
                        file_item.setFlags(file_item.flags() & ~Qt.ItemIsEnabled)
                    parent.addChild(file_item)
                    self._file_items[file_path]=file_item
        finally:
            self.tree.blockSignals(False)

    def _ensure_file_children(self, item: QTreeWidgetItem):
        if item is None or item.data(0,Qt.UserRole+1)!='file' or bool(item.data(0,Qt.UserRole+3)):
            return
        path=str(item.data(0,Qt.UserRole+2) or '')
        rows=self._rows_by_file.get(path,[])
        inherited=item.checkState(0)
        _was_blocked=self.tree.blockSignals(True)
        try:
            for row in rows:
                key=str(row.get('sample_key') or '')
                if not key:continue
                status='已在工作台' if key in self.already else ''
                leaf=QTreeWidgetItem([str(row.get('display_name') or key),status])
                leaf.setData(0,Qt.UserRole+1,'sample'); leaf.setData(0,Qt.UserRole,key)
                leaf.setIcon(0,_index_row_swatch_icon(row))
                lab=row.get('lab_d65_10')
                if lab is not None:
                    try:leaf.setToolTip(0,f"{str(row.get('display_name') or key)}\nL* {float(lab[0]):.2f}   a* {float(lab[1]):+.2f}   b* {float(lab[2]):+.2f}")
                    except Exception:pass
                state=Qt.Checked if key in self.already or inherited==Qt.Checked else Qt.Unchecked
                self._checkable(leaf,state)
                if key in self.already:
                    leaf.setFlags(leaf.flags() & ~Qt.ItemIsEnabled)
                item.addChild(leaf)
            item.setData(0,Qt.UserRole+3,True)
        finally:
            self.tree.blockSignals(_was_blocked)

    def _on_item_expanded(self,item):
        self._ensure_file_children(item)

    def _set_descendants(self, item: QTreeWidgetItem, state):
        for i in range(item.childCount()):
            child=item.child(i)
            if child.data(0,Qt.UserRole+1)=='file' and not bool(child.data(0,Qt.UserRole+3)):
                if child.flags() & Qt.ItemIsEnabled:child.setCheckState(0,state)
                continue
            if child.flags() & Qt.ItemIsEnabled:child.setCheckState(0,state)
            self._set_descendants(child,state)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._changing or column!=0:return
        self._changing=True
        try:
            kind=item.data(0,Qt.UserRole+1)
            if kind in {'group','file'}:
                self._set_descendants(item,item.checkState(0))
            parent=item.parent()
            while parent is not None:
                states=[parent.child(i).checkState(0) for i in range(parent.childCount())
                        if parent.child(i).flags() & Qt.ItemIsEnabled]
                if states:
                    parent.setCheckState(0,Qt.Checked if all(x==Qt.Checked for x in states)
                                         else Qt.Unchecked if all(x==Qt.Unchecked for x in states)
                                         else Qt.PartiallyChecked)
                parent=parent.parent()
        finally:
            self._changing=False

    def _filter_tree(self, text):
        q=(text or '').strip().casefold()
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            def visit(item, ancestor_match=False):
                kind=item.data(0,Qt.UserRole+1)
                own=(q in item.text(0).casefold() or q in item.text(1).casefold()) if q else True
                if kind=='file' and q:
                    blob=str(item.data(0,Qt.UserRole+4) or '')
                    sample_match=q in blob
                    if sample_match and not bool(item.data(0,Qt.UserRole+3)):
                        self._ensure_file_children(item)
                    own=own or sample_match
                child_visible=False
                for i in range(item.childCount()):
                    ch=item.child(i)
                    if ch.data(0,Qt.UserRole+1)=='sample':
                        vis=(not q) or ancestor_match or own or q in ch.text(0).casefold()
                        ch.setHidden(not vis); child_visible=child_visible or vis
                    else:
                        child_visible=visit(ch,ancestor_match or own) or child_visible
                visible=(not q) or ancestor_match or own or child_visible
                item.setHidden(not visible)
                if q and visible and item.childCount():item.setExpanded(True)
                elif not q:item.setExpanded(False)
                return visible
            for i in range(self.tree.topLevelItemCount()):visit(self.tree.topLevelItem(i))
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)

    def chosen(self):
        selected_keys=[]; seen=set()
        def add_key(key):
            key=str(key or '')
            if key and key not in self.already and key not in seen:
                seen.add(key); selected_keys.append(key)
        def walk(item):
            kind=item.data(0,Qt.UserRole+1)
            if kind=='file':
                path=str(item.data(0,Qt.UserRole+2) or '')
                if bool(item.data(0,Qt.UserRole+3)):
                    for i in range(item.childCount()):
                        leaf=item.child(i)
                        if leaf.checkState(0)==Qt.Checked:add_key(leaf.data(0,Qt.UserRole))
                elif item.checkState(0)==Qt.Checked:
                    for row in self._rows_by_file.get(path,[]):add_key(row.get('sample_key'))
                return
            if kind=='sample':
                if item.checkState(0)==Qt.Checked:add_key(item.data(0,Qt.UserRole))
                return
            for i in range(item.childCount()):walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):walk(self.tree.topLevelItem(i))
        if not selected_keys:return []

        persisted=[k for k in selected_keys if k in self._saved_keys]
        try:
            loaded={sample_key(x):x for x in self.owner.store.load_samples_by_keys(persisted)}
        except Exception:
            loaded={}
        out=[]
        for key in selected_keys:
            sm=loaded.get(key) or self._workspace_by_key.get(key)
            if sm is not None:out.append(sm)
        return out


class WorkbenchTargetDialog(QDialog):
    """Choose an existing comparison workbench or create a new one.

    Hotfix49: adding colours should be one explicit decision instead of a
    context-menu submenu that hides the create-new path once workbenches exist.
    """
    def __init__(self, workbenches, default_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("加入比色工作台")
        self.setModal(True)
        self.setMinimumWidth(430)
        root=QVBoxLayout(self); root.setContentsMargins(18,16,18,16); root.setSpacing(12)
        title=QLabel("选择目标工作台")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        hint=QLabel("可加入已经建立好的工作台，也可以创建一个新的工作台。加入完成后会自动打开目标工作台。")
        hint.setWordWrap(True); hint.setObjectName("muted"); root.addWidget(hint)

        self.existing_radio=QRadioButton("加入已有工作台")
        self.existing_combo=QComboBox()
        visible=[]
        for wb in workbenches or []:
            name=str(wb.get('name') or '工作台')
            if wb.get('is_collapsed'):
                name += "  · 已收起"
            self.existing_combo.addItem(name, wb.get('workbench_id'))
            visible.append(wb)
        self.existing_combo.setEnabled(bool(visible))
        root.addWidget(self.existing_radio); root.addWidget(self.existing_combo)

        self.new_radio=QRadioButton("创建新工作台")
        self.new_name=QLineEdit(str(default_name or "新工作台"))
        self.new_name.setPlaceholderText("输入新工作台名称")
        root.addWidget(self.new_radio); root.addWidget(self.new_name)

        group=QButtonGroup(self); group.addButton(self.existing_radio); group.addButton(self.new_radio)
        if visible:self.existing_radio.setChecked(True)
        else:self.new_radio.setChecked(True)
        self.existing_radio.toggled.connect(self._sync_state)
        self.new_radio.toggled.connect(self._sync_state)
        self._sync_state()

        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("加入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_state(self, *_):
        self.existing_combo.setEnabled(self.existing_radio.isChecked() and self.existing_combo.count()>0)
        self.new_name.setEnabled(self.new_radio.isChecked())

    def _accept_checked(self):
        if self.new_radio.isChecked() and not self.new_name.text().strip():
            QMessageBox.information(self,"加入比色工作台","请输入新工作台名称。")
            self.new_name.setFocus(); return
        if self.existing_radio.isChecked() and self.existing_combo.count()<=0:
            QMessageBox.information(self,"加入比色工作台","当前没有可用工作台，请创建一个新的工作台。")
            self.new_radio.setChecked(True); return
        self.accept()

    def target(self):
        if self.new_radio.isChecked():
            return ("new", self.new_name.text().strip())
        return ("existing", self.existing_combo.currentData())


class CollapsedWorkbenchesDialog(QDialog):
    def __init__(self, collapsed: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("已收起的工作台")
        self.resize(480, 420)
        self.collapsed = collapsed
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选要恢复显示的工作台："))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        for wb in collapsed:
            item = QListWidgetItem(f"{wb['name']}  （{len(wb.get('samples_data', []))} 个色样）")
            item.setData(Qt.UserRole, wb["workbench_id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("恢复")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def restore_ids(self):
        return {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}


class WorkbenchAverageDialog(QDialog):
    def __init__(self, samples: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择参与平均的色样")
        self.resize(480, 460)
        self.samples = samples
        lay = QVBoxLayout(self)
        note = QLabel("勾选要参与平均的色样。\n计算方式：逐波长反射率平均，再重新计算 XYZ/Lab。\n不会修改原 QTX。")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.list.setIconSize(QSize(30,18))
        for s in samples:
            try:colour=sample_display_qcolor(s,s.lab_d65_10)
            except Exception:colour=lab_to_qcolor(s.lab_d65_10)
            item = QListWidgetItem(_picker_swatch_icon(colour.name(),30,18),s.display_name)
            item.setData(Qt.UserRole, sample_key(s))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            try:
                L,a,b=s.lab_d65_10; item.setToolTip(f'{s.display_name}\nL* {L:.2f}   a* {a:+.2f}   b* {b:+.2f}')
            except Exception:pass
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("建立平均标准")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def chosen(self):
        keys = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}
        return [s for s in self.samples if sample_key(s) in keys]



class _LargeQtxSelectionModel(QAbstractTableModel):
    """Virtualised selection model for large QTX imports.

    QListWidget creates one Qt item for every colour and becomes expensive for
    3k+ sample libraries.  This model keeps only Python lists/bytearrays and lets
    QTableView request data for visible rows on demand.

    HF126 keeps the large-file presentation consistent with the small-file picker:
    every visible row has an actual colour swatch.  The swatch is painted lazily
    through ``BackgroundRole`` so a 3,500-colour QTX does not allocate 3,500
    QPixmaps/icons up front.
    """
    HEADERS = ['色块', '色样名称', 'L*', 'a*', 'b*', 'C*', 'h°']

    def __init__(self, samples, already=None, parent=None):
        super().__init__(parent)
        self.samples=list(samples or [])
        self.already=set(already or set())
        self._all_rows=list(range(len(self.samples)))
        self._visible=list(self._all_rows)
        self._names=[str(sm.display_name) for sm in self.samples]
        self._names_cf=[x.casefold() for x in self._names]
        self._keys=[sample_key(sm) for sm in self.samples]
        self._enabled=bytearray(0 if k in self.already else 1 for k in self._keys)
        self._checked=bytearray(self._enabled)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role==Qt.DisplayRole and orientation==Qt.Horizontal and 0<=section<len(self.HEADERS):
            return self.HEADERS[section]
        return super().headerData(section,orientation,role)

    def _source_row(self, model_row):
        return self._visible[model_row] if 0<=model_row<len(self._visible) else -1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():return None
        src=self._source_row(index.row())
        if src<0:return None
        sm=self.samples[src]; col=index.column()
        # Checkbox lives with the sample name, while column 0 is reserved for a
        # consistent colour swatch just like the small-QTX list.
        if role==Qt.CheckStateRole and col==1:
            return Qt.Checked if self._checked[src] else Qt.Unchecked
        if role==Qt.BackgroundRole and col==0:
            try:return sample_display_qcolor(sm, sm.lab_d65_10)
            except Exception:return lab_to_qcolor(sm.lab_d65_10)
        if role==Qt.DisplayRole:
            if col==0:return ''
            if col==1:return self._names[src]
            L,a,b=sm.lab_d65_10
            if col==2:return f'{L:.2f}'
            if col==3:return f'{a:+.2f}'
            if col==4:return f'{b:+.2f}'
            if col==5:return f'{math.hypot(a,b):.2f}'
            if col==6:return f'{math.degrees(math.atan2(b,a))%360:.1f}'
        if role==Qt.UserRole:
            return sm
        if role==Qt.ToolTipRole:
            if not self._enabled[src]:return '该色样已经在当前色卡方案中'
            return self._names[src]
        if role==Qt.TextAlignmentRole and col!=1:
            return Qt.AlignCenter
        return None

    def flags(self, index):
        if not index.isValid():return Qt.NoItemFlags
        src=self._source_row(index.row())
        flags=Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if src>=0 and not self._enabled[src]:
            return Qt.ItemIsSelectable
        if index.column()==1:flags|=Qt.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if role!=Qt.CheckStateRole or not index.isValid() or index.column()!=1:return False
        src=self._source_row(index.row())
        if src<0 or not self._enabled[src]:return False
        self._checked[src]=1 if value==Qt.Checked else 0
        self.dataChanged.emit(index,index,[Qt.CheckStateRole])
        return True

    def set_filter(self, text):
        q=(text or '').strip().casefold()
        self.beginResetModel()
        self._visible=(list(self._all_rows) if not q else [i for i,n in enumerate(self._names_cf) if q in n])
        self.endResetModel()

    def toggle_all_visible(self, checked):
        value=1 if checked else 0
        changed=False
        for src in self._visible:
            if self._enabled[src] and self._checked[src]!=value:
                self._checked[src]=value; changed=True
        if changed and self._visible:
            self.dataChanged.emit(self.index(0,1),self.index(len(self._visible)-1,1),[Qt.CheckStateRole])

    def counts(self):
        chosen=int(sum(self._checked)); available=int(sum(self._enabled))
        vis_enabled=[src for src in self._visible if self._enabled[src]]
        vis_checked=sum(1 for src in vis_enabled if self._checked[src])
        return chosen,available,len(vis_enabled),vis_checked

    def chosen_samples(self):
        return [sm for i,sm in enumerate(self.samples) if self._checked[i]]

    def sample_at(self, model_row):
        src=self._source_row(model_row)
        return self.samples[src] if src>=0 else None


class _QtxSwatchDelegate(QStyledItemDelegate):
    """Paint large-QTX swatches lazily without allocating thousands of icons.

    HF126 exposed a colour column through BackgroundRole, but several Windows
    styles do not visibly paint that background inside a QTableView cell.  A
    delegate makes the swatch explicit and keeps the 3,500-row picker virtual.
    """
    def paint(self, painter, option, index):
        if index.column()!=0:
            return super().paint(painter,option,index)
        painter.save(); painter.setRenderHint(QPainter.Antialiasing,True)
        rect=QRectF(option.rect).adjusted(8,5,-8,-5)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect,QColor('#EFF6FF'))
        sm=index.data(Qt.UserRole)
        color=QColor('#EEF2F7')
        if sm is not None:
            try:color=sample_display_qcolor(sm,sm.lab_d65_10)
            except Exception:
                try:color=lab_to_qcolor(sm.lab_d65_10)
                except Exception:pass
        painter.setPen(QPen(QColor('#D5DCE6'),1)); painter.setBrush(color)
        painter.drawRoundedRect(rect,4,4)
        if sm is not None and sample_is_fluorescent(sm):
            fold=min(11.0,max(7.0,rect.height()*0.42))
            tri=QPainterPath(); tri.moveTo(rect.right()-fold,rect.top()); tri.lineTo(rect.right(),rect.top()); tri.lineTo(rect.right(),rect.top()+fold); tri.closeSubpath()
            painter.fillPath(tri,QColor('#F8FAFD')); painter.setPen(QPen(QColor('#CFD8E6'),1)); painter.drawLine(QPointF(rect.right()-fold,rect.top()),QPointF(rect.right(),rect.top()+fold))
        painter.restore()


class QtxImportSelectionDialog(QDialog):
    """Select QTX samples without freezing on 3k+ colour files.

    Small imports keep the richer QListWidget presentation.  Large imports use a
    virtual QTableView/QAbstractTableModel so only visible rows are materialised.
    """
    LARGE_MODEL_THRESHOLD = 400

    def __init__(self, source_name: str, samples: list[Sample], already: set[str] | None = None, parent=None, purpose: str = "比色工作台"):
        super().__init__(parent)
        self.setWindowTitle(f"选择导入色样 · {Path(source_name).name}")
        self.resize(800, 590)
        self.samples = list(samples)
        self.already = set(already or set())
        self._large_mode = len(self.samples) > self.LARGE_MODEL_THRESHOLD
        lay = QVBoxLayout(self); lay.setContentsMargins(18,16,18,16); lay.setSpacing(10)

        title = QLabel(f"{Path(source_name).name}")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        lay.addWidget(title)
        note_text=(f"勾选需要加入{purpose}的色样。搜索只筛选当前列表，不改变勾选状态。"
                   + (" 大文件已启用虚拟列表，不会一次创建数千个卡片控件。" if self._large_mode else " 双击可查看测色明细。"))
        note = QLabel(note_text)
        note.setObjectName("muted"); note.setWordWrap(True); lay.addWidget(note)

        tools=QHBoxLayout()
        self.search=QLineEdit(); self.search.setPlaceholderText("搜索色样名称…"); self.search.textChanged.connect(self._apply_filter); tools.addWidget(self.search,1)
        self.check_all=QCheckBox("全选当前可见"); self.check_all.clicked.connect(self._toggle_all_clicked); tools.addWidget(self.check_all)
        lay.addLayout(tools)

        if self._large_mode:
            self.model=_LargeQtxSelectionModel(self.samples,self.already,self)
            self.table=QTableView(self)
            self.table.setModel(self.model)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.setAlternatingRowColors(True)
            self.table.setSortingEnabled(False)
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(28)
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Fixed)
            self.table.setColumnWidth(0,74)
            self.table.setItemDelegateForColumn(0,_QtxSwatchDelegate(self.table))
            self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
            for col in range(2,7): self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
            self.table.doubleClicked.connect(self._show_details_index)
            self.model.dataChanged.connect(lambda *_:self._update_count())
            lay.addWidget(self.table,1)
            self.list=None
        else:
            self.list=QListWidget(); self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.list.setAlternatingRowColors(True); self.list.setUniformItemSizes(True); self.list.itemDoubleClicked.connect(self._show_details)
            lay.addWidget(self.list,1)
            for sm in self.samples:
                L,a,b=sm.lab_d65_10; C=math.hypot(a,b); h=math.degrees(math.atan2(b,a))%360
                label=f"{sm.display_name}\nL* {L:.2f}   a* {a:.2f}   b* {b:.2f}   C* {C:.2f}   h° {h:.1f}"
                item=QListWidgetItem(label)
                item.setData(Qt.UserRole, sample_key(sm)); item.setData(Qt.UserRole+1, sm)
                item.setIcon(QIcon(self._swatch_pixmap(sm)))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                if sample_key(sm) in self.already:
                    item.setCheckState(Qt.Unchecked); item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setToolTip("该色样已经在当前工作台中")
                else:item.setCheckState(Qt.Checked)
                item.setSizeHint(QSize(0,58)); self.list.addItem(item)
            self.list.itemChanged.connect(lambda *_: self._update_count())

        bottom=QHBoxLayout(); self.count_label=QLabel(); self.count_label.setObjectName("muted"); bottom.addWidget(self.count_label); bottom.addStretch(1)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导入所选"); buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); bottom.addWidget(buttons); lay.addLayout(bottom)
        self._update_count()

    @staticmethod
    def _swatch_pixmap(sm: Sample):
        px=QPixmap(54,38); px.fill(sample_display_qcolor(sm,sm.lab_d65_10)); return px

    def _toggle_all_clicked(self, checked: bool):
        if self._large_mode:
            self.model.toggle_all_visible(bool(checked)); self._update_count(sync_checkbox=False); return
        self.list.blockSignals(True)
        try:
            for i in range(self.list.count()):
                it=self.list.item(i)
                if not (it.flags() & Qt.ItemIsEnabled) or it.isHidden():continue
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:self.list.blockSignals(False)
        self._update_count(sync_checkbox=False)

    def keyPressEvent(self, event):
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_A:
            self.check_all.setChecked(True); self._toggle_all_clicked(True); event.accept(); return
        super().keyPressEvent(event)

    def _apply_filter(self, text):
        if self._large_mode:
            self.model.set_filter(text); self._update_count(); return
        q=(text or "").strip().casefold()
        for i in range(self.list.count()):
            it=self.list.item(i); sm=it.data(Qt.UserRole+1)
            it.setHidden(bool(q) and q not in sm.display_name.casefold())
        self._update_count()

    def _show_details(self, item):
        sm=item.data(Qt.UserRole+1); owner=self.parent()
        if sm is not None and owner is not None and hasattr(owner,'show_sample_details_dialog'):owner.show_sample_details_dialog(sm)

    def _show_details_index(self,index):
        sm=self.model.sample_at(index.row()); owner=self.parent()
        if sm is not None and owner is not None and hasattr(owner,'show_sample_details_dialog'):owner.show_sample_details_dialog(sm)

    def _update_count(self, sync_checkbox=True):
        if self._large_mode:
            chosen,available,visible_count,visible_checked=self.model.counts()
            visible_enabled=visible_count
        else:
            chosen=sum(1 for i in range(self.list.count()) if self.list.item(i).checkState()==Qt.Checked)
            available=sum(1 for i in range(self.list.count()) if self.list.item(i).flags() & Qt.ItemIsEnabled)
            visible=[self.list.item(i) for i in range(self.list.count()) if (self.list.item(i).flags() & Qt.ItemIsEnabled) and not self.list.item(i).isHidden()]
            visible_enabled=len(visible); visible_checked=sum(1 for it in visible if it.checkState()==Qt.Checked)
        self.count_label.setText(f"已选择 {chosen} / {available} 个可导入色样")
        if sync_checkbox:
            self.check_all.blockSignals(True)
            try:self.check_all.setChecked(bool(visible_enabled) and visible_checked==visible_enabled)
            finally:self.check_all.blockSignals(False)

    def chosen(self) -> list[Sample]:
        if self._large_mode:return self.model.chosen_samples()
        out=[]
        for i in range(self.list.count()):
            it=self.list.item(i)
            if it.checkState()==Qt.Checked:
                sm=it.data(Qt.UserRole+1)
                if sm is not None:out.append(sm)
        return out


class ThresholdDialog(QDialog):
    def __init__(self, thresholds: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("阈值设置")
        self.resize(340, 260)
        self.thresholds = dict(thresholds)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("为每个色差公式设置阈值（判定合格与否）："))
        self.edits = {}
        for key in FORMULA_KEYS:
            row = QHBoxLayout()
            row.addWidget(QLabel(FORMULA_LABELS[key]), 1)
            edit = QLineEdit(f"{thresholds.get(key, 2.0):.2f}")
            edit.setFixedWidth(80)
            self.edits[key] = edit
            row.addWidget(edit)
            lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict:
        out = {}
        for key, edit in self.edits.items():
            try:
                out[key] = float(edit.text())
            except ValueError:
                out[key] = 2.0
        return out


class ColumnSettingsDialog(QDialog):
    """Categorised column chooser for dense QC tables."""
    def __init__(self, headers: list, keys: list, current_hidden: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("列与视图")
        self.resize(440, 650)
        self.headers=headers; self.keys=keys; self.hidden=set(current_hidden)
        root=QVBoxLayout(self); root.setContentsMargins(16,14,16,14); root.setSpacing(10)
        title=QLabel('选择数据列'); title.setObjectName('sectionTitle'); root.addWidget(title)
        hint=QLabel('色块与名称固定显示。其它列按任务分组；此设置只影响当前工作台的屏幕显示，不改变数据或导出内容。')
        hint.setWordWrap(True); hint.setObjectName('muted'); root.addWidget(hint)
        self.search=QLineEdit(); self.search.setPlaceholderText('搜索列名称…'); self.search.setClearButtonEnabled(True); root.addWidget(self.search)
        presets=QFrame(); pl=FlowLayout(presets,0,6,6)
        for preset_id in ('basic','chromaticity','difference','shade555','full'):
            b=QPushButton(WORKBENCH_VIEW_PRESETS[preset_id]['label']); b.setProperty('preset_id',preset_id); b.clicked.connect(lambda _=False,p=preset_id:self._apply_preset(p)); pl.addWidget(b)
        root.addWidget(presets)
        self.tree=QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setRootIsDecorated(True); self.tree.setAlternatingRowColors(False); root.addWidget(self.tree,1)
        categories=[
            ('基础信息',{'name','illuminant','observer','role'}),
            ('CIELAB / LCh',{'L','a','b','C','h'}),
            ('色差分量',{'dL','da','db','dC','dh'}),
            ('三刺激值',{'X','Y','Z'}),
            ('白度 / 色调',{'WI','Tint'}),
            ('色差公式',{'de76','cmc','de94','de00'}),
            ('质量判定',{'mi','shade555','verdict'}),
        ]
        self._items={}
        header_by_key={k:h for h,k in zip(headers,keys) if k}
        existing=set(k for k in keys if k)
        for group_name,group_keys in categories:
            root_item=QTreeWidgetItem([group_name]); root_item.setFlags(root_item.flags() & ~Qt.ItemIsSelectable); self.tree.addTopLevelItem(root_item)
            for k in keys:
                if not k or k not in group_keys:continue
                item=QTreeWidgetItem([header_by_key.get(k,k)]); item.setData(0,Qt.UserRole,k); item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                if k=='name':
                    item.setCheckState(0,Qt.Checked); item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
                else:item.setCheckState(0,Qt.Unchecked if k in self.hidden else Qt.Checked)
                root_item.addChild(item); self._items[k]=item
            root_item.setExpanded(True)
        # Future/unknown columns still remain configurable instead of disappearing.
        leftovers=[k for k in existing if k not in self._items]
        if leftovers:
            other=QTreeWidgetItem(['其它']); other.setFlags(other.flags() & ~Qt.ItemIsSelectable); self.tree.addTopLevelItem(other)
            for k in leftovers:
                item=QTreeWidgetItem([header_by_key.get(k,k)]); item.setData(0,Qt.UserRole,k); item.setFlags(item.flags()|Qt.ItemIsUserCheckable); item.setCheckState(0,Qt.Unchecked if k in self.hidden else Qt.Checked); other.addChild(item); self._items[k]=item
            other.setExpanded(True)
        self.search.textChanged.connect(self._filter)
        actions=QHBoxLayout();
        all_btn=QPushButton('全选'); none_btn=QPushButton('仅保留名称'); default_btn=QPushButton('恢复默认')
        all_btn.clicked.connect(self._select_all); none_btn.clicked.connect(self._select_none); default_btn.clicked.connect(lambda:self._apply_keys(DEFAULT_VISIBLE_KEYS))
        actions.addWidget(all_btn); actions.addWidget(none_btn); actions.addWidget(default_btn); actions.addStretch(1); root.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText('确定'); buttons.button(QDialogButtonBox.Cancel).setText('取消'); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _filter(self,text):
        q=str(text or '').strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            group=self.tree.topLevelItem(i); any_vis=False
            for j in range(group.childCount()):
                child=group.child(j); vis=(not q or q in child.text(0).casefold() or q in str(child.data(0,Qt.UserRole) or '').casefold()); child.setHidden(not vis); any_vis|=vis
            group.setHidden(not any_vis)

    def _apply_keys(self,visible_keys):
        visible=set(visible_keys or [])
        for k,item in self._items.items():
            if k=='name':item.setCheckState(0,Qt.Checked)
            else:item.setCheckState(0,Qt.Checked if k in visible else Qt.Unchecked)

    def _apply_preset(self,preset_id):
        cfg=WORKBENCH_VIEW_PRESETS.get(preset_id) or {}; keys=cfg.get('keys')
        self._apply_keys(set(self._items) if keys is None else keys)

    def _select_all(self): self._apply_keys(set(self._items))
    def _select_none(self): self._apply_keys({'name'})

    def hidden_keys(self) -> list[str]:
        return [k for k,item in self._items.items() if k!='name' and item.checkState(0)!=Qt.Checked]


class WorkbenchNameDelegate(QStyledItemDelegate):
    """工作台名称列编辑器：双击时保留原名称并蓝色全选，Enter 提交，Esc 取消。"""
    def createEditor(self, parent, option, index):
        editor=QLineEdit(parent)
        editor.setFrame(False)
        editor.setStyleSheet("QLineEdit{background:#FFFFFF;border:2px solid #4C8DFF;padding:4px 6px;color:#172033;} QLineEdit:selected{background:#3478F6;color:white;}")
        return editor
    def setEditorData(self, editor, index):
        editor.setText(str(index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or ""))
        QTimer.singleShot(0, editor.selectAll)
    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), Qt.EditRole)


class WorkbenchTableView(QTableView):
    """Comparison table with responsive widths and two frozen identity columns.

    The frozen overlay shares the exact same model/selection model as the main
    view.  No data is duplicated and exports continue reading the original model.
    """
    FROZEN_COLUMNS=(0,1)
    def __init__(self, owner, workbench_id, parent=None):
        super().__init__(parent); self.owner=owner; self.workbench_id=workbench_id
        self.setAcceptDrops(True); self.setDragEnabled(True); self.setDragDropMode(QAbstractItemView.DragDrop)
        self._view_mode='fit'; self._fit_pending=False
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel); self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._frozen=QTableView(self)
        self._frozen.setObjectName('workbenchFrozenColumns')
        self._frozen.setFocusPolicy(Qt.StrongFocus)
        self._frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self._frozen.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._frozen.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel); self._frozen.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._frozen.setSelectionBehavior(QAbstractItemView.SelectRows); self._frozen.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._frozen.setEditTriggers(QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed|QAbstractItemView.SelectedClicked)
        self._frozen.setDragEnabled(False); self._frozen.setAcceptDrops(False)
        self._frozen.verticalHeader().hide(); self._frozen.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._frozen.setStyleSheet('QTableView#workbenchFrozenColumns{border:0;border-right:1px solid #CBD5E1;background:#FFFFFF;}')
        self._frozen.setContextMenuPolicy(Qt.CustomContextMenu)
        self._frozen.customContextMenuRequested.connect(lambda pos:self.owner.show_workbench_row_menu(self.workbench_id,self._frozen,pos))
        self._frozen.installEventFilter(self); self._frozen.viewport().installEventFilter(self)
        self.horizontalHeader().sectionResized.connect(self._sync_frozen_section_width)
        self.verticalHeader().sectionResized.connect(lambda row,_old,new:self._frozen.setRowHeight(row,new))
        self.verticalScrollBar().valueChanged.connect(self._frozen.verticalScrollBar().setValue)
        self._frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)

    def setModel(self,model):
        super().setModel(model)
        self._frozen.setModel(model)
        if self.selectionModel() is not None:self._frozen.setSelectionModel(self.selectionModel())
        if model is not None:
            for c in range(model.columnCount()):self._frozen.setColumnHidden(c,c not in self.FROZEN_COLUMNS)
            for c in self.FROZEN_COLUMNS:
                if c<model.columnCount():self._frozen.setColumnWidth(c,self.columnWidth(c))
        self._update_frozen_geometry(); self._frozen.show(); self._frozen.raise_()

    def setItemDelegateForColumn(self,column,delegate):
        super().setItemDelegateForColumn(column,delegate)
        if column in self.FROZEN_COLUMNS:self._frozen.setItemDelegateForColumn(column,delegate)

    def _sync_frozen_section_width(self,logical,old,new):
        if logical in self.FROZEN_COLUMNS:
            self._frozen.setColumnWidth(logical,new); self._update_frozen_geometry()

    def _frozen_width(self):
        return sum(self.columnWidth(c) for c in self.FROZEN_COLUMNS if self.model() is not None and c<self.model().columnCount())

    def _update_frozen_geometry(self):
        if not hasattr(self,'_frozen'):return
        try:self._frozen.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        except RuntimeError:pass
        x=self.verticalHeader().width()+self.frameWidth() if self.verticalHeader().isVisible() else self.frameWidth()
        w=max(0,self._frozen_width())
        h=max(0,self.viewport().height()+self.horizontalHeader().height())
        self._frozen.setGeometry(x,self.frameWidth(),w,h); self._frozen.raise_()

    def eventFilter(self,obj,event):
        if obj in (getattr(self,'_frozen',None),getattr(getattr(self,'_frozen',None),'viewport',lambda:None)()):
            if event.type()==QEvent.KeyPress:
                if event.matches(QKeySequence.Copy):self.copy_selected(); event.accept(); return True
                if event.matches(QKeySequence.SelectAll):self.selectAll(); event.accept(); return True
                if event.key()==Qt.Key_Delete and event.modifiers()==Qt.NoModifier:
                    self.owner.remove_workbench_samples(self.workbench_id); event.accept(); return True
            if event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton and obj is self._frozen.viewport():
                if not self._frozen.indexAt(event.position().toPoint()).isValid():
                    self.clearSelection(); self.setCurrentIndex(QModelIndex()); self.owner._on_workbench_table_focus_changed(self.workbench_id,self)
        return super().eventFilter(obj,event)

    def set_view_mode(self, mode):
        self._view_mode=str(mode or 'fit'); self.apply_view_mode()

    def apply_view_mode(self):
        model=self.model()
        if model is None:return
        visible=[c for c in range(model.columnCount()) if not self.isColumnHidden(c)]
        if not visible:return
        keys=getattr(model,'KEYS',[None]*model.columnCount())
        if self._view_mode=='compact':
            for c in visible:
                k=keys[c] if c<len(keys) else None; w=48 if k not in {'name','role','verdict'} else (145 if k=='name' else 72)
                if c==0:w=48
                self.setColumnWidth(c,w)
            self._update_frozen_geometry(); return
        if self._view_mode=='standard':
            for c in visible:
                k=keys[c] if c<len(keys) else None; w=58
                if c==0:w=62
                elif k=='name':w=210
                elif k=='role':w=96
                elif k in {'illuminant','observer','verdict'}:w=76
                self.setColumnWidth(c,w)
            self._update_frozen_geometry(); return
        desired=[]
        for c in visible:
            k=keys[c] if c<len(keys) else None
            if c==0:w=58
            elif k=='name':w=190
            elif k=='role':w=88
            elif k in {'illuminant','observer','verdict'}:w=68
            else:w=55
            desired.append((c,w,k))

        # Fit mode keeps numeric columns compact, then gives genuinely spare width
        # to the pinned name column.  The previous min(1.0, scale) path could leave
        # hundreds of empty pixels on wide screens even though long sample names
        # were still elided.  Scientific values and column visibility are unchanged.
        viewport=max(360,self.viewport().width()-8)
        base_total=sum(w for _,w,_ in desired)
        extra=max(0,viewport-base_total)
        name_extra=min(extra,420)
        adjusted=[]
        for c,w,k in desired:
            if k=='name':w+=name_extra
            adjusted.append((c,w,k))
        desired=adjusted
        frozen=sum(w for c,w,k in desired if c in self.FROZEN_COLUMNS)
        scroll_desired=[(c,w,k) for c,w,k in desired if c not in self.FROZEN_COLUMNS]
        total=sum(w for _,w,_ in scroll_desired); avail=max(260,viewport-frozen); scale=min(1.0,avail/max(1,total))
        for c,w,k in desired:
            if c in self.FROZEN_COLUMNS:
                floor=46 if c==0 else 150; value=max(floor,w)
            else:
                floor=60 if k=='role' else 44; value=max(floor,int(w*scale))
            self.setColumnWidth(c,value)
        self._update_frozen_geometry()

    def resizeEvent(self,event):
        super().resizeEvent(event); self._update_frozen_geometry()
        if self._view_mode=='fit' and not self._fit_pending:
            self._fit_pending=True; QTimer.singleShot(0,self._apply_fit_after_resize)

    def _apply_fit_after_resize(self):
        self._fit_pending=False
        if self._view_mode=='fit':self.apply_view_mode()

    def showEvent(self,event):
        super().showEvent(event); self._update_frozen_geometry(); self._frozen.show(); self._frozen.raise_()

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection(); self.setCurrentIndex(QModelIndex()); self.owner._on_workbench_table_focus_changed(self.workbench_id,self)
        super().mousePressEvent(event)

    def copy_selected(self):
        model=self.model()
        if model is None or self.selectionModel() is None:return
        rows=sorted({index.row() for index in self.selectionModel().selectedRows()})
        columns=[col for col in range(model.columnCount()) if not self.isColumnHidden(col)]
        if rows:QApplication.clipboard().setText('\n'.join('\t'.join(str(model.data(model.index(row,col),Qt.DisplayRole) or '') for col in columns) for row in rows))

    def keyPressEvent(self,event):
        if event.matches(QKeySequence.Copy):self.copy_selected(); event.accept(); return
        if event.matches(QKeySequence.SelectAll):self.selectAll(); event.accept(); return
        if event.key()==Qt.Key_Delete and event.modifiers()==Qt.NoModifier:self.owner.remove_workbench_samples(self.workbench_id); event.accept(); return
        super().keyPressEvent(event)

    def startDrag(self,supportedActions):
        model=self.model(); rows=sorted({i.row() for i in self.selectionModel().selectedRows()}); keys=[]
        for row in rows:
            if hasattr(model,'_keys') and 0<=row<len(model._keys):
                key=model._keys[row]
                if key and key!='__AVERAGE__':keys.append(key)
        if not keys:return
        mime=QMimeData(); mime.setData(CARD_MIME,'\n'.join(keys).encode('utf-8')); mime.setData('application/x-chromatic-workbench-sample',keys[0].encode('utf-8'))
        drag=QDrag(self); drag.setMimeData(mime); drag.exec(Qt.CopyAction|Qt.MoveAction,Qt.MoveAction)

    def dragEnterEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME):event.setDropAction(Qt.CopyAction); event.accept(); return
        if event.mimeData().hasUrls():
            paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragEnterEvent(event)
    def dragMoveEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME):event.setDropAction(Qt.CopyAction); event.accept(); return
        if event.mimeData().hasUrls():event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragMoveEvent(event)
    def dropEvent(self,event):
        if event.source() is self and event.mimeData().hasFormat('application/x-chromatic-workbench-sample'):
            event.setDropAction(Qt.MoveAction); super().dropEvent(event); return
        if event.mimeData().hasFormat(CARD_MIME):
            keys=[x for x in bytes(event.mimeData().data(CARD_MIME)).decode('utf-8').splitlines() if x]; self.owner.drop_sample_keys_to_workbench(self.workbench_id,keys); event.setDropAction(Qt.CopyAction); event.accept(); return
        if event.mimeData().hasUrls():
            paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:self.owner.import_paths_to_workbench(self.workbench_id,paths); event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dropEvent(event)


class WorkbenchTableModel(QAbstractTableModel):
    HEADERS = ["色块", "名称", "光源", "观察者", "角色", "L*", "a*", "b*", "C*", "h°",
               "ΔL*", "Δa*", "Δb*", "ΔC*", "ΔH*",
               "X", "Y", "Z", "WI", "Tint",
               "ΔE*ab", "CMC", "CIE94", "ΔE00", "MI", "555", "判定"]
    KEYS = [None, "name", "illuminant", "observer", "role", "L", "a", "b", "C", "h",
            "dL", "da", "db", "dC", "dh",
            "X", "Y", "Z", "WI", "Tint",
            "de76", "cmc", "de94", "de00", "mi", "shade555", "verdict"]

    def __init__(self, workbench_id: str, owner: "MainWindow"):
        super().__init__(owner)
        self.workbench_id = workbench_id
        self.owner = owner
        self._rows: list[dict] = []
        self._keys: list[str] = []

    def flags(self, index):
        base=super().flags(index)
        if index.isValid():
            flags = base | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            # 名称列允许像 Excel/Datacolor 一样直接双击改工作台显示名。
            if index.column() == 1 and self._keys[index.row()] != "__AVERAGE__":
                flags |= Qt.ItemIsEditable
            return flags
        return base | Qt.ItemIsDropEnabled

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole and index.isValid() and index.column() == 1:
            row=index.row()
            if 0 <= row < len(self._keys):
                name=str(value or '').strip()
                if name:
                    self.owner.rename_workbench_sample(self.workbench_id, self._keys[row], name)
                    return True
        return False

    def supportedDropActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return ["application/x-chromatic-workbench-sample"]

    def mimeData(self, indexes):
        mime=QMimeData()
        rows=sorted({i.row() for i in indexes if i.isValid()})
        if rows:
            key=self._keys[rows[0]] if rows[0] < len(self._keys) else ""
            mime.setData("application/x-chromatic-workbench-sample", str(key).encode("utf-8"))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        if action!=Qt.MoveAction or not data.hasFormat("application/x-chromatic-workbench-sample"): return False
        drag_key=bytes(data.data("application/x-chromatic-workbench-sample")).decode("utf-8")
        target_row=parent.row() if parent.isValid() else row
        target_key=self._keys[target_row] if 0 <= target_row < len(self._keys) else None
        self.owner.reorder_workbench_sample(self.workbench_id, drag_key, target_key)
        return True

    @profiled('workbench.model_reload')
    def reload(self, wb: dict):
        self.beginResetModel()
        self._rows = []
        self._keys = []
        samples = self.owner._workbench_samples(wb)
        std_sample = None
        if wb.get("standard_key") == "__AVERAGE__":
            pass
        elif wb.get("standard_key"):
            std_sample = next((s for s in samples if sample_key(s) == wb["standard_key"]), None)

        # 手动标准始终放到数据表第一行，方便一眼确认当前参考样。
        # 平均标准不是一个真实色样，不占用表格行。
        if std_sample is not None:
            std_key = sample_key(std_sample)
            samples = [std_sample] + [s for s in samples if sample_key(s) != std_key]

        avg_standard = wb.get("average_standard")
        avg_sample_obj = None
        if avg_standard is not None:
            try:
                avg_sample_obj = self.owner._deserialize_sample(avg_standard)
            except Exception:
                avg_sample_obj = None

        active_formula = self.owner.active_formula
        threshold = self.owner.thresholds.get(active_formula, 2.0)
        shade_cfg=dict(wb.get("shade555") or {})

        illuminants=list(dict.fromkeys(wb.get("illuminants") or ["D65"]))
        wb_observer=int(wb.get("observer",10))

        # 平均标准也是当前工作台的真实参考对象，应像手动标准一样固定显示在第一行。
        # 旧版只在黄色标准卡中显示平均值，表格/导出看不到标准本身，容易误解。
        if wb.get("standard_key") == "__AVERAGE__" and avg_sample_obj is not None:
            for illum in illuminants:
                try:
                    if illum == "D65" and wb_observer == 10:
                        (X,Y,Z)=avg_sample_obj.xyz_d65_10; lab=avg_sample_obj.lab_d65_10
                    else:
                        (X,Y,Z),lab=self.owner._workbench_xyz_lab(avg_sample_obj,illum,wb_observer)
                except Exception:
                    continue
                C=math.hypot(lab[1],lab[2]); h=math.degrees(math.atan2(lab[2],lab[1]))%360
                try:
                    WI=f"{cie_whiteness_d65_10((X,Y,Z)):.2f}" if illum=="D65" and wb_observer==10 else "—"
                    Tint=f"{cie_tint_d65_10((X,Y,Z)):.2f}" if illum=="D65" and wb_observer==10 else "—"
                except Exception:
                    WI=Tint="—"
                self._rows.append({
                    "lab":lab,"sample":avg_sample_obj,"standard":True,"name":avg_sample_obj.display_name,"illuminant":illum,"observer":f"{wb_observer}°",
                    "role":"当前标准（平均）","L":f"{lab[0]:.2f}","a":f"{lab[1]:.2f}","b":f"{lab[2]:.2f}","C":f"{C:.2f}","h":f"{h:.1f}",
                    "dL":"0.00","da":"0.00","db":"0.00","dC":"0.00","dh":"0.00",
                    "X":f"{X:.3f}","Y":f"{Y:.3f}","Z":f"{Z:.3f}","WI":WI,"Tint":Tint,
                    "de76":"—","cmc":"—","de94":"—","de00":"—","mi":"—","shade555":self.owner._shade555_standard_code(shade_cfg) if shade_cfg.get('enabled') else "—","verdict":"当前标准",
                })
                self._keys.append("__AVERAGE__")

        for s in samples:
            is_std = (std_sample is not None and sample_key(s) == sample_key(std_sample))
            ref = std_sample if std_sample is not None else avg_sample_obj
            for illum in illuminants:
                try:
                    if illum == "D65" and wb_observer == 10 and s.kind != "AVERAGE":
                        (X,Y,Z)=s.xyz_d65_10; lab=s.lab_d65_10
                    else:
                        (X,Y,Z), lab = self.owner._workbench_xyz_lab(s, illum, wb_observer)
                except Exception:
                    # 没有光谱时仍允许显示权威 D65/10° 数据；其它光源跳过。
                    if illum != "D65":
                        continue
                    lab=s.lab_d65_10; (X,Y,Z)=s.xyz_d65_10
                C = math.hypot(lab[1], lab[2]); h = math.degrees(math.atan2(lab[2], lab[1])) % 360
                try:
                    if illum != "D65" or wb_observer != 10:
                        raise ValueError
                    WI=f"{cie_whiteness_d65_10((X,Y,Z)):.2f}"; Tint=f"{cie_tint_d65_10((X,Y,Z)):.2f}"
                except Exception:
                    WI=Tint="—"
                de76=cmc=de94=de00=mi="—"; dL=da=db=dC=dh="—"; verdict="—"; pair_result=None
                shade_code=shade1=shade2=shade3="—"
                if is_std:
                    dL=da=db=dC=dh="0.00"; verdict="当前标准"
                elif ref is not None:
                    try:
                        if illum == "D65" and wb_observer == 10 and ref.kind != "AVERAGE":
                            ref_lab=ref.lab_d65_10
                        else:
                            _,ref_lab=self.owner._workbench_xyz_lab(ref,illum,wb_observer)
                        ref_C=math.hypot(ref_lab[1],ref_lab[2]); ref_h=math.degrees(math.atan2(ref_lab[2],ref_lab[1]))%360
                        dL_v=lab[0]-ref_lab[0]; da_v=lab[1]-ref_lab[1]; db_v=lab[2]-ref_lab[2]; dC_v=C-ref_C
                        # Datacolor 对应的 CIELAB ΔH*，不是直接的 hue-angle 差 Δh°。
                        dh_v=delta_h_cielab_signed(ref_lab, lab)
                        dL,da,db,dC,dh=(f"{v:+.2f}" for v in (dL_v,da_v,db_v,dC_v,dh_v))
                        r=self.owner._workbench_pair_analysis(ref,s,illum,wb_observer); pair_result=r
                        de76=f"{r.delta_e76:.2f}"; cmc=f"{r.cmc21:.2f}"; de94=f"{r.delta_e94:.2f}"; de00=f"{r.delta_e00:.2f}"
                        mi="—" if r.metamerism_index is None else f"{r.metamerism_index:.2f}"
                        current_val={"delta_e00":r.delta_e00,"delta_e76":r.delta_e76,"cmc21":r.cmc21,"delta_e94":r.delta_e94}.get(active_formula)
                        verdict="合格" if (current_val is not None and current_val<=threshold) else "超差"
                    except Exception:
                        pass
                if shade_cfg.get('enabled') and ref is not None:
                    try:
                        mode=str(shade_cfg.get('mode','LAB')).upper(); ranges=dict(shade_cfg.get('ranges') or {})
                        if not ranges:ranges={'L':(-1.8,1.8),'a':(-.9,.9),'b':(-.9,.9)} if mode=='LAB' else {'L':(-1.8,1.8),'C':(-.9,.9),'H':(-.9,.9)}
                        blocks=int(shade_cfg.get('blocks',9)); qualified=(verdict=='合格')
                        allowed=(is_std or qualified)
                        if allowed:
                            sr=shade_555(ref_lab if not is_std else lab,lab,ranges,mode=mode,blocks=blocks)
                            shade_code=sr.code; shade1=str(sr.first) if sr.first is not None else '—'; shade2=str(sr.second) if sr.second is not None else '—'; shade3=str(sr.third) if sr.third is not None else '—'
                    except Exception:
                        pass
                self._rows.append({
                    "lab":lab,"sample":s,"standard":is_std,"name":s.display_name,"illuminant":illum,"observer":f"{wb_observer}°",
                    "role":"当前标准" if is_std else "批次样",
                    "L":f"{lab[0]:.2f}","a":f"{lab[1]:.2f}","b":f"{lab[2]:.2f}","C":f"{C:.2f}","h":f"{h:.1f}",
                    "dL":dL,"da":da,"db":db,"dC":dC,"dh":dh,
                    "X":f"{X:.3f}","Y":f"{Y:.3f}","Z":f"{Z:.3f}","WI":WI,"Tint":Tint,
                    "de76":de76,"cmc":cmc,"de94":de94,"de00":de00,"mi":mi,
                    "shade555":shade_code,"verdict":verdict,
                })
                self._keys.append(sample_key(s))
        self.endResetModel()

    def key_at(self, row: int) -> str | None:
        if 0 <= row < len(self._keys):
            return self._keys[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        return self.HEADERS[section] if role == Qt.DisplayRole and orientation == Qt.Horizontal else None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DecorationRole and col == 0:
            return sample_display_qcolor(row["sample"],row["lab"])
        if role == Qt.BackgroundRole and row["standard"]:
            return QColor("#EFF6FF")
        if role == Qt.ForegroundRole:
            return QColor("#DC2626") if row["verdict"] == "超差" else QColor("#374151")
        if role == Qt.TextAlignmentRole and col >= 2:
            return Qt.AlignCenter
        if role == Qt.EditRole and col == 1:
            # QTableView 编辑器会先读取 EditRole；返回当前名称，避免双击后出现空白编辑框。
            return row.get("name", "")
        if role == Qt.DisplayRole:
            return "" if self.KEYS[col] is None else row[self.KEYS[col]]
        return None


class CustomerQtxManagerDialog(QDialog):
    """客户 QTX 管理器：左侧文件列表 + 类浏览器可关闭标签页。"""
    def __init__(self, owner, customer, parent=None):
        super().__init__(parent or owner); self.owner=owner; self.customer=customer
        self.setWindowTitle(f'客户 QTX 管理 · {customer}'); self.resize(1080,720)
        self.states={}; self.deleted_paths=set()
        root=QVBoxLayout(self)
        top=QHBoxLayout(); top.addWidget(QLabel(f'客户：{customer}')); top.addStretch()
        add=QPushButton('＋ 添加 QTX'); add.clicked.connect(self.add_qtx); top.addWidget(add)
        delete=QPushButton('删除当前 QTX'); delete.clicked.connect(self.delete_current_qtx); top.addWidget(delete)
        steward=QPushButton('数据管理员…'); steward.setToolTip('正式色库属于组织/客户；这里调整负责维护该数据的管理员，不改变业务归属。'); steward.clicked.connect(self.change_steward); top.addWidget(steward)
        save=QPushButton('保存更改'); save.clicked.connect(self.save_changes); top.addWidget(save); root.addLayout(top)
        split=QSplitter(Qt.Horizontal); root.addWidget(split,1)
        left=QFrame(); left.setObjectName('filePanel'); ll=QVBoxLayout(left); ll.addWidget(QLabel('客户 QTX（双击打开）'))
        self.list=QListWidget(); self.list.itemDoubleClicked.connect(lambda item:self.open_state(item.data(Qt.UserRole))); ll.addWidget(self.list,1); split.addWidget(left)
        self.tabs=QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setMovable(True); self.tabs.tabCloseRequested.connect(self.tabs.removeTab); split.addWidget(self.tabs); split.setSizes([280,760])
        note=QLabel('保存规则：名称未改变 → 覆盖该客户色库中的原 QTX 快照；名称改过 → 保留原数据并生成一份新的 QTX 快照。关闭标签页只关闭视图，不删除数据。')
        note.setWordWrap(True); root.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.load_customer_files()

    def load_customer_files(self):
        self.states.clear(); self.list.clear()
        for row in self.owner.store.list_files():
            if row.customer != self.customer: continue
            samples=self.owner.store.load_samples(row.path); sid=str(uuid.uuid4())
            self.states[sid]={'id':sid,'original_path':row.path,'source_path':row.path,'original_name':Path(row.path).name,'name':Path(row.path).name,'samples':samples,'new':False,'deleted':False,'meta':self.owner.store.formal_file_metadata(row.path) or {}}
            self._add_list_item(sid)

    def _add_list_item(self,sid):
        st=self.states[sid]; item=QListWidgetItem(f"{st['name']}\n{len(st['samples'])} 个色样"); item.setData(Qt.UserRole,sid); self.list.addItem(item)

    def _tab_for_state(self,sid):
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).property('state_id')==sid: return i
        return -1

    def open_state(self,sid):
        if sid not in self.states or self.states[sid].get('deleted'): return
        idx=self._tab_for_state(sid)
        if idx>=0: self.tabs.setCurrentIndex(idx); return
        st=self.states[sid]; page=QWidget(); page.setProperty('state_id',sid); vl=QVBoxLayout(page)
        row=QHBoxLayout(); row.addWidget(QLabel('QTX 名称')); edit=QLineEdit(st['name']); edit.textChanged.connect(lambda text,sid=sid:self._rename_state(sid,text)); row.addWidget(edit,1); vl.addLayout(row)
        meta=st.get('meta') or {}; users={int(u.user_id):u for u in self.owner.auth_store.list_users()}
        def ulabel(uid):
            u=users.get(int(uid or 0)); return (u.display_name or u.username) if u else ('历史未知' if not int(uid or 0) else f'用户 #{int(uid)}')
        created=time.strftime('%Y-%m-%d %H:%M',time.localtime(float(meta.get('created_at') or 0))) if float(meta.get('created_at') or 0)>0 else '历史未知'
        updated=time.strftime('%Y-%m-%d %H:%M',time.localtime(float(meta.get('updated_at') or 0))) if float(meta.get('updated_at') or 0)>0 else '历史未知'
        info=QLabel(f"业务归属：{meta.get('customer') or self.customer}   数据状态：正式   数据管理员：{ulabel(meta.get('steward_user_id'))}\n创建人：{ulabel(meta.get('created_by_user_id'))}   最后修改人：{ulabel(meta.get('updated_by_user_id'))}   创建：{created}   更新：{updated}   色样数：{len(st['samples'])}"); info.setObjectName('muted'); info.setWordWrap(True); vl.addWidget(info)
        lst=QListWidget(); lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for sm in st['samples']:
            L,a,b=self.owner.sample_lab(sm); it=QListWidgetItem(f'{sm.display_name}    L* {L:.2f}  a* {a:.2f}  b* {b:.2f}'); lst.addItem(it)
        vl.addWidget(lst,1)
        idx=self.tabs.addTab(page,st['name']); self.tabs.setCurrentIndex(idx)

    def _rename_state(self,sid,text):
        st=self.states[sid]; st['name']=text.strip() or st['original_name']
        idx=self._tab_for_state(sid)
        if idx>=0: self.tabs.setTabText(idx,st['name'])
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole)==sid:
                self.list.item(i).setText(f"{st['name']}\n{len(st['samples'])} 个色样"); break

    def add_qtx(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加 QTX 到客户','', 'QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)')
        for path in paths:
            try:
                full=str(Path(path).resolve()); samples=[replace(x,source_file=full) for x in parse_qtx_file(full)]; sid=str(uuid.uuid4())
                self.states[sid]={'id':sid,'original_path':None,'source_path':full,'original_name':Path(full).name,'name':Path(full).name,'samples':samples,'new':True,'deleted':False}; self._add_list_item(sid); self.open_state(sid)
            except Exception as exc: QMessageBox.warning(self,'添加 QTX 失败',f'{Path(path).name}：{exc}')

    def change_steward(self):
        item=self.list.currentItem(); sid=item.data(Qt.UserRole) if item else (self.tabs.currentWidget().property('state_id') if self.tabs.currentWidget() else None)
        if not sid or sid not in self.states:return
        st=self.states[sid]
        if not st.get('original_path'):
            QMessageBox.information(self,'数据管理员','请先保存/发布该 QTX，正式业务数据才需要数据管理员。');return
        users=[u for u in self.owner.auth_store.list_users() if u.enabled]
        if not users:return
        labels=[f'{u.display_name or u.username}  ({u.username})' for u in users]
        current=int((st.get('meta') or {}).get('steward_user_id') or 0); start=next((i for i,u in enumerate(users) if int(u.user_id)==current),0)
        choice,ok=QInputDialog.getItem(self,'数据管理员','负责维护该正式色库数据的管理员：',labels,start,False)
        if not ok:return
        user=users[labels.index(choice)]
        try:
            self.owner.store.set_formal_file_steward(st['original_path'],user.user_id); self.owner.audit('FORMAL_LIBRARY_STEWARD',st.get('name',''),f'{self.customer} -> {user.username}')
            st['meta']=self.owner.store.formal_file_metadata(st['original_path']) or {}; QMessageBox.information(self,'数据管理员',f'已设置为：{user.display_name or user.username}\n\n正式色库的业务归属仍是：{self.customer}')
            idx=self._tab_for_state(sid)
            if idx>=0:self.tabs.removeTab(idx); self.open_state(sid)
        except Exception as exc:QMessageBox.warning(self,'数据管理员',str(exc))

    def delete_current_qtx(self):
        item=self.list.currentItem(); sid=item.data(Qt.UserRole) if item else (self.tabs.currentWidget().property('state_id') if self.tabs.currentWidget() else None)
        if not sid or sid not in self.states: return
        st=self.states[sid]
        if QMessageBox.question(self,'删除客户 QTX',f"确定从客户【{self.customer}】删除【{st['name']}】吗？\n不会删除电脑上的原 QTX 文件。",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        if st.get('original_path'): self.deleted_paths.add(st['original_path'])
        st['deleted']=True
        idx=self._tab_for_state(sid)
        if idx>=0: self.tabs.removeTab(idx)
        for i in range(self.list.count()-1,-1,-1):
            if self.list.item(i).data(Qt.UserRole)==sid: self.list.takeItem(i)

    def save_changes(self):
        # 先应用显式删除
        for path in self.deleted_paths: self.owner.store.remove_file(path)
        existing=list(self.owner.store.list_files())
        for st in self.states.values():
            if st.get('deleted'): continue
            name=(st.get('name') or st.get('original_name') or '未命名.qtx').strip()
            if not name.lower().endswith('.qtx'): name += '.qtx'
            if st.get('original_path') and name==st.get('original_name'):
                target=st['original_path']
            elif st.get('original_path'):
                target=self.owner.make_library_snapshot_path(name)
            else:
                same=next((r for r in existing if r.customer==self.customer and Path(r.path).name.casefold()==name.casefold()),None)
                if same: target=same.path
                elif name==Path(st['source_path']).name: target=st['source_path']
                else: target=self.owner.make_library_snapshot_path(name)
            samples=[replace(sm,source_file=target) for sm in st['samples']]
            self.owner.store.save_file(target,self.customer,samples)
        self.owner.update_customers(); self.owner.refresh_card_source(); self.owner.library_has_explicit_view=True; self.owner._tiles_keys=None; self.owner.build_tiles()
        QMessageBox.information(self,'客户 QTX','更改已保存到色库。')
        self.load_customer_files()




class FindQtxDropZone(QFrame):
    """查色标准 QTX 拖放区。

    Windows 下拖放事件有时会先落到内部 QLabel；因此内部标签设为鼠标透明，
    并完整实现 dragEnter/dragMove/drop，保证 Explorer 拖入 QTX 时由本控件接收。
    """
    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(92)
        self.setStyleSheet(
            "QFrame{border:1px dashed #CBD5E1;border-radius:10px;background:#F8FAFC;}"
            "QFrame:hover{border-color:#60A5FA;background:#EFF6FF;}"
        )
        lay=QVBoxLayout(self); lay.setContentsMargins(12,10,12,10)
        t=QLabel("拖入一个或多个 QTX 到这里"); t.setAlignment(Qt.AlignCenter); t.setStyleSheet("font-weight:600;border:0;background:transparent;")
        sub=QLabel("或单击选择文件 · 一个 QTX 含多个色样时会建立独立查色任务"); sub.setAlignment(Qt.AlignCenter); sub.setObjectName("muted"); sub.setStyleSheet("border:0;background:transparent;color:#657184;")
        self._drop_title_label=t; self._drop_hint_label=sub; self._drop_layout=lay
        for child in (t, sub):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(t); lay.addWidget(sub)

    def set_compact_mode(self, compact: bool):
        """Presentation-only density switch for small work areas.

        No import/drop behaviour changes: compact mode only reduces the drop-zone
        height/padding and hides the explanatory second line to give result/data
        views more vertical room on 1366×768-class displays.
        """
        compact=bool(compact)
        self.setMinimumHeight(58 if compact else 92)
        if hasattr(self,'_drop_layout'):
            self._drop_layout.setContentsMargins(10,6 if compact else 10,10,6 if compact else 10)
            self._drop_layout.setSpacing(2 if compact else 6)
        if hasattr(self,'_drop_hint_label'):
            self._drop_hint_label.setVisible(not compact)

    @staticmethod
    def _paths_from_event(event):
        if not event.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and (Path(u.toLocalFile()).is_dir() or Path(u.toLocalFile()).suffix.lower() in {'.qtx','.txt'})]

    def mousePressEvent(self, event):
        if event.button()==Qt.LeftButton:
            self.clicked.emit(); event.accept(); return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if self._paths_from_event(event):
            event.setDropAction(Qt.CopyAction); event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._paths_from_event(event):
            event.setDropAction(Qt.CopyAction); event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths=self._paths_from_event(event)
        if paths:
            self.filesDropped.emit(paths)
            event.setDropAction(Qt.CopyAction); event.accept(); return
        event.ignore()


class FindScopeDialog(QDialog):
    """Unified official/formal multi-select data-source picker for Find."""
    OFFICIAL_ROOT='__official__'
    FORMAL_ROOT='__formal__'

    def __init__(self, resource_paths, selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择查色范围')
        self.resize(520, 590)
        self._updating=False
        self._items={}
        self._selected_initial=None if selected is None else set(selected)
        root=QVBoxLayout(self); root.setContentsMargins(16,16,16,14); root.setSpacing(10)
        title=QLabel('查色数据源'); title.setStyleSheet('font-size:17px;font-weight:700;color:#172033;'); root.addWidget(title)
        hint=QLabel('可以选择一个或多个色库。这里只显示当前账号已经获得访问权限的数据。')
        hint.setWordWrap(True); hint.setStyleSheet('color:#738198;'); root.addWidget(hint)
        self.tree=QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setObjectName('findScopeTree'); self.tree.setAlternatingRowColors(False); root.addWidget(self.tree,1)
        actions=QHBoxLayout(); select_all=QPushButton('全选授权数据'); select_all.clicked.connect(self._select_all); clear=QPushButton('清空'); clear.clicked.connect(self._clear_all)
        actions.addWidget(select_all); actions.addWidget(clear); actions.addStretch(1); root.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.setStyleSheet('''
            QDialog{background:#F6F8FC;color:#172033;}
            QTreeWidget#findScopeTree{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;padding:6px;outline:0;}
            QTreeWidget#findScopeTree::item{height:32px;border-radius:6px;}
            QTreeWidget#findScopeTree::item:selected{background:#EAF3FF;color:#145EC7;}
            QPushButton{background:#FFFFFF;border:1px solid #DDE5EF;border-radius:8px;padding:7px 12px;}
        ''')
        self._build(resource_paths)
        self.tree.itemChanged.connect(self._item_changed)

    @staticmethod
    def _norm(value):
        return str(value or '').replace('\\','/').strip('/')

    def _make_item(self,parent,label,key):
        item=QTreeWidgetItem([label]); item.setData(0,Qt.UserRole,key); item.setFlags(item.flags()|Qt.ItemIsUserCheckable); item.setCheckState(0,Qt.Unchecked)
        if parent is None:self.tree.addTopLevelItem(item)
        else:parent.addChild(item)
        self._items[key]=item; return item

    def _build(self,resource_paths):
        self._updating=True
        try:
            official=self._make_item(None,'官方色库',self.OFFICIAL_ROOT)
            formal=self._make_item(None,'正式色库',self.FORMAL_ROOT)
            roots={'官方色库':official,'正式色库':formal}
            for raw in sorted({self._norm(x) for x in resource_paths if self._norm(x)},key=str.casefold):
                if raw in {'官方色库','正式色库'}:continue
                scope='官方色库' if raw.startswith('官方色库/') else '正式色库'
                relative=raw.split('/',1)[1] if scope=='官方色库' else raw
                parent=roots[scope]; acc='' if scope=='正式色库' else '官方色库'
                for part in [x for x in relative.split('/') if x]:
                    acc=(acc+'/'+part).strip('/')
                    key=acc if scope=='正式色库' else acc
                    item=self._items.get(key)
                    if item is None:item=self._make_item(parent,part,key)
                    parent=item
            if self._selected_initial is None:
                official.setCheckState(0,Qt.Checked); formal.setCheckState(0,Qt.Checked); self._set_descendants(official,Qt.Checked); self._set_descendants(formal,Qt.Checked)
            else:
                for key in self._selected_initial:
                    item=self._items.get(key)
                    if item is not None:
                        item.setCheckState(0,Qt.Checked); self._set_descendants(item,Qt.Checked)
                self._refresh_all_parents()
            self.tree.expandToDepth(1)
        finally:self._updating=False

    def _set_descendants(self,item,state):
        for i in range(item.childCount()):
            child=item.child(i); child.setCheckState(0,state); self._set_descendants(child,state)

    def _refresh_parent(self,item):
        parent=item.parent()
        if parent is None:return
        states=[parent.child(i).checkState(0) for i in range(parent.childCount())]
        if states and all(s==Qt.Checked for s in states):state=Qt.Checked
        elif states and all(s==Qt.Unchecked for s in states):state=Qt.Unchecked
        else:state=Qt.PartiallyChecked
        parent.setCheckState(0,state); self._refresh_parent(parent)

    def _refresh_all_parents(self):
        for i in range(self.tree.topLevelItemCount()):
            root=self.tree.topLevelItem(i)
            def visit(node):
                for j in range(node.childCount()):visit(node.child(j))
                if node.childCount():
                    states=[node.child(j).checkState(0) for j in range(node.childCount())]
                    node.setCheckState(0,Qt.Checked if all(s==Qt.Checked for s in states) else Qt.Unchecked if all(s==Qt.Unchecked for s in states) else Qt.PartiallyChecked)
            visit(root)

    def _item_changed(self,item,column):
        if self._updating:return
        self._updating=True
        try:
            if item.checkState(0) in (Qt.Checked,Qt.Unchecked):self._set_descendants(item,item.checkState(0))
            self._refresh_parent(item)
        finally:self._updating=False

    def _select_all(self):
        self._updating=True
        try:
            for i in range(self.tree.topLevelItemCount()):
                root=self.tree.topLevelItem(i); root.setCheckState(0,Qt.Checked); self._set_descendants(root,Qt.Checked)
        finally:self._updating=False

    def _clear_all(self):
        self._updating=True
        try:
            for i in range(self.tree.topLevelItemCount()):
                root=self.tree.topLevelItem(i); root.setCheckState(0,Qt.Unchecked); self._set_descendants(root,Qt.Unchecked)
        finally:self._updating=False

    def selection(self):
        # None means all authorised data. Otherwise return minimal selected subtrees.
        if self.tree.topLevelItemCount()>=2 and all(self.tree.topLevelItem(i).checkState(0)==Qt.Checked for i in range(self.tree.topLevelItemCount())):
            return None
        out=[]
        def collect(item):
            state=item.checkState(0)
            if state==Qt.Checked:
                out.append(str(item.data(0,Qt.UserRole))); return
            if state==Qt.PartiallyChecked:
                for i in range(item.childCount()):collect(item.child(i))
        for i in range(self.tree.topLevelItemCount()):collect(self.tree.topLevelItem(i))
        return out

class WorkbenchIlluminantDialog(QDialog):
    """Compact multi-select light-source / observer editor."""
    COMMON = {"D65", "D50", "A", "F02 / CWF", "F11 / TL84", "F12 / TL83", "U30", "U35"}

    def __init__(self, current, observer=10, parent=None):
        super().__init__(parent)
        self.setWindowTitle('光源 / 观察者')
        self.resize(560, 610)
        self._items = {}
        root=QVBoxLayout(self); root.setContentsMargins(16,16,16,14); root.setSpacing(10)
        title=QLabel('显示条件'); title.setStyleSheet('font-size:18px;font-weight:700;color:#172033;'); root.addWidget(title)
        hint=QLabel('勾选需要同时计算/显示的光源；至少保留一个。常用光源放在最上方，LED 与其它光源分组显示。')
        hint.setWordWrap(True); hint.setStyleSheet('color:#6B7A90;'); root.addWidget(hint)
        self.search=QLineEdit(); self.search.setPlaceholderText('搜索光源，例如 D65、TL84、LED…')
        self.search.textChanged.connect(self._filter_tree); root.addWidget(self.search)
        self.tree=QTreeWidget(); self.tree.setColumnCount(2); self.tree.setHeaderLabels(['光源','说明'])
        self.tree.header().setSectionResizeMode(0,QHeaderView.ResizeToContents); self.tree.header().setStretchLastSection(True)
        self.tree.setRootIsDecorated(True); self.tree.setAlternatingRowColors(False); root.addWidget(self.tree,1)
        groups={'常用光源':[],'传统 / F 系列':[],'LED 光源':[],'其它':[]}
        for name in SUPPORTED_ILLUMINANTS:
            if name in self.COMMON: groups['常用光源'].append(name)
            elif str(name).upper().startswith('LED'): groups['LED 光源'].append(name)
            elif str(name).upper().startswith('F') or name in {'C','D55','D60','D75','E','Horizon'}: groups['传统 / F 系列'].append(name)
            else: groups['其它'].append(name)
        current=set(display_illuminant(x) for x in (current or ['D65']))
        for gname,names in groups.items():
            if not names: continue
            root_item=QTreeWidgetItem([gname,'']); root_item.setExpanded(gname=='常用光源'); root_item.setFirstColumnSpanned(True); self.tree.addTopLevelItem(root_item)
            for name in names:
                note=illuminant_note(name) or ''
                item=QTreeWidgetItem([name,note]); item.setFlags(item.flags()|Qt.ItemIsUserCheckable); item.setCheckState(0,Qt.Checked if name in current else Qt.Unchecked)
                root_item.addChild(item); self._items[name]=item
        if self.tree.topLevelItemCount(): self.tree.expandItem(self.tree.topLevelItem(0))
        obs_box=QGroupBox('观察者'); obs_lay=QHBoxLayout(obs_box)
        self.obs10=QRadioButton('10°（纺织常用）'); self.obs2=QRadioButton('2°')
        self.obs10.setChecked(int(observer)==10); self.obs2.setChecked(int(observer)==2)
        obs_lay.addWidget(self.obs10); obs_lay.addWidget(self.obs2); obs_lay.addStretch(1); root.addWidget(obs_box)
        btns=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept_checked); btns.rejected.connect(self.reject); root.addWidget(btns)
        self.setStyleSheet('QDialog{background:#F7F9FC;color:#172033;} QLineEdit,QTreeWidget,QGroupBox{background:#FFFFFF;border:1px solid #DDE5EF;border-radius:9px;} QLineEdit{padding:7px 10px;} QTreeWidget{padding:5px;} QTreeWidget::item{height:29px;} QTreeWidget::item:selected{background:#EAF3FF;color:#145EC7;} QGroupBox{font-weight:700;margin-top:8px;padding-top:12px;}')

    def _filter_tree(self, text):
        q=str(text or '').strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            root=self.tree.topLevelItem(i); any_visible=False
            for j in range(root.childCount()):
                child=root.child(j); hay=(child.text(0)+' '+child.text(1)).casefold(); visible=(not q or q in hay)
                child.setHidden(not visible); any_visible |= visible
            root.setHidden(not any_visible); root.setExpanded(bool(q) or root.text(0)=='常用光源')

    def _accept_checked(self):
        if not self.selected_illuminants():
            QMessageBox.information(self,'光源 / 观察者','至少保留一个光源条件。'); return
        self.accept()

    def selected_illuminants(self):
        return [name for name in SUPPORTED_ILLUMINANTS if self._items.get(name) is not None and self._items[name].checkState(0)==Qt.Checked]

    def selected_observer(self):
        return 2 if self.obs2.isChecked() else 10


class WorkbenchQtxDropZone(FindQtxDropZone):
    """Compact importer accepting QTX paths or formal-library colour blocks."""
    sampleKeysDropped = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(72)
        labels=self.findChildren(QLabel)
        if labels:
            labels[0].setText('拖入 QTX / 文件夹，或从正式色库拖入色块')
        if len(labels)>1:
            labels[1].setText('拖入色块会复制到当前工作台，不修改正式色库和原文件')

    def set_compact_mode(self, compact: bool):
        super().set_compact_mode(compact)
        self.setMinimumHeight(54 if compact else 72)

    def dragEnterEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME):
            event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragEnterEvent(event)

    def dragMoveEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME):
            event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragMoveEvent(event)

    def dropEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME):
            keys=[x for x in bytes(event.mimeData().data(CARD_MIME)).decode('utf-8').splitlines() if x]
            if keys:self.sampleKeysDropped.emit(keys)
            event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dropEvent(event)



class ColorCardSchemeManagerDialog(QDialog):
    """DG4.1 colour-card ownership / sharing / publishing manager.

    Ownership, visibility, business classification and lifecycle are independent:
    an administrator's newly saved scheme is still that administrator's private
    scheme until an explicit sharing/publishing action changes its scope.
    """
    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self.owner=owner
        self.setWindowTitle('色卡方案管理')
        self.resize(1040,620)
        lay=QVBoxLayout(self); lay.setContentsMargins(16,16,16,14); lay.setSpacing(10)
        title=QLabel('色卡方案'); title.setObjectName('sectionTitle'); lay.addWidget(title)
        note=QLabel('所有者、可见范围、客户/项目和方案状态独立管理。新方案默认属于当前用户且“仅自己”可见；共享不会改变所有者。\n'
                    '双击打开 · Ctrl+A 全选 · F2 重命名 · Delete 删除；右键可设置共享、发布状态、转移所有者或复制到我的方案。')
        note.setObjectName('muted'); note.setWordWrap(True); lay.addWidget(note)
        self.search=QLineEdit(self); self.search.setPlaceholderText('搜索方案名称、所有者、可见范围、客户/项目或状态…'); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text:self.reload()); lay.addWidget(self.search)

        self.table=QTableWidget(0,7,self)
        self.table.setHorizontalHeaderLabels(['方案名称','所有者','可见范围','状态','客户 / 项目','色卡数','最后修改'])
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection); self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu); self.table.customContextMenuRequested.connect(self._menu)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        header=self.table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.Stretch)
        for col in (1,2,3,5,6): header.setSectionResizeMode(col,QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4,QHeaderView.Stretch); self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table,1)
        self.summary=QLabel(); self.summary.setObjectName('muted'); lay.addWidget(self.summary)
        self.reload()
        for seq,slot in [(QKeySequence.SelectAll,self.table.selectAll),(QKeySequence(Qt.Key_Delete),self.delete_selected),
                         (QKeySequence(Qt.Key_F2),self.rename_selected),(QKeySequence(Qt.Key_Return),self.open_selected),
                         (QKeySequence(Qt.Key_Enter),self.open_selected)]:
            act=QAction(self); act.setShortcut(seq); act.setShortcutContext(Qt.WidgetWithChildrenShortcut); act.triggered.connect(slot); self.addAction(act)

    def _users(self):
        try:return {int(u.user_id):u for u in self.owner.auth_store.list_users()}
        except Exception:return {}

    def _owner_label(self, card):
        uid=int(card.get('owner_user_id',0) or 0)
        if uid<=0:return '历史未归属'
        user=self._users().get(uid)
        if user is None:return f'用户 #{uid}'
        return str(user.display_name or user.username or f'用户 #{uid}')

    @staticmethod
    def _visibility_label(card):
        vis=str(card.get('visibility') or ('legacy_unassigned' if int(card.get('owner_user_id',0) or 0)<=0 else 'private'))
        return {'private':'仅自己','organization':'全部用户','legacy_unassigned':'历史兼容可见'}.get(vis,vis)

    @staticmethod
    def _status_label(card):
        return {'draft':'草稿','published':'已发布'}.get(str(card.get('lifecycle_status') or 'draft'),str(card.get('lifecycle_status') or '草稿'))

    @staticmethod
    def _modified_text(value):
        try:return time.strftime('%Y-%m-%d %H:%M',time.localtime(float(value or 0)))
        except Exception:return ''

    def reload(self, keep_ids=None):
        keep_ids=set(keep_ids or []); cards=self.owner.store.list_color_cards(); query=self.search.text().strip().casefold() if hasattr(self,'search') else ''
        visible=[]
        for card in cards:
            owner_label=self._owner_label(card); vis_label=self._visibility_label(card); status_label=self._status_label(card)
            hay=' '.join([str(card.get('name','')),str(card.get('customer','')),owner_label,vis_label,status_label]).casefold()
            if not query or query in hay: visible.append((card,owner_label,vis_label,status_label))
        self.table.setRowCount(len(visible)); counts={'private':0,'organization':0,'legacy':0,'published':0}
        for row,(card,owner_label,vis_label,status_label) in enumerate(visible):
            layout=card.get('layout',[]) or []; count=sum(1 for e in layout if (e.get('sample_key') if isinstance(e,dict) else e))
            vis=str(card.get('visibility') or 'private'); counts['legacy' if int(card.get('owner_user_id',0) or 0)<=0 else vis]+=1
            if str(card.get('lifecycle_status') or 'draft')=='published':counts['published']+=1
            values=[card.get('name',''),owner_label,vis_label,status_label,card.get('customer','') or '未分类',str(count),self._modified_text(card.get('updated_at'))]
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value));
                if col in (2,3,5,6):item.setTextAlignment(Qt.AlignCenter)
                if col==0:
                    item.setData(Qt.UserRole,card.get('card_id')); item.setToolTip('双击打开；右键管理所有权、共享和发布状态')
                self.table.setItem(row,col,item)
            if card.get('card_id') in keep_ids:self.table.selectRow(row)
        prefix=f'找到 {len(visible)} / {len(cards)} 个方案' if query else f'共 {len(cards)} 个方案'
        self.summary.setText(f"{prefix} · 仅自己 {counts['private']} · 共享 {counts['organization']} · 已发布 {counts['published']} · 历史未归属 {counts['legacy']}")

    def selected_ids(self):
        ids=[]; model=self.table.selectionModel()
        if model is not None:
            for idx in model.selectedRows(0):
                item=self.table.item(idx.row(),0); cid=item.data(Qt.UserRole) if item else None
                if cid and str(cid) not in ids:ids.append(str(cid))
        if not ids:
            row=self.table.currentRow(); item=self.table.item(row,0) if row>=0 else None
            if item and item.data(Qt.UserRole):ids=[str(item.data(Qt.UserRole))]
        return ids

    def _selected_card(self):
        ids=self.selected_ids()
        if len(ids)!=1:return None
        return next((c for c in self.owner.store.list_color_cards() if str(c.get('card_id'))==ids[0]),None)

    def open_selected(self):
        ids=self.selected_ids()
        for cid in ids:self.owner.open_color_card_window(cid)
        if ids:self.accept()

    def rename_selected(self):
        ids=self.selected_ids()
        if len(ids)!=1:
            if ids:QMessageBox.information(self,'重命名方案','请只选择一个方案进行重命名。')
            return
        try:self.owner.rename_color_card(ids[0]); self.reload({ids[0]})
        except PermissionError as exc:QMessageBox.information(self,'重命名方案',str(exc))

    def share_selected(self):
        card=self._selected_card()
        if not card:return
        if int(card.get('owner_user_id',0) or 0)<=0:
            QMessageBox.information(self,'共享设置','这是历史未归属方案，请先由管理员指定所有者。');return
        if str(card.get('lifecycle_status') or 'draft')=='published':
            QMessageBox.information(self,'共享设置','已发布方案必须保持组织可见。若要改为私有，请先取消发布。');return
        current='全部用户' if str(card.get('visibility') or 'private')=='organization' else '仅自己'
        choice,ok=QInputDialog.getItem(self,'共享设置','可见范围：',['仅自己','全部用户'],0 if current=='仅自己' else 1,False)
        if not ok:return
        vis='organization' if choice=='全部用户' else 'private'
        try:
            owner_name=self._owner_label(card); self.owner.store.set_color_card_visibility(card['card_id'],vis,owner_name)
            self.owner.audit('COLOR_CARD_VISIBILITY',card.get('name',''),f'{current}->{choice}')
            self.owner.refresh_color_card_list(); self.reload({card['card_id']})
        except Exception as exc:QMessageBox.warning(self,'共享设置',str(exc))

    def publish_selected(self):
        card=self._selected_card()
        if not card:return
        if not self.owner.require_admin('发布色卡方案'):return
        published=str(card.get('lifecycle_status') or 'draft')=='published'
        target='draft' if published else 'published'; verb='取消发布' if published else '发布'
        detail='取消发布后保留当前可见范围；可再通过“共享设置”改为仅自己。' if published else '发布后方案将成为组织正式业务方案，并自动对全部用户可见。'
        if QMessageBox.question(self,verb+'色卡方案',f'{verb}“{card.get("name","")}”？\n\n{detail}',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.owner.store.set_color_card_lifecycle(card['card_id'],target,self._owner_label(card)); self.owner.audit('COLOR_CARD_PUBLISH',card.get('name',''),target)
            self.owner.refresh_color_card_list(); self.reload({card['card_id']})
        except Exception as exc:QMessageBox.warning(self,verb+'色卡方案',str(exc))

    def transfer_owner_selected(self):
        card=self._selected_card()
        if not card or not self.owner.require_admin('转移色卡方案所有者'):return
        users=[u for u in self.owner.auth_store.list_users() if u.enabled]
        if not users:return
        labels=[f'{u.display_name or u.username}  ({u.username})' for u in users]
        choice,ok=QInputDialog.getItem(self,'转移所有者','新的所有者：',labels,0,False)
        if not ok:return
        user=users[labels.index(choice)]
        if QMessageBox.question(self,'转移所有者',f'将“{card.get("name","")}”的所有者改为 {user.display_name or user.username}？\n\n共享范围和方案内容保持不变。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.owner.store.transfer_color_card_owner(card['card_id'],user.user_id,user.username); self.owner.audit('COLOR_CARD_OWNER_TRANSFER',card.get('name',''),f'owner->{user.username}')
            self.owner.refresh_color_card_list(); self.reload()
        except Exception as exc:QMessageBox.warning(self,'转移所有者',str(exc))

    def duplicate_selected(self):
        card=self._selected_card()
        if not card:return
        name,ok=QInputDialog.getText(self,'复制到我的方案','新方案名称：',text=f"{card.get('name','色卡方案')} 副本")
        if not ok or not name.strip():return
        try:
            copied=self.owner.store.duplicate_color_card_to_current_user(card['card_id'],name.strip()); self.owner.audit('COLOR_CARD_COPY_TO_ME',card.get('name',''),copied.get('name',''))
            self.owner.refresh_color_card_list(); self.reload({copied.get('card_id')})
        except Exception as exc:QMessageBox.warning(self,'复制到我的方案',str(exc))

    def delete_selected(self):
        ids=self.selected_ids()
        if not ids:return
        cards={c['card_id']:c for c in self.owner.store.list_color_cards()}; names=[cards[x]['name'] for x in ids if x in cards]
        preview='、'.join(names[:4])+('…' if len(names)>4 else '')
        if QMessageBox.question(self,'删除色卡方案',f'确定删除 {len(ids)} 个方案？\n{preview}\n\n只删除编排方案，不会删除色库中的任何色样。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        failures=[]
        for cid in ids:
            try:self.owner.store.remove_color_card(cid)
            except Exception as exc:failures.append(str(exc))
        self.owner.refresh_color_card_list(); self.reload()
        if failures:QMessageBox.information(self,'删除色卡方案','部分方案未删除：\n'+'\n'.join(dict.fromkeys(failures)))

    def _menu(self,pos):
        item=self.table.itemAt(pos)
        if item is not None and not self.table.selectionModel().isRowSelected(item.row(),QModelIndex()):
            self.table.clearSelection(); self.table.selectRow(item.row()); self.table.setCurrentCell(item.row(),0)
        selected=self.selected_ids(); card=self._selected_card(); menu=QMenu(self)
        new=menu.addAction('新建方案'); open_a=menu.addAction('打开选中'); rename=menu.addAction('重命名…'); rename.setEnabled(len(selected)==1)
        menu.addSeparator(); copy_me=menu.addAction('复制到我的方案…'); copy_me.setEnabled(card is not None)
        share=menu.addAction('共享设置…'); share.setEnabled(card is not None and int(card.get('owner_user_id',0) or 0)>0)
        published=bool(card and str(card.get('lifecycle_status') or 'draft')=='published')
        publish=menu.addAction('取消发布' if published else '发布为正式色卡方案…'); publish.setEnabled(card is not None and self.owner.is_admin)
        transfer=menu.addAction('转移所有者…'); transfer.setEnabled(card is not None and self.owner.is_admin)
        menu.addSeparator(); select_all=menu.addAction('全选    Ctrl+A'); delete=menu.addAction(f'删除选中方案（{len(selected)}）'); delete.setEnabled(bool(selected))
        chosen=menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen==new:self.owner.new_color_card(); self.accept()
        elif chosen==open_a:self.open_selected()
        elif chosen==rename:self.rename_selected()
        elif chosen==copy_me:self.duplicate_selected()
        elif chosen==share:self.share_selected()
        elif chosen==publish:self.publish_selected()
        elif chosen==transfer:self.transfer_owner_selected()
        elif chosen==select_all:self.table.selectAll()
        elif chosen==delete:self.delete_selected()


CARD_PLAN_SOURCE_MIME = "application/x-chromatic-card-plan-source"
CARD_PLAN_SAMPLES_MIME = "application/x-chromatic-card-plan-samples+json"
# Cross-window source marker for personal/new data windows.  CARD_PLAN_SAMPLES_MIME
# is intentionally reused as the generic serialized-sample payload so palette,
# workspace and clipboard transfers all carry the actual measurement data, not
# only a process-local sample key.
WORKSPACE_SOURCE_MIME = "application/x-chromatic-workspace-source+json"


class _ElidedLabel(QLabel):
    """Single-line label that always elides on the right, matching palette-card UIs."""
    def __init__(self, text='', parent=None):
        super().__init__(parent); self._full_text=str(text or '')
        self.setMinimumHeight(18); self.setToolTip(self._full_text)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    def setFullText(self,text):
        self._full_text=str(text or ''); self.setToolTip(self._full_text); self.update()
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(self.palette().color(self.foregroundRole()))
        fm=p.fontMetrics(); txt=fm.elidedText(self._full_text, Qt.ElideRight, max(4,self.width()-6))
        p.drawText(self.rect().adjusted(3,0,-3,0), Qt.AlignLeft|Qt.AlignVCenter, txt)


class _PaletteSwatch(QWidget):
    """Pure colour swatch with an optional folded upper-right corner."""
    def __init__(self, color=QColor('#808080'), blank=False, parent=None):
        super().__init__(parent); self.color=QColor(color); self.blank=bool(blank); self.folded=False; self.selected=False; self.dimmed=False; self.quiet_blank=False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
    def set_state(self, *, color=None, blank=None, folded=None, selected=None, dimmed=None, quiet_blank=None):
        changed=False
        if color is not None and self.color!=QColor(color):self.color=QColor(color);changed=True
        if blank is not None and self.blank!=bool(blank):self.blank=bool(blank);changed=True
        if folded is not None and self.folded!=bool(folded):self.folded=bool(folded);changed=True
        if selected is not None and self.selected!=bool(selected):self.selected=bool(selected);changed=True
        if dimmed is not None and self.dimmed!=bool(dimmed):self.dimmed=bool(dimmed);changed=True
        if quiet_blank is not None and self.quiet_blank!=bool(quiet_blank):self.quiet_blank=bool(quiet_blank);changed=True
        if changed:self.update()
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        r=self.rect().adjusted(1,1,-1,-1)
        if self.blank:
            # CPX source palettes use ChromaShare-like quiet blanks: preserve the
            # slot coordinate without turning every empty source slot into a large
            # dashed '+' card.  Selection still becomes explicit for editing.
            if self.quiet_blank and not self.selected:
                p.fillRect(r,QColor('#FFFFFF'))
                p.setPen(QPen(QColor('#E6EBF1'),1.0,Qt.SolidLine))
                p.drawRoundedRect(QRectF(r),7,7)
                return
            # Ordinary palette blanks remain first-class editable slots.
            p.fillRect(r,QColor('#EFF6FF') if self.selected else QColor('#FBFCFE'))
            pen=QPen(QColor('#2563EB') if self.selected else QColor('#C7D2E0'),
                     2.6 if self.selected else 1.2,
                     Qt.SolidLine if self.selected else Qt.DashLine)
            p.setPen(pen); p.drawRoundedRect(QRectF(r),7,7)
            c=r.center(); p.setPen(QPen(QColor('#2563EB') if self.selected else QColor('#94A3B8'),1.8 if self.selected else 1.6))
            p.drawLine(c.x()-7,c.y(),c.x()+7,c.y()); p.drawLine(c.x(),c.y()-7,c.x(),c.y()+7)
            return
        shape=QPainterPath(); shape.addRoundedRect(QRectF(r),8,8)
        p.fillPath(shape,self.color)
        p.setPen(QPen(QColor(0,0,0,24),1)); p.drawPath(shape)
        if self.selected:
            p.setPen(QPen(QColor('#2563EB'),3)); p.drawPath(shape)
        if self.dimmed:
            p.fillPath(shape,QColor(255,255,255,170))
        if self.folded:
            d=max(10,min(17,int(min(r.width(),r.height())*.20)))
            tri=QPainterPath(); tri.moveTo(r.right()-d,r.top()); tri.lineTo(r.right(),r.top()); tri.lineTo(r.right(),r.top()+d); tri.closeSubpath()
            p.fillPath(tri,QColor('#EDEDED'))
            p.setPen(QPen(QColor(70,70,70,75),1)); p.drawLine(r.right()-d,r.top(),r.right(),r.top()+d)


class ColorCardCell(QWidget):
    """Palette slot with colour, original name and measured Lab values."""
    def __init__(self, name='', color=None, blank=False, parent=None):
        super().__init__(parent); self.blank=bool(blank); self.selected=None; self.folded=False; self.quiet_blank=False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName('paletteCell')
        lay=QVBoxLayout(self); lay.setContentsMargins(3,3,3,3); lay.setSpacing(0)
        self.swatch=_PaletteSwatch(color or QColor('#8A8A88'),blank,self); lay.addWidget(self.swatch,68)
        self.name=_ElidedLabel('' if blank else name,self); lay.addWidget(self.name,18)
        self.name.setStyleSheet('color:#1F2937;font-size:10px;background:#FFFFFF;padding:2px 7px;font-weight:600;')
        self.lab=_ElidedLabel('',self); lay.addWidget(self.lab,14)
        self.lab.setStyleSheet('color:#64748B;font-size:9px;background:#FFFFFF;padding:1px 7px;')
        if self.blank:
            self.name.hide(); self.lab.hide()
        self.set_selected(False)
    def _apply_selection_style(self):
        if self.selected:
            self.setStyleSheet('QWidget#paletteCell{background:#EFF6FF;border:3px solid #2563EB;border-radius:11px;}')
        elif self.blank and self.quiet_blank:
            self.setStyleSheet('QWidget#paletteCell{background:#FFFFFF;border:0;}')
        else:
            self.setStyleSheet('QWidget#paletteCell{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:11px;}')
    def set_selected(self,selected):
        changed=self.selected!=bool(selected); self.selected=bool(selected)
        self._apply_selection_style()
        self.swatch.set_state(selected=self.selected)
        if changed:self.update()
    def set_quiet_blank(self,quiet):
        self.quiet_blank=bool(quiet)
        self.swatch.set_state(quiet_blank=self.quiet_blank)
        self._apply_selection_style()
    def set_folded(self,folded):
        self.folded=bool(folded); self.swatch.set_state(folded=self.folded)
    def set_dimmed(self,dimmed):
        dimmed=bool(dimmed); self.swatch.set_state(dimmed=dimmed)
        self.name.setStyleSheet(('color:#A0A9B5;' if dimmed else 'color:#1F2937;')+'font-size:10px;background:#FFFFFF;padding:2px 7px;font-weight:600;')
        self.lab.setStyleSheet(('color:#B0B8C2;' if dimmed else 'color:#64748B;')+'font-size:9px;background:#FFFFFF;padding:1px 7px;')




class FlowLayout(QLayout):
    """Small dependency-free wrapping layout used by responsive toolbars.

    Unlike a scroll area, controls stay visible and simply wrap onto the next row
    when the MDI client becomes narrow.  It changes only widget geometry.
    """
    def __init__(self, parent=None, margin=0, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        self._items=[]
        self._h_spacing=int(h_spacing)
        self._v_spacing=int(v_spacing)
        self.setContentsMargins(margin,margin,margin,margin)
    def addItem(self,item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self,index): return self._items[index] if 0<=index<len(self._items) else None
    def takeAt(self,index): return self._items.pop(index) if 0<=index<len(self._items) else None
    def expandingDirections(self): return Qt.Orientation(0)
    def hasHeightForWidth(self): return True
    def heightForWidth(self,width): return self._do_layout(QRect(0,0,max(0,width),0),True)
    def setGeometry(self,rect):
        super().setGeometry(rect); self._do_layout(rect,False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self):
        size=QSize()
        for item in self._items:size=size.expandedTo(item.minimumSize())
        l,t,r,b=self.getContentsMargins(); return size+QSize(l+r,t+b)
    def _do_layout(self,rect,test_only):
        l,t,r,b=self.getContentsMargins()
        effective=rect.adjusted(l,t,-r,-b)
        x=effective.x(); y=effective.y(); line_h=0
        right=effective.right()
        for item in self._items:
            w=item.widget()
            if w is not None and not w.isVisible():
                continue
            hint=item.sizeHint(); next_x=x+hint.width()+self._h_spacing
            if line_h>0 and next_x-self._h_spacing>right:
                x=effective.x(); y+=line_h+self._v_spacing; next_x=x+hint.width()+self._h_spacing; line_h=0
            if not test_only:item.setGeometry(QRect(QPoint(x,y),hint))
            x=next_x; line_h=max(line_h,hint.height())
        return max(0,y+line_h-effective.y()+t+b)


class ResponsiveWorkbenchPage(QWidget):
    resized = Signal(int)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.resized.emit(self.width())


class StudioMdiTitleBar(QFrame):
    """Modern internal title bar without changing the embedded business widget."""
    def __init__(self, subwindow):
        super().__init__(subwindow)
        self.subwindow=subwindow
        self.setObjectName('studioMdiTitleBar')
        self.setCursor(Qt.ArrowCursor)
        self._dragging=False
        lay=QHBoxLayout(self); lay.setContentsMargins(10,0,4,0); lay.setSpacing(4)
        self.icon=QLabel(''); self.icon.setObjectName('studioMdiTitleIcon'); self.icon.setFixedWidth(6); lay.addWidget(self.icon)
        self.title=QLabel(''); self.title.setObjectName('studioMdiTitleText'); self.title.setTextInteractionFlags(Qt.NoTextInteraction); lay.addWidget(self.title,1)
        self.min_btn=QToolButton(self); self.min_btn.setObjectName('studioMdiControl'); self.min_btn.setText('—'); self.min_btn.setToolTip('最小化')
        self.max_btn=QToolButton(self); self.max_btn.setObjectName('studioMdiControl'); self.max_btn.setText('□'); self.max_btn.setToolTip('最大化 / 还原')
        self.close_btn=QToolButton(self); self.close_btn.setObjectName('studioMdiClose'); self.close_btn.setText('×'); self.close_btn.setToolTip('关闭')
        for b in (self.min_btn,self.max_btn,self.close_btn): b.setFixedSize(30,26); lay.addWidget(b)
        self.min_btn.clicked.connect(subwindow._request_taskbar_minimize)
        self.max_btn.clicked.connect(subwindow._toggle_custom_maximize)
        self.close_btn.clicked.connect(subwindow.close)
    def set_title(self,text): self.title.setText(str(text or ''))
    def set_active(self,active):
        self.setProperty('active',bool(active)); self.style().unpolish(self); self.style().polish(self); self.update()
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self._dragging=True; self.subwindow._begin_title_drag(event.globalPosition().toPoint()); event.accept(); return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self._dragging:
            self.subwindow._continue_title_drag(event.globalPosition().toPoint()); event.accept(); return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self._dragging:
            self._dragging=False; self.subwindow._end_title_drag(); event.accept(); return
        super().mouseReleaseEvent(event)
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.subwindow._toggle_custom_maximize(); event.accept(); return
        super().mouseDoubleClickEvent(event)


class PaletteMdiArea(QMdiArea):
    """MDI workspace whose blank area reliably receives right-click/double-click on Windows."""
    workspaceMenuRequested=Signal(QPoint)
    blankDoubleClicked=Signal()
    newRequested=Signal()
    closeAllRequested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self._event_viewport=self.viewport()
        self._event_viewport.installEventFilter(self)
        self.setFocusPolicy(Qt.StrongFocus)
    def eventFilter(self,obj,event):
        try:
            if QApplication.closingDown() or getattr(self.window(),'_app_closing',False):
                return False
        except RuntimeError:
            return False
        if obj is getattr(self,'_event_viewport',None):
            if event.type()==QEvent.ContextMenu:
                self.workspaceMenuRequested.emit(event.globalPos()); return True
            if event.type()==QEvent.MouseButtonDblClick and event.button()==Qt.LeftButton:
                try: sub=self.subWindowAt(event.position().toPoint())
                except Exception: sub=None
                if sub is None:self.blankDoubleClicked.emit(); return True
        try:
            return super().eventFilter(obj,event)
        except RuntimeError:
            return False
    def keyPressEvent(self,event):
        mod=event.modifiers()
        if (mod & Qt.ControlModifier) and event.key()==Qt.Key_N:self.newRequested.emit(); event.accept(); return
        if (mod & Qt.ControlModifier) and (mod & Qt.ShiftModifier) and event.key()==Qt.Key_W:self.closeAllRequested.emit(); event.accept(); return
        sub=self.activeSubWindow(); win=sub.widget() if sub is not None else None
        if isinstance(win,ColorCardPlanWindow):
            if event.matches(QKeySequence.SelectAll):win.trigger_command('select_all'); event.accept(); return
            if event.matches(QKeySequence.Copy):win.trigger_command('copy'); event.accept(); return
            if event.matches(QKeySequence.Cut):win.trigger_command('cut'); event.accept(); return
            if event.matches(QKeySequence.Paste):win.trigger_command('paste'); event.accept(); return
            if (mod & Qt.ControlModifier) and (mod & Qt.ShiftModifier) and event.key()==Qt.Key_S:win.trigger_command('save_as'); event.accept(); return
            if (mod & Qt.ControlModifier) and event.key()==Qt.Key_S:win.trigger_command('save'); event.accept(); return
            if (mod & Qt.ControlModifier) and event.key()==Qt.Key_E:win.trigger_command('export'); event.accept(); return
        super().keyPressEvent(event)


class StudioToolSubWindow(QMdiSubWindow):
    """Persistent internal tool/document window with stable custom chrome.

    HF138 removes the old geometry fight between ``QMdiSubWindow``'s internal
    layout and manual ``page.setGeometry(...)`` calls.  The MDI subwindow now owns
    one ordinary shell widget; the modern title bar and the mature tool page live
    in that shell's QVBoxLayout.  Qt therefore has exactly one geometry owner for
    the embedded page on Windows.

    ``widget()`` / ``setWidget()`` are intentionally kept source-compatible for
    the rest of the application: Python callers still see the mature page, while
    the actual QWidget installed in QMdiSubWindow is the private shell.
    """
    hiddenByUser = Signal()
    minimizedToTaskbar = Signal(object)
    _TITLE_H=34
    _BORDER=5

    def __init__(self, parent=None):
        super().__init__(parent)
        # Establish frameless subwindow flags before installing the stable shell;
        # changing flags afterwards can recreate native window state on Windows.
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)
        self._studio_first_open_fitted = False
        self._studio_first_open_scheduled = False
        self._resize_edges=set(); self._resize_press_global=None; self._resize_press_geometry=None
        self._title_drag_global=None; self._title_drag_pos=None
        self._studio_content=None
        self.setMouseTracking(True)
        self.setContentsMargins(0,0,0,0)

        # One stable shell is the only widget actually installed in QMdiSubWindow.
        # The 5 px outer layout margin is also the custom resize hit area.
        self._studio_shell=QFrame()
        self._studio_shell.setObjectName('studioMdiShell')
        self._studio_shell.setMouseTracking(True)
        self._studio_shell.installEventFilter(self)
        shell_lay=QVBoxLayout(self._studio_shell)
        shell_lay.setContentsMargins(self._BORDER,self._BORDER,self._BORDER,self._BORDER)
        shell_lay.setSpacing(0)
        self._studio_shell_layout=shell_lay

        self._studio_titlebar=StudioMdiTitleBar(self)
        self._studio_titlebar.setFixedHeight(self._TITLE_H)
        shell_lay.addWidget(self._studio_titlebar,0)

        self._studio_client=QFrame(self._studio_shell)
        self._studio_client.setObjectName('studioMdiClient')
        client_lay=QVBoxLayout(self._studio_client)
        client_lay.setContentsMargins(0,0,0,0)
        client_lay.setSpacing(0)
        self._studio_client_layout=client_lay
        shell_lay.addWidget(self._studio_client,1)

        # Bypass our compatibility setWidget() override for the private shell.
        QMdiSubWindow.setWidget(self,self._studio_shell)
        self.windowTitleChanged.connect(self._studio_titlebar.set_title)
        self._studio_titlebar.set_title(self.windowTitle())

    # ----- compatibility: callers still work with the mature page -----
    def setWidget(self, widget):
        if widget is None:return
        old=self._studio_content
        if old is widget:return
        if old is not None:
            try:self._studio_client_layout.removeWidget(old)
            except RuntimeError:pass
            try:old.setParent(None)
            except RuntimeError:pass
        self._studio_content=widget
        widget.setParent(self._studio_client)
        self._studio_client_layout.addWidget(widget,1)
        widget.show()

    def widget(self):
        return self._studio_content

    def _actual_shell_widget(self):
        try:return QMdiSubWindow.widget(self)
        except RuntimeError:return None

    def _apply_custom_chrome_geometry(self):
        """Layout-owned chrome: never position the mature page manually."""
        try:
            self._studio_titlebar.setFixedHeight(self._TITLE_H)
            self._studio_shell_layout.invalidate(); self._studio_shell_layout.activate()
            self._studio_client_layout.invalidate(); self._studio_client_layout.activate()
        except RuntimeError:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_custom_chrome_geometry(); self._sync_embedded_client_layout()
        self._update_custom_title_active()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._apply_custom_chrome_geometry(); self._sync_embedded_client_layout()

    def _sync_embedded_client_layout(self):
        """Flush layouts only; never set the page geometry by hand.

        The old HF135-HF137 implementation called ``page.setGeometry`` while
        QMdiSubWindow was simultaneously laying out the same page.  That was the
        direct cause of clipped top rows / overlapping title bars / delayed
        self-correction.  The shell layout is now authoritative.
        """
        try:
            shell=self._actual_shell_widget()
            if shell is not None:
                shell.ensurePolished(); shell.updateGeometry()
                lay=shell.layout()
                if lay is not None:lay.invalidate(); lay.activate()
            w=self._studio_content
            if w is not None:
                w.ensurePolished(); w.updateGeometry()
                lay=w.layout()
                if lay is not None:lay.invalidate(); lay.activate()
                w.update()
        except RuntimeError:
            pass

    def moveEvent(self,event):
        super().moveEvent(event)
        self._update_custom_title_active()

    def _update_custom_title_active(self):
        try:
            active=(self.mdiArea() is not None and self.mdiArea().activeSubWindow() is self)
            self._studio_titlebar.set_active(active)
        except RuntimeError:
            pass

    # Kept for source compatibility.  No timer calls this method in HF138.
    def _fit_first_open_to_mdi(self, final=False):
        if self._studio_first_open_fitted:return
        mdi=self.mdiArea()
        if mdi is None or not self.isVisible() or bool(self.property('studio_taskbar_minimized')):return
        viewport=mdi.viewport(); vw,vh=viewport.width(),viewport.height()
        if vw<360 or vh<260:return
        margin=8 if self.property('tool_index') in (0,1,2,3,4) and (vw<1450 or vh<820) else 18
        avail_w=max(360,vw-margin*2); avail_h=max(300,vh-margin*2)
        want_w=min(avail_w,max(760,int(vw*.90))); want_h=min(avail_h,max(500,int(vh*.86)))
        self.setGeometry(max(margin,(vw-want_w)//2),max(margin,(vh-want_h)//2),want_w,want_h)
        self._studio_first_open_fitted=True; self._studio_first_open_scheduled=False

    def _retry_first_open_fit(self):
        if not self._studio_first_open_fitted:self._fit_first_open_to_mdi(True)

    # ----- custom title-bar movement / maximize -----
    def _toggle_custom_maximize(self):
        if self.isMaximized(): self.showNormal()
        else: self.showMaximized()
        self._apply_custom_chrome_geometry()

    def _begin_title_drag(self,global_pos):
        if self.isMaximized():
            mdi=self.mdiArea(); old_w=max(1,self.width()); ratio=min(.9,max(.1,(global_pos.x()-self.mapToGlobal(QPoint(0,0)).x())/old_w))
            self.showNormal(); QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            new_x=global_pos.x()-int(self.width()*ratio); new_y=global_pos.y()-self._TITLE_H//2
            if mdi is not None:
                local=mdi.viewport().mapFromGlobal(QPoint(new_x,new_y)); self.move(local)
        self._title_drag_global=QPoint(global_pos); self._title_drag_pos=QPoint(self.pos())
        self.setProperty('responsive_auto_fitted',False)

    def _continue_title_drag(self,global_pos):
        if self._title_drag_global is None or self._title_drag_pos is None or self.isMaximized():return
        delta=QPoint(global_pos)-self._title_drag_global; target=self._title_drag_pos+delta
        mdi=self.mdiArea()
        if mdi is not None:
            vw,vh=mdi.viewport().width(),mdi.viewport().height()
            target.setX(min(max(-self.width()+120,target.x()),max(0,vw-120)))
            target.setY(min(max(0,target.y()),max(0,vh-self._TITLE_H)))
        self.move(target)

    def _end_title_drag(self):
        self._title_drag_global=None; self._title_drag_pos=None

    # ----- frameless resize handles -----
    def _edge_set(self,pos):
        if self.isMaximized():return set()
        x,y=pos.x(),pos.y(); w,h=self.width(),self.height(); m=self._BORDER+2; edges=set()
        if x<=m:edges.add('l')
        elif x>=w-m:edges.add('r')
        if y<=m:edges.add('t')
        elif y>=h-m:edges.add('b')
        return edges

    def _set_resize_cursor(self,edges):
        if ('l' in edges and 't' in edges) or ('r' in edges and 'b' in edges):cursor=Qt.SizeFDiagCursor
        elif ('r' in edges and 't' in edges) or ('l' in edges and 'b' in edges):cursor=Qt.SizeBDiagCursor
        elif 'l' in edges or 'r' in edges:cursor=Qt.SizeHorCursor
        elif 't' in edges or 'b' in edges:cursor=Qt.SizeVerCursor
        else:cursor=Qt.ArrowCursor
        try:self._studio_shell.setCursor(cursor)
        except RuntimeError:pass
        if cursor==Qt.ArrowCursor:self.unsetCursor()
        else:self.setCursor(cursor)

    def _resize_press(self,pos,global_pos):
        edges=self._edge_set(pos)
        if not edges:return False
        self._resize_edges=edges; self._resize_press_global=QPoint(global_pos); self._resize_press_geometry=QRect(self.geometry())
        self.setProperty('responsive_auto_fitted',False)
        return True

    def _resize_move(self,pos,global_pos):
        if not self._resize_edges or self._resize_press_global is None or self._resize_press_geometry is None:
            self._set_resize_cursor(self._edge_set(pos)); return False
        d=QPoint(global_pos)-self._resize_press_global; g=QRect(self._resize_press_geometry)
        minw=max(360,self.minimumWidth()); minh=max(260,self.minimumHeight())
        if 'l' in self._resize_edges:
            nx=min(g.left()+d.x(),g.right()-minw); g.setLeft(nx)
        if 'r' in self._resize_edges:g.setRight(max(g.left()+minw,g.right()+d.x()))
        if 't' in self._resize_edges:
            ny=min(g.top()+d.y(),g.bottom()-minh); g.setTop(ny)
        if 'b' in self._resize_edges:g.setBottom(max(g.top()+minh,g.bottom()+d.y()))
        mdi=self.mdiArea()
        if mdi is not None:
            bounds=mdi.viewport().rect(); g.setRight(min(g.right(),bounds.right())); g.setBottom(min(g.bottom(),bounds.bottom())); g.setLeft(max(g.left(),-g.width()+120)); g.setTop(max(0,g.top()))
        self.setGeometry(g); return True

    def _resize_release(self):
        if not self._resize_edges:return False
        self._resize_edges=set(); self._resize_press_global=None; self._resize_press_geometry=None; self._set_resize_cursor(set()); return True

    def eventFilter(self,obj,event):
        # Only the shell's 5 px outer margin is intercepted.  Mature page events,
        # drag/drop, selections and context menus continue to the original widgets.
        if obj is getattr(self,'_studio_shell',None):
            try:
                et=event.type(); pos=event.position().toPoint()
                if et==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
                    if self._resize_press(pos,event.globalPosition().toPoint()):event.accept(); return True
                elif et==QEvent.MouseMove:
                    if self._resize_move(pos,event.globalPosition().toPoint()):event.accept(); return True
                elif et==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton:
                    if self._resize_release():event.accept(); return True
                elif et==QEvent.Leave and not self._resize_edges:
                    self._set_resize_cursor(set())
            except (AttributeError,RuntimeError):
                pass
        return super().eventFilter(obj,event)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self._resize_press(event.position().toPoint(),event.globalPosition().toPoint()):
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._resize_move(event.position().toPoint(),event.globalPosition().toPoint()):event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self._resize_release():event.accept(); return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        if bool(self.property('delete_on_close')):
            event.accept(); return
        event.ignore(); self.hide(); self.hiddenByUser.emit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.WindowActivate,QEvent.WindowDeactivate,QEvent.WindowStateChange):
            self._update_custom_title_active(); self._apply_custom_chrome_geometry()
        # Compatibility only: custom title-bar minimisation never enters native MDI
        # minimized state, but older call sites may still call showMinimized().
        if event.type()==QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0,self._minimize_to_taskbar)

    def _request_taskbar_minimize(self):
        if bool(self.property('studio_taskbar_minimized')):return
        if self.isMaximized():self.showNormal()
        self.setProperty('studio_taskbar_minimized',True)
        self.hide(); self.minimizedToTaskbar.emit(self)

    def _minimize_to_taskbar(self):
        if bool(self.property('studio_taskbar_minimized')):return
        if self.isMinimized():self.showNormal()
        self.setProperty('studio_taskbar_minimized',True)
        self.hide(); self.minimizedToTaskbar.emit(self)

    def _hide_minimized(self): self._request_taskbar_minimize()


class StudioMdiArea(QMdiArea):
    """Light-Studio desktop canvas for independent internal tool windows."""
    workspaceMenuRequested = Signal(QPoint)
    blankDoubleClicked = Signal()
    viewportResized = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QMdiArea.SubWindowView)
        self.setOption(QMdiArea.DontMaximizeSubWindowOnActivation, True)
        self._event_viewport=self.viewport()
        self._event_viewport.installEventFilter(self)
        self.setFocusPolicy(Qt.StrongFocus)
        self._empty = QWidget(self.viewport())
        self._empty.setObjectName('studioEmpty')
        self._empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        el = QVBoxLayout(self._empty)
        el.setContentsMargins(44, 38, 44, 38)
        el.setSpacing(7)
        hero = QLabel('专业的色彩管理 · 从灵感到标准')
        hero.setObjectName('heroTitle')
        sub = QLabel('管理色彩   ·   比较色差   ·   组织方案   ·   提升品质')
        sub.setObjectName('heroSub')
        el.addWidget(hero)
        el.addWidget(sub)
        el.addStretch(1)
        hint = QFrame(); hint.setObjectName('homeDropZone'); hint.setFixedSize(560, 120)
        hl = QVBoxLayout(hint); hl.setContentsMargins(28, 22, 28, 22); hl.setSpacing(6)
        h1 = QLabel('将 QTX / CPX / Excel 拖到这里'); h1.setObjectName('dropTitle'); h1.setAlignment(Qt.AlignCenter)
        h2 = QLabel('右键空白区域新建文件窗口，或从左侧打开文件'); h2.setObjectName('dropHint'); h2.setAlignment(Qt.AlignCenter)
        hl.addWidget(h1); hl.addWidget(h2)
        el.addWidget(hint, 0, Qt.AlignHCenter)
        el.addStretch(2)
        watermark = QLabel('Chromatic\nAnalysis')
        watermark.setObjectName('watermark'); watermark.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        el.addWidget(watermark)
        self.refresh_empty_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._empty.setGeometry(self.viewport().rect())
        try:
            self.viewportResized.emit(self.viewport().width(), self.viewport().height())
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        try:
            if QApplication.closingDown() or getattr(self.window(),'_app_closing',False):
                return False
        except RuntimeError:
            return False
        if obj is getattr(self,'_event_viewport',None):
            if event.type() == QEvent.ContextMenu:
                self.workspaceMenuRequested.emit(event.globalPos())
                return True
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                try:
                    sub = self.subWindowAt(event.position().toPoint())
                except Exception:
                    sub = None
                if sub is None:
                    self.blankDoubleClicked.emit()
                    return True
        try:
            return super().eventFilter(obj, event)
        except RuntimeError:
            return False

    def refresh_empty_state(self):
        visible = any(w.isVisible() for w in self.subWindowList())
        self._empty.setVisible(not visible)
        if not visible:
            self._empty.raise_()



class PaletteSlotModel(QAbstractListModel):
    """Palette slots on the same frozen HF50 QListView/QAbstractListModel pattern.

    One logical slot == one model row.  No QListWidgetItem, no giant table geometry,
    no per-card QWidget.  QListView/IconMode performs viewport-bounded painting.
    """
    def __init__(self,parent=None):
        super().__init__(parent); self._slots=[]; self._meta=[]
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self._slots)
    def slot_count(self):return len(self._slots)
    def columns(self):
        try:return max(1,int(self.parent().plan_window.cols))
        except Exception:return 1
    def linear_from_index(self,index):
        return index.row() if index.isValid() and 0<=index.row()<len(self._slots) else -1
    def index_for_linear(self,row):
        row=int(row); return self.index(row,0) if 0<=row<len(self._slots) else QModelIndex()
    def data_by_linear(self,row,role=Qt.DisplayRole):
        row=int(row)
        if row<0 or row>=len(self._slots):return None
        if role==Qt.UserRole:return self._slots[row]
        meta=self._meta[row]; key=self._slots[row]
        if role in (Qt.UserRole+1,Qt.ToolTipRole) and key and not meta.get('name' if role==Qt.UserRole+1 else 'tooltip'):
            try:
                plan=self.parent().plan_window; samples=getattr(plan,'_paint_sample_map',None) or getattr(plan,'_sample_map_cache',None) or {}
                sm=samples.get(str(key)); fallback=(sm.display_name if sm is not None else '缺失色样')
            except Exception:fallback=''
            return fallback
        if role==Qt.UserRole+1:return meta.get('name','')
        if role==Qt.ToolTipRole:return meta.get('tooltip','')
        if role==Qt.ForegroundRole:return meta.get('foreground')
        if role==Qt.SizeHintRole:return meta.get('size_hint')
        if role==Qt.DisplayRole:return ''
        return meta.get(role)
    def set_data_by_linear(self,row,value,role=Qt.EditRole,emit=True):
        row=int(row)
        if row<0 or row>=len(self._slots):return False
        if role==Qt.UserRole:self._slots[row]=value
        elif role==Qt.UserRole+1:self._meta[row]['name']=str(value or '')
        elif role==Qt.ToolTipRole:self._meta[row]['tooltip']=str(value or '')
        elif role==Qt.ForegroundRole:self._meta[row]['foreground']=value
        elif role==Qt.SizeHintRole:self._meta[row]['size_hint']=value
        else:self._meta[row][role]=value
        if emit:
            ix=self.index_for_linear(row)
            if ix.isValid():self.dataChanged.emit(ix,ix,[role])
        return True
    def data(self,index,role=Qt.DisplayRole):return self.data_by_linear(self.linear_from_index(index),role)
    def setData(self,index,value,role=Qt.EditRole):return self.set_data_by_linear(self.linear_from_index(index),value,role)
    def flags(self,index):
        return (Qt.ItemIsEnabled|Qt.ItemIsSelectable|Qt.ItemIsDragEnabled|Qt.ItemIsDropEnabled) if self.linear_from_index(index)>=0 else (Qt.ItemIsEnabled|Qt.ItemIsDropEnabled)
    def _normalise_meta(self,meta=None):
        m=dict(meta or {});m.setdefault('name','');m.setdefault('tooltip','');return m
    def reset_slots(self,slots,metadata=None):
        slots=list(slots or []);metadata=list(metadata or [])
        self.beginResetModel();self._slots=slots;self._meta=[self._normalise_meta(metadata[i] if i<len(metadata) else None) for i in range(len(slots))];self.endResetModel()
    def set_columns(self,columns):
        # QListView owns visual wrapping; logical column count remains in plan_window.cols.
        return None
    def append_slot(self,key=None,meta=None):
        row=len(self._slots);self.beginInsertRows(QModelIndex(),row,row);self._slots.append(key);self._meta.append(self._normalise_meta(meta));self.endInsertRows()
    def insert_slot(self,row,key=None,meta=None):
        row=max(0,min(int(row),len(self._slots)));self.beginInsertRows(QModelIndex(),row,row);self._slots.insert(row,key);self._meta.insert(row,self._normalise_meta(meta));self.endInsertRows()
    def pop_slot(self,row):
        row=int(row)
        if row<0 or row>=len(self._slots):return None,None
        self.beginRemoveRows(QModelIndex(),row,row);key=self._slots.pop(row);meta=self._meta.pop(row);self.endRemoveRows();return key,meta
    def ensure_count(self,count):
        count=max(0,int(count));current=len(self._slots)
        if count==current:return
        if count>current:
            self.beginInsertRows(QModelIndex(),current,count-1);add=count-current;self._slots.extend([None]*add);self._meta.extend(self._normalise_meta() for _ in range(add));self.endInsertRows()
        else:
            self.beginRemoveRows(QModelIndex(),count,current-1);del self._slots[count:];del self._meta[count:];self.endRemoveRows()


class PaletteItemProxy:
    """Small compatibility object replacing QListWidgetItem for palette code."""
    def __init__(self,grid=None,row=-1,roles=None):
        self._grid=grid;self._row=int(row);self._roles=dict(roles or {});self._hidden=False
    @property
    def attached(self):return self._grid is not None and self._row>=0
    def __eq__(self,other):return isinstance(other,PaletteItemProxy) and self._grid is other._grid and self._row==other._row and (self.attached or self._roles==other._roles)
    def __hash__(self):return hash((id(self._grid),self._row)) if self.attached else id(self)
    def data(self,role):
        if self.attached:return self._grid._model.data_by_linear(self._row,role)
        return self._roles.get(role)
    def setData(self,role,value):
        if self.attached:return self._grid._model.set_data_by_linear(self._row,value,role)
        self._roles[role]=value;return True
    def setToolTip(self,text):return self.setData(Qt.ToolTipRole,str(text or ''))
    def toolTip(self):return str(self.data(Qt.ToolTipRole) or '')
    def setForeground(self,value):return self.setData(Qt.ForegroundRole,value)
    def setSizeHint(self,size):return self.setData(Qt.SizeHintRole,size)
    def sizeHint(self):return self.data(Qt.SizeHintRole) or QSize()
    def setHidden(self,hidden):self._hidden=bool(hidden)
    def isHidden(self):return self._hidden
    def flags(self):return Qt.ItemIsEnabled|Qt.ItemIsSelectable|Qt.ItemIsDragEnabled|Qt.ItemIsDropEnabled
    def setFlags(self,flags):return None
    def setSelected(self,selected):
        if not self.attached:return
        ix=self._grid.index_for_linear(self._row)
        if not ix.isValid():return
        self._grid.selectionModel().select(ix,QItemSelectionModel.Select if selected else QItemSelectionModel.Deselect)
    def isSelected(self):
        if not self.attached:return False
        ix=self._grid.index_for_linear(self._row);return bool(ix.isValid() and self._grid.selectionModel().isSelected(ix))

class PaletteCardDelegate(QStyledItemDelegate):
    """Paint palette cards directly; no QWidget exists per sample."""
    def __init__(self,plan_window,parent=None):super().__init__(parent);self.plan_window=plan_window
    def sizeHint(self,option,index):
        grid=self.parent();return getattr(grid,'_grid_size',QSize(136,120))
    def paint(self,painter,option,index):
        grid=self.parent(); model=grid._model; linear=model.linear_from_index(index)
        if linear<0:return
        key=model.data_by_linear(linear,Qt.UserRole)
        selected=bool(option.state & QStyle.State_Selected)
        spacing=max(2,int(getattr(grid,'_spacing',10)//2))
        card=QRectF(option.rect.adjusted(spacing,spacing,-spacing,-spacing))
        if card.width()<12 or card.height()<12:return
        p=painter;p.save();p.setRenderHint(QPainter.Antialiasing,True);p.setRenderHint(QPainter.TextAntialiasing,True)
        query=str(getattr(self.plan_window,'_palette_search_query','') or '')
        matches=getattr(self.plan_window,'_palette_search_match_rows',None)
        dimmed=bool(query and isinstance(matches,set) and linear not in matches)
        if not key:
            quiet=self.plan_window._is_cpx_palette()
            if quiet and not selected:
                p.setPen(QPen(QColor('#E6EBF1'),1));p.setBrush(QColor('#FFFFFF'));p.drawRoundedRect(card,8,8);p.restore();return
            p.setPen(QPen(QColor('#2563EB') if selected else QColor('#C7D2E0'),2.6 if selected else 1.2,Qt.SolidLine if selected else Qt.DashLine))
            p.setBrush(QColor('#EFF6FF') if selected else QColor('#FBFCFE'));p.drawRoundedRect(card,8,8)
            c=card.center();p.setPen(QPen(QColor('#2563EB') if selected else QColor('#94A3B8'),1.8))
            p.drawLine(QPointF(c.x()-7,c.y()),QPointF(c.x()+7,c.y()));p.drawLine(QPointF(c.x(),c.y()-7),QPointF(c.x(),c.y()+7));p.restore();return
        rec=self.plan_window._palette_visual_record(str(key))
        name,lab_text,color,folded,missing=rec
        border=QColor('#2563EB') if selected else QColor('#E5E7EB')
        p.setPen(QPen(border,3 if selected else 1));p.setBrush(QColor('#EFF6FF') if selected else QColor('#FFFFFF'));p.drawRoundedRect(card,10,10)
        inner=card.adjusted(3,3,-3,-3); text_h=max(32,min(42,int(inner.height()*0.32))); sw=QRectF(inner.left(),inner.top(),inner.width(),max(24,inner.height()-text_h))
        p.setPen(QPen(QColor(0,0,0,22),1));p.setBrush(QColor(color));p.drawRoundedRect(sw,7,7)
        if dimmed:p.fillRect(sw,QColor(255,255,255,170))
        if folded:
            d=max(10,min(17,int(min(sw.width(),sw.height())*.20))); tri=QPainterPath();tri.moveTo(sw.right()-d,sw.top());tri.lineTo(sw.right(),sw.top());tri.lineTo(sw.right(),sw.top()+d);tri.closeSubpath();p.fillPath(tri,QColor('#EDEDED'))
        name_rect=QRectF(inner.left()+6,sw.bottom()+3,inner.width()-12,max(15,text_h*.52));lab_rect=QRectF(inner.left()+6,name_rect.bottom()-1,inner.width()-12,max(13,text_h*.42))
        font=p.font();font.setPointSizeF(max(7.5,font.pointSizeF()-0.5));font.setBold(True);p.setFont(font);p.setPen(QColor('#B42332') if missing else (QColor('#A0A9B5') if dimmed else QColor('#1F2937')))
        fm=QFontMetrics(font);p.drawText(name_rect,Qt.AlignLeft|Qt.AlignVCenter,fm.elidedText(str(name),Qt.ElideRight,max(8,int(name_rect.width()))))
        font.setBold(False);font.setPointSizeF(max(7.0,font.pointSizeF()-0.7));p.setFont(font);p.setPen(QColor('#B0B8C2') if dimmed else QColor('#64748B'));fm=QFontMetrics(font);p.drawText(lab_rect,Qt.AlignLeft|Qt.AlignVCenter,fm.elidedText(str(lab_text),Qt.ElideRight,max(8,int(lab_rect.width()))))
        p.restore()

class ColorCardPlanGrid(QListView):
    """Palette grid using the frozen HF50 virtual workspace surface.

    This intentionally follows WorkspaceCardList/WorkspaceVirtualModel instead of
    inventing another renderer: IconMode + uniform item sizes + batched layout +
    the view's own scrollbar.  Only visible indexes are painted.
    """
    def __init__(self,plan_window):
        super().__init__(plan_window);self.plan_window=plan_window;self._spacing=10;self._grid_size=QSize(136,120);self._selection_anchor_row=-1
        self._model=PaletteSlotModel(self);self.setModel(self._model);self.setItemDelegate(PaletteCardDelegate(plan_window,self))
        # Exact HF50/HF51 large-workspace pattern.
        self.setViewMode(QListView.IconMode);self.setFlow(QListView.LeftToRight);self.setWrapping(True);self.setResizeMode(QListView.Adjust);self.setMovement(QListView.Static)
        self.setUniformItemSizes(True);self.setLayoutMode(QListView.Batched);self.setBatchSize(180)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection);self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel);self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAcceptDrops(True);self.setDragEnabled(True);self.setDropIndicatorShown(True);self.setDragDropMode(QAbstractItemView.DragDrop);self.setDefaultDropAction(Qt.MoveAction);self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(self.plan_window.show_context_menu)
        self.selectionModel().selectionChanged.connect(lambda *_:self.plan_window.refresh_cell_states())
        self.setStyleSheet('QListView{background:#F8FAFC;border:0;padding:14px;} QListView::item{background:transparent;border:0;padding:0;} QListView::item:selected{background:transparent;border:0;}')
        self.setGridSize(self._grid_size)
    def setSpacing(self,value):self._spacing=max(0,int(value));super().setSpacing(0);self.viewport().update()
    def spacing(self):return self._spacing
    def setGridSize(self,size):
        self._grid_size=QSize(size);super().setGridSize(QSize(max(24,self._grid_size.width()+self._spacing),max(24,self._grid_size.height()+self._spacing)));self.viewport().update()
    def setWrapping(self,value):super().setWrapping(bool(value))
    def doItemsLayout(self):super().doItemsLayout()
    def count(self):return self._model.slot_count()
    def index_for_linear(self,row):return self._model.index_for_linear(row)
    def linear_from_index(self,index):return self._model.linear_from_index(index)
    def item(self,row):
        row=int(row);return PaletteItemProxy(self,row) if 0<=row<self.count() else None
    def itemAt(self,point):
        ix=self.indexAt(point);row=self.linear_from_index(ix);return self.item(row) if row>=0 else None
    def row(self,item):return int(getattr(item,'_row',-1)) if item is not None else -1
    def _meta_from_proxy(self,item):
        return {k:v for k,v in item._roles.items() if k!=Qt.UserRole} if isinstance(item,PaletteItemProxy) else {}
    def addItem(self,item):
        key=item.data(Qt.UserRole) if isinstance(item,PaletteItemProxy) else None;self._model.append_slot(key,self._meta_from_proxy(item))
    def insertItem(self,row,item):
        key=item.data(Qt.UserRole) if isinstance(item,PaletteItemProxy) else None;self._model.insert_slot(row,key,self._meta_from_proxy(item))
    def takeItem(self,row):
        key,meta=self._model.pop_slot(row);roles={Qt.UserRole:key,Qt.UserRole+1:(meta or {}).get('name',''),Qt.ToolTipRole:(meta or {}).get('tooltip',''),Qt.ForegroundRole:(meta or {}).get('foreground')};return PaletteItemProxy(None,-1,roles)
    def clear(self):self._model.reset_slots([])
    def set_slots(self,slots,metadata=None):self._model.reset_slots(slots,metadata)
    def ensure_count(self,count):self._model.ensure_count(count)
    def selectedItems(self):
        rows=sorted({self.linear_from_index(ix) for ix in self.selectionModel().selectedIndexes() if self.linear_from_index(ix)>=0});return [self.item(r) for r in rows]
    def currentRow(self):return self.linear_from_index(self.currentIndex())
    def setCurrentRow(self,row):
        ix=self.index_for_linear(row)
        if ix.isValid():self.selectionModel().setCurrentIndex(ix,QItemSelectionModel.ClearAndSelect);self.scrollTo(ix,QAbstractItemView.EnsureVisible)
    def setCurrentItem(self,item):self.setCurrentRow(self.row(item))
    def scrollToItem(self,item,hint=QAbstractItemView.EnsureVisible):
        ix=self.index_for_linear(self.row(item))
        if ix.isValid():self.scrollTo(ix,hint)
    def itemWidget(self,*_):return None
    def setItemWidget(self,*_):return None
    def removeItemWidget(self,*_):return None
    def setFixedSize(self,*args):
        # Frozen virtual view must never become a document-height canvas.
        if len(args)==1 and isinstance(args[0],QSize):self.setMinimumWidth(args[0].width());self.setMaximumWidth(args[0].width())
        elif len(args)>=2:self.setMinimumWidth(int(args[0]));self.setMaximumWidth(int(args[0]))
    def startDrag(self,supportedActions):
        items=[it for it in self.selectedItems() if it.data(Qt.UserRole)]
        if not items:
            item=self.item(self.currentRow());items=[item] if item and item.data(Qt.UserRole) else []
        if not items:return
        available=self.plan_window._all_samples();items=[it for it in items if str(it.data(Qt.UserRole)) in available]
        if not items:self.plan_window.main.statusBar().showMessage('所选色卡缺少测量数据，请先恢复后再拖动',4500);return
        rows=[self.row(it) for it in items];keys=[str(it.data(Qt.UserRole)) for it in items]
        mime=QMimeData();mime.setData(CARD_MIME,'\n'.join(keys).encode('utf-8'))
        payload=[{'key':key,'sample':self.plan_window.main._serialize_sample(available[key])} for key in keys]
        mime.setData(CARD_PLAN_SAMPLES_MIME,json.dumps(payload,ensure_ascii=False).encode('utf-8'));mime.setData(CARD_PLAN_SOURCE_MIME,(self.plan_window.card['card_id']+'|'+','.join(map(str,rows))).encode('utf-8'))
        drag=QDrag(self);drag.setMimeData(mime);drag.exec(Qt.CopyAction|Qt.MoveAction,Qt.MoveAction)
    def dragEnterEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME) or event.mimeData().hasUrls():event.acceptProposedAction()
        else:super().dragEnterEvent(event)
    def dragMoveEvent(self,event):
        if event.mimeData().hasFormat(CARD_MIME) or event.mimeData().hasUrls():event.acceptProposedAction()
        else:super().dragMoveEvent(event)
    def dropEvent(self,event):
        if event.mimeData().hasUrls():
            paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:self.plan_window.import_external_paths(paths);event.setDropAction(Qt.CopyAction);event.accept();return
        if not event.mimeData().hasFormat(CARD_MIME):return super().dropEvent(event)
        keys=[x for x in bytes(event.mimeData().data(CARD_MIME)).decode('utf-8').splitlines() if x]
        target=self.linear_from_index(self.indexAt(event.position().toPoint()));target=max(0,target if target>=0 else self.count()-1)
        source_id=None;source_rows=[]
        if event.mimeData().hasFormat(CARD_PLAN_SOURCE_MIME):
            try:raw=bytes(event.mimeData().data(CARD_PLAN_SOURCE_MIME)).decode('utf-8');source_id,rr=raw.rsplit('|',1);source_rows=[int(x) for x in rr.split(',') if x!='']
            except Exception:source_id=None;source_rows=[]
        if event.mimeData().hasFormat(CARD_PLAN_SAMPLES_MIME):self.plan_window.embed_transfer_samples(event.mimeData(),keys)
        copy_mode=bool(event.modifiers() & Qt.ControlModifier);self.plan_window.receive_drop_many(keys,target,source_id,source_rows,copy_mode);event.setDropAction(Qt.CopyAction if copy_mode else Qt.MoveAction);event.accept()
    def mousePressEvent(self,event):
        item=self.itemAt(event.position().toPoint());row=self.row(item) if item is not None else -1;mods=event.modifiers();anchor_before=self._selection_anchor_row;current_before=self.currentRow();super().mousePressEvent(event)
        if event.button()!=Qt.LeftButton or item is None:
            if item is None and not (mods & (Qt.ControlModifier|Qt.ShiftModifier)):self._selection_anchor_row=-1
            QTimer.singleShot(0,self.plan_window.refresh_cell_states);return
        model=self.selectionModel();idx=self.index_for_linear(row)
        if model is None or not idx.isValid():return
        if mods & Qt.ShiftModifier:
            anchor=anchor_before if anchor_before>=0 else (current_before if current_before>=0 else row);lo,hi=sorted((max(0,anchor),row))
            if not (mods & Qt.ControlModifier):model.clearSelection()
            selection=QItemSelection()
            for r in range(lo,hi+1):
                ix=self.index_for_linear(r)
                if ix.isValid():selection.select(ix,ix)
            model.select(selection,QItemSelectionModel.Select);model.setCurrentIndex(idx,QItemSelectionModel.NoUpdate)
        elif mods & Qt.ControlModifier:model.setCurrentIndex(idx,QItemSelectionModel.NoUpdate);self._selection_anchor_row=row
        else:model.clearSelection();model.select(idx,QItemSelectionModel.Select);model.setCurrentIndex(idx,QItemSelectionModel.NoUpdate);self._selection_anchor_row=row
        self.setFocus(Qt.MouseFocusReason);QTimer.singleShot(0,self.plan_window.refresh_cell_states)
    def keyPressEvent(self,event):
        mod=event.modifiers();key=event.key()
        if key==Qt.Key_A and (mod & Qt.ControlModifier):self.plan_window.select_all_cards();event.accept();return
        if key in (Qt.Key_Delete,Qt.Key_Backspace) and not (mod & (Qt.ControlModifier|Qt.AltModifier|Qt.MetaModifier)):self.plan_window.remove_selected();event.accept();return
        if key==Qt.Key_Z and (mod & Qt.ControlModifier):self.plan_window.undo();event.accept();return
        if key==Qt.Key_Y and (mod & Qt.ControlModifier):self.plan_window.redo();event.accept();return
        if key==Qt.Key_Escape:self.clearSelection();self._selection_anchor_row=-1;self.plan_window.refresh_cell_states();event.accept();return
        if key in (Qt.Key_Left,Qt.Key_Right,Qt.Key_Up,Qt.Key_Down) and (mod & Qt.AltModifier):
            delta={Qt.Key_Left:-1,Qt.Key_Right:1,Qt.Key_Up:-max(1,self.plan_window.cols),Qt.Key_Down:max(1,self.plan_window.cols)}[key];self.plan_window.move_selected(delta);event.accept();return
        super().keyPressEvent(event)
        if key in (Qt.Key_Left,Qt.Key_Right,Qt.Key_Up,Qt.Key_Down):
            if not (mod & Qt.ShiftModifier):self._selection_anchor_row=self.currentRow()
            QTimer.singleShot(0,self.plan_window.refresh_cell_states)
    def mouseDoubleClickEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item is None or not item.data(Qt.UserRole):self.plan_window.add_from_library();event.accept();return
        sm=self.plan_window._all_samples().get(str(item.data(Qt.UserRole)))
        if sm is not None:self.plan_window.main.show_sample_details_dialog(sm);event.accept();return
        super().mouseDoubleClickEvent(event)


class ColorCardPlanWindow(QWidget):
    """Visual palette editor. Layout stays separate from the customer/library database."""
    def __init__(self, main, card):
        super().__init__(); self.main=main; self.card=dict(card); self.sort_key='manual'; self.sort_desc=False; self._undo_stack=[]; self._redo_stack=[]; self._clipboard_keys=[]
        # HF67: heavy palette sorts run outside the Qt GUI thread.  The token
        # prevents an older queued result from overwriting a newer user choice.
        self._sort_future=None; self._sort_generation=0; self._sort_job=None
        settings=self.card.get('settings',{}) or {}; self.auto_grid=bool(settings.get('auto_grid',True))
        self.fluorescent_force_on=set(settings.get('fluorescent_force_on',[]) or [])
        self.fluorescent_force_off=set(settings.get('fluorescent_force_off',[]) or [])
        self.cols=max(1,int(settings.get('grid_cols',card.get('columns_count',10) or 10))); self.rows=max(1,int(settings.get('grid_rows',3) or 3))
        raw_w=int(settings.get('cell_w',136) or 136); raw_h=int(settings.get('cell_h',120) or 120)
        if str(settings.get('visual_style','')).startswith('palette-studio-v3'):
            raw_w=min(raw_w,136); raw_h=min(raw_h,120)
        self.cell_w=max(96,min(280,raw_w)); self.cell_h=max(96,min(280,raw_h))
        # Palette Studio P1: viewing zoom is UI-only and never changes saved slot coordinates.
        self.view_scale=1.0
        root=QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # HF84 / Palette Workspace UX P1: the primary palette workflow is now
        # visible instead of hidden behind nested context menus.  These controls
        # call the same existing editor methods; no colour data or file semantics
        # are changed by the toolbar itself.
        command_bar=QFrame(self); command_bar.setObjectName('paletteCommandBar')
        command_bar.setStyleSheet(
            'QFrame#paletteCommandBar{background:#FFFFFF;border:0;border-bottom:1px solid #E5E7EB;}'
            'QPushButton,QToolButton{min-height:28px;padding:3px 10px;border:1px solid #D7DEE8;border-radius:6px;background:#FFFFFF;color:#26364A;}'
            'QPushButton:hover,QToolButton:hover{background:#F4F7FB;border-color:#B7C5D8;}'
            'QPushButton#palettePrimary{background:#1557D2;color:#FFFFFF;border-color:#1557D2;font-weight:600;}'
            'QPushButton#palettePrimary:hover{background:#1049B5;}'
            'QPushButton:disabled,QToolButton:disabled{color:#A8B1BE;background:#F7F8FA;border-color:#E7EAF0;}'
        )
        command_lay=QHBoxLayout(command_bar); command_lay.setContentsMargins(12,8,12,8); command_lay.setSpacing(6)
        self.import_palette_btn=QPushButton('添加色样',command_bar); self.import_palette_btn.setToolTip('QTX / Excel：添加到当前色卡；CPX：按原始版式打开为独立色卡子窗口，不会打乱当前方案。')
        self.import_palette_btn.clicked.connect(self.import_files_dialog); command_lay.addWidget(self.import_palette_btn)
        # HF99 / LABC Atlas V1: additive read-only atlas browser.  It reads
        # current palette samples but never mutates slots, files, library data or
        # permissions; all existing Palette Studio operations remain untouched.
        self.labc_atlas_btn=QPushButton('色彩空间图谱',command_bar)
        self.labc_atlas_btn.setToolTip('按 Lab/LCh 色彩空间浏览当前色卡：色相、明度、彩度横截面与对向色相剖面；只读，不改变原卡位')
        self.labc_atlas_btn.clicked.connect(self.open_labc_atlas); command_lay.addWidget(self.labc_atlas_btn)
        # HF98 reset: palette arrangement returns to a clean scalar CIELAB
        # baseline.  No Munsell / spectral / appearance / family sorting command
        # is exposed from the colour-card workspace.  New atlas-style work will
        # be designed from this baseline instead of stacking more legacy modes.

        # HF85 / UX P2: layout is a first-class concept, separate from colour
        # ordering. Users can keep editable blank slots, choose a fixed column
        # count, or create an exact page/grid for print/export.
        self.palette_layout_btn=QToolButton(command_bar); self.palette_layout_btn.setPopupMode(QToolButton.InstantPopup)
        layout_menu=QMenu(self.palette_layout_btn)
        auto_menu=layout_menu.addMenu('固定列数 · 自动扩展行')
        for n in (4,5,6,8):
            act=auto_menu.addAction(f'{n} 列'); act.triggered.connect(lambda _=False,c=n:self.set_auto_grid_columns(c))
        layout_menu.addSeparator()
        custom_grid=layout_menu.addAction('自定义固定网格…'); custom_grid.triggered.connect(self.configure_grid)
        self.palette_layout_btn.setMenu(layout_menu); command_lay.addWidget(self.palette_layout_btn)

        self.palette_view_btn=QToolButton(command_bar); self.palette_view_btn.setPopupMode(QToolButton.InstantPopup)
        view_menu=QMenu(self.palette_view_btn)
        for label,scale in [('75%',0.75),('100%',1.0),('125%',1.25)]:
            act=view_menu.addAction(label); act.triggered.connect(lambda _=False,s=scale:self.set_view_scale(s))
        view_menu.addSeparator(); fit_act=view_menu.addAction('适应窗口宽度'); fit_act.triggered.connect(self.fit_view_to_width)
        self.palette_view_btn.setMenu(view_menu); command_lay.addWidget(self.palette_view_btn)


        self.palette_sort_btn=QToolButton(command_bar); self.palette_sort_btn.setText('排序 ▾'); self.palette_sort_btn.setPopupMode(QToolButton.InstantPopup)
        self.palette_sort_btn.setToolTip('综合色坐标排序用于 L*/a*/b*/C*/h°；基准色差用于参考色距离；连续色差用于综合色带连续性；综合色图谱用于空间浏览。')
        sort_menu=QMenu(self.palette_sort_btn)
        labch_menu=sort_menu.addMenu('综合色坐标排序')
        for title,key in [('L* 明度','L'),('a* 红绿轴','a'),('b* 黄蓝轴','b'),('C* 彩度','C'),('h° 色相（环形排序）','h')]:
            act=labch_menu.addAction(title); act.triggered.connect(lambda _=False,k=key:self.sort_items(k))
        sort_menu.addSeparator()
        diff_act=sort_menu.addAction('基准色差')
        diff_act.setToolTip('以当前唯一选中的色样为基准，按当前色差公式由近到远排序。')
        diff_act.triggered.connect(self.sort_by_color_difference)
        running_act=sort_menu.addAction('连续色差')
        running_act.setToolTip('自动寻找全局较优的连续色差路径：多起点候选 + 瓶颈优先 2-opt；不再要求选定起点。')
        running_act.triggered.connect(self.sort_by_running_delta)
        sort_menu.addSeparator()
        reverse_act=sort_menu.addAction('反向当前顺序')
        reverse_act.triggered.connect(self.reverse_current_order)
        self.palette_sort_btn.setMenu(sort_menu); command_lay.addWidget(self.palette_sort_btn)

        self.restore_palette_btn=QPushButton('恢复导入顺序',command_bar); self.restore_palette_btn.setToolTip('恢复本次打开或导入时的原始槽位顺序')
        self.restore_palette_btn.clicked.connect(self.restore_original_order); command_lay.addWidget(self.restore_palette_btn)
        self.undo_palette_btn=QPushButton('撤销',command_bar); self.undo_palette_btn.clicked.connect(self.undo); command_lay.addWidget(self.undo_palette_btn)
        self.redo_palette_btn=QPushButton('重做',command_bar); self.redo_palette_btn.clicked.connect(self.redo); command_lay.addWidget(self.redo_palette_btn)
        self.palette_more_btn=QToolButton(command_bar); self.palette_more_btn.setText('更多 ▾'); self.palette_more_btn.setPopupMode(QToolButton.InstantPopup)
        more_menu=QMenu(self.palette_more_btn)
        self.palette_more_atlas_action=more_menu.addAction('色彩空间图谱'); self.palette_more_atlas_action.triggered.connect(self.open_labc_atlas)
        self.palette_more_restore_action=more_menu.addAction('恢复导入顺序'); self.palette_more_restore_action.triggered.connect(self.restore_original_order)
        more_menu.addSeparator()
        self.palette_more_undo_action=more_menu.addAction('撤销'); self.palette_more_undo_action.setShortcut(QKeySequence.Undo); self.palette_more_undo_action.triggered.connect(self.undo)
        self.palette_more_redo_action=more_menu.addAction('重做'); self.palette_more_redo_action.setShortcut(QKeySequence.Redo); self.palette_more_redo_action.triggered.connect(self.redo)
        self.palette_more_btn.setMenu(more_menu); command_lay.addWidget(self.palette_more_btn); self.palette_more_btn.hide()
        command_lay.addStretch(1)
        self.save_palette_btn=QPushButton('保存',command_bar); self.save_palette_btn.setObjectName('primaryButton'); self.save_palette_btn.setToolTip('保存当前二维布局（Ctrl+S）'); self.save_palette_btn.clicked.connect(self.save); command_lay.addWidget(self.save_palette_btn)
        self.export_palette_btn=QToolButton(command_bar); self.export_palette_btn.setText('导出 ▾'); self.export_palette_btn.setPopupMode(QToolButton.InstantPopup)
        export_menu=QMenu(self.export_palette_btn)
        visual_export=export_menu.addMenu('展示与打印')
        for title,fmt in [('PDF 色卡…','pdf'),('PNG 图片…','png'),('JPG 图片…','jpg')]:
            act=visual_export.addAction(title); act.triggered.connect(lambda _=False,f=fmt:self.export_format(f))
        export_menu.addSeparator()
        for title,fmt in [('Excel 色卡工作簿…','excel'),('QTX…','qtx'),('CPX…','cpx')]:
            act=export_menu.addAction(title); act.triggered.connect(lambda _=False,f=fmt:self.export_format(f))
        self.export_palette_btn.setMenu(export_menu); command_lay.addWidget(self.export_palette_btn)
        root.addWidget(command_bar)

        search_row=QHBoxLayout();search_row.setContentsMargins(12,5,12,5)
        self.search_box=QLineEdit(self);self.search_box.setPlaceholderText('搜索当前方案的色号、名称或 LAB…');self.search_box.setClearButtonEnabled(True)
        self._sample_map_cache=None; self._sample_map_cache_key=None; self._sample_map_epoch=0
        self._plan_search_timer=QTimer(self); self._plan_search_timer.setSingleShot(True); self._plan_search_timer.setInterval(200)
        self._plan_search_timer.timeout.connect(self._apply_plan_search)
        self.search_box.textChanged.connect(self.filter_cards);search_row.addWidget(self.search_box,1)
        self.search_summary=QLabel(self);self.search_summary.setObjectName('muted');search_row.addWidget(self.search_summary)
        root.addLayout(search_row)
        # HF124: true Model/View palette grid.  The QTableView owns its own
        # scrollbars and paints only visible cells through a delegate; there is no
        # giant fixed QListWidget canvas and no one-QWidget-per-card materialisation.
        self.grid=ColorCardPlanGrid(self)
        self.canvas_scroll=self.grid  # compatibility alias used by fit-to-width helpers
        root.addWidget(self.grid,1)
        self._palette_virtual_threshold=0; self._materialized_palette_rows=set()
        self._palette_search_query=''; self._palette_search_match_rows=set(); self._palette_paint_cache={}; self._paint_sample_map={}
        self.count_label=QLabel(); self.count_label.setObjectName('muted'); self.count_label.setStyleSheet('background:#F8FAFC;color:#6B7280;padding:7px 12px;border:0;border-top:1px solid #E5E7EB;font-size:11px;'); root.addWidget(self.count_label)
        self.sort_buttons={};self._source_recovery_cache={};self._original_slots=[]
        self.load_layout(); self._capture_original_order(); QTimer.singleShot(0,self.update_grid_size)
        # One command per operation: the grid, MDI keyboard route and menus all
        # trigger the same QAction. Keep scope on this editor, never on the app.
        self.commands={}
        for name,seq,fn in (
            ('select_all','Ctrl+A',self.select_all_cards),('copy','Ctrl+C',self.copy_selected),
            ('cut','Ctrl+X',self.cut_selected),('paste','Ctrl+V',self.paste_selected),
            ('undo','Ctrl+Z',self.undo),('redo','Ctrl+Y',self.redo),
            ('remove','Delete',self.remove_selected),('save','Ctrl+S',self.save),
            ('save_as','Ctrl+Shift+S',self.save_as),('export','Ctrl+E',self.choose_export),
            ('view3d','Ctrl+Alt+3',self.open_3d),
        ):
            act=QAction(name,self.grid); act.setShortcut(QKeySequence(seq))
            act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            act.triggered.connect(fn); self.grid.addAction(act); self.commands[name]=act
        search_action=QAction(self);search_action.setShortcut(QKeySequence.Find)
        search_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        search_action.triggered.connect(lambda:self.search_box.setFocus(Qt.ShortcutFocusReason));self.addAction(search_action)
        self._saved_fingerprint=self._state_fingerprint()
        self._sync_dirty_state()
        self._refresh_palette_toolbar_state()
        QTimer.singleShot(0,self._update_palette_command_density)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self._update_palette_command_density)

    def _update_palette_command_density(self):
        """Keep the core palette workflow visible and fold low-frequency commands on narrow screens."""
        width=max(0,self.width())
        try:
            self.labc_atlas_btn.setVisible(width>=1320)
            self.restore_palette_btn.setVisible(width>=1180)
            self.undo_palette_btn.setVisible(width>=1060)
            self.redo_palette_btn.setVisible(width>=1060)
            self.palette_more_btn.setVisible(width<1420)
        except RuntimeError:
            return

    def _refresh_palette_toolbar_state(self):
        if hasattr(self,'undo_palette_btn'):self.undo_palette_btn.setEnabled(bool(self._undo_stack))
        if hasattr(self,'redo_palette_btn'):self.redo_palette_btn.setEnabled(bool(self._redo_stack))
        if hasattr(self,'palette_more_undo_action'):self.palette_more_undo_action.setEnabled(bool(self._undo_stack))
        if hasattr(self,'palette_more_redo_action'):self.palette_more_redo_action.setEnabled(bool(self._redo_stack))
        has_cards=bool(self.keys()) if hasattr(self,'grid') else False
        if hasattr(self,'palette_more_atlas_action'):self.palette_more_atlas_action.setEnabled(has_cards)
        if hasattr(self,'palette_more_restore_action'):self.palette_more_restore_action.setEnabled(has_cards)
        if hasattr(self,'smart_arrange_btn'):self.smart_arrange_btn.setEnabled(has_cards)
        if hasattr(self,'palette_sort_btn'):self.palette_sort_btn.setEnabled(has_cards)
        if hasattr(self,'restore_palette_btn'):self.restore_palette_btn.setEnabled(has_cards)
        if hasattr(self,'palette_layout_btn'):
            if self._is_cpx_palette():
                settings=self.card.get('settings',{}) or {}; source_cols=int(settings.get('cpx_original_cols') or self.cols); source_rows=int(settings.get('cpx_original_rows') or self.rows)
                self.palette_layout_btn.setText(f'版式 · CPX 显示{self.cols}列')
                self.palette_layout_btn.setToolTip(f'当前只隐藏右侧/底部完全空白区域；源 CPX 固定版式仍为 {source_cols}×{source_rows}，导出 CPX 时完整恢复。')
                self.palette_layout_btn.setEnabled(False)
            else:
                self.palette_layout_btn.setEnabled(True)
                self.palette_layout_btn.setText(f'版式 · {self.cols}列 ▾' if self.auto_grid else f'版式 · {self.cols}×{self.rows} ▾')
        if hasattr(self,'palette_view_btn'):
            self.palette_view_btn.setText(f'显示 · {int(round(self.view_scale*100))}% ▾')

    def set_view_scale(self, scale):
        self.view_scale=max(0.50,min(1.75,float(scale or 1.0)))
        self.update_grid_size()
        self.main.statusBar().showMessage(f'显示缩放：{int(round(self.view_scale*100))}%（不改变色卡版式）',2500)

    def fit_view_to_width(self):
        try: viewport=max(200,self.canvas_scroll.viewport().width()-32)
        except Exception: viewport=900
        spacing=max(0,self.grid.spacing())
        logical=max(1,self.cols*self.cell_w+max(0,self.cols-1)*spacing)
        self.set_view_scale(max(0.50,min(1.50,viewport/logical)))

    def _locate_atlas_key(self, key):
        key=str(key or '')
        if not key:return
        self.grid.clearSelection()
        for i in range(self.grid.count()):
            item=self.grid.item(i)
            if str(item.data(Qt.UserRole) or '')==key:
                item.setSelected(True); self.grid.setCurrentItem(item)
                self.grid.scrollToItem(item,QAbstractItemView.PositionAtCenter)
                self.refresh_cell_states(); self.update_count(); return

    def open_labc_atlas(self):
        # One live atlas per palette. Qt may delete the C++ object on close, so
        # never keep stale dialog instances in a list.
        existing=getattr(self,'_labc_atlas_window',None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_(); existing.activateWindow(); return
            except RuntimeError:
                self._labc_atlas_window=None
        samples=self._all_samples(); records=[]
        for key in self.keys():
            sm=samples.get(str(key))
            if sm is None:continue
            try: lab=tuple(float(x) for x in sm.lab_d65_10)
            except Exception: lab=tuple(float(x) for x in self.main.sample_lab(sm))
            try: color=sample_display_qcolor(sm,lab).name()
            except Exception: color=lab_to_qcolor(lab).name()
            records.append({'key':str(key),'sample_id':str(sm.sample_id),'name':str(sm.display_name),'lab':lab,'hex':color})
        if not records:
            QMessageBox.information(self,'色彩空间图谱','当前色卡还没有可浏览的色样。');return
        try:
            from .labc_atlas_window import LabcAtlasDialog
            formula=getattr(self.main,'active_formula','delta_e00')
            method={'delta_e00':'CIE 2000','delta_e94':'CIE 1994','delta_e76':'CIE 1976','cmc21':'CMC'}.get(formula,'CIE 2000')
            kwargs={'textiles':True} if formula=='delta_e94' else ({'l':2,'c':1} if formula=='cmc21' else {})
            def distance_cb(lab1,lab2):
                return float(delta_e(tuple(lab1),tuple(lab2),method,**kwargs))
            dlg=LabcAtlasDialog(records,self,locate_callback=self._locate_atlas_key,context_label='D65 / 10°',distance_callback=distance_cb,distance_label=FORMULA_LABELS.get(formula,formula))
            dlg.setAttribute(Qt.WA_DeleteOnClose,True)
            self._labc_atlas_window=dlg
            dlg.destroyed.connect(lambda *_: setattr(self,'_labc_atlas_window',None))
            dlg.show()
        except Exception as exc:
            self._labc_atlas_window=None
            QMessageBox.critical(self,'色彩空间图谱',f'无法打开色彩空间图谱：\n{exc}')

    def import_files_dialog(self):
        paths,_=QFileDialog.getOpenFileNames(
            self,'添加色样 / 打开 CPX 色卡','',
            '颜色数据 (*.qtx *.QTX *.txt *.cpx *.CPX *.xlsx *.XLSX);;QTX (*.qtx *.QTX *.txt);;CPX (*.cpx *.CPX);;Excel (*.xlsx *.XLSX);;所有文件 (*)'
        )
        if paths:self.import_external_paths(paths)

    def trigger_command(self,name):
        action=self.commands.get(name)
        if action is not None and action.isEnabled():action.trigger()

    def closeEvent(self,event):
        self._sync_dirty_state()
        if getattr(self,'_dirty',False) and not getattr(self.main,'_app_closing',False):
            box=QMessageBox(self); box.setWindowTitle('保存色卡修改')
            box.setText(f"“{self.card.get('name','色卡方案')}”有尚未保存的修改。")
            box.setInformativeText('是否在关闭前保存？')
            save_btn=box.addButton('保存',QMessageBox.AcceptRole); discard_btn=box.addButton('不保存',QMessageBox.DestructiveRole); cancel_btn=box.addButton('取消',QMessageBox.RejectRole)
            box.setDefaultButton(save_btn); box.exec()
            if box.clickedButton() is cancel_btn:event.ignore();return
            if box.clickedButton() is save_btn:self.save()
        self._sort_generation += 1
        future=getattr(self,'_sort_future',None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._sort_job=None; self._sort_future=None
        atlas=getattr(self,'_labc_atlas_window',None)
        if atlas is not None:
            try:atlas.close()
            except RuntimeError:pass
        super().closeEvent(event)

    def _state_fingerprint(self):
        if not hasattr(self,'grid'):return None
        return (
            tuple(self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())),
            int(self.cols),int(self.rows),bool(self.auto_grid),
            tuple(sorted(self.fluorescent_force_on)),tuple(sorted(self.fluorescent_force_off)),
        )

    def _sync_dirty_state(self):
        if not hasattr(self,'grid'):return
        saved=getattr(self,'_saved_fingerprint',None)
        self._dirty=bool(self.card.get('_draft')) or (saved is not None and self._state_fingerprint()!=saved)
        title=str(self.card.get('name','色卡方案')) + (' *' if self._dirty else '')
        sub=getattr(self.main,'card_subwindows',{}).get(self.card.get('card_id'))
        if sub is not None:
            try:sub.setWindowTitle(title)
            except RuntimeError:pass
        if hasattr(self,'save_palette_btn'):self.save_palette_btn.setEnabled(bool(self._dirty))

    def _snapshot(self):
        return {
            'slots':[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())],
            'cols':self.cols,'rows':self.rows,'auto_grid':self.auto_grid,'cell_w':self.cell_w,'cell_h':self.cell_h,
            'fluor_on':sorted(self.fluorescent_force_on),'fluor_off':sorted(self.fluorescent_force_off),
        }
    def _push_undo(self):
        snap=self._snapshot()
        if not self._undo_stack or self._undo_stack[-1]!=snap:self._undo_stack.append(snap)
        if len(self._undo_stack)>80:self._undo_stack=self._undo_stack[-80:]
        self._redo_stack.clear(); self._refresh_palette_toolbar_state()
    def _restore_snapshot(self,snap):
        self.cols=max(1,int(snap.get('cols',self.cols))); self.rows=max(0,int(snap.get('rows',self.rows))); self.auto_grid=bool(snap.get('auto_grid',self.auto_grid)); self.cell_w=int(snap.get('cell_w',self.cell_w)); self.cell_h=int(snap.get('cell_h',self.cell_h))
        self.fluorescent_force_on=set(snap.get('fluor_on',[])); self.fluorescent_force_off=set(snap.get('fluor_off',[]))
        self.grid.set_slots(list(snap.get('slots',[]) or []))
        self.update_grid_size(); self.grid.clearSelection()
    def undo(self):
        if not self._undo_stack:return
        current=self._snapshot(); snap=self._undo_stack.pop(); self._redo_stack.append(current); self._restore_snapshot(snap); self._refresh_palette_toolbar_state()
    def redo(self):
        if not self._redo_stack:return
        current=self._snapshot(); snap=self._redo_stack.pop(); self._undo_stack.append(current); self._restore_snapshot(snap); self._refresh_palette_toolbar_state()
    def select_all_cards(self):
        # Windows semantics: Ctrl+A selects every visible layout slot, including
        # empty positions. Copy/Cut still operate only on occupied colour cards.
        model=self.grid.selectionModel()
        if model is None:return
        self.grid.selectAll()
        if len(self.grid.selectedItems())!=self.grid.count():
            model.clearSelection(); indexes=QItemSelection()
            for i in range(self.grid.count()):
                index=self.grid.index_for_linear(i); indexes.select(index,index)
            model.select(indexes,QItemSelectionModel.Select)
        if self.grid.count()>0:model.setCurrentIndex(self.grid.index_for_linear(0),QItemSelectionModel.NoUpdate)
        self.grid.setFocus(Qt.ShortcutFocusReason); self.refresh_cell_states()
        selected=self.grid.selectedItems(); occupied=sum(bool(it.data(Qt.UserRole)) for it in selected); blanks=len(selected)-occupied
        self.main.statusBar().showMessage(f'已选 {len(selected)} 个版位 · 色样 {occupied} · 空位 {blanks}',4000)
    def select_blank_slots(self):
        """Select every visible blank slot without touching colour cards."""
        model=self.grid.selectionModel()
        if model is None:return
        model.clearSelection(); selection=QItemSelection(); first=-1; count=0
        for i in range(self.grid.count()):
            it=self.grid.item(i)
            if it is None or it.isHidden() or it.data(Qt.UserRole):continue
            ix=self.grid.index_for_linear(i); selection.select(ix,ix); count+=1
            if first<0:first=i
        if count:model.select(selection,QItemSelectionModel.Select)
        if first>=0:
            model.setCurrentIndex(self.grid.index_for_linear(first),QItemSelectionModel.NoUpdate)
            self.grid._selection_anchor_row=first
        self.grid.setFocus(Qt.ShortcutFocusReason); self.refresh_cell_states()
        self.main.statusBar().showMessage(f'已选择全部空位：{count} 个 · Delete 删除 · Ctrl+Z 撤销',4000)
    def clear_selection(self):
        self.grid.clearSelection()
    def open_3d(self):
        samples=[self._all_samples().get(it.data(Qt.UserRole)) for it in self.grid.selectedItems() if it.data(Qt.UserRole)]
        samples=[x for x in samples if x]
        if not samples:
            samples=[self._all_samples().get(k) for k in self.keys()]
            samples=[x for x in samples if x]
        if samples:self.main.open_lab3d_window(samples)

    def copy_selected(self):
        rows=sorted({self.grid.row(it) for it in self.grid.selectedItems() if it.data(Qt.UserRole)})
        available=self._all_samples()
        keys=[str(self.grid.item(r).data(Qt.UserRole)) for r in rows if self.grid.item(r).data(Qt.UserRole)]
        self._clipboard_keys=[key for key in keys if key in available]
        if not self._clipboard_keys:
            if keys:QMessageBox.warning(self,'复制色卡','所选色卡缺少原始测量数据，请先恢复缺失色样。')
            return
        payload=[{'key':key,'sample':self.main._serialize_sample(available[key])} for key in self._clipboard_keys]
        mime=QMimeData();mime.setData(CARD_MIME,'\n'.join(self._clipboard_keys).encode('utf-8'))
        mime.setData(CARD_PLAN_SAMPLES_MIME,json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        mime.setText('\n'.join(self._clipboard_keys));QApplication.clipboard().setMimeData(mime)
        self.main.statusBar().showMessage(f'已复制 {len(self._clipboard_keys)} 张完整色卡'+(f'；跳过 {len(keys)-len(self._clipboard_keys)} 张缺失色样' if len(keys)>len(self._clipboard_keys) else ''),3500)
    def embed_transfer_samples(self,mime,keys):
        if mime is None or not mime.hasFormat(CARD_PLAN_SAMPLES_MIME):return {}
        try:payload=json.loads(bytes(mime.data(CARD_PLAN_SAMPLES_MIME)).decode('utf-8'))
        except (ValueError,TypeError):return {}
        transfer={}
        for entry in payload:
            try:
                key=str(entry['key']);sm=self.main._deserialize_sample(entry['sample'])
                if key in keys and sample_key(sm)==key:transfer[key]=sm
            except (KeyError,TypeError,ValueError):continue
        if not transfer:return {}
        settings=dict(self.card.get('settings') or {});embedded=list(settings.get('embedded_samples') or [])
        known=set()
        for data in embedded:
            try:known.add(sample_key(self.main._deserialize_sample(data)))
            except (KeyError,TypeError,ValueError):continue
        for key,sm in transfer.items():
            if key not in known:embedded.append(self.main._serialize_sample(sm))
        settings['embedded_samples']=embedded;self.card['settings']=settings; self.invalidate_sample_cache()
        return transfer
    def cut_selected(self):
        selected={str(it.data(Qt.UserRole)) for it in self.grid.selectedItems() if it.data(Qt.UserRole)}
        self.copy_selected()
        if self._clipboard_keys and selected==set(self._clipboard_keys):self.remove_selected()
        elif selected:QMessageBox.information(self,'剪切色卡','所选卡中有缺失测量数据，已取消剪切，避免丢失原方案中的卡位。')
    def paste_selected(self):
        keys=[]
        clipboard=QApplication.clipboard().mimeData()
        if clipboard and clipboard.hasFormat(CARD_MIME):
            keys=[x.strip() for x in bytes(clipboard.data(CARD_MIME)).decode('utf-8','ignore').splitlines() if x.strip()]
        else:
            text=QApplication.clipboard().text().strip(); keys=[x.strip() for x in text.splitlines() if x.strip() and '|' in x]
        if not keys and (clipboard is None or not clipboard.text().strip()):keys=list(self._clipboard_keys)
        if not keys:return
        sources=self._all_samples()
        # Collect first; only attach snapshots after capacity is checked below.
        if clipboard and clipboard.hasFormat(CARD_PLAN_SAMPLES_MIME):
            try:
                payload=json.loads(bytes(clipboard.data(CARD_PLAN_SAMPLES_MIME)).decode('utf-8'))
                for entry in payload:
                    key=str(entry['key']);sm=self.main._deserialize_sample(entry['sample'])
                    if key in keys and sample_key(sm)==key:sources[key]=sm
            except (ValueError,TypeError,KeyError):pass
        unresolved=[key for key in keys if key not in sources]
        if unresolved:
            for other in self.main.card_plan_windows.values():
                if other is not self:
                    candidates=other._all_samples()
                    for key in unresolved:
                        if key in candidates:sources[key]=candidates[key]
            unresolved=[key for key in keys if key not in sources]
        keys=[key for key in keys if key in sources]
        if not keys:
            QMessageBox.warning(self,'粘贴色卡','剪贴板中的色卡没有可恢复的测量数据；请先打开源方案或原始 QTX。');return
        start=self.grid.currentRow() if self.grid.currentRow()>=0 else max(0,self._last_occupied_index()+1)
        need=start+len(keys)
        if need>self.grid.count():
            if not self.auto_grid:
                QMessageBox.information(self,'粘贴','固定网格剩余位置不足，请先扩大网格。'); return
        self._push_undo()
        settings=dict(self.card.get('settings') or {})
        embedded=list(settings.get('embedded_samples') or [])
        known=set()
        for data in embedded:
            try:known.add(sample_key(self.main._deserialize_sample(data)))
            except (KeyError,TypeError,ValueError):continue
        for key in keys:
            if key not in known:embedded.append(self.main._serialize_sample(sources[key]));known.add(key)
        settings['embedded_samples']=embedded;self.card['settings']=settings; self.invalidate_sample_cache()
        if need>self.grid.count():self.grid.ensure_count(need)
        for off,key in enumerate(keys):self.grid._model.set_data_by_linear(start+off,key,Qt.UserRole,emit=False)
        self.grid._model.layoutChanged.emit(); self.refresh_cell_widgets()
        self.update_grid_size(); self.grid.clearSelection()
        for off in range(len(keys)):
            if start+off<self.grid.count():self.grid.item(start+off).setSelected(True)
        self.main.statusBar().showMessage(f'已粘贴 {len(keys)} 张完整色卡'+(f'；跳过 {len(unresolved)} 张没有测量数据的色卡' if unresolved else '')+'，按 Ctrl+S 保存方案',4500)
    def move_selected(self,delta):
        rows=sorted({self.grid.row(it) for it in self.grid.selectedItems() if it.data(Qt.UserRole)})
        if not rows or delta==0:return
        targets=[r+delta for r in rows]
        if min(targets)<0:return
        if max(targets)>=self.grid.count():
            if not self.auto_grid:return
            self.grid.ensure_count(max(targets)+1)
        self._push_undo(); slots=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        pairs=list(zip(rows,targets)); pairs=sorted(pairs,reverse=delta>0)
        for src,dst in pairs:slots[src],slots[dst]=slots[dst],slots[src]
        self.grid.set_slots(slots)
        self.update_grid_size(); self.grid.clearSelection()
        for r in targets:
            if 0<=r<self.grid.count() and self.grid.item(r).data(Qt.UserRole):self.grid.item(r).setSelected(True)
        if targets:self.grid.setCurrentRow(targets[0])
    def save_as(self):
        name,ok=QInputDialog.getText(self,'另存为色卡方案副本','副本名称：',text=f"{self.card.get('name','色卡方案')} 副本")
        if not ok or not name.strip():return
        clone=dict(self.card); clone['card_id']=str(uuid.uuid4()); clone['name']=name.strip(); clone['columns_count']=self.cols; clone['layout']=[{'sample_key':self.grid.item(i).data(Qt.UserRole)} for i in range(self.grid.count())]
        settings=dict(clone.get('settings',{}) or {}); settings.update({'grid_cols':self.cols,'grid_rows':self.rows,'auto_grid':self.auto_grid,'cell_w':self.cell_w,'cell_h':self.cell_h,'sort':self.sort_key,'fluorescent_force_on':sorted(self.fluorescent_force_on),'fluorescent_force_off':sorted(self.fluorescent_force_off)})
        samples=self._all_samples();settings['embedded_samples']=[self.main._serialize_sample(samples[k]) for k in dict.fromkeys(self.keys()) if k in samples];clone['settings']=settings
        saved=self.main.store.save_color_card(clone); self.main.refresh_color_card_list(); self.main.open_color_card_window(saved['card_id']); self.main.statusBar().showMessage(f'已另存为：{saved["name"]}',3500)
    def choose_export(self):
        choice,ok=QInputDialog.getItem(self,'导出色卡方案','格式：',['PDF 色卡','PNG 图片','JPG 图片','Excel 数据','QTX','CPX'],0,False)
        if not ok:return
        mapping={'PDF 色卡':'pdf','PNG 图片':'png','JPG 图片':'jpg','Excel 数据':'excel','QTX':'qtx','CPX':'cpx'}
        self.export_format(mapping.get(choice,choice.lower()))
    def export_format(self,fmt):
        f=str(fmt).lower()
        if f=='qtx':self.export_qtx()
        elif f=='cpx':self.export_cpx()
        elif f in {'excel','xlsx'}:self.export_excel()
        elif f in {'pdf','png','jpg','jpeg'}:self.export_palette_visual(f)

    def _load_palette_source_responsive(self, canonical, suffix):
        """Parse a large palette source without freezing the Qt event loop.

        The parser remains unchanged; only its execution is moved to the existing
        single-worker import executor while a modal progress surface keeps Windows
        responsive.  This matters most for 3k+ colour QTX files.
        """
        if suffix not in {'.qtx','.txt'}:
            result=import_excel_workbook(canonical)
            return [replace(sm,source_file=canonical) for sm in result.samples], list(result.warnings or [])
        dlg=QProgressDialog(f'正在解析 {Path(canonical).name}…','取消',0,0,self.main)
        dlg.setWindowTitle('色卡编排 · 导入 QTX'); dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0)
        dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.show(); QApplication.processEvents()
        future=self.main._import_executor.submit(parse_qtx_file,canonical)
        cancelled=False
        while not future.done():
            QApplication.processEvents()
            if dlg.wasCanceled():
                cancelled=True
                try:future.cancel()
                except Exception:pass
                break
            time.sleep(0.015)
        if cancelled:
            dlg.close(); return None, []
        try:incoming=[replace(sm,source_file=canonical) for sm in future.result()]
        finally:dlg.close()
        return incoming, []

    def _serialize_palette_samples_responsive(self, samples):
        """Serialize a large palette batch while keeping the Windows event loop alive.

        The previous path serialized thousands of spectral samples synchronously and
        then immediately rebuilt the Qt grid.  On 3,500-colour sources Windows could
        mark the application as non-responsive even though it was still working.
        This keeps exactly the same embedded data, but yields to Qt every small chunk.
        """
        samples=list(samples or [])
        if len(samples)<500:
            return [self.main._serialize_sample(sm) for sm in samples]
        dlg=QProgressDialog('正在准备色样数据…','',0,len(samples),self.main)
        dlg.setWindowTitle('色卡编排 · 大文件导入')
        dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0)
        dlg.setCancelButton(None); dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.show()
        out=[]
        try:
            for i,sm in enumerate(samples,1):
                out.append(self.main._serialize_sample(sm))
                if i==1 or i%128==0 or i==len(samples):
                    dlg.setValue(i); dlg.setLabelText(f'正在准备色样数据… {i} / {len(samples)}')
                    QApplication.processEvents()
        finally:
            dlg.close()
        return out

    def _prime_palette_sample_cache(self, sample_map):
        """Reuse already-parsed Sample objects after a local file import.

        Without this cache prime, a 3,500-colour import serialized the samples and
        then immediately deserialized all 3,500 again just to paint the first screen.
        """
        try:
            self._sample_map_cache=dict(sample_map or {})
            self._sample_map_cache_key=(
                getattr(self.main,'_library_data_revision',0),
                self._sample_map_epoch,
                self._source_recovery_signature(),
            )
        except Exception:
            self._sample_map_cache=None; self._sample_map_cache_key=None

    @profiled('palette.import_external_paths')
    def import_external_paths(self, paths):
        """Drop QTX / CPX / Excel (or folders containing them) into this scheme.

        This is a palette-local import: source files are parsed into embedded sample
        snapshots and do not get written into the formal colour library.  A draft
        palette also remains a draft until the user explicitly presses Ctrl+S.
        """
        expanded=self.main._expand_workspace_inputs(list(paths),include_excel=True)
        supported=[p for p in expanded if Path(p).suffix.lower() in {'.qtx','.txt','.cpx','.xlsx'}]
        if not supported:
            QMessageBox.information(self,'色卡编排','没有找到可导入的 QTX / CPX / Excel 文件。')
            return

        # CPX is a palette document, not merely a bag of samples.  Its fixed grid,
        # blank slots, TileCount/TileSize/TileGap and raw XML must stay intact.
        # Therefore a CPX selected from “添加色样” opens as its own child palette
        # instead of being flattened into the current plan. QTX/Excel still append.
        cpx_paths=[p for p in supported if Path(p).suffix.lower()=='.cpx']
        for cpx_path in cpx_paths:
            try:
                self.main.import_cpx_project(cpx_path)
            except Exception as exc:
                QMessageBox.warning(self,'CPX 打开失败',f'{Path(cpx_path).name}: {exc}')
        supported=[p for p in supported if Path(p).suffix.lower()!='.cpx']
        if not supported:
            self.main.statusBar().showMessage('CPX 已按原版式作为独立色卡子窗口打开；当前方案未被改动。',5000)
            return

        chosen_all=[]; errors=[]; warnings=[]; pending_cpx_templates={}
        already=set(self.keys())
        for path in supported:
            try:
                canonical=str(Path(path).resolve()); suffix=Path(canonical).suffix.lower()
                if suffix in {'.qtx','.txt','.xlsx'}:
                    incoming, import_warnings=self._load_palette_source_responsive(canonical,suffix)
                    if incoming is None:
                        continue
                    if import_warnings:
                        warnings.append(f'{Path(canonical).name}: {len(import_warnings)} 项导入提示')
                else:
                    continue
                if not incoming:
                    raise ValueError('没有识别到色样')
                # HF73 data-integrity guard: preserve every measurement record
                # even when a source repeats the same GUID/sample_id.
                incoming=_palette_disambiguate_duplicate_samples(list(incoming))
                dlg=QtxImportSelectionDialog(canonical,incoming,already=already,parent=self.main,purpose='当前色卡方案')
                if dlg.exec()!=QDialog.Accepted:
                    continue
                chosen=dlg.chosen()
                for sm in chosen:
                    key=sample_key(sm)
                    if key in already:
                        continue
                    chosen_all.append(sm); already.add(key)
                self.main._remember_recent(canonical)
            except Exception as exc:
                errors.append(f'{Path(path).name}: {exc}')
        if not chosen_all:
            if errors:QMessageBox.warning(self,'部分文件无法导入','\n'.join(errors[:20]))
            return
        # Palette imports stay isolated from the colour-library workspace. Embed
        # real measurements in this scheme so they survive source-file removal.
        # Keep a runtime map before mutating settings.  This is usually tiny for a
        # new palette and avoids re-deserializing the newly selected 660/3500 samples.
        existing_runtime=dict(self._all_samples())
        settings=dict(self.card.get('settings',{}) or {})
        embedded=list(settings.get('embedded_samples',[]) or [])
        by_key={}
        for data in embedded:
            try:
                sm=self.main._deserialize_sample(data); by_key[sample_key(sm)]=data
            except Exception:
                pass
        serialized_new=self._serialize_palette_samples_responsive(chosen_all)
        chosen_map={sample_key(sm):sm for sm in chosen_all}
        for sm,data in zip(chosen_all,serialized_new):
            by_key[sample_key(sm)]=data
        settings['embedded_samples']=list(by_key.values())
        templates=dict(settings.get('cpx_templates') or {}); templates.update(pending_cpx_templates); settings['cpx_templates']=templates
        self.card['settings']=settings; self.invalidate_sample_cache()
        runtime_map=existing_runtime; runtime_map.update(chosen_map)
        start=max(0,self._last_occupied_index()+1)
        self.receive_drop_many([sample_key(x) for x in chosen_all],start,copy_mode=True,
                               preembedded=True,sample_map=runtime_map,responsive=True)
        self._prime_palette_sample_cache(runtime_map)
        # This is the user's source order before any analytical/experimental sort.
        self._capture_original_order()
        # Palette Studio P1: imports never auto-save. Every edit follows the same
        # predictable rule: mark the palette dirty and let Ctrl+S / 保存 persist it.
        suffix_note='；尚未保存，请按 Ctrl+S 保存'
        if warnings:
            suffix_note+=f'；{len(warnings)} 个文件有导入提示'
        self.main.statusBar().showMessage(f'已拖入 {len(chosen_all)} 个色样到当前色卡方案{suffix_note}',5000)
        if errors:
            QMessageBox.warning(self,'部分文件无法导入','\n'.join(errors[:20]))

    def invalidate_sample_cache(self):
        self._sample_map_epoch += 1
        self._sample_map_cache = None
        self._sample_map_cache_key = None
        self._paint_sample_map={}; self._palette_paint_cache={}

    def _source_recovery_signature(self):
        """Cheap signature for legacy palettes that still reference source QTX paths."""
        paths={str(k).rpartition('|')[0] for k in self.keys() if k}
        signature=[]
        for path in sorted(paths):
            if not path or Path(path).suffix.lower() not in {'.qtx','.txt'}:
                continue
            try: signature.append((path,Path(path).stat().st_mtime_ns))
            except OSError: signature.append((path,None))
        return tuple(signature)

    @profiled("palette.all_samples")
    def _all_samples(self):
        cache_key=(getattr(self.main,'_library_data_revision',0),self._sample_map_epoch,self._source_recovery_signature())
        if self._sample_map_cache is not None and self._sample_map_cache_key==cache_key:
            return self._sample_map_cache
        out={}
        # CPX palettes embed their sample payload so a saved palette remains editable
        # after an application restart even when the CPX was opened only temporarily.
        for data in (self.card.get('settings',{}) or {}).get('embedded_samples',[]) or []:
            try:
                sm=self.main._deserialize_sample(data)
                if self.main._sample_allowed_by_data_scope(sm):out[sample_key(sm)]=sm
            except Exception:pass
        # P3-3: a palette only needs the colours referenced by its own layout.
        # Do not decode the complete formal library merely to open one palette.
        needed=set(self.keys())
        for entry in self.card.get('layout',[]) or []:
            key=entry.get('sample_key') if isinstance(entry,dict) else entry
            if key:needed.add(str(key))
        for sm in self.main._samples_by_keys_on_demand(needed,full=True):out[sample_key(sm)]=sm
        # Older palettes saved keys without snapshots. Recover original QTX
        # measurements if the source file still exists, then embed on next save.
        missing={str(k) for k in self.keys() if k and str(k) not in out}
        for path in {k.rpartition('|')[0] for k in missing}:
            if not path or Path(path).suffix.lower() not in {'.qtx','.txt'} or not Path(path).is_file():continue
            if not self.main.can_view_source_path(path):continue
            try:
                stamp=Path(path).stat().st_mtime_ns
                if path not in self._source_recovery_cache or self._source_recovery_cache[path][0]!=stamp:
                    self._source_recovery_cache[path]=(stamp,parse_qtx_file(path))
                for sm in self._source_recovery_cache[path][1]:
                    recovered=replace(sm,source_file=path)
                    key=sample_key(recovered)
                    if key in missing:out[key]=recovered
            except Exception:pass
        self._sample_map_cache=out
        self._sample_map_cache_key=cache_key
        return out
    def _item_for_key(self,key,samples=None):
        if samples is None:samples=self._all_samples()
        sm=samples.get(key)
        roles={Qt.UserRole:key,Qt.UserRole+1:('缺失色样' if sm is None else sm.display_name),Qt.ToolTipRole:('缺失色样' if sm is None else sm.display_name)}
        if sm is None:roles[Qt.ForegroundRole]=QColor('#B42332')
        return PaletteItemProxy(None,-1,roles)
    def _blank(self):
        return PaletteItemProxy(None,-1,{Qt.UserRole:None,Qt.UserRole+1:'',Qt.ToolTipRole:'空位 · 单击选择 / Ctrl 多选 / Shift 连选 / Ctrl+A 全选 / Delete 删除 / Ctrl+Z 撤销'})
    def keys(self):return [self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count()) if self.grid.item(i).data(Qt.UserRole)]
    def _last_occupied_index(self):
        # HF121: reverse scan avoids allocating a temporary list for 3k+ slots.
        for i in range(self.grid.count()-1,-1,-1):
            if self.grid.item(i).data(Qt.UserRole):
                return i
        return -1
    def load_layout(self):
        self._palette_size_hint_token=None; self._materialized_palette_rows=set()
        samples=self._all_samples(); slots=[]; meta=[]
        for entry in self.card.get('layout',[]):
            key=entry.get('sample_key') if isinstance(entry,dict) else entry
            slots.append(key or None)
            sm=samples.get(key) if key else None
            meta.append({'name':('缺失色样' if key and sm is None else (sm.display_name if sm else '')),
                         'tooltip':('缺失色样' if key and sm is None else (sm.display_name if sm else '空位 · 单击选择 / Ctrl 多选 / Shift 连选 / Ctrl+A 全选 / Delete 删除 / Ctrl+Z 撤销'))})
        self.grid.set_slots(slots,meta); self.ensure_slots()
        if self._recover_missing_samples():
            recovered=self._all_samples()
            for i in range(self.grid.count()):
                item=self.grid.item(i); sm=recovered.get(item.data(Qt.UserRole))
                if sm:item.setData(Qt.UserRole+1,sm.display_name);item.setToolTip(sm.display_name)
        self.update_count(); self.refresh_cell_widgets()
    def _recover_missing_samples(self, extra_samples=()):
        """Repair moved-source references by exact GUID; never guess from colour."""
        available=self._all_samples()
        missing={str(key) for key in self.keys() if key and str(key) not in available}
        if not missing:return 0
        candidates={}
        wanted_ids={key.rpartition('|')[2] for key in missing if key.rpartition('|')[2]}
        try:library_candidates=self.main.store.load_samples_by_sample_ids(wanted_ids)
        except Exception:library_candidates=[]
        for sm in list(library_candidates)+list(extra_samples):
            candidates.setdefault(str(sm.sample_id),[]).append(sm)
        for card in self.main.store.list_color_cards():
            for data in (card.get('settings') or {}).get('embedded_samples',[]) or []:
                try:
                    sm=self.main._deserialize_sample(data)
                    candidates.setdefault(str(sm.sample_id),[]).append(sm)
                except (KeyError,TypeError,ValueError):continue
        recovered=[]
        for key in missing:
            matches=candidates.get(key.rpartition('|')[2],[])
            distinct={(x.kind,x.display_name,tuple(x.lab_d65_10),tuple(x.reflectance)) for x in matches}
            if len(distinct)!=1:continue
            sm=replace(matches[0],source_file=key.rpartition('|')[0])
            recovered.append(self.main._serialize_sample(sm))
        if not recovered:return 0
        settings=dict(self.card.get('settings') or {})
        settings['embedded_samples']=list(settings.get('embedded_samples') or [])+recovered
        self.card['settings']=settings;self.card=self.main.store.save_color_card(self.card); self.invalidate_sample_cache()
        return len(recovered)
    def recover_from_qtx(self):
        available=self._all_samples()
        missing={str(key) for key in self.keys() if key and str(key) not in available}
        if not missing:
            QMessageBox.information(self,'恢复缺失色样','当前方案没有缺失色样。');return
        paths,_=QFileDialog.getOpenFileNames(self,'选择缺失色样的原始 QTX','','QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)')
        if not paths:return
        samples=[];errors=[]
        for path in paths:
            try:samples.extend(parse_qtx_file(path))
            except Exception as exc:errors.append(f'{Path(path).name}：{exc}')
        repaired=self._recover_missing_samples(samples)
        if repaired:self.load_layout()
        available=self._all_samples()
        remaining=sum(bool(k and str(k) not in available) for k in self.keys())
        QMessageBox.information(self,'恢复缺失色样',f'按内部色样编号恢复 {repaired} 张；仍缺失 {remaining} 张。'+('\n'+'\n'.join(errors[:4]) if errors else ''))
    def replace_missing_with_library_sample(self,row):
        if row<0 or row>=self.grid.count():return
        old_key=self.grid.item(row).data(Qt.UserRole)
        if not old_key or old_key in self._all_samples():return
        dlg=AddSamplesDialog(self.main,set(self.keys()),self)
        dlg.setWindowTitle('为缺失卡指定现有色样')
        if dlg.exec()!=QDialog.Accepted:return
        chosen=dlg.chosen()
        if len(chosen)!=1:
            QMessageBox.information(self,'指定替代色样','每次请选择一张色样，便于核对原卡位。');return
        sm=chosen[0];key=sample_key(sm)
        self._push_undo()
        settings=dict(self.card.get('settings') or {})
        embedded=list(settings.get('embedded_samples') or []);embedded.append(self.main._serialize_sample(sm))
        settings['embedded_samples']=embedded;self.card['settings']=settings; self.invalidate_sample_cache()
        self._replace(row,self._item_for_key(key,{key:sm}))
        self.update_grid_size()
        self.main.statusBar().showMessage(f'已在原卡位指定 {sm.display_name}；按 Ctrl+S 保存方案',5000)
    def ensure_slots(self):
        # HF124: resize the light model in one reset instead of creating/removing
        # thousands of Qt item objects one by one.
        if self.auto_grid:
            n=max(0,self._last_occupied_index()+1,len(self.keys()))
            self.rows=0 if n<=0 else max(1,int(math.ceil(n/max(1,self.cols))))
        needed=max(0,self.cols*self.rows)
        current=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        if len(current)<needed: current.extend([None]*(needed-len(current)))
        elif len(current)>needed:
            while len(current)>needed and current and current[-1] is None: current.pop()
        if current!=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]:self.grid.set_slots(current)
    def update_count(self):
        if self._is_cpx_palette():
            settings=self.card.get('settings',{}) or {}; src_cols=int(settings.get('cpx_original_cols') or self.cols); src_rows=int(settings.get('cpx_original_rows') or self.rows)
            mode=f'CPX 源版式 {src_cols}×{src_rows} · 当前显示 {self.cols}×{self.rows}'
        else:
            mode=(f'{self.cols} 列 · 自动扩展行'
                  if self.auto_grid else f'固定版式 {self.cols}列 × {self.rows}行')
        mode += f' · 显示 {int(round(self.view_scale*100))}%'
        selected_items=list(self.grid.selectedItems()); selected_names=[it.data(Qt.UserRole+1) for it in selected_items if it.data(Qt.UserRole)]
        selected_blanks=sum(not bool(it.data(Qt.UserRole)) for it in selected_items)
        selected_text=(f'已选 {len(selected_items)} 个版位（色样 {len(selected_names)} / 空位 {selected_blanks}）' if selected_items else '已选 0 个')
        self.count_label.setText(f"{self.card.get('name','色卡方案')}  ·  {len(self.keys())} 色样  ·  {selected_text}  ·  {mode}  ·  Ctrl+S 保存")
        self.count_label.setToolTip(('已选色样：\n'+'\n'.join(str(x) for x in selected_names[:40])+f'\n空位：{selected_blanks}') if selected_items else '点击版位选择；Ctrl/Shift 多选，Ctrl+A 全选，Delete 删除所选')
        self.count_label.setStyleSheet('background:#EFF6FF;color:#1557D2;padding:7px 12px;border:1px solid #93B7FF;font-size:11px;font-weight:600;' if selected_items else 'background:#F8FAFC;color:#6B7280;padding:7px 12px;border:0;border-top:1px solid #E5E7EB;font-size:11px;')
        self._sync_dirty_state()
        self._refresh_palette_toolbar_state()
    def update_grid_size(self):
        # Frozen HF50 virtual-list geometry: the QListView owns the scrollbar and
        # remains viewport-height.  Only its width is capped so exactly ``cols``
        # slots fit; height is never expanded to all 3,500 colours.
        self.ensure_slots()
        w=max(64,int(round(self.cell_w*self.view_scale)));h=max(64,int(round(self.cell_h*self.view_scale)))
        self.grid.setGridSize(QSize(w,h));self.grid.setWrapping(True)
        self.grid.setResizeMode(QListView.Adjust);self.grid.setUniformItemSizes(True);self.grid.setLayoutMode(QListView.Batched);self.grid.setBatchSize(180)
        self.grid.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        spacing=max(0,self.grid.spacing())
        # HF128: reserve the real vertical-scrollbar/frame width as well as the
        # 14 px visual padding on each side.  HF124-HF127 capped the QListView
        # to exactly 28 + cols*(cell+spacing), but once the vertical scrollbar
        # appeared Qt reduced the viewport enough to wrap the last logical
        # column onto the next row (e.g. toolbar said 8 columns while only 7
        # were visible).  Keep the frozen virtual QListView, just size its
        # viewport for the requested logical column count.
        try:
            scroll_extent=max(0,int(self.grid.style().pixelMetric(QStyle.PM_ScrollBarExtent,None,self.grid)))
        except Exception:
            scroll_extent=18
        frame_extra=max(0,int(self.grid.frameWidth())*2)
        canvas_w=28+max(1,self.cols)*(w+spacing)+scroll_extent+frame_extra+6
        self.grid.setMinimumWidth(canvas_w);self.grid.setMaximumWidth(canvas_w)
        self._palette_size_hint_token=(w,h,self.grid.count(),self.cols)
        self.refresh_cell_widgets();self.grid.updateGeometries();self.grid.viewport().update();self.update_count()

    def filter_cards(self,value):
        # Do not rebuild/filter the whole palette for every keystroke.
        self._plan_search_timer.start()
    def _apply_plan_search(self):
        query=self.search_box.text().strip().casefold(); self._palette_search_query=query
        if not query:
            self._palette_search_match_rows=set(range(self.grid.count())); self.search_summary.setText(f'{len(self.keys())} 张色卡'); self.grid.viewport().update(); return
        samples=self._all_samples(); matched=0; rows=set()
        for i in range(self.grid.count()):
            key=self.grid.item(i).data(Qt.UserRole); sm=samples.get(key) if key else None
            if sm is not None:
                lab=self.main.sample_lab(sm); L,C,h=lab_to_lch(lab)
                content=f'{sm.display_name} {sm.sample_id} {lab[0]:.2f} {lab[1]:+.2f} {lab[2]:+.2f} {C:.2f} {h:.1f}'.casefold()
            else:content=f'缺失色样 {key}'.casefold() if key else ''
            if bool(key) and query in content:rows.add(i);matched+=1
        self._palette_search_match_rows=rows;self.search_summary.setText(f'匹配 {matched} / {len(self.keys())} · 原版位保持不变');self.grid.viewport().update()
    def _is_fluorescent_key(self,key,sample=None):
        key=str(key or '')
        if not key:
            return False
        if key in self.fluorescent_force_on:
            return True
        if key in self.fluorescent_force_off:
            return False
        if sample is None:
            sample=self._all_samples().get(key)
        return sample_is_fluorescent(sample)
    def set_fluorescent_override(self, keys, state):
        keys={str(k) for k in keys if k}
        if not keys:return
        self._push_undo()
        if state is True:
            self.fluorescent_force_on.update(keys); self.fluorescent_force_off.difference_update(keys)
        elif state is False:
            self.fluorescent_force_off.update(keys); self.fluorescent_force_on.difference_update(keys)
        else:
            self.fluorescent_force_on.difference_update(keys); self.fluorescent_force_off.difference_update(keys)
        self.refresh_cell_widgets()
    def _schedule_visible_palette_refresh(self):
        self.grid.viewport().update()

    def _visible_palette_rows(self):
        # QTableView performs viewport culling internally. Keep this helper for
        # compatibility with older callers/debug tooling.
        return set()

    def _palette_visual_record(self,key):
        key=str(key or '')
        cache=getattr(self,'_palette_paint_cache',None)
        if cache is None:self._palette_paint_cache={};cache=self._palette_paint_cache
        cache_key=(key,getattr(self,'sort_key',''),key in self.fluorescent_force_on,key in self.fluorescent_force_off)
        if cache_key in cache:return cache[cache_key]
        samples=getattr(self,'_paint_sample_map',None) or self._all_samples(); sm=samples.get(key)
        if sm is None:
            rec=('缺失色样','LAB 未找到',QColor('#A45A5A'),False,True)
        else:
            lab=self.main.sample_lab(sm);lab_text=f'L* {lab[0]:.2f}  a* {lab[1]:+.2f}  b* {lab[2]:+.2f}'
            if getattr(self,'sort_key','')=='color_difference':
                de_map=getattr(self,'_last_reference_delta',{}) or {}
                if key in de_map:lab_text=f"{getattr(self,'_last_reference_delta_label','ΔE')} {float(de_map[key]):.2f}  ·  L* {lab[0]:.2f}"
            color=fluorescent_lab_to_qcolor(lab) if key in self.fluorescent_force_on else (lab_to_qcolor(lab) if key in self.fluorescent_force_off else sample_display_qcolor(sm,lab))
            rec=(sm.display_name,lab_text,color,self._is_fluorescent_key(key,sm),False)
        cache[cache_key]=rec;return rec

    def _make_or_update_palette_cell(self,i,samples,selected):
        # No-op compatibility shim: cards are painted by PaletteCardDelegate.
        self._paint_sample_map=samples; self.grid.viewport().update()

    def _refresh_visible_palette_cells(self):
        self.grid.viewport().update()

    def refresh_cell_widgets(self):
        # One cache lookup for the whole viewport, not one SQLite/QTX lookup per cell.
        self._paint_sample_map=self._all_samples();self._palette_paint_cache={};self.grid.viewport().update()

    def refresh_cell_states(self):
        self.grid.viewport().update();self.update_count()

    @profiled('palette.rebuild_slots_bulk')
    def _rebuild_slots_bulk(self, slots, select_rows=(), sample_map=None, responsive=False):
        """Replace the entire palette model in one Qt reset.

        HF124 removes the former 3,500-QListWidgetItem construction loop.  The
        domain slots become one Python list; QTableView requests only visible
        indexes, so model load stays bounded and first paint is viewport-sized.
        """
        t0=time.perf_counter(); wanted=list(slots); selected_rows={int(x) for x in select_rows if int(x)>=0}
        if sample_map is not None:self._paint_sample_map=dict(sample_map)
        self.grid.setUpdatesEnabled(False);self.grid.blockSignals(True)
        try:
            self.grid.set_slots(wanted)
            self._materialized_palette_rows=set();self._palette_paint_cache={}
        finally:
            self.grid.blockSignals(False);self.grid.setUpdatesEnabled(True)
        self.update_grid_size();self.grid.clearSelection()
        model=self.grid.selectionModel(); selection=QItemSelection()
        for row in sorted(selected_rows):
            ix=self.grid.index_for_linear(row)
            if ix.isValid():selection.select(ix,ix)
        if model is not None and selected_rows:model.select(selection,QItemSelectionModel.Select)
        if selected_rows:
            ix=self.grid.index_for_linear(min(selected_rows))
            if ix.isValid():model.setCurrentIndex(ix,QItemSelectionModel.NoUpdate);self.grid.scrollTo(ix,QAbstractItemView.PositionAtCenter)
        self.refresh_cell_states();self.grid.viewport().update()
        model_ms=(time.perf_counter()-t0)*1000.0
        self._last_palette_model_load_ms=model_ms
        # Measure actual first viewport paint after Qt has processed geometry/layout.
        stamp=time.perf_counter();self._palette_first_paint_pending=stamp
        QTimer.singleShot(0,lambda:self._record_palette_first_paint(stamp,len(wanted)))

    def _record_palette_first_paint(self,stamp,count):
        if getattr(self,'_palette_first_paint_pending',None)!=stamp:return
        try:self.grid.viewport().repaint()
        except Exception:self.grid.viewport().update()
        elapsed=(time.perf_counter()-stamp)*1000.0;self._last_palette_first_paint_ms=elapsed;self._palette_first_paint_pending=None
        model_ms=float(getattr(self,'_last_palette_model_load_ms',0.0))
        self.main.statusBar().showMessage(f'色卡模型 {count} 位：{model_ms:.1f} ms · 首屏 {elapsed:.1f} ms',4500)
        try:self.main._append_ui_perf_event('palette_first_paint',elapsed,{'slots':int(count),'model_ms':round(model_ms,3)})
        except Exception:pass

    def _replace(self,row,item):
        if row<0 or row>=self.grid.count():return
        self.grid._model.set_data_by_linear(row,item.data(Qt.UserRole),Qt.UserRole,emit=False)
        self.grid._model.set_data_by_linear(row,item.data(Qt.UserRole+1),Qt.UserRole+1,emit=False)
        self.grid._model.set_data_by_linear(row,item.data(Qt.ToolTipRole),Qt.ToolTipRole,emit=False)
        ix=self.grid.index_for_linear(row)
        if ix.isValid():self.grid._model.dataChanged.emit(ix,ix,[Qt.UserRole,Qt.UserRole+1,Qt.ToolTipRole])
        self.refresh_cell_widgets()
    def receive_drop(self,key,target,source_id=None,source_row=-1,copy_mode=False):
        if not key:return
        if source_id is None:
            try:self._embed_samples(self.main._samples_by_keys_on_demand([key],full=True))
            except Exception:pass
        self._push_undo()
        if source_id==self.card['card_id'] and source_row>=0:
            if source_row==target:return
            a=self.grid.item(source_row).data(Qt.UserRole); b=self.grid.item(target).data(Qt.UserRole)
            self._replace(source_row,self._item_for_key(b) if b else self._blank()); self._replace(target,self._item_for_key(a)); self.update_count(); return
        existing=next((i for i in range(self.grid.count()) if self.grid.item(i).data(Qt.UserRole)==key),-1)
        if existing>=0:self.grid.setCurrentRow(existing); return
        if source_id and source_id!=self.card['card_id'] and not copy_mode:
            src=self.main.card_plan_windows.get(source_id)
            if src:src.remove_key(key)
        if target<0 or target>=self.grid.count():target=max(0,self.grid.count()-1)
        if self.grid.item(target).data(Qt.UserRole):
            blank=next((i for i in range(self.grid.count()) if not self.grid.item(i).data(Qt.UserRole)),-1)
            if blank<0:self.grid.addItem(self._item_for_key(key)); self.update_grid_size(); return
            target=blank
        self._replace(target,self._item_for_key(key)); self.update_grid_size() if self.auto_grid else (self.update_count(),self.refresh_cell_widgets())
    def _embed_samples(self,samples):
        samples=[x for x in samples if x is not None]
        if not samples:return 0
        settings=dict(self.card.get('settings',{}) or {}); embedded=list(settings.get('embedded_samples',[]) or [])
        by_key={}
        for data in embedded:
            try: by_key[sample_key(self.main._deserialize_sample(data))]=data
            except Exception: pass
        before=len(by_key)
        for sm in samples: by_key[sample_key(sm)]=self.main._serialize_sample(sm)
        settings['embedded_samples']=list(by_key.values()); self.card['settings']=settings; self.invalidate_sample_cache()
        return len(by_key)-before

    @profiled('palette.receive_drop_many')
    def receive_drop_many(self,keys,target,source_id=None,source_rows=None,copy_mode=False,preembedded=False,sample_map=None,responsive=False):
        keys=[k for k in keys if k]
        if not keys:return
        src=self.main.card_plan_windows.get(source_id) if source_id else None
        if src is not None and src is not self:
            # Capture real measurement data before moving cards out of their
            # source. A saved palette may be the only remaining data source.
            source_samples=src._all_samples(); self._embed_samples([source_samples[k] for k in keys if k in source_samples])
        elif source_id is None and not preembedded:
            # Library/workspace -> palette previously copied only opaque keys. Once
            # the source view changed or the app restarted those keys could no
            # longer resolve and the cards became “缺失色样”. File imports pass
            # preembedded=True because their real measurements were just attached;
            # do not immediately deserialize/query all 3,500 of them again.
            try:
                available=self._all_samples(); missing=[k for k in keys if k not in available]
                if missing:self._embed_samples(self.main._samples_by_keys_on_demand(missing,full=True))
            except Exception:pass
        self._push_undo()
        if source_id==self.card['card_id'] and len(keys)==1 and source_rows:
            self.receive_drop(keys[0],target,source_id,source_rows[0],copy_mode); return
        if source_id==self.card['card_id'] and source_rows and len(keys)>1:
            slots=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
            rows=sorted({r for r in source_rows if 0<=r<len(slots) and slots[r] in keys})
            moved=[slots[r] for r in rows]
            for row in rows:slots[row]=None
            pos=max(0,target)
            for key in moved:
                while pos<len(slots) and slots[pos] is not None:pos+=1
                if pos>=len(slots):slots.append(key)
                else:slots[pos]=key
                pos+=1
            self.grid.set_slots(slots); self.update_grid_size(); return
        # HF86 batch path: update a Python slot model first, then rebuild Qt once.
        # This removes the former per-colour _replace()->refresh-all loop that made
        # adding hundreds of samples feel frozen.
        slots=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        existing={str(k) for k in slots if k}; pos=max(0,min(int(target),len(slots))); inserted=[]; selected_rows=[]
        # HF121: previous code scanned the slot list once or twice for *every* new
        # colour (O(n²)).  A 3,500-colour import could therefore perform millions
        # of Python checks before Qt even started drawing.  Build the available
        # positions once and consume them in order: O(existing + imported).
        free_after=[i for i in range(pos,len(slots)) if not slots[i]]
        free_before=[i for i in range(0,pos) if not slots[i]]
        free_positions=iter(free_after+free_before)
        for key in keys:
            if key in existing:continue
            try:
                blank=next(free_positions)
                slots[blank]=key
            except StopIteration:
                blank=len(slots); slots.append(key)
            inserted.append(key); selected_rows.append(blank); existing.add(key)
        if inserted:
            # Selecting thousands of freshly imported rows is itself very expensive
            # in QListWidget and provides little value.  Small batches keep the old
            # behaviour; large batches focus only the first imported card.
            rows_to_select=selected_rows if len(selected_rows)<=64 else selected_rows[:1]
            self._rebuild_slots_bulk(slots,rows_to_select,sample_map=sample_map,responsive=responsive)
        else:self.update_count()
        if src is not None and src is not self and not copy_mode:
            for key in inserted:src.remove_key(key)
    def remove_key(self,key):
        self._push_undo()
        for i in range(self.grid.count()):
            if self.grid.item(i).data(Qt.UserRole)==key:self._replace(i,self._blank()); break
        self.update_count(); self.refresh_cell_widgets()
    def remove_selected(self):
        rows=sorted({self.grid.row(it) for it in self.grid.selectedItems() if self.grid.row(it)>=0})
        if not rows:return
        slots=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        blank_rows={r for r in rows if not slots[r]}; removed_cards=sum(bool(slots[r]) for r in rows)
        if removed_cards:
            detail=(f'将从当前方案移除 {removed_cards} 张色样' +
                    (('。CPX 空位属于版式坐标，不会因 Delete 而移位。' if self._is_cpx_palette() else (f'，并删除 {len(blank_rows)} 个空位' if blank_rows else '')))+
                    '\n\n原始色库/测量数据不会被删除；操作可用 Ctrl+Z 撤销。')
            if QMessageBox.question(self,'删除所选版位',detail,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        elif blank_rows and self._is_cpx_palette():
            self.grid.clearSelection(); self.refresh_cell_states()
            self.main.statusBar().showMessage('CPX 空位是源版式坐标：Delete 不会删除/左移空位。右侧完全空白列已自动隐藏。',4500)
            return
        self._push_undo()
        # Occupied cards are cleared in-place. For CPX, blank coordinates are
        # never removed from the linear list because that would shift every
        # following source position and corrupt the composition.
        for r in rows:
            if slots[r]:slots[r]=None
        if blank_rows and not self._is_cpx_palette():slots=[v for i,v in enumerate(slots) if i not in blank_rows]
        self._rebuild_slots_bulk(slots)
        self.grid.clearSelection(); self.update_count()
        removed_blank_count=0 if self._is_cpx_palette() else len(blank_rows)
        self.main.statusBar().showMessage(f'已移除色样 {removed_cards} 张；删除空位 {removed_blank_count} 个 · Ctrl+Z 可撤销',3500)
    def insert_blank(self,row):
        self._push_undo(); row=max(0,min(row,self.grid.count())); self.grid.insertItem(row,self._blank()); self.update_grid_size()
    def delete_blank(self,row):
        if row<0 or row>=self.grid.count() or self.grid.item(row).data(Qt.UserRole):return
        if self._is_cpx_palette():
            self.main.statusBar().showMessage('CPX 空位不会被物理删除，以免后续版位整体移位。右侧完全空列由显示层自动隐藏。',4500); return
        self._push_undo(); self.grid.takeItem(row); self.update_grid_size()
    def compact_blanks(self):
        if self._is_cpx_palette():
            QMessageBox.information(self,'CPX 版式保护','CPX 的内部空位属于源版式坐标，不能用“压缩空位”整体左移。\n如果需要紧凑排列，请使用“排序”；导出 CPX 时仍会恢复源 Fixed Layout。'); return
        self._push_undo(); keys=self.keys(); total=max(self.grid.count(),self.cols*max(1,self.rows)); self.grid.set_slots(keys+[None]*max(0,total-len(keys))); self.update_grid_size()
    def _capture_original_order(self):
        """Remember the pre-sort palette order for this editor session.

        A draft imported from QTX/CPX/Excel has no persisted ``card['layout']`` yet,
        so "restore saved order" could not restore anything.  HF75 keeps a
        session baseline independent of saving and of later sorting.
        """
        self._original_slots=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]

    def _invalidate_pending_sort(self):
        """Latest request wins; an older background sort may finish but may not repaint."""
        self._sort_generation += 1
        future=getattr(self,'_sort_future',None)
        if future is not None and not future.done():
            try:future.cancel()
            except Exception:pass
        self._sort_job=None; self._sort_future=None

    def _is_cpx_palette(self):
        settings=self.card.get('settings',{}) or {}
        return str(settings.get('source_format') or '').upper()=='CPX' or bool(settings.get('source_cpx'))

    def restore_original_order(self):
        samples=self._all_samples()
        previous=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        current=[str(k) for k in previous if k and str(k) in samples]
        if not current:return

        # Restore the exact session/source slot map.  CPX is special: its blank
        # positions are part of the source document layout, so trailing blanks
        # must NOT be trimmed.  QTX/Excel drafts may still collapse unused tail.
        source_slots=list(getattr(self,'_original_slots',[]) or [])
        if not source_slots:
            source_slots=[e.get('sample_key') if isinstance(e,dict) else e for e in self.card.get('layout',[]) or []]
        current_set=set(current); seen=set(); target=[]
        for raw in source_slots:
            key=str(raw) if raw else None
            if key and key in current_set and key not in seen:
                target.append(key); seen.add(key)
            else:
                target.append(None)
        extras=[k for k in current if k not in seen]

        settings=self.card.get('settings',{}) or {}
        if self._is_cpx_palette():
            # Restore the source *active view*: internal blank coordinates are
            # preserved, while completely empty tail rows remain folded.  The
            # full source CPX height is metadata for round-trip export, not a
            # requirement to display hundreds of empty cards.
            for extra in extras:
                try:blank_i=target.index(None); target[blank_i]=extra
                except ValueError:target.append(extra)
            display_cols=max(1,int(settings.get('cpx_active_cols') or self.cols or 1))
            display_rows=max(1,int(settings.get('cpx_original_display_rows') or math.ceil(max(1,len(target))/display_cols)))
            needed_rows=max(display_rows,math.ceil(max(1,len(target))/display_cols))
            visible_total=display_cols*needed_rows
            if len(target)<visible_total:target.extend([None]*(visible_total-len(target)))
            self.cols=display_cols; self.rows=needed_rows; self.auto_grid=False
        else:
            target.extend(extras)
            while target and target[-1] is None:target.pop()
            if not target:return
            self.rows=max(1,int(math.ceil(len(target)/max(1,self.cols))))

        self._invalidate_pending_sort(); self._push_undo(); self.sort_key='manual'; self.sort_desc=False
        self.grid.setUpdatesEnabled(False); self.grid.blockSignals(True)
        try:self.grid.set_slots(target)
        finally:self.grid.blockSignals(False); self.grid.setUpdatesEnabled(True)
        self.update_grid_size(); self.grid.clearSelection(); self._apply_plan_search(); self._sync_dirty_state()
        suffix=' · CPX 原版式已完整还原' if self._is_cpx_palette() else ''
        self.main.statusBar().showMessage(f'已恢复本次打开/导入时的原始槽位顺序 · {len(current)} 张色卡{suffix}',4500)

    def _apply_palette_sort_order(self, previous, ordered_keys, samples):
        """Reorder palette data in-place without destroying 300+ Qt cell widgets.

        HF111 CPX rule: opening/restoring a CPX preserves every source blank,
        but an explicit sort is treated as an intentional rearrangement.  For
        CPX only, sorted occupied cards are packed from the top-left and all
        remaining blanks are moved to the tail while the original CPX grid
        dimensions are retained.  This avoids sparse 'checkerboard' results
        without changing the file format or deleting source capacity.
        """
        if self._is_cpx_palette():
            valid=[str(k) for k in ordered_keys if k and str(k) in samples]
            unresolved=[str(k) for k in previous if k and str(k) not in samples]
            occupied=valid+unresolved
            settings=self.card.get('settings',{}) or {}
            # Sorting changes colour order only. Presentation width remains
            # the CPX active width; source Fixed Layout is still stored separately.
            self.cols=max(1,int(settings.get('cpx_active_cols') or self.cols or 1))
            self.rows=max(1,int(math.ceil(max(1,len(occupied))/self.cols)))
            total=self.cols*self.rows
            new_slots=occupied[:total] + [None]*max(0,total-len(occupied))
            # A CPX sort is an intentional rearrangement: show the compact active
            # region, but keep original source TileCount separately for export.
            self._rebuild_slots_bulk(new_slots)
            self._sync_dirty_state()
            source_cols=int(settings.get('cpx_original_cols') or self.cols); source_rows=int(settings.get('cpx_original_rows') or self.rows)
            self.main.statusBar().showMessage(
                f'CPX 排序后已紧凑显示：{len(occupied)} 色样 · {self.cols}列 × {self.rows}行；源 CPX 仍保留 {source_cols}×{source_rows} 固定版式，恢复导入顺序可还原原卡位。',5500)
            return
        else:
            order=iter(ordered_keys)
            new_slots=[]
            for key in previous:
                if not key:
                    new_slots.append(None)
                elif key not in samples:
                    # Preserve unresolved legacy references in their original slots.
                    new_slots.append(key)
                else:
                    new_slots.append(next(order,key))
        self.grid.setUpdatesEnabled(False); self.grid.blockSignals(True)
        try:
            self.grid.set_slots(new_slots); self.grid.clearSelection(); self._palette_paint_cache={}
            self.refresh_cell_widgets(); self.update_count(); self._apply_plan_search()
        finally:
            self.grid.blockSignals(False); self.grid.setUpdatesEnabled(True); self.grid.viewport().update()
        self._sync_dirty_state()

    def sort_items(self,mode):
        """LABCH scalar sorting: L*, a*, b*, C*, h° stay together as one family.

        This is deliberately a coordinate sort, not an appearance classifier.
        h° is the ordinary CIELAB polar angle atan2(b*, a*) in [0, 360).
        Color Difference and Running ΔE are separate tools below.
        """
        if mode=='manual':
            self.restore_original_order(); return
        if mode not in {'L','a','b','C','h'}:
            self.main.statusBar().showMessage('LABCH 排序支持 L* / a* / b* / C* / h°',3500)
            return
        samples=self._all_samples()
        previous=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        selected=[samples[k] for k in previous if k in samples]
        if not selected:return
        if self.sort_key==mode:
            self.sort_desc=not self.sort_desc
        else:
            self.sort_key=mode
            # Hue convention is naturally read 0° -> 360°; the four scalar axes
            # keep the established first-click descending behaviour.
            self.sort_desc=False if mode=='h' else True
        self._invalidate_pending_sort(); self._push_undo()
        if mode in {'L','a','b'}:
            idx={'L':0,'a':1,'b':2}[mode]
            selected.sort(key=lambda x:self.main.sample_lab(x)[idx],reverse=self.sort_desc)
        elif mode=='C':
            selected.sort(key=lambda x:math.hypot(self.main.sample_lab(x)[1],self.main.sample_lab(x)[2]),reverse=self.sort_desc)
        else:
            # HF110: h° is a circular variable, not a linear 0..360 number line.
            # Choose the cut at the largest reliable hue gap so 359° and 1°
            # remain adjacent when they belong to the same red cluster.
            chromatic=[]; neutral=[]; all_labs=[]
            for stable_i,sm in enumerate(selected):
                lab=tuple(float(x) for x in self.main.sample_lab(sm)); all_labs.append(lab)
                L,C,h=lab_to_lch(lab)
                rec=(sm,stable_i,L,C,h)
                if C <= neutral_limit(L): neutral.append(rec)
                else: chromatic.append(rec)
            reliable_hues=reliable_hues_for_cut(all_labs)
            cut=circular_hue_cut(reliable_hues if reliable_hues else [r[4] for r in chromatic])
            chromatic.sort(key=lambda r:(circular_hue_position(r[4],cut), -r[2], r[3], r[1]))
            neutral.sort(key=lambda r:(-r[2], r[3], r[1]))
            result=[r[0] for r in chromatic] + [r[0] for r in neutral]
            self._last_hue_cut=float(cut)
            selected=list(reversed(result)) if self.sort_desc else result
        self._apply_palette_sort_order(previous,[sample_key(sm) for sm in selected],samples)
        direction='降序' if self.sort_desc else '升序'
        label={'L':'L* 明度','a':'a* 红绿轴','b':'b* 黄蓝轴','C':'C* 彩度','h':f"h° 色相角（环形切口 {getattr(self,'_last_hue_cut',0.0):.1f}°；中性色置后）"}[mode]
        self.main.statusBar().showMessage(f'{label} {direction}完成 · {len(selected)} 张色卡',3500)

    def _palette_formula_spec(self):
        key=str(getattr(self.main,'active_formula','delta_e00') or 'delta_e00')
        label=FORMULA_LABELS.get(key,key)
        if key=='delta_e76':return label,'CIE 1976',{}
        if key=='delta_e94':return label,'CIE 1994',{'textiles':True}
        if key=='cmc21':return label,'CMC',{'l':2,'c':1}
        return label,'CIE 2000',{}

    def _selected_sort_reference(self):
        samples=self._all_samples(); found=[]
        for item in self.grid.selectedItems():
            key=item.data(Qt.UserRole)
            if key and key in samples:found.append(samples[key])
        # Deduplicate repeated Qt selection echoes while preserving selection order.
        unique=[]; seen=set()
        for sm in found:
            k=sample_key(sm)
            if k in seen:continue
            seen.add(k); unique.append(sm)
        if len(unique)!=1:
            QMessageBox.information(self,'选择基准色','请先在色卡中只选中 1 张色样。\n\n基准色差：该色作为基准色，只按到该色的 ΔE 距离排序。\n连续色差现在为自动全局优化，不需要选择起始色。')
            return None
        return unique[0]

    def sort_by_color_difference(self):
        """Sort all occupied cards by ΔE to one explicitly selected reference."""
        ref=self._selected_sort_reference()
        if ref is None:return
        samples=self._all_samples()
        previous=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        ordered=[samples[k] for k in previous if k in samples]
        if not ordered:return
        formula_label,method,kwargs=self._palette_formula_spec(); ref_lab=tuple(self.main.sample_lab(ref))
        self._invalidate_pending_sort(); self._push_undo(); self.sort_key='color_difference'; self.sort_desc=False
        rank={sample_key(sm):i for i,sm in enumerate(ordered)}
        distances={}
        for sm in ordered:
            k=sample_key(sm)
            distances[k]=float(delta_e(ref_lab,tuple(self.main.sample_lab(sm)),method,**kwargs))
        # The explicitly selected reference must occupy the first colour slot.
        # Identical duplicate measurements can also have ΔE=0; letting the
        # original-row tie break win used to make a different duplicate appear
        # before the selected reference, which looked like a wrong sort.
        ref_key=sample_key(ref)
        others=[sm for sm in ordered if sample_key(sm)!=ref_key]
        others.sort(key=lambda sm:strict_reference_distance_key(distances[sample_key(sm)],rank.get(sample_key(sm),10**9),sample_key(sm)))
        ordered=[ref]+others
        # HF106: self-audit the exact reference-distance contract.  This mode
        # must be monotonically non-decreasing by ΔE from the selected reference;
        # visual adjacency is deliberately handled by Running ΔE instead.
        audit_values=[float(distances.get(sample_key(sm), 0.0)) for sm in ordered]
        audit_ok=all(audit_values[i] <= audit_values[i+1] + 1e-12 for i in range(len(audit_values)-1))
        if not audit_ok:
            QMessageBox.warning(self,'Color Difference','基准色差排序自检失败：ΔE 序列不是单调递增。\n本次排序已取消，请保留当前文件用于诊断。')
            return
        # Keep the exact radial ΔE values for tooltips so the user can verify
        # that this mode is numerically monotonic even when far-away colours
        # belong to very different hue families.  Running ΔE remains the
        # separate visual-continuity tool.
        self._last_reference_delta=distances
        self._last_reference_delta_label=formula_label
        self._last_reference_name=ref.display_name
        self._apply_palette_sort_order(previous,[sample_key(sm) for sm in ordered],samples)
        last=max(distances.values()) if distances else 0.0
        self.main.statusBar().showMessage(f'基准色差距离完成 · 基准色：{ref.display_name} · {formula_label} · 基准ΔE单调自检 PASS · 0.00→{last:.2f} · {len(ordered)} 张',6000)

    def sort_by_running_delta(self):
        """Globally improved Running ΔE open path (HF110).

        No selected start is required.  The worker constructs multiple
        deterministic nearest-neighbour candidates, then applies bottleneck-
        aware 2-opt and chooses by max adjacent ΔE -> P95 -> total ΔE.
        """
        samples=self._all_samples()
        previous=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        ordered=[samples[k] for k in previous if k in samples]
        if not ordered:return
        if len(ordered)>1500:
            QMessageBox.information(self,'连续色差','当前方案超过 1500 个色样。\n\n全局连续色差需要构建成对色差矩阵并做 2-opt 优化；请先筛选/拆分后再使用。综合色坐标排序与色彩空间图谱不受此限制。')
            return
        keys=[sample_key(sm) for sm in ordered]
        labs=[tuple(float(x) for x in self.main.sample_lab(sm)) for sm in ordered]
        formula_label,method,kwargs=self._palette_formula_spec()
        directional_formula=method in {'CIE 1994','CMC'}
        self._invalidate_pending_sort(); self._push_undo(); self.sort_key='running_delta'; self.sort_desc=False
        self._sort_generation+=1; token=self._sort_generation

        def worker():
            def pair_distance(a,b):
                return float(delta_e(a,b,method,**kwargs))
            # Scale the number of starts/passes so normal palettes get stronger
            # optimisation while large palettes remain practical in background.
            n=len(labs)
            max_starts=12 if n<=500 else (8 if n<=900 else 6)
            opt_candidates=3 if n<=700 else 2
            passes=20 if n<=500 else (10 if n<=900 else 6)
            result=optimise_global_open_path(
                labs,pair_distance,directional_formula=directional_formula,
                max_starts=max_starts,optimise_candidates=opt_candidates,
                two_opt_passes=passes,
            )
            path=[keys[i] for i in result.order]
            return {
                'path':path,
                'initial':result.initial_metrics,
                'final':result.final_metrics,
                'starts':len(result.candidate_starts),
                'moves':result.two_opt_moves,
            }

        future=self.main._card_sort_executor.submit(worker); self._sort_future=future
        self._sort_job={'token':token,'previous':previous,'samples':samples,'label':formula_label}
        self.main.statusBar().showMessage(f'连续色差全局优化中 · {formula_label} · 多起点 + 2-opt…',0)
        QTimer.singleShot(40,lambda:self._poll_running_delta(token,future))

    def _poll_running_delta(self,token,future):
        if token!=self._sort_generation:return
        if not future.done():
            QTimer.singleShot(40,lambda:self._poll_running_delta(token,future));return
        job=self._sort_job or {}
        try:result=future.result()
        except Exception as exc:
            self._sort_future=None; self._sort_job=None
            QMessageBox.warning(self,'连续色差',f'连续色差全局优化失败：\n{exc}')
            return
        if token!=self._sort_generation:return
        previous=job.get('previous',[]); samples=job.get('samples',{})
        path=result.get('path',[])
        self._apply_palette_sort_order(previous,path,samples)
        self._sort_future=None; self._sort_job=None
        ini=result.get('initial'); fin=result.get('final')
        if ini is not None and fin is not None:
            msg=(f"连续色差完成 · {job.get('label','')} · {len(path)} 张 · "
                 f"最大ΔE {ini.maximum:.2f}→{fin.maximum:.2f} · "
                 f"P95 {ini.p95:.2f}→{fin.p95:.2f} · "
                 f"总ΔE {ini.total:.1f}→{fin.total:.1f} · "
                 f"{result.get('starts',0)} 起点 / {result.get('moves',0)} 次2-opt")
        else:
            msg=f"连续色差完成 · {job.get('label','')} · {len(path)} 张"
        self.main.statusBar().showMessage(msg,8000)

    def reverse_current_order(self):
        samples=self._all_samples()
        previous=[self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())]
        occupied=[str(k) for k in previous if k and str(k) in samples]
        if not occupied:return
        self._invalidate_pending_sort(); self._push_undo()
        self._apply_palette_sort_order(previous,list(reversed(occupied)),samples)
        self.sort_desc=not bool(self.sort_desc)
        self.main.statusBar().showMessage(f'已反向当前色样顺序 · {len(occupied)} 张',3500)

    def add_from_library(self):
        already=set(self.keys()); dlg=AddSamplesDialog(self.main,already,self); dlg.setWindowTitle(f'从色库加入色样 · {self.card["name"]}')
        if dlg.exec()!=QDialog.Accepted:return
        samples=dlg.chosen()
        if not samples:return
        self.receive_drop_many([sample_key(x) for x in samples],0,copy_mode=True); self._capture_original_order(); self.main.statusBar().showMessage(f'已加入 {len(samples)} 个色样；按 Ctrl+S 保存方案',3500)
    def show_context_menu(self,pos):
        item=self.grid.itemAt(pos); menu=QMenu(self)
        selected=list(self.grid.selectedItems())
        if item is not None and item not in selected:
            self.grid.clearSelection(); item.setSelected(True); selected=[item]
        selected_cards=[it for it in selected if it.data(Qt.UserRole)]
        selected_blanks=[it for it in selected if not it.data(Qt.UserRole)]
        samples=[self._all_samples().get(it.data(Qt.UserRole)) for it in selected_cards]; samples=[x for x in samples if x]
        row=self.grid.row(item) if item else self.grid.count()
        if item is None:
            self.main.color_card_workspace_menu(self.grid.mapToGlobal(pos),already_global=True)
            return
        details=menu.addAction('查看测色明细') if len(samples)==1 else None
        addwb=menu.addAction('加入比色工作台…') if len(samples)==1 else None
        add_library=menu.addAction('从色库添加色样…') if not item.data(Qt.UserRole) else None
        insert_before=menu.addAction('在前方插入空位')
        insert_after=menu.addAction('在后方插入空位')
        remove=menu.addAction(f'删除所选版位 / 色样（{len(selected)}）    Delete') if selected else None
        if selected_blanks:
            menu.addAction(f'已选空位：{len(selected_blanks)} 个').setEnabled(False)
        menu.addSeparator()
        copy_a=menu.addAction(f'复制 {len(selected_cards)} 张色卡    Ctrl+C') if selected_cards else None
        cut_a=menu.addAction(f'剪切 {len(selected_cards)} 张色卡    Ctrl+X') if selected_cards else None
        paste_a=menu.addAction('粘贴    Ctrl+V'); paste_a.setEnabled(bool(self._clipboard_keys or QApplication.clipboard().text().strip()))
        select_all=menu.addAction('全选当前方案版位    Ctrl+A')
        select_blanks=menu.addAction('仅选择全部空位')
        available=self._all_samples()
        recovery=menu.addAction('从原始 QTX 恢复缺失色样…') if any(str(k) not in available for k in self.keys()) else None
        replace_missing=menu.addAction('为此缺失卡指定现有色样…') if item and item.data(Qt.UserRole) and str(item.data(Qt.UserRole)) not in available else None
        clear_sel=menu.addAction('取消选择    Esc')
        analysis=menu.addMenu('分析与显示') if samples else None
        view2d=analysis.addAction('查看 2D 色彩空间') if analysis else None
        view3d=analysis.addAction('查看 3D 色彩空间    Ctrl+Alt+3') if analysis else None
        fluor_menu=analysis.addMenu('荧光标记') if analysis else None
        fluor_on=fluor_menu.addAction('标记为荧光色（显示折角）') if fluor_menu else None
        fluor_off=fluor_menu.addAction('标记为普通色（取消折角）') if fluor_menu else None
        fluor_auto=fluor_menu.addAction('恢复自动判断') if fluor_menu else None
        chosen=menu.exec(self.grid.mapToGlobal(pos))
        if copy_a is not None and chosen==copy_a:self.trigger_command('copy'); return
        if cut_a is not None and chosen==cut_a:self.trigger_command('cut'); return
        if chosen==paste_a:self.trigger_command('paste'); return
        if add_library is not None and chosen==add_library:self.add_from_library(); return
        if chosen==insert_before:self.insert_blank(row); return
        if chosen==insert_after:self.insert_blank(row+1); return
        if remove is not None and chosen==remove:self.trigger_command('remove'); return
        if chosen==select_all:self.trigger_command('select_all'); return
        if chosen==select_blanks:self.select_blank_slots(); return
        if recovery is not None and chosen==recovery:self.recover_from_qtx();return
        if replace_missing is not None and chosen==replace_missing:self.replace_missing_with_library_sample(row);return
        if chosen==clear_sel:self.grid.clearSelection(); return
        if view2d is not None and chosen==view2d:self.main.open_lab2d_window(samples); return
        if view3d is not None and chosen==view3d:self.trigger_command('view3d'); return
        if fluor_on is not None and chosen==fluor_on:self.set_fluorescent_override([str(it.data(Qt.UserRole)) for it in selected_cards],True); return
        if fluor_off is not None and chosen==fluor_off:self.set_fluorescent_override([str(it.data(Qt.UserRole)) for it in selected_cards],False); return
        if fluor_auto is not None and chosen==fluor_auto:self.set_fluorescent_override([str(it.data(Qt.UserRole)) for it in selected_cards],None); return
        if details is not None and chosen==details:self.main.show_sample_details_dialog(samples[0]); return
        if addwb is not None and chosen==addwb:self.main._menu_add_sample_to_workbench(samples[0],self.grid.mapToGlobal(pos)); return

    def set_auto_grid_columns(self, columns):
        if self._is_cpx_palette():
            QMessageBox.information(self,'CPX 源版式','CPX 的列数由源文件 Fixed Layout 定义。为保证再次导出 CPX 不破坏原格式，这里不允许改变列数。')
            return
        columns=max(1,int(columns))
        if self.auto_grid and self.cols==columns:return
        self._push_undo(); self.auto_grid=True; self.cols=columns
        self.rows=max(1,int(math.ceil(max(1,self._last_occupied_index()+1)/columns))) if self.keys() else 1
        self.update_grid_size()
        self.main.statusBar().showMessage(f'布局已设为 {columns} 列 · 自动扩展行；空槽仍可继续拖入颜色',3500)

    def configure_grid(self):
        if self._is_cpx_palette():
            QMessageBox.information(self,'CPX 源版式','CPX 的固定网格由源文件定义。当前界面只折叠尾部全空行，不修改源 TileCount。')
            return
        dlg=QDialog(self); dlg.setWindowTitle('手动设置网格')
        box=QVBoxLayout(dlg)
        tip=QLabel('设置色卡的固定列数和行数。屏幕显示大小请使用工具栏“显示”缩放；缩放不会改变色卡版式。')
        tip.setWordWrap(True); box.addWidget(tip)
        form=QGridLayout(); box.addLayout(form)
        cols=QSpinBox(); cols.setRange(1,30); cols.setValue(max(1,self.cols))
        rows=QSpinBox(); rows.setRange(1,60); rows.setValue(max(1,self.rows or 1))
        form.addWidget(QLabel('列数'),0,0); form.addWidget(cols,0,1)
        form.addWidget(QLabel('行数'),1,0); form.addWidget(rows,1,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); box.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        if cols.value()*rows.value()<len(self.keys()):
            QMessageBox.warning(self,'网格太小',f'当前已有 {len(self.keys())} 张色卡，至少需要 {len(self.keys())} 个槽位。'); return
        self._push_undo(); self.auto_grid=False; self.cols,self.rows=cols.value(),rows.value(); self.update_grid_size()
    def save(self):
        self.card['layout']=[{'sample_key':self.grid.item(i).data(Qt.UserRole)} for i in range(self.grid.count())]
        self.card['columns_count']=self.cols; settings=dict(self.card.get('settings',{}) or {}); settings.update({'grid_cols':self.cols,'grid_rows':self.rows,'auto_grid':self.auto_grid,'cell_w':self.cell_w,'cell_h':self.cell_h,'sort':self.sort_key,'visual_style':'color-studio-v4-gamutmap','fluorescent_force_on':sorted(self.fluorescent_force_on),'fluorescent_force_off':sorted(self.fluorescent_force_off)})
        all_samples=self._all_samples(); seen=set(); embedded=[]
        for key in self.keys():
            if key in seen or key not in all_samples:continue
            seen.add(key); embedded.append(self.main._serialize_sample(all_samples[key]))
        # Keep snapshots not currently linked to a visible slot: they may be
        # needed to repair an older scheme after its source file moves.
        for data in settings.get('embedded_samples',[]) or []:
            try:key=sample_key(self.main._deserialize_sample(data))
            except (KeyError,TypeError,ValueError):continue
            if key not in seen:embedded.append(data);seen.add(key)
        settings['embedded_samples']=embedded
        self.card['settings']=settings; self.invalidate_sample_cache()
        self.card=self.main.store.save_color_card(self.card)
        # save_color_card preserves extra transient dict keys; once persisted this
        # window is no longer a draft, so clear the UI-only marker explicitly.
        self.card.pop('_draft',None)
        self._saved_fingerprint=self._state_fingerprint(); self._sync_dirty_state()
        sub=getattr(self.main,'card_subwindows',{}).get(self.card.get('card_id'))
        if sub is not None:sub.setWindowTitle(self.card['name'])
        self.main.refresh_color_card_list(); scope='全部用户' if str(self.card.get('visibility') or 'private')=='organization' else '仅自己'; self.main.statusBar().showMessage(f"已保存色卡方案：{self.card['name']} · 所有者：{self.main.current_user.display_name or self.main.current_user.username} · {scope}（源 CPX/源文件不会被改写）",4000)
    def _export_slot_snapshot(self, purpose: str):
        """Resolve every occupied palette slot before an export.

        Never silently export fewer colours than the editor shows.  Blank slots
        remain valid for CPX/Excel layout; unresolved occupied slots stop export
        with an actionable message.
        """
        samples=self._all_samples(); ordered=[]; missing=[]; occupied=0
        for i in range(self.grid.count()):
            item=self.grid.item(i); key=item.data(Qt.UserRole) if item is not None else None
            if not key:
                ordered.append(None); continue
            occupied += 1
            sm=samples.get(str(key)); ordered.append(sm)
            if sm is None:
                missing.append((i+1,str(key)))
        resolved=sum(1 for x in ordered if x is not None)
        if missing or resolved!=occupied:
            preview='\n'.join(f'槽位 {row}: {key}' for row,key in missing[:8])
            QMessageBox.warning(self,purpose,
                f'数据完整性检查未通过：界面有 {occupied} 张色卡，但只解析到 {resolved} 张。\n'
                f'为避免静默丢失，本次导出已取消。' + (f'\n\n缺失示例：\n{preview}' if preview else ''))
            return None
        return ordered

    def _visual_export_options(self, fmt):
        dlg=QDialog(self); dlg.setWindowTitle('导出色卡 · '+fmt.upper())
        lay=QVBoxLayout(dlg)
        if fmt in {'png','jpg'}:
            tip=QLabel('图片支持“单张完整图（不分页）”和“分页高清图”。单张完整图会把当前方案全部颜色写入一个文件，并自动压缩纵向密度以避免超大画布保存失败。')
        else:
            tip=QLabel('按当前色卡顺序分页导出。空槽可保留为浅色虚线占位，不会重新排序颜色。')
        tip.setWordWrap(True); lay.addWidget(tip)
        form=QGridLayout(); lay.addLayout(form)
        cols=QSpinBox(); cols.setRange(1,20); cols.setValue(max(1,self.cols))
        if self._is_cpx_palette():
            cols.setEnabled(False); cols.setToolTip('CPX 展示导出保持当前有效区列数；源 Fixed Layout 不会被修改。')
        image_mode=None
        if fmt in {'png','jpg'}:
            image_mode=QComboBox(); image_mode.addItems(['单张完整图（不分页）','分页高清图'])
            image_mode.setCurrentIndex(0)
            form.addWidget(QLabel('图片方式'),0,0); form.addWidget(image_mode,0,1)
            form.addWidget(QLabel('每行列数'),1,0); form.addWidget(cols,1,1)
            rows=QSpinBox(); rows.setRange(1,30); rows.setValue(max(1,min(int(self.rows or 1),8)))
            form.addWidget(QLabel('分页时每页行数'),2,0); form.addWidget(rows,2,1)
            orientation=QComboBox(); orientation.addItems(['横向','纵向']); orientation.setCurrentIndex(0)
            form.addWidget(QLabel('分页页面方向'),3,0); form.addWidget(orientation,3,1)
            def sync_mode(*_):
                paged=image_mode.currentIndex()==1
                rows.setEnabled(paged); orientation.setEnabled(paged)
            image_mode.currentIndexChanged.connect(sync_mode); sync_mode()
        else:
            rows=QSpinBox(); rows.setRange(1,20); rows.setValue(max(1,min(int(self.rows or 1),8)))
            orientation=QComboBox(); orientation.addItems(['横向','纵向']); orientation.setCurrentIndex(0)
            form.addWidget(QLabel('每行列数'),0,0); form.addWidget(cols,0,1)
            form.addWidget(QLabel('每页行数'),1,0); form.addWidget(rows,1,1)
            form.addWidget(QLabel('页面方向'),2,0); form.addWidget(orientation,2,1)
        show_name=QCheckBox('显示色号 / 名称'); show_name.setChecked(True)
        show_lab=QCheckBox('显示 L* a* b*'); show_lab.setChecked(True)
        show_title=QCheckBox('显示方案名称和页码'); show_title.setChecked(True)
        show_blank_guides=QCheckBox('显示空槽虚线（便于看出版位）'); show_blank_guides.setChecked(False if self._is_cpx_palette() else True)
        box=QVBoxLayout(); box.addWidget(show_name); box.addWidget(show_lab); box.addWidget(show_title); box.addWidget(show_blank_guides); lay.addLayout(box)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); lay.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:return None
        return {'cols':cols.value(),'rows_per_page':rows.value(),'landscape':orientation.currentText()=='横向',
                'single_image':bool(fmt in {'png','jpg'} and image_mode is not None and image_mode.currentIndex()==0),
                'show_name':show_name.isChecked(),'show_lab':show_lab.isChecked(),'show_title':show_title.isChecked(),
                'show_blank_guides':show_blank_guides.isChecked()}

    def _render_palette_export_page(self, slots, options, page_index=0, page_count=1, target_size=(2480,1754)):
        """Render one presentation page with a fixed page aspect ratio.

        Previous image export derived height from row count, so 6×20/30 layouts
        became extremely tall strips.  A page-sized canvas keeps PNG/JPG/PDF
        visually consistent; large palettes are split into more pages instead of
        creating unreadable skyscraper images.
        """
        cols=max(1,int(options.get('cols') or self.cols)); rows=max(1,int(options.get('rows_per_page') or 8))
        if isinstance(target_size,QSize):
            target_width,target_height=max(1,target_size.width()),max(1,target_size.height())
        elif isinstance(target_size,(tuple,list)) and len(target_size)>=2:
            target_width,target_height=max(1,int(target_size[0])),max(1,int(target_size[1]))
        else:
            target_width=max(1,int(target_size or 2200)); target_height=int(round(target_width*(1754/2480)))
        margin=72; gap=18; header=92 if options.get('show_title',True) else 34; footer=30
        grid_top=header; grid_bottom=target_height-footer
        avail_w=max(1,target_width-margin*2-gap*(cols-1)); avail_h=max(1,grid_bottom-grid_top-gap*(rows-1))
        card_w=max(54,int(avail_w/cols)); card_h=max(54,int(avail_h/rows))
        # Text sizes adapt to dense pages but stay large enough for a customer-facing image.
        name_pt=max(6.0,min(10.0,card_w/28.0,card_h/12.0)); lab_pt=max(5.5,name_pt-1.0)
        name_h=(max(18,int(card_h*.16)) if options.get('show_name',True) else 0)
        lab_h=(max(17,int(card_h*.14)) if options.get('show_lab',True) else 0)
        swatch_h=max(28,card_h-name_h-lab_h-12)
        image=QImage(target_width,target_height,QImage.Format_ARGB32_Premultiplied); image.fill(QColor('#FFFFFF'))
        p=QPainter(image); p.setRenderHint(QPainter.Antialiasing,True); p.setRenderHint(QPainter.TextAntialiasing,True)
        if options.get('show_title',True):
            f=QFont(); f.setPointSize(20); f.setBold(True); p.setFont(f); p.setPen(QColor('#172033'))
            p.drawText(QRect(margin,22,target_width-margin*2,38),Qt.AlignLeft|Qt.AlignVCenter,str(self.card.get('name','色卡方案')))
            f.setPointSize(9); f.setBold(False); p.setFont(f); p.setPen(QColor('#64748B'))
            p.drawText(QRect(margin,58,target_width-margin*2,24),Qt.AlignLeft|Qt.AlignVCenter,f'{len(self.keys())} 色样 · {cols} 列 · 第 {page_index+1}/{page_count} 页')
        normal_font=QFont(); normal_font.setPointSizeF(lab_pt); bold_font=QFont(); bold_font.setPointSizeF(name_pt); bold_font.setBold(True)
        for pos in range(rows*cols):
            r=pos//cols; c=pos%cols; x=margin+c*(card_w+gap); y=grid_top+r*(card_h+gap)
            sm=slots[pos] if pos<len(slots) else None
            if sm is None:
                if options.get('show_blank_guides',True):
                    p.fillRect(QRect(x,y,card_w,swatch_h),QColor('#FBFCFE'))
                    pen=QPen(QColor('#C7D2E0'),2,Qt.DashLine); p.setPen(pen); p.drawRoundedRect(QRectF(x,y,card_w,swatch_h),8,8)
                continue
            lab=self.main.sample_lab(sm); key=sample_key(sm)
            color=(fluorescent_lab_to_qcolor(lab) if key in self.fluorescent_force_on else (
                lab_to_qcolor(lab) if key in self.fluorescent_force_off else sample_display_qcolor(sm,lab)))
            p.setPen(QPen(QColor(0,0,0,28),1)); p.setBrush(color); p.drawRoundedRect(QRectF(x,y,card_w,swatch_h),8,8)
            if self._is_fluorescent_key(key,sm):
                fold=min(22.0,max(10.0,swatch_h*.16)); tri=QPainterPath(); tri.moveTo(x+card_w-fold,y); tri.lineTo(x+card_w,y); tri.lineTo(x+card_w,y+fold); tri.closeSubpath()
                p.fillPath(tri,QColor('#F8FAFD')); p.setPen(QPen(QColor('#CFD8E6'),1)); p.drawLine(QPointF(x+card_w-fold,y),QPointF(x+card_w,y+fold))
            ty=y+swatch_h+3
            if options.get('show_name',True):
                p.setFont(bold_font); p.setPen(QColor('#1F2937')); fm=p.fontMetrics(); name=fm.elidedText(sm.display_name,Qt.ElideRight,max(12,card_w-8))
                p.drawText(QRect(x+3,ty,card_w-6,name_h),Qt.AlignLeft|Qt.AlignVCenter,name); ty+=name_h
            if options.get('show_lab',True):
                p.setFont(normal_font); p.setPen(QColor('#64748B')); fm=p.fontMetrics()
                txt=f'L* {lab[0]:.2f}  a* {lab[1]:+.2f}  b* {lab[2]:+.2f}'
                p.drawText(QRect(x+3,ty,card_w-6,lab_h),Qt.AlignLeft|Qt.AlignVCenter,fm.elidedText(txt,Qt.ElideRight,max(12,card_w-8)))
        p.end(); return image

    def _render_palette_complete_image(self, slots, options):
        """Render the whole palette into ONE image without pagination.

        This restores the old one-file workflow, but avoids the unbounded 100k+
        pixel canvas that caused QImageWriter failures.  The logical column count
        is preserved; for very large palettes the row height is compressed to a
        bounded ~45 MP canvas.  The result is intended as a zoomable overview;
        users who need large readable labels can still choose paged high-res mode.
        """
        cols=max(1,int(options.get('cols') or self.cols)); total_rows=max(1,int(math.ceil(max(1,len(slots))/cols)))
        # Keep enough horizontal resolution for colour blocks, but cap the total
        # raster allocation so Coloro 3500 remains practical on normal Windows PCs.
        target_width=max(1400,min(2200,180+cols*210))
        max_pixels=45_000_000
        max_height=min(30000,max(2400,int(max_pixels//max(1,target_width))))
        margin=36; gap_x=8; gap_y=4; header=72 if options.get('show_title',True) else 24; footer=20
        card_w=max(42,int((target_width-margin*2-gap_x*(cols-1))/cols))
        usable_h=max(1,max_height-header-footer-gap_y*(total_rows-1))
        row_h=max(24,int(usable_h/total_rows))
        # Use a comfortable row height when the palette is small; large palettes
        # automatically compress but always stay one file.
        natural_row=max(58,min(96,int(card_w*.42)))
        row_h=min(natural_row,row_h)
        target_height=header+footer+total_rows*row_h+max(0,total_rows-1)*gap_y
        if target_height<400:target_height=400
        image=QImage(target_width,target_height,QImage.Format_RGB32); image.fill(QColor('#FFFFFF'))
        if image.isNull():return image
        p=QPainter(image); p.setRenderHint(QPainter.Antialiasing,True); p.setRenderHint(QPainter.TextAntialiasing,True)
        if options.get('show_title',True):
            f=QFont(); f.setPointSize(max(10,min(18,target_width/150))); f.setBold(True); p.setFont(f); p.setPen(QColor('#172033'))
            p.drawText(QRect(margin,12,target_width-margin*2,30),Qt.AlignLeft|Qt.AlignVCenter,str(self.card.get('name','色卡方案')))
            f.setPointSize(max(7,min(9,target_width/260))); f.setBold(False); p.setFont(f); p.setPen(QColor('#64748B'))
            p.drawText(QRect(margin,42,target_width-margin*2,20),Qt.AlignLeft|Qt.AlignVCenter,f'{len(self.keys())} 色样 · {cols} 列 · 单张完整图')
        # Vertical allocation is dynamic: prioritise swatch area, then name/Lab.
        want_name=bool(options.get('show_name',True)); want_lab=bool(options.get('show_lab',True))
        name_h=max(0,int(row_h*.20)) if want_name else 0; lab_h=max(0,int(row_h*.18)) if want_lab else 0
        if row_h<46:
            name_h=max(0,9 if want_name else 0); lab_h=max(0,8 if want_lab else 0)
        swatch_h=max(12,row_h-name_h-lab_h-3)
        name_font=QFont(); name_font.setBold(True); name_font.setPointSizeF(max(4.5,min(8.0,row_h/9.0)))
        lab_font=QFont(); lab_font.setPointSizeF(max(4.2,min(7.0,row_h/10.0)))
        for pos in range(total_rows*cols):
            r=pos//cols; c=pos%cols; x=margin+c*(card_w+gap_x); y=header+r*(row_h+gap_y)
            sm=slots[pos] if pos<len(slots) else None
            if sm is None:
                if options.get('show_blank_guides',True):
                    p.fillRect(QRect(x,y,card_w,swatch_h),QColor('#FBFCFE'));p.setPen(QPen(QColor('#C7D2E0'),1,Qt.DashLine));p.drawRect(QRect(x,y,card_w,swatch_h))
                continue
            lab=self.main.sample_lab(sm); key=sample_key(sm)
            color=(fluorescent_lab_to_qcolor(lab) if key in self.fluorescent_force_on else (lab_to_qcolor(lab) if key in self.fluorescent_force_off else sample_display_qcolor(sm,lab)))
            p.setPen(QPen(QColor(0,0,0,24),1));p.setBrush(color);p.drawRoundedRect(QRectF(x,y,card_w,swatch_h),4,4)
            if self._is_fluorescent_key(key,sm):
                fold=min(14.0,max(6.0,swatch_h*.18));tri=QPainterPath();tri.moveTo(x+card_w-fold,y);tri.lineTo(x+card_w,y);tri.lineTo(x+card_w,y+fold);tri.closeSubpath();p.fillPath(tri,QColor('#F8FAFD'))
            ty=y+swatch_h+1
            if want_name and name_h>0:
                p.setFont(name_font);p.setPen(QColor('#1F2937'));fm=p.fontMetrics();txt=fm.elidedText(sm.display_name,Qt.ElideRight,max(8,card_w-4));p.drawText(QRect(x+2,ty,card_w-4,name_h),Qt.AlignLeft|Qt.AlignVCenter,txt);ty+=name_h
            if want_lab and lab_h>0:
                p.setFont(lab_font);p.setPen(QColor('#64748B'));fm=p.fontMetrics();txt=f'L* {lab[0]:.2f} a* {lab[1]:+.2f} b* {lab[2]:+.2f}';p.drawText(QRect(x+2,ty,card_w-4,lab_h),Qt.AlignLeft|Qt.AlignVCenter,fm.elidedText(txt,Qt.ElideRight,max(8,card_w-4)))
        p.end();return image

    def _notify_export_success(self, kind, paths, *, note=''):
        paths=[Path(x) for x in (paths or [])]
        if not paths:return
        if len(paths)==1:
            detail=f'文件：{paths[0].name}\n保存位置：{paths[0].parent}'
        else:
            detail=f'文件数量：{len(paths)}\n保存位置：{paths[0].parent}\n文件范围：{paths[0].name} ～ {paths[-1].name}'
        if note:detail+=f'\n\n{note}'
        QMessageBox.information(self,f'导出 {kind}',f'{kind} 导出成功。\n\n{detail}')

    def export_palette_visual(self, fmt):
        if not self.main.can_use('export'):return
        fmt='jpg' if fmt=='jpeg' else fmt
        options=self._visual_export_options(fmt)
        if not options:return
        slots=self._export_slot_snapshot('导出色卡')
        if slots is None:return
        if self._is_cpx_palette():
            slots,active_cols,active_rows=cpx_crop_trailing_empty_edges(slots,self.cols)
            options['cols']=active_cols
            options['rows_per_page']=min(int(options['rows_per_page']),max(1,active_rows))
        samples=[x for x in slots if x is not None]
        if not samples:
            QMessageBox.information(self,'导出色卡','当前方案没有可导出的色样。'); return
        if not self.main.can_export_samples(samples,'导出色卡'):return
        cols=max(1,int(options['cols'])); rows=max(1,int(options['rows_per_page'])); per_page=cols*rows
        page_count=max(1,int(math.ceil(len(slots)/per_page)))
        if fmt in {'png','jpg'} and (not options.get('single_image',False)) and page_count>30:
            answer=QMessageBox.question(self,'大型图片导出',
                f'当前方案共有 {len(samples)} 个色样，按当前版式会生成 {page_count} 张页面型图片。\n\n'
                '图片会保持正常页面比例，不会再导出成长条图。若用于整套交付，建议优先使用 PDF。\n\n是否继续导出图片？',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)
            if answer!=QMessageBox.Yes:return
        suffix={'pdf':'.pdf','png':'.png','jpg':'.jpg'}[fmt]
        filt={'pdf':'PDF (*.pdf)','png':'PNG (*.png)','jpg':'JPEG (*.jpg *.jpeg)'}[fmt]
        path,_=QFileDialog.getSaveFileName(self,'导出色卡',f"{self.card.get('name','色卡方案')}{suffix}",filt)
        if not path:return
        lower_path=path.lower()
        if fmt=='jpg':
            if not lower_path.endswith(('.jpg','.jpeg')):path+='.jpg'
        elif not lower_path.endswith(suffix):path+=suffix
        landscape=bool(options.get('landscape',True))
        page_px=(2480,1754) if landscape else (1754,2480)
        saved_paths=[]
        if fmt=='pdf':
            writer=QPdfWriter(path); writer.setResolution(150); writer.setPageSize(QPageSize(QPageSize.A4)); writer.setPageOrientation(QPageLayout.Landscape if landscape else QPageLayout.Portrait)
            painter=QPainter(writer)
            try:
                for page in range(page_count):
                    chunk=slots[page*per_page:(page+1)*per_page]
                    page_opts=dict(options); page_opts['rows_per_page']=rows
                    image=self._render_palette_export_page(chunk,page_opts,page,page_count,page_px)
                    if page>0:writer.newPage()
                    viewport=painter.viewport(); scaled=image.size(); scaled.scale(viewport.size(),Qt.KeepAspectRatio)
                    x=viewport.x()+(viewport.width()-scaled.width())//2; y=viewport.y()+(viewport.height()-scaled.height())//2
                    painter.drawImage(QRect(x,y,scaled.width(),scaled.height()),image)
            finally:painter.end()
            saved_paths=[Path(path)]
        else:
            out_path=Path(path); quality=95 if fmt=='jpg' else -1
            if options.get('single_image',False):
                image=self._render_palette_complete_image(slots,options)
                if image.isNull():
                    QMessageBox.warning(self,'导出色卡','单张完整图画布创建失败。可改用“分页高清图”后重试。'); return
                if not image.save(str(out_path),'JPG' if fmt=='jpg' else 'PNG',quality):
                    QMessageBox.warning(self,'导出色卡',f'单张完整图保存失败。\n\n文件：{out_path}\n尺寸：{image.width()} × {image.height()} px\n可切换为“分页高清图”后重试。')
                    return
                saved_paths=[out_path]
                note=f'已将 {len(samples)} 个色样导出为 1 张完整图片；版式保持 {cols} 列。大型方案会自动压缩纵向密度，适合总览和放大查看。'
                self._notify_export_success(fmt.upper(),saved_paths,note=note)
            else:
                for page in range(page_count):
                    start=page*per_page; end=min(len(slots),(page+1)*per_page); chunk=slots[start:end]
                    page_opts=dict(options); page_opts['rows_per_page']=rows
                    image=self._render_palette_export_page(chunk,page_opts,page,page_count,page_px)
                    if image.isNull():
                        QMessageBox.warning(self,'导出色卡','图片画布创建失败；请降低每页行数后重试。'); return
                    page_path=out_path if page_count==1 else out_path.with_name(f'{out_path.stem}_{page+1:02d}{out_path.suffix}')
                    if not image.save(str(page_path),'JPG' if fmt=='jpg' else 'PNG',quality):
                        for done in saved_paths:
                            try:done.unlink(missing_ok=True)
                            except OSError:pass
                        QMessageBox.warning(self,'导出色卡',f'图片保存失败。\n\n文件：{page_path}\n尺寸：{image.width()} × {image.height()} px\n请检查保存目录权限后重试。')
                        return
                    saved_paths.append(page_path)
                note=f'当前方案共导出 {page_count} 张分页高清图片。' if page_count>1 else ''
                self._notify_export_success(fmt.upper(),saved_paths,note=note)
        action={'pdf':'EXPORT_PALETTE_PDF','png':'EXPORT_PALETTE_PNG','jpg':'EXPORT_PALETTE_JPG'}[fmt]
        audit_path=str(saved_paths[0] if saved_paths else path)
        self.main.audit(action,audit_path,f'color_card={self.card.get("name","")} samples={len(samples)} cols={cols}')
        self.main.statusBar().showMessage(f'已导出色卡：{audit_path}',5000)
        if fmt=='pdf':self._notify_export_success('PDF',[Path(path)])

    def export_qtx(self):
        if not self.main.can_use("export"):return
        path,_=QFileDialog.getSaveFileName(self,'导出 QTX',f"{self.card['name']}.qtx",'QTX (*.qtx)')
        if not path:return
        slots=self._export_slot_snapshot('导出 QTX')
        if slots is None:return
        ordered=[x for x in slots if x is not None]
        if not ordered:
            QMessageBox.information(self,'导出 QTX','当前色卡方案没有可导出的色样。'); return
        if not self.main.can_export_samples(ordered,'导出色卡'):return
        export_qtx_file(path,ordered); self.main.audit('EXPORT_QTX',path,f'color_card={self.card.get("name","")} samples={len(ordered)}'); self.main.statusBar().showMessage(f'已导出 QTX：{path}',5000)
        self._notify_export_success('QTX',[Path(path)])

    def export_cpx(self):
        if not self.main.can_use("export"):return
        path,_=QFileDialog.getSaveFileName(self,'导出 ChromaShare CPX',f"{self.card['name']}.cpx",'CPX (*.cpx)')
        if not path:return
        settings=self.card.get('settings',{}) or {}; source_cpx=str(settings.get('source_cpx') or '')
        if source_cpx:
            try:same_source=Path(path).resolve()==Path(source_cpx).resolve()
            except Exception:same_source=False
            if same_source:
                QMessageBox.warning(self,'保护源 CPX','当前打开的是源 CPX。为避免破坏原文件，不能直接覆盖源 CPX。\n请另存为新的 CPX 文件。')
                return
        visible_slots=self._export_slot_snapshot('导出 CPX')
        if visible_slots is None:return
        if not self.main.can_export_samples([x for x in visible_slots if x is not None],'导出色卡'):return
        tile=tuple(settings.get('cpx_tile_size') or (84,34)); gap=tuple(settings.get('cpx_tile_gap') or (20,22))
        templates=dict(settings.get('cpx_templates') or {})
        source_cpx=str(settings.get('source_cpx') or '')

        export_cols=self.cols; export_rows=self.rows or max(1,math.ceil(max(1,len(visible_slots))/max(1,export_cols)))
        slots=list(visible_slots)
        # CPX editor uses a presentation rectangle that may fold source-only
        # right columns / tail rows. Reconstruct the original Fixed Layout here.
        if self._is_cpx_palette():
            source_cols=max(1,int(settings.get('cpx_original_cols') or self.cols or 1))
            source_rows=max(1,int(settings.get('cpx_original_rows') or self.rows or 1))
            slots,export_rows=cpx_expand_to_source_grid(slots,self.cols,source_cols,source_rows)
            export_cols=source_cols

        original_keys=list(settings.get('cpx_original_full_slot_keys') or [])
        current_keys=[sample_key(sm) if sm is not None else None for sm in slots]
        exact_source=bool(
            source_cpx and source_cpx in templates
            and str(self.card.get('name',''))==str(settings.get('cpx_original_palette_name',self.card.get('name','')))
            and export_cols==int(settings.get('cpx_original_cols',export_cols))
            and export_rows==int(settings.get('cpx_original_rows',export_rows))
            and original_keys and current_keys==original_keys
        )
        export_cpx_file(path,self.card['name'],slots,export_cols,export_rows,tile_size=(int(tile[0]),int(tile[1])),tile_gap=(int(gap[0]),int(gap[1])),illuminant=str(settings.get('illuminant','D65')),observer=int(settings.get('observer',10)),source_templates=templates,root_template_source=source_cpx or None,preserve_source_if_unchanged=exact_source)
        self.main.audit('EXPORT_CPX',path,f'color_card={self.card.get("name","")}')
        self.main.statusBar().showMessage(f'已导出 CPX：{path}',5000)
        self._notify_export_success('CPX',[Path(path)])

    def export_excel(self):
        if not self.main.can_use("export"):return
        path,_=QFileDialog.getSaveFileName(self,'导出 Excel 色卡工作簿',f"{self.card['name']}.xlsx",'Excel (*.xlsx)')
        if not path:return
        ordered=self._export_slot_snapshot('导出 Excel')
        if ordered is None:return
        if not self.main.can_export_samples([x for x in ordered if x is not None],'导出色卡'):return
        display_hexes=[]
        for sm in ordered:
            if sm is None:display_hexes.append(None);continue
            lab=self.main.sample_lab(sm); key=sample_key(sm)
            qc=(fluorescent_lab_to_qcolor(lab) if key in self.fluorescent_force_on else (lab_to_qcolor(lab) if key in self.fluorescent_force_off else sample_display_qcolor(sm,lab)))
            display_hexes.append(qc.name().lstrip('#').upper())
        export_client_card(path,self.card['name'],ordered,self.cols,display_hexes=display_hexes,app_version=BUILD_LABEL)
        self.main.audit('EXPORT_EXCEL',path,f'color_card={self.card.get("name","")} samples={sum(x is not None for x in ordered)} cols={self.cols}')
        self.main.statusBar().showMessage(f'已导出：{path}',4000)
        self._notify_export_success('Excel',[Path(path)])


class ReflectancePlotWidget(QWidget):
    """Dependency-free reflectance chart using the original measured arrays unchanged."""
    def __init__(self, sample: Sample | None, parent=None):
        super().__init__(parent); self.sample=sample; self.setMinimumSize(260,180); self.setMouseTracking(True); self._points=[]; self._hover=None
    def set_sample(self, sample: Sample | None):
        self.sample=sample; self._points=[]; self._hover=None; self.update()
    @staticmethod
    def _spectral_color(w):
        # Display-only wavelength hue approximation. Never used in colour calculations.
        w=float(w)
        if w<380 or w>700:return QColor('#5B6B7F')
        if w<440:r,g,b=-(w-440)/(440-380),0,1
        elif w<490:r,g,b=0,(w-440)/(490-440),1
        elif w<510:r,g,b=0,1,-(w-510)/(510-490)
        elif w<580:r,g,b=(w-510)/(580-510),1,0
        elif w<645:r,g,b=1,-(w-645)/(645-580),0
        else:r,g,b=1,0,0
        return QColor(int(max(0,min(1,r))*220),int(max(0,min(1,g))*220),int(max(0,min(1,b))*220))
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.fillRect(self.rect(),QColor('#FFFFFF'))
        r=self.rect().adjusted(58,36,-24,-48); panel=QPainterPath(); panel.addRoundedRect(QRectF(r).adjusted(-12,-12,12,12),12,12); p.fillPath(panel,QColor('#F9FBFE'))
        if self.sample is None or not self.sample.has_spectrum():p.setPen(QColor('#8797AD')); p.drawText(r,Qt.AlignCenter,'无反射率数据'); return
        waves=list(self.sample.wavelengths); vals=[float(v) for v in self.sample.reflectance]; xmin,xmax=min(waves),max(waves); ymax=max(100.0,max(vals)*1.08); ymin=0.0
        def xmap(x):return r.left()+(float(x)-xmin)/(xmax-xmin)*r.width()
        def ymap(y):return r.bottom()-(float(y)-ymin)/(ymax-ymin)*r.height()
        p.setPen(QPen(QColor('#E7EDF5'),1))
        for pct in (0,20,40,60,80,100):
            y=ymap(pct); p.drawLine(QPointF(r.left(),y),QPointF(r.right(),y)); p.setPen(QColor('#7B8DA5')); p.drawText(QRectF(5,y-9,44,18),Qt.AlignRight|Qt.AlignVCenter,f'{pct}'); p.setPen(QPen(QColor('#E7EDF5'),1))
        for w in (360,400,450,500,550,600,650,700):
            if xmin<=w<=xmax:
                x=xmap(w); p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom())); p.setPen(QColor('#7B8DA5')); p.drawText(QRectF(x-22,r.bottom()+10,44,18),Qt.AlignCenter,str(w)); p.setPen(QPen(QColor('#E7EDF5'),1))
        pts=[QPointF(xmap(w),ymap(v)) for w,v in zip(waves,vals)]; self._points=list(zip(pts,waves,vals))
        for i in range(1,len(pts)):
            p.setPen(QPen(self._spectral_color((waves[i-1]+waves[i])/2),2.4)); p.drawLine(pts[i-1],pts[i])
        p.setPen(QColor('#5E718D')); p.drawText(QRectF(r.left(),4,r.width(),22),Qt.AlignLeft|Qt.AlignVCenter,'反射率曲线'); p.drawText(QRectF(r.left(),r.bottom()+29,r.width(),18),Qt.AlignCenter,'波长 / nm'); p.save(); p.translate(15,r.center().y()); p.rotate(-90); p.drawText(QRectF(-60,-10,120,20),Qt.AlignCenter,'Reflectance / %'); p.restore()
        if max(vals)>100:
            y=ymap(100); pen=QPen(QColor('#E59B27'),1.2,Qt.DashLine); p.setPen(pen); p.drawLine(QPointF(r.left(),y),QPointF(r.right(),y))
        if self._hover is not None:
            pt,w,v=self._hover; p.setPen(QPen(QColor('#234C92'),1)); p.setBrush(QColor('#FFFFFF')); p.drawEllipse(pt,4.5,4.5); label=f'{w} nm   {v:.4f} %'; fm=p.fontMetrics(); bw=fm.horizontalAdvance(label)+18; box=QRectF(min(r.right()-bw,max(r.left(),pt.x()+10)),max(r.top(),pt.y()-35),bw,26); p.setPen(Qt.NoPen); p.setBrush(QColor('#17345E')); p.drawRoundedRect(box,6,6); p.setPen(QColor('#FFFFFF')); p.drawText(box,Qt.AlignCenter,label)
    def mouseMoveEvent(self,event):
        if not self._points:return
        pos=event.position(); cand=min(self._points,key=lambda x:abs(x[0].x()-pos.x()))
        self._hover=cand if abs(cand[0].x()-pos.x())<18 else None; self.update()
        if self._hover:QToolTip.showText(event.globalPosition().toPoint(),f'{cand[1]} nm\n反射率 {cand[2]:.4f} %',self)
        super().mouseMoveEvent(event)
    def leaveEvent(self,event):self._hover=None;self.update();super().leaveEvent(event)


class SampleDetailsDialog(QDialog):
    """Light-Studio colour detail card with all original scientific data intact."""
    def __init__(self, owner, sample: Sample, parent=None):
        super().__init__(parent or owner); self.owner=owner; self.sample=sample; self._dirty=False
        self.setObjectName('sampleDetailsDialog'); self.setWindowTitle(f'色样详情 · {sample.display_name}'); _fit_dialog_to_screen(self,820,650); self.setMinimumSize(440,340)
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        root=QVBoxLayout(self); root.setContentsMargins(20,18,20,16); root.setSpacing(12)

        hero=QFrame(); hero.setObjectName('detailHero'); header=QHBoxLayout(hero); header.setContentsMargins(0,0,0,0); header.setSpacing(16)
        lab=owner.sample_lab(sample); colour=sample_display_qcolor(sample,lab)
        self.hero_swatch=QLabel(); self.hero_swatch.setFixedSize(132,108); self.hero_swatch.setStyleSheet(f'background:{colour.name()};border:1px solid #D8E0EA;border-radius:10px;'); header.addWidget(self.hero_swatch)
        texts=QVBoxLayout(); texts.setSpacing(5)
        name=QLabel(sample.display_name); name.setObjectName('detailName'); texts.addWidget(name)
        kind='标准样' if sample.kind=='STD' else '批次样'; source=Path(sample.source_file or '').name or '—'
        sub=QLabel(f'{kind}  ·  {source}'); sub.setObjectName('detailSub'); texts.addWidget(sub)
        condition=QLabel(f'当前分析条件  {owner.illuminant} / {owner.observer}°'); condition.setObjectName('detailCondition'); texts.addWidget(condition); texts.addStretch(1)
        header.addLayout(texts,1); root.addWidget(hero)

        self.tabs=QTabWidget(); self.tabs.setObjectName('detailTabs'); root.addWidget(self.tabs,1)
        self._build_measurement_tab(); self._build_colour_tab(); self._build_spectrum_tab(); self._build_attributes_tab()

        actions=QFrame(); actions.setObjectName('detailActions'); al=QHBoxLayout(actions); al.setContentsMargins(0,10,0,0); al.setSpacing(8)
        action_title=QLabel('相关操作'); action_title.setObjectName('detailActionTitle'); al.addWidget(action_title); al.addStretch(1)
        copy_btn=QPushButton('复制色号'); copy_btn.clicked.connect(self.copy_colour_number); al.addWidget(copy_btn)
        add_btn=QPushButton('加入工作台'); add_btn.clicked.connect(self.add_to_workbench); al.addWidget(add_btn)
        view2d_btn=QPushButton('2D 色彩空间'); view2d_btn.clicked.connect(self.open_2d); al.addWidget(view2d_btn)
        view3d_btn=QPushButton('3D 色彩空间'); view3d_btn.clicked.connect(self.open_3d); al.addWidget(view3d_btn)
        more_btn=QPushButton('更多 ▾'); more_btn.clicked.connect(self.show_more_menu); al.addWidget(more_btn)
        self.save_button=QPushButton('保存备注 / Attributes'); self.save_button.setObjectName('primaryButton'); self.save_button.setEnabled(False); self.save_button.setVisible(False); self.save_button.clicked.connect(self.save_attributes); al.addWidget(self.save_button)
        close_btn=QPushButton('关闭'); close_btn.clicked.connect(self.reject); al.addWidget(close_btn); root.addWidget(actions)
    @staticmethod
    def _kv_table(rows, editable=False):
        table=QTableWidget(len(rows),2); table.setHorizontalHeaderLabels(['项目','值']); table.verticalHeader().hide(); table.setAlternatingRowColors(True); table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setEditTriggers(QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed if editable else QAbstractItemView.NoEditTriggers)
        header=table.horizontalHeader()
        # HF51: resizeColumnsToContents() collapses the first column when an
        # Attributes table is empty, so only “值” remains visible.  Keep both
        # headers visible and let the user drag the 项目/值 divider.
        header.setMinimumSectionSize(90)
        header.setSectionResizeMode(0,QHeaderView.Interactive)
        header.setSectionResizeMode(1,QHeaderView.Stretch)
        table.setColumnWidth(0,190)
        for r,(k,v) in enumerate(rows):
            a=QTableWidgetItem(str(k)); b=QTableWidgetItem(str(v));
            if not editable:a.setFlags(a.flags() & ~Qt.ItemIsEditable); b.setFlags(b.flags() & ~Qt.ItemIsEditable)
            table.setItem(r,0,a); table.setItem(r,1,b)
        return table
    def _build_colour_tab(self):
        lab=self.owner.sample_lab(self.sample); C=math.hypot(lab[1],lab[2]); h=math.degrees(math.atan2(lab[2],lab[1]))%360
        xyz=self.owner.sample_xyz(self.sample)
        assess=fluorescence_assessment(self.sample)
        try:munsell=self.owner._perceptual_hue_label(self.sample)
        except Exception:munsell='—'
        rows=[('当前条件',f'{self.owner.illuminant} / {self.owner.observer}°'),('L*',f'{lab[0]:.4f}'),('a*',f'{lab[1]:.4f}'),('b*',f'{lab[2]:.4f}'),('C*',f'{C:.4f}'),('h°',f'{h:.4f}'),('Munsell / 色相',munsell),('X',f'{xyz[0]:.5f}'),('Y',f'{xyz[1]:.5f}'),('Z',f'{xyz[2]:.5f}'),('荧光折角','是' if assess['fluorescent'] else '否'),('判断依据',assess['reason'])]
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(4,10,4,4); table=self._kv_table(rows); table.setObjectName('detailDataTable'); lay.addWidget(table); self.tabs.addTab(tab,'色彩数据')
    def _build_spectrum_tab(self):
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(4,10,4,4)
        split=QSplitter(Qt.Horizontal); lay.addWidget(split,1)
        rows=[(f'{w} nm',f'{r:.6f}') for w,r in zip(self.sample.wavelengths,self.sample.reflectance)]
        table=self._kv_table(rows); table.setHorizontalHeaderLabels(['波长','反射率 (%)']); table.setMinimumWidth(250); split.addWidget(table); split.addWidget(ReflectancePlotWidget(self.sample)); split.setSizes([280,620])
        feat=spectral_feature_summary(self.sample); assess=fluorescence_assessment(self.sample)
        summary=QLabel(f"Start {self.sample.wavelengths[0] if self.sample.wavelengths else '—'} nm   ·   End {self.sample.wavelengths[-1] if self.sample.wavelengths else '—'} nm   ·   Max R {feat.get('max_r',float('nan')):.2f}% @ {feat.get('max_nm',float('nan')):.0f} nm   ·   荧光：{'是' if assess['fluorescent'] else '否'}")
        summary.setObjectName('muted'); lay.addWidget(summary); self.tabs.addTab(tab,'光谱数据')
    def _build_measurement_tab(self):
        tab=QWidget(); lay=QHBoxLayout(tab); lay.setContentsMargins(4,10,4,4); lay.setSpacing(12)
        details=list(self.owner.sample_measurement_details(self.sample).items())
        rows=[('色号',self.sample.display_name),('类型','标准样' if self.sample.kind=='STD' else '批次样'),('来源文件',Path(self.sample.source_file or '').name or '—')]
        rows.extend((k,v) for k,v in details if str(k) not in {'名称','类型','来源文件'})
        info=QFrame(); info.setObjectName('detailInfoCard'); il=QVBoxLayout(info); il.setContentsMargins(10,10,10,10); il.setSpacing(6)
        info_title=QLabel('基本信息'); info_title.setObjectName('detailCardTitle'); il.addWidget(info_title)
        table=self._kv_table(rows); table.setObjectName('detailDataTable'); il.addWidget(table,1); lay.addWidget(info,3)

        preview=QFrame(); preview.setObjectName('detailPreviewCard'); pl=QVBoxLayout(preview); pl.setContentsMargins(14,12,14,12); pl.setSpacing(8)
        pt=QLabel('色彩预览'); pt.setObjectName('detailCardTitle'); pl.addWidget(pt)
        lab=self.owner.sample_lab(self.sample); colour=sample_display_qcolor(self.sample,lab)
        sw=QLabel('屏幕模拟'); sw.setAlignment(Qt.AlignCenter); sw.setMinimumHeight(125); sw.setStyleSheet(f'background:{colour.name()};color:rgba(255,255,255,150);border:1px solid #D8E0EA;border-radius:8px;'); pl.addWidget(sw)
        rgb=f'{colour.red()}, {colour.green()}, {colour.blue()}'
        preview_rows=[('RGB',rgb),('HEX',colour.name().upper()),('Lab',f'{lab[0]:.2f}  {lab[1]:.2f}  {lab[2]:.2f}')]
        for key,value in preview_rows:
            row=QHBoxLayout(); k=QLabel(key); k.setObjectName('detailPreviewKey'); v=QLabel(value); v.setObjectName('detailPreviewValue'); row.addWidget(k); row.addStretch(1); row.addWidget(v); pl.addLayout(row)
        pl.addStretch(1); lay.addWidget(preview,2); self.tabs.addTab(tab,'基本信息')
    def _build_attributes_tab(self):
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(8,10,8,8)
        top=QHBoxLayout(); hint=QLabel('双击单元格编辑；属性会随 CPX / Excel 导出保存'); hint.setObjectName('muted'); top.addWidget(hint); top.addStretch(1); add=QPushButton('＋ 添加'); rem=QPushButton('删除'); top.addWidget(add); top.addWidget(rem); lay.addLayout(top)
        attrs=[(str(k)[5:],str(v)) for k,v in dict(self.sample.raw or {}).items() if str(k).startswith('ATTR_')]
        self.attr_table=self._kv_table(attrs,editable=True); self.attr_table.setContextMenuPolicy(Qt.CustomContextMenu); lay.addWidget(self.attr_table,1)
        add.clicked.connect(self.add_attribute); rem.clicked.connect(self.remove_attributes); self.attr_table.itemChanged.connect(self._mark_dirty); self.attr_table.customContextMenuRequested.connect(self.attr_menu)
        self.tabs.addTab(tab,'备注 / Attributes')
    def _mark_dirty(self,*_): self._dirty=True; self.save_button.setEnabled(True); self.save_button.setVisible(True)
    def add_attribute(self):
        self.attr_table.blockSignals(True); r=self.attr_table.rowCount(); self.attr_table.insertRow(r); self.attr_table.setItem(r,0,QTableWidgetItem('New Attribute')); self.attr_table.setItem(r,1,QTableWidgetItem('')); self.attr_table.blockSignals(False); self.attr_table.setCurrentCell(r,0); self.attr_table.editItem(self.attr_table.item(r,0)); self._mark_dirty()
    def remove_attributes(self):
        rows=sorted({i.row() for i in self.attr_table.selectionModel().selectedRows()},reverse=True)
        for r in rows:self.attr_table.removeRow(r)
        if rows:self._mark_dirty()
    def attr_menu(self,pos):
        m=QMenu(self); add=m.addAction('新增属性'); delete=m.addAction('删除选中属性'); chosen=m.exec(self.attr_table.viewport().mapToGlobal(pos));
        if chosen==add:self.add_attribute()
        elif chosen==delete:self.remove_attributes()
    def save_attributes(self):
        attrs={}
        for r in range(self.attr_table.rowCount()):
            k=(self.attr_table.item(r,0).text() if self.attr_table.item(r,0) else '').strip(); v=(self.attr_table.item(r,1).text() if self.attr_table.item(r,1) else '').strip()
            if k:attrs[k]=v
        self.sample=self.owner.update_sample_attributes(self.sample,attrs); self._dirty=False; self.save_button.setEnabled(False); self.save_button.setVisible(False); self.owner.statusBar().showMessage('Attributes 已更新；导出 CPX / Excel 时会一并保存',4000)

    def copy_colour_number(self):
        QApplication.clipboard().setText(self.sample.display_name)
        self.owner.statusBar().showMessage(f'已复制色号：{self.sample.display_name}',2500)

    def add_to_workbench(self):
        self.owner._menu_add_sample_to_workbench(self.sample,QCursor.pos())

    def export_colour_card(self):
        if not self.owner.can_export_samples([self.sample],'导出当前色样'):return
        path,_=QFileDialog.getSaveFileName(self,'导出当前色样',self.sample.display_name+'.qtx','QTX (*.qtx)')
        if path:
            export_qtx_file(path,[self.sample]); self.owner.audit('EXPORT_QTX',path,f'sample={self.sample.display_name}'); self.owner.statusBar().showMessage(f'已导出色样：{path}',4000)

    def open_2d(self):
        self.owner.open_lab2d_window([self.sample])

    def open_3d(self):
        self.owner.open_lab3d_window([self.sample])

    def show_more_menu(self):
        menu=QMenu(self); export=menu.addAction('导出当前色样…'); find=menu.addAction('以当前色样查色')
        chosen=menu.exec(QCursor.pos())
        if chosen==export:self.export_colour_card()
        elif chosen==find:self.owner.set_find_standard(self.sample)


class LibrarySampleDetailPanel(QFrame):
    """Compact structured detail panel with persistent editable Attributes."""
    def __init__(self, owner, parent=None):
        super().__init__(parent); self.owner=owner; self.sample=None; self._attr_loading=False
        self.setMinimumWidth(410); self.setMaximumWidth(520); self.setObjectName('panel')
        root=QVBoxLayout(self); root.setContentsMargins(10,10,10,10); root.setSpacing(8)
        head=QHBoxLayout(); self.swatch=QLabel(); self.swatch.setFixedSize(54,54); head.addWidget(self.swatch)
        hv=QVBoxLayout(); self.name=QLabel(''); self.name.setWordWrap(True); self.name.setStyleSheet('font-weight:600;font-size:13px;color:#172033;'); hv.addWidget(self.name)
        self.sub=QLabel(''); self.sub.setWordWrap(True); self.sub.setObjectName('muted'); hv.addWidget(self.sub); head.addLayout(hv,1); root.addLayout(head)
        self.tabs=QTabWidget(); root.addWidget(self.tabs,1)
        self.colour_table=self._table(); self.measure_table=self._table(); self.spectrum_table=self._table()
        for title,table in [('颜色数据',self.colour_table),('测量信息',self.measure_table)]:
            host=QWidget(); lay=QVBoxLayout(host); lay.setContentsMargins(4,6,4,4); lay.addWidget(table); self.tabs.addTab(host,title)
        # Formal-library reflectance uses the exact same visual chart component as
        # the compare/sample-detail view. The original numerical table is retained.
        spectrum_host=QWidget(); spectrum_lay=QVBoxLayout(spectrum_host); spectrum_lay.setContentsMargins(4,6,4,4); spectrum_lay.setSpacing(8)
        self.spectrum_plot=ReflectancePlotWidget(None,self); self.spectrum_plot.setMinimumHeight(235)
        spectrum_lay.addWidget(self.spectrum_plot,1); self.spectrum_table.setMinimumHeight(210); spectrum_lay.addWidget(self.spectrum_table,1)
        self.tabs.addTab(spectrum_host,'反射率')
        # Attributes is a first-class, editable part of the sample record.
        attr_host=QWidget(); al=QVBoxLayout(attr_host); al.setContentsMargins(4,6,4,4); al.setSpacing(6)
        tools=QHBoxLayout(); add=QPushButton('＋ 添加'); rem=QPushButton('删除'); save=QPushButton('保存'); tools.addWidget(add); tools.addWidget(rem); tools.addStretch(1); tools.addWidget(save); al.addLayout(tools)
        can_edit=bool(getattr(owner,'is_admin',False)); self.attr_table=self._table(editable=can_edit); al.addWidget(self.attr_table,1); self.tabs.addTab(attr_host,'Attributes')
        add.setEnabled(can_edit); rem.setEnabled(can_edit); save.setEnabled(can_edit)
        if can_edit:
            add.clicked.connect(self.add_attribute); rem.clicked.connect(self.remove_attributes); save.clicked.connect(self.save_attributes)
        else:
            add.setToolTip('正式色库 Attributes 只有管理者可以修改'); rem.setToolTip(add.toolTip()); save.setToolTip(add.toolTip())

    @staticmethod
    def _table(editable=False):
        table=QTableWidget(0,2); table.setHorizontalHeaderLabels(['项目','值']); table.horizontalHeader().setStretchLastSection(True); table.verticalHeader().hide()
        table.setAlternatingRowColors(True); table.setEditTriggers(QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed if editable else QAbstractItemView.NoEditTriggers); table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setWordWrap(True); table.setColumnWidth(0,92)
        return table

    @staticmethod
    def _set_rows(table, rows, editable=False):
        table.setRowCount(len(rows))
        for r,(key,value) in enumerate(rows):
            a=QTableWidgetItem(str(key)); b=QTableWidgetItem(str(value))
            if not editable:
                a.setFlags(a.flags() & ~Qt.ItemIsEditable); b.setFlags(b.flags() & ~Qt.ItemIsEditable)
            table.setItem(r,0,a); table.setItem(r,1,b)
        table.resizeRowsToContents()

    def clear(self):
        self.sample=None; self.name.clear(); self.sub.clear()
        self.swatch.setStyleSheet('background:#FFFFFF;border:1px solid #D7DCE3;border-radius:8px;')
        for table in (self.colour_table,self.measure_table,self.spectrum_table,self.attr_table):table.setRowCount(0)
        if hasattr(self,'spectrum_plot'): self.spectrum_plot.set_sample(None)

    def set_sample(self, sample):
        self.sample=sample
        lab=self.owner.sample_lab(sample); xyz=self.owner.sample_xyz(sample); C=math.hypot(lab[1],lab[2]); h=math.degrees(math.atan2(lab[2],lab[1]))%360
        try:munsell=self.owner._perceptual_hue_label(sample)
        except Exception:munsell='—'
        self.name.setText(sample.display_name)
        source=Path(sample.source_file or '').name or '—'; kind='标准样' if sample.kind=='STD' else '批次样'
        self.sub.setText(f'{kind} · {source}')
        self.swatch.setStyleSheet(f'background:{sample_display_qcolor(sample,lab).name()};border:1px solid #D7DCE3;border-radius:8px;')
        colour_rows=[('当前条件',f'{self.owner.illuminant} / {self.owner.observer}°'),('L*',f'{lab[0]:.4f}'),('a*',f'{lab[1]:.4f}'),('b*',f'{lab[2]:.4f}'),('C*',f'{C:.4f}'),('h°',f'{h:.3f}'),('Munsell/色相',munsell),('X',f'{xyz[0]:.5f}'),('Y',f'{xyz[1]:.5f}'),('Z',f'{xyz[2]:.5f}')]
        self._set_rows(self.colour_table,colour_rows)
        details=self.owner.sample_measurement_details(sample); self._set_rows(self.measure_table,list(details.items()))
        refl=[(f'{w} nm',f'{float(r):.4f} %') for w,r in zip(sample.wavelengths,sample.reflectance)]; self._set_rows(self.spectrum_table,refl if refl else [('反射率','—')])
        if hasattr(self,'spectrum_plot'): self.spectrum_plot.set_sample(sample)
        attrs=[(str(k)[5:],str(v)) for k,v in dict(sample.raw or {}).items() if str(k).startswith('ATTR_')]
        self._set_rows(self.attr_table,attrs,editable=bool(getattr(self.owner,'is_admin',False)))

    def add_attribute(self):
        if self.sample is None:return
        r=self.attr_table.rowCount(); self.attr_table.insertRow(r); self.attr_table.setItem(r,0,QTableWidgetItem('New Attribute')); self.attr_table.setItem(r,1,QTableWidgetItem('')); self.attr_table.setCurrentCell(r,0); self.attr_table.editItem(self.attr_table.item(r,0))
    def remove_attributes(self):
        for r in sorted({i.row() for i in self.attr_table.selectionModel().selectedRows()},reverse=True):self.attr_table.removeRow(r)
    def save_attributes(self):
        if self.sample is None:return
        attrs={}
        for r in range(self.attr_table.rowCount()):
            k=(self.attr_table.item(r,0).text() if self.attr_table.item(r,0) else '').strip(); v=(self.attr_table.item(r,1).text() if self.attr_table.item(r,1) else '').strip()
            if k:attrs[k]=v
        self.sample=self.owner.update_sample_attributes(self.sample,attrs); self.set_sample(self.sample); self.owner.statusBar().showMessage('Attributes 已保存并会随 CPX / Excel 导出',3500)


class Shade555Dialog(QDialog):
    """Datacolor CHECK II style 555 configuration.

    555 is a shade *sorting* code for batches which already passed the
    acceptability tolerance.  Each configured axis uses an explicit low/high
    tolerance and the complete span is split equally into 3/5/7/9 boxes.
    """
    def __init__(self, wb: dict, parent=None):
        super().__init__(parent); self.wb=wb; self.owner=parent
        self.setWindowTitle('555 色阶分选 · Datacolor')
        self.resize(560,430)
        self.cfg=dict(wb.get('shade555') or {})
        root=QVBoxLayout(self); root.setContentsMargins(18,16,18,14); root.setSpacing(10)
        intro=QLabel('555 仅用于对已通过合格容差的批次样进行色阶分组，不参与合格/超差判定。\n每个方向的最小/最大容差会被平均分成 3、5、7 或 9 个箱。')
        intro.setWordWrap(True); intro.setObjectName('muted'); root.addWidget(intro)
        form=QFormLayout()
        self.mode=QComboBox(); self.mode.addItems(['CIELAB：ΔL* / Δa* / Δb*','CIELCh：ΔL* / ΔC* / ΔH*'])
        saved_mode=str(self.cfg.get('mode','LAB')).upper(); self.mode.setCurrentIndex(1 if saved_mode=='LCH' else 0)
        form.addRow('分选空间',self.mode)
        self.boxes=QComboBox(); self.boxes.addItems(['3','5','7','9']); self.boxes.setCurrentText(str(self.cfg.get('blocks',9) if self.cfg.get('blocks') in (3,5,7,9) else 9)); form.addRow('箱数',self.boxes)
        formula=FORMULA_LABELS.get(getattr(self.owner,'active_formula','delta_e00'),'CIEDE2000'); threshold=float(getattr(self.owner,'thresholds',{}).get(getattr(self.owner,'active_formula','delta_e00'),2.0))
        follow=QLabel(f'{formula} ≤ {threshold:g}（合格判定沿用当前工作台阈值）'); follow.setWordWrap(True); form.addRow('合格判定',follow)
        self.only_pass=QCheckBox('仅对已合格批次进行 555 分选（Datacolor 规则）'); self.only_pass.setChecked(True); self.only_pass.setEnabled(False); form.addRow('',self.only_pass)
        root.addLayout(form)
        self.range_box=QTableWidget(3,3); self.range_box.setHorizontalHeaderLabels(['维度','最小值','最大值']); self.range_box.horizontalHeader().setStretchLastSection(True); self.range_box.verticalHeader().hide(); self.range_box.setAlternatingRowColors(True); root.addWidget(self.range_box)
        note=QLabel('示例（Datacolor 手册）：L* ±1.80、a* ±0.90、b* ±0.90，9 箱时每箱宽度分别为 0.40、0.20、0.20。上下限对称时，标准位于中心 5-5-5。')
        note.setWordWrap(True); note.setObjectName('muted'); root.addWidget(note)
        self.mode.currentIndexChanged.connect(self._fill); self._fill()
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText('确定'); buttons.button(QDialogButtonBox.Cancel).setText('取消'); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _mode_key(self): return 'LAB' if self.mode.currentIndex()==0 else 'LCH'
    def _keys(self, mode=None):
        return ('L','a','b') if (mode or self._mode_key())=='LAB' else ('L','C','H')
    def _defaults(self, mode):
        return {'L':(-1.80,1.80),'a':(-0.90,0.90),'b':(-0.90,0.90)} if mode=='LAB' else {'L':(-1.80,1.80),'C':(-0.90,0.90),'H':(-0.90,0.90)}
    def _saved_ranges(self, mode):
        raw=dict(self.cfg.get('ranges') or {}) if str(self.cfg.get('mode','LAB')).upper()==mode else {}
        out=self._defaults(mode)
        for k in self._keys(mode):
            v=raw.get(k)
            if isinstance(v,(list,tuple)) and len(v)>=2:
                try:
                    lo,hi=float(v[0]),float(v[1])
                    if hi>lo: out[k]=(lo,hi)
                except Exception: pass
        return out
    def _fill(self,*_):
        mode=self._mode_key(); ranges=self._saved_ranges(mode); self.range_box.blockSignals(True)
        try:
            labels={'L':'ΔL*','a':'Δa*','b':'Δb*','C':'ΔC*','H':'ΔH*'}
            for r,k in enumerate(self._keys(mode)):
                it=QTableWidgetItem(labels[k]); it.setFlags(it.flags() & ~Qt.ItemIsEditable); self.range_box.setItem(r,0,it)
                self.range_box.setItem(r,1,QTableWidgetItem(f'{ranges[k][0]:.4f}')); self.range_box.setItem(r,2,QTableWidgetItem(f'{ranges[k][1]:.4f}'))
        finally:self.range_box.blockSignals(False)
    def _read_ranges(self):
        out={}; mode=self._mode_key()
        for r,k in enumerate(self._keys(mode)):
            lo=float(self.range_box.item(r,1).text()); hi=float(self.range_box.item(r,2).text())
            if hi<=lo: raise ValueError(f'{k} 方向最大值必须大于最小值')
            out[k]=(lo,hi)
        return out
    def settings(self):
        return {'enabled':True,'algorithm':'datacolor_tolerance_bins_v1','mode':self._mode_key(),'blocks':int(self.boxes.currentText()),'only_pass':True,'ranges':self._read_ranges()}


class WorkspaceSwatchDelegate(QStyledItemDelegate):
    """Professional electronic swatch: large colour field + one quiet label line."""
    def __init__(self, owner, card_size=QSize(148,170), parent=None):
        super().__init__(parent); self.owner=owner; self.card_size=card_size
    def sizeHint(self, option, index): return QSize(self.card_size.width()+10,self.card_size.height()+10)
    def set_card_size(self, size): self.card_size=QSize(size)
    def paint(self,painter,option,index):
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(option.rect).adjusted(5,5,-5,-5); selected=bool(option.state & QStyle.State_Selected)
        shadow=QPainterPath(); shadow.addRoundedRect(rect.translated(0,2),12,12); painter.fillPath(shadow,QColor(17,24,39,18))
        path=QPainterPath(); path.addRoundedRect(rect,12,12)
        painter.fillPath(path,QColor('#FFFFFF')); painter.setPen(QPen(QColor('#2563EB') if selected else QColor('#E3DFD7'),2.0 if selected else 1.0)); painter.drawPath(path)
        sm=index.data(Qt.UserRole)
        if sm is not None:
            try:lab=self.owner.sample_lab(sm); color=sample_display_qcolor(sm,lab)
            except Exception:lab=getattr(sm,'lab_d65_10',(50,0,0)); color=sample_display_qcolor(sm,lab)
            sw=QRectF(rect.left()+1,rect.top()+1,rect.width()-2,rect.height()*0.70)
            clip=QPainterPath(); clip.addRoundedRect(sw,11,11); painter.fillPath(clip,color)
            painter.fillRect(QRectF(sw.left(),sw.bottom()-10,sw.width(),10),color)
            # Visual-only fluorescence fold. Slot order/rows/columns stay exactly
            # as parsed from the original CPX project.
            if sample_is_fluorescent(sm):
                fold=20.0
                tri=QPainterPath(); tri.moveTo(sw.right()-fold,sw.top()); tri.lineTo(sw.right(),sw.top()); tri.lineTo(sw.right(),sw.top()+fold); tri.closeSubpath()
                painter.fillPath(tri,QColor('#F8FAFD')); painter.setPen(QPen(QColor('#CFD8E6'),1)); painter.drawLine(QPointF(sw.right()-fold,sw.top()),QPointF(sw.right(),sw.top()+fold))
            painter.setPen(QColor('#20242A')); f=painter.font(); f.setBold(True); f.setPointSizeF(8.3); painter.setFont(f)
            name=str(getattr(sm,'display_name','')); nr=QRectF(rect.left()+9,sw.bottom()+5,rect.width()-18,18)
            fm=QFontMetrics(f); painter.drawText(nr,Qt.AlignLeft|Qt.AlignVCenter,fm.elidedText(name,Qt.ElideRight,max(12,int(nr.width()))))
            f.setBold(False); f.setPointSizeF(7.4); painter.setFont(f); painter.setPen(QColor('#64748B'))
            lab_line=f'L* {lab[0]:.2f}  a* {lab[1]:+.2f}  b* {lab[2]:+.2f}'
            lab_rect=QRectF(nr.left(),nr.bottom()+2,nr.width(),18)
            painter.drawText(lab_rect,Qt.AlignLeft|Qt.AlignVCenter,QFontMetrics(f).elidedText(lab_line,Qt.ElideRight,max(12,int(lab_rect.width()))))
        painter.restore()


class WorkspaceVirtualModel(QAbstractListModel):
    """Virtual model for large unsaved QTX/CPX workspaces.

    P3-4 keeps every parsed Sample in the document, but removes the former
    one-QListWidgetItem-per-sample UI allocation.  QListView now asks this
    model only for rows it needs to paint.  3,500/10,000-sample files therefore
    keep their complete measurement data without creating thousands of item
    objects or a viewport thousands of pixels tall.
    """
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document=document
        self._slots=[]
        self._rows=[]
        self._query=''
        self._search_cache=[]

    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid() or index.row()<0 or index.row()>=len(self._rows):return None
        source_row=self._rows[index.row()]
        sm=self._slots[source_row] if 0<=source_row<len(self._slots) else None
        if role==Qt.UserRole:return sm
        if role==Qt.ToolTipRole:return '空白格' if sm is None else str(getattr(sm,'display_name',''))
        if role==Qt.DisplayRole:return ''
        return None

    def flags(self,index):
        if not index.isValid():return Qt.ItemIsEnabled|Qt.ItemIsDropEnabled
        sm=self.data(index,Qt.UserRole)
        if sm is None:return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled|Qt.ItemIsSelectable|Qt.ItemIsDragEnabled

    def set_slots(self,slots):
        self.beginResetModel()
        self._slots=list(slots)
        self._search_cache=[None]*len(self._slots)
        self._rows=list(range(len(self._slots)))
        self._query=''
        self.endResetModel()

    def _search_text(self,source_row):
        cached=self._search_cache[source_row] if 0<=source_row<len(self._search_cache) else None
        if cached is not None:return cached
        sm=self._slots[source_row] if 0<=source_row<len(self._slots) else None
        if sm is None:text=''
        else:
            try:lab=self.document.main.sample_lab(sm)
            except Exception:lab=getattr(sm,'lab_d65_10',(50.0,0.0,0.0))
            text=f'{getattr(sm,"display_name","")} {getattr(sm,"sample_id","")} '+" ".join(f'{float(v):.2f}' for v in lab)
            text=text.casefold()
        if 0<=source_row<len(self._search_cache):self._search_cache[source_row]=text
        return text

    def set_filter(self,value):
        query=str(value or '').strip().casefold()
        if query==self._query:return
        self.beginResetModel()
        self._query=query
        if not query:self._rows=list(range(len(self._slots)))
        else:self._rows=[i for i,sm in enumerate(self._slots) if sm is not None and query in self._search_text(i)]
        self.endResetModel()

    def sample_at(self,row):
        if row<0 or row>=len(self._rows):return None
        source_row=self._rows[row]
        return self._slots[source_row] if 0<=source_row<len(self._slots) else None

    def source_row_at(self,row):
        return self._rows[row] if 0<=row<len(self._rows) else -1

    def visible_sample_count(self):
        return sum(1 for r in self._rows if 0<=r<len(self._slots) and self._slots[r] is not None)

    def indexes_for_keys(self,keys):
        wanted={str(k) for k in keys}
        out=[]
        for row,source_row in enumerate(self._rows):
            sm=self._slots[source_row] if 0<=source_row<len(self._slots) else None
            if sm is not None and sample_key(sm) in wanted:out.append(self.index(row,0))
        return out


class WorkspaceCardList(QListView):
    """Virtualised copy/move card view shared by temporary data browsers."""
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document=document
        self._drag_press_pos=None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)

    def mousePressEvent(self,event):
        # P3-4/HF51: QListWidget used to start drags automatically.  After the
        # virtual QListView migration some Windows/Qt combinations keep the
        # selection gesture but never enter startDrag().  Record the press and
        # explicitly promote a real mouse move into the same drag path.
        self._drag_press_pos=event.position().toPoint() if event.button()==Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if (event.buttons() & Qt.LeftButton) and self._drag_press_pos is not None:
            distance=(event.position().toPoint()-self._drag_press_pos).manhattanLength()
            if distance>=QApplication.startDragDistance():
                self._drag_press_pos=None
                self.startDrag(Qt.CopyAction|Qt.MoveAction)
                event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self._drag_press_pos=None
        super().mouseReleaseEvent(event)

    def selected_samples(self):
        return [idx.data(Qt.UserRole) for idx in self.selectionModel().selectedIndexes() if idx.data(Qt.UserRole) is not None]

    def current_sample(self):
        idx=self.currentIndex()
        return idx.data(Qt.UserRole) if idx.isValid() else None

    def sample_at_pos(self,pos):
        idx=self.indexAt(pos)
        return (idx,idx.data(Qt.UserRole) if idx.isValid() else None)

    def keyPressEvent(self,event):
        if event.matches(QKeySequence.SelectAll):
            self.document.select_all(); event.accept(); return
        super().keyPressEvent(event)

    def startDrag(self, supportedActions):
        samples=self.selected_samples()
        if not samples:
            current=self.current_sample()
            if current is not None:samples=[current]
        if not samples:return
        keys=[sample_key(x) for x in samples]
        mime=QMimeData(); mime.setData(CARD_MIME,"\n".join(keys).encode('utf-8'))
        # Unsaved/new windows are not yet addressable through the formal library,
        # so internal transfers carry a complete read-only measurement snapshot.
        payload=[{'key':sample_key(sm),'sample':self.document.main._serialize_sample(sm)} for sm in samples]
        mime.setData(CARD_PLAN_SAMPLES_MIME,json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        mime.setData(WORKSPACE_SOURCE_MIME,json.dumps({
            'document_uid':self.document.document_uid,'keys':keys
        },ensure_ascii=False).encode('utf-8'))
        drag=QDrag(self); drag.setMimeData(mime)
        try:
            colour=sample_display_qcolor(samples[0],self.document.main.sample_lab(samples[0])); pix=QPixmap(92,62); pix.fill(colour); drag.setPixmap(pix)
        except Exception:pass
        drag.exec(Qt.CopyAction|Qt.MoveAction,Qt.MoveAction)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction();return
        if event.mimeData().hasFormat(CARD_MIME):event.setDropAction(Qt.CopyAction);event.accept();return
        super().dragEnterEvent(event)

    def dragMoveEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction();return
        if event.mimeData().hasFormat(CARD_MIME):event.setDropAction(Qt.CopyAction);event.accept();return
        super().dragMoveEvent(event)

    def dropEvent(self,event):
        if event.mimeData().hasUrls():
            paths=[url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:self.document.import_external_paths(paths);event.acceptProposedAction();return
        if event.mimeData().hasFormat(CARD_MIME):
            keys=[x for x in bytes(event.mimeData().data(CARD_MIME)).decode('utf-8','ignore').splitlines() if x]
            added=self.document.receive_sample_keys(keys,event.mimeData())
            source_uid=''
            if event.mimeData().hasFormat(WORKSPACE_SOURCE_MIME):
                try:
                    source_uid=str(json.loads(bytes(event.mimeData().data(WORKSPACE_SOURCE_MIME)).decode('utf-8')).get('document_uid') or '')
                except (ValueError,TypeError,UnicodeDecodeError):source_uid=''
            copy_mode=bool(event.modifiers() & Qt.ControlModifier) or not source_uid or source_uid==self.document.document_uid
            if added and source_uid and source_uid!=self.document.document_uid and not copy_mode:
                source=self.document.main._workspace_document_by_uid(source_uid)
                if source is not None:source.remove_sample_keys(added)
            event.setDropAction(Qt.CopyAction if copy_mode else Qt.MoveAction);event.accept();return
        super().dropEvent(event)


class WorkspaceDocument(QWidget):
    """Independent QTX/CPX document in the unified workspace."""
    def __init__(self, main, title: str, samples: list[Sample], source_path: str='', document_type: str='QTX', parent=None, slots=None):
        super().__init__(parent); self.main=main; self.title=title; self.samples=list(samples); self.slots=list(slots) if slots is not None else list(samples); self.source_path=source_path; self.project_path=''; self.library_record_path=''; self.library_customer=''; self.document_uid=uuid.uuid4().hex; self.document_type=document_type; self.standard_key=None; self.columns=6; self.rows=max(1,math.ceil(max(1,len(self.slots))/6)); self.auto_layout=True; self.card_size=QSize(148,170); self._undo=[]; self._redo=[]
        root=QVBoxLayout(self); root.setContentsMargins(16,12,16,12); root.setSpacing(8)
        head=QHBoxLayout(); name=QLabel(title); self.name_label=name; f=name.font(); f.setPointSize(12); f.setBold(True); name.setFont(f); head.addWidget(name)
        self.meta=QLabel(f'{document_type} · {len(self.samples)} 色样'); self.meta.setObjectName('muted'); head.addWidget(self.meta); head.addStretch(1)
        root.addLayout(head)
        filters=QHBoxLayout()
        self.card_search=QLineEdit();self.card_search.setPlaceholderText('搜索当前文件的色号、名称或 LAB…');self.card_search.setClearButtonEnabled(True)
        # P3-4: debounce large-workspace search so typing does not rescan 3,500+
        # samples for every keystroke.  The data stays complete; only the visible
        # model rows are refreshed after a short idle interval.
        self._workspace_filter_timer=QTimer(self); self._workspace_filter_timer.setSingleShot(True); self._workspace_filter_timer.setInterval(180)
        self._workspace_filter_timer.timeout.connect(lambda:self.filter_cards(self.card_search.text()))
        self.card_search.textChanged.connect(lambda *_:self._workspace_filter_timer.start());filters.addWidget(self.card_search,1)
        self.selection_label=QLabel();self.selection_label.setObjectName('muted');filters.addWidget(self.selection_label)
        root.addLayout(filters)
        note=QLabel('Ctrl/Shift 多选 · Ctrl+A 全选 · Ctrl+S 保存当前数据 · 右键更多操作 · 双击查看明细'); note.setObjectName('muted'); root.addWidget(note)
        # New documents reflow with the available width. Explicitly configured
        # row/column layouts remain fixed and scroll in both directions.
        self.canvas_holder=QWidget(); hl=QHBoxLayout(self.canvas_holder); hl.setContentsMargins(0,0,0,0)
        self.list=WorkspaceCardList(self); self.list.setViewMode(QListView.IconMode); self.list.setFlow(QListView.LeftToRight); self.list.setWrapping(True); self.list.setResizeMode(QListView.Adjust); self.list.setMovement(QListView.Static); self.list.setSelectionMode(QAbstractItemView.ExtendedSelection); self.list.setSpacing(0); self.list.setContextMenuPolicy(Qt.CustomContextMenu); self.list.customContextMenuRequested.connect(self.context_menu); self.list.doubleClicked.connect(lambda *_:self.show_details()); self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.list.setStyleSheet('QListView{background:#F8FAFC;border:0;padding:12px;} QListView::item{background:transparent;border:0;} QListView::item:selected{background:transparent;border:0;}'); self.canvas_holder.setStyleSheet('background:#F8FAFC;')
        # P3-4: the workspace is now a real model/view surface.  QListView only
        # paints visible indexes instead of creating and painting thousands of
        # QListWidgetItems inside an enormous outer scroll viewport.
        self.list.setUniformItemSizes(True); self.list.setLayoutMode(QListView.Batched); self.list.setBatchSize(180)
        self.virtual_model=WorkspaceVirtualModel(self,self.list); self.list.setModel(self.virtual_model)
        self.delegate=WorkspaceSwatchDelegate(main,self.card_size,self.list); self.list.setItemDelegate(self.delegate); hl.addWidget(self.list)
        root.addWidget(self.canvas_holder,1)
        # Keep the old attribute name for resize/layout helpers, but the actual
        # scroll surface is now the virtual QListView itself.
        self.canvas_scroll=self.list; self._list_event_viewport=self.list.viewport(); self._list_event_viewport.installEventFilter(self)
        self._selected_count=0
        self.list.selectionModel().selectionChanged.connect(lambda *_:self.update_selection_label())
        self.reload_items()
        for seq,fn in [('Ctrl+C',self.copy_selected),('Ctrl+X',self.cut_selected),('Ctrl+V',self.paste),('Ctrl+Z',self.undo),('Ctrl+Y',self.redo),('Delete',self.remove_selected),('Ctrl+S',self.save_document),('Ctrl+Shift+S',self.save_document_as),('Ctrl+Alt+3',self.open_3d)]:
            act=QAction(self); act.setShortcut(QKeySequence(seq)); act.setShortcutContext(Qt.WidgetWithChildrenShortcut); act.triggered.connect(fn); self.addAction(act)
        self._clipboard=[]
    def reload_items(self):
        destination=('已保存到色库 · '+self.library_customer if self.library_record_path else
                     '已保存个人工作文件' if self.project_path else '尚未保存')
        self.meta.setText(f'{self.document_type} · {len(self.samples)} 色样 · {destination}')
        # P3-4: one model reset replaces thousands of QListWidgetItem allocations.
        self.virtual_model.set_slots(self.slots)
        self.filter_cards(self.card_search.text())

    def update_selection_label(self):
        self._selected_count=len(self.list.selectionModel().selectedIndexes())
        self.selection_label.setText(
            f'显示 {getattr(self,"_visible_cards",len(self.samples))} / {len(self.samples)} · 已选 {self._selected_count} 张'
        )

    def filter_cards(self,value):
        self.virtual_model.set_filter(value)
        self._visible_cards=self.virtual_model.visible_sample_count()
        self.update_selection_label()
        self.apply_layout()

    def apply_layout(self):
        cell=QSize(self.card_size.width()+12,self.card_size.height()+12)
        self.list.setGridSize(cell); self.delegate.set_card_size(self.card_size)
        if self.auto_layout or self.card_search.text().strip():
            # Virtual scrolling: the view keeps a normal viewport and paints only
            # visible rows.  Do not create a widget whose height equals every card.
            self.list.setResizeMode(QListView.Adjust)
            self.list.setWrapping(True)
            self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.list.setMinimumWidth(0); self.list.setMaximumWidth(16777215)
            self.list.updateGeometries(); self.list.viewport().update(); return
        # Explicit user layouts still honour the requested column count, but use
        # the QListView's own vertical scrollbar instead of an enormous outer
        # QScrollArea.  Blank slots remain part of the model.
        width=max(cell.width()+20,self.columns*cell.width()+24)
        self.list.setResizeMode(QListView.Fixed); self.list.setWrapping(True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(width); self.list.setMaximumWidth(width)
        self.list.updateGeometries(); self.list.viewport().update()

    def eventFilter(self,watched,event):
        if QApplication.closingDown() or getattr(self.main,'_app_closing',False):
            return False
        if watched is getattr(self,'_list_event_viewport',None) and event.type()==QEvent.Resize and self.auto_layout:
            QTimer.singleShot(0,self.apply_layout)
        try:
            return super().eventFilter(watched,event)
        except RuntimeError:
            return False

    def selected_samples(self):
        return self.list.selected_samples()

    def current_sample(self):
        return self.list.current_sample()

    def _samples_from_transfer_mime(self,mime,keys):
        transfer={}
        if mime is None or not mime.hasFormat(CARD_PLAN_SAMPLES_MIME):return transfer
        try:
            payload=json.loads(bytes(mime.data(CARD_PLAN_SAMPLES_MIME)).decode('utf-8'))
        except (ValueError,TypeError,UnicodeDecodeError):
            return transfer
        wanted={str(k) for k in keys}
        for entry in payload if isinstance(payload,list) else []:
            try:
                key=str(entry['key']); sm=self.main._deserialize_sample(entry['sample'])
                if key in wanted and sample_key(sm)==key:transfer[key]=sm
            except (KeyError,TypeError,ValueError):
                continue
        return transfer

    def receive_sample_keys(self,keys,mime=None):
        keys=[str(k) for k in keys if k]
        embedded=self._samples_from_transfer_mime(mime,keys)
        existing={sample_key(x) for x in self.samples}; additions=[]
        for key in keys:
            sm=embedded.get(key) or self.main._sample_by_any_key(key)
            if sm is not None and sample_key(sm) not in existing:
                additions.append(sm); existing.add(sample_key(sm))
        if not additions:return []
        self._push_history(); self.samples.extend(additions)
        for sm in additions:
            try:slot=self.slots.index(None); self.slots[slot]=sm
            except ValueError:self.slots.append(sm)
        self.rows=max(self.rows,math.ceil(max(1,len(self.slots))/max(1,self.columns)))
        self.reload_items(); self.apply_layout()
        self.main.statusBar().showMessage(f'已加入 {len(additions)} 个色样到 {self.title}',3000)
        return [sample_key(sm) for sm in additions]

    def remove_sample_keys(self,keys):
        keys={str(k) for k in keys if k}
        if not keys:return
        present={sample_key(sm) for sm in self.samples}
        keys &= present
        if not keys:return
        self._push_history()
        self.samples=[sm for sm in self.samples if sample_key(sm) not in keys]
        self.slots=[None if (sm is not None and sample_key(sm) in keys) else sm for sm in self.slots]
        self.reload_items(); self.apply_layout()
        self.main.statusBar().showMessage(f'已从 {self.title} 移动 {len(keys)} 个色样',3000)
    def open_into_document(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加文件到当前窗口','','色彩文件 (*.qtx *.QTX *.txt *.cpx *.CPX *.xlsx);;所有文件 (*)')
        if paths:self.import_external_paths(paths)
    def import_external_paths(self,paths):
        expanded=self.main._expand_workspace_inputs(paths,include_excel=True)
        if self.main._workspace_import_should_run_async(expanded):
            self.main._start_workspace_import_job(expanded,target_document=self); return
        errors=[]; additions=[]; first_path=''
        known={sample_key(sm) for sm in self.samples}
        for path in expanded:
            try:
                suffix=Path(path).suffix.lower(); full=str(Path(path).resolve())
                if suffix in {'.qtx','.txt'}:incoming=parse_qtx_file(full)
                elif suffix=='.cpx':incoming=parse_cpx_file(full).samples
                elif suffix=='.xlsx':incoming=import_excel_workbook(full).samples
                else:continue
                if not incoming:raise ValueError('没有识别到色样')
                if not first_path:first_path=full
                for sm in incoming:
                    sm=replace(sm,source_file=full)
                    if sample_key(sm) not in known:
                        additions.append(sm); known.add(sample_key(sm))
                self.main._remember_recent(full)
            except Exception as exc:errors.append(f'{Path(path).name}: {exc}')
        if additions:
            self._push_history(); self.samples.extend(additions)
            for sm in additions:
                try:self.slots[self.slots.index(None)]=sm
                except ValueError:self.slots.append(sm)
            if first_path and not self.source_path:self.source_path=first_path
            self.rows=max(self.rows,math.ceil(max(1,len(self.slots))/max(1,self.columns)))
            self.reload_items(); self.apply_layout()
            self.main.statusBar().showMessage(f'已加入 {len(additions)} 个色样',4000)
        if errors:QMessageBox.warning(self,'部分文件无法导入','\n'.join(errors[:20]))

    def append_prepared_imports(self,prepared_results):
        """Append worker-parsed external files as one history/UI transaction."""
        additions=[]; first_path=''; known={sample_key(sm) for sm in self.samples}
        for prepared in prepared_results or []:
            full=str(prepared.get('path') or '')
            incoming=list(prepared.get('samples') or [])
            if incoming and not first_path:first_path=full
            for sm in incoming:
                if sample_key(sm) not in known:
                    additions.append(sm); known.add(sample_key(sm))
            if full:self.main._remember_recent(full)
        if not additions:return 0
        self._push_history(); self.samples.extend(additions)
        for sm in additions:
            try:self.slots[self.slots.index(None)]=sm
            except ValueError:self.slots.append(sm)
        if first_path and not self.source_path:self.source_path=first_path
        self.rows=max(self.rows,math.ceil(max(1,len(self.slots))/max(1,self.columns)))
        self.reload_items(); self.apply_layout()
        self.main.statusBar().showMessage(f'已加入 {len(additions)} 个色样',4000)
        return len(additions)
    def select_all(self):
        """Select a large temporary document as one UI transaction.

        Hotfix29: the former per-item loop emitted selectionChanged and repaint
        work thousands of times.  QListWidget.selectAll() is a single selection
        operation and already respects non-selectable blank slots.
        """
        self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        try:
            self.list.clearSelection()
            self.list.selectAll()
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.blockSignals(False)
        self.update_selection_label()
        self.list.viewport().update()
    def open_3d(self):
        samples=self.selected_samples() or list(self.samples)
        if samples:self.main.open_lab3d_window(samples)
    def open_2d(self):
        samples=self.selected_samples() or list(self.samples)
        if samples:self.main.open_lab2d_window(samples)
    def _state_fingerprint(self):
        if not hasattr(self,'grid'):return None
        return (
            tuple(self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())),
            int(self.cols),int(self.rows),bool(self.auto_grid),
            tuple(sorted(self.fluorescent_force_on)),tuple(sorted(self.fluorescent_force_off)),
        )

    def _sync_dirty_state(self):
        if not hasattr(self,'grid'):return
        saved=getattr(self,'_saved_fingerprint',None)
        self._dirty=bool(self.card.get('_draft')) or (saved is not None and self._state_fingerprint()!=saved)
        title=str(self.card.get('name','色卡方案')) + (' *' if self._dirty else '')
        sub=getattr(self.main,'card_subwindows',{}).get(self.card.get('card_id'))
        if sub is not None:
            try:sub.setWindowTitle(title)
            except RuntimeError:pass
        if hasattr(self,'save_palette_btn'):self.save_palette_btn.setEnabled(bool(self._dirty))

    def _snapshot(self): return [None if x is None else self.main._serialize_sample(x) for x in self.slots]
    def _push_history(self):
        self._undo.append(self._snapshot()); self._undo=self._undo[-80:]; self._redo.clear()
    def undo(self):
        if not self._undo:return
        self._redo.append(self._snapshot()); snap=self._undo.pop(); self.slots=[None if x is None else self.main._deserialize_sample(x) for x in snap]; self.samples=[x for x in self.slots if x is not None]; self.reload_items(); self.apply_layout()
    def redo(self):
        if not self._redo:return
        self._undo.append(self._snapshot()); snap=self._redo.pop(); self.slots=[None if x is None else self.main._deserialize_sample(x) for x in snap]; self.samples=[x for x in self.slots if x is not None]; self.reload_items(); self.apply_layout()
    def show_details(self):
        samples=self.selected_samples();
        if not samples:
            current=self.current_sample(); samples=[current] if current is not None else []
        if samples:self.main.show_sample_details_dialog(samples[0])
    def set_standard(self):
        samples=self.selected_samples();
        if len(samples)!=1: QMessageBox.information(self,'设为标准','请选择一个色样。'); return
        self.standard_key=sample_key(samples[0]); self.main.statusBar().showMessage(f'当前文档标准：{samples[0].display_name}',3000)
    def find_selected(self):
        if not self.main.can_use("find"):return
        current=self.current_sample(); samples=self.selected_samples() or ([current] if current is not None else [])
        samples=[x for x in samples if x is not None]
        if not samples:
            QMessageBox.information(self,'查色 / 找色','请先选择一个或多个色样作为查色标准。')
            return
        # Reuse the existing multi-standard Find workflow.  A multi-selection from
        # one opened QTX becomes one file-level find session, exactly like selecting
        # multiple standards in the dedicated 查色 / 找色 page.
        self.main.library_has_explicit_view=True
        self.main.find_toggle.setChecked(True); self.main.find_panel.show(); self.main.find_source.setCurrentText('QTX色样'); self.main.update_find_source_ui()
        if hasattr(self.main,'library_sort_widget'): self.main.library_sort_widget.hide()
        title=Path(self.source_path).name if self.source_path else self.title
        self.main._add_find_file_session(samples,title)
        self.main.show_find_page()
        self.main.statusBar().showMessage(f'已加入 {len(samples)} 个查色标准；点击【查询】可一次完成全部标准查色',5000)

    def send_to_workbench(self):
        if not self.main.can_use("compare"):return
        current=self.current_sample(); samples=self.selected_samples() or ([current] if current is not None else [])
        if not samples:return
        self.main._choose_workbench_target(samples,self)
    def sort_samples(self,mode):
        self._push_history(); selected=set(sample_key(x) for x in self.selected_samples());
        if mode=='name':self.samples.sort(key=lambda x:x.display_name.lower())
        elif mode in {'L','a','b'}: self.samples.sort(key=lambda x:self.main.sample_lab(x)[{'L':0,'a':1,'b':2}[mode]])
        elif mode=='C': self.samples.sort(key=lambda x:math.hypot(self.main.sample_lab(x)[1],self.main.sample_lab(x)[2]))
        elif mode=='h': self.samples.sort(key=lambda x:(math.degrees(math.atan2(self.main.sample_lab(x)[2],self.main.sample_lab(x)[1]))+360)%360)
        self.slots=list(self.samples); self.reload_items(); self.apply_layout()
        if selected:
            selection=QItemSelection()
            for idx in self.virtual_model.indexes_for_keys(selected):selection.select(idx,idx)
            if not selection.isEmpty():self.list.selectionModel().select(selection,QItemSelectionModel.Select)
    def layout_dialog(self):
        dlg=QDialog(self); dlg.setWindowTitle('色卡显示布局'); form=QFormLayout(dlg); cols=QSpinBox(); cols.setRange(1,12); cols.setValue(self.columns); rows=QSpinBox(); rows.setRange(1,200); rows.setValue(max(self.rows,math.ceil(max(1,len(self.slots))/max(1,self.columns)))); size=QComboBox(); size.addItem('紧凑 124×142',(124,142)); size.addItem('标准 148×170',(148,170)); size.addItem('大 180×208',(180,208)); size.setCurrentIndex(1); form.addRow('列数',cols); form.addRow('行数',rows); form.addRow('卡片大小',size); buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); form.addRow(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        self.auto_layout=False
        self.columns=cols.value(); self.rows=max(rows.value(),math.ceil(max(1,len([x for x in self.slots if x is not None]))/self.columns)); target=self.rows*self.columns
        if len(self.slots)<target:self.slots.extend([None]*(target-len(self.slots)))
        w,h=size.currentData(); self.card_size=QSize(w,h); self.reload_items(); self.apply_layout()
    def shade555(self):
        if not self.standard_key: QMessageBox.information(self,'555 色阶分选','请先右键一个色样【设为标准】。'); return
        std=next((x for x in self.samples if sample_key(x)==self.standard_key),None)
        if not std:return
        fake={'shade555':{'mode':'LAB','blocks':9,'only_pass':False,'ranges':{'L':(-1.8,1.8),'a':(-.9,.9),'b':(-.9,.9)}}}
        dlg=Shade555Dialog(fake,self.main)
        if dlg.exec()!=QDialog.Accepted:return
        cfg=dlg.settings(); lines=[]; sl=self.main.sample_lab(std); threshold=float(self.main.thresholds.get(self.main.active_formula,2.0))
        for sm in self.samples:
            if sm is std or sample_key(sm)==self.standard_key:
                r=shade_555(sl,sl,cfg['ranges'],mode=cfg['mode'],blocks=cfg['blocks']); lines.append((r.code,sm.display_name,'当前标准')); continue
            try:
                pair=analyse_pair(std,sm,self.main.illuminant,reference_illuminant='D65',observer_degrees=self.main.observer)
                val={'delta_e00':pair.delta_e00,'delta_e94':pair.delta_e94,'delta_e76':pair.delta_e76,'cmc21':pair.cmc21}.get(self.main.active_formula,float('inf'))
                if val>threshold:
                    lines.append(('—',sm.display_name,f'超差 {val:.2f}')); continue
            except Exception:
                lines.append(('—',sm.display_name,'无法判定合格')); continue
            r=shade_555(sl,self.main.sample_lab(sm),cfg['ranges'],mode=cfg['mode'],blocks=cfg['blocks']); lines.append((r.code,sm.display_name,'合格'))
        lines.sort(key=lambda x:(x[0]=='—',x[0],x[1])); view='\n'.join(f'{code:>4}   {name}   {state}' for code,name,state in lines[:500]); box=QDialog(self); box.setWindowTitle('555 分阶结果'); lay=QVBoxLayout(box); text=QTextEdit(); text.setReadOnly(True); text.setPlainText(view); lay.addWidget(text); b=QDialogButtonBox(QDialogButtonBox.Close); b.rejected.connect(box.reject); b.accepted.connect(box.accept); lay.addWidget(b); box.resize(680,650); box.exec()
    def _library_target(self):
        # Personal workfiles keep their own save target even if originally made
        # from a library sample. An existing library document uses its DB record.
        if self.project_path and not self.library_record_path:return None
        source=str(self.source_path or '')
        for row in self.main.store.list_files():
            if self.library_record_path and row.path==self.library_record_path:return row
            if source and source in (row.path,str(row.mirror_path or '')):return row
        return None

    def _select_library_customer(self, title='发布到色库'):
        destination,ok=QInputDialog.getItem(self,title,'目标色库：',['正式色库','官方色库'],0,False)
        if not ok:
            return
        if destination=='正式色库':
            return self.main._choose_customer_dialog('发布到正式色库',official=False)
        return self.main._choose_customer_dialog('发布到官方色库',official=True)

    def _persist_library(self,customer,record_path='',new_name=''):
        if not self.main.require_admin('发布/更新正式色库'):return False
        if not record_path:
            if not self.samples:
                QMessageBox.information(self,'发布到色库','当前窗口没有色样。');return False
            stem=self.main.store._safe_component(new_name or self.title)
            record_path=str(self.main.store.customer_data_root/'_imports'/f'{stem}__{uuid.uuid4().hex}.qtx')
        # Copy measured data, never modify the original external QTX/CPX/Excel.
        old_keys=[sample_key(sm) for sm in self.samples]
        persisted=[];used_ids=set()
        for sm in self.samples:
            sample_id=sm.sample_id
            if sample_id in used_ids:
                # Different source files can both start with e.g. SAMPLE_1.
                # The library key must be unique within the combined document.
                original=sample_id
                suffix=1
                while sample_id in used_ids:
                    sample_id=f'{original}__{suffix}'
                    suffix+=1
                raw=dict(sm.raw);raw['CHROMATIC_ORIGINAL_SAMPLE_ID']=original
                sm=replace(sm,sample_id=sample_id,raw=raw)
            used_ids.add(sample_id)
            persisted.append(replace(sm,source_file=record_path))
        try:self.main.store.save_file(record_path,customer,persisted)
        except Exception as exc:
            QMessageBox.warning(self,'发布到色库',f'发布失败；当前数据仍在工作窗口中。\n{exc}');return False
        mapping=dict(zip(old_keys,persisted))
        self.slots=[mapping.get(sample_key(sm),sm) if sm is not None else None for sm in self.slots]
        if self.standard_key in mapping:self.standard_key=sample_key(mapping[self.standard_key])
        self.samples=persisted;self.source_path=record_path;self.library_record_path=record_path
        self.library_customer=customer;self.project_path='';self.document_type='QTX'
        if new_name:self.title=new_name;self.name_label.setText(new_name)
        if self.window() is not self:self.window().setWindowTitle(self.title)
        self.reload_items();self.apply_layout()
        self.main.auth_store.log(self.main.current_user.username,'SAVE_LIBRARY',customer,self.title)
        self.main.refresh()
        self.main.refresh_file_list()
        self.main.statusBar().showMessage(f'已发布/更新到 {customer}：{self.title}',5000)
        return True

    def save_to_library(self):
        if not self.main.require_admin('发布到正式色库'):return
        customer=self._select_library_customer('发布当前工作数据到色库')
        if customer:self._persist_library(customer,new_name=self.title)
    def export_menu(self):
        menu=QMenu(self); q=menu.addAction('QTX…'); c=menu.addAction('CPX…'); x=menu.addAction('Excel 色卡…')
        chosen=menu.exec(QCursor.pos())
        if chosen==q:self.export_file('qtx')
        elif chosen==c:self.export_file('cpx')
        elif chosen==x:self.export_file('excel')
    def export_file(self,kind):
        if not self.main.can_export_samples(self.samples,'导出工作文件'):return
        if kind=='qtx':
            path,_=QFileDialog.getSaveFileName(self,'导出 QTX',self.title+'.qtx','QTX (*.qtx)');
            if path:export_qtx_file(path,self.samples)
        elif kind=='cpx':
            path,_=QFileDialog.getSaveFileName(self,'导出 CPX',self.title+'.cpx','CPX (*.cpx)');
            if path:export_cpx_file(path,self.title,self.slots,self.columns,tile_size=(104,76),tile_gap=(12,20))
        elif kind=='excel':
            path,_=QFileDialog.getSaveFileName(self,'导出 Excel 色卡',self.title+'.xlsx','Excel (*.xlsx)');
            if path:export_client_card(path,self.title,self.samples,self.columns)
    def _workfile_payload(self):
        return {
            'title':self.title,'document_uid':self.document_uid,
            'document_type':self.document_type,'source_path':self.source_path,
            'samples':[self.main._serialize_sample(sm) for sm in self.samples],
            'slots':[self.main._serialize_sample(sm) if sm is not None else None for sm in self.slots],
            'standard_key':self.standard_key,'columns':self.columns,'rows':self.rows,'auto_layout':self.auto_layout,
            'card_size':[self.card_size.width(),self.card_size.height()],
        }
    def save_document(self):
        # DG4.1: Save means "save the current object". Only a document already
        # belonging to the formal library updates that formal asset. Local/imported
        # data (including an administrator's) stays personal until explicit Publish.
        target=self._library_target()
        if target:
            if not self.main.require_admin('更新正式色库'):return
            self._persist_library(target.customer,record_path=target.path);return
        if not self.main.can_use('workfile'):return
        if self.project_path:self._write_workfile(self.project_path)
        else:self._choose_personal_workfile(copy=False)
    def save_document_as(self,force_library=False):
        if force_library:
            if not self.main.require_admin('发布为新的色库副本'):return
            name,ok=QInputDialog.getText(self,'发布为新的色库副本','副本名称：',text=f'{self.title} 副本')
            if not ok or not name.strip():return
            customer=self._select_library_customer('发布为新的独立色库副本（原文件不变）')
            if customer:self._persist_library(customer,new_name=name.strip())
            return
        self._choose_personal_workfile(copy=True)
    def _choose_personal_workfile(self,copy):
        if not self.main.can_use('workfile'):return
        stem=Path(self.project_path or self.title).stem+(' 副本' if copy else '')
        default_path=str(self.main.personal_workfile_root/(stem+WORKFILE_SUFFIX)); path,_=QFileDialog.getSaveFileName(self,'另存为个人工作文件副本' if copy else '保存个人工作文件',default_path,'Chromatic 工作文件 (*.chromatic)')
        if path:self._write_workfile(path,copy=copy)
    def _write_workfile(self,path,copy=False):
        target=Path(path).with_suffix(WORKFILE_SUFFIX)
        if copy and self.project_path and target.resolve()==Path(self.project_path).resolve():
            QMessageBox.warning(self,'另存为副本','请选择不同的文件名称或位置。');return
        try:
            payload=self._workfile_payload()
            if copy:
                payload['document_uid']=uuid.uuid4().hex
                payload['title']=target.stem
            write_workfile(target,payload)
        except Exception as exc:
            QMessageBox.warning(self,'保存工作文件',str(exc)); return
        if copy:
            self.document_uid=payload['document_uid'];self.title=payload['title'];self.name_label.setText(self.title)
            if self.window() is not self:self.window().setWindowTitle(self.title)
        self.project_path=str(target)
        self.reload_items();self.apply_layout()
        self.main._remember_recent(self.project_path)
        self.main.audit('SAVE_PERSONAL_WORKFILE',str(target),self.title)
        self.main.statusBar().showMessage(f'已保存个人工作文件：{target.name}',4000)
    def copy_selected(self):
        samples=list(self.selected_samples()); self._clipboard=samples
        if not samples:return
        keys=[sample_key(sm) for sm in samples]
        payload=[{'key':sample_key(sm),'sample':self.main._serialize_sample(sm)} for sm in samples]
        mime=QMimeData(); mime.setData(CARD_MIME,'\n'.join(keys).encode('utf-8'))
        mime.setData(CARD_PLAN_SAMPLES_MIME,json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        mime.setText('\n'.join(keys)); QApplication.clipboard().setMimeData(mime)
        self.main.statusBar().showMessage(f'已复制 {len(samples)} 个色样，可粘贴到其它文件窗口或色卡方案',3000)
    def cut_selected(self):
        samples=list(self.selected_samples())
        if not samples:return
        self.copy_selected(); self.remove_selected()
    def paste(self):
        mime=QApplication.clipboard().mimeData(); keys=[]
        if mime and mime.hasFormat(CARD_MIME):
            keys=[x for x in bytes(mime.data(CARD_MIME)).decode('utf-8','ignore').splitlines() if x]
        if keys:
            self.receive_sample_keys(keys,mime); return
        # Backward-compatible in-window clipboard for old sessions/actions.
        existing={sample_key(x) for x in self.samples}; additions=[x for x in self._clipboard if sample_key(x) not in existing]
        if not additions:return
        self._push_history(); self.samples.extend(additions); self.slots.extend(additions); self.rows=max(self.rows,math.ceil(max(1,len(self.slots))/max(1,self.columns))); self.reload_items(); self.apply_layout()
    def remove_selected(self):
        keys={sample_key(x) for x in self.selected_samples()};
        if not keys:return
        self._push_history(); self.samples=[x for x in self.samples if sample_key(x) not in keys]; self.slots=[None if (x is not None and sample_key(x) in keys) else x for x in self.slots]; self.reload_items(); self.apply_layout()
    def context_menu(self,pos):
        index=self.list.indexAt(pos)
        sample=index.data(Qt.UserRole) if index.isValid() else None
        if not index.isValid() or sample is None:
            menu=QMenu(self)
            add=menu.addAction('添加 QTX / CPX / Excel…')
            library_mode=bool(self._library_target())
            if library_mode:
                save=menu.addAction('更新当前正式色库文件    Ctrl+S')
            else:
                save=menu.addAction('保存个人工作文件    Ctrl+S')
            save_as=menu.addAction('另存为个人工作文件副本…    Ctrl+Shift+S')
            export=menu.addMenu('导出')
            exports={export.addAction('QTX…'):'qtx',export.addAction('CPX…'):'cpx',export.addAction('Excel 色卡…'):'excel'}
            # Personal work files still need an explicit promotion path into the
            # formal/official library.  Unsaved/library documents already use
            # the single Ctrl+S entry above, so do not show a duplicate action.
            library=menu.addAction('发布到色库…') if self.main.is_admin and not library_mode else None
            library_copy=menu.addAction('发布为新的色库副本…') if self.main.is_admin and library_mode else None
            menu.addSeparator(); arrange=menu.addMenu('整理文件')
            sorts={arrange.addAction(title):key for title,key in [('按名称排序','name'),('按 L* 排序','L'),('按 a* 排序','a'),('按 b* 排序','b'),('按 C* 排序','C'),('按 h° 排序','h')]}
            responsive=arrange.addAction('随窗口自动排列色卡'); responsive.setCheckable(True); responsive.setChecked(self.auto_layout)
            layout=arrange.addAction('设置固定行列 / 卡片大小…')
            chosen=menu.exec(self.list.viewport().mapToGlobal(pos))
            if chosen==add:self.open_into_document()
            elif chosen==save:self.save_document()
            elif chosen==save_as:self.save_document_as()
            elif chosen in exports:self.export_file(exports[chosen])
            elif library is not None and chosen==library:self.save_to_library()
            elif library_copy is not None and chosen==library_copy:self.save_document_as(force_library=True)
            elif chosen in sorts:self.sort_samples(sorts[chosen])
            elif chosen==responsive:self.auto_layout=responsive.isChecked(); self.apply_layout()
            elif chosen==layout:self.layout_dialog()
            return
        if index.isValid() and sample is not None and not self.list.selectionModel().isSelected(index):
            self.list.clearSelection(); self.list.selectionModel().select(index,QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Current)
            self.list.setCurrentIndex(index)
        menu=QMenu(self); selected_count=int(getattr(self,'_selected_count',0))
        details=menu.addAction('查看测色明细'); details.setEnabled(selected_count==1)
        find=menu.addAction(f'查色 / 找色（{selected_count} 个标准）' if selected_count>1 else '查色 / 找色'); find.setEnabled(selected_count>0)
        menu.addSeparator()
        copy=menu.addAction(f'复制 {selected_count} 张色卡    Ctrl+C'); copy.setEnabled(selected_count>0)
        cut=menu.addAction(f'剪切 {selected_count} 张色卡    Ctrl+X'); cut.setEnabled(selected_count>0)
        paste=menu.addAction('粘贴    Ctrl+V'); paste.setEnabled(bool(self._clipboard))
        remove=menu.addAction(f'从当前工作文件移除 {selected_count} 张色卡    Delete'); remove.setEnabled(selected_count>0)
        analysis=menu.addMenu('分析')
        view2d=analysis.addAction('查看 2D 色彩空间'); view3d=analysis.addAction('查看 3D 色彩空间    Ctrl+Alt+3')
        std=analysis.addAction('设为标准'); std.setEnabled(selected_count==1)
        compare=analysis.addAction('加入比色工作台…'); compare.setEnabled(selected_count>0)
        shade=analysis.addAction('555 色阶分选…')
        selectall=menu.addAction('全选当前文件色卡    Ctrl+A')
        chosen=menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen==details:self.show_details()
        elif chosen==find:self.find_selected()
        elif chosen==view2d:self.open_2d()
        elif chosen==view3d:self.open_3d()
        elif chosen==selectall:self.select_all()
        elif chosen==copy:self.copy_selected()
        elif chosen==cut:self.cut_selected()
        elif chosen==paste:self.paste()
        elif chosen==remove:self.remove_selected()
        elif chosen==std:self.set_standard()
        elif chosen==compare:self.send_to_workbench()
        elif chosen==shade:self.shade555()


class WorkspaceFolderDocument(QWidget):
    """Workspace collection that shows imported files as colour cards immediately.

    The folder is still temporary/non-destructive: dropping or opening QTX/CPX/Excel
    never writes to the formal library.  Unlike the first Stage-1 draft, it is a real
    visual colour workspace rather than a list of filenames.
    """
    def __init__(self, main, name='工作区文件夹', parent=None):
        super().__init__(parent); self.main=main; self.folder_name=name; self.paths=[]; self.samples=[]; self.setAcceptDrops(True)
        self.card_size=QSize(148,170)
        root=QVBoxLayout(self); root.setContentsMargins(18,14,18,14); root.setSpacing(8)
        head=QHBoxLayout(); title=QLabel(name); f=title.font(); f.setPointSize(13); f.setBold(True); title.setFont(f); head.addWidget(title)
        self.meta=QLabel('0 个文件 · 0 色样'); self.meta.setObjectName('muted'); head.addWidget(self.meta); head.addStretch(1); root.addLayout(head)
        tip=QLabel('拖入或打开 QTX / CPX / Excel 后直接显示色卡 · Ctrl+A 全选 · Delete 从当前文件夹移出 · 双击查看明细')
        tip.setObjectName('muted'); root.addWidget(tip)
        self.list=QListWidget(); self.list.setViewMode(QListView.IconMode); self.list.setFlow(QListView.LeftToRight); self.list.setWrapping(True)
        self.list.setResizeMode(QListView.Adjust); self.list.setMovement(QListView.Static); self.list.setSelectionMode(QAbstractItemView.ExtendedSelection); self.list.setSpacing(4)
        self.list.setUniformItemSizes(True); self.list.setLayoutMode(QListView.Batched); self.list.setBatchSize(240)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu); self.list.customContextMenuRequested.connect(self.context_menu); self.list.itemDoubleClicked.connect(lambda *_:self.show_details())
        self.list.setStyleSheet('QListWidget{background:#F8FAFC;border:0;padding:12px;} QListWidget::item{background:transparent;border:0;} QListWidget::item:selected{background:transparent;border:0;}')
        self.delegate=WorkspaceSwatchDelegate(main,self.card_size,self.list); self.list.setItemDelegate(self.delegate); self.list.setGridSize(QSize(self.card_size.width()+14,self.card_size.height()+14)); root.addWidget(self.list,1)
        for seq,fn in [('Ctrl+A',self.select_all),('Delete',self.remove_selected),('Ctrl+Alt+3',self.open_3d)]:
            act=QAction(self); act.setShortcut(QKeySequence(seq)); act.setShortcutContext(Qt.WidgetWithChildrenShortcut); act.triggered.connect(fn); self.addAction(act)

    def _parse_path(self, raw):
        path=str(Path(raw).resolve()); suf=Path(path).suffix.lower()
        if suf in {'.qtx','.txt'}:
            samples=[replace(x,source_file=path) for x in parse_qtx_file(path)]
            return samples
        if suf=='.cpx':
            project=parse_cpx_file(path)
            # Keep CPX slot order while omitting physical empty slots in this
            # temporary collection view. The dedicated palette editor retains them.
            return [x for x in project.slots if x is not None] or list(project.samples)
        if suf=='.xlsx':
            result=import_excel_workbook(path)
            return list(result.samples)
        return []

    def add_paths(self,paths):
        errors=[]; added=0
        self.list.blockSignals(True); self.list.setUpdatesEnabled(False)
        try:
            for raw in self.main._expand_workspace_inputs(paths,include_excel=True):
                try: full=str(Path(raw).resolve())
                except Exception: full=str(raw)
                if full in self.paths: continue
                try:
                    samples=self._parse_path(full)
                    if not samples:
                        raise ValueError('没有识别到可显示的色样')
                    self.paths.append(full); self.samples.extend(samples); added += len(samples)
                    size_hint=QSize(self.card_size.width()+10,self.card_size.height()+10)
                    for sm in samples:
                        it=QListWidgetItem(); it.setData(Qt.UserRole,sm); it.setData(Qt.UserRole+1,full)
                        it.setSizeHint(size_hint)
                        it.setToolTip(f'{sm.display_name}\n{Path(full).name}')
                        self.list.addItem(it)
                except Exception as exc:
                    errors.append(f'{Path(full).name}: {exc}')
        finally:
            self.list.setUpdatesEnabled(True); self.list.blockSignals(False); self.list.viewport().update()
        self.meta.setText(f'{len(self.paths)} 个文件 · {len(self.samples)} 色样')
        if added:self.main.statusBar().showMessage(f'{self.folder_name}：已加入 {added} 个色样',3500)
        if errors:QMessageBox.warning(self,'部分文件无法加入','\n'.join(errors[:20]))

    def add_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加到工作区文件夹','','色彩文件 (*.qtx *.QTX *.txt *.cpx *.CPX *.xlsx);;所有文件 (*)')
        if paths:self.add_paths(paths)

    def selected_samples(self):
        return [it.data(Qt.UserRole) for it in self.list.selectedItems() if it.data(Qt.UserRole) is not None]

    def select_all(self):
        self.list.blockSignals(True); self.list.setUpdatesEnabled(False)
        try:
            self.list.clearSelection(); self.list.selectAll()
        finally:
            self.list.setUpdatesEnabled(True); self.list.blockSignals(False); self.list.viewport().update()

    def show_details(self):
        samples=self.selected_samples()
        if not samples and self.list.currentItem(): samples=[self.list.currentItem().data(Qt.UserRole)]
        if samples and samples[0] is not None:self.main.show_sample_details_dialog(samples[0])

    def open_3d(self):
        samples=self.selected_samples() or list(self.samples)
        if samples:self.main.open_lab3d_window(samples)
    def open_2d(self):
        samples=self.selected_samples() or list(self.samples)
        if samples:self.main.open_lab2d_window(samples)

    def remove_selected(self):
        rows=sorted({self.list.row(it) for it in self.list.selectedItems()},reverse=True)
        if not rows:return
        for row in rows:
            item=self.list.takeItem(row); sm=item.data(Qt.UserRole)
            try:self.samples.remove(sm)
            except ValueError:pass
        # Recalculate path list from remaining card sources. This only affects the
        # temporary folder; original disk files and the formal library are untouched.
        used={str(Path(getattr(sm,'source_file','')).resolve()) for sm in self.samples if getattr(sm,'source_file','')}
        self.paths=[p for p in self.paths if p in used or any((self.list.item(i).data(Qt.UserRole+1)==p) for i in range(self.list.count()))]
        self.meta.setText(f'{len(self.paths)} 个文件 · {len(self.samples)} 色样')

    def context_menu(self,pos):
        item=self.list.itemAt(pos)
        if item is not None and not item.isSelected():self.list.clearSelection();item.setSelected(True)
        menu=QMenu(self); details=menu.addAction('查看测色明细'); view2d=menu.addAction('查看 2D 色彩空间'); view3d=menu.addAction('查看 3D 色彩空间    Ctrl+Alt+3'); menu.addSeparator(); add=menu.addAction('添加 QTX / CPX / Excel…'); remove=menu.addAction('从当前文件夹移出    Delete')
        chosen=menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen==details:self.show_details()
        elif chosen==view2d:self.open_2d()
        elif chosen==view3d:self.open_3d()
        elif chosen==add:self.add_files()
        elif chosen==remove:self.remove_selected()

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction();return
        super().dragEnterEvent(event)
    def dragMoveEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction();return
        super().dragMoveEvent(event)
    def dropEvent(self,event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:self.add_paths(paths);event.acceptProposedAction();return
        super().dropEvent(event)


class LargeFileImportDialog(QDialog):
    """Small non-blocking progress surface for heavy QTX/CPX/Excel imports.

    Parsing is performed in a worker thread by MainWindow.  The dialog keeps the
    Windows UI responsive and gives the user immediate feedback instead of the
    operating system showing "未响应" while a large palette is being decoded.
    """
    cancelRequested=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('导入大文件')
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(470)
        self.setModal(False)
        self._busy=True
        self._started=time.perf_counter()

        root=QVBoxLayout(self); root.setContentsMargins(22,20,22,18); root.setSpacing(12)
        title=QLabel('正在导入大文件…'); title.setObjectName('sectionTitle'); root.addWidget(title)
        self.hint=QLabel('正在解析文件并读取颜色数据，请稍候…'); self.hint.setObjectName('muted'); root.addWidget(self.hint)

        card=QFrame(); card.setObjectName('analysisCard'); cl=QVBoxLayout(card); cl.setContentsMargins(14,12,14,12); cl.setSpacing(8)
        row=QHBoxLayout(); self.file_label=QLabel('—'); self.file_label.setStyleSheet('font-weight:700;color:#1E293B;'); row.addWidget(self.file_label,1)
        self.size_label=QLabel(''); self.size_label.setObjectName('muted'); row.addWidget(self.size_label); cl.addLayout(row)
        self.detail_label=QLabel('准备导入…'); self.detail_label.setObjectName('muted'); cl.addWidget(self.detail_label)
        self.progress=QProgressBar(); self.progress.setTextVisible(True); self.progress.setRange(0,0); cl.addWidget(self.progress)
        timerow=QHBoxLayout(); self.elapsed=QLabel('已用时间：00:00:00'); self.elapsed.setObjectName('muted'); timerow.addWidget(self.elapsed); timerow.addStretch(1)
        self.stage_label=QLabel('解析中'); self.stage_label.setObjectName('muted'); timerow.addWidget(self.stage_label); cl.addLayout(timerow)
        root.addWidget(card)

        actions=QHBoxLayout(); actions.addStretch(1)
        self.cancel_btn=QPushButton('取消导入'); self.cancel_btn.clicked.connect(self._cancel); actions.addWidget(self.cancel_btn); root.addLayout(actions)

    def begin_file(self,path,index,total):
        self._started=time.perf_counter(); self._busy=True
        p=Path(path); self.file_label.setText(p.name)
        try:
            n=p.stat().st_size
            self.size_label.setText(f'{n/1024/1024:.1f} MB' if n>=1024*1024 else f'{n/1024:.0f} KB')
        except Exception:self.size_label.setText('')
        self.hint.setText(f'正在处理第 {index} / {total} 个文件')
        self.detail_label.setText('正在解析文件并读取颜色数据…')
        self.stage_label.setText('解析中')
        self.progress.setRange(0,0)
        self.cancel_btn.setEnabled(True)
        self.show(); self.raise_(); self.activateWindow()

    def parsed(self,count):
        self.progress.setRange(0,100); self.progress.setValue(72)
        self.detail_label.setText(f'解析完成 · 已识别 {int(count):,} 个颜色样本')
        self.stage_label.setText('正在创建色卡…')

    def committed(self,count):
        self.progress.setRange(0,100); self.progress.setValue(100)
        self.detail_label.setText(f'完成 · 已加载 {int(count):,} 个颜色样本')
        self.stage_label.setText('完成')

    def failed(self,message):
        self.progress.setRange(0,100); self.progress.setValue(0)
        text=str(message or '未知错误').strip().replace('\n',' ')
        if len(text)>180:text=text[:177]+'…'
        self.detail_label.setText('启动解析失败：'+text)
        self.stage_label.setText('失败')
        self.cancel_btn.setEnabled(False)
        self.repaint()

    def tick(self):
        seconds=max(0,int(time.perf_counter()-self._started))
        self.elapsed.setText(f'已用时间：{seconds//3600:02d}:{(seconds//60)%60:02d}:{seconds%60:02d}')

    def mark_cancelling(self):
        self.cancel_btn.setEnabled(False); self.stage_label.setText('正在取消…'); self.detail_label.setText('当前解析完成后将停止后续导入。')

    def finish(self):
        self._busy=False; self.accept()

    def _cancel(self):
        if not self._busy:return
        self.mark_cancelling(); self.cancelRequested.emit()

    def closeEvent(self,event):
        if self._busy:
            self._cancel(); event.ignore(); return
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, current_user: AuthUser | None = None, auth_store: AuthStore | None = None):
        super().__init__()
        self._startup_perf_t0=time.perf_counter(); self._startup_first_paint_recorded=False
        self._app_closing=False
        self.current_user = current_user or AuthUser(0, "local", "Local User", "admin", True)
        self.auth_store = auth_store or AuthStore()
        self.is_admin = bool(self.current_user.is_admin)
        self.store = LibraryStore()
        self.store.set_user_context(self.current_user.user_id, self.is_admin, self.current_user.username)
        # Bind access before any library read. Ordinary users fail closed if
        # permission metadata cannot be read; administrators retain repair access.
        from .access_policy import configure_library_access
        self._data_scope_policy = configure_library_access(
            self.store, self.auth_store, self.current_user
        )
        self.settings = QSettings("ChromaticAnalysis", "ChromaticAnalysis")
        self.personal_workfile_root = user_workfile_root(self.current_user.user_id, self.current_user.username)
        # One consistent SQLite snapshot per day. Do this before any DG-3 data migration.
        try:
            backup=maybe_daily_backup(self.settings,self.auth_store.path,self.store.path)
            if backup is not None:
                self.auth_store.log(self.current_user.username,'AUTO_BACKUP',str(backup),'DG-3 daily local snapshot')
        except Exception:
            pass
        # DG-3: migrate only app-generated library mirror files; original imported QTX files are untouched.
        if self.is_admin:
            try:
                moved=self.store.migrate_legacy_customer_mirrors()
                if moved:
                    self.auth_store.log(self.current_user.username,'MIGRATE_LIBRARY_MIRRORS','managed_data',str(moved))
            except Exception:
                pass
        # Heavy external-file parsing is isolated from the Qt GUI thread.  The
        # executor is intentionally single-worker: imports remain deterministic
        # and do not compete for colour-science/Excel resources, while the UI
        # stays responsive and can present progress/cancel feedback.
        self._import_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='chromatic-import')
        self._workspace_import_state=None
        # HF51: immutable Sample objects make a same-session parse cache safe.
        # Cold-open still performs authoritative parsing; reopening an unchanged
        # QTX/CPX/Excel file reuses the parsed domain objects immediately.
        self._workspace_parse_cache={}
        # Hotfix49: keep expensive maintenance work off the GUI thread; browsing
        # Qt GUI thread. Library Munsell browsing no longer waits for full-spectrum renotation.
        self._maintenance_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='chromatic-maint')
        self._munsell_index_state=None
        # HF67: palette spectral/Munsell ordering can be CPU-heavy (notably the
        # O(n²) spectral path). Keep it off the Qt GUI thread and independent of
        # library-maintenance jobs so opening/sorting a 300+ card plan cannot
        # freeze the application event loop.
        self._card_sort_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='chromatic-card-sort')
        # Navigation preferences are private to the signed-in user.
        self._nav_prefix = f"navigation/user_{self.current_user.user_id}/"
        self.loaded_files: dict[str, dict] = {}
        self.samples: list[Sample] = []
        self.manual_order: list[str] = []
        self.hidden_cards: set[str] = set()
        self._lab_cache = {}
        self._xyz_cache = {}
        self._hue_order_cache = {}
        # HF86: exact Munsell C/2° renotation results survive application restarts.
        # Repeated QTX files therefore pay the ~50s science cost only for spectra
        # not seen before, while the scientific formula itself stays unchanged.
        cache_root=runtime_root()/'cache'
        self._munsell_cache_path=cache_root/'munsell_c2_v1.json'; self._munsell_persistent_cache={}; self._munsell_cache_dirty=False
        try:
            if self._munsell_cache_path.exists():
                raw=json.loads(self._munsell_cache_path.read_text(encoding='utf-8'))
                if isinstance(raw,dict):self._munsell_persistent_cache=raw
        except Exception:self._munsell_persistent_cache={}
        self._munsell_cache_flush_timer=QTimer(self); self._munsell_cache_flush_timer.setSingleShot(True); self._munsell_cache_flush_timer.setInterval(700); self._munsell_cache_flush_timer.timeout.connect(self._flush_munsell_cache)
        self._sample_icon_cache = {}
        self._library_picker_cache = None
        self._library_view_cache = None
        self._library_data_revision = 0
        # HF126: cache only the lightweight scalar Find index.  A query at
        # D65/10° no longer deserialises every spectral payload.
        self._find_index_cache_revision = -1
        self._find_index_cache = []
        self._tiles_keys = None
        # Formal-library cards are virtualised: keep only enough QWidget cards for
        # one visible page and recycle them instead of accumulating one widget per
        # colour across thousands of records. _tile_pool maps *current page* keys.
        self._tile_pool: dict[str, ColorTile] = {}
        self._tile_slots: list[ColorTile] = []
        # Workbench results are expensive under alternate illuminants. Cache science
        # results without changing formulas; keys include the immutable measurement
        # data so edits cannot reuse stale calculations.
        self._workbench_color_cache = {}
        self._workbench_pair_cache = {}
        self._workbench_refreshing = False
        # Formal-library pagination. P3-1 uses SQLite LIMIT/OFFSET for supported
        # browse paths while keeping Hotfix26's page-sized recycled ColorTile pool.
        self.library_page = 0
        _exact_visible=self._data_scope_policy.get('_exact_visible_paths') or []
        _formal_visible=bool(self._data_scope_policy.get('unrestricted') or any(str(p)!='官方色库' and not str(p).startswith('官方色库/') for p in _exact_visible))
        _official_visible=bool(self._data_scope_policy.get('unrestricted') or any(str(p).startswith('官方色库/') for p in _exact_visible))
        self.library_scope = '正式色库' if _formal_visible or not _official_visible else '官方色库'
        self.library_page_size = 0  # 0 = auto-fill the visible card canvas
        self._library_total_items = 0
        self._library_grid_cols = 1
        self._library_grid_rows = 1
        self._library_card_width = 136
        self._last_effective_page_size = 1
        self._saved_customer_by_path = {}
        self.sample_runtime_overrides: dict[str, Sample] = {}
        # 搜索/筛选输入采用轻量防抖，避免每敲一个字符就重建大量色卡控件。
        self._tile_rebuild_timer = QTimer(self)
        self._tile_rebuild_timer.setSingleShot(True)
        self._tile_rebuild_timer.setInterval(200)
        self._tile_rebuild_timer.timeout.connect(self.build_tiles)
        self.library_sort_key = "manual"
        self.library_sort_desc = True
        self._library_sort_buttons = {}
        self.card_sort_key = None
        self.card_sort_desc = True
        self.hue_order_method = "Munsell"  # v0.8.5: 色卡实体编排固定使用 Munsell 感知色相，移除 CAM16-UCS 切换
        self._card_sort_buttons = {}
        self.find_standard = None
        self.find_mode_active = False
        self.find_scores = {}
        self.find_sessions = []  # [{id, sample, title}]，支持同时打开多个查色标准
        # Hotfix58: None = all data authorised by RBAC; list = selected resource subtrees.
        self.find_scope_selection = None
        self.active_find_session_id = None
        self.library_has_explicit_view = False
        self.library_selected_keys = set()
        # 色库客户浏览采用“网页标签”模型：可同时打开多个客户、收起/恢复/关闭。
        self.open_library_customers: list[str] = []
        self.minimized_library_customers: list[str] = []
        self.active_library_customer: str | None = None
        self.workbenches: list[dict] = []
        self.color_cards: list[dict] = []
        self.active_color_card_id: str | None = None
        self.card_plan_windows: dict[str, ColorCardPlanWindow] = {}
        self.card_subwindows: dict[str, QMdiSubWindow] = {}
        self._mdi_task_buttons: dict[QMdiSubWindow, QToolButton] = {}
        self._mdi_minimized_windows: set[QMdiSubWindow] = set()
        self._workspace_document_windows: list[QMdiSubWindow] = []
        self.active_card_customer_path: str | None = None
        self.active_workbench_id: str | None = None
        self._primary_windows_arranged = False
        self._sample_detail_windows: dict[str, SampleDetailsDialog] = {}
        self._lab2d_windows = []
        self.illuminant = "D65"
        self.observer = 10
        self.active_formula = "delta_e00"
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        self._load_thresholds()
        self.setWindowTitle(f"Chromatic Analysis · v0.14.6.4 · {BUILD_LABEL} · Studio Taskbar & Data Browser Preview 1")
        self.setAcceptDrops(True)
        # HF130: responsive MDI shell + adaptive sidebar state.  This stays
        # presentation-only: business widgets/models/actions keep the same objects
        # and handlers.  Until the user explicitly toggles the sidebar, small
        # workspaces manage it automatically; once toggled, that preference is kept.
        self._workspace_compact_mode = None
        self._sidebar_setting_key = 'ui/studio_sidebar_collapsed'
        self._sidebar_has_manual_preference = self.settings.contains(self._sidebar_setting_key)
        _sidebar_saved = self.settings.value(self._sidebar_setting_key, False)
        self._sidebar_collapsed = (_sidebar_saved if isinstance(_sidebar_saved, bool)
                                   else str(_sidebar_saved).strip().lower() in ('1','true','yes','on'))
        self._library_inner_sidebar_key='ui/library_inner_sidebar_collapsed'
        self._library_inner_sidebar_manual=self.settings.contains(self._library_inner_sidebar_key)
        _lib_side=self.settings.value(self._library_inner_sidebar_key,False)
        self._library_inner_sidebar_collapsed=(_lib_side if isinstance(_lib_side,bool) else str(_lib_side).strip().lower() in ('1','true','yes','on'))
        self._responsive_layout_timer = QTimer(self)
        self._responsive_layout_timer.setSingleShot(True)
        self._responsive_layout_timer.setInterval(70)
        self._responsive_layout_timer.timeout.connect(self._apply_responsive_workspace_layout)
        # Windows reports logical pixels after display scaling. A fixed 1120×700
        # minimum exceeds the available desktop at 125–150% scaling and hides
        # the bottom of tables and MDI resize handles.
        screen=QApplication.primaryScreen()
        available=screen.availableGeometry() if screen is not None else None
        if available is not None:
            self.setMinimumSize(min(640,max(300,available.width()-64)),min(420,max(240,available.height()-64)))
            self.resize(min(1500,max(300,available.width()-24)),min(920,max(240,available.height()-24)))
        else:
            self.setMinimumSize(860,540);self.resize(1500,920)
        self._build_ui()
        self._apply_style()
        self.refresh()
        QApplication.instance().installEventFilter(self)
        # HF124: let Windows paint the complete home shell before restoring saved state.
        QTimer.singleShot(180, self.restore_saved_files)

    def _load_thresholds(self):
        for key in FORMULA_KEYS:
            v = self.settings.value(f"threshold/{key}", DEFAULT_THRESHOLDS[key])
            try:
                self.thresholds[key] = float(v)
            except (ValueError, TypeError):
                self.thresholds[key] = DEFAULT_THRESHOLDS[key]

    def _save_thresholds(self):
        for key, v in self.thresholds.items():
            self.settings.setValue(f"threshold/{key}", v)

    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root)
        lay=QVBoxLayout(root); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)

        # Compatibility page stack. All mature tools remain the same objects and
        # call the same handlers; this section only replaces the visual shell.
        self.nav_group=QButtonGroup(self); self.nav_group.setExclusive(True)
        self.nav_library=QPushButton('正式色库'); self.nav_find=QPushButton('查色 / 找色'); self.nav_cards=QPushButton('色卡编排'); self.nav_compare=QPushButton('比色工作台'); self.nav_spectrum=QPushButton('光谱分析')
        for b in (self.nav_library,self.nav_find,self.nav_cards,self.nav_compare,self.nav_spectrum): b.setCheckable(True); b.hide(); self.nav_group.addButton(b)
        self.nav_library.clicked.connect(lambda:self.show_page(0)); self.nav_find.clicked.connect(self.show_find_page); self.nav_cards.clicked.connect(lambda:self.show_page(2)); self.nav_compare.clicked.connect(lambda:self.show_page(3)); self.nav_spectrum.clicked.connect(lambda:self.show_page(4))
        self.page_title=QLabel('工作台'); self.page_title.hide()

        self.light=QComboBox(self); self.light.addItems(SUPPORTED_ILLUMINANTS); self.light.setCurrentText('D65'); self.light.currentTextChanged.connect(self.conditions_changed)
        self.observer_box=QComboBox(self); self.observer_box.addItems(['10°','2°']); self.observer_box.setCurrentText('10°'); self.observer_box.currentTextChanged.connect(self.conditions_changed)
        self.light.hide(); self.observer_box.hide()
        self.menuBar().hide()

        # Existing actions / shortcuts are preserved verbatim.
        # Old folder data stays readable internally; no separate folder entry or shortcut.
        self.action_new_folder=QAction('新建工作区文件夹…',self); self.action_new_folder.triggered.connect(self.new_workspace_folder)
        self.action_new_window=QAction('新建文件窗口',self); self.action_new_window.setShortcut(QKeySequence('Ctrl+Shift+N')); self.action_new_window.triggered.connect(self.new_workspace_window)
        self.action_open_qtx=QAction('打开 QTX…',self); self.action_open_qtx.setShortcut(QKeySequence.Open); self.action_open_qtx.triggered.connect(self.open_files)
        self.action_open_cpx=QAction('打开 CPX…',self); self.action_open_cpx.setShortcut(QKeySequence('Ctrl+Shift+P')); self.action_open_cpx.triggered.connect(self.open_cpx_files)
        self.action_open_excel=QAction('打开 Excel…',self); self.action_open_excel.setShortcut(QKeySequence('Ctrl+Shift+O')); self.action_open_excel.triggered.connect(self.open_excel_files)
        self.action_open_workfile=QAction('打开个人工作文件…',self); self.action_open_workfile.triggered.connect(self.open_workfiles)
        self.action_open_folder=QAction('打开 QTX 文件夹…',self); self.action_open_folder.setShortcut(QKeySequence('Ctrl+Shift+Q')); self.action_open_folder.triggered.connect(self.open_qtx_folder)
        self.action_find=QAction('查色 / 找色',self); self.action_find.setShortcut(QKeySequence('Ctrl+1')); self.action_find.triggered.connect(lambda:self.show_page(1))
        self.action_compare=QAction('比色工作台',self); self.action_compare.setShortcut(QKeySequence('Ctrl+2')); self.action_compare.triggered.connect(lambda:self.show_page(3))
        self.action_cards=QAction('色卡编排',self); self.action_cards.setShortcut(QKeySequence('Ctrl+3')); self.action_cards.triggered.connect(lambda:self.show_page(2))
        self.action_spectrum=QAction('光谱分析',self); self.action_spectrum.setShortcut(QKeySequence('Ctrl+4')); self.action_spectrum.triggered.connect(lambda:self.show_page(4))
        self.action_library=QAction('正式色库',self); self.action_library.setShortcut(QKeySequence('Ctrl+5')); self.action_library.triggered.connect(self.open_library_overview)
        self.action_users=QAction('用户与权限…',self); self.action_users.triggered.connect(self.manage_users)
        self.action_customer_folder=QAction('打开客户数据文件夹',self); self.action_customer_folder.triggered.connect(self.open_customer_data_folder)
        self.action_data_management=QAction('数据管理中心…',self); self.action_data_management.triggered.connect(self.open_data_management)
        self.action_quit=QAction('退出',self); self.action_quit.setShortcut(QKeySequence.Quit); self.action_quit.triggered.connect(self.close)
        for action in (self.action_new_window,self.action_open_qtx,self.action_open_cpx,self.action_open_excel,self.action_open_folder,self.action_find,self.action_compare,self.action_cards,self.action_spectrum,self.action_library,self.action_quit):
            self.addAction(action)

        # Reference-image inspired header: brand on the left, roomy navigation,
        # global search and account on the right. No business logic lives here.
        bar=QToolBar(); bar.setMovable(False); bar.setFloatable(False); bar.setObjectName('workspaceBar'); lay.addWidget(bar)
        # HF130: ChatGPT-style persistent sidebar toggle.  The sidebar itself lives
        # inside the Studio home page, while this button stays reachable even when
        # that sidebar is fully collapsed and its width is returned to the canvas.
        self.sidebar_toggle_btn=QToolButton(); self.sidebar_toggle_btn.setObjectName('sidebarToggleButton')
        self.sidebar_toggle_btn.setCheckable(True); self.sidebar_toggle_btn.setAutoRaise(True)
        self.sidebar_toggle_btn.setIcon(self._sidebar_toggle_icon(False)); self.sidebar_toggle_btn.setIconSize(QSize(20,20))
        self.sidebar_toggle_btn.setFixedSize(34,34); self.sidebar_toggle_btn.setToolTip('收起左侧导航')
        self.sidebar_toggle_btn.clicked.connect(self._toggle_studio_sidebar)
        self.sidebar_toggle_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar_toggle_btn.customContextMenuRequested.connect(self._show_sidebar_toggle_menu)
        bar.addWidget(self.sidebar_toggle_btn)
        logo=QLabel(); logo.setObjectName('brandMark'); logo.setFixedSize(22,22); pix=QPixmap(22,22); pix.fill(Qt.transparent); pp=QPainter(pix);
        for rr,cc,col in [(0,0,'#2F6FF4'),(0,1,'#21B878'),(1,0,'#8B6EE8'),(1,1,'#F7A623')]: pp.fillRect(cc*11,rr*11,9,9,QColor(col))
        pp.end(); logo.setPixmap(pix); bar.addWidget(logo)
        brandbox=QWidget(); brandbox.setFixedWidth(205); self.workspace_brandbox=brandbox; brandlay=QVBoxLayout(brandbox); brandlay.setContentsMargins(6,0,8,0); brandlay.setSpacing(0)
        brandrow=QHBoxLayout(); brandrow.setContentsMargins(0,0,0,0); brandrow.setSpacing(8); brand=QLabel('Chromatic Analysis'); brand.setObjectName('workspaceBrand'); version=QLabel('v0.14.6.4'); version.setObjectName('brandVersion'); brandrow.addWidget(brand); brandrow.addWidget(version); brandrow.addStretch(1); tagline=QLabel('Color Drives a Better World'); tagline.setObjectName('brandTagline'); brandlay.addLayout(brandrow); brandlay.addWidget(tagline); bar.addWidget(brandbox)

        # Two primary destinations. All analysis functions live under Tools;
        # window actions are available from the blank desktop context menu.
        library_btn=QToolButton(); library_btn.setObjectName('topNav'); library_btn.setIcon(self.style().standardIcon(QStyle.SP_DirIcon)); library_btn.setIconSize(QSize(16,16)); library_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); library_btn.setText('色库'); library_btn.clicked.connect(self.open_library_overview); bar.addWidget(library_btn)
        tools_btn=QToolButton(); tools_btn.setObjectName('topNav'); tools_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView)); tools_btn.setIconSize(QSize(16,16)); tools_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); tools_btn.setText('工具'); tm=QMenu(tools_btn)
        for action in (self.action_compare,self.action_find,self.action_cards,self.action_spectrum):tm.addAction(action)
        tools_btn.setMenu(tm); tools_btn.setPopupMode(QToolButton.InstantPopup); bar.addWidget(tools_btn)

        spacer=QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred); bar.addWidget(spacer)
        self.global_search=QLineEdit(); self.global_search.setObjectName('globalSearch'); self.global_search.setPlaceholderText('搜索色号、名称或备注...'); self.global_search.setMinimumWidth(110); self.global_search.setMaximumWidth(270); self.global_search.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Preferred); self.global_search.returnPressed.connect(self.run_global_search); bar.addWidget(self.global_search)
        avatar=QLabel((self.current_user.display_name or self.current_user.username or 'A')[:1].upper()); avatar.setObjectName('avatarChip'); avatar.setAlignment(Qt.AlignCenter); avatar.setFixedSize(30,30); bar.addWidget(avatar)
        account=QToolButton(); account.setObjectName('accountChip'); account.setText(self.current_user.display_name or self.current_user.username); am=QMenu(account)
        if self.is_admin:
            am.addAction(self.action_users)
            am.addAction(self.action_data_management)
        am.addSeparator(); am.addAction(self.action_quit); account.setMenu(am); account.setPopupMode(QToolButton.MenuButtonPopup); bar.addWidget(account)

        # Keep every mature tool page exactly once. The hidden stack is retained as a
        # compatibility owner, but visible navigation now opens each page in its own
        # persistent QMdiSubWindow so Formal Library and Compare can stay open together.
        # Build each mature tool page exactly once, but DO NOT put them into a hidden
        # QStackedWidget.  A hidden QStackedWidget propagates the hidden state to its
        # pages on Windows; reparenting those pages into QMdiSubWindow can therefore
        # produce a blank white tool window even though all controls still exist.
        #
        # The stack object is retained only as a compatibility attribute for older
        # callers.  Tool pages themselves are owned by their persistent MDI windows.
        self.tool_pages=[self.build_library(), self.build_find_page(), self.build_color_cards_page(), self.build_workbench_page(), self.placeholder()]
        for page in self.tool_pages[:4]:page.setAcceptDrops(True)
        self.stack=QStackedWidget(root)
        self.stack.hide()
        self.tool_subwindows={}

        self.workspace_tabs=QTabWidget(); self.workspace_tabs.setObjectName('workspaceTabs'); self.workspace_tabs.setTabsClosable(True); self.workspace_tabs.setMovable(True); self.workspace_tabs.setContextMenuPolicy(Qt.CustomContextMenu); self.workspace_tabs.customContextMenuRequested.connect(self.workspace_context_menu); self.workspace_tabs.tabCloseRequested.connect(self.close_workspace_tab); lay.addWidget(self.workspace_tabs,1)
        self.workspace_home=self.build_workspace_home(); self.workspace_tabs.addTab(self.workspace_home,'首页')
        self.workspace_tabs.tabBar().setTabButton(0,QTabBar.ButtonPosition.RightSide,None)

        # Compatibility alias retained; the visible giant Open button is intentionally
        # removed. Existing code/tests can still trigger the original action.
        self.quick_import_qtx_btn=QPushButton(); self.quick_import_qtx_btn.hide(); self.quick_import_qtx_btn.clicked.connect(self.open_files)
        self.setStatusBar(QStatusBar())
        self.status_identity=QLabel(f'{self.current_user.display_name} · {"管理者" if self.is_admin else "使用者"}'); self.status_identity.setObjectName('statusIdentity')
        self.status_clock=QLabel(''); self.status_clock.setObjectName('statusClock')
        self.statusBar().addPermanentWidget(self.status_identity); self.statusBar().addPermanentWidget(self.status_clock)
        self._clock_timer=QTimer(self); self._clock_timer.setInterval(1000); self._clock_timer.timeout.connect(self._update_status_clock); self._clock_timer.start(); self._update_status_clock()
        self.statusBar().showMessage('就绪 · 右键空白区域新建文件窗口，或直接打开 / 拖入 QTX、CPX、Excel')
        QTimer.singleShot(0,self._apply_responsive_workspace_layout)


    def _update_status_clock(self):
        self.status_clock.setText(datetime.datetime.now().strftime('%Y-%m-%d  %H:%M'))

    def _sidebar_toggle_icon(self, collapsed=False):
        """Small split-panel icon matching the light Studio chrome."""
        pix=QPixmap(20,20); pix.fill(Qt.transparent)
        painter=QPainter(pix); painter.setRenderHint(QPainter.Antialiasing)
        outline=QColor('#5F6F85'); fill=QColor('#E8EEF7') if not collapsed else QColor('#F4F7FB')
        painter.setPen(QPen(outline,1.35)); painter.setBrush(QColor('#FFFFFF'))
        painter.drawRoundedRect(QRectF(2.2,3.0,15.6,14.0),3.0,3.0)
        painter.setPen(Qt.NoPen); painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(3.4,4.2,4.6,11.6),1.8,1.8)
        painter.setPen(QPen(outline,1.15)); painter.drawLine(QPointF(8.8,4.0),QPointF(8.8,16.0))
        if collapsed:
            painter.drawLine(QPointF(11.2,8.0),QPointF(14.0,10.0)); painter.drawLine(QPointF(14.0,10.0),QPointF(11.2,12.0))
        else:
            painter.drawLine(QPointF(14.0,8.0),QPointF(11.2,10.0)); painter.drawLine(QPointF(11.2,10.0),QPointF(14.0,12.0))
        painter.end(); return QIcon(pix)

    def _set_studio_sidebar_collapsed(self, collapsed, user_initiated=False):
        """Collapse the Studio sidebar without changing any navigation actions."""
        collapsed=bool(collapsed)
        self._sidebar_collapsed=collapsed
        if user_initiated:
            self._sidebar_has_manual_preference=True
            self.settings.setValue(self._sidebar_setting_key,collapsed)
        sidebar=getattr(self,'studio_sidebar',None)
        if sidebar is not None:
            sidebar.setVisible(not collapsed)
        button=getattr(self,'sidebar_toggle_btn',None)
        if button is not None:
            button.blockSignals(True); button.setChecked(collapsed); button.blockSignals(False)
            button.setIcon(self._sidebar_toggle_icon(collapsed))
            button.setToolTip(('展开左侧导航' if collapsed else '收起左侧导航')+' · 右键可恢复自动适配')
            button.setAccessibleName('展开左侧导航' if collapsed else '收起左侧导航')
        # Visibility changes alter the MDI viewport width. Defer one layout pass so
        # maximisation/card/table geometry sees the final available rectangle.
        self._schedule_responsive_workspace_layout()

    def _toggle_studio_sidebar(self, _checked=False):
        self._set_studio_sidebar_collapsed(not bool(getattr(self,'_sidebar_collapsed',False)),True)

    def _show_sidebar_toggle_menu(self, pos):
        button=getattr(self,'sidebar_toggle_btn',None)
        if button is None:return
        menu=QMenu(button)
        auto=menu.addAction('自动适配屏幕'); auto.setCheckable(True); auto.setChecked(not self._sidebar_has_manual_preference)
        menu.addSeparator()
        expand=menu.addAction('展开左侧导航'); collapse=menu.addAction('收起左侧导航')
        chosen=menu.exec(button.mapToGlobal(pos))
        if chosen==auto:
            self._sidebar_has_manual_preference=False
            self.settings.remove(self._sidebar_setting_key)
            self._set_studio_sidebar_collapsed(self._workspace_should_compact(),False)
        elif chosen==expand:
            self._set_studio_sidebar_collapsed(False,True)
        elif chosen==collapse:
            self._set_studio_sidebar_collapsed(True,True)

    def build_workspace_home(self):
        """Reference-image shell: slim left navigation + MDI desktop.

        Only presentation/parenting changes here. Every action points to the same
        existing handlers and every mature tool page is reused unchanged.
        """
        page=QWidget(); page.setObjectName('workspaceHome')
        outer=QHBoxLayout(page); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        sidebar=QFrame(); sidebar.setObjectName('studioSidebar'); sidebar.setFixedWidth(218)
        self.studio_sidebar=sidebar
        sl=QVBoxLayout(sidebar); sl.setContentsMargins(14,16,14,14); sl.setSpacing(6)
        self.studio_sidebar_layout=sl

        open_btn=QToolButton(); open_btn.setObjectName('primaryOpenButton'); open_btn.setText('＋  打开文件'); open_btn.setToolButtonStyle(Qt.ToolButtonTextOnly); open_btn.setPopupMode(QToolButton.MenuButtonPopup)
        om=QMenu(open_btn); om.addAction(self.action_open_qtx); om.addAction(self.action_open_cpx); om.addAction(self.action_open_excel); om.addAction(self.action_open_workfile); om.addSeparator(); om.addAction(self.action_open_folder)
        open_btn.setMenu(om); open_btn.clicked.connect(self.open_files); sl.addWidget(open_btn)
        for label, kind, glyph, color in [('最近打开','recent','◷','#536888'),('我的收藏','favorites','☆','#536888'),('常用色库','common','◇','#536888')]:
            button=QToolButton(sidebar); button.setObjectName('sideFile'); button.setText(label)
            button.setIcon(self._sidebar_icon(glyph,color)); button.setIconSize(QSize(18,18))
            button.setPopupMode(QToolButton.InstantPopup); button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setMinimumHeight(34)
            menu=QMenu(button); menu.aboutToShow.connect(lambda m=menu,k=kind:self._populate_nav_menu(m,k))
            button.setMenu(menu); sl.addWidget(button)
        sl.addSpacing(4)

        # The top Workbench and Tools menus own these actions. Keep the legacy
        # nav buttons hidden for existing shortcuts and old page handlers.
        for btn in (self.nav_library,self.nav_compare,self.nav_find,self.nav_cards,self.nav_spectrum):
            btn.hide()

        sl.addSpacing(12)
        local_title=QLabel('本地文件'); local_title.setObjectName('sideGroupTitle'); sl.addWidget(local_title)
        def local_button(text, suffix, action, glyph, color):
            b=QToolButton(); b.setObjectName('sideFile'); b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setIcon(self._sidebar_icon(glyph,color)); b.setIconSize(QSize(18,18)); b.setText(text); b.setToolTip(suffix); b.clicked.connect(action); return b
        sl.addWidget(local_button('QTX 文件     .qtx','.qtx',self.open_files,'Q','#8A8CCB'))
        sl.addWidget(local_button('CPX 文件     .cpx','.cpx',self.open_cpx_files,'C','#366EE8'))
        sl.addWidget(local_button('Excel 文件   .xlsx','.xlsx',self.open_excel_files,'X','#209864'))
        sl.addWidget(local_button('个人工作文件  .chromatic','.chromatic',self.open_workfiles,'W','#735AC9'))
        sl.addSpacing(8)
        # Hotfix29: official-library shortcuts reflect the database.  Empty
        # placeholders such as RAL/NCS are no longer shown before import.
        self.official_nav_title=QLabel('官方色库'); self.official_nav_title.setObjectName('sideGroupTitle'); sl.addWidget(self.official_nav_title)
        self.official_nav_container=QWidget(sidebar)
        self.official_nav_layout=QVBoxLayout(self.official_nav_container)
        self.official_nav_layout.setContentsMargins(0,0,0,0); self.official_nav_layout.setSpacing(2)
        sl.addWidget(self.official_nav_container)
        self._refresh_official_sidebar()
        sl.addStretch(1)

        drop=QFrame(); drop.setObjectName('sideDropZone'); drop.setMinimumHeight(112)
        dl=QVBoxLayout(drop); dl.setContentsMargins(14,14,14,14); dl.setSpacing(5)
        di=QLabel('⇧'); di.setObjectName('sideDropIcon'); di.setAlignment(Qt.AlignCenter)
        dt=QLabel('拖入文件到这里'); dt.setObjectName('sideDropTitle'); dt.setAlignment(Qt.AlignCenter)
        dh=QLabel('支持 QTX / CPX / Excel'); dh.setObjectName('sideDropHint'); dh.setAlignment(Qt.AlignCenter)
        dl.addWidget(di); dl.addWidget(dt); dl.addWidget(dh); sl.addWidget(drop)
        outer.addWidget(sidebar)

        stage=QWidget(page); stage.setObjectName('studioStage')
        stage_lay=QVBoxLayout(stage); stage_lay.setContentsMargins(0,0,0,0); stage_lay.setSpacing(0)
        self.studio_mdi=StudioMdiArea(stage); self.studio_mdi.setObjectName('studioMdi')
        self.studio_mdi.workspaceMenuRequested.connect(lambda _gp:self.workspace_context_menu(QPoint()))
        self.studio_mdi.viewportResized.connect(lambda _w,_h:self._schedule_responsive_workspace_layout())
        # New windows are created only by right-clicking the empty desktop.
        stage_lay.addWidget(self.studio_mdi,1)
        self.studio_mdi.viewport().setAcceptDrops(True)
        self.drop_overlay=QFrame(self.studio_mdi.viewport())
        self.drop_overlay.setObjectName('workspaceDropOverlay')
        self.drop_overlay.setAttribute(Qt.WA_TransparentForMouseEvents,True)
        self.drop_overlay.setStyleSheet('QFrame#workspaceDropOverlay{background:rgba(234,243,255,224);border:3px dashed #3479E9;border-radius:18px;}')
        overlay_layout=QVBoxLayout(self.drop_overlay);overlay_layout.setContentsMargins(30,30,30,30)
        overlay_layout.addStretch(1)
        self.drop_title=QLabel('⇩  将文件拖放到这里');self.drop_title.setAlignment(Qt.AlignCenter)
        self.drop_title.setStyleSheet('color:#18478F;font-size:23px;font-weight:700;background:transparent;border:0;')
        self.drop_hint=QLabel('支持 QTX / CPX / Excel');self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setStyleSheet('color:#52719D;font-size:13px;background:transparent;border:0;')
        overlay_layout.addWidget(self.drop_title);overlay_layout.addWidget(self.drop_hint)
        overlay_layout.addStretch(1);self.drop_overlay.hide()
        self.studio_taskbar=QFrame(stage); self.studio_taskbar.setObjectName('studioTaskbar'); self.studio_taskbar.setMinimumHeight(40); self.studio_taskbar.hide()
        self.studio_taskbar_layout=QHBoxLayout(self.studio_taskbar); self.studio_taskbar_layout.setContentsMargins(8,5,8,5); self.studio_taskbar_layout.setSpacing(6)
        task_title=QLabel('已最小化'); task_title.setObjectName('taskbarLabel'); self.studio_taskbar_layout.addWidget(task_title)
        self.studio_taskbar_layout.addStretch(1)
        stage_lay.addWidget(self.studio_taskbar)
        outer.addWidget(stage,1)
        return page

    def _sidebar_icon(self,glyph,color):
        pix=QPixmap(22,22); pix.fill(Qt.transparent)
        painter=QPainter(pix); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen); painter.setBrush(QColor(color)); painter.drawRoundedRect(1,1,20,20,4,4)
        font=painter.font(); font.setBold(True); font.setPointSize(10 if len(glyph)==1 else 8); painter.setFont(font)
        painter.setPen(QColor('white')); painter.drawText(pix.rect(),Qt.AlignCenter,glyph); painter.end()
        return QIcon(pix)

    def _official_library_names(self):
        """Return real top-level official libraries that contain saved QTX data."""
        names=set()
        for row in self.store.list_files():
            customer=str(row.customer or '')
            if customer.startswith('官方色库/'):
                relative=customer.split('/',1)[1].strip('/')
                if relative:
                    names.add(relative.split('/',1)[0])
        return sorted(names,key=str.casefold)

    def _official_nav_icon(self,name):
        known={
            'pantone':('P','#F3A327'),
            'ral':('R','#E96B44'),
            'ncs':('N','#2D527A'),
            '中国传统色':('中','#D83F4A'),
        }
        glyph,color=known.get(str(name).casefold(),('', '#64748B'))
        if not glyph:
            leaf=str(name).rstrip('/').split('/')[-1].strip()
            glyph=(leaf[:1] or '库').upper()
        return self._sidebar_icon(glyph,color)

    def _refresh_official_sidebar(self):
        if not hasattr(self,'official_nav_layout'):
            return
        while self.official_nav_layout.count():
            item=self.official_nav_layout.takeAt(0)
            widget=item.widget()
            if widget is not None:
                widget.deleteLater()
        names=self._official_library_names()
        self.official_nav_title.setVisible(bool(names))
        self.official_nav_container.setVisible(bool(names))
        if not names:
            return
        # Keep the left rail compact.  The first six real official libraries
        # are direct shortcuts; extras live under "更多…".
        for name in names[:6]:
            button=QToolButton(self.official_nav_container); button.setObjectName('sideFile')
            button.setText(name.replace('/',' / ')); button.setIcon(self._official_nav_icon(name)); button.setIconSize(QSize(18,18))
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _=False,c=name:self._open_named_library(c))
            self.official_nav_layout.addWidget(button)
        if len(names)>6:
            more=QToolButton(self.official_nav_container); more.setObjectName('sideFile'); more.setText('更多…')
            more.setIcon(self._sidebar_icon('···','#8193AC')); more.setIconSize(QSize(18,18))
            more.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); more.setPopupMode(QToolButton.InstantPopup)
            menu=QMenu(more)
            for name in names[6:]:
                menu.addAction(name.replace('/',' / '),lambda _=False,n=name:self._open_named_library(n))
            more.setMenu(menu); self.official_nav_layout.addWidget(more)

    def _populate_official_menu(self,menu):
        menu.clear(); names=self._official_library_names()
        for name in names:
            menu.addAction(name.replace('/',' / '),lambda _=False,n=name:self._open_named_library(n))
        if not names:
            menu.addAction('暂无官方色库').setEnabled(False)

    def _force_studio_layout(self):
        """Flush only layout work needed by the Studio shell before sizing MDI children.

        This avoids reading a stale viewport immediately after sidebar/taskbar changes.
        It deliberately does not process user input or rebuild page contents.
        """
        for obj in (getattr(self,'workspace_home',None),
                    getattr(getattr(self,'studio_mdi',None),'parentWidget',lambda:None)(),
                    getattr(self,'studio_taskbar',None)):
            if obj is None:continue
            try:
                lay=obj.layout()
                if lay is not None:
                    lay.invalidate(); lay.activate()
            except RuntimeError:
                pass
        try:
            QApplication.sendPostedEvents(None,QEvent.LayoutRequest)
        except Exception:
            pass

    def _sync_studio_taskbar_visibility(self):
        bar=getattr(self,'studio_taskbar',None)
        if bar is None:return
        registry=getattr(self,'_mdi_minimized_windows',set())
        # Prune stale QObject wrappers without letting one destroyed window hide the
        # whole taskbar.  Visibility is driven by window state, not button lifetime.
        for sub in list(registry):
            try:sub.windowTitle()
            except RuntimeError:registry.discard(sub)
        should_show=bool(registry)
        if bar.isVisible()!=should_show:
            bar.setVisible(should_show)
            self._force_studio_layout()
            self._schedule_responsive_workspace_layout()

    def _minimize_to_studio_taskbar(self,sub):
        if sub is None:return
        self._mdi_minimized_windows.add(sub)
        sub.setProperty('studio_taskbar_minimized',True)
        button=self._mdi_task_buttons.get(sub)
        if button is None:
            button=QToolButton(self.studio_taskbar); button.setObjectName('studioTaskButton')
            button.setIcon(self.style().standardIcon(QStyle.SP_TitleBarNormalButton)); button.setIconSize(QSize(14,14))
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); button.setText(sub.windowTitle() or '窗口')
            button.setToolTip('单击恢复窗口'); button.clicked.connect(lambda _checked=False,s=sub:self._restore_from_studio_taskbar(s))
            self.studio_taskbar_layout.insertWidget(max(1,self.studio_taskbar_layout.count()-1),button)
            self._mdi_task_buttons[sub]=button
        else:
            button.setText(sub.windowTitle() or '窗口')
        self._sync_studio_taskbar_visibility(); self.studio_mdi.refresh_empty_state()

    def _remove_studio_task_button(self,sub):
        if getattr(self,'_app_closing',False) or QApplication.closingDown():
            return
        if sub is not None:self._mdi_minimized_windows.discard(sub)
        button=self._mdi_task_buttons.pop(sub,None)
        if button is not None:button.deleteLater()
        try:
            if sub is not None:sub.setProperty('studio_taskbar_minimized',False)
        except RuntimeError:
            pass
        self._sync_studio_taskbar_visibility()

    def _restore_from_studio_taskbar(self,sub):
        if sub is None:return
        self._remove_studio_task_button(sub)
        self._force_studio_layout()
        if sub.isMinimized():sub.showNormal()
        sub.setProperty('studio_taskbar_minimized',False)
        index=sub.property('tool_index')
        if index is not None and self._workspace_should_compact():
            self._fit_tool_subwindow_to_viewport(sub,int(index),False)
        sub.show(); sub.raise_(); self.studio_mdi.setActiveSubWindow(sub)
        if hasattr(sub,'_sync_embedded_client_layout'):sub._sync_embedded_client_layout()
        self.studio_mdi.refresh_empty_state(); self._schedule_responsive_workspace_layout()

    def show_home(self):
        if hasattr(self,'workspace_tabs') and self.workspace_tabs.count():
            self.workspace_tabs.setCurrentIndex(0)
        if hasattr(self,'studio_mdi'):
            self.studio_mdi.refresh_empty_state()

    def _nav_items(self, name):
        try:
            value=json.loads(self.settings.value(self._nav_prefix+name,'[]'))
            return value if isinstance(value,list) else []
        except (ValueError,TypeError):
            return []

    def _set_nav_items(self, name, values):
        self.settings.setValue(self._nav_prefix+name,json.dumps(values,ensure_ascii=False))

    def _toggle_nav_item(self, name, value):
        items=self._nav_items(name)
        if value in items:items.remove(value)
        else:items.insert(0,value)
        self._set_nav_items(name,items)
        self.statusBar().showMessage('已更新我的收藏' if name in ('favorite_samples','favorite_qtx') else '已更新常用色库',3000)

    def _remember_recent(self, path):
        if not path:return
        path=str(Path(path).resolve())
        items=[p for p in self._nav_items('recent_files') if p!=path]
        self._set_nav_items('recent_files',[path]+items[:19])

    def _open_named_library(self, customer):
        if not self.can_use('library_view'):return
        names=set(self.store.customer_counts()) | set(self.store.list_customer_groups())
        official='官方色库/'+customer
        if official in names or customer in names:
            selected=official if official in names else customer
            scope='官方色库' if selected.startswith('官方色库/') else '正式色库'
            self._switch_library_scope_and_open(scope, selected)
        else:QMessageBox.information(self,'官方色库',f'尚未导入 {customer} 色库。可由管理员从文件导入。')

    def _populate_nav_menu(self, menu, kind):
        menu.clear()
        if kind=='recent':
            paths=self._nav_items('recent_files')
            for path in paths:
                a=menu.addAction(Path(path).name); a.setToolTip(path)
                a.setEnabled(Path(path).is_file())
                a.triggered.connect(lambda _=False,p=path:self.open_workspace_paths([p]))
            if paths:
                menu.addSeparator(); menu.addAction('清除最近打开',lambda:self._set_nav_items('recent_files',[]))
        elif kind=='common':
            names=self._nav_items('common_customers')
            existing=set(self.store.customer_counts()) | set(self.store.list_customer_groups())
            for customer in names:
                a=menu.addAction(customer); a.setEnabled(customer in existing)
                a.triggered.connect(lambda _=False,c=customer:self._open_named_library(c))
            if names:
                menu.addSeparator()
                remove=menu.addMenu('移除常用色库')
                for customer in names:remove.addAction(customer,lambda _=False,c=customer:self._toggle_nav_item('common_customers',c))
        else:
            favorite_files=self._nav_items('favorite_qtx')
            saved_by_path={str(Path(row.path).resolve()):row for row in self.store.list_files()}
            for path in favorite_files:
                row=saved_by_path.get(path)
                item=menu.addAction('★  '+Path(path).name)
                item.setToolTip(f'{row.customer if row else "已从色库移除"} · {path}')
                item.setEnabled(row is not None)
                if row:item.triggered.connect(lambda _=False,p=path:self.open_favorite_qtx(p))
            if favorite_files:
                remove_files=menu.addMenu('管理收藏的 QTX')
                for path in favorite_files:
                    remove_files.addAction('移除 '+Path(path).name,
                                           lambda _=False,p=path:self._toggle_nav_item('favorite_qtx',p))
            keys=self._nav_items('favorite_samples')
            bykey={sample_key(sm):sm for sm in self._samples_by_keys_on_demand(keys,full=True)} if keys else {}
            if keys and favorite_files:menu.addSeparator()
            for key in keys:
                sm=bykey.get(key)
                a=menu.addAction(sm.display_name if sm else Path(key.split('|',1)[0]).name+' · 已失效')
                a.setEnabled(sm is not None)
                if sm:a.triggered.connect(lambda _=False,s=sm:self.show_sample_details_dialog(s))
            if keys:
                menu.addSeparator()
                remove=menu.addMenu('移除收藏')
                for key in keys:
                    sm=bykey.get(key); label=sm.display_name if sm else key.rsplit('|',1)[-1]
                    remove.addAction(label,lambda _=False,k=key:self._toggle_nav_item('favorite_samples',k))
        if not menu.actions():menu.addAction('暂无内容').setEnabled(False)

    def open_favorite_qtx(self,path):
        if not self.can_use('library_view'):return
        canonical=str(Path(path).resolve())
        row=next((x for x in self.store.list_files() if str(Path(x.path).resolve())==canonical),None)
        if row is None:
            QMessageBox.information(self,'我的收藏','这个 QTX 已从色库移除。');return
        scope='官方色库' if row.customer.startswith('官方色库/') else '正式色库'
        self._switch_library_scope_and_open(scope, row.customer)
        samples=[replace(sm,source_file=canonical) for sm in self.store.load_samples(row.path)]
        self.loaded_files[canonical]={'samples':samples,'saved':True,'customer':row.customer}
        self.refresh_file_list()
        for i in range(self.file_list.count()):
            item=self.file_list.item(i)
            if item.data(Qt.UserRole)==canonical:
                self.file_list.setCurrentItem(item); break
        self.library_has_explicit_view=True;self.library_page=0;self._tiles_keys=None;self.build_tiles()

    def _favorite_qtx_from_samples(self,samples):
        saved={str(Path(row.path).resolve()) for row in self.store.list_files()}
        paths=list(dict.fromkeys(str(Path(sm.source_file).resolve()) for sm in samples
                                 if sm.source_file and str(Path(sm.source_file).resolve()) in saved))
        if not paths:return
        favorites=self._nav_items('favorite_qtx')
        if all(path in favorites for path in paths):favorites=[p for p in favorites if p not in paths]
        else:favorites=list(dict.fromkeys(paths+favorites))
        self._set_nav_items('favorite_qtx',favorites)
        self.statusBar().showMessage(f'已更新 {len(paths)} 个 QTX 文件的收藏',3000)

    def new_workspace_window(self):
        # A blank document has exactly the same editor as a dropped/opened file.
        title=f'新建文件 {len(self._workspace_document_windows)+1}'
        doc=self.add_workspace_document(title,[],'','工作文件')
        self.statusBar().showMessage('已新建文件窗口 · 可以拖入色样或文件',4000)
        return doc

    def close_current_workspace_window(self):
        if not hasattr(self,'workspace_tabs'): return
        # If the MDI desktop is active, close/hide its active internal window first.
        if self.workspace_tabs.currentIndex()==0 and hasattr(self,'studio_mdi'):
            sub=self.studio_mdi.activeSubWindow()
            if sub is not None:
                sub.close(); self.studio_mdi.refresh_empty_state(); return
        idx=self.workspace_tabs.currentIndex()
        if idx>0:self.close_workspace_tab(idx)

    def workspace_context_menu(self,pos):
        menu=QMenu(self)
        new=menu.addAction('新建文件窗口')
        if menu.exec(QCursor.pos())==new:self.new_workspace_window()

    def _tool_window_geometry(self,index):
        """HF129 UI-only default geometry for all primary tools.

        Large displays keep movable MDI windows, but use more of the available
        workspace. Small displays are handled by the responsive maximisation path.
        """
        vw=max(900,self.studio_mdi.viewport().width()); vh=max(620,self.studio_mdi.viewport().height())
        if index in (0,1,2,3,4):
            margin=18
            w=min(max(760,vw-margin*2),max(1040,int(vw*0.94)))
            h=min(max(500,vh-margin*2),max(660,int(vh*0.92)))
            x=max(margin,(vw-w)//2); y=max(margin,(vh-h)//2)
            return (x,y,w,h)
        return (54+index*18,42+index*16,max(760,int(vw*0.76)),max(500,int(vh*0.76)))

    def _workspace_should_compact(self, vw=None, vh=None):
        if not hasattr(self,'studio_mdi'):
            return False
        # HF130 uses the whole Studio page for the breakpoint, not the MDI viewport.
        # Otherwise hiding the 218 px sidebar can push the viewport across the
        # threshold and immediately expand it again (layout oscillation).
        home=getattr(self,'workspace_home',None)
        if home is not None and home.width()>0 and home.height()>0:
            vw,vh=home.width(),home.height()
        else:
            if vw is None: vw=self.studio_mdi.viewport().width()
            if vh is None: vh=self.studio_mdi.viewport().height()
        # Industrial-desktop rule: smaller work areas prioritise the active data
        # surface instead of squeezing an MDI window into an unusable rectangle.
        return int(vw) < 1450 or int(vh) < 820

    def _schedule_responsive_workspace_layout(self):
        timer=getattr(self,'_responsive_layout_timer',None)
        if timer is not None:
            timer.start()

    def _set_library_inner_sidebar_collapsed(self, collapsed, manual=False):
        collapsed=bool(collapsed); self._library_inner_sidebar_collapsed=collapsed
        if manual:
            self._library_inner_sidebar_manual=True; self.settings.setValue(self._library_inner_sidebar_key,collapsed)
        if hasattr(self,'library_files_panel'):self.library_files_panel.setVisible(not collapsed)
        if hasattr(self,'library_panel_expand_btn'):self.library_panel_expand_btn.setVisible(collapsed)
        if hasattr(self,'library_panel_collapse_btn'):self.library_panel_collapse_btn.setVisible(not collapsed)
        if hasattr(self,'library_outer_layout'):self.library_outer_layout.invalidate()

    def _apply_compact_tool_ui(self, compact):
        """Presentation-only density changes; no data/model/action semantics."""
        compact=bool(compact)
        # Global shell: reclaim width before shrinking any data table or colour card.
        if hasattr(self,'studio_sidebar'):
            self.studio_sidebar.setFixedWidth(176 if compact else 218)
        if hasattr(self,'studio_sidebar_layout'):
            self.studio_sidebar_layout.setContentsMargins(9 if compact else 14,10 if compact else 16,9 if compact else 14,9 if compact else 14)
            self.studio_sidebar_layout.setSpacing(3 if compact else 6)
        if hasattr(self,'workspace_brandbox'):
            self.workspace_brandbox.setFixedWidth(170 if compact else 205)
        if hasattr(self,'global_search'):
            self.global_search.setMaximumWidth(190 if compact else 270)

        # Formal Library: keep the tree, but make navigation denser and hide only
        # explanatory copy; filters, selection, paging and drag/drop stay untouched.
        if hasattr(self,'library_outer_layout'):
            self.library_outer_layout.setContentsMargins(9 if compact else 16,9 if compact else 16,9 if compact else 16,9 if compact else 16)
            self.library_outer_layout.setSpacing(9 if compact else 16)
        if hasattr(self,'library_files_panel'):
            self.library_files_panel.setFixedWidth(178 if compact else 220)
        if hasattr(self,'library_group_hint'):
            self.library_group_hint.setVisible(not compact)
        if hasattr(self,'library_content_layout'):
            self.library_content_layout.setSpacing(7 if compact else 10)
        if not getattr(self,'_library_inner_sidebar_manual',False) and hasattr(self,'studio_mdi'):
            self._set_library_inner_sidebar_collapsed(bool(compact and self.studio_mdi.viewport().width()<1180),False)

        # Find: remove non-essential explanatory line and reduce whitespace/drop zone.
        if hasattr(self,'find_body_layout'):
            self.find_body_layout.setContentsMargins(12 if compact else 24,9 if compact else 20,12 if compact else 24,10 if compact else 20)
            self.find_body_layout.setSpacing(7 if compact else 12)
        if hasattr(self,'find_intro_subtitle'):
            self.find_intro_subtitle.setVisible(not compact)
        if hasattr(self,'find_panel_layout'):
            self.find_panel_layout.setContentsMargins(8 if compact else 12,7 if compact else 10,8 if compact else 12,7 if compact else 10)
            self.find_panel_layout.setSpacing(5 if compact else 8)
        if hasattr(self,'find_qtx_drop'):
            self.find_qtx_drop.set_compact_mode(compact)
        if hasattr(self,'find_results_folder'):
            self.find_results_folder.setMinimumHeight(170 if compact else 205)
        if hasattr(self,'find_multi_table'):
            self.find_multi_table.setMinimumHeight(130 if compact else 150)
            self.find_multi_table.setMaximumHeight(240 if compact else 300)
        if hasattr(self,'find_action_hint'):self.find_action_hint.setVisible(not compact)
        if hasattr(self,'find_result_hint'):self.find_result_hint.setVisible(not compact)

        # Palette Studio: preserve the exact saved grid/column semantics; only reclaim
        # chrome around the internal MDI canvas.
        if hasattr(self,'card_workspace_layout'):
            self.card_workspace_layout.setContentsMargins(9 if compact else 18,7 if compact else 14,9 if compact else 18,7 if compact else 14)
            self.card_workspace_layout.setSpacing(4 if compact else 6)
        if hasattr(self,'card_workspace_info'):
            self.card_workspace_info.setVisible(not compact)

        # Compare: commands reflow into “更多” instead of forcing horizontal scroll.
        if hasattr(self,'workbench_body_layout'):
            self.workbench_body_layout.setContentsMargins(10 if compact else 20,8 if compact else 18,10 if compact else 20,8 if compact else 16)
            self.workbench_body_layout.setSpacing(6 if compact else 10)
        if hasattr(self,'workbench_tabs'):
            for i in range(self.workbench_tabs.count()):
                page=self.workbench_tabs.widget(i)
                if page is None:continue
                updater=getattr(page,'_responsive_action_updater',None)
                if updater is not None:updater(page.width())
                drop=getattr(page,'workbench_import_zone',None)
                if drop is not None:drop.set_compact_mode(compact)

    def _fit_tool_subwindow_to_viewport(self, sub, index=None, save_geometry=True):
        """HF131: fill the current MDI viewport while staying in *normal* state.

        ``QMdiSubWindow.showMaximized()`` looks attractive for a small screen, but on
        Windows it has two undesirable side effects for this application: the title
        bar is no longer draggable, and a page with a large sizeHint can make the
        maximised frame extend past the live MDI viewport while the shell/sidebar is
        still settling.  A fitted normal window gives the same usable area without
        either problem.  This method changes presentation geometry only.
        """
        if sub is None or not hasattr(self,'studio_mdi'):
            return
        self._force_studio_layout()
        viewport=self.studio_mdi.viewport(); vw=viewport.width(); vh=viewport.height()
        if vw < 320 or vh < 240:
            return

        # Respect an explicit user maximisation.  Only undo the old responsive
        # auto-maximised state left by earlier shell code.
        if sub.isMaximized():
            if bool(sub.property('responsive_auto_maximized')):
                sub.showNormal()
            else:
                return

        if save_geometry and not bool(sub.property('responsive_auto_fitted')):
            try:
                sub.setProperty('responsive_saved_geometry',sub.geometry())
            except RuntimeError:
                pass

        # Keep the whole title bar, frame and resize handles inside the *current*
        # viewport. Eight logical pixels are enough to show that this is still an
        # internal movable MDI window, while giving the data area essentially the
        # full screen on 1366×768-class displays.
        margin=8
        target_w=max(360,vw-margin*2)
        target_h=max(300,vh-margin*2)

        # The normal large-screen minimums are useful, but must never force a compact
        # window wider/taller than the live viewport. Store and restore them.
        base_w=sub.property('responsive_base_min_w')
        base_h=sub.property('responsive_base_min_h')
        if base_w is None or base_h is None:
            base=sub.minimumSize()
            base_w=max(0,base.width()); base_h=max(0,base.height())
            sub.setProperty('responsive_base_min_w',base_w)
            sub.setProperty('responsive_base_min_h',base_h)
        sub.setMinimumSize(min(int(base_w),target_w),min(int(base_h),target_h))

        sub.setGeometry(margin,margin,target_w,target_h)
        sub.setProperty('responsive_auto_fitted',True)
        sub.setProperty('responsive_auto_maximized',False)
        # Prevent the legacy first-show fitter from shrinking this freshly fitted
        # window again a few milliseconds later.
        if hasattr(sub,'_studio_first_open_fitted'):
            sub._studio_first_open_fitted=True
            sub._studio_first_open_scheduled=False
        widget=sub.widget()
        if widget is not None:
            widget.updateGeometry(); widget.update()
        if hasattr(sub,'_sync_embedded_client_layout'):
            sub._sync_embedded_client_layout()

    def _restore_tool_subwindow_from_compact(self, sub, index=None):
        """Restore a responsive-fitted window when the workspace becomes large."""
        if sub is None or not hasattr(self,'studio_mdi'):
            return
        if sub.isMaximized() and bool(sub.property('responsive_auto_maximized')):
            sub.showNormal()
        base_w=sub.property('responsive_base_min_w'); base_h=sub.property('responsive_base_min_h')
        if base_w is not None and base_h is not None:
            sub.setMinimumSize(int(base_w),int(base_h))
        if not bool(sub.property('responsive_auto_fitted')) and not bool(sub.property('responsive_auto_maximized')):
            return

        viewport=self.studio_mdi.viewport(); vw=viewport.width(); vh=viewport.height()
        saved=sub.property('responsive_saved_geometry')
        if isinstance(saved,QRect) and saved.isValid():
            w=min(max(360,saved.width()),max(360,vw-36))
            h=min(max(300,saved.height()),max(300,vh-36))
            x=min(max(18,saved.x()),max(18,vw-w-18))
            y=min(max(18,saved.y()),max(18,vh-h-18))
        else:
            x,y,w,h=self._tool_window_geometry(index if index is not None else 0)
            w=min(w,max(360,vw-36)); h=min(h,max(300,vh-36))
            x=min(max(18,x),max(18,vw-w-18)); y=min(max(18,y),max(18,vh-h-18))
        sub.setGeometry(x,y,w,h)
        sub.setProperty('responsive_auto_fitted',False)
        sub.setProperty('responsive_auto_maximized',False)

    def _apply_responsive_workspace_layout(self):
        if not hasattr(self,'studio_mdi'):
            return
        self._sync_studio_taskbar_visibility(); self._force_studio_layout()
        viewport=self.studio_mdi.viewport(); vw=viewport.width(); vh=viewport.height()
        if vw < 320 or vh < 240:
            return
        compact=self._workspace_should_compact(vw,vh)
        # Auto mode mirrors the responsive workspace: compact screens reclaim the
        # complete navigation width; larger screens keep the rich browser sidebar.
        # A user click becomes a persistent manual preference and is never overridden.
        if not getattr(self,'_sidebar_has_manual_preference',False):
            if bool(getattr(self,'_sidebar_collapsed',False)) != bool(compact):
                self._set_studio_sidebar_collapsed(compact,False)
                # Sidebar visibility changes the MDI viewport width. Re-read it before
                # fitting any child window so geometry always uses the final live area.
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
                viewport=self.studio_mdi.viewport(); vw=viewport.width(); vh=viewport.height()
        if compact != getattr(self,'_workspace_compact_mode',None):
            self._workspace_compact_mode=compact
            self._apply_compact_tool_ui(compact)
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            viewport=self.studio_mdi.viewport(); vw=viewport.width(); vh=viewport.height()
        # The library's secondary browser pane follows the live viewport as well,
        # even when the outer compact breakpoint did not change in this resize.
        if not getattr(self,'_library_inner_sidebar_manual',False) and hasattr(self,'library_files_panel'):
            self._set_library_inner_sidebar_collapsed(bool(compact and vw<1180),False)

        for index,sub in list(getattr(self,'tool_subwindows',{}).items()):
            if sub is None or not sub.isVisible() or sub.isMinimized():
                continue
            try:
                if compact:
                    self._fit_tool_subwindow_to_viewport(sub,index,True)
                else:
                    self._restore_tool_subwindow_from_compact(sub,index)
            except RuntimeError:
                continue

    def _finish_first_mdi_layout(self, sub, tool_index=None, document_order=None, attempt=0):
        """Apply first-open geometry after the MDI viewport has completed layout.

        This is UI-shell only.  On Windows, switching back to the Studio desktop and
        immediately sizing a new QMdiSubWindow can read the *previous* viewport size.
        The result is the first-open clipping seen in every tool/document window; a
        later click works only because the viewport has finished laying out by then.
        """
        if sub is None or not hasattr(self, 'studio_mdi'):
            return
        self._force_studio_layout()
        try:
            viewport = self.studio_mdi.viewport()
            vw, vh = viewport.width(), viewport.height()
        except Exception:
            return
        # If the tab/layout switch is still settling, wait for one more event-loop turn.
        if (vw < 480 or vh < 360) and attempt < 4:
            QTimer.singleShot(35, lambda s=sub, ti=tool_index, do=document_order, a=attempt+1:
                              self._finish_first_mdi_layout(s, ti, do, a))
            return
        if vw <= 0 or vh <= 0:
            return
        try:
            if tool_index is not None:
                # HF137: open_tool_window pre-sizes against the live viewport before
                # showing the frame.  This deferred pass is now a safety/sync pass,
                # not a competing first-paint geometry owner.
                if bool(sub.property('responsive_auto_fitted')):
                    if hasattr(sub,'_sync_embedded_client_layout'):sub._sync_embedded_client_layout()
                    sub._studio_first_open_fitted=True
                    return
                x, y, w, h = self._tool_window_geometry(tool_index)
                max_w = max(360, vw - 36)
                max_h = max(300, vh - 36)
                w = min(max_w, max(360, w))
                h = min(max_h, max(300, h))
                x = min(max(18, x), max(18, vw - w - 18))
                y = min(max(18, y), max(18, vh - h - 18))
                sub.setGeometry(x, y, w, h)
                sub._studio_first_open_fitted=True
                if self._workspace_should_compact(vw,vh):
                    self._fit_tool_subwindow_to_viewport(sub,tool_index,True)
            elif document_order is not None:
                offset = int(document_order) % 5
                w = min(max(360, vw - 36), max(760, int(vw * .76)))
                h = min(max(300, vh - 36), max(500, int(vh * .76)))
                x = min(24 + offset * 24, max(18, vw - w - 18))
                y = min(24 + offset * 22, max(18, vh - h - 18))
                sub.setGeometry(max(18, x), max(18, y), w, h)
            widget = sub.widget()
            if widget is not None:
                widget.updateGeometry()
                widget.update()
            sub.show()
            sub.raise_()
            self.studio_mdi.setActiveSubWindow(sub)
        except RuntimeError:
            # Window may have been closed before the deferred layout runs.
            return

    def arrange_primary_tool_windows(self, force=False):
        """Place Formal Library and Compare as the approved large-left/small-right pair."""
        library=self.tool_subwindows.get(0); compare=self.tool_subwindows.get(3)
        if library is None or compare is None or not library.isVisible() or not compare.isVisible():
            return
        if self._primary_windows_arranged and not force:
            return
        vw=self.studio_mdi.viewport().width(); vh=self.studio_mdi.viewport().height()
        if vw<1180 or vh<620:
            return
        active=self.studio_mdi.activeSubWindow()
        margin=18; gap=14; usable=vw-margin*2-gap
        library_w=max(760,int(usable*0.70)); compare_w=usable-library_w
        if compare_w<330:
            compare_w=330; library_w=usable-compare_w
        height=max(520,vh-margin*2)
        for sub in (library,compare):
            if sub.isMinimized():sub.showNormal()
        library.setGeometry(margin,margin,library_w,height)
        compare.setGeometry(margin+library_w+gap,margin,compare_w,height)
        library.show(); compare.show()
        if active in (library,compare):self.studio_mdi.setActiveSubWindow(active); active.raise_()
        self._primary_windows_arranged=True


    def open_tool_window(self,index:int,title:str|None=None):
        title=title or {0:'正式色库',1:'查色 / 找色',2:'色卡编排',3:'比色工作台',4:'光谱分析'}.get(index,'工具')
        self.show_home()
        sub=self.tool_subwindows.get(index)
        created = sub is None
        if sub is None:
            page=self.tool_pages[index]
            # UI-shell hotfix only: make sure the mature page is not carrying a
            # hidden state from any previous container before embedding it in MDI.
            # No page controls, signals, models or business methods are recreated.
            try:
                if self.stack.indexOf(page)>=0:
                    self.stack.removeWidget(page)
            except Exception:
                pass
            try:
                page.setParent(None)
                page.setVisible(True)
            except Exception:
                pass
            sub=StudioToolSubWindow(self.studio_mdi)
            sub.setAcceptDrops(True)
            sub.setObjectName('studioToolWindow')
            sub.setWindowTitle(title)
            sub.setAttribute(Qt.WA_DeleteOnClose,False)
            sub.setWidget(page)
            # QStackedWidget/QMdiSubWindow visibility is platform-sensitive on
            # Windows.  Explicitly show the existing page after setWidget so the
            # original Formal Library / Compare / Find / Palette / Spectrum UI is
            # visible instead of a blank white client area.
            page.show()
            page.raise_()
            sub.setProperty('tool_index',index)
            # Do not allow mature pages to be squeezed into an unusable sliver.
            # Windows remain normal movable/overlapping MDI windows; users decide
            # whether to tile them, maximize them or keep them side by side.
            min_sizes={0:(720,480),1:(860,540),2:(700,480),3:(680,460),4:(700,480)}
            mw,mh=min_sizes.get(index,(620,440)); sub.setMinimumSize(mw,mh)
            sub.hiddenByUser.connect(self.studio_mdi.refresh_empty_state)
            sub.minimizedToTaskbar.connect(self._minimize_to_studio_taskbar)
            sub.destroyed.connect(lambda _=None,s=sub:self._remove_studio_task_button(s))
            self.studio_mdi.addSubWindow(sub)
            x,y,w,h=self._tool_window_geometry(index); sub.setGeometry(x,y,min(w,self.studio_mdi.viewport().width()-36),min(h,self.studio_mdi.viewport().height()-36))
            self.tool_subwindows[index]=sub
        else:
            sub.setWindowTitle(title)
        self._remove_studio_task_button(sub)
        self._force_studio_layout()
        if sub.isMinimized():sub.showNormal()
        sub.setProperty('studio_taskbar_minimized',False)
        compact=self._workspace_should_compact()
        if compact != getattr(self,'_workspace_compact_mode',None):
            self._workspace_compact_mode=compact; self._apply_compact_tool_ui(compact); self._force_studio_layout()
        # Size the frame *before* exposing it.  This removes the visible clipped
        # first frame seen in HF136 and also applies on every later re-open.
        if compact:
            self._fit_tool_subwindow_to_viewport(sub,index,True)
        elif created:
            # Recompute from the live viewport after the page/sidebar layouts have
            # been activated.  Do not expose a frame sized from the previous layout.
            vw=self.studio_mdi.viewport().width(); vh=self.studio_mdi.viewport().height()
            x,y,w,h=self._tool_window_geometry(index)
            w=min(max(360,w),max(360,vw-36)); h=min(max(300,h),max(300,vh-36))
            x=min(max(18,x),max(18,vw-w-18)); y=min(max(18,y),max(18,vh-h-18))
            sub.setGeometry(x,y,w,h)
        else:
            self._restore_tool_subwindow_from_compact(sub,index)
        try:
            w=sub.widget()
            if w is not None:
                w.setVisible(True); w.show(); w.raise_()
        except Exception:
            pass
        sub.show();
        if hasattr(sub,'_sync_embedded_client_layout'):sub._sync_embedded_client_layout()
        sub.raise_(); self.studio_mdi.setActiveSubWindow(sub)
        # HF138: no deferred first-open geometry owner.  The shell layout is stable
        # before the frame is shown, and the central responsive pass is the only
        # later geometry owner.
        # One central shell pass handles late Windows layout requests.  It is safe
        # for re-opened windows too and does not introduce a second geometry owner.
        self._schedule_responsive_workspace_layout()
        self.studio_mdi.refresh_empty_state(); self.page_title.setText(title)
        # HF64: never auto-tile Formal Library + Compare. The old 70/30 split could
        # compress Compare below its usable width and look frozen although the app
        # event loop was healthy. Keep independent MDI windows instead.
        return sub

    def open_library_overview(self):
        # HF43: both formal and official libraries open as navigation containers.
        # Do not auto-open “全部客户”; users explicitly choose a customer/QTX from
        # the left tree. This matches the official-library behaviour and avoids
        # unnecessary full-library queries when the window is merely opened.
        if not self.can_use('library_view'):return
        self.open_tool_window(0,self.library_scope)
        if not self.open_library_customers:
            self.active_library_customer=None
            self.library_has_explicit_view=False
            self._tiles_keys=None
            if hasattr(self,'tile_grid'): self.build_tiles()

    def _library_scope_contains(self, customer):
        official=str(customer or '').startswith('官方色库/') or str(customer or '')=='官方色库'
        return official if self.library_scope=='官方色库' else not official

    def _library_scope_changed(self,scope):
        if scope not in ('正式色库','官方色库') or scope==self.library_scope:return
        self.library_scope=scope
        if hasattr(self,'library_group_title'):
            self.library_group_title.setText('官方色库' if scope=='官方色库' else '客户色库')
        if hasattr(self,'library_group_hint'):
            self.library_group_hint.setText('官方色库是父级目录；展开后选择具体子库或 QTX。' if scope=='官方色库' else
                                            '点击客户查看色卡；右键可管理客户分类。')
        if hasattr(self,'library_page_title'):
            self.library_page_title.setText('官方色库' if scope=='官方色库' else '正式色库')
        if hasattr(self,'library_customer_tabs'):
            self.library_customer_tabs.blockSignals(True)
            try:
                while self.library_customer_tabs.count():
                    self.library_customer_tabs.removeTab(self.library_customer_tabs.count()-1)
            finally:
                self.library_customer_tabs.blockSignals(False)
            self.open_library_customers.clear()
            self.minimized_library_customers.clear()
            self.active_library_customer=None
            self.file_list.blockSignals(True)
            self.file_list.clearSelection(); self.file_list.setCurrentRow(-1)
            self.file_list.blockSignals(False)
            self.library_selected_keys.clear(); self.hidden_cards.clear(); self.library_page=0; self._tiles_keys=None
            # Both scopes now start closed.  This avoids a large automatic query
            # and makes “正式色库” behave like “官方色库”.
            self.library_has_explicit_view=False; self.active_library_customer=None
            if hasattr(self,'tile_grid'): self.build_tiles()
            self.refresh_library_customer_tree()
        if hasattr(self,'tool_subwindows') and 0 in self.tool_subwindows:
            self.tool_subwindows[0].setWindowTitle(scope)

    def _switch_library_scope_and_open(self, scope, customer=None):
        """One-click, race-free switch between formal and official libraries."""
        if scope not in ('正式色库','官方色库'):
            return
        if hasattr(self,'library_scope_combo'):
            self.library_scope_combo.blockSignals(True)
            try: self.library_scope_combo.setCurrentText(scope)
            finally: self.library_scope_combo.blockSignals(False)
        if scope != self.library_scope:
            self._library_scope_changed(scope)
        # Bring the single persistent library MDI window to the front immediately.
        self.open_tool_window(0, scope)
        if customer:
            # Defer the customer open until the scope tree/tab reset has completed
            # its Qt layout/event cycle. This fixes the occasional “first click did
            # nothing, second click worked” race when jumping between scopes.
            QTimer.singleShot(0, lambda c=customer: self.open_library_customer(c))

    def run_global_search(self):
        if not self.can_use('library_view'):return
        text=(self.global_search.text() if hasattr(self,'global_search') else '').strip()
        if not text:return
        self.open_library_overview()
        try:
            self.search.setCurrentText(text); self.schedule_tile_rebuild()
        except Exception:
            pass

    def open_tool_tab(self,index:int,title:str|None=None):
        # Compatibility method name retained for every existing caller. It now opens
        # an independent MDI window instead of reusing one shared tab.
        return self.open_tool_window(index,title)

    def close_workspace_tab(self,index):
        if index<=0:return
        widget=self.workspace_tabs.widget(index)
        self.workspace_tabs.removeTab(index)
        widget.deleteLater()

    def add_workspace_document(self,title,samples,source_path='',document_type='QTX',slots=None):
        self.show_home()
        doc=WorkspaceDocument(self,title,list(samples),source_path,document_type,None,slots=slots)
        doc.setAcceptDrops(True)
        sub=StudioToolSubWindow(self.studio_mdi); sub.setObjectName('studioToolWindow'); sub.setWindowTitle(title)
        sub.setAcceptDrops(True)
        sub.setAttribute(Qt.WA_DeleteOnClose,True); sub.setProperty('delete_on_close',True); sub.setProperty('document_type',document_type)
        sub.setWidget(doc); self.studio_mdi.addSubWindow(sub)
        count=len(self._workspace_document_windows)
        self._force_studio_layout()
        vw=max(1,self.studio_mdi.viewport().width()); vh=max(1,self.studio_mdi.viewport().height())
        max_w=max(360,vw-36); max_h=max(300,vh-36)
        w=min(max_w,max(620,int(vw*.76))); h=min(max_h,max(420,int(vh*.76)))
        x=min(24+(count%5)*24,max(18,vw-w-18)); y=min(24+(count%5)*22,max(18,vh-h-18))
        sub.setGeometry(max(18,x),max(18,y),w,h)
        sub.minimizedToTaskbar.connect(self._minimize_to_studio_taskbar)
        sub.destroyed.connect(lambda _=None,s=sub:self._workspace_document_closed(s))
        self._workspace_document_windows.append(sub); sub.show(); sub.raise_(); self.studio_mdi.setActiveSubWindow(sub); self.studio_mdi.refresh_empty_state()
        return doc

    def _workspace_document_closed(self,sub):
        if getattr(self,'_app_closing',False) or QApplication.closingDown():
            return
        self._remove_studio_task_button(sub)
        try:self._workspace_document_windows.remove(sub)
        except ValueError:pass
        if hasattr(self,'studio_mdi'):self.studio_mdi.refresh_empty_state()

    def new_workspace_folder(self):
        name,ok=QInputDialog.getText(self,'新建工作区文件夹','文件夹名称：',text='新建文件夹')
        if not ok or not name.strip():return
        folder=WorkspaceFolderDocument(self,name.strip(),self); idx=self.workspace_tabs.addTab(folder,name.strip()); self.workspace_tabs.setCurrentIndex(idx)

    def manage_users(self):
        if self.require_admin('用户管理'):UserManagementDialog(self.auth_store,self.current_user,self).exec()

    def open_data_management(self):
        if not self.require_admin('数据管理中心'):return
        DataManagementDialog(self.auth_store,self.store,self.current_user,self).exec()

    def open_customer_data_folder(self):
        if not self.require_admin('打开客户数据文件夹'):return
        import os, subprocess
        path=str(self.store.customer_data_root)
        try:os.startfile(path)
        except Exception:
            try:subprocess.Popen(['xdg-open',path])
            except Exception:QMessageBox.information(self,'客户数据文件夹',path)

    def audit(self, action: str, target: str = '', detail: str = '') -> None:
        try:self.auth_store.log(self.current_user.username,action,target,detail)
        except Exception:pass

    def require_admin(self,action='此操作'):
        if self.is_admin:return True
        QMessageBox.information(self,'权限限制',f'{action} 仅管理者可以执行。\n当前账号：{self.current_user.display_name}（使用者）')
        return False

    def can_use(self, feature):
        if self.auth_store.can_use(self.current_user,feature):return True
        QMessageBox.information(self,'权限限制','当前账户没有使用该功能的权限。\n功能权限是总能力开关，数据授权不能反向打开该功能。\n请联系管理者在“用户与权限 → 权限设置”中调整。')
        return False

    def can_view_library_customer(self, customer: str) -> bool:
        try:return bool(self.auth_store.can_view_customer(self.current_user,str(customer or '')))
        except Exception:return bool(self.is_admin)

    def can_export_library_customer(self, customer: str) -> bool:
        try:return bool(self.auth_store.can_export_customer(self.current_user,str(customer or '')))
        except Exception:return bool(self.is_admin and self.auth_store.can_use(self.current_user,'export'))

    def can_view_source_path(self, source_path: str) -> bool:
        src=str(source_path or '')
        if not src or '://' in src:return True
        try:canonical=str(Path(src).resolve())
        except Exception:canonical=src
        try:customer=self.store.customer_for_path(canonical,enforce_access=False)
        except Exception:customer=None
        return True if not customer else self.can_view_library_customer(customer)

    def _sample_allowed_by_data_scope(self, sample) -> bool:
        return self.can_view_source_path(str(getattr(sample,'source_file','') or ''))

    def can_export_samples(self, samples, action='导出') -> bool:
        """Enforce DG-2 per-customer export scope for library-derived samples."""
        if not self.can_use('export'):
            return False
        denied=[]
        try:
            if not getattr(self,'_saved_customer_by_path',None):self.update_customers()
        except Exception:pass
        bypath=getattr(self,'_saved_customer_by_path',{}) or {}
        for sm in list(samples or []):
            src=str(getattr(sm,'source_file','') or '')
            if not src or '://' in src:continue
            try:canonical=str(Path(src).resolve())
            except Exception:canonical=src
            customer=bypath.get(canonical)
            if not customer:
                try:customer=self.store.customer_for_path(canonical,enforce_access=False)
                except Exception:customer=None
            if customer and not self.can_export_library_customer(customer):
                denied.append((getattr(sm,'display_name','色样'),customer))
        if denied:
            customers=sorted({c for _n,c in denied},key=str.casefold)
            QMessageBox.information(self,'数据范围限制',
                f'{action}包含当前账号仅可查看、不可导出的色库数据：\n'+'\n'.join(customers[:8])+
                ('\n…' if len(customers)>8 else '')+'\n\n请联系管理者在“权限设置”中检查“文件导出”功能和对应数据的“可导出”授权。')
            return False
        return True

    def active_tool_index(self):
        try:
            if self.workspace_tabs.currentIndex()!=0 or not hasattr(self,'studio_mdi'):
                return -1
            sub=self.studio_mdi.activeSubWindow()
            if sub is None or not sub.isVisible():
                return -1
            return int(sub.property('tool_index'))
        except Exception:
            return -1

    def _nav(self, text, index, checked=False):
        b = QPushButton(text)
        b.setObjectName("nav")
        b.setCheckable(True)
        b.setChecked(checked)
        self.nav_group.addButton(b)
        b.clicked.connect(lambda: self.show_page(index))
        return b

    # ============ 序列化 ============
    def _serialize_sample(self, s: Sample) -> dict:
        return {
            "sample_id": s.sample_id,
            "display_name": s.display_name,
            "kind": s.kind,
            "xyz_d65_10": list(s.xyz_d65_10),
            "lab_d65_10": list(s.lab_d65_10),
            "reflectance": list(s.reflectance),
            "wavelengths": list(s.wavelengths),
            "source_file": s.source_file,
            "viewing": s.viewing,
            "raw": dict(s.raw) if s.raw else {},
        }

    def _deserialize_sample(self, d: dict) -> Sample:
        return Sample(
            sample_id=d["sample_id"],
            display_name=d["display_name"],
            kind=d["kind"],
            xyz_d65_10=tuple(d["xyz_d65_10"]),
            lab_d65_10=tuple(d["lab_d65_10"]),
            reflectance=tuple(d["reflectance"]),
            wavelengths=tuple(d["wavelengths"]),
            source_file=d.get("source_file", ""),
            viewing=d.get("viewing", ""),
            raw=d.get("raw", {}),
        )

    def _workbench_samples(self, wb: dict) -> list[Sample]:
        """工作台样本运行时缓存。

        综合分析的 paint/mouseMove 会频繁读取样本；旧实现每次都重新反序列化整张工作台，
        在几十条光谱同时显示时会明显卡顿。
        """
        data=wb.get("samples_data", [])
        sig=tuple((d.get("source_file"), d.get("sample_id"), d.get("display_name"),
                   d.get("kind"), tuple(d.get("xyz_d65_10", ())), tuple(d.get("lab_d65_10", ())),
                   tuple(d.get("reflectance", ())), tuple(d.get("wavelengths", ())),
                   d.get("viewing"), json.dumps(d.get("raw", {}), sort_keys=True, ensure_ascii=False))
                  for d in data)
        if wb.get("_runtime_samples_sig")==sig and "_runtime_samples" in wb:
            return wb["_runtime_samples"]
        samples=[self._deserialize_sample(d) for d in data]
        wb["_runtime_samples_sig"]=sig
        wb["_runtime_samples"]=samples
        return samples

    @staticmethod
    def _science_sample_signature(sample: Sample) -> int:
        """Compact runtime fingerprint for immutable measurement data.

        display_name/raw metadata are intentionally excluded: renaming a row must not
        invalidate spectral maths, while any XYZ/Lab/spectrum change does.
        """
        return hash((sample.source_file, sample.sample_id, sample.kind,
                     sample.xyz_d65_10, sample.lab_d65_10,
                     sample.wavelengths, sample.reflectance))

    def _workbench_xyz_lab(self, sample: Sample, illuminant: str, observer: int):
        key=(self._science_sample_signature(sample), display_illuminant(illuminant), int(observer))
        cached=self._workbench_color_cache.get(key)
        if cached is not None:
            return cached
        if display_illuminant(illuminant)=='D65' and int(observer)==10 and sample.kind!='AVERAGE':
            value=(sample.xyz_d65_10, sample.lab_d65_10)
        else:
            value=reflectance_to_xyz_lab(sample.reflectance, illuminant, sample.wavelengths, observer)
        # Bound memory in very long sessions; clearing is safe because this is only a cache.
        if len(self._workbench_color_cache) > 20000:
            self._workbench_color_cache.clear()
        self._workbench_color_cache[key]=value
        return value

    def _workbench_pair_analysis(self, standard: Sample, batch: Sample, illuminant: str, observer: int):
        key=(self._science_sample_signature(standard), self._science_sample_signature(batch),
             display_illuminant(illuminant), int(observer))
        cached=self._workbench_pair_cache.get(key)
        if cached is not None:
            return cached
        value=analyse_pair(standard,batch,illuminant,reference_illuminant='D65',observer_degrees=observer)
        if len(self._workbench_pair_cache) > 20000:
            self._workbench_pair_cache.clear()
        self._workbench_pair_cache[key]=value
        return value

    def _repair_workbench_standard_key(self, wb: dict) -> None:
        """兼容旧版本使用的 ``sample_id|path`` 标准键。

        当前版本统一使用 ``path|sample_id``，否则旧工作台虽然有样本却无法识别
        标准，导致色差列显示为横杠。
        """
        standard_key = wb.get("standard_key")
        if not standard_key or standard_key == "__AVERAGE__":
            return
        try:
            samples = self._workbench_samples(wb)
        except Exception:
            return
        current_keys = {sample_key(sample) for sample in samples}
        if standard_key in current_keys:
            return
        for sample in samples:
            legacy_key = f"{sample.sample_id}|{sample.source_file}"
            if standard_key == legacy_key:
                wb["standard_key"] = sample_key(sample)
                if self.is_admin:self.store.save_workbench(wb)
                return

    # ============ 色库页 ============
    def build_library(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        files = QFrame(); files.setObjectName("filePanel"); files.setFixedWidth(220)
        fl = QVBoxLayout(files)
        self.library_outer_layout=outer; self.library_files_panel=files; self.library_files_layout=fl
        self.library_scope_combo=QComboBox()
        policy=getattr(self,'_data_scope_policy',{}) or {}
        formal_ok=bool(policy.get('unrestricted') or policy.get('formal_all_view') or any(v.get('view') for v in (policy.get('customers') or {}).values()))
        official_ok=bool(policy.get('unrestricted') or policy.get('official_view') or any(v.get('view') for v in (policy.get('officials') or {}).values()))
        scopes=[]
        if formal_ok:scopes.append('正式色库')
        if official_ok:scopes.append('官方色库')
        self.library_scope_combo.addItems(scopes or ['正式色库'])
        if self.library_scope_combo.findText(self.library_scope)<0:self.library_scope=self.library_scope_combo.itemText(0)
        self.library_scope_combo.setCurrentText(self.library_scope)
        self.library_scope_combo.activated.connect(lambda _idx:self._switch_library_scope_and_open(self.library_scope_combo.currentText()))
        scope_row=QHBoxLayout(); scope_row.setContentsMargins(0,0,0,0); scope_row.setSpacing(5); scope_row.addWidget(self.library_scope_combo,1)
        self.library_panel_collapse_btn=QToolButton(); self.library_panel_collapse_btn.setObjectName('panelToggle'); self.library_panel_collapse_btn.setText('‹'); self.library_panel_collapse_btn.setToolTip('收起色库导航'); self.library_panel_collapse_btn.setFixedSize(30,30); self.library_panel_collapse_btn.clicked.connect(lambda:self._set_library_inner_sidebar_collapsed(True,True)); scope_row.addWidget(self.library_panel_collapse_btn)
        fl.addLayout(scope_row)
        self.library_group_title=QLabel('官方色库' if self.library_scope=='官方色库' else '客户色库'); self.library_group_title.setObjectName("sectionTitle"); fl.addWidget(self.library_group_title)
        self.library_group_hint=QLabel('官方色库是父级目录；展开后选择具体子库或 QTX。' if self.library_scope=='官方色库' else '点击客户查看色卡；右键可管理客户分类。')
        self.library_group_hint.setWordWrap(True); self.library_group_hint.setObjectName("muted"); fl.addWidget(self.library_group_hint)
        self.library_sidebar_search=QLineEdit(); self.library_sidebar_search.setObjectName('librarySidebarSearch'); self.library_sidebar_search.setPlaceholderText('搜索客户或 QTX…'); self.library_sidebar_search.setClearButtonEnabled(True); self.library_sidebar_search.textChanged.connect(self._filter_library_sidebar); fl.addWidget(self.library_sidebar_search)
        self.library_customer_tree=QTreeWidget(); self.library_customer_tree.setHeaderHidden(True); self.library_customer_tree.setMaximumHeight(220)
        self.library_customer_tree.setMaximumHeight(16777215)
        self.library_customer_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.library_customer_tree.itemClicked.connect(self.library_customer_tree_clicked)
        self.library_customer_tree.customContextMenuRequested.connect(self.library_customer_tree_menu)
        fl.addWidget(self.library_customer_tree,1)
        ws=QLabel("QTX 工作区"); ws.setObjectName("sectionTitle"); fl.addWidget(ws)
        wh=QLabel("导入文件先进入临时工作区；保存后归入上方客户色库。")
        wh.setWordWrap(True); wh.setObjectName("muted"); fl.addWidget(wh)
        self.file_list = QtxWorkspaceList()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setToolTip("支持 Ctrl 多选、Shift 连续选择、Ctrl+A 全选；Delete 从工作区移除；右键可进行有权限的色库管理")
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self.show_file_menu)
        self.file_list.filesDropped.connect(self.open_workspace_paths)
        self.file_list.itemDoubleClicked.connect(lambda *_: self.file_selected())
        self.file_list.currentItemChanged.connect(self.file_selected)
        # Windows 风格快捷键：Delete 仅移出工作区；Shift+Delete 删除正式色库记录（会二次确认）。
        act_remove_qtx = QAction(self.file_list)
        act_remove_qtx.setShortcut(Qt.Key_Delete)
        act_remove_qtx.setShortcutContext(Qt.WidgetShortcut)
        act_remove_qtx.triggered.connect(self.remove_selected_file)
        self.file_list.addAction(act_remove_qtx)
        act_delete_qtx = QAction(self.file_list)
        act_delete_qtx.setShortcut("Shift+Delete")
        act_delete_qtx.setShortcutContext(Qt.WidgetShortcut)
        act_delete_qtx.triggered.connect(self.delete_from_database)
        self.file_list.addAction(act_delete_qtx)
        fl.addWidget(self.file_list, 1)
        save = QPushButton("发布到色库…"); save.setObjectName("primaryButton")
        save.clicked.connect(self.save_selected_file)
        save.setToolTip("把当前导入的 QTX 作为组织正式业务数据发布到正式色库；可选择已有客户或新建客户")
        fl.addWidget(save)
        tip = QLabel("提示：从工作区移除、移动客户、从色库删除等操作请右键 QTX。")
        tip.setWordWrap(True); tip.setObjectName("muted"); fl.addWidget(tip)
        # Preserve mature import/save handlers and their list model while the
        # redundant colour-library sidebar controls are out of sight.
        for obsolete in (ws,wh,self.file_list,save,tip):obsolete.hide()
        outer.addWidget(files)

        content = QWidget(); content.setObjectName("libraryContent"); lay = QVBoxLayout(content); lay.setContentsMargins(0,0,0,0); lay.setSpacing(10)
        self.library_content_layout=lay
        head=QHBoxLayout(); head.setContentsMargins(4,0,4,0)
        self.library_panel_expand_btn=QToolButton(); self.library_panel_expand_btn.setObjectName('panelToggle'); self.library_panel_expand_btn.setText('›'); self.library_panel_expand_btn.setToolTip('展开色库导航'); self.library_panel_expand_btn.setFixedSize(30,30); self.library_panel_expand_btn.clicked.connect(lambda:self._set_library_inner_sidebar_collapsed(False,True)); head.addWidget(self.library_panel_expand_btn); self.library_panel_expand_btn.hide()
        head_text=QVBoxLayout(); head_text.setSpacing(2)
        lib_title=QLabel(self.library_scope); lib_title.setObjectName("pageTitle"); self.library_page_title=lib_title; head_text.addWidget(lib_title)
        self.library_meta_label=QLabel("选择客户或 QTX 后浏览颜色"); self.library_meta_label.setObjectName("pageSubtitle"); head_text.addWidget(self.library_meta_label)
        self.library_condition_label=QLabel()
        self.library_condition_label.setWordWrap(True)
        self.library_condition_label.setAccessibleName('色库显示条件与屏幕预览说明')
        head_text.addWidget(self.library_condition_label)
        self._refresh_library_condition_status()
        head.addLayout(head_text); head.addStretch(1); lay.addLayout(head)
        # Responsive library browse bar: preserve search as the dominant control;
        # filter chips wrap instead of forcing the content page wider than its viewport.
        browse_top=QHBoxLayout(); browse_top.setSpacing(8)
        self.search = QComboBox(); self.search.setObjectName('librarySearch'); self.search.setEditable(True); self.search.setInsertPolicy(QComboBox.NoInsert); self.search.setMinimumWidth(220); self.search.lineEdit().setPlaceholderText('搜索色号、名称或备注...'); self.search.currentTextChanged.connect(self.schedule_tile_rebuild); browse_top.addWidget(self.search,1)
        self.find_toggle = QPushButton("查色 / 找色"); self.find_toggle.setCheckable(True); self.find_toggle.clicked.connect(self.start_find_from_library_selection); browse_top.addWidget(self.find_toggle)
        lay.addLayout(browse_top)
        # Customer switching remains in the searchable left tree. Keep this
        # compatibility selector hidden for the mature customer-tab handlers.
        self.customer = QComboBox(content); self.customer.addItem("请选择客户"); self.customer.addItem("全部客户"); self.customer.activated.connect(self.customer_filter_activated); self.customer.hide()
        browse_filters=QWidget(content); browse_filters.setObjectName('libraryBrowseFilters'); browse=FlowLayout(browse_filters,0,8,6)
        self.library_type_filter=QComboBox(); self.library_type_filter.addItem('所有分类','all'); self.library_type_filter.addItem('标准样','STD'); self.library_type_filter.addItem('批次样','BAT'); self.library_type_filter.currentIndexChanged.connect(self._library_filter_changed); self.library_type_filter.setMinimumWidth(118); browse.addWidget(self.library_type_filter)
        self.library_family_filter=QComboBox(); self.library_family_filter.addItem('所有色系','all')
        for text,key in [('红色系','red'),('橙色系','orange'),('黄色系','yellow'),('绿色系','green'),('青色系','cyan'),('蓝色系','blue'),('紫色系','purple'),('中性色','neutral')]:self.library_family_filter.addItem(text,key)
        self.library_family_filter.currentIndexChanged.connect(self._library_filter_changed); self.library_family_filter.setMinimumWidth(118); browse.addWidget(self.library_family_filter)
        self.library_sort_combo=QComboBox()
        for text,key in [('排序：原始顺序','manual'),('排序：色号','name'),('排序：L*','L'),('排序：a*','a'),('排序：b*','b'),('排序：C*','C'),('排序：Munsell 色相','h')]:
            self.library_sort_combo.addItem(text,key)
            if key=='h':
                self.library_sort_combo.setItemData(self.library_sort_combo.count()-1,'浏览排序使用轻量色相序，避免整库光谱重算；精确 Munsell 标记仍在测色明细中按需计算。',Qt.ToolTipRole)
        # activated is intentional: selecting the already-active criterion again
        # toggles its direction, matching the comparison-workbench interaction.
        self.library_sort_combo.activated.connect(self._library_sort_combo_activated)
        self.library_sort_combo.setMinimumWidth(155); browse.addWidget(self.library_sort_combo)
        lay.addWidget(browse_filters)

        # 客户浏览标签：像网页标签一样可同时打开多个客户。关闭仅关闭视图，收起可稍后恢复。
        customer_tabs_row = QHBoxLayout()
        customer_tabs_row.addWidget(QLabel("已打开"))
        self.library_customer_tabs = QTabBar()
        self.library_customer_tabs.setTabsClosable(True)
        self.library_customer_tabs.setMovable(True)
        self.library_customer_tabs.setExpanding(False)
        self.library_customer_tabs.setDrawBase(False)
        self.library_customer_tabs.currentChanged.connect(self.library_customer_tab_changed)
        self.library_customer_tabs.tabCloseRequested.connect(self.close_library_customer_tab)
        self.library_customer_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.library_customer_tabs.customContextMenuRequested.connect(self.library_customer_tab_menu)
        customer_tabs_row.addWidget(self.library_customer_tabs, 1)
        self.minimized_customers_btn = QPushButton("已收起 0")
        self.minimized_customers_btn.clicked.connect(self.show_minimized_customers_menu)
        self.minimized_customers_btn.hide()
        customer_tabs_row.addWidget(self.minimized_customers_btn)
        lay.addLayout(customer_tabs_row)

        selection_row=QHBoxLayout(); selection_row.addStretch(1)
        self.selection_label=QLabel("已选 0 个色样"); self.selection_label.setObjectName("muted"); selection_row.addWidget(self.selection_label)
        self.library_sort_widget=QWidget(); self.library_sort_widget.setLayout(selection_row); lay.addWidget(self.library_sort_widget)

        # 查色面板：标准来源按模式动态显示；支持多个查色标准标签。
        self.find_panel=QFrame(); self.find_panel.setObjectName("filePanel")
        fp=QVBoxLayout(self.find_panel); fp.setContentsMargins(12,10,12,10); fp.setSpacing(8)
        self.find_panel_layout=fp

        # 查色标签：像客户标签一样，可同时打开多个标准色。
        tabsrow=QHBoxLayout(); tabsrow.addWidget(QLabel("查色任务"))
        self.find_tabs=QTabBar(); self.find_tabs.setTabsClosable(True); self.find_tabs.setMovable(True); self.find_tabs.setExpanding(False); self.find_tabs.setUsesScrollButtons(False); self.find_tabs.setElideMode(Qt.ElideRight)
        self.find_tabs.currentChanged.connect(self.find_tab_changed); self.find_tabs.tabCloseRequested.connect(self.close_find_tab)
        tabsrow.addWidget(self.find_tabs,1)
        self.find_prev_btn=QPushButton("← 上一个"); self.find_prev_btn.setMinimumWidth(82); self.find_prev_btn.setToolTip("上一个查色任务")
        self.find_next_btn=QPushButton("下一个 →"); self.find_next_btn.setMinimumWidth(82); self.find_next_btn.setToolTip("下一个查色任务")
        self.find_tasks_btn=QPushButton("任务 ▾"); self.find_tasks_btn.setToolTip("查看全部查色任务")
        self.find_prev_btn.clicked.connect(lambda:self._step_find_task(-1)); self.find_next_btn.clicked.connect(lambda:self._step_find_task(1)); self.find_tasks_btn.clicked.connect(self._show_find_tasks_menu)
        tabsrow.addWidget(self.find_prev_btn); tabsrow.addWidget(self.find_next_btn); tabsrow.addWidget(self.find_tasks_btn)
        fp.addLayout(tabsrow)

        std_row=QHBoxLayout(); self.find_standard_label=QLabel("标准色：未设置"); self.find_standard_label.setObjectName("sectionTitle"); std_row.addWidget(self.find_standard_label,1)
        self.find_sample_combo=QComboBox(); self.find_sample_combo.setMinimumWidth(180); self.find_sample_combo.setToolTip("当前 QTX 含多个色样时，在这里选择本任务的标准色"); self.find_sample_combo.currentIndexChanged.connect(self._find_sample_combo_changed); self.find_sample_combo.hide(); std_row.addWidget(self.find_sample_combo)
        fp.addLayout(std_row)
        cond_wrap=QFrame(); cond_wrap.setObjectName('findConditionFlow'); cond_flow=FlowLayout(cond_wrap,0,8,6)
        def add_find_field(label_text,control,minw=0):
            host=QFrame(); host.setObjectName('inlineField'); hl=QHBoxLayout(host); hl.setContentsMargins(0,0,0,0); hl.setSpacing(5); lab=QLabel(label_text); lab.setObjectName('inlineFieldLabel'); hl.addWidget(lab)
            if minw:control.setMinimumWidth(minw)
            hl.addWidget(control); cond_flow.addWidget(host); return host
        self.find_formula=QComboBox(); self.find_formula.addItems(["CIEDE2000","CIE94","ΔE*ab","CMC(2:1)"]); self.find_formula.currentTextChanged.connect(self._find_condition_changed); add_find_field('色差公式',self.find_formula,112)
        self.find_illuminant=QComboBox(); self.find_illuminant.addItems(SUPPORTED_ILLUMINANTS); [self.find_illuminant.setItemData(i,illuminant_note(name),Qt.ToolTipRole) for i,name in enumerate(SUPPORTED_ILLUMINANTS) if illuminant_note(name)]; self.find_illuminant.setCurrentText('D65'); self.find_illuminant.currentTextChanged.connect(self._find_condition_changed); add_find_field('光源',self.find_illuminant,88)
        self.find_observer=QComboBox(); self.find_observer.addItems(['10°','2°']); self.find_observer.setCurrentText('10°'); self.find_observer.currentTextChanged.connect(self._find_condition_changed); add_find_field('观察者',self.find_observer,72)
        self.find_scope_btn=QPushButton('全部授权数据 ▾'); self.find_scope_btn.setMinimumWidth(150); self.find_scope_btn.setToolTip('选择一个或多个官方/正式色库作为查色候选范围'); self.find_scope_btn.clicked.connect(self.show_find_scope_dialog); add_find_field('查色范围',self.find_scope_btn)
        fp.addWidget(cond_wrap)
        self.find_condition_flow=cond_wrap
        # Compatibility selector retained hidden for older state-refresh code.
        self.find_customer=QComboBox(); self.find_customer.addItem("全部客户"); self.find_customer.hide()
        # HF135a: legacy fr1 row was removed by the responsive find-condition refactor.
        # Do not re-add it here; cond_wrap above is now the condition container.

        # 来源选择。只显示当前来源需要的控件，避免 LAB/RGB/QTX 同时挤在一行。
        source_row=QHBoxLayout(); source_row.addWidget(QLabel("标准色来源"))
        self.find_source=QComboBox(); self.find_source.addItems(["QTX色样","LAB输入","RGB输入"]); self.find_source.currentIndexChanged.connect(self.update_find_source_ui); source_row.addWidget(self.find_source); source_row.addStretch(1)
        fp.addLayout(source_row)

        self.find_source_stack=QStackedWidget()
        # QTX 来源页：拖入文件或从已保存色库选择。
        qtx_page=QWidget(); ql=QHBoxLayout(qtx_page); ql.setContentsMargins(0,0,0,0)
        self.find_qtx_drop=FindQtxDropZone(); self.find_qtx_drop.filesDropped.connect(self.add_find_qtx_files); self.find_qtx_drop.clicked.connect(self.choose_find_qtx_files); ql.addWidget(self.find_qtx_drop,1)
        choose_lib=QPushButton("从色库选择色样…"); choose_lib.clicked.connect(self.choose_find_standard_from_library); ql.addWidget(choose_lib)
        self.find_source_stack.addWidget(qtx_page)
        # LAB 输入页。
        lab_page=QWidget(); ll=QHBoxLayout(lab_page); ll.setContentsMargins(0,0,0,0)
        self.find_L=QLineEdit(); self.find_a=QLineEdit(); self.find_b=QLineEdit()
        for lab,ed in [("L*",self.find_L),("a*",self.find_a),("b*",self.find_b)]: ll.addWidget(QLabel(lab)); ed.setMaximumWidth(100); ll.addWidget(ed)
        add_lab=QPushButton("添加为查色标准"); add_lab.clicked.connect(self.add_manual_find_standard); ll.addWidget(add_lab); ll.addStretch(1)
        self.find_source_stack.addWidget(lab_page)
        # RGB 输入页。
        rgb_page=QWidget(); rl=QHBoxLayout(rgb_page); rl.setContentsMargins(0,0,0,0)
        self.find_R=QLineEdit(); self.find_G=QLineEdit(); self.find_B=QLineEdit()
        for lab,ed in [("R",self.find_R),("G",self.find_G),("B",self.find_B)]: rl.addWidget(QLabel(lab)); ed.setMaximumWidth(100); rl.addWidget(ed)
        add_rgb=QPushButton("添加为查色标准"); add_rgb.clicked.connect(self.add_manual_find_standard); rl.addWidget(add_rgb); rl.addStretch(1)
        self.find_source_stack.addWidget(rgb_page)
        fp.addWidget(self.find_source_stack)

        filter_wrap=QFrame(); filter_wrap.setObjectName('findFilterFlow'); fr2=FlowLayout(filter_wrap,0,8,6)
        self.find_de_min=QLineEdit(); self.find_de_min.setText(''); self.find_de_min.hide()
        self.find_de_max=QLineEdit(); self.find_de_max.setPlaceholderText('空 = 不限'); self.find_de_max.setFixedWidth(110)
        de_host=QFrame(); dhl=QHBoxLayout(de_host); dhl.setContentsMargins(0,0,0,0); dhl.setSpacing(5); dhl.addWidget(QLabel('最大色差')); dhl.addWidget(self.find_de_max); fr2.addWidget(de_host)
        self.find_name_keyword=QComboBox(); self.find_name_keyword.setEditable(True); self.find_name_keyword.setInsertPolicy(QComboBox.NoInsert); self.find_name_keyword.setMaxVisibleItems(12); self.find_name_keyword.addItems(['全部','DTY','FDY','YARN','SOCK','FABRIC']); self.find_name_keyword.setFixedWidth(150)
        if self.find_name_keyword.lineEdit() is not None:self.find_name_keyword.lineEdit().setPlaceholderText('全部 / 手动输入')
        self.find_name_keyword.setStyleSheet('QComboBox{border-top-right-radius:0;border-bottom-right-radius:0;}QComboBox::drop-down{width:0;border:0;}')
        self.find_name_keyword_drop=QToolButton(); self.find_name_keyword_drop.setText('▾'); self.find_name_keyword_drop.setToolTip('选择常用 QTX 名称关键字'); self.find_name_keyword_drop.setFixedSize(30,38); self.find_name_keyword_drop.setStyleSheet('QToolButton{border-left:0;border-top-left-radius:0;border-bottom-left-radius:0;padding:0;font-size:14px;}'); self.find_name_keyword_drop.clicked.connect(self.find_name_keyword.showPopup)
        name_host=QFrame(); nh=QHBoxLayout(name_host); nh.setContentsMargins(0,0,0,0); nh.setSpacing(0); nh.addWidget(QLabel('QTX名称包含')); nh.addSpacing(5); nh.addWidget(self.find_name_keyword); nh.addWidget(self.find_name_keyword_drop); fr2.addWidget(name_host)
        self.find_limit=QSpinBox(); self.find_limit.setRange(1,500); self.find_limit.setValue(30); self.find_limit.setFixedWidth(86)
        limit_host=QFrame(); lh=QHBoxLayout(limit_host); lh.setContentsMargins(0,0,0,0); lh.setSpacing(5); lh.addWidget(QLabel('结果数')); lh.addWidget(self.find_limit); fr2.addWidget(limit_host)
        self.find_query_btn=QPushButton('查询'); self.find_query_btn.setObjectName('primaryButton'); self.find_query_btn.setMinimumWidth(92); self.find_query_btn.clicked.connect(self.run_find_query); fr2.addWidget(self.find_query_btn)
        clearfind=QPushButton('清空任务'); clearfind.clicked.connect(self.clear_find_mode); fr2.addWidget(clearfind)
        fp.addWidget(filter_wrap); self.find_filter_flow=filter_wrap

        # 结果后续动作：比较 / 导出 Excel / 导出原 QTX。
        actions=QHBoxLayout(); self.find_result_summary=QLabel("查询结果：尚未查询"); self.find_result_summary.setObjectName("muted"); actions.addWidget(self.find_result_summary); actions.addStretch(1)
        hint_actions=QLabel("Ctrl/Shift 多选 · Ctrl+A 全选 · Enter 与标准比较 · Delete 从当前结果移除 · Esc 取消"); hint_actions.setObjectName('muted'); self.find_action_hint=hint_actions; actions.addWidget(hint_actions)
        self.find_export_btn=QPushButton("导出 ▾"); self.find_export_btn.clicked.connect(self._show_find_export_menu); actions.addWidget(self.find_export_btn)
        fp.addLayout(actions)
        self.find_panel.hide(); lay.addWidget(self.find_panel)

        self.empty_label=QLabel("色库当前未打开\n\n请选择左侧 QTX、选择客户，或进入【查色 / 找色】")
        self.empty_label.setObjectName("empty"); self.empty_label.setAlignment(Qt.AlignCenter); lay.addWidget(self.empty_label,1)
        self.tile_host=QWidget(); self.tile_host.setObjectName('libraryTileHost'); self.tile_host.installEventFilter(self); self.tile_grid=QGridLayout(self.tile_host); self.tile_grid.setContentsMargins(4,6,4,6); self.tile_grid.setHorizontalSpacing(12); self.tile_grid.setVerticalSpacing(12)
        self.tile_scroll=QScrollArea(); self.tile_scroll.setObjectName('libraryTileScroll'); self.tile_scroll.setWidgetResizable(True); self.tile_scroll.setFrameShape(QFrame.NoFrame); self.tile_scroll.setWidget(self.tile_host); self.tile_scroll.viewport().setAcceptDrops(True); self.tile_scroll.viewport().installEventFilter(self); self.tile_scroll.hide(); lay.addWidget(self.tile_scroll,1)

        # Presentation-only pager. Business data is never truncated; only the current
        # page is painted, so hundreds of colours stay uniform and the UI stays light.
        self.library_pager=QFrame(); self.library_pager.setObjectName('libraryPager')
        pager=QHBoxLayout(self.library_pager); pager.setContentsMargins(4,6,4,2); pager.setSpacing(6)
        self.library_count_label=QLabel('0 个颜色'); self.library_count_label.setObjectName('pageSubtitle'); pager.addWidget(self.library_count_label)
        pager.addStretch(1)
        self.library_prev_btn=QPushButton('‹'); self.library_prev_btn.setObjectName('pagerArrow'); self.library_prev_btn.setFixedSize(32,32); self.library_prev_btn.clicked.connect(lambda:self._set_library_page(self.library_page-1)); pager.addWidget(self.library_prev_btn)
        self.library_page_buttons=QWidget(); self.library_page_buttons_layout=QHBoxLayout(self.library_page_buttons); self.library_page_buttons_layout.setContentsMargins(0,0,0,0); self.library_page_buttons_layout.setSpacing(4); pager.addWidget(self.library_page_buttons)
        self.library_next_btn=QPushButton('›'); self.library_next_btn.setObjectName('pagerArrow'); self.library_next_btn.setFixedSize(32,32); self.library_next_btn.clicked.connect(lambda:self._set_library_page(self.library_page+1)); pager.addWidget(self.library_next_btn)
        self.library_page_size_combo=QComboBox(); self.library_page_size_combo.setObjectName('pageSizeCombo'); self.library_page_size_combo.addItem('自动铺满',0); self.library_page_size_combo.addItem('24 张/页',24); self.library_page_size_combo.addItem('36 张/页',36); self.library_page_size_combo.addItem('48 张/页',48); self.library_page_size_combo.currentIndexChanged.connect(self._library_page_size_changed); pager.addWidget(self.library_page_size_combo)
        self.library_pager.hide(); lay.addWidget(self.library_pager)
        outer.addWidget(content,1)
        QTimer.singleShot(0,lambda:self._set_library_inner_sidebar_collapsed(bool(getattr(self,'_library_inner_sidebar_collapsed',False)),False))
        # Keep the legacy panel object for compatibility with old callers, but all
        # visible detail entry points now use the single unified detail window.
        self.detail=LibrarySampleDetailPanel(self); self.detail.hide()
        return page

    # ============ 独立查色 / 找色页 ============
    def build_find_page(self):
        """查色是独立工作区：不显示客户树和 QTX 临时工作区，只保留标准、条件、任务和结果。"""
        page=QWidget(); body=QWidget(); lay=QVBoxLayout(body); lay.setContentsMargins(24,20,24,20); lay.setSpacing(12)
        self.find_body_layout=lay
        intro=QHBoxLayout()
        title=QLabel("查色 / 找色"); title.setObjectName("sectionTitle"); intro.addWidget(title)
        subtitle=QLabel("建立标准 → 设置条件 → 查询 → 多选结果进行比较或导出"); subtitle.setObjectName("muted"); intro.addWidget(subtitle)
        self.find_intro_subtitle=subtitle
        intro.addStretch(1); lay.addLayout(intro)

        self.find_panel.setParent(page); self.find_panel.show(); lay.addWidget(self.find_panel)

        # 查询结果标题保持原有操作提示；仅包成可隐藏容器，供下方“匹配文件夹”最大化时使用。
        self.find_result_head_widget=QWidget()
        result_head=QHBoxLayout(self.find_result_head_widget); result_head.setContentsMargins(0,0,0,0)
        result_title=QLabel("查询结果"); result_title.setObjectName("sectionTitle"); result_head.addWidget(result_title)
        self.find_selection_label=QLabel("已选 0 个结果"); self.find_selection_label.setObjectName("muted"); result_head.addWidget(self.find_selection_label)
        result_head.addStretch(1)
        result_hint=QLabel("Ctrl/Shift 多选 · Ctrl+A 全选 · Enter 比较 · Delete 移除当前结果"); result_hint.setObjectName("muted"); self.find_result_hint=result_hint; result_head.addWidget(result_hint)
        lay.addWidget(self.find_result_head_widget)

        # 一个 QTX 勾选多个标准时，查询一次后用并列表概览“标准 ↔ 最佳匹配”。
        # 第二轮仅增强结果展示：色块使用真实控件显示，并在双方名称后补充 LABCH；查询/排序算法不变。
        # HF115: “当前查看”放到“最佳匹配”下一行，色差值跟随自己的色样一起移动。
        self.find_multi_table=QTableWidget(0,21)
        self.find_multi_table.setHorizontalHeaderLabels([
            "标准色","标准名称","L*","a*","b*","C*","h°",
            "比较类型","对比色","对比名称","L*","a*","b*","C*","h°",
            "ΔE","ΔL*","Δa*","Δb*","ΔC*","ΔH*"
        ])
        self.find_multi_table.verticalHeader().setVisible(False); self.find_multi_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.find_multi_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.find_multi_table.setMinimumHeight(150);self.find_multi_table.setMaximumHeight(300)
        self.find_multi_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # HF118: ResizeToContents could squeeze numeric LABCH columns down to an
        # ellipsis on a narrow/smaller screen. Keep the data visible and let the
        # user scroll horizontally instead of replacing measurements with “...”.
        self.find_multi_table.setTextElideMode(Qt.ElideNone)
        hdr=self.find_multi_table.horizontalHeader(); hdr.setSectionResizeMode(QHeaderView.Interactive)
        widths={0:76,1:190,2:62,3:62,4:62,5:62,6:66,7:82,8:76,9:205,10:62,11:62,12:62,13:62,14:66,15:66,16:66,17:66,18:66,19:66,20:66}
        for col,width in widths.items(): self.find_multi_table.setColumnWidth(col,width)
        self.find_multi_table.setToolTip('最佳匹配固定在第一行；下方只显示当前选中的查询结果。Ctrl/Shift 多选会逐行一起显示；点击空白后当前查看行消失。')
        self.find_multi_table.hide(); lay.addWidget(self.find_multi_table)

        # 下方匹配色样仍使用原有 ColorTile / 选择 / 拖拽 / 右键逻辑，只增加一个“文件夹式”视觉容器。
        # 容器可收起或最大化，方便集中查看大量查询结果；不改变结果数据本身。
        self.find_results_folder=QFrame(); self.find_results_folder.setObjectName('findResultsFolder')
        self.find_results_folder.setMinimumHeight(205)
        self.find_results_folder.setStyleSheet('QFrame#findResultsFolder{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:10px;}')
        folder_root=QVBoxLayout(self.find_results_folder); folder_root.setContentsMargins(10,8,10,10); folder_root.setSpacing(6)
        folder_head=QHBoxLayout(); folder_head.setSpacing(8)
        folder_icon=QLabel(); folder_icon.setPixmap(self.style().standardIcon(QStyle.SP_DirIcon).pixmap(18,18)); folder_head.addWidget(folder_icon)
        folder_title=QLabel('查询结果文件'); folder_title.setObjectName('sectionTitle'); folder_head.addWidget(folder_title)
        self.find_results_folder_count=QLabel('0 个色样'); self.find_results_folder_count.setObjectName('muted'); folder_head.addWidget(self.find_results_folder_count)
        folder_head.addStretch(1)
        # 仅增强查询结果文件夹右上角窗口控制按钮的可见性；行为保持不变。
        result_ctrl_qss='''QToolButton{background:#FFFFFF;border:1px solid #CBD5E1;border-radius:6px;color:#334155;font-size:15px;font-weight:700;padding:0px;} QToolButton:hover{background:#EFF6FF;border-color:#93C5FD;color:#1D4ED8;} QToolButton:pressed{background:#DBEAFE;border-color:#60A5FA;}'''
        self.find_results_collapse_btn=QToolButton(); self.find_results_collapse_btn.setText('−'); self.find_results_collapse_btn.setToolTip('收起 / 展开查询结果文件')
        self.find_results_collapse_btn.setFixedSize(32,28); self.find_results_collapse_btn.setStyleSheet(result_ctrl_qss); self.find_results_collapse_btn.setCursor(Qt.PointingHandCursor); self.find_results_collapse_btn.clicked.connect(self._toggle_find_results_folder)
        folder_head.addWidget(self.find_results_collapse_btn)
        self.find_results_max_btn=QToolButton(); self.find_results_max_btn.setText('□'); self.find_results_max_btn.setToolTip('最大化 / 还原查询结果文件')
        self.find_results_max_btn.setFixedSize(32,28); self.find_results_max_btn.setStyleSheet(result_ctrl_qss); self.find_results_max_btn.setCursor(Qt.PointingHandCursor); self.find_results_max_btn.clicked.connect(self._toggle_find_results_folder_maximized)
        folder_head.addWidget(self.find_results_max_btn)
        folder_root.addLayout(folder_head)

        self.find_results_folder_content=QWidget(); folder_content=QVBoxLayout(self.find_results_folder_content); folder_content.setContentsMargins(0,0,0,0); folder_content.setSpacing(0)
        self.find_empty_label=QLabel("尚未查询\n\n拖入 QTX、从色库选择标准，或输入 LAB / RGB，然后点击【查询】")
        self.find_empty_label.setObjectName("empty"); self.find_empty_label.setAlignment(Qt.AlignCenter)
        self.find_tile_host=QWidget(); self.find_tile_grid=QGridLayout(self.find_tile_host); self.find_tile_grid.setContentsMargins(2,8,2,4); self.find_tile_grid.setSpacing(16)
        self.find_tile_scroll=QScrollArea(); self.find_tile_scroll.setWidgetResizable(True); self.find_tile_scroll.setFrameShape(QFrame.NoFrame); self.find_tile_scroll.setWidget(self.find_tile_host); self.find_tile_scroll.viewport().installEventFilter(self)
        self.find_tile_scroll.hide(); folder_content.addWidget(self.find_empty_label,1); folder_content.addWidget(self.find_tile_scroll,1)
        folder_root.addWidget(self.find_results_folder_content,1)
        lay.addWidget(self.find_results_folder,1)
        self.find_results_folder_collapsed=False; self.find_results_folder_maximized=False
        self._find_tile_pool={}
        self._find_tile_reflow_timer=QTimer(self); self._find_tile_reflow_timer.setSingleShot(True); self._find_tile_reflow_timer.setInterval(80)
        self._find_tile_reflow_timer.timeout.connect(self.build_find_tiles)
        scroll=QScrollArea(page);scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True);scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);scroll.setWidget(body)
        outer=QVBoxLayout(page);outer.setContentsMargins(0,0,0,0);outer.addWidget(scroll)
        return page

    def _find_labch_values(self, lab):
        L,a,b=(float(lab[0]),float(lab[1]),float(lab[2]))
        C=math.hypot(a,b); h=(math.degrees(math.atan2(b,a))%360.0)
        return (L,a,b,C,h)

    def _set_find_table_swatch(self,row,col,lab,tooltip='',sample=None):
        host=QWidget(); hl=QHBoxLayout(host); hl.setContentsMargins(5,3,5,3); hl.setAlignment(Qt.AlignCenter)
        sw=QFrame(); sw.setFixedSize(56,24); sw.setToolTip(tooltip)
        color=(sample_display_qcolor(sample,lab) if sample else lab_to_qcolor(lab)).name()
        sw.setStyleSheet(f'QFrame{{background:{color};border:1px solid #D1D5DB;border-radius:5px;}}')
        hl.addWidget(sw); self.find_multi_table.setCellWidget(row,col,host)

    def _toggle_find_results_folder(self):
        if not hasattr(self,'find_results_folder_content'): return
        self.find_results_folder_collapsed=not bool(getattr(self,'find_results_folder_collapsed',False))
        self.find_results_folder_content.setVisible(not self.find_results_folder_collapsed)
        self.find_results_collapse_btn.setText('+' if self.find_results_folder_collapsed else '−')

    def _toggle_find_results_folder_maximized(self):
        if not hasattr(self,'find_results_folder'): return
        self.find_results_folder_maximized=not bool(getattr(self,'find_results_folder_maximized',False))
        if self.find_results_folder_maximized and getattr(self,'find_results_folder_collapsed',False):
            self.find_results_folder_collapsed=False; self.find_results_folder_content.show(); self.find_results_collapse_btn.setText('−')
        # 只改变查色页的呈现面积：查询条件、数据和结果均保留。
        self.find_panel.setVisible(not self.find_results_folder_maximized)
        if hasattr(self,'find_result_head_widget'): self.find_result_head_widget.setVisible(not self.find_results_folder_maximized)
        if self.find_results_folder_maximized:
            self.find_multi_table.hide(); self.find_results_max_btn.setText('❐')
        else:
            self.find_results_max_btn.setText('□'); self._refresh_find_multi_overview()
        # 查询结果色卡只调整显示尺寸：最大化时给完整名称更多横向空间；查询数据与交互不变。
        if hasattr(self,'find_tile_grid'):
            self.build_find_tiles()

    def _refresh_find_multi_overview(self):
        """HF115 查询概览：标准固定，最佳匹配 + 当前选中结果纵向比较。"""
        if not hasattr(self,'find_multi_table'):
            return
        session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
        standards=[x for x in ((session or {}).get('samples') or []) if x is not None]
        per=(session or {}).get('per_standard') or {}
        show=bool(self.find_mode_active and standards and per and not getattr(self,'find_results_folder_maximized',False))
        self.find_multi_table.setVisible(show)
        if not show:
            self.find_multi_table.clearSpans(); self.find_multi_table.setRowCount(0); return

        selected=set(getattr(self,'library_selected_keys',set()) or set())
        # Use only the active task/standard snapshot. Never ask the generic
        # library filter for Find overview rows.
        ordered_result_keys=[k for k in self._find_result_keys(session,self.find_standard) if k not in self.hidden_cards]
        current_keys=[k for k in ordered_result_keys if k in selected]
        for k in selected:
            if k not in current_keys: current_keys.append(k)

        lookup_keys=list(current_keys)
        for vals in per.values():
            keys=vals.get('result_keys',[]) or []
            if keys: lookup_keys.append(keys[0])
        bykey={sample_key(x):x for x in self._samples_by_keys_on_demand(lookup_keys,full=True)}
        current_samples=[bykey[k] for k in current_keys if k in bykey]

        rows_per_standard=1+len(current_samples)
        self.find_multi_table.clearSpans(); self.find_multi_table.setRowCount(len(standards)*rows_per_standard)

        def put(row,col,text,bg=None,align=Qt.AlignCenter):
            item=QTableWidgetItem(str(text)); item.setTextAlignment(align)
            if bg is not None:item.setBackground(bg)
            self.find_multi_table.setItem(row,col,item)
            return item

        def delta_values(std,other):
            if other is None:return (None,)*6
            ls=self.sample_lab(std); lo=self.sample_lab(other); vals=self._find_values(std,other)
            cs=math.hypot(ls[1],ls[2]); co=math.hypot(lo[1],lo[2])
            return (vals.get('色差',float('nan')),lo[0]-ls[0],lo[1]-ls[1],lo[2]-ls[2],co-cs,delta_h_cielab_signed(ls,lo))

        row=0
        for std in standards:
            cache=per.get(sample_key(std),{}); keys=cache.get('result_keys',[]) or []
            best=bykey.get(keys[0]) if keys else None
            lstd=self.sample_lab(std); std_labch=self._find_labch_values(lstd); group_count=1+len(current_samples)
            self._set_find_table_swatch(row,0,lstd,std.display_name,std)
            put(row,1,std.display_name,align=Qt.AlignLeft|Qt.AlignVCenter)
            for col,val in enumerate(std_labch,start=2): put(row,col,f'{val:.1f}' if col==6 else f'{val:.2f}')
            if group_count>1:
                for col in range(0,7): self.find_multi_table.setSpan(row,col,group_count,1)

            comparison=[('最佳匹配',best,QColor('#F8FAFC'))]
            comparison.extend(('当前查看',sm,QColor('#EFF6FF')) for sm in current_samples)
            for offset,(kind,sm,bg) in enumerate(comparison):
                rr=row+offset; kind_item=put(rr,7,kind,bg); ft=kind_item.font(); ft.setBold(True); kind_item.setFont(ft)
                if sm is None:
                    put(rr,8,'',bg); put(rr,9,'—',bg,Qt.AlignLeft|Qt.AlignVCenter)
                    for col in range(10,21): put(rr,col,'—',bg)
                    continue
                lab=self.sample_lab(sm); labch=self._find_labch_values(lab)
                self._set_find_table_swatch(rr,8,lab,sm.display_name,sm)
                host=self.find_multi_table.cellWidget(rr,8)
                if host is not None and kind=='当前查看': host.setStyleSheet('background:#EFF6FF;')
                put(rr,9,sm.display_name,bg,Qt.AlignLeft|Qt.AlignVCenter)
                for col,val in enumerate(labch,start=10): put(rr,col,f'{val:.1f}' if col==14 else f'{val:.2f}',bg)
                vals=delta_values(std,sm)
                for col,val in enumerate(vals,start=15):
                    txt='—' if val is None or not math.isfinite(float(val)) else (f'{float(val):.3f}' if col==15 else f'{float(val):+.3f}')
                    put(rr,col,txt,bg)
            row += group_count
        self.find_multi_table.resizeRowsToContents()

    def build_find_tiles(self):
        if not hasattr(self,'find_tile_grid'): return
        # Find cards come exclusively from the active session cache. This fixes
        # the intermittent 309-card/library bleed where a black card could appear
        # in a red task and disappear after switching tabs.
        items=[s for s in self._find_result_samples(full=True) if sample_key(s) not in self.hidden_cards] if self.find_mode_active else []
        if not self.find_mode_active:
            self.library_selected_keys.clear()
        while self.find_tile_grid.count():
            it=self.find_tile_grid.takeAt(0); w=it.widget()
            if w is not None: w.hide()
        self.find_empty_label.setVisible(not items); self.find_tile_scroll.setVisible(bool(items))
        if hasattr(self,'find_results_folder_count'):
            source_files={str(getattr(x,'source_file','') or '') for x in items if str(getattr(x,'source_file','') or '')}
            self.find_results_folder_count.setText(f'{len(items)} 个色样 · {len(source_files)} 个文件')
        visible=set(sample_key(x) for x in items); self.library_selected_keys.intersection_update(visible)
        if getattr(self,'find_focus_key',None) not in visible: self.find_focus_key=None
        for i,sample in enumerate(items):
            key=sample_key(sample); tile=self._find_tile_pool.get(key)
            if tile is None:
                tile=ColorTile(sample,self); tile.toggledForSample.connect(self.toggle_sample); self._find_tile_pool[key]=tile
            elif tile.sample is not sample:
                tile.sample=sample; tile.refresh_color()
            # HF115: 自适应紧凑网格，不再固定四列，也不在宽屏上拉出大间隔。
            tile.setProperty('findResultCard',True)
            maximized=bool(getattr(self,'find_results_folder_maximized',False))
            compact=bool(getattr(self,'_workspace_compact_mode',False))
            card_w=(260 if compact else 280) if maximized else (225 if compact else 250)
            card_h=136 if compact else 150
            tile.setFixedSize(card_w,card_h); tile.setToolTip(sample.display_name)
            tile.blockSignals(True); tile.setChecked(key in self.library_selected_keys); tile.blockSignals(False)
            viewport_w=max(320,self.find_tile_scroll.viewport().width() if hasattr(self,'find_tile_scroll') else 1200)
            cols=max(1,int((viewport_w+10)//(card_w+10)))
            self.find_tile_grid.setHorizontalSpacing(10); self.find_tile_grid.setVerticalSpacing(10); self.find_tile_grid.setAlignment(Qt.AlignLeft|Qt.AlignTop)
            self.find_tile_grid.addWidget(tile,i//cols,i%cols); tile.show(); tile.updateGeometry(); tile.update(); QTimer.singleShot(0,tile.update)
        if hasattr(self,'find_selection_label'): self.find_selection_label.setText(f"已选 {len(self.library_selected_keys)} 个结果")
        if hasattr(self,'find_result_summary'):
            state='尚未查询' if not self.find_mode_active else f'{len(items)} 个'
            session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
            multi=len((session or {}).get('samples') or [])
            done=len((session or {}).get('per_standard') or {})
            extra=f" · 本任务 {done}/{multi} 个标准已有独立结果" if self.find_mode_active and multi>1 else ""
            self.find_result_summary.setText(f"查询结果：{state}{extra}")
        self._refresh_find_multi_overview()

    # ============ 色卡编排页 ============
    def build_color_cards_page(self):
        """Palette Studio: full-width canvas; management stays in menus/dialogs, not sidebars."""
        page=QWidget(); lay=QVBoxLayout(page); lay.setContentsMargins(18,14,18,14); lay.setSpacing(6)
        self.card_workspace_layout=lay
        head=QHBoxLayout(); head.setSpacing(10)
        title=QLabel('色卡编排工作区'); title.setObjectName('sectionTitle'); head.addWidget(title)
        self.card_workspace_info=QLabel('右键：新建/打开/导入/导出 · 双击空白：打开已有方案 · Ctrl+N 新建')
        self.card_workspace_info.setObjectName('muted'); head.addWidget(self.card_workspace_info)
        head.addStretch(1); lay.addLayout(head)

        self.card_mdi=PaletteMdiArea(); self.card_mdi.setViewMode(QMdiArea.SubWindowView)
        self.card_mdi.setOption(QMdiArea.DontMaximizeSubWindowOnActivation,True)
        self.card_mdi.setStyleSheet('QMdiArea{background:#F3F4F6;border:0;} QMdiSubWindow{background:#FFFFFF;border:0;}')
        self.card_mdi.workspaceMenuRequested.connect(lambda gp:self.color_card_workspace_menu(gp,True))
        # 双击空白不再占用画布创建新方案，而是弹出已有方案选择。
        self.card_mdi.blankDoubleClicked.connect(self.open_color_card_manager)
        self.card_mdi.newRequested.connect(self.new_color_card)
        self.card_mdi.closeAllRequested.connect(self.close_all_color_card_windows)
        lay.addWidget(self.card_mdi,1)
        empty=QLabel('尚未打开色卡方案\n\n可直接拖入 QTX / CPX / Excel，或右键新建 / 打开已有方案\n双击空白可打开已有方案；窗口大小变化不会改变已保存色卡位置',self.card_mdi.viewport())
        empty.setObjectName('empty'); empty.setAlignment(Qt.AlignCenter); empty.setAttribute(Qt.WA_TransparentForMouseEvents); empty.setGeometry(0,0,900,520); self.card_mdi_empty=empty
        QTimer.singleShot(0,self.refresh_color_card_list)
        return page

    def refresh_card_customer_tree(self):
        if not hasattr(self,'card_customer_tree'): return
        self.card_customer_tree.clear()
        # P3-3: folder counts are scalar SQL metadata; never materialise spectra
        # merely to draw the colour-card source navigation tree.
        try: counts=dict(self.store.customer_counts())
        except Exception: counts={}
        for group in self.store.list_customer_groups():counts.setdefault(group,0)
        nodes={}
        for customer in sorted(counts,key=str.casefold):
            parts=[p.strip() for p in customer.replace('\\','/').split('/') if p.strip()]
            if not parts: parts=['未分类']
            parent=None; acc=[]
            for part in parts:
                acc.append(part); full='/'.join(acc); key=(full,id(parent) if parent else 0)
                node=nodes.get(key)
                if node is None:
                    node=QTreeWidgetItem([part]); node.setData(0,Qt.UserRole,full)
                    if parent is None:self.card_customer_tree.addTopLevelItem(node)
                    else:parent.addChild(node)
                    nodes[key]=node
                parent=node
            parent.setText(0,f"{parts[-1]}   ({counts[customer]} 色样)"); parent.setData(0,Qt.UserRole,customer)
        self.card_customer_tree.collapseAll()

    def card_customer_tree_clicked(self,item,column=0):
        path=item.data(0,Qt.UserRole)
        if not path:return
        self.active_card_customer_path=str(path); self.refresh_card_source()

    def card_customer_tree_menu(self,pos):
        if not self.require_admin('维护正式色库客户分类'): return
        item=self.card_customer_tree.itemAt(pos); menu=QMenu(self)
        new_child=menu.addAction('新建子分类…'); rename=menu.addAction('重命名分类…') if item else None
        delete_group=menu.addAction('删除客户 / 分类…') if item else None
        chosen=menu.exec(self.card_customer_tree.viewport().mapToGlobal(pos))
        if chosen==new_child:
            parent=str(item.data(0,Qt.UserRole)) if item else ''
            name,ok=QInputDialog.getText(self,'新建子分类','子分类名称：')
            if ok and name.strip():
                full=(parent.rstrip('/')+'/'+name.strip()).strip('/') if parent else name.strip()
                self.store.save_customer_group(full); self.refresh_card_customer_tree(); self.update_customers()
                self.statusBar().showMessage(f'已建立客户分类：{full}',3500)
        elif rename is not None and chosen==rename:
            old=str(item.data(0,Qt.UserRole)); name,ok=QInputDialog.getText(self,'重命名分类','新名称：',text=old.split('/')[-1])
            if not ok or not name.strip():return
            prefix=old.rsplit('/',1)[0]+'/' if '/' in old else ''; newbase=prefix+name.strip()
            changed=0
            for row in list(self.store.list_files()):
                if row.customer==old or row.customer.startswith(old+'/'):
                    suffix=row.customer[len(old):]; self.store.save_file(row.path,newbase+suffix); changed+=1
            self._library_picker_cache=None; self.refresh_card_customer_tree(); self.update_customers(); self.statusBar().showMessage(f'已更新 {changed} 个 QTX 的客户分类',3500)
        elif delete_group is not None and chosen==delete_group:
            old=str(item.data(0,Qt.UserRole)); affected=[row for row in self.store.list_files() if row.customer==old or row.customer.startswith(old+'/')]
            if affected:
                QMessageBox.information(self,'删除客户 / 分类',f'【{old}】中仍有 {len(affected)} 个 QTX，请先移动或删除 QTX。')
            elif QMessageBox.question(self,'删除客户 / 分类',f'确定删除空客户/分类【{old}】吗？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
                self.store.remove_customer_group(old); self.refresh_card_customer_tree(); self.update_customers()

    def refresh_card_source(self):
        if not hasattr(self,'card_source'): return
        customer=self.active_card_customer_path
        self.card_source.clear()
        if not customer:
            self.card_source.hide(); self.card_source_hint.show(); self.card_customer_count.setText('请选择客户'); return
        # Default D65/10 browsing uses only the scalar index. Alternate viewing
        # conditions explicitly need reflectance, so only that customer is hydrated.
        if self.illuminant=='D65' and int(self.observer)==10:
            try: source_samples=self.store.customer_index_samples(customer)
            except Exception: source_samples=[]
        else:
            try:
                source_samples=[]
                for saved,items in self.store.library_contents(customer):
                    canonical=str(Path(saved.path).resolve())
                    source_samples.extend(replace(x,source_file=canonical) for x in items)
            except Exception: source_samples=[]
        count=0
        for sample in source_samples:
            item=QListWidgetItem(self._sample_icon(sample,QSize(84,56)),sample.display_name); item.setData(Qt.UserRole,sample_key(sample)); L,a,b=self.sample_lab(sample); C=math.hypot(a,b); h=(math.degrees(math.atan2(b,a))+360.0)%360.0; item.setToolTip(f'{sample.display_name}\nL* {L:.2f}   a* {a:.2f}   b* {b:.2f}\nC* {C:.2f}   h {h:.2f}°'); self.card_source.addItem(item); count+=1
        self.card_customer_count.setText(f'{customer} · {count} 色样'); self.card_source_hint.setVisible(count==0); self.card_source.setVisible(count>0)

    @staticmethod
    def _qt_object_alive(obj):
        """Return False when a PySide wrapper outlives its deleted C++ object.

        hasattr() is not enough for Qt objects: during QObject destruction the
        Python wrapper may still exist while libshiboken has already deleted the
        underlying QLabel/QWidget.  Any method call then raises RuntimeError.
        """
        if obj is None:
            return False
        try:
            obj.objectName()
            return True
        except RuntimeError:
            return False

    def refresh_color_card_list(self):
        # HF65: a QMdiSubWindow.destroyed signal can arrive while the main window
        # (or the palette page) is already being torn down.  Do not touch UI
        # wrappers after their C++ objects have been deleted.
        if getattr(self,'_app_closing',False) or QApplication.closingDown():
            return
        self.color_cards=self.store.list_color_cards()
        scheme_list=getattr(self,'card_scheme_list',None)
        if self._qt_object_alive(scheme_list):
            current=scheme_list.currentItem().data(Qt.UserRole) if scheme_list.currentItem() else None
            scheme_list.blockSignals(True); scheme_list.clear()
            for card in self.color_cards:
                item=QListWidgetItem(card.get('name','未命名色卡')); item.setData(Qt.UserRole,card.get('card_id'))
                settings=card.get('settings',{}) or {}; cols=int(settings.get('grid_cols',card.get('columns_count',0) or 0)); rows=int(settings.get('grid_rows',0) or 0)
                count=sum(1 for e in card.get('layout',[]) if (e.get('sample_key') if isinstance(e,dict) else e))
                item.setToolTip(f'{count} 色样' + (f' · {cols}×{rows} 固定网格' if cols and rows else ''))
                scheme_list.addItem(item)
                if card.get('card_id')==current:scheme_list.setCurrentItem(item)
            scheme_list.blockSignals(False)
        open_combo=getattr(self,'card_open_combo',None)
        if self._qt_object_alive(open_combo):
            current=open_combo.currentData(); open_combo.blockSignals(True); open_combo.clear()
            open_combo.addItem('请选择已有方案…',None)
            for card in self.color_cards:open_combo.addItem(card['name'],card['card_id'])
            idx=open_combo.findData(current); open_combo.setCurrentIndex(idx if idx>=0 else 0); open_combo.blockSignals(False)
        workspace_info=getattr(self,'card_workspace_info',None)
        if self._qt_object_alive(workspace_info):
            opened=len(getattr(self,'card_subwindows',{})); workspace_info.setText(f'已保存 {len(self.color_cards)} 个方案 · 已打开 {opened} 个 · 可直接拖入 QTX / CPX / Excel；右键新建/打开/导入/导出')
        mdi_empty=getattr(self,'card_mdi_empty',None); card_mdi=getattr(self,'card_mdi',None)
        if self._qt_object_alive(mdi_empty) and self._qt_object_alive(card_mdi):
            mdi_empty.setGeometry(card_mdi.viewport().rect())
            mdi_empty.setVisible(not bool(getattr(self,'card_subwindows',{})))

    def color_card_workspace_menu(self,pos,already_global=False):
        menu=QMenu(self)
        new_a=menu.addAction('新建色卡方案    Ctrl+N')
        open_qtx_a=menu.addAction('打开 QTX 到色卡编排…')
        open_cpx_a=menu.addAction('打开 CPX 色卡方案…')
        openm=menu.addMenu('打开已有方案')
        cards=self.store.list_color_cards(); open_actions={}
        # Keep the workspace menu compact: only recent saved schemes appear inline.
        # The searchable manager is the canonical entry when the scheme count grows.
        for card in cards[:6]:open_actions[openm.addAction(card['name'])]=card['card_id']
        if cards:
            openm.addSeparator(); more=openm.addAction(f'查看全部方案…（共 {len(cards)} 个）')
        else:
            empty_a=openm.addAction('暂无已保存方案'); empty_a.setEnabled(False); more=None
        active=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
        active_win=active.widget() if active and isinstance(active.widget(),ColorCardPlanWindow) else None
        export_actions={}
        if active_win:
            menu.addSeparator(); save_a=menu.addAction('保存当前方案    Ctrl+S'); save_as_a=menu.addAction('另存为方案副本…    Ctrl+Shift+S')
            undo_a=menu.addAction('撤销    Ctrl+Z'); undo_a.setEnabled(bool(active_win._undo_stack))
            redo_a=menu.addAction('重做    Ctrl+Y'); redo_a.setEnabled(bool(active_win._redo_stack))
            arrange=menu.addMenu('整理方案')
            restore_original=arrange.addAction('恢复导入顺序')
            compact=arrange.addAction('压缩空位')
            arrange.addSeparator()
            sortm=arrange.addMenu('排序'); sortacts={}
            labch=sortm.addMenu('综合色坐标排序')
            for title,key in [('L* 明度','L'),('a* 红绿轴','a'),('b* 黄蓝轴','b'),('C* 彩度','C'),('h° 色相（环形排序）','h')]:
                sortacts[labch.addAction(title)]=key
            sortm.addSeparator()
            color_diff_a=sortm.addAction('基准色差')
            running_de_a=sortm.addAction('连续色差')
            reverse_order_a=sortm.addAction('反向当前顺序')
            atlas_a=arrange.addAction('打开色彩图谱')
            auto=arrange.addAction('固定列数 · 自动扩展行'); auto.setCheckable(True); auto.setChecked(active_win.auto_grid)
            manual=arrange.addAction('设置固定网格…')
            exportm=menu.addMenu('导出')
            export_actions[exportm.addAction('PDF 色卡…')]='pdf'; export_actions[exportm.addAction('PNG 图片…')]='png'; export_actions[exportm.addAction('JPG 图片…')]='jpg'
            exportm.addSeparator(); export_actions[exportm.addAction('Excel 色卡工作簿…')]='excel'; export_actions[exportm.addAction('QTX…')]='qtx'; export_actions[exportm.addAction('CPX…')]='cpx'
            rename_a=menu.addAction('重命名当前方案…'); close_a=menu.addAction('关闭当前窗口'); delete_a=menu.addAction('删除当前方案…')
        else:save_a=save_as_a=rename_a=close_a=delete_a=undo_a=redo_a=restore_original=compact=auto=manual=color_diff_a=running_de_a=reverse_order_a=atlas_a=None; sortacts={}
        if getattr(self,'card_subwindows',{}):
            menu.addSeparator(); close_all=menu.addAction(f'关闭全部方案窗口（{len(self.card_subwindows)}）    Ctrl+Shift+W')
        else:close_all=None
        chosen=menu.exec(pos if already_global else self.card_mdi.viewport().mapToGlobal(pos))
        if chosen==new_a:self.new_color_card(); return
        if chosen==open_qtx_a:self.open_qtx_in_color_card(); return
        if chosen==open_cpx_a:self.open_cpx_files(); return
        if chosen in open_actions:self.open_color_card_window(open_actions[chosen]); return
        if more is not None and chosen==more:self.open_color_card_manager(); return
        if active_win and chosen==save_a:active_win.trigger_command('save'); return
        if active_win and chosen==save_as_a:active_win.trigger_command('save_as'); return
        if active_win and chosen==undo_a:active_win.trigger_command('undo'); return
        if active_win and chosen==redo_a:active_win.trigger_command('redo'); return
        if active_win and chosen==restore_original:active_win.restore_original_order(); return
        if active_win and chosen==compact:active_win.compact_blanks(); return
        if active_win and chosen in sortacts:active_win.sort_items(sortacts[chosen]); return
        if active_win and chosen==color_diff_a:active_win.sort_by_color_difference(); return
        if active_win and chosen==running_de_a:active_win.sort_by_running_delta(); return
        if active_win and chosen==reverse_order_a:active_win.reverse_current_order(); return
        if active_win and chosen==atlas_a:active_win.open_labc_atlas(); return
        if active_win and chosen==auto:active_win.auto_grid=True; active_win.update_grid_size(); return
        if active_win and chosen==manual:active_win.configure_grid(); return
        if active_win and chosen in export_actions:active_win.export_format(export_actions[chosen]); return
        if active_win and chosen==rename_a:self.rename_color_card(active_win.card['card_id']); return
        if active_win and chosen==close_a:active.close(); return
        if active_win and chosen==delete_a:
            if active_win.card.get('_draft'):
                active.close(); self.statusBar().showMessage('未保存的新建方案已丢弃',3000)
            else:
                self.delete_color_cards([active_win.card['card_id']],confirm=True)
            return
        if close_all is not None and chosen==close_all:self.close_all_color_card_windows(); return

    def open_color_card_manager(self):
        dlg=ColorCardSchemeManagerDialog(self,self); dlg.exec(); self.refresh_color_card_list()

    def close_all_color_card_windows(self):
        for sub in list(getattr(self,'card_subwindows',{}).values()):
            try:sub.close()
            except RuntimeError:pass
        self.refresh_color_card_list()

    def delete_color_cards(self,card_ids,confirm=True):
        ids=[str(x) for x in card_ids if x]
        if not ids:return
        cards={c['card_id']:c for c in self.store.list_color_cards()}; names=[cards[x]['name'] for x in ids if x in cards]
        if confirm:
            preview='、'.join(names[:4])+('…' if len(names)>4 else '')
            if QMessageBox.question(self,'删除色卡方案',f'确定删除 {len(ids)} 个方案？\n{preview}\n\n只删除方案布局，不会删除任何色库色样。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        failures=[]; deleted=0
        for cid in ids:
            try:
                self.store.remove_color_card(cid); deleted+=1
                sub=getattr(self,'card_subwindows',{}).get(cid)
                if sub:
                    try:sub.close()
                    except RuntimeError:pass
            except Exception as exc:failures.append(str(exc))
        self.refresh_color_card_list(); self.statusBar().showMessage(f'已删除 {deleted} 个色卡方案',3500)
        if failures:QMessageBox.information(self,'删除色卡方案','部分方案未删除：\n'+'\n'.join(dict.fromkeys(failures)))

    def _auto_open_color_card(self,index):
        if index<=0:return
        cid=self.card_open_combo.itemData(index)
        if cid:QTimer.singleShot(0,lambda c=cid:self.open_color_card_window(c))

    def open_selected_color_card(self):
        cid=self.card_open_combo.currentData() if hasattr(self,'card_open_combo') else None
        if cid:self.open_color_card_window(cid)

    def _open_color_card_object(self,card):
        card=dict(card or {})
        card_id=str(card.get('card_id') or uuid.uuid4()); card['card_id']=card_id
        if card_id in self.card_subwindows:
            sub=self.card_subwindows[card_id]; sub.showMaximized(); self.card_mdi.setActiveSubWindow(sub)
            QTimer.singleShot(0,self.card_plan_windows[card_id].grid.setFocus)
            return
        win=ColorCardPlanWindow(self,card); sub=QMdiSubWindow(); sub.setWidget(win)
        sub.setWindowTitle(card['name'] + (' · 未保存' if card.get('_draft') else ''))
        sub.setAttribute(Qt.WA_DeleteOnClose,True); sub.resize(min(1120,max(780,self.card_mdi.width()-90)),min(760,max(520,self.card_mdi.height()-90)))
        sub.setContextMenuPolicy(Qt.CustomContextMenu)
        sub.customContextMenuRequested.connect(lambda pos, w=sub: self.color_card_workspace_menu(w.mapToGlobal(pos),already_global=True))
        self.card_mdi.addSubWindow(sub); self.card_plan_windows[card_id]=win; self.card_subwindows[card_id]=sub
        def cleanup(_obj=None,cid=card_id):
            self.card_plan_windows.pop(cid,None); self.card_subwindows.pop(cid,None)
            # QObject.destroyed is emitted during C++ teardown.  Defer the normal
            # refresh to the event loop and skip it entirely during app shutdown.
            if getattr(self,'_app_closing',False) or QApplication.closingDown():
                return
            QTimer.singleShot(0,self.refresh_color_card_list)
        sub.destroyed.connect(cleanup); sub.showMaximized(); self.card_mdi.setActiveSubWindow(sub); self.card_mdi_empty.hide()
        QTimer.singleShot(0,win.grid.setFocus)

    def open_color_card_window(self,card_id):
        if card_id in self.card_subwindows:
            sub=self.card_subwindows[card_id]; sub.showMaximized(); self.card_mdi.setActiveSubWindow(sub)
            QTimer.singleShot(0,self.card_plan_windows[card_id].grid.setFocus)
            return
        card=next((x for x in self.store.list_color_cards() if x['card_id']==card_id),None)
        if card:self._open_color_card_object(card)

    def new_color_card(self):
        # A newly created palette is a transient draft until Ctrl+S / 保存 is used.
        # Closing it therefore cannot pollute the saved-scheme list.
        existing={str(c.get('name','')).strip() for c in self.store.list_color_cards()}
        existing.update(str(w.card.get('name','')).strip() for w in getattr(self,'card_plan_windows',{}).values())
        n=1
        while f'色卡方案 {n:03d}' in existing: n+=1
        name=f'色卡方案 {n:03d}'
        settings={'sort':'manual','illuminant':self.illuminant,'observer':self.observer,'grid_cols':6,'grid_rows':0,'auto_grid':True,'cell_w':136,'cell_h':120}
        card={'card_id':str(uuid.uuid4()),'name':name,'customer':'色卡方案','columns_count':6,'layout':[],'settings':settings,'owner_user_id':int(self.current_user.user_id or 0),'visibility':'private','lifecycle_status':'draft','_draft':True}
        self.active_color_card_id=card['card_id']; self._open_color_card_object(card); self.refresh_color_card_list()
        self.statusBar().showMessage(f'已新建 {name}（未保存）；Ctrl+S 保存，直接关闭将不进入已有方案',5000)

    def rename_color_card(self, card_id):
        card=next((x for x in self.store.list_color_cards() if x['card_id']==card_id),None)
        win=self.card_plan_windows.get(card_id)
        if card is None and win is not None and win.card.get('_draft'):
            card=win.card
        if not card:return
        name,ok=QInputDialog.getText(self,'重命名色卡方案','方案名称：',text=card['name'])
        if not ok or not name.strip():return
        card['name']=name.strip()
        if card.get('_draft'):
            if win:win.card=card
            sub=self.card_subwindows.get(card_id)
            if sub:sub.setWindowTitle(card['name']+' · 未保存')
        else:
            card=self.store.save_color_card(card)
            sub=self.card_subwindows.get(card_id)
            if sub:sub.setWindowTitle(card['name'])
            if win:win.card=card
            self.refresh_color_card_list()
        self.statusBar().showMessage(f'已重命名为：{card["name"]}'+('（未保存）' if card.get('_draft') else ''),3500)

    def color_card_combo_menu(self,pos):
        cid=self.card_open_combo.currentData(); menu=QMenu(self)
        rename=menu.addAction('重命名方案…'); rename.setEnabled(bool(cid))
        chosen=menu.exec(self.card_open_combo.mapToGlobal(pos))
        if chosen==rename and cid:self.rename_color_card(cid)

    def add_samples_to_color_card(self,samples):
        samples=[x for x in samples if x is not None]
        if not samples:return
        # 优先当前激活方案；没有方案时自动新建，避免“只跳页面不带数据”。
        sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
        win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
        if win is None:
            opened=list(self.card_plan_windows.values()) if hasattr(self,'card_plan_windows') else []
            win=opened[0] if opened else None
        if win is None:
            self.new_color_card()
            sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
            win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
        if win is not None:
            win._embed_samples(samples)
            win.receive_drop_many([sample_key(x) for x in samples],0,copy_mode=True)
            win.save()
        self.show_page(2)
        if win is not None:
            sub=self.card_subwindows.get(win.card['card_id']);
            if sub:self.card_mdi.setActiveSubWindow(sub); sub.showMaximized()
        self.statusBar().showMessage(f'已将 {len(samples)} 个色样加入色卡编排方案',4000)

    def _activate_workbench_after_add(self, wb):
        """Open/focus the exact workbench that just received samples."""
        if not wb:return
        wb_id=wb.get('workbench_id')
        self.active_workbench_id=wb_id
        self.show_page(3)
        def focus_target():
            if not hasattr(self,'workbench_tabs'):return
            for i in range(self.workbench_tabs.count()):
                widget=self.workbench_tabs.widget(i)
                if widget is not None and widget.property('workbench_id')==wb_id:
                    self.workbench_tabs.setCurrentIndex(i); break
            try:
                sub=getattr(self,'tool_subwindows',{}).get(3)
                if sub is not None:
                    sub.show(); self.studio_mdi.setActiveSubWindow(sub); sub.raise_()
            except Exception: pass
        QTimer.singleShot(0,focus_target)

    def _choose_workbench_target(self, samples, parent=None):
        samples=[sm for sm in (samples or []) if sm is not None]
        if not samples:return None
        dlg=WorkbenchTargetDialog(self.workbenches, f"工作台 {len(self.workbenches)+1}", parent or self)
        if dlg.exec()!=QDialog.Accepted:return None
        mode,value=dlg.target()
        wb=None
        if mode=='existing':
            wb=self._find_workbench(str(value))
            if wb and wb.get('is_collapsed'):
                wb['is_collapsed']=False; self.store.save_workbench(wb); self.refresh_workbench_tabs()
        else:
            wb=self.create_workbench(str(value))
        if wb:
            self._append_samples_to_workbench(wb,samples)
            self._activate_workbench_after_add(wb)
        return wb

    def _menu_add_sample_to_workbench(self,sample,global_pos=None):
        samples=list(sample) if isinstance(sample,(list,tuple)) else [sample]
        return self._choose_workbench_target(samples,self)

    def _sample_icon(self, sample: Sample, size=QSize(120, 76)) -> QIcon:
        # 图标颜色只与色样 + 当前观察条件 + 尺寸有关。缓存可避免色卡编排/客户切换时
        # 为同一色样重复建立 QPixmap。
        key=(self._science_sample_signature(sample), self.illuminant, self.observer, size.width(), size.height())
        icon=self._sample_icon_cache.get(key)
        if icon is not None:
            return icon
        pix = QPixmap(size)
        pix.fill(sample_display_qcolor(sample,self.sample_lab(sample)))
        icon=QIcon(pix)
        self._sample_icon_cache[key]=icon
        return icon

    def _blank_card_item(self) -> QListWidgetItem:
        # 空位使用与真实色卡完全相同的图标尺寸，便于观察和手工拖动排序。
        size = QSize(120, 76)
        pix = QPixmap(size)
        pix.fill(QColor("#FFFFFF"))
        painter = QPainter(pix)
        pen = QPen(QColor("#AEB6C2"))
        pen.setWidth(2)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(1, 1, size.width() - 3, size.height() - 3)
        painter.end()
        item = QListWidgetItem(QIcon(pix), "空位")
        item.setData(Qt.UserRole, None)
        item.setToolTip("空位：可像色卡一样拖动，用于手工调整排版")
        item.setSizeHint(self.card_layout.gridSize() if hasattr(self, "card_layout") else QSize(165, 120))
        item.setTextAlignment(Qt.AlignHCenter)
        return item

    def _all_library_samples(self) -> list[Sample]:
        return self.samples_for_library_picker()

    def _legacy_refresh_card_source(self):
        if not hasattr(self, "card_source"):
            return
        customer = self.card_customer.currentText()
        path_customer = {str(Path(x.path).resolve()): x.customer for x in self.store.list_files()}
        self.card_source.clear()
        for sample in self._all_library_samples():
            if customer != "全部客户" and path_customer.get(str(Path(sample.source_file).resolve())) != customer:
                continue
            item = QListWidgetItem(self._sample_icon(sample, QSize(84, 56)), sample.display_name)
            item.setData(Qt.UserRole, sample_key(sample))
            item.setToolTip(sample.display_name)
            self.card_source.addItem(item)

    def _legacy_refresh_color_card_list(self):
        if not hasattr(self, "card_selector"):
            return
        current = self.active_color_card_id
        self.color_cards = self.store.list_color_cards()
        self.card_selector.blockSignals(True)
        self.card_selector.clear()
        for card in self.color_cards:
            self.card_selector.addItem(f"{card['customer']} · {card['name']}", card["card_id"])
        self.card_selector.blockSignals(False)
        if current:
            idx = self.card_selector.findData(current)
            if idx >= 0:
                self.card_selector.setCurrentIndex(idx)
        if self.card_selector.count():
            self.load_selected_color_card()
        else:
            self.card_layout.clear()

    def _legacy_new_color_card(self):
        name, ok = QInputDialog.getText(self, "新建色卡", "色卡名称：", text="MPC 色卡")
        if not ok or not name.strip():
            return
        customer, ok = QInputDialog.getText(self, "新建色卡", "客户名称：", text="MPC")
        if not ok:
            return
        card = self.store.save_color_card({"name": name.strip(), "customer": customer.strip() or "未分类",
                                           "columns_count": 6, "layout": [],
                                           "settings": {"sort": "manual", "illuminant": self.illuminant,
                                                        "observer": self.observer, "grid_cols": 6, "grid_rows": 4,
                                                        "auto_grid": True, "cell_w": 165, "cell_h": 120}})
        self.active_color_card_id = card["card_id"]
        self.refresh_color_card_list()

    def _current_card(self):
        card_id = self.card_selector.currentData() if hasattr(self, "card_selector") else None
        return next((x for x in self.color_cards if x["card_id"] == card_id), None)

    def load_selected_color_card(self, *_):
        card = self._current_card()
        self.card_layout.clear()
        if not card:
            return
        self.active_color_card_id = card["card_id"]
        settings = card.get("settings", {}) or {}
        self.card_cols.blockSignals(True); self.card_rows.blockSignals(True)
        self.card_cols.setValue(int(settings.get("grid_cols", card.get("columns_count", 6) or 6)))
        self.card_rows.setValue(int(settings.get("grid_rows", 4)))
        self.card_cols.blockSignals(False); self.card_rows.blockSignals(False)
        if hasattr(self,"card_auto_grid"):
            is_auto = bool(settings.get("auto_grid", True))
            self.card_auto_grid.blockSignals(True); self.card_auto_grid.setChecked(is_auto); self.card_auto_grid.blockSignals(False)
            if hasattr(self, "card_grid_mode"):
                self.card_grid_mode.blockSignals(True); self.card_grid_mode.setCurrentIndex(0 if is_auto else 1); self.card_grid_mode.blockSignals(False)
                self.manual_grid_btn.setVisible(not is_auto)
        QTimer.singleShot(0, self.update_adaptive_card_grid)
        layout_keys=[]
        for entry in card.get("layout", []):
            key=entry.get("sample_key") if isinstance(entry,dict) else entry
            if key:layout_keys.append(str(key))
        try:
            if self.illuminant=='D65' and int(self.observer)==10:
                indexed=self.store.load_index_samples_by_keys(layout_keys)
            else:
                indexed=self.store.load_samples_by_keys(layout_keys)
        except Exception:
            indexed=[]
        by_key={sample_key(x):x for x in indexed}
        # Current unsaved workspace samples can also appear in a palette.
        for x in self.samples:by_key.setdefault(sample_key(x),x)
        for data in (card.get('settings',{}) or {}).get('embedded_samples',[]) or []:
            try:
                x=self._deserialize_sample(data);by_key.setdefault(sample_key(x),x)
            except Exception:pass
        for entry in card.get("layout", []):
            key = entry.get("sample_key") if isinstance(entry, dict) else entry
            if not key:
                item = self._blank_card_item()
            elif key in by_key:
                sample = by_key[key]
                item = QListWidgetItem(self._sample_icon(sample), sample.display_name)
                item.setData(Qt.UserRole, key)
            else:
                item = QListWidgetItem("缺失色样")
                item.setData(Qt.UserRole, key)
                item.setForeground(QColor("#B42332"))
            self.card_layout.addItem(item)
        self.ensure_card_slots()
        if hasattr(self, "card_auto_grid") and self.card_auto_grid.isChecked():
            self._schedule_card_auto_reflow(0, force_shape=True)

    def _card_nonblank_keys(self):
        return [self.card_layout.item(i).data(Qt.UserRole) for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole)]

    def _card_auto_shape(self, n):
        """自动模式只在容量不足时扩容，并预留少量空位，避免每加一张卡都跳一次布局。"""
        n=max(1, int(n or 1))
        # 4 张以上预留约 20% 容量，连续加入时通常无需立即再次改变行列。
        target = min(400, max(6, n, int(math.ceil(n * 1.20))))
        # 使用 QListWidget 外框尺寸，而不是 viewport 尺寸。viewport 会受滚动条
        # 出现/消失影响，拿它反算列数会导致自动网格来回跳。
        outer=self.card_layout.size()
        aspect=max(.75,min(3.5, outer.width()/max(outer.height(),1)))
        cols=max(1,min(20,int(math.ceil(math.sqrt(target*aspect)))))
        rows=max(1,min(20,int(math.ceil(target/cols))))
        while cols*rows<n and rows<20:
            rows+=1
        return cols, rows

    def _card_grid_mode_changed(self, index):
        """切换自动/手动网格。手动模式立即弹出行列设置，界面不常驻两只 SpinBox。"""
        if not hasattr(self, "card_auto_grid"):
            return
        is_auto = (index == 0)
        self.card_auto_grid.blockSignals(True); self.card_auto_grid.setChecked(is_auto); self.card_auto_grid.blockSignals(False)
        if hasattr(self, "manual_grid_btn"):
            self.manual_grid_btn.setVisible(not is_auto)
        if is_auto:
            self._schedule_card_auto_reflow(0)
        else:
            QTimer.singleShot(0, self._open_manual_grid_dialog)

    def _open_manual_grid_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("手动设置网格")
        box = QVBoxLayout(dlg)
        tip = QLabel("设置当前布局的列数和行数。格子大小仍会根据右侧可用区域自动适配。")
        tip.setWordWrap(True); box.addWidget(tip)
        grid = QGridLayout(); box.addLayout(grid)
        cols = QSpinBox(); cols.setRange(1,20); cols.setValue(self.card_cols.value())
        rows = QSpinBox(); rows.setRange(1,20); rows.setValue(self.card_rows.value())
        grid.addWidget(QLabel("列数"),0,0); grid.addWidget(cols,0,1)
        grid.addWidget(QLabel("行数"),1,0); grid.addWidget(rows,1,1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); box.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return False
        need = len(self._card_nonblank_keys())
        if cols.value()*rows.value() < need:
            QMessageBox.warning(self, "网格太小", f"当前已有 {need} 张色卡，{cols.value()}×{rows.value()} 只有 {cols.value()*rows.value()} 个位置。")
            return self._open_manual_grid_dialog()
        self.card_cols.blockSignals(True); self.card_rows.blockSignals(True)
        self.card_cols.setValue(cols.value()); self.card_rows.setValue(rows.value())
        self.card_cols.blockSignals(False); self.card_rows.blockSignals(False)
        self.ensure_card_slots(); self.update_adaptive_card_grid(); self.card_layout.viewport().update()
        return True

    def _schedule_card_auto_reflow(self, delay=120, force_shape=False):
        if not hasattr(self, "card_auto_grid") or not self.card_auto_grid.isChecked():
            return
        self._pending_card_auto_target = len(self._card_nonblank_keys())
        self._force_card_auto_shape = bool(self._force_card_auto_shape or force_shape)
        if hasattr(self, "_card_auto_reflow_timer"):
            self._card_auto_reflow_timer.start(max(0, int(delay)))

    def _apply_scheduled_card_auto_reflow(self):
        if not hasattr(self, "card_auto_grid") or not self.card_auto_grid.isChecked():
            return
        n = max(1, int(self._pending_card_auto_target or len(self._card_nonblank_keys()) or 1))
        self._pending_card_auto_target = None
        # 只有现有容量不足时才改变行列；删除色卡不会自动缩网格，避免画面来回跳。
        capacity = max(1, self.card_cols.value()*self.card_rows.value())
        force_shape = bool(getattr(self, "_force_card_auto_shape", False))
        self._force_card_auto_shape = False
        shape_changed = False
        if force_shape or n > capacity or self.card_layout.count() == 0:
            cols, rows = self._card_auto_shape(n)
            shape_changed = (cols != self.card_cols.value() or rows != self.card_rows.value())
            self._set_card_grid_shape(cols, rows)
        self.ensure_card_slots()
        # 容量足够时，加入色卡只替换槽位，不再重新计算所有 item 的尺寸。
        if shape_changed or force_shape or not hasattr(self, "_last_card_grid_metrics"):
            self.update_adaptive_card_grid()
        else:
            self.card_layout.viewport().update()

    def _set_card_grid_shape(self, cols, rows):
        """只改变行列参数，不触发 rebuild，避免加入色卡时反复清空/重画造成闪烁。"""
        self.card_cols.blockSignals(True); self.card_rows.blockSignals(True)
        self.card_cols.setValue(int(cols)); self.card_rows.setValue(int(rows))
        self.card_cols.blockSignals(False); self.card_rows.blockSignals(False)

    def _compact_card_items(self, keys=None):
        """一次性重排网格。重绘期间冻结 viewport，防止 clear/addItem 过程被用户看到。"""
        if keys is None:
            keys=self._card_nonblank_keys()
        total=max(1, self.card_cols.value()*self.card_rows.value())
        sb=self.card_layout.verticalScrollBar(); scroll=sb.value()
        self.card_layout.setUpdatesEnabled(False)
        self.card_layout.blockSignals(True)
        try:
            self.card_layout.clear()
            for key in keys[:total]:
                self.card_layout.addItem(self._card_item_for_key(key))
            for _ in range(max(0,total-len(keys))):
                self.card_layout.addItem(self._blank_card_item())
        finally:
            self.card_layout.blockSignals(False)
            self.card_layout.setUpdatesEnabled(True)
        sb.setValue(min(scroll, sb.maximum()))
        self.update_adaptive_card_grid()
        self.card_layout.viewport().update()

    def auto_fit_card_dimensions(self, target_count=None, compact=True):
        if not hasattr(self,'card_layout') or not hasattr(self,'card_auto_grid') or not self.card_auto_grid.isChecked():
            return
        keys=self._card_nonblank_keys()
        n=max(1, target_count if target_count is not None else len(keys))
        cols,rows=self._card_auto_shape(n)
        shape_changed=(cols!=self.card_cols.value() or rows!=self.card_rows.value())
        self._set_card_grid_shape(cols,rows)
        if compact:
            self._compact_card_items(keys)
        else:
            # 只扩容/收缩尾部空位，不清空现有色卡，加入时不会闪屏。
            self.ensure_card_slots()
            if shape_changed:
                self.update_adaptive_card_grid()

    def add_card_source_item(self, item):
        if not item: return
        key=item.data(Qt.UserRole)
        if not key: return
        current=self._card_nonblank_keys()
        was_empty = not current
        if key in current: return
        blank=next((i for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) is None),-1)
        if blank < 0:
            if self.card_auto_grid.isChecked():
                # 自动模式先直接追加，120ms 内的连续操作合并成一次网格重算。
                self.card_layout.addItem(self._card_item_for_key(key))
                self._schedule_card_auto_reflow()
                return
            if not self._open_manual_grid_dialog():
                return
            blank=next((i for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) is None),-1)
            if blank < 0:
                return
        self.card_layout.setUpdatesEnabled(False)
        try:
            self.place_sample_in_card_slot(key,blank)
        finally:
            self.card_layout.setUpdatesEnabled(True)
        if self.card_auto_grid.isChecked():
            self._schedule_card_auto_reflow(force_shape=was_empty)
        self.card_layout.viewport().update()

    def add_selected_to_card(self):
        selected=[x for x in self.card_source.selectedItems() if x.data(Qt.UserRole)]
        current=self._card_nonblank_keys(); was_empty = not current; existing=set(current)
        new_keys=[]
        for x in selected:
            key=x.data(Qt.UserRole)
            if key not in existing:
                existing.add(key); new_keys.append(key)
        if not new_keys:
            return
        if not self.card_auto_grid.isChecked():
            free=sum(1 for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) is None)
            if free < len(new_keys):
                if not self._open_manual_grid_dialog():
                    return
                free=sum(1 for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) is None)
                if free < len(new_keys):
                    QMessageBox.information(self,"网格已满","手动网格空位不足，请增大行数或列数。")
                    return
        self.card_layout.setUpdatesEnabled(False); self.card_layout.blockSignals(True)
        try:
            for key in new_keys:
                blank=next((i for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) is None),-1)
                if blank >= 0:
                    self._replace_card_slot(blank,self._card_item_for_key(key))
                elif self.card_auto_grid.isChecked():
                    self.card_layout.addItem(self._card_item_for_key(key))
        finally:
            self.card_layout.blockSignals(False); self.card_layout.setUpdatesEnabled(True)
        if self.card_auto_grid.isChecked():
            self._schedule_card_auto_reflow(force_shape=was_empty)
        else:
            self.update_adaptive_card_grid()
        self.card_layout.viewport().update()

    def _card_item_for_key(self, key):
        need_full=not (self.illuminant=='D65' and int(self.observer)==10)
        loaded=self._samples_by_keys_on_demand([key],full=need_full)
        sample=loaded[0] if loaded else None
        if not sample:
            return self._blank_card_item()
        item = QListWidgetItem(self._sample_icon(sample), sample.display_name)
        item.setData(Qt.UserRole, key)
        item.setSizeHint(self.card_layout.gridSize())
        item.setTextAlignment(Qt.AlignHCenter)
        return item

    def _replace_card_slot(self, row, item):
        if row < 0 or row >= self.card_layout.count():
            return
        self.card_layout.takeItem(row)
        self.card_layout.insertItem(row, item)

    def place_sample_in_card_slot(self, key, target):
        if not key or target < 0:
            return
        # 同一色样已在网格中时，移动到目标格，并在原位置留下空位。
        existing = next((i for i in range(self.card_layout.count()) if self.card_layout.item(i).data(Qt.UserRole) == key), -1)
        if existing == target:
            return
        target_key = self.card_layout.item(target).data(Qt.UserRole)
        self._replace_card_slot(target, self._card_item_for_key(key))
        if existing >= 0:
            self._replace_card_slot(existing, self._card_item_for_key(target_key) if target_key else self._blank_card_item())

    def swap_card_slots(self, source, target):
        if source == target or min(source, target) < 0 or max(source, target) >= self.card_layout.count():
            return
        a = self.card_layout.item(source).data(Qt.UserRole)
        b = self.card_layout.item(target).data(Qt.UserRole)
        self._replace_card_slot(source, self._card_item_for_key(b) if b else self._blank_card_item())
        self._replace_card_slot(target, self._card_item_for_key(a) if a else self._blank_card_item())

    def ensure_card_slots(self):
        total = self.card_cols.value() * self.card_rows.value()
        while self.card_layout.count() < total:
            self.card_layout.addItem(self._blank_card_item())
        while self.card_layout.count() > total:
            last = self.card_layout.item(self.card_layout.count()-1)
            if last.data(Qt.UserRole) is not None:
                break
            self.card_layout.takeItem(self.card_layout.count()-1)

    def rebuild_card_grid(self, *_):
        if hasattr(self, "card_layout"):
            if hasattr(self,"card_auto_grid") and self.card_auto_grid.isChecked():
                self.auto_fit_card_dimensions()
            else:
                self.ensure_card_slots(); QTimer.singleShot(0, self.update_adaptive_card_grid)

    def update_adaptive_card_grid(self):
        """稳定地按当前控件外框和行/列数计算格子尺寸。

        这里故意不使用 viewport() 的宽高：viewport 会因滚动条状态改变而变化，
        如果再用它反算 gridSize，会产生反馈循环并表现为闪烁。
        """
        if not hasattr(self, "card_layout"):
            return
        cols=max(1, self.card_cols.value()); rows=max(1, self.card_rows.value())
        outer=self.card_layout.size()
        if outer.width() < 80 or outer.height() < 80:
            return

        # 始终预留固定的滚动条/边框余量，这样滚动条出现或消失都不会改变计算结果。
        avail_w=max(80, outer.width()-34)
        avail_h=max(80, outer.height()-30)
        w=max(96, int(avail_w / cols) - 8)
        h=max(86, int(avail_h / rows) - 8)
        size=QSize(w,h)
        icon=QSize(max(68,w-22), max(48,h-38))
        metrics=(cols,rows,w,h,icon.width(),icon.height())
        if getattr(self, "_last_card_grid_metrics", None) == metrics:
            return
        self._last_card_grid_metrics=metrics

        # 一次性提交尺寸变化，避免用户看到 item 逐个变化。
        self.card_layout.setUpdatesEnabled(False)
        self.card_layout.blockSignals(True)
        try:
            self.card_layout.setGridSize(size)
            self.card_layout.setIconSize(icon)
            for i in range(self.card_layout.count()):
                self.card_layout.item(i).setSizeHint(size)
            self.card_layout.doItemsLayout()
        finally:
            self.card_layout.blockSignals(False)
            self.card_layout.setUpdatesEnabled(True)
        self.card_layout.viewport().update()

    def apply_card_grid_size(self, *_):
        # 兼容旧调用；v0.6.9 起改为自适应格子。
        self.update_adaptive_card_grid()

    def insert_card_blank(self):
        row = self.card_layout.currentRow()
        if row >= 0:
            self._replace_card_slot(row, self._blank_card_item())

    def remove_selected_from_card(self):
        rows=sorted((self.card_layout.row(item) for item in self.card_layout.selectedItems()), reverse=True)
        if not rows:
            return
        self.card_layout.setUpdatesEnabled(False)
        try:
            for row in rows:
                self._replace_card_slot(row, self._blank_card_item())
        finally:
            self.card_layout.setUpdatesEnabled(True)
        if hasattr(self,"card_auto_grid") and self.card_auto_grid.isChecked():
            # 自动模式删除后不立即缩小网格，避免画面跳动；只保留空位。
            self._schedule_card_auto_reflow()
        self.card_layout.viewport().update()

    def sort_color_card(self, mode: str):
        """当前布局独立排序。与色库排序状态完全分离；色相固定采用 Munsell。"""
        current_keys = [self.card_layout.item(i).data(Qt.UserRole) for i in range(self.card_layout.count())]
        need_full=not (self.illuminant=='D65' and int(self.observer)==10)
        samples={sample_key(x):x for x in self._samples_by_keys_on_demand(current_keys,full=need_full)}
        selected = [samples[k] for k in current_keys if k in samples]

        if mode == "manual":
            self.card_sort_key, self.card_sort_desc = "manual", False
            card = self._current_card()
            saved = [slot.get("sample_key") for slot in (card or {}).get("layout", []) if slot.get("sample_key")]
            pos = {k:i for i,k in enumerate(saved)}
            selected.sort(key=lambda x:(pos.get(sample_key(x), 10**9), current_keys.index(sample_key(x)) if sample_key(x) in current_keys else 10**9))
        else:
            if self.card_sort_key == mode:
                self.card_sort_desc = not self.card_sort_desc
            else:
                self.card_sort_key = mode
                self.card_sort_desc = False if mode in {"name", "h"} else True

            if mode == "name":
                selected.sort(key=lambda x:x.display_name.casefold(), reverse=self.card_sort_desc)
            elif mode in {"L","a","b"}:
                idx={"L":0,"a":1,"b":2}[mode]
                selected.sort(key=lambda x:(self.sample_lab(x)[idx], x.display_name.casefold()), reverse=self.card_sort_desc)
            elif mode == "C":
                selected.sort(key=lambda x:(math.hypot(self.sample_lab(x)[1], self.sample_lab(x)[2]), x.display_name.casefold()), reverse=self.card_sort_desc)
            elif mode == "h":
                selected = self._sort_samples_by_perceptual_hue(selected, reverse=self.card_sort_desc)

        # 复用现有 QListWidgetItem，不 clear()/重建图标，减少闪烁和卡顿。
        capacity = max(self.card_rows.value() * self.card_cols.value(), len(selected))
        existing=[]
        while self.card_layout.count():
            existing.append(self.card_layout.takeItem(0))
        by_key={it.data(Qt.UserRole):it for it in existing if it.data(Qt.UserRole)}
        blanks=[it for it in existing if not it.data(Qt.UserRole)]
        self.card_layout.setUpdatesEnabled(False); self.card_layout.blockSignals(True)
        try:
            for sample in selected:
                key=sample_key(sample); item=by_key.get(key)
                if item is None:
                    item=QListWidgetItem(self._sample_icon(sample), sample.display_name); item.setData(Qt.UserRole,key)
                L,a,b=self.sample_lab(sample); C=math.hypot(a,b)
                hue_text=self._perceptual_hue_label(sample)
                item.setToolTip(f"{sample.display_name}\nL* {L:.2f}   a* {a:.2f}   b* {b:.2f}   C* {C:.2f}\n{hue_text}")
                self.card_layout.addItem(item)
            need=capacity-len(selected)
            for i in range(need):
                self.card_layout.addItem(blanks[i] if i < len(blanks) else self._blank_card_item())
            self.ensure_card_slots()
        finally:
            self.card_layout.blockSignals(False); self.card_layout.setUpdatesEnabled(True); self.card_layout.viewport().update()

        names={"manual":"原始","name":"名称","L":"L*","a":"a*","b":"b*","C":"C*","h":"h"}
        for key,btn in self._card_sort_buttons.items():
            base=names[key]
            if key==self.card_sort_key and key!="manual":
                btn.setText(base + ((" ←" if self.card_sort_desc else " →") if key=="h" else (" ↓" if self.card_sort_desc else " ↑")))
            else:
                btn.setText(base)
        if mode=="h":
            self.statusBar().showMessage(f"当前布局已按 Munsell Hue（C/2°）{'逆序' if self.card_sort_desc else '正序'}排列；近中性色按 L* 亮→暗",3500)
        elif mode=="manual":
            self.statusBar().showMessage("当前布局已恢复保存/原始顺序",2500)
        else:
            self.statusBar().showMessage(f"当前布局已按 {names[mode]} {'最大→最小' if self.card_sort_desc else '最小→最大'} 排列",3000)

    def save_current_color_card(self):
        card = self._current_card()
        if not card:
            QMessageBox.information(self, "保存色卡", "请先新建一个色卡方案。")
            return
        card["layout"] = [{"sample_key": self.card_layout.item(i).data(Qt.UserRole)}
                          for i in range(self.card_layout.count())]
        card["columns_count"] = self.card_cols.value()
        card["settings"] = {"illuminant": self.illuminant, "observer": self.observer,
                            "grid_cols": self.card_cols.value(), "grid_rows": self.card_rows.value(),
                            "auto_grid": bool(self.card_auto_grid.isChecked()) if hasattr(self,"card_auto_grid") else False,
                            "cell_w": self.card_layout.gridSize().width(), "cell_h": self.card_layout.gridSize().height()}
        try:self.store.save_color_card(card)
        except Exception as exc:
            QMessageBox.information(self,"保存色卡",str(exc)); return
        self.refresh_color_card_list()
        self.statusBar().showMessage("色卡布局已保存", 4000)

    def delete_current_color_card(self):
        card = self._current_card()
        if not card:
            return
        if QMessageBox.question(self, "删除色卡", f"确定删除色卡方案【{card['name']}】吗？\n不会删除色库中的色样。",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:self.store.remove_color_card(card["card_id"])
        except Exception as exc:
            QMessageBox.information(self,"删除色卡",str(exc)); return
        self.active_color_card_id = None
        self.refresh_color_card_list()

    def export_current_color_card(self):
        if not self.can_use("export"):return
        card = self._current_card()
        if not card:
            QMessageBox.information(self, "导出色卡", "请先选择色卡方案。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出客户色卡", f"{card['name']}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        keys=[self.card_layout.item(i).data(Qt.UserRole) for i in range(self.card_layout.count())]
        by_key={sample_key(x):x for x in self._samples_by_keys_on_demand(keys,full=True)}
        ordered = [by_key.get(key) for key in keys]
        export_client_card(path, card["name"], ordered, card.get("columns_count", 10))
        self.statusBar().showMessage(f"客户色卡已导出：{path}", 6000)
        QMessageBox.information(self,'导出 Excel',f'Excel 导出成功。\n\n文件：{Path(path).name}\n保存位置：{Path(path).parent}')

    # ============ 比色工作台页 ============
    def build_workbench_page(self):
        page = ResponsiveWorkbenchPage()
        body = QWidget()
        body.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.workbench_body_layout=lay

        # HF136: use a real single-row command bar instead of a height-for-width
        # FlowLayout here.  The old flow container could report a one-control
        # minimum height to QScrollArea on first layout, leaving only the top edge
        # of the buttons visible.  Low-frequency controls move into “更多” when
        # narrow; no command or handler is removed.
        top_wrap=QFrame(); top_wrap.setObjectName('workbenchTopBar'); top_wrap.setMinimumHeight(48)
        top_row=QHBoxLayout(top_wrap); top_row.setContentsMargins(6,5,6,5); top_row.setSpacing(6)
        new_btn=QPushButton("＋ 新建工作台"); new_btn.setObjectName('primaryButton'); new_btn.clicked.connect(lambda _checked=False:self.create_workbench()); top_row.addWidget(new_btn)
        collapsed_btn=QPushButton("已收起工作台"); collapsed_btn.clicked.connect(self.show_collapsed_workbenches); top_row.addWidget(collapsed_btn)
        top_row.addStretch(1)
        formula_group=QFrame(); formula_group.setObjectName('inlineField'); fg=QHBoxLayout(formula_group); fg.setContentsMargins(0,0,0,0); fg.setSpacing(5)
        fgl=QLabel("判定公式"); fgl.setObjectName('inlineFieldLabel'); fg.addWidget(fgl)
        self.formula_box=QComboBox(); self.formula_box.addItems(["CIEDE2000","CIE94","ΔE*ab","CMC(2:1)"]); self.formula_box.setCurrentText("CIEDE2000"); self.formula_box.currentTextChanged.connect(self.on_formula_changed); fg.addWidget(self.formula_box); top_row.addWidget(formula_group)
        self.wb_lights_btn=QPushButton('光源：D65 · 10° ▾'); self.wb_lights_btn.setToolTip('打开分组光源选择器；支持搜索、多选光源以及 10°/2°观察者。MI 仍以 D65 为参考计算。'); self.wb_lights_btn.clicked.connect(self.show_workbench_illuminant_menu); top_row.addWidget(self.wb_lights_btn)
        self.wb_column_preset_btn=QPushButton('视图：自定义 ▾'); preset_menu=QMenu(self.wb_column_preset_btn)
        for pid in ('basic','chromaticity','difference','shade555','full'):
            preset_menu.addAction(WORKBENCH_VIEW_PRESETS[pid]['label'],lambda _=False,p=pid:self.set_workbench_column_preset(p))
        preset_menu.addSeparator(); preset_menu.addAction('自定义列…',self.open_active_workbench_column_settings)
        self.wb_column_preset_btn.setMenu(preset_menu); self.wb_column_preset_btn.setToolTip('按任务切换屏幕列组合；不改变数据和导出。'); top_row.addWidget(self.wb_column_preset_btn)
        self.wb_table_view_btn=QPushButton('密度：适应窗口 ▾'); table_menu=QMenu(self.wb_table_view_btn)
        table_menu.addAction('适应窗口',lambda:self.set_workbench_table_view('fit')); table_menu.addAction('标准宽度',lambda:self.set_workbench_table_view('standard')); table_menu.addAction('紧凑显示',lambda:self.set_workbench_table_view('compact'))
        self.wb_table_view_btn.setMenu(table_menu); self.wb_table_view_btn.setToolTip('只调整列宽密度，不改变数据和导出内容。'); top_row.addWidget(self.wb_table_view_btn)
        workspace_more=QToolButton(); workspace_more.setText('更多 ▾'); workspace_more.setPopupMode(QToolButton.InstantPopup)
        wmenu=QMenu(workspace_more); wmenu.addAction('已收起工作台…',self.show_collapsed_workbenches)
        wview=wmenu.addMenu('表格视图')
        for pid in ('basic','chromaticity','difference','shade555','full'):
            wview.addAction(WORKBENCH_VIEW_PRESETS[pid]['label'],lambda _=False,p=pid:self.set_workbench_column_preset(p))
        wview.addSeparator(); wview.addAction('自定义列…',self.open_active_workbench_column_settings)
        wdensity=wmenu.addMenu('列宽密度'); wdensity.addAction('适应窗口',lambda:self.set_workbench_table_view('fit')); wdensity.addAction('标准宽度',lambda:self.set_workbench_table_view('standard')); wdensity.addAction('紧凑显示',lambda:self.set_workbench_table_view('compact'))
        workspace_more.setMenu(wmenu); workspace_more.hide(); top_row.addWidget(workspace_more)
        lay.addWidget(top_wrap)

        def update_top_bar(width):
            width=int(width or page.width())
            collapsed_btn.setVisible(width>=1180)
            self.wb_table_view_btn.setVisible(width>=1060)
            self.wb_column_preset_btn.setVisible(width>=930)
            workspace_more.setVisible(width<1180)
        page.resized.connect(update_top_bar)
        QTimer.singleShot(0,lambda:update_top_bar(page.width()))

        self.workbench_tabs = QTabWidget()
        self.workbench_tabs.setTabsClosable(True)
        self.workbench_tabs.setMovable(True)
        self.workbench_tabs.tabCloseRequested.connect(self.close_workbench_tab)
        self.workbench_tabs.currentChanged.connect(self.on_workbench_tab_changed)
        self.workbench_tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.workbench_tabs.tabBar().customContextMenuRequested.connect(self.workbench_tab_menu)
        lay.addWidget(self.workbench_tabs, 1)

        scroll=QScrollArea(page);scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True);scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);scroll.setWidget(body)
        outer=QVBoxLayout(page);outer.setContentsMargins(0,0,0,0);outer.addWidget(scroll)
        return page

    def workbench_tab_menu(self,pos):
        bar=self.workbench_tabs.tabBar(); index=bar.tabAt(pos)
        if index<0:return
        widget=self.workbench_tabs.widget(index); wb_id=widget.property('workbench_id') if widget else None; wb=self._find_workbench(wb_id) if wb_id else None
        if not wb:return
        menu=QMenu(self); rename=menu.addAction('重命名工作台…'); delete=menu.addAction('删除工作台…')
        chosen=menu.exec(bar.mapToGlobal(pos))
        if chosen==rename:
            name,ok=QInputDialog.getText(self,'重命名工作台','工作台名称：',text=wb['name'])
            if ok and name.strip():wb['name']=name.strip(); self.store.save_workbench(wb); self.refresh_workbench_tabs()
        elif chosen==delete:
            self.workbench_tabs.setCurrentIndex(index); self.close_workbench_tab(index)

    def show_workbench_illuminant_menu(self):
        wb=self._find_workbench(self.active_workbench_id) if self.active_workbench_id else None
        if wb is None:
            QMessageBox.information(self,'光源 / 观察者','请先新建或打开一个工作台。'); return
        current=list(dict.fromkeys(display_illuminant(x) for x in (wb.get('illuminants') or ['D65'])))
        dlg=WorkbenchIlluminantDialog(current,int(wb.get('observer',10)),self)
        if dlg.exec()!=QDialog.Accepted:return
        wb['illuminants']=dlg.selected_illuminants(); wb['observer']=dlg.selected_observer()
        self.store.save_workbench(wb); self._refresh_one_workbench(wb['workbench_id'])
        self._update_workbench_toolbar_labels(wb)
        self.statusBar().showMessage('工作台显示条件：'+' / '.join(wb['illuminants'])+f" · {wb['observer']}°",3500)

    def _update_workbench_toolbar_labels(self, wb=None):
        if wb is None and self.active_workbench_id: wb=self._find_workbench(self.active_workbench_id)
        if hasattr(self,'wb_lights_btn'):
            if wb:
                lights=list(dict.fromkeys(display_illuminant(x) for x in (wb.get('illuminants') or ['D65'])))
                light_text=lights[0] if len(lights)==1 else f'{len(lights)}项'
                self.wb_lights_btn.setText(f"光源：{light_text} · {int(wb.get('observer',10))}° ▾")
            else:self.wb_lights_btn.setText('光源 / 观察者 ▾')

    def set_workbench_table_view(self, mode):
        widget=self._find_workbench_widget(self.active_workbench_id) if self.active_workbench_id else None
        table=getattr(widget,'workbench_table',None) if widget is not None else None
        if table is None:return
        table.set_view_mode(mode)
        labels={'fit':'密度：适应窗口 ▾','standard':'密度：标准宽度 ▾','compact':'密度：紧凑显示 ▾'}
        if hasattr(self,'wb_table_view_btn'):self.wb_table_view_btn.setText(labels.get(mode,'密度设置 ▾'))


    def open_active_workbench_column_settings(self):
        widget=self._find_workbench_widget(self.active_workbench_id) if self.active_workbench_id else None
        table=getattr(widget,'workbench_table',None) if widget is not None else None
        if table is None:return
        self.open_column_settings(self.active_workbench_id,table)

    def set_workbench_column_preset(self, preset_id):
        widget=self._find_workbench_widget(self.active_workbench_id) if self.active_workbench_id else None
        table=getattr(widget,'workbench_table',None) if widget is not None else None
        if table is None:return
        model=table.model(); cfg=WORKBENCH_VIEW_PRESETS.get(str(preset_id))
        if not cfg or model is None:return
        visible=cfg.get('keys')
        for col in range(model.columnCount()):
            key=model.KEYS[col] if hasattr(model,'KEYS') else None
            # Swatch and name are permanent identity columns.
            hide=False if col in (0,1) else (False if visible is None else bool(key) and key not in visible)
            table.setColumnHidden(col,hide)
        wb=self._find_workbench(self.active_workbench_id) if self.active_workbench_id else None
        if wb is not None:
            wb['column_preset']=str(preset_id); self._save_hidden_columns(table,wb['workbench_id'])
        table.apply_view_mode() if hasattr(table,'apply_view_mode') else None
        if hasattr(self,'wb_column_preset_btn'):self.wb_column_preset_btn.setText(f"视图：{cfg.get('label','自定义')} ▾")

    def _sync_workbench_preset_label(self, wb=None):
        if wb is None and self.active_workbench_id:wb=self._find_workbench(self.active_workbench_id)
        preset=str((wb or {}).get('column_preset') or 'custom')
        label=WORKBENCH_VIEW_PRESETS.get(preset,{}).get('label','自定义')
        if hasattr(self,'wb_column_preset_btn'):self.wb_column_preset_btn.setText(f'视图：{label} ▾')

    def on_formula_changed(self, text: str):
        fmap = {
            "CIEDE2000": "delta_e00",
            "CIE94": "delta_e94",
            "ΔE*ab": "delta_e76",
            "CMC(2:1)": "cmc21",
        }
        self.active_formula = fmap.get(text, "delta_e00")
        self.refresh_all_workbenches()

    @profiled('workbench.refresh_tabs')
    def refresh_workbench_tabs(self):
        """Reconcile tabs instead of destroying/recreating them on every visit.

        Rebuilding comparison widgets used to discard plot caches, leak detached pages
        and immediately recalculate every workbench.  Keep live widgets when the set of
        visible workbenches is unchanged; when structure changes, build lightweight
        shells and calculate only the active tab.
        """
        current_id = self.active_workbench_id
        visible = [wb for wb in self.workbenches if not wb.get('is_collapsed')]
        wanted_ids=[wb['workbench_id'] for wb in visible]
        existing_ids=[]
        for i in range(self.workbench_tabs.count()):
            w=self.workbench_tabs.widget(i)
            wid=w.property('workbench_id') if w is not None else None
            if wid: existing_ids.append(wid)

        if existing_ids == wanted_ids and wanted_ids:
            # Preserve all table/plot widgets and their caches. Only sync tab names.
            for i,wb in enumerate(visible):
                if self.workbench_tabs.tabText(i)!=wb['name']:
                    self.workbench_tabs.setTabText(i,wb['name'])
            target=current_id if current_id in wanted_ids else wanted_ids[0]
            idx=wanted_ids.index(target)
            if self.workbench_tabs.currentIndex()!=idx:
                self.workbench_tabs.setCurrentIndex(idx)
            self._refresh_one_workbench(target, force=False)
            return

        self.workbench_tabs.blockSignals(True)
        try:
            while self.workbench_tabs.count() > 0:
                w=self.workbench_tabs.widget(0)
                self.workbench_tabs.removeTab(0)
                if w is not None:
                    w.setParent(None); w.deleteLater()
            if not visible:
                empty=QLabel('还没有工作台\n\n点左上角【＋ 新建工作台】开始')
                empty.setAlignment(Qt.AlignCenter); empty.setObjectName('empty')
                self.workbench_tabs.addTab(empty,'（空）')
                self.workbench_tabs.tabBar().setTabButton(0,QTabBar.ButtonPosition.RightSide,None)
                return
            for wb in visible:
                self.add_workbench_tab(wb, refresh=False)
            target=current_id if current_id in wanted_ids else wanted_ids[0]
            idx=wanted_ids.index(target); self.workbench_tabs.setCurrentIndex(idx)
            self.active_workbench_id=target
        finally:
            self.workbench_tabs.blockSignals(False)
        self._refresh_one_workbench(self.active_workbench_id, force=True)

    def add_workbench_tab(self, wb: dict, refresh: bool = True):
        content=self.build_workbench_content(wb)
        content.setProperty('workbench_id',wb['workbench_id'])
        idx=self.workbench_tabs.addTab(content,wb['name'])
        self.workbench_tabs.setTabToolTip(idx,f"工作台：{wb['name']}")
        if refresh:
            self._refresh_one_workbench(wb['workbench_id'], force=True)

    def build_workbench_content(self, wb: dict) -> QWidget:
        page=ResponsiveWorkbenchPage()
        lay=QVBoxLayout(page); lay.setContentsMargins(8,8,8,8); lay.setSpacing(8)

        # Standard summary + responsive command bar.  Buttons never force the MDI
        # wider than the screen: lower-frequency actions move into “更多” while the
        # exact original handlers remain available.
        std_card=QFrame(); std_card.setObjectName('standardCard')
        card_lay=QVBoxLayout(std_card); card_lay.setContentsMargins(10,8,10,8); card_lay.setSpacing(7)
        summary=QHBoxLayout(); summary.setSpacing(7)
        swatch=QFrame(); swatch.setFixedSize(72,56); summary.addWidget(swatch)
        texts=QVBoxLayout(); texts.setSpacing(2)
        name_lbl=QLabel('等待导入第一个色样'); name_lbl.setObjectName('standardName'); name_lbl.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)
        val_lbl=QLabel('导入后的第一个真实色样将作为默认标准'); val_lbl.setObjectName('standardValues'); val_lbl.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)
        texts.addWidget(name_lbl); texts.addWidget(val_lbl); summary.addLayout(texts,1)
        add_btn=QPushButton('＋ 添加色样'); add_btn.setObjectName('primaryButton'); add_btn.clicked.connect(lambda:self.add_samples_to_workbench(wb['workbench_id'])); summary.addWidget(add_btn)
        std_btn=QPushButton('设为标准'); std_btn.setToolTip('先在下方数据表中选中一个色样，再点击这里把该色样设为标准。'); std_btn.clicked.connect(lambda:self.set_workbench_standard(wb['workbench_id'])); summary.addWidget(std_btn)
        avg_btn=QPushButton('平均标准'); avg_btn.setToolTip('选择参与平均的色样，生成光谱平均标准。'); avg_btn.clicked.connect(lambda:self.set_workbench_average_standard(wb['workbench_id'])); summary.addWidget(avg_btn)
        rm_btn=QPushButton('移除选中'); rm_btn.setToolTip('从当前工作台移除表格中选中的色样；不修改原 QTX 或正式色库。'); rm_btn.clicked.connect(lambda:self.remove_workbench_samples(wb['workbench_id'])); summary.addWidget(rm_btn)
        more_btn=QToolButton(); more_btn.setText('更多 ▾'); more_btn.setPopupMode(QToolButton.InstantPopup); summary.addWidget(more_btn)
        card_lay.addLayout(summary)

        action_row=QHBoxLayout(); action_row.setSpacing(6)
        sort_wrap=QFrame(); sort_wrap.setObjectName('workbenchSortSegments'); sort_lay=QHBoxLayout(sort_wrap); sort_lay.setContentsMargins(0,0,0,0); sort_lay.setSpacing(0)
        page._sort_state={'key':'original','desc':False}; page._sort_buttons={}
        for text,key in [('原始','original'),('色差','de'),('L*','L'),('a*','a'),('b*','b'),('C*','C'),('h°','h')]:
            btn=QPushButton(text); btn.setObjectName('segmentButton'); btn.setCheckable(True); btn.setToolTip('重复点击切换方向。色差第一次：最近→最远；L/a/b/C/h 第一次：最大→最小。')
            btn.setChecked(key=='original')
            btn.clicked.connect(lambda checked=False,wid=wb['workbench_id'],k=key:self.toggle_workbench_sort(wid,k)); sort_lay.addWidget(btn); page._sort_buttons[key]=btn
        action_row.addWidget(sort_wrap)
        sort_menu_btn=QToolButton(); sort_menu_btn.setText('排序 ▾'); sort_menu_btn.setPopupMode(QToolButton.InstantPopup)
        sort_menu=QMenu(sort_menu_btn)
        for text,key in [('原始顺序','original'),('按色差','de'),('按 L*','L'),('按 a*','a'),('按 b*','b'),('按 C*','C'),('按 h°','h')]:
            sort_menu.addAction(text,lambda _=False,wid=wb['workbench_id'],k=key:self.toggle_workbench_sort(wid,k))
        sort_menu_btn.setMenu(sort_menu); action_row.addWidget(sort_menu_btn)
        action_row.addStretch(1)

        card_btn=QPushButton('色卡编排'); card_btn.setToolTip('把当前工作台色样直接生成一个色卡方案，不需要重新导入。'); card_btn.clicked.connect(lambda:self.workbench_to_color_card(wb['workbench_id'])); action_row.addWidget(card_btn)
        save_lib_btn=None
        if self.is_admin:
            save_lib_btn=QPushButton('发布到正式色库'); save_lib_btn.setToolTip('管理者：把当前比色结果作为组织正式业务数据发布到客户色库。'); save_lib_btn.clicked.connect(lambda:self.save_workbench_to_library(wb['workbench_id'])); action_row.addWidget(save_lib_btn)
        export_btn=QToolButton(); export_btn.setText('导出 ▾'); export_btn.setPopupMode(QToolButton.InstantPopup); export_btn.setToolTip('Excel：按当前可见列导出；QTX：保留原始测量信息。')
        export_menu=QMenu(export_btn); export_menu.addAction('Excel…',lambda wid=wb['workbench_id']:self.export_workbench_excel(wid)); export_menu.addAction('QTX…',lambda wid=wb['workbench_id']:self.export_workbench_qtx(wid)); export_btn.setMenu(export_menu); action_row.addWidget(export_btn)
        collapse_btn=QPushButton('收起'); collapse_btn.clicked.connect(lambda:self.collapse_workbench(wb['workbench_id'])); action_row.addWidget(collapse_btn)
        card_lay.addLayout(action_row); lay.addWidget(std_card)

        # Mirror hidden commands in one compact menu.  This is only another route
        # to the same handlers, so no operation disappears on a 1366×768 display.
        more_menu=QMenu(more_btn)
        more_menu.addAction('设为平均标准',lambda:self.set_workbench_average_standard(wb['workbench_id']))
        more_menu.addAction('生成色卡编排',lambda:self.workbench_to_color_card(wb['workbench_id']))
        if self.is_admin:more_menu.addAction('发布到正式色库',lambda:self.save_workbench_to_library(wb['workbench_id']))
        ex_more=more_menu.addMenu('导出'); ex_more.addAction('Excel…',lambda:self.export_workbench_excel(wb['workbench_id'])); ex_more.addAction('QTX…',lambda:self.export_workbench_qtx(wb['workbench_id']))
        more_menu.addSeparator(); more_menu.addAction('移除选中',lambda:self.remove_workbench_samples(wb['workbench_id'])); more_menu.addAction('收起当前工作台',lambda:self.collapse_workbench(wb['workbench_id']))
        more_btn.setMenu(more_menu)

        def update_actions(width):
            width=int(width or page.width()); compact=width<1180; narrow=width<920
            sort_wrap.setVisible(not compact); sort_menu_btn.setVisible(compact)
            avg_btn.setVisible(width>=980)
            rm_btn.setVisible(width>=1160)
            card_btn.setVisible(width>=1180); collapse_btn.setVisible(width>=1420)
            if save_lib_btn is not None:save_lib_btn.setVisible(width>=1320)
            more_btn.setVisible(width<1420 or not rm_btn.isVisible() or not avg_btn.isVisible())
            # Export is intentionally always reachable as a first-class action.
            std_card.updateGeometry()
        page.resized.connect(update_actions); page._responsive_action_updater=update_actions

        import_zone=WorkbenchQtxDropZone(); import_zone.filesDropped.connect(lambda paths,wid=wb['workbench_id']:self.import_paths_to_workbench(wid,paths)); import_zone.sampleKeysDropped.connect(lambda keys,wid=wb['workbench_id']:self.drop_sample_keys_to_workbench(wid,keys)); import_zone.clicked.connect(lambda wid=wb['workbench_id']:self.import_qtx_to_workbench(wid)); lay.addWidget(import_zone)
        page.workbench_action_card=std_card; page.workbench_import_zone=import_zone
        if bool(getattr(self,'_workspace_compact_mode',False)):import_zone.set_compact_mode(True)

        model=WorkbenchTableModel(wb['workbench_id'],self); table=WorkbenchTableView(self,wb['workbench_id']); table.setModel(model)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn); table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setDragEnabled(True); table.setAcceptDrops(True); table.setDropIndicatorShown(True); table.setDragDropMode(QAbstractItemView.DragDrop); table.setDefaultDropAction(Qt.MoveAction)
        table.setContextMenuPolicy(Qt.CustomContextMenu); table.customContextMenuRequested.connect(lambda pos,t=table,wid=wb['workbench_id']:self.show_workbench_row_menu(wid,t,pos)); table.setAlternatingRowColors(True)
        table.setItemDelegateForColumn(0,SwatchDelegate(table)); table.setItemDelegateForColumn(1,WorkbenchNameDelegate(table)); table.verticalHeader().setDefaultSectionSize(56); table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed); table.setColumnWidth(0,72); table.setColumnWidth(1,190)
        table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu); table.horizontalHeader().customContextMenuRequested.connect(lambda pos,t=table:self.show_workbench_column_menu(t,pos))

        analysis_tabs=QTabWidget(); analysis_tabs.setMinimumHeight(240); analysis_tabs.addTab(table,'数据表')
        pair_view=InteractiveAnalysisWidget('compare',self,wb['workbench_id']); spectrum=InteractiveAnalysisWidget('spectrum',self,wb['workbench_id']); delta_r=InteractiveAnalysisWidget('delta_r',self,wb['workbench_id']); delta=InteractiveAnalysisWidget('delta',self,wb['workbench_id'])
        def analysis_panel(widget):
            frame=QFrame(); frame.setObjectName('analysisPanel'); frame.setStyleSheet('QFrame#analysisPanel{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:9px;}'); vl=QVBoxLayout(frame); vl.setContentsMargins(2,2,2,2); vl.setSpacing(0); vl.addWidget(widget); return frame
        dash=ResponsiveAnalysisDashboard([analysis_panel(pair_view),analysis_panel(spectrum),analysis_panel(delta_r),analysis_panel(delta)])
        dashboard_scroll=QScrollArea(); dashboard_scroll.setFrameShape(QFrame.NoFrame); dashboard_scroll.setWidgetResizable(True); dashboard_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); dashboard_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); dashboard_scroll.setWidget(dash)
        analysis_tabs.addTab(dashboard_scroll,'综合分析'); lay.addWidget(analysis_tabs,1)

        page.workbench_analysis=[pair_view,spectrum,delta_r,delta]; page.workbench_analysis_tabs=analysis_tabs; page.workbench_table=table; page.workbench_model=model; page.workbench_swatch=swatch; page.workbench_name_lbl=name_lbl; page.workbench_val_lbl=val_lbl
        self._apply_saved_columns(table,wb['workbench_id'])
        table.selectionModel().currentChanged.connect(lambda *_args,wid=wb['workbench_id'],t=table:self._on_workbench_table_focus_changed(wid,t)); table.selectionModel().selectionChanged.connect(lambda *_args,wid=wb['workbench_id'],t=table:self._on_workbench_table_focus_changed(wid,t))
        QTimer.singleShot(0,lambda t=table:t.set_view_mode('fit')); QTimer.singleShot(0,lambda:update_actions(page.width()))
        return page

    def _on_workbench_table_focus_changed(self, wb_id, table):
        """Use the table's current row as the sole analysis target.

        Ctrl/Shift selection can contain many rows for batch commands, but charts
        follow the last/current clicked row only. Selecting the standard row or
        clicking whitespace intentionally clears the current analysis sample.
        """
        model=table.model(); sel=table.selectionModel()
        if model is None or sel is None:return
        key=None; idx=table.currentIndex()
        if idx.isValid():
            row=idx.row()
            if hasattr(model,'_keys') and 0<=row<len(model._keys):
                candidate=model._keys[row]
                wb=self._find_workbench(wb_id); std_key=(wb or {}).get('standard_key')
                if candidate and candidate!='__AVERAGE__' and candidate!=std_key:key=candidate
        if not hasattr(self,'_workbench_focus_keys'):self._workbench_focus_keys={}
        if key:self._workbench_focus_keys[wb_id]=key
        else:self._workbench_focus_keys.pop(wb_id,None)
        widget=self._find_workbench_widget(wb_id)
        if widget is not None and hasattr(widget,'workbench_analysis'):
            for chart in widget.workbench_analysis:chart.update()


    def _find_workbench(self, wb_id: str) -> dict | None:
        return next((w for w in self.workbenches if w["workbench_id"] == wb_id), None)

    def _find_workbench_widget(self, wb_id: str):
        for i in range(self.workbench_tabs.count()):
            widget = self.workbench_tabs.widget(i)
            if widget.property("workbench_id") == wb_id:
                return widget
        return None

    def _workbench_refresh_signature(self, wb: dict):
        data=wb.get('samples_data') or []
        sample_sig=tuple((d.get('source_file'),d.get('sample_id'),d.get('display_name'),
                          tuple(d.get('xyz_d65_10') or ()), tuple(d.get('lab_d65_10') or ()),
                          hash(tuple(d.get('reflectance') or ())), hash(tuple(d.get('wavelengths') or ())))
                         for d in data)
        shade=wb.get('shade555') or {}
        try: shade_sig=json.dumps(shade,sort_keys=True,ensure_ascii=False)
        except Exception: shade_sig=str(shade)
        return (sample_sig,wb.get('standard_key'),tuple(wb.get('illuminants') or ['D65']),
                int(wb.get('observer',10)),self.active_formula,
                float(self.thresholds.get(self.active_formula,2.0)),shade_sig)

    @profiled('workbench.refresh_one')
    def _refresh_one_workbench(self, wb_id: str, force: bool = True):
        wb=self._find_workbench(wb_id)
        if not wb:return
        widget=self._find_workbench_widget(wb_id)
        if not widget:return
        signature=self._workbench_refresh_signature(wb)
        if not force and getattr(widget,'_last_refresh_signature',None)==signature:
            return
        if self._workbench_refreshing:
            return
        self._workbench_refreshing=True
        widget.setUpdatesEnabled(False)
        try:
            if hasattr(widget,'workbench_model'):
                widget.workbench_model.reload(wb)
            if hasattr(widget,'workbench_name_lbl'):
                std_sample=None
                if wb.get('standard_key')=='__AVERAGE__' and wb.get('average_standard'):
                    try:std_sample=self._deserialize_sample(wb['average_standard'])
                    except Exception:std_sample=None
                elif wb.get('standard_key'):
                    std_sample=next((s for s in self._workbench_samples(wb) if sample_key(s)==wb['standard_key']),None)
                if std_sample:
                    illum=(list(dict.fromkeys(wb.get('illuminants') or ['D65'])) or ['D65'])[0]
                    obs=int(wb.get('observer',10))
                    try:
                        _,lab=self._workbench_xyz_lab(std_sample,illum,obs)
                    except Exception:
                        lab=std_sample.lab_d65_10
                    widget.workbench_name_lbl.setText(f"当前标准 · {std_sample.display_name}")
                    widget.workbench_val_lbl.setText(f"L* {lab[0]:.2f}   a* {lab[1]:.2f}   b* {lab[2]:.2f}    ·    {illum} / {obs}°")
                    widget.workbench_swatch.setStyleSheet(
                        f"background:{sample_display_qcolor(std_sample,lab).name()};border:1px solid #CBD5E1;border-radius:8px;")
                else:
                    widget.workbench_name_lbl.setText('尚未设置标准')
                    widget.workbench_val_lbl.setText('可在表格中选择一个色样设为标准，或使用平均标准')
                    widget.workbench_swatch.setStyleSheet('background:#FFFFFF;border:1px solid #DDD;')
            widget._last_refresh_signature=signature
        finally:
            widget.setUpdatesEnabled(True); widget.update()
            self._workbench_refreshing=False
        if hasattr(widget,'workbench_analysis'):
            for x in widget.workbench_analysis:x.update()

    def recompute_workbench_average(self, wb, save=True, force=False):
        """Repair the default reference without ever creating an average.

        v0.11.1 uses the first real sample as the default standard.  An average
        exists only after the user explicitly chooses ``设为平均标准``.
        """
        current_key = wb.get('standard_key')
        samples=self._workbench_samples(wb)
        if not samples:
            wb['standard_key']=None; wb['average_standard']=None
            wb['average_explicit']=False
            if save: self.store.save_workbench(wb)
            return False
        if current_key=='__AVERAGE__' and wb.get('average_explicit') and wb.get('average_standard') and not force:
            return False
        keys={sample_key(x) for x in samples}
        if current_key in keys and not force:
            return False
        wb['standard_key']=sample_key(samples[0]); wb['average_standard']=None; wb['average_explicit']=False
        if save: self.store.save_workbench(wb)
        return True

    def refresh_all_workbenches(self):
        """Refresh only the visible/active comparison tab.

        Hidden tabs are recalculated lazily when selected. This keeps navigation
        responsive when users keep several large workbenches open.
        """
        wb_id=self.active_workbench_id
        if wb_id:
            self._refresh_one_workbench(wb_id, force=True)

    def create_workbench(self, name: str | None = None):
        # Qt clicked/triggered signals may pass a checked: bool argument.  A bool is never a workbench name.
        if isinstance(name, bool):
            name = None
        if name is None:
            name, ok = QInputDialog.getText(self, "新建工作台", "工作台名称：",
                                            text=f"工作台 {len(self.workbenches) + 1}")
            if not ok or not name.strip():
                return None
        name=str(name).strip()
        if not name:return None
        wb = {
            "workbench_id": str(uuid.uuid4()),
            "name": name.strip(),
            "standard_key": None,
            "created_at": time.time(),
            "is_collapsed": False,
            "samples_data": [],
            "average_standard": None,
            "hidden_columns": [],
            "column_preset": "custom",
            "illuminants": ["D65"], "observer": 10,
        }
        self.workbenches.append(wb)
        self.store.save_workbench(wb)
        self.active_workbench_id = wb["workbench_id"]
        self.refresh_workbench_tabs()
        return wb

    def close_workbench_tab(self, index: int):
        """标签页 X = 真正删除；需要暂时隐藏请使用工作台内的【收起】按钮。"""
        widget = self.workbench_tabs.widget(index)
        if widget is None:
            return
        wb_id = widget.property("workbench_id")
        if not wb_id:
            return
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        reply = QMessageBox.question(
            self, "删除工作台",
            f"确定永久删除工作台【{wb['name']}】吗？\n\n"
            "该工作台及其中保存的色样列表会从数据库删除。\n"
            "原始 QTX 文件和色库中的色样不会被删除。\n\n"
            "如果只是暂时不想显示，请取消并使用【收起】按钮。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.store.remove_workbench(wb_id)
        self.workbenches = [w for w in self.workbenches if w["workbench_id"] != wb_id]
        if self.active_workbench_id == wb_id:
            self.active_workbench_id = None
        self.refresh_workbench_tabs()
        self.statusBar().showMessage(f"工作台“{wb['name']}”已永久删除", 5000)

    def on_workbench_tab_changed(self, index: int):
        widget=self.workbench_tabs.widget(index)
        if widget:
            wb_id=widget.property('workbench_id')
            if wb_id:
                self.active_workbench_id=wb_id
                wb=self._find_workbench(wb_id); self._update_workbench_toolbar_labels(wb); self._sync_workbench_preset_label(wb)
                table=getattr(widget,'workbench_table',None)
                if table is not None and hasattr(self,'wb_table_view_btn'):
                    labels={'fit':'密度：适应窗口 ▾','standard':'密度：标准宽度 ▾','compact':'密度：紧凑显示 ▾'}
                    self.wb_table_view_btn.setText(labels.get(getattr(table,'_view_mode','fit'),'密度：适应窗口 ▾'))
                # First visit computes the tab; revisits with unchanged data are instant.
                QTimer.singleShot(0,lambda wid=wb_id:self._refresh_one_workbench(wid,force=False))

    def add_samples_to_workbench(self, wb_id: str):
        if not self.samples and not self.store.has_samples():
            QMessageBox.information(self, "加入色样", "色库和临时工作区都没有色样，请先导入 QTX 或 Excel。")
            return
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        already = set()
        # Serialized workbench rows already contain the identity fields.  Build
        # keys without reconstructing every spectrum before the picker opens.
        for data in wb.get("samples_data", []):
            try:
                sid=str(data.get("sample_id", ""))
                raw=data.get("raw") or {}
                inst=str(raw.get("__palette_instance", "") or "").strip()
                if inst:sid=f"{sid}~@{inst}"
                already.add(_sample_key_cached(str(data.get("source_file", "")), sid))
            except Exception:
                continue
        dialog = AddSamplesDialog(self, already, self)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.chosen()
        for s in chosen:
            sk = sample_key(s)
            if sk in already:
                continue
            wb.setdefault("samples_data", []).append(self._serialize_sample(s))
            wb.setdefault("sample_keys", []).append(sk)
            already.add(sk)
        self.recompute_workbench_average(wb, save=False)
        self.store.save_workbench(wb)
        self._refresh_one_workbench(wb_id)

    def import_paths_to_workbench(self, wb_id: str, paths):
        """Drop QTX / CPX / Excel files directly into a compare workbench."""
        wb=self._find_workbench(wb_id)
        if not wb:
            return
        expanded=self._expand_workspace_inputs(list(paths),include_excel=True)
        qtx=[p for p in expanded if Path(p).suffix.lower() in {'.qtx','.txt','.cpx','.xlsx'}]
        if not qtx:
            QMessageBox.information(self,'比色工作台','没有找到可导入的 QTX / CPX / Excel 文件。')
            return
        existing={d.get('sample_id','')+'|'+d.get('source_file','') for d in wb.get('samples_data',[])}
        added=[]
        for path in qtx:
            try:
                canonical=str(Path(path).resolve())
                samples=self._parse_external_samples(canonical)
                already={sample_key(self._deserialize_sample(d)) for d in wb.get('samples_data',[]) if d}
                dlg=QtxImportSelectionDialog(canonical,samples,already=already,parent=self,purpose='当前比色工作台')
                if dlg.exec()!=QDialog.Accepted:
                    continue
                for sm in dlg.chosen():
                    sk=sm.sample_id+'|'+sm.source_file
                    if sk in existing:
                        continue
                    wb.setdefault('samples_data',[]).append(self._serialize_sample(sm)); wb.setdefault('sample_keys',[]).append(sample_key(sm)); existing.add(sk); added.append(sm)
            except Exception as exc:
                QMessageBox.warning(self,'比色工作台',f'{Path(path).name} 导入失败：{exc}')
        if added:
            self.recompute_workbench_average(wb,save=False)
            self.store.save_workbench(wb); self._refresh_one_workbench(wb_id)
            self.statusBar().showMessage(f'已拖入 {len(added)} 个色样到当前比色工作台',4000)

    def import_qtx_to_workbench(self, wb_id: str):
        """选择 QTX 后先弹出色样选择器，再把勾选内容加入当前工作台。"""
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入 QTX 到当前工作台", "", "QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)")
        if not paths:
            return

        existing = set()
        for data in wb.get("samples_data", []):
            try: existing.add(sample_key(self._deserialize_sample(data)))
            except Exception: continue

        added, duplicate, errors = 0, 0, []
        for path in paths:
            source = str(Path(path).resolve())
            try:
                parsed = [replace(sample, source_file=source) for sample in parse_qtx_file(source)]
                if not parsed:
                    errors.append(f"{Path(path).name}：没有识别到可导入色样")
                    continue
                # 单色样仍显示轻量选择窗，保持操作一致，也允许用户取消。
                picker = QtxImportSelectionDialog(source, parsed, existing, self)
                if picker.exec() != QDialog.Accepted:
                    continue
                chosen = picker.chosen()
                for sample in chosen:
                    key = sample_key(sample)
                    if key in existing:
                        duplicate += 1; continue
                    wb.setdefault("samples_data", []).append(self._serialize_sample(sample))
                    existing.add(key); added += 1
            except Exception as exc:
                errors.append(f"{Path(path).name}：{exc}")

        if added:
            self.recompute_workbench_average(wb, save=False)
            self.store.save_workbench(wb)
            self._refresh_one_workbench(wb_id)
        message = f"已临时加入 {added} 个色样"
        if duplicate: message += f"，跳过 {duplicate} 个重复色样"
        if errors:
            QMessageBox.warning(self, "部分 QTX 未导入", message + "\n\n" + "\n".join(errors))
        else:
            self.statusBar().showMessage(message + "。仅加入当前工作台，不修改原 QTX 和正式色库。", 6000)

    def set_workbench_standard(self, wb_id: str):
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        widget = self._find_workbench_widget(wb_id)
        if not widget or not hasattr(widget, "workbench_table"):
            return
        table = widget.workbench_table
        idx = table.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "设为标准", "请先在下方数据表中选中一行。")
            return
        model = getattr(widget, "workbench_model", None)
        key = model.key_at(idx.row()) if model is not None and hasattr(model, "key_at") else None
        if not key:
            QMessageBox.warning(self, "设为标准", "无法确定当前行对应的色样。")
            return
        wb["standard_key"] = key
        wb["average_standard"] = None
        wb["average_explicit"] = False
        self.store.save_workbench(wb)
        self._refresh_one_workbench(wb_id)
        self.statusBar().showMessage("已将选中色样设为当前标准。", 4000)

    def set_workbench_average_standard(self, wb_id: str):
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        samples = self._workbench_samples(wb)
        if not samples:
            QMessageBox.information(self, "平均标准", "工作台里还没有色样。")
            return
        dialog = WorkbenchAverageDialog(samples, self)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            QMessageBox.information(self, "平均标准", "请至少勾选一个色样。")
            return
        try:
            avg_sample = average_spectral_standard(chosen, f"光谱平均（{len(chosen)}个）")
            wb["standard_key"] = "__AVERAGE__"
            wb["average_standard"] = self._serialize_sample(avg_sample)
            wb["average_explicit"] = True
            self.store.save_workbench(wb)
            self._refresh_one_workbench(wb_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法建立平均标准", str(exc))

    def toggle_workbench_sort(self, wb_id: str, key: str):
        widget = self._find_workbench_widget(wb_id)
        wb = self._find_workbench(wb_id)
        if not widget or not wb:
            return
        state = getattr(widget, "_sort_state", {"key":"original", "desc":False})
        if key == "original":
            state = {"key":"original", "desc":False}
        elif state.get("key") == key:
            state["desc"] = not state.get("desc", False)
        else:
            # 色差第一次最近→最远；数值第一次最大→最小。
            state = {"key":key, "desc": key != "de"}
        widget._sort_state = state
        for k, btn in getattr(widget, "_sort_buttons", {}).items():
            base = {"original":"原始", "de":"色差", "L":"L*", "a":"a*", "b":"b*", "C":"C*", "h":"h°"}[k]
            btn.setChecked(k == state["key"])
            if k == state["key"] and k != "original":
                btn.setText(base + ("↓" if state["desc"] else "↑"))
            else:
                btn.setText(base)
        if key == "original":
            return
        samples = self._workbench_samples(wb)
        if not samples:
            return
        reverse = state["desc"]
        if key in {"L", "a", "b", "C", "h"}:
            def value(sample):
                L, a, b = self.sample_lab(sample)
                return {"L":L, "a":a, "b":b, "C":math.hypot(a,b), "h":math.degrees(math.atan2(b,a)) % 360}[key]
            samples.sort(key=lambda x: (value(x), x.display_name.casefold()), reverse=reverse)
        elif key == "de":
            if wb.get("standard_key") == "__AVERAGE__" and wb.get("average_standard"):
                ref = self._deserialize_sample(wb["average_standard"])
            else:
                ref = next((x for x in samples if sample_key(x) == wb.get("standard_key")), None)
            if ref is None:
                QMessageBox.information(self, "排序", "按色差排序前，请先设置标准色。")
                return
            def value(sample):
                if sample_key(sample) == sample_key(ref):
                    return 0.0
                try:
                    r = analyse_pair(ref, sample, self.illuminant, observer_degrees=self.observer)
                    return float(getattr(r, self.active_formula))
                except Exception:
                    return float("inf")
            samples.sort(key=lambda x: (value(x), x.display_name.casefold()), reverse=reverse)
        wb["samples_data"] = [self._serialize_sample(x) for x in samples]
        self.store.save_workbench(wb)
        self._refresh_one_workbench(wb_id)

    def rename_workbench_sample(self, wb_id: str, key: str, new_name: str):
        """只修改当前工作台中的显示名称；原 QTX 和正式色库均不改。"""
        wb=self._find_workbench(wb_id)
        if not wb or not key or not str(new_name).strip(): return
        changed=False; new_data=[]
        for d in wb.get('samples_data', []):
            try:
                sm=self._deserialize_sample(d); sk=sample_key(sm)
            except Exception:
                new_data.append(d); continue
            if sk==key:
                raw=dict(sm.raw or {})
                raw.setdefault('__ORIGINAL_DISPLAY_NAME', sm.display_name)
                sm=replace(sm, display_name=str(new_name).strip(), raw=raw)
                d=self._serialize_sample(sm); changed=True
            new_data.append(d)
        if changed:
            wb['samples_data']=new_data
            if wb.get('average_standard'):
                # 平均标准不依赖名称；保留原对象即可。
                pass
            self.store.save_workbench(wb)
            wb.pop('_runtime_samples',None); wb.pop('_runtime_samples_sig',None)
            self._refresh_one_workbench(wb_id)
            self.statusBar().showMessage(f'当前工作台显示名称已改为：{new_name}',3500)

    def restore_workbench_sample_name(self, wb_id: str, key: str):
        wb=self._find_workbench(wb_id)
        if not wb:return
        for d in wb.get('samples_data',[]):
            try: sm=self._deserialize_sample(d)
            except Exception: continue
            if sample_key(sm)==key:
                original=(sm.raw or {}).get('__ORIGINAL_DISPLAY_NAME')
                if original:
                    self.rename_workbench_sample(wb_id,key,original)
                return

    @staticmethod
    def _patch_qtx_names(text: str, samples: list[Sample]) -> str:
        """按 GUID 精确修改导出副本中的 STD_NAME/BAT_NAME，其他测量字段原样保留。"""
        aliases={}
        for sm in samples:
            original=(sm.raw or {}).get('__ORIGINAL_DISPLAY_NAME', sm.display_name)
            if sm.display_name != original:
                aliases[str(sm.sample_id).strip()] = (sm.kind, sm.display_name)
        if not aliases:return text
        # 逐 section 操作，避免同名色样误替换。
        block_re=re.compile(r'(?ms)^\[((?:STANDARD_DATA|BATCH_DATA)(?:\s+\d+)?)\](\s*,?)\s*\n(.*?)(?=^\[[^\]\r\n]+\]\s*,?\s*$|\Z)')
        def repl(m):
            section_header=m.group(1); delimiter=m.group(2); body=m.group(3)
            section='STANDARD_DATA' if section_header.startswith('STANDARD_DATA') else 'BATCH_DATA'
            prefix='STD' if section=='STANDARD_DATA' else 'BAT'
            gm=re.search(rf'(?m)^{prefix}_GUID\s*=\s*([^\r\n,]+)',body)
            if not gm:return m.group(0)
            guid=gm.group(1).strip()
            info=aliases.get(guid)
            if not info:return m.group(0)
            name=info[1].replace('\r',' ').replace('\n',' ').strip()
            pat=rf'(?m)^({prefix}_NAME\s*=).*$'
            if re.search(pat,body): body=re.sub(pat,lambda mm:mm.group(1)+name,body,count=1)
            else: body=f'{prefix}_NAME={name}\n'+body
            return f'[{section_header}]{delimiter}\n'+body
        return block_re.sub(repl,text)

    def save_workbench_to_library(self, wb_id: str):
        if not self.require_admin('保存比色结果到正式色库'):return
        wb=self._find_workbench(wb_id); samples=self._workbench_samples(wb) if wb else []
        if not wb or not samples:return
        customer=self._choose_customer_dialog('保存比色结果到正式色库')
        if not customer:return
        folder=self.store.customer_data_root
        for part in customer.replace('\\','/').split('/'):
            if part.strip():folder=folder/self.store._safe_component(part)
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(self.store._safe_component(wb.get('name','比色结果'))+'.qtx')
        export_qtx_file(path,samples); self.store.save_file(str(path),customer,samples)
        self.auth_store.log(self.current_user.username,'SAVE_WORKBENCH_LIBRARY',customer,wb.get('name',''))
        self.statusBar().showMessage(f'比色结果已发布到正式色库：{customer}',5000)

    def workbench_to_color_card(self, wb_id: str):
        wb=self._find_workbench(wb_id); samples=self._workbench_samples(wb) if wb else []
        if not wb or not samples:return
        name=f"{wb.get('name','工作台')} 色卡"
        layout=[{'sample_key':sample_key(x)} for x in samples]
        settings={'sort':'manual','illuminant':(wb.get('illuminants') or ['D65'])[0],'observer':int(wb.get('observer',10)),'grid_cols':6,'grid_rows':0,'auto_grid':True,'cell_w':144,'cell_h':184,'embedded_samples':[self._serialize_sample(x) for x in samples]}
        card=self.store.save_color_card({'name':name,'customer':'工作区色卡','columns_count':6,'layout':layout,'settings':settings,'owner_user_id':int(self.current_user.user_id or 0),'visibility':'private','lifecycle_status':'draft'})
        self.refresh_color_card_list(); self.show_page(2); self.open_color_card_window(card['card_id'])
        self.statusBar().showMessage('已从比色工作台直接生成色卡方案，无需重新导入',4500)

    def export_workbench_qtx(self, wb_id: str):
        """Export the current workbench as one user-named QTX file.

        HF119 fixes the former folder-picker workflow: users naturally type a QTX
        filename, not a new directory name.  The current workbench is an analysis
        document, so its export is one consolidated QTX containing the spectral
        samples currently present in the workbench.
        """
        wb=self._find_workbench(wb_id)
        if not wb:return
        samples=self._workbench_samples(wb)
        if not self.can_export_samples(samples,'导出比色工作台 QTX'):return
        spectral=[sm for sm in samples if sm.has_spectrum()]
        skipped=[sm for sm in samples if not sm.has_spectrum()]
        if not spectral:
            QMessageBox.information(self,'导出 QTX','当前工作台中的色样没有可用于 QTX 的反射率光谱。\n\nLab/RGB-only 色样不能凭空生成反射率。');return
        safe=(self.store._safe_component(wb.get('name','比色工作台')) if hasattr(self.store,'_safe_component') else re.sub(r'[\\/:*?"<>|]+','_',wb.get('name','比色工作台')))
        default_name=str(safe or '比色工作台')+'.qtx'
        path,_=QFileDialog.getSaveFileName(self,'导出工作台 QTX',default_name,'QTX 文件 (*.qtx)')
        if not path:return
        target=Path(path)
        if target.suffix.lower()!='.qtx':target=target.with_suffix('.qtx')
        try:
            target.parent.mkdir(parents=True,exist_ok=True)
            export_qtx_file(target,spectral)
            self.audit('EXPORT_WORKBENCH_QTX',str(target),f'workbench={wb.get("name","")} samples={len(spectral)} skipped={len(skipped)}')
            self.statusBar().showMessage(f'已导出工作台 QTX：{target}',5000)
            msg=f'已导出 {len(spectral)} 个色样到：\n{target}'
            if skipped:msg+=f'\n\n另有 {len(skipped)} 个仅含 Lab/RGB、没有反射率的色样未写入 QTX。'
            QMessageBox.information(self,'导出 QTX',msg)
        except Exception as exc:
            QMessageBox.warning(self,'导出 QTX 失败',str(exc))

    def export_workbench_excel(self, wb_id: str):
        wb = self._find_workbench(wb_id)
        if wb is not None and not self.can_export_samples(self._workbench_samples(wb),'导出比色工作台 Excel'):return
        widget = self._find_workbench_widget(wb_id)
        if not wb or not widget or not hasattr(widget, "workbench_table"):
            return
        table = widget.workbench_table
        model = table.model()
        visible_cols = [c for c in range(model.columnCount()) if not table.isColumnHidden(c)]
        if not visible_cols:
            QMessageBox.information(self, "导出 Excel", "当前没有可导出的显示列。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出比色工作台", f"{wb['name']}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        headers = [model.HEADERS[c] for c in visible_cols]
        rows = []
        for r in range(model.rowCount()):
            row = []
            for c in visible_cols:
                if c == 0:
                    row.append({"swatch_lab": model._rows[r]["lab"]})
                else:
                    row.append(model.data(model.index(r, c), Qt.DisplayRole))
            rows.append(row)
        export_workbench_table(path, wb["name"], headers, rows, samples=self._workbench_samples(wb))
        self.audit('EXPORT_EXCEL',path,f'workbench={wb.get("name","")}')
        self.statusBar().showMessage(f"工作台 Excel 已导出：{path}", 6000)

    def remove_workbench_samples(self, wb_id: str):
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        widget = self._find_workbench_widget(wb_id)
        if not widget or not hasattr(widget, "workbench_table"):
            return
        table = widget.workbench_table
        rows = sorted({i.row() for i in table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "移除色样", "请先选中要移除的行。")
            return
        model = table.model()
        keys_to_remove = []
        for r in rows:
            key = model.key_at(r) if hasattr(model, "key_at") else None
            if key and key not in keys_to_remove:
                keys_to_remove.append(key)
        new_data = []
        for d in wb.get("samples_data", []):
            try:
                sk = sample_key(self._deserialize_sample(d))
            except Exception:
                new_data.append(d)
                continue
            if sk in keys_to_remove:
                if wb.get("standard_key") == sk:
                    wb["standard_key"] = None
                continue
            new_data.append(d)
        wb["samples_data"] = new_data
        self.recompute_workbench_average(wb, save=False)
        self.store.save_workbench(wb)
        self._refresh_one_workbench(wb_id)

    def collapse_workbench(self, wb_id: str):
        wb = self._find_workbench(wb_id)
        if not wb:
            return
        wb["is_collapsed"] = True
        self.store.save_workbench(wb)
        if self.active_workbench_id == wb_id:
            self.active_workbench_id = None
        self.refresh_workbench_tabs()

    def reorder_workbench_sample(self, wb_id, drag_key, target_key):
        wb=self._find_workbench(wb_id)
        if not wb or not drag_key or drag_key==target_key:return
        data=list(wb.get('samples_data') or [])
        def key_of(d):
            try:return sample_key(self._deserialize_sample(d))
            except Exception:return ''
        pos={key_of(d):i for i,d in enumerate(data)}
        if drag_key not in pos:return
        moving=data.pop(pos[drag_key])
        target_index=len(data)
        if target_key:
            for i,d in enumerate(data):
                if key_of(d)==target_key:target_index=i;break
        # 当前标准固定第一位；其它样本可自由拖动。
        std=wb.get('standard_key')
        if drag_key==std:return
        if std and data and key_of(data[0])==std: target_index=max(1,target_index)
        data.insert(target_index,moving); wb['samples_data']=data; wb['sample_keys']=[key_of(d) for d in data]
        self.store.save_workbench(wb); self._refresh_one_workbench(wb_id)

    def show_workbench_row_menu(self, wb_id, table, pos):
        wb=self._find_workbench(wb_id); model=table.model()
        if not wb or model is None:return
        clicked=table.indexAt(pos)
        if clicked.isValid() and not table.selectionModel().isRowSelected(clicked.row(), QModelIndex()):
            table.selectRow(clicked.row())
        selected_rows=sorted({i.row() for i in table.selectionModel().selectedRows()})
        keys=[]
        for r in selected_rows:
            if hasattr(model,'_keys') and r < len(model._keys):
                k=model._keys[r]
                if k not in keys:keys.append(k)
        menu=QMenu(self)
        rename=None; restore=None; details=None
        if len(keys)==1:
            rename=menu.addAction('重命名显示名称…')
            restore=menu.addAction('恢复原始名称')
            details=menu.addAction('查看测色明细')
            menu.addSeparator()
        copy=menu.addAction(f'复制 {len(keys)} 行数据    Ctrl+C') if keys else None
        select_all=menu.addAction('全选当前工作台    Ctrl+A')
        menu.addSeparator()
        analysis_menu=menu.addMenu('分析')
        shade_settings=analysis_menu.addAction('555 色阶分选设置…')
        shade_sort=analysis_menu.addAction('按 555 代码排序')
        shade_sort.setEnabled(bool((wb.get('shade555') or {}).get('enabled')))
        threshold=analysis_menu.addAction('色差阈值设置…')
        standard=menu.addAction('设为标准') if len(keys)==1 else None
        add=menu.addAction('加入色样…')
        up=menu.addAction('上移') if len(keys)==1 else None
        down=menu.addAction('下移') if len(keys)==1 else None
        remove=menu.addAction(f'从当前工作台移除（{len(keys)}）    Delete') if keys else None
        chosen=menu.exec(table.viewport().mapToGlobal(pos))
        if copy is not None and chosen==copy:table.copy_selected();return
        if chosen==select_all:table.selectAll();return
        if chosen==threshold:self.open_threshold_dialog();return
        if standard is not None and chosen==standard:self.set_workbench_standard(wb_id);return
        if chosen==shade_settings:self.open_555_settings(wb_id);return
        if chosen==shade_sort:self.sort_workbench_by_555(wb_id);return
        if len(keys)==1 and chosen==rename:
            sm=next((x for x in self._workbench_samples(wb) if sample_key(x)==keys[0]),None)
            if sm:
                name,ok=QInputDialog.getText(self,'重命名显示名称','当前工作台名称：',text=sm.display_name)
                if ok and name.strip():self.rename_workbench_sample(wb_id,keys[0],name.strip())
            return
        if len(keys)==1 and chosen==restore:self.restore_workbench_sample_name(wb_id,keys[0]);return
        if len(keys)==1 and chosen==details:
            sm=next((x for x in self._workbench_samples(wb) if sample_key(x)==keys[0]),None)
            if sm:self.show_sample_details_dialog(sm)
            return
        if chosen==add:self.add_samples_to_workbench(wb_id);return
        if remove is not None and chosen==remove:
            self.remove_workbench_samples(wb_id);return
        if len(keys)==1 and chosen in (up,down):
            data=list(wb.get('samples_data') or []); samples=[self._deserialize_sample(d) for d in data]; order=[sample_key(x) for x in samples]
            k=keys[0]; i=order.index(k) if k in order else -1
            if i<0:return
            j=max(0,i-1) if chosen==up else min(len(order)-1,i+1)
            if wb.get('standard_key') and j==0 and k!=wb.get('standard_key'): j=1
            if i!=j:data[i],data[j]=data[j],data[i]; wb['samples_data']=data; wb['sample_keys']=[sample_key(self._deserialize_sample(d)) for d in data]; self.store.save_workbench(wb); self._refresh_one_workbench(wb_id)

    def show_collapsed_workbenches(self):
        collapsed = [w for w in self.workbenches if w.get("is_collapsed")]
        if not collapsed:
            QMessageBox.information(self, "已收起工作台", "没有已收起的工作台。")
            return
        dialog = CollapsedWorkbenchesDialog(collapsed, self)
        if dialog.exec() != QDialog.Accepted:
            return
        to_restore = dialog.restore_ids()
        for wb in self.workbenches:
            if wb["workbench_id"] in to_restore:
                wb["is_collapsed"] = False
                self.store.save_workbench(wb)
        self.refresh_workbench_tabs()

    # ============ 列设置 ============
    def show_workbench_column_menu(self, table: QTableView, pos):
        menu=QMenu(self)
        wb_id=None
        for i in range(self.workbench_tabs.count()):
            w=self.workbench_tabs.widget(i)
            if hasattr(w,'workbench_table') and w.workbench_table is table:
                wb_id=w.property('workbench_id'); break
        presets=menu.addMenu('视图预设')
        for pid in ('basic','chromaticity','difference','shade555','full'):
            presets.addAction(WORKBENCH_VIEW_PRESETS[pid]['label'],lambda _=False,p=pid:self._apply_workbench_preset_to_table(table,wb_id,p))
        menu.addAction('列与视图…',lambda:self.open_column_settings(wb_id,table))
        density=menu.addMenu('列宽密度')
        density.addAction('适应窗口',lambda:table.set_view_mode('fit'))
        density.addAction('标准宽度',lambda:table.set_view_mode('standard'))
        density.addAction('紧凑显示',lambda:table.set_view_mode('compact'))
        menu.addSeparator(); menu.addAction('恢复默认列',lambda:self.default_workbench_columns(table,wb_id))
        menu.exec(table.horizontalHeader().mapToGlobal(pos))

    def _apply_workbench_preset_to_table(self, table, wb_id, preset_id):
        model=table.model(); cfg=WORKBENCH_VIEW_PRESETS.get(str(preset_id))
        if model is None or not cfg:return
        visible=cfg.get('keys')
        for col in range(model.columnCount()):
            key=model.KEYS[col] if hasattr(model,'KEYS') else None
            hide=False if col in (0,1) else (False if visible is None else bool(key) and key not in visible)
            table.setColumnHidden(col,hide)
        if wb_id:
            wb=self._find_workbench(wb_id)
            if wb is not None:wb['column_preset']=str(preset_id)
        self._save_hidden_columns(table,wb_id)
        if hasattr(table,'apply_view_mode'):table.apply_view_mode()
        if wb_id==self.active_workbench_id and hasattr(self,'wb_column_preset_btn'):
            self.wb_column_preset_btn.setText(f"视图：{cfg.get('label','自定义')} ▾")

    def on_column_toggle(self, table: QTableView, wb_id: str | None, col: int, visible: bool):
        table.setColumnHidden(col, not visible)
        if wb_id:
            wb=self._find_workbench(wb_id)
            if wb is not None:wb['column_preset']='custom'
        self._save_hidden_columns(table, wb_id)
        if wb_id==self.active_workbench_id:self._sync_workbench_preset_label(self._find_workbench(wb_id))

    def _save_hidden_columns(self, table: QTableView, wb_id: str | None):
        model = table.model()
        if not hasattr(model, "KEYS"):
            return
        hidden_keys = [model.KEYS[c] for c in range(model.columnCount())
                       if table.isColumnHidden(c)]
        hidden_keys = [k for k in hidden_keys if k]
        if wb_id:
            wb = self._find_workbench(wb_id)
            if wb is not None:
                wb["hidden_columns"] = hidden_keys
                self.store.save_workbench(wb)
                return
        self.store.save_column_settings("default", hidden_keys)

    def open_column_settings(self, wb_id: str | None, table: QTableView):
        model = table.model()
        if not hasattr(model, "HEADERS"):
            return
        current_hidden = [model.KEYS[c] for c in range(model.columnCount())
                          if table.isColumnHidden(c)]
        dialog = ColumnSettingsDialog(model.HEADERS, model.KEYS, current_hidden, self)
        if dialog.exec() != QDialog.Accepted:
            return
        hidden_keys = dialog.hidden_keys()
        for c in range(model.columnCount()):
            k=model.KEYS[c]
            table.setColumnHidden(c,False if c in (0,1) else k in hidden_keys)
        if wb_id:
            wb=self._find_workbench(wb_id)
            if wb is not None:wb['column_preset']='custom'
        self._save_hidden_columns(table, wb_id)
        if hasattr(table,'apply_view_mode'):table.apply_view_mode()
        if wb_id==self.active_workbench_id:self._sync_workbench_preset_label(self._find_workbench(wb_id))

    def default_workbench_columns(self, table: QTableView, wb_id: str | None = None):
        model=table.model()
        for col in range(model.columnCount()):
            key=model.KEYS[col] if hasattr(model,'KEYS') else None
            table.setColumnHidden(col,False if col in (0,1) else bool(key) and key not in DEFAULT_VISIBLE_KEYS)
        if wb_id:
            wb=self._find_workbench(wb_id)
            if wb is not None:wb['column_preset']='custom'
        self._save_hidden_columns(table,wb_id)
        if hasattr(table,'apply_view_mode'):table.apply_view_mode()
        if wb_id==self.active_workbench_id:self._sync_workbench_preset_label(self._find_workbench(wb_id))


    def _apply_saved_columns(self, table: QTableView, wb_id: str | None):
        model = table.model()
        if not hasattr(model, "KEYS"):
            return
        hidden_keys = []
        if wb_id:
            wb = self._find_workbench(wb_id)
            if wb is not None:
                hidden_keys = wb.get("hidden_columns", [])
        if not hidden_keys:
            hidden_keys = self.store.load_column_settings("default")
        if not hidden_keys:
            # 新工作台首次打开采用精简默认列，而不是把所有高级列一次性铺满。
            for c in range(model.columnCount()):
                k=model.KEYS[c]
                table.setColumnHidden(c, bool(k) and k not in DEFAULT_VISIBLE_KEYS)
            # Identity columns are the visual anchor of the frozen pane and are never hidden.
            table.setColumnHidden(0,False); table.setColumnHidden(1,False)
            return
        wb=self._find_workbench(wb_id) if wb_id else None
        for c in range(model.columnCount()):
            k=model.KEYS[c]
            if c in (0,1):
                table.setColumnHidden(c,False); continue
            if not k:continue
            if k=='shade555' and not bool(wb and (wb.get('shade555') or {}).get('enabled')):
                table.setColumnHidden(c,True)
            else:
                table.setColumnHidden(c,k in hidden_keys)

    def _shade555_standard_code(self, cfg):
        try:
            mode=str(cfg.get('mode','LAB')).upper(); ranges=dict(cfg.get('ranges') or {}); ranges=ranges or ({'L':(-1.8,1.8),'a':(-.9,.9),'b':(-.9,.9)} if mode=='LAB' else {'L':(-1.8,1.8),'C':(-.9,.9),'H':(-.9,.9)}); blocks=int(cfg.get('blocks',9))
            # zero delta relative to itself; asymmetric tolerances may place the
            # standard away from the numeric centre by design.
            return shade_555((50,0,0),(50,0,0),ranges,mode=mode,blocks=blocks).code
        except Exception:return '—'

    def open_555_settings(self, wb_id):
        wb=self._find_workbench(wb_id) if wb_id else None
        if not wb:return
        dlg=Shade555Dialog(wb,self)
        if dlg.exec()!=QDialog.Accepted:return
        try:wb['shade555']=dlg.settings()
        except Exception as exc:QMessageBox.warning(self,'555 色阶分选',str(exc)); return
        self.store.save_workbench(wb); self._refresh_one_workbench(wb_id)
        cfg=wb.get('shade555') or {}; widget=self._find_workbench_widget(wb_id)
        if widget is not None and hasattr(widget,'workbench_table'):
            model=widget.workbench_table.model()
            if hasattr(model,'KEYS') and 'shade555' in model.KEYS:
                widget.workbench_table.setColumnHidden(model.KEYS.index('shade555'),not bool(cfg.get('enabled')))
                self._save_hidden_columns(widget.workbench_table,wb_id)
        self.statusBar().showMessage('555 分选已启用；按 Datacolor 低/高容差与箱数进行分阶，且只对已合格批次编码' if cfg.get('enabled') else '555 分选未启用',4000)

    def sort_workbench_by_555(self, wb_id):
        wb=self._find_workbench(wb_id) if wb_id else None; widget=self._find_workbench_widget(wb_id) if wb_id else None
        if not wb or not widget or not hasattr(widget,'workbench_model'):return
        model=widget.workbench_model; model.reload(wb)
        code_by_key={}
        for r,key in enumerate(model._keys):
            if key not in code_by_key:
                code_by_key[key]=model._rows[r].get('shade555','—')
        def rank(code):
            s=str(code or '—')
            if s.isdigit():return (0,int(s))
            if s=='超范围':return (1,9999)
            return (2,9999)
        std=wb.get('standard_key'); data=list(wb.get('samples_data') or [])
        def k(d):
            try:return sample_key(self._deserialize_sample(d))
            except Exception:return ''
        data.sort(key=lambda d:((0,0) if k(d)==std else (1,0),rank(code_by_key.get(k(d)))))
        wb['samples_data']=data; wb['sample_keys']=[k(d) for d in data]; self.store.save_workbench(wb); self._refresh_one_workbench(wb_id)

    def open_threshold_dialog(self):
        dialog = ThresholdDialog(self.thresholds, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.thresholds = dialog.values()
        self._save_thresholds()
        self.refresh_all_workbenches()

    # ============ 光谱页 ============
    def placeholder(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(26, 24, 26, 22)
        a = QLabel("反射率数据（360–700 nm，间隔10 nm）")
        a.setObjectName("sectionTitle")
        l.addWidget(a)
        self.spectrum_text = QTextEdit()
        self.spectrum_text.setReadOnly(True)
        self.spectrum_text.setPlaceholderText("先在色库选择色样，再打开光谱分析")
        l.addWidget(self.spectrum_text, 1)
        return w

    def _apply_style(self):
        # UI-only Light Studio design system. No parser, algorithm, database,
        # signal binding, shortcut or export behavior is changed here.
        self.setStyleSheet("""
            QMainWindow{background:#F3F4F6;color:#1F2937;font-family:'Microsoft YaHei UI','Segoe UI';font-size:12px;}
            QWidget{selection-background-color:#DBEAFE;selection-color:#1F2937;}

            /* top brand / navigation */
            QToolBar#workspaceBar{background:#FFFFFF;border:0;border-bottom:1px solid #E5E7EB;spacing:6px;padding:7px 18px;min-height:56px;max-height:56px;}
            QLabel#brandMark{background:transparent;border:0;}
            QLabel#workspaceBrand{font-size:15px;font-weight:800;color:#111827;padding:0;}
            QLabel#brandVersion{font-size:10px;color:#9CA3AF;padding-top:2px;}
            QLabel#brandTagline{font-size:9px;color:#6B7280;padding:0;}
            QToolButton#topNav,QToolButton#topNavSelected{background:transparent;color:#4B5563;border:0;border-radius:8px;padding:8px 10px;font-weight:600;min-height:24px;min-width:58px;}
            QToolButton#topNav::menu-indicator,QToolButton#topNavSelected::menu-indicator{width:0px;height:0px;image:none;}
            QToolButton#topNav:hover{background:#F3F4F6;color:#111827;}
            QToolButton#topNavSelected{background:#EFF6FF;color:#2563EB;}
            QLabel#avatarChip{background:#2563EB;color:#FFFFFF;border-radius:15px;font-size:12px;font-weight:800;margin-left:10px;}
            QToolButton#accountChip{background:transparent;color:#1F2937;border:0;border-radius:8px;padding:7px 8px;font-weight:700;margin-left:2px;}
            QToolButton#accountChip:hover{background:#F3F4F6;}
            QLineEdit#globalSearch{background:#F3F4F6;border:1px solid #E5E7EB;border-radius:8px;padding:8px 12px;color:#374151;min-height:22px;}
            QLineEdit#globalSearch:focus{background:#FFFFFF;border:1px solid #3B82F6;}

            /* buttons */
            QPushButton,QToolButton{background:#FFFFFF;color:#374151;border:1px solid #D1D5DB;border-radius:8px;padding:7px 12px;min-height:20px;}
            QPushButton:hover,QToolButton:hover{background:#F9FAFB;border-color:#BFC5CD;color:#111827;}
            QPushButton:pressed,QToolButton:pressed{background:#F3F4F6;}
            QPushButton:checked,QToolButton:checked{background:#EFF6FF;border-color:#93C5FD;color:#2563EB;}
            QPushButton:disabled,QToolButton:disabled{color:#B0B7C1;background:#F9FAFB;border-color:#E5E7EB;}
            QPushButton#primaryButton,QToolButton#primaryButton{background:#3B82F6;color:#FFFFFF;border:1px solid #3B82F6;font-weight:650;}
            QPushButton#primaryButton:hover,QToolButton#primaryButton:hover{background:#2563EB;border-color:#2563EB;}
            QPushButton#dangerButton{background:#EF4444;color:#FFFFFF;border-color:#EF4444;}

            /* inputs */
            QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox{background:#FFFFFF;color:#374151;border:1px solid #D1D5DB;border-radius:8px;padding:7px 10px;min-height:22px;}
            QComboBox:hover,QLineEdit:hover,QSpinBox:hover,QDoubleSpinBox:hover{border-color:#B8BEC7;}
            QComboBox:focus,QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus{border:1px solid #3B82F6;background:#FFFFFF;}
            QComboBox::drop-down{border:0;width:24px;}

            /* surfaces / typography */
            QLabel#muted{color:#6B7280;font-size:11px;}
            QLabel#empty{color:#9CA3AF;font-size:13px;}
            QLabel#sectionTitle{font-size:14px;font-weight:700;color:#1F2937;}
            QLabel#pageTitle{font-size:18px;font-weight:800;color:#111827;}
            QLabel#pageSubtitle{font-size:11px;color:#6B7280;}
            QFrame#filePanel{background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;}
            QFrame#analysisCard,QFrame#panel{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;}
            QWidget#libraryContent{background:transparent;}
            QWidget#libraryTileHost{background:#FFFFFF;border:1px solid #EEF0F3;border-radius:12px;}
            QScrollArea#libraryTileScroll{background:#FFFFFF;border:0;border-radius:12px;}

            /* home */
            QWidget#workspaceHome{background:#F3F4F6;}
            QFrame#heroPanel{background:transparent;border:0;}
            QLabel#heroTitle{font-size:22px;font-weight:800;color:#4B5563;}
            QLabel#heroSub{font-size:12px;color:#9CA3AF;}
            QFrame#homeDropZone{background:#FFFFFF;border:1px dashed #CBD5E1;border-radius:12px;max-width:720px;}
            QLabel#dropTitle{font-size:14px;font-weight:700;color:#374151;}
            QLabel#dropHint{font-size:11px;color:#9CA3AF;}
            QLabel#watermark{font-size:42px;font-weight:800;color:#E5E7EB;}

            /* nav/list */
            QListWidget{background:#FFFFFF;color:#374151;border:1px solid #E5E7EB;border-radius:8px;padding:4px;}
            QListWidget::item{padding:7px;border-radius:6px;}
            QListWidget::item:hover{background:#F3F4F6;}
            QListWidget::item:selected{background:#EFF6FF;color:#2563EB;}
            QTreeWidget{background:#F9FAFB;color:#4B5563;border:0;}
            QTreeWidget::item{padding:7px 5px;border-radius:6px;}
            QTreeWidget::item:hover{background:#F3F4F6;}
            QTreeWidget::item:selected{background:#EFF6FF;color:#2563EB;}

            /* tabs */
            QTabWidget{background:#F3F4F6;}
            QTabWidget::pane{border:0;background:transparent;}
            QTabBar::tab{background:transparent;color:#6B7280;padding:9px 14px;border:0;border-bottom:2px solid transparent;margin-right:2px;}
            QTabBar::tab:selected{background:#FFFFFF;color:#2563EB;font-weight:700;border-bottom:2px solid #3B82F6;}
            QTabBar::tab:hover{background:#F9FAFB;color:#374151;}

            /* standard card / data tables */
            QFrame#standardCard{background:#FFFFFF;border:1px solid #E5E7EB;border-left:3px solid #3B82F6;border-radius:12px;padding:8px;}
            QLabel#standardName{font-size:14px;font-weight:700;color:#292524;}
            QLabel#standardValues{color:#78716C;}
            QTableView,QTableWidget{background:#FFFFFF;color:#374151;border:1px solid #E5E7EB;border-radius:8px;alternate-background-color:#F9FAFB;selection-background-color:#EFF6FF;selection-color:#1F2937;gridline-color:#F3F4F6;}
            QTableView::item,QTableWidget::item{padding:7px;}
            QTableView::item:hover,QTableWidget::item:hover{background:#F9FAFB;}
            QHeaderView::section{background:#F9FAFB;color:#4B5563;border:0;border-bottom:1px solid #E5E7EB;padding:8px;font-weight:500;}

            /* formal-library pager */
            QFrame#libraryPager{background:transparent;border:0;}
            QPushButton#pagerPage,QPushButton#pagerCurrent,QPushButton#pagerArrow{padding:0;min-height:0;border-radius:7px;}
            QPushButton#pagerPage,QPushButton#pagerArrow{background:#FFFFFF;border:1px solid #E5E7EB;color:#4B5563;}
            QPushButton#pagerPage:hover,QPushButton#pagerArrow:hover{background:#F9FAFB;border-color:#CBD5E1;}
            QPushButton#pagerCurrent{background:#EFF6FF;border:1px solid #BFDBFE;color:#2563EB;font-weight:700;}
            QLabel#pagerDots{color:#9CA3AF;}
            QComboBox#pageSizeCombo{min-width:92px;}

            /* Light Studio shell / independent internal windows */
            QFrame#studioSidebar{background:#F9FAFB;border:0;border-right:1px solid #E5E7EB;}
            QLabel#sideGroupTitle{color:#9CA3AF;font-size:10px;font-weight:600;padding:8px 7px 3px 7px;}
            QPushButton#sideNav{background:transparent;border:0;border-radius:6px;color:#6B7280;text-align:left;padding:8px 10px;min-height:20px;}
            QPushButton#sideNav:hover{background:#F3F4F6;color:#374151;}
            QPushButton#sideNav:checked{background:#EFF6FF;color:#2563EB;font-weight:700;}
            QToolButton#sideFile{background:transparent;border:0;border-radius:6px;color:#6B7280;text-align:left;padding:8px 10px;min-height:20px;}
            QToolButton#sideFile:hover{background:#F3F4F6;color:#374151;}
            QToolButton#primaryOpenButton{background:#3B82F6;color:white;border:1px solid #3B82F6;border-radius:8px;padding:9px 14px;font-weight:700;min-height:22px;}
            QToolButton#primaryOpenButton:hover{background:#2563EB;border-color:#2563EB;}
            QFrame#sideDropZone{background:#FFFFFF;border:1px dashed #CBD5E1;border-radius:10px;}
            QLabel#sideDropIcon{font-size:24px;color:#3B82F6;font-weight:700;}
            QLabel#sideDropTitle{font-size:11px;color:#475569;font-weight:700;}
            QLabel#sideDropHint{font-size:9px;color:#94A3B8;}
            QMdiArea#studioMdi{background:#F3F4F6;border:0;}
            QWidget#studioEmpty{background:#F3F4F6;}
            QMdiSubWindow#studioToolWindow{background:transparent;border:0;}
            QFrame#studioTaskbar{background:#FFFFFF;border:0;border-top:1px solid #DCE4EE;min-height:40px;max-height:40px;}
            QLabel#taskbarLabel{color:#94A3B8;font-size:10px;padding:0 5px;}
            QToolButton#studioTaskButton{background:#F8FAFC;color:#334155;border:1px solid #CBD5E1;border-radius:7px;padding:4px 10px;min-height:20px;max-width:240px;text-align:left;}
            QToolButton#studioTaskButton:hover{background:#EFF6FF;color:#2563EB;border-color:#93C5FD;}
            QTabWidget#workspaceTabs::pane{border:0;background:#F3F4F6;}
            QTabWidget#workspaceTabs > QTabBar::tab{padding:9px 14px;}
            /* menus / dialogs / status */
            QMenu{background:#FFFFFF;color:#374151;border:1px solid #E5E7EB;border-radius:8px;padding:5px;}
            QMenu::item{padding:8px 24px;border-radius:6px;}
            QMenu::item:selected{background:#EFF6FF;color:#2563EB;}
            QMenu::separator{height:1px;background:#E5E7EB;margin:5px 8px;}
            QDialog#libraryDestinationDialog{background:#FFFFFF;}
            QProgressBar{background:#E8EEF7;border:0;border-radius:6px;min-height:12px;text-align:center;color:#475569;font-size:10px;}
            QProgressBar::chunk{background:#3B82F6;border-radius:6px;}
            QStatusBar{background:#FFFFFF;color:#6B7280;border-top:1px solid #E5E7EB;padding:0 8px;}
            QStatusBar QLabel#statusIdentity{color:#374151;padding:5px 10px;font-weight:650;}
            QStatusBar QLabel#statusClock{color:#9CA3AF;padding:5px 8px;}
            QScrollArea{background:#F3F4F6;border:0;}
            QSplitter::handle{background:#E5E7EB;width:1px;}
            QMdiArea{background:#F3F4F6;border:0;}
            QMdiSubWindow{background:#FFFFFF;}
            QToolTip{background:#111827;color:#FFFFFF;border:0;border-radius:6px;padding:5px;}
            QScrollBar:vertical{background:transparent;width:10px;margin:2px;}
            QScrollBar::handle:vertical{background:#D1D5DB;border-radius:5px;min-height:28px;}
            QScrollBar::handle:vertical:hover{background:#9CA3AF;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
        """)

        # Visual-only refinement layer based on the approved Light Studio mockups.
        # This intentionally changes only Qt styling; no models, signals, actions,
        # shortcuts, parsers, database calls or export/import handlers are touched.
        self.setStyleSheet(self.styleSheet() + r"""
            /* ===== Approved Light Studio visual refinement ===== */
            QMainWindow{
                background:#EEF3F8;
                color:#1F2937;
                font-family:'Microsoft YaHei UI','Segoe UI';
                font-size:12px;
            }

            /* Top brand / primary navigation */
            QToolBar#workspaceBar{
                background:#FFFFFF;
                border:0;
                border-bottom:1px solid #E5E7EB;
                spacing:8px;
                padding:8px 28px;
                min-height:58px;
                max-height:58px;
            }
            QLabel#workspaceBrand{font-size:15px;font-weight:800;color:#172033;}
            QLabel#brandVersion{font-size:10px;color:#94A3B8;padding-left:2px;}
            QLabel#brandTagline{font-size:9px;color:#64748B;}
            QToolButton#topNav,QToolButton#topNavSelected{
                background:transparent;
                color:#334155;
                border:0;
                border-radius:8px;
                padding:8px 12px;
                min-height:26px;
                min-width:60px;
                font-weight:600;
            }
            QToolButton#topNav:hover{background:#F1F5F9;color:#0F172A;}
            QToolButton#topNavSelected{background:#EFF6FF;color:#2563EB;}
            QToolButton#sidebarToggleButton{
                background:transparent;
                border:1px solid transparent;
                border-radius:8px;
                padding:5px;
            }
            QToolButton#sidebarToggleButton:hover{background:#F1F5F9;border-color:#E2E8F0;}
            QToolButton#sidebarToggleButton:pressed,QToolButton#sidebarToggleButton:checked{background:#EFF6FF;border-color:#DBEAFE;}
            QLineEdit#globalSearch{
                min-height:34px;
                max-height:34px;
                background:#F3F6FA;
                color:#334155;
                border:1px solid #E2E8F0;
                border-radius:8px;
                padding:0 12px;
            }
            QLineEdit#globalSearch:hover{background:#F8FAFC;border-color:#CBD5E1;}
            QLineEdit#globalSearch:focus{background:#FFFFFF;border:1px solid #3B82F6;}
            QLabel#avatarChip{background:#2563EB;color:#FFFFFF;border-radius:15px;font-weight:800;}
            QToolButton#accountChip{border:0;background:transparent;color:#1E293B;font-weight:700;padding:7px 8px;}
            QToolButton#accountChip:hover{background:#F1F5F9;}

            /* Secondary document tab strip */
            QTabWidget#workspaceTabs{background:#EEF3F8;}
            QTabWidget#workspaceTabs::pane{border:0;background:#EEF3F8;margin-top:-1px;}
            QTabWidget#workspaceTabs > QTabBar{background:#F8FAFC;}
            QTabWidget#workspaceTabs > QTabBar::tab{
                background:transparent;
                color:#64748B;
                border:0;
                border-bottom:2px solid transparent;
                padding:9px 14px;
                margin:0 1px;
                min-height:20px;
            }
            QTabWidget#workspaceTabs > QTabBar::tab:hover{background:#F1F5F9;color:#334155;}
            QTabWidget#workspaceTabs > QTabBar::tab:selected{
                background:#FFFFFF;
                color:#2563EB;
                font-weight:700;
                border-bottom:2px solid #3B82F6;
            }

            /* Left navigation from the approved browser mockup */
            QFrame#studioSidebar{
                background:#F9FAFB;
                border:0;
                border-right:1px solid #E5E7EB;
            }
            QLabel#sideGroupTitle{
                color:#94A3B8;
                font-size:10px;
                font-weight:650;
                padding:9px 8px 4px 8px;
            }
            QPushButton#sideNav,QToolButton#sideFile{
                background:transparent;
                color:#64748B;
                border:0;
                border-radius:7px;
                text-align:left;
                padding:8px 10px;
                min-height:20px;
            }
            QPushButton#sideNav:hover,QToolButton#sideFile:hover{background:#F1F5F9;color:#334155;}
            QPushButton#sideNav:checked{background:#EFF6FF;color:#2563EB;font-weight:700;}
            QToolButton#primaryOpenButton{
                min-height:38px;
                background:#3B82F6;
                color:#FFFFFF;
                border:1px solid #3B82F6;
                border-radius:8px;
                padding:0 14px;
                font-weight:700;
            }
            QToolButton#primaryOpenButton:hover{background:#2563EB;border-color:#2563EB;}
            QFrame#sideDropZone{background:#FFFFFF;border:1px dashed #CBD5E1;border-radius:10px;}
            QFrame#sideDropZone:hover{background:#F8FBFF;border-color:#93C5FD;}

            /* Main desktop / floating MDI tools */
            QWidget#workspaceHome{
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #EFF5FC,stop:0.42 #F5F8FC,stop:1 #EEF3F8);
            }
            QMdiArea#studioMdi{
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #EFF5FC,stop:0.45 #F7F9FC,stop:1 #EEF3F8);
                border:0;
            }
            QWidget#studioEmpty{background:transparent;}
            QLabel#heroTitle{font-size:22px;font-weight:800;color:#7890B0;}
            QLabel#heroSub{font-size:12px;color:#9AABC1;}
            QLabel#watermark{font-size:44px;font-weight:800;color:#E3EBF5;}
            QFrame#homeDropZone{background:rgba(255,255,255,210);border:1px dashed #CAD6E5;border-radius:12px;}
            QMdiSubWindow#studioToolWindow{
                background:transparent;
                border:0;
            }
            QMdiSubWindow#studioToolWindow > QWidget{background:#FFFFFF;}

            /* Content cards and library/browser surfaces */
            QFrame#filePanel{background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;}
            QWidget#libraryContent{background:transparent;}
            QWidget#libraryTileHost{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;}
            QScrollArea#libraryTileScroll{background:#FFFFFF;border:0;border-radius:12px;}
            QFrame#analysisCard,QFrame#panel{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;}
            QLabel#pageTitle{font-size:18px;font-weight:800;color:#111827;}
            QLabel#pageSubtitle{font-size:11px;color:#7C8A9D;}
            QLabel#sectionTitle{font-size:14px;font-weight:700;color:#1F2937;}
            QLabel#muted{color:#7C8A9D;}

            /* Inputs / filters */
            QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox{
                background:#FFFFFF;
                color:#374151;
                border:1px solid #D1D5DB;
                border-radius:8px;
                padding:7px 10px;
                min-height:22px;
            }
            QComboBox:hover,QLineEdit:hover,QSpinBox:hover,QDoubleSpinBox:hover{border-color:#B8C2CF;}
            QComboBox:focus,QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus{border:1px solid #3B82F6;background:#FFFFFF;}

            /* Buttons */
            QPushButton,QToolButton{
                background:#FFFFFF;
                color:#374151;
                border:1px solid #D1D5DB;
                border-radius:8px;
                padding:7px 12px;
            }
            QPushButton:hover,QToolButton:hover{background:#F9FAFB;border-color:#B8C2CF;color:#111827;}
            QPushButton:checked,QToolButton:checked{background:#EFF6FF;border-color:#93C5FD;color:#2563EB;}
            QPushButton#primaryButton,QToolButton#primaryButton{background:#3B82F6;color:#FFFFFF;border-color:#3B82F6;font-weight:700;}
            QPushButton#primaryButton:hover,QToolButton#primaryButton:hover{background:#2563EB;border-color:#2563EB;}

            /* Workbench: retain every control, modernize only the visual hierarchy */
            QFrame#standardCard{
                background:#FFFFFF;
                border:1px solid #E5E7EB;
                border-left:3px solid #3B82F6;
                border-radius:12px;
                padding:8px;
            }
            QLabel#standardName{font-size:14px;font-weight:750;color:#1F2937;}
            QLabel#standardValues{color:#64748B;}

            /* Tables */
            QTableView,QTableWidget{
                background:#FFFFFF;
                color:#374151;
                border:1px solid #E5E7EB;
                border-radius:8px;
                alternate-background-color:#FBFCFE;
                selection-background-color:#EFF6FF;
                selection-color:#1F2937;
                gridline-color:#F1F5F9;
            }
            QTableView::item,QTableWidget::item{padding:8px;border-bottom:1px solid #F3F4F6;}
            QTableView::item:hover,QTableWidget::item:hover{background:#F8FAFC;}
            QHeaderView::section{
                background:#F9FAFB;
                color:#475569;
                border:0;
                border-right:1px solid #F1F5F9;
                border-bottom:1px solid #E5E7EB;
                padding:8px 10px;
                font-weight:600;
            }

            /* All local tabs/dialogs use one studio language */
            QTabWidget::pane{border:0;background:transparent;}
            QTabBar::tab{
                background:transparent;
                color:#64748B;
                border:0;
                border-bottom:2px solid transparent;
                padding:9px 13px;
            }
            QTabBar::tab:hover{background:#F8FAFC;color:#334155;}
            QTabBar::tab:selected{background:#FFFFFF;color:#2563EB;font-weight:700;border-bottom:2px solid #3B82F6;}
            QDialog{background:#F8FAFC;color:#1F2937;}
            QGroupBox{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:10px;margin-top:10px;padding-top:10px;}
            QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;color:#334155;font-weight:700;}

            /* Menus / bottom status bar */
            QMenu{background:#FFFFFF;color:#374151;border:1px solid #E5E7EB;border-radius:8px;padding:5px;}
            QMenu::item{padding:8px 24px;border-radius:6px;}
            QMenu::item:selected{background:#EFF6FF;color:#2563EB;}
            QMenu::separator{height:1px;background:#E5E7EB;margin:5px 8px;}
            QStatusBar{
                background:#FFFFFF;
                color:#64748B;
                border-top:1px solid #E5E7EB;
                min-height:30px;
                max-height:30px;
                padding:0 14px;
            }
            QStatusBar QLabel#statusIdentity{color:#334155;padding:5px 12px;font-weight:700;}
            QStatusBar QLabel#statusClock{color:#94A3B8;padding:5px 10px;}

            /* Scrollbars */
            QScrollBar:vertical{background:transparent;width:10px;margin:2px;}
            QScrollBar::handle:vertical{background:#CBD5E1;border-radius:5px;min-height:30px;}
            QScrollBar::handle:vertical:hover{background:#94A3B8;}
            QScrollBar:horizontal{background:transparent;height:10px;margin:2px;}
            QScrollBar::handle:horizontal{background:#CBD5E1;border-radius:5px;min-width:30px;}
            QScrollBar::handle:horizontal:hover{background:#94A3B8;}
            QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}

            /* Colour-detail card from the approved reference */
            QDialog#sampleDetailsDialog{background:#FFFFFF;}
            QFrame#detailHero{background:#FFFFFF;border:0;}
            QLabel#detailName{font-size:20px;font-weight:800;color:#172033;}
            QLabel#detailSub{font-size:12px;color:#64748B;}
            QLabel#detailCondition{font-size:11px;color:#94A3B8;}
            QTabWidget#detailTabs::pane{background:#FFFFFF;border:0;border-top:1px solid #E8EDF3;}
            QTabWidget#detailTabs > QTabBar::tab{padding:11px 17px;}
            QFrame#detailInfoCard,QFrame#detailPreviewCard{background:#F8FAFC;border:1px solid #E7EDF4;border-radius:10px;}
            QLabel#detailCardTitle{font-size:13px;font-weight:750;color:#26364D;}
            QLabel#detailPreviewKey{color:#94A3B8;font-size:11px;}
            QLabel#detailPreviewValue{color:#475569;font-size:11px;font-weight:650;}
            QTableWidget#detailDataTable{background:#FFFFFF;border:0;border-radius:7px;alternate-background-color:#F8FAFC;}
            QFrame#detailActions{background:#FFFFFF;border:0;border-top:1px solid #E8EDF3;}
            QLabel#detailActionTitle{font-size:12px;font-weight:750;color:#334155;}
        """)

        # HF135 / UI Responsive P2: one visual language for modern MDI chrome,
        # responsive command surfaces, frozen identity columns and secondary panels.
        self.setStyleSheet(self.styleSheet() + r"""
            QFrame#studioMdiShell{background:#FFFFFF;border:1px solid #DDE3EA;border-radius:12px;}
            QFrame#studioMdiClient{background:#FFFFFF;border:0;}
            QFrame#studioMdiTitleBar{
                background:#FBFCFE;
                border:0;
                border-bottom:1px solid #E6EBF2;
                border-top-left-radius:9px;
                border-top-right-radius:9px;
            }
            QFrame#studioMdiTitleBar[active="true"]{background:#FFFFFF;border-bottom:1px solid #D9E4F2;}
            QLabel#studioMdiTitleIcon{background:#CBD5E1;border-radius:3px;min-width:6px;max-width:6px;}
            QFrame#studioMdiTitleBar[active="true"] QLabel#studioMdiTitleIcon{background:#3B82F6;}
            QLabel#studioMdiTitleText{color:#64748B;font-size:11px;font-weight:650;padding-left:5px;}
            QFrame#studioMdiTitleBar[active="true"] QLabel#studioMdiTitleText{color:#243247;font-weight:750;}
            QToolButton#studioMdiControl,QToolButton#studioMdiClose{
                background:transparent;border:0;border-radius:6px;color:#64748B;padding:0;font-size:13px;
            }
            QToolButton#studioMdiControl:hover{background:#EEF2F7;color:#172033;}
            QToolButton#studioMdiClose:hover{background:#FEE2E2;color:#B91C1C;}

            QFrame#workbenchTopBar{background:#FFFFFF;border:1px solid #E5EAF1;border-radius:10px;padding:5px;}
            QFrame#workbenchSortSegments{background:#F6F8FB;border:1px solid #DDE4ED;border-radius:8px;}
            QPushButton#segmentButton{background:transparent;border:0;border-right:1px solid #E2E8F0;border-radius:0;padding:6px 9px;min-height:22px;}
            QPushButton#segmentButton:hover{background:#FFFFFF;color:#1D4ED8;}
            QPushButton#segmentButton:pressed{background:#EFF6FF;color:#1D4ED8;}
            QPushButton#segmentButton:checked{background:#FFFFFF;color:#1D4ED8;font-weight:700;}
            QFrame#inlineField{background:transparent;border:0;}
            QFrame#pickerPreview{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:9px;}
            QLabel#pickerPreviewName{color:#26364D;font-size:12px;font-weight:700;}
            QLabel#inlineFieldLabel{color:#64748B;font-size:11px;font-weight:600;}
            QToolButton#panelToggle{background:#FFFFFF;border:1px solid #D9E1EA;border-radius:7px;color:#64748B;padding:0;font-size:16px;font-weight:700;}
            QToolButton#panelToggle:hover{background:#EFF6FF;border-color:#93C5FD;color:#2563EB;}
            QWidget#libraryBrowseFilters{background:transparent;}

            QTableView#workbenchFrozenColumns{
                background:#FFFFFF;border:0;border-right:1px solid #CBD5E1;border-radius:0;
                selection-background-color:#EFF6FF;selection-color:#1F2937;gridline-color:#F1F5F9;
            }
            QTableView#workbenchFrozenColumns QHeaderView::section{background:#F8FAFC;color:#334155;font-weight:700;}
        """)

    # ============ 文件加载 ============
    def restore_saved_files(self):
        saved_order = self.store.load_order()
        self.manual_order = [key for key, _ in sorted(saved_order.items(), key=lambda item: item[1])]
        # 不在启动时把所有数据库色样都塞进右侧工作区：右侧应保持空白，
        # 直到用户在左侧点击某个已保存 QTX。数据库记录仍始终显示在文件列表中。
        try:
            self.workbenches = self.store.list_workbenches()
            # DG-2: legacy workbenches are globally stored in older databases.
            # Never let an embedded pre-ACL sample bypass the current user's
            # customer scope; filter only the in-memory view, leaving admin data
            # intact for the later per-user workspace migration.
            for wb in self.workbenches:
                scoped=[]
                for data in list(wb.get('samples_data') or []):
                    try:
                        sm=self._deserialize_sample(data)
                        if self._sample_allowed_by_data_scope(sm):scoped.append(data)
                    except Exception:
                        continue
                wb['samples_data']=scoped
                avg=wb.get('average_standard')
                if avg:
                    try:
                        if not self._sample_allowed_by_data_scope(self._deserialize_sample(avg)):wb['average_standard']=None
                    except Exception:wb['average_standard']=None
                old_lights=list(wb.get('illuminants') or ['D65'])
                new_lights=list(dict.fromkeys(display_illuminant(x) for x in old_lights))
                if new_lights!=old_lights:
                    wb['illuminants']=new_lights
                    if self.is_admin:self.store.save_workbench(wb)
                self._repair_workbench_standard_key(wb)
                if wb.get("samples_data"):
                    self.recompute_workbench_average(wb,save=self.is_admin)
        except Exception:
            self.workbenches = []
        self.refresh_file_list()
        self.refresh()
        # Keep formal-library/card indexes warm as in the frozen pre-HF124 path.
        self.refresh_color_card_list()
        self.statusBar().showMessage("已恢复本地索引和已保存工作台 · 正式色库按需加载")

    def _load_path(self, path, saved=False, customer="临时导入"):
        full = str(Path(path).resolve())
        if full in self.loaded_files:
            if saved:
                self.loaded_files[full].update(saved=True, customer=customer)
            return 0
        samples = [replace(sample, source_file=full) for sample in parse_qtx_file(full)]
        self.loaded_files[full] = {"samples": samples, "saved": saved, "customer": customer}
        return len(samples)

    def rebuild_samples(self):
        new_samples = [s for info in self.loaded_files.values() for s in info["samples"]]
        # refresh() is called from many harmless UI paths.  Do not invalidate the
        # expensive formal-library/palette caches when the workspace objects are
        # literally unchanged.  Replaced Sample objects with the same key still
        # count as a real data change and correctly invalidate the caches.
        if len(new_samples)==len(self.samples) and all(a is b for a,b in zip(new_samples,self.samples)):
            return
        old_keys={sample_key(s) for s in self.samples}
        self.samples = new_samples
        keys = {sample_key(s) for s in self.samples}
        self._library_data_revision += 1
        self._library_view_cache = None
        self.manual_order = [k for k in self.manual_order if k in keys]
        known = set(self.manual_order)
        self.manual_order.extend(sample_key(s) for s in self.samples if sample_key(s) not in known)
        # 当前工作区变化会影响“所有可用色样”缓存；只有实际样本变化时才失效。
        self._library_picker_cache=None
        self._tiles_keys=None

    @profiled("main.samples_for_library_picker")
    def samples_for_library_picker(self) -> list[Sample]:
        """返回可加入工作台的全部色样，并缓存正式色库快照。

        旧版色库/色卡编排多处会重复遍历 SQLite 并反序列化全部色样；数据量大后
        会明显卡顿。v0.8.4 在数据库或工作区真正变化时才失效此缓存。
        """
        if self._library_picker_cache is not None:
            return list(self._library_picker_cache)
        output: dict[str, Sample] = {}
        try:
            for saved, stored_samples in self.store.library_contents():
                canonical_path = str(Path(saved.path).resolve())
                for sample in stored_samples:
                    sample = replace(sample, source_file=canonical_path)
                    output[sample_key(sample)] = sample
        except Exception:
            pass
        for sample in self.samples:
            output.setdefault(sample_key(sample), sample)
        # Runtime metadata edits (not yet committed to the formal library) override the
        # snapshot while preserving the original file and database until an explicit save.
        for key, sample in self.sample_runtime_overrides.items():
            if key in output:
                output[key]=sample
        self._library_picker_cache=tuple(output.values())
        return list(self._library_picker_cache)

    def _expand_workspace_inputs(self, paths, include_excel=True):
        """Expand files/folders into a deterministic list of importable workspace files.

        A folder means "open this QTX folder": all QTX/TXT files under it are collected
        recursively.  This lets a user open hundreds of split QTX files in one action instead
        of repeating File > Open for every file.
        """
        out=[]; seen=set()
        for raw in paths or []:
            if not raw:
                continue
            path=Path(raw)
            candidates=[]
            if path.is_dir():
                candidates=sorted((x for x in path.rglob('*') if x.is_file()), key=lambda x:str(x).lower())
            else:
                candidates=[path]
            for item in candidates:
                suf=item.suffix.lower()
                allowed=suf in {'.qtx','.txt','.cpx',WORKFILE_SUFFIX} or (include_excel and suf=='.xlsx')
                if not allowed:
                    continue
                try:key=str(item.resolve())
                except Exception:key=str(item)
                if key in seen:
                    continue
                seen.add(key); out.append(key)
        return out

    def _parse_external_samples(self, path):
        canonical=str(Path(path).resolve()); suffix=Path(canonical).suffix.lower()
        if suffix in {'.qtx','.txt'}:samples=list(parse_qtx_file(canonical))
        elif suffix=='.cpx':samples=list(parse_cpx_file(canonical).samples)
        elif suffix=='.xlsx':samples=list(import_excel_workbook(canonical).samples)
        else:raise ValueError('不支持的色样文件格式')
        return [replace(sm,source_file=canonical) for sm in samples]

    def _import_library_preview(self,paths):
        if not self.can_use('library_view'):return
        added=[]; errors=[]
        for path in self._expand_workspace_inputs(paths,include_excel=True):
            if Path(path).suffix.lower() not in {'.qtx','.txt','.cpx','.xlsx'}:continue
            try:
                samples=self._parse_external_samples(path)
                if not samples:raise ValueError('未找到色样')
                self.loaded_files[path]={'samples':samples,'saved':False,'customer':'临时导入'}
                added.append(path);self._remember_recent(path)
            except Exception as exc:errors.append(f'{Path(path).name}: {exc}')
        if added:
            self.refresh_file_list()
            for i in range(self.file_list.count()):
                item=self.file_list.item(i)
                if item.data(Qt.UserRole)==added[-1]:
                    self.file_list.setCurrentItem(item);break
            self.library_has_explicit_view=True;self.library_page=0;self._tiles_keys=None
            self.build_tiles()
            self.statusBar().showMessage(f'已导入 {len(added)} 个文件至色库临时预览；色库记录尚未改变',5000)
        if errors:QMessageBox.warning(self,'导入色库预览','\n'.join(errors[:12]))

    def open_qtx_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'打开 QTX 文件夹（自动包含子文件夹）','')
        if not folder:
            return
        paths=[p for p in self._expand_workspace_inputs([folder], include_excel=False) if Path(p).suffix.lower() in {'.qtx','.txt'}]
        if not paths:
            QMessageBox.information(self,'打开 QTX 文件夹','该文件夹及其子文件夹中没有找到 QTX/TXT 文件。')
            return
        self.open_workspace_paths(paths)

    def _prepare_workspace_import(self,path):
        """Parse one external file without touching any Qt widget.

        HF51 keeps the authoritative parser unchanged, but records real parse
        time and reuses immutable parsed Samples when the same unchanged file is
        reopened during the session.  This makes repeat opens near-instant while
        keeping a cold parse scientifically identical to Hotfix50.
        """
        full=str(Path(path).resolve()); suf=Path(full).suffix.lower()
        try:
            st=Path(full).stat(); signature=(int(st.st_size),int(st.st_mtime_ns))
        except Exception:
            signature=(0,0)
        cached=self._workspace_parse_cache.get(full)
        if cached and cached.get('signature')==signature:
            prepared=dict(cached['prepared'])
            prepared['parse_seconds']=0.0; prepared['cache_hit']=True
            return prepared

        started=time.perf_counter()
        if suf in {'.qtx','.txt'}:
            samples=[replace(x,source_file=full) for x in parse_qtx_file(full)]
            prepared={'kind':'QTX','path':full,'title':Path(full).stem,'samples':samples,'slots':None}
        elif suf=='.cpx':
            project=parse_cpx_file(full); samples=[replace(x,source_file=full) for x in project.samples]
            by_id={x.sample_id:x for x in samples}; slots=[None if x is None else by_id.get(x.sample_id,x) for x in project.slots]
            prepared={'kind':'CPX','path':full,'title':project.palette_name or Path(full).stem,'samples':samples,'slots':slots,
                    'columns':max(1,int(project.columns or 6)),
                    'rows':max(1,int(project.rows or math.ceil(max(1,len(project.slots))/max(1,int(project.columns or 6))))),
                    'tile_width':int(project.tile_width),'tile_height':int(project.tile_height),
                    'gap_x':int(project.gap_x),'gap_y':int(project.gap_y),
                    'illuminant':str(project.illuminant or 'D65'),'observer':int(project.observer or 10),
                    'source_snapshot_b64':str(project.source_snapshot_b64 or ''),'source_sha256':str(project.source_sha256 or ''),
                    'original_slot_indices':[(int((sm.raw or {}).get('CPX_SLOT_INDEX',-1)) if sm is not None else None) for sm in slots]}
        elif suf=='.xlsx':
            result=import_excel_workbook(full); samples=[replace(x,source_file=full) for x in result.samples]
            prepared={'kind':'Excel','path':full,'title':Path(full).stem,'samples':samples,'slots':None}
        elif suf==WORKFILE_SUFFIX:
            data=read_workfile(full)
            samples=[self._deserialize_sample(x) for x in data['samples']]
            slots=[self._deserialize_sample(x) if x is not None else None for x in data['slots']]
            prepared={'kind':'工作文件','path':full,'title':str(data.get('title') or Path(full).stem),'samples':samples,'slots':slots,
                    'source_path':str(data.get('source_path') or ''),'document_uid':str(data.get('document_uid') or ''),
                    'standard_key':data.get('standard_key'),'columns':max(1,int(data.get('columns',6))),
                    'rows':max(1,int(data.get('rows',1))),'auto_layout':bool(data.get('auto_layout',False)),
                    'card_size':data.get('card_size') or [148,170]}
        else:
            raise ValueError('不支持的色样文件格式')
        prepared['parse_seconds']=max(0.0,time.perf_counter()-started); prepared['cache_hit']=False
        # Workfiles can be edited/saved in-app; caching their deserialised state
        # across edits is less useful.  External measurement files are immutable
        # from this workflow and safe to reuse while their size/mtime is unchanged.
        if suf in {'.qtx','.txt','.cpx','.xlsx'}:
            self._workspace_parse_cache[full]={'signature':signature,'prepared':dict(prepared)}
        return prepared

    def _commit_workspace_import(self,prepared):
        """Create the existing workspace document from a parsed worker result."""
        kind=prepared['kind']; full=prepared['path']; samples=list(prepared.get('samples') or [])
        if kind in {'QTX','Excel'}:
            if not samples:return None
            self.loaded_files[full]={'samples':samples,'saved':False,'customer':'临时导入'}
        if kind=='QTX':
            doc=self.add_workspace_document(prepared['title'],samples,full,'QTX')
        elif kind=='CPX':
            # A CPX is opened in Palette Studio so its exact fixed layout and
            # blank slots remain meaningful.  Do not flatten it into the generic
            # sample browser.
            return self._open_cpx_prepared_as_color_card(prepared)
        elif kind=='Excel':
            doc=self.add_workspace_document(prepared['title'],samples,full,'Excel')
        elif kind=='工作文件':
            doc=self.add_workspace_document(prepared['title'],samples,prepared.get('source_path',''),'工作文件',slots=prepared.get('slots'))
            doc.project_path=full
            if prepared.get('document_uid'):doc.document_uid=prepared['document_uid']
            doc.standard_key=prepared.get('standard_key')
            doc.columns=max(1,int(prepared.get('columns',6))); doc.rows=max(1,int(prepared.get('rows',1)))
            doc.auto_layout=bool(prepared.get('auto_layout',False))
            size=prepared.get('card_size') or [148,170]; doc.card_size=QSize(max(90,int(size[0])),max(90,int(size[1])))
            doc.reload_items(); doc.apply_layout()
        else:
            return None
        self._remember_recent(full)
        return doc

    def _workspace_import_should_run_async(self,paths):
        """Use worker/progress mode only when waiting would be noticeable."""
        total=0
        for path in paths or []:
            try:total += Path(path).stat().st_size
            except Exception:pass
        return len(paths or [])>=8 or total>=512*1024

    def _start_workspace_import_job(self,paths,target_document=None):
        if self._workspace_import_state is not None:
            QMessageBox.information(self,'正在导入','已有大文件导入任务正在进行，请等待当前任务完成或取消。'); return
        queue=list(paths or [])
        if not queue:return
        dlg=LargeFileImportDialog(self)
        state={'queue':queue,'total':len(queue),'done':0,'errors':[],'cancelled':False,'future':None,'current':None,'dialog':dlg,
               'target_document':target_document,'prepared_results':[]}
        self._workspace_import_state=state
        dlg.cancelRequested.connect(self._cancel_workspace_import_job)
        self._start_next_workspace_import_file()

    def _cancel_workspace_import_job(self):
        state=self._workspace_import_state
        if state is None:return
        state['cancelled']=True
        try:
            if state.get('future') is not None:state['future'].cancel()
        except Exception:pass

    def _start_next_workspace_import_file(self):
        state=self._workspace_import_state
        if state is None:return
        if state['cancelled'] or not state['queue']:
            self._finish_workspace_import_job(); return
        path=state['queue'].pop(0); state['current']=path
        # Hotfix52: paint the progress surface before starting the CPU-heavy
        # parser.  A ThreadPoolExecutor keeps the main Qt loop separate, but
        # QTX parsing is still Python-heavy and can briefly contend for the
        # GIL.  Starting the worker in the same event turn as show() meant
        # Windows occasionally displayed an unpainted white dialog.
        state['dialog'].begin_file(path,state['done']+1,state['total'])
        QTimer.singleShot(80,self._launch_current_workspace_import)

    def _launch_current_workspace_import(self):
        state=self._workspace_import_state
        if state is None or state.get('cancelled'):
            if state is not None:self._finish_workspace_import_job()
            return
        path=state.get('current')
        if not path:return
        dlg=state.get('dialog')
        try:
            if dlg is not None:
                dlg.ensurePolished()
                if dlg.layout() is not None:dlg.layout().activate()
                dlg.repaint()
                # Flush the first paint before the parser can contend for the
                # Python GIL.  This is deliberately done only once per file,
                # not in the polling loop.
                QApplication.processEvents()
            state['future']=self._import_executor.submit(self._prepare_workspace_import,path)
        except Exception as exc:
            state['errors'].append(f'{Path(path).name}: {exc}')
            if dlg is not None:dlg.failed(str(exc))
            state['done']+=1; state['future']=None; state['current']=None
            QTimer.singleShot(350,self._start_next_workspace_import_file)
            return
        QTimer.singleShot(60,self._poll_workspace_import_job)

    def _poll_workspace_import_job(self):
        state=self._workspace_import_state
        if state is None:return
        dlg=state['dialog']; dlg.tick(); future=state.get('future')
        if future is None:
            self._start_next_workspace_import_file(); return
        if not future.done():
            QTimer.singleShot(60,self._poll_workspace_import_job); return
        path=state.get('current') or ''
        try:
            prepared=future.result()
            count=len(prepared.get('samples') or [])
            if not state['cancelled']:
                dlg.parsed(count)
                # Flush the progress text before potentially creating thousands
                # of list items.  WorkspaceDocument itself now uses batched Qt
                # layout, so this stage should remain much shorter than Hotfix29.
                QApplication.processEvents()
                if state.get('target_document') is None:
                    build_started=time.perf_counter(); self._commit_workspace_import(prepared); build_seconds=max(0.0,time.perf_counter()-build_started)
                    parse_seconds=float(prepared.get('parse_seconds') or 0.0)
                    cache_note=' · 解析缓存命中' if prepared.get('cache_hit') else ''
                    self.statusBar().showMessage(f'已打开 {Path(path).name} · 解析 {parse_seconds:.2f}s · 界面 {build_seconds:.2f}s{cache_note}',6000)
                else:
                    state['prepared_results'].append(prepared)
                dlg.committed(count); QApplication.processEvents()
        except Exception as exc:
            state['errors'].append(f'{Path(path).name}: {exc}')
        state['done']+=1; state['future']=None; state['current']=None
        if state['cancelled']:
            self._finish_workspace_import_job(); return
        QTimer.singleShot(30,self._start_next_workspace_import_file)

    def _finish_workspace_import_job(self):
        state=self._workspace_import_state
        if state is None:return
        errors=list(state.get('errors') or []); cancelled=bool(state.get('cancelled'))
        target=state.get('target_document'); prepared_results=list(state.get('prepared_results') or [])
        dlg=state.get('dialog'); self._workspace_import_state=None
        if target is not None and not cancelled and prepared_results:
            try:target.append_prepared_imports(prepared_results)
            except Exception as exc:errors.append(f'加入当前窗口失败：{exc}')
        if self.loaded_files:
            self.rebuild_samples()
            self.refresh_file_list()
        if dlg is not None:
            dlg.finish(); dlg.deleteLater()
        if errors:QMessageBox.warning(self,'部分文件无法打开','\n'.join(errors[:20]))
        if cancelled:self.statusBar().showMessage('已取消大文件导入',3500)
        elif not errors:self.statusBar().showMessage('大文件导入完成',3500)

    def open_workspace_paths(self, paths):
        """Open files in the current workspace context without touching the formal library.

        If a temporary workspace folder is active, Open/drag means "add here" and
        the colour cards appear immediately. Otherwise each file opens as its own
        independent internal colour-data browser window.
        """
        current=self.workspace_tabs.currentWidget() if hasattr(self,'workspace_tabs') else None
        if isinstance(current,WorkspaceFolderDocument):
            current.add_paths(paths); return
        paths=self._expand_workspace_inputs(paths, include_excel=True)
        if self._workspace_import_should_run_async(paths):
            self._start_workspace_import_job(paths); return
        errors=[]
        for path in paths or []:
            try:
                prepared=self._prepare_workspace_import(path)
                self._commit_workspace_import(prepared)
            except Exception as exc:
                errors.append(f'{Path(path).name}: {exc}')
        if self.loaded_files:
            self.rebuild_samples();
            self.refresh_file_list()
        if errors:QMessageBox.warning(self,'部分文件无法打开','\n'.join(errors[:20]))


    def open_qtx_in_color_card(self):
        paths,_=QFileDialog.getOpenFileNames(self,'打开 QTX 到色卡编排','','QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)')
        if not paths:return
        sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
        win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
        if win is None:
            self.new_color_card()
            sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
            win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
        if win is not None:
            win.import_external_paths(paths)
            self.show_page(2)

    def open_cpx_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'打开一个或多个 CPX','','ChromaShare CPX (*.cpx *.CPX);;所有文件 (*)')
        if not paths:return
        errors=[]
        for path in paths:
            try:self.import_cpx_project(path)
            except Exception as exc:errors.append(f'{Path(path).name}: {exc}')
        if errors:QMessageBox.warning(self,'部分 CPX 无法打开','\n'.join(errors[:20]))

    def _open_cpx_prepared_as_color_card(self, prepared):
        full=str(Path(prepared.get('path') or '').resolve())
        samples=list(prepared.get('samples') or [])
        full_slots=list(prepared.get('slots') or [])
        columns=max(1,int(prepared.get('columns') or 1)); source_rows=max(1,int(prepared.get('rows') or math.ceil(max(1,len(full_slots))/columns)))
        source_total=columns*source_rows
        if len(full_slots)<source_total:full_slots.extend([None]*(source_total-len(full_slots)))
        elif len(full_slots)>source_total:
            source_rows=math.ceil(len(full_slots)/columns); source_total=columns*source_rows; full_slots.extend([None]*(source_total-len(full_slots)))

        # CPX has two geometries:
        #   1) source Fixed Layout (lossless round-trip contract), and
        #   2) presentation rectangle used on screen / visual exports.
        # Fold ONLY columns that are completely empty on the far right and rows
        # that are completely empty at the bottom.  Internal/leading blanks are
        # kept exactly where they are.  For the supplied 14×67 CPX this produces
        # a 10×10 presentation while source metadata remains 14×67.
        active_cols,visible_rows=cpx_active_geometry(full_slots,columns)
        visible_slots=cpx_project_active_slots(full_slots,columns,active_cols,visible_rows)

        existing={str(c.get('name','')).strip() for c in self.store.list_color_cards()}
        existing.update(str(w.card.get('name','')).strip() for w in getattr(self,'card_plan_windows',{}).values())
        base=str(prepared.get('title') or Path(full).stem).strip() or Path(full).stem; name=base; n=2
        while name in existing:name=f'{base} ({n})';n+=1
        tile_w=int(prepared.get('tile_width') or 84); tile_h=int(prepared.get('tile_height') or 34)
        gap_x=int(prepared.get('gap_x') or 20); gap_y=int(prepared.get('gap_y') or 22)
        cell_w=max(96,min(240,tile_w+gap_x)); cell_h=max(96,min(220,tile_h+gap_y+32))
        layout=[{'sample_key':sample_key(sm) if sm is not None else None} for sm in visible_slots]
        full_slot_keys=[sample_key(sm) if sm is not None else None for sm in full_slots]
        snapshot=str(prepared.get('source_snapshot_b64') or '')
        settings={
            'sort':'manual','illuminant':str(prepared.get('illuminant') or 'D65'),'observer':int(prepared.get('observer') or 10),
            'grid_cols':active_cols,'grid_rows':visible_rows,'auto_grid':False,'cell_w':cell_w,'cell_h':cell_h,
            'source_cpx':full,'cpx_tile_size':[tile_w,tile_h],'cpx_tile_gap':[gap_x,gap_y],
            'embedded_samples':[self._serialize_sample(s) for s in samples],
            'cpx_templates':{full:snapshot} if snapshot else {},
            'cpx_source_sha256':str(prepared.get('source_sha256') or ''),
            'cpx_original_palette_name':str(prepared.get('title') or Path(full).stem),
            'cpx_original_slot_indices':list(prepared.get('original_slot_indices') or [(int((sm.raw or {}).get('CPX_SLOT_INDEX',-1)) if sm is not None else None) for sm in full_slots]),
            'cpx_original_full_slot_keys':full_slot_keys,
            'cpx_original_cols':columns,'cpx_original_rows':source_rows,
            'cpx_active_cols':active_cols,'cpx_original_display_rows':visible_rows,
            'source_format':'CPX','source_read_only':True,
        }
        # The editor shows only the meaningful active portion of the source CPX.
        # Full source dimensions remain in settings and are restored on CPX export.
        card={'card_id':str(uuid.uuid4()),'name':name,'customer':'CPX','columns_count':active_cols,'layout':layout,'settings':settings,'_external_source':True}
        self.active_color_card_id=card['card_id']; self.show_page(2); self._open_color_card_object(card)
        self._remember_recent(full)
        hidden_tail=max(0,source_rows-visible_rows)
        self.statusBar().showMessage(
            f'已打开 CPX：{name} · {len(samples)} 色样 · 源版式 {columns}×{source_rows} · 当前显示 {active_cols}列×{visible_rows}行'
            + (f'（尾部 {hidden_tail} 个全空行已折叠，导出 CPX 时仍原样保留）' if hidden_tail else ''),8500)
        return card

    def import_cpx_project(self,path):
        project=parse_cpx_file(path); full=str(Path(path).resolve())
        samples=[replace(x,source_file=full) for x in project.samples]
        by_id={x.sample_id:x for x in samples}; slots=[None if x is None else by_id.get(x.sample_id,x) for x in project.slots]
        prepared={
            'kind':'CPX','path':full,'title':project.palette_name or Path(full).stem,'samples':samples,'slots':slots,
            'columns':project.columns,'rows':project.rows,'tile_width':project.tile_width,'tile_height':project.tile_height,
            'gap_x':project.gap_x,'gap_y':project.gap_y,'illuminant':project.illuminant,'observer':project.observer,
            'source_snapshot_b64':project.source_snapshot_b64,'source_sha256':project.source_sha256,
            'original_slot_indices':[(int((sm.raw or {}).get('CPX_SLOT_INDEX',-1)) if sm is not None else None) for sm in slots],
        }
        return self._open_cpx_prepared_as_color_card(prepared)

    def quick_import_qtx(self):
        """Open QTX directly into the unified workspace."""
        self.open_files()

    def open_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"打开一个或多个 QTX（可 Ctrl/Shift 多选）","","QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)")
        if paths: self.open_workspace_paths(paths)

    def open_workfiles(self):
        paths,_=QFileDialog.getOpenFileNames(self,'打开个人工作文件',str(self.personal_workfile_root),'Chromatic 工作文件 (*.chromatic)')
        if paths:
            self.audit('OPEN_PERSONAL_WORKFILE',';'.join(paths),str(len(paths)))
            self.open_workspace_paths(paths)

    def open_excel_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"打开 Excel","","Excel 文件 (*.xlsx)")
        if not paths:return
        current=self.workspace_tabs.currentWidget() if hasattr(self,'workspace_tabs') else None
        if isinstance(current,WorkspaceFolderDocument):current.add_paths(paths)
        else:self._open_excel_paths(paths)

    def _open_excel_paths(self, paths):
        if not paths:return
        messages=[]; errors=[]
        for path in paths:
            try:
                result=import_excel_workbook(path)
                if not result.samples:raise ValueError('没有识别到可导入的光谱色样')
                full=str(Path(path).resolve()); samples=[replace(x,source_file=full) for x in result.samples]
                self.loaded_files[full]={'samples':samples,'saved':False,'customer':'临时导入'}
                self.add_workspace_document(Path(path).stem,samples,full,'Excel')
                self._remember_recent(full)
                warning=f'；{len(result.warnings)} 项需核对' if result.warnings else ''
                messages.append(f'{Path(path).name}：打开 {len(samples)} 个色样{warning}')
            except Exception as exc:errors.append(f'{Path(path).name}：{exc}')
        if messages:self.statusBar().showMessage('；'.join(messages),6000)
        if self.loaded_files:self.rebuild_samples()
        if errors:QMessageBox.warning(self,'Excel 打开结果','\n'.join(errors[:20]))


    def refresh_file_list(self, refresh_navigation=True):
        """左侧只显示本次导入/当前工作区 QTX；正式数据库通过客户筛选浏览。"""
        selected = self.file_list.currentItem().data(Qt.UserRole) if self.file_list.currentItem() else None
        self.file_list.clear()
        for path, info in self.loaded_files.items():
            mark = f"已保存 · {info['customer']}" if info.get("saved") else "临时"
            item = QListWidgetItem(f"{Path(path).name}\n{mark} · {len(info['samples'])} 个色样")
            item.setData(Qt.UserRole, path); item.setToolTip(path); self.file_list.addItem(item)
            if path == selected: self.file_list.setCurrentItem(item)
        if refresh_navigation:self.update_customers()
        if hasattr(self,'library_sidebar_search'):self._filter_library_sidebar(self.library_sidebar_search.text())
        self._refresh_official_sidebar()

    def current_file(self):
        item = self.file_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def selected_file_paths(self):
        """Return unique QTX paths selected in the left workspace, preserving list order."""
        paths = []
        seen = set()
        for item in self.file_list.selectedItems():
            path = item.data(Qt.UserRole)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        if not paths:
            path = self.current_file()
            if path:
                paths = [path]
        return paths

    def file_selected(self, *_):
        path = self.current_file()
        if path: self.library_has_explicit_view=True
        # 左侧 QTX 与右侧“客户筛选”是两种独立浏览入口。
        # 用户点击具体 QTX 时，明确切换为“查看这个文件”，避免被客户筛选条件挡住。
        if path and hasattr(self, "customer"):
            self.customer.blockSignals(True)
            self.customer.setCurrentText("全部客户")
            self.customer.blockSignals(False)
        if path and path not in self.loaded_files:
            # 点击本地色库中的条目时，从 SQLite 快照恢复到当前右侧卡片区。
            saved = next((row for row in self.store.list_files() if row.path == path), None)
            if saved:
                samples = self.store.load_samples(path)
                if samples:
                    canonical_path = str(Path(path).resolve())
                    samples = [replace(s, source_file=canonical_path) for s in samples]
                    self.loaded_files[path] = {
                        "samples": samples, "saved": True, "customer": saved.customer,
                    }
                    self.rebuild_samples()
                else:
                    QMessageBox.warning(self, "无法打开色库文件", "该记录没有保存色样数据。请重新导入并点击【保存】。")
        self.hidden_cards.clear()
        self.detail.hide()
        self.library_page=0
        self._tiles_keys = None
        self.build_tiles()

    def _choose_customer_dialog(self, title="发布到色库", current="", official=False):
        """Unified two-stage destination chooser used by both library types.

        Hotfix30 keeps the existing destination semantics but replaces the old
        compact combo+line-edit stack with the approved two-card design:
        choose an existing destination first, then optionally create a top-level
        library/customer or a child category.  The live path preview prevents
        accidental saves into the wrong hierarchy.
        """
        if not self.require_admin(title):
            return None
        dlg=QDialog(self); dlg.setWindowTitle(title); dlg.setObjectName('libraryDestinationDialog'); dlg.setMinimumWidth(560)
        root=QVBoxLayout(dlg); root.setContentsMargins(20,18,20,16); root.setSpacing(12)

        heading=QLabel('发布到官方色库' if official else '发布到正式色库'); heading.setObjectName('sectionTitle'); root.addWidget(heading)
        intro=QLabel('先选择已有位置；如需新增顶级目录或子分类，可在第二步填写。')
        intro.setObjectName('muted'); intro.setWordWrap(True); root.addWidget(intro)

        # Section 1 — existing destination.
        choose_card=QFrame(); choose_card.setStyleSheet('QFrame{background:#F5F9FF;border:1px solid #DCEAFF;border-radius:10px;}')
        cv=QVBoxLayout(choose_card); cv.setContentsMargins(14,12,14,12); cv.setSpacing(7)
        ct=QLabel('1. 选择已有官方色库 / 分类' if official else '1. 选择已有客户 / 分类')
        ct.setStyleSheet('font-weight:700;color:#24476F;background:transparent;border:0;'); cv.addWidget(ct)
        ch=QLabel('选择现有目录后，可直接保存；也可以在下一步继续创建子分类。')
        ch.setObjectName('muted'); ch.setWordWrap(True); ch.setStyleSheet('background:transparent;border:0;'); cv.addWidget(ch)
        if official:
            raw=[x for x in self.store.customers() if str(x).startswith('官方色库/')]
            choices=sorted({str(x).split('/',1)[1] for x in raw if '/' in str(x) and str(x).split('/',1)[1]},key=str.casefold)
            first='— 请选择已有官方色库 / 分类 —'
            placeholder='输入新官方色库或子分类名称（可留空）'
        else:
            choices=sorted({str(x) for x in self.store.customers() if str(x)!='官方色库' and not str(x).startswith('官方色库/')},key=str.casefold)
            first='— 请选择已有客户 / 分类 —'
            placeholder='输入新客户或子分类名称（可留空）'
        existing=QComboBox(); existing.setMinimumHeight(36); existing.addItem(first); existing.addItems(choices); cv.addWidget(existing)
        root.addWidget(choose_card)

        # Section 2 — optional new top-level or child destination.
        create_card=QFrame(); create_card.setStyleSheet('QFrame{background:#F7FBF7;border:1px solid #DDEEDF;border-radius:10px;}')
        nv=QVBoxLayout(create_card); nv.setContentsMargins(14,12,14,12); nv.setSpacing(7)
        nt=QLabel('2. 新建官方色库 / 子分类（可选）' if official else '2. 新建客户 / 子分类（可选）')
        nt.setStyleSheet('font-weight:700;color:#315C38;background:transparent;border:0;'); nv.addWidget(nt)
        nh=QLabel('未选择已有目录时，将创建顶级目录；已选择时，则在该目录下创建子分类。')
        nh.setObjectName('muted'); nh.setWordWrap(True); nh.setStyleSheet('background:transparent;border:0;'); nv.addWidget(nh)
        child=QLineEdit(); child.setMinimumHeight(36); child.setPlaceholderText(placeholder); child.setClearButtonEnabled(True); nv.addWidget(child)
        root.addWidget(create_card)

        preview_frame=QFrame(); preview_frame.setStyleSheet('QFrame{background:#FFFFFF;border:1px solid #E5E7EB;border-radius:9px;}')
        pv=QHBoxLayout(preview_frame); pv.setContentsMargins(12,9,12,9)
        ptitle=QLabel('保存位置'); ptitle.setObjectName('muted'); pv.addWidget(ptitle)
        preview=QLabel('—'); preview.setStyleSheet('font-weight:700;color:#2563EB;background:transparent;border:0;'); preview.setWordWrap(True); pv.addWidget(preview,1)
        root.addWidget(preview_frame)

        actions=QHBoxLayout(); actions.addStretch(1)
        cancel=QPushButton('取消'); save=QPushButton('保存'); save.setObjectName('primaryButton'); save.setDefault(True)
        actions.addWidget(cancel); actions.addWidget(save); root.addLayout(actions)
        cancel.clicked.connect(dlg.reject); save.clicked.connect(dlg.accept)

        def upd(*_):
            base=existing.currentText() if existing.currentIndex()>0 else ''
            sub=child.text().strip()
            relative=(base.rstrip('/')+'/'+sub).strip('/') if base and sub else (sub or base)
            full=('官方色库/'+relative if official and relative else relative)
            preview.setText(full or '—')
            save.setEnabled(bool(relative))
        existing.currentIndexChanged.connect(upd); child.textChanged.connect(upd)
        if current and current not in {'临时导入','未分类'}:
            normalized=str(current)
            if official and normalized.startswith('官方色库/'):
                normalized=normalized.split('/',1)[1]
            idx=existing.findText(normalized)
            if idx>=0:existing.setCurrentIndex(idx)
        upd()
        if dlg.exec()!=QDialog.Accepted:
            return None
        base=existing.currentText() if existing.currentIndex()>0 else ''
        sub=child.text().strip()
        relative=(base.rstrip('/')+'/'+sub).strip('/') if base and sub else (sub or base)
        if not relative:
            return None
        name=('官方色库/'+relative if official else relative)
        self.store.save_customer_group(name)
        return name

    def save_dropped_samples_to_formal_library(self,keys):
        """Admin-only copy from any colour workspace into the formal library."""
        if not self.require_admin('拖入正式色库'):return False
        samples=[]; seen=set()
        for key in keys:
            sm=self._sample_by_any_key(key)
            if sm is not None and sample_key(sm) not in seen:samples.append(sm);seen.add(sample_key(sm))
        if not samples:return False
        customer=self._choose_customer_dialog(f'发布 {len(samples)} 个色样到正式色库')
        if not customer:return False
        folder=self.store.customer_data_root
        for part in customer.replace('\\','/').split('/'):
            if part.strip():folder=folder/self.store._safe_component(part)
        folder.mkdir(parents=True,exist_ok=True)
        base='拖入色卡_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); path=folder/(base+'.qtx'); suffix=2
        while path.exists():path=folder/(f'{base}_{suffix}.qtx');suffix+=1
        export_qtx_file(path,samples); self.store.save_file(str(path),customer,samples)
        self.auth_store.log(self.current_user.username,'DROP_TO_LIBRARY',customer,str(len(samples)))
        self._library_picker_cache=None; self._tiles_keys=None; self.refresh()
        self.statusBar().showMessage(f'已发布 {len(samples)} 个色样到正式色库：{customer}',5000)
        return True

    def save_selected_file(self):
        if not self.require_admin('发布到正式色库'): return
        selected_paths=self.selected_file_paths()
        path=self.current_file()
        if not selected_paths:
            QMessageBox.information(self,"发布到色库","请先在左侧选择一个或多个 QTX 文件。")
            return
        if len(selected_paths) > 1:
            customer=self._choose_customer_dialog(f"批量发布 {len(selected_paths)} 个 QTX 到色库")
            if not customer: return
            ok=0
            for pth in selected_paths:
                info=self.loaded_files.get(pth)
                if not info: continue
                self.store.save_file(pth,customer,info["samples"]); info.update(saved=True,customer=customer); ok+=1
            self.store.save_order(self.manual_order); self.refresh_file_list(); self.refresh()
            self.auth_store.log(self.current_user.username,'SAVE_LIBRARY_BATCH',customer,str(ok)); self.statusBar().showMessage(f"已将 {ok} 个 QTX 发布到正式色库：{customer}",5000)
            return
        path=selected_paths[0]
        info=self.loaded_files.get(path)
        if info is None:
            samples=self.store.load_samples(path)
            if not samples:
                QMessageBox.warning(self,"发布到色库","没有找到可发布的色样数据。")
                return
            info={"samples":samples,"saved":True,"customer":"未分类"}; self.loaded_files[path]=info
        customer=self._choose_customer_dialog("发布到正式色库", info.get("customer",""))
        if not customer: return
        self.store.save_file(path,customer,info["samples"]); info.update(saved=True,customer=customer)
        self.auth_store.log(self.current_user.username,'SAVE_LIBRARY',customer,Path(path).name)
        self.store.save_order(self.manual_order); self.refresh_file_list(); self.refresh()
        self.statusBar().showMessage(f"{Path(path).name} 已发布到正式色库：{customer}",5000)

    def show_file_menu(self, pos):
        item=self.file_list.itemAt(pos)
        if not item:
            menu=QMenu(self); oq=menu.addAction("打开 QTX…    Ctrl+O"); oqf=menu.addAction("打开 QTX 文件夹…    Ctrl+Shift+Q"); oe=menu.addAction("打开 Excel…"); ocpx=menu.addAction("打开 CPX 色卡方案…")
            if self.file_list.count(): menu.addSeparator(); allact=menu.addAction("全选    Ctrl+A")
            else: allact=None
            chosen=menu.exec(self.file_list.viewport().mapToGlobal(pos))
            if chosen==oq: self.open_files()
            elif chosen==oqf: self.open_qtx_folder()
            elif chosen==oe: self.open_excel_files()
            elif chosen==ocpx: self.open_cpx_files()
            elif allact is not None and chosen==allact: self.file_list.selectAll()
            return
        if not item.isSelected():
            self.file_list.clearSelection(); item.setSelected(True)
        self.file_list.setCurrentItem(item, QItemSelectionModel.NoUpdate); item.setSelected(True); path=item.data(Qt.UserRole)
        info=self.loaded_files.get(path)
        saved_paths={x.path for x in self.store.list_files()}
        saved=bool(info and info.get("saved")) or path in saved_paths
        menu=QMenu(self)
        selected_paths=self.selected_file_paths()
        selected_count=len(selected_paths)
        saved_count=sum(1 for p in selected_paths if bool(self.loaded_files.get(p, {}).get("saved")) or p in saved_paths)
        if selected_count > 1:
            head=menu.addAction(f"已选中 {selected_count} 个 QTX"); head.setEnabled(False); menu.addSeparator()
        # 像文件管理器一样：先是“打开/分析”，再是“保存/移动”，最后才是移除/删除。
        a_open=menu.addAction("打开") if selected_count==1 else None
        a_find=menu.addAction("查色") if selected_count==1 else None
        menu.addSeparator()
        a_save=menu.addAction("发布到色库…" if selected_count <= 1 else f"发布 {selected_count} 个 QTX 到色库…"); a_save.setVisible(self.is_admin)
        a_move=None
        if saved_count>0:
            a_move=menu.addAction("移动到客户…" if selected_count <= 1 else f"移动 {saved_count} 个已保存 QTX 到客户…"); a_move.setVisible(self.is_admin)
        menu.addSeparator()
        a_remove=menu.addAction("从工作区移除    Delete" if selected_count <= 1 else f"从工作区移除 {selected_count} 个 QTX    Delete")
        a_delete=None
        if saved_count>0:
            a_delete=menu.addAction("从色库删除 QTX…" if selected_count <= 1 else f"从色库删除 {saved_count} 个 QTX…"); a_delete.setVisible(self.is_admin)
        menu.addSeparator(); a_all=menu.addAction("全选    Ctrl+A")
        chosen=menu.exec(self.file_list.viewport().mapToGlobal(pos))
        if a_open is not None and chosen==a_open: self.file_selected()
        elif a_find is not None and chosen==a_find:
            self.add_find_qtx_files([path]); self.show_find_page()
        elif chosen==a_save: self.save_selected_file()
        elif a_move is not None and chosen==a_move: self.change_customer()
        elif chosen==a_remove: self.remove_selected_file()
        elif a_delete is not None and chosen==a_delete: self.delete_from_database()
        elif chosen==a_all: self.file_list.selectAll()

    def remove_selected_file(self):
        paths = self.selected_file_paths()
        if not paths:
            return
        # 只从“当前工作区”移除，不删除数据库记录，也不删除电脑原 QTX。
        self.file_list.blockSignals(True)
        try:
            self.file_list.clearSelection()
            self.file_list.setCurrentItem(None)
        finally:
            self.file_list.blockSignals(False)
        removed = 0
        for path in paths:
            if self.loaded_files.pop(path, None) is not None:
                removed += 1
        self.rebuild_samples()
        self.refresh_file_list()
        self.refresh()
        self.statusBar().showMessage(f"已从当前工作区移除 {removed} 个 QTX；电脑原文件和已保存色库数据未删除", 6000)

    def unsave_selected_file(self):
        if not self.require_admin('取消正式色库保存'): return
        path = self.current_file()
        if not path:
            return
        self.store.remove_file(path)
        self.loaded_files[path].update(saved=False, customer="临时导入")
        self.refresh_file_list()
        self.refresh()
        self.statusBar().showMessage("已取消保存；文件仍保留在当前工作区和电脑原位置")

    def delete_from_database(self):
        if not self.require_admin('从色库删除 QTX'): return
        paths = self.selected_file_paths()
        if not paths:
            QMessageBox.information(self, "从色库删除", "请先在左侧选择一个或多个 QTX。")
            return
        saved_rows = {row.path: row for row in self.store.list_files()}
        saved_paths = [p for p in paths if bool(self.loaded_files.get(p, {}).get("saved")) or p in saved_rows]
        if not saved_paths:
            QMessageBox.information(self, "从色库删除", "选中的 QTX 都还没有保存到正式色库。")
            return
        preview = "\n".join(f"• {Path(p).name}" for p in saved_paths[:8])
        if len(saved_paths) > 8:
            preview += f"\n… 另有 {len(saved_paths)-8} 个"
        unsaved_count = len(paths) - len(saved_paths)
        extra = f"\n\n另外 {unsaved_count} 个未保存 QTX 不会受影响。" if unsaved_count else ""
        reply = QMessageBox.question(
            self, "从色库批量删除" if len(saved_paths)>1 else "从色库删除",
            f"确定从色库中删除以下 {len(saved_paths)} 个 QTX 吗？\n\n{preview}"
            f"{extra}\n\n电脑上的原 QTX 文件不会删除。",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        deleted = 0
        for path in saved_paths:
            self.store.remove_file(path)
            info = self.loaded_files.get(path)
            if info is not None:
                info.update(saved=False, customer="临时导入")
            deleted += 1
        self.refresh_file_list()
        self.refresh()
        self.auth_store.log(self.current_user.username,'DELETE_LIBRARY','',str(deleted)); self.statusBar().showMessage(f"已从正式色库删除 {deleted} 个 QTX；电脑原文件未删除", 6000)

    def change_customer(self):
        if not self.require_admin('移动正式色库数据'): return
        paths = self.selected_file_paths()
        if not paths:
            return
        saved_rows = {row.path: row for row in self.store.list_files()}
        saved_paths = [p for p in paths if bool(self.loaded_files.get(p, {}).get("saved")) or p in saved_rows]
        if not saved_paths:
            QMessageBox.information(self, "客户分类", "选中的 QTX 都还没有保存到正式色库。")
            return
        current = ""
        if len(saved_paths) == 1:
            current = self.loaded_files.get(saved_paths[0], {}).get("customer", "")
        customer = self._choose_customer_dialog(
            "移动到客户" if len(saved_paths)==1 else f"批量移动 {len(saved_paths)} 个 QTX 到客户", current)
        if not customer:
            return
        moved = 0
        for path in saved_paths:
            info = self.loaded_files.get(path)
            samples = info.get("samples") if info else self.store.load_samples(path)
            if not samples:
                continue
            self.store.save_file(path, customer, samples)
            if info is not None:
                info["saved"] = True
                info["customer"] = customer
            moved += 1
        self.refresh_file_list(); self.refresh()
        self.auth_store.log(self.current_user.username,'MOVE_LIBRARY',customer,str(moved)); self.statusBar().showMessage(f"已将 {moved} 个 QTX 移动到客户：{customer}", 5000)

    @profiled("main.refresh_library_customer_tree")
    def refresh_library_customer_tree(self, snapshot=None):
        if not hasattr(self,'library_customer_tree'):return
        if snapshot is None:
            snapshot=self.store.library_navigation_snapshot()
        all_counts, groups, saved_rows = snapshot
        self.library_customer_tree.clear()

        # Hotfix30: the official library is a folder root, not a real customer.
        # It must not show a sample count and must not open as its own data tab.
        # Real libraries (Coloro/Pantone/...) are children below that root.
        if self.library_scope=='官方色库':
            root=QTreeWidgetItem(['官方色库'])
            root.setData(0,Qt.UserRole,'')
            root.setData(0,Qt.UserRole+2,'official-root')
            root.setIcon(0,self.style().standardIcon(QStyle.SP_DirIcon))
            font=root.font(0); font.setBold(True); root.setFont(0,font)
            # Keep it clickable only as an expand/collapse folder, never as a
            # selectable customer that can create a tab.
            root.setFlags((root.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsSelectable)
            self.library_customer_tree.addTopLevelItem(root)

            counts={}
            for name,value in all_counts.items():
                name=str(name or '').replace('\\','/').strip('/')
                if name.startswith('官方色库/'):
                    rel=name.split('/',1)[1].strip('/')
                    if rel:counts['官方色库/'+rel]=value
            for group in groups:
                group=str(group or '').replace('\\','/').strip('/')
                if group.startswith('官方色库/'):
                    rel=group.split('/',1)[1].strip('/')
                    if rel:counts.setdefault('官方色库/'+rel,0)

            nodes={}; customer_nodes={}
            for customer in sorted(counts,key=str.casefold):
                relative=customer.split('/',1)[1] if '/' in customer else customer
                parts=[p.strip() for p in relative.split('/') if p.strip()]
                if not parts:continue
                parent=root; rel_acc=[]
                for part in parts:
                    rel_acc.append(part); rel_full='/'.join(rel_acc)
                    node=nodes.get(rel_full)
                    if node is None:
                        node=QTreeWidgetItem(parent,[part]); node.setIcon(0,self.style().standardIcon(QStyle.SP_DirIcon))
                        node.setData(0,Qt.UserRole,'官方色库/'+rel_full)
                        parent.addChild(node); nodes[rel_full]=node
                    parent=node
                count=int(counts.get(customer,0) or 0)
                parent.setText(0,f"{parts[-1]}   ({count:,} 色样)" if count>0 else parts[-1])
                parent.setData(0,Qt.UserRole,customer); customer_nodes[customer]=parent

            for saved in saved_rows:
                customer=str(saved.customer or '').replace('\\','/').strip('/')
                if not customer.startswith('官方色库/'):continue
                parent=customer_nodes.get(customer)
                if parent is None:continue
                item=QTreeWidgetItem(parent,[Path(saved.path).name])
                item.setIcon(0,self.style().standardIcon(QStyle.SP_FileIcon))
                item.setData(0,Qt.UserRole,customer)
                item.setData(0,Qt.UserRole+1,str(Path(saved.path).resolve()))
                item.setToolTip(0,saved.path)
            root.setExpanded(True)
        else:
            counts={name:value for name,value in all_counts.items() if self._library_scope_contains(name)}
            for group in groups:
                if self._library_scope_contains(group):counts.setdefault(group,0)
            nodes={}; customer_nodes={}
            for customer in sorted(counts,key=str.casefold):
                parts=[p.strip() for p in str(customer).replace('\\','/').split('/') if p.strip()] or ['未分类']; parent=None; acc=[]
                for part in parts:
                    acc.append(part); full='/'.join(acc); key=(full,id(parent) if parent else 0); node=nodes.get(key)
                    if node is None:
                        node=QTreeWidgetItem([part]); node.setData(0,Qt.UserRole,full); node.setIcon(0,self.style().standardIcon(QStyle.SP_DirIcon))
                        (self.library_customer_tree.addTopLevelItem(node) if parent is None else parent.addChild(node)); nodes[key]=node
                    parent=node
                parent.setText(0,f"{parts[-1]}   ({counts[customer]:,} 色样)"); parent.setData(0,Qt.UserRole,customer)
                customer_nodes[customer]=parent
            for saved in saved_rows:
                if not self._library_scope_contains(saved.customer):continue
                parent=customer_nodes.get(saved.customer)
                if parent is None:continue
                item=QTreeWidgetItem(parent,[Path(saved.path).name]); item.setIcon(0,self.style().standardIcon(QStyle.SP_FileIcon))
                item.setData(0,Qt.UserRole,saved.customer)
                item.setData(0,Qt.UserRole+1,str(Path(saved.path).resolve()))
                item.setToolTip(0,saved.path)
            self.library_customer_tree.collapseAll()

        if hasattr(self,'library_sidebar_search'):self._filter_library_sidebar(self.library_sidebar_search.text())

    def library_customer_tree_clicked(self,item,column=0):
        if str(item.data(0,Qt.UserRole+2) or '')=='official-root':
            item.setExpanded(not item.isExpanded()); return
        path=item.data(0,Qt.UserRole+1)
        if path:
            path=str(path)
            try:
                if path not in self.loaded_files:
                    samples=[replace(sm,source_file=path) for sm in self.store.load_samples(path)]
                    self.loaded_files[path]={'samples':samples,'saved':True,'customer':str(item.data(0,Qt.UserRole) or '')}
                self.refresh_file_list(refresh_navigation=False)
                for i in range(self.file_list.count()):
                    row=self.file_list.item(i)
                    if row.data(Qt.UserRole)==path:
                        self.file_list.setCurrentItem(row); self.file_selected(); break
                self.library_has_explicit_view=True
                # file_selected() already resets paging and builds the current page.
                # Avoid a second synchronous tile pass on the same click.
            except Exception as exc:
                QMessageBox.warning(self,'打开客户 QTX',f'{Path(path).name}：{exc}')
            return
        customer=str(item.data(0,Qt.UserRole) or '').strip()
        if customer:self.open_library_customer(customer)

    def library_customer_tree_menu(self,pos):
        item=self.library_customer_tree.itemAt(pos); customer=str(item.data(0,Qt.UserRole) or '') if item else ''
        official_root=bool(item and str(item.data(0,Qt.UserRole+2) or '')=='official-root')
        menu=QMenu(self)
        path=str(item.data(0,Qt.UserRole+1) or '') if item else ''
        canonical=str(Path(path).resolve()) if path else ''
        favorite_files=self._nav_items('favorite_qtx')
        favorite=menu.addAction('从我的收藏移除该 QTX' if canonical in favorite_files else '收藏该 QTX') if canonical else None
        pinned=self._nav_items('common_customers')
        pin=menu.addAction('从常用色库移除' if customer in pinned else '加入常用色库')
        pin.setEnabled(bool(customer) and not official_root)
        if not self.is_admin:
            chosen=menu.exec(self.library_customer_tree.viewport().mapToGlobal(pos))
            if favorite is not None and chosen==favorite:self._toggle_nav_item('favorite_qtx',canonical)
            elif chosen==pin:self._toggle_nav_item('common_customers',customer)
            return
        menu.addSeparator()
        new_top=menu.addAction('新建官方色库…' if self.library_scope=='官方色库' else '新建客户…')
        new_child=menu.addAction('新建子分类…' if self.library_scope=='官方色库' else '新建子客户…'); new_child.setEnabled(bool(customer) or official_root)
        rename=menu.addAction('重命名…'); rename.setEnabled(bool(customer)); manage=menu.addAction('管理该客户 QTX…'); manage.setEnabled(bool(customer))
        menu.addSeparator(); delete_group=menu.addAction('删除客户 / 分类…'); delete_group.setEnabled(bool(customer))
        chosen=menu.exec(self.library_customer_tree.viewport().mapToGlobal(pos))
        if favorite is not None and chosen==favorite:self._toggle_nav_item('favorite_qtx',canonical)
        elif chosen==pin:self._toggle_nav_item('common_customers',customer)
        elif chosen in (new_top,new_child):
            caption='新建官方色库' if self.library_scope=='官方色库' else '新建客户'
            label='官方色库 / 分类名称：' if self.library_scope=='官方色库' else '客户名称：'
            name,ok=QInputDialog.getText(self,caption,label)
            if ok and name.strip():
                if self.library_scope=='官方色库':
                    base=customer if customer.startswith('官方色库/') else '官方色库'
                    full=(base.rstrip('/')+'/'+name.strip()) if chosen==new_child else ('官方色库/'+name.strip())
                else:
                    full=(customer.rstrip('/')+'/'+name.strip()) if chosen==new_child and customer else name.strip()
                self.store.save_customer_group(full); self.update_customers()
        elif chosen==rename and customer:
            name,ok=QInputDialog.getText(self,'重命名客户','新名称：',text=customer.split('/')[-1])
            if ok and name.strip():
                prefix=customer.rsplit('/',1)[0]+'/' if '/' in customer else ''; newbase=prefix+name.strip()
                for row in list(self.store.list_files()):
                    if row.customer==customer or row.customer.startswith(customer+'/'):
                        samples=self.store.load_samples(row.path); self.store.save_file(row.path,newbase+row.customer[len(customer):],samples)
                self.update_customers()
        elif chosen==manage and customer:
            CustomerQtxManagerDialog(self,customer,self).exec(); self.update_customers(); self._tiles_keys=None; self.build_tiles()
        elif chosen==delete_group and customer:
            affected=[row for row in self.store.list_files() if row.customer==customer or row.customer.startswith(customer+'/')]
            if affected:
                QMessageBox.information(self,'删除客户 / 分类',f'【{customer}】中仍有 {len(affected)} 个 QTX。\n\n为避免误删数据，请先在“管理该客户 QTX…”中移动或删除这些 QTX；空分类即可删除。')
                return
            if QMessageBox.question(self,'删除客户 / 分类',f'确定删除空客户/分类【{customer}】及其空子分类吗？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
                self.store.remove_customer_group(customer); self.update_customers(); self.statusBar().showMessage(f'已删除空客户/分类：{customer}',3500)

    def customer_filter_activated(self, _index=None):
        """从下拉框打开客户，但不关闭已经打开的其他客户标签。"""
        customer=self.customer.currentText().strip()
        if not customer or customer=="请选择客户":
            return
        self.open_library_customer(customer)
        # 下拉框只是“打开器”，打开后回到提示项，避免让它看起来像唯一当前筛选。
        self.customer.blockSignals(True); self.customer.setCurrentIndex(0); self.customer.blockSignals(False)

    def open_library_customer(self, customer: str):
        customer=(customer or '').strip()
        if not customer or customer=='请选择客户': return
        if self.library_scope=='官方色库' and customer in {'官方色库','全部客户'}:
            # Root is navigation only; never create a separate official-library tab.
            return
        if customer in self.minimized_library_customers:
            self.minimized_library_customers.remove(customer)

        # Performance hotfix: when the very first library tab is created, QTabBar
        # emits currentChanged immediately.  The original handler rebuilds all library
        # tiles there, and this method rebuilt them again a few lines later.  On a
        # large formal library that means the expensive snapshot/materialisation work
        # ran twice before the first window could finish opening.
        #
        # Block only the tab-change signal while creating/selecting the requested tab,
        # then perform the same single explicit refresh below.  No library data,
        # filtering, sorting, selection or customer-tab behaviour is changed.
        tabs=self.library_customer_tabs
        tabs.blockSignals(True)
        try:
            if customer not in self.open_library_customers:
                self.open_library_customers.append(customer)
                tab_text=(customer.split('/',1)[1] if self.library_scope=='官方色库' and customer.startswith('官方色库/') else customer)
                idx=tabs.addTab(tab_text)
                tabs.setTabToolTip(idx, f'客户：{customer}\n右键可收起、关闭或管理 QTX')
            idx=self.open_library_customers.index(customer)
            tabs.setCurrentIndex(idx)
        finally:
            tabs.blockSignals(False)

        self.active_library_customer=customer
        self.library_has_explicit_view=True
        self.file_list.blockSignals(True); self.file_list.setCurrentItem(None); self.file_list.clearSelection(); self.file_list.blockSignals(False)
        self.hidden_cards.clear(); self.detail.hide(); self.library_page=0; self._tiles_keys=None; self.build_tiles(); self._update_minimized_customer_button()

    def library_customer_tab_changed(self, index:int):
        if index<0 or index>=len(self.open_library_customers):
            self.active_library_customer=None
            self.library_has_explicit_view=False
        else:
            self.active_library_customer=self.open_library_customers[index]
            self.library_has_explicit_view=True
        if hasattr(self,'detail'): self.detail.hide()
        self._tiles_keys=None
        if hasattr(self,'tile_grid'): self.build_tiles()

    def close_library_customer_tab(self,index:int):
        if index<0 or index>=len(self.open_library_customers): return
        customer=self.open_library_customers.pop(index)
        self.library_customer_tabs.blockSignals(True); self.library_customer_tabs.removeTab(index); self.library_customer_tabs.blockSignals(False)
        if self.open_library_customers:
            new_index=min(index,len(self.open_library_customers)-1)
            self.library_customer_tabs.setCurrentIndex(new_index)
            self.active_library_customer=self.open_library_customers[new_index]
            self.library_has_explicit_view=True
        else:
            self.active_library_customer=None; self.library_has_explicit_view=False
        self.detail.hide(); self._tiles_keys=None; self.build_tiles()

    def minimize_library_customer_tab(self,index:int):
        if index<0 or index>=len(self.open_library_customers): return
        customer=self.open_library_customers[index]
        if customer not in self.minimized_library_customers: self.minimized_library_customers.append(customer)
        self.close_library_customer_tab(index)
        self._update_minimized_customer_button()

    def restore_library_customer(self,customer:str):
        if customer in self.minimized_library_customers: self.minimized_library_customers.remove(customer)
        self.open_library_customer(customer); self._update_minimized_customer_button()

    def _update_minimized_customer_button(self):
        if not hasattr(self,'minimized_customers_btn'): return
        n=len(self.minimized_library_customers); self.minimized_customers_btn.setText(f'已收起 {n}')
        self.minimized_customers_btn.setVisible(n>0)

    def show_minimized_customers_menu(self):
        if not self.minimized_library_customers: return
        menu=QMenu(self)
        for customer in list(self.minimized_library_customers):
            act=menu.addAction(f'恢复  {customer}')
            act.triggered.connect(lambda checked=False,c=customer:self.restore_library_customer(c))
        menu.addSeparator(); restore_all=menu.addAction('全部恢复')
        restore_all.triggered.connect(lambda: [self.restore_library_customer(c) for c in list(self.minimized_library_customers)])
        menu.exec(self.minimized_customers_btn.mapToGlobal(QPoint(0,self.minimized_customers_btn.height())))

    def library_customer_tab_menu(self,pos):
        idx=self.library_customer_tabs.tabAt(pos)
        if idx<0 or idx>=len(self.open_library_customers): return
        customer=self.open_library_customers[idx]
        menu=QMenu(self)
        manage=menu.addAction('管理该客户 QTX…') if self.is_admin else None
        menu.addSeparator(); minimize=menu.addAction('收起客户'); close=menu.addAction('关闭客户')
        chosen=menu.exec(self.library_customer_tabs.mapToGlobal(pos))
        if manage is not None and chosen==manage:
            CustomerQtxManagerDialog(self,customer,self).exec(); self.update_customers(); self._tiles_keys=None; self.build_tiles()
        elif chosen==minimize: self.minimize_library_customer_tab(idx)
        elif chosen==close: self.close_library_customer_tab(idx)

    def update_customers(self):
        self._library_data_revision += 1
        self._library_view_cache = None
        if hasattr(self,'_p3_full_sample_cache'):self._p3_full_sample_cache.clear()
        # 保存/移动/删除 QTX 后通常都会进入这里；同步让正式色库快照失效。
        self._library_picker_cache=None
        current = self.customer.currentText() if hasattr(self, "customer") else "请选择客户"
        try:
            nav_snapshot = self.store.library_navigation_snapshot()
            rows = nav_snapshot[2]
            db_customers = sorted({r.customer for r in rows})
            self._saved_customer_by_path = {
                (str(Path(r.path).resolve()) if '://' not in r.path else r.path): r.customer
                for r in rows
            }
        except Exception:
            nav_snapshot = None
            db_customers = []
            self._saved_customer_by_path = {}
        local_customers = {info["customer"] for info in self.loaded_files.values() if info["customer"] != "临时导入"}
        # 下拉只显示一级客户；子客户在打开一级客户后由客户页/管理器呈现。
        names = sorted({str(x).replace('\\','/').split('/')[0] for x in (set(db_customers) | local_customers) if str(x).strip()}, key=str.casefold)
        self.customer.blockSignals(True)
        self.customer.clear()
        self.customer.addItem("请选择客户")
        self.customer.addItem("全部客户")
        self.customer.addItems(names)
        self.customer.setCurrentText(current if current in ["请选择客户", "全部客户", *names] else "请选择客户")
        self.customer.blockSignals(False)
        if hasattr(self,'library_customer_tree'): self.refresh_library_customer_tree(nav_snapshot)
        if hasattr(self,'card_customer_tree'): self.refresh_card_customer_tree()
        if hasattr(self, "card_customer"):
            card_current = self.card_customer.currentText()
            self.card_customer.blockSignals(True)
            self.card_customer.clear(); self.card_customer.addItem("全部客户"); self.card_customer.addItems(names)
            self.card_customer.setCurrentText(card_current if card_current in ["全部客户", *names] else "全部客户")
            self.card_customer.blockSignals(False)
        if hasattr(self, "find_customer"):
            find_current=self.find_customer.currentText(); self.find_customer.blockSignals(True)
            self.find_customer.clear(); self.find_customer.addItem("全部客户"); self.find_customer.addItems(names)
            self.find_customer.setCurrentText(find_current if find_current in ["全部客户", *names] else "全部客户"); self.find_customer.blockSignals(False)

    def schedule_tile_rebuild(self, *_args):
        """合并短时间内连续的搜索/筛选变化，减轻大量色样时的卡顿。"""
        self.library_page=0
        self._tiles_keys = None
        if hasattr(self, "_tile_rebuild_timer"):
            self._tile_rebuild_timer.start()
        else:
            self.build_tiles()

    def toggle_find_panel(self, checked=None):
        show = self.find_toggle.isChecked() if checked is None else bool(checked)
        self.find_toggle.setChecked(show); self.find_panel.setVisible(show)
        if hasattr(self,'library_sort_widget'): self.library_sort_widget.setVisible(not show)
        if show:
            self.library_has_explicit_view=True; self.empty_label.setVisible(False)
        else:
            self.find_mode_active=False
        self.update_find_source_ui()

    def _find_scope_resources(self):
        paths=[]
        try:paths.extend(list(self.store.customer_counts()))
        except Exception:pass
        try:paths.extend(list(self.store.list_customer_groups()))
        except Exception:pass
        return sorted({str(x).replace('\\','/').strip('/') for x in paths if str(x).strip()},key=str.casefold)

    def _update_find_scope_button(self):
        if not hasattr(self,'find_scope_btn'):return
        sel=self.find_scope_selection
        if sel is None:
            text='全部授权数据 ▾'
        elif not sel:
            text='未选择数据源 ▾'
        elif len(sel)==1:
            name={'__official__':'官方色库','__formal__':'正式色库'}.get(sel[0],str(sel[0]).split('/')[-1])
            text=f'{name} ▾'
        else:
            text=f'已选 {len(sel)} 个范围 ▾'
        self.find_scope_btn.setText(text)

    def show_find_scope_dialog(self):
        dlg=FindScopeDialog(self._find_scope_resources(),self.find_scope_selection,self)
        if dlg.exec()!=QDialog.Accepted:return
        self.find_scope_selection=dlg.selection()
        session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
        if session is not None:
            session['scope_selection']=None if self.find_scope_selection is None else list(self.find_scope_selection)
            session['queried']=False; session['needs_query']=False; session['result_keys']=[]; session['scores']={}; session['per_standard']={}; session['selected_keys']=set()
        self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear(); self._tiles_keys=None
        self._update_find_scope_button()
        if hasattr(self,'find_result_summary'):self.find_result_summary.setText('查询结果：范围已更改，请重新查询')
        if hasattr(self,'find_tile_grid'):self.build_find_tiles()

    def _find_scope_queries(self):
        sel=self.find_scope_selection
        if sel is None:return [(None,None)]
        if not sel:return []
        out=[]
        for key in sel:
            if key=='__official__':out.append((None,'官方色库'))
            elif key=='__formal__':out.append((None,'正式色库'))
            else:out.append((str(key),None))
        return out

    def _find_condition_changed(self,*_args):
        session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
        if session is not None:
            session['illuminant']=self.find_illuminant.currentText() if hasattr(self,'find_illuminant') else 'D65'
            session['observer']=2 if hasattr(self,'find_observer') and self.find_observer.currentText()=='2°' else 10
            # Formula/light/observer belongs to this task. Any cached standard
            # result was computed under the previous condition, so invalidate all
            # per-standard snapshots even if the currently viewed one was unqueried.
            session['queried']=False; session['needs_query']=False; session['result_keys']=[]; session['scores']={}; session['selected_keys']=set(); session['per_standard']={}
            session['query_params']=self._find_query_params_from_ui()
        self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear()
        if hasattr(self,'find_result_summary'): self.find_result_summary.setText('查询结果：尚未查询')
        if hasattr(self,'find_tile_grid'): self.build_find_tiles()

    def _current_find_session(self):
        return next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)

    def _find_query_params_from_ui(self):
        # A Find task owns the controls that produced its cached result.  Do not
        # reuse the formal-library search box here: that is a separate workspace.
        return {
            'formula': self.find_formula.currentText() if hasattr(self,'find_formula') else 'CIEDE2000',
            'illuminant': self.find_illuminant.currentText() if hasattr(self,'find_illuminant') else 'D65',
            'observer': 2 if hasattr(self,'find_observer') and self.find_observer.currentText()=='2°' else 10,
            'de_min': self._optional_float(self.find_de_min) if hasattr(self,'find_de_min') else None,
            'de_max': self._optional_float(self.find_de_max) if hasattr(self,'find_de_max') else None,
            'qtx_keyword': self.find_name_keyword.currentText().strip() if hasattr(self,'find_name_keyword') else '',
            'limit': int(self.find_limit.value()) if hasattr(self,'find_limit') else 30,
            'scope_selection': None if self.find_scope_selection is None else list(self.find_scope_selection),
        }

    def _restore_find_query_params(self, session):
        params=dict((session or {}).get('query_params') or {})
        if not params:return
        for name,value in [
            ('find_formula',str(params.get('formula','CIEDE2000'))),
            ('find_illuminant',str(params.get('illuminant','D65'))),
            ('find_observer','2°' if int(params.get('observer',10))==2 else '10°'),
            ('find_name_keyword',str(params.get('qtx_keyword','') or '全部')),
        ]:
            box=getattr(self,name,None)
            if box is None:continue
            box.blockSignals(True);box.setCurrentText(value);box.blockSignals(False)
        if hasattr(self,'find_de_max'):
            self.find_de_max.blockSignals(True)
            value=params.get('de_max');self.find_de_max.setText('' if value is None else str(value))
            self.find_de_max.blockSignals(False)
        if hasattr(self,'find_limit'):
            self.find_limit.blockSignals(True);self.find_limit.setValue(max(1,min(500,int(params.get('limit',30)))));self.find_limit.blockSignals(False)
        self.find_scope_selection=None if params.get('scope_selection') is None else list(params.get('scope_selection') or [])
        self._update_find_scope_button()

    def _find_index_rows(self):
        revision=int(getattr(self,'_library_data_revision',0))
        if getattr(self,'_find_index_cache_revision',-1)!=revision:
            try:self._find_index_cache=list(self.store.sample_index())
            except Exception:self._find_index_cache=[]
            self._find_index_cache_revision=revision
        return self._find_index_cache

    @staticmethod
    def _find_customer_in_selection(customer, selection):
        if selection is None:return True
        if not selection:return False
        customer=str(customer or '')
        for key in selection:
            key=str(key)
            if key=='__official__' and (customer=='官方色库' or customer.startswith('官方色库/')):return True
            if key=='__formal__' and customer!='官方色库' and not customer.startswith('官方色库/'):return True
            if key not in {'__official__','__formal__'} and (customer==key or customer.startswith(key.rstrip('/')+'/')):return True
        return False

    def _find_candidate_index_rows(self, session):
        params=dict((session or {}).get('query_params') or self._find_query_params_from_ui())
        selection=params.get('scope_selection')
        keyword=str(params.get('qtx_keyword','') or '').strip().casefold()
        if keyword in {'全部','all'}:keyword=''
        hidden=set(getattr(self,'hidden_cards',set()) or set())
        out=[]
        for row in self._find_index_rows():
            key=str(row.get('sample_key') or '');lab=row.get('lab_d65_10')
            if not key or lab is None or key in hidden:continue
            if not self._find_customer_in_selection(row.get('customer',''),selection):continue
            if keyword and keyword not in Path(str(row.get('qtx_path') or '')).name.casefold():continue
            out.append(row)
        return out

    @staticmethod
    def _find_delta_many(standard_lab, labs, formula):
        formula=str(formula or 'CIEDE2000')
        if formula=='CIE94':return delta_e_many(standard_lab,labs,'CIE 1994',textiles=True)
        if formula=='ΔE*ab':return delta_e_many(standard_lab,labs,'CIE 1976')
        if formula=='CMC(2:1)':return delta_e_many(standard_lab,labs,'CMC',l=2,c=1)
        return delta_e_many(standard_lab,labs,'CIE 2000')

    def _find_alt_labs_by_key(self, keys, illuminant, observer):
        # Alternate illuminants genuinely need spectrum.  Hydrate only eligible
        # rows, then reuse the existing P3-6 batch E308 path by wavelength group.
        samples=self.store.load_samples_by_keys(keys);groups={};fallback=[];out={}
        for sm in samples:
            if not sm.has_spectrum():continue
            waves=tuple(sm.wavelengths)
            if len(waves)>=2:groups.setdefault(waves,[]).append(sm)
        for waves,members in groups.items():
            try:
                converted=reflectances_to_xyz_lab([sm.reflectance for sm in members],illuminant,waves,observer)
                for sm,(_xyz,lab) in zip(members,converted):out[sample_key(sm)]=lab
            except Exception:fallback.extend(members)
        for sm in fallback:
            try:out[sample_key(sm)]=reflectance_to_xyz_lab(sm.reflectance,illuminant,sm.wavelengths,observer)[1]
            except Exception:pass
        return out

    def _query_find_standard(self, session, standard, candidate_rows=None, alt_lab_by_key=None):
        # One task/standard writes only to its own per_standard cache.
        params=dict(session.get('query_params') or self._find_query_params_from_ui())
        source_rows=self._find_candidate_index_rows(session) if candidate_rows is None else candidate_rows
        rows=[r for r in source_rows if str(r.get('sample_key'))!=sample_key(standard)]
        illuminant=str(params.get('illuminant','D65'));observer=int(params.get('observer',10));formula=str(params.get('formula','CIEDE2000'))
        if display_illuminant(illuminant)=='D65' and observer==10:
            standard_lab=standard.lab_d65_10;labs=[r['lab_d65_10'] for r in rows]
            values=self._find_delta_many(standard_lab,labs,formula) if labs else []
        else:
            if not standard.has_spectrum():
                cache={'result_keys':[],'scores':{},'selected_keys':set(),'query_params':dict(params)}
                session.setdefault('per_standard',{})[sample_key(standard)]=cache;return cache
            try:standard_lab=reflectance_to_xyz_lab(standard.reflectance,illuminant,standard.wavelengths,observer)[1]
            except Exception:
                cache={'result_keys':[],'scores':{},'selected_keys':set(),'query_params':dict(params)}
                session.setdefault('per_standard',{})[sample_key(standard)]=cache;return cache
            lab_by_key=alt_lab_by_key if alt_lab_by_key is not None else self._find_alt_labs_by_key([str(r['sample_key']) for r in rows],illuminant,observer)
            rows=[r for r in rows if str(r['sample_key']) in lab_by_key];labs=[lab_by_key[str(r['sample_key'])] for r in rows]
            values=self._find_delta_many(standard_lab,labs,formula) if labs else []
        de_min=params.get('de_min');de_max=params.get('de_max');ranked=[]
        for row,de in zip(rows,values):
            try:value=float(de)
            except Exception:continue
            if not math.isfinite(value):continue
            if de_min is not None and value<float(de_min):continue
            if de_max is not None and value>float(de_max):continue
            ranked.append((value,str(row.get('display_name') or '').casefold(),str(row['sample_key'])))
        ranked.sort(key=lambda x:(x[0],x[1],x[2]));ranked=ranked[:max(1,min(500,int(params.get('limit',30))))]
        keys=[key for _de,_name,key in ranked];scores={key:de for de,_name,key in ranked}
        old=(session.get('per_standard') or {}).get(sample_key(standard)) or {}
        selected=set(old.get('selected_keys',set())).intersection(keys)
        cache={'result_keys':keys,'scores':scores,'selected_keys':selected,'query_params':dict(params)}
        session.setdefault('per_standard',{})[sample_key(standard)]=cache
        return cache

    def _find_current_cache(self, session=None, standard=None):
        session=session or self._current_find_session();standard=standard or self.find_standard
        if session is None or standard is None:return None
        return (session.get('per_standard') or {}).get(sample_key(standard))

    def _find_result_keys(self, session=None, standard=None):
        return list((self._find_current_cache(session,standard) or {}).get('result_keys',[]) or [])

    def _find_result_samples(self, full=False, session=None, standard=None):
        keys=self._find_result_keys(session,standard)
        if not keys:return []
        cache=self._find_current_cache(session,standard)
        try:
            if not full:
                return self.store.load_index_samples_by_keys(keys)
            # Result count is bounded by the user's limit.  Hydrate only those
            # final result rows once so fluorescent/out-of-gamut preview uses the
            # same measured spectrum as QTX import, details and palette cards.
            # This avoids the old D65/10 lightweight-vs-D65/2 full-data mismatch.
            cached=(cache or {}).get('_full_result_samples') if cache is not None else None
            if cached is not None and [sample_key(x) for x in cached]==keys:
                return list(cached)
            samples=self.store.load_samples_by_keys(keys)
            if cache is not None:cache['_full_result_samples']=list(samples)
            return samples
        except Exception:return []

    def update_find_source_ui(self, *_args):
        if hasattr(self, "find_source_stack"):
            self.find_source_stack.setCurrentIndex(max(0,self.find_source.currentIndex()))

    def _add_find_session(self, sample, title=None):
        """兼容单色标准；内部统一为一个任务可含多个色样。"""
        return self._add_find_file_session([sample], title or sample.display_name)

    def _add_find_file_session(self, samples, title):
        samples=list(samples or [])
        if not samples:
            return None
        # 一个 QTX 文件可以作为一个查色任务，并在任务内包含多个标准色样。
        # 点击一次“查询”会把该 QTX 中已选择的标准全部计算并缓存；随后切换标准
        # 只切换已经计算好的结果，不会把前一个标准的结果清掉。
        real_src=str(samples[0].source_file or '')
        source_key=real_src if real_src and not real_src.startswith('manual://') else ''
        existing=None
        if source_key:existing=next((x for x in self.find_sessions if x.get('source_key')==source_key),None)
        if existing:
            # 同一 QTX 再次加入时合并新选择，不重复创建标签。
            known={sample_key(x) for x in existing.get('samples',[])}
            for sm in samples:
                if sample_key(sm) not in known:
                    existing.setdefault('samples',[]).append(sm); known.add(sample_key(sm))
            existing.setdefault('per_standard',{})
            self.active_find_session_id=existing['id']
            idx=next((i for i in range(self.find_tabs.count()) if self.find_tabs.tabData(i)==existing['id']),-1)
            if idx>=0: self.find_tabs.setCurrentIndex(idx)
            self._activate_find_session(existing)
            return existing
        sid=str(uuid.uuid4())
        session={'id':sid,'samples':samples,'sample_index':0,'sample':samples[0],
                 'title':title,'source_file':real_src if not real_src.startswith('manual://') else '',
                 'source_key':source_key,'queried':False,'needs_query':False,'result_keys':[],
                 'scores':{},'per_standard':{},'selected_keys':set(),'scope_selection':None if self.find_scope_selection is None else list(self.find_scope_selection),'illuminant':self.find_illuminant.currentText() if hasattr(self,'find_illuminant') else 'D65','observer':2 if hasattr(self,'find_observer') and self.find_observer.currentText()=='2°' else 10,'query_params':self._find_query_params_from_ui()}
        self.find_sessions.append(session)
        idx=self.find_tabs.addTab(title); self.find_tabs.setTabData(idx,sid)
        self.find_tabs.blockSignals(True); self.find_tabs.setCurrentIndex(idx); self.find_tabs.blockSignals(False)
        self.active_find_session_id=sid; self._activate_find_session(session)
        return session

    def _activate_find_session(self, session):
        self.active_find_session_id=session['id']
        self._restore_find_query_params(session)
        samples=session.get('samples') or [session.get('sample')]
        samples=[x for x in samples if x is not None]
        idx=max(0,min(int(session.get('sample_index',0)),max(0,len(samples)-1)))
        session['sample_index']=idx
        if samples:
            session['sample']=samples[idx]; self.find_standard=samples[idx]
        else:
            self.find_standard=None
        cache=self._find_current_cache(session,self.find_standard)
        if cache is not None:
            session['result_keys']=list(cache.get('result_keys',[])); session['scores']=dict(cache.get('scores',{})); session['selected_keys']=set(cache.get('selected_keys',set()))
            session['queried']=True; session['needs_query']=False
        else:
            # Legacy single-standard sessions may predate per_standard. Migrate
            # their cached result once instead of borrowing any global/library view.
            if self.find_standard is not None and not session.get('per_standard') and session.get('queried') and session.get('result_keys'):
                cache={'result_keys':list(session.get('result_keys',[])),'scores':dict(session.get('scores',{})),'selected_keys':set(session.get('selected_keys',set())),'query_params':dict(session.get('query_params') or {})}
                session.setdefault('per_standard',{})[sample_key(self.find_standard)]=cache
            else:
                session['queried']=False; session['needs_query']=False; session['result_keys']=[]; session['scores']={}; session['selected_keys']=set()
        self.find_mode_active=bool(session.get('queried',False) and self._find_current_cache(session,self.find_standard) is not None)
        self.find_scores=dict(session.get('scores',{})) if self.find_mode_active else {}
        self.find_scope_selection = None if session.get('scope_selection') is None else list(session.get('scope_selection') or [])
        self._update_find_scope_button()
        self.library_selected_keys=set(session.get('selected_keys',set())) if self.find_mode_active else set()
        if hasattr(self,'find_sample_combo'):
            self.find_sample_combo.blockSignals(True); self.find_sample_combo.clear()
            for sm in samples: self.find_sample_combo.addItem(sm.display_name)
            self.find_sample_combo.setCurrentIndex(idx); self.find_sample_combo.setVisible(len(samples)>1)
            self.find_sample_combo.blockSignals(False)
        self._update_find_standard_label()
        if hasattr(self,'find_tile_grid'): self.build_find_tiles()

    def _find_sample_combo_changed(self, index):
        session=next((x for x in self.find_sessions if x['id']==self.active_find_session_id),None)
        if not session or index<0: return
        samples=session.get('samples') or []
        if index>=len(samples): return
        session['sample_index']=index; session['sample']=samples[index]; self.find_standard=samples[index]
        cached=(session.get('per_standard') or {}).get(sample_key(self.find_standard))
        if cached:
            session['result_keys']=list(cached.get('result_keys',[])); session['scores']=dict(cached.get('scores',{})); session['selected_keys']=set(cached.get('selected_keys',set()))
            session['queried']=True; session['needs_query']=False; self.find_mode_active=True; self.find_scores=dict(session['scores']); self.library_selected_keys=set(session['selected_keys'])
        else:
            # This standard has never been queried. Clear only the task's current
            # projection; keep other standards in per_standard untouched.
            session['queried']=False; session['needs_query']=False; session['result_keys']=[]; session['scores']={}; session['selected_keys']=set()
            self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear()
        self._update_find_standard_label(); self._tiles_keys=None; self.build_find_tiles()

    def _step_find_task(self, step):
        n=self.find_tabs.count() if hasattr(self,'find_tabs') else 0
        if n<=0: return
        cur=max(0,self.find_tabs.currentIndex()); self.find_tabs.setCurrentIndex((cur+step)%n)

    def _show_find_tasks_menu(self):
        if not hasattr(self,'find_tabs'): return
        menu=QMenu(self)
        for i in range(self.find_tabs.count()):
            act=menu.addAction(self.find_tabs.tabText(i)); act.setCheckable(True); act.setChecked(i==self.find_tabs.currentIndex()); act.setData(i)
        chosen=menu.exec(self.find_tasks_btn.mapToGlobal(QPoint(0,self.find_tasks_btn.height())))
        if chosen is not None: self.find_tabs.setCurrentIndex(int(chosen.data()))

    def _show_find_export_menu(self):
        menu=QMenu(self); excel=menu.addAction('Excel'); qtx=menu.addAction('QTX')
        chosen=menu.exec(self.find_export_btn.mapToGlobal(QPoint(0,self.find_export_btn.height())))
        if chosen==excel: self.export_find_results_excel()
        elif chosen==qtx: self.export_find_results_qtx()

    def _update_find_standard_label(self):
        if self.find_standard is None:
            self.find_standard_label.setText("标准色：未设置"); return
        lab=self.sample_lab(self.find_standard)
        self.find_standard_label.setText(f"标准色：{self.find_standard.display_name}    L* {lab[0]:.2f}  a* {lab[1]:.2f}  b* {lab[2]:.2f}")

    def find_tab_changed(self, index):
        if index<0: return
        sid=self.find_tabs.tabData(index); session=next((x for x in self.find_sessions if x['id']==sid),None)
        if not session: return
        # Find tabs are fully isolated. Never rebuild/borrow the formal-library
        # card view when only the active Find task changes.
        self._activate_find_session(session)

    def close_find_tab(self, index):
        sid=self.find_tabs.tabData(index); self.find_tabs.removeTab(index); self.find_sessions=[x for x in self.find_sessions if x['id']!=sid]
        if not self.find_sessions:
            self.active_find_session_id=None; self.find_standard=None; self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear(); self._update_find_standard_label(); self._tiles_keys=None; self.build_find_tiles()

    def set_find_standard(self, sample):
        # Find/compare can switch illuminants and therefore needs the measured spectrum.
        sample=self._full_library_sample(sample)
        self.library_has_explicit_view=True
        self.find_toggle.setChecked(True); self.find_panel.show(); self.find_source.setCurrentText("QTX色样"); self.update_find_source_ui(); self.library_sort_widget.hide() if hasattr(self,"library_sort_widget") else None
        self._add_find_session(sample)
        self.file_list.blockSignals(True); self.file_list.setCurrentItem(None); self.file_list.clearSelection(); self.file_list.blockSignals(False)
        self.show_find_page()
        self.statusBar().showMessage(f"已加入查色标准：{sample.display_name}；点击【查询】开始查找",5000)

    def choose_find_qtx_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"选择查色标准 QTX","","QTX 文件 (*.qtx *.QTX *.txt);;所有文件 (*)")
        if paths: self.add_find_qtx_files(paths)

    def add_find_qtx_files(self, paths):
        # 支持多文件/文件夹。每个 QTX 先弹出色样选择器；一个 QTX 内勾选多个色样时
        # 建立一个“文件级”任务，查询一次即可把所有标准的结果都算好并缓存。
        expanded=self._expand_workspace_inputs(list(paths),include_excel=True)
        qtx=[p for p in expanded if Path(p).suffix.lower() in {'.qtx','.txt','.cpx','.xlsx'}]
        added_files=0; added_samples=0; errors=[]
        for path in qtx:
            try:
                canonical=str(Path(path).resolve())
                samples=self._parse_external_samples(canonical)
                if not samples: continue
                dlg=QtxImportSelectionDialog(path,samples,parent=self,purpose='查色 / 找色')
                if dlg.exec()!=QDialog.Accepted: continue
                chosen=dlg.chosen()
                if not chosen: continue
                self._add_find_file_session(chosen,Path(path).name); added_files+=1; added_samples+=len(chosen)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if added_files:
            self.find_toggle.setChecked(True); self.find_panel.show(); self.find_source.setCurrentText("QTX色样"); self.update_find_source_ui(); self.library_has_explicit_view=True
            self.show_find_page()
            if hasattr(self,'library_sort_widget'): self.library_sort_widget.hide()
            self.statusBar().showMessage(f"已建立 {added_files} 个 QTX 查色任务，共 {added_samples} 个标准色样；同一 QTX 可一次查询全部标准",5000)
        if errors: QMessageBox.warning(self,"QTX 查色", "部分文件无法读取：\n"+"\n".join(errors[:8]))

    def choose_find_standard_from_library(self):
        """从正式色库中选择一个或多个色样，作为查色标准。

        v0.8.0 合并标准模式时这里的 def 声明被误删，导致启动阶段
        clicked.connect(self.choose_find_standard_from_library) 直接抛 AttributeError。
        """
        dlg=FindStandardPickerDialog(self,self)
        if dlg.exec()!=QDialog.Accepted:
            return
        chosen=dlg.chosen()
        if not chosen:
            QMessageBox.information(self,"查色","没有选择色样。")
            return
        for sample in chosen:
            self._add_find_session(sample)
        self.find_toggle.setChecked(True)
        self.find_panel.show()
        if hasattr(self,'library_sort_widget'): self.library_sort_widget.hide()
        self.find_source.setCurrentText("QTX色样")
        self.update_find_source_ui()
        self.library_has_explicit_view=True
        self.show_find_page(); self.statusBar().showMessage(f"已从色库加入 {len(chosen)} 个查色标准；可逐个标签查询",5000)

    def _manual_standard_from_inputs(self):
        mode=self.find_source.currentText()
        if mode=="LAB输入":
            lab=(float(self.find_L.text()),float(self.find_a.text()),float(self.find_b.text())); title=f"LAB {lab[0]:.2f},{lab[1]:.2f},{lab[2]:.2f}"
        elif mode=="RGB输入":
            vals=[float(x.text()) for x in (self.find_R,self.find_G,self.find_B)]
            if any(v<0 or v>255 for v in vals): raise ValueError("RGB 必须在 0–255")
            rgb=[v/255 for v in vals]; lin=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]; r,g,b=lin
            X=(.4124564*r+.3575761*g+.1804375*b)*100; Y=(.2126729*r+.7151522*g+.072175*b)*100; Z=(.0193339*r+.119192*g+.9503041*b)*100
            xr,yr,zr=X/95.047,Y/100,Z/108.883; f=lambda t:t**(1/3) if t>0.008856 else 7.787*t+16/116; fx,fy,fz=f(xr),f(yr),f(zr); lab=(116*fy-16,500*(fx-fy),200*(fy-fz)); title=f"RGB {int(vals[0])},{int(vals[1])},{int(vals[2])}"
        else:
            return None,None
        sample=Sample(f'manual-{uuid.uuid4()}',title,'MANUAL',(0,0,0),lab,tuple(),source_file=f'manual://{uuid.uuid4()}')
        return sample,title

    def add_manual_find_standard(self):
        try: sample,title=self._manual_standard_from_inputs()
        except Exception as exc:
            QMessageBox.warning(self,"查色标准",str(exc)); return
        if sample is None: return
        self._add_find_session(sample,title); self.find_toggle.setChecked(True); self.find_panel.show(); self.library_has_explicit_view=True
        self.show_find_page(); self.statusBar().showMessage("已添加手动查色标准；点击【查询】开始",4000)

    def apply_manual_find_standard(self):
        # 兼容旧调用：新界面改为点击“添加为查色标准”。
        self.add_manual_find_standard()

    def run_find_query(self):
        if self.find_standard is None:
            QMessageBox.information(self,"查色","请先拖入/选择 QTX 色样，或输入 LAB/RGB 并添加为标准。")
            return
        if self.find_scope_selection == []:
            QMessageBox.information(self,"查色范围","当前没有选择任何查色数据源。请先点击“查色范围”选择一个或多个已授权色库。")
            return
        session=self._current_find_session()
        if not session:return
        params=self._find_query_params_from_ui(); session['query_params']=dict(params)
        session['scope_selection']=None if params.get('scope_selection') is None else list(params.get('scope_selection') or [])
        session['illuminant']=params.get('illuminant','D65');session['observer']=int(params.get('observer',10))
        standards=[x for x in (session.get('samples') or [self.find_standard]) if x is not None]
        current_idx=max(0,min(int(session.get('sample_index',0)),len(standards)-1)) if standards else 0
        # Always compute the currently viewed standard first.  Large multi-standard
        # QTX tasks stay responsive and can be cancelled instead of freezing Qt.
        order=list(range(len(standards)))
        if standards and current_idx in order:
            order.remove(current_idx);order.insert(0,current_idx)
        progress=None
        if len(order)>8:
            progress=QProgressDialog('正在查色…','取消',0,len(order),self)
            progress.setWindowTitle('查色 / 找色');progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(250)
        candidate_rows=self._find_candidate_index_rows(session)
        alt_lab_by_key=None
        if display_illuminant(str(params.get('illuminant','D65')))!='D65' or int(params.get('observer',10))!=10:
            alt_lab_by_key=self._find_alt_labs_by_key([str(r['sample_key']) for r in candidate_rows],str(params.get('illuminant','D65')),int(params.get('observer',10)))
        completed=0
        for pos,idx in enumerate(order):
            if progress is not None:
                progress.setLabelText(f'正在计算 {pos+1} / {len(order)}：{standards[idx].display_name}')
                progress.setValue(pos);QApplication.processEvents()
                if progress.wasCanceled():break
            self._query_find_standard(session,standards[idx],candidate_rows,alt_lab_by_key);completed+=1
        if progress is not None:progress.setValue(len(order))
        if standards:
            session['sample_index']=current_idx;session['sample']=standards[current_idx];self.find_standard=standards[current_idx]
            cached=self._find_current_cache(session,self.find_standard)
            if cached is not None:
                session['result_keys']=list(cached.get('result_keys',[]));session['scores']=dict(cached.get('scores',{}));session['selected_keys']=set(cached.get('selected_keys',set()))
                session['queried']=True;session['needs_query']=False;self.find_scores=dict(session['scores']);self.library_selected_keys=set(session['selected_keys'])
            else:
                session['queried']=False;session['result_keys']=[];session['scores']={};session['selected_keys']=set();self.find_scores={};self.library_selected_keys.clear()
        self.find_mode_active=bool(self._find_current_cache(session,self.find_standard) is not None)
        self._tiles_keys=None;self.build_find_tiles()
        shown=len(self._find_result_keys(session,self.find_standard))
        if hasattr(self,'find_result_summary'):
            suffix=f" · 已计算 {completed}/{len(standards)} 个标准" if len(standards)>1 else ''
            self.find_result_summary.setText(f"查询结果：{shown} 个 · 已选 {len(self.library_selected_keys)} 个{suffix}")
        self.statusBar().showMessage(f"查色完成：当前任务结果已独立缓存；已计算 {completed}/{len(standards)} 个标准",4500)

    def clear_find_mode(self):
        self.find_standard=None; self.find_mode_active=False; self.find_scores={}; self.find_sessions=[]; self.active_find_session_id=None
        if hasattr(self,'find_tabs'):
            while self.find_tabs.count(): self.find_tabs.removeTab(0)
        self.find_toggle.setChecked(True); self.find_panel.show(); self.find_standard_label.setText("标准色：未设置")
        self.library_selected_keys.clear(); self._tiles_keys=None
        if hasattr(self,'find_result_summary'): self.find_result_summary.setText("查询结果：尚未查询")
        self.build_find_tiles()

    def _selected_find_samples(self):
        # Hydrate only explicitly selected result keys from the active task.
        keyset=set(self.library_selected_keys);ordered=[k for k in self._find_result_keys() if k in keyset]
        try:return self.store.load_samples_by_keys(ordered)
        except Exception:return []

    def compare_find_selection(self):
        if self.find_standard is None:
            QMessageBox.information(self,"比较","请先设置查色标准。") ; return
        found=self._selected_find_samples()
        if not found:
            QMessageBox.information(self,"比较","请先在查色结果中选择一个或多个颜色（Ctrl/Shift 可多选）。") ; return
        name=f"查色比较 · {self.find_standard.display_name}"
        wb={"workbench_id":str(uuid.uuid4()),"name":name,"samples_data":[self._serialize_sample(x) for x in [self.find_standard,*found]],"sample_keys":[sample_key(self.find_standard),*[sample_key(x) for x in found]],"standard_key":sample_key(self.find_standard),"created_at":time.time(),"is_collapsed":False,"average_standard":None,"hidden_columns":[],"sort_key":"manual","sort_desc":False,"illuminants":[self.find_illuminant.currentText() if hasattr(self,'find_illuminant') else "D65"],"observer":2 if hasattr(self,'find_observer') and self.find_observer.currentText()=="2°" else 10}
        # 手动 LAB/RGB 标准没有反射率，因此不能作为光谱平均，但可以作为指定标准比较。
        self.workbenches.append(wb); self.store.save_workbench(wb); self.active_workbench_id=wb['workbench_id']; self.show_page(3)

    def export_find_results_excel(self):
        if not self.can_use("export"):return
        if self.find_standard is None:
            QMessageBox.information(self,"导出 Excel","请先设置查色标准。") ; return
        session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
        standards=[x for x in ((session or {}).get('samples') or []) if x is not None]
        per=(session or {}).get('per_standard') or {}
        # 多标准 QTX：直接导出“标准 ↔ 最佳匹配”的并列结构，和屏幕上的批量概览一致。
        if len(standards)>1 and per:
            path,_=QFileDialog.getSaveFileName(self,"导出多标准查色比较","多标准查色比较.xlsx","Excel (*.xlsx)")
            if not path:return
            match_keys=[vals.get('result_keys',[None])[0] for vals in per.values() if vals.get('result_keys')]
            bykey={sample_key(x):x for x in self._samples_by_keys_on_demand(match_keys,full=True)}
            if not self.can_export_samples([*standards,*bykey.values()],'导出查色结果'):return
            headers=["标准色块","标准名称","标准L*","标准a*","标准b*","匹配色块","最佳匹配","匹配L*","匹配a*","匹配b*",self.find_formula.currentText(),"ΔL*","Δa*","Δb*","ΔC*","ΔH*","标准来源QTX","匹配来源QTX"]
            rows=[]
            for ref in standards:
                cache=per.get(sample_key(ref),{}); keys=cache.get('result_keys',[]); match=bykey.get(keys[0]) if keys else None
                lr=self.sample_lab(ref)
                if match is None:
                    rows.append([{"swatch_lab":list(lr)},ref.display_name,*[round(v,4) for v in lr],None,"—",None,None,None,None,None,None,None,None,None,ref.source_file,""])
                    continue
                lm=self.sample_lab(match); cr=math.hypot(lr[1],lr[2]); cm=math.hypot(lm[1],lm[2]); vals=self._find_values(ref,match)
                rows.append([{"swatch_lab":list(lr)},ref.display_name,*[round(v,4) for v in lr],{"swatch_lab":list(lm)},match.display_name,*[round(v,4) for v in lm],round(vals['色差'],4),round(lm[0]-lr[0],4),round(lm[1]-lr[1],4),round(lm[2]-lr[2],4),round(cm-cr,4),round(delta_h_cielab_signed(lr,lm),4),ref.source_file,match.source_file])
            export_workbench_table(path,"多标准查色比较",headers,rows)
            self.statusBar().showMessage(f"已导出 {len(rows)} 组标准 ↔ 最佳匹配：{path}",5000); return
        samples=self._selected_find_samples()
        if not samples:
            QMessageBox.information(self,"导出 Excel","请先在查询结果中选择要与标准一起导出的色样。") ; return
        if not self.can_export_samples(([self.find_standard] if self.find_standard is not None else [])+samples,'导出查色结果'):return
        path,_=QFileDialog.getSaveFileName(self,"导出标准 + 已选查色结果","查色比较.xlsx","Excel (*.xlsx)")
        if not path: return
        headers=["色块","名称","角色","L*","a*","b*","C*","h°",self.find_formula.currentText(),"ΔL*","Δa*","Δb*","ΔC*","ΔH*","来源QTX"]
        rows=[]; ref=self.find_standard; lr=self.sample_lab(ref)
        def row_for(s,role,is_ref=False):
            lab=self.sample_lab(s); C=math.hypot(lab[1],lab[2]); h=math.degrees(math.atan2(lab[2],lab[1]))%360
            if is_ref:
                de=0.0; dL=da=db=dC=dH=0.0
            else:
                vals=self._find_values(ref,s); de=vals['色差']; dL=lab[0]-lr[0]; da=lab[1]-lr[1]; db=lab[2]-lr[2]; dC=C-math.hypot(lr[1],lr[2]); dH=delta_h_cielab_signed(lr,lab)
            return [{"swatch_lab":list(lab)},s.display_name,role,*[round(v,4) for v in lab],round(C,4),round(h,2),round(de,4),round(dL,4),round(da,4),round(db,4),round(dC,4),round(dH,4),s.source_file]
        rows.append(row_for(ref,"当前标准",True))
        rows.extend(row_for(s,"查色结果",False) for s in samples)
        export_workbench_table(path,"查色比较",headers,rows)
        self.statusBar().showMessage(f"已导出标准 + {len(samples)} 个查色结果：{path}",5000)

    def export_find_results_qtx(self):
        samples=self._selected_find_samples()
        if not samples:
            QMessageBox.information(self,"导出原 QTX","请先选择要与当前标准一起导出的查色结果。") ; return
        if not self.can_export_samples(([self.find_standard] if self.find_standard is not None else [])+samples,'导出查色原始 QTX'):return
        folder=QFileDialog.getExistingDirectory(self,"导出当前标准 + 已选结果的原始 QTX")
        if not folder: return
        paths=[]
        if self.find_standard is not None and not str(self.find_standard.source_file).startswith('manual://'):
            paths.append(self.find_standard.source_file)
        paths.extend(s.source_file for s in samples if not str(s.source_file).startswith('manual://'))
        copied=0; missing=[]
        for src in dict.fromkeys(paths):
            p=Path(src)
            if not p.exists(): missing.append(str(p)); continue
            target=Path(folder)/p.name
            if target.exists():
                stem,suffix=target.stem,target.suffix; n=2
                while target.exists(): target=Path(folder)/f"{stem}_{n}{suffix}"; n+=1
            shutil.copy2(p,target); copied+=1
        msg=f"已导出 {copied} 个原始 QTX 文件（当前标准 + 已选结果，重复来源只导出一次）。"
        if self.find_standard is not None and str(self.find_standard.source_file).startswith('manual://'):
            msg += "\n当前标准来自 LAB/RGB 手动输入，没有原始 QTX。"
        if missing: msg+=f"\n{len(missing)} 个原文件路径已失效，无法复制。"
        QMessageBox.information(self,"导出原 QTX",msg)

    def _find_values(self, ref, sample):
        lr=self.sample_lab(ref); ls=self.sample_lab(sample)
        dL=ls[0]-lr[0]; da=ls[1]-lr[1]; db=ls[2]-lr[2]
        cr=math.hypot(lr[1],lr[2]); cs=math.hypot(ls[1],ls[2]); dc=cs-cr
        hr=math.degrees(math.atan2(lr[2],lr[1]))%360; hs=math.degrees(math.atan2(ls[2],ls[1]))%360
        dh=abs(hs-hr); dh=min(dh,360-dh)
        try:
            illum=(self.find_illuminant.currentText() if self.active_tool_index()==1 and hasattr(self,'find_illuminant') else self.illuminant)
            obs=(2 if hasattr(self,'find_observer') and self.find_observer.currentText()=='2°' else 10) if self.active_tool_index()==1 else self.observer
            r=analyse_pair(ref,sample,illum,observer_degrees=obs)
            fmap={"CIEDE2000":"delta_e00","CIE94":"delta_e94","ΔE*ab":"delta_e76","CMC(2:1)":"cmc21"}
            de=float(getattr(r,fmap[self.find_formula.currentText()]))
        except Exception: de=float("inf")
        return {"色差":de,"L*":abs(dL),"a*":abs(da),"b*":abs(db),"C*":abs(dc),"h°":dh,"dL":dL}

    def _optional_float(self, edit):
        text=edit.text().strip().replace(",", ".")
        if not text: return None
        try: return float(text)
        except ValueError: return None

    def _sample_colour_family(self, sample):
        L,a,b=self.sample_lab(sample); C=math.hypot(a,b)
        if C<4.0:return 'neutral'
        h=math.degrees(math.atan2(b,a))%360.0
        if h<25 or h>=330:return 'red'
        if h<70:return 'orange'
        if h<110:return 'yellow'
        if h<165:return 'green'
        if h<210:return 'cyan'
        if h<270:return 'blue'
        return 'purple'

    def _library_filter_changed(self, *_):
        self.library_page=0; self._tiles_keys=None; self.build_tiles()

    def _library_sort_combo_activated(self, index=0):
        if not hasattr(self,'library_sort_combo'):return
        key=str(self.library_sort_combo.itemData(int(index)) or 'manual')
        self.toggle_library_sort(key)

    def _library_sort_combo_changed(self, _index=0):
        """Compatibility entry point used by older settings/tests."""
        self._library_sort_combo_activated(_index)

    def _refresh_library_sort_combo_text(self):
        if not hasattr(self,'library_sort_combo'):return
        bases={'manual':'排序：原始顺序','name':'排序：色号','L':'排序：L*','a':'排序：a*','b':'排序：b*','C':'排序：C*','h':'排序：Munsell 色相'}
        for i in range(self.library_sort_combo.count()):
            key=str(self.library_sort_combo.itemData(i) or 'manual')
            text=bases.get(key,'排序：'+key)
            if key==self.library_sort_key and key!='manual':
                if key=='name':text += '  ' + ('Z → A' if self.library_sort_desc else 'A → Z')
                elif key=='h':text += '  ' + ('逆序' if self.library_sort_desc else '正序')
                else:text += '  ' + ('高 → 低' if self.library_sort_desc else '低 → 高')
            self.library_sort_combo.setItemText(i,text)
        wanted=str(getattr(self,'library_sort_key','manual') or 'manual')
        for i in range(self.library_sort_combo.count()):
            if str(self.library_sort_combo.itemData(i))==wanted:
                self.library_sort_combo.blockSignals(True); self.library_sort_combo.setCurrentIndex(i); self.library_sort_combo.blockSignals(False); break

    def _munsell_index_scope(self):
        wanted=getattr(self,'active_library_customer',None)
        if not wanted:
            return None,None
        return (None if wanted=='全部客户' else wanted,
                self.library_scope if wanted=='全部客户' else None)

    def _start_munsell_index_if_needed(self):
        """Start a one-time exact Munsell index build without blocking Qt.

        Returns True while a build is running/started, so the caller should keep
        the current cards on screen until the persistent sort keys are ready.
        """
        if self.active_tool_index()==1 or self.current_file() or not self.library_has_explicit_view:
            return False
        customer,scope=self._munsell_index_scope()
        if customer is None and scope is None and not self.active_library_customer:
            return False
        try: missing=self.store.munsell_missing_count(customer,scope)
        except Exception:
            return False
        if missing<=0:
            return False
        state=getattr(self,'_munsell_index_state',None)
        if state and state.get('future') is not None and not state['future'].done():
            return True
        dlg=QProgressDialog(f'正在建立 Munsell 色相索引…\n首次需要处理 {missing:,} 个色样，完成后以后排序会直接走 SQLite。','',0,0,self)
        dlg.setWindowTitle('Munsell 色相索引')
        dlg.setWindowModality(Qt.WindowModal); dlg.setCancelButton(None); dlg.setMinimumDuration(0); dlg.setAutoClose(False); dlg.setAutoReset(False)
        future=self._maintenance_executor.submit(self.store.build_munsell_index,customer,scope)
        self._munsell_index_state={'future':future,'dialog':dlg,'customer':customer,'scope':scope}
        if hasattr(self,'library_sort_combo'): self.library_sort_combo.setEnabled(False)
        self.statusBar().showMessage(f'正在建立 Munsell 色相索引：{missing:,} 个色样（仅首次）')
        dlg.show(); QTimer.singleShot(80,self._poll_munsell_index_job)
        return True

    def _poll_munsell_index_job(self):
        state=getattr(self,'_munsell_index_state',None)
        if not state:return
        future=state.get('future'); dlg=state.get('dialog')
        if future is None:return
        if not future.done():
            QTimer.singleShot(100,self._poll_munsell_index_job); return
        try:
            changed=int(future.result() or 0)
        except Exception as exc:
            changed=-1
            QMessageBox.warning(self,'Munsell 色相索引',f'建立 Munsell 色相索引失败：\n{exc}')
        try:
            if dlg is not None: dlg.close(); dlg.deleteLater()
        except Exception: pass
        self._munsell_index_state=None
        if hasattr(self,'library_sort_combo'): self.library_sort_combo.setEnabled(True)
        if changed>=0:
            self.statusBar().showMessage(f'Munsell 色相索引已建立：{changed:,} 个色样 · 后续排序直接使用 SQLite',4500)
            self._library_view_cache=None; self._tiles_keys=None; self.library_page=0; self.build_tiles()
        else:
            # Restore a safe scalar sort instead of falling back to the old
            # all-payload Munsell path.
            self.library_sort_key='manual'; self.library_sort_desc=True
            if hasattr(self,'library_sort_combo'):
                idx=self.library_sort_combo.findData('manual')
                if idx>=0:
                    self.library_sort_combo.blockSignals(True); self.library_sort_combo.setCurrentIndex(idx); self.library_sort_combo.blockSignals(False)
            self._tiles_keys=None; self.build_tiles()

    def _filter_library_sidebar(self, text=''):
        query=str(text or '').strip().casefold()
        if hasattr(self,'library_customer_tree'):
            def apply_tree(item):
                own=query in item.text(0).casefold()
                child_match=False
                for i in range(item.childCount()):child_match=apply_tree(item.child(i)) or child_match
                visible=(not query) or own or child_match; item.setHidden(not visible); return visible
            for i in range(self.library_customer_tree.topLevelItemCount()):apply_tree(self.library_customer_tree.topLevelItem(i))
        if hasattr(self,'file_list'):
            for i in range(self.file_list.count()):
                item=self.file_list.item(i); item.setHidden(bool(query) and query not in item.text().casefold())

    @profiled("main.filtered_samples")
    def filtered_samples(self):
        on_find_page = self.active_tool_index()==1
        # Find results are task-owned snapshots. Rendering/switching a task must
        # never fall through to the current formal-library view.
        if on_find_page:
            session=self._current_find_session()
            if session is None or self.find_standard is None or not self.find_mode_active:
                self.find_scores={}
                return []
            cache=self._find_current_cache(session,self.find_standard)
            if cache is None:
                self.find_scores={}
                return []
            self.find_scores=dict(cache.get('scores',{}))
            session['result_keys']=list(cache.get('result_keys',[]));session['scores']=dict(self.find_scores)
            return self._find_result_samples(full=False,session=session,standard=self.find_standard)

        self.find_scores={}
        current=self.current_file(); result=[]
        if not current and not self.library_has_explicit_view:
            return []
        cache_key=(self._library_data_revision,current,self.active_library_customer,self.library_scope,
                   self.search.currentText().lower().strip(),self.library_type_filter.currentData(),
                   self.library_family_filter.currentData(),self.library_sort_key,self.library_sort_desc)
        cached=self._library_view_cache
        if cached is not None and cached[0]==cache_key:return list(cached[1])
        if current and current in self.loaded_files:
            result.extend(self.loaded_files[current]["samples"])
        else:
            wanted=self.active_library_customer
            if not wanted: return []
            try:
                for saved, stored in self.store.library_contents(None if wanted=="全部客户" else wanted,
                                                                 scope=self.library_scope if wanted=="全部客户" else None):
                    if wanted=='全部客户' and not self._library_scope_contains(saved.customer):continue
                    canonical=str(Path(saved.path).resolve())
                    result.extend(replace(x,source_file=canonical) for x in stored)
            except Exception: result=[]
        text=self.search.currentText().lower().strip(); result=[x for x in result if text in x.display_name.lower()]
        type_filter=self.library_type_filter.currentData() if hasattr(self,'library_type_filter') else 'all'
        if type_filter in {'STD','BAT'}:result=[x for x in result if x.kind==type_filter]
        family_filter=self.library_family_filter.currentData() if hasattr(self,'library_family_filter') else 'all'
        if family_filter and family_filter!='all':result=[x for x in result if self._sample_colour_family(x)==family_filter]
        key=self.library_sort_key; reverse=self.library_sort_desc
        if key=="name": result.sort(key=lambda x:x.display_name.casefold(),reverse=reverse)
        elif key in {"L","a","b"}:
            i={"L":0,"a":1,"b":2}[key]; result.sort(key=lambda x:self.sample_lab(x)[i],reverse=reverse)
        elif key=="C": result.sort(key=lambda x:math.hypot(self.sample_lab(x)[1],self.sample_lab(x)[2]),reverse=reverse)
        elif key=="h":
            # Fast visual hue order for temporary/legacy views. Formal-library
            # paging uses the equivalent SQLite-side key.
            result.sort(key=lambda x:((math.degrees(math.atan2(self.sample_lab(x)[2],self.sample_lab(x)[1]))+360.0)%360.0, -self.sample_lab(x)[0]), reverse=reverse)
        else:
            positions={k:i for i,k in enumerate(self.manual_order)}; result.sort(key=lambda x:positions.get(sample_key(x),999999))
        self._library_view_cache=(cache_key,tuple(result))
        return result

    def toggle_library_sort(self, key: str):
        key=str(key or 'manual')
        if key == "manual":
            self.library_sort_key, self.library_sort_desc = "manual", False
        elif self.library_sort_key == key:
            self.library_sort_desc = not bool(self.library_sort_desc)
        else:
            self.library_sort_key = key
            # First click is always the natural forward/ascending order.
            self.library_sort_desc = False
        self._refresh_library_sort_combo_text()
        self.library_page=0
        self._tiles_keys=None
        # Hotfix49: Munsell browsing uses an immediate lightweight hue order.
        # Exact Munsell notation remains available in sample details; sorting must
        # never block the UI to renotate thousands of spectra.
        self.build_tiles()

    def _library_page_size_changed(self, _index=0):
        if not hasattr(self,'library_page_size_combo'):
            return
        try: self.library_page_size=int(self.library_page_size_combo.currentData())
        except Exception: self.library_page_size=0
        self.library_page=0; self._tiles_keys=None; self.build_tiles()

    def _library_grid_metrics(self):
        viewport=self.tile_scroll.viewport() if hasattr(self,'tile_scroll') else None
        viewport_w=max(360,viewport.width() if viewport is not None else 960)
        viewport_h=max(260,viewport.height() if viewport is not None else 620)
        gap=12; available=max(320,viewport_w-20); target=148
        cols=max(1,min(12,int((available+gap)//(target+gap))))
        card_w=max(124,min(176,int((available-gap*(cols-1))/cols)))
        rows=max(1,min(8,int((max(180,viewport_h-18)+gap)//(138+gap))))
        return cols,rows,card_w

    def _library_effective_page_size(self):
        cols,rows,card_w=self._library_grid_metrics()
        self._library_grid_cols,self._library_grid_rows,self._library_card_width=cols,rows,card_w
        requested=int(getattr(self,'library_page_size',0) or 0)
        return requested if requested>0 else max(1,cols*rows)

    def _set_library_page(self, page:int):
        total=max(0,int(getattr(self,'_library_total_items',0)))
        size=self._library_effective_page_size()
        if hasattr(self,'library_page_size_combo') and int(getattr(self,'library_page_size',0) or 0)==0:
            self.library_page_size_combo.setItemText(0,f'自动 · {size} 张/页')
        pages=max(1,(total+size-1)//size)
        page=max(0,min(int(page),pages-1))
        if page==getattr(self,'library_page',0) and self._tiles_keys is not None:
            return
        self.library_page=page; self._tiles_keys=None; self.build_tiles()

    def _update_library_pager(self, total:int):
        if not hasattr(self,'library_pager'):
            return
        total=max(0,int(total)); self._library_total_items=total
        size=self._library_effective_page_size()
        if hasattr(self,'library_page_size_combo') and int(getattr(self,'library_page_size',0) or 0)==0:
            self.library_page_size_combo.setItemText(0,f'自动 · {size} 张/页')
        pages=max(1,(total+size-1)//size)
        self.library_page=max(0,min(int(getattr(self,'library_page',0)),pages-1))
        self.library_count_label.setText(f'{total:,} 个颜色')
        self.library_prev_btn.setEnabled(self.library_page>0)
        self.library_next_btn.setEnabled(self.library_page<pages-1)
        while self.library_page_buttons_layout.count():
            it=self.library_page_buttons_layout.takeAt(0); w=it.widget()
            if w is not None: w.deleteLater()
        if pages<=7:
            labels=list(range(pages))
        else:
            cur=self.library_page; labels=[0]
            start=max(1,cur-1); end=min(pages-2,cur+1)
            if start>1: labels.append(None)
            labels.extend(range(start,end+1))
            if end<pages-2: labels.append(None)
            labels.append(pages-1)
        for value in labels:
            if value is None:
                dot=QLabel('…'); dot.setObjectName('pagerDots'); dot.setAlignment(Qt.AlignCenter); dot.setFixedWidth(22); self.library_page_buttons_layout.addWidget(dot); continue
            btn=QPushButton(str(value+1)); btn.setObjectName('pagerCurrent' if value==self.library_page else 'pagerPage'); btn.setFixedSize(32,32); btn.clicked.connect(lambda checked=False,p=value:self._set_library_page(p)); self.library_page_buttons_layout.addWidget(btn)
        self.library_pager.setVisible(total>0)
        if hasattr(self,'library_meta_label'):
            source=self.library_scope
            if self.current_file(): source=Path(self.current_file()).name
            elif self.active_library_customer: source=str(self.active_library_customer)
            self.library_meta_label.setText(f'共 {total:,} 个颜色  |  数据来源：{source}')

    def _formal_library_paging_supported(self):
        """Whether the current formal-library view can use P3-1 SQL true paging.

        Hotfix48 persists exact Munsell sort keys on first use, so Munsell can
        use the same SQLite paging path once its derived index is ready. Alternate
        illuminants still require full data for Lab-derived filters/sorts.
        """
        if self.active_tool_index()==1 or self.current_file():
            return False
        if not self.library_has_explicit_view or not self.active_library_customer:
            return False
        sort_key=str(getattr(self,'library_sort_key','manual') or 'manual')
        family=(self.library_family_filter.currentData() if hasattr(self,'library_family_filter') else 'all') or 'all'
        lab_dependent=(family!='all' or sort_key in {'L','a','b','C','h'})
        if lab_dependent and not (self.illuminant=='D65' and self.observer==10):
            return False
        return True

    def _formal_library_query_kwargs(self, include_hidden=True):
        wanted=self.active_library_customer
        return dict(
            customer=None if wanted=='全部客户' else wanted,
            scope=self.library_scope if wanted=='全部客户' else None,
            search_text=self.search.currentText().strip() if hasattr(self,'search') else '',
            kind=self.library_type_filter.currentData() if hasattr(self,'library_type_filter') else 'all',
            family=self.library_family_filter.currentData() if hasattr(self,'library_family_filter') else 'all',
            # h_fast is a lightweight a*b* hue-ring ordering used only for
            # browsing. It avoids the old 3,500-spectrum Munsell renotation job.
            sort_key=('h_fast' if str(getattr(self,'library_sort_key','manual') or 'manual')=='h' else str(getattr(self,'library_sort_key','manual') or 'manual')),
            sort_desc=bool(getattr(self,'library_sort_desc',False)),
            hidden_keys=set(self.hidden_cards) if include_hidden else set(),
        )

    def _formal_library_visible_keys(self):
        if not self._formal_library_paging_supported():
            return None
        try:
            return self.store.library_query_keys(**self._formal_library_query_kwargs(True))
        except Exception:
            return None

    @profiled('main.build_tiles')
    def build_tiles(self):
        if self.active_tool_index()==1:
            return self.build_find_tiles()
        # Hotfix49: Munsell browsing is always immediate via lightweight
        # a*b* hue ordering. Exact Munsell notation is computed only when a
        # feature explicitly needs it; no first-click progress dialog here.

        using_sql_page=False; query_kwargs=None; total=0
        if self._formal_library_paging_supported():
            size=self._library_effective_page_size()
            query_kwargs=self._formal_library_query_kwargs(True)
            try:
                total,page_items=self.store.library_page(
                    **query_kwargs, offset=max(0,int(self.library_page))*size, limit=size,
                    lightweight=(self.illuminant=='D65' and int(self.observer)==10))
                pages=max(1,(total+size-1)//size)
                clamped=max(0,min(int(getattr(self,'library_page',0)),pages-1))
                if clamped!=self.library_page:
                    self.library_page=clamped
                    total,page_items=self.store.library_page(
                        **query_kwargs, offset=self.library_page*size, limit=size,
                        lightweight=(self.illuminant=='D65' and int(self.observer)==10))
                using_sql_page=True
            except Exception:
                # Compatibility fallback: a SQLite build without JSON1/custom
                # functions must never break the mature library browser.
                using_sql_page=False

        if using_sql_page:
            self._update_library_pager(total)
            items=page_items
            wanted=[sample_key(s) for s in page_items]
        else:
            items=[s for s in self.filtered_samples() if sample_key(s) not in self.hidden_cards]
            total=len(items)
            self._update_library_pager(total)
            size=self._library_effective_page_size()
            pages=max(1,(len(items)+size-1)//size)
            self.library_page=max(0,min(int(getattr(self,'library_page',0)),pages-1))
            start=self.library_page*size; page_items=items[start:start+size]
            wanted=[sample_key(s) for s in page_items]

        if self._tiles_keys==wanted:
            for key in wanted:
                tile=self._tile_pool.get(key)
                if tile is None:continue
                tile.blockSignals(True);tile.setChecked(key in self.library_selected_keys);tile.blockSignals(False);tile.update()
            if hasattr(self,'selection_label'):self.selection_label.setText(f"已选 {len(self.library_selected_keys)} 个色样")
            self.empty_label.setVisible(total<=0);self.tile_scroll.setVisible(total>0);return

        self._tiles_keys=wanted
        self.tile_host.setUpdatesEnabled(False)
        try:
            # Detach current layout positions but keep a bounded page-sized widget pool.
            while self.tile_grid.count():
                layout_item=self.tile_grid.takeAt(0)
                w=layout_item.widget()
                if w is not None:w.hide()
            while len(self._tile_slots)<max(1,len(page_items)):
                # Temporary sample is immediately replaced below; signals reference self.sample.
                seed=page_items[0] if page_items else (self.samples[0] if self.samples else None)
                if seed is None:break
                tile=ColorTile(seed,self);tile.toggledForSample.connect(self.toggle_sample)
                self._tile_slots.append(tile)
            self._tile_pool.clear()
            self.empty_label.setVisible(total<=0);self.tile_scroll.setVisible(total>0)
            if using_sql_page:
                # Keep cross-page selection intact. Only when something is selected
                # do we fetch scalar keys to prune records hidden by a new filter.
                if self.library_selected_keys:
                    try:
                        visible_all=set(self.store.library_query_keys(**query_kwargs))
                        self.library_selected_keys.intersection_update(visible_all)
                    except Exception:
                        pass
            else:
                visible_all={sample_key(x) for x in items};self.library_selected_keys.intersection_update(visible_all)
            cols=self._library_grid_cols;card_w=self._library_card_width
            for i,sample in enumerate(page_items):
                tile=self._tile_slots[i];key=sample_key(sample)
                tile.sample=sample;tile.refresh_color()
                self._tile_pool[key]=tile
                tile.set_card_width(card_w)
                tile.blockSignals(True);tile.setChecked(key in self.library_selected_keys);tile.blockSignals(False)
                self.tile_grid.addWidget(tile,i//cols,i%cols,Qt.AlignTop|Qt.AlignHCenter);tile.show()
            for tile in self._tile_slots[len(page_items):]:tile.hide()
            for c in range(cols):self.tile_grid.setColumnStretch(c,1)
        finally:
            self.tile_host.setUpdatesEnabled(True);self.tile_host.update()
        self.selection_label.setText(f"已选 {len(self.library_selected_keys)} 个色样")

    def show_tile_menu(self, sample, global_pos):
        """Open a library context menu without eagerly materialising selection payloads.

        Hotfix31: a 3,500-card selection used to call load_samples_by_keys()
        before QMenu.exec(), forcing thousands of JSON payloads/spectra to be
        deserialised just to draw menu labels.  Menu construction now uses only
        the lightweight sample keys/count.  Full Sample objects are loaded only
        after the user selects an operation that genuinely needs them.
        """
        current_key=sample_key(sample)
        if current_key not in self.library_selected_keys:
            self.clear_library_tile_selection(); self.set_library_tile_checked(current_key,True); self._last_tile_anchor=current_key
        selected_keys=set(self.library_selected_keys) or {current_key}
        selected_count=len(selected_keys)
        _sample_cache={}

        def ordered_selected_keys():
            # Find selection/order is owned by the active task result snapshot.
            if self.active_tool_index()==1 and self.find_mode_active:
                result=[k for k in self._find_result_keys() if k in selected_keys]
                return result or [current_key]
            # Ordering matters only to downstream analytical views.  Delay the
            # scalar-key query until such an action is actually chosen.
            if self._formal_library_paging_supported():
                try:
                    ordered=self._formal_library_visible_keys() or []
                    result=[key for key in ordered if key in selected_keys]
                    return result or [current_key]
                except Exception:
                    return list(selected_keys) or [current_key]
            try:
                pool=self.filtered_samples()
                result=[sample_key(x) for x in pool if sample_key(x) in selected_keys]
                return result or [current_key]
            except Exception:
                return list(selected_keys) or [current_key]

        def chosen_samples(full=True):
            cache_key='full' if full else 'light'
            if cache_key in _sample_cache:return _sample_cache[cache_key]
            keys=ordered_selected_keys()
            if self._formal_library_paging_supported():
                try:
                    if not full and self.illuminant=='D65' and int(self.observer)==10:
                        values=self.store.load_index_samples_by_keys(keys)
                    else:
                        values=self.store.load_samples_by_keys(keys)
                    _sample_cache[cache_key]=values or [sample]
                except Exception:_sample_cache[cache_key]=[sample]
            else:
                try:
                    bykey={sample_key(x):x for x in self.filtered_samples()}
                    _sample_cache[cache_key]=[bykey[k] for k in keys if k in bykey] or [sample]
                except Exception:_sample_cache[cache_key]=[sample]
            return _sample_cache[cache_key]

        if self.active_tool_index()==1 and self.find_mode_active:
            menu=QMenu(self)
            if selected_count>1:
                head=menu.addAction(f'已选 {selected_count} 个查询结果'); head.setEnabled(False); menu.addSeparator()
            details=menu.addAction('查看测色明细') if selected_count==1 else None
            compare_act=menu.addAction('与当前标准比较    Enter')
            copy_act=menu.addAction(f'复制 {selected_count} 个色样    Ctrl+C')
            analysis=menu.addMenu('分析')
            view2d=analysis.addAction(f'2D Lab 色彩空间（{selected_count}）')
            view3d=analysis.addAction(f'3D Lab 色彩空间（{selected_count}）')
            addwb=analysis.addAction('加入比色工作台…')
            menu.addSeparator()
            select_all_act=menu.addAction('全选查询结果    Ctrl+A')
            clear_act=menu.addAction('取消选择    Esc')
            remove_result_act=menu.addAction('从当前查询结果移除    Delete')
            menu.addSeparator(); exportm=menu.addMenu('导出'); ex=exportm.addAction('Excel'); qx=exportm.addAction('QTX')
            chosen=menu.exec(global_pos)
            if details is not None and chosen==details:self.show_sample_details_dialog(sample)
            elif chosen==copy_act:self.copy_selected_library_tiles()
            elif chosen==compare_act:self.compare_find_selection()
            elif chosen==view2d:Lab2DDialog(chosen_samples(),self).exec()
            elif chosen==view3d:self.open_lab3d_window(chosen_samples())
            elif chosen==addwb:self.compare_find_selection()
            elif chosen==select_all_act:self.select_all_library_tiles()
            elif chosen==clear_act:self.clear_library_tile_selection()
            elif chosen==remove_result_act:self.remove_selected_find_results()
            elif chosen==ex:self.export_find_results_excel()
            elif chosen==qx:self.export_find_results_qtx()
            return

        menu=QMenu(self)
        if selected_count>1:
            head=menu.addAction(f'已选 {selected_count} 个色样'); head.setEnabled(False); menu.addSeparator()
        details=menu.addAction('查看测色明细') if selected_count==1 else None
        find_act=menu.addAction('查色' if selected_count==1 else f'查色 / 找色（{selected_count} 个标准）')
        copy_act=menu.addAction(f'复制 {selected_count} 个色样    Ctrl+C')
        favorite_keys=self._nav_items('favorite_samples')
        favorite_key_set=set(favorite_keys)
        all_samples_favorite=all(k in favorite_key_set for k in selected_keys)
        favorite=menu.addAction('取消收藏' if all_samples_favorite else f'收藏选中色样（{selected_count}）')

        # source_file is encoded in sample_key, so QTX-favourite labels can also
        # be built without loading full Sample payloads.
        saved_paths={str(Path(row.path).resolve()) for row in self.store.list_files()}
        selected_source_paths={key.rsplit('|',1)[0] for key in selected_keys if '|' in key}
        qtx_sources={str(Path(path).resolve()) for path in selected_source_paths if str(Path(path).resolve()) in saved_paths}
        file_favorites=self._nav_items('favorite_qtx')
        favorite_qtx=menu.addAction('取消收藏所在 QTX' if qtx_sources and qtx_sources.issubset(file_favorites)
                                    else f'收藏所在 QTX（{len(qtx_sources)}）') if qtx_sources else None
        add_menu=menu.addMenu('加入…')
        add_wb=add_menu.addAction('比色工作台…')
        add_card=add_menu.addAction('色卡编排（拖入已打开方案）')
        view2d=add_menu.addAction(f'2D Lab 查看（{selected_count}）')
        view3d=add_menu.addAction(f'3D Lab 查看（{selected_count}）')
        customer_mgr=menu.addAction('管理所在客户 QTX…') if self.is_admin and selected_count==1 else None
        menu.addSeparator(); select_all_act=menu.addAction('全选当前客户筛选结果    Ctrl+A')
        hide=menu.addAction(f'从当前视图隐藏 {selected_count} 个色样')
        # DG4.2: official libraries are immutable; formal-library deletion is an
        # Administrator-only destructive action.  Keep "hide from current view"
        # as a separate, explicitly named non-destructive command.
        delete=(menu.addAction(f'从正式色库删除 {selected_count} 个色样…')
                if self.is_admin and self.library_scope=='正式色库' else None)
        chosen=menu.exec(global_pos)
        if chosen==select_all_act:self.select_all_library_tiles(); return
        if details is not None and chosen==details:self.show_sample_details(sample); return
        if chosen==find_act:
            samples = chosen_samples(full=True)
            customer = str(getattr(self, 'active_library_customer', '') or '').strip()
            if customer.startswith('官方色库/'):
                source_name = customer.split('/', 1)[1]
            elif customer and customer not in {'全部客户', '请选择客户'}:
                source_name = customer
            else:
                source_name = '官方色库' if getattr(self, 'library_scope', '') == '官方色库' else '正式色库'
            title = source_name if len(samples) == 1 else f'{source_name} · {len(samples)} 个标准'
            self._add_find_library_session(samples, title)
            self.show_find_page()
            self.statusBar().showMessage(f'已把选中的 {len(samples)} 个色样加入查色标准；点击【查询】开始查找',5000)
            return
        if chosen==copy_act:self.copy_selected_library_tiles(); return
        if chosen==favorite:
            if all_samples_favorite:
                favorite_keys=[k for k in favorite_keys if k not in selected_keys]
            else:
                favorite_keys=list(dict.fromkeys(list(selected_keys)+favorite_keys))
            self._set_nav_items('favorite_samples',favorite_keys)
            self.statusBar().showMessage('我的收藏已更新',3000); return
        if favorite_qtx is not None and chosen==favorite_qtx:
            favorites=self._nav_items('favorite_qtx')
            if qtx_sources and qtx_sources.issubset(favorites):favorites=[p for p in favorites if p not in qtx_sources]
            else:favorites=list(dict.fromkeys(list(qtx_sources)+favorites))
            self._set_nav_items('favorite_qtx',favorites)
            self.statusBar().showMessage(f'已更新 {len(qtx_sources)} 个 QTX 文件的收藏',3000); return
        if chosen==view2d:Lab2DDialog(chosen_samples(full=not (self.illuminant=='D65' and int(self.observer)==10)),self).exec(); return
        if chosen==view3d:
            self.open_lab3d_library_keys(ordered_selected_keys(), fallback_sample=sample)
            return
        if chosen==add_card:self.add_samples_to_color_card(chosen_samples()); return
        if chosen==add_wb:self._menu_add_sample_to_workbench(chosen_samples(),global_pos); return
        if customer_mgr is not None and chosen==customer_mgr:
            customer=self.customer_for_sample(sample)
            if customer:CustomerQtxManagerDialog(self,customer,self).exec()
            else:QMessageBox.information(self,'客户 QTX','该色样不属于已保存客户。')
            return
        if chosen==hide:
            self.hidden_cards.update(selected_keys); self.clear_library_tile_selection(); self._tiles_keys=None; self.build_tiles(); return
        if delete is not None and chosen==delete:self.delete_selected_library_samples(); return

    def visible_library_tiles(self):
        # 查色结果与色库卡片使用独立控件池，避免左右键/选择状态串线。
        if self.active_tool_index()==1 and hasattr(self,'_find_tile_pool'):
            return [t for t in self._find_tile_pool.values() if t.isVisible()]
        if not hasattr(self,'tile_host'):
            return []
        keys=self._tiles_keys or []
        return [self._tile_pool[k] for k in keys if k in self._tile_pool and self._tile_pool[k].isVisible()]

    def _describe_3d_source(self, samples):
        """Human-readable source for a 3D view: prefer one saved customer, then one QTX filename."""
        samples=list(samples or [])
        if not samples:
            return ''
        paths=[]
        for sm in samples:
            src=str(getattr(sm,'source_file','') or '')
            if src and src not in paths:
                paths.append(src)
        customer_by_path={}
        try:
            for row in self.store.list_files():
                try:key=str(Path(row.path).resolve())
                except Exception:key=str(row.path)
                customer_by_path[key]=row.customer
        except Exception:
            pass
        customers=[]
        for src in paths:
            try:key=str(Path(src).resolve())
            except Exception:key=src
            customer=customer_by_path.get(key)
            if not customer:
                info=self.loaded_files.get(key) or self.loaded_files.get(src)
                if info and info.get('saved'):
                    customer=info.get('customer')
            if customer and customer not in customers:
                customers.append(customer)
        if len(customers)==1:
            if len(paths)==1:
                return f'{customers[0]} · {Path(paths[0]).name}'
            return f'{customers[0]} · {len(paths)} 个 QTX'
        if len(paths)==1:
            return Path(paths[0]).name
        if 1 < len(paths) <= 3:
            return ' + '.join(Path(x).name for x in paths)
        if len(paths)>3:
            return f'{len(paths)} 个 QTX'
        return ''

    def open_lab2d_window(self, samples):
        """Open 2D without making the colour-detail window modal or always-on-top."""
        samples=list(samples or [])
        if not samples:return
        dlg=Lab2DDialog(samples,self); dlg.setAttribute(Qt.WA_DeleteOnClose,True)
        self._lab2d_windows.append(dlg)
        dlg.destroyed.connect(lambda *_args,d=dlg:self._lab2d_windows.remove(d) if d in self._lab2d_windows else None)
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def open_lab3d_library_keys(self, keys, fallback_sample=None):
        """Open 3D from the P3-2 lightweight index whenever D65/10° is active.

        The 3D viewer needs Lab/name/source only; loading 3,500 full JSON spectral
        payloads just to draw their coordinates causes avoidable latency.  For
        non-D65/10° conditions we keep the mature full-payload path because the
        spectrum is required to recalculate Lab.
        """
        keys=[str(k) for k in (keys or []) if k]
        if not keys:
            if fallback_sample is not None: self.open_lab3d_window([fallback_sample])
            return
        try:
            if self.illuminant=='D65' and int(self.observer)==10:
                samples=self.store.load_index_samples_by_keys(keys)
            else:
                samples=self.store.load_samples_by_keys(keys)
        except Exception:
            samples=[]
        if not samples and fallback_sample is not None:
            samples=[fallback_sample]
        self.open_lab3d_window(samples)

    def open_lab3d_window(self, samples):
        """Open the unified Qt Quick 3D/RHI CIELAB viewer.

        Hotfix36 deliberately removes the old "new white GPU dialog -> close ->
        reopen legacy black QPainter dialog" path.  The new dialog owns both the
        RHI viewport and its same-window compatibility renderer, so the UI never
        changes underneath the user when a graphics backend is unavailable.
        """
        samples=list(samples or [])
        if not samples:
            return
        context=self._describe_3d_source(samples)
        if not hasattr(self,'_lab3d_windows'):
            self._lab3d_windows=[]

        if QuickLab3DDialog is None:
            QMessageBox.warning(
                self,
                f'3D 色彩空间 · {BUILD_LABEL}',
                'Hotfix43 使用统一 Qt Quick 3D / RHI 色彩空间界面。\n\n'
                '当前 Qt Quick 3D / RHI 模块未能加载，因此本次不会退回旧 Lab3DDialog。\n\n'
                f'导入错误：{QUICK3D_IMPORT_ERROR or "unknown"}\n\n'
                f'构建：{BUILD_ID}'
            )
            return

        try:
            if getattr(QuickLab3DDialog, '__module__', '') != 'qtx_app.lab3d_quick':
                raise RuntimeError(f'运行时载入了错误的 3D 模块：{getattr(QuickLab3DDialog, "__module__", "?")}')
            dlg=QuickLab3DDialog(
                samples, self, context,
                color_fn=sample_display_qcolor,
                key_fn=sample_key,
            )
        except Exception as exc:
            QMessageBox.warning(self,f'3D 色彩空间 · {BUILD_LABEL}',f'{BUILD_LABEL} Quick3D 初始化失败：\n{exc}\n\n构建：{BUILD_ID}')
            return

        dlg.setAttribute(Qt.WA_DeleteOnClose,True)
        self._lab3d_windows.append(dlg)
        dlg.destroyed.connect(
            lambda *_args,d=dlg:self._lab3d_windows.remove(d)
            if d in self._lab3d_windows else None
        )
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def set_library_tile_checked(self,key,checked):
        for tile in self.visible_library_tiles():
            if sample_key(tile.sample)==key:
                tile.blockSignals(True); tile.setChecked(checked); tile.blockSignals(False); tile.update(); break
        if checked: self.library_selected_keys.add(key)
        else: self.library_selected_keys.discard(key)
        if getattr(self,'find_mode_active',False) and self.active_find_session_id:
            session=next((x for x in self.find_sessions if x['id']==self.active_find_session_id),None)
            if session is not None:
                session['selected_keys']=set(self.library_selected_keys)
                cache=self._find_current_cache(session,self.find_standard)
                if cache is not None:cache['selected_keys']=set(self.library_selected_keys)
        self.selection_label.setText(f"已选 {len(self.library_selected_keys)} 个色样")
        if getattr(self,'find_mode_active',False) and hasattr(self,'find_result_summary'):
            self.find_result_summary.setText(f"查询结果：{len(self.filtered_samples())} 个 · 已选 {len(self.library_selected_keys)} 个")

    def clear_library_tile_selection(self):
        self.library_selected_keys.clear()
        if getattr(self,'find_mode_active',False) and self.active_find_session_id:
            session=next((x for x in self.find_sessions if x['id']==self.active_find_session_id),None)
            if session is not None:
                session['selected_keys']=set()
                cache=self._find_current_cache(session,self.find_standard)
                if cache is not None:cache['selected_keys']=set()
        for tile in self.visible_library_tiles():
            tile.blockSignals(True); tile.setChecked(False); tile.blockSignals(False); tile.update()
        if hasattr(self,'selection_label'): self.selection_label.setText('已选 0 个色样')
        if getattr(self,'find_mode_active',False):
            self.find_focus_key=None
            self._refresh_find_multi_overview()

    def handle_library_tile_click(self,tile,modifiers):
        key=sample_key(tile.sample); visible=[sample_key(t.sample) for t in self.visible_library_tiles()]
        if modifiers & Qt.ShiftModifier and getattr(self,'_last_tile_anchor',None) in visible:
            if not (modifiers & Qt.ControlModifier): self.clear_library_tile_selection()
            a=visible.index(self._last_tile_anchor); b=visible.index(key)
            for k in visible[min(a,b):max(a,b)+1]: self.set_library_tile_checked(k,True)
        elif modifiers & Qt.ControlModifier:
            self.set_library_tile_checked(key,key not in self.library_selected_keys); self._last_tile_anchor=key
        else:
            self.clear_library_tile_selection(); self.set_library_tile_checked(key,True); self._last_tile_anchor=key
        tile.setFocus()
        if getattr(self,'find_mode_active',False):
            self.find_focus_key=key
            self._refresh_find_multi_overview()

    def select_all_library_tiles(self):
        # Find selection belongs to the active task snapshot, never to the
        # formal-library page/filter that happened to be visible previously.
        if getattr(self,'find_mode_active',False) and self.active_tool_index()==1:
            keys=self._find_result_keys()
            self.library_selected_keys=set(keys)
            session=self._current_find_session();cache=self._find_current_cache(session,self.find_standard)
            if cache is not None:cache['selected_keys']=set(keys)
            if session is not None:session['selected_keys']=set(keys)
            for tile in self.visible_library_tiles():
                key=sample_key(tile.sample);tile.blockSignals(True);tile.setChecked(key in self.library_selected_keys);tile.blockSignals(False);tile.update()
            self.selection_label.setText(f'已选 {len(self.library_selected_keys)} 个色样')
            self._refresh_find_multi_overview();return
        # Select the entire current customer/filter result, including pages
        # whose widgets have not been constructed yet. P3-1 reads only scalar keys.
        keys=self._formal_library_visible_keys()
        if keys is None:
            keys=[sample_key(sm) for sm in self.filtered_samples() if sample_key(sm) not in self.hidden_cards]
        self.library_selected_keys.update(keys)
        for tile in self.visible_library_tiles():
            key=sample_key(tile.sample)
            tile.blockSignals(True);tile.setChecked(key in self.library_selected_keys);tile.blockSignals(False);tile.update()
        self.selection_label.setText(f'已选 {len(self.library_selected_keys)} 个色样')
        if getattr(self,'find_mode_active',False): self._refresh_find_multi_overview()

    def copy_selected_library_tiles(self):
        if getattr(self,'find_mode_active',False) and self.active_tool_index()==1:
            keys=[k for k in self._find_result_keys() if k in self.library_selected_keys]
            if not keys:return
            mime=QMimeData();mime.setData(CARD_MIME,'\n'.join(keys).encode('utf-8'));mime.setText('\n'.join(keys))
            QApplication.clipboard().setMimeData(mime)
            self.statusBar().showMessage(f'已复制 {len(keys)} 个查色结果',3000);return
        visible_keys=self._formal_library_visible_keys()
        if visible_keys is None:
            keys=[sample_key(sm) for sm in self.filtered_samples() if sample_key(sm) in self.library_selected_keys]
        else:
            keys=[key for key in visible_keys if key in self.library_selected_keys]
        if not keys:return
        mime=QMimeData(); mime.setData(CARD_MIME,'\n'.join(keys).encode('utf-8')); mime.setText('\n'.join(keys))
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(f'已复制 {len(keys)} 个色样，可粘贴至色卡方案',3000)

    def customer_for_sample(self,sample):
        sp=str(Path(sample.source_file).resolve()) if sample.source_file and '://' not in sample.source_file else sample.source_file
        if not getattr(self, '_saved_customer_by_path', None):
            self.update_customers()
        return self._saved_customer_by_path.get(sp)

    def remove_selected_find_results(self):
        if not self.find_mode_active or not self.library_selected_keys:return
        session=self._current_find_session()
        if not session:return
        remove=set(self.library_selected_keys);cache=self._find_current_cache(session,self.find_standard)
        if cache is not None:
            cache['result_keys']=[k for k in cache.get('result_keys',[]) if k not in remove]
            for k in remove:cache.get('scores',{}).pop(k,None)
            cache['selected_keys']=set()
            session['result_keys']=list(cache['result_keys']);session['scores']=dict(cache.get('scores',{}))
        session['selected_keys']=set();self.library_selected_keys.clear();self.find_scores=dict(session.get('scores',{}));self._tiles_keys=None;self.build_find_tiles()
        self.statusBar().showMessage(f'已从当前查询结果移除 {len(remove)} 个色样；不会删除色库数据',3000)

    def delete_selected_library_samples(self):
        # Persistent per-sample deletion is supported only for the organisation's
        # formal library.  Official libraries are vendor/system assets and remain
        # read-only even for Administrators.
        if self.library_scope!='正式色库':
            self.statusBar().showMessage('官方色库为只读数据，不能删除色样',4500)
            return
        if not self.require_admin('从正式色库删除色样'): return
        keys=set(self.library_selected_keys)
        if not keys: return
        if QMessageBox.question(self,'删除色样',f'确定从{self.library_scope}删除选中的 {len(keys)} 个色样吗？\n电脑上的原 QTX 文件不会删除。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        # 正式数据库删快照色样；当前工作区同步移除。临时色样只从当前工作区移除。
        try: self.store.remove_samples(list(keys))
        except Exception: pass
        if hasattr(self,'_p3_full_sample_cache'):
            for key in keys:self._p3_full_sample_cache.pop(key,None)
        for path,info in list(self.loaded_files.items()):
            info['samples']=[x for x in info['samples'] if sample_key(x) not in keys]
        self.clear_library_tile_selection(); self.rebuild_samples(); self._tiles_keys=None; self.build_tiles(); self.refresh_card_source()
        self.statusBar().showMessage('已删除选中色样；原 QTX 文件未删除',4500)

    def make_library_snapshot_path(self,name):
        safe=''.join(c if c not in '<>:"/\\|?*' else '_' for c in name).strip() or '未命名.qtx'
        folder=self.store.path.parent/'library_snapshots'/uuid.uuid4().hex; folder.mkdir(parents=True,exist_ok=True)
        return str((folder/safe).resolve())

    def show_customer_context_menu(self,pos):
        if not self.require_admin('维护客户与正式色库'): return
        selected=self.customer.currentText()
        customer=selected if selected not in {'请选择客户','所有分类',''} else self.active_library_customer
        menu=QMenu(self)
        open_act=menu.addAction('打开为客户标签'); open_act.setEnabled(bool(customer))
        manage=menu.addAction('管理客户 QTX…'); manage.setEnabled(bool(customer) and customer!='全部客户')
        chosen=menu.exec(self.customer.mapToGlobal(pos))
        if chosen==open_act: self.open_library_customer(customer)
        elif chosen==manage: CustomerQtxManagerDialog(self,customer,self).exec(); self.update_customers(); self._tiles_keys=None; self.build_tiles()

    def toggle_sample(self, key, checked):
        if checked:self.library_selected_keys.add(key)
        else:self.library_selected_keys.discard(key)
        if self.find_mode_active and self.active_find_session_id:
            session=next((x for x in self.find_sessions if x['id']==self.active_find_session_id),None)
            if session is not None:
                session['selected_keys']=set(self.library_selected_keys)
                if self.find_standard is not None:
                    cache=session.setdefault('per_standard',{}).setdefault(sample_key(self.find_standard),{'result_keys':list(session.get('result_keys',[])),'scores':dict(session.get('scores',{})),'selected_keys':set()})
                    cache['selected_keys']=set(self.library_selected_keys)
        self.selection_label.setText(f"已选 {len(self.library_selected_keys)} 个色样")
        if self.find_mode_active:self._refresh_find_multi_overview()

    def _selected_library_samples_for_find(self):
        """Resolve the currently checked library cards as full find standards.

        The library page intentionally keeps lightweight rows for paging.  Find
        can change illuminant/observer, so only the selected keys are hydrated
        here.  This is also the bridge that Hotfix58's top "查色 / 找色" button
        was missing: it navigated away before consuming library_selected_keys.
        """
        selected = set(getattr(self, 'library_selected_keys', set()) or set())
        if not selected:
            return []
        ordered = []
        for key in list(getattr(self, '_tiles_keys', None) or []):
            if key in selected and key not in ordered:
                ordered.append(key)
        # Preserve every selection even if it was made through Ctrl+A or a prior
        # page/filter; deterministic fallback order keeps task tabs stable.
        ordered.extend(sorted((k for k in selected if k not in set(ordered)), key=str.casefold))
        return self._samples_by_keys_on_demand(ordered, full=True)

    def _add_find_library_session(self, samples, title):
        """Create one multi-standard find task from an arbitrary library selection.

        Do not use the first sample's QTX source as the merge key: a customer or
        official-library view may contain cards from several QTX files.  The task
        therefore owns exactly the cards the user selected at this moment.
        """
        samples = [x for x in list(samples or []) if x is not None]
        if not samples:
            return None
        sid = str(uuid.uuid4())
        session = {
            'id': sid, 'samples': samples, 'sample_index': 0, 'sample': samples[0],
            'title': title, 'source_file': '', 'source_key': '',
            'queried': False, 'needs_query': False, 'result_keys': [], 'scores': {},
            'per_standard': {}, 'selected_keys': set(),
            'scope_selection': None if self.find_scope_selection is None else list(self.find_scope_selection),
            'illuminant': self.find_illuminant.currentText() if hasattr(self, 'find_illuminant') else 'D65',
            'observer': 2 if hasattr(self, 'find_observer') and self.find_observer.currentText() == '2°' else 10,
            'query_params': self._find_query_params_from_ui(),
        }
        self.find_sessions.append(session)
        idx = self.find_tabs.addTab(title)
        self.find_tabs.setTabData(idx, sid)
        self.find_tabs.blockSignals(True); self.find_tabs.setCurrentIndex(idx); self.find_tabs.blockSignals(False)
        self.active_find_session_id = sid
        self._activate_find_session(session)
        return session

    def start_find_from_library_selection(self, *_args):
        """Library toolbar Find button: selected cards become the standards.

        With no selected cards the button keeps its old behaviour and simply
        opens the Find workspace.  With one or more selected cards it creates a
        single multi-standard task, then opens Find with those actual standards
        already present.
        """
        if not self.can_use('find'):
            return
        # The toolbar lives on the library page.  Capture selection before
        # show_find_page() switches tools, because tool switching deliberately
        # restores the Find task's own selection state.
        samples = self._selected_library_samples_for_find() if self.active_tool_index() == 0 else []
        if samples:
            customer = str(getattr(self, 'active_library_customer', '') or '').strip()
            if customer.startswith('官方色库/'):
                source_name = customer.split('/', 1)[1]
            elif customer and customer not in {'全部客户', '请选择客户'}:
                source_name = customer
            else:
                source_name = '官方色库' if getattr(self, 'library_scope', '') == '官方色库' else '正式色库'
            title = source_name if len(samples) == 1 else f'{source_name} · {len(samples)} 个标准'
            self._add_find_library_session(samples, title)
            self.library_has_explicit_view = True
            self.find_source.setCurrentText('QTX色样')
            self.update_find_source_ui()
            if hasattr(self, 'library_sort_widget'):
                self.library_sort_widget.hide()
            self.show_find_page()
            self.statusBar().showMessage(
                f'已把选中的 {len(samples)} 个色样加入查色标准；点击【查询】开始查找', 5000
            )
            return
        self.show_find_page()

    def show_find_page(self):
        if not self.can_use('find'):return
        self.open_tool_tab(1,'查色 / 找色'); self.nav_find.setChecked(True)
        # 查色页独立于色库：只有当前查色任务自己的标准、结果和选择状态会被恢复。
        if self.active_find_session_id:
            session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
            if session:self._activate_find_session(session)
        else:
            self.find_standard=None; self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear()
            self._update_find_standard_label(); self.build_find_tiles()
        self.find_panel.show()

    def show_page(self, index):
        permission={0:'library_view',1:'find',2:'cards',3:'compare',4:'spectrum'}.get(index)
        if permission and not self.can_use(permission):return
        old_index=self.active_tool_index()
        if old_index==1 and index!=1 and self.active_find_session_id:
            session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
            if session is not None: session['selected_keys']=set(self.library_selected_keys)
        if index!=1:
            # 离开查色页后关闭查色渲染状态；任务数据保存在 session 中，返回时再恢复。
            self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear()
        titles={0:'正式色库管理',1:'查色 / 找色',2:'色卡编排',3:'比色工作台',4:'光谱分析'}
        self.open_tool_tab(index,titles.get(index,'工具'))
        self.nav_library.setChecked(index == 0)
        if hasattr(self,'nav_find'): self.nav_find.setChecked(index == 1)
        self.nav_cards.setChecked(index == 2)
        self.nav_compare.setChecked(index == 3)
        self.nav_spectrum.setChecked(index == 4)
        if index == 0:
            self.library_has_explicit_view = bool(self.active_library_customer)
            self._tiles_keys = None
            self.build_tiles()
        elif index == 1:
            # 查色页只恢复查色任务自己的状态，绝不借用刚打开的色库/色卡选择。
            self.find_panel.show()
            if self.active_find_session_id:
                session=next((x for x in self.find_sessions if x.get('id')==self.active_find_session_id),None)
                if session:self._activate_find_session(session)
            else:
                self.find_standard=None; self.find_mode_active=False; self.find_scores={}; self.library_selected_keys.clear(); self._update_find_standard_label(); self.build_find_tiles()
        elif index == 2:
            self.refresh_card_source()
            self.refresh_color_card_list()
        elif index == 3:
            # Tabs keep their live widgets and cached calculations; refresh_tabs
            # calculates only the active tab if its data actually changed.
            self.refresh_workbench_tabs()
        elif index == 4:
            self.update_spectrum()

    def _refresh_library_condition_status(self):
        if not hasattr(self, 'library_condition_label'):
            return
        from .condition_status import condition_status
        text, tooltip, approximate = condition_status(self.illuminant, int(self.observer))
        failures = getattr(self, '_preview_condition_failures', {})
        relevant = [reason for (signature, illuminant, observer), reason in failures.items()
                    if illuminant == self.illuminant and observer == self.observer]
        if relevant:
            text += f' · {len(relevant)} 个色样显示 D65/10° 回退值'
            tooltip += '\n回退值不能代表所选光源下的结果：\n' + '\n'.join(relevant[:5])
        self.library_condition_label.setText(text)
        self.library_condition_label.setToolTip(tooltip)
        self.library_condition_label.setStyleSheet(
            'font-size:12px;color:#92400E;background:#FFFBEB;padding:3px 6px;border-radius:4px;'
            if approximate or relevant else
            'font-size:12px;color:#334155;background:#EFF6FF;padding:3px 6px;border-radius:4px;')

    def conditions_changed(self):
        self._library_data_revision += 1
        self._library_view_cache = None
        self.illuminant = self.light.currentText()
        self.observer = 2 if self.observer_box.currentText() == "2°" else 10
        self._lab_cache.clear()
        self._preview_condition_failures = {}
        self._p3_full_sample_cache = {}
        self._refresh_library_condition_status()
        self._xyz_cache.clear()
        self._hue_order_cache.clear()
        self._sample_icon_cache.clear()
        for tile in self._tile_pool.values():
            tile.refresh_color()
        # 只有当前正在看比色页时立即重绘；其他页面切换进去时再刷新。
        if self.active_tool_index()==3:
            self.refresh_all_workbenches()
        if hasattr(self,'card_layout'):
            self.refresh_card_source()
        # Unified-workspace swatches are independent documents. Their delegates
        # query sample_lab() at paint time, so a lightweight repaint is enough
        # after the colour-condition caches are cleared.
        if hasattr(self,'workspace_tabs'):
            for i in range(self.workspace_tabs.count()):
                doc=self.workspace_tabs.widget(i)
                if isinstance(doc,WorkspaceDocument):
                    doc.list.viewport().update()

    @staticmethod
    def _munsell_spectrum_fingerprint(sample):
        h=hashlib.sha1(); h.update(b'Munsell-C-2-v1|')
        for w,r in zip(sample.wavelengths or (),sample.reflectance or ()):
            h.update(f'{float(w):.6f}:{float(r):.10f};'.encode('ascii'))
        return h.hexdigest()

    def munsell_cache_lookup(self,sample):
        try:
            raw=self._munsell_persistent_cache.get(self._munsell_spectrum_fingerprint(sample))
            if isinstance(raw,list) and len(raw)>=5:return tuple(raw[:5])
        except Exception:pass
        return None

    def munsell_cache_store(self,sample,info):
        try:
            fp=self._munsell_spectrum_fingerprint(sample); value=list(info[:5])
            if self._munsell_persistent_cache.get(fp)!=value:
                self._munsell_persistent_cache[fp]=value; self._munsell_cache_dirty=True
        except Exception:pass

    def schedule_munsell_cache_flush(self):
        if self._munsell_cache_dirty:
            try:self._munsell_cache_flush_timer.start()
            except RuntimeError:pass

    def _flush_munsell_cache(self):
        if not getattr(self,'_munsell_cache_dirty',False):return
        try:
            self._munsell_cache_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self._munsell_cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._munsell_persistent_cache,ensure_ascii=False,allow_nan=True,separators=(',',':')),encoding='utf-8')
            tmp.replace(self._munsell_cache_path); self._munsell_cache_dirty=False
        except Exception:pass

    def sample_xyz(self, s):
        if not (self.illuminant == 'D65' and self.observer == 10):
            if str((s.raw or {}).get('__P3_LIGHTWEIGHT__', '')) == '1':
                s = self._full_library_sample(s)
        key=(self._science_sample_signature(s), self.illuminant, self.observer)
        if key not in self._xyz_cache:
            if self.illuminant == "D65" and self.observer == 10 and s.kind != "AVERAGE":
                self._xyz_cache[key] = s.xyz_d65_10
            else:
                self._xyz_cache[key] = reflectance_to_xyz_lab(s.reflectance, self.illuminant, s.wavelengths, self.observer)[0]
        return self._xyz_cache[key]

    def set_hue_order_method(self, method):
        """兼容旧设置。v0.8.5 起 UI 固定使用 Munsell，不再联动色库/色卡编排。"""
        self.hue_order_method = "Munsell"

    def _perceptual_hue_info(self, sample):
        """Munsell Hue（C/2°）：彩色按 Hue；近中性色按 L*；无法映射样本置后。"""
        ck=(sample_key(sample), "Munsell-C-2-v085")
        if ck in self._hue_order_cache:return self._hue_order_cache[ck]
        persisted=self.munsell_cache_lookup(sample)
        if persisted is not None:
            self._hue_order_cache[ck]=persisted; return persisted
        L,a,b=self.sample_lab(sample); C=math.hypot(a,b)
        # 低彩度时 hue 本身不稳定；作为“近中性色”进入独立组。
        if C < 3.0:
            info=(True, float("inf"), "近中性色 · C*<3 · 按 L* 排列", L, C)
        else:
            try:
                mkey=(sample_key(sample), "Munsell-C-2")
                if mkey not in self._xyz_cache:
                    self._xyz_cache[mkey]=reflectance_to_xyz_lab(sample.reflectance, "C", sample.wavelengths, 2)[0]
                idx, notation, value, chroma=munsell_hue_order_from_xyz(self._xyz_cache[mkey])
                neutral=(not math.isfinite(idx)) or chroma < 1.5
                info=(True, idx, f"Munsell {notation} · 近中性(C<1.5) · 按 L* 排列", L, C) if neutral else (False, idx, f"Munsell {notation} · C/2°", value, chroma)
            except Exception:
                info=(False, float("nan"), "Munsell 无法映射 · 按 L* 置后", L, C)
        self._hue_order_cache[ck]=info; self.munsell_cache_store(sample,info); self.schedule_munsell_cache_flush()
        return info

    def _sort_samples_by_perceptual_hue(self, samples, reverse=False):
        """实体色卡：Munsell彩色 → 近中性色(L*亮→暗) → 无法映射(L*亮→暗)。"""
        chromatic=[]; neutral=[]; fallback=[]
        for x in samples:
            info=self._perceptual_hue_info(x)
            if str(info[2]).startswith("Munsell 无法映射"):
                fallback.append((info,x))
            elif info[0]:
                neutral.append((info,x))
            else:
                chromatic.append((info,x))
        def ck(z):
            info,x=z; idx=float(info[1]); family=int(idx//10) if math.isfinite(idx) else 99; pos=idx%10 if math.isfinite(idx) else 0.0
            return ((-family if reverse else family), (-pos if reverse else pos), -float(info[3]), -float(info[4]), x.display_name.casefold())
        chromatic.sort(key=ck)
        neutral.sort(key=lambda z:(-float(z[0][3]), -float(z[0][4]), z[1].display_name.casefold()))
        fallback.sort(key=lambda z:(-float(z[0][3]), -float(z[0][4]), z[1].display_name.casefold()))
        return [x for _,x in chromatic] + [x for _,x in neutral] + [x for _,x in fallback]

    def _perceptual_hue_label(self, sample):
        return self._perceptual_hue_info(sample)[2]

    def _workspace_document_by_uid(self, uid):
        uid=str(uid or '')
        if not uid:return None
        for sub in list(getattr(self,'_workspace_document_windows',[]) or []):
            try:doc=sub.widget()
            except RuntimeError:continue
            if isinstance(doc,WorkspaceDocument) and doc.document_uid==uid:return doc
        return None

    def _sample_by_any_key(self, key):
        if not key: return None
        for info in self.loaded_files.values():
            for x in info.get('samples',[]):
                if sample_key(x)==key:return x
        # P3-3: resolve formal-library keys directly. A drag or detail action
        # hydrates one requested sample instead of deserialising the whole library.
        try:
            stored=self.store.load_sample_by_key(str(key))
            if stored is not None:return stored
        except Exception:
            pass
        for wb in self.workbenches:
            for x in self._workbench_samples(wb):
                if sample_key(x)==key: return x
        # New/unsaved file windows and palette schemes are valid data sources
        # too.  Older drag payloads may carry only CARD_MIME, so keep this as a
        # compatibility fallback in addition to the serialized MIME snapshot.
        for sub in list(getattr(self,'_workspace_document_windows',[]) or []):
            try:doc=sub.widget()
            except RuntimeError:continue
            if isinstance(doc,WorkspaceDocument):
                for x in doc.samples:
                    if sample_key(x)==key:return x
        for win in list(getattr(self,'card_plan_windows',{}).values()):
            try:sm=win._all_samples().get(key)
            except (AttributeError,RuntimeError):sm=None
            if sm is not None:return sm
        return None

    def _add_samples_to_workbench_menu(self, menu, samples):
        sub=menu.addMenu("加入比色工作台")
        acts={}
        for wb in [w for w in self.workbenches if not w.get("is_collapsed")]:
            a=sub.addAction(wb.get("name","工作台")); acts[a]=wb
        if not acts:
            a=sub.addAction("（暂无工作台，请先新建）"); a.setEnabled(False)
        return acts

    def show_card_source_menu(self, pos):
        item=self.card_source.itemAt(pos)
        if item is None:return
        if not item.isSelected():self.card_source.clearSelection(); item.setSelected(True)
        selected=[i for i in self.card_source.selectedItems() if i.data(Qt.UserRole)]
        samples=[self._sample_by_any_key(i.data(Qt.UserRole)) for i in selected]; samples=[x for x in samples if x is not None]
        if not samples:return
        menu=QMenu(self)
        if len(samples)>1:
            head=menu.addAction(f'已选 {len(samples)} 个色样'); head.setEnabled(False); menu.addSeparator()
        details=menu.addAction('查看测色明细') if len(samples)==1 else None
        find=menu.addAction('查色') if len(samples)==1 else None
        plans=menu.addMenu('加入色卡方案…')
        plan_actions={}
        for cid,win in self.card_plan_windows.items():plan_actions[plans.addAction(win.card['name'])]=cid
        if not plan_actions:plans.addAction('（请先打开或新建方案）').setEnabled(False)
        view2d=menu.addAction(f'2D Lab 查看（{len(samples)}）')
        view3d=menu.addAction(f'3D Lab 查看（{len(samples)}）')
        add_wb=menu.addAction('加入比色工作台…') if len(samples)==1 else None
        chosen=menu.exec(self.card_source.viewport().mapToGlobal(pos))
        if details is not None and chosen==details:self.show_sample_details_dialog(samples[0])
        elif find is not None and chosen==find:self.set_find_standard(samples[0]); self.nav_library.click()
        elif chosen==view2d:Lab2DDialog(samples,self).exec()
        elif chosen==view3d:self.open_lab3d_window(samples)
        elif chosen in plan_actions:
            win=self.card_plan_windows[plan_actions[chosen]]
            win._embed_samples(samples)
            win.receive_drop_many([sample_key(sm) for sm in samples],max(0,win._last_occupied_index()+1),copy_mode=True)
        elif add_wb is not None and chosen==add_wb:self._menu_add_sample_to_workbench(samples[0],self.card_source.viewport().mapToGlobal(pos))

    def show_card_layout_menu(self, pos):
        item=self.card_layout.itemAt(pos)
        if item is None or not item.data(Qt.UserRole): return
        if not item.isSelected():
            self.card_layout.clearSelection(); item.setSelected(True)
        selected=[i for i in self.card_layout.selectedItems() if i.data(Qt.UserRole)]
        samples=[self._sample_by_any_key(i.data(Qt.UserRole)) for i in selected]; samples=[x for x in samples if x is not None]
        if not samples: return
        menu=QMenu(self); details=menu.addAction("查看测色明细"); find=menu.addAction("以当前色样查色"); view2d=menu.addAction(f"2D Lab 查看选中色样（{len(samples)}）"); view3d=menu.addAction(f"3D Lab 查看选中色样（{len(samples)}）")
        menu.addSeparator(); wb_actions=self._add_samples_to_workbench_menu(menu,samples); menu.addSeparator(); remove=menu.addAction(f"从当前布局移除（{len(samples)}）")
        chosen=menu.exec(self.card_layout.viewport().mapToGlobal(pos))
        if chosen==details: self.show_sample_details_dialog(samples[0])
        elif chosen==find: self.set_find_standard(samples[0]); self.nav_find.click()
        elif chosen==view2d: self.open_lab2d_window(samples)
        elif chosen==view3d: self.open_lab3d_window(samples)
        elif chosen==remove: self.remove_selected_from_card()
        elif chosen in wb_actions:
            self._append_samples_to_workbench(wb_actions[chosen],samples)

    def _append_samples_to_workbench(self, wb, samples):
        existing={sample_key(x) for x in self._workbench_samples(wb)}
        changed=False
        for x in samples:
            if sample_key(x) in existing: continue
            wb.setdefault("samples_data",[]).append(self._serialize_sample(x)); wb.setdefault("sample_keys",[]).append(sample_key(x)); existing.add(sample_key(x)); changed=True
        if changed:
            self.store.save_workbench(wb); self.refresh_workbench_tabs(); self.refresh_all_workbenches(); self.statusBar().showMessage(f"已加入 {len(samples)} 个色样到 {wb.get('name','工作台')}",3000)

    def drop_sample_keys_to_workbench(self, wb_id, keys):
        """Resolve the existing colour-block MIME payload and copy into a workbench."""
        wb=self._find_workbench(wb_id)
        if not wb:return
        samples=[]; seen=set()
        for key in keys or []:
            sm=self._sample_by_any_key(str(key))
            if sm is None or sample_key(sm) in seen:continue
            samples.append(sm); seen.add(sample_key(sm))
        if not samples:
            self.statusBar().showMessage('未能识别拖入的色块',3000); return
        self.active_workbench_id=wb_id
        self._append_samples_to_workbench(wb,samples)

    @staticmethod
    def _is_lightweight_library_sample(sample):
        try:return str((sample.raw or {}).get('__P3_LIGHTWEIGHT__',''))=='1'
        except Exception:return False

    def _full_library_sample(self, sample):
        """Hydrate a P3-3 display-only library sample only when heavy data is needed.

        A small bounded cache avoids repeatedly decoding the same spectrum when a
        user reopens details/analysis for a recently used colour.
        """
        if sample is None or not self._is_lightweight_library_sample(sample):
            return sample
        key=sample_key(sample)
        override=getattr(self,'sample_runtime_overrides',{}).get(key)
        if override is not None:return override
        cache=getattr(self,'_p3_full_sample_cache',None)
        if cache is None:
            cache={};self._p3_full_sample_cache=cache
        if key in cache:return cache[key]
        try: full=self.store.load_sample_by_key(key)
        except Exception: full=None
        if full is not None:
            cache[key]=full
            while len(cache)>96:cache.pop(next(iter(cache)))
            return full
        return sample

    def _full_library_samples(self, samples):
        items=list(samples or [])
        if not items:return []
        cache=getattr(self,'_p3_full_sample_cache',None)
        if cache is None:
            cache={};self._p3_full_sample_cache=cache
        result=[None]*len(items);missing_keys=[];missing_positions={}
        overrides=getattr(self,'sample_runtime_overrides',{})
        for i,x in enumerate(items):
            if not self._is_lightweight_library_sample(x):result[i]=x;continue
            key=sample_key(x)
            if key in overrides:result[i]=overrides[key]
            elif key in cache:result[i]=cache[key]
            else:
                missing_positions.setdefault(key,[]).append(i);missing_keys.append(key)
        if missing_keys:
            unique=list(dict.fromkeys(missing_keys))
            try:loaded=self.store.load_samples_by_keys(unique)
            except Exception:loaded=[]
            bykey={sample_key(x):x for x in loaded}
            for key,positions in missing_positions.items():
                value=bykey.get(key)
                if value is not None:
                    cache[key]=value
                    for i in positions:result[i]=value
            while len(cache)>96:cache.pop(next(iter(cache)))
        return [result[i] if result[i] is not None else items[i] for i in range(len(items))]

    def _samples_by_keys_on_demand(self, keys, *, full=True):
        """Resolve only the requested sample keys.

        Formal-library rows are fetched in one targeted SQLite query; workspace
        samples and runtime metadata overrides are then overlaid.  This is the
        preferred P3-3 path for palettes, favourites and cached find results.
        """
        ordered=list(dict.fromkeys(str(k) for k in (keys or []) if k))
        if not ordered:return []
        wanted=set(ordered);bykey={}
        try:
            loaded=(self.store.load_samples_by_keys(ordered) if full
                    else self.store.load_index_samples_by_keys(ordered))
            bykey.update((sample_key(x),x) for x in loaded)
        except Exception:
            pass
        for x in getattr(self,'samples',[]):
            k=sample_key(x)
            if k in wanted:bykey[k]=x
        for k,x in getattr(self,'sample_runtime_overrides',{}).items():
            if k in wanted:bykey[k]=x
        return [bykey[k] for k in ordered if k in bykey]

    def sample_lab(self, s):
        # 查色页面使用当前查色任务自己的光源/观察者；其它页面使用色库显示条件。
        on_find=self.active_tool_index()==1 and hasattr(self,'find_illuminant')
        illum=self.find_illuminant.currentText() if on_find else self.illuminant
        obs=(2 if hasattr(self,'find_observer') and self.find_observer.currentText()=='2°' else 10) if on_find else self.observer
        # Index-only display rows intentionally omit spectra. Fetch exactly this
        # measurement when a different light/observer requires spectral maths.
        if not (illum == 'D65' and obs == 10):
            if str((s.raw or {}).get('__P3_LIGHTWEIGHT__', '')) == '1':
                s = self._full_library_sample(s)
        key=(self._science_sample_signature(s),illum,obs)
        if key not in self._lab_cache:
            try:
                if illum=='D65' and obs==10 and s.kind!='AVERAGE': self._lab_cache[key]=s.lab_d65_10
                else:self._lab_cache[key]=reflectance_to_xyz_lab(s.reflectance,illum,s.wavelengths,obs)[1]
            except Exception as exc:
                # Legacy display fallback remains explicit. Pair analysis uses the
                # stricter core path and never treats this fallback as measurement.
                self._lab_cache[key]=s.lab_d65_10
                if not hasattr(self, '_preview_condition_failures'):
                    self._preview_condition_failures = {}
                self._preview_condition_failures[key] = f'{s.display_name}: {exc}'
                if hasattr(self, '_refresh_library_condition_status'):
                    self._refresh_library_condition_status()
        return self._lab_cache[key]

    def _dropped_local_paths(self, event):
        if not event.mimeData().hasUrls():
            return []
        paths=[]
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path=url.toLocalFile()
            p=Path(path)
            if p.is_dir() or p.suffix.lower() in {'.qtx','.txt','.xlsx','.cpx'}:
                paths.append(path)
        return paths

    def _drop_target(self, watched):
        node=watched if isinstance(watched,QWidget) else None
        within_stage=False; document=None; index=-1
        while node is not None:
            if isinstance(node,WorkspaceDocument):document=node
            if isinstance(node,StudioToolSubWindow):
                value=node.property('tool_index')
                if value is not None:index=int(value)
            if hasattr(self,'studio_mdi') and node is self.studio_mdi:within_stage=True
            node=node.parentWidget()
        return (index,document) if within_stage else (-1,None)

    def _native_card_drop_target(self,watched):
        node=watched if isinstance(watched,QWidget) else None
        while node is not None:
            if isinstance(node,(ColorCardPlanGrid,WorkspaceCardList)):return True
            node=node.parentWidget()
        return False

    def _show_drop_hint(self,index,document,card_drag=False,watched=None):
        if not hasattr(self,'drop_overlay'):return
        viewport=self.studio_mdi.viewport()
        node=watched if isinstance(watched,QWidget) else None
        tile=None
        while node is not None:
            if isinstance(node,(ColorCardPlanGrid,WorkspaceCardList)):
                tile=node
                break
            if isinstance(node,ColorTile):
                tile=node
                break
            node=node.parentWidget()
        if card_drag and isinstance(tile,(ColorCardPlanGrid,WorkspaceCardList)):
            local=tile.viewport().mapFromGlobal(QCursor.pos())
            cell=None
            if isinstance(tile,QListWidget):
                item=tile.itemAt(local)
                if item is not None:cell=tile.visualItemRect(item)
            else:
                # WorkspaceCardList is a QListView (model/view), not QListWidget.
                model_index=tile.indexAt(local)
                if model_index.isValid():cell=tile.visualRect(model_index)
            if cell is not None and cell.isValid():
                corner=tile.viewport().mapTo(viewport,cell.topLeft())
                target=cell.translated(corner-cell.topLeft())
            else:target=None
        elif card_drag and isinstance(tile,ColorTile):
            target=tile.rect().translated(tile.mapTo(viewport,QPoint(0,0)))
        else:target=None
        if target is None:
            point=viewport.mapFromGlobal(QCursor.pos())
            width,height=(156,124) if card_drag else (380,132)
            x=max(0,min(point.x()-width//2,viewport.width()-width))
            y=max(0,min(point.y()-height//2,viewport.height()-height))
            target=QRect(x,y,width,height)
        target=target.intersected(viewport.rect())
        if self.drop_overlay.geometry()!=target:self.drop_overlay.setGeometry(target)
        if getattr(self,'_drop_hint_card_mode',None)!=card_drag:
            self._drop_hint_card_mode=card_drag
            self.drop_overlay.setStyleSheet('QFrame#workspaceDropOverlay{background:rgba(234,243,255,35);border:2px dashed #3479E9;border-radius:11px;}' if card_drag else
                                            'QFrame#workspaceDropOverlay{background:rgba(234,243,255,230);border:2px dashed #3479E9;border-radius:14px;}')
            self.drop_title.setVisible(not card_drag)
            self.drop_hint.setVisible(not card_drag)
        names={0:'色库临时预览',1:'查色 / 找色',2:'色卡编排',3:'比色工作台'}
        title=('将色卡拖放到'+names.get(index,'文件窗口')) if card_drag else ('将文件拖放到'+('当前文件窗口' if document else names.get(index,'工作区')))
        hints={0:'先预览色样；管理员确认后可存入正式或官方色库',1:'导入色样作为查色标准',2:'加入当前色卡方案',3:'加入当前比色工作台'}
        title='⇩  '+title; hint='松开鼠标即可加入 · '+hints.get(index,'支持 QTX / CPX / Excel')
        if self.drop_title.text()!=title:self.drop_title.setText(title)
        if self.drop_hint.text()!=hint:self.drop_hint.setText(hint)
        if not self.drop_overlay.isVisible():self.drop_overlay.show()
        self.drop_overlay.raise_()

    def _route_tool_file_drop(self,index,document,paths):
        if document is not None:document.import_external_paths(paths);return
        if index==0 and self.can_use('library_view'):self._import_library_preview(paths)
        elif index==1 and self.can_use('find'):self.add_find_qtx_files(paths)
        elif index==3 and self.can_use('compare'):
            wb_id=self.active_workbench_id
            if not wb_id:self.new_workbench();wb_id=self.active_workbench_id
            if wb_id:self.import_paths_to_workbench(wb_id,paths)
        elif index==2 and self.can_use('cards'):
            sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
            win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
            # Explorer files dropped onto the Palette Studio blank canvas should be
            # useful immediately. Create a transient draft only when no scheme is
            # open; it still obeys the Hotfix64 rule: closing without Ctrl+S does
            # not add anything to the saved-scheme list.
            if win is None:
                self.new_color_card()
                sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
                win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
            if isinstance(win,ColorCardPlanWindow):win.import_external_paths(paths)
        elif index==-1:self.open_workspace_paths(paths)

    def _route_tool_card_drop(self,index,document,keys):
        if document is not None:document.receive_sample_keys(keys);return True
        if index==2:
            sub=self.card_mdi.activeSubWindow(); win=sub.widget() if sub is not None else None
            if isinstance(win,ColorCardPlanWindow):
                win.receive_drop_many(keys,max(0,win.grid.count()-1),copy_mode=True);return True
        if index==1 and self.can_use('find'):
            samples=[self._sample_by_any_key(key) for key in keys]
            samples=[sample for sample in samples if sample is not None]
            if samples:self._add_find_file_session(samples,'拖入色样');return True
        if index==3 and self.can_use('compare'):
            samples=[self._sample_by_any_key(key) for key in keys]
            samples=[sample for sample in samples if sample is not None]
            if not samples:return False
            wb_id=self.active_workbench_id
            if not wb_id:self.new_workbench();wb_id=self.active_workbench_id
            wb=self._find_workbench(wb_id) if wb_id else None
            if wb:self._append_samples_to_workbench(wb,samples);return True
        return False

    def dragEnterEvent(self, event):
        # Fallback target for Windows Explorer drops.  Child drop zones still get
        # first chance; if they do not, the current page can accept the files.
        if self._dropped_local_paths(event):
            event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dropped_local_paths(event):
            event.setDropAction(Qt.CopyAction); event.accept(); return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths=self._dropped_local_paths(event)
        if not paths:
            return super().dropEvent(event)
        idx=self.active_tool_index()
        if idx==2:
            sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None
            win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
            if win is None:
                self.new_color_card(); sub=self.card_mdi.activeSubWindow() if hasattr(self,'card_mdi') else None; win=sub.widget() if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow) else None
            if win is not None:win.import_external_paths(paths)
        else:self._route_tool_file_drop(idx,None,paths)
        event.setDropAction(Qt.CopyAction); event.accept()

    def eventFilter(self, watched, event):
        # HF76: the application-wide filter outlives many child widgets during Qt
        # teardown. Once closeEvent starts, never dereference scroll areas/MDI
        # viewports that Qt may already have destroyed.
        if getattr(self,'_app_closing',False) or QApplication.closingDown():
            return False
        if event.type() in (QEvent.DragEnter,QEvent.DragMove,QEvent.DragLeave,QEvent.Drop):
            index,document=self._drop_target(watched)
            if event.type()==QEvent.DragLeave:
                if hasattr(self,'drop_overlay'):self.drop_overlay.hide()
            elif index>=0 or document is not None:
                paths=self._dropped_local_paths(event)
                card_drag=event.mimeData().hasFormat(CARD_MIME)
                if paths or card_drag:
                    self._show_drop_hint(index,document,card_drag,watched)
                    if card_drag and self._native_card_drop_target(watched):
                        if event.type()==QEvent.Drop:self.drop_overlay.hide()
                        # The palette/editor grid owns precise slot positioning.
                        # Let its existing native drag handler receive this event.
                        return False
                    if event.type()==QEvent.Drop:
                        self.drop_overlay.hide()
                        if paths:
                            self._route_tool_file_drop(index,document,paths)
                            event.setDropAction(Qt.CopyAction);event.accept();return True
                        if card_drag and (index in (1,2,3) or document is not None):
                            keys=bytes(event.mimeData().data(CARD_MIME)).decode('utf-8','ignore').splitlines()
                            if self._route_tool_card_drop(index,document,keys):
                                event.setDropAction(Qt.CopyAction);event.accept();return True
                    elif paths:
                        event.setDropAction(Qt.CopyAction);event.accept();return True
                    else:
                        event.setDropAction(Qt.CopyAction);event.accept();return True
        if hasattr(self,'find_tile_scroll') and watched is self.find_tile_scroll.viewport():
            if event.type()==QEvent.Resize:
                if hasattr(self,'_find_tile_reflow_timer'): self._find_tile_reflow_timer.start()
            elif event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
                child=watched.childAt(event.position().toPoint()); w=child
                while w is not None and w is not watched and not isinstance(w,ColorTile): w=w.parentWidget()
                if not isinstance(w,ColorTile):
                    self.clear_library_tile_selection(); self.find_focus_key=None; self._refresh_find_multi_overview()
        if hasattr(self,'tile_scroll') and watched is self.tile_scroll.viewport():
            if event.type()==QEvent.Resize:
                self._tiles_keys=None; self._tile_rebuild_timer.start(80)
            elif event.type() in (QEvent.DragEnter,QEvent.DragMove) and event.mimeData().hasFormat(CARD_MIME):
                event.setDropAction(Qt.CopyAction);event.accept();return True
            elif event.type()==QEvent.Drop and event.mimeData().hasFormat(CARD_MIME):
                keys=bytes(event.mimeData().data(CARD_MIME)).decode('utf-8','ignore').splitlines()
                if self.save_dropped_samples_to_formal_library(keys):event.setDropAction(Qt.CopyAction);event.accept()
                else:event.ignore()
                return True
        if event.type() in (QEvent.ShortcutOverride,QEvent.KeyPress):
            focus=QApplication.focusWidget()
            editing=isinstance(focus,(QLineEdit,QTextEdit,QComboBox,QSpinBox,QDoubleSpinBox))
            plan=None; document=None; widget=focus
            while widget is not None:
                if isinstance(widget,ColorCardPlanGrid):plan=widget.plan_window;break
                if isinstance(widget,ColorCardPlanWindow):plan=widget;break
                if isinstance(widget,WorkspaceCardList):document=widget.document;break
                if isinstance(widget,WorkspaceDocument):document=widget;break
                widget=widget.parentWidget()
            if plan is None and document is None and self.active_tool_index()==2 and hasattr(self,'card_mdi') and focus in (self.card_mdi,self.card_mdi.viewport()):
                sub=self.card_mdi.activeSubWindow()
                if sub is not None and isinstance(sub.widget(),ColorCardPlanWindow):plan=sub.widget()
            if not editing and plan is not None:
                command=('select_all' if event.matches(QKeySequence.SelectAll) else
                         'copy' if event.matches(QKeySequence.Copy) else
                         'cut' if event.matches(QKeySequence.Cut) else
                         'paste' if event.matches(QKeySequence.Paste) else
                         'undo' if event.matches(QKeySequence.Undo) else
                         'redo' if event.matches(QKeySequence.Redo) else
                         'save_as' if (event.modifiers() & Qt.ControlModifier) and (event.modifiers() & Qt.ShiftModifier) and event.key()==Qt.Key_S else
                         'save' if event.matches(QKeySequence.Save) else
                         'export' if (event.modifiers() & Qt.ControlModifier) and event.key()==Qt.Key_E else
                         'remove' if event.key()==Qt.Key_Delete and event.modifiers()==Qt.NoModifier else None)
                if command is not None:
                    if event.type()==QEvent.KeyPress:plan.trigger_command(command)
                    event.accept();return True
            if not editing and document is not None and event.matches(QKeySequence.SelectAll):
                if event.type()==QEvent.KeyPress:document.select_all()
                event.accept();return True
        if event.type() == QEvent.KeyPress and self.active_tool_index() in (0,1):
            focus=QApplication.focusWidget()
            editing=isinstance(focus,(QLineEdit,QTextEdit,QComboBox,QSpinBox,QDoubleSpinBox))
            tile_focus=isinstance(focus,ColorTile) or focus in (
                self.tile_scroll.viewport(),self.find_tile_scroll.viewport())
            if tile_focus and not editing and event.matches(QKeySequence.SelectAll):
                self.select_all_library_tiles(); return True
            if tile_focus and not editing and event.matches(QKeySequence.Copy) and self.library_selected_keys:
                self.copy_selected_library_tiles(); return True
            if tile_focus and not editing and event.key() in (Qt.Key_Return,Qt.Key_Enter) and self.find_mode_active and self.library_selected_keys:
                self.compare_find_selection(); return True
            if tile_focus and not editing and event.key()==Qt.Key_Delete and event.modifiers()==Qt.NoModifier and self.library_selected_keys:
                if self.find_mode_active:
                    # Find results are session-local, so Delete only removes rows
                    # from the current result set and never touches the library.
                    self.remove_selected_find_results()
                elif self.library_scope=='官方色库':
                    self.statusBar().showMessage('官方色库为只读数据；Delete 不会删除官方色样',4500)
                elif self.library_scope=='正式色库':
                    if self.is_admin:
                        # Administrator: Delete is a real, confirmed persistent
                        # delete.  The store also rewrites the readable QTX mirror.
                        self.delete_selected_library_samples()
                    else:
                        # Operators must never get a fake-delete experience.  The
                        # old behaviour only hid cards in memory, then they came
                        # back after reopening the customer/library.
                        self.statusBar().showMessage('正式色库为受控数据：当前账号无删除权限；可右键选择“从当前视图隐藏”',5000)
                else:
                    self.hidden_cards.update(self.library_selected_keys)
                    self.clear_library_tile_selection(); self._tiles_keys=None; self.build_tiles()
                return True
            if tile_focus and not editing and event.key()==Qt.Key_Escape:
                self.clear_library_tile_selection(); return True
        if event.type() == QEvent.MouseButtonPress and hasattr(self, "detail") and self.detail.isVisible():
            # 测色明细只由右键菜单打开；点击明细以外任意位置立即关闭。
            pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
            keep = False
            if pos is not None:
                w = QApplication.widgetAt(pos)
                while w is not None:
                    if w is self.detail: keep = True; break
                    w = w.parentWidget()
            if not keep: self.detail.clear(); self.detail.hide()
        return super().eventFilter(watched, event)

    def sample_measurement_details(self, sample):
        prefix = "STD" if sample.kind == "STD" else "BAT"
        raw=dict(sample.raw or {}); params=raw.get(f"{prefix}_MEASDLL_PARAMS","")
        fields={}
        for part in str(params).split(','):
            if ':' in part:
                k,v=part.split(':',1); fields[k.strip()]=v.strip()
        def first(*keys, default='—'):
            for key in keys:
                value=raw.get(key)
                if value not in (None,''):
                    return str(value).strip().rstrip(',')
            return default
        date=first(f'{prefix}_CreationDATE',f'{prefix}_CREATIONDATE','ATTR_Measurement Date','ATTR_Date')
        time_str=first(f'{prefix}_TIME','ATTR_Measurement Time','ATTR_Time')
        measured=first('CPX_MEASURED',default='')
        if measured:
            try:
                dt=datetime.datetime.fromisoformat(measured.replace('Z','+00:00'))
                if date=='—':date=dt.strftime('%Y-%m-%d')
                if time_str=='—':time_str=dt.strftime('%H:%M:%S')
            except Exception:pass
        if time_str=='—':
            stamp=first(f'{prefix}_DATETIME',f'{prefix}_Creation_DATETIME',default='')
            if stamp:
                try:
                    dt=datetime.datetime.fromtimestamp(float(stamp.rstrip(',')))
                    if date=='—':date=dt.strftime('%Y-%m-%d')
                    time_str=dt.strftime('%H:%M:%S')
                except Exception:pass
        viewing=sample.viewing or raw.get(f'{prefix}_VIEWING','')
        specular='SCI' if ('SCI' in str(viewing) or fields.get('Specular component')=='I') else 'SCE' if ('SCE' in str(viewing) or fields.get('Specular component')=='E') else '—'
        return {
            '来源文件': sample.source_file or '—',
            '测量日期': date,
            '测量时间': time_str,
            '仪器型号': first('CPX_INSTRUMENTMODEL','ATTR_Model', default=fields.get('Model','—')),
            '仪器序列号': first('CPX_INSTRUMENTSERIAL',f'{prefix}_INSTRUMENT_SERIAL_NO','ATTR_Serial number',default=fields.get('Serial number','—')),
            '几何条件': first('ATTR_Geometry',default=fields.get('Geometry','—')),
            '测量孔径': first('ATTR_MeasSpot',default=fields.get('MeasSpot','—')),
            '镜面光': specular,
            'UV': first('ATTR_UV',default=('UV Cal' if 'UV Cal' in str(viewing) else fields.get('UV-Filter%','—'))),
            '闪光次数': first('ATTR_Number flashes(Energy)',default=fields.get('Number flashes(Energy)','—')),
            '原始测量条件': first('CPX_INSTRUMENTSETTINGS',default=(viewing or '—')),
            '当前分析条件': f'{self.illuminant} / {self.observer}°',
        }

    def update_sample_attributes(self, sample, attributes: dict[str,str]):
        """Persist editable Attributes everywhere the sample is stored, without modifying the source file."""
        key=sample_key(sample); raw={k:v for k,v in dict(sample.raw or {}).items() if not str(k).startswith('ATTR_')}
        for k,v in (attributes or {}).items():
            if str(k).strip():raw[f'ATTR_{str(k).strip()}']=str(v)
        updated=replace(sample,raw=raw); self.sample_runtime_overrides[key]=updated
        # Temporary/imported workspace copy.
        for info in self.loaded_files.values():
            info['samples']=[updated if sample_key(x)==key else x for x in info.get('samples',[])]
        # Formal-library snapshots are immutable for Operator accounts. An
        # operator may edit a temporary/workbench copy, but only an
        # Administrator may persist the change into the formal library.
        if self.is_admin:
            try:self.store.update_sample_snapshot(key,updated)
            except Exception:pass
        # Workbench snapshots.
        for wb in self.workbenches:
            changed=False; new_data=[]
            for data in wb.get('samples_data',[]):
                try:sm=self._deserialize_sample(data)
                except Exception:new_data.append(data); continue
                if sample_key(sm)==key:sm=replace(sm,raw=raw); changed=True
                new_data.append(self._serialize_sample(sm))
            if changed:
                wb['samples_data']=new_data; self.store.save_workbench(wb)
        # Saved colour-card embedded snapshots, whether or not the card is currently open.
        for card in self.store.list_color_cards():
            settings=dict(card.get('settings',{}) or {}); embedded=[]; changed=False
            for data in settings.get('embedded_samples',[]) or []:
                try:sm=self._deserialize_sample(data)
                except Exception:embedded.append(data); continue
                if sample_key(sm)==key:sm=replace(sm,raw=raw); changed=True
                embedded.append(self._serialize_sample(sm))
            if changed:
                settings['embedded_samples']=embedded; card['settings']=settings; self.store.save_color_card(card)
                win=getattr(self,'card_plan_windows',{}).get(card.get('card_id'))
                if win is not None:win.card=card; win.invalidate_sample_cache(); win.refresh_cell_widgets()
        # Active find-session standards may be independent snapshots too.
        for session in self.find_sessions:
            if session.get('sample') is not None and sample_key(session['sample'])==key:session['sample']=updated
            if session.get('samples'):
                session['samples']=[updated if sample_key(x)==key else x for x in session['samples']]
        self._library_picker_cache=None
        if hasattr(self,'_p3_full_sample_cache'):self._p3_full_sample_cache.pop(key,None)
        self.rebuild_samples(); self.refresh_all_workbenches(); self.refresh_color_card_list()
        return updated

    def _sample_details_text(self, sample):
        prefix = "STD" if sample.kind == "STD" else "BAT"
        raw = dict(sample.raw or {})
        params = raw.get(f"{prefix}_MEASDLL_PARAMS", "")
        fields = {}
        for part in params.split(","):
            if ":" in part:
                key, value = part.split(":", 1); fields[key.strip()] = value.strip()
        viewing = sample.viewing or raw.get(f'{prefix}_VIEWING','')
        specular = "包含镜面光（SCI）" if "SCI" in viewing or fields.get("Specular component") == "I" else "排除镜面光（SCE）" if "SCE" in viewing or fields.get("Specular component") == "E" else "未记录"

        def first(*keys, default='—'):
            for key in keys:
                value=raw.get(key)
                if value not in (None,''):
                    return str(value).strip().rstrip(',')
            return default

        date=first(f'{prefix}_CreationDATE',f'{prefix}_CREATIONDATE','ATTR_Measurement Date','ATTR_Date')
        time_str=first(f'{prefix}_TIME','ATTR_Measurement Time','ATTR_Time')
        if time_str=='—':
            stamp=first(f'{prefix}_DATETIME',f'{prefix}_Creation_DATETIME',default='')
            if stamp:
                try:
                    dt=datetime.datetime.fromtimestamp(float(stamp.rstrip(',')))
                    if date=='—': date=dt.strftime('%d-%b-%y')
                    time_str=dt.strftime('%H:%M:%S')
                except Exception: pass
        source_type='QTX' if Path(sample.source_file or '').suffix.lower() in {'.qtx','.txt'} else 'Excel' if Path(sample.source_file or '').suffix.lower() in {'.xlsx','.xls'} else '其他'
        lab = self.sample_lab(sample); C=math.hypot(lab[1],lab[2]); h=math.degrees(math.atan2(lab[2],lab[1]))%360
        try: hue_model=self._perceptual_hue_label(sample)
        except Exception: hue_model="—"
        original=raw.get('__ORIGINAL_DISPLAY_NAME')
        lines=[f"色样名称：{sample.display_name}"]
        if original and original != sample.display_name: lines.append(f"原始名称：{original}")
        lines += [f"类型：{'标准样' if sample.kind == 'STD' else '批次样'}", f"来源：{source_type} · {sample.source_file}", "",
                  f"测量日期：{date}", f"测量时间：{time_str}",
                  f"仪器：{fields.get('Instrument Name', first('ATTR_Instrument','ATTR_Instrument Name'))}",
                  f"型号：{fields.get('Model', first('ATTR_Model'))}",
                  f"序列号：{fields.get('Serial number', first(f'{prefix}_INSTRUMENT_SERIAL_NO','ATTR_Serial number'))}",
                  f"几何条件：{fields.get('Geometry', first('ATTR_Geometry'))}",
                  f"测量孔径：{fields.get('MeasSpot', first('ATTR_MeasSpot'))}",
                  f"镜面光：{specular}",
                  f"UV：{'UV Cal' if 'UV Cal' in viewing else fields.get('UV-Filter%', first('ATTR_UV'))}",
                  f"闪光次数：{fields.get('Number flashes(Energy)', first('ATTR_Number flashes(Energy)'))}", "",
                  f"原始测量条件：{viewing or '未记录'}",
                  f"当前分析条件：{self.illuminant} / {self.observer}°",
                  f"L* {lab[0]:.3f}   a* {lab[1]:.3f}   b* {lab[2]:.3f}", f"C* {C:.3f}   CIELAB h° {h:.2f}", f"感知色相：{hue_model}"]
        if source_type=='Excel' and date=='—' and time_str=='—':
            lines += ["", "说明：当前 Excel 源文件未包含测量日期/时间字段，因此无法从该文件恢复；QTX 来源会保留并显示原始测量信息。"]
        lines += ["", "反射率（%）："]
        lines.extend(f"{w} nm   {r:.4f}" for w, r in zip(sample.wavelengths, sample.reflectance))
        return "\n".join(lines)

    def show_sample_details_dialog(self, sample):
        # P3-3 cards are metadata-only. Details are the explicit point where raw
        # measurement metadata + spectrum are required, so hydrate exactly one row.
        sample=self._full_library_sample(sample)
        key=sample_key(sample); dlg=self._sample_detail_windows.get(key)
        if dlg is None:
            dlg=SampleDetailsDialog(self,sample,self); dlg.setAttribute(Qt.WA_DeleteOnClose,True)
            self._sample_detail_windows[key]=dlg
            dlg.destroyed.connect(lambda *_args,k=key:self._sample_detail_windows.pop(k,None))
        if dlg.isMinimized():dlg.showNormal()
        else:dlg.show()
        dlg.raise_(); dlg.activateWindow()
        return dlg


    def show_sample_details(self, sample):
        return self.show_sample_details_dialog(sample)

    def update_spectrum(self):
        chosen = [s for s in self.samples]
        if not chosen:
            self.spectrum_text.clear()
            return
        lines = []
        for sample in chosen[:20]:
            lines.append(sample.display_name)
            lines.append("波长(nm)    反射率(%)")
            lines.extend(f"{w:>4}          {r:.4f}" for w, r in zip(sample.wavelengths, sample.reflectance))
            lines.append("")
        self.spectrum_text.setPlainText("\n".join(lines))

    def refresh(self):
        self.illuminant = self.light.currentText()
        self.observer = 2 if self.observer_box.currentText() == "2°" else 10
        self.rebuild_samples()
        # 当前页优先：不要每次保存/选择都在后台同时重绘色库、光谱和全部工作台。
        index=self.active_tool_index()
        if index==0:
            self.build_tiles()
        elif index==1:
            self.build_find_tiles()
        elif index==2:
            self.refresh_card_source(); self.refresh_color_card_list()
        elif index==3:
            self.refresh_workbench_tabs(); self.refresh_all_workbenches()
        elif index==4:
            self.update_spectrum()

    def closeEvent(self, event):
        self._app_closing=True
        # HF76 shutdown lifecycle: detach global/viewport event filters while all
        # widgets are still alive. Qt destroys child C++ objects after this point;
        # leaving Python filters attached caused viewport() calls on deleted
        # QScrollArea/QMdiArea objects during application exit.
        try:
            app=QApplication.instance()
            if app is not None:app.removeEventFilter(self)
        except (RuntimeError,AttributeError):
            pass
        for _mdi_name in ('studio_mdi','card_mdi'):
            try:
                _mdi=getattr(self,_mdi_name,None)
                _vp=getattr(_mdi,'_event_viewport',None) if _mdi is not None else None
                if _vp is not None:_vp.removeEventFilter(_mdi)
            except (RuntimeError,AttributeError):
                pass
        for _scroll_name in ('tile_scroll','find_tile_scroll'):
            try:
                _scroll=getattr(self,_scroll_name,None)
                if _scroll is not None:_scroll.viewport().removeEventFilter(self)
            except (RuntimeError,AttributeError):
                pass
        try:
            for _sub in list(getattr(self,'_workspace_document_windows',())):
                _doc=_sub.widget() if _sub is not None else None
                _vp=getattr(_doc,'_list_event_viewport',None) if _doc is not None else None
                if _vp is not None:_vp.removeEventFilter(_doc)
        except (RuntimeError,AttributeError):
            pass
        for _timer_name in ('_tile_rebuild_timer','_card_list_refresh_timer','_find_refresh_timer'):
            try:
                _timer=getattr(self,_timer_name,None)
                if _timer is not None:_timer.stop()
            except RuntimeError:
                pass
        try:
            if hasattr(self,'drop_overlay'):self.drop_overlay.hide()
        except RuntimeError:
            pass
        """P3-5: stop queued background work and flush performance diagnostics.

        Running QTX parsing is cooperative: Python threads cannot be killed safely,
        so the current parse may finish, but no queued file/maintenance job is
        allowed to start after shutdown begins.
        """
        try:
            state=getattr(self,'_workspace_import_state',None)
            if state is not None:
                state['cancelled']=True
                future=state.get('future')
                if future is not None:future.cancel()
        except Exception:
            pass
        try:
            executor=getattr(self,'_import_executor',None)
            if executor is not None:executor.shutdown(wait=False,cancel_futures=True)
        except Exception:
            pass
        try:
            executor=getattr(self,'_maintenance_executor',None)
            if executor is not None:executor.shutdown(wait=False,cancel_futures=True)
        except Exception:
            pass
        try:
            executor=getattr(self,'_card_sort_executor',None)
            if executor is not None:executor.shutdown(wait=False,cancel_futures=True)
        except Exception:
            pass
        try:self._flush_munsell_cache()
        except Exception:pass
        try:
            store=getattr(self,'store',None)
            if store is not None:write_performance_summary(getattr(store,'perf_summary_path',None))
        except Exception:
            pass
        super().closeEvent(event)

