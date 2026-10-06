from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from qtx_core import parse_qtx_file
from qtx_app.main_window import ColorCardPlanWindow

REPORT_DIR=ROOT/'performance_reports'


def fingerprint(sample):
    h=hashlib.sha1(); h.update(b'Munsell-C-2-v1|')
    for w,r in zip(sample.wavelengths or (),sample.reflectance or ()):
        h.update(f'{float(w):.6f}:{float(r):.10f};'.encode('ascii'))
    return h.hexdigest()


def cache_path():
    appdata=os.environ.get('APPDATA')
    if not appdata:return None
    return Path(appdata)/'QTX 色彩分析'/'cache'/'munsell_c2_v1.json'


def load_cache():
    p=cache_path(); raw={}
    if p and p.exists():
        try:
            obj=json.loads(p.read_text(encoding='utf-8'))
            if isinstance(obj,dict):raw=obj
        except Exception:pass
    return p,raw


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('qtx'); args=ap.parse_args(argv)
    path=Path(args.qtx)
    if not path.exists():raise SystemExit(f'找不到文件: {path}')
    REPORT_DIR.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    print('Chromatic Analysis · Munsell 生产路径 / 持久缓存专项测试 R1A')
    print('='*72)
    print(f'文件: {path}')
    t0=perf_counter(); samples=list(parse_qtx_file(path)); parse_s=perf_counter()-t0
    samples=[s for s in samples if s.has_spectrum()]
    p,cache=load_cache()
    fps=[fingerprint(s) for s in samples]
    unique=len(set(fps)); duplicates=len(fps)-unique
    hits=sum(1 for fp in fps if isinstance(cache.get(fp),list) and len(cache.get(fp))>=5)
    misses=len(fps)-hits
    print(f'解析: {len(samples)} 完整光谱 · {parse_s:.3f}s')
    print(f'持久缓存: {p if p else "APPDATA不可用"}')
    print(f'缓存文件条目: {len(cache)}；本QTX命中: {hits}/{len(samples)}；未命中: {misses}；重复光谱: {duplicates}')

    payload=[]
    for i,s in enumerate(samples):
        fp=fps[i]; raw=cache.get(fp); cached=tuple(raw[:5]) if isinstance(raw,list) and len(raw)>=5 else None
        payload.append((f'{i}:{s.display_name}',s.display_name,s.lab_d65_10,s,cached))

    print('[1/3] 当前真实缓存状态下，执行 HF88 生产 Munsell 排序核心...',flush=True)
    t0=perf_counter(); result=ColorCardPlanWindow._compute_palette_heavy_sort('h',payload,False); actual_s=perf_counter()-t0
    print(f"    {actual_s:.3f}s · worker报告 cache_hits={result.get('cache_hits',0)} cache_misses={result.get('cache_misses',0)}")

    # Use just-computed info as 100% cache to isolate sort/application-core overhead.
    info=result.get('hue_info') or {}
    full_cached=[]
    for row in payload:
        key,name,lab,sample,_=row
        ci=info.get(key)
        full_cached.append((key,name,lab,sample,ci))
    print('[2/3] 模拟100% Munsell结果缓存命中，仅测试生产排序核心...',flush=True)
    t0=perf_counter(); result2=ColorCardPlanWindow._compute_palette_heavy_sort('h',full_cached,False); hot_s=perf_counter()-t0
    print(f'    {hot_s:.6f}s')

    # Diagnose disk fingerprint stability by re-reading the same file and comparing.
    print('[3/3] 重新解析同一QTX，检查光谱指纹是否稳定...',flush=True)
    t0=perf_counter(); samples2=[s for s in parse_qtx_file(path) if s.has_spectrum()]; reparse_s=perf_counter()-t0
    fps2=[fingerprint(s) for s in samples2]
    stable=(fps==fps2)
    stable_hits=sum(1 for fp in fps2 if isinstance(cache.get(fp),list) and len(cache.get(fp))>=5)
    print(f'    重解析 {reparse_s:.3f}s · 指纹完全一致={stable} · 重新解析后缓存命中={stable_hits}/{len(fps2)}')

    miss_cost=(actual_s/hits if hits and not misses else (actual_s/misses if misses else 0.0))
    diagnosis=[]
    if misses==0 and actual_s>1.0:
        diagnosis.append('异常：本QTX已100%命中持久缓存，但生产核心仍明显耗时；需继续检查UI/缓存读取路径。')
    elif misses>0:
        diagnosis.append(f'当前慢主要来自 {misses} 个未命中光谱需要首次精确Munsell计算。')
    if hot_s>0.5:
        diagnosis.append('异常：100%缓存情况下核心仍超过0.5s。')
    else:
        diagnosis.append('100%缓存核心耗时很低，说明真正瓶颈仍是首次renotation，而不是排序本身。')
    if not stable:
        diagnosis.append('异常：同一QTX重解析后的光谱指纹不稳定，持久缓存无法可靠复用。')
    if p is None or not p.exists():
        diagnosis.append('持久缓存文件不存在：若此前已完整执行过Munsell，这是需要修复的问题。')

    report={
        'file':str(path),'created_at':datetime.now().isoformat(timespec='seconds'),'platform':platform.platform(),'python':sys.version.split()[0],
        'sample_count':len(samples),'unique_spectra':unique,'duplicate_spectra':duplicates,'parse_s':parse_s,
        'cache_path':str(p) if p else None,'cache_file_exists':bool(p and p.exists()),'cache_entries':len(cache),
        'persistent_hits':hits,'persistent_misses':misses,'production_actual_s':actual_s,'production_hot_100pct_cache_s':hot_s,
        'worker_cache_hits':result.get('cache_hits',0),'worker_cache_misses':result.get('cache_misses',0),
        'reparse_s':reparse_s,'fingerprint_stable':stable,'reparse_cache_hits':stable_hits,'diagnosis':diagnosis,
    }
    js=REPORT_DIR/f'MUNSELL_PRODUCTION_BENCHMARK_{stamp}.json'; txt=REPORT_DIR/f'MUNSELL_PRODUCTION_BENCHMARK_{stamp}.txt'
    js.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['Chromatic Analysis · Munsell 生产路径 / 持久缓存专项测试 R1A','='*72,
           f'文件: {path}',f'色样(完整光谱): {len(samples)}',f'唯一光谱: {unique}',f'重复光谱: {duplicates}',f'QTX解析: {parse_s:.3f}s','',
           f'缓存路径: {p}',f'缓存文件存在: {bool(p and p.exists())}',f'缓存总条目: {len(cache)}',f'本QTX缓存命中: {hits}',f'本QTX未命中: {misses}','',
           f'HF88生产核心（真实缓存状态）: {actual_s:.3f}s',f'worker cache_hits: {result.get("cache_hits",0)}',f'worker cache_misses: {result.get("cache_misses",0)}',
           f'100%缓存核心: {hot_s:.6f}s','',f'同QTX重解析: {reparse_s:.3f}s',f'光谱指纹完全一致: {stable}',f'重解析后缓存命中: {stable_hits}/{len(fps2)}','',
           '诊断结论:']
    lines += [f'  - {x}' for x in diagnosis]
    lines += ['',f'JSON: {js}','请把本 TXT 完整内容发给 ChatGPT。']
    txt.write_text('\n'.join(lines),encoding='utf-8')
    print('\n诊断结论:')
    for x in diagnosis:print('  - '+x)
    print(f'\n已生成:\n{txt}\n{js}')
    print('请把 TXT 完整内容发给 ChatGPT。')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
