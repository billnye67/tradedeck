"""the user's box (36-bar close band 5/95 <=3 ATR). Entry B: after the poke and re-entry, the first close beyond the FAR edge within 48 bars -> go with it.
Control: plain first close beyond either edge of the same box with no prior poke. Raw + drift. 1h, BTC/ETH/XRP 2021-23."""
import json, numpy as np, pandas as pd, sys
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
N,QLO,QHI,HCAP=36,0.05,0.95,3.0; HZ=(4,8,24,48,96)
rows=[]
for coin in ('BTC','ETH','XRP'):
    d=pd.DataFrame(json.load(open(rf'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\{coin}-60m.json')),columns=['t','o','h','l','c']).set_index('t')
    pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    t=d.index.values; c=d.c.values; h=d.h.values; l=d.l.values; atr=d.atr.values
    mon=pd.Series(pd.to_datetime(t,unit='s').to_period('M')); drift={z:pd.Series(np.r_[c[z:]/c[:-z]-1,[np.nan]*z]*1e4).groupby(mon.values).mean() for z in HZ}
    def meas(j,s,kind,extra):
        E=c[j]; r=dict(coin=coin,ts=int(t[j])*1000,side=s,kind=kind,**extra)
        for z in HZ: r[f'f{z}']=s*(c[j+z]/E-1)*1e4; r[f'x{z}']=r[f'f{z}']-s*drift[z][mon.iloc[j]]
        rows.append(r)
    last=-10**9
    for i in range(N+3,len(c)-200):
        if i<last+12: continue
        w=c[i-N:i]; top=np.quantile(w,QHI); bot=np.quantile(w,QLO); a=atr[i]
        if not a>0 or (top-bot)/a>HCAP: continue
        if c[i]>top: # plain close above: control (breakout up), unless it is a poke that returns
            back=[k for k in range(i+1,i+4) if bot<c[k]<top]
            if back:  # poke up, back inside -> wait for the far side (bottom) break within 48 bars = SHORT with it
                j=back[0]; brk=[k for k in range(j+1,j+49) if c[k]<bot]; last=i
                if brk and not any(c[k]>top for k in range(j+1,brk[0])): meas(brk[0],-1,'second-break',dict(wait=brk[0]-j))
            else: meas(i,1,'plain-break',{}); last=i
        elif c[i]<bot:
            back=[k for k in range(i+1,i+4) if bot<c[k]<top]
            if back:
                j=back[0]; brk=[k for k in range(j+1,j+49) if c[k]>top]; last=i
                if brk and not any(c[k]<bot for k in range(j+1,brk[0])): meas(brk[0],1,'second-break',dict(wait=brk[0]-j))
            else: meas(i,-1,'plain-break',{}); last=i
d=pd.DataFrame(rows)
def ci(g,col): lo,hi,p=d4.boot(g[col].values,g.ts.values); return f"{g[col].mean():+5.0f} [{lo:+.0f},{hi:+.0f}]"
lines=[]
def P(s=''): lines.append(s); print(s,flush=True)
P("# the user's box: SECOND-BREAK entry (poke, back in, then close out the far side -> go with it) vs PLAIN break of the same box. 1h BTC/ETH/XRP 2021-23, excess over drift, bp")
for kind in ('second-break','plain-break'):
    for s,nm in ((1,'LONG'),(-1,'SHORT')):
        g=d[(d.kind==kind)&(d.side==s)]; yrs=pd.to_datetime(g.ts,unit='ms').dt.year
        P(f"{kind:13s} {nm:5s} n={len(g):4d}: "+' | '.join(f"{z:>2}h {ci(g,f'x{z}')}" for z in HZ)+" | yrs 48h "+' '.join(f"{y}:{v:+.0f}" for y,v in g.x48.groupby(yrs).mean().items())+" | coins "+' '.join(f"{k} {v:+.0f}" for k,v in g.groupby('coin').x48.mean().items()))
g=d[d.kind=='second-break']; P(f"second-break: median wait from re-entry to the far-side break {g.wait.median():.0f} bars; raw 48h {g.f48.mean():+.0f}bp, excess {g.x48.mean():+.0f}bp, n={len(g)}")
open(r'C:\Users\you\Desktop\TradeDeck\specs\AMD-blind-results.md','a',encoding='utf-8').write('\n'+'\n'.join(lines)+'\n')
