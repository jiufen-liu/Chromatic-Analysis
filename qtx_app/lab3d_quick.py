from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QBrush, QVector3D
from PySide6.QtQuick import QQuickView
from PySide6.QtQuick3D import QQuick3DGeometry
from .build_info import BUILD_ID, BUILD_LABEL

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)


def _sample_name(sm) -> str:
    for attr in ("display_name", "sample_id", "name", "id"):
        value = getattr(sm, attr, "")
        if value:
            return str(value)
    return "未命名色样"


def _lch(lab):
    L, a, b = map(float, lab)
    C = math.hypot(a, b)
    h = (math.degrees(math.atan2(b, a)) + 360.0) % 360.0
    return L, C, h


def _soft_render_color(color: QColor, lab=None) -> QColor:
    """Pearl / ChromaShare-inspired display-only 3D rendering profile.

    This profile intentionally changes *presentation only*.  Measured Lab,
    spectra, library swatches, Delta-E/CMC/MI/555 and every saved value remain
    untouched.  Compared with HF44 it is brighter and less saturated, matching
    the supplied ChromaShare reference much more closely: pearl-like highlights,
    lifted mid-tones and softer chroma instead of dark saturated plastic balls.
    """
    c = QColor(color)
    h, s, v, alpha = c.getHsvF()
    if h < 0:
        h, s = 0.0, 0.0
    if lab is not None:
        try:
            L, a, b = map(float, lab)
            L = max(0.0, min(100.0, L))
            C = max(0.0, math.hypot(a, b))

            # Reference-video comparison: our HF44 markers were ~18-20% more
            # saturated and ~13-15% darker.  Compress chroma into a pastel,
            # professional range while preserving the original hue family.
            target_s = 0.045 + 0.60 * (1.0 - math.exp(-C / 43.0))
            # Very light colours should become especially pearl/pastel-like.
            light_soften = 0.88 - 0.13 * (L / 100.0)
            target_s *= light_soften
            s = max(0.0, min(0.74, 0.22 * s + 0.78 * target_s))

            # Lift middle and dark values without destroying measured L* order.
            # The 3D material will add shape shading on top of this base colour.
            target_v = 0.30 + 0.69 * ((L / 100.0) ** 0.53)
            v = max(0.0, min(1.0, 0.10 * v + 0.90 * target_v))
        except Exception:
            s = min(0.74, s * 0.84)
            v = min(1.0, 0.18 + 0.82 * v)
    else:
        s = min(0.74, s * 0.84)
        v = min(1.0, 0.18 + 0.82 * v)

    out = QColor.fromHsvF(h, s, v, alpha)
    # Pearl-paper lift: a small warm-white blend gives the clean, soft studio
    # look from the reference without changing the analytical colour data.
    paper_mix = 0.065
    out.setRed(round(out.red() * (1-paper_mix) + 250 * paper_mix))
    out.setGreen(round(out.green() * (1-paper_mix) + 249 * paper_mix))
    out.setBlue(round(out.blue() * (1-paper_mix) + 247 * paper_mix))
    return out



class Quick3DBridge(QObject):
    selectedChanged = Signal()

    def __init__(self, samples, labs, colors, instance_path: str, parent=None):
        super().__init__(parent)
        self.samples = list(samples)
        self.labs = [tuple(map(float, x)) for x in labs]
        self.colors = [QColor(c) for c in colors]
        self._instance_url = QUrl.fromLocalFile(instance_path)
        self._selected = -1

    @Property(QUrl, constant=True)
    def instanceUrl(self):
        return self._instance_url

    @Property(int, constant=True)
    def sampleCount(self):
        return len(self.samples)

    @Property(int, notify=selectedChanged)
    def selectedIndex(self):
        return self._selected

    @Property(bool, notify=selectedChanged)
    def hasSelection(self):
        return 0 <= self._selected < len(self.samples)

    @Property(str, notify=selectedChanged)
    def selectedName(self):
        if self.hasSelection:
            return _sample_name(self.samples[self._selected])
        return "未选择色样"

    @Property(str, notify=selectedChanged)
    def selectedLab(self):
        if self.hasSelection:
            L, a, b = self.labs[self._selected]
            _, C, h = _lch((L, a, b))
            return f"L* {L:.1f}   a* {a:+.1f}   b* {b:+.1f}\nC* {C:.1f}   h° {h:.1f}°"
        return "点击左侧色卡或 3D 色点查看"

    @Property(float, notify=selectedChanged)
    def selectedL(self):
        return self.labs[self._selected][0] if self.hasSelection else 0.0

    @Property(float, notify=selectedChanged)
    def selectedA(self):
        return self.labs[self._selected][1] if self.hasSelection else 0.0

    @Property(float, notify=selectedChanged)
    def selectedB(self):
        return self.labs[self._selected][2] if self.hasSelection else 0.0

    @Property(float, notify=selectedChanged)
    def selectedC(self):
        return _lch(self.labs[self._selected])[1] if self.hasSelection else 0.0

    @Property(float, notify=selectedChanged)
    def selectedH(self):
        return _lch(self.labs[self._selected])[2] if self.hasSelection else 0.0

    @Property(float, notify=selectedChanged)
    def selectedSceneX(self):
        return self.selectedA

    @Property(float, notify=selectedChanged)
    def selectedSceneY(self):
        return self.selectedL - 50.0

    @Property(float, notify=selectedChanged)
    def selectedSceneZ(self):
        return self.selectedB

    @Property(str, notify=selectedChanged)
    def selectedHex(self):
        if self.hasSelection:
            return self.colors[self._selected].name(QColor.NameFormat.HexRgb)
        return "#E7ECF3"

    @Slot(int)
    def selectInstance(self, index: int):
        index = int(index)
        if 0 <= index < len(self.samples) and index != self._selected:
            self._selected = index
            self.selectedChanged.emit()
        elif 0 <= index < len(self.samples):
            # Re-emit so a filtered card can still synchronize its visual state.
            self.selectedChanged.emit()

    @Slot()
    def clearSelection(self):
        """Clear the shared selection model.

        HF40: clicking empty analytical/3D space dismisses the callout and
        highlight without changing any sample data.
        """
        if self._selected != -1:
            self._selected = -1
            self.selectedChanged.emit()


