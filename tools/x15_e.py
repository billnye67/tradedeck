"""X15e: ICT queue ranks 39, 40, 41, 47, 48, 49 on NQ/ES (cme + broker costs), gold, BTC/ETH, FX.
Rules: specs/X15-ict-rest.md section X15e (pre-registered). LEARN ERA ONLY: data truncated at load, exam never computed.
Every model is written once in long form; short setups run the same code on price-negated ("mirror") arrays."""
import os, sys, sqlite3, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools'); import ictlib as L
OUT = r'C:\Users\you\Desktop\TradeDeck\specs\X15e-results.md'
RNG = np.random.default_rng(1515)
ROWS = []

def load_gold():
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

# name, group, primary venue, cost market id, tick, start, end(excl)
MK = {'NQ': ('NQ', 'cme', 'NQ', 0.25), 'ES': ('ES', 'cme', 'ES', 0.25), 'XAUUSD': ('GOLD', 'broker_cmdty', 'XAUUSD', 0.01),
      'BTCUSDT': ('CRYPTO', 'broker', 'BTCUSDT', 0.1), 'ETHUSDT': ('CRYPTO', 'broker', 'ETHUSDT', 0.01)}
for p in L.FX_PAIRS: MK[p] = ('FX', 'broker_fx', p, 0.1 * L.pip_of(p))
ERA = {'NQ': (dt.date(2019, 1, 1), dt.date(2024, 1, 1)), 'ES': (dt.date(2019, 1, 1), dt.date(2024, 1, 1)),
       'XAUUSD': (dt.date(2010, 1, 1), dt.date(2023, 1, 1)), 'BTCUSDT': (dt.date(2019, 1, 1), dt.date(2024, 1, 1)),
       'ETHUSDT': (dt.date(2019, 1, 1), dt.date(2024, 1, 1))}
for p in L.FX_PAIRS: ERA[p] = (dt.date(2012, 1, 1), dt.date(2020, 1, 1))
LEARN_END = {'NQ': 2023, 'ES': 2023, 'GOLD': 2022, 'CRYPTO': 2023, 'FX': 2019}

def load(m, tf):
    if m in ('NQ', 'ES'): d = L.load_cme(m, '1min' if tf == 'base' else '5min')
    elif m == 'XAUUSD': d = load_gold()
    elif m in ('BTCUSDT', 'ETHUSDT'): d = L.load_crypto(m, '5m')
    else: d = L.load_fx(m, '5m')
    a, b = ERA[m]; d = d[(d.date >= a) & (d.date < b)].reset_index(drop=True)
    return d

def pts(x_es, price, m):   # ES points -> this market's price units
    return x_es if m == 'ES' else x_es / 4000.0 * abs(price)

def swings(h, l, dord, n):
    """Fractal swing highs/lows (n each side, same day). Returns bool arrays sh, sl (at the swing bar j)."""
    H, Lw = pd.Series(h), pd.Series(l)
    lh = H.shift(1).rolling(n).max().values; rh = H[::-1].shift(1).rolling(n).max().values[::-1]
    ll = Lw.shift(1).rolling(n).min().values; rl = Lw[::-1].shift(1).rolling(n).min().values[::-1]
    same = np.zeros(len(h), bool); same[n:-n] = dord[:-2 * n] == dord[2 * n:]
    with np.errstate(invalid='ignore'):
        sh = same & (h > lh) & (h >= rh); sl = same & (l < ll) & (l <= rl)
    return sh, sl

def last_known(flag, val, n):
    """Value of the latest swing confirmed by the close of bar i (j + n <= i); nan if none."""
    N = len(flag); out = np.full(N, np.nan); j = np.flatnonzero(flag); k = j + n; ok = k < N
    out[k[ok]] = val[j[ok]]; return pd.Series(out).ffill().values

