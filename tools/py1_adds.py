import os, sys, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT,'tools'))
from s1_evalsim import sim
T = pd.read_csv(os.path.join(ROOT,'specs','coinflip','s1_trades_nopeek.csv')).sort_values('entryTs').reset_index(drop=True)
EXAM = 1704067200000
def replay(T, mode):  # mode: 'base' | 'profit' | 'loss' (placebo) | 'any'
    open_=[]; keep=[]; isadd=[]
    for r in T.itertuples():
        open_=[o for o in open_ if o.exitTs > r.entryTs]
        if len(open_)>=2: continue
        same=[o for o in open_ if o.market==r.market]; add=False
        if same:
            if mode=='base' or len(same)>=2: continue
            f=same[0]
            if f.side!=r.side: continue
            inp = (r.entry > f.entry) if f.side==1 else (r.entry < f.entry)
            if mode=='profit' and not inp: continue
            if mode=='loss' and inp: continue
            add=True
        open_.append(r); keep.append(r); isadd.append(add)
    K=pd.DataFrame(keep); K['isadd']=isadd; K['day']=pd.to_datetime(K.exitTs,unit='ms').dt.floor('D'); return K
rng=np.random.default_rng(1)
for mode in ('base','profit','loss','any'):
    K=replay(T,mode)
    for name,k in (('learn',K[K.entryTs<EXAM]),('exam',K[K.entryTs>=EXAM])):
        a=k[k.isadd].netR; base=k[~k.isadd].netR
        t = (a.mean()-base.mean())/ (a.std(ddof=1)/np.sqrt(len(a))) if len(a)>2 else float('nan')
        boots=[rng.choice(a.values,len(a)).mean() for _ in range(2000)] if len(a)>2 else [np.nan]
        print(f"{mode:7s} {name}: total {k.netR.sum():+6.1f}R | adds n {len(a):3d} sum {a.sum():+6.1f}R avg {a.mean() if len(a) else 0:+.3f}R win% {(a>0).mean() if len(a) else 0:.0%} | non-add avg {base.mean():+.4f} | t vs non-add {t:+.2f} | boot 5th pct {np.percentile(boots,5):+.3f}")
    if mode in ('profit','loss'):
        p,b,m=sim(K[K.entryTs>=EXAM],start=53079,tgt=55000,floor=46500); print(f"        evalsim exam PASS {p:.0%} BLOW {b:.0%}")
# where the add money lands: by year
K=replay(T,'profit'); K['yr']=pd.to_datetime(K.entryTs,unit='ms').dt.year
print(K[K.isadd].groupby('yr').netR.agg(['count','sum']).round(1).to_string())
print('--- maxDD and R/mo by mode ---')
def maxdd(k):
    c=k.sort_values('exitTs').netR.cumsum(); return float((c.cummax()-c).max())
for mode in ('base','profit','loss','any'):
    K=replay(T,mode)
    for name,k,mo in (('learn',K[K.entryTs<EXAM],48.0),('exam',K[K.entryTs>=EXAM],31.9)):
        print(f"{mode:7s} {name}: n {len(k):4d} R/mo {k.netR.sum()/mo:+.2f} maxDD {maxdd(k):.1f}R")
