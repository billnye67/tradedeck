"""X15g: queue ranks 59, 60, 65, 66, 69, 72, 73, 76, 77 on NQ/ES/gold/BTC/ETH/FX (pre-registered in specs/X15-ict-rest.md).
LEARN ERA ONLY: every series is truncated at load to its learn era; no exam-era bar is ever read into the models.
Every model is coded for the long side on a price space; shorts = the same code on the negated space (h'=-l, l'=-h)."""
import os, sys, math, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
from multiprocessing import Pool

OUT = r'C:\Users\you\Desktop\TradeDeck\specs\X15g-results.md'
H40 = 480

def load_gold():
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

def trunc(d, le):
    d = d[pd.to_datetime(d.date).dt.year <= le].reset_index(drop=True); return d

# ---------------------------------------------------------------- spaces
def space(d, sgn, rolls):
    """Arrays for one price space. sgn=+1 real, -1 negated (shorts become longs)."""
    S = {}
    if sgn == 1: o, h, l, c = d.o.values, d.h.values, d.l.values, d.c.values
    else: o, h, l, c = -d.o.values, -d.l.values, -d.h.values, -d.c.values
    o, h, l, c = [np.asarray(x, float) for x in (o, h, l, c)]
    S.update(o=o, h=h, l=l, c=c, ol=o.tolist(), hl=h.tolist(), ll=l.tolist(), cl=c.tolist(), sgn=sgn)
    hr = d.hr.values.astype(float); dow = d.dow.values.astype(int)
    dint = pd.to_datetime(d.date).values.astype('datetime64[D]').astype(np.int64)
    T = dint * 24.0 + hr
    S.update(hr=hr, hrl=hr.tolist(), dow=dow, dint=dint, T=T, date=d.date.values, n=len(d))
    S['sess'] = np.floor((T - 17.0) / 24.0).astype(np.int64)
    rf = np.array([x in rolls for x in d.date.values]) if rolls else np.zeros(len(d), bool)
    S['rollcum'] = np.cumsum(rf)
    # per-date bounds
    chg = np.r_[0, np.where(dint[1:] != dint[:-1])[0] + 1]; ends = np.r_[chg[1:] - 1, len(d) - 1]
    S['dstart'] = dict(zip(dint[chg].tolist(), chg.tolist())); S['dend'] = dict(zip(dint[chg].tolist(), ends.tolist()))
    def end_before(X):
        e = np.full(len(d), -1)
        for s0, e0 in zip(chg, ends):
            w = np.where(hr[s0:e0 + 1] < X)[0]
            if len(w): e[s0:e0 + 1] = s0 + w[-1]
        return e
    S['end16'] = end_before(16.0); S['end95'] = end_before(9.5)
    # daily table (weekdays only)
    wk = dow <= 4
    D = pd.DataFrame(dict(di=dint[wk], o=o[wk], h=h[wk], l=l[wk], c=c[wk])).groupby('di').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    rng = (D.h - D.l)
    D['b1'] = np.sign(D.c - D.o).shift(1); D['b2'] = np.sign(D.c.shift(1) - D.c.rolling(20).mean().shift(1))
    D['adr5'] = rng.rolling(5).mean().shift(1); D['adr20'] = rng.rolling(20).mean().shift(1); D['pc'] = D.c.shift(1)
    S['D'] = D; S['drow'] = {k: i for i, k in enumerate(D.index.tolist())}
    # 5m fractals
    n = len(d); fh = np.zeros(n, bool); fl = np.zeros(n, bool)
    fh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); fl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    S['fh'] = fh; S['fl'] = fl
    LFH = np.full(n, np.nan); last = np.nan
    for k in range(n):
        if k >= 2 and fh[k - 1]: last = h[k - 1]
        LFH[k] = last
    S['LFH'] = LFH
    # 15m bars
    key = dint * 96 + np.floor(hr * 4 + 1e-9).astype(np.int64)
    st = np.r_[0, np.where(key[1:] != key[:-1])[0] + 1]; en = np.r_[st[1:] - 1, n - 1]
    H15 = np.maximum.reduceat(h, st); L15 = np.minimum.reduceat(l, st); C15 = c[en]
    grp = np.repeat(np.arange(len(st)), en - st + 1)
    ph = pd.Series(h).groupby(grp).idxmax().values; pl = pd.Series(l).groupby(grp).idxmin().values
    S.update(st15=st, en15=en, H15=H15, L15=L15, C15=C15, ph15=ph, pl15=pl)
    m = len(st); f15h = np.zeros(m, bool); f15l = np.zeros(m, bool)
    f15h[1:-1] = (H15[1:-1] > H15[:-2]) & (H15[1:-1] > H15[2:]); f15l[1:-1] = (L15[1:-1] < L15[:-2]) & (L15[1:-1] < L15[2:])
    ev = {}   # 5m confirm index -> list of (type, price, pos5, j15)
    for j in np.where(f15h)[0]: ev.setdefault(int(en[j + 1]), []).append(('H', H15[j], int(ph[j]), int(j)))
    for j in np.where(f15l)[0]: ev.setdefault(int(en[j + 1]), []).append(('L', L15[j], int(pl[j]), int(j)))
    S['ev15'] = ev
    return S

