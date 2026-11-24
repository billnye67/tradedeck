"""R13X the user retrace rule (pre-declared 2026-10-13): if the 15m bar that triggers already OPENED past E, don't chase.
Rest a limit order at E until the end of that kill zone (05:00 or 10:00 NY); fill at E only if price comes back; else no trade.
Clean touches fill at E as before. Same stop, 16:55 NY flat, Broker FX costs. Learn first; exam only if learn passes."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r13_midnight as R
EXAM = '--exam' in sys.argv
PAIRS = ('EURUSD', 'USDJPY', 'AUDUSD'); rows = []
for m in PAIRS:
    pip = L.pip_of(m); d = L.load_fx(m, '5m'); keys = sorted(set(d.date)); by = dict(tuple(d.groupby('date')))
    for i in range(1, len(keys)):
        dt = keys[i]; yr = pd.Timestamp(dt).year
        if not EXAM and yr >= 2020: continue
        if pd.Timestamp(dt).dayofweek > 4: continue
        for side in (1, -1):
            t = R.day_trade(by[keys[i - 1]], by[dt], side, pip, '15min')
            if t is None: continue
            b = t['b']; h, l, c, o, hr = b.h.values, b.l.values, b.c.values, b.o.values, b.hr.values
            ee = [k for k in range(t['i0'], len(h)) if hr[k] >= 16.9]; end = ee[0] if ee else len(h)
            q = t['q']
            if end <= q: continue
            s = -side; E = t['E']; risk = abs(E - t['stop']); stop = E - s * risk
            gap = (o[q] < E) if s == -1 else (o[q] > E)
            k = q
            if gap:
                kz_end = 5.0 if hr[q] < 5 else 10.0
                k = None
                for j in range(q, end):
                    if hr[j] >= kz_end: break
                    if (h[j] >= E) if s == -1 else (l[j] <= E): k = j; break
                if k is None:
                    rows.append(dict(m=m, date=dt, yr=yr, gap=True, filled=False, net=np.nan)); continue
            xp, _, how = L.walk(h[:end], l[:end], c[:end], hr[:end], k, s, E, stop, None)
            g = s * (xp - E) / risk
            rows.append(dict(m=m, date=dt, yr=yr, gap=gap, filled=True, net=g - L.cost_R('broker_fx', m, E, risk)))
T = pd.DataFrame(rows); F = T[T.filled]
for era, g in (('learn 2012-19', F[F.yr <= 2019]), ('exam 2020-26', F[F.yr >= 2020])):
    if len(g) == 0: continue
    cum = g.sort_values('date').net.cumsum(); dd = (cum.cummax() - cum).max()
    print(f"{era}: n={len(g)} net {g.net.mean():+.3f}R total {g.net.sum():+.0f}R ex-top5 {g.net.sort_values().iloc[:-5].sum():+.0f}R maxDD {dd:.0f}R | "
          f"came-back fills {g.gap.sum()} ({g[g.gap].net.mean():+.3f}R) clean {(~g.gap).sum()} ({g[~g.gap].net.mean():+.3f}R) | "
          + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
print('skipped (never came back):', int((~T.filled).sum()))
