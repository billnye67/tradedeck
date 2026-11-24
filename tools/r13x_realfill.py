"""R13X with realistic fills: when the 15m bar that 'touches' E already OPENED beyond E (gap / price already through), the
order fills at that bar's open, not at E (the backtest's stale price). Stop stays where it was (E + risk), so risk grows."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r13_midnight as R
PAIRS = ('EURUSD', 'USDJPY', 'AUDUSD'); rows = []
for m in PAIRS:
    pip = L.pip_of(m); d = L.load_fx(m, '5m'); keys = sorted(set(d.date)); by = dict(tuple(d.groupby('date')))
    for i in range(1, len(keys)):
        dt = keys[i]
        if pd.Timestamp(dt).dayofweek > 4: continue
        for side in (1, -1):
            t = R.day_trade(by[keys[i - 1]], by[dt], side, pip, '15min')
            if t is None: continue
            b = t['b']; h, l, c, o, hr = b.h.values, b.l.values, b.c.values, b.o.values, b.hr.values
            ee = [k for k in range(t['i0'], len(h)) if hr[k] >= 16.9]; end = ee[0] if ee else len(h)
            if end <= t['q']: continue
            s = -side; E = t['E']; risk0 = abs(E - t['stop']); stop = E - s * risk0
            q = t['q']
            gap = (o[q] < E) if s == -1 else (o[q] > E)                # we sell (s=-1): bar opened below E already
            fill = o[q] if gap else E
            risk = abs(fill - stop)
            for name, F, rk in (('stale', E, risk0), ('real', fill, risk)):
                xp, _, how = L.walk(h[:end], l[:end], c[:end], hr[:end], q, s, F, stop, None)
                g = s * (xp - F) / rk
                rows.append(dict(m=m, date=dt, yr=pd.Timestamp(dt).year, fillmode=name, gap=gap, gross=g, net=g - L.cost_R('broker_fx', m, F, rk), slip_pips=abs(fill - E) / pip))
T = pd.DataFrame(rows)
for era, g0 in (('learn 2012-19', T[T.yr <= 2019]), ('exam 2020-26', T[T.yr >= 2020])):
    for fm, g in g0.groupby('fillmode'):
        print(f"{era} {fm:5s}: n={len(g)} net {g.net.mean():+.3f}R ex5 {g.net.sort_values().iloc[:-5].mean():+.3f} | gap trades {g.gap.mean():.0%} avg gap {g[g.gap].slip_pips.mean():.1f} pips | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
T.to_csv(os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R13X-realfill.csv'), index=False)