def cand(cell, q, E, stop, tgt, end, limit=True, cexit=False, lvl=False, sig=None, day=None):
    return dict(cell=cell, q=q, E=E, stop=stop, tgt=tgt, end=end, limit=limit, cexit=cexit, lvl=lvl, sig=sig, day=day)

def find_fill(S, E, q0, q1, void_close=None, hr_max=None, void_low=None):
    l, c, hr = S['ll'], S['cl'], S['hrl']; q1 = min(q1, S['n'] - 1)
    for q in range(q0, q1 + 1):
        if hr_max is not None and hr[q] >= hr_max: return None
        if l[q] <= E: return q
        if void_close is not None and c[q] < void_close: return None
        if void_low is not None and l[q] < void_low: return None
    return None

# ---------------------------------------------------------------- models (long side on the given space)
def m59(S, P):
    out = []; h, l, hr, T = S['hl'], S['ll'], S['hrl'], S['T']; D, drow = S['D'], S['drow']; fh = S['fh']; LFH = S['LFH']
    for di, s0 in S['dstart'].items():
        e0 = S['dend'][di]
        if S['dow'][s0] > 4 or di not in drow: continue
        r = drow[di]; adr5 = D.adr5.iat[r]
        if not adr5 == adr5: continue
        a0, a1 = np.searchsorted(S['T'], [di * 24 - 5, di * 24 + 5])
        fx = [i for i in range(a0, min(a1, S['n'] - 1)) if fh[i]]
        lv = []
        for x in range(len(fx)):
            for y in range(x + 1, len(fx)):
                i, j = fx[x], fx[y]
                if abs(h[i] - h[j]) <= 0.03 * adr5: lv.append((max(h[i], h[j]), j))
        if not lv: continue
        mins = 10.0 if P['m'] == 'NQ' else 0.04 * adr5
        for t in range(s0 + 2, e0 + 1):
            if hr[t - 2] < 7.0: continue
            if hr[t] >= 9.0: break
            if not (l[t] > h[t - 2] and l[t] - h[t - 2] >= mins): continue
            ref = LFH[t - 2]
            if not (ref == ref and max(h[t - 1], h[t]) > ref): continue
            E = l[t]; best = None
            for lev, j in lv:
                if lev > E and j < t and max(h[j + 1:t + 1]) <= lev and (best is None or lev < best): best = lev
            if best is None: continue
            stop = min(l[t - 5:t - 1]) - P['tick']
            q = find_fill(S, E, t + 1, e0, hr_max=9.0)
            c = cand('A', q, E, stop, best, S['end95'][q] if q is not None else -1, lvl=True, sig=t, day=di)
            out.append(c); out.append(dict(c, cell='B', skip=not D.b1.iat[r] > 0))
            break
    return out