class Space:
    """One price space: sign=+1 original, -1 mirror (short setups become long). Day tables built in this space."""
    def __init__(s, d, m, sign, crypto):
        s.m, s.sign = m, sign; s.grp, s.venue, s.cm, s.tick = MK[m]
        g = 1.0 * sign
        if sign == 1: s.o, s.h, s.l, s.c = d.o.values * g, d.h.values * g, d.l.values * g, d.c.values * g
        else: s.o, s.h, s.l, s.c = -d.o.values, -d.l.values, -d.h.values, -d.c.values
        s.hr = d.hr.values; s.N = len(d)
        dates = d.date.values; s.dord = np.array([x.toordinal() for x in dates]) if len(dates) else np.array([])
        st = np.r_[0, np.flatnonzero(np.diff(s.dord)) + 1]; en = np.r_[st[1:], s.N]
        s.dates = [dates[i] for i in st]; s.ds, s.de = st, en
        dow = np.array([x.weekday() for x in s.dates])
        keep = np.ones(len(st), bool) if crypto else dow <= 4
        s.trade = keep.copy()
        if MK[m][1] == 'cme':
            rolls = L.roll_dates(d); s.trade &= np.array([x not in rolls for x in s.dates])
        # daily (NY date) in this space, rows = kept dates
        idx = np.flatnonzero(keep); s.didx = idx
        O = np.array([s.o[st[i]] for i in idx]); C = np.array([s.c[en[i] - 1] for i in idx])
        Hh = np.array([s.h[st[i]:en[i]].max() for i in idx]); Ll = np.array([s.l[st[i]:en[i]].min() for i in idx])
        sma = pd.Series(C).rolling(20).mean().values
        b2 = np.r_[0, np.where(np.isnan(sma), 0, np.sign(C - sma))[:-1]]          # shifted: known today
        pdh = np.r_[np.nan, Hh[:-1]]; pdl = np.r_[np.nan, Ll[:-1]]
        wk = np.array([s.dates[i].isocalendar()[:2] for i in idx]); wkey = wk[:, 0] * 100 + wk[:, 1]
        W = pd.DataFrame(dict(k=wkey, h=Hh, l=Ll)).groupby('k').agg(h=('h', 'max'), l=('l', 'min'))
        wprev = {k: (W.h.iloc[j - 1], W.l.iloc[j - 1]) for j, k in enumerate(W.index) if j > 0}
        pwh = np.array([wprev.get(k, (np.nan, np.nan))[0] for k in wkey]); pwl = np.array([wprev.get(k, (np.nan, np.nan))[1] for k in wkey])
        # day position -> daily row
        s.row = {int(i): r for r, i in enumerate(idx)}
        s.b2, s.pdh, s.pdl, s.pwh, s.pwl, s.wkey, s.DO, s.DC = b2, pdh, pdl, pwh, pwl, wkey, O, C
        s.ddow = dow[idx]
        s.bull = np.zeros(s.N, bool); s.bull[2:] = (s.l[2:] > s.h[:-2]) & (s.dord[2:] == s.dord[:-2])
        s.body = np.abs(s.c - s.o); s.mbody = pd.Series(s.body).shift(1).rolling(20).mean().values

    def first_at(s, a, b, x):  # first index in [a,b) with hr >= x
        return a + int(np.searchsorted(s.hr[a:b], x - 1e-9))

def cost_ok(sp, E, risk):
    return risk > 0 and L.cost_R(sp.venue, sp.cm, abs(E), risk) <= 0.25

def walk2(sp, i, e, side, E, stop, tgts, flat_hr, limit_fill):
    h, l, c, o, hr = sp.h, sp.l, sp.c, sp.o, sp.hr
    risk = abs(E - stop); left = 1.0; g = 0.0; ti = 0; q = i
    if limit_fill:
        if (l[i] <= stop) if side == 1 else (h[i] >= stop): return side * (stop - E) / risk
        q = i + 1
    while q < e:
        if hr[q] >= flat_hr: return g + left * side * ((c[q - 1] if q > i else E) - E) / risk
        if (o[q] <= stop) if side == 1 else (o[q] >= stop): return g + left * side * (o[q] - E) / risk
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return g + left * side * (stop - E) / risk
        while ti < len(tgts):
            tp, fr = tgts[ti]
            if (h[q] >= tp) if side == 1 else (l[q] <= tp):
                px = o[q] if ((o[q] >= tp) if side == 1 else (o[q] <= tp)) else tp
                g += fr * side * (px - E) / risk; left -= fr; ti += 1
            else: break
        if left <= 1e-9: return g
        q += 1
    return g + left * side * (c[e - 1] - E) / risk

def add(model, cell, sp, date, E, risk, gross, nights=0):
    for v in ([sp.venue, 'broker'] if sp.venue == 'cme' else [sp.venue]):
        ROWS.append(dict(model=model, cell=cell, m=sp.m, grp=sp.grp, venue=v, date=date, side=sp.sign, gross=gross,
                         net=gross - L.cost_R(v, sp.cm, abs(E), risk, nights)))

