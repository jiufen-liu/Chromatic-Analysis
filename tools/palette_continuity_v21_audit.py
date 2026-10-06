from __future__ import annotations
import argparse, csv, json, math, statistics, sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from qtx_core import parse_qtx_file, spectral_order, appearance_continuity_layout_v21
from qtx_core.colorimetry import delta_e
from qtx_app.excel_exchange import _lab_to_hex

OUT=ROOT/'diagnostics_reports'

def pct(v,q):
    v=sorted(v); return v[min(len(v)-1,max(0,int(round((len(v)-1)*q))))] if v else 0.0

def stats(v):
    v=[float(x) for x in v if math.isfinite(float(x))]
    return {'count':len(v),'mean':statistics.fmean(v) if v else 0.0,'p95':pct(v,.95),'max':max(v) if v else 0.0}

def row_metrics(grid):
    vals=[]; hard10=hard20=0
    for row in grid:
        ss=[s for s in row if s is not None]
        for a,b in zip(ss,ss[1:]):
            d=float(delta_e(a.lab_d65_10,b.lab_d65_10,'CIE 2000')); vals.append(d)
            hard10 += d>10.0; hard20 += d>20.0
    r=stats(vals); r['over10']=hard10; r['over20']=hard20; return r

def linear_metrics(order, columns=6):
    grid=[]
    for i in range(0,len(order),columns):
        row=list(order[i:i+columns]); row += [None]*(columns-len(row)); grid.append(row)
    return row_metrics(grid)

def safe_hex(lab):
    try:return _lab_to_hex(tuple(float(x) for x in lab)).replace('#','')
    except Exception:return 'D9D9D9'

def preview(path, result):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:return False
    grid=result['grid']; cols=result['columns']; info=result['info']
    cw,ch,sw,label=205,105,60,125; w=label+cols*cw; h=max(1,len(grid))*ch
    im=Image.new('RGB',(w,h),'white'); dr=ImageDraw.Draw(im); font=ImageFont.load_default()
    for r,row in enumerate(grid):
        y=r*ch; first=next((s for s in row if s is not None),None)
        if first is not None:
            ii=info[id(first)]; dr.text((4,y+7),f"{ii['family']}\n{ii['tone']} / {ii['chroma']}",fill=(55,65,78),font=font)
        for c,sm in enumerate(row):
            if sm is None:continue
            x=label+c*cw; hx=safe_hex(sm.lab_d65_10); rgb=tuple(int(hx[i:i+2],16) for i in (0,2,4))
            dr.rounded_rectangle((x+4,y+4,x+cw-8,y+sw),radius=7,fill=rgb,outline=(215,220,228))
            name=sm.display_name or '(unnamed)'; name=name if len(name)<=25 else name[:24]+'…'
            L,a,b=(float(x) for x in sm.lab_d65_10); C=math.hypot(a,b); hue=math.degrees(math.atan2(b,a))%360 if C>1e-12 else 0
            dr.text((x+7,y+sw+5),name,fill=(20,25,32),font=font); dr.text((x+7,y+sw+21),f'L {L:.1f}  C {C:.1f}  h {hue:.0f}°',fill=(85,95,110),font=font)
    im.save(path); return True

