"""Appearance-continuity layout V2.2 (diagnostic candidate).

Structural, dataset-agnostic revision of V2.1.  Fixed L*/C* buckets are removed
from layout grouping because batch tests showed over-segmentation near arbitrary
bin edges.  Broad hue family remains the only hard skeleton.  Within a family,
CIEDE2000 is primary; L*/C*/h° continuity stabilises appearance; spectral RMS is
only a secondary tie-break.  Neutral ignores h° and follows lightness continuity.
Only genuinely large perceptual jumps start a new row.
"""
from __future__ import annotations
import math, statistics
from dataclasses import dataclass
from typing import Sequence, Any
from .models import Sample
from .palette_arrangement import _de00_lab
from .palette_continuity_v21 import (
    FAMILY_ORDER, _hue_distance, _lab_props, _broad_family, _neutral_core,
    _spectral_rms, _median_positive,
)
TONE_NAMES=("Deep","Dark","Mid","Light","Pale","VeryLight")
TONE_EDGES=(0.0,25.0,42.0,60.0,78.0,90.0,101.0)
@dataclass
class _Rec:
    idx:int; sample:Sample; L:float; a:float; b:float; C:float; h:float; family:str=""
def _tone_name(L:float)->str:
    for i in range(len(TONE_EDGES)-1):
        if TONE_EDGES[i] <= L < TONE_EDGES[i+1]: return TONE_NAMES[i]
    return TONE_NAMES[-1]
def _chroma_name(C:float,neutral:bool=False)->str:
    if neutral:return "Neutral"
    if C<10.0:return "Muted"
    if C<30.0:return "Normal"
    return "Vivid"
def _pair_matrices(recs:list[_Rec]):
    n=len(recs);de=[[0.0]*n for _ in range(n)];sp=[[float('nan')]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1,n):
            d=float(_de00_lab(recs[i].sample.lab_d65_10,recs[j].sample.lab_d65_10));s=_spectral_rms(recs[i].sample,recs[j].sample)
            de[i][j]=de[j][i]=d;sp[i][j]=sp[j][i]=s
    return de,sp
def _assign_families(recs:list[_Rec],de:list[list[float]])->None:
    anchors=[]
    for r in recs:
        if _neutral_core(r.L,r.C):r.family='Neutral'
        else:
            r.family=_broad_family(r.h)
            if r.C>=8.0:anchors.append(r.idx)
    for r in recs:
        if r.family=='Neutral' or r.C>=8.0:continue
        cands=[j for j in anchors if abs(recs[j].L-r.L)<=18.0]
        if not cands:r.family='Neutral';continue
        j=min(cands,key=lambda x:(de[r.idx][x],abs(recs[x].L-r.L)))
        r.family=recs[j].family if de[r.idx][j]<=8.5 else 'Neutral'
def _family_score(indices:list[int],family:str,recs:list[_Rec],de,sp):
    de_vals=[];sp_vals=[]
    for p,a in enumerate(indices):
        for b in indices[p+1:]:
            de_vals.append(de[a][b])
            if math.isfinite(sp[a][b]):sp_vals.append(sp[a][b])
    de_med=_median_positive(de_vals,1.0);sp_med=_median_positive(sp_vals,1.0);score={}
    for a in indices:
        for b in indices:
            if a==b:score[(a,b)]=0.0;continue
            dn=de[a][b]/de_med;sn=(sp[a][b]/sp_med) if math.isfinite(sp[a][b]) else dn
            dL=abs(recs[a].L-recs[b].L);dC=abs(recs[a].C-recs[b].C)
            if family=='Neutral':
                val=0.56*dn+0.08*sn+0.30*(dL/15.0)+0.06*(dC/10.0)
            else:
                dh=(_hue_distance(recs[a].h,recs[b].h)/35.0) if min(recs[a].C,recs[b].C)>=6.0 else 0.0
                val=0.62*dn+0.10*sn+0.16*(dL/18.0)+0.07*(dC/18.0)+0.05*dh
            if de[a][b]>10.0:val+=((de[a][b]-10.0)/4.5)**2
            if dL>28.0:val+=((dL-28.0)/12.0)**2
            score[(a,b)]=val
    return score
def _two_opt(path,score,passes=2):
    p=list(path)
    for _ in range(passes):
        changed=False
        for i in range(len(p)-3):
            a,b=p[i],p[i+1]
            for k in range(i+2,len(p)-1):
                c,d=p[k],p[k+1]
                if score[(a,c)]+score[(b,d)]+1e-12 < score[(a,b)]+score[(c,d)]:
                    p[i+1:k+1]=reversed(p[i+1:k+1]);changed=True
        if not changed:break
    return p
