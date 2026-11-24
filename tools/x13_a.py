"""X13a: ICT queue ranks 10 (monthly-open), 27 (weekly-open fade), 25 (classic Tuesday low), 42 (Charter Model 8).
Pre-registered in specs/X13-ict-broker.md (section X13a). LEARN ONLY: bars dated 2020+ are dropped at load."""
import sys, os, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ictlib
from ictlib import load_fx, pip_of, cost_R, report, FX_LEARN_END

CUT = dt.date(FX_LEARN_END + 1, 1, 1)
RNG = np.random.default_rng(13)
ROWS = []


def prep(m, tf):
    d = load_fx(m, tf); d = d[d.date < CUT].reset_index(drop=True)
    A = dict(o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values, hr=d.hr.values, dow=d.dow.values, date=d.date.values)
    ordd = np.array([x.toordinal() for x in A['date']])
    A['key'] = ordd * 24 + A['hr']; A['nord'] = ordd + (A['hr'] >= 17)
    return d, A


def idx(A, day, hour):
    return int(np.searchsorted(A['key'], day.toordinal() * 24 + hour, 'left'))


def daily5(d):
    D = d.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    D = D[D.n >= 200].copy()
    D['b1'] = np.sign(D.c - D.o); D['b2'] = np.sign(D.c - D.c.rolling(20).mean())
    return D


def bias_at(D, day, col):
    """value of col on the last completed daily bar strictly before `day` (already-closed info only)"""
    k = D.index.searchsorted(day) - 1
    if k < 20: return 0
    v = D[col].iloc[k]
    return 0 if np.isnan(v) else int(v)


def sim(A, f, iend, side, E, stop, tgt, steps=(), partial=None):
    """stop-first walker from fill bar f (fill bar: stop checked; target/partial/steps not). Returns gross R, exit index."""
    h, l, c = A['h'], A['l'], A['c']; risk = abs(E - stop); cur = stop; booked = 0.0; rem = 1.0; pdone = False
    td = None if tgt is None else abs(tgt - E)
    for q in range(f, iend):
        if (l[q] <= cur) if side == 1 else (h[q] >= cur): return booked + rem * side * (cur - E) / risk, q
        if q == f: continue
        fav = (h[q] - E) if side == 1 else (E - l[q])
        if partial and not pdone and fav >= partial[0]:
            booked += partial[1] * partial[0] / risk; rem -= partial[1]; pdone = True; cur = E
        if td is not None and fav >= td: return booked + rem * td / risk, q
        for s_fav, s_off in steps:
            if fav >= s_fav:
                ns = E - side * s_off
                cur = max(cur, ns) if side == 1 else min(cur, ns)
    q = max(iend - 1, f)
    return booked + rem * side * (c[q] - E) / risk, q


def book(A, m, model, cell, f, iend, side, E, stop, tgt, steps=(), partial=None, control=False):
    if f >= iend: return
    g, x = sim(A, f, iend, side, E, stop, tgt, steps, partial)
    risk = abs(E - stop); cost = cost_R('broker_fx', m, E, risk, int(A['nord'][x] - A['nord'][f]))
    ROWS.append(dict(model=model, cell=cell, m=m, date=A['date'][f], gross=g, net=g - cost))
    if control:  # same entry bar/price, same stop/target distances, coin-flip side
        s = int(RNG.choice([-1, 1])); sd = abs(E - stop); t2 = None if tgt is None else E + s * abs(tgt - E)
        g2, x2 = sim(A, f, iend, s, E, E - s * sd, t2, steps, partial)
        c2 = cost_R('broker_fx', m, E, sd, int(A['nord'][x2] - A['nord'][f]))
        ROWS.append(dict(model=model, cell=cell + '_RANDOM', m=m, date=A['date'][f], gross=g2, net=g2 - c2))


def first_fill(A, a, b, side, E):
    """first bar in [a,b) that trades to limit E (buy limit: low<=E; sell limit: high>=E)"""
    if b <= a: return None
    hit = (A['l'][a:b] <= E) if side == 1 else (A['h'][a:b] >= E)
    return a + int(np.argmax(hit)) if hit.any() else None


