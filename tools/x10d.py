"""X10-D: the user's weekly-trend counter-line break (specs/X10-hunt-round.md section D). 15m Binance BTC/ETH/XRP.
Learn 2020-2023 runs first; the exam block prints only if a cell passes learn (EXAM=1 env to open it, once)."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
LEARN_END = 1704067200000; EXAM_END = 1788000000000; WEEK = 7 * 86_400_000; DAY = 86_400_000
FEE, SLIP, SWAP = 3.0, 2.0, 5.0       # bp per side, bp per fill, bp per midnight held
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)

def load(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='binance_um' and market='{m}' and interval='15m' order by t", c)
    return d

def run(m):
    d = load(m); t = d.t.values; o, h, l, cl = d.o.values, d.h.values, d.l.values, d.c.values; n = len(d)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[cl[0], cl[:-1]]), abs(l - np.r_[cl[0], cl[:-1]])))
    atr = pd.Series(tr).rolling(14).mean().values
    # weeks start Monday 00:00 UTC (epoch was a Thursday -> offset 4 days)
    wk = (t + 3 * DAY) // WEEK
    wopen = pd.Series(o).groupby(wk).transform('first').values
    wclose = pd.Series(cl).groupby(wk).transform('last').values
    wk_o = pd.Series(o).groupby(wk).first(); wk_c = pd.Series(cl).groupby(wk).last()
    prev_dir = np.sign((wk_c - wk_o).shift(1)).reindex(wk).values      # last week's candle colour
    state = {'T1': np.where(cl < wopen, -1, np.where(cl > wopen, 1, 0)), 'T2': np.nan_to_num(prev_dir).astype(int)}
    lastbar_of_week = np.r_[wk[1:] != wk[:-1], True]
    # confirmed 3-bar fractals: index i known at close of i+1
    flo = np.r_[False, (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:]), False]
    fhi = np.r_[False, (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]), False]
    trades = []; raw = []
    for T in ('T1', 'T2'):
        st = state[T]
        for side in (-1, 1):   # -1 = short in down week, line under rising lows; +1 = long in up week, line over falling highs
            piv = flo if side == -1 else fhi; px = l if side == -1 else h
            a = b = -1; used = True; busy_until = {'E1': -1, 'E2': -1}
            for i in range(2, n):
                j = i - 1                                   # fractal at j-? confirmed at close of i
                k = i - 1
                if piv[k] and k >= 1:                       # fractal at k confirmed now (bar i closed)
                    a, b = b, k; used = False
                if a < 0 or used: continue
                rising = px[b] > px[a] if side == -1 else px[b] < px[a]
                if not rising or b - a < int(os.environ.get('GAP','4')): used = True; continue
                slope = (px[b] - px[a]) / (b - a)
                line_i = px[a] + slope * (i - a); line_p = px[a] + slope * (i - 1 - a)
                if i - 1 > b and ((side == -1 and cl[i - 1] < line_p) or (side == 1 and cl[i - 1] > line_p)): used = True; continue
                broke = cl[i] < line_i if side == -1 else cl[i] > line_i
                if not broke or i <= b: continue
                used = True
                if st[i] != side: continue
                if t[i] >= EXAM_END or np.isnan(atr[i]): continue
                E = cl[i]
                stop = h[a:i + 1].max() if side == -1 else l[a:i + 1].min()
                dist = abs(stop - E)
                if dist <= 0 or dist > 3 * atr[i]: continue
                era = 'learn' if t[i] < LEARN_END else 'exam'
                for z in (4, 16, 48):
                    if i + z < n: raw.append((T, era, m, z, side * (cl[i + z] / E - 1) * 1e4))
                for X in ('E1', 'E2'):
                    if i <= busy_until[X]: continue
                    tgt = E + side * 3 * dist; res = None
                    for q in range(i + 1, n):
                        hit_stop = h[q] >= stop if side == -1 else l[q] <= stop
                        hit_tgt = X == 'E1' and (l[q] <= tgt if side == -1 else h[q] >= tgt)
                        if hit_stop: res = (q, stop); break
                        if hit_tgt: res = (q, tgt); break
                        if X == 'E2' and lastbar_of_week[q]: res = (q, cl[q]); break
                        if X == 'E1' and q - i > 7 * 96: res = (q, cl[q]); break
                    if res is None: continue
                    q, xp = res; busy_until[X] = q
                    gross = side * (xp - E) / dist * -1 if False else (E - xp) / dist if side == -1 else (xp - E) / dist
                    nights = (t[q] // DAY) - (t[i] // DAY)
                    cost_bp = 2 * FEE + 2 * SLIP + SWAP * nights
                    net = gross - cost_bp * E / 1e4 / dist
                    trades.append(dict(T=T, X=X, era=era, m=m, side=side, t=t[i], tq=t[q], gross=gross, net=net, stop_bp=dist / E * 1e4))
        # control: signed forward move of every bar in the same state
        for side in (-1, 1):
            idx = np.where((st == side) & (t < EXAM_END))[0]
            for z in (4, 16, 48):
                ii = idx[idx + z < n]
                for era, msk in (('learn', t[ii] < LEARN_END), ('exam', t[ii] >= LEARN_END)):
                    v = side * (cl[ii[msk] + z] / cl[ii[msk]] - 1) * 1e4
                    raw.append((T, era, m + '#ctl', z, v.mean() if len(v) else np.nan))
    return trades, raw

allt, allr = [], []
for m in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
    tr_, rw = run(m); allt += tr_; allr += rw
T = pd.DataFrame(allt); R = pd.DataFrame(allr, columns=['T', 'era', 'm', 'z', 'v'])
show_exam = os.environ.get('EXAM') == '1'
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
P('# X10-D results — weekly-trend counter-line break, BTC/ETH/XRP 15m, strict costs')
for era in (['learn', 'exam'] if show_exam else ['learn']):
    P(f'\n## {era.upper()}')
    P('Raw look (bp, signed with the week): break vs every bar in the same week-state')
    for Tn in ('T1', 'T2'):
        r = R[(R['T'] == Tn) & (R.era == era)]
        brk = r[~r.m.str.contains('#ctl')].groupby('z').v.agg(['mean', 'count'])
        ctl = r[r.m.str.contains('#ctl')].groupby('z').v.mean()
        P(f'  {Tn}: ' + ' | '.join(f'{z*15//60 if z>=4 else z}h break {brk.loc[z,"mean"]:+.1f} (n={int(brk.loc[z,"count"])}) vs state {ctl.loc[z]:+.1f}' for z in (4, 16, 48)))
    P('Trades (R per trade; NET includes 3+3bp fee, 2bp slip per fill, 5bp per night):')
    for (Tn, X), g in T[T.era == era].groupby(['T', 'X']):
        g = g.sort_values('tq'); per = g.groupby('m').net.mean()
        P(f'  {Tn}/{X}: n={len(g):5d} gross {g.gross.mean():+.3f}R net {g.net.mean():+.3f}R win {100*(g.net>0).mean():.0f}% maxDD {dd(g.net.values):.0f}R '
          f'median stop {g.stop_bp.median():.0f}bp | ' + ' '.join(f'{k[:3]} {v:+.3f}' for k, v in per.items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X10D-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