def m60(S, P):
    out = []; o, h, l, c, hr = S['ol'], S['hl'], S['ll'], S['cl'], S['hrl']; D, drow = S['D'], S['drow']
    for di, s0 in S['dstart'].items():
        e0 = S['dend'][di]
        if S['dow'][s0] > 4 or di not in drow: continue
        r = drow[di]; idx = range(s0, e0 + 1)
        orb = [i for i in idx if 9.5 <= hr[i] < 10.0]
        if len(orb) < 3: continue
        ORH = max(h[i] for i in orb); ORL = min(l[i] for i in orb)
        # TS
        poked = False; lo = math.inf
        for k in idx:
            if hr[k] < 10.0: continue
            if hr[k] >= 12.0: break
            lo = min(lo, l[k])
            if l[k] < ORL: poked = True
            if poked and c[k] > ORL:
                q = k + 1
                if q <= e0 and hr[q] < 16.0:
                    out.append(cand('TS', q, o[q], lo - P['tick'], ORH, S['end16'][q], limit=False, lvl=True, sig=k, day=di))
                else: out.append(cand('TS', None, 0, 0, 0, -1, sig=k, day=di))
                break
        # DR (long side = B2 bullish)
        if not D.b2.iat[r] > 0: continue
        rb = [i for i in idx if 7.0 <= hr[i] < 9.0]; pb = [i for i in idx if 9.0 <= hr[i] < 9.5]
        if len(rb) < 6 or len(pb) < 2: continue
        RH = max(h[i] for i in rb); RL = min(l[i] for i in rb); rg = RH - RL
        if rg <= 0: continue
        if min(l[i] for i in orb) < min(l[i] for i in pb) and min(l[i] for i in orb) > RH - 0.75 * rg:
            q = next((i for i in idx if hr[i] >= 10.0), None)
            if q is not None and hr[q] < 10.1:
                out.append(cand('DR', q, o[q], RL - P['tick'], RH + rg, S['end16'][q], limit=False, lvl=True, sig=q, day=di))
    return out

def breaker_setups(S):
    """C1 structure: yields (L1, H, iH5, L2, iL2, k_mss)."""
    h, l = S['hl'], S['ll']; ev = S['ev15']; lastFL = None; arm = None; res = []
    for k in range(S['n']):
        if arm is not None:
            if not arm['raid']:
                if l[k] < arm['L1']: arm.update(raid=True, L2=l[k], iL2=k)
            else:
                if l[k] < arm['L2']: arm.update(L2=l[k], iL2=k)
                elif h[k] > arm['H'] and k - arm['iL2'] <= 24:
                    res.append((arm['L1'], arm['H'], arm['iH5'], arm['L2'], arm['iL2'], k, arm['iL1'])); arm = None
                elif k - arm['iL2'] > 24: arm = None
        for typ, price, pos, j in ev.get(k, ()):
            if typ == 'L': lastFL = (price, pos)
            elif lastFL is not None and lastFL[1] < pos and (arm is None or not arm['raid']):
                arm = dict(L1=lastFL[0], iL1=lastFL[1], H=price, iH5=pos, raid=False)
    return res

def m65_66u(S, P, setups):
    out65, out66 = [], []; o, h, l, c = S['ol'], S['hl'], S['ll'], S['cl']; tk = P['tick']; n = S['n']
    for L1, H, iH5, L2, iL2, k, _ in setups:
        win = [i for i in range(max(0, iH5 - 2), min(n, iH5 + 3)) if c[i] > o[i]]
        if not win: continue
        b = max(win, key=lambda i: h[i]); bh, bl = h[b], l[b]
        q = find_fill(S, bh, k + 1, k + 36, void_close=bl)
        if q is not None:
            end = min(q + H40, n - 1)
            out65.append(cand('A', q, bh, L2 - tk, None, end, sig=k)); out65[-1]['R'] = 3.0
            out65.append(cand('B', q, bh, bl - tk, None, end, sig=k)); out65[-1]['R'] = 3.0
        for t in range(iL2 + 1, min(k + 2, n)):
            if t >= 2 and l[t] > h[t - 2] and min(l[t], bh) - max(h[t - 2], bl) >= tk:
                E = (l[t] + h[t - 2]) / 2
                q = find_fill(S, E, t + 1, t + 36, void_close=bl)
                if q is not None:
                    out66.append(cand('U', q, E, L2 - tk, None, min(q + H40, n - 1), sig=t)); out66[-1]['R'] = 3.0
                break
    return out65, out66

