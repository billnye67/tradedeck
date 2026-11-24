"""X13e: ICT batch e (queue ranks 43, 44, 57, 68, 74, 75) at Broker FX costs. Rules: specs/X13-ict-broker.md section X13e.
Learn era only: every bar dated 2020+ is dropped at load, so the exam cannot be computed here."""
import sys, datetime as dt
sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools')
import numpy as np, pandas as pd, ictlib as X

END = dt.date(2020, 1, 1)
RNG = np.random.default_rng(13)
ROWS = []

def load(m, tf):
    d = X.load_fx(m, tf); d = d[d.date < END].reset_index(drop=True); return d

def daily_of(d, nmin):
    D = X.daily(d); D = D[D.n >= nmin].copy()
    D['sma20'] = D.c.rolling(20).mean(); D['b1'] = np.sign(D.c - D.o); D['b2'] = np.sign(D.c - D.sma20)
    D['adr20'] = (D.h - D.l).rolling(20).mean()
    return D

def add(model, cell, m, date, side, E, stop, gross, nights):
    risk = abs(E - stop)
    if risk <= 0 or not np.isfinite(gross): return
    ROWS.append(dict(model=model, cell=cell, m=m, date=date, gross=gross, net=gross - X.cost_R('broker_fx', m, abs(E), risk, nights)))

# ---------- intraday executor: stop-first, partial targets, gaps at the open ----------
def walk2(A, i, side, E, stop, tgts, max_bars=None, flat=None, fill_bar=True):
    h, l, c, o, hr, dd = A['h'], A['l'], A['c'], A['o'], A['hr'], A['date']
    risk = abs(E - stop); left = 1.0; g = 0.0; nights = 0; ti = 0; d0 = dd[i]
    end = len(h) if max_bars is None else min(len(h), i + max_bars + 1)
    q = i
    if fill_bar:
        if (l[i] <= stop) if side == 1 else (h[i] >= stop): return side * (stop - E) / risk, 0, i
        q = i + 1
    else:
        q = i + 1
    while q < end:
        if hr[q] >= 17 and hr[q - 1] < 17: nights += 1
        if flat is not None and (hr[q] >= flat or dd[q] != d0):
            return g + left * side * (c[q - 1] - E) / risk, nights, q
        if (o[q] <= stop) if side == 1 else (o[q] >= stop): return g + left * side * (o[q] - E) / risk, nights, q
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return g + left * side * (stop - E) / risk, nights, q
        while ti < len(tgts):
            tp, fr = tgts[ti]
            if (h[q] >= tp) if side == 1 else (l[q] <= tp):
                px = o[q] if ((o[q] >= tp) if side == 1 else (o[q] <= tp)) else tp
                g += fr * side * (px - E) / risk; left -= fr; ti += 1
            else: break
        if left <= 1e-9: return g, nights, q
        q += 1
    q = max(i, end - 1); return g + left * side * (c[q] - E) / risk, nights, q

def fire(model, cell, m, A, i, side, E, stop, tgts, ctrl, **kw):
    """Run a trade; if ctrl, also run the coin-flip control with stop/targets reflected around E."""
    g, n, x = walk2(A, i, side, E, stop, tgts, **kw)
    add(model, cell, m, A['date'][i], side, E, stop, g, n)
    if ctrl:
        s2 = side if RNG.random() < 0.5 else -side
        st2 = stop if s2 == side else 2 * E - stop
        tg2 = tgts if s2 == side else [(2 * E - tp, fr) for tp, fr in tgts]
        g2, n2, _ = walk2(A, i, s2, E, st2, tg2, **kw)
        add(model, 'CTRL_' + cell, m, A['date'][i], s2, E, st2, g2, n2)
    return x

def arrs(d, sign=1):
    """Arrays dict; sign=-1 gives the price-negated mirror (bear logic written once as bull logic)."""
    if sign == 1: return dict(o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values, hr=d.hr.values, date=d.date.values)
    return dict(o=-d.o.values, h=-d.l.values, l=-d.h.values, c=-d.c.values, hr=d.hr.values, date=d.date.values)

