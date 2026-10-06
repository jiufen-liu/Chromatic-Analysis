from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import statistics
import sys
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qtx_core import (
    analyse_pair,
    parse_qtx_text,
    reflectances_to_xyz_lab,
    spectral_order,
)
from qtx_core.qtx_parser import qtx_parse_diagnostics

# Historical user-machine reference from Performance P3-6 / Coloro 3500.
# It is only shown when the current file is also a 3500-sample Coloro file.
P3_6_COLORO_PARSE_MS_PER_1000 = 671.73


def ms(sec: float) -> float:
    return round(sec * 1000.0, 3)


def timed(fn):
    t0 = perf_counter()
    result = fn()
    return result, perf_counter() - t0


def p95(values):
    vals = sorted(values)
    if not vals:
        return 0.0
    return vals[min(len(vals) - 1, int(round((len(vals) - 1) * 0.95)))]


@lru_cache(maxsize=4096)
def canonical_source(source_file: str) -> str:
    try:
        return str(Path(source_file).resolve())
    except Exception:
        return str(source_file)


def serialize_sample(s):
    # Mirrors MainWindow._serialize_sample without importing Qt/UI modules.
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


def palette_prepare(samples):
    """Model the non-visual part of a full palette import.

    HF122 mirrors the runtime fix: canonicalise each *source file* once, not once
    per sample.  The result also exposes phase timings so a future regression can
    be assigned to key generation, snapshot serialisation, or slot placement.
    """
    started = perf_counter()
    source_cache = {str(s.source_file): canonical_source(str(s.source_file)) for s in samples}

    totals = {}
    for s in samples:
        base = (source_cache[str(s.source_file)], str(s.sample_id))
        totals[base] = totals.get(base, 0) + 1

    counts = {}
    keys = []
    for s in samples:
        base = (source_cache[str(s.source_file)], str(s.sample_id))
        counts[base] = counts.get(base, 0) + 1
        occurrence = counts[base] if totals[base] > 1 else 1
        suffix = f"~@{occurrence}" if occurrence > 1 else ""
        keys.append(f"{base[0]}|{s.sample_id}{suffix}")
    keys_done = perf_counter()

    payload = [serialize_sample(s) for s in samples]
    serial_done = perf_counter()

    # HF121 linear placement path: one set pass, then append.
    slots = []
    existing = set()
    for key in keys:
        if key in existing:
            continue
        slots.append(key)
        existing.add(key)
    place_done = perf_counter()

    return payload, slots, {
        "key_ms": ms(keys_done - started),
        "serialize_ms": ms(serial_done - keys_done),
        "placement_ms": ms(place_done - serial_done),
        "total_ms": ms(place_done - started),
    }


def simple_sorts(samples):
    labs = [tuple(float(x) for x in s.lab_d65_10) for s in samples]
    timings = {}
    tests = {
        "name": lambda: sorted(range(len(samples)), key=lambda i: samples[i].display_name.casefold()),
        "L": lambda: sorted(range(len(samples)), key=lambda i: labs[i][0], reverse=True),
        "a": lambda: sorted(range(len(samples)), key=lambda i: labs[i][1], reverse=True),
        "b": lambda: sorted(range(len(samples)), key=lambda i: labs[i][2], reverse=True),
        "C": lambda: sorted(range(len(samples)), key=lambda i: math.hypot(labs[i][1], labs[i][2]), reverse=True),
        "h": lambda: sorted(range(len(samples)), key=lambda i: math.degrees(math.atan2(labs[i][2], labs[i][1])) % 360.0),
    }
    for name, fn in tests.items():
        _, sec = timed(fn)
        timings[name] = ms(sec)
    return timings


def process_memory_info() -> dict[str, float]:
    """Read process memory without enabling tracemalloc during timed sections."""
    try:
        if sys.platform.startswith("win"):
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if ok:
                return {
                    "working_set_mib": round(counters.WorkingSetSize / 1024.0 / 1024.0, 2),
                    "peak_working_set_mib": round(counters.PeakWorkingSetSize / 1024.0 / 1024.0, 2),
                }
        else:
            import resource

            peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # Linux reports KiB; macOS reports bytes.
            peak_mib = peak / (1024.0 if sys.platform.startswith("linux") else 1024.0 * 1024.0)
            return {"peak_working_set_mib": round(peak_mib, 2)}
    except Exception:
        pass
    return {}