def fire(model, cell, sp, di, i, E, stop, tgts, flat_hr, limit_fill, ctrl):
    """Long trade in space sp at bar i. Returns gross R."""
    e = sp.de[di]; risk = abs(E - stop)
    g = walk2(sp, i, e, 1, E, stop, tgts, flat_hr, limit_fill); add(model, cell, sp, sp.dates[di], E, risk, g)
    if ctrl:
        if RNG.random() < 0.5: g2 = walk2(sp, i, e, 1, E, stop, tgts, flat_hr, limit_fill)
        else: g2 = walk2(sp, i, e, -1, E, 2 * E - stop, [(2 * E - tp, fr) for tp, fr in tgts], flat_hr, limit_fill)
        add(model, 'CTRL', sp, sp.dates[di], E, risk, g2)
    return g

def tgt_ok(E, stop, tgt): return tgt - E >= 1.0 * (E - stop)

# ============================ M39 ============================
def m39(SP):
    for sp in SP.values():
        nd = len(sp.dates)
        for di in range(1, nd):
            if not sp.trade[di] or di not in sp.row: continue
            r = sp.row[di]; pi = di - 1
            while pi >= 0 and pi not in sp.row: pi -= 1
            if pi < 0: continue
            a, e = sp.ds[di], sp.de[di]; i930 = sp.first_at(a, e, 9.5)
            if i930 >= e or sp.hr[i930] >= 9.5 + 5 / 60 + 1e-9: continue
            m39a(sp, di, r, pi, a, e, i930); m39b(sp, di, r, a, e, i930)

def fvgs_in(sp, a, b, bull):
    """Indices k (c3) in [a,b) of FVGs; bull in this space, or bear (= bull in mirror) via h[k] < l[k-2]."""
    k = np.arange(max(a, 2), b)
    if bull: ok = (sp.l[k] > sp.h[k - 2])
    else: ok = (sp.h[k] < sp.l[k - 2])
    ok &= sp.dord[k] == sp.dord[k - 2]
    return k[ok]

def m39a(sp, di, r, pi, a, e, i930):
    if sp.b2[r] != 1: return
    pa, pe = sp.ds[pi], sp.de[pi]; p930 = sp.first_at(pa, pe, 9.5); p1600 = sp.first_at(pa, pe, 16.0)
    allk = [(k, True) for k in fvgs_in(sp, p930 + 2, p1600, True)] + [(k, False) for k in fvgs_in(sp, p930 + 2, p1600, False)]
    if not allk: return
    allk.sort(); k0, fpbull = allk[0]
    if fpbull: return                      # long case needs a bear FPFVG (reflection = bull BISI below)
    c1 = k0 - 2; pre0 = sp.first_at(pa, pe, 8.5)
    if c1 > pre0 and sp.l[k0 - 1] < sp.l[pre0:c1].min(): return       # displacement FPFVG -> no reflection
    R = [k for k, b in allk if b and k > k0]
    if not R: return
    kr = R[0]; rtop = sp.l[kr]
    o9, h9, l9, c9 = sp.o[i930], sp.h[i930], sp.l[i930], sp.c[i930]
    if not (l9 < rtop and c9 > rtop): return
    M = (l9 + min(o9, c9)) / 2; stop = l9 - sp.tick
    a7, b9 = sp.first_at(a, e, 7.0), sp.first_at(a, e, 9.0)
    if b9 - a7 < 2: return
    rh, rl = sp.h[a7:b9].max(), sp.l[a7:b9].min(); ce = (rh + rl) / 2
    i10 = sp.first_at(a, e, 10.0)
    for k in range(i930 + 1, i10):
        if sp.c[k] < M or sp.l[k] < stop: return
        if k >= i930 + 2 and sp.l[k] > sp.h[k - 2]:            # first bull FVG after 09:30
            E = sp.h[k - 2] + sp.tick
            if E >= sp.l[k] or E <= stop: return
            tgt = ce if ce > E else (rh if rh > E else None)
            if tgt is None or not tgt_ok(E, stop, tgt) or not cost_ok(sp, E, E - stop): return
            for q in range(k + 1, i10):
                if sp.l[q] <= E:
                    fire('M39', 'A', sp, di, q, E, stop, [(tgt, 1.0)], 12.0, True, True); return
                if sp.c[q] < M or sp.l[q] < stop: return
            return