# ======================= M43 monthly OB swing set (daily) =======================
def m43(m, D):
    pip = X.pip_of(m); dates = list(D.index); o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values
    ym = np.array([d.year * 12 + d.month for d in dates]); n = len(dates)
    lo40 = pd.Series(l).rolling(40).min().shift(1).values; lo20 = pd.Series(l).rolling(20).min().shift(1).values
    hi40 = pd.Series(h).rolling(40).max().shift(1).values; hi20 = pd.Series(h).rolling(20).max().shift(1).values
    M = D.assign(ym=ym).groupby('ym').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    first = {}; last = {}
    for k, v in enumerate(ym):
        first.setdefault(v, k); last[v] = k
    mk = list(M.index)

    def run_trade(k, side, E, stop, tgt):
        risk0 = abs(E - stop)
        if (l[k] <= stop) if side == 1 else (h[k] >= stop): return side * (stop - E) / risk0, 0
        best = h[k] if side == 1 else l[k]
        for q in range(k + 1, min(n, k + 121)):
            half = (best - E) * side >= 0.5 * abs(tgt - E)
            if side == 1:
                tr = lo20[q] if half else lo40[q]; stop = max(stop, tr) if tr == tr else stop
            else:
                tr = hi20[q] if half else hi40[q]; stop = min(stop, tr) if tr == tr else stop
            if (o[q] <= stop) if side == 1 else (o[q] >= stop): return side * (o[q] - E) / risk0, q - k
            if (l[q] <= stop) if side == 1 else (h[q] >= stop): return side * (stop - E) / risk0, q - k
            if (h[q] >= tgt) if side == 1 else (l[q] <= tgt):
                px = o[q] if ((o[q] >= tgt) if side == 1 else (o[q] <= tgt)) else tgt; return side * (px - E) / risk0, q - k
            best = max(best, h[q]) if side == 1 else min(best, l[q])
        q = min(n, k + 121) - 1; return side * (c[q] - E) / risk0, q - k

    for j in range(3, len(mk)):
        cur = mk[j]
        if mk[j - 3] != cur - 3: continue
        prev3 = M.loc[[cur - 3, cur - 2, cur - 1]]
        for side in (1, -1):
            cand = prev3[(prev3.c < prev3.o)] if side == 1 else prev3[(prev3.c > prev3.o)]
            if not len(cand): continue
            ob_ym = cand.index[-1]; ob = cand.iloc[-1]
            tgt = prev3.h.max() if side == 1 else prev3.l.min()
            k0 = last[ob_ym] + 1; ks, ke = first[cur], last[cur]
            for cell, lvl in (('A', ob.h if side == 1 else ob.l), ('B', ob.o)):
                conf = False; canc = False
                for k in range(k0, ke + 1):
                    if k >= ks and conf and not canc:
                        hit = (l[k] <= lvl) if side == 1 else (h[k] >= lvl)
                        if hit:
                            E = min(o[k], lvl) if side == 1 else max(o[k], lvl)
                            stop = lo40[k] if side == 1 else hi40[k]
                            if stop != stop or (E - stop) * side < 10 * pip or (tgt - E) * side <= 0: break
                            g, nt = run_trade(k, side, E, stop, tgt); add('M43', cell, m, dates[k], side, E, stop, g, nt)
                            if cell == 'A':
                                s2 = side if RNG.random() < 0.5 else -side
                                st2, tg2 = (stop, tgt) if s2 == side else (2 * E - stop, 2 * E - tgt)
                                g2, n2 = run_trade(k, s2, E, st2, tg2); add('M43', 'CTRL_A', m, dates[k], s2, E, st2, g2, n2)
                            break
                    if (h[k] > ob.h) if side == 1 else (l[k] < ob.l): conf = True
                    if (c[k] < ob.l) if side == 1 else (c[k] > ob.h): canc = True

