from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from qtx_core import parse_qtx_file, spectral_order, spectral_rms_distance, delta_e

REPORT_DIR=ROOT/'diagnostics_reports'


def _legacy_hybrid(samples):
    """Historical spectral + 0.18*CIEDE2000 path, without HF88 warm-earth guard."""
    items=list(samples)
    spectral=[s for s in items if s.has_spectrum()]
    missing=[s for s in items if not s.has_spectrum()]
    if len(spectral)<=1:
        return spectral+missing

    def hue_key(s):
        L,a,b=(float(x) for x in s.lab_d65_10)
        c=math.hypot(a,b); h=math.degrees(math.atan2(b,a))%360.0
        return (h,L,c,s.display_name.casefold())

    remaining=sorted(spectral,key=hue_key)
    ordered=[remaining.pop(0)]
    while remaining:
        prev=ordered[-1]
        def score(s):
            try: spec=spectral_rms_distance(prev,s)
            except Exception: spec=1e9
            try: de=delta_e(prev.lab_d65_10,s.lab_d65_10,'CIE 2000')
            except Exception: de=0.0
            return (spec+0.18*de,spec,hue_key(s))
        nxt=min(remaining,key=score)
        remaining.remove(nxt); ordered.append(nxt)
    return ordered+missing


def _stats(vals):
    vals=list(vals)
    if not vals:return {'count':0,'mean':0.0,'p95':0.0,'max':0.0}
    sv=sorted(vals); p95=sv[min(len(sv)-1,int(round((len(sv)-1)*0.95)))]
    return {'count':len(vals),'mean':statistics.fmean(vals),'p95':p95,'max':max(vals)}


def _lab_props(s):
    L,a,b=(float(x) for x in s.lab_d65_10)
    C=math.hypot(a,b); h=math.degrees(math.atan2(b,a))%360.0
    return L,a,b,C,h


def _core_earth(s):
    L,a,b,C,h=_lab_props(s)
    return 30.0<=L<=85.0 and 3.5<=C<=30.0 and 45.0<=h<=105.0


def _broad_earth(s):
    # Audit-only broader net. This is deliberately NOT a production rule.
    L,a,b,C,h=_lab_props(s)
    return 25.0<=L<=90.0 and 2.0<=C<=35.0 and 30.0<=h<=120.0


def _positions(order,predicate):
    pos=[i+1 for i,s in enumerate(order) if predicate(s)]
    if not pos:return {'count':0,'positions':[],'blocks':0,'span':0,'gaps':0}
    blocks=1
    for a,b in zip(pos,pos[1:]):
        if b!=a+1:blocks+=1
    span=pos[-1]-pos[0]+1
    gaps=span-len(pos)
    return {'count':len(pos),'positions':pos,'blocks':blocks,'span':span,'gaps':gaps}


def _adjacent_metrics(order):
    des=[]; specs=[]; transitions=[]
    for i,(a,b) in enumerate(zip(order,order[1:]),1):
        try: de=float(delta_e(a.lab_d65_10,b.lab_d65_10,'CIE 2000'))
        except Exception: de=float('nan')
        try: sp=float(spectral_rms_distance(a,b))
        except Exception: sp=float('nan')
        if math.isfinite(de):des.append(de)
        if math.isfinite(sp):specs.append(sp)
        La,aa,ba,Ca,ha=_lab_props(a); Lb,ab,bb,Cb,hb=_lab_props(b)
        dh=abs(((hb-ha+180)%360)-180)
        transitions.append({'rank':i,'from':a.display_name,'to':b.display_name,'de00':de,'spectral_rms':sp,
                            'from_L':La,'from_C':Ca,'from_h':ha,'to_L':Lb,'to_C':Cb,'to_h':hb,'hue_jump':dh})
    transitions.sort(key=lambda x:(-x['de00'] if math.isfinite(x['de00']) else float('inf')))
    return {'de00':_stats(des),'spectral_rms':_stats(specs),'top_jumps':transitions[:20]}


