from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qtx_core import analyse_pair, parse_qtx_file, reflectance_to_xyz_lab
from qtx_core.colorimetry import clear_science_cache, science_cache_info
from qtx_core.qtx_parser import qtx_parse_diagnostics


def timed(fn, repeat: int = 1):
    values = []
    result = None
    for _ in range(max(1, int(repeat))):
        started = perf_counter()
        result = fn()
        values.append((perf_counter() - started) * 1000.0)
    return result, values


def stats(values):
    values = list(values)
    if not values:
        return {"count": 0, "mean_ms": 0.0, "median_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 4),
        "median_ms": round(statistics.median(values), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "max_ms": round(max(values), 4),
    }


def default_qtx() -> Path:
    candidates = [
        ROOT / "tests" / "data" / "Sun Way.qtx",
        ROOT / "tests" / "data" / "Col.9 FH0092.qtx",
    ]
    return next((x for x in candidates if x.exists()), candidates[0])


def run(path: Path, warm_repeat: int = 300) -> dict:
    path = path.resolve()
    report = {
        "schema": 1,
        "build": "Hotfix54 · Performance P3-6 · QTX Batch Parser",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(path),
        "file_size_bytes": path.stat().st_size,
    }

    samples, parse_times = timed(lambda: parse_qtx_file(path), 1)
    report["parse"] = stats(parse_times)
    report["sample_count"] = len(samples)
    report["qtx_parser"] = qtx_parse_diagnostics()
    if not samples:
        report["error"] = "No samples parsed."
        return report

    spectral = next((s for s in samples if s.has_spectrum()), None)
    if spectral is not None:
        clear_science_cache()
        _, cold = timed(
            lambda: reflectance_to_xyz_lab(
                spectral.reflectance, "D50", spectral.wavelengths, 10
            ),
            1,
        )
        _, warm = timed(
            lambda: reflectance_to_xyz_lab(
                spectral.reflectance, "D50", spectral.wavelengths, 10
            ),
            warm_repeat,
        )
        report["spectral_conversion"] = {
            "condition": "D50 / 10°",
            "cold": stats(cold),
            "warm": stats(warm),
            "cache": science_cache_info(),
            "speedup_vs_warm_mean": round(cold[0] / max(0.000001, statistics.fmean(warm)), 2),
        }

    if len(samples) >= 2 and samples[0].has_spectrum() and samples[1].has_spectrum():
        standard, batch = samples[0], samples[1]
        clear_science_cache()
        _, pair_cold = timed(
            lambda: analyse_pair(
                standard, batch, "F11 / TL84", reference_illuminant="D65", observer_degrees=10
            ),
            1,
        )
        _, pair_warm = timed(
            lambda: analyse_pair(
                standard, batch, "F11 / TL84", reference_illuminant="D65", observer_degrees=10
            ),
            min(120, warm_repeat),
        )
        report["pair_analysis"] = {
            "condition": "F11 / TL84 / 10°",
            "cold": stats(pair_cold),
            "warm": stats(pair_warm),
            "cache": science_cache_info(),
        }

    count = max(1, len(samples))
    report["normalized"] = {
        "parse_ms_per_1000_samples": round(parse_times[0] * 1000.0 / count, 2),
        "file_kib": round(path.stat().st_size / 1024.0, 1),
    }
    return report


def render_text(report: dict) -> str:
    lines = [
        "Chromatic Analysis · Performance P3-6 QTX Parser Baseline",
        "=" * 58,
        f"Input: {report.get('input')}",
        f"File: {report.get('file_size_bytes', 0) / 1024.0:.1f} KiB",
        f"Samples: {report.get('sample_count', 0)}",
    ]
    parse = report.get("parse", {})
    lines.append(f"QTX parse: {parse.get('mean_ms', 0):.2f} ms")
    norm = report.get("normalized", {})
    if norm:
        lines.append(f"Parse / 1000 samples: {norm.get('parse_ms_per_1000_samples', 0):.2f} ms")
    parser_diag = report.get("qtx_parser") or {}
    if parser_diag:
        lines += [
            "QTX parser fast path:",
            f"  block parse: {float(parser_diag.get('block_parse_ms', 0) or 0):.2f} ms",
            f"  sample prepare: {float(parser_diag.get('sample_prepare_ms', 0) or 0):.2f} ms",
            f"  colour science + sample build: {float(parser_diag.get('science_ms', 0) or 0):.2f} ms",
            f"  batch: {parser_diag.get('batch_samples', 0)} samples / {parser_diag.get('batch_groups', 0)} groups",
            f"  scalar: {parser_diag.get('scalar_samples', 0)} samples",
            f"  fallback groups: {parser_diag.get('fallback_groups', 0)}",
            f"  verification failures: {parser_diag.get('verification_failures', 0)}",
        ]
    spectral = report.get("spectral_conversion")
    if spectral:
        lines += [
            "",
            f"Spectral conversion ({spectral['condition']}):",
            f"  cold: {spectral['cold'].get('mean_ms', 0):.4f} ms",
            f"  warm mean: {spectral['warm'].get('mean_ms', 0):.4f} ms",
            f"  warm p95: {spectral['warm'].get('p95_ms', 0):.4f} ms",
            f"  speedup: {spectral.get('speedup_vs_warm_mean', 0):.2f}x",
            f"  cache: {spectral.get('cache')}",
        ]
    pair = report.get("pair_analysis")
    if pair:
        lines += [
            "",
            f"Pair analysis ({pair['condition']}):",
            f"  cold: {pair['cold'].get('mean_ms', 0):.4f} ms",
            f"  warm mean: {pair['warm'].get('mean_ms', 0):.4f} ms",
            f"  warm p95: {pair['warm'].get('p95_ms', 0):.4f} ms",
        ]
    lines += [
        "",
        "Note: absolute timings depend on CPU, storage and file content.",
        "Use the same QTX on the same PC when comparing future releases.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chromatic Analysis P3-6 QTX parser performance baseline")
    parser.add_argument("qtx", nargs="?", type=Path, default=default_qtx(), help="QTX file to benchmark")
    parser.add_argument("--repeat", type=int, default=300, help="warm compute repetitions")
    args = parser.parse_args()
    if not args.qtx.exists():
        print(f"File not found: {args.qtx}", file=sys.stderr)
        return 2

    report = run(args.qtx, max(20, args.repeat))
    out_dir = ROOT / "performance_reports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"P3_6_baseline_{stamp}.json"
    txt_path = out_dir / f"P3_6_baseline_{stamp}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = render_text(report)
    txt_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nJSON: {json_path}")
    print(f"TEXT: {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