# ======================= M44 rolling HTF level sweep-and-return (daily) =======================
def m44(m, D):
    pip = X.pip_of(m); dates = list(D.index); o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values; n = len(dates)
    wk = np.array([(d - dt.timedelta(days=d.weekday())).toordinal() for d in dates]); ym = np.array([d.year * 12 + d.month for d in dates])
    W = pd.DataFrame(dict(w=wk, h=h, l=l)).groupby('w').agg(h=('h', 'max'), l=('l', 'min'))
    Mo = pd.DataFrame(dict(ym=ym, h=h, l=l)).groupby('ym').agg(h=('h', 'max'), l=('l', 'min'))
    def cal_levels(k):
        w = wk[k] - 7; y = ym[k]; up, dn = [], []
        if w in W.index: up.append(W.h[w]); dn.append(W.l[w])
        for span in (1, 3, 12):
            ks = [y - s for s in range(1, span + 1)]
            if all(x in Mo.index for x in ks): up.append(Mo.h[ks].max()); dn.append(Mo.l[ks].min())
        return up, dn
    def ipda_levels(k):
        if k < 60: return [], []
        return [h[k - N:k].max() for N in (20, 40, 60)], [l[k - N:k].min() for N in (20, 40, 60)]

    def run_trade(e, E, side, stop, tgt):
        risk = abs(E - stop)
        for q in range(e, min(n, e + 20)):
            if q > e and ((o[q] <= stop) if side == 1 else (o[q] >= stop)): return side * (o[q] - E) / risk, q - e
            if (l[q] <= stop) if side == 1 else (h[q] >= stop): return side * (stop - E) / risk, q - e
            if (h[q] >= tgt) if side == 1 else (l[q] <= tgt):
                px = o[q] if q > e and ((o[q] >= tgt) if side == 1 else (o[q] <= tgt)) else tgt; return side * (px - E) / risk, q - e
        q = min(n, e + 20) - 1; return side * (c[q] - E) / risk, q - e + 1

    for cell, fn in (('CAL', cal_levels), ('IPDA', ipda_levels)):
        LV = [fn(k) for k in range(n)]
        for k in range(1, n - 1):
            up, dn = LV[k]
            if not up: continue
            su = [x for x in up if h[k] > x and c[k] < x]; sd = [x for x in dn if l[k] < x and c[k] > x]
            if (su and sd) or (not su and not sd): continue
            side = -1 if su else 1; e = k + 1; E = o[e]
            stop = h[k] + 5 * pip if side == -1 else l[k] - 5 * pip
            up2, dn2 = LV[e]
            tg = [x for x in (dn2 if side == -1 else up2) if (x - E) * side > 0]
            if not tg or (E - stop) * side <= 0: continue
            tgt = max(tg) if side == -1 else min(tg)
            g, nt = run_trade(e, E, side, stop, tgt); add('M44', cell, m, dates[e], side, E, stop, g, nt)
            if cell == 'CAL':
                s2 = side if RNG.random() < 0.5 else -side
                st2, tg2 = (stop, tgt) if s2 == side else (2 * E - stop, 2 * E - tgt)
                g2, n2 = run_trade(e, E, s2, st2, tg2); add('M44', 'CTRL_CAL', m, dates[e], s2, E, st2, g2, n2)

# ======================= M57 Sick Sister EURUSD vs GBPUSD (5m) =======================
def m57(d5):
    P = ('EURUSD', 'GBPUSD')
    a = d5['EURUSD'][['ts', 'o', 'h', 'l', 'c', 'date', 'hr']]; b = d5['GBPUSD'][['ts', 'o', 'h', 'l', 'c']]
    J = a.merge(b, on='ts', suffixes=('_E', '_G')).reset_index(drop=True)
    Ds = {p: daily_of(d5[p], 200) for p in P}
    A = {p: dict(o=J['o_' + p[0]].values, h=J['h_' + p[0]].values, l=J['l_' + p[0]].values, c=J['c_' + p[0]].values,
                 hr=J.hr.values, date=J.date.values) for p in P}
    Am = {p: dict(o=-A[p]['o'], h=-A[p]['l'], l=-A[p]['h'], c=-A[p]['c'], hr=A[p]['hr'], date=A[p]['date']) for p in P}
    hr = J.hr.values
    for date, g in J.groupby('date'):
        idx = g.index.values
        if any(date not in Ds[p].index for p in P): continue
        pv = {}
        for p in P:
            k = Ds[p].index.get_loc(date)
            if k < 21: break
            pv[p] = Ds[p].iloc[k - 1]
        if len(pv) < 2: continue
        for sgn in (1, -1):   # 1 = bull (leader takes PDH); -1 = bear, run in the price-negated mirror
            S = A if sgn == 1 else Am
            ref = {p: (pv[p].h if sgn == 1 else -pv[p].l) for p in P}
            hi_cum = {p: np.maximum.accumulate(S[p]['h'][idx]) for p in P}
            ev = None
            for lead, lag in (('EURUSD', 'GBPUSD'), ('GBPUSD', 'EURUSD')):
                hc = hi_cum[lead]
                if not (hc > ref[lead]).any(): continue
                f = int(np.argmax(hc > ref[lead])); q = idx[f]
                if not (2 <= hr[q] < 11): continue
                if hi_cum[lag][f] >= ref[lag]: continue
                if ev is None or q < ev[0]: ev = (q, lead, lag, f)
            if ev is None: continue
            q, lead, lag, f = ev; s = S[lag]; pip = X.pip_of(lag)
            lo = s['l'][idx[:f + 1]].min(); hi = s['h'][idx[:f + 1]].max(); tgt = ref[lag]
            entries = {'OTE': hi - 0.705 * (hi - lo)}
            dc = [r for r in range(max(idx[0], q - 11), q + 1) if s['c'][r] < s['o'][r]]
            if dc: entries['OC'] = s['o'][dc[-1]]
            for cell, bias in (('OTE_B1', 'b1'), ('OTE_B2', 'b2'), ('OC_B2', 'b2')):
                bv = pv[lag][bias]
                if bv != bv or sgn * bv <= 0: continue
                lim = entries.get(cell[:cell.index('_')])
                if lim is None: continue
                stop = lo - 5 * pip; fill = None
                for r in range(q + 1, idx[-1] + 1):
                    if hr[r] >= 13: break
                    if s['h'][r] >= tgt: break
                    if s['o'][r] <= lim: fill, E = r, s['o'][r]; break
                    if s['l'][r] <= lim: fill, E = r, lim; break
                if fill is None or E <= stop or tgt <= E: continue
                fire('M57', cell, lag, s, fill, 1, E, stop, [(tgt, 1.0)], cell == 'OTE_B2', flat=16)

