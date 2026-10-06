from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qtx_core import (
    parse_qtx_file,
    reflectance_to_xyz_lab,
    munsell_hue_order_from_xyz,
    spectral_order,
)
from qtx_core.colorimetry import clear_science_cache, science_cache_info


def elapsed(fn):
    t0 = perf_counter()
    result = fn()
    return result, perf_counter() - t0


def fmt_s(v: float) -> str:
    return f"{v:.3f} s"


def stats(vals):
    vals = list(vals)
    if not vals:
        return {"count": 0, "mean_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(vals)
    p95 = ordered[min(len(ordered)-1, int(round((len(ordered)-1)*0.95)))]
    return {
        "count": len(vals),
        "mean_ms": round(statistics.fmean(vals)*1000.0, 4),
        "p95_ms": round(p95*1000.0, 4),
        "max_ms": round(max(vals)*1000.0, 4),
    }


def munsell_exact_pass(samples, progress=True):
    """Reproduce HF67 exact Munsell CPU path, while timing its two expensive stages."""
    infos = {}
    xyz_times = []
    renotation_times = []
    chromatic = []
    neutral = []
    fallback = 0
    n = len(samples)

    for i, sample in enumerate(samples, 1):
        key = f"{getattr(sample, 'display_name', '')}::{i}"
        L, a, b = (float(x) for x in sample.lab_d65_10)
        C = math.hypot(a, b)
        if C < 3.0:
            info = (True, float('inf'), 'Munsell 中性色 / 低彩度', L, C)
        else:
            try:
                t0 = perf_counter()
                xyz = reflectance_to_xyz_lab(sample.reflectance, 'C', sample.wavelengths, 2)[0]
                xyz_times.append(perf_counter() - t0)

                t0 = perf_counter()
                idx, notation, value, chroma = munsell_hue_order_from_xyz(xyz)
                renotation_times.append(perf_counter() - t0)
                is_neutral = (not math.isfinite(idx)) or chroma < 1.5
                info = (is_neutral, idx, f'Munsell {notation}', value, chroma)
            except Exception:
                fallback += 1
                h = math.degrees(math.atan2(b, a)) % 360.0
                info = (False, h/3.6, f'Munsell 回退 / Lab h° {h:.1f}', L, C)
        infos[key] = info
        (neutral if info[0] else chromatic).append((info, key, getattr(sample, 'display_name', '')))
        if progress and (i % 25 == 0 or i == n):
            print(f"    Munsell: {i}/{n}", flush=True)

    def ck(row):
        info, key, name = row
        idx = float(info[1])
        family = int(idx // 10) if math.isfinite(idx) else 99
        pos = idx % 10 if math.isfinite(idx) else 0.0
        return (family, pos, -float(info[3]), -float(info[4]), str(name).casefold())

    t_sort = perf_counter()
    chromatic.sort(key=ck)
    neutral.sort(key=lambda row: (-float(row[0][3]), -float(row[0][4]), str(row[2]).casefold()))
    sort_only = perf_counter() - t_sort

    return {
        'info': infos,
        'xyz': stats(xyz_times),
        'renotation': stats(renotation_times),
        'sort_only_s': sort_only,
        'fallback': fallback,
        'neutral': len(neutral),
        'chromatic': len(chromatic),
        'keys': [row[1] for row in chromatic] + [row[1] for row in neutral],
    }


def cached_munsell_sort(infos):
    chromatic = []
    neutral = []
    for key, info in infos.items():
        (neutral if info[0] else chromatic).append((info, key))

    def ck(row):
        info, key = row
        idx = float(info[1])
        family = int(idx // 10) if math.isfinite(idx) else 99
        pos = idx % 10 if math.isfinite(idx) else 0.0
        return (family, pos, -float(info[3]), -float(info[4]), key.casefold())

    chromatic.sort(key=ck)
    neutral.sort(key=lambda row: (-float(row[0][3]), -float(row[0][4]), row[1].casefold()))
    return [k for _, k in chromatic] + [k for _, k in neutral]


def main() -> int:
    ap = argparse.ArgumentParser(description='Chromatic Analysis HF67 palette-sort diagnostic benchmark')
    ap.add_argument('qtx', type=Path, help='QTX file used by the palette plan')
    ap.add_argument('--limit', type=int, default=0, help='test only first N samples; 0 = all')
    args = ap.parse_args()

    path = args.qtx.expanduser().resolve()
    if not path.exists():
        print(f"文件不存在: {path}")
        return 2

    print('Chromatic Analysis · 色卡排序专项性能测试 R1')
    print('=' * 60)
    print(f'文件: {path}')
    print(f'Windows: {platform.platform()}')
    print(f'Python: {platform.python_version()}')
    print(f'CPU: {platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown")}')
    print()

    report = {
        'schema': 1,
        'build': 'HF67-SORT-DIAGNOSTIC-R1',
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'input': str(path),
        'platform': platform.platform(),
        'python': platform.python_version(),
        'processor': platform.processor() or os.environ.get('PROCESSOR_IDENTIFIER', 'unknown'),
    }

    print('[1/7] 解析 QTX...', flush=True)
    samples, parse_s = elapsed(lambda: parse_qtx_file(path))
    if args.limit > 0:
        samples = samples[:args.limit]
    spectral = [s for s in samples if s.has_spectrum()]
    report['sample_count'] = len(samples)
    report['spectral_count'] = len(spectral)
    report['parse_s'] = parse_s
    print(f'    {len(samples)} 个色样；其中 {len(spectral)} 个有完整光谱；解析 {fmt_s(parse_s)}')

    if not samples:
        print('没有可测试色样。')
        return 3

    print('[2/7] 普通排序基线（名称/L*/a*/b*/C*）...', flush=True)
    labs = [tuple(float(x) for x in s.lab_d65_10) for s in samples]
    simple = {}
    tests = {
        'name': lambda: sorted(samples, key=lambda s: s.display_name.casefold()),
        'L': lambda: sorted(range(len(samples)), key=lambda i: labs[i][0], reverse=True),
        'a': lambda: sorted(range(len(samples)), key=lambda i: labs[i][1], reverse=True),
        'b': lambda: sorted(range(len(samples)), key=lambda i: labs[i][2], reverse=True),
        'C': lambda: sorted(range(len(samples)), key=lambda i: math.hypot(labs[i][1], labs[i][2]), reverse=True),
    }
    for name, fn in tests.items():
        _, sec = elapsed(fn)
        simple[name] = sec
    report['simple_sort_s'] = simple
    print('    ' + ' / '.join(f'{k}={v*1000:.2f}ms' for k,v in simple.items()))

    print('[3/7] 精确 Munsell 感知色相：第一次（冷缓存）...', flush=True)
    clear_science_cache()
    m1, m1_s = elapsed(lambda: munsell_exact_pass(samples, progress=True))
    report['munsell_cold_s'] = m1_s
    report['munsell_cold'] = {k:v for k,v in m1.items() if k not in {'info','keys'}}
    report['science_cache_after_m1'] = science_cache_info()
    print(f'    总计 {fmt_s(m1_s)}')
    print(f"    C/2° 光谱→XYZ 平均 {m1['xyz']['mean_ms']:.3f}ms/色样")
    print(f"    Munsell renotation 平均 {m1['renotation']['mean_ms']:.3f}ms/色样")

    print('[4/7] 精确 Munsell 感知色相：第二次（科学计算缓存已热）...', flush=True)
    m2, m2_s = elapsed(lambda: munsell_exact_pass(samples, progress=True))
    report['munsell_warm_s'] = m2_s
    report['munsell_warm'] = {k:v for k,v in m2.items() if k not in {'info','keys'}}
    print(f'    总计 {fmt_s(m2_s)}')

    print('[5/7] 已有 Munsell 结果仅重新排序（模拟结果缓存复用）...', flush=True)
    _, mc_s = elapsed(lambda: cached_munsell_sort(m1['info']))
    report['munsell_cached_sort_s'] = mc_s
    print(f'    {fmt_s(mc_s)}')

    if len(spectral) >= 2:
        print('[6/7] 当前光谱相似度路径算法...', flush=True)
        _, sp_s = elapsed(lambda: spectral_order(spectral, hybrid=False))
        report['spectral_sort_s'] = sp_s
        print(f'    {fmt_s(sp_s)}')

        print('[7/7] 当前实验性光谱 + 感知路径算法...', flush=True)
        _, hy_s = elapsed(lambda: spectral_order(spectral, hybrid=True))
        report['spectral_hybrid_sort_s'] = hy_s
        print(f'    {fmt_s(hy_s)}')
    else:
        print('[6/7] 光谱不足，跳过光谱排列。')
        print('[7/7] 光谱不足，跳过光谱+感知排列。')

    out = ROOT / 'performance_reports'
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_path = out / f'PALETTE_SORT_R1_{stamp}.json'
    txt_path = out / f'PALETTE_SORT_R1_{stamp}.txt'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    lines = [
        'Chromatic Analysis · 色卡排序专项性能测试 R1',
        '='*60,
        f'文件: {path}',
        f'色样: {len(samples)}；完整光谱: {len(spectral)}',
        f'QTX解析: {parse_s:.3f} s',
        f"普通名称排序: {simple['name']*1000:.2f} ms",
        f"普通L*排序: {simple['L']*1000:.2f} ms",
        f'Munsell第一次: {m1_s:.3f} s',
        f'Munsell第二次: {m2_s:.3f} s',
        f'Munsell已有结果重排: {mc_s*1000:.2f} ms',
    ]
    if 'spectral_sort_s' in report:
        lines += [
            f"光谱排列: {report['spectral_sort_s']:.3f} s",
            f"光谱+感知排列: {report['spectral_hybrid_sort_s']:.3f} s",
        ]
    lines += [
        '',
        'Munsell分项:',
        f"  C/2° 光谱→XYZ: mean={m1['xyz']['mean_ms']:.3f} ms, p95={m1['xyz']['p95_ms']:.3f} ms",
        f"  Munsell renotation: mean={m1['renotation']['mean_ms']:.3f} ms, p95={m1['renotation']['p95_ms']:.3f} ms",
        f"  fallback={m1['fallback']}, neutral={m1['neutral']}, chromatic={m1['chromatic']}",
        '',
        '请把本 TXT 完整内容发给 ChatGPT。',
    ]
    txt_path.write_text('\n'.join(lines), encoding='utf-8')

    print('\n' + '='*60)
    print('\n'.join(lines[3:]))
    print(f'\n报告已保存:\n{txt_path}\n{json_path}')
    print('\n请把 TXT 报告直接拖给我，或者复制里面的全部内容。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
