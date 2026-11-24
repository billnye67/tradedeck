"""Is the user's pick visible BEFORE the move? Features known at the decision bar for marked vs unmarked BTC events; a simple score fitted on BTC,
tested on ETH/XRP which he never saw."""
import json, numpy as np, pandas as pd
d=pd.read_csv('blind_events.csv')
# add decision-time features
feats={}
for coin in ('BTC','ETH','XRP'):
    b=pd.DataFrame(json.load(open(rf'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\{coin}-60m.json')),columns=['t','o','h','l','c']); b['ts']=b.t*1000; b=b.set_index('ts')
    pc=b.c.shift(1); tr=np.maximum(b.h-b.l,np.maximum((b.h-pc).abs(),(b.l-pc).abs())); b['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    pos={v:i for i,v in enumerate(b.index.values)}; c=b.c.values; h=b.h.values; l=b.l.values; atr=b.atr.values
    for idx,r in d[d.coin==coin].iterrows():
        j=pos[r.ts]; i=j-1
        while i>j-4 and not ((h[i]>c[i-36:i].max()) or (l[i]<c[i-36:i].min())): i-=1   # approx poke bar
        a=atr[j]
        trend=(c[i-36]/c[i-144]-1)*1e4 if i>=144 else np.nan                     # move into the box over the prior 4.5 days
        # did the poke take out a swing high/low from before the box (liquidity)?
        pre_h=h[max(0,i-300):i-36]; pre_l=l[max(0,i-300):i-36]
        took=(h[i]>pre_h.max()) if r.side==-1 else (l[i]<pre_l.min()) if len(pre_h) else False
        vol_ratio=np.nan
        rng36=(c[i-36:i].max()-c[i-36:i].min())/a
        feats[idx]=dict(trend_in=trend,took_liq=bool(took),hour=pd.Timestamp(r.ts,unit='ms').hour,rng36=rng36,
                        with_trend=(np.sign(trend)==r.side) if trend==trend else np.nan)
f=pd.DataFrame(feats).T; d=d.join(f)
btc=d[d.coin=='BTC']; mk=btc[btc.marked]; um=btc[~btc.marked]
print("decision-time features, BTC: marked (n=%d) vs unmarked (n=%d)"%(len(mk),len(um)))
for col in ('H','depth','closeOut','trend_in','took_liq','with_trend','rng36'):
    print(f"  {col:11s} marked {pd.to_numeric(mk[col]).mean():+8.2f}   unmarked {pd.to_numeric(um[col]).mean():+8.2f}")
print("  side        marked short %.0f%%   unmarked short %.0f%%"%((mk.side==-1).mean()*100,(um.side==-1).mean()*100))
# simple score: standardized features with sign set by marked-vs-unmarked difference on BTC; apply to ETH/XRP
cols=['H','depth','trend_in','rng36']; X=d[cols].apply(pd.to_numeric); mu=X[d.coin=='BTC'].mean(); sd=X[d.coin=='BTC'].std()
Z=(X-mu)/sd; w={c_: np.sign(pd.to_numeric(mk[c_]).mean()-pd.to_numeric(um[c_]).mean()) for c_ in cols}
d['score']=sum(Z[c_]*w[c_] for c_ in cols)+1.0*(d.closeOut.astype(float)*(1 if mk.closeOut.mean()>um.closeOut.mean() else -1))+1.0*(d.took_liq.astype(float)*(1 if mk.took_liq.mean()>um.took_liq.mean() else -1))
print("\nweights (sign of marked-minus-unmarked on BTC):",w,"closeOut",'+' if mk.closeOut.mean()>um.closeOut.mean() else '-',"took_liq",'+' if mk.took_liq.mean()>um.took_liq.mean() else '-')
print("BTC sanity: mean score marked %.2f vs unmarked %.2f"%(mk.index.map(d.score).mean() if False else d.loc[mk.index,'score'].mean(), d.loc[um.index,'score'].mean()))
oos=d[d.coin!='BTC'].copy(); oos['q']=pd.qcut(oos.score,4,labels=['Q1 least the user-like','Q2','Q3','Q4 most the user-like'])
print("\nOUT OF SAMPLE (ETH+XRP, never marked): forward move against the poke by the user-likeness quartile, mean bp (n)")
print(oos.groupby('q')[['f8','f24','f48','f96']].mean().round(0).join(oos.groupby('q').size().rename('n')).to_string())