def mondays(D):
    return [x for x in D.index if x.weekday() == 0]


def prev_week(D, md):
    return D[(D.index >= md - dt.timedelta(7)) & (D.index < md - dt.timedelta(2))]


# ---------------------------------------------------------------- M10 monthly-open
def m10(m, d, A, D):
    p = pip_of(m); h, l = A['h'], A['l']; n = len(h)
    mo = pd.PeriodIndex(pd.to_datetime(pd.Series(D.index)), freq='M')
    MM = D.assign(mo=mo.values).groupby('mo').agg(H=('h', 'max'), L=('l', 'min'))
    per = list(MM.index); H = MM.H.values; L = MM.L.values
    bar_mo = pd.PeriodIndex(pd.to_datetime(pd.Series(d.date)), freq='M')
    bm = np.array([x.year * 12 + x.month for x in bar_mo])
    first_bar = {pr: int(np.searchsorted(bm, pr.year * 12 + pr.month, 'left')) for pr in per}
    ev = []
    for k in range(12, len(per) - 2):
        s0 = first_bar[per[k + 2]]
        if L[k] < L[k - 6:k].min() and L[k] < L[k + 1]:
            hit = h[s0:] > H[k + 1]
            if hit.any(): ev.append((s0 + int(np.argmax(hit)), 1, H[k - 12:k].max()))
        if H[k] > H[k - 6:k].max() and H[k] > H[k + 1]:
            hit = l[s0:] < L[k + 1]
            if hit.any(): ev.append((s0 + int(np.argmax(hit)), -1, L[k - 12:k].min()))
    ev.sort()
    st = np.zeros(n, int); TT = np.full(n, np.nan)
    for j, (v, s_, T) in enumerate(ev):
        nxt = ev[j + 1][0] if j + 1 < len(ev) else n
        st[v:nxt] = 0
        if (s_ == 1 and h[v] >= T) or (s_ == -1 and l[v] <= T): continue
        seg = (h[v:nxt] >= T) if s_ == 1 else (l[v:nxt] <= T)
        end = v + int(np.argmax(seg)) if seg.any() else nxt
        st[v:end] = s_; TT[v:end] = T
    # cell MO
    for pr in per:
        i0 = first_bar[pr]
        if i0 < 1 or i0 >= n: continue
        s_ = st[i0 - 1]
        if s_ == 0: continue
        T = TT[i0 - 1]; E = A['o'][i0] - s_ * 20 * p
        if s_ * (T - E) <= 0: continue
        last = int(np.searchsorted(bm, pr.year * 12 + pr.month, 'right'))
        d15 = idx(A, dt.date(pr.year, pr.month, 15), 24)
        f = first_fill(A, i0, min(d15, last), s_, E)
        if f is None: continue
        tgt = E + s_ * min(abs(T - E), 450 * p)
        book(A, m, 'M10', 'MO', f, last, s_, E, E - s_ * 150 * p, tgt)
    # cell WO
    for md in mondays(D):
        i0 = idx(A, md, 0)
        if i0 < 1 or i0 >= n or A['date'][i0] != md: continue
        s_ = st[i0 - 1]
        if s_ == 0: continue
        pw = prev_week(D, md)
        if len(pw) == 0 or np.sign(pw.c.iloc[-1] - pw.o.iloc[0]) != s_: continue
        T = TT[i0 - 1]; E = A['o'][i0] - s_ * 10 * p
        if s_ * (T - E) <= 0: continue
        f = first_fill(A, i0, idx(A, md + dt.timedelta(3), 0), s_, E)
        if f is None: continue
        tgt = E + s_ * min(abs(T - E), 80 * p)
        book(A, m, 'M10', 'WO', f, idx(A, md + dt.timedelta(4), 16), s_, E, E - s_ * 40 * p, tgt, control=True)