def m39b(sp, di, r, a, e, i930):
    if sp.ddow[r] != 4 or r < 4: return
    # Mon open -> Thu close of this ISO week, in this space; long case needs it DOWN
    wk = sp.wkey[r]; rows = [x for x in range(r - 4, r) if sp.wkey[x] == wk]
    if not rows or sp.ddow[rows[0]] != 0 or sp.ddow[rows[-1]] != 3: return
    if not (sp.DC[rows[-1]] - sp.DO[rows[0]] < 0): return
    i1130 = sp.first_at(a, e, 11.5)
    cand = sorted([(k, True) for k in fvgs_in(sp, i930 + 2, i1130, True)] + [(k, False) for k in fvgs_in(sp, i930 + 2, i1130, False)])
    if not cand or cand[0][1]: return                   # need a bear FPFVG (SIBI above) in long form
    k0 = cand[0][0]; top, bot = sp.l[k0 - 2], sp.h[k0]; mid = (top + bot) / 2
    visits, inside, q3 = 0, False, None
    for q in range(k0 + 1, i1130):
        if sp.c[q] > top: return
        if not inside and sp.h[q] >= bot: visits += 1; inside = True
        elif inside and sp.h[q] < bot: inside = False
        if visits == 3: q3 = q; break
    if q3 is None: return
    for k in range(max(q3, i930 + 2), i1130):
        if sp.l[k] > sp.h[k - 2] and sp.c[k] >= mid:
            E = sp.l[k]; stop = sp.l[q3:k + 1].min() - sp.tick
            tgt = max(sp.h[i930:k + 1].max(), top) - 2 * sp.tick
            if E <= stop or not tgt_ok(E, stop, tgt) or not cost_ok(sp, E, E - stop): return
            for q in range(k + 1, i1130):
                if sp.l[q] <= E:
                    fire('M39', 'B', sp, di, q, E, stop, [(tgt, 1.0)], 12.0, True, False); return
                if sp.h[q] >= tgt: return
            return

# ============================ M40 (daily) ============================
def m40(m, d, crypto):
    cme = MK[m][1] == 'cme'
    if cme:
        rolls = L.roll_dates(d); x = d[(d.hr >= 9.5) & (d.hr < 16) & (d.dow <= 4)]
    else:
        rolls = set(); x = d if crypto else d[d.dow <= 4]
    D = x.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    D = D[D.n >= (200 if cme else 100)]
    dates = list(D.index); isroll = np.array([t in rolls for t in dates])
    for sign in (1, -1):
        if sign == 1: o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values
        else: o, h, l, c = -D.o.values, -D.l.values, -D.h.values, -D.c.values
        sp = type('S', (), {})(); sp.m = m; sp.sign = sign; sp.grp, sp.venue, sp.cm, sp.tick = MK[m]
        lo60 = pd.Series(l).rolling(60).min().shift(1).values
        n = len(c); t = 61; busy = 0
        while t < n - 2:
            if t < busy or not (c[t] < lo60[t]) or not (c[t - 1] >= lo60[t - 1] if lo60[t - 1] == lo60[t - 1] else True):
                t += 1; continue
            ce = (c[t - 1] + o[t]) / 2; last = c[t]; s = 1; u3 = None
            for u in range(t + 1, min(n, t + 11)):
                if c[u] < last: s += 1; last = c[u]
                if s == 3: u3 = u; break
            if u3 is None or u3 + 1 >= n: t += 1; continue
            stop = l[t:u3 + 1].min() - sp.tick; end = u3
            for cell in ('A', 'B'):
                if cell == 'A': q = u3 + 1; E = o[q]; ent = q
                else:
                    ent = None
                    for q in range(u3 + 1, min(n, u3 + 4)):
                        if h[q] >= h[u3]: ent = q; E = max(o[q], h[u3]); break
                    if ent is None: continue
                if E <= stop or not tgt_ok(E, stop, ce) or not cost_ok(sp, E, E - stop): continue
                risk = E - stop; xi = None
                if (l[ent] <= stop) or (o[ent] <= stop): xp, xi = min(stop, o[ent]), ent
                elif cell == 'A' and h[ent] >= ce: xp, xi = ce, ent
                else:
                    for q in range(ent + 1, min(n, ent + 20)):
                        if o[q] <= stop: xp, xi = o[q], q; break
                        if l[q] <= stop: xp, xi = stop, q; break
                        if h[q] >= ce: xp, xi = (o[q] if o[q] >= ce else ce), q; break
                    if xi is None: xi = min(n, ent + 20) - 1; xp = c[xi]
                end = max(end, xi)
                if isroll[ent:xi + 1].any(): continue
                g = (xp - E) / risk; add('M40', cell, sp, dates[ent], E, risk, g, nights=xi - ent + (0 if cell == 'A' else 0))
                if cell == 'A':
                    if RNG.random() < 0.5: g2 = g
                    else:
                        st2, tg2 = 2 * E - stop, 2 * E - ce; xp2 = None
                        for q in range(ent, min(n, ent + 20)):
                            if q > ent and o[q] >= st2: xp2 = o[q]; break
                            if h[q] >= st2: xp2 = st2; break
                            if l[q] <= tg2: xp2 = o[q] if (q > ent and o[q] <= tg2) else tg2; break
                        if xp2 is None: xp2 = c[min(n, ent + 20) - 1]
                        g2 = -(xp2 - E) / risk
                    add('M40', 'CTRL', sp, dates[ent], E, risk, g2, nights=xi - ent)
            busy = end + 1; t += 1