def m66r(S, P):
    out = []; h, l, c = S['hl'], S['ll'], S['cl']; ev = S['ev15']; tk = P['tick']; n = S['n']
    lastFL = None; arm = None
    for k in range(n):
        if arm is not None:
            if arm['st'] == 0:
                if l[k] < arm['A']: arm.update(st=1, L2=l[k], ir=k)
            else:
                arm['L2'] = min(arm['L2'], l[k])
                if c[k] > arm['top']:
                    q = find_fill(S, arm['ce'], k + 1, k + 36)
                    if q is not None:
                        L2 = min(arm['L2'], min(l[k + 1:q])) if q > k + 1 else arm['L2']
                        out.append(cand('R', q, arm['ce'], L2 - tk, arm['B'] + (arm['B'] - arm['A']), min(q + H40, n - 1), lvl=True, sig=k))
                    arm = None
                elif k - arm['ir'] > 96: arm = None
        for typ, price, pos, j in ev.get(k, ()):
            if typ == 'L': lastFL = (price, pos)
            elif lastFL is not None and lastFL[1] < pos and (arm is None or arm['st'] == 0):
                A, B = lastFL[0], price; rp = None
                for t in range(lastFL[1] + 2, pos + 1):
                    if l[t] > h[t - 2] and l[t] < (A + B) / 2: rp = t; break
                arm = None if rp is None else dict(A=A, B=B, top=l[rp], ce=(l[rp] + h[rp - 2]) / 2, st=0)
    return out

def m69(S, P):
    out = []; o, h, l, c = S['ol'], S['hl'], S['ll'], S['cl']; fh, fl = S['fh'], S['fl']; tk = P['tick']; n = S['n']
    lastFH = None
    for iS in range(1, n - 2):
        if fh[iS - 1]: lastFH = h[iS - 1]   # fractal high at iS-1 is confirmed by the close of iS
        if not fl[iS] or lastFH is None: continue
        S0 = l[iS]; STH = lastFH; k = None
        for x in range(iS + 1, min(iS + 25, n)):
            if l[x] < S0: break
            if c[x] > STH: k = x; break
        if k is None: continue
        t = next((t for t in range(iS + 2, min(k + 2, n)) if l[t] > h[t - 2]), None)
        if t is None: continue
        ob = next((i for i in range(t - 2, iS - 1, -1) if c[i] < o[i]), None)
        if ob is None: continue
        E = l[ob] - tk
        if E <= S0 - tk: continue
        Hi = max(h[iS:max(k, t) + 1]); q = None
        for x in range(max(k, t) + 1, min(k + 37, n)):
            pct = (Hi - E) / (Hi - S0) if Hi > S0 else -1
            if 0.62 <= pct <= 0.705 and l[x] <= E: q = x; break
            if l[x] < S0: break
            Hi = max(Hi, h[x])
        if q is None: continue
        end = min(q + H40, n - 1)
        out.append(cand('A', q, E, S0 - tk, Hi, end, lvl=True, sig=iS))
        c2 = cand('B', q, E, S0 - tk, None, end, sig=iS); c2['R'] = 2.8; out.append(c2)
    return out

def m72if(S, P):
    out = []; h, l, c = S['hl'], S['ll'], S['cl']; ev = S['ev15']; tk = P['tick']; n = S['n']
    st15, en15, L15, C15 = S['st15'], S['en15'], S['L15'], S['C15']
    j_of = np.repeat(np.arange(len(st15)), en15 - st15 + 1)
    lastFL = None; cf, pf, tf = untaken_arrays(S)
    for k in range(n):
        j = j_of[k]
        if k == en15[j] and lastFL is not None and L15[j] < lastFL and C15[j] > lastFL:
            raid = L15[j]; lastFL = None; s5 = st15[j]; t0 = None
            for t in range(s5 - 1, max(1, s5 - 36) - 1, -1):
                if h[t] < l[t - 2] and h[t] > raid: t0 = t; break
            if t0 is not None:
                top, bot = l[t0 - 2], h[t0]
                v = next((x for x in range(k + 1, min(k + 37, n)) if c[x] > top), None)
                if v is not None:
                    q = find_fill(S, top, v + 1, v + 36, void_close=bot)
                    if q is not None:
                        # untaken highs known by close of q-1
                        mk = (cf <= q - 1) & (tf >= q) & (pf > top)
                        if mk.any():
                            out.append(cand('IF', q, top, bot - tk, pf[mk].min() - tk, min(q + H40, n - 1), lvl=True, sig=k))
        for typ, price, pos, jj in ev.get(k, ()):
            if typ == 'L': lastFL = price
    return out