# ---------------------------------------------------------------- M27 weekly-open fade
def m27(m, d, A, D):
    p = pip_of(m); n = len(A['h'])
    for col in ('b1', 'b2'):
        for md in mondays(D):
            i0 = idx(A, md, 0)
            if i0 >= n or A['date'][i0] != md: continue
            side = bias_at(D, md, col)
            if side == 0: continue
            E = A['o'][i0] - side * 30 * p   # bearish: sell limit at WO + 30 pips
            f = first_fill(A, i0, idx(A, md + dt.timedelta(3), 0), side, E)
            if f is None: continue
            book(A, m, 'M27', col.upper(), f, idx(A, md + dt.timedelta(4), 16), side, E, E - side * 50 * p, E + side * 150 * p,
                 partial=(100 * p, 0.5), control=(col == 'b2'))


# ---------------------------------------------------------------- M42 Charter Model 8
def m42(m, d, A, D):
    p = pip_of(m); h, l, c, hr, dow, date = A['h'], A['l'], A['c'], A['hr'], A['dow'], A['date']; n = len(h)
    steps = ((12.5 * p, 11.25 * p), (18.75 * p, 0.0))
    for col in ('b1', 'b2'):
        for md in mondays(D):
            i0 = idx(A, md, 0)
            if i0 >= n or date[i0] != md: continue
            s = bias_at(D, md, col)
            if s == 0: continue
            iend = idx(A, md + dt.timedelta(4), 16)
            anc = None; aidx = None; dext = None; eq = None; disp = False; ent = None; dstart = i0
            for q in range(i0, iend):
                if q > i0 and date[q] != date[q - 1]: dstart = q
                kz = 1 <= dow[q] <= 3 and ((2 <= hr[q] < 5) or (7 <= hr[q] < 10))
                if ent is not None:
                    touched = (h[q] >= ent) if s == -1 else (l[q] <= ent)
                    if touched and kz:
                        book(A, m, 'M42', col.upper(), q, iend, s, ent, ent - s * 15 * p, ent + s * 25 * p, steps=steps,
                             control=(col == 'b2'))
                        break
                    if touched: ent = None
                # bearish week (s=-1): anchor = Mon-Wed high, trade short; bullish mirrors
                ext = h[q] if s == -1 else l[q]
                beyond = anc is None or ((ext > anc) if s == -1 else (ext < anc))
                if dow[q] <= 2 and beyond:
                    anc = ext; aidx = q
                    rext = l[i0:q + 1].min() if s == -1 else h[i0:q + 1].max()
                    dext = l[dstart:q + 1].min() if s == -1 else h[dstart:q + 1].max()
                    eq = (anc + rext) / 2; disp = False; ent = None
                    continue
                if dow[q] >= 3 and beyond: break
                if not disp and ((c[q] < dext) if s == -1 else (c[q] > dext)): disp = True
                if disp and q - 2 >= aidx:
                    if s == -1 and h[q] < l[q - 2] and h[q] > eq: ent = h[q]
                    if s == 1 and l[q] > h[q - 2] and l[q] < eq: ent = l[q]


# ---------------------------------------------------------------- M25 classic Tuesday low (A, 15m) and 2021 window (B, 5m)
def fractals(h, l):
    n = len(h); fh = np.zeros(n, bool); fl = np.zeros(n, bool)
    fh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); fl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    return fh, fl


