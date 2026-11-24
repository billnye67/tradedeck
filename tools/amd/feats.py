"""What do the user's 55 boxes look like, mechanically? Features per mark from the 1h/15m bars he drew on."""
import json, numpy as np, pandas as pd
m=pd.DataFrame(json.load(open(r'C:\Users\you\Desktop\TradeDeck\reviews\amd-marks.json')))
rows=[]
for tf in (60,15):
    d=pd.DataFrame(json.load(open(rf'C:\Users\you\Desktop\TradeDeck\reviews\amd-data\BTC-{tf}m.json')),columns=['t','o','h','l','c']).set_index('t')
    pc=d.c.shift(1); tr=np.maximum(d.h-d.l,np.maximum((d.h-pc).abs(),(d.l-pc).abs())); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    for _,r in m[m.tf==tf].iterrows():
        box=d.loc[r.boxStartTs:r.boxEndTs]; pre=d.loc[:r.boxStartTs].iloc[-1]
        if len(box)<3: continue
        atr=d.atr.loc[r.boxStartTs]; H=r.top-r.bot
        # how well his lines sit on the data: fraction of bars whose close is inside; actual high/low of the window
        inside=((box.c<=r.top)&(box.c>=r.bot)).mean(); hh=box.h.max(); ll=box.l.min()
        poke=d.loc[r.pokeTs]; pokeUp=r.pokeDir=='UP'
        depth=(poke.h-r.top)/atr if pokeUp else (r.bot-poke.l)/atr; closeOut=(poke.c>r.top) if pokeUp else (poke.c<r.bot)
        after=d.loc[r.pokeTs:].iloc[1:49]; back=np.where((after.c<r.top)&(after.c>r.bot))[0]; backBars=int(back[0])+1 if len(back) else None
        touches_top=((box.h>=r.top-0.1*H)).sum(); touches_bot=((box.l<=r.bot+0.1*H)).sum()
        # the move after the entry, signed AGAINST the poke (reversal), at 4/8/24/48 bars
        e=d.loc[r.entryTs:]; E=e.c.iloc[0]; s=-1 if pokeUp else 1
        fw={z:(s*(e.c.iloc[z]/E-1)*1e4 if len(e)>z else np.nan) for z in (4,8,24,48)}
        # pre-box context: move into the box over the previous 3x box length
        pre_n=min(len(box)*3,len(d.loc[:r.boxStartTs])-1); ctx=(d.c.loc[r.boxStartTs]/d.c.loc[:r.boxStartTs].iloc[-pre_n]-1)*1e4 if pre_n>5 else np.nan
        rows.append(dict(tf=tf,bars=len(box),H_atr=H/atr,H_bp=H/r.bot*1e4,inside=inside,top_err=(hh-r.top)/atr,bot_err=(r.bot-ll)/atr,ttop=touches_top,tbot=touches_bot,
                         pokeUp=pokeUp,depth_atr=depth,closeOut=bool(closeOut),backBars=backBars,ctx_bp=ctx,**{f'f{z}':v for z,v in fw.items()}))
f=pd.DataFrame(rows); pd.set_option('display.width',200)
print(f.describe().T[['count','mean','50%','min','max']].round(2))
print('\nclose outside vs wick only, mean move after (bp, signed toward the reversal):'); print(f.groupby('closeOut')[['f4','f8','f24','f48']].agg(['mean','count']).round(0))
print('\nby box length tercile:'); print(f.groupby(pd.qcut(f.bars,3))[['f24','f48']].mean().round(0))
print('\npoke direction:',f.pokeUp.value_counts().to_dict(),'| came back inside within 48 bars:',f.backBars.notna().mean().round(2),'median bars',f.backBars.median())
print('\ncontext into the box (bp over 3x box length):', f.ctx_bp.describe().round(0).to_dict())
f.to_csv('marks_features.csv',index=False)
