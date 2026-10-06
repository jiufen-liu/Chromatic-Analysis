from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QMatrix4x4, QMouseEvent, QPainter, QPen,
    QSurfaceFormat, QVector2D, QVector3D, QVector4D,
    QOpenGLContext,
)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QSlider, QToolTip, QVBoxLayout, QWidget,
)


# OpenGL constants used through Qt's portable OpenGL function wrappers.
GL_FLOAT = 0x1406
GL_TRIANGLE_STRIP = 0x0005
GL_POINTS = 0x0000
GL_VERTEX_PROGRAM_POINT_SIZE = 0x8642
GL_NO_ERROR = 0x0000
GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_BLEND = 0x0BE2
GL_DEPTH_TEST = 0x0B71
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_LEQUAL = 0x0203


@dataclass
class RenderSample:
    sample: object
    lab: tuple[float, float, float]
    color: QColor


def _lab_to_qcolor(lab: tuple[float, float, float]) -> QColor:
    """Small local Lab->sRGB helper for derived surface vertices only.

    Real sample point colours are supplied by main_window.sample_display_qcolor,
    so this helper never changes measured data or the application's colour math.
    """
    L, a, b = map(float, lab)
    L = max(0.0, min(100.0, L))
    d = 6 / 29

    def linear_rgb(scale: float):
        aa, bb = a * scale, b * scale
        fy = (L + 16) / 116
        fx = fy + aa / 500
        fz = fy - bb / 200

        def inv(t):
            return t ** 3 if t > d else 3 * d * d * (t - 4 / 29)

        x, y, z = inv(fx) * .95047, inv(fy), inv(fz) * 1.08883
        return (
            3.2406 * x - 1.5372 * y - .4986 * z,
            -.9689 * x + 1.8758 * y + .0415 * z,
            .0557 * x - .2040 * y + 1.0570 * z,
        )

    rgb = linear_rgb(1.0)
    if not all(0.0 <= c <= 1.0 for c in rgb):
        lo, hi = 0.0, 1.0
        for _ in range(18):
            mid = (lo + hi) * 0.5
            candidate = linear_rgb(mid)
            if all(0.0 <= c <= 1.0 for c in candidate):
                lo = mid
            else:
                hi = mid
        rgb = linear_rgb(lo * .97)

    def gamma(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= .0031308 else 1.055 * c ** (1 / 2.4) - .055

    return QColor.fromRgbF(*(gamma(c) for c in rgb))


def _qcolor_rgba(c: QColor, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b, a = c.getRgbF()
    return float(r), float(g), float(b), float(a * alpha)


class LabOpenGLView(QOpenGLWidget):
    """GPU-rendered CIELAB viewport.

    Points are rendered as one batched GPU vertex stream.  The compatibility
    baseline is OpenGL 2.0 / OpenGL ES 2.0 rather than a hard 3.3 Core request.
    The optional gamut surface is a data envelope built from the currently
    supplied Lab samples, not a replacement for the real points.
    """

    glStatusChanged = Signal(str)
    glFailed = Signal(str)
    frameReady = Signal()
    hoverChanged = Signal(object)

    def __init__(self, render_samples: list[RenderSample], parent=None):
        super().__init__(parent)
        self.samples = list(render_samples)
        self.mode = "surface"  # surface / points / both
        self.surface_alpha = .54
        self.edge_alpha = .20
        self.point_size = 5.0
        self.show_axes = True
        self.show_grid = True
        self.show_points_on_surface = False
        self.yaw = -34.0
        self.pitch = 18.0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._last_pos: QPoint | None = None
        self._dragging = False
        self._hover_sample = None
        self._hover_cache_key = None
        self._hover_grid = {}
        self._hover_cell = 28.0
        self._mvp = QMatrix4x4()
        self._gl_error = ""
        self._gl_ready = False
        self._frame_emitted = False
        self._instance_count = 0
        self._surface_vertex_count = 0
        self._wire_vertex_count = 0
        self._reference_data: list[tuple[tuple[float, float, float], QColor]] = []
        self._reference_visible = False
        self._reference_instance_count = 0

        # OpenGL objects are only create()'d in initializeGL, when a context is current.
        self._point_program = QOpenGLShaderProgram(self)
        self._surface_program = QOpenGLShaderProgram(self)
        self._quad_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._instance_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._reference_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._surface_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._wire_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._point_vao = QOpenGLVertexArrayObject(self)
        self._surface_vao = QOpenGLVertexArrayObject(self)
        self._wire_vao = QOpenGLVertexArrayObject(self)
        self._extra = None
        self._funcs = None

        # Compatibility baseline: request OpenGL 2.0 without a core-profile
        # requirement.  Qt/driver may provide a newer context, but old office
        # PCs and virtual/remote desktops are no longer rejected just because
        # they cannot create a 3.3 Core context.  3,500 points are tiny for a
        # single GPU draw call, so instancing is not required for performance.
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(2, 0)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(0)
        fmt.setSamples(0)
        self.setFormat(fmt)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(620, 520)

        self._surface_vertices, self._wire_vertices = self._build_envelope_mesh()

        self._rotate_timer = QTimer(self)
        self._rotate_timer.setInterval(16)
        self._rotate_timer.timeout.connect(self._auto_rotate_tick)
        self.auto_rotate = False
        self.rotation_speed = 22.0  # deg / second
        self._last_rotate_t = time.monotonic()

    # ---------- data preparation ----------
    def _sample_instance_array(self, items: Iterable[RenderSample], alpha=1.0) -> np.ndarray:
        rows = []
        for item in items:
            L, a, b = item.lab
            r, g, bl, al = _qcolor_rgba(item.color, alpha)
            rows.append((a, L - 50.0, b, r, g, bl, al))
        if not rows:
            return np.zeros((0, 7), dtype=np.float32)
        return np.asarray(rows, dtype=np.float32)

    def _reference_instance_array(self) -> np.ndarray:
        rows = []
        for lab, color in self._reference_data:
            L, a, b = lab
            r, g, bl, al = _qcolor_rgba(color, .22)
            rows.append((a, L - 50.0, b, r, g, bl, al))
        return np.asarray(rows, dtype=np.float32) if rows else np.zeros((0, 7), dtype=np.float32)

    def _build_envelope_mesh(self, l_slices: int = 12, hue_bins: int = 48):
        labs = [x.lab for x in self.samples if all(math.isfinite(v) for v in x.lab)]
        if len(labs) < 60:
            return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)

        Ls = [x[0] for x in labs]
        lo = max(0.0, min(Ls))
        hi = min(100.0, max(Ls))
        if hi - lo < 6:
            return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)

        radii = np.zeros((l_slices, hue_bins), dtype=np.float32)
        counts = np.zeros((l_slices, hue_bins), dtype=np.int32)
        stepL = (hi - lo) / max(1, l_slices - 1)

        for L, a, b in labs:
            c = math.hypot(a, b)
            if c <= .01:
                continue
            li = int(round((L - lo) / max(1e-9, hi - lo) * (l_slices - 1)))
            li = max(0, min(l_slices - 1, li))
            h = (math.atan2(b, a) + 2 * math.pi) % (2 * math.pi)
            hi_idx = int(h / (2 * math.pi) * hue_bins) % hue_bins
            # Keep a robust outer envelope without letting one extreme outlier dominate.
            radii[li, hi_idx] = max(radii[li, hi_idx], min(float(c), 150.0))
            counts[li, hi_idx] += 1

        global_nonzero = radii[radii > 0]
        if global_nonzero.size < hue_bins:
            return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)
        global_default = float(np.percentile(global_nonzero, 55))

        # Fill angular gaps from nearest known neighbors on the same slice.
        for li in range(l_slices):
            ring = radii[li]
            known = np.where(ring > 0)[0]
            if known.size == 0:
                continue
            for j in range(hue_bins):
                if ring[j] > 0:
                    continue
                best = min(known, key=lambda k: min((j-k) % hue_bins, (k-j) % hue_bins))
                ring[j] = ring[best]

        # Fill empty L slices from nearest populated slice.
        populated = [i for i in range(l_slices) if np.any(radii[i] > 0)]
        if not populated:
            return np.zeros((0, 7), np.float32), np.zeros((0, 7), np.float32)
        for li in range(l_slices):
            if not np.any(radii[li] > 0):
                nearest = min(populated, key=lambda k: abs(k-li))
                radii[li] = radii[nearest]

        # Gentle circular and vertical smoothing -> professional shell, not jagged spikes.
        for _ in range(3):
            radii = .18 * np.roll(radii, 1, axis=1) + .64 * radii + .18 * np.roll(radii, -1, axis=1)
        for _ in range(2):
            temp = radii.copy()
            for li in range(1, l_slices-1):
                temp[li] = .16*radii[li-1] + .68*radii[li] + .16*radii[li+1]
            radii = temp

        # Avoid a blunt cylinder: taper top and bottom while preserving measured outline.
        for li in range(l_slices):
            t = li / max(1, l_slices - 1)
            taper = .48 + .52 * (math.sin(math.pi * t) ** .42)
            radii[li] = np.maximum(radii[li] * taper, global_default * .10)

        rings = []
        for li in range(l_slices):
            L = lo + stepL * li
            ring = []
            for j in range(hue_bins):
                h = 2 * math.pi * j / hue_bins
                c = float(radii[li, j])
                a = c * math.cos(h)
                b = c * math.sin(h)
                col = _lab_to_qcolor((L, a, b))
                r, g, bl, _ = _qcolor_rgba(col, 1.0)
                ring.append((a, L-50.0, b, r, g, bl, 1.0))
            rings.append(ring)

        tris = []
        lines = []
        for li in range(l_slices - 1):
            for j in range(hue_bins):
                nj = (j + 1) % hue_bins
                a0, a1 = rings[li][j], rings[li][nj]
                b0, b1 = rings[li+1][j], rings[li+1][nj]
                tris.extend((a0, b0, b1, a0, b1, a1))
        # Restrained wireframe: all L rings + every fourth longitude.
        for li in range(l_slices):
            for j in range(hue_bins):
                lines.extend((rings[li][j], rings[li][(j+1) % hue_bins]))
        for j in range(0, hue_bins, 4):
            for li in range(l_slices - 1):
                lines.extend((rings[li][j], rings[li+1][j]))

        return np.asarray(tris, dtype=np.float32), np.asarray(lines, dtype=np.float32)

    # ---------- OpenGL ----------
    @staticmethod
    def _compile(program: QOpenGLShaderProgram, vertex_src: str, frag_src: str):
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex_src):
            raise RuntimeError(program.log())
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, frag_src):
            raise RuntimeError(program.log())
        if not program.link():
            raise RuntimeError(program.log())

    def _shader_sources(self, is_es: bool):
        """Return an OpenGL-2/ES2 compatible shader set.

        The previous Hotfix34 used GLSL 330 + vertex instancing.  That is fast
        but unnecessarily excludes many integrated GPUs, remote desktops and
        older company PCs.  This backend sends every sample as one vertex and
        renders all points in ONE glDrawArrays(GL_POINTS) call.  1k-10k points
        are trivial for even very old GPUs and this path only needs GL2 / ES2.
        """
        if is_es:
            point_vs = r"""
                #version 100
                attribute vec3 aPos;
                attribute vec4 aColor;
                uniform mat4 uMVP;
                uniform float uPointSize;
                varying vec4 vColor;
                void main(){
                    gl_Position = uMVP * vec4(aPos, 1.0);
                    gl_PointSize = uPointSize;
                    vColor = aColor;
                }
            """
            point_fs = r"""
                #version 100
                precision mediump float;
                varying vec4 vColor;
                void main(){
                    vec2 q = gl_PointCoord * 2.0 - 1.0;
                    float d2 = dot(q,q);
                    if (d2 > 1.0) discard;
                    float edge = 1.0 - smoothstep(0.72, 1.0, sqrt(d2));
                    float hi = max(0.0, 1.0 - distance(q, vec2(-0.28,0.30))*1.45);
                    vec3 rgb = mix(vColor.rgb, vec3(1.0), hi*0.12);
                    gl_FragColor = vec4(rgb, vColor.a * edge);
                }
            """
            surf_vs = r"""
                #version 100
                attribute vec3 aPos;
                attribute vec4 aColor;
                uniform mat4 uMVP;
                varying vec4 vColor;
                void main(){ gl_Position=uMVP*vec4(aPos,1.0); vColor=aColor; }
            """
            surf_fs = r"""
                #version 100
                precision mediump float;
                varying vec4 vColor;
                uniform float uAlpha;
                void main(){ gl_FragColor=vec4(vColor.rgb, vColor.a*uAlpha); }
            """
        else:
            point_vs = r"""
                #version 120
                attribute vec3 aPos;
                attribute vec4 aColor;
                uniform mat4 uMVP;
                uniform float uPointSize;
                varying vec4 vColor;
                void main(){
                    gl_Position = uMVP * vec4(aPos, 1.0);
                    gl_PointSize = uPointSize;
                    vColor = aColor;
                }
            """
            point_fs = r"""
                #version 120
                varying vec4 vColor;
                void main(){
                    vec2 q = gl_PointCoord * 2.0 - 1.0;
                    float d2 = dot(q,q);
                    if (d2 > 1.0) discard;
                    float edge = 1.0 - smoothstep(0.72, 1.0, sqrt(d2));
                    float hi = max(0.0, 1.0 - distance(q, vec2(-0.28,0.30))*1.45);
                    vec3 rgb = mix(vColor.rgb, vec3(1.0), hi*0.12);
                    gl_FragColor = vec4(rgb, vColor.a * edge);
                }
            """
            surf_vs = r"""
                #version 120
                attribute vec3 aPos;
                attribute vec4 aColor;
                uniform mat4 uMVP;
                varying vec4 vColor;
                void main(){ gl_Position=uMVP*vec4(aPos,1.0); vColor=aColor; }
            """
            surf_fs = r"""
                #version 120
                varying vec4 vColor;
                uniform float uAlpha;
                void main(){ gl_FragColor=vec4(vColor.rgb, vColor.a*uAlpha); }
            """
        return point_vs, point_fs, surf_vs, surf_fs

    def initializeGL(self):
        try:
            ctx = QOpenGLContext.currentContext()
            if ctx is None:
                raise RuntimeError("系统没有建立可用的 OpenGL 上下文")
            self._funcs = ctx.functions()
            self._funcs.initializeOpenGLFunctions()
            self._extra = ctx.extraFunctions()
            try:
                self._extra.initializeOpenGLFunctions()
            except Exception:
                self._extra = None

            point_vs, point_fs, surf_vs, surf_fs = self._shader_sources(bool(ctx.isOpenGLES()))
            self._compile(self._point_program, point_vs, point_fs)
            self._compile(self._surface_program, surf_vs, surf_fs)

            # One interleaved VBO, one GPU draw call.  No VAO, no instancing,
            # no Core-profile-only API: this is intentionally the broadest Qt
            # OpenGL path that still keeps thousands of samples on the GPU.
            instances = self._sample_instance_array(self.samples)
            self._instance_count = len(instances)
            self._instance_vbo.create(); self._instance_vbo.bind()
            self._instance_vbo.allocate(instances.tobytes(), instances.nbytes)
            self._instance_vbo.release()
            self._reference_vbo.create()

            self._surface_vbo.create(); self._surface_vbo.bind()
            self._surface_vbo.allocate(self._surface_vertices.tobytes(), self._surface_vertices.nbytes)
            self._surface_vertex_count = len(self._surface_vertices)
            self._surface_vbo.release()

            self._wire_vbo.create(); self._wire_vbo.bind()
            self._wire_vbo.allocate(self._wire_vertices.tobytes(), self._wire_vertices.nbytes)
            self._wire_vertex_count = len(self._wire_vertices)
            self._wire_vbo.release()

            # Desktop GL needs programmable point size enabled on some drivers.
            if not ctx.isOpenGLES():
                try: self._funcs.glEnable(GL_VERTEX_PROGRAM_POINT_SIZE)
                except Exception: pass

            self._gl_ready = True
            fmt = ctx.format()
            api = "OpenGL ES" if ctx.isOpenGLES() else "OpenGL"
            self.glStatusChanged.emit(
                f"{api} {fmt.majorVersion()}.{fmt.minorVersion()} · GPU 批量绘制 · 自动兼容"
            )
        except Exception as exc:
            self._gl_error = str(exc)
            self._gl_ready = False
            msg = "GPU 渲染初始化失败：" + self._gl_error
            self.glStatusChanged.emit(msg)
            self.glFailed.emit(msg)

    def _bind_interleaved(self, program: QOpenGLShaderProgram, buffer: QOpenGLBuffer):
        buffer.bind()
        pos = program.attributeLocation("aPos")
        col = program.attributeLocation("aColor")
        if pos >= 0:
            program.enableAttributeArray(pos)
            program.setAttributeBuffer(pos, GL_FLOAT, 0, 3, 7*4)
        if col >= 0:
            program.enableAttributeArray(col)
            program.setAttributeBuffer(col, GL_FLOAT, 3*4, 4, 7*4)
        return pos, col

    @staticmethod
    def _unbind_interleaved(program: QOpenGLShaderProgram, buffer: QOpenGLBuffer, locs):
        for loc in locs:
            if loc >= 0:
                program.disableAttributeArray(loc)
        buffer.release()

    def _draw_point_buffer(self, mvp: QMatrix4x4, buffer: QOpenGLBuffer, count: int, size: float):
        if not count:
            return
        self._point_program.bind()
        self._point_program.setUniformValue("uMVP", mvp)
        # Device-pixel ratio keeps points visually consistent on 125/150/200% Windows scaling.
        self._point_program.setUniformValue("uPointSize", float(size * max(1.0, self.devicePixelRatioF())))
        locs = self._bind_interleaved(self._point_program, buffer)
        self._funcs.glDrawArrays(GL_POINTS, 0, int(count))
        self._unbind_interleaved(self._point_program, buffer, locs)
        self._point_program.release()

    def _draw_points(self, mvp: QMatrix4x4):
        self._draw_point_buffer(mvp, self._instance_vbo, self._instance_count, self.point_size)

    def _draw_reference(self, mvp: QMatrix4x4):
        if self._reference_visible and self._reference_instance_count:
            self._draw_point_buffer(mvp, self._reference_vbo, self._reference_instance_count,
                                    max(2.0, self.point_size*.58))

    def _draw_surface_buffer(self, mvp, buffer, count, primitive, alpha):
        if not count:
            return
        self._surface_program.bind()
        self._surface_program.setUniformValue("uMVP", mvp)
        self._surface_program.setUniformValue("uAlpha", float(alpha))
        locs = self._bind_interleaved(self._surface_program, buffer)
        self._funcs.glDrawArrays(primitive, 0, int(count))
        self._unbind_interleaved(self._surface_program, buffer, locs)
        self._surface_program.release()

    def _draw_surface(self, mvp: QMatrix4x4):
        if not self._surface_vertex_count:
            return
        self._funcs.glDepthMask(False)
        self._draw_surface_buffer(mvp, self._surface_vbo, self._surface_vertex_count,
                                  GL_TRIANGLES, self.surface_alpha)
        if self.edge_alpha > .001 and self._wire_vertex_count:
            self._draw_surface_buffer(mvp, self._wire_vbo, self._wire_vertex_count,
                                      GL_LINES, self.edge_alpha)
        self._funcs.glDepthMask(True)

    def _matrix(self) -> QMatrix4x4:
        aspect = max(.2, self.width() / max(1.0, float(self.height())))
        projection = QMatrix4x4()
        projection.perspective(30.0, aspect, 0.1, 1400.0)
        view = QMatrix4x4()
        camera_z = 360.0 / max(.35, self.zoom)
        view.lookAt(QVector3D(0, 0, camera_z), QVector3D(0, 0, 0), QVector3D(0, 1, 0))
        model = QMatrix4x4()
        model.translate(self.pan_x, self.pan_y, 0.0)
        model.rotate(self.pitch, 1.0, 0.0, 0.0)
        model.rotate(self.yaw, 0.0, 1.0, 0.0)
        return projection * view * model

    def paintGL(self):
        if not self._funcs:
            return
        self._funcs.glViewport(0, 0, max(1,self.width()), max(1,self.height()))
        self._funcs.glClearColor(0.965, 0.975, 0.993, 1.0)
        self._funcs.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._funcs.glEnable(GL_DEPTH_TEST)
        self._funcs.glDepthFunc(GL_LEQUAL)
        self._funcs.glEnable(GL_BLEND)
        self._funcs.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        mvp = self._matrix(); self._mvp = mvp
        if self._gl_ready:
            if self.mode in ("surface", "both"):
                self._draw_surface(mvp)
            if self.mode == "points" or self.mode == "both" or (self.mode == "surface" and self.show_points_on_surface):
                self._draw_reference(mvp)
                self._draw_points(mvp)
            # A successful first paint is the health handshake used by the parent
            # dialog.  If Qt cannot create/paint this widget, a timeout will
            # automatically switch the user to the proven CPU renderer.
            try:
                err = self._funcs.glGetError()
            except Exception:
                err = GL_NO_ERROR
            if err not in (None, GL_NO_ERROR):
                self._gl_ready = False
                self._gl_error = f"OpenGL 绘制错误 0x{int(err):04X}"
                self.glFailed.emit(self._gl_error)
            elif not self._frame_emitted:
                self._frame_emitted = True
                self.frameReady.emit()

        # Qt painter overlay: exact axes, grid, labels and hover card. Tiny cost vs thousands of GPU points.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._gl_error:
            p.setPen(QColor("#B42318")); p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            p.drawText(self.rect().adjusted(30,30,-30,-30), Qt.AlignCenter | Qt.TextWordWrap,
                       "OpenGL 3D 初始化失败\n\n" + self._gl_error)
        else:
            self._paint_axes(p, mvp)
            self._paint_hover(p, mvp)
        p.end()

    # ---------- 2D overlay / hit testing ----------
    def _project_world(self, v: QVector3D, mvp: QMatrix4x4 | None = None):
        mat = mvp or self._mvp
        clip = mat * QVector4D(v.x(), v.y(), v.z(), 1.0)
        w = clip.w()
        if abs(w) < 1e-8:
            return None
        nx, ny, nz = clip.x()/w, clip.y()/w, clip.z()/w
        return QPointF((nx*.5+.5)*self.width(), (-ny*.5+.5)*self.height()), nz

    def _paint_axes(self, p: QPainter, mvp: QMatrix4x4):
        if not self.show_axes:
            return
        axis_pen = QPen(QColor(67, 81, 104, 185), 1.25)
        grid_pen = QPen(QColor(112, 132, 160, 42), 1.0)
        grid_pen.setStyle(Qt.DashLine)
        p.setFont(QFont("Segoe UI", 8))

        if self.show_grid:
            p.setPen(grid_pen)
            for t in (-100,-75,-50,-25,0,25,50,75,100):
                a = self._project_world(QVector3D(-100,0,t), mvp); b = self._project_world(QVector3D(100,0,t),mvp)
                if a and b: p.drawLine(a[0],b[0])
                a = self._project_world(QVector3D(t,0,-100),mvp); b = self._project_world(QVector3D(t,0,100),mvp)
                if a and b: p.drawLine(a[0],b[0])

        p.setPen(axis_pen)
        axes = [
            (QVector3D(-112,0,0), QVector3D(112,0,0), "-a", "+a"),
            (QVector3D(0,0,-112), QVector3D(0,0,112), "-b", "+b"),
            (QVector3D(0,-54,0), QVector3D(0,56,0), "-L", "+L"),
        ]
        for v0,v1,l0,l1 in axes:
            q0=self._project_world(v0,mvp); q1=self._project_world(v1,mvp)
            if not q0 or not q1: continue
            p.drawLine(q0[0],q1[0])
            f=p.font(); f.setPointSize(9); f.setBold(True); p.setFont(f)
            p.drawText(q0[0]+QPointF(-18,12),l0); p.drawText(q1[0]+QPointF(5,-4),l1)
            f.setBold(False); f.setPointSize(8); p.setFont(f)

        # ticks: useful analytical context without visual noise.
        p.setPen(QColor(78,91,112,145))
        for t in (-100,-50,0,50,100):
            for pos in (QVector3D(t,0,0), QVector3D(0,0,t)):
                q=self._project_world(pos,mvp)
                if q:p.drawText(q[0]+QPointF(3,13),str(t))
        for L in (0,50,100):
            q=self._project_world(QVector3D(0,L-50,0),mvp)
            if q:p.drawText(q[0]+QPointF(6,-4),str(L))

    def _hover_screen_cache(self):
        key=(self.width(),self.height(),round(self.yaw,3),round(self.pitch,3),round(self.zoom,3),round(self.pan_x,2),round(self.pan_y,2))
        if key == self._hover_cache_key:
            return
        grid={}; cell=self._hover_cell
        for item in self.samples:
            L,a,b=item.lab
            proj=self._project_world(QVector3D(a,L-50,b))
            if not proj: continue
            q,z=proj
            if z < -1.15 or z > 1.15: continue
            k=(int(q.x()//cell),int(q.y()//cell)); grid.setdefault(k,[]).append((q,item))
        self._hover_cache_key=key; self._hover_grid=grid

    def _paint_hover(self, p: QPainter, mvp: QMatrix4x4):
        item=self._hover_sample
        if item is None:return
        L,a,b=item.lab; c=math.hypot(a,b); h=(math.degrees(math.atan2(b,a))+360)%360
        proj=self._project_world(QVector3D(a,L-50,b),mvp)
        if not proj:return
        q=proj[0]
        p.setPen(QPen(QColor("#FFFFFF"),2)); p.setBrush(item.color)
        p.drawEllipse(q,7,7)
        box_w,box_h=245,86
        left=min(self.width()-box_w-16,max(16,q.x()+18)); top=min(self.height()-box_h-16,max(16,q.y()-box_h*.5))
        box=QRectF(left,top,box_w,box_h)
        p.setPen(QPen(QColor(204,214,229,220),1)); p.setBrush(QColor(255,255,255,238)); p.drawRoundedRect(box,12,12)
        p.setPen(QColor("#152238")); f=QFont("Segoe UI",9,QFont.Weight.DemiBold); p.setFont(f)
        name=str(getattr(item.sample,"display_name","色样")); p.drawText(box.adjusted(12,9,-12,-55),Qt.AlignLeft|Qt.AlignVCenter,name)
        f.setWeight(QFont.Weight.Normal); f.setPointSize(8); p.setFont(f); p.setPen(QColor("#516279"))
        p.drawText(box.adjusted(12,35,-12,-28),Qt.AlignLeft,f"L* {L:.2f}    a* {a:.2f}    b* {b:.2f}")
        p.drawText(box.adjusted(12,57,-12,-7),Qt.AlignLeft,f"C* {c:.2f}    h° {h:.1f}")

    # ---------- interaction ----------
    def mousePressEvent(self, e: QMouseEvent):
        self.setFocus(); self._last_pos=e.position().toPoint(); self._dragging=True
        if e.button()==Qt.LeftButton and self.auto_rotate:self.set_auto_rotate(False)
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._last_pos=None; self._dragging=False; self._hover_cache_key=None; self.update(); e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        pos=e.position().toPoint()
        if self._last_pos is not None and (e.buttons() & Qt.LeftButton):
            d=pos-self._last_pos
            if e.modifiers() & Qt.ShiftModifier:
                self.pan_x += d.x()*.16/max(.35,self.zoom)
                self.pan_y -= d.y()*.16/max(.35,self.zoom)
            else:
                self.yaw += d.x()*.36
                self.pitch = max(-82.0,min(82.0,self.pitch+d.y()*.28))
            self._last_pos=pos; self._hover_sample=None; self._hover_cache_key=None; self.update(); return
        if self._last_pos is not None and (e.buttons() & (Qt.MiddleButton|Qt.RightButton)):
            d=pos-self._last_pos; self.pan_x += d.x()*.16/max(.35,self.zoom); self.pan_y -= d.y()*.16/max(.35,self.zoom); self._last_pos=pos; self._hover_cache_key=None; self.update(); return

        self._hover_screen_cache()
        cell=self._hover_cell; cx=int(pos.x()//cell); cy=int(pos.y()//cell); best=None; bestd=18.0**2
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for q,item in self._hover_grid.get((cx+dx,cy+dy),()):
                    d=(q.x()-pos.x())**2+(q.y()-pos.y())**2
                    if d<bestd:bestd=d;best=item
        if best is not self._hover_sample:
            self._hover_sample=best; self.hoverChanged.emit(best.sample if best else None); self.update()

    def wheelEvent(self,e):
        self.zoom=max(.35,min(4.2,self.zoom*(1.13**(e.angleDelta().y()/120.0)))); self._hover_cache_key=None; self.update(); e.accept()

    def mouseDoubleClickEvent(self,e):self.reset_view(); e.accept()

    def keyPressEvent(self,e):
        if e.key()==Qt.Key_Space:self.set_auto_rotate(not self.auto_rotate); return
        if e.key() in (Qt.Key_R,Qt.Key_0):self.reset_view(); return
        if e.key()==Qt.Key_1:self.set_view_preset(0); return
        if e.key()==Qt.Key_2:self.set_view_preset(1); return
        if e.key()==Qt.Key_3:self.set_view_preset(2); return
        super().keyPressEvent(e)

    def _auto_rotate_tick(self):
        now=time.monotonic(); dt=min(.05,now-self._last_rotate_t); self._last_rotate_t=now
        self.yaw += self.rotation_speed*dt; self._hover_sample=None; self._hover_cache_key=None; self.update()

    def set_auto_rotate(self,on:bool):
        self.auto_rotate=bool(on); self._last_rotate_t=time.monotonic()
        if self.auto_rotate:self._rotate_timer.start()
        else:self._rotate_timer.stop()
        self.update()

    def reset_view(self):
        self.yaw=-34.0; self.pitch=18.0; self.zoom=1.0; self.pan_x=self.pan_y=0.0; self._hover_cache_key=None; self.update()

    def set_view_preset(self,index:int):
        presets=[(-34,18),(0,0),(-90,0),(0,78)]
        self.yaw,self.pitch=presets[max(0,min(len(presets)-1,index))]; self.zoom=1.0; self.pan_x=self.pan_y=0; self._hover_cache_key=None; self.update()

    def set_mode(self,mode:str):self.mode=mode; self.update()
    def set_surface_alpha(self,v:float):self.surface_alpha=max(.05,min(.92,float(v))); self.update()
    def set_edge_alpha(self,v:float):self.edge_alpha=max(0.0,min(.65,float(v))); self.update()
    def set_point_size(self,v:float):self.point_size=max(1.5,min(16.0,float(v))); self.update()
    def set_axes(self,on:bool):self.show_axes=bool(on); self.update()
    def set_grid(self,on:bool):self.show_grid=bool(on); self.update()

    def set_reference_data(self,data,visible=True):
        self._reference_data=list(data or [])[:2500]; self._reference_visible=bool(visible and self._reference_data)
        if self._gl_ready:
            arr=self._reference_instance_array(); self._reference_instance_count=len(arr)
            self.makeCurrent(); self._reference_vbo.bind(); self._reference_vbo.allocate(arr.tobytes(),arr.nbytes); self._reference_vbo.release(); self.doneCurrent()
        self.update()


class Lab3DDialog(QDialog):
    fallbackRequested = Signal(str)

    """Adaptive GPU CIELAB viewer.

    This dialog is intentionally isolated from the rest of the application:
    it consumes existing Sample objects and owner.sample_lab(), then renders a
    GPU view.  No QTX/CPX/SQLite/colour-calculation path is modified.
    """

    def __init__(self, samples, owner, context_label='', color_fn: Callable | None=None, key_fn: Callable | None=None):
        super().__init__(owner)
        self.samples=list(samples); self.owner=owner; self.context_label=(context_label or '').strip(); self.color_fn=color_fn; self.key_fn=key_fn
        render=[]
        for sm in self.samples:
            lab=owner.sample_lab(sm)
            color=(color_fn(sm,lab) if color_fn else _lab_to_qcolor(lab))
            render.append(RenderSample(sm,tuple(map(float,lab)),QColor(color)))

        self.setWindowTitle(f"3D CIELAB 色彩空间 · {self.context_label or '当前色样'} · {len(self.samples)} 色样 · OpenGL")
        self.resize(1320,850); self.setMinimumSize(920,620)
        self.setStyleSheet(self._style())

        root=QVBoxLayout(self); root.setContentsMargins(14,12,14,12); root.setSpacing(10)
        root.addLayout(self._build_topbar())
        body=QHBoxLayout(); body.setSpacing(12); root.addLayout(body,1)
        controls=self._build_controls(); body.addWidget(controls,0)
        self.view=LabOpenGLView(render,self); body.addWidget(self.view,1)
        self._gpu_frame_ready=False
        self._fallback_emitted=False
        self.view.glStatusChanged.connect(self._set_status)
        self.view.glFailed.connect(self._request_fallback)
        self.view.frameReady.connect(self._gpu_ready)
        self.view.hoverChanged.connect(self._hover_changed)
        if self.view._surface_vertices.size == 0:
            self.mode_surface.setEnabled(False)
            self.mode_both.setEnabled(False)
            self._choose_mode("points")

        self.status=QLabel(f"{len(self.samples)} 个真实色样 · 自动选择渲染后端…")
        self.status.setObjectName("statusLabel"); root.addWidget(self.status)
        # Wait long enough for slower/remote PCs, but never leave a blank viewport.
        QTimer.singleShot(1800,self._check_gl)

    @staticmethod
    def _style():
        return r"""
        QDialog{background:#F4F7FB;color:#172033;}
        QLabel{color:#23324A;}
        QLabel#sectionTitle{font-weight:700;font-size:13px;color:#1E2C43;}
        QLabel#muted{color:#78869B;font-size:11px;}
        QLabel#statusLabel{color:#66758B;font-size:11px;padding:3px 6px;}
        QFrame#controlPanel{background:rgba(255,255,255,235);border:1px solid #DDE6F1;border-radius:14px;}
        QPushButton{background:#FFFFFF;border:1px solid #D9E2EE;border-radius:8px;padding:7px 11px;color:#31415B;}
        QPushButton:hover{border-color:#9BB9F5;background:#F7FAFF;}
        QPushButton:checked{background:#EAF2FF;border-color:#6EA0F7;color:#1763D6;font-weight:700;}
        QComboBox{background:#FFFFFF;border:1px solid #D9E2EE;border-radius:8px;padding:7px 9px;min-height:20px;}
        QCheckBox{spacing:7px;color:#46566F;}
        QSlider::groove:horizontal{height:5px;background:#DCE5F1;border-radius:2px;}
        QSlider::sub-page:horizontal{background:#4A8AF4;border-radius:2px;}
        QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#FFFFFF;border:2px solid #4A8AF4;border-radius:7px;}
        """

    def _build_topbar(self):
        lay=QHBoxLayout(); lay.setSpacing(8)
        title=QLabel("3D 色彩空间"); f=title.font(); f.setPointSize(14); f.setBold(True); title.setFont(f); lay.addWidget(title)
        sub=QLabel("基于 CIELAB 的 GPU 可视化 · 数据坐标保持不变"); sub.setObjectName("muted"); lay.addWidget(sub)
        lay.addStretch(1)
        for text in ("CIELAB 空间","L*a*b*","L*C*h°"):
            b=QPushButton(text); b.setCheckable(True); b.setChecked(text=="CIELAB 空间"); b.setEnabled(text=="CIELAB 空间"); lay.addWidget(b)
        return lay

    def _build_controls(self):
        panel=QFrame(); panel.setObjectName("controlPanel"); panel.setFixedWidth(285)
        lay=QVBoxLayout(panel); lay.setContentsMargins(14,14,14,14); lay.setSpacing(9)
        t=QLabel("色库"); t.setObjectName("sectionTitle"); lay.addWidget(t)
        combo=QComboBox(); combo.addItem(f"{self.context_label or '当前选择'} ({len(self.samples):,})"); lay.addWidget(combo)

        t=QLabel("显示模式"); t.setObjectName("sectionTitle"); lay.addWidget(t)
        row=QHBoxLayout(); self.mode_surface=QPushButton("色域表面"); self.mode_points=QPushButton("色彩点云"); self.mode_both=QPushButton("表面+点")
        for b in (self.mode_surface,self.mode_points,self.mode_both):b.setCheckable(True); row.addWidget(b)
        self.mode_surface.setChecked(True); lay.addLayout(row)
        self.mode_surface.clicked.connect(lambda:self._choose_mode("surface")); self.mode_points.clicked.connect(lambda:self._choose_mode("points")); self.mode_both.clicked.connect(lambda:self._choose_mode("both"))

        lay.addWidget(QLabel("透明度")); self.alpha=QSlider(Qt.Horizontal); self.alpha.setRange(10,90); self.alpha.setValue(54); lay.addWidget(self.alpha); self.alpha.valueChanged.connect(lambda v:self.view.set_surface_alpha(v/100))
        lay.addWidget(QLabel("边缘线")); self.edge=QSlider(Qt.Horizontal); self.edge.setRange(0,50); self.edge.setValue(20); lay.addWidget(self.edge); self.edge.valueChanged.connect(lambda v:self.view.set_edge_alpha(v/100))
        lay.addWidget(QLabel("点大小")); self.point=QSlider(Qt.Horizontal); self.point.setRange(2,14); self.point.setValue(5); lay.addWidget(self.point); self.point.valueChanged.connect(self.view_point_size_later)

        t=QLabel("参考坐标轴"); t.setObjectName("sectionTitle"); lay.addWidget(t)
        axes=QCheckBox("显示 L* / a* / b* 轴"); axes.setChecked(True); axes.toggled.connect(lambda v:self.view.set_axes(v) if hasattr(self,'view') else None); lay.addWidget(axes)
        grid=QCheckBox("显示 a*b* 参考网格"); grid.setChecked(True); grid.toggled.connect(lambda v:self.view.set_grid(v) if hasattr(self,'view') else None); lay.addWidget(grid)

        t=QLabel("视角预设"); t.setObjectName("sectionTitle"); lay.addWidget(t)
        views=QHBoxLayout()
        for i,text in enumerate(("透视","正面","侧面","俯视")):
            b=QPushButton(text); b.clicked.connect(lambda _=False,j=i:self.view.set_view_preset(j)); views.addWidget(b)
        lay.addLayout(views)

        auto=QPushButton("自动旋转   Space"); auto.setCheckable(True); auto.toggled.connect(lambda on:self.view.set_auto_rotate(on)); lay.addWidget(auto)
        reset=QPushButton("重置视角   R"); reset.clicked.connect(lambda:self.view.reset_view()); lay.addWidget(reset)
        ref=QPushButton("选择客户参考点云…"); ref.clicked.connect(self.choose_reference_cloud); lay.addWidget(ref)
        help_text=QLabel("拖动旋转 · Shift/中键平移 · 滚轮缩放\n双击复位 · 1/2/3 视角预设")
        help_text.setObjectName("muted"); help_text.setWordWrap(True); lay.addWidget(help_text)
        lay.addStretch(1)
        return panel

    def view_point_size_later(self,v):
        if hasattr(self,'view'):self.view.set_point_size(float(v))

    def _choose_mode(self,mode):
        for b,m in ((self.mode_surface,"surface"),(self.mode_points,"points"),(self.mode_both,"both")):b.setChecked(m==mode)
        self.view.set_mode(mode)

    def _set_status(self,text):
        self.status.setText(f"{len(self.samples)} 个真实色样 · {text}")

    def _hover_changed(self,sample):
        if sample is None:return
        # Tooltip is optional; main visual callout is painted in the viewport.

    def _gpu_ready(self):
        self._gpu_frame_ready=True

    def _request_fallback(self, reason):
        if self._fallback_emitted:
            return
        self._fallback_emitted=True
        reason=str(reason or "GPU 3D 不可用")
        self.status.setText(f"GPU 后端不可用，正在自动切换兼容模式 · {reason}")
        # Queue the signal so we do not destroy a QOpenGLWidget from inside its
        # own initializeGL()/paintGL() stack frame.
        QTimer.singleShot(0, lambda r=reason:self.fallbackRequested.emit(r))

    def _check_gl(self):
        if self._gpu_frame_ready:
            return
        if not self.view.isValid():
            self._request_fallback("Qt 未能建立有效 OpenGL 上下文")
        else:
            self._request_fallback("GPU 视口在启动时限内没有完成首帧")

    def choose_reference_cloud(self):
        try:
            customers=[str(x) for x in self.owner.store.list_customer_groups() if str(x).strip()]
        except Exception:
            customers=[]
        if not customers:
            QMessageBox.information(self,"客户参考点云","正式色库中还没有已保存的客户数据。")
            return
        from PySide6.QtWidgets import QInputDialog
        customer,ok=QInputDialog.getItem(self,"选择客户参考点云","客户：",sorted(dict.fromkeys(customers),key=str.casefold),0,False)
        if not ok or not customer:return
        try:
            contents=list(self.owner.store.library_contents(customer)); samples=[sm for _row,items in contents for sm in items]
        except Exception:
            samples=[]
        selected_keys={self.key_fn(s) for s in self.samples} if self.key_fn else set()
        data=[]
        if len(samples)>2500:
            step=max(1,len(samples)//2500); samples=samples[::step]
        for sm in samples:
            if self.key_fn and self.key_fn(sm) in selected_keys:continue
            try:
                lab=tuple(map(float,self.owner.sample_lab(sm))); color=self.color_fn(sm,lab) if self.color_fn else _lab_to_qcolor(lab); data.append((lab,QColor(color)))
            except Exception:pass
        self.view.set_reference_data(data,True); self.status.setText(f"{len(self.samples)} 个真实色样 · 参考点云：{customer} ({len(data)})")
