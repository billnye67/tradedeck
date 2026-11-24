"""X15 batch b: ICT models ranks 14, 21, 22, 23, 24 on NQ/ES/gold/crypto/FX (pre-registered in specs/X15-ict-rest.md).
LEARN ONLY: bars from the exam era are cut at load and never enter memory."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
RNG = np.random.default_rng(15)
OUT = r'C:\Users\you\Desktop\TradeDeck\specs\X15b-results.md'

def load_gold():  # copied from x14_sb.py
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

# ---------------------------------------------------------------- markets
def MK():
    out = []
    for m, fl, nd in (('NQ', 8.0, 4.0), ('ES', 2.0, 1.0)):
        out.append(dict(name=m, venues=[('cme', m), ('broker', m + '@broker')], tick=lambda E: 0.25, pip5=lambda E: 1.25,
                        floor=(lambda f: lambda E: f)(fl), ndog=nd, cme=True, start='2020-01-01', end='2024-01-01',
                        d1=(lambda m: lambda: L.load_cme(m, '1min'))(m), d5=(lambda m: lambda: L.load_cme(m, '5min'))(m)))
    out.append(dict(name='XAUUSD', venues=[('broker_cmdty', 'GOLD')], tick=lambda E: 0.01, pip5=lambda E: 0.5, floor=lambda E: 1.0,
                    ndog=0.5, cme=False, start='2019-01-01', end='2023-01-01', d1=load_gold, d5=None))
    for m in ('BTCUSDT', 'ETHUSDT'):
        out.append(dict(name=m, venues=[('broker', 'CRYPTO')], tick=lambda E: abs(E) * 1e-5, pip5=lambda E: abs(E) * 5e-4,
                        floor=lambda E: abs(E) * 0.0025, ndog=None, cme=False, start='2020-01-01', end='2024-01-01',
                        d1=(lambda m: lambda: L.load_crypto(m, '5m'))(m), d5=None, crypto=True))
    for m in L.FX_PAIRS:
        p = L.pip_of(m)
        out.append(dict(name=m, venues=[('broker_fx', 'FX')], tick=(lambda p: lambda E: 0.1 * p)(p), pip5=(lambda p: lambda E: 5 * p)(p),
                        floor=(lambda p: lambda E: 5 * p)(p), ndog=None, cme=False, start='2012-01-01', end='2020-01-01',
                        d1=(lambda m: lambda: L.load_fx(m, '5m'))(m), d5=None))
    return out

def prep(d, mk):
    dd = pd.to_datetime(pd.Series(d.date))
    d = d[((dd >= mk['start']) & (dd < mk['end'])).values].reset_index(drop=True)   # exam era never kept
    A = {k: d[k].values.astype(float) for k in ('o', 'h', 'l', 'c', 'hr')}
    A['dow'] = d.dow.values.astype(int); A['date'] = d.date.values
    A['dord'] = pd.to_datetime(pd.Series(d.date)).values.astype('datetime64[D]').astype(np.int64)
    A['tday'] = A['dord'] + (A['hr'] >= 17)
    A['rolls'] = {pd.Timestamp(x).to_datetime64().astype('datetime64[D]').astype(np.int64) for x in L.roll_dates(d)} if mk['cme'] else set()
    ch = np.r_[True, A['dord'][1:] != A['dord'][:-1]]; A['ds'] = np.flatnonzero(ch); A['de'] = np.r_[A['ds'][1:], len(ch)]
    # daily for B2 and ATR (shifted: known at the day's open)
    D = d.groupby('date').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    b2 = np.sign(D.c - D.c.rolling(20).mean()).shift(1); atr = (D.h - D.l).rolling(14).mean().shift(1)
    keys = pd.to_datetime(pd.Series(D.index)).values.astype('datetime64[D]').astype(np.int64)
    A['b2'] = dict(zip(keys, b2.values)); A['atr'] = dict(zip(keys, atr.values))
    return A

def orient(A, s):
    """T-space arrays: s=1 real, s=-1 negated (short logic becomes long logic)."""
    if s == 1: o, h, l, c = A['o'], A['h'], A['l'], A['c']
    else: o, h, l, c = -A['o'], -A['l'], -A['h'], -A['c']
    fh = np.zeros(len(h), bool); fl = np.zeros(len(h), bool)
    fh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); fl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    fvb = np.zeros(len(h), bool); fvb[2:] = h[:-2] < l[2:]
    fvs = np.zeros(len(h), bool); fvs[2:] = l[:-2] > h[2:]
    return dict(o=o, h=h, l=l, c=c, fh=fh, fl=fl, fvb=fvb, fvs=fvs, hr=A['hr'], s=s)

# ---------------------------------------------------------------- simulation
def sim(A, q, side, E, stop, tgts, wts, end, be=None):
    h, l, c = A['h'], A['l'], A['c']; risk = abs(E - stop)
    if (l[q] <= stop) if side == 1 else (h[q] >= stop): return -1.0, q
    st = stop; rem = 1.0; pnl = 0.0; ti = 0; moved = False; end = max(end, q + 1)
    for k in range(q + 1, end):
        if (l[k] <= st) if side == 1 else (h[k] >= st):
            return (pnl + rem * side * (st - E)) / risk, k
        while ti < len(tgts) and ((h[k] >= tgts[ti]) if side == 1 else (l[k] <= tgts[ti])):
            pnl += wts[ti] * side * (tgts[ti] - E); rem -= wts[ti]; ti += 1
        if rem <= 1e-9: return pnl / risk, k
        if be is not None and not moved and ((h[k] > be) if side == 1 else (l[k] < be)): st = E; moved = True
    k = end - 1; return (pnl + rem * side * (c[k] - E)) / risk, k

def _add(rows, mk, A, model, cell, q, x, E, risk, g):
    nights = int(A['tday'][x] - A['tday'][q])
    for venue, label in mk['venues']:
        rows.append(dict(model=label, cell=f'{model}{cell}', m=mk['name'], date=A['date'][q], gross=g,
                         net=g - L.cost_R(venue, mk['name'], E, risk, nights)))

def emit(rows, mk, A, model, cell, q, side, E, stop, tgts, wts, end, be=None, control=False):
    risk = abs(E - stop)
    if risk <= 0 or risk < mk['floor'](E): return None
    g, x = sim(A, q, side, E, stop, tgts, wts, end, be); _add(rows, mk, A, model, cell, q, x, E, risk, g)
    if control:
        s = int(RNG.choice([-1, 1])); t2 = [E + s * abs(t - E) for t in tgts]; be2 = None if be is None else E + s * abs(be - E)
        g2, x2 = sim(A, q, s, E, E - s * risk, t2, wts, end, be2); _add(rows, mk, A, model, cell + '_RANDOM', q, x2, E, risk, g2)
    return x

def first_at(hr, a, b, t):
    """first index in [a,b) with hr >= t, else b."""
    k = a + np.searchsorted(hr[a:b], t - 1e-9); return min(k, b)

# ---------------------------------------------------------------- b14 dynamic canvas
def m14(A, mk, rows, cell):
    h, l, c, hr = A['h'], A['l'], A['c'], A['hr']
    qk = A['dord'] * 96 + np.floor(hr * 4 + 1e-9).astype(np.int64)
    ch = np.r_[True, qk[1:] != qk[:-1]]; st = np.flatnonzero(ch); bid = np.cumsum(ch) - 1
    H, Lw = np.maximum.reduceat(h, st), np.minimum.reduceat(l, st); n = len(st)
    bot = np.full(n, np.nan); top = np.full(n, np.nan)
    bu = np.zeros(n, bool); bu[2:] = H[:-2] < Lw[2:]; be = np.zeros(n, bool); be[2:] = Lw[:-2] > H[2:]
    bot[bu] = H[np.flatnonzero(bu) - 2]; top[bu] = Lw[bu]; bot[be] = H[be]; top[be] = Lw[np.flatnonzero(be) - 2]
    l1 = np.full(n, -1); l2 = np.full(n, -1); a = b = -1
    for j in range(n):
        if bu[j] or be[j]: b, a = a, j
        l1[j], l2[j] = a, b
    for ds, de in zip(A['ds'], A['de']):
        if A['dord'][ds] in A['rolls'] or A['dow'][ds] > 4: continue
        bias = A['b2'].get(A['dord'][ds], np.nan)
        if cell == 'B' and not bias in (1.0, -1.0): continue
        k0 = first_at(hr, ds, de, 9.5); k1 = first_at(hr, ds, de, 15.75); end = first_at(hr, ds, de, 16.0); busy = -1
        for k in range(max(k0, 1), k1):
            if k <= busy: continue
            j = bid[k] - 1
            if j < 0 or l2[j] < 0: continue
            f1, f2 = l1[j], l2[j]
            U, D = (f1, f2) if bot[f1] > bot[f2] else (f2, f1)
            if top[D] >= bot[U]: continue
            for side in (-1, 1):
                if cell == 'B' and side != bias: continue
                if side == -1 and c[k - 1] < bot[U] and h[k] >= bot[U]: E, stop, tgt = bot[U], top[U] + mk['tick'](bot[U]), top[D]
                elif side == 1 and c[k - 1] > top[D] and l[k] <= top[D]: E, stop, tgt = top[D], bot[D] - mk['tick'](top[D]), bot[U]
                else: continue
                if abs(tgt - E) < abs(E - stop): continue
                x = emit(rows, mk, A, 'b14', cell, k, side, E, stop, [tgt], [1.0], end, control=(cell == 'A'))
                if x is not None: busy = x; break

# ---------------------------------------------------------------- b21 lunch macro
def k21(T, i10, i1210, pip5, floor):
    h, l, fh, fl, fvb = T['h'], T['l'], T['fh'], T['fl'], T['fvb']
    Lb = next((j for j in range(max(i10, 1), i1210 - 1) if fl[j]), None)
    if Lb is None: return None
    Lv = l[Lb]; tgt = h[i10:Lb + 1].max(); H1b = None
    for k in range(Lb + 1, i1210):
        if l[k] < Lv: return None
        if k - 1 > Lb and fh[k - 1]: H1b = k - 1; break
    if H1b is None: return None
    mss = None
    for k in range(H1b + 2, i1210):
        if l[k] < Lv: return None
        if h[k] > h[H1b]: mss = k; break
    if mss is None: return None
    DLb = H1b + int(np.argmin(l[H1b:mss + 1])); DL = l[DLb]
    i = next((i for i in range(min(mss + 2, i1210 - 2), DLb + 1, -1) if fvb[i]), None)
    if i is None: return None
    s0 = max(i, mss)   # order is live only once both the MSS and the FVG are known
    if h[mss:s0 + 1].max() >= tgt: return None
    E = l[i]; stop = DL - pip5(E)
    if not (E > stop) or tgt - E < E - stop: return None
    for q in range(s0 + 1, i1210):
        if l[q] <= E: return q, E, stop, tgt
        if h[q] >= tgt: return None
    return None

def m21(A, mk, rows, cell):
    o, h, l, c, hr = A['o'], A['h'], A['l'], A['c'], A['hr']; TT = {1: orient(A, 1), -1: orient(A, -1)}
    for ds, de in zip(A['ds'], A['de']):
        if A['dord'][ds] in A['rolls'] or A['dow'][ds] > 4: continue
        i930 = first_at(hr, ds, de, 9.5); i10 = first_at(hr, ds, de, 10.0)
        if i930 >= de or abs(hr[i930] - 9.5) > 1e-6 or i10 - i930 < 5: continue
        i1210 = first_at(hr, ds, de, 12 + 10 / 60); i1330 = first_at(hr, ds, de, 13.5)
        hi, lo = h[i930:i10].max(), l[i930:i10].min(); rg = hi - lo; cl = c[i10 - 1]
        if cell == 'A':
            hb = i930 + int(np.argmax(h[i930:i10])); lb = i930 + int(np.argmin(l[i930:i10]))
            if hr[hb] < 9.5 + 5 / 60 - 1e-9 and cl < lo + rg / 3: s = 1
            elif hr[lb] < 9.5 + 5 / 60 - 1e-9 and cl > hi - rg / 3: s = -1
            else: continue
        else:
            if cl == o[i930]: continue
            s = 1 if cl < o[i930] else -1
        r = k21(TT[s], i10, i1210, mk['pip5'], mk['floor'])
        if r is None: continue
        q, E, stop, tgt = r
        emit(rows, mk, A, 'b21', cell, q, s, s * E, s * stop, [s * tgt], [1.0], i1330, control=(cell == 'A'))

# ---------------------------------------------------------------- b22 last hour / MOC
def hourly(T, A):
    hk = A['dord'] * 24 + np.floor(A['hr'] + 1e-9).astype(np.int64)
    ch = np.r_[True, hk[1:] != hk[:-1]]; st = np.flatnonzero(ch); hid = np.cumsum(ch) - 1
    H, Lw = np.maximum.reduceat(T['h'], st), np.minimum.reduceat(T['l'], st); C = T['c'][np.r_[st[1:], len(hk)] - 1]
    bu = np.zeros(len(st), bool); bu[2:] = H[:-2] < Lw[2:]
    return dict(hid=hid, H=H, L=Lw, C=C, bu=bu)

def k22a(T, HR, i930, i15, i1550, i1555, tick, floor):
    h, l, o, fvb = T['h'], T['l'], T['o'], T['fvb']; hb = HR['hid'][i15]
    zones = []
    for j in range(max(hb - 24, 2), hb):
        if HR['bu'][j]:
            zb, zt = HR['H'][j - 2], HR['L'][j]
            if (HR['C'][j + 1:hb] < zb).any(): continue
            zones.append((zb, zt))
    if not zones: return None
    for i in range(i15 + 2, i1550):
        if not fvb[i]: continue
        fb, ft = h[i - 2], l[i]
        if not any(max(zb, fb) <= min(zt, ft) for zb, zt in zones): continue
        E = ft; stop = o[i - 1] - tick(E); tgt = h[i930:i + 1].max()
        if not (E > stop) or tgt - E < E - stop or E - stop < floor(E): continue
        for q in range(i + 1, i1550):
            if h[q] >= tgt: return (i, None)
            if l[q] <= E: return (i, (q, E, stop, tgt))
        return (i, None)
    return None

def k22b(T, i930, i14, i1510, i1530, i1550, i16, tick, floor):
    h, l, c, o, fh, fvs, hr = T['h'], T['l'], T['c'], T['o'], T['fh'], T['fvs'], T['hr']
    if not (c[i1530 - 1] < o[i930]): return None
    if hr[i930 + int(np.argmin(l[i930:i1530]))] < 14: return None
    js = [j for j in range(i930 + 1, i1510) if fh[j] and j + 1 < len(h)]
    if not js: return None
    tgt = h[js[-1]]; pend = []; act = None
    for k in range(i14, i1550):
        if act is not None and k > act[2] and k >= i1530 and l[k] <= act[0]: return k, act[0], act[1], tgt
        newest = None
        for i in list(pend):
            if c[k] > l[i - 2]: pend.remove(i); newest = i if newest is None or i > newest else newest
        if newest is not None:
            E, stop = l[newest - 2], h[newest] - tick(l[newest - 2])
            if E > stop and tgt - E >= E - stop and E - stop >= floor(E): act = (E, stop, k)
        if fvs[k] and hr[k - 2] >= 14 and hr[k] < 15.75: pend.append(k)
    return None

def m22(A, mk, rows, cell):
    hr = A['hr']; TT = {s: orient(A, s) for s in (1, -1)}; HRs = {s: hourly(TT[s], A) for s in (1, -1)} if cell == 'A' else None
    for ds, de in zip(A['ds'], A['de']):
        if A['dord'][ds] in A['rolls'] or A['dow'][ds] > 4: continue
        i930 = first_at(hr, ds, de, 9.5); i15 = first_at(hr, ds, de, 15.0)
        if i930 >= de or i15 >= de or hr[i15] >= 15.5 or i15 - i930 < 20: continue
        i1550 = first_at(hr, ds, de, 15 + 50 / 60); i16 = first_at(hr, ds, de, 16.0)
        if cell == 'A':
            i1555 = first_at(hr, ds, de, 15 + 55 / 60); best = None
            for s in (1, -1):
                r = k22a(TT[s], HRs[s], i930, i15, i1550, i1555, mk['tick'], mk['floor'])
                if r is not None and (best is None or r[0] < best[1][0]): best = (s, r)
            if best is None or best[1][1] is None: continue
            s, (_, (q, E, stop, tgt)) = best; end = i1555
        else:
            i14 = first_at(hr, ds, de, 14.0); i1510 = first_at(hr, ds, de, 15 + 10 / 60); i1530 = first_at(hr, ds, de, 15.5)
            if i14 >= i1530: continue
            r = None
            for s in (1, -1):
                r = k22b(TT[s], i930, i14, i1510, i1530, i1550, i16, mk['tick'], mk['floor'])
                if r is not None: break
            if r is None: continue
            q, E, stop, tgt = r; end = i16
        emit(rows, mk, A, 'b22', cell, q, s, s * E, s * stop, [s * tgt], [1.0], end, control=(cell == 'A'))

# ---------------------------------------------------------------- b23 NDOG
def k23a(T, e0, i19, i20, i21, lo, hi, tick):
    h, l, c, o, fh, fl = T['h'], T['l'], T['c'], T['o'], T['fh'], T['fl']
    IH = next((j for j in range(e0 + 1, i19 - 1) if fh[j]), None)
    if IH is None: return None
    kx = next((k for k in range(IH + 1, i19) if l[k] < lo), None)
    if kx is None: return None
    IL = next((j for j in range(max(kx, IH + 1), i19 - 1) if fl[j] and l[j] < lo), None)
    if IL is None: return None
    rt = next((k for k in range(max(i19, IL + 2), i20) if l[k] <= hi and h[k] >= lo), None)
    if rt is None: return None
    dk = next((k for k in range(rt, i21) if c[k] > h[IH]), None)
    if dk is None: return None
    j3 = next((j for j in range(dk - 1, e0 - 1, -1) if c[j] < o[j]), None)
    if j3 is None: return None
    E = c[j3]; stop = (lo + hi) / 2 - tick(E); tgt = h[IH] + 1.25 * (h[IH] - l[IL]); hh = h[dk]
    if not (E > stop): return None
    for q in range(dk + 1, i21):
        if l[q] <= E: return dk, (q, E, stop, tgt, hh)
        if h[q] >= tgt: return dk, None
        hh = max(hh, h[q])
    return dk, None

def m23(A, mk, rows, cell):
    o, h, l, c, hr = A['o'], A['h'], A['l'], A['c'], A['hr']; TT = {s: orient(A, s) for s in (1, -1)}
    for ds, de in zip(A['ds'], A['de']):
        if A['dord'][ds] in A['rolls'] or A['dow'][ds] > 3: continue
        e0 = first_at(hr, ds, de, 18.0); pre = first_at(hr, ds, de, 17.0) - 1
        if e0 >= de or pre < ds or hr[e0] > 18.1 or hr[pre] < 16.5: continue
        lo, hi = min(c[pre], o[e0]), max(c[pre], o[e0]); H = hi - lo
        if H <= mk['ndog']: continue
        if cell == 'A':
            i19 = first_at(hr, e0, de, 19.0); i20 = first_at(hr, e0, de, 20.0); i21 = first_at(hr, e0, de, 21.0); i22 = first_at(hr, e0, de, 22.0)
            best = None
            for s in (1, -1):
                tlo, thi = (lo, hi) if s == 1 else (-hi, -lo)
                r = k23a(TT[s], e0, i19, i20, i21, tlo, thi, mk['tick'])
                if r is not None and (best is None or r[0] < best[1][0]): best = (s, r)
            if best is None or best[1][1] is None: continue
            s, (_, (q, E, stop, tgt, hh)) = best
            emit(rows, mk, A, 'b23', 'A', q, s, s * E, s * stop, [s * tgt], [1.0], i22, be=s * hh, control=True)
        else:
            above = below = -1
            for k in range(e0, de):
                if above >= 0 and k > above and l[k] <= hi:
                    E = min(hi, o[k]); emit(rows, mk, A, 'b23', 'B', k, -1, E, hi + H, [lo], [1.0], de); break
                if below >= 0 and k > below and h[k] >= lo:
                    E = max(lo, o[k]); emit(rows, mk, A, 'b23', 'B', k, 1, E, lo - H, [hi], [1.0], de); break
                if above < 0 and l[k] > hi: above = k
                if below < 0 and h[k] < lo: below = k

# ---------------------------------------------------------------- b24 TGIF
def k24(T, w0, a, e15, WHa, WLa, sweep, atr, floor):
    """Short case in T-space (bull week). Returns (q, E, stop, tgts) or None."""
    h, l, c, fl, fvs = T['h'], T['l'], T['c'], T['fl'], T['fvs']
    WH, WL = WHa, WLa; flv = None
    for j in range(w0 + 1, a - 1):
        if fl[j]: flv = l[j]
    swept = not sweep; LH = WHa; LHb = w0 + int(np.argmax(h[w0:a])); mss = False; E = None; Eb = None
    for k in range(a, e15):
        if E is not None and k > Eb and h[k] >= E:
            WH = max(WH, h[k]); WL = min(WL, l[k]); WR = WH - WL
            tg = [WH - 0.2 * WR, WH - 0.3 * WR, WH - 0.4 * WR]; stop = LH + 0.1 * atr
            if E <= tg[0] or stop - E < floor(E): return None
            return k, E, stop, tg
        WH = max(WH, h[k]); WL = min(WL, l[k])
        if l[k] <= WH - 0.2 * (WH - WL): return None
        if not swept:
            if h[k] > WHa: swept = True; LH = h[k]; LHb = k; mss = False; E = None
        elif h[k] > LH: LH = h[k]; LHb = k; mss = False; E = None
        if swept:
            if not mss and flv is not None and c[k] < flv:
                mss = True
                i = next((i for i in range(k, LHb + 1, -1) if fvs[i]), None)
                if i is not None: E, Eb = h[i], k
            elif mss and E is None and fvs[k]: E, Eb = h[k], k
        if k - 1 > w0 and fl[k - 1]: flv = l[k - 1]
    return None

def m24(A, mk, rows, cell):
    h, l, hr, dow = A['h'], A['l'], A['hr'], A['dow']; TT = {s: orient(A, s) for s in (1, -1)}
    mon = A['dord'] - np.where(dow == 6, -1, dow)
    keep = (dow != 5) & ~((dow == 4) & (hr >= 17))
    if mk.get('crypto'): keep &= dow != 6
    ath, atl = np.maximum.accumulate(h), np.minimum.accumulate(l)
    wk = np.where(keep, mon, -1)
    ch = np.flatnonzero(np.r_[True, wk[1:] != wk[:-1]]); ends = np.r_[ch[1:], len(wk)]
    for w0, we in zip(ch, ends):
        if wk[w0] < 0: continue
        if any(d in A['rolls'] for d in np.unique(A['dord'][w0:we])): continue
        isthu = np.flatnonzero((dow[w0:we] == 3) & (hr[w0:we] >= 13))
        if not len(isthu): continue
        a = w0 + isthu[0]
        if a - w0 < 50: continue
        isf15 = np.flatnonzero((dow[w0:we] == 4) & (hr[w0:we] >= 15)); e15 = w0 + isf15[0] if len(isf15) else we
        isf16 = np.flatnonzero((dow[w0:we] == 4) & (hr[w0:we] >= 16)); e16 = w0 + isf16[0] if len(isf16) else we
        WO = A['o'][w0]; WH, WL = h[w0:a].max(), l[w0:a].min(); P = A['c'][a - 1]; WR = WH - WL
        atr = A['atr'].get(A['dord'][a], np.nan)
        if not np.isfinite(atr) or WR <= 0: continue
        if P - WO >= 0.6 * WR and WH < 0.99 * ath[a - 1]: s = -1
        elif WO - P >= 0.6 * WR and WL > 1.01 * atl[a - 1]: s = 1
        else: continue
        T = TT[-s]   # short logic in T-space: real bull week -> s=-1 uses real arrays (T-space = real)
        WHa, WLa = (WH, WL) if s == -1 else (-WL, -WH)
        r = k24(T, w0, a, e15, WHa, WLa, cell == 'A', atr, mk['floor'])
        if r is None: continue
        q, E, stop, tg = r; f = -s   # T-space price -> real = f * price; T-space short -> real side s
        emit(rows, mk, A, 'b24', cell, q, s, f * E, f * stop, [f * t for t in tg], [1 / 3, 1 / 3, 1 / 3], e16, control=(cell == 'A'))

# ---------------------------------------------------------------- run
MODELS = [('b14', m14, False), ('b21', m21, False), ('b22', m22, False), ('b23', m23, False), ('b24', m24, True)]
GROUPS = [('NQ', 'NQ'), ('NQ@broker', 'NQ'), ('ES', 'ES'), ('ES@broker', 'ES'), ('GOLD', 'GOLD'), ('CRYPTO', 'CRYPTO'), ('FX', 'FX')]
ROWS = []
for mk in MK():
    A1 = prep(mk['d1'](), mk); A5 = prep(mk['d5'](), mk) if mk['d5'] else A1
    print(mk['name'], len(A1['h']), flush=True)
    for name, fn, five in MODELS:
        if name == 'b23' and mk['ndog'] is None: continue
        for cell in ('A', 'B'):
            n0 = len(ROWS); fn(A5 if five else A1, mk, ROWS, cell); print(' ', name, cell, len(ROWS) - n0, flush=True)
    del A1, A5
T = pd.DataFrame(ROWS); T.to_csv(os.path.join(os.environ.get('TEMP', '.'), 'x15b_rows.csv'), index=False)
txt = ['# X15 batch b results (LEARN only; exam bars never loaded). Pre-registration: specs/X15-ict-rest.md "X15 batch b".',
       'CME/crypto learn 2020-2023, gold 2019-2022, FX 2012-2019. NQ/ES rows at CME costs; NQ@broker/ES@broker = same trades at Broker costs.']
for g, _ in GROUPS:
    S = T[T.model == g]
    if len(S): txt.append(L.report(S, f'{g}', 2100, os.devnull))
txt.append('\n# PASS table (NET >= +0.05R, maxDD <= 30R; FX also >= 3/4 pairs positive). _RANDOM rows are the coin-flip controls.')
for (g, cell), S in T.groupby(['model', 'cell']):
    S = S.sort_values('date', kind='stable'); net = S.net.mean(); dd = L.maxdd(S.net.values)
    ok = net >= 0.05 and dd <= 30
    if g == 'FX': pp = int((S.groupby('m').net.mean() > 0).sum()); ok = ok and pp >= 3; extra = f' pairs+ {pp}/4'
    else: extra = ''
    txt.append(f'{g:8s} {cell:10s} n={len(S):5d} gross {S.gross.mean():+.3f} NET {net:+.3f} maxDD {dd:6.1f}{extra} -> '
               + ('control' if 'RANDOM' in cell else ('PASS' if ok else 'FAIL')))
open(OUT, 'w', encoding='utf-8').write('\n'.join(txt) + '\n'); print('\n'.join(txt[-80:]))
