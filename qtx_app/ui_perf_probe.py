from __future__ import annotations

import json
import os
import statistics
import time
from collections import Counter, deque
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QTimer, Qt


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return float(data[lo] * (1.0 - frac) + data[hi] * frac)


class UiPerformanceProbe(QObject):
    """Low-overhead, opt-in probe for real GUI responsiveness.

    Enabled only when CHROMATIC_UI_PERF_PROBE=1. It does not change business
    logic. The probe measures Qt event-loop stalls and an approximate
    input-to-paint response latency while the user performs a normal manual
    regression session.
    """

    def __init__(self, app, output_path: str | Path, interval_ms: int = 50):
        super().__init__(app)
        self.app = app
        self.output_path = Path(output_path)
        self.interval_ms = int(max(20, interval_ms))
        self.started_wall = time.time()
        self.started_perf = time.perf_counter()
        self._last_tick = self.started_perf
        self._timer_delays_ms: list[float] = []
        self._stall_events: list[dict] = []
        self._input_to_paint_ms: list[float] = []
        self._pending_input_at: float | None = None
        self._pending_input_kind: str = ''
        self._event_counts = Counter()
        self._input_counts = Counter()
        self._window_shows: list[dict] = []
        self.main_window_show_ms: float | None = None
        self.main_window_first_paint_ms: float | None = None
        self._recent_stalls = deque(maxlen=200)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._heartbeat)
        self.timer.start()
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.write_report)

    def _heartbeat(self) -> None:
        now = time.perf_counter()
        elapsed_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        delay_ms = max(0.0, elapsed_ms - self.interval_ms)
        self._timer_delays_ms.append(delay_ms)
        if delay_ms >= 100.0:
            item = {
                'at_s': round(now - self.started_perf, 3),
                'delay_ms': round(delay_ms, 3),
                'severity': (
                    'severe' if delay_ms >= 1000 else
                    'freeze' if delay_ms >= 500 else
                    'slow' if delay_ms >= 250 else
                    'noticeable'
                ),
            }
            self._stall_events.append(item)
            self._recent_stalls.append(item)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API name
        try:
            et = event.type()
            self._event_counts[str(int(et))] += 1

            input_types = {
                QEvent.Type.MouseButtonPress: 'mouse_press',
                QEvent.Type.MouseButtonRelease: 'mouse_release',
                QEvent.Type.MouseButtonDblClick: 'mouse_double_click',
                QEvent.Type.KeyPress: 'key_press',
                QEvent.Type.Wheel: 'wheel',
            }
            if et in input_types:
                kind = input_types[et]
                self._input_counts[kind] += 1
                self._pending_input_at = time.perf_counter()
                self._pending_input_kind = kind

            elif et == QEvent.Type.Paint:
                now = time.perf_counter()
                cls_name = obj.__class__.__name__
                if self.main_window_show_ms is not None and self.main_window_first_paint_ms is None and cls_name == 'MainWindow':
                    self.main_window_first_paint_ms = (now - self.started_perf) * 1000.0
                if self._pending_input_at is not None:
                    latency = (now - self._pending_input_at) * 1000.0
                    if 0.0 <= latency <= 2000.0:
                        self._input_to_paint_ms.append(latency)
                    self._pending_input_at = None
                    self._pending_input_kind = ''

            elif et == QEvent.Type.Show:
                cls_name = obj.__class__.__name__
                row = {
                    'class': cls_name,
                    'object_name': getattr(obj, 'objectName', lambda: '')() or '',
                    'at_ms': round((time.perf_counter() - self.started_perf) * 1000.0, 3),
                }
                if len(self._window_shows) < 500:
                    self._window_shows.append(row)
                if cls_name == 'MainWindow' and self.main_window_show_ms is None:
                    self.main_window_show_ms = row['at_ms']
        except Exception:
            pass
        return False

    def snapshot(self) -> dict:
        now = time.perf_counter()
        delays = self._timer_delays_ms
        lat = self._input_to_paint_ms
        duration = max(0.001, now - self.started_perf)
        return {
            'schema': 1,
            'started_at_unix': self.started_wall,
            'captured_at_unix': time.time(),
            'duration_s': round(duration, 3),
            'heartbeat_interval_ms': self.interval_ms,
            'startup': {
                'main_window_show_ms': round(self.main_window_show_ms, 3) if self.main_window_show_ms is not None else None,
                'main_window_first_paint_ms': round(self.main_window_first_paint_ms, 3) if self.main_window_first_paint_ms is not None else None,
            },
            'event_loop': {
                'samples': len(delays),
                'delay_p50_ms': round(_pct(delays, 0.50), 3),
                'delay_p95_ms': round(_pct(delays, 0.95), 3),
                'delay_p99_ms': round(_pct(delays, 0.99), 3),
                'max_delay_ms': round(max(delays), 3) if delays else 0.0,
                'over_100_ms': sum(1 for x in delays if x >= 100),
                'over_250_ms': sum(1 for x in delays if x >= 250),
                'over_500_ms': sum(1 for x in delays if x >= 500),
                'over_1000_ms': sum(1 for x in delays if x >= 1000),
                'stall_events': self._stall_events[-300:],
            },
            'interaction': {
                'input_events': int(sum(self._input_counts.values())),
                'input_counts': dict(self._input_counts),
                'input_to_paint_samples': len(lat),
                'response_p50_ms': round(_pct(lat, 0.50), 3),
                'response_p95_ms': round(_pct(lat, 0.95), 3),
                'response_p99_ms': round(_pct(lat, 0.99), 3),
                'response_max_ms': round(max(lat), 3) if lat else 0.0,
            },
            'window_shows': self._window_shows[-200:],
        }

    def write_report(self) -> None:
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.output_path.with_suffix(self.output_path.suffix + '.tmp')
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.output_path)
        except Exception:
            pass


def install_ui_perf_probe(app):
    if os.getenv('CHROMATIC_UI_PERF_PROBE', '').strip() not in {'1', 'true', 'TRUE', 'yes', 'YES'}:
        return None
    output = os.getenv('CHROMATIC_UI_PERF_REPORT', '').strip()
    if not output:
        output = str(Path.cwd() / 'performance_reports' / 'ui_perf_probe.json')
    interval = int(os.getenv('CHROMATIC_UI_PERF_INTERVAL_MS', '50') or '50')
    return UiPerformanceProbe(app, output, interval_ms=interval)
