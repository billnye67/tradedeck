import json, numpy as np, pandas as pd
m=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-marks.json'))); m=m[m.tf==60]
d=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\BTC-60m.json')),columns=['t','o','h','l','c']).set_index('t')
pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
t=d.index.values; pos={v:i for i,v in enumerate(t)}; c=d.c.values; h=d.h.values; l=d.l.values; atr=d.atr.values
pre=[]; post=[]; exit_after=[]; f={48:[],96:[]}
for _,r in m.iterrows():
    i0,i1,ip,ie=pos[r.boxStartTs],pos[r.boxEndTs],pos[r.pokeTs],pos[r.entryTs]; pre.append(ip-i0); post.append(i1-ip)
    s=-1 if r.pokeDir=='UP' else 1   # trade direction = against the poke
    far=r.bot if s==-1 else r.top; k=next((k for k in range(ip+1,ip+200) if (c[k]<far if s==-1 else c[k]>far)),None); exit_after.append(None if k is None else k-ip)
    for z in f: f[z].append(s*(c[ie+z]/c[ie]-1)*1e4)
pre=np.array(pre); post=np.array(post); ea=np.array([e for e in exit_after if e is not None])
print(f"bars from box start to poke: median {np.median(pre):.0f} (IQR {np.percentile(pre,25):.0f}-{np.percentile(pre,75):.0f}) | poke to box end: median {np.median(post):.0f} (IQR {np.percentile(post,25):.0f}-{np.percentile(post,75):.0f})")
print(f"first close beyond the FAR edge after the poke: {len(ea)}/{len(m)} within 200 bars, median {np.median(ea):.0f} bars (IQR {np.percentile(ea,25):.0f}-{np.percentile(ea,75):.0f})")
print(f"move from his entry, against the poke: 48 bars mean {np.mean(f[48]):+.0f}bp median {np.median(f[48]):+.0f} | 96 bars mean {np.mean(f[96]):+.0f} median {np.median(f[96]):+.0f}")
def detect(N,qlo,qhi,Hcap,prevN):
    ev=[]; last=-10**9
    for i in range(N+3,len(c)-100):
        if i<last+12: continue
        w=c[i-N:i]; top=np.quantile(w,qhi); bot=np.quantile(w,qlo); a=atr[i]
        if not a>0 or (top-bot)/a>Hcap: continue
        if prevN and not ((h[i-prevN:i].max()<=top+0.1*a) and (l[i-prevN:i].min()>=bot-0.1*a)): continue
        if h[i]>top+0.1*a and c[i]<top+0.5*a: s=-1
        elif l[i]<bot-0.1*a and c[i]>bot-0.5*a: s=1
        else: continue
        if not any(bot<c[k]<top for k in range(i+1,i+4)): continue
        ev.append((i,s,top,bot)); last=i
    return ev
marks=[(pos[r.pokeTs],-1 if r.pokeDir=='UP' else 1) for _,r in m.iterrows()]
def recall(ev,tol=6): return np.mean([any(abs(e[0]-pi)<=tol and e[1]==ps for e in ev) for pi,ps in marks])
months=(t[-1]-t[0])/(30.44*86400); res=[]
for N in (16,24,36,48):
    for (qlo,qhi) in ((0.05,0.95),(0.02,0.98),(0.0,1.0)):
        for Hcap in (3.0,4.0,5.0):
            for prevN in (0,1):
                ev=detect(N,qlo,qhi,Hcap,prevN); res.append((N,qlo,qhi,Hcap,prevN,recall(ev),len(ev)/months))
r=pd.DataFrame(res,columns=['N','qlo','qhi','Hcap','prevIn','recall','ev_per_month']).sort_values('recall',ascending=False)
pd.set_option('display.width',160); print(r.head(14).to_string(index=False)); print('...'); print(r[r.recall>=0.5].sort_values('ev_per_month').head(6).to_string(index=False))