# ============================ M41 (5m) ============================
def m41(SP5):
    for sp in SP5.values():
        sh, sl = swings(sp.h, sp.l, sp.dord, 2)
        for di in range(len(sp.dates)):
            if not sp.trade[di]: continue
            a, e = sp.ds[di], sp.de[di]; doneA = doneB = False
            js = [j for j in range(a, e) if sl[j]]; hs = [j for j in range(a, e) if sh[j]]
            for t in range(2, len(js)):
                j1, j2, j3 = js[t - 2], js[t - 1], js[t]; cf = j3 + 2
                if not (a <= cf < e and 2.0 <= sp.hr[cf] < 15.0): continue
                if not (sp.l[j1] > sp.l[j2] > sp.l[j3]): continue
                if not any(j1 < x < j2 for x in hs) or not any(j2 < x < j3 for x in hs): continue
                h0s = [x for x in hs if x < j1]
                if not h0s: continue
                H0 = sp.h[h0s[-1]]; L3 = sp.l[j3]; stop = L3 - sp.tick
                if not doneA and cf + 1 < e:
                    E = sp.o[cf + 1]; T1 = L3 + 0.5 * (H0 - L3)
                    if E > stop and E < T1 and tgt_ok(E, stop, H0) and cost_ok(sp, E, E - stop):
                        fire('M41', 'A', sp, di, cf + 1, E, stop, [(T1, 0.5), (H0, 0.5)], 16.0, False, True); doneA = True
                if not doneB:
                    H2 = sp.h[j2:j3 + 1].max(); mss = None
                    for r in range(j3 + 1, min(e, j3 + 25)):
                        if sp.l[r] < L3: break
                        if r >= cf and sp.c[r] > H2: mss = r; break
                    if mss is None: continue
                    hi = sp.h[j3:mss + 1].max(); mid = (hi + L3) / 2; fk = None
                    for k in range(j3 + 2, mss + 1):
                        if sp.l[k] > sp.h[k - 2] and sp.h[k - 2] <= mid: fk = k; break
                    if fk is None: continue
                    E = sp.l[fk]; dh = sp.h[a:mss + 1].max(); tgt = dh if dh > E else H0
                    if E <= stop or tgt <= E or not tgt_ok(E, stop, tgt) or not cost_ok(sp, E, E - stop): continue
                    for q in range(mss + 1, min(e, mss + 13)):
                        if sp.hr[q] >= 16: break
                        if sp.l[q] <= E:
                            fire('M41', 'B', sp, di, q, E, stop, [(tgt, 1.0)], 16.0, True, False); doneB = True; break
                        if sp.h[q] >= tgt: break
                if doneA and doneB: break