def _order_rows(order):
    rows=[]
    for i,s in enumerate(order,1):
        L,a,b,C,h=_lab_props(s)
        rows.append({'rank':i,'name':s.display_name,'L':L,'a':a,'b':b,'C':C,'h':h,
                     'core_earth':_core_earth(s),'broad_earth':_broad_earth(s),'has_spectrum':bool(s.has_spectrum())})
    return rows


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('qtx'); args=ap.parse_args(argv)
    path=Path(args.qtx)
    if not path.exists():raise SystemExit(f'找不到文件: {path}')
    REPORT_DIR.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    print('Chromatic Analysis · 色貌连续排序专项验证 R2')
    print('='*68)
    print(f'文件: {path}')
    t0=perf_counter(); samples=list(parse_qtx_file(path)); parse_s=perf_counter()-t0
    print(f'解析: {len(samples)} 色样 · {sum(s.has_spectrum() for s in samples)} 完整光谱 · {parse_s:.3f}s')

    print('[1/4] 计算历史“光谱+感知”基线（无暖土色保护）...',flush=True)
    t0=perf_counter(); legacy=_legacy_hybrid(samples); legacy_s=perf_counter()-t0
    print(f'    {legacy_s:.3f}s')
    print('[2/4] 计算 HF88 当前“色貌连续排序”...',flush=True)
    t0=perf_counter(); current=spectral_order(samples,hybrid=True); current_s=perf_counter()-t0
    print(f'    {current_s:.3f}s')

    print('[3/4] 检查卡其 / 米色 / 棕黄连续性...',flush=True)
    legacy_core=_positions(legacy,_core_earth); current_core=_positions(current,_core_earth)
    legacy_broad=_positions(legacy,_broad_earth); current_broad=_positions(current,_broad_earth)
    broad_not_core=[]
    for s in current:
        if _broad_earth(s) and not _core_earth(s):
            L,a,b,C,h=_lab_props(s)
            broad_not_core.append({'rank':current.index(s)+1,'name':s.display_name,'L':L,'C':C,'h':h})

    print(f"    核心暖土色: {current_core['count']} 张；HF88 分块={current_core['blocks']}，中间夹入非暖土色={current_core['gaps']} 张")
    print(f"    宽口径暖土候选: {current_broad['count']} 张；分块={current_broad['blocks']}，中间夹入非候选={current_broad['gaps']} 张")
    if broad_not_core:
        print(f'    宽口径但未被 HF88 保护规则覆盖: {len(broad_not_core)} 张（重点检查）')
        for r in broad_not_core[:12]:
            print(f"      #{r['rank']:03d} {r['name']}  L*={r['L']:.1f} C*={r['C']:.1f} h°={r['h']:.1f}")

    print('[4/4] 计算相邻综合色貌 / 光谱跳跃...',flush=True)
    legacy_metrics=_adjacent_metrics(legacy); current_metrics=_adjacent_metrics(current)

    report={
        'file':str(path),'created_at':datetime.now().isoformat(timespec='seconds'),'python':sys.version.split()[0],
        'platform':platform.platform(),'sample_count':len(samples),'spectral_count':sum(s.has_spectrum() for s in samples),
        'parse_s':parse_s,'legacy_hybrid_s':legacy_s,'hf88_current_s':current_s,
        'rules':{
            'historical_hybrid':'score = spectral RMS + 0.18*CIEDE2000, greedy nearest-neighbour path',
            'hf88_guard':'production core warm-earth guard: L* 30-85, C* 3.5-30, h° 45-105',
            'audit_broad_only':'diagnostic only: L* 25-90, C* 2-35, h° 30-120; NOT used by production'
        },
        'legacy':{'core_earth':legacy_core,'broad_earth':legacy_broad,'metrics':legacy_metrics,'order':_order_rows(legacy)},
        'hf88':{'core_earth':current_core,'broad_earth':current_broad,'metrics':current_metrics,'order':_order_rows(current),
                'broad_not_core':broad_not_core},
    }
    json_path=REPORT_DIR/f'PALETTE_CONTINUITY_AUDIT_{stamp}.json'
    txt_path=REPORT_DIR/f'PALETTE_CONTINUITY_AUDIT_{stamp}.txt'
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')

    def st(x):return f"mean={x['mean']:.3f}, p95={x['p95']:.3f}, max={x['max']:.3f}"
    lines=[
        'Chromatic Analysis · 色貌连续排序专项验证 R2','='*68,
        f'文件: {path}',f'色样: {len(samples)}；完整光谱: {sum(s.has_spectrum() for s in samples)}',f'QTX解析: {parse_s:.3f}s','',
        'A. 历史 光谱+感知（无暖土色保护）',f'  计算: {legacy_s:.3f}s',
        f"  核心暖土色: count={legacy_core['count']} blocks={legacy_core['blocks']} gaps={legacy_core['gaps']} positions={legacy_core['positions']}",
        f"  宽口径暖土候选: count={legacy_broad['count']} blocks={legacy_broad['blocks']} gaps={legacy_broad['gaps']}",
        f"  相邻ΔE00: {st(legacy_metrics['de00'])}",f"  相邻光谱RMS: {st(legacy_metrics['spectral_rms'])}",'',
        'B. HF88 当前 色貌连续排序',f'  计算: {current_s:.3f}s',
        f"  核心暖土色: count={current_core['count']} blocks={current_core['blocks']} gaps={current_core['gaps']} positions={current_core['positions']}",
        f"  宽口径暖土候选: count={current_broad['count']} blocks={current_broad['blocks']} gaps={current_broad['gaps']}",
        f"  相邻ΔE00: {st(current_metrics['de00'])}",f"  相邻光谱RMS: {st(current_metrics['spectral_rms'])}",'',
        'C. HF88 保护规则未覆盖但宽口径判为暖土色的样本'
    ]
    if broad_not_core:
        for r in broad_not_core:
            lines.append(f"  #{r['rank']:03d} {r['name']} | L*={r['L']:.3f} C*={r['C']:.3f} h°={r['h']:.3f}")
    else:lines.append('  无')
    lines += ['', 'D. HF88 最大综合色貌跳跃（Top 20）']
    for t in current_metrics['top_jumps']:
        lines.append(f"  #{t['rank']:03d} ΔE00={t['de00']:.3f} RMS={t['spectral_rms']:.3f} hueJump={t['hue_jump']:.1f}° | {t['from']} -> {t['to']}")
    lines += ['', 'E. HF88 当前完整顺序（rank / name / L* / C* / h° / earth flags）']
    for r in report['hf88']['order']:
        lines.append(f"  {r['rank']:03d}\t{r['name']}\tL={r['L']:.3f}\tC={r['C']:.3f}\th={r['h']:.3f}\tcore={int(r['core_earth'])}\tbroad={int(r['broad_earth'])}")
    lines += ['', f'JSON: {json_path}', '请把本 TXT 完整内容发给 ChatGPT。']
    txt_path.write_text('\n'.join(lines),encoding='utf-8')
    print('\n'+'='*68)
    print(f'已生成:\n{txt_path}\n{json_path}')
    print('请把 TXT 完整内容发给 ChatGPT。')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