def grade(report):
    n = max(1, report.get("sample_count", 1))
    core_per_1000 = report.get("qtx_core_parse_ms_per_1000", 0.0)
    palette_per_1000 = report.get("palette_prepare_ms_per_1000", 0.0)
    issues = []
    notes = []

    # Release targets are deliberately stricter than HF121's first-pass alarms.
    if core_per_1000 > 1000:
        issues.append(f"QTX核心解析偏慢 ({core_per_1000:.0f} ms/1000色)")
    if palette_per_1000 > 800:
        issues.append(f"色卡导入准备偏慢 ({palette_per_1000:.0f} ms/1000色)")
    if report.get("pair_analysis_p95_ms", 0) > 60:
        issues.append(f"单对比计算偏慢 (P95 {report['pair_analysis_p95_ms']:.1f} ms)")
    if report.get("spectral_sort_ms", 0) > 3000:
        issues.append(f"光谱排序偏慢 ({report['spectral_sort_ms']:.0f} ms/{report.get('spectral_sort_count',0)}色)")
    if report.get("spectral_perceptual_sort_ms", 0) > 5000:
        issues.append(f"光谱+感知排序偏慢 ({report['spectral_perceptual_sort_ms']:.0f} ms/{report.get('spectral_sort_count',0)}色)")

    if report.get("file_read_ms", 0) > 2500:
        notes.append("源文件读取较慢；若文件位于网络共享，请把该项与程序CPU性能分开判断")

    if not issues:
        return "核心性能正常", issues, notes
    if len(issues) <= 2:
        return "存在需要关注的性能点", issues, notes
    return "存在明显性能瓶颈", issues, notes


