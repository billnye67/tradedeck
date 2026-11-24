"""X15f: ICT queue ranks 50 (CISD + first FVG), 51 (PM sweep of lunch extreme), 52 (range-day 15-16 run -> midpoint fade),
55 (body contraction -> expansion), 56 (index SMT at key level), 58 (YT-C AM REH/REL). Pre-registered in
specs/X15-ict-rest.md section X15f. LEARN ERA ONLY: data truncated at load, exam years never loaded."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L

RNG = np.random.default_rng(1515)
ROWS = []; CLAIM = []
ONLY = set(os.environ.get('X15F_ONLY', '').split(',')) - {''}

def load_gold():  # copied from x14_sb.py
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

def cut(d, y0, y1):
    y = pd.to_datetime(pd.Series(d.date)).dt.year.values
    return d[(y >= y0) & (y <= y1) & (d.dow.values <= 4)].reset_index(drop=True)

def daily_tab(d5):
    D = L.daily(d5)
    D['b1'] = np.sign(D.c - D.o).shift(1); D['b2'] = np.sign(D.c - D.c.rolling(20).mean()).shift(1)
    D['adr'] = (D.h - D.l).rolling(20).mean().shift(1); D['pdh'] = D.h.shift(1); D['pdl'] = D.l.shift(1)
    body = (D.c - D.o).abs(); D['contr'] = body.shift(1) < 0.5 * body.shift(2).rolling(6).mean()
    D['rng_ratio'] = (D.h - D.l) / D['adr']
    wk = pd.to_datetime(pd.Series(list(D.index))).dt.to_period('W').values
    W = D.groupby(wk).agg(h=('h', 'max'), l=('l', 'min')); W['pwh'] = W.h.shift(1); W['pwl'] = W.l.shift(1)
    D['pwh'] = W.pwh.reindex(wk).values; D['pwl'] = W.pwl.reindex(wk).values
    return D

def days(d):
    dt = d.date.values; idx = np.r_[0, np.where(dt[1:] != dt[:-1])[0] + 1, len(dt)]
    return [(dt[idx[k]], idx[k], idx[k + 1]) for k in range(len(idx) - 1)]

def fractals(h, l):
    n = len(h); sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    if n >= 3:
        sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    return np.where(sh)[0], np.where(sl)[0]   # middle index m is known from bar m+2 on

class Day:
    def __init__(s, o, h, l, c, hr):
        s.o, s.h, s.l, s.c, s.hr = o, h, l, c, hr; s.n = len(h); s.SH, s.SL = fractals(h, l)
    @staticmethod
    def of(d, a, b): return Day(*(d[x].values[a:b] for x in ('o', 'h', 'l', 'c', 'hr')))
    def first(s, t):
        w = np.where(s.hr >= t)[0]; return w[0] if len(w) else s.n
    def known(s, arr, i, lo=0, hi=None):
        """swing middles m known at bar i (m + 2 <= i), lo <= m < hi."""
        x = arr[(arr + 2 <= i) & (arr >= lo)]
        return x if hi is None else x[x < hi]

def mirror(B): return Day(-B.o, -B.l, -B.h, -B.c, B.hr)
def unmirror(tr):
    q, side, E, stop, tgts, flat = tr; return (q, -side, -E, -stop, [(-t, f) for t, f in tgts], flat)
def bisi(B, k): return k >= 2 and B.l[k] > B.h[k - 2]
def sibi(B, k): return k >= 2 and B.h[k] < B.l[k - 2]

def sim(B, q, side, E, stop, tgts, flat):
    """stop-first: stop on fill bar = loss; targets from next bar; gap through stop at the open; flat at hour `flat`."""
    h, l, c, o, hr = B.h, B.l, B.c, B.o, B.hr; risk = abs(E - stop)
    if (l[q] <= stop) if side == 1 else (h[q] >= stop): return -1.0
    left, g, ti = 1.0, 0.0, 0
    for p in range(q + 1, B.n):
        if hr[p] >= flat: return g + left * side * (c[p - 1] - E) / risk
        if (o[p] <= stop) if side == 1 else (o[p] >= stop): return g + left * side * (o[p] - E) / risk
        if (l[p] <= stop) if side == 1 else (h[p] >= stop): return g + left * side * (stop - E) / risk
        while ti < len(tgts):
            tp, fr = tgts[ti]
            if (h[p] >= tp) if side == 1 else (l[p] <= tp):
                px = o[p] if ((o[p] >= tp) if side == 1 else (o[p] <= tp)) else tp
                g += fr * side * (px - E) / risk; left -= fr; ti += 1
            else: break
        if left <= 1e-9: return g
    return g + left * side * (c[B.n - 1] - E) / risk

def emit(model, cell, M, date, B, tr, ctrl=False):
    q, side, E, stop, tgts, flat = tr; risk = abs(E - stop)
    if not (risk > 0) or not np.isfinite(risk): return
    if L.cost_R(M['venues'][0], M['m'], abs(E), risk) > 0.25: return                                  # skip ugly: cost
    if tgts and cell != 'A3R' and side * (tgts[-1][0] - E) < 1.0 * risk: return                        # skip ugly: level < 1R
    g = sim(B, q, side, E, stop, tgts, flat)
    lab = lambda c, v: f"{c}|{M['grp']}" + (f"-{v}" if M['grp'] == 'NQES' else '')
    for v in M['venues']:
        ROWS.append(dict(model=model, cell=lab(cell, v), m=M['m'], date=date, gross=g, net=g - L.cost_R(v, M['m'], abs(E), risk)))
    if ctrl:
        s2 = side if RNG.random() < 0.5 else -side
        st2 = stop if s2 == side else 2 * E - stop; tg2 = tgts if s2 == side else [(2 * E - t, f) for t, f in tgts]
        g2 = sim(B, q, s2, E, st2, tg2, flat)
        for v in M['venues']:
            ROWS.append(dict(model=model, cell=lab('COIN', v), m=M['m'], date=date, gross=g2, net=g2 - L.cost_R(v, M['m'], abs(E), risk)))

# ---------------- Model 50: CISD + first FVG (short logic; longs via mirror). 5m bars, 15m swings ----------------
def m50_short(B, tick):
    h, l, c, o, hr = B.h, B.l, B.c, B.o, B.hr
    k15 = np.floor(hr * 4 + 1e-9) / 4; keys, st = np.unique(k15, return_index=True)
    H15 = np.maximum.reduceat(h, st); L15 = np.minimum.reduceat(l, st); end15 = keys + 0.25
    SH, SL = fractals(H15, L15)
    def known(arr, t):
        ok = arr[arr + 1 < len(keys)]; return ok[end15[ok + 1] <= t + 1e-9]
    used = set(); i = 0
    while i < B.n:
        if not (2 <= hr[i] < 15): i += 1; continue
        js = known(SH, hr[i])
        if not len(js) or js[-1] in used or h[i] <= H15[js[-1]]: i += 1; continue
        used.add(js[-1]); RH, RB = h[i], i; cb = None; j = i + 1
        while j < B.n and j - RB <= 12:
            if h[j] > RH: RH, RB = h[j], j
            else:
                e = RB if c[RB] > o[RB] else RB - 1; s = e
                while s >= 0 and c[s] > o[s]: s -= 1
                s += 1
                if s <= e and l[j] <= o[s] - tick: cb = j; break
            j += 1
        if cb is None: i = j; continue
        k = cb
        while k < B.n and k - cb <= 36 and hr[k] < 16:
            if h[k] > RH: break
            if sibi(B, k):
                E = h[k] - tick; stop = RH + tick
                for q in range(k + 1, min(B.n, cb + 37)):
                    if hr[q] >= 16: break
                    if h[q] >= E:
                        lows = [L15[x] for x in known(SL, hr[q]) if L15[x] < E]
                        return (q, -1, E, stop, lows[-1] if lows else None)
                break
            k += 1
        i = max(j, k) + 1
    return None

def run50(M, B, date):
    cands = []; s = m50_short(B, M['tick']); b = m50_short(mirror(B), M['tick'])
    if s: cands.append(s)
    if b: cands.append((b[0], 1, -b[2], -b[3], -b[4] if b[4] is not None else None))
    if not cands: return
    cands.sort(key=lambda x: x[0]); q, side, E, stop, lvl = cands[0]; R = abs(E - stop)
    emit('50 CISD+FVG', 'A3R', M, date, B, (q, side, E, stop, [(E + side * 3 * R, 1.0)], 16), ctrl=True)
    cb = [x for x in cands if x[4] is not None]
    if cb:
        q, side, E, stop, lvl = cb[0]; emit('50 CISD+FVG', 'B15m', M, date, B, (q, side, E, stop, [(lvl, 1.0)], 16))

# ---------------- Model 51: PM sweep of the lunch extreme (long logic; shorts via mirror) ----------------
def m51_long(B, tick):
    h, l, c, o, hr = B.h, B.l, B.c, B.o, B.hr
    am = np.where((hr >= 9.5) & (hr < 12))[0]; lu = np.where((hr >= 12) & (hr < 13.5))[0]; pre = np.where(hr < 13.5)[0]
    if len(am) < 10 or len(lu) < 6: return None
    if not c[am[-1]] > o[am[0]]: return None
    if (h[lu].max() - l[lu].min()) > 0.5 * (h[am].max() - l[am].min()): return None
    LL = l[lu].min(); EQ = (h[pre].max() + l[pre].min()) / 2
    i = lu[-1] + 1
    while i < B.n and hr[i] < 15 and l[i] >= LL: i += 1
    if i >= B.n or hr[i] >= 15: return None
    SL, SB = l[i], i; j = i + 1; mss = None
    while j < B.n and hr[j] < 15.5:
        if l[j] < SL: SL, SB = l[j], j; j += 1; continue
        ms = B.known(B.SH, j, lu[0], SB)
        if len(ms) and h[j] > h[ms[-1]]: mss = j; break
        j += 1
    if mss is None or SL >= EQ: return None
    for k in range(mss, B.n):
        if hr[k] >= 15.5 or l[k] < SL: return None
        if bisi(B, k):
            E = l[k] - tick; stop = SL - tick
            for q in range(k + 1, B.n):
                if hr[q] >= 15.5: return None
                if l[q] <= E: return (q, 1, E, stop, [(h[:q + 1].max(), 1.0)], 16)
            return None
    return None

def run51(M, B, date, D):
    for cell, bcol in (('A-B1', 'b1'), ('B-B2', 'b2')):
        bias = D[bcol]
        if not np.isfinite(bias) or bias == 0: continue
        if bias > 0: tr = m51_long(B, M['tick'])
        else:
            t = m51_long(mirror(B), M['tick']); tr = unmirror(t) if t else None
        if tr: emit('51 PM lunch sweep', cell, M, date, B, tr, ctrl=(cell == 'A-B1'))

# ---------------- Model 52: range-day 15-16 run, midpoint fade (short logic; longs via mirror) ----------------
def m52_short(B, tick, i0, H, Lo):
    h, l, hr = B.h, B.l, B.hr
    RX, RB = h[i0], i0; j = i0 + 1; mss = None
    while j < B.n and hr[j] < 16:
        if h[j] > RX: RX, RB = h[j], j; j += 1; continue
        ms = B.known(B.SL, j, 0, RB)
        if len(ms) and l[j] < l[ms[-1]]: mss = j; break
        j += 1
    if mss is None: return None
    for k in range(mss, B.n):
        if hr[k] >= 16 or h[k] > RX: return None
        if sibi(B, k):
            E = h[k] + tick; stop = RX + tick; mid = (H + Lo) / 2
            if not mid < E: return None
            for q in range(k + 1, B.n):
                if hr[q] >= 16: return None
                if h[q] >= E: return (q, -1, E, stop, [(mid, 0.5), (Lo, 0.5)], 16.5)
            return None
    return None

def run52(M, B, date, D):
    pre = np.where(B.hr < 15)[0]; w = np.where((B.hr >= 15) & (B.hr < 16))[0]
    if len(pre) < 20 or len(w) < 6: return
    H, Lo = B.h[pre].max(), B.l[pre].min()
    up = w[B.h[w] > H]; dn = w[B.l[w] < Lo]
    fu = up[0] if len(up) else 10**9; fd = dn[0] if len(dn) else 10**9
    if fu == fd: return
    if fu < fd: tr = m52_short(B, M['tick'], fu, H, Lo)
    else:
        t = m52_short(mirror(B), M['tick'], fd, -Lo, -H); tr = unmirror(t) if t else None
    if not tr: return
    adr = D['adr']
    if np.isfinite(adr) and (H - Lo) <= 0.8 * adr: emit('52 Range-day fade', 'A-rangeday', M, date, B, tr, ctrl=True)
    emit('52 Range-day fade', 'B-all', M, date, B, tr)

# ---------------- Model 55: body contraction -> expansion (5m) ----------------
def run55(M, B, date, D):
    adr = D['adr']
    if np.isfinite(D['rng_ratio']): CLAIM.append(dict(m=M['m'], contr=bool(D['contr']), ratio=D['rng_ratio']))
    if not D['contr'] or not np.isfinite(adr) or adr <= 0: return
    for cell, bcol in (('A-B1', 'b1'), ('B-B2', 'b2')):
        s = D[bcol]
        if not np.isfinite(s) or s == 0: continue
        s = int(s); E = B.o[0] - s * 0.1 * adr; stop = E - s * 0.5 * adr; tg = E + s * adr
        for q in range(B.n):
            if B.hr[q] >= 16: break
            if (B.l[q] <= E) if s == 1 else (B.h[q] >= E):
                emit('55 Body contraction', cell, M, date, B, (q, s, E, stop, [(tg, 1.0)], 16.917), ctrl=(cell == 'A-B1')); break

# ---------------- Model 58: YT-C AM REH/REL (short logic; longs via mirror) ----------------
def m58_short(B, tick, tol):
    h, l, c, o, hr = B.h, B.l, B.c, B.o, B.hr
    i = B.first(7); lvl = None; lastb = -1; Rb = None
    while i < B.n and hr[i] < 11:
        sw = B.known(B.SH, i); sw = sw[hr[sw] >= 7]
        if len(sw) >= 2 and sw[-1] != lastb:            # a new swing high: is it the right side of a REH?
            b = sw[-1]; lastb = b
            for a in sw[-2::-1]:
                if h[a] >= h[b] and h[a] - h[b] <= tol and h[a + 1:b].max(initial=-np.inf) < h[b] and h[b + 1:i].max(initial=-np.inf) <= h[a]:
                    lvl, Rb = h[a], b; break
        if lvl is None or h[i] <= lvl: i += 1; continue
        RH, RB = h[i], i; LS = Rb + int(np.argmin(l[Rb:RB + 1])); j = i + 1; mss = None
        while j < B.n and hr[j] < 11:
            if h[j] > RH: RH, RB = h[j], j; LS = Rb + int(np.argmin(l[Rb:RB + 1]))
            elif c[j] < l[LS]: mss = j; break
            j += 1
        lvl = None
        if mss is None: i = j; continue
        fv = [k for k in range(LS + 2, RB + 1) if bisi(B, k)]
        if fv: E = h[fv[-1] - 2]; stop = RH + 2 * tick
        else:
            e = LS
            while e >= 0 and not (c[e] < o[e]): e -= 1
            if e < 0: i = mss + 1; continue
            s = e
            while s - 1 >= 0 and c[s - 1] < o[s - 1]: s -= 1
            E = o[e]; stop = h[s:e + 1].max() + 2 * tick
        if not (E < stop and E > c[mss]): i = mss + 1; continue
        for q in range(mss + 1, B.n):
            if hr[q] >= 11: return None
            if h[q] >= E:
                sw = B.known(B.SL, q); sw = sw[hr[sw] >= 7]; best = None
                for bi in range(len(sw)):
                    for ai in range(bi):
                        a, b = sw[ai], sw[bi]
                        if l[a] <= l[b] and l[b] - l[a] <= tol and l[a] < E and l[a + 1:b].min(initial=np.inf) > l[b] \
                                and l[b + 1:q].min(initial=np.inf) >= l[a] and (best is None or l[a] > best): best = l[a]
                tg = best if best is not None else l[B.first(7):q + 1].min()
                return (q, -1, E, stop, [(tg, 1.0)], 12)
        return None
    return None

def run58(M, B, date, D):
    adr = D['adr']
    if not np.isfinite(adr): return
    tol = 0.005 * adr; s = m58_short(B, M['tick'], tol); b = m58_short(mirror(B), M['tick'], tol)
    c = [t for t in (s, unmirror(b) if b else None) if t]
    if c: emit('58 YT-C REH/REL', 'A-nobias', M, date, B, min(c, key=lambda t: t[0]), ctrl=True)
    b2 = D['b2']; agree = [t for t in c if np.isfinite(b2) and t[1] == b2]
    if agree: emit('58 YT-C REH/REL', 'B-B2', M, date, B, agree[0])

# ---------------- Model 56: SMT pair (5m) ----------------
def run56(Mx, My):
    dX = {dt: (a, b) for dt, a, b in days(Mx['d5'])}; dY = {dt: (a, b) for dt, a, b in days(My['d5'])}
    for date in sorted(set(dX) & set(dY)):
        if date not in Mx['D'].index or date not in My['D'].index or date in Mx['rolls'] or date in My['rolls']: continue
        Rs = [Mx['D'].loc[date], My['D'].loc[date]]
        if not (Rs[0].b2 == Rs[1].b2 and Rs[0].b2 in (1, -1)): continue
        s = int(Rs[0].b2); Os = [Day.of(Mx['d5'], *dX[date]), Day.of(My['d5'], *dY[date])]
        Bs = Os if s == 1 else [mirror(b) for b in Os]; st = []
        for B, R in zip(Bs, Rs):
            ref = B.hr < 5; win = (B.hr >= 5) & (B.hr < 9.5)
            if ref.sum() < 6 or win.sum() < 6: st = None; break
            key = np.nanmax([R.pdl, R.pwl]) if s == 1 else np.nanmax([-R.pdh, -R.pwh])
            wl, rl = B.l[win].min(), B.l[ref].min(); st.append((wl < rl and wl < key, wl > rl, wl))
        if st is None: continue
        if st[0][0] and st[1][1]: X, Y = 0, 1
        elif st[1][0] and st[0][1]: X, Y = 1, 0
        else: continue
        for cell, T in (('A-swept', X), ('B-held', Y)):
            B = Bs[T]; M = (Mx, My)[T]; wl = st[T][2]; tr = None
            for k in range(max(B.first(9.5), 2), B.n):
                if B.hr[k] >= 10.5: break
                if bisi(B, k):
                    E = B.l[k]; stop = wl - M['tick']
                    for q in range(k + 1, B.n):
                        if B.hr[q] >= 10.5: break
                        if B.l[q] <= E: tr = (q, 1, E, stop, [], 12); break
                    break
            if tr is None or not tr[2] > tr[3]: continue
            if s == -1: tr = unmirror(tr)
            emit('56 SMT key level', cell, M, date, Os[T], tr, ctrl=(cell == 'A-swept'))

# ---------------- markets ----------------
def mk(m, grp, venues, tick, fine, d5, rolls=frozenset()):
    return dict(m=m, grp=grp, venues=venues, tick=tick, fine=fine, d5=d5, D=daily_tab(d5), rolls=set(rolls))

def markets():
    out = []
    for m in ('NQ', 'ES'):
        d1 = cut(L.load_cme(m, '1min'), 2020, 2023); d5 = cut(L.load_cme(m, '5min'), 2020, 2023)
        out.append(mk(m, 'NQES', ['cme', 'broker'], 0.25, d1, d5, L.roll_dates(d1)))
    g = cut(load_gold(), 2019, 2022); out.append(mk('XAUUSD', 'gold', ['broker_cmdty'], 0.01, g, g))
    for m in ('BTCUSDT', 'ETHUSDT'):
        d = cut(L.load_crypto(m, '5m'), 2020, 2023); out.append(mk(m, 'crypto', ['broker'], 0.0, d, d))
    for m in L.FX_PAIRS:
        d = cut(L.load_fx(m, '5m'), 2012, 2019); out.append(mk(m, 'fx', ['broker_fx'], 0.0, d, d))
    return out

def main():
    MS = markets(); byname = {M['m']: M for M in MS}
    if ONLY: MS = [M for M in MS if M['m'] in ONLY]
    for M in MS:
        print('market', M['m'], len(M['fine']), flush=True); D = M['D']
        for date, a, b in days(M['fine']):
            if date in M['rolls'] or date not in D.index: continue
            B = Day.of(M['fine'], a, b); R = D.loc[date]
            run51(M, B, date, R); run52(M, B, date, R); run58(M, B, date, R)
        for date, a, b in days(M['d5']):
            if date in M['rolls'] or date not in D.index: continue
            B = Day.of(M['d5'], a, b); R = D.loc[date]
            run50(M, B, date); run55(M, B, date, R)
    for x, y in (('NQ', 'ES'), ('BTCUSDT', 'ETHUSDT'), ('EURUSD', 'GBPUSD')):
        if x in byname and y in byname and (not ONLY or x in ONLY): run56(byname[x], byname[y])
    T = pd.DataFrame(ROWS)
    T = T[pd.to_datetime(T.date).dt.year <= 2023]   # belt and braces; data already cut to learn years
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'X15f-results.md')
    L.report(T, 'X15f results (learn era only; CME/crypto 2020-23, gold 2019-22, FX 2012-19)', 2100, path)
    out = ['\n## PASS table (learn): NET >= +0.05R and maxDD <= 30R per market group; FX >= 3/4 pairs positive (SMT: both pairs)']
    for (mo, cell), g in T.groupby(['model', 'cell']):
        g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values); ok = net >= 0.05 and dd <= 30
        if cell.endswith('|fx'):
            per = g.groupby('m').net.mean(); ok = ok and (per > 0).sum() >= (2 if mo.startswith('56') else 3)
        verdict = 'control' if cell.startswith('COIN') else ('PASS' if ok else 'FAIL')
        out.append(f"{mo} / {cell}: n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R -> {verdict}")
    if CLAIM:
        C = pd.DataFrame(CLAIM); out.append('\n## Claim C7 (T13.1): today range / ADR20 on contraction days vs all days')
        for m, g in C.groupby('m'):
            out.append(f"{m}: contraction n={int(g.contr.sum())} mean {g[g.contr].ratio.mean():.3f} | all n={len(g)} mean {g.ratio.mean():.3f}")
    t2 = '\n'.join(out); print(t2); open(path, 'a', encoding='utf-8').write(t2 + '\n')

if __name__ == '__main__': main()