def _family_path(indices:list[int],family:str,recs:list[_Rec],de,sp)->list[int]:
    if len(indices)<=1:return list(indices)
    score=_family_score(indices,family,recs,de,sp)
    if family=='Neutral':
        # Neutral axis: stable black -> grey -> white skeleton.  One bounded
        # local optimisation keeps near-equal tones coherent without hue noise.
        base=sorted(indices,key=lambda i:(recs[i].L,recs[i].C,recs[i].sample.display_name.casefold()))
        return _two_opt(base,score,1)
    def greedy(start):
        rem=set(indices);rem.remove(start);out=[start]
        while rem:
            cur=out[-1]
            nxt=min(rem,key=lambda j:(score[(cur,j)],de[cur][j],abs(recs[cur].L-recs[j].L),recs[j].sample.display_name.casefold()))
            out.append(nxt);rem.remove(nxt)
        return out
    starts={min(indices,key=lambda i:recs[i].L),max(indices,key=lambda i:recs[i].L),min(indices,key=lambda i:recs[i].C),max(indices,key=lambda i:recs[i].C),min(indices,key=lambda i:recs[i].h),max(indices,key=lambda i:recs[i].h),indices[0]}
    best=None;best_cost=float('inf')
    for st in list(starts)[:8]:
        p=_two_opt(greedy(st),score,2);c=sum(score[(a,b)] for a,b in zip(p,p[1:]))
        if c<best_cost:best,best_cost=p,c
    return best or list(indices)
def _segment_path(path:list[int],recs:list[_Rec],de):
    if len(path)<=1:return [path],10.0
    jumps=[de[a][b] for a,b in zip(path,path[1:])];med=statistics.median(jumps);mad=statistics.median(abs(x-med) for x in jumps)
    threshold=min(11.0,max(7.5,med+3.5*1.4826*mad));parts=[];start=0
    for i,(a,b) in enumerate(zip(path,path[1:])):
        d=de[a][b];dL=abs(recs[a].L-recs[b].L)
        if d>threshold or (d>9.5 and dL>24.0):parts.append(path[start:i+1]);start=i+1
    parts.append(path[start:]);return [p for p in parts if p],threshold
def _seg_label(path,family,recs):
    L=statistics.median(recs[i].L for i in path);C=statistics.median(recs[i].C for i in path)
    return _tone_name(L),_chroma_name(C,family=='Neutral')
def appearance_continuity_layout_v22(samples:Sequence[Sample],columns:int=6)->dict[str,Any]:
    samples=list(samples);columns=max(1,int(columns));recs=[]
    for i,sm in enumerate(samples):
        L,a,b,C,h=_lab_props(sm);recs.append(_Rec(i,sm,L,a,b,C,h))
    if not recs:return {'order':[],'grid':[],'groups':[],'breaks':[],'info':{},'columns':columns}
    de,sp=_pair_matrices(recs);_assign_families(recs,de);families={f:[] for f in FAMILY_ORDER}
    for r in recs:families[r.family].append(r.idx)
    segments=[];breaks=[]
    for fam in FAMILY_ORDER:
        inds=families[fam]
        if not inds:continue
        path=_family_path(inds,fam,recs,de,sp);parts,thr=_segment_path(path,recs,de)
        for pi,part in enumerate(parts):
            tone,chroma=_seg_label(part,fam,recs);segments.append((fam,part,tone,chroma,thr,pi))
        for l,r in zip(parts,parts[1:]):breaks.append({'from':l[-1],'to':r[0],'de00':de[l[-1]][r[0]],'reason':'appearance_jump'})
    grid=[];row=[];order=[];groups=[];prev=None;prevfam=None
    for fam,path,tone,chroma,thr,pi in segments:
        if not path:continue
        if row and ((prevfam is not None and fam!=prevfam) or pi>0):
            row += [None]*(columns-len(row));grid.append(row);row=[]
            breaks.append({'from':prev,'to':path[0],'de00':(de[prev][path[0]] if prev is not None else None),'reason':'family_or_jump_boundary'})
        groups.append({'family':fam,'tone':tone,'chroma':chroma,'size':len(path),'break_threshold':thr})
        for idx in path:
            row.append(recs[idx].sample);order.append(recs[idx].sample);prev=idx
            if len(row)==columns:grid.append(row);row=[]
        prevfam=fam
    if row:row += [None]*(columns-len(row));grid.append(row)
    if len(order)!=len(samples) or len({id(x) for x in order})!=len(samples):raise ValueError('Appearance continuity V2.2 integrity failure')
    info={id(r.sample):{'family':r.family,'tone':_tone_name(r.L),'chroma':_chroma_name(r.C,r.family=='Neutral'),'L':r.L,'C':r.C,'h':r.h} for r in recs}
    slots=max(1,len(grid)*columns)
    return {'order':order,'grid':grid,'groups':groups,'breaks':breaks,'info':info,'columns':columns,'compactness':len(samples)/slots,'extra_slots':slots-len(samples)}