# ======================= M68 market maker buy/sell model (15m) =======================
def m68(m, d15):
    pip = X.pip_of(m)
    for sgn in (1, -1):
        A = arrs(d15, sgn); h, l, c, o, hr = A['h'], A['l'], A['c'], A['o'], A['hr']; n = len(h)
        pm = pd.Series(l).rolling(96).min().shift(1).values; pT = pd.Series(h).rolling(96).max().shift(1).values
        fl = np.zeros(n, bool); fh = np.zeros(n, bool)
        fl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:]); fh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:])
        fhi = np.where(fh)[0]
        busy = {'CARD': -1, 'PLAN': -1}
        for i in np.where(fl & (l < pm))[0]:
            if i < 100 or i + 2 >= n: continue
            L = l[i]; T = pT[i]; R = T - L
            if R < 40 * pip: continue
            jj = fhi[fhi < i]
            if not len(jj): continue
            sh = h[jj[-1]]; hmax = h[i]; msb = None
            for q in range(i + 1, min(n, i + 97)):
                hmax = max(hmax, h[q])
                if l[q] < L or hmax >= T: break
                if q >= i + 2 and c[q] > sh and hmax >= L + 0.2 * R: msb = q; break
            if msb is None: continue
            fv = [k for k in range(i + 2, msb + 1) if l[k] > h[k - 2]]
            if not fv: continue
            k = fv[-1]; top, bot = l[k], h[k - 2]
            for cell in ('CARD', 'PLAN'):
                if busy[cell] > msb: continue
                if cell == 'CARD': lim, stop = (top + bot) / 2, L - 2 * pip
                else: lim, stop = top + 5 * pip, L - 20 * pip
                fill = None
                for r in range(msb + 1, min(n, msb + 49)):
                    if h[r] >= T: break
                    if cell == 'PLAN' and not (2 <= hr[r] < 5 or 8.5 <= hr[r] < 11): continue
                    if o[r] <= lim: fill, E = r, o[r]; break
                    if l[r] <= lim: fill, E = r, lim; break
                if fill is None: busy[cell] = msb + 48; continue
                if E <= stop or T - E < 30 * pip: busy[cell] = fill; continue
                tg = [(T + 10 * pip, 1.0)] if cell == 'CARD' else [(E + 20 * pip, 1 / 3), (E + 40 * pip, 1 / 3), (E + 60 * pip, 1 / 3)]
                busy[cell] = fire('M68', cell, m, A, fill, 1, E, stop, tg, cell == 'CARD', max_bars=480)

