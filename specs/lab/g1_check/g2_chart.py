import numpy as np, pandas as pd, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as mdates
x = pd.read_parquet('xau_1m.parquet'); x.index = pd.to_datetime(x.t, unit='ms', utc=True)
d = x.resample('15min').agg({'o':'first','h':'max','l':'min','c':'last'}).dropna()
D = d.resample('1D').agg({'o':'first','h':'max','l':'min','c':'last'}).dropna()
tr = np.maximum(D.h-D.l, np.maximum((D.h-D.c.shift()).abs(), (D.l-D.c.shift()).abs())); atr = tr.rolling(14).mean().shift(1)
Hh = d.resample('1h').agg({'h':'max','l':'min'}).dropna()
pts = pd.concat([Hh.h[Hh.h==Hh.h.rolling(7,center=True).max()], Hh.l[Hh.l==Hh.l.rolling(7,center=True).min()]]).sort_index(); pts.index = pts.index + pd.Timedelta(hours=4)
w0, w1 = pd.Timestamp('2026-09-29', tz='UTC'), pd.Timestamp('2026-10-10', tz='UTC')
fig, ax = plt.subplots(figsize=(17, 8)); v = d[(d.index>=w0)&(d.index<w1)]; ax.plot(v.index, v.c, color='#3b6cff', lw=1.1)
ax.axhline(4175, color='green', lw=1.2, ls='--')
for k, dd in enumerate(D.index):
    if dd < w0 or dd >= w1 or k < 10: continue
    A = atr.iloc[k]; p = pts[(pts.index >= D.index[k-10]) & (pts.index < dd)].values; tol = 0.1*A
    grid = np.arange(p.min(), p.max()+tol/4, tol/4); cnt = (np.abs(p[None,:]-grid[:,None])<=tol).sum(1); levels=[]
    for j in np.argsort(-cnt, kind='stable'):
        if cnt[j] < 5 or len(levels)==3: break
        if all(abs(grid[j]-L0) > 2*tol for L0 in levels): levels.append(grid[j])
    day = d[(d.index>=dd)&(d.index<dd+pd.Timedelta(days=1))]
    for L in levels:
        ax.hlines(L, dd, dd+pd.Timedelta(days=1), color='orange', lw=1.8, alpha=.8)
        for side in (-1, 1):
            broke = False
            for ts, r in day.iterrows():
                if not broke: broke = (r.c < L-0.5*A) if side == -1 else (r.c > L+0.5*A); continue
                if (r.h >= L-0.05*A and r.c < L) if side == -1 else (r.l <= L+0.05*A and r.c > L):
                    nx = d[d.index>ts].iloc[0]; stop = L - side*0.25*A; R = abs(nx.o-stop); tgt = nx.o - side*-3*R if False else nx.o + side*3*R
                    fut = d[(d.index>ts)&(d.index<=ts+pd.Timedelta(hours=48))]; res = 'time out'
                    for _, f in fut.iterrows():
                        if (f.h >= stop) if side == -1 else (f.l <= stop): res = 'LOSS -1R'; break
                        if (f.l <= tgt) if side == -1 else (f.h >= tgt): res = 'WIN +3R'; break
                    col = 'red' if side == -1 else 'green'
                    ax.scatter([nx.name], [nx.o], marker='v' if side==-1 else '^', color=col, s=140, zorder=6, edgecolor='k')
                    ax.annotate(f"{'SELL' if side==-1 else 'BUY'} {nx.o:,.0f}\n{res}", (nx.name, nx.o), xytext=(8, 18 if side==-1 else -32), textcoords='offset points', fontsize=9, color=col, fontweight='bold')
                    break
ax.plot([], [], color='orange', lw=2, label='levels the computer found that day (prices gold turned at 5+ times in the past 10 days)')
ax.plot([], [], color='green', ls='--', label='your green line (~4,175)')
ax.legend(loc='upper right', fontsize=10); ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d')); ax.grid(alpha=.2)
ax.set_title('Your level setup, done right: gold 15m. Sell = price came back up to a level and got rejected. Buy = came down to a level and held.', fontsize=11)
fig.tight_layout(); fig.savefig(r'C:\Users\you\Desktop\gold_levels_v2.png', dpi=105)
