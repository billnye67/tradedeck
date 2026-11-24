"""X10-D2: the user's weekly-trend counter-line break with real bounce lines (specs/X10-hunt-round.md D2). EXAM=1 opens the exam once."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
LEARN_END = 1704067200000; EXAM_END = 1788000000000; WEEK = 7 * 86_400_000; DAY = 86_400_000
FEE, SLIP, SWAP = 3.0, 2.0, 5.0
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)

def run(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='binance_um' and market='{m}' and interval='15m' order by t", c)
    t = d.t.values; o, h, l, cl = d.o.values, d.h.values, d.l.values, d.c.values; n = len(d)
    pc = np.r_[cl[0], cl[:-1]]
    atr = pd.Series(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))).rolling(14).mean().values
    wk = (t + 3 * DAY) // WEEK
    wopen = pd.Series(o).groupby(wk).transform('first').values
    wk_o = pd.Series(o).groupby(wk).first(); wk_c = pd.Series(cl).groupby(wk).last()
    prev_dir = np.nan_to_num(np.sign((wk_c - wk_o).shift(1)).reindex(wk).values).astype(int)
    state = {'T1': np.where(cl < wopen, -1, np.where(cl > wopen, 1, 0)), 'T2': prev_dir}
    lastbar = np.r_[wk[1:] != wk[:-1], True]
    trades, raw = [], []
    for side in (-1, 1):
        # "short" orientation; for longs flip prices so the up-week case reuses the same code
        if side == -1: L, H, C = l, h, cl
        else: L, H, C = -h, -l, -cl
        frac = np.r_[False, (L[1:-1] < L[:-2]) & (L[1:-1] < L[2:]), False]
        rollmin = pd.Series(L).rolling(97).min().values
        sigs = []
        A = -1; B = -1; done = True
        for i in range(2, n):
            k = i - 1
            if frac[k]:
                if rollmin[k] == L[k]: A, B, done = k, -1, False
                elif not done and A >= 0 and L[k] > L[A] and k - A >= 16:
                    sl = (L[k] - L[A]) / (k - A); seg = np.arange(A + 1, k)
                    if (L[seg] >= L[A] + sl * (seg - A) - 1e-12).all(): B = k
            if done or B < 0 or i <= B: continue
            line = L[A] + (L[B] - L[A]) / (B - A) * (i - A)
            if C[i] < line:
                done = True
                hi = H[A:i + 1].max()
                if np.isnan(atr[i]) or hi - L[A] < 3 * atr[i]: continue
                dist = hi - C[i]
                if dist <= 0 or dist > 4 * atr[i] or t[i] >= EXAM_END: continue
                sigs.append((i, dist))
        for Tn in ('T1', 'T2'):
            st = state[Tn]
            for X in ('E1', 'E2'):
                busy = -1
                for i, dist in sigs:
                    if st[i] != side or i <= busy: continue
                    E = cl[i]; stop = E - side * dist; tgt = E + side * 3 * dist; res = None
                    for q in range(i + 1, n):
                        if (h[q] >= stop) if side == -1 else (l[q] <= stop): res = (q, stop); break
                        if X == 'E1' and ((l[q] <= tgt) if side == -1 else (h[q] >= tgt)): res = (q, tgt); break
                        if X == 'E2' and lastbar[q]: res = (q, cl[q]); break
                        if X == 'E1' and q - i > 7 * 96: res = (q, cl[q]); break
                    if res is None: continue
                    q, xp = res; busy = q
                    gross = side * (xp - E) / dist
                    cost = (2 * FEE + 2 * SLIP + SWAP * (t[q] // DAY - t[i] // DAY)) * E / 1e4 / dist
                    trades.append(dict(T=Tn, X=X, era='learn' if t[i] < LEARN_END else 'exam', m=m, t=t[i], tq=t[q], gross=gross, net=gross - cost, stop_bp=dist / E * 1e4))
            for i, dist in sigs:
                if st[i] != side: continue
                for z in (4, 16, 48):
                    if i + z < n: raw.append((Tn, 'learn' if t[i] < LEARN_END else 'exam', m, z, side * (cl[i + z] / cl[i] - 1) * 1e4))
            idx = np.where((st == side) & (t < EXAM_END))[0]
            for z in (4, 16, 48):
                ii = idx[idx + z < n]
                for era, msk in (('learn', t[ii] < LEARN_END), ('exam', t[ii] >= LEARN_END)):
                    v = side * (cl[ii[msk] + z] / cl[ii[msk]] - 1) * 1e4
                    raw.append((Tn, era, m + '#ctl', z, v.mean()))
    return trades, raw

allt, allr = [], []
for m in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
    a, b = run(m); allt += a; allr += b
T = pd.DataFrame(allt); R = pd.DataFrame(allr, columns=['T', 'era', 'm', 'z', 'v'])
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
P('# X10-D2 results — weekly-trend counter-line break, the user-sized bounce lines, BTC/ETH/XRP 15m, strict costs')
for era in (['learn', 'exam'] if os.environ.get('EXAM') == '1' else ['learn']):
    P(f'\n## {era.upper()}'); P('Raw look (bp, signed with the week): break vs every bar in the same week-state')
    for Tn in ('T1', 'T2'):
        r = R[(R['T'] == Tn) & (R.era == era)]; b = r[~r.m.str.contains('#ctl')]; ct = r[r.m.str.contains('#ctl')].groupby('z').v.mean()
        P(f'  {Tn}: ' + ' | '.join(f'{z//4}h break {b[b.z==z].v.mean():+.1f} (n={len(b[b.z==z])}) vs state {ct.loc[z]:+.1f}' for z in (4, 16, 48)))
    P('Trades (R/trade, NET = 3+3bp fee, 2bp slip per fill, 5bp/night):')
    for (Tn, X), g in T[T.era == era].groupby(['T', 'X']):
        g = g.sort_values('tq'); per = g.groupby('m').net.mean(); yr = g.groupby(pd.to_datetime(g.t, unit='ms').dt.year).net.sum()
        P(f'  {Tn}/{X}: n={len(g):4d} gross {g.gross.mean():+.3f}R net {g.net.mean():+.3f}R win {100*(g.net>0).mean():.0f}% maxDD {dd(g.net.values):.0f}R '
          f'stop {g.stop_bp.median():.0f}bp | ' + ' '.join(f'{k[:3]} {v:+.3f}' for k, v in per.items()) + ' | years ' + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X10D2-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
