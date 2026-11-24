"""D6 honesty check: the discovery 'plain-break' selected breaks whose NEXT THREE closes stayed outside the band = lookahead.
The only honest version of that number: enter at the close of the 3rd bar after the break IF all three closed outside (confirmed breakout).
Raw forward moves from that later entry, drift-controlled, plus a simple costed 8h hold. 1h BTC/ETH/XRP 2021-23."""
import json, numpy as np, pandas as pd, sys
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
N,QLO,QHI,HCAP=36,0.05,0.95,3.0; HZ=(4,8,24,48)
rows=[]
for coin in ('BTC','ETH','XRP'):
    d=pd.DataFrame(json.load(open(rf'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\{coin}-60m.json')),columns=['t','o','h','l','c']).set_index('t')
    pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    t=d.index.values; c=d.c.values; atr=d.atr.values
    mon=pd.Series(pd.to_datetime(t,unit='s').to_period('M')); drift={z:pd.Series(np.r_[c[z:]/c[:-z]-1,[np.nan]*z]*1e4).groupby(mon.values).mean() for z in HZ}
    last=-10**9
    for i in range(N+3,len(c)-100):
        if i<last+12: continue
        w=c[i-N:i]; top=np.quantile(w,QHI); bot=np.quantile(w,QLO); a=atr[i]
        if not a>0 or (top-bot)/a>HCAP: continue
        if c[i]>top: s=1
        elif c[i]<bot: s=-1
        else: continue
        last=i
        conf=all((c[k]>top) if s==1 else (c[k]<bot) for k in (i+1,i+2,i+3))
        for lab,j in (('at break close (honest, all breaks)',i),('3 bars later IF confirmed (honest)',i+3)):
            if lab.startswith('3') and not conf: continue
            E=c[j]; r=dict(coin=coin,ts=int(t[j])*1000,side=s,entry=lab)
            for z in HZ: r[f'x{z}']=s*(c[j+z]/E-1)*1e4-s*drift[z][mon.iloc[j]]
            rows.append(r)
        # the discovery number, reproduced: measured from the BREAK close but only for confirmed ones (lookahead)
        if conf:
            E=c[i]; r=dict(coin=coin,ts=int(t[i])*1000,side=s,entry='discovery: from break close, confirmed-only (LOOKAHEAD)')
            for z in HZ: r[f'x{z}']=s*(c[i+z]/E-1)*1e4-s*drift[z][mon.iloc[i]]
            rows.append(r)
d=pd.DataFrame(rows)
def ci(g,col): lo,hi,p=d4.boot(g[col].values,g.ts.values); return f"{g[col].mean():+5.0f} [{lo:+.0f},{hi:+.0f}]"
lines=[]
def P(s=''): lines.append(s); print(s,flush=True)
P("# D6 honesty check — excess over drift, bp, 1h BTC/ETH/XRP 2021-23")
for lab,g in d.groupby('entry',sort=False):
    P(f"{lab:58s} n={len(g):5d}: "+' | '.join(f"{z:>2}h {ci(g,f'x{z}')}" for z in HZ))
g=d[d.entry.str.startswith('3')]; P(f"confirmed-breakout entry, net of 10bp (no stop, 8h): {g.x8.mean()-10:+.0f}bp; share of breaks that confirm: {len(g)/len(d[d.entry.str.startswith('at')])*100:.0f}%")
open(r'C:\Users\you\Desktop\TradeDeck\specs\D6-results-learn.md','a',encoding='utf-8').write('\n'+'\n'.join(lines)+'\n')
