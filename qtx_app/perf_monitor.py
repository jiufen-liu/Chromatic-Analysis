from __future__ import annotations

import atexit
import json
import os
import platform
import sys
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from time import perf_counter, time

_LOCK = threading.Lock()
_DEFAULT_THRESHOLD_MS = float(os.getenv("CHROMATIC_PERF_THRESHOLD_MS", "5"))
_MAX_BYTES = 1_000_000
_STATS: dict[str, dict[str, float | int]] = {}
_SUMMARY_PATH: Path | None = None
_SUMMARY_REGISTERED = False
_STARTED_AT = time()


def _resolve_log_path(obj) -> Path | None:
    path = getattr(obj, "perf_log_path", None)
    if path:
        return Path(path)
    store = getattr(obj, "store", None)
    if store is not None and getattr(store, "perf_log_path", None):
        return Path(store.perf_log_path)
    main = getattr(obj, "main", None)
    if main is not None:
        store = getattr(main, "store", None)
        if store is not None and getattr(store, "perf_log_path", None):
            return Path(store.perf_log_path)
    return None


def _record(operation: str, elapsed_ms: float) -> None:
    """Aggregate hot-path timings with near-zero behavioural impact."""
    try:
        with _LOCK:
            row = _STATS.setdefault(
                operation,
                {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "slow_count": 0},
            )
            row["count"] = int(row["count"]) + 1
            row["total_ms"] = float(row["total_ms"]) + float(elapsed_ms)
            row["max_ms"] = max(float(row["max_ms"]), float(elapsed_ms))
            if elapsed_ms >= _DEFAULT_THRESHOLD_MS:
                row["slow_count"] = int(row["slow_count"]) + 1
    except Exception:
        # Diagnostics must never affect application behaviour.
        pass


def _append(path: Path | None, operation: str, elapsed_ms: float) -> None:
    _record(operation, elapsed_ms)
    if path is None or elapsed_ms < _DEFAULT_THRESHOLD_MS:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                backup = path.with_suffix(path.suffix + ".1")
                try:
                    backup.unlink(missing_ok=True)
                    path.replace(backup)
                except Exception:
                    pass
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{operation}\t{elapsed_ms:.2f} ms\n")
    except Exception:
        pass


def performance_snapshot() -> dict:
    """Return a stable aggregate performance snapshot for diagnostics/benchmarks."""
    with _LOCK:
        operations = {}
        for name, row in _STATS.items():
            count = int(row.get("count", 0))
            total = float(row.get("total_ms", 0.0))
            operations[name] = {
                "count": count,
                "total_ms": round(total, 3),
                "average_ms": round(total / count, 3) if count else 0.0,
                "max_ms": round(float(row.get("max_ms", 0.0)), 3),
                "slow_count": int(row.get("slow_count", 0)),
            }
    snapshot = {
        "schema": 1,
        "started_at_unix": _STARTED_AT,
        "captured_at_unix": time(),
        "threshold_ms": _DEFAULT_THRESHOLD_MS,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "operations": dict(sorted(operations.items())),
    }
    try:
        from qtx_core.colorimetry import science_cache_info
        snapshot["science_cache"] = science_cache_info()
    except Exception:
        pass
    return snapshot


def write_performance_summary(path: str | Path | None = None) -> Path | None:
    target = Path(path) if path else _SUMMARY_PATH
    if target is None:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(performance_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
        return target
    except Exception:
        return None


def configure_performance_summary(path: str | Path) -> None:
    """Configure one best-effort JSON summary written on normal process exit."""
    global _SUMMARY_PATH, _SUMMARY_REGISTERED
    _SUMMARY_PATH = Path(path)
    if not _SUMMARY_REGISTERED:
        atexit.register(write_performance_summary)
        _SUMMARY_REGISTERED = True


def reset_performance_stats() -> None:
    with _LOCK:
        _STATS.clear()


def profiled(operation: str):
    """Low-overhead timing decorator for user-visible hot paths.

    Every call contributes to the aggregate P3-5 summary. Only calls slower
    than CHROMATIC_PERF_THRESHOLD_MS (default 5 ms) are appended to the legacy
    human-readable performance.log file.
    """
    def decorate(func):
        @wraps(func)
        def wrapped(self, *args, **kwargs):
            started = perf_counter()
            try:
                return func(self, *args, **kwargs)
            finally:
                _append(_resolve_log_path(self), operation, (perf_counter() - started) * 1000.0)
        return wrapped
    return decorate


@contextmanager
def perf_scope(obj, operation: str):
    started = perf_counter()
    try:
        yield
    finally:
        _append(_resolve_log_path(obj), operation, (perf_counter() - started) * 1000.0)
