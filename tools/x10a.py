"""X10-A: SMT raid (specs/X10-hunt-round.md A). 1h raid of the prior-24h low/high that closes back inside; SMT = the reference coin did NOT
break its own prior-24h extreme in the same bar. Entry A2 = reclaim close; A1 = 15m displacement gap after the reclaim, limit at the gap.
Stop = sweep extreme, 3R, 40h. Control = same raids where the reference coin also broke. EXAM=1 opens the exam once."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
LEARN_END = 1704067200000; EXAM_END = 1788000000000; DAY = 86_400_000; HOUR = 3_600_000
FEE, SLIP, SWAP = 3.0, 2.0, 5.0
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)
def q15(m): return pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='binance_um' and market='{m}' and interval='15m' order by t", c).set_index('t')
D = {m: q15(m) for m in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT')}
def hourly(d):
    g = d.groupby(d.index // HOUR * HOUR)
    return pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last(), 'n': g.size()})
Hh = {m: hourly(d) for m, d in D.items()}

def sim(d15, i0, side, E, stop, slip_entry=True):
    """walk 15m bars from position i0 (first bar after entry); returns (gross R, exit ts)"""
    dist = abs(E - stop); tgt = E + side * 3 * dist
    h, l, cl, t = d15.h.values, d15.l.values, d15.c.values, d15.index.values
    end = min(len(t), i0 + 160)
    for q in range(i0, end):
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return -1.0, t[q]
        if (h[q] >= tgt) if side == 1 else (l[q] <= tgt): return 3.0, t[q]
    q = end - 1; return side * (cl[q] - E) / dist, t[q]

rows, raw = [], []
for coin, ref in (('BTCUSDT', 'ETHUSDT'), ('ETHUSDT', 'BTCUSDT'), ('XRPUSDT', 'BTCUSDT')):
    a, b = Hh[coin], Hh[ref]; j = a.join(b, rsuffix='_r', how='inner'); j = j[(j.n == 4) & (j.n_r == 4)]
    j = j.reindex(range(j.index[0], j.index[-1] + HOUR, HOUR))
    for side in (1, -1):                         # 1 = long after a low sweep, -1 = short after a high sweep
        if side == 1:
            lvl = j.l.shift(1).rolling(24, min_periods=24).min(); lvl_r = j.l_r.shift(1).rolling(24, min_periods=24).min()
            broke = j.l < lvl; reclaim = j.c > lvl; ref_broke = j.l_r < lvl_r; ext = j.l
        else:
            lvl = j.h.shift(1).rolling(24, min_periods=24).max(); lvl_r = j.h_r.shift(1).rolling(24, min_periods=24).max()
            broke = j.h > lvl; reclaim = j.c < lvl; ref_broke = j.h_r > lvl_r; ext = j.h
        ev = j[broke & reclaim & lvl.notna() & lvl_r.notna() & (j.index < EXAM_END)]
        d15 = D[coin]; idx15 = d15.index.values
        for ts, r in ev.iterrows():
            smt = not bool(ref_broke.loc[ts]); era = 'learn' if ts < LEARN_END else 'exam'
            E = r.c; stop = ext.loc[ts]; dist = abs(E - stop)
            if dist <= 0 or dist / E > 0.04: continue
            i0 = np.searchsorted(idx15, ts + HOUR)          # first 15m bar after the 1h close
            for z in (4, 12, 24):
                k = j.index.get_indexer([ts + z * HOUR])[0]
                if k >= 0 and j.c.iloc[k] == j.c.iloc[k]: raw.append((coin, era, smt, z, side * (j.c.iloc[k] / E - 1) * 1e4))
            # A2: market at the reclaim close
            g, tx = sim(d15, i0, side, E, stop)
            cost = (2 * FEE + 2 * SLIP + SWAP * (tx // DAY - ts // DAY)) * E / 1e4 / dist
            rows.append(dict(cell='A2', coin=coin, era=era, smt=smt, t=ts, tx=tx, gross=g, net=g - cost))
            # A1: 15m displacement gap within the next 8 bars, limit at its near edge, fill within 36 bars
            h, l, cl, o = d15.h.values, d15.l.values, d15.c.values, d15.o.values
            rng = pd.Series(h - l)
            for k in range(i0 + 2, min(i0 + 8, len(h))):
                gap = (l[k] > h[k - 2]) if side == 1 else (h[k] < l[k - 2])
                big = (h[k - 1] - l[k - 1]) >= 1.5 * np.mean(h[k - 21:k - 1] - l[k - 21:k - 1])
                if not (gap and big): continue
                near = l[k] if side == 1 else h[k]; far = h[k - 2] if side == 1 else l[k - 2]
                if (near - stop) * side <= 0: break
                fill = None
                for q in range(k + 1, min(k + 37, len(h))):
                    if (l[q] <= stop) if side == 1 else (h[q] >= stop): break
                    if (l[q] <= near) if side == 1 else (h[q] >= near): fill = q; break
                    if (cl[q] < far) if side == 1 else (cl[q] > far): break
                if fill is None: break
                if (l[fill] <= stop) if side == 1 else (h[fill] >= stop): g, tx = -1.0, idx15[fill]
                else: g, tx = sim(d15, fill + 1, side, near, stop)
                d2 = abs(near - stop); cost = (2 * FEE + 2 * SLIP + SWAP * (tx // DAY - idx15[fill] // DAY)) * near / 1e4 / d2
                rows.append(dict(cell='A1', coin=coin, era=era, smt=smt, t=idx15[fill], tx=tx, gross=g, net=g - cost))
                break

T = pd.DataFrame(rows); R = pd.DataFrame(raw, columns=['coin', 'era', 'smt', 'z', 'v'])
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
def onepos(g):  # one open trade per coin per cell
    keep, busy = [], {}
    for r in g.sort_values('t').itertuples():
        if r.t <= busy.get(r.coin, -1): continue
        busy[r.coin] = r.tx; keep.append(r.Index)
    return g.loc[keep]
P('# X10-A results — SMT raid (1h sweep of the prior-24h extreme that closes back inside; reference coin held), BTC/ETH/XRP, strict costs')
for era in (['learn', 'exam'] if os.environ.get('EXAM') == '1' else ['learn']):
    P(f'\n## {era.upper()}'); P('Raw look, bp in the trade direction from the reclaim close (SMT = reference held | control = reference also broke):')
    r = R[R.era == era]
    for z in (4, 12, 24):
        s1 = r[(r.z == z) & r.smt].v; s0 = r[(r.z == z) & ~r.smt].v
        P(f'  {z:2d}h: SMT {s1.mean():+.1f} (n={len(s1)}) | control {s0.mean():+.1f} (n={len(s0)})')
    P('Trades (R/trade, NET = 3+3bp fee, 2bp slip per fill, 5bp/night; one open trade per coin):')
    for (cell, smt), g in T[T.era == era].groupby(['cell', 'smt']):
        g = onepos(g).sort_values('tx'); per = g.groupby('coin').net.mean(); yr = g.groupby(pd.to_datetime(g.t, unit='ms').dt.year).net.sum()
        P(f"  {cell} {'SMT    ' if smt else 'control'}: n={len(g):5d} gross {g.gross.mean():+.3f}R net {g.net.mean():+.3f}R win {100*(g.net>0).mean():.0f}% maxDD {dd(g.net.values):.0f}R | "
          + ' '.join(f'{k[:3]} {v:+.3f}' for k, v in per.items()) + ' | years ' + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X10A-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