def untaken_arrays(S):
    """15m fractal highs: confirm index, price, first 5m index after confirmation with h > price (n if never)."""
    h = S['h']; n = S['n']; cf, pf, tf = [], [], []
    for k, lst in S['ev15'].items():
        for typ, price, pos, jj in lst:
            if typ != 'H': continue
            x = k + 1; tk = n
            while x < n:
                w = np.where(h[x:x + 2000] > price)[0]
                if len(w): tk = x + int(w[0]); break
                x += 2000
            cf.append(k); pf.append(price); tf.append(tk)
    return np.array(cf), np.array(pf), np.array(tf)

def m72md(S, B, P):
    """B = intraday space (CME 1m, else same 5m). Daily table from S (weekday NY dates)."""
    out = []; D = S['D']; tk = P['tick']
    o, h, l, c, hr = B['ol'], B['hl'], B['ll'], B['cl'], B['hrl']
    Dh, Dl, Do, Dc, adr20 = D.h.values, D.l.values, D.o.values, D.c.values, D.adr20.values; idx = D.index.values
    gaps = []   # (m, gap_bot, gap_top, mid_of_m) formed by daily triplet (m-1, m, m+1), known after m+1
    for r in range(len(D)):
        # gaps known at today's open: m+1 <= r-1
        m = r - 2
        if m >= 1 and Dc[m] < Do[m] and Dh[m] - Dl[m] >= adr20[m] and Dh[m + 1] < Dl[m - 1]:
            gaps.append(dict(m=m, bot=Dh[m + 1], top=Dl[m - 1], mid=(Dh[m] + Dl[m]) / 2))
        # delivery: any daily high >= gap bottom on days m+2..r-1
        if r >= 1: gaps = [g for g in gaps if not (g['m'] + 2 <= r - 1 and Dh[r - 1] >= g['bot'])]
        gaps = [g for g in gaps if r - (g['m'] + 1) <= 10]
        if not gaps or not D.b1.iat[r] > 0: continue
        di = int(idx[r])
        if di not in B['dstart']: continue
        g = gaps[-1]; s0, e0 = B['dstart'][di], B['dend'][di]
        i8 = next((i for i in range(s0, e0 + 1) if hr[i] >= 8.0), None)
        if i8 is None or hr[i8] >= 8.1 or not o[i8] < g['mid']: continue
        sib = []; done = False
        for v in range(s0 + 2, e0 + 1):
            if hr[v] >= 8.5: break
            if hr[v] >= 8.0:
                for (top, t) in sib:
                    if c[v] > top:
                        E = c[v]; stop = min(l[t - 2:t + 1]) - tk; q = v + 1
                        if q <= e0 and stop < E and hr[q] < 16:
                            out.append(cand('MD', q, E, stop, (g['bot'] + g['top']) / 2, B['end16'][q], limit=False, lvl=True, sig=v, day=di))
                        done = True; break
                if done: break
            if hr[v - 2] >= 7.0 and h[v] < l[v - 2]: sib.append((l[v - 2], v))
    return out

def m73(S, P):
    out = []; o, h, l, c = S['ol'], S['hl'], S['ll'], S['cl']; T = S['T']; tk = P['tick']; n = S['n']
    gi = [g for g in range(13, n) if T[g] - T[g - 1] >= 0.75]
    for x, g in enumerate(gi):
        if not o[g] > h[g - 1]: continue
        E = c[g - 1]; nxt = gi[x + 1] - 1 if x + 1 < len(gi) else n - 1
        q = find_fill(S, E, g, min(g + 275, nxt))
        if q is None: continue
        stop = min(l[g - 12:g]) - tk; end = min(q + 288, n - 1)
        out.append(cand('A', q, E, stop, h[g], end, cexit=True, lvl=True, sig=g))
        c2 = cand('B', q, E, stop, None, end, cexit=True, sig=g); c2['R'] = 2.0; out.append(c2)
    return out

