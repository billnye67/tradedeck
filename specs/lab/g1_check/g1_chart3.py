"""G1 spot-check on the user's gold window: Yahoo GC=F 15m (futures), shifted to spot using the median gap vs our 'metals' spot feed."""
import json, os, sqlite3, numpy as np, pandas as pd, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as mdates
HERE = os.path.dirname(os.path.abspath(__file__))
x = pd.read_parquet(os.path.join(HERE, 'xau_1m.parquet')); x.index = pd.to_datetime(x.t, unit='ms', utc=True)
m = x.resample('15min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna(); print('15m bars', len(m), m.index.min(), m.index.max(), 'last', m.c.iloc[-1])
D = m.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
tr = np.maximum(D.h - D.l, np.maximum((D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs())); atr = tr.rolling(14).mean().shift(1)
w0, w1 = pd.Timestamp('2026-09-27', tz='UTC'), pd.Timestamp('2026-10-14', tz='UTC'); v = m[(m.index >= w0) & (m.index < w1)]
fig, ax = plt.subplots(figsize=(16, 7)); ax.plot(v.index, v.c, color='#3b6cff', lw=1)
for dd in D.index:
    if dd < w0 or dd >= w1 or not atr.get(dd, np.nan) > 0: continue
    A = atr[dd]; hist = m.c[(m.index >= dd - pd.Timedelta(days=5)) & (m.index < dd)].values
    if len(hist) < 100: continue
    wb = 0.1 * A; b = np.floor(hist / wb).astype(int); vals, cnt = np.unique(b, return_counts=True); L = (vals[cnt.argmax()] + 0.5) * wb
    ax.hlines(L, dd, dd + pd.Timedelta(days=1), color='orange', lw=2.5); ax.text(dd + pd.Timedelta(hours=2), L + 4, f'{L:,.0f}', color='darkorange', fontsize=9)
    day_ = m[(m.index >= dd) & (m.index < dd + pd.Timedelta(days=1))]; broke = {1: False, -1: False}; done = {1: False, -1: False}
    for ts, x in day_.iterrows():
        for side in (-1, 1):
            if done[side]: continue
            if not broke[side]:
                if (x.c < L - 0.5 * A) if side == -1 else (x.c > L + 0.5 * A): broke[side] = True
                continue
            if (x.h >= L - 0.05 * A and x.c < L) if side == -1 else (x.l <= L + 0.05 * A and x.c > L):
                ax.scatter([ts], [x.c], marker='v' if side == -1 else '^', color='red' if side == -1 else 'green', s=110, zorder=5); done[side] = True
                print(dd.date(), 'SHORT' if side == -1 else 'LONG', ts, round(x.c, 1), 'level', round(L, 1))
ax.axhline(4175, color='green', lw=1, ls='--', label="the user's green line (~4,175)")
ax.plot([], [], color='orange', lw=2.5, label="computer's level each day (busiest price of the prior 5 days)")
ax.scatter([], [], marker='v', color='red', label='test took a short'); ax.scatter([], [], marker='^', color='green', label='test took a long')
ax.legend(loc='upper right'); ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d')); ax.grid(alpha=.2)
ax.set_title('Gold 15m, Sep 27 - Oct 13 2026 (Dukascopy spot)'); fig.tight_layout(); fig.savefig(os.path.join(HERE, 'g1_gold_check.png'), dpi=110)