# ============================ M47 ENDS / 2022 model ============================
def m47(SPd):
    m = SPd[1].m; tick = SPd[1].tick
    sh1 = {s: swings(sp.h, sp.l, sp.dord, 1)[0] for s, sp in SPd.items()}
    lastsh = {s: last_known(sh1[s], SPd[s].h, 1) for s in SPd}
    thr5 = lambda P: pts(5.0, P, m); s4 = lambda P: pts(4.0, P, m); t525 = lambda P: pts(5.25, P, m)
    week = None; wins = losses = 0
    sp0 = SPd[1]
    for di in range(len(sp0.dates)):
        if not sp0.trade[di] or di not in sp0.row: continue
        r = sp0.row[di]; bias = sp0.b2[r]
        if bias == 0: continue
        sp = SPd[int(bias)]; a, e = sp.ds[di], sp.de[di]
        if sp.wkey[r] != week: week = sp.wkey[r]; wins = losses = 0
        sess = lambda x, y: (sp.l[sp.first_at(a, e, x):sp.first_at(a, e, y)])
        def lv(x, y):
            z = sess(x, y); return z.min() if len(z) else np.nan
        def hv(x, y):
            z = sp.h[sp.first_at(a, e, x):sp.first_at(a, e, y)]; return z.max() if len(z) else np.nan
        for cell in ('ENDS', 'M22'):
            traded_day = False
            for win in ('AM', 'PM'):
                if cell == 'ENDS' and (traded_day or wins >= 1 or losses >= 2): break
                if cell == 'ENDS': w0, w1 = (9.5, 11.5) if win == 'AM' else (13.5, 15.5)
                else: w0, w1 = (8.5, 11.0) if win == 'AM' else (13.5, 16.0)
                levels = [(sp.pwl[r], 0.0), (sp.pdl[r], 0.0)]
                if win == 'AM': levels.append((lv(8.5, 9.5), 9.5) if cell == 'ENDS' else (lv(0.0, 8.5), 8.5))
                else: levels += [(lv(9.5, 12.0), 12.0), (lv(12.0, 13.0), 13.0)]
                ia, ib = sp.first_at(a, e, w0), sp.first_at(a, e, w1)
                if ib - ia < 5: continue
                valid = []
                for v, fe in levels:
                    if v != v: continue
                    z = sp.l[sp.first_at(a, e, fe):ia]
                    if len(z) and z.min() <= v: continue
                    valid.append(v)
                if not valid: continue
                sw = None
                for k in range(ia, ib):
                    hit = [v for v in valid if sp.l[k] <= v - tick]
                    if hit: sw = k; break
                if sw is None: continue
                SLi = sw; res = None
                for q in range(sw, ib):
                    if sp.l[q] < sp.l[SLi]: SLi = q
                    if q == SLi: continue
                    shv = lastsh[int(bias)][SLi]
                    if shv != shv or not (sp.c[q] > shv) or not (sp.body[q] >= 1.5 * sp.mbody[q]): continue
                    lo = sp.l[SLi]; hi = sp.h[SLi:q + 1].max(); eq = (lo + hi) / 2
                    fk = None
                    for k in range(SLi + 2, q + 1):
                        if sp.bull[k] and sp.h[k - 2] <= eq: fk = k; break
                    if fk is None: continue
                    res = (q, fk); break
                if res is None: continue
                q, fk = res; top, bot = sp.l[fk], sp.h[fk - 2]
                if cell == 'ENDS':
                    E = (top - tick) if (top - bot) <= thr5(top) else (top + bot) / 2
                    stop = E - s4(E); tgt = E + t525(E)
                    if bot < stop: continue
                else:
                    E = top; stop = sp.l[fk - 2]
                    cands = [x for x in ((sp.pdh[r], hv(0.0, 8.5)) if win == 'AM' else (sp.pdh[r], hv(9.5, 12.0))) if x == x and x > E]
                    if not cands: continue
                    tgt = min(cands)
                    if not tgt_ok(E, stop, tgt): continue
                if E <= stop or not cost_ok(sp, E, E - stop): continue
                for f in range(q + 1, ib):
                    if sp.l[f] <= E:
                        g = fire('M47', cell, sp, di, f, E, stop, [(tgt, 1.0)], 16.0, True, cell == 'ENDS')
                        if cell == 'ENDS':
                            traded_day = True
                            if g > 0: wins += 1
                            else: losses += 1
                        break
                    if sp.h[f] >= tgt: break