def m25a(m, A, D):
    p = pip_of(m); h, l, c, o, date = A['h'], A['l'], A['c'], A['o'], A['date']; n = len(h)
    fh, fl = fractals(h, l)
    for col in ('b1', 'b2'):
        for md in mondays(D):
            i0 = idx(A, md, 0)
            if i0 >= n or date[i0] != md: continue
            s = bias_at(D, md, col)
            if s == 0: continue
            tue, wed = md + dt.timedelta(1), md + dt.timedelta(2)
            if tue not in D.index: continue
            pw = prev_week(D, md)
            if len(pw) == 0: continue
            WO = o[i0]
            tgt = max(D.h[md], pw.h.max()) if s == 1 else min(D.l[md], pw.l.min())
            iend = idx(A, md + dt.timedelta(3), 12)
            for day, ref in ((tue, D.l[md] if s == 1 else D.h[md]), (wed, D.l[tue] if s == 1 else D.h[tue])):
                lvl = min(WO, ref) if s == 1 else max(WO, ref)
                w0, w1 = idx(A, day, 2), idx(A, day, 11)
                swept = False; rx = None; ri = None; mss = None
                for q in range(w0, w1):
                    if not swept:
                        if (l[q] < lvl) if s == 1 else (h[q] > lvl):
                            swept = True; rx = l[q] if s == 1 else h[q]; ri = q
                        continue
                    if (l[q] < rx) if s == 1 else (h[q] > rx):
                        rx = l[q] if s == 1 else h[q]; ri = q; continue
                    j = next((j for j in range(ri - 1, i0 - 1, -1) if (fh[j] if s == 1 else fl[j])), None)
                    if j is None: continue
                    if (c[q] > h[j]) if s == 1 else (c[q] < l[j]): mss = q; break
                if mss is None: continue
                i = next((i for i in range(mss, ri + 1, -1) if ((l[i] > h[i - 2]) if s == 1 else (h[i] < l[i - 2]))), None)
                if i is None: continue
                E = l[i] if s == 1 else h[i]; stop = rx - s * p
                if s * (tgt - E) < abs(E - stop): continue
                f = first_fill(A, mss + 1, idx(A, day, 16), s, E)
                if f is None: continue
                book(A, m, 'M25', 'A_' + col.upper(), f, iend, s, E, stop, tgt, control=(col == 'b2'))
                break


def m25b(m, A, D):
    p = pip_of(m); h, l, c = A['h'], A['l'], A['c']
    fh, fl = fractals(h, l)
    for md in mondays(D):
        s = bias_at(D, md, 'b2')
        if s == 0: continue
        done = False
        for k in range(3):
            day = md + dt.timedelta(k)
            if day not in D.index: continue
            w0, w1 = idx(A, day, 8.5), idx(A, day, 11)
            anc = None; ai = None; mss = False; ext = None; lim = None
            for q in range(w0, w1):
                if lim is not None and ((l[q] <= lim) if s == 1 else (h[q] >= lim)):
                    book(A, m, 'M25', 'B_B2', q, idx(A, day, 16), s, lim, lim - s * 10 * p, lim + s * 30 * p)
                    done = True; break
                x = l[q] if s == 1 else h[q]
                if anc is None or ((x < anc) if s == 1 else (x > anc)):
                    anc = x; ai = q; mss = False; ext = None; lim = None; continue
                if not mss:
                    j = next((j for j in range(ai - 1, w0 - 1, -1) if (fh[j] if s == 1 else fl[j])), None)
                    if j is not None and ((c[q] > h[j]) if s == 1 else (c[q] < l[j])): mss = True
                if mss:
                    y = h[q] if s == 1 else l[q]
                    ext = y if ext is None else (max(ext, y) if s == 1 else min(ext, y))
                    leg = abs(ext - anc)
                    lim = (ext - s * 0.705 * leg) if leg >= 10 * p else None
            if done: break


if __name__ == '__main__':
    for m in ictlib.FX_PAIRS:
        d5, A5 = prep(m, '5m'); D = daily5(d5)
        m10(m, d5, A5, D); m27(m, d5, A5, D); m42(m, d5, A5, D); m25b(m, A5, D)
        d15, A15 = prep(m, '15m'); m25a(m, A15, D)
        print(m, len(ROWS), flush=True)
    T = pd.DataFrame(ROWS)
    T = T[pd.to_datetime(T.date).dt.year <= FX_LEARN_END]
    report(T, 'X13a learn (FX 2012-2019, Broker costs): M10 monthly-open, M27 weekly-open fade, M25 Tuesday low, M42 Charter M8',
           FX_LEARN_END, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'X13a-results.md'))