def nice_bf(x):
    k = 10 ** math.floor(math.log10(x)); best = k
    for f in (1, 2, 5, 10):
        if f * k <= x: best = f * k
    return best

def m76(S, P):
    out = []; h, l, hr = S['hl'], S['ll'], S['hrl']; ev = S['ev15']; D, drow = S['D'], S['drow']; n = S['n']
    lastFL = None; prevFH = None
    for k in range(n):
        for typ, price, pos, j in ev.get(k, ()):
            if typ == 'L': lastFL = (price, pos); continue
            SH, pFH = price, prevFH; prevFH = price
            if pFH is None or not SH > pFH or lastFL is None or lastFL[1] >= pos: continue
            if S['dow'][k] > 4: continue
            kz = 5.0 if 2.0 <= hr[k] < 5.0 else (11.0 if 8.5 <= hr[k] < 11.0 else None)
            di = int(S['dint'][k])
            if kz is None or di not in drow: continue
            r = drow[di]
            if not D.b2.iat[r] > 0: continue
            pc = abs(D.pc.iat[r])
            if not pc == pc: continue
            BF = P['bf'] if P['bf'] else nice_bf(0.01 * pc); half = BF / 2
            SL = lastFL[0]; leg = SH - SL
            if leg < 0.15 * BF: continue
            zlo, zhi = SH - 0.79 * leg, SH - 0.62 * leg
            lev = math.floor(zhi / half + 1e-9) * half
            if lev < zlo - 1e-12: continue
            E = lev; stop = SL - 0.05 * BF
            q = find_fill(S, E, k + 1, S['dend'][di], hr_max=kz)
            if q is None: continue
            nx = math.ceil(E / BF + 1e-9) * BF
            end = S['end16'][q]
            out.append(cand('A', q, E, stop, nx - 0.03 * BF, end, lvl=True, sig=k))
            out.append(cand('B', q, E, stop, nx + 0.10 * BF, end, lvl=True, sig=k))
    return out

def m77(S, P):
    out = []; h, l, hr = S['hl'], S['ll'], S['hrl']; fh = S['fh']; tk = P['tick']; n = S['n']; dint = S['dint']
    for di, s0 in S['dstart'].items():
        e0 = S['dend'][di]
        if S['dow'][s0] > 4: continue
        fx = []   # (index, price) fractal highs confirmed so far today
        for t in range(s0 + 2, e0 + 1):
            if t - 1 > s0 and fh[t - 1]: fx.append(h[t - 1])
            if hr[t - 2] < 9.5: continue
            if hr[t] >= 15.5: break
            if not l[t] > h[t - 2]: continue
            E = l[t]; ab = [p for p in fx if p > E]
            if not ab: continue
            tg = min(ab)
            q = find_fill(S, E, t + 1, t + 10, hr_max=15.5)
            if q is None or q > e0: continue
            end = S['end16'][q]
            out.append(cand('A', q, E, l[t - 2] - tk, tg, end, lvl=True, sig=t))
            out.append(cand('B', q, E, E - (tg - E), tg, end, lvl=True, sig=t))
    return out

# ---------------------------------------------------------------- execution
def sim(S, q, E, stop, tgt, end, limit, cexit):
    h, l, c = S['hl'], S['ll'], S['cl']
    if limit:
        if l[q] <= stop: return stop, q
        k0 = q + 1
    else: k0 = q
    for k in range(k0, end + 1):
        if l[k] <= stop: return stop, k
        if tgt is not None and h[k] >= tgt: return tgt, k
        if cexit and k > q and c[k] < E: return c[k], k
    return c[end], end