class GamutGeometry(QQuick3DGeometry):
    """Derived envelope for display only; measured Lab points are never altered."""

    def __init__(self, labs, color_fn, parent=None, defer=False):
        super().__init__(parent)
        self._labs = [tuple(map(float, x)) for x in labs]
        self._color_fn = color_fn
        self._built = False
        if not defer:
            self.rebuild()

    def ensure_built(self):
        if not self._built:
            self.rebuild()

    def rebuild(self):
        self.clear()
        self._built = True
        labs = [x for x in self._labs if all(math.isfinite(v) for v in x)]
        if len(labs) < 40:
            return
        l_slices, hue_bins = 12, 48
        Ls = [x[0] for x in labs]
        lo, hi = max(0.0, min(Ls)), min(100.0, max(Ls))
        if hi - lo < 5:
            return
        radii = np.zeros((l_slices, hue_bins), np.float32)
        for L, a, b in labs:
            c = min(150.0, math.hypot(a, b))
            if c < 0.1:
                continue
            li = max(0, min(l_slices - 1, int(round((L - lo) / (hi - lo) * (l_slices - 1)))))
            h = (math.atan2(b, a) + 2 * math.pi) % (2 * math.pi)
            j = int(h / (2 * math.pi) * hue_bins) % hue_bins
            radii[li, j] = max(radii[li, j], c)
        nz = radii[radii > 0]
        if nz.size < hue_bins // 2:
            return
        fallback = float(np.percentile(nz, 55))
        populated = [i for i in range(l_slices) if np.any(radii[i] > 0)]
        for li in range(l_slices):
            if not np.any(radii[li] > 0):
                radii[li] = radii[min(populated, key=lambda k: abs(k - li))]
            known = np.where(radii[li] > 0)[0]
            for j in range(hue_bins):
                if radii[li, j] <= 0:
                    if known.size:
                        near = min(known, key=lambda k: min((j-k) % hue_bins, (k-j) % hue_bins))
                        radii[li, j] = radii[li, near]
                    else:
                        radii[li, j] = fallback
        for _ in range(4):
            radii = .16*np.roll(radii, 1, axis=1) + .68*radii + .16*np.roll(radii, -1, axis=1)
        for _ in range(2):
            tmp = radii.copy()
            for li in range(1, l_slices-1):
                tmp[li] = .18*radii[li-1] + .64*radii[li] + .18*radii[li+1]
            radii = tmp
        for li in range(l_slices):
            t = li / max(1, l_slices-1)
            radii[li] *= .54 + .46 * (math.sin(math.pi*t) ** .48)

        rings = []
        for li in range(l_slices):
            L = lo + (hi-lo) * li / (l_slices-1)
            ring = []
            for j in range(hue_bins):
                h = 2*math.pi*j/hue_bins
                c = float(radii[li, j])
                a, b = c*math.cos(h), c*math.sin(h)
                col = QColor(self._color_fn((L, a, b)))
                r, g, bl, _ = col.getRgbF()
                ring.append((a, L-50.0, b, r, g, bl, 1.0))
            rings.append(ring)
        verts = []
        for li in range(l_slices-1):
            for j in range(hue_bins):
                nj = (j+1) % hue_bins
                p00, p01 = rings[li][j], rings[li][nj]
                p10, p11 = rings[li+1][j], rings[li+1][nj]
                verts.extend((p00, p10, p11, p00, p11, p01))
        arr = np.asarray(verts, dtype=np.float32)
        if not arr.size:
            return
        self.setVertexData(arr.tobytes())
        self.setStride(7*4)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0, QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.ColorSemantic, 3*4, QQuick3DGeometry.Attribute.ComponentType.F32Type)
        mins = np.min(arr[:, :3], axis=0)
        maxs = np.max(arr[:, :3], axis=0)
        self.setBounds(QVector3D(*map(float, mins)), QVector3D(*map(float, maxs)))


class _LineGeometry(QQuick3DGeometry):
    def _set_lines(self, lines, bounds):
        self.clear()
        arr = np.asarray(lines, dtype=np.float32).reshape(-1, 3)
        if not arr.size:
            return
        self.setVertexData(arr.tobytes())
        self.setStride(3*4)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Lines)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0, QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.setBounds(QVector3D(*bounds[0]), QVector3D(*bounds[1]))


