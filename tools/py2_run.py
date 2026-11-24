import os, sys, numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,os.path.join(ROOT,'tools'))
from s1_evalsim import sim
T=pd.read_csv(os.path.join(ROOT,'specs','coinflip','s1_trades_nopeek.csv')).sort_values('entryTs').reset_index(drop=True)
EXAM=1704067200000
def replay(T, mode):  # base | any | new (adds only if stop differs from 1st unit's stop)
    open_=[]; keep=[]; isadd=[]; samestop=[]
    for r in T.itertuples():
        open_=[o for o in open_ if o.exitTs>r.entryTs]
        if len(open_)>=2: continue
        same=[o for o in open_ if o.market==r.market]; add=False; ss=False
        if same:
            if mode=='base' or len(same)>=2: continue
            f=same[0]
            if f.side!=r.side: continue
            ss = abs(f.stop-r.stop)/r.stop < 1e-6
            if mode=='new' and ss: continue
            add=True
        open_.append(r); keep.append(r); isadd.append(add); samestop.append(ss)
    K=pd.DataFrame(keep); K['isadd']=isadd; K['samestop']=samestop
    K['day']=pd.to_datetime(K.exitTs,unit='ms').dt.floor('D'); K['yr']=pd.to_datetime(K.entryTs,unit='ms').dt.year; return K
def maxdd(k): c=k.sort_values('exitTs').netR.cumsum(); return float((c.cummax()-c).max())
def worst(k):
    d=k.groupby('day').netR.sum(); full=d.reindex(pd.date_range(d.index.min(),d.index.max(),freq='D'),fill_value=0.0)
    return float(full.min()), float(full.rolling(7).sum().min())
rng=np.random.default_rng(3)
K_any=replay(T,'any'); a=K_any[K_any.isadd]
print(f"FIDELITY: {len(a)} adds, same-stop (same sweep re-fired) {a.samestop.sum()} = {a.samestop.mean():.0%}; "
      f"same-stop adds avg {a[a.samestop].netR.mean():+.3f}R, new-setup adds avg {a[~a.samestop].netR.mean():+.3f}R")
for mode in ('base','any','new'):
    K=replay(T,mode)
    for name,k,mo in (('learn',K[K.entryTs<EXAM],48.0),('exam',K[K.entryTs>=EXAM],31.9)):
        ad=k[k.isadd].netR; wd,ww=worst(k)
        boots=[rng.choice(ad.values,len(ad)).mean() for _ in range(2000)] if len(ad)>2 else [np.nan]
        print(f"{mode:5s} {name}: n {len(k):4d} R/mo {k.netR.sum()/mo:+.2f} maxDD {maxdd(k):.1f}R worstDay {wd:+.1f}R worst7d {ww:+.1f}R | adds n {len(ad):3d} avg {ad.mean() if len(ad) else 0:+.3f}R boot5 {np.percentile(boots,5):+.3f}")
    p,b,m=sim(K[K.entryTs>=EXAM],start=53079,tgt=55000,floor=46500)
    yrs=K.groupby('yr').netR.sum(); print(f"{mode:5s} evalsim exam PASS {p:.0%} BLOW {b:.0%} median {m} | positive years {(yrs>0).sum()}/{len(yrs)}: "+' '.join(f"{y}:{v:+.0f}" for y,v in yrs.items()))