# ============================ M48 IOFED ============================
def m48(SPd):
    sh2 = {s: swings(sp.h, sp.l, sp.dord, 2)[0] for s, sp in SPd.items()}
    lastsh = {s: last_known(sh2[s], SPd[s].h, 2) for s in SPd}
    sp0 = SPd[1]
    for di in range(len(sp0.dates)):
        if not sp0.trade[di] or di not in sp0.row: continue
        r = sp0.row[di]; bias = int(sp0.b2[r])
        if bias == 0: continue
        sp = SPd[bias]; LS = lastsh[bias]; a, e = sp.ds[di], sp.de[di]
        ia, ib = sp.first_at(a, e, 8.5), sp.first_at(a, e, 11.5); done = {'A': False, 'B': False}
        for k in range(max(ia, a + 3), ib):
            if done['A'] and done['B']: break
            if not sp.bull[k]: continue
            swh = LS[k - 1]
            if swh != swh or not (sp.c[k - 1] > swh and sp.c[k - 2] <= swh): continue
            E = sp.l[k] - sp.tick; bot = sp.h[k - 2]
            if E <= bot: continue
            tgt = sp.pdh[r] if sp.pdh[r] > E else (sp.pwh[r] if sp.pwh[r] > E else None)
            if tgt is None or tgt != tgt: continue
            fill = None
            for q in range(k + 1, min(e, k + 31)):
                if sp.hr[q] >= 16: break
                if sp.l[q] <= E: fill = q; break
                if sp.c[q] < bot: break
            if fill is None: continue
            for cell, stop in (('A', sp.l[k - 2] - sp.tick), ('B', bot - sp.tick)):
                if done[cell] or E <= stop or not tgt_ok(E, stop, tgt) or not cost_ok(sp, E, E - stop): continue
                fire('M48', cell, sp, di, fill, E, stop, [(tgt, 1.0)], 16.0, True, cell == 'A'); done[cell] = True

