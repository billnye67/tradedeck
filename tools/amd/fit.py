"""Fit a mechanical box to the user's 55 marks (BTC 1h), then measure recall and firing rate. Box edges = quantile band of CLOSES."""
import json, numpy as np, pandas as pd
m=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-marks.json'))); m=m[m.tf==60]
d=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\BTC-60m.json')),columns=['t','o','h','l','c']).set_index('t')
pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
t=d.index.values; pos={v:i for i,v in enumerate(t)}; c=d.c.values; h=d.h.values; l=d.l.values; atr=d.atr.values
# 1. where do his edges sit in the distribution of closes inside his window?
qt=[];qb=[]
for _,r in m.iterrows():
    w=c[pos[r.boxStartTs]:pos[r.boxEndTs]+1]; qt.append((w<=r.top).mean()); qb.append((w<bot).mean() if (bot:=r.bot) else 0)
print(f"his TOP sits at the {np.median(qt)*100:.0f}th percentile of closes (IQR {np.percentile(qt,25)*100:.0f}-{np.percentile(qt,75)*100:.0f}); his BOT at the {np.median(qb)*100:.0f}th (IQR {np.percentile(qb,25)*100:.0f}-{np.percentile(qb,75)*100:.0f})")
# 2. detector: at each bar i (candidate poke bar), box = closes of the N bars before i; band = [q_lo, q_hi] of those closes; height <= Hcap ATR;
#    poke: high > top + 0.1 ATR (UP) or low < bot - 0.1 ATR (DOWN) while the previous 3 bars' extremes were inside the band +/- 0.1 ATR;
#    re-entry: a close back inside within 3 bars. Event time = the poke bar.
def detect(N,qlo,qhi,Hcap):
    ev=[]; last=-10**9
    for i in range(N+3,len(c)-60):
        if i<last+12: continue
        w=c[i-N:i]; top=np.quantile(w,qhi); bot=np.quantile(w,qlo); a=atr[i]
        if not a>0 or (top-bot)/a>Hcap: continue
        prev_in=(h[i-3:i].max()<=top+0.1*a) and (l[i-3:i].min()>=bot-0.1*a)
        if not prev_in: continue
        if h[i]>top+0.1*a: s=-1
        elif l[i]<bot-0.1*a: s=1
        else: continue
        back=[k for k in range(i+1,i+4) if bot<c[k]<top]
        if not back: continue
        ev.append((i,s,top,bot,back[0])); last=i
    return ev
marks=[(pos[r.pokeTs],-1 if r.pokeDir=='UP' else 1) for _,r in m.iterrows()]
def recall(ev):
    hit=0
    for pi,ps in marks:
        if any(abs(e[0]-pi)<=6 and e[1]==ps for e in ev): hit+=1
    return hit/len(marks)
months=(t[-1]-t[0])/(30.44*86400)
print("\nN  qlo qhi Hcap | recall of his 48 | events/month BTC 1h")
best=None
for N in (48,72,96):
    for (qlo,qhi) in ((0.15,0.85),(0.10,0.90),(0.05,0.95)):
        for Hcap in (3.0,4.0):
            ev=detect(N,qlo,qhi,Hcap); rc=recall(ev); rate=len(ev)/months
            print(f"{N:3d} {qlo:.2f} {qhi:.2f} {Hcap:.0f}   | {rc*100:5.0f}%           | {rate:5.1f}")
            if best is None or (rc,-rate)>(best[0],-best[1]): best=(rc,rate,(N,qlo,qhi,Hcap))
print("\nbest recall:",best)
