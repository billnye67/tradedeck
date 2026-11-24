"""X15d: queue ranks 30 (news), 31 (daily-range rebalance), 32 (immediate rebalance), 36 (wick CE), 37 (suspension block),
38 (lunch-reversal gap). Pre-registered in specs/X15-ict-rest.md (X15d). LEARN ERA ONLY: data truncated at load."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
HERE = os.path.dirname(os.path.abspath(__file__)); SPECS = os.path.join(HERE, '..', 'specs')
RNG = np.random.default_rng(15)

def load_gold():  # copied from tools/x14_sb.py (importing it runs that script)
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

# ---------------- calendar ----------------
def bls(fn):
    s = open(os.path.join(SPECS, 'hunt15-data', fn)).read().strip().split(',')
    return {pd.Timestamp(f'{x[4:]}-{x[:2]}-{x[2:4]}').date() for x in s}
NEWS830 = bls('empsit_dates.txt') | bls('cpi_dates.txt')
_f = pd.read_csv(os.path.join(SPECS, 'hunt15-data', 'fomc_statements_2012-2026.csv'))
_t = pd.to_datetime(_f.t0, utc=True).dt.tz_convert('America/New_York')
FOMC = {t.date(): t.hour + t.minute / 60 for t in _t}

# ---------------- generic walker ----------------
def sim(A, f, side, E, risk, tdist, flat_hr, follow=False):
    """Fill at bar f (stop-first on fill bar, target from next bar). Exit at stop/target/flat_hr/day end. follow: IR rule."""
    h, l, c, hr, d0 = A['h'], A['l'], A['c'], A['hr'], A['dc']
    stop = E - side * risk; tgt = None if tdist is None else E + side * tdist
    q = f; end = len(h); best = -1e18
    while q < end and d0[q] == d0[f]:
        if q > f and hr[q] >= flat_hr: return c[q - 1]
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop
        if q > f and tgt is not None and ((h[q] >= tgt) if side == 1 else (l[q] <= tgt)): return tgt
        best = max(best, (h[q] - E) if side == 1 else (E - l[q]))
        if follow and q == f + 1 and best < risk: return c[q]
        q += 1
    return c[q - 1]

def arrays(d):
    dc = pd.factorize(d.date)[0]
    A = dict(o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values, hr=d.hr.values, dc=dc, date=d.date.values, dow=d.dow.values)
    st = np.r_[0, np.flatnonzero(np.diff(dc)) + 1]; en = np.r_[st[1:], len(dc)]
    A['days'] = {A['date'][s]: (s, e) for s, e in zip(st, en)}
    return A

def first_at(A, s, e, x):
    """first index in [s,e) with hr >= x"""
    k = s + np.searchsorted(A['hr'][s:e], x - 1e-9); return k if k < e else None

def daily_tab(d, crypto):
    D = L.daily(d if crypto else d[d.dow < 5])
    D['b2'] = np.sign(D.c - D.c.rolling(20).mean()).shift(1)
    return D

# ---------------- models: each returns list of (cell, date, f, side, E, risk, tdist, flat_hr, follow) ----------------
def m_news(A, tick, rolls):
    out = []
    for dt in sorted(NEWS830):
        if dt not in A['days'] or dt in rolls: continue
        s, e = A['days'][dt]; hr = A['hr'][s:e]; h = A['h'][s:e]; l = A['l'][s:e]
        pre = (hr >= 7) & (hr < 8.5); post = (hr >= 8.5) & (hr < 8.75)
        if pre.sum() < 6 or post.sum() < 2: continue
        Rh, Rl = h[pre].max(), l[pre].min(); up = h[post].max() > Rh; dn = l[post].min() < Rl
        if up == dn: continue
        f = first_at(A, s, e, 8.75)
        if f is None or A['hr'][f] > 8.76: continue
        E = A['o'][f]; side = -1 if up else 1
        stop = h[post].max() + tick if up else l[post].min() - tick; risk = (stop - E) * -side
        tgt = (Rh + Rl) / 2; td = (tgt - E) * side
        if risk <= 0 or td < risk: continue
        out.append(('N1', dt, f, side, E, risk, td, 9.5, False))
    for dt, h0 in FOMC.items():
        if dt not in A['days'] or dt in rolls: continue
        s, e = A['days'][dt]; f0 = first_at(A, s, e, h0); f = first_at(A, s, e, h0 + 40 / 60)
        if f0 is None or f is None or abs(A['hr'][f0] - h0) > 0.01 or abs(A['hr'][f] - h0 - 40 / 60) > 0.01: continue
        k30 = first_at(A, s, e, h0 + 0.5)
        if k30 is None or k30 <= f0: continue
        mv = A['c'][k30 - 1] - A['o'][f0]
        if mv == 0: continue
        d1 = int(np.sign(mv)); side = -d1; E = A['o'][f]
        ext = A['h'][f0:f].max() + tick if d1 == 1 else A['l'][f0:f].min() - tick
        risk = (ext - E) * d1
        if risk <= 0: continue
        out.append(('N2', dt, f, side, E, risk, 2 * risk, 16, False))
    return out

def m_dr(A, D, tick, rolls):
    out = []; dl = list(D.index); rg = (D.h - D.l).values
    for j in range(22, len(dl)):
        dt = dl[j]
        if dt not in A['days'] or {dt, dl[j - 1], dl[j - 2]} & rolls or A['dow'][A['days'][dt][0]] > 4: continue
        big = D.iloc[j - 1]; r = rg[j - 1]; avg = rg[j - 21:j - 1].mean()
        if r < 1.5 * avg or r <= 0: continue
        if (big.c - big.l) / r <= 0.25: side, X, tgt = -1, D.l.iloc[j - 2], big.l
        elif (big.h - big.c) / r <= 0.25: side, X, tgt = 1, D.h.iloc[j - 2], big.h
        else: continue
        s, e = A['days'][dt]; k0 = first_at(A, s, e, 8.5)
        if k0 is None or A['hr'][k0] > 8.6: continue
        if (A['o'][k0] - X) * side <= 0: continue   # short: price below X at 08:30
        k1 = first_at(A, s, e, 10.0) or e
        tq = next((q for q in range(k0, k1) if ((A['h'][q] >= X) if side == -1 else (A['l'][q] <= X))), None)
        if tq is None: continue
        # DRa
        E = max(X, A['o'][tq]) if side == -1 else min(X, A['o'][tq]); risk = 0.25 * r - abs(E - X)
        td = (E - tgt) * -side
        if td >= risk: out.append(('a', dt, tq, side, E, risk, td, 16, False))
        # DRb
        k11 = first_at(A, s, e, 11.0) or e; h, l = A['h'], A['l']
        for k in range(tq + 2, k11):
            fvg = (h[k] < l[k - 2]) if side == -1 else (l[k] > h[k - 2])
            if not fvg: continue
            lev = h[k] if side == -1 else l[k]
            ext = (h[k0:k + 1].max() + tick) if side == -1 else (l[k0:k + 1].min() - tick)
            fq = next((q for q in range(k + 1, k11) if ((h[q] >= lev) if side == -1 else (l[q] <= lev))), None)
            if fq is not None:
                E = max(lev, A['o'][fq]) if side == -1 else min(lev, A['o'][fq]); risk = (ext - E) * -side; td = (E - tgt) * -side
                if risk > 0 and td >= risk: out.append(('b', dt, fq, side, E, risk, td, 16, False))
            break
    return out

def m_ir(A, D, tick, rolls):
    h, l, o, c, hr, dc = A['h'], A['l'], A['o'], A['c'], A['hr'], A['dc']
    rg = h - l; avg = pd.Series(rg).rolling(20).mean().shift(1).values; out = []; done = {'a': set(), 'b': set()}
    b2 = D.b2.to_dict()
    for side in (1, -1):
        disp = (rg >= 1.5 * avg) & ((c > o) if side == 1 else (c < o))
        for j in np.flatnonzero(disp):
            if j < 1 or j + 2 >= len(h) or dc[j - 1] != dc[j + 2]: continue
            dt = A['date'][j + 2]
            if A['dow'][j + 2] > 4 or dt in rolls or not (7 <= hr[j + 2] <= 15.5): continue
            if side == 1: ok = (l[j + 1] <= h[j - 1] + tick) and (l[j + 1] >= h[j - 1] - 0.25 * rg[j])
            else: ok = (h[j + 1] >= l[j - 1] - tick) and (h[j + 1] <= l[j - 1] + 0.25 * rg[j])
            if not ok: continue
            f = j + 2
            if side == 1:
                lev = h[j + 1] + tick
                if h[f] < lev: continue
                E = max(lev, o[f]); risk = E - (l[j + 1] - tick)
            else:
                lev = l[j + 1] - tick
                if l[f] > lev: continue
                E = min(lev, o[f]); risk = (h[j + 1] + tick) - E
            if risk <= 0: continue
            out.append(('a', dt, f, side, E, risk, 3 * risk, 16, True))
            if b2.get(dt) == side: out.append(('b', dt, f, side, E, risk, 3 * risk, 16, True))
    # one per day per cell: earliest fill
    out.sort(key=lambda t: t[2]); seen = set(); res = []
    for t in out:
        if (t[0], t[1]) in seen: continue
        seen.add((t[0], t[1])); res.append(t)
    return res

def m_wick(A, D, tick, rolls):
    out = []; dl = list(D.index)
    o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values
    uw = h - np.maximum(o, c); lw = np.minimum(o, c) - l; rg = h - l
    for j in range(4, len(dl)):
        dt = dl[j]
        if dt not in A['days'] or set(dl[j - 3:j + 1]) & rolls or A['dow'][A['days'][dt][0]] > 4: continue
        y = j - 1
        if rg[y] <= 0: continue
        qu = uw[y] >= 0.5 * rg[y] and uw[y] > uw[y - 1] and uw[y] > uw[y - 2]
        ql = lw[y] >= 0.5 * rg[y] and lw[y] > lw[y - 1] and lw[y] > lw[y - 2]
        if qu == ql: continue
        side = -1 if qu else 1
        CE = max(o[y], c[y]) + uw[y] / 2 if qu else min(o[y], c[y]) - lw[y] / 2
        stop = h[y] + tick if qu else l[y] - tick
        s, e = A['days'][dt]
        if (side == -1 and A['o'][s] >= CE) or (side == 1 and A['o'][s] <= CE): continue
        ke = first_at(A, s, e, 15.5) or e
        fq = next((q for q in range(s, ke) if ((A['h'][q] >= CE) if side == -1 else (A['l'][q] <= CE))), None)
        if fq is None: continue
        E = max(CE, A['o'][fq]) if side == -1 else min(CE, A['o'][fq]); risk = (stop - E) * -side
        if risk <= 0: continue
        out.append(('a', dt, fq, side, E, risk, 2 * risk, 16, False))
        if D.b2.iloc[j] == side: out.append(('b', dt, fq, side, E, risk, 2 * risk, 16, False))
    return out

def m_sb(A, tick, rolls):
    rth = {}
    for dt, (s, e) in A['days'].items():
        if A['dow'][s] > 4: continue
        k0 = first_at(A, s, e, 9.5); k1 = first_at(A, s, e, 16.0) or e
        if k0 is None or k1 - k0 < 300: continue
        rth[dt] = (k0, k1, A['o'][k0], A['h'][k0:k1].max(), A['l'][k0:k1].min(), A['c'][k1 - 1])
    dl = sorted(rth); bt = np.array([max(rth[d][2], rth[d][5]) for d in dl]); bb = np.array([min(rth[d][2], rth[d][5]) for d in dl])
    rollidx = [i for i, d in enumerate(dl) if d in rolls]
    blocks = []
    for k in range(1, len(dl) - 1):
        if bt[k - 1] < bb[k] and bb[k + 1] > bt[k]: side = 1
        elif bb[k - 1] > bt[k] and bt[k + 1] < bb[k]: side = -1
        else: continue
        H, Lo = rth[dl[k]][3], rth[dl[k]][4]; R = H - Lo
        nxt_roll = min([r for r in rollidx if r >= k - 1] + [10 ** 9])
        last = min(k + 11, nxt_roll - 1)
        lev = Lo + 0.25 * R if side == -1 else Lo + 0.75 * R; CE = Lo + 0.5 * R
        blocks.append([k + 2, last, side, lev, CE + tick if side == -1 else CE - tick])
    out = []
    for cell, w0, w1 in (('a', 9.8333, 10.1667), ('b', 9.5, 15.5)):
        used = set(); taken = set()
        for i in range(len(dl)):
            dt = dl[i]; k0, k1 = rth[dt][0], rth[dt][1]; cands = []
            if dt in rolls: continue
            for bi, (a0, a1, side, lev, stop) in enumerate(blocks):
                if bi in used or not (a0 <= i <= a1): continue
                if (side == -1 and A['o'][k0] >= lev) or (side == 1 and A['o'][k0] <= lev): used.add(bi); continue
                tq = next((q for q in range(k0, k1) if ((A['h'][q] >= lev) if side == -1 else (A['l'][q] <= lev))), None)
                if tq is None: continue
                used.add(bi)
                if w0 <= A['hr'][tq] < w1: cands.append((tq, side, lev, stop))
            if cands and dt not in taken:
                tq, side, lev, stop = min(cands)
                E = max(lev, A['o'][tq]) if side == -1 else min(lev, A['o'][tq]); risk = (stop - E) * -side
                if risk > 0: out.append((cell, dt, tq, side, E, risk, 2 * risk, 16, False)); taken.add(dt)
    return out

def m_lunch(A, tick, rolls):
    out = []; dl = [d for d in sorted(A['days']) if A['dow'][A['days'][d][0]] <= 4]
    h, l, o, c, hr = A['h'], A['l'], A['o'], A['c'], A['hr']
    for i in range(1, len(dl)):
        dp, dt = dl[i - 1], dl[i]
        if dp in rolls or dt in rolls: continue
        s, e = A['days'][dp]; a = first_at(A, s, e, 9.5); b = first_at(A, s, e, 11.5); z = first_at(A, s, e, 13.5) or e
        if a is None or b is None or b - a < 6: continue
        H, Lo = h[a:b].max(), l[a:b].min(); setups = []
        for side in (-1, 1):
            sq = next((q for q in range(b, z) if ((h[q] > H) if side == -1 else (l[q] < Lo))), None)
            if sq is None: continue
            lev = H if side == -1 else Lo
            if not any(((c[q] < lev) if side == -1 else (c[q] > lev)) for q in range(sq, min(sq + 3, e))): continue
            zone = None
            for k in range(sq - 1, a + 1, -1):
                if side == -1 and l[k] > h[k - 2]: zone = (h[k - 2], l[k]); break
                if side == 1 and h[k] < l[k - 2]: zone = (h[k], l[k - 2]); break
            if zone: setups.append((sq, side, zone))
        if not setups: continue
        sq, side, (zlo, zhi) = min(setups)
        s2, e2 = A['days'][dt]; k0 = first_at(A, s2, e2, 9.5); ke = first_at(A, s2, e2, 15.5) or e2
        if k0 is None: continue
        lev = zlo if side == -1 else zhi; stop = zhi + tick if side == -1 else zlo - tick
        if (side == -1 and o[k0] >= lev) or (side == 1 and o[k0] <= lev): continue
        fq = next((q for q in range(k0, ke) if ((h[q] >= lev) if side == -1 else (l[q] <= lev))), None)
        if fq is None: continue
        E = max(lev, o[fq]) if side == -1 else min(lev, o[fq]); risk = (stop - E) * -side
        if risk <= 0: continue
        out.append(('a', dt, fq, side, E, risk, 2 * risk, 16, False))
        p0 = first_at(A, s2, e2, 7.0)
        if p0 is not None and p0 < k0:
            tgt = l[p0:k0].min() if side == -1 else h[p0:k0].max(); td = (E - tgt) * -side
            if td >= risk: out.append(('b', dt, fq, side, E, risk, td, 16, False))
    return out

# ---------------- run ----------------
def run_market(m, grp, d, d5, venues, tick, crypto, cme):
    rolls = L.roll_dates(d) if cme else set()
    A = arrays(d); A5 = arrays(d5) if d5 is not None else A; D = daily_tab(d, crypto)
    sigs = {'N': (A, m_news(A, tick, rolls)), 'DR': (A, m_dr(A, D, tick, rolls)), 'IR': (A5, m_ir(A5, D, tick, rolls)),
            'W': (A, m_wick(A, D, tick, rolls)), 'L': (A, m_lunch(A, tick, rolls))}
    if cme: sigs['SB'] = (A, m_sb(A, tick, rolls))
    ctrl_cell = {'N': 'N1', 'DR': 'a', 'IR': 'a', 'W': 'a', 'SB': 'a', 'L': 'a'}
    rows = []
    for mo, (AA, S) in sigs.items():
        for (cell, dt, f, side, E, risk, td, fh, fol) in S:
            if L.cost_R(venues[0], m, E, risk) > 0.25: continue
            runs = [(cell, side)]
            if cell == ctrl_cell[mo]: runs.append(('COIN', int(RNG.choice([-1, 1]))))
            for cl, sd in runs:
                x = sim(AA, f, sd, E, risk, td, fh, fol); g = (x - E) * sd / risk
                for v in venues:
                    rows.append(dict(model=f'{mo} {grp}' + (f'@{v}' if cme else ''), cell=cl, m=m, date=dt, gross=g, net=g - L.cost_R(v, m, E, risk)))
    print(m, {k: len(v[1]) for k, v in sigs.items()}, flush=True)
    return rows

def cut(d, le): return d[pd.to_datetime(d.date) < pd.Timestamp(f'{le + 1}-01-01')].reset_index(drop=True)

rows = []
for m in ('NQ', 'ES'):
    d = cut(L.load_cme(m, '1min'), L.CME_LEARN_END); d5 = cut(L.load_cme(m, '5min'), L.CME_LEARN_END)
    rows += run_market(m, m, d, d5, ('cme', 'broker'), 0.25, False, True)
rows += run_market('XAUUSD', 'GOLD', cut(load_gold(), 2022), None, ('broker_cmdty',), 0.01, False, False)
for m in ('BTCUSDT', 'ETHUSDT'):
    rows += run_market(m, 'CRYPTO', cut(L.load_crypto(m, '5m'), L.CRYPTO_LEARN_END), None, ('broker',), 0.0, True, False)
for m in L.FX_PAIRS:
    rows += run_market(m, 'FX', cut(L.load_fx(m, '5m'), L.FX_LEARN_END), None, ('broker_fx',), 0.0, False, False)
T = pd.DataFrame(rows)
path = os.path.join(SPECS, 'X15d-results.md')
txt = L.report(T, 'X15d - news / daily-range rebalance / immediate rebalance / wick CE / suspension block / lunch gap (LEARN only)', 2023, os.devnull)
# pass/fail per cell (learn ends differ per group; data already truncated so every row is learn)
lines = ['\n## PASS/FAIL (learn; NET >= +0.05R and maxDD <= 30R; FX also >= 3/4 pairs positive; COIN = control, not a look)']
for (mo, cell), g in T.groupby(['model', 'cell']):
    g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values)
    ok = net >= 0.05 and dd <= 30
    if mo.startswith(tuple(f'{x} FX' for x in ('N', 'DR', 'IR', 'W', 'L'))): ok = ok and (g.groupby('m').net.mean() > 0).sum() >= 3
    lines.append(f'{mo}/{cell}: n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R -> {"PASS" if ok and cell != "COIN" else ("ctrl" if cell == "COIN" else "FAIL")}')
txt = txt.replace('## LEARN', '## LEARN (CME/crypto 2020-23, gold 2019-22, FX 2012-19; rows labelled by model+group, all years shown are learn)') + '\n'.join(lines)
print('\n'.join(lines)); open(path, 'w', encoding='utf-8').write(txt + '\n')