def execute(S, cd, P):
    q = cd['q']
    if cd.get('skip') or q is None or cd['end'] < q or S['dow'][q] > 4: return None
    E, stop = cd['E'], cd['stop']; risk = E - stop
    if not risk > 0: return None
    tgt = cd['tgt'] if cd.get('R') is None else E + cd['R'] * risk
    if cd['lvl'] and tgt - E < risk: return None
    if L.cost_R(P['prim'], P['m'], abs(E), risk) > 0.25: return None
    if P['cme'] and S['date'][q] in P['rolls']: return None
    xp, xi = sim(S, q, E, stop, tgt, cd['end'], cd['limit'], cd['cexit'])
    if P['cme'] and S['rollcum'][xi] - S['rollcum'][q] > 0: return None
    return dict(q=q, xi=xi, E=abs(E), risk=risk, tgt=tgt, gross=(xp - E) / risk, nights=int(S['sess'][xi] - S['sess'][q]),
                date=S['date'][q], limit=cd['limit'], cexit=cd['cexit'], end=cd['end'], stop=stop, Eraw=E)

def first_per_day(cands):
    """keep, per day, the earliest-signal candidate across both spaces (for 'first of day' models)."""
    best = {}
    for sp, cd in cands:
        key = (cd['cell'], cd['day'])
        if key not in best or cd['sig'] < best[key][1]['sig']: best[key] = (sp, cd)
    return list(best.values())

def run_market(name):
    P = dict(MK[name])
    d = P['loader'](); d = trunc(d, P['le'])
    rolls = L.roll_dates(d) if P['cme'] else set(); P['rolls'] = rolls
    Sp, Sn = space(d, 1, rolls), space(d, -1, rolls)
    if P['cme']:
        d1 = trunc(L.load_cme(P['m'], '1min'), P['le']); r1 = L.roll_dates(d1)
        B1p, B1n = space(d1, 1, r1), space(d1, -1, r1)
    else: B1p, B1n = Sp, Sn
    sp = {1: Sp, -1: Sn}; sp1 = {1: B1p, -1: B1n}
    C = {}   # model -> list of (sgn, cand, space)
    def add(model, sg, lst, SS): C.setdefault(model, []).extend((sg, x, SS) for x in lst)
    for sg in (1, -1):
        S = sp[sg]; B = sp1[sg]
        add('59 Asia-FVG', sg, m59(S, P), S)
        add('60 OR-fades', sg, m60(S, P), S)
        st = breaker_setups(S); a, b = m65_66u(S, P, st)
        add('65 Breaker', sg, a, S); add('66 Unicorn/Reaper', sg, b, S); add('66 Unicorn/Reaper', sg, m66r(S, P), S)
        add('69 FVG+OB+OTE', sg, m69(S, P), S)
        add('72 IFVG', sg, m72if(S, P), S); add('72 IFVG', sg, m72md(S, B, P), B)
        if not P['crypto']: add('73 Vacuum', sg, m73(S, P), S)
        add('76 RoundOTE', sg, m76(S, P), S)
        add('77 Pinball', sg, m77(B, P), B)
    rows = []; rng = np.random.default_rng(15 + sum(map(ord, name)))
    for model, lst in C.items():
        if model.startswith(('59', '60')):
            byday = {}
            for sg, cd, SS in lst: byday.setdefault((cd['cell'], cd['day']), []).append((sg, cd, SS))
            lst = [min(v, key=lambda z: z[1]['sig']) for v in byday.values()]
        ex = {}
        for sg, cd, SS in lst:
            r = execute(SS, cd, P)
            if r is not None: r.update(sg=sg, SS=SS, other=sp1[-sg] if SS is sp1[sg] and SS is not sp[sg] else sp[-sg]); ex.setdefault(cd['cell'], []).append(r)
        cells = sorted(ex)
        for cell in cells:
            acc = []; last = -1
            for r in sorted(ex[cell], key=lambda z: (z['q'], z['sg'])):
                if r['q'] > last: acc.append(r); last = r['xi']
            for r in acc: rows.append(row(model, cell, P, r, r['gross']))
            if cell == cells[0]:
                for r in acc:
                    if rng.random() < 0.5: rows.append(row(model, 'COIN', P, r, r['gross']))
                    else:
                        O = r['other']; E2 = -r['Eraw']; d2 = r['tgt'] - r['Eraw']
                        xp, xi = sim(O, r['q'], E2, E2 - r['risk'], E2 + d2, r['end'], r['limit'], r['cexit'])
                        rr = dict(r, gross=(xp - E2) / r['risk'], nights=int(O['sess'][xi] - O['sess'][r['q']]))
                        rows.append(row(model, 'COIN', P, rr, rr['gross']))
    return name, rows

