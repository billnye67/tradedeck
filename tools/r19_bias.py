"""R19: does ICT's daily-bias rule predict the day? (specs/ict-rebuild/R19-daily-bias.md). LEARN unless EXAM=1.
Rule (2022 Ep 10 S9ORTYmXwdE t=4604-4846 + t=190-1409): daily dealing range = most recent confirmed 3-candle swing low to the
highest high since; if the prior day traded to <= 50% of that range (and did not close below the swing low) -> today bullish.
Mirror: swing high to the lowest low since, prior day traded to >= 50% -> bearish. Both or neither -> no bias.
Scored on today: expansion above the midnight open vs below it (his question), and whether today takes the prior day's high
(bullish) / low (bearish)."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r17_htf as H
EXAM = os.environ.get('EXAM') == '1'

def days(d, kind):
    """per day: midnight open, day high/low (00:00-16:00 NY for index/stocks-ish; full NY day for fx/crypto), daily OHLC for structure."""
    x = d.copy(); x['td'] = x.date
    if kind == 'index': x = x[x.hr < 16]
    g = x.groupby('td').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    return g[g.n >= 20]

def bias(D):
    h, l, c = D.h.values, D.l.values, D.c.values; n = len(D); out = np.zeros(n, int)
    for t in range(4, n):
        y = t - 1                                                       # use data up to yesterday's close
        sl = [k for k in range(1, y) if l[k] < l[k - 1] and l[k] < l[k + 1]]
        sh = [k for k in range(1, y) if h[k] > h[k - 1] and h[k] > h[k + 1]]
        b = 0
        if sl:
            k = sl[-1]; hi = h[k:y + 1].max(); eq = (l[k] + hi) / 2
            if hi > l[k] and l[y] <= eq and c[y] > l[k] and h[k:y + 1].argmax() + k < y: b += 1
        if sh:
            k = sh[-1]; lo = l[k:y + 1].min(); eq = (h[k] + lo) / 2
            if lo < h[k] and h[y] >= eq and c[y] < h[k] and l[k:y + 1].argmin() + k < y: b -= 1
        out[t] = b
    return out

def score(m, d, kind, learn_end):
    D = days(d, kind); D = D[pd.to_datetime(D.index).year <= (2026 if EXAM else learn_end)]
    b = bias(D); o, h, l = D.o.values, D.h.values, D.l.values
    up = h - o; dn = o - l; rows = []
    for t in range(4, len(D)):
        if b[t] == 0: continue
        rows.append(dict(m=m, kind=kind, date=D.index[t], b=b[t], hit=int((up[t] > dn[t]) == (b[t] == 1)),
                         takes=int(h[t] > h[t - 1]) if b[t] == 1 else int(l[t] < l[t - 1]),
                         takes_other=int(l[t] < l[t - 1]) if b[t] == 1 else int(h[t] > h[t - 1])))
    base = np.mean(up[4:] > dn[4:])
    return rows, base

if __name__ == '__main__':
    R = []; bases = []
    for m in ('NQ', 'ES'):
        r, bs = score(m, L.load_cme_ict(m, '5min'), 'index', L.CME_LEARN_END); R += r; bases.append((m, bs))
    for m in L.FX_PAIRS:
        r, bs = score(m, L.load_fx(m, '15m'), 'fx', L.FX_LEARN_END); R += r; bases.append((m, bs))
    for m in ('BTCUSDT', 'ETHUSDT'):
        r, bs = score(m, H.load('binance_um', m), 'crypto', L.CRYPTO_LEARN_END); R += r; bases.append((m, bs))
    r, bs = score('XAUUSD', H.load('metals', 'XAUUSD'), 'fx', 2023); R += r; bases.append(('XAUUSD', bs))
    T = pd.DataFrame(R)
    lines = ['# R19 daily bias rule' + (' EXAM' if EXAM else ' learn'), 'hit = day expanded further in the bias direction than against it (from the midnight open); coin = 50%']
    for m, g in T.groupby('m', sort=False):
        lines.append(f"{m}: bias days {len(g)} (bull {int((g.b == 1).sum())} / bear {int((g.b == -1).sum())}) hit {g.hit.mean():.1%} | bull-hit {g[g.b == 1].hit.mean():.1%} bear-hit {g[g.b == -1].hit.mean():.1%} | takes PDH/PDL in bias dir {g.takes.mean():.1%} vs other side {g.takes_other.mean():.1%} | base up-day share {dict(bases)[m]:.1%}")
    lines.append(f"ALL: n={len(T)} hit {T.hit.mean():.1%}")
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R19-results' + ('-exam' if EXAM else '-learn') + '.md')
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