# ======================= M74 consolidation false-break fade to EQ (15m) =======================
def m74(m, d15, D):
    pip = X.pip_of(m); A = arrs(d15); h, l, c, hr, dd = A['h'], A['l'], A['c'], A['hr'], A['date']; n = len(h)
    RH = pd.Series(h).rolling(24).max().shift(1).values; RL = pd.Series(l).rolling(24).min().shift(1).values
    prev = D[['b1', 'b2', 'adr20']].shift(1)
    pmap = {k: v for k, v in zip(prev.index, prev.itertuples(index=False))}
    for cell in ('B1', 'B2'):
        done = set(); q = 24
        while q < n:
            d = dd[q]
            if not (2 <= hr[q] < 12) or d in done or d not in pmap: q += 1; continue
            p = pmap[d]; b = p.b1 if cell == 'B1' else p.b2
            if b != b or b == 0 or p.adr20 != p.adr20 or RH[q] - RL[q] > 0.35 * p.adr20: q += 1; continue
            mid = (RH[q] + RL[q]) / 2
            if b < 0 and h[q] > RH[q] and c[q] < RH[q] and c[q] > mid:
                done.add(d); q = fire('M74', cell, m, A, q, -1, c[q], h[q] + 2 * pip, [(mid, 1.0)], cell == 'B2', flat=16, fill_bar=False) + 1; continue
            if b > 0 and l[q] < RL[q] and c[q] > RL[q] and c[q] < mid:
                done.add(d); q = fire('M74', cell, m, A, q, 1, c[q], l[q] - 2 * pip, [(mid, 1.0)], cell == 'B2', flat=16, fill_bar=False) + 1; continue
            q += 1

# ======================= M75 trend-line phantom fade (1h) =======================
def m75(m, d15, D):
    pip = X.pip_of(m)
    g = d15.assign(k=pd.to_datetime(d15.ts).dt.floor('h')).groupby('k')
    H1 = g.agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), date=('date', 'first'), hr=('hr', 'first')).reset_index(drop=True)
    H1['hr'] = np.floor(H1.hr)
    b2 = D.b2.shift(1); b2map = dict(zip(b2.index, b2.values))
    for sgn in (1, -1):   # 1: rising line of lows -> short; -1 (mirror): falling line of highs -> long
        A = arrs(H1, sgn); h, l, c, dd = A['h'], A['l'], A['c'], A['date']; n = len(h)
        pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
        atr = pd.Series(tr).rolling(14).mean().values
        f = np.zeros(n, bool); f[2:-2] = (l[2:-2] < l[1:-3]) & (l[2:-2] < l[:-4]) & (l[2:-2] < l[3:-1]) & (l[2:-2] < l[4:])
        F = np.where(f)[0]; busy = {'P3': -1, 'P1': -1}
        for t in range(2, len(F)):
            j1, j2, j3 = F[t - 2], F[t - 1], F[t]; c3 = j3 + 2
            if c3 >= n or not (l[j1] < l[j2] < l[j3]) or j2 - j1 < 4 or j3 - j2 < 4: continue
            a = atr[c3]
            if a != a: continue
            sl = (l[j2] - l[j1]) / (j2 - j1)
            if abs(l[j3] - (l[j1] + sl * (j3 - j1))) > 0.2 * a: continue
            js = np.arange(j1 + 1, j3)
            if (l[js] < l[j1] + sl * (js - j1) - 0.2 * a).any(): continue
            H23 = h[j2:j3 + 1].max(); trig = None
            for r in range(c3 + 1, min(n, c3 + 49)):
                if c[r] < l[j3]: break
                if h[r] > H23 and c[r] < H23: trig = r; break
            if trig is None: continue
            bb = b2map.get(dd[trig], np.nan)
            if bb != bb or sgn * bb > 0: continue
            E = c[trig]; stop = h[trig] + 5 * pip
            for cell, tp in (('P3', l[j3] - 2 * pip), ('P1', l[j1] - 2 * pip)):
                if busy[cell] >= trig or E <= tp: continue
                busy[cell] = fire('M75', cell, m, A, trig, -1, E, stop, [(tp, 1.0)], cell == 'P3', max_bars=120, fill_bar=False)

# ======================= run =======================
if __name__ == '__main__':
    d5 = {}
    for m in X.FX_PAIRS:
        d15 = load(m, '15m'); D = daily_of(d15, 64)
        m43(m, D); m44(m, D); m68(m, d15); m74(m, d15, D); m75(m, d15, D)
        if m in ('EURUSD', 'GBPUSD'): d5[m] = load(m, '5m')
        print(m, len(ROWS), flush=True)
    m57(d5)
    T = pd.DataFrame(ROWS)
    assert pd.to_datetime(T.date).dt.year.max() <= X.FX_LEARN_END
    X.report(T, 'X13e results - ICT batch e (ranks 43, 44, 57, 68, 74, 75), Broker FX costs, LEARN 2012-2019 only',
             X.FX_LEARN_END, r'C:\Users\you\Desktop\TradeDeck\specs\X13e-results.md')