# ============================ M49 turtle soup into 4H FVG ============================
def m49(SPd):
    m = SPd[1].m; picks = {}
    for s, sp in SPd.items():
        bin4 = sp.dord * 6 + (sp.hr // 4).astype(int); bin1 = sp.dord * 24 + sp.hr.astype(int)
        def bars(key):
            st = np.r_[0, np.flatnonzero(np.diff(key)) + 1]; en = np.r_[st[1:], sp.N]
            H = np.maximum.reduceat(sp.h, st); Lw = np.minimum.reduceat(sp.l, st); return st, en, H, Lw
        st4, en4, H4, L4 = bars(bin4); st1, en1, H1, L1 = bars(bin1)
        n1 = len(H1); shp = np.zeros(n1, bool); slp = np.zeros(n1, bool)
        for J in range(2, n1 - 2):
            if H1[J] > max(H1[J - 2], H1[J - 1]) and H1[J] >= max(H1[J + 1], H1[J + 2]): shp[J] = True
            if L1[J] < min(L1[J - 2], L1[J - 1]) and L1[J] <= min(L1[J + 1], L1[J + 2]): slp[J] = True
        # known at base index en1[J+2] (first bar after 1H bar J+2 closes)
        kh = [(en1[J + 2], H1[J]) for J in np.flatnonzero(shp)]; kl = [(en1[J + 2], L1[J]) for J in np.flatnonzero(slp)]
        khi = np.array([x[0] for x in kh]); khv = np.array([x[1] for x in kh]); kli = np.array([x[0] for x in kl]); klv = np.array([x[1] for x in kl])
        for K in range(2, len(H4)):
            if not (L4[K] > H4[K - 2]): continue                # bull 4H FVG (BISI) in this space
            top, bot = L4[K], H4[K - 2]; ht = top - bot; ce = (top + bot) / 2; q25 = top - 0.25 * ht
            i0 = en4[K]; i1 = en4[min(len(H4) - 1, K + 30)]
            seg = np.arange(max(i0, 1), i1)
            if not len(seg): continue
            dead = np.flatnonzero(sp.l[seg] < bot)
            if len(dead): seg = seg[:dead[0]]
            ok = (sp.l[seg] <= q25) & (sp.l[seg - 1] > q25) & (sp.l[seg] > ce) & (sp.hr[seg] >= 2.0) & (sp.hr[seg] < 15.0)
            for p in seg[ok]:
                di = int(np.searchsorted(sp.ds, p, 'right') - 1)
                if not sp.trade[di]: continue
                j = np.searchsorted(kli, p, 'right') - 1
                if j < 0 or not (sp.l[p] < klv[j]): continue        # raid of the latest confirmed 1H swing low
                e = sp.de[di]; f = None
                for r in range(p + 1, e):
                    if (sp.dord[r] * 1440 + sp.hr[r] * 60) - (sp.dord[p] * 1440 + sp.hr[p] * 60) > 60: break
                    if sp.l[r] <= ce: break
                    if sp.c[r] > top: f = r; break
                if f is None or f + 1 >= e or sp.hr[f + 1] >= 16: continue
                E = sp.o[f + 1]
                jj = np.searchsorted(khi, f + 1, 'right') - 1; tgt = None
                while jj >= 0:
                    if khv[jj] > E: tgt = khv[jj]; break
                    jj -= 1
                if tgt is None: continue
                stA = ce - pts(2.0, ce, m); stB = sp.l[p:f + 1].min() - sp.tick
                for cell, stop in (('A', stA), ('B', stB)):
                    if E <= stop or not tgt_ok(E, stop, tgt) or not cost_ok(sp, E, E - stop): continue
                    key = (di, cell)
                    if key in picks and picks[key][0] <= f + 1: continue
                    picks[key] = (f + 1, s, E, stop, tgt)
    for (di, cell), (i, s, E, stop, tgt) in sorted(picks.items()):
        sp = SPd[s]
        fire('M49', cell, sp, di, i, E, stop, [(tgt, 1.0)], 16.0, False, cell == 'A')

# ============================ main ============================
def main():
    only = os.environ.get('ONLY')
    for m in ['NQ', 'ES', 'XAUUSD', 'BTCUSDT', 'ETHUSDT'] + list(L.FX_PAIRS):
        if only and m not in only.split(','): continue
        crypto = m in ('BTCUSDT', 'ETHUSDT'); n0 = len(ROWS)
        d = load(m, 'base'); SP = {s: Space(d, m, s, crypto) for s in (1, -1)}
        m39(SP); m47(SP); m48(SP); m49(SP); m40(m, d, crypto)
        if m in ('NQ', 'ES'): d5 = load(m, '5m'); SP5 = {s: Space(d5, m, s, crypto) for s in (1, -1)}
        else: SP5 = SP
        m41(SP5)
        print(m, len(d), 'rows', len(ROWS) - n0, flush=True)
    T = pd.DataFrame(ROWS)
    T.to_csv(os.path.join(os.environ.get('TMPX', '.'), 'x15e_rows.csv'), index=False)
    write(T)

def write(T):
    out = ['# X15e results — LEARN ERA ONLY (queue 39, 40, 41, 47, 48, 49). Rules: specs/X15-ict-rest.md section X15e.',
           'Groups: NQ/ES at CME futures costs and at Broker CFD costs (same trades); GOLD broker_cmdty; CRYPTO = BTC+ETH broker; FX = 4 pairs broker_fx.',
           'Pass bar per cell: NET >= +0.05R and maxDD <= 30R (FX also >= 3/4 pairs positive). CTRL = coin-flip side on cell A.', '']
    T = T.copy(); T['grpv'] = np.where(T.venue.isin(['cme', 'broker']) & T.grp.isin(['NQ', 'ES']), T.grp + '@' + T.venue, T.grp)
    passes = []; summ = []
    for gv in ['NQ@cme', 'NQ@broker', 'ES@cme', 'ES@broker', 'GOLD', 'CRYPTO', 'FX']:
        g = T[T.grpv == gv]
        if not len(g): continue
        G = g.assign(model=g.model + ' ' + gv)
        le = LEARN_END[gv.split('@')[0]]
        out.append(L.report(G, f'{gv}', le, os.devnull))
        for (mo, cell), x in g.groupby(['model', 'cell']):
            x = x.sort_values('date'); net = x.net.mean(); dd = L.maxdd(x.net.values)
            per = x.groupby('m').net.mean(); fxok = (per > 0).sum() >= 3 if gv == 'FX' else True
            ok = net >= 0.05 and dd <= 30 and fxok and cell != 'CTRL'
            summ.append(f'| {mo} | {cell} | {gv} | {len(x)} | {x.gross.mean():+.3f} | {net:+.3f} | {net*len(x):+.0f} | {dd:.0f} | '
                        + ('PASS' if ok else ('-' if cell == 'CTRL' else 'FAIL')) + ' |')
            if ok: passes.append(f'{mo}/{cell} {gv}')
    out += ['', '## Verdict table (learn)', '| model | cell | group | n | gross R | NET R/trade | NET R total | maxDD | verdict |',
            '|---|---|---|---|---|---|---|---|---|'] + summ + ['', 'PASS cells: ' + (', '.join(passes) if passes else 'none')]
    txt = chr(10).join(out); open(OUT, 'w', encoding='utf-8').write(txt + chr(10)); print(chr(10).join(summ)); print('PASS:', passes)

if __name__ == '__main__':
    main()
