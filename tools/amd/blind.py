"""Blind run of the fitted box (N=36 closes, 5/95 quantile band, <=3 ATR, poke >0.1 ATR with close within 0.5 ATR, back inside <=3 bars).
Trade AGAINST the poke from the re-entry close. BTC/ETH/XRP 1h, 2021-2023. Raw moves + drift control. Compare the user-marked events vs the rest."""
import json, numpy as np, pandas as pd, sys
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
N,QLO,QHI,HCAP=36,0.05,0.95,3.0; HZ=(4,8,24,48,96)
m=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-marks.json'))); m=m[m.tf==60]
rows=[]
for coin in ('BTC','ETH','XRP'):
    d=pd.DataFrame(json.load(open(rf'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\{coin}-60m.json')),columns=['t','o','h','l','c']).set_index('t')
    pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    t=d.index.values; c=d.c.values; h=d.h.values; l=d.l.values; atr=d.atr.values
    mon=pd.Series(pd.to_datetime(t,unit='s').to_period('M')); drift={z:pd.Series(np.r_[c[z:]/c[:-z]-1,[np.nan]*z]*1e4).groupby(mon.values).mean() for z in HZ}
    marked={int(r.pokeTs) for _,r in m.iterrows()} if coin=='BTC' else set()
    last=-10**9
    for i in range(N+3,len(c)-120):
        if i<last+12: continue
        w=c[i-N:i]; top=np.quantile(w,QHI); bot=np.quantile(w,QLO); a=atr[i]
        if not a>0 or (top-bot)/a>HCAP: continue
        if h[i]>top+0.1*a and c[i]<top+0.5*a: s=-1; closeOut=c[i]>top; depth=(h[i]-top)/a
        elif l[i]<bot-0.1*a and c[i]>bot-0.5*a: s=1; closeOut=c[i]<bot; depth=(bot-l[i])/a
        else: continue
        back=[k for k in range(i+1,i+4) if bot<c[k]<top]
        if not back: continue
        j=back[0]; E=c[j]; last=i
        r=dict(coin=coin,ts=int(t[j])*1000,side=s,closeOut=closeOut,depth=depth,H=(top-bot)/a,marked=any(abs(int(t[i])-mk)<=6*3600 for mk in marked))
        for z in HZ: r[f'f{z}']=s*(c[j+z]/E-1)*1e4; r[f'x{z}']=r[f'f{z}']-s*drift[z][mon.iloc[j]]
        far=bot if s==-1 else top; hit=[k for k in range(j+1,j+97) if (l[k]<=far if s==-1 else h[k]>=far)]; r['far96']=bool(hit)
        rows.append(r)
d=pd.DataFrame(rows)
def ci(g,col): lo,hi,p=d4.boot(g[col].values,g.ts.values); return f"{g[col].mean():+5.0f} [{lo:+.0f},{hi:+.0f}]"
lines=[]
def P(s=''): lines.append(s); print(s,flush=True)
P(f"# AMD blind run — the user's box fitted (36-bar close band 5/95 pct, <=3 ATR), poke and re-entry, trade against the poke. 1h, 2021-2023")
P(f"events: {len(d)} ({d.groupby('coin').size().to_dict()}) = {len(d)/36:.0f}/month across 3 coins | far edge reached within 96h: {d.far96.mean()*100:.0f}%")
P("ALL events   excess over drift: "+' | '.join(f"{z:>2}h {ci(d,f'x{z}')}" for z in HZ))
P("ALL events   raw            : "+' | '.join(f"{z:>2}h {ci(d,f'f{z}')}" for z in HZ))
for lab,g in (('the user-marked (BTC)',d[d.marked]),('NOT marked, BTC only',d[(~d.marked)&(d.coin=='BTC')])):
    P(f"{lab:22s} n={len(g)}: raw "+' | '.join(f"{z:>2}h {g[f'f{z}'].mean():+5.0f}" for z in HZ)+f" | far edge {g.far96.mean()*100:.0f}%")
yrs=pd.to_datetime(d.ts,unit='ms').dt.year
P("by year, excess 48h: "+' '.join(f"{y}:{v:+.0f}" for y,v in d.x48.groupby(yrs).mean().items())+" | by coin: "+' '.join(f"{k} {v:+.0f}" for k,v in d.groupby('coin').x48.mean().items())+" | by side: "+' '.join(f"{'long' if k==1 else 'short'} {v:+.0f}" for k,v in d.groupby('side').x48.mean().items()))
P("the user's hunches on the blind set, excess 48h: wick-only "+f"{d[~d.closeOut].x48.mean():+.0f} (n={int((~d.closeOut).sum())}) vs close-outside {d[d.closeOut].x48.mean():+.0f} (n={int(d.closeOut.sum())})")
P("box height terciles (ATR), excess 48h: "+' '.join(f"{str(k)}:{v:+.0f}" for k,v in d.groupby(pd.qcut(d.H,3)).x48.mean().items()))
P("poke depth terciles (ATR), excess 48h: "+' '.join(f"{str(k)}:{v:+.0f}" for k,v in d.groupby(pd.qcut(d.depth,3)).x48.mean().items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\AMD-blind-results.md','w',encoding='utf-8').write('\n'.join(lines)+'\n'); d.to_csv('blind_events.csv',index=False)
