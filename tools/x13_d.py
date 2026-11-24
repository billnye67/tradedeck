"""X13d: ICT batch d on Broker FX costs, LEARN era only (2012-2019). Pre-registered in specs/X13-ict-broker.md (X13d section).
Models: #34 mitigation block / failure swing (30m), #54 swing-hierarchy OTE (60m), #67 ICT ATM (60m),
#70 Charter Model 11 (5m + 60m), #71 Charter Model 12 (5m)."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools')
import ictlib as L

KZ = ((2.0, 5.0), (7.0, 10.0))
RNG = np.random.default_rng(13)


def in_kz(h):
    return (2.0 <= h < 5.0) or (7.0 <= h < 10.0)


def kz_end(h):
    for a, b in KZ:
        if a <= h < b: return b
    return None


def load(m):
    d = L.load_fx(m, '5m')
    yr = pd.to_datetime(d.date).dt.year
    d = d[(yr >= 2012) & (yr <= L.FX_LEARN_END)].reset_index(drop=True)   # exam sealed: no 2020+ bar is kept
    sd = pd.to_datetime(d.date) + pd.to_timedelta((d.hr >= 17).astype(int), unit='D')   # session date: 17:00 NY roll
    d['sid'] = pd.factorize(sd)[0]
    d['sdate'] = sd.values
    return d


def bias_tables(d):
    D = L.daily(d)
    D = D[pd.to_datetime(pd.Series(D.index)).dt.dayofweek.values != 6]          # drop Sunday-evening stubs
    B = pd.DataFrame(index=D.index)
    B['B1'] = np.sign(D.c - D.o).shift(1)
    B['B2'] = np.sign(D.c - D.c.rolling(20).mean()).shift(1)
    B['L20'] = D.l.rolling(20).min().shift(1)
    B['H20'] = D.h.rolling(20).max().shift(1)
    return B


def resample(d, minutes):
    key = pd.to_datetime(d.ts).dt.floor(f'{minutes}min')
    g = d.groupby(key, sort=True)
    R = g.agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), date=('date', 'first'), hr=('hr', 'first'),
              dow=('dow', 'first'), sid=('sid', 'first'), sid2=('sid', 'last')).reset_index(drop=True)
    R['hr'] = np.floor(R.hr * 60 / minutes) * minutes / 60
    return R, g.ngroup().values


def fractals(h, l):
    n = len(h); sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    return sh, sl   # swing at i is known after bar i+1 closes


class Arr:
    def __init__(s, D):
        s.o, s.h, s.l, s.c = (D[k].values.astype(float) for k in 'ohlc')
        s.hr = D.hr.values.astype(float); s.dow = D.dow.values; s.date = D.date.values
        s.sid = (D.sid2 if 'sid2' in D else D.sid).values; s.sid0 = D.sid.values
        s.n = len(s.h)


def sim(A, i0, side, E, sd, td, steps=(), flat=None, maxb=None, fri=False):
    """Stop-first walker from fill bar i0. Target not allowed on the fill bar; stop moves take effect from next bar.
    steps: ((profit distance, new stop distance from entry on the loss side; 0 = breakeven), ...)."""
    stop = E - side * sd; tgt = E + side * td
    end = A.n if maxb is None else min(A.n, i0 + maxb)
    d0 = A.date[i0]; si = 0
    for q in range(i0, end):
        if q > i0:
            if flat is not None and (A.date[q] != d0 or A.hr[q] >= flat): return A.c[q - 1], q - 1
            if fri and ((A.dow[q] == 4 and A.hr[q] >= 16) or A.dow[q] < A.dow[q - 1] or (A.date[q] - A.date[i0]).days > 6):
                return A.c[q - 1], q - 1
        if (A.l[q] <= stop) if side == 1 else (A.h[q] >= stop): return stop, q
        if q > i0 and ((A.h[q] >= tgt) if side == 1 else (A.l[q] <= tgt)): return tgt, q
        if q > i0:
            fav = (A.h[q] - E) if side == 1 else (E - A.l[q])
            while si < len(steps) and fav >= steps[si][0]:
                stop = E - side * steps[si][1]; si += 1
    return A.c[end - 1], end - 1


def book(rows, A, m, model, cell, i0, side, E, sd, td, steps=(), flat=None, maxb=None, fri=False):
    X, xi = sim(A, i0, side, E, sd, td, steps, flat, maxb, fri)
    g = side * (X - E) / sd
    nights = int(A.sid[xi] - A.sid0[i0])
    rows.append(dict(model=model, cell=cell, m=m, date=A.date[i0], gross=g, net=g - L.cost_R('broker_fx', m, E, sd, nights),
                     i0=i0, side=side, E=E, sd=sd, td=td, steps=steps, flat=flat, maxb=maxb, fri=fri, xi=xi))
    return xi


def control(rows, A, m, model, cell):
    out = []
    for r in [r for r in rows if r['cell'] == cell and r['m'] == m]:
        s = 1 if RNG.random() < 0.5 else -1
        book(out, A, m, model, 'RANDOM-' + cell, r['i0'], s, r['E'], r['sd'], r['td'], r['steps'], r['flat'], r['maxb'], r['fri'])
    return out


# ---------------------------------------------------------------- #34 mitigation block / failure swing, 30m
def m34(m, R, B, cell):
    A = Arr(R); p = L.pip_of(m); rows = []
    sh, sl = fractals(A.h, A.l)
    b2 = pd.Series(R.date).map(B.B2).values
    SH, SLs = [], []          # known swings (idx, price)
    cand = {1: None, -1: None}  # side -> (level L, Lidx, deadline)
    busy_until = -1
    i = 2
    while i < A.n:
        k = i - 1
        newH = sh[k]; newL = sl[k]
        if newL: SLs.append((k, A.l[k]))
        if newH: SH.append((k, A.h[k]))
        # bearish failure swing: H1 > L > H2 (H2 new), H2 < H1
        if newH and len(SH) >= 2:
            h2i, h2 = SH[-1]
            lows = [x for x in SLs if x[0] < h2i]
            if lows:
                li, lv = lows[-1]; highs = [x for x in SH[:-1] if x[0] < li]
                if highs and h2 < highs[-1][1]: cand[-1] = (lv, li, i + 24)
        if newL and len(SLs) >= 2:
            l2i, l2 = SLs[-1]
            highs = [x for x in SH if x[0] < l2i]
            if highs:
                hi_, hv = highs[-1]; lows = [x for x in SLs[:-1] if x[0] < hi_]
                if lows and l2 > lows[-1][1]: cand[1] = (hv, hi_, i + 24)
        for side in (-1, 1):
            cd = cand[side]
            if cd is None: continue
            lv, li, dl = cd
            if i > dl: cand[side] = None; continue
            broke = (A.c[i] < lv) if side == -1 else (A.c[i] > lv)
            if not broke: continue
            cand[side] = None
            if i <= busy_until or b2[i] != side: continue
            blk = None
            for q in range(li, max(li - 13, -1), -1):
                if (A.c[q] < A.o[q]) if side == -1 else (A.c[q] > A.o[q]): blk = q; break
            if blk is None: continue
            if side == -1:
                E = A.c[blk] - p; stop = max(A.h[blk] + 5 * p, E + 10 * p); far = A.h[blk]
            else:
                E = A.c[blk] + p; stop = min(A.l[blk] - 5 * p, E - 10 * p); far = A.l[blk]
            sd = abs(stop - E); ext = A.l[i] if side == -1 else A.h[i]
            for q in range(i + 1, min(i + 49, A.n)):
                hit = (A.h[q] >= E) if side == -1 else (A.l[q] <= E)
                if hit:
                    if cell == '2R': td = 2 * sd
                    else:
                        td = (E - (ext - p)) if side == -1 else ((ext + p) - E)
                        if td < sd: break
                    busy_until = book(rows, A, m, 'M34-mitblock', cell, q, side, E, sd, td, maxb=96)
                    break
                if (A.c[q] > far) if side == -1 else (A.c[q] < far): break
                ext = min(ext, A.l[q]) if side == -1 else max(ext, A.h[q])
        i += 1
    return rows, A


# ---------------------------------------------------------------- #54 swing-hierarchy OTE, 60m
def m54(m, R, B, cell):
    A = Arr(R); p = L.pip_of(m); rows = []
    sh, sl = fractals(A.h, A.l)
    b2 = pd.Series(R.date).map(B.B2).values
    STH, STL, ITH, ITL = [], [], [], []
    busy_until = -1; order = None
    for i in range(2, A.n):
        k = i - 1; trig = None
        if sh[k]:
            STH.append((k, A.h[k]))
            if len(STH) >= 3 and STH[-2][1] > STH[-3][1] and STH[-2][1] > STH[-1][1]:
                ITH.append(STH[-2])
                if len(ITH) >= 2 and ITH[-1][1] > ITH[-2][1]:
                    lo = [x for x in ITL if x[0] < ITH[-1][0]]
                    if lo: trig = (1, lo[-1], ITH[-1])
        if sl[k]:
            STL.append((k, A.l[k]))
            if len(STL) >= 3 and STL[-2][1] < STL[-3][1] and STL[-2][1] < STL[-1][1]:
                ITL.append(STL[-2])
                if len(ITL) >= 2 and ITL[-1][1] < ITL[-2][1]:
                    hi_ = [x for x in ITH if x[0] < ITL[-1][0]]
                    if hi_: trig = (-1, hi_[-1], ITL[-1])
        if trig is not None and i > busy_until:
            side, (ai, av), (bi, bv) = trig      # anchor (IT low for longs) -> extreme (IT high for longs)
            leg = abs(bv - av)
            ok = not (cell == 'mmB2' and b2[i] != side)
            if side == 1:
                E = bv - 0.705 * leg; stop = av - 2 * p; touched = A.l[bi:i + 1].min() <= av; above = A.c[i] > E
                td = leg if cell != 'p15' else (bv + 1.5 * leg - E)
            else:
                E = bv + 0.705 * leg; stop = av + 2 * p; touched = A.h[bi:i + 1].max() >= av; above = A.c[i] < E
                td = leg if cell != 'p15' else (E - (bv - 1.5 * leg))
            order = (side, E, abs(E - stop), td, av, i + 48) if (ok and not touched and above and leg > 0) else None
        if order is not None and i > busy_until and trig is None:
            side, E, sd, td, av, dl = order
            if i > dl: order = None
            elif (A.l[i] <= E) if side == 1 else (A.h[i] >= E):
                busy_until = book(rows, A, m, 'M54-swingOTE', cell, i, side, E, sd, td, maxb=120); order = None
    return rows, A


# ---------------------------------------------------------------- #67 ICT ATM, 60m
def m67(m, R, B, cell):
    A = Arr(R); p = L.pip_of(m); rows = []
    sh, sl = fractals(A.h, A.l)
    SH, SLs = [], []
    st = {-1: None, 1: None}   # side -> dict(phase, H1, L1, L1i, r, KH, KHi)
    busy_until = -1

    def first_exceed(si, sv, up, until):
        for q in range(si + 1, until + 1):
            if (A.h[q] > sv) if up else (A.l[q] < sv): return q
        return None

    for i in range(2, A.n):
        k = i - 1
        if sh[k]: SH.append((k, A.h[k]))
        if sl[k]: SLs.append((k, A.l[k]))
        # arm new candidates (only when idle / waiting for a run)
        if sh[k] and (st[-1] is None or st[-1]['ph'] == 0):
            lo = [x for x in SLs if x[0] < k]
            if lo and lo[-1][1] < A.h[k]: st[-1] = dict(ph=0, H1=A.h[k], L1=lo[-1][1], L1i=lo[-1][0])
        if sl[k] and (st[1] is None or st[1]['ph'] == 0):
            hi_ = [x for x in SH if x[0] < k]
            if hi_ and hi_[-1][1] > A.l[k]: st[1] = dict(ph=0, H1=A.l[k], L1=hi_[-1][1], L1i=hi_[-1][0])
        for side in (-1, 1):
            s = st[side]
            if s is None: continue
            if s['ph'] == 0:
                if (A.h[i] > s['H1']) if side == -1 else (A.l[i] < s['H1']):
                    s.update(ph=1, r=i, KH=(A.h[i] if side == -1 else A.l[i]), KHi=i)
                continue
            if (A.h[i] > s['KH']) if side == -1 else (A.l[i] < s['KH']): s['KH'] = A.h[i] if side == -1 else A.l[i]; s['KHi'] = i
            if i - s['r'] > 24: st[side] = None; continue
            broke = (A.c[i] < s['L1']) if side == -1 else (A.c[i] > s['L1'])
            if not broke: continue
            st[side] = None
            if i <= busy_until: continue
            E = s['L1']; stop = s['KH'] + 2 * p if side == -1 else s['KH'] - 2 * p; sd = abs(stop - E)
            # nearest unswept swing low below L1 (bear) known by bar i, lookback 120
            best = None
            for (si, sv) in (SLs if side == -1 else SH):
                if si < i - 120 or si + 1 > i: continue
                if side == -1 and sv < E and A.l[si + 1:i + 1].min() > sv and (best is None or sv > best): best = sv
                if side == 1 and sv > E and A.h[si + 1:i + 1].max() < sv and (best is None or sv < best): best = sv
            if best is None: continue
            tgt = best - p if side == -1 else best + p; td = abs(E - tgt)
            if td < sd: continue
            if cell == '2stage':
                cnt = 0
                for (si, sv) in (SH if side == -1 else SLs):
                    if si <= s['L1i'] - 48 or si >= s['r']: continue
                    fe = first_exceed(si, sv, side == -1, s['KHi'])
                    if fe is not None and s['L1i'] <= fe <= s['KHi']: cnt += 1
                if cnt < 2: continue
            for q in range(i + 1, min(i + 49, A.n)):
                if (A.h[q] >= E) if side == -1 else (A.l[q] <= E):
                    busy_until = book(rows, A, m, 'M67-ATM', cell, q, side, E, sd, td, fri=True); break
                if (A.l[q] <= tgt) if side == -1 else (A.h[q] >= tgt): break
    return rows, A


# ---------------------------------------------------------------- #70 Charter Model 11, 5m entries, 60m swings
def m70(m, d, B, cell):
    A = Arr(d); p = L.pip_of(m); rows = []
    H, hb = resample(d, 60)
    hh, hl = H.h.values, H.l.values
    sh, sl = fractals(hh, hl)
    nH = len(H)
    # latest swing known at the start of each 60m bucket k = swing idx s with s <= k-2 (confirmed by bar s+1 <= k-1)
    sl_before = np.full(nH, -1); sh_before = np.full(nH, -1); a, b = -1, -1
    for s in range(nH):
        sl_before[s] = a; sh_before[s] = b
        if sl[s]: a = s
        if sh[s]: b = s
    lastSH = np.full(nH, -1); lastSL = np.full(nH, -1); a, b = -1, -1
    for k in range(nH):
        s = k - 2
        if s >= 0:
            if sh[s]: a = s
            if sl[s]: b = s
        lastSH[k] = a; lastSL[k] = b
    psl_at = {s: sl_before[s] for s in range(nH) if sh[s]}; psh_at = {s: sh_before[s] for s in range(nH) if sl[s]}
    b2 = pd.Series(d.date).map(B.B2).values
    L20 = pd.Series(d.date).map(B.L20).values; H20 = pd.Series(d.date).map(B.H20).values
    wk = pd.Series(pd.to_datetime(d.sdate)).dt.to_period('W-SAT').values
    wopen = pd.Series(A.o).groupby(wk).transform('first').values
    days = (0, 1, 2, 3, 4) if cell == 'MonFri' else (0, 1, 2)
    steps = ((15 * p, 15 * p), (20 * p, 0.0))
    traded = None; busy_until = -1
    dayopen_i = 0
    for j in range(1, A.n):
        if A.date[j] != A.date[j - 1]: dayopen_i = j
        if not in_kz(A.hr[j]) or A.dow[j] not in days or j <= busy_until or traded == A.date[j] or j <= dayopen_i: continue
        side = b2[j]
        if side not in (1, -1) or np.isnan(L20[j]): continue
        k = hb[j]
        if side == -1:
            s = lastSH[k]
            if s < 0: continue
            psl = psl_at.get(s, -1)
            if psl < 0: continue
            SHp = hh[s]; j0 = np.searchsorted(hb, s)
            seg_l = A.l[j0:j]; seg_h = A.h[j0:j]
            if len(seg_l) == 0: continue
            li = int(np.argmin(seg_l)); LL = seg_l[li]
            if LL >= hl[psl] or SHp - LL < 30 * p: continue
            E = LL + 0.62 * (SHp - LL) - 5 * p
            TL = L20[j]
            if wopen[j] - TL < 50 * p or E - TL < 30 * p or A.l[dayopen_i:j].min() <= TL: continue
            if seg_h[li + 1:].max(initial=-np.inf) >= E or A.c[j - 1] >= E: continue
            if A.h[j] >= E:
                busy_until = book(rows, A, m, 'M70-Charter11', cell, j, -1, E, 20 * p, 30 * p, steps, flat=16.0); traded = A.date[j]
        else:
            s = lastSL[k]
            if s < 0: continue
            psh = psh_at.get(s, -1)
            if psh < 0: continue
            SLp = hl[s]; j0 = np.searchsorted(hb, s)
            seg_l = A.l[j0:j]; seg_h = A.h[j0:j]
            if len(seg_h) == 0: continue
            hi_ = int(np.argmax(seg_h)); HH = seg_h[hi_]
            if HH <= hh[psh] or HH - SLp < 30 * p: continue
            E = HH - 0.62 * (HH - SLp) + 5 * p
            TH = H20[j]
            if TH - wopen[j] < 50 * p or TH - E < 30 * p or A.h[dayopen_i:j].max() >= TH: continue
            if seg_l[hi_ + 1:].min(initial=np.inf) <= E or A.c[j - 1] <= E: continue
            if A.l[j] <= E:
                busy_until = book(rows, A, m, 'M70-Charter11', cell, j, 1, E, 20 * p, 30 * p, steps, flat=16.0); traded = A.date[j]
    return rows, A


# ---------------------------------------------------------------- #71 Charter Model 12, 5m
def nfp_block(d):
    dt = pd.to_datetime(d.date)
    first_fri = {}
    for (y, mo) in set(zip(dt.dt.year, dt.dt.month)):
        f = pd.Timestamp(y, mo, 1); f = f + pd.Timedelta(days=(4 - f.dayofweek) % 7); first_fri[(y, mo)] = f
    ff = np.array([first_fri[(y, mo)] for y, mo in zip(dt.dt.year, dt.dt.month)], dtype='datetime64[ns]')
    delta = (ff - dt.values).astype('timedelta64[D]').astype(int)
    return ((delta >= 0) & (delta <= 2) & (d.hr.values >= 7)).astype(bool)   # Wed-Fri of NFP week, NY session


def m71(m, d, B, cell):
    A = Arr(d); p = L.pip_of(m); rows = []
    b1 = pd.Series(d.date).map(B.B1).values
    hmax20 = pd.Series(A.h).rolling(20).max().shift(1).values; lmin20 = pd.Series(A.l).rolling(20).min().shift(1).values
    nfp = nfp_block(d)
    steps = ((10 * p, 10 * p), (15 * p, 0.0))
    S = {1: dict(ph=0), -1: dict(ph=0)}
    busy_until = -1; daycount = {}
    lastdown = -1; lastup = -1
    for j in range(20, A.n):
        if A.c[j] < A.o[j]: pass
        for side in (1, -1):
            s = S[side]
            allowed = cell == 'nobias' or b1[j] == side
            imp = (A.h[j] > hmax20[j]) if side == 1 else (A.l[j] < lmin20[j])
            if s['ph'] in (0, 1) and imp and allowed:
                ob = lastdown if side == 1 else lastup
                if ob >= j - 20 and ob >= 0:
                    seg = A.h[ob:j + 1].max() if side == 1 else A.l[ob:j + 1].min()
                    S[side] = s = dict(ph=1, oh=A.h[ob], ol=A.l[ob], IH=seg, armed=j)
                    continue
            if s['ph'] == 1:
                s['IH'] = max(s['IH'], A.h[j]) if side == 1 else min(s['IH'], A.l[j])
                if (A.c[j] < s['ol']) if side == 1 else (A.c[j] > s['oh']): S[side] = dict(ph=0); continue
                touch = (A.l[j] <= s['oh']) if side == 1 else (A.h[j] >= s['ol'])
                if touch:
                    if not in_kz(A.hr[j]) or not allowed: S[side] = dict(ph=0); continue
                    s.update(ph=2, rt=j, RL=(A.l[j] if side == 1 else A.h[j]), RLi=j, kze=kz_end(A.hr[j]), kzd=A.date[j])
                continue
            if s['ph'] == 2:
                if (A.c[j] < s['ol']) if side == 1 else (A.c[j] > s['oh']): S[side] = dict(ph=0); continue
                if j - s['rt'] > 24: S[side] = dict(ph=0); continue
                if (A.l[j] < s['RL']) if side == 1 else (A.h[j] > s['RL']):
                    s['RL'] = A.l[j] if side == 1 else A.h[j]; s['RLi'] = j
                if (A.c[j] > s['IH']) if side == 1 else (A.c[j] < s['IH']):
                    LH = A.h[s['RLi']:j + 1].max() if side == 1 else A.l[s['RLi']:j + 1].min()
                    mid = s['RL'] + 0.5 * (LH - s['RL'])
                    E = None
                    for q in range(s['RLi'] + 2, j + 1):
                        if side == 1 and A.h[q - 2] < A.l[q] and A.l[q] <= mid: E = A.l[q]; break
                        if side == -1 and A.l[q - 2] > A.h[q] and A.h[q] >= mid: E = A.h[q]; break
                    if E is None: S[side] = dict(ph=0); continue
                    s.update(ph=3, E=E); continue
                continue
            if s['ph'] == 3:
                if A.date[j] != s['kzd'] or A.hr[j] >= s['kze']: S[side] = dict(ph=0); continue
                if (A.l[j] < s['RL']) if side == 1 else (A.h[j] > s['RL']):
                    # same bar may also fill: stop-first says the RL break voids; conservative skip
                    S[side] = dict(ph=0); continue
                if (A.l[j] <= s['E']) if side == 1 else (A.h[j] >= s['E']):
                    S[side] = dict(ph=0)
                    if j <= busy_until or nfp[j] or daycount.get(A.date[j], 0) >= 2: continue
                    daycount[A.date[j]] = daycount.get(A.date[j], 0) + 1
                    busy_until = book(rows, A, m, 'M71-Charter12', cell, j, side, s['E'], 20 * p, 20 * p, steps, flat=16.0)
        if A.c[j] < A.o[j]: lastdown = j
        if A.c[j] > A.o[j]: lastup = j
    return rows, A


if __name__ == '__main__':
    out = []
    plan = [('M34-mitblock', m34, 30, ('lowx', '2R'), '2R'), ('M54-swingOTE', m54, 60, ('mm', 'p15', 'mmB2'), 'mm'),
            ('M67-ATM', m67, 60, ('base', '2stage'), 'base'), ('M70-Charter11', m70, 5, ('MonFri', 'MonWed'), 'MonFri'),
            ('M71-Charter12', m71, 5, ('B1', 'nobias'), 'B1')]
    for m in L.FX_PAIRS:
        d = load(m); B = bias_tables(d)
        R30, _ = resample(d, 30); R60, _ = resample(d, 60)
        for name, fn, tf, cells, ctl in plan:
            src = {30: R30, 60: R60, 5: d}[tf]
            for cell in cells:
                rows, A = fn(m, src, B, cell)
                out += rows
                if cell == ctl: out += control(rows, A, m, name, cell)
            print(m, name, 'done', flush=True)
    T = pd.DataFrame(out)
    T.drop(columns=['steps']).to_csv(r'C:\Users\you\AppData\Local\Temp\x13d_trades.csv', index=False)
    path = r'C:\Users\you\Desktop\TradeDeck\specs\X13d-results.md'
    L.report(T[['model', 'cell', 'm', 'date', 'gross', 'net']], 'X13d results (learn 2012-2019, Broker FX costs)', L.FX_LEARN_END, path)
    lines = ['\n## Verdicts (pass bar: NET >= +0.05R, maxDD <= 30R, >= 3/4 pairs positive)']
    for (mo, cell), g in T.groupby(['model', 'cell']):
        g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values); pp = int((g.groupby('m').net.mean() > 0).sum())
        v = 'PASS' if (net >= 0.05 and dd <= 30 and pp >= 3) else 'FAIL'
        lines.append(f'{mo}/{cell}: n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R pairs+ {pp}/4 -> {v}')
    txt = '\n'.join(lines); print(txt)
    open(path, 'a', encoding='utf-8').write(txt + '\n')