def run(path: Path) -> dict:
    path = path.expanduser().resolve()
    report = {
        "schema": 2,
        "build": "HF122-PERFORMANCE-FINAL",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(path),
        "file_size_bytes": path.stat().st_size,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
    }

    # Important: I/O and parser CPU are timed separately.  Do not enable
    # tracemalloc here: allocation tracing materially distorts the very timings
    # this suite is intended to compare with the P3-6 baseline.
    text, read_s = timed(lambda: path.read_text(encoding="utf-8-sig", errors="ignore"))
    parsed, core_parse_s = timed(lambda: parse_qtx_text(text, path.name))
    parse_diag = qtx_parse_diagnostics()
    canonical = str(path)
    samples, bind_s = timed(lambda: [replace(s, source_file=canonical) for s in parsed])

    report["sample_count"] = len(samples)
    report["spectral_count"] = sum(1 for s in samples if s.has_spectrum())
    report["file_read_ms"] = ms(read_s)
    report["qtx_core_parse_ms"] = ms(core_parse_s)
    report["source_bind_ms"] = ms(bind_s)
    report["parse_ms"] = ms(read_s + core_parse_s + bind_s)
    report["parse_ms_per_1000"] = round(report["parse_ms"] * 1000.0 / max(1, len(samples)), 2)
    report["qtx_core_parse_ms_per_1000"] = round(report["qtx_core_parse_ms"] * 1000.0 / max(1, len(samples)), 2)
    report["qtx_parser"] = parse_diag

    (_, _, prep_parts), prep_s = timed(lambda: palette_prepare(samples))
    report["palette_prepare_ms"] = ms(prep_s)
    report["palette_prepare_ms_per_1000"] = round(report["palette_prepare_ms"] * 1000.0 / max(1, len(samples)), 2)
    report["palette_prepare_breakdown"] = prep_parts

    report["simple_sort_ms"] = simple_sorts(samples)

    spectral = [s for s in samples if s.has_spectrum()]
    if spectral:
        subset = spectral[: min(1000, len(spectral))]
        refl = [s.reflectance for s in subset]
        waves = subset[0].wavelengths
        _, d65_s = timed(lambda: reflectances_to_xyz_lab(refl, "D65", waves, 10))
        _, f11_s = timed(lambda: reflectances_to_xyz_lab(refl, "F11 / TL84", waves, 10))
        report["batch_colour_compute"] = {
            "count": len(subset),
            "D65_10_ms": ms(d65_s),
            "F11_10_ms": ms(f11_s),
        }

    if len(spectral) >= 2:
        pair_times = []
        standard = spectral[0]
        for batch in spectral[1 : min(len(spectral), 101)]:
            t0 = perf_counter()
            analyse_pair(standard, batch, "D65", reference_illuminant="D65", observer_degrees=10)
            pair_times.append((perf_counter() - t0) * 1000.0)
        report["pair_analysis_mean_ms"] = round(statistics.fmean(pair_times), 4) if pair_times else 0.0
        report["pair_analysis_p95_ms"] = round(p95(pair_times), 4)
        report["pair_analysis_max_ms"] = round(max(pair_times), 4) if pair_times else 0.0

        # Keep unattended validation bounded while still stressing the N² path.
        sort_subset = spectral[: min(1000, len(spectral))]
        _, sp_s = timed(lambda: spectral_order(sort_subset, hybrid=False))
        report["spectral_sort_count"] = len(sort_subset)
        report["spectral_sort_ms"] = ms(sp_s)
        _, hy_s = timed(lambda: spectral_order(sort_subset, hybrid=True))
        report["spectral_perceptual_sort_ms"] = ms(hy_s)

    mem = process_memory_info()
    if mem:
        report["memory"] = mem

    # Only compare against the historical Coloro 3500 P3-6 record when the
    # current input really is the same class of reference file.
    if len(samples) == 3500 and "coloro" in path.name.casefold():
        ratio = report["qtx_core_parse_ms_per_1000"] / P3_6_COLORO_PARSE_MS_PER_1000
        report["historical_reference"] = {
            "name": "P3-6 Coloro 3500",
            "parse_ms_per_1000": P3_6_COLORO_PARSE_MS_PER_1000,
            "current_ratio": round(ratio, 3),
            "change_percent": round((ratio - 1.0) * 100.0, 1),
            "status": "PASS" if ratio <= 1.25 else "WARN" if ratio <= 1.75 else "FAIL",
        }

    verdict, issues, notes = grade(report)
    report["verdict"] = verdict
    report["issues"] = issues
    report["notes"] = notes
    return report


