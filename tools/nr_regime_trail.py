"""NR (2026-11-20, the user): (A) does #1 do worse in shock / high-volatility / counter-trend markets ("is it the news?"),
(B) "once it hits 3R, trail the stop instead of taking profit". Pre-registered in specs/NR-regime-trail.md before this ran.
Trades = specs/coinflip/s1_trades_nopeek.csv as every registry row uses it (learn 2020-23, exam 2024-26). Candles = Binance 5m
monthly files in lab/flow/klines5m. Research only; no live change."""
import os, sys, glob, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import v2_build as V2
EXAM = 1704067200000
T = pd.read_csv(os.path.join(ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv')).sort_values('entryTs').reset_index(drop=True)
T['era'] = np.where(T.entryTs >= EXAM, 'exam', 'learn'); T['long'] = T.side == 1

def k5(coin):
    K = V2.read_csvs(sorted(glob.glob(os.path.join(V2.FLOW, 'klines5m', f'{coin}-5m-*.csv'))), ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tbq'])
    return K.astype(float).drop_duplicates('t').sort_values('t').reset_index(drop=True)
C = {m: k5(m) for m in T.market.unique()}

# ---------- A: market state at entry, measured on BTC daily closes known before the entry day
B = C['BTCUSDT']; D = B.assign(day=pd.to_datetime(B.t, unit='ms', utc=True).dt.floor('D')).groupby('day').c.last()
ret = D.pct_change()
vol7 = ret.rolling(7).std()
volpct = vol7.rolling(365, min_periods=180).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)   # today's 7d vol vs the past year
trend = D / D.shift(31) - 1
shock_days = ret[ret <= -0.08].index                                                             # BTC down 8%+ in one UTC day
in_shock = pd.Series(False, index=D.index)
for d in shock_days: in_shock[(in_shock.index > d) & (in_shock.index <= d + pd.Timedelta(days=14))] = True
S = pd.DataFrame({'volpct': volpct, 'trend': trend, 'shock': in_shock}).shift(1)                  # yesterday's values = known at entry
T['day'] = pd.to_datetime(T.entryTs, unit='ms', utc=True).dt.floor('D'); T = T.join(S, on='day')
T['vol'] = pd.cut(T.volpct, [-1, 0.8, 0.95, 2], labels=['normal (<80th pct)', 'high (80-95th)', 'extreme (95th+)'])
T['aligned'] = np.where(T.long, T.trend > 0, T.trend < 0)

def tab(g):
    return g.netR.agg(n='count', avgR='mean', totR='sum').assign(win=g.netR.apply(lambda x: (x > 0).mean())).round(3)
print(f'A. #1 trades by market state ({len(T)} rows, BTC shock days since 2020: {len(shock_days)})')
for col in ('vol', 'shock', 'aligned'):
    for era in ('learn', 'exam'):
        x = T[T.era == era]
        print(f'\n-- {col} / {era}'); print(tab(x.groupby(col, observed=True)).to_string())
        print('   longs only:'); print(tab(x[x.long].groupby(col, observed=True)).to_string())
print('\nshock days:', ', '.join(d.strftime('%Y-%m-%d') for d in shock_days))

# ---------- B: trail after 3R. Only trades whose gross result was exactly +3R change; everything else keeps its own result.
SWAP = 0.0005
def trail(r, lock, dist, cap_h=72):
    K = C[r.market]; risk = abs(r.entry - r.stop); s = 1 if r.long else -1
    i = int(np.searchsorted(K.t.values, r.exitTs, side='right'))       # bars strictly after the 3R touch bar
    stop = r.entry + s * lock * risk; best = r.entry + s * 3 * risk; end = min(len(K), i + cap_h * 12)
    for j in range(i, end):
        h, l = K.h.iat[j], K.l.iat[j]
        if (s == 1 and l <= stop) or (s == -1 and h >= stop): px, tj = stop, j; break
        best = max(best, h) if s == 1 else min(best, l)
        stop = max(stop, best - dist * risk) if s == 1 else min(stop, best + dist * risk)
    else:
        tj = end - 1; px = K.c.iat[tj]
    g = s * (px - r.entry) / risk
    nights = int((K.t.iat[tj] - 1800000) // 86400000 - (r.exitTs - 1800000) // 86400000)
    return g - r.feeR - nights * SWAP * r.entry / risk                 # same fees as the original trade + swaps for the extra nights

hit = T.grossR.round(3) == 3
print(f'\nB. trail after 3R: {hit.sum()} of {len(T)} trades reached 3R')
res = {'fixed 3R (live rule)': T.netR.copy()}
for name, lock, dist in (('T1 lock +2R, trail 1R', 2.0, 1.0), ('T2 lock +1.5R, trail 1.5R', 1.5, 1.5)):
    x = T.netR.copy(); x[hit] = [trail(r, lock, dist) for r in T[hit].itertuples()]; res[name] = x
for name, x in res.items():
    for era in ('learn', 'exam'):
        m = T.era == era; xs = x[m]; c = xs.cumsum(); dd = float((c.cummax() - c).max())
        w = x[m & hit]
        print(f'{name:28s} {era:5s} total {xs.sum():+7.1f}R avg {xs.mean():+.3f}R maxDD {dd:5.1f}R | the 3R trades avg {w.mean():+.2f}R, gave back below 3R {(w < T.netR[m & hit] - 1e-9).mean():.0%}')

if os.environ.get('NR_HOLD'):   # follow-up (the user 2026-11-20): hold time by vol bucket, info only
    T['hold_h'] = (T.exitTs - T.entryTs) / 3.6e6
    print('\nhold hours by vol bucket (median / share of trades lasting 12h+):')
    print(T.groupby(['era', 'vol'], observed=True).hold_h.agg(med='median', over12=lambda x: (x >= 12).mean(), n='count').round(2).to_string())