def row(model, cell, P, r, g):
    out = []
    for v in P['venues']:
        out.append(dict(model=model, cell=cell, m=P['m'], venue=v, date=r['date'], gross=g, net=g - L.cost_R(v, P['m'], r['E'], r['risk'], r['nights'])))
    return out

MK = {
    'NQ': dict(m='NQ', loader=lambda: L.load_cme('NQ', '5min'), le=L.CME_LEARN_END, cme=True, crypto=False, tick=0.25, prim='cme', venues=('cme', 'broker'), bf=None, grp='NQ'),
    'ES': dict(m='ES', loader=lambda: L.load_cme('ES', '5min'), le=L.CME_LEARN_END, cme=True, crypto=False, tick=0.25, prim='cme', venues=('cme', 'broker'), bf=None, grp='ES'),
    'XAUUSD': dict(m='XAUUSD', loader=load_gold, le=2022, cme=False, crypto=False, tick=0.01, prim='broker_cmdty', venues=('broker_cmdty',), bf=None, grp='GOLD'),
    'BTCUSDT': dict(m='BTCUSDT', loader=lambda: L.load_crypto('BTCUSDT', '5m'), le=L.CRYPTO_LEARN_END, cme=False, crypto=True, tick=0.1, prim='broker', venues=('broker',), bf=None, grp='CRYPTO'),
    'ETHUSDT': dict(m='ETHUSDT', loader=lambda: L.load_crypto('ETHUSDT', '5m'), le=L.CRYPTO_LEARN_END, cme=False, crypto=True, tick=0.01, prim='broker', venues=('broker',), bf=None, grp='CRYPTO'),
}
for fxm in L.FX_PAIRS:
    MK[fxm] = dict(m=fxm, loader=(lambda z=fxm: L.load_fx(z, '5m')), le=L.FX_LEARN_END, cme=False, crypto=False, tick=L.pip_of(fxm) / 10,
                   prim='broker_fx', venues=('broker_fx',), bf=100 * L.pip_of(fxm), grp='FX')

if __name__ == '__main__':
    only = sys.argv[1:] or list(MK)
    with Pool(min(len(only), 9)) as pool:
        res = pool.map(run_market, only)
    rows = [x for _, rs in res for rr in rs for x in rr]
    T = pd.DataFrame(rows); T.to_pickle(os.path.join(os.environ.get('TEMP', '.'), 'x15g_rows.pkl'))
    txt = []; verdict = ['\n# PASS/FAIL per cell (learn): NET >= +0.05R and maxDD <= 30R; FX also >= 3/4 pairs positive', '']
    groups = [('NQ', 'cme'), ('NQ', 'broker'), ('ES', 'cme'), ('ES', 'broker'), ('GOLD', 'broker_cmdty'), ('CRYPTO', 'broker'), ('FX', 'broker_fx')]
    grp_of = {k: v['grp'] for k, v in MK.items()}; le_of = {'NQ': L.CME_LEARN_END, 'ES': L.CME_LEARN_END, 'GOLD': 2022, 'CRYPTO': L.CRYPTO_LEARN_END, 'FX': L.FX_LEARN_END}
    T['grp'] = T.m.map(grp_of)
    for gname, ven in groups:
        G = T[(T.grp == gname) & (T.venue == ven)]
        if not len(G): continue
        txt.append(L.report(G[['model', 'cell', 'm', 'date', 'gross', 'net']], f'X15g — {gname} @ {ven}', le_of[gname], os.devnull))
        for (mo, cell), g in G.groupby(['model', 'cell']):
            g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values)
            ok = net >= 0.05 and dd <= 30
            if gname == 'FX': pos = (g.groupby('m').net.mean() > 0).sum(); ok = ok and pos >= 3
            verdict.append(f'{"PASS" if ok else "fail"} | {gname}@{ven} | {mo}/{cell} | n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R')
    open(OUT, 'w', encoding='utf-8').write('\n\n'.join(txt) + '\n' + '\n'.join(verdict) + '\n')
    print('\n'.join(verdict))