class GridGeometry(_LineGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        lines = []
        def add(a, b): lines.extend((*a, *b))
        for t in (-100, -75, -50, -25, 0, 25, 50, 75, 100):
            add((-100, 0, t), (100, 0, t))
            add((t, 0, -100), (t, 0, 100))
        self._set_lines(lines, ((-105, -1, -105), (105, 1, 105)))


class AxisGeometry(_LineGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        lines = []
        def add(a, b): lines.extend((*a, *b))
        add((-115, 0, 0), (115, 0, 0))
        add((0, -55, 0), (0, 55, 0))
        add((0, 0, -115), (0, 0, 115))
        self._set_lines(lines, ((-118, -58, -118), (118, 58, 118)))


class SphereGeometry(QQuick3DGeometry):
    """Low-poly smooth sphere for high-count instancing.

    Qt's built-in #Sphere is intentionally high fidelity.  Repeating it 3,500
    times wastes fragment/vertex work on a small screen-space marker.  This
    sphere keeps smooth vertex normals but uses only 10×6 segments, which is
    visually indistinguishable at the normal point sizes and substantially
    lowers GPU load on integrated office graphics.  Radius 50 matches Qt's
    built-in primitive scale so all existing point-size controls remain valid.
    """
    def __init__(self, parent=None, lon_segments=10, lat_segments=6):
        super().__init__(parent)
        verts=[]
        radius=50.0
        def p(theta, phi):
            st, ct = math.sin(theta), math.cos(theta)
            sp, cp = math.sin(phi), math.cos(phi)
            nx, ny, nz = st*cp, ct, st*sp
            return (radius*nx, radius*ny, radius*nz, nx, ny, nz)
        for iy in range(lat_segments):
            t0=math.pi*iy/lat_segments; t1=math.pi*(iy+1)/lat_segments
            for ix in range(lon_segments):
                f0=2*math.pi*ix/lon_segments; f1=2*math.pi*(ix+1)/lon_segments
                p00,p01,p10,p11=p(t0,f0),p(t0,f1),p(t1,f0),p(t1,f1)
                # Degenerate pole triangles are harmless and avoid index buffers.
                verts.extend((p00,p10,p11,p00,p11,p01))
        arr=np.asarray(verts,dtype=np.float32)
        self.setVertexData(arr.tobytes())
        self.setStride(6*4)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic,0,QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.NormalSemantic,3*4,QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.setBounds(QVector3D(-radius,-radius,-radius),QVector3D(radius,radius,radius))


def _write_instance_xml(labs, colors, point_scale=1.0) -> str:
    fd, path = tempfile.mkstemp(prefix="chromatic_lab3d_", suffix=".xml")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<InstanceTable>\n')
        for (L, a, b), color in zip(labs, colors):
            c = QColor(color).name(QColor.NameFormat.HexRgb)
            f.write(
                f'  <Instance position="{a:.5f} {L-50.0:.5f} {b:.5f}" '
                f'scale="{point_scale:.5f} {point_scale:.5f} {point_scale:.5f}" color="{escape(c)}"/>\n'
            )
        f.write('</InstanceTable>\n')
    return path


class SampleCard(QFrame):
    clicked = Signal(int)

    def __init__(self, index, sample, lab, color, parent=None):
        super().__init__(parent)
        self.index = int(index)
        self._selected = False
        self.setObjectName("sampleCard")
        self.setCursor(Qt.PointingHandCursor)
        # HF114: the left navigator is already paged, so each preview card can be
        # compact instead of consuming most of the panel height.  The 3D scene
        # still contains every sample; this changes presentation only.
        self.setFixedSize(128, 100)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(3)
        self.swatch = QLabel()
        self.swatch.setFixedHeight(42)
        self.swatch.setStyleSheet(f"background:{QColor(color).name()};border:0;border-radius:6px;")
        self.swatch.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.swatch)
        full_name = _sample_name(sample)
        self.name = QLabel()
        f = self.name.font(); f.setBold(True); f.setPointSizeF(7.6); self.name.setFont(f)
        self.name.setText(QFontMetrics(f).elidedText(full_name, Qt.ElideRight, 116))
        self.name.setToolTip(full_name)
        self.name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.name)
        L, a, b = lab
        self.lab = QLabel(f"L* {L:.1f}  a* {a:+.1f}\nb* {b:+.1f}")
        self.lab.setStyleSheet("color:#7A8799;font-size:8.5px;")
        self.lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lab)
        self.setSelected(False)

    def setSelected(self, selected: bool):
        self._selected = bool(selected)
        if self._selected:
            self.setStyleSheet(
                "QFrame#sampleCard{background:#FBFDFF;border:2px solid #3F8CFF;border-radius:9px;}"
            )
        else:
            self.setStyleSheet(
                "QFrame#sampleCard{background:#FBFCFD;border:1px solid #DCE3EC;border-radius:9px;}"
                "QFrame#sampleCard:hover{background:#F7FAFF;border-color:#9DBFF6;}"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
            event.accept()
            return
        super().mousePressEvent(event)


class _Base2DCoordinateView(QWidget):
    def __init__(self, labs, colors, bridge: Quick3DBridge, parent=None):
        super().__init__(parent)
        self.labs = np.asarray(labs, dtype=np.float32)
        self.colors = [QColor(c) for c in colors]
        self.bridge = bridge
        self._selected = -1
        self._screen_points = []
        self.setMinimumSize(620, 520)
        self.setMouseTracking(True)
        bridge.selectedChanged.connect(self._bridge_selection_changed)

    def _bridge_selection_changed(self):
        self._selected = int(self.bridge.selectedIndex)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self._screen_points:
            return super().mousePressEvent(event)
        x, y = event.position().x(), event.position().y()
        best, dist2 = -1, 10.5 * 10.5
        for idx, px, py in self._screen_points:
            d = (px-x)*(px-x) + (py-y)*(py-y)
            if d < dist2:
                dist2, best = d, idx
        if best >= 0:
            self.bridge.selectInstance(best)
            event.accept()
            return
        # Blank analytical space dismisses the shared selection/callout.
        self.bridge.clearSelection()
        event.accept()

    @staticmethod
    def _soft_bg(p: QPainter, rect):
        p.fillRect(rect, QColor("#F4F7FB"))
        p.setPen(QPen(QColor("#DCE4EE"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)

    def _draw_selected_ring(self, p, x, y, color):
        p.setPen(QPen(QColor("#FFFFFF"), 5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(x, y), 9.5, 9.5)
        p.setPen(QPen(QColor("#2E83FF"), 3))
        p.drawEllipse(QPointF(x, y), 8.0, 8.0)
        p.setBrush(QBrush(color))
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawEllipse(QPointF(x, y), 4.5, 4.5)


class LabCoordinateView(_Base2DCoordinateView):
    """Functional L*a*b* Cartesian view: a*b* scatter plus L* position."""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._soft_bg(p, self.rect())
        margin = 58
        side_w = 105
        plot = QRectF(margin, 74, max(260, self.width()-margin*2-side_w), max(260, self.height()-128))
        cx, cy = plot.center().x(), plot.center().y()
        span = min(plot.width(), plot.height()) * 0.43
        p.setPen(QPen(QColor("#2C3C52"), 1.25))
        p.drawLine(QPointF(plot.left(), cy), QPointF(plot.right(), cy))
        p.drawLine(QPointF(cx, plot.top()), QPointF(cx, plot.bottom()))
        p.setPen(QPen(QColor("#C8D3E0"), 1, Qt.DashLine))
        for t in (-100, -50, 50, 100):
            x = cx + t/128.0*span
            y = cy - t/128.0*span
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(QColor("#17263A"))
        f = p.font(); f.setBold(True); f.setPointSize(13); p.setFont(f)
        p.drawText(int(plot.left()), 40, "L*a*b* · 笛卡尔坐标")
        f.setBold(False); f.setPointSize(10); p.setFont(f)
        p.setPen(QColor("#6F7F94"))
        p.drawText(int(plot.left()), 60, "a*: 绿 ↔ 红    b*: 蓝 ↔ 黄    L*: 黑 ↔ 白")
        p.setPen(QColor("#25364D"))
        p.drawText(int(plot.right()-18), int(cy-8), "+a*")
        p.drawText(int(plot.left()+4), int(cy-8), "-a*")
        p.drawText(int(cx+8), int(plot.top()+14), "+b*")
        p.drawText(int(cx+8), int(plot.bottom()-6), "-b*")

        self._screen_points = []
        for idx, (lab, color) in enumerate(zip(self.labs, self.colors)):
            L, a, b = map(float, lab)
            x = cx + max(-128, min(128, a))/128.0*span
            y = cy - max(-128, min(128, b))/128.0*span
            c = QColor(color); c.setAlpha(155)
            p.setPen(Qt.NoPen); p.setBrush(c)
            p.drawEllipse(QPointF(x, y), 2.35, 2.35)
            self._screen_points.append((idx, x, y))

        # L* vertical scale on the right side.
        bx = plot.right() + 46
        p.setPen(QPen(QColor("#B8C5D5"), 8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(bx, plot.bottom()), QPointF(bx, plot.top()))
        p.setPen(QColor("#64748A"))
        p.drawText(int(bx+12), int(plot.top()+5), "100")
        p.drawText(int(bx+12), int(plot.bottom()+5), "0")
        p.drawText(int(bx-7), int(plot.top()-14), "L*")

        if 0 <= self._selected < len(self.labs):
            L, a, b = map(float, self.labs[self._selected])
            x = cx + max(-128, min(128, a))/128.0*span
            y = cy - max(-128, min(128, b))/128.0*span
            self._draw_selected_ring(p, x, y, self.colors[self._selected])
            ly = plot.bottom() - max(0, min(100, L))/100.0*plot.height()
            p.setPen(QPen(QColor("#2E83FF"), 3))
            p.drawLine(QPointF(bx-13, ly), QPointF(bx+13, ly))
            p.setPen(QColor("#2E83FF"))
            p.drawText(int(bx+18), int(ly+5), f"{L:.1f}")


class LChCoordinateView(_Base2DCoordinateView):
    """Functional L*C*h° cylindrical view using a hue/chroma polar plot."""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._soft_bg(p, self.rect())
        margin = 58
        side_w = 105
        plot = QRectF(margin, 74, max(260, self.width()-margin*2-side_w), max(260, self.height()-128))
        center = plot.center()
        R = min(plot.width(), plot.height()) * 0.40
        max_c = max(100.0, min(160.0, float(np.percentile(np.hypot(self.labs[:, 1], self.labs[:, 2]), 99.0)) if len(self.labs) else 100.0))

        f = p.font(); f.setBold(True); f.setPointSize(13); p.setFont(f)
        p.setPen(QColor("#17263A")); p.drawText(int(plot.left()), 40, "L*C*h° · 柱坐标")
        f.setBold(False); f.setPointSize(10); p.setFont(f)
        p.setPen(QColor("#6F7F94")); p.drawText(int(plot.left()), 60, "C*: 彩度（离中心距离）    h°: 色相角    L*: 明度")

        p.setBrush(Qt.NoBrush)
        for frac in (.25, .5, .75, 1.0):
            p.setPen(QPen(QColor("#CCD7E4"), 1, Qt.DashLine))
            p.drawEllipse(center, R*frac, R*frac)
            p.setPen(QColor("#8795A8"))
            p.drawText(int(center.x()+R*frac-16), int(center.y()-5), f"{max_c*frac:.0f}")
        p.setPen(QPen(QColor("#AEBBCB"), 1))
        p.drawLine(QPointF(center.x()-R, center.y()), QPointF(center.x()+R, center.y()))
        p.drawLine(QPointF(center.x(), center.y()-R), QPointF(center.x(), center.y()+R))
        p.setPen(QColor("#33455D"))
        p.drawText(int(center.x()+R+8), int(center.y()+5), "0° / +a*")
        p.drawText(int(center.x()-22), int(center.y()-R-8), "90° / +b*")
        p.drawText(int(center.x()-R-70), int(center.y()+5), "180° / -a*")
        p.drawText(int(center.x()-28), int(center.y()+R+22), "270° / -b*")

        self._screen_points = []
        for idx, (lab, color) in enumerate(zip(self.labs, self.colors)):
            L, a, b = map(float, lab)
            C = math.hypot(a, b)
            h = math.atan2(b, a)
            rr = min(1.0, C/max_c) * R
            x = center.x() + math.cos(h)*rr
            y = center.y() - math.sin(h)*rr
            c = QColor(color); c.setAlpha(155)
            p.setPen(Qt.NoPen); p.setBrush(c); p.drawEllipse(QPointF(x, y), 2.35, 2.35)
            self._screen_points.append((idx, x, y))

        bx = plot.right() + 46
        p.setPen(QPen(QColor("#B8C5D5"), 8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(bx, plot.bottom()), QPointF(bx, plot.top()))
        p.setPen(QColor("#64748A")); p.drawText(int(bx+12), int(plot.top()+5), "100"); p.drawText(int(bx+12), int(plot.bottom()+5), "0"); p.drawText(int(bx-7), int(plot.top()-14), "L*")

        if 0 <= self._selected < len(self.labs):
            L, a, b = map(float, self.labs[self._selected]); C = math.hypot(a, b); h = math.atan2(b, a)
            rr = min(1.0, C/max_c) * R
            x = center.x()+math.cos(h)*rr; y = center.y()-math.sin(h)*rr
            self._draw_selected_ring(p, x, y, self.colors[self._selected])
            ly = plot.bottom() - max(0, min(100, L))/100.0*plot.height()
            p.setPen(QPen(QColor("#2E83FF"), 3)); p.drawLine(QPointF(bx-13, ly), QPointF(bx+13, ly))
            p.setPen(QColor("#2E83FF")); p.drawText(int(bx+18), int(ly+5), f"{L:.1f}")


class LiteProjectionView(QWidget):
    """Same-window emergency renderer. Never opens the legacy black dialog."""
    def __init__(self, labs, colors, parent=None):
        super().__init__(parent)
        self.labs = np.asarray([(a, L-50.0, b) for L, a, b in labs], dtype=np.float32)
        self.colors = [QColor(c) for c in colors]
        self.yaw = -34.0; self.pitch = 18.0; self.zoom = 1.0; self.last = None; self.dragging = False
        self.setMinimumSize(620, 520); self.setMouseTracking(True)

    def _project(self):
        if not len(self.labs): return np.zeros((0, 3), np.float32)
        y = math.radians(self.yaw); p = math.radians(self.pitch)
        cy, sy = math.cos(y), math.sin(y); cp, sp = math.cos(p), math.sin(p)
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], np.float32)
        Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], np.float32)
        pts = self.labs @ (Rx@Ry).T
        s = min(self.width(), self.height())/300.0*self.zoom
        x = self.width()/2 + pts[:, 0]*s; yy = self.height()/2 - pts[:, 1]*s
        return np.column_stack((x, yy, pts[:, 2]))

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, not self.dragging); p.fillRect(self.rect(), QColor('#898A87'))
        pts = self._project(); n = len(pts)
        if not n: return
        order = np.argsort(pts[:, 2]); stride = max(1, math.ceil(n/(1500 if self.dragging else 4000))); r = 4.5 if self.dragging else 5.5
        for idx in order[::stride]:
            q = pts[idx]; col = QColor(self.colors[int(idx)]); col.setAlpha(220)
            p.setPen(QPen(col.darker(112), 0.55)); p.setBrush(col); p.drawEllipse(float(q[0]-r), float(q[1]-r), 2*r, 2*r)
        p.setPen(QPen(QColor(35, 38, 42, 180), 1)); f = p.font(); f.setBold(True); f.setPointSize(11); p.setFont(f)
        p.drawText(12, 24, f'{BUILD_ID} · 同窗兼容渲染 · 数据坐标不变')

    def mousePressEvent(self, e): self.last = e.position(); self.dragging = True
    def mouseMoveEvent(self, e):
        if self.last is not None and self.dragging:
            d = e.position()-self.last; self.last = e.position(); self.yaw += d.x()*.35; self.pitch = max(-88, min(88, self.pitch+d.y()*.28)); self.update()
    def mouseReleaseEvent(self, _e): self.dragging = False; self.last = None; self.update()
    def wheelEvent(self, e): self.zoom = max(.35, min(3.2, self.zoom*(1.0+e.angleDelta().y()/1200.0))); self.update()


class Lab3DDialog(QDialog):
    """HF41 unified color-space viewer.

    * Qt Quick 3D/RHI owns continuous 3D rotation/rendering.
    * L*a*b* and L*C*h° tabs are real analytical views, not decorative buttons.
    * All views share one selection model. Selecting a left swatch highlights the
      same measured Lab coordinate in the 3D cloud and analytical plots.
    """
    def __init__(self, samples, owner, context_label='', color_fn=None, key_fn=None):
        # HF45: use a genuinely independent top-level window.  A QDialog owned by
        # the main window can disappear from the Windows taskbar when minimized;
        # parentless Qt.Window gets its own taskbar button and is easy to restore.
        # The main window still keeps a Python reference and owner.destroyed closes
        # this viewer, so application lifetime remains controlled.
        super().__init__(None)
        self.owner = owner
        if owner is not None:
            try:
                self.setWindowIcon(owner.windowIcon())
                owner.destroyed.connect(self.close)
            except Exception:
                pass
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        # Expose standard min/max/restore controls on Windows.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setSizeGripEnabled(True)
        self.samples = list(samples or [])
        self.context_label = (context_label or '').strip()
        self.color_fn = color_fn
        self.key_fn = key_fn
        self.labs = []
        self.colors = []
        for sm in self.samples:
            lab = tuple(map(float, owner.sample_lab(sm)))
            self.labs.append(lab)
            self.colors.append(QColor(color_fn(sm, lab) if color_fn else '#B8C4D2'))
        self.render_colors = [_soft_render_color(c, lab) for c, lab in zip(self.colors, self.labs)]
        self._instance_path = _write_instance_xml(self.labs, self.render_colors)
        self.bridge = Quick3DBridge(self.samples, self.labs, self.colors, self._instance_path, self)
        self.gamut = GamutGeometry(self.labs, self._derived_color, None, defer=False)
        self.grid_geometry = GridGeometry(None)
        self.axis_geometry = AxisGeometry(None)
        # HF46: all samples remain present, but dense clouds use a lighter
        # smooth marker mesh.  This cuts GPU vertex/fragment load without
        # changing a single measured Lab coordinate.
        if len(self.samples) >= 3000:
            _lon, _lat = 8, 5
        elif len(self.samples) >= 1400:
            _lon, _lat = 10, 6
        else:
            _lon, _lat = 12, 7
        self.sphere_geometry = SphereGeometry(None, lon_segments=_lon, lat_segments=_lat)
        self._quick3d_owned_objects = (self.gamut, self.grid_geometry, self.axis_geometry, self.sphere_geometry)
        self._frame_ready = False; self._quick_failed = False; self._init_stage = 'geometry-ready'
        self._coord_mode = 'space'
        self._card_by_index = {}
        self._preview_indices = []
        self._preview_all_indices = []
        self._preview_page = 0
        self._preview_page_size = 16

        self.setWindowTitle(f"3D CIELAB 色彩空间 · {BUILD_LABEL} · {self.context_label or '当前选择'} · {len(self.samples)} 色样")
        self.resize(1580, 920); self.setMinimumSize(1180, 730); self.setStyleSheet(self._style())
        root = QVBoxLayout(self); root.setContentsMargins(14, 10, 14, 10); root.setSpacing(9)
        root.addLayout(self._topbar())
        body = QHBoxLayout(); body.setSpacing(10); root.addLayout(body, 1)
        body.addWidget(self._library_panel(), 0)

        center = QFrame(); center.setObjectName('centerCard'); cl = QVBoxLayout(center); cl.setContentsMargins(8, 8, 8, 8); cl.setSpacing(5)
        self.coordinate_stack = QStackedWidget(); cl.addWidget(self.coordinate_stack, 1); body.addWidget(center, 1)

        # 3D page: Quick3D with same-window compatibility renderer.
        self.render_stack = QStackedWidget()
        self.coordinate_stack.addWidget(self.render_stack)
        self._init_stage = 'creating-qquickview'
        self.quick = QQuickView(); self.quick.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView); self.quick.setColor(QColor('#E7EAED'))
        self.quick.rootContext().setContextProperty('bridge', self.bridge)
        self.quick.rootContext().setContextProperty('gamutGeometry', self.gamut)
        self.quick.rootContext().setContextProperty('gridGeometry', self.grid_geometry)
        self.quick.rootContext().setContextProperty('axisGeometry', self.axis_geometry)
        self.quick.rootContext().setContextProperty('sphereGeometry', self.sphere_geometry)
        qml = Path(__file__).resolve().parent/'qml'/'Lab3DView.qml'
        self.quick.statusChanged.connect(self._quick_status); self.quick.frameSwapped.connect(self._first_frame)
        self.quick_container = QWidget.createWindowContainer(self.quick); self.quick_container.setMinimumSize(620, 520)
        self.render_stack.addWidget(self.quick_container)
        self.fallback = LiteProjectionView(self.labs, self.render_colors); self.render_stack.addWidget(self.fallback)
        self._init_stage = 'loading-qml'; self.quick.setSource(QUrl.fromLocalFile(str(qml))); self._init_stage = 'qml-source-set'

        # Real Lab and LCh analytical pages.
        self.lab_view = LabCoordinateView(self.labs, self.render_colors, self.bridge)
        self.lch_view = LChCoordinateView(self.labs, self.render_colors, self.bridge)
        self.coordinate_stack.addWidget(self.lab_view); self.coordinate_stack.addWidget(self.lch_view)

        body.addWidget(self._control_panel(), 0)
        self.status = QLabel(f"{BUILD_ID} · {len(self.samples):,} 个真实色样 · Qt Quick 3D / RHI 初始化中…"); self.status.setObjectName('status'); root.addWidget(self.status)
        self.bridge.selectedChanged.connect(self._refresh_selected)
        QTimer.singleShot(350, self._probe_frame); QTimer.singleShot(1000, self._probe_frame); QTimer.singleShot(2200, self._probe_frame); QTimer.singleShot(5200, self._health_check)

    def _derived_color(self, lab):
        L, a, b = map(float, lab); L = max(0, min(100, L)); C = math.hypot(a, b); h = (math.degrees(math.atan2(b, a))+360) % 360
        sat = min(0.64, C/125.0); val = .50 + .46*(L/100.0)
        c = QColor.fromHsvF(h/360.0, sat, min(1.0, val))
        return _soft_render_color(c, lab)

    @staticmethod
    def _style():
        return '''
        QDialog{background:#F4F6FA;color:#17243A;font-family:"Microsoft YaHei UI";}
        QFrame#sideCard,QFrame#controlCard,QFrame#centerCard,QFrame#infoCard{background:#FFFFFF;border:1px solid #DCE4EE;border-radius:12px;}
        QLabel#h1{font-size:20px;font-weight:700;color:#17243A;} QLabel#muted{color:#77879C;font-size:11px;} QLabel#status{color:#68788E;padding:2px 5px;}
        QLabel#section{font-size:13px;font-weight:700;color:#25364D;padding-top:5px;} QLabel#coordHead{font-size:11px;font-weight:700;color:#66778E;}
        QLineEdit{background:#F8FAFD;border:1px solid #D8E1EC;border-radius:8px;padding:7px 10px;min-height:22px;}
        QPushButton{background:#FFFFFF;border:1px solid #D8E1EC;border-radius:8px;padding:7px 10px;color:#33455D;}
        QPushButton:hover{background:#F6F9FE;border-color:#9ABDF4;} QPushButton:checked{background:#EAF2FF;border-color:#4B8CF6;color:#1763D2;font-weight:700;}
        QCheckBox{spacing:7px;color:#43536A;} QSlider::groove:horizontal{height:5px;background:#DDE5EF;border-radius:2px;} QSlider::sub-page:horizontal{background:#408BFA;border-radius:2px;} QSlider::handle:horizontal{width:14px;margin:-5px 0;background:white;border:2px solid #408BFA;border-radius:7px;}
        QScrollArea{border:0;background:transparent;}
        '''

    def _topbar(self):
        lay = QHBoxLayout(); title = QLabel('3D 色彩空间'); title.setObjectName('h1'); lay.addWidget(title)
        sub = QLabel('CIELAB 专业可视化 · Qt Quick 3D / RHI · 数据坐标保持不变'); sub.setObjectName('muted'); lay.addWidget(sub); lay.addStretch(1)
        self.coord_buttons = {}
        for text, mode in [('CIELAB 空间', 'space'), ('L*a*b*', 'lab'), ('L*C*h°', 'lch')]:
            b = QPushButton(text); b.setCheckable(True); b.setChecked(mode == 'space'); b.clicked.connect(lambda _=False, m=mode: self._set_coord_mode(m)); lay.addWidget(b); self.coord_buttons[mode] = b
        return lay

    def _library_panel(self):
        panel = QFrame(); panel.setObjectName('sideCard'); panel.setFixedWidth(294)
        lay = QVBoxLayout(panel); lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(8)
        title = QLabel(self.context_label.split('·')[0].strip() or '当前色库'); title.setObjectName('h1'); lay.addWidget(title)
        count = QLabel(f'共 {len(self.samples):,} 个颜色'); count.setObjectName('muted'); lay.addWidget(count)
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText('搜索色号或名称…'); self.search_edit.textChanged.connect(self._rebuild_preview); lay.addWidget(self.search_edit)
        self.preview_scroll = QScrollArea(); self.preview_scroll.setWidgetResizable(True)
        self.preview_content = QWidget(); self.preview_grid = QGridLayout(self.preview_content); self.preview_grid.setContentsMargins(0, 0, 0, 0); self.preview_grid.setSpacing(7)
        self.preview_scroll.setWidget(self.preview_content); lay.addWidget(self.preview_scroll, 1)

        # HF46: the left navigator is now genuinely pageable instead of silently
        # truncating the library to the first 24 cards.  The 3D view still uses
        # every real sample; pagination only controls the lightweight preview UI.
        pager = QHBoxLayout()
        self.preview_prev = QPushButton('‹ 上一页')
        self.preview_prev.setFixedHeight(30)
        self.preview_prev.clicked.connect(lambda: self._change_preview_page(-1))
        pager.addWidget(self.preview_prev)
        self.preview_page_label = QLabel('第 1 / 1 页')
        self.preview_page_label.setAlignment(Qt.AlignCenter)
        self.preview_page_label.setObjectName('muted')
        pager.addWidget(self.preview_page_label, 1)
        self.preview_next = QPushButton('下一页 ›')
        self.preview_next.setFixedHeight(30)
        self.preview_next.clicked.connect(lambda: self._change_preview_page(1))
        pager.addWidget(self.preview_next)
        lay.addLayout(pager)
        self.preview_summary = QLabel('')
        self.preview_summary.setWordWrap(True); self.preview_summary.setObjectName('muted'); lay.addWidget(self.preview_summary)
        self._rebuild_preview('', reset_page=True)
        return panel

    def _change_preview_page(self, delta):
        if not self._preview_all_indices:
            return
        pages = max(1, math.ceil(len(self._preview_all_indices) / self._preview_page_size))
        new_page = max(0, min(pages - 1, self._preview_page + int(delta)))
        if new_page != self._preview_page:
            self._preview_page = new_page
            self._render_preview_page()

    def _rebuild_preview(self, query='', reset_page=True):
        if not hasattr(self, 'preview_grid'):
            return
        q = str(query or '').strip().casefold()
        self._preview_all_indices = [
            i for i, sm in enumerate(self.samples)
            if (not q) or q in _sample_name(sm).casefold()
        ]
        if reset_page:
            self._preview_page = 0
        pages = max(1, math.ceil(len(self._preview_all_indices) / self._preview_page_size))
        self._preview_page = max(0, min(self._preview_page, pages - 1))
        self._render_preview_page()

    def _render_preview_page(self):
        while self.preview_grid.count():
            item = self.preview_grid.takeAt(0); w = item.widget()
            if w is not None: w.deleteLater()
        self._card_by_index = {}
        total = len(self._preview_all_indices)
        pages = max(1, math.ceil(total / self._preview_page_size))
        start = self._preview_page * self._preview_page_size
        end = min(total, start + self._preview_page_size)
        self._preview_indices = self._preview_all_indices[start:end]
        selected = int(self.bridge.selectedIndex) if hasattr(self, 'bridge') else -1
        for pos, idx in enumerate(self._preview_indices):
            card = SampleCard(idx, self.samples[idx], self.labs[idx], self.colors[idx])
            card.setSelected(idx == selected); card.clicked.connect(self.bridge.selectInstance)
            self.preview_grid.addWidget(card, pos//2, pos%2); self._card_by_index[idx] = card
        self.preview_grid.setRowStretch(max(1, (len(self._preview_indices)+1)//2), 1)
        if hasattr(self, 'preview_page_label'):
            self.preview_page_label.setText(f'第 {self._preview_page + 1} / {pages} 页')
            self.preview_prev.setEnabled(self._preview_page > 0)
            self.preview_next.setEnabled(self._preview_page < pages - 1)
            shown = f'{start + 1}-{end}' if total else '0'
            self.preview_summary.setText(
                f'3D 使用全部 {len(self.samples):,} 个真实色样；左侧显示 {shown} / {total:,}（每页 {self._preview_page_size}）'
            )
        try:
            self.preview_scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _control_panel(self):
        panel = QFrame(); panel.setObjectName('controlCard'); panel.setFixedWidth(305)
        lay = QVBoxLayout(panel); lay.setContentsMargins(13, 13, 13, 13); lay.setSpacing(7)
        sec = lambda s: (lambda x: (x.setObjectName('section'), x)[1])(QLabel(s))
        lay.addWidget(sec('显示模式'))
        row = QHBoxLayout(); self.mode_buttons = {}
        for text, mode in [('色彩点云', 'points'), ('表面+点', 'both'), ('色域表面', 'surface')]:
            b = QPushButton(text); b.setCheckable(True); b.setChecked(mode == 'points'); b.clicked.connect(lambda _=False, m=mode: self._set_mode(m)); row.addWidget(b); self.mode_buttons[mode] = b
        lay.addLayout(row)

        def slider_row(label, minimum, maximum, value, callback, formatter):
            head = QHBoxLayout(); head.addWidget(QLabel(label)); val = QLabel(formatter(value)); val.setObjectName('muted'); head.addStretch(1); head.addWidget(val); lay.addLayout(head)
            sl = QSlider(Qt.Horizontal); sl.setRange(minimum, maximum); sl.setValue(value); sl.valueChanged.connect(lambda v: (val.setText(formatter(v)), callback(v))); lay.addWidget(sl); return sl

        lay.addWidget(sec('点云设置'))
        # Dense official libraries need smaller spheres to preserve visual
        # separation.  Every measured point is still rendered; only the
        # presentation radius changes.
        default_point_size = 3 if len(self.samples) >= 3000 else (4 if len(self.samples) >= 1400 else 5)
        # HF67: keep the same visual 2..10 point-size range, but expose ten
        # substeps per visible unit.  The previous integer-only 2..10 slider had
        # just nine positions, so dragging felt coarse next to the opacity sliders
        # even though the QML scale itself supports continuous real values.
        # 20..100 maps exactly to the old 2.0..10.0 radius range.
        def _point_text(v):
            value=v/10.0
            return str(int(value)) if float(value).is_integer() else f'{value:.1f}'
        self.point = slider_row('点大小', 20, 100, default_point_size*10,
                                lambda v: self._root_prop('pointScale', v/1250.0),
                                _point_text)
        self.point.setSingleStep(1); self.point.setPageStep(5)
        self.popacity = slider_row('点透明度', 55, 100, 100, lambda v: self._root_prop('pointOpacity', v/100.0), lambda v: f'{v/100:.2f}')
        self.salpha = slider_row('表面透明度', 5, 55, 22, lambda v: self._root_prop('surfaceOpacity', v/100.0), lambda v: f'{v/100:.2f}')

        lay.addWidget(sec('显示选项'))
        self.axes = QCheckBox('显示 L* / a* / b* 轴'); self.axes.setChecked(True); self.axes.toggled.connect(lambda v: self._root_prop('showAxes', v)); lay.addWidget(self.axes)
        self.grid = QCheckBox('显示 a*b* 参考网格'); self.grid.setChecked(True); self.grid.toggled.connect(lambda v: self._root_prop('showGrid', v)); lay.addWidget(self.grid)
        self.labels = QCheckBox('显示坐标标签'); self.labels.setChecked(True); self.labels.toggled.connect(lambda v: self._root_prop('showLabels', v)); lay.addWidget(self.labels)

        lay.addWidget(sec('视图控制')); vr = QGridLayout()
        for i, text in enumerate(('透视', '正面', '侧面', '俯视', '仰视')):
            b = QPushButton(text); b.clicked.connect(lambda _=False, j=i: self._call_root('setPreset', j)); vr.addWidget(b, i//3, i%3)
        self.auto = QPushButton('自动旋转'); self.auto.setCheckable(True); self.auto.toggled.connect(lambda v: self._root_prop('autoRotate', v)); vr.addWidget(self.auto, 1, 2)
        lay.addLayout(vr)
        reset = QPushButton('重置视角  R'); reset.clicked.connect(lambda: self._call_root('resetView')); lay.addWidget(reset)

        lay.addWidget(sec('颜色信息（当前选中）'))
        info = QFrame(); info.setObjectName('infoCard'); il = QVBoxLayout(info); il.setContentsMargins(10, 10, 10, 10); il.setSpacing(7)
        top = QHBoxLayout(); self.swatch = QLabel(); self.swatch.setFixedSize(70, 64); self.swatch.setStyleSheet('background:#E7ECF3;border-radius:9px;'); top.addWidget(self.swatch)
        nt = QVBoxLayout(); self.selected_name = QLabel('未选择色样'); f = self.selected_name.font(); f.setBold(True); f.setPointSize(11); self.selected_name.setFont(f); nt.addWidget(self.selected_name); self.selected_source = QLabel(self.context_label.split('·')[0].strip()); self.selected_source.setObjectName('muted'); nt.addWidget(self.selected_source); top.addLayout(nt, 1); il.addLayout(top)
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(4)
        labh = QLabel('Lab（笛卡尔坐标）'); labh.setObjectName('coordHead'); lchh = QLabel('LCh（柱坐标）'); lchh.setObjectName('coordHead'); grid.addWidget(labh, 0, 0, 1, 2); grid.addWidget(lchh, 0, 2, 1, 2)
        self.info_labels = {}
        for rowi, key in enumerate(('L*', 'a*', 'b*'), 1):
            grid.addWidget(QLabel(key), rowi, 0); val = QLabel('—'); val.setStyleSheet('font-weight:700;color:#1E385D;'); grid.addWidget(val, rowi, 1); self.info_labels[key] = val
        for rowi, key in enumerate(('C*', 'h°', 'L*_lch'), 1):
            label = 'L*' if key == 'L*_lch' else key; grid.addWidget(QLabel(label), rowi, 2); val = QLabel('—'); val.setStyleSheet('font-weight:700;color:#1E385D;'); grid.addWidget(val, rowi, 3); self.info_labels[key] = val
        il.addLayout(grid); lay.addWidget(info)
        lay.addStretch(1)
        return panel

    def _set_coord_mode(self, mode):
        self._coord_mode = mode
        for m, b in self.coord_buttons.items(): b.setChecked(m == mode)
        self.coordinate_stack.setCurrentIndex({'space': 0, 'lab': 1, 'lch': 2}[mode])
        if hasattr(self, 'status'):
            suffix = {'space': 'CIELAB 3D 空间', 'lab': 'L*a*b* 笛卡尔坐标', 'lch': 'L*C*h° 柱坐标'}[mode]
            self.status.setText(f'{BUILD_ID} · {len(self.samples):,} 个真实色样 · {suffix}')

    def _set_mode(self, mode):
        # HF46: the lightweight gamut mesh is prebuilt before QML binds to it.
        # This avoids the unreliable post-binding geometry refresh that made
        # “表面+点 / 色域表面” appear empty in HF43-HF45.
        for m, b in self.mode_buttons.items(): b.setChecked(m == mode)
        self._root_prop('displayMode', mode)

    def _root_prop(self, name, value):
        root = self.quick.rootObject() if hasattr(self, 'quick') else None
        if root is not None: root.setProperty(name, value)

    def _call_root(self, name, *args):
        root = self.quick.rootObject() if hasattr(self, 'quick') else None
        if root is not None:
            try: getattr(root, name)(*args)
            except Exception:
                if name == 'resetView': root.setProperty('resetToken', int(root.property('resetToken') or 0)+1)
                elif name == 'setPreset' and args: root.setProperty('presetIndex', int(args[0]))

    def _refresh_selected(self):
        idx = int(self.bridge.selectedIndex)
        for i, card in self._card_by_index.items():
            card.setSelected(i == idx)
        if 0 <= idx < len(self.labs):
            self.selected_name.setText(self.bridge.selectedName)
            self.selected_source.setText(self.context_label.split('·')[0].strip())
            self.swatch.setStyleSheet(
                f'background:{self.bridge.selectedHex};border:1px solid #E1E6EC;border-radius:9px;'
            )
            L, a, b = self.labs[idx]
            _, C, h = _lch((L, a, b))
            self.info_labels['L*'].setText(f'{L:.1f}')
            self.info_labels['a*'].setText(f'{a:+.1f}')
            self.info_labels['b*'].setText(f'{b:+.1f}')
            self.info_labels['C*'].setText(f'{C:.1f}')
            self.info_labels['h°'].setText(f'{h:.1f}°')
            self.info_labels['L*_lch'].setText(f'{L:.1f}')
        else:
            self.selected_name.setText('未选择色样')
            self.selected_source.setText('点击左侧色卡或 3D 色点查看')
            self.swatch.setStyleSheet('background:#EEF2F5;border:1px solid #E1E6EC;border-radius:9px;')
            for v in self.info_labels.values():
                v.setText('—')
        self.lab_view.update()
        self.lch_view.update()

    def _quick_status(self, status):
        if status == QQuickView.Status.Error:
            errs = '; '.join(e.toString() for e in self.quick.errors())
            self._use_fallback(f'Qt Quick 3D/QML 加载失败（阶段 {self._init_stage}）：'+errs)
        elif status == QQuickView.Status.Ready:
            self._init_stage = 'qml-ready'
            # Apply HF41 premium visual defaults after QML root exists.
            self._root_prop('pointScale', self.point.value()/1250.0 if hasattr(self, 'point') else 0.032)
            self._root_prop('pointOpacity', self.popacity.value()/100.0 if hasattr(self, 'popacity') else 1.0)
            self._root_prop('surfaceOpacity', self.salpha.value()/100.0 if hasattr(self, 'salpha') else 0.22)

    def _first_frame(self):
        if self._frame_ready or self._quick_failed: return
        self._frame_ready = True; api = 'Qt RHI'
        try: api = str(self.quick.rendererInterface().graphicsApi()).split('.')[-1]
        except Exception: pass
        if self._coord_mode == 'space': self.status.setText(f'{BUILD_ID} · {len(self.samples):,} 个真实色样 · Qt Quick 3D / RHI · {api} · GPU 实例化')

    def _probe_frame(self):
        if self._frame_ready or self._quick_failed: return
        if self.quick.status() != QQuickView.Status.Ready: return
        try:
            img = self.quick.grabWindow()
            if img is not None and not img.isNull() and img.width() > 16 and img.height() > 16: self._first_frame()
        except Exception: pass

    def _health_check(self):
        self._probe_frame()
        if not self._frame_ready and not self._quick_failed: self._use_fallback('Qt Quick 3D / RHI 在启动时限内未生成有效帧')

    def _use_fallback(self, reason):
        if self._quick_failed: return
        self._quick_failed = True; self.render_stack.setCurrentWidget(self.fallback)
        self.status.setText(f'{BUILD_ID} · {len(self.samples):,} 个真实色样 · 同窗兼容渲染 · {reason}')

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.auto.toggle(); event.accept(); return
        if event.key() == Qt.Key_R:
            self._call_root('resetView'); event.accept(); return
        super().keyPressEvent(event)

    def closeEvent(self, e):
        try:
            if hasattr(self, 'quick'):
                self.quick.setSource(QUrl()); self.quick.close()
        except Exception: pass
        try: os.remove(self._instance_path)
        except Exception: pass
        super().closeEvent(e)
