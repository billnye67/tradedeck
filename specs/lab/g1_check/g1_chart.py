"""Draw G1's levels (computer-drawn, prior 5 days only) on gold 15m for the user's screenshot window, from fresh Dukascopy data."""
import os, sys, datetime as dt, concurrent.futures as cf, numpy as np, pandas as pd, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools'); import dl_dukascopy as DK
HERE = os.path.dirname(os.path.abspath(__file__))
days = [dt.date(2026, 9, 1) + dt.timedelta(n) for n in range(45)]
DK_pt = 1e3
def day(d):
    rows = DK.day('XAUUSD', d)
    return [(t, o * 1e5 / DK_pt, h * 1e5 / DK_pt, l * 1e5 / DK_pt, c * 1e5 / DK_pt) for t, o, h, l, c in rows]
with cf.ThreadPoolExecutor(16) as ex: res = list(ex.map(day, days))
d = pd.DataFrame([r for rr in res for r in rr], columns=['t', 'o', 'h', 'l', 'c']).sort_values('t'); d.index = pd.to_datetime(d.t, unit='ms', utc=True)
print('1m bars', len(d), d.index.min(), d.index.max(), 'last close', d.c.iloc[-1])
m = d.resample('15min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
D = m.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
tr = np.maximum(D.h - D.l, np.maximum((D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs())); atr = tr.rolling(14).mean().shift(1)
fig, ax = plt.subplots(figsize=(16, 7)); w0 = pd.Timestamp('2026-09-27', tz='UTC'); w1 = pd.Timestamp('2026-10-14', tz='UTC')
v = m[(m.index >= w0) & (m.index < w1)]; ax.plot(v.index, v.c, color='#3b6cff', lw=1)
for dd in D.index:
    if dd < w0 or dd >= w1 or not atr.get(dd, np.nan) > 0: continue
    A = atr[dd]; hist = m.c[(m.index >= dd - pd.Timedelta(days=5)) & (m.index < dd)].values
    if len(hist) < 100: continue
    wb = 0.1 * A; b = np.floor(hist / wb).astype(int); vals, cnt = np.unique(b, return_counts=True); L = (vals[cnt.argmax()] + 0.5) * wb
    ax.hlines(L, dd, dd + pd.Timedelta(days=1), color='orange', lw=2); ax.text(dd, L + 3, f'{L:,.0f}', color='orange', fontsize=8)
    day_ = m[(m.index >= dd) & (m.index < dd + pd.Timedelta(days=1))]; broke = {1: False, -1: False}; done = {1: False, -1: False}
    for i, (ts, r) in enumerate(day_.iterrows()):
        for side in (-1, 1):
            if done[side]: continue
            if not broke[side]:
                if (r.c < L - 0.5 * A) if side == -1 else (r.c > L + 0.5 * A): broke[side] = True
                continue
            if (r.h >= L - 0.05 * A and r.c < L) if side == -1 else (r.l <= L + 0.05 * A and r.c > L):
                ax.scatter([ts], [r.c], marker='v' if side == -1 else '^', color='red' if side == -1 else 'lime', s=90, zorder=5); done[side] = True
ax.axhline(4175, color='green', lw=1, ls='--'); ax.text(w0, 4178, "the user's green line ~4,175", color='green')
ax.set_title('Gold 15m: orange = level the test drew each day (busiest price of the prior 5 days). Triangles = trades the test took.')
ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(os.path.join(HERE, 'g1_gold_check.png'), dpi=110); print('saved')