def audit_one(qtx:Path,out_dir:Path,columns=6,prefix='PAC_V21'):
    t=perf_counter(); samples=list(parse_qtx_file(qtx)); parse_s=perf_counter()-t
    t=perf_counter(); base=spectral_order(samples,hybrid=True); base_s=perf_counter()-t
    t=perf_counter(); cand=appearance_continuity_layout_v21(samples,columns); cand_s=perf_counter()-t
    base_m=linear_metrics(base,columns); cand_m=row_metrics(cand['grid'])
    singleton=sum(1 for g in cand['groups'] if g['size']==1)
    stem=f"{prefix}_{qtx.stem}".replace(' ','_')
    png=out_dir/f'{stem}_PREVIEW.png'; txt=out_dir/f'{stem}_AUDIT.txt'; js=out_dir/f'{stem}_AUDIT.json'
    preview(png,cand)
    payload={'file':str(qtx),'samples':len(samples),'parse_s':parse_s,'legacy_s':base_s,'candidate_s':cand_s,
             'legacy_row_de':base_m,'candidate_row_de':cand_m,'groups':cand['groups'],'break_count':len(cand['breaks']),
             'singleton_groups':singleton,'rows':len(cand['grid'])}
    js.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['Chromatic Analysis · 色貌连续排序 V2.1 结构性离线验证','='*72,f'文件: {qtx}',f'色样: {len(samples)}',
           f'解析: {parse_s:.3f}s  旧光谱+感知: {base_s:.3f}s  V2.1候选: {cand_s:.3f}s','',
           f"旧版行内ΔE00: mean={base_m['mean']:.3f} p95={base_m['p95']:.3f} max={base_m['max']:.3f} >10={base_m['over10']} >20={base_m['over20']}",
           f"V2.1行内ΔE00: mean={cand_m['mean']:.3f} p95={cand_m['p95']:.3f} max={cand_m['max']:.3f} >10={cand_m['over10']} >20={cand_m['over20']}",
           f"结构: rows={len(cand['grid'])} groups={len(cand['groups'])} singleton_groups={singleton} breaks={len(cand['breaks'])}",'',
           '分组明细:']
    for i,g in enumerate(cand['groups'],1):lines.append(f"  {i:02d}. {g['family']} / {g['tone']} / {g['chroma']} · {g['size']} 张 · break≤{g['break_threshold']:.2f}")
    lines+=['',f'预览: {png}',f'JSON: {js}','请重点目视检查：浅蓝/天蓝、深蓝、黑灰、粉色、卡其/米色是否各自成团；若异常，把 PNG + TXT 发给 ChatGPT。']
    txt.write_text('\n'.join(lines),encoding='utf-8')
    return payload|{'png':str(png),'txt':str(txt)}

def batch(folder:Path,columns:int):
    files=sorted(p for p in folder.rglob('*.qtx') if p.is_file())
    if not files:print(f'文件夹中没有QTX: {folder}');return 2
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S'); out=OUT/f'PAC_V21_BATCH_{stamp}';out.mkdir(parents=True,exist_ok=True)
    rows=[]
    print(f'批量验证 {len(files)} 个 QTX；结果目录: {out}')
    for i,p in enumerate(files,1):
        print(f'[{i}/{len(files)}] {p.name}',flush=True)
        try:rows.append(audit_one(p,out,columns,prefix=f'{i:03d}_PAC_V21'))
        except Exception as e:rows.append({'file':str(p),'error':repr(e)});print('  失败:',e)
    csvp=OUT/f'PAC_V21_BATCH_SUMMARY_{stamp}.csv'
    headers=['file','samples','legacy_mean','candidate_mean','legacy_p95','candidate_p95','legacy_max','candidate_max','legacy_over10','candidate_over10','legacy_over20','candidate_over20','groups','singletons','breaks','rows','status']
    with csvp.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(headers)
        for x in rows:
            if 'error' in x:w.writerow([x['file']]+['']*15+[x['error']]);continue
            a=x['legacy_row_de'];b=x['candidate_row_de'];w.writerow([x['file'],x['samples'],a['mean'],b['mean'],a['p95'],b['p95'],a['max'],b['max'],a['over10'],b['over10'],a['over20'],b['over20'],len(x['groups']),x['singleton_groups'],x['break_count'],x['rows'],'PASS'])
    print(f'\n批量验证完成。汇总: {csvp}\n每个QTX都有 PREVIEW.png + AUDIT.txt。')
    return 0

def main():
    ap=argparse.ArgumentParser();ap.add_argument('path',type=Path);ap.add_argument('--columns',type=int,default=6);a=ap.parse_args();p=a.path.expanduser().resolve();OUT.mkdir(exist_ok=True)
    print('Chromatic Analysis · 色貌连续排序 V2.1 结构性离线验证');print('='*72)
    print('结构：综合色相大族 → L*/C*子群 → 感知优先/光谱辅助 → 大跳跃自动换行')
    print('说明：支持单个QTX或整个文件夹；只生成报告，不改正式色卡编排。\n')
    if not p.exists():print('路径不存在:',p);return 2
    if p.is_dir():return batch(p,a.columns)
    if p.suffix.lower()!='.qtx':print('请选择QTX或包含QTX的文件夹');return 2
    r=audit_one(p,OUT,a.columns);print(f"完成。\n预览: {r['png']}\n报告: {r['txt']}");return 0
if __name__=='__main__':raise SystemExit(main())