def render(report: dict) -> str:
    lines = [
        "Chromatic Analysis · 一键自动核心性能验证 · HF122 Performance Final",
        "=" * 76,
        f"文件: {report.get('input','')}",
        f"色样: {report.get('sample_count',0)}；完整光谱: {report.get('spectral_count',0)}",
        "",
        "QTX输入 / 解析:",
        f"  文件读取: {report.get('file_read_ms',0):.1f} ms",
        f"  核心解析: {report.get('qtx_core_parse_ms',0):.1f} ms  ({report.get('qtx_core_parse_ms_per_1000',0):.1f} ms/1000色)",
        f"  绑定规范源路径: {report.get('source_bind_ms',0):.1f} ms",
        f"  输入+解析总计: {report.get('parse_ms',0):.1f} ms",
    ]
    parser_diag = report.get("qtx_parser") or {}
    if parser_diag:
        lines += [
            f"  ├─ QTX块解析: {float(parser_diag.get('block_parse_ms',0) or 0):.1f} ms",
            f"  ├─ 样本准备: {float(parser_diag.get('sample_prepare_ms',0) or 0):.1f} ms",
            f"  └─ 色彩科学+Sample构建: {float(parser_diag.get('science_ms',0) or 0):.1f} ms",
            f"     batch {parser_diag.get('batch_samples',0)} 色 / {parser_diag.get('batch_groups',0)} 组；scalar {parser_diag.get('scalar_samples',0)} 色；fallback {parser_diag.get('fallback_groups',0)}",
        ]
    ref = report.get("historical_reference") or {}
    if ref:
        lines += [
            f"  P3-6历史参考: {ref.get('parse_ms_per_1000',0):.1f} ms/1000色",
            f"  当前相对参考: {ref.get('current_ratio',0):.2f}× ({ref.get('change_percent',0):+.1f}%) · {ref.get('status','')}",
        ]

    prep = report.get("palette_prepare_breakdown") or {}
    lines += [
        "",
        f"色卡导入准备: {report.get('palette_prepare_ms',0):.1f} ms  ({report.get('palette_prepare_ms_per_1000',0):.1f} ms/1000色)",
        f"  ├─ 唯一键/路径: {prep.get('key_ms',0):.1f} ms",
        f"  ├─ 完整测量快照: {prep.get('serialize_ms',0):.1f} ms",
        f"  └─ 版位准备: {prep.get('placement_ms',0):.1f} ms",
    ]
    mem = report.get("memory") or {}
    if mem:
        lines.append(
            f"进程内存: 当前 {mem.get('working_set_mib',0):.1f} MiB · 峰值 {mem.get('peak_working_set_mib',0):.1f} MiB"
            if "working_set_mib" in mem else f"进程峰值内存: {mem.get('peak_working_set_mib',0):.1f} MiB"
        )

    lines += ["", "普通排序:"]
    for k, v in (report.get("simple_sort_ms") or {}).items():
        lines.append(f"  {k}: {v:.2f} ms")
    bc = report.get("batch_colour_compute") or {}
    if bc:
        lines += [
            "",
            f"批量颜色计算 ({bc.get('count',0)}色):",
            f"  D65 / 10°: {bc.get('D65_10_ms',0):.2f} ms",
            f"  F11 / 10°: {bc.get('F11_10_ms',0):.2f} ms",
        ]
    if "pair_analysis_p95_ms" in report:
        lines += [
            "",
            "单样比色计算（自动抽样最多100对）:",
            f"  mean: {report.get('pair_analysis_mean_ms',0):.3f} ms",
            f"  P95:  {report.get('pair_analysis_p95_ms',0):.3f} ms",
            f"  max:  {report.get('pair_analysis_max_ms',0):.3f} ms",
        ]
    if "spectral_sort_ms" in report:
        lines += [
            "",
            f"排序自动抽样 ({report.get('spectral_sort_count',0)}色):",
            f"  光谱: {report.get('spectral_sort_ms',0):.1f} ms",
            f"  光谱+感知: {report.get('spectral_perceptual_sort_ms',0):.1f} ms",
        ]

    lines += ["", f"综合结果: {report.get('verdict','')}"]
    issues = report.get("issues") or []
    if issues:
        lines.append("需要关注:")
        lines.extend(f"  - {x}" for x in issues)
    notes = report.get("notes") or []
    if notes:
        lines.append("提示:")
        lines.extend(f"  - {x}" for x in notes)
    lines += [
        "",
        "说明：这是无人值守的核心性能回归，不要求逐个点击软件功能。",
        "HF122 已把网络/磁盘读取与程序核心解析分开计时；正式发布前再跑一次 [22] 即可。",
    ]
    return "\n".join(lines)


def ask_path() -> Path:
    raw = input("把 Coloro 3500 或其它代表性大 QTX 拖到这里，然后按 Enter:\n> ").strip()
    if raw.startswith("&"):
        raw = raw[1:].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'\"', "'"}:
        raw = raw[1:-1].strip()
    return Path(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description="Chromatic Analysis one-click automatic performance suite")
    ap.add_argument("qtx", nargs="?", type=Path)
    args = ap.parse_args()
    path = args.qtx or ask_path()
    if not path.exists():
        print(f"找不到文件: {path}")
        return 2
    out = ROOT / "performance_reports"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("\n开始自动测试。测试期间不需要操作软件，请等待完成……\n", flush=True)
    report = run(path)
    text = render(report)
    txt = out / f"AUTO_PERFORMANCE_{stamp}.txt"
    js = out / f"AUTO_PERFORMANCE_{stamp}.json"
    txt.write_text(text, encoding="utf-8")
    js.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    print(f"\n报告:\n{txt}\n{js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
