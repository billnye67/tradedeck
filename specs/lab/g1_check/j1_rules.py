"""J1 = the user's 'respected level' setup, his words (2026-10-15): a level is live once price has turned there >= 2 times
(15m swing highs/lows, 4 bars each side, within 0.1 x daily ATR). Each later return: bar pokes into/through the level and
closes back on the side it came from -> trade away (from above = buy, from below = sell), next bar open, stop = past that
bar's wick (+0.02 ATR), target 2R and 3R, 48h max. Re-arm only after price has moved 0.5 ATR away from the level."""
import numpy as np, pandas as pd
NB = 4; REB = 0.5
def atr_daily(d):
    D = d.resample('1D').agg({'h':'max','l':'min','c':'last'}).dropna()
    tr = np.maximum(D.h-D.l, np.maximum((D.h-D.c.shift()).abs(), (D.l-D.c.shift()).abs())); return tr.rolling(14).mean().shift(1)
def signals(d, keep_days=10, min_touch=2):
    A_d = atr_daily(d); A = A_d.reindex(d.index.floor('D')).values
    o, h, l, c = d.o.values, d.h.values, d.l.values, d.c.values; n = len(d); t = d.index
    piv = []   # (confirm_index, price)
    for i in range(NB, n - NB):      # a turn counts only once price has moved REB x ATR away from it within 16 bars (a real rebound)
        a = A[i]
        if not a > 0: continue
        if h[i] == h[i-NB:i+NB+1].max():
            k = np.nonzero(l[i+1:i+17] <= h[i] - REB * a)[0]
            if len(k): piv.append((max(i + NB, i + 1 + k[0]), h[i], i))
        if l[i] == l[i-NB:i+NB+1].min():
            k = np.nonzero(h[i+1:i+17] >= l[i] + REB * a)[0]
            if len(k): piv.append((max(i + NB, i + 1 + k[0]), l[i], i))
    piv.sort(); levels = []; out = []; pj = 0; ALL = []
    for i in range(1, n - 1):
        a = A[i]
        if not a > 0: continue
        tol = 0.1 * a
        while pj < len(piv) and piv[pj][0] <= i:          # pivot confirmed: attach to a level or start one
            _, p, born = piv[pj]; pj += 1; hit = None
            for L in levels:
                if abs(L['p'] - p) <= tol: hit = L; break
            if hit: hit['n'] += 1; hit['p'] = (hit['p'] * (hit['n'] - 1) + p) / hit['n']; hit['last'] = t[born]
            else: levels.append(dict(p=p, n=1, last=t[born], since=t[born], armed=False, live_from=None, id=len(ALL))); ALL.append(levels[-1])
        levels = [L for L in levels if t[i] - L['last'] <= pd.Timedelta(days=keep_days)]
        for L in levels:
            if L['n'] < min_touch: continue
            if L['live_from'] is None: L['live_from'] = t[i]
            L['end'] = t[i]
            P = L['p']
            if not L['armed']:
                if abs(c[i] - P) >= 0.5 * a: L['armed'] = True; L['side_from'] = 1 if c[i] > P else -1
                continue
            frm = L['side_from']
            if frm == 1 and l[i] <= P + tol and c[i] > P - tol:      # came down from above, poked, closed back above-ish
                if c[i] > P: out.append(dict(lid=L['id'], i=i, side=1, level=P, stop=l[i] - 0.02 * a, t=t[i+1], entry=o[i+1], a=a)); L['armed'] = False
                elif c[i] < P - 0.5 * a: L['armed'] = False
            elif frm == -1 and h[i] >= P - tol and c[i] < P + tol:
                if c[i] < P: out.append(dict(lid=L['id'], i=i, side=-1, level=P, stop=h[i] + 0.02 * a, t=t[i+1], entry=o[i+1], a=a)); L['armed'] = False
            if L['armed'] and abs(c[i] - P) >= 0.5 * a: L['side_from'] = 1 if c[i] > P else -1
    return out, [L for L in ALL if L['live_from'] is not None]
