"""X13 batch b: ICT models 26 (CBDR/Model 5 SD fade), 61 (London close fade), 63 (Asian sweep -> stop at opposite extreme),
64 (liquidity purge and revert, 3-day). Pre-registered in specs/X13-ict-broker.md (batch b). LEARN ERA ONLY (2012-2019)."""
import sys, os, datetime as dtm, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ictlib as L

LEARN_END = dtm.date(L.FX_LEARN_END, 12, 31)
rng = np.random.default_rng(13)
ROWS = []


def prep(m, tf):
    d = L.load_fx(m, tf)
    d = d[d.date <= LEARN_END].reset_index(drop=True)          # exam sealed: never even loaded past 2019
    o, h, l, c, hr = (d[k].values.astype(float) for k in ('o', 'h', 'l', 'c', 'hr'))
    dates = d.date.values
    # trading-day id for overnight charge: a new id starts at each 17:00 NY
    key = np.array([x.toordinal() for x in dates]) + (hr >= 17)
    _, nid = np.unique(key, return_inverse=True)
    rng_idx = {}
    starts = np.r_[0, np.flatnonzero(dates[1:] != dates[:-1]) + 1]
    ends = np.r_[starts[1:], len(d)]
    for s, e in zip(starts, ends): rng_idx[dates[s]] = (s, e)
    D = L.daily(d)
    minbars = 200 if tf == '5m' else 66
    D = D[(D.n >= minbars) & (pd.to_datetime(D.index).dayofweek < 5)].copy()
    D['adr5'] = (D.h - D.l).rolling(5).mean().shift(1)
    D['b1'] = pd.Series(np.where(D.c > D.o, 1.0, -1.0), index=D.index).shift(1)
    sma = D.c.rolling(20).mean()
    D['b2'] = pd.Series(np.where(sma.isna(), np.nan, np.where(D.c > sma, 1.0, -1.0)), index=D.index).shift(1)
    return dict(o=o, h=h, l=l, c=c, hr=hr, nid=nid, R=rng_idx, D=D, pip=L.pip_of(m), m=m)


def exit_trade(X, q, side, E, stop, tgt, end):
    """fill at bar q: on the fill bar only the stop counts; then the stop-first walker until bar end-1 (time exit at its close)."""
    h, l, c = X['h'], X['l'], X['c']
    if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop, q
    xp, xi, _ = L.walk(h, l, c, X['hr'], q + 1, side, E, stop, tgt, max_bars=max(0, end - (q + 1)))
    return xp, xi


def add(X, model, cell, date, side, E, stop, parts, q, end):
    """parts: list of (weight, target or None)."""
    risk = abs(E - stop)
    if risk <= 0: return None
    g = 0.0; last = q; hits = []
    for w, tg in parts:
        xp, xi = exit_trade(X, q, side, E, stop, tg, end)
        g += w * side * (xp - E) / risk; last = max(last, xi); hits.append(tg is not None and xp == tg)
    nights = int(X['nid'][last] - X['nid'][q])
    ROWS.append(dict(model=model, cell=cell, m=X['m'], date=date, gross=g, net=g - L.cost_R('broker_fx', X['m'], E, risk, nights)))
    return hits


def control(X, model, date, side, E, stop, parts, q, end):
    s2 = side if rng.random() < 0.5 else -side
    if s2 == side: return add(X, model, 'RANDctl', date, side, E, stop, parts, q, end)
    return add(X, model, 'RANDctl', date, s2, E, 2 * E - stop, [(w, None if t is None else 2 * E - t) for w, t in parts], q, end)


def first_at(X, s, e, hh):
    w = np.flatnonzero(X['hr'][s:e] >= hh); return s + w[0] if len(w) else e


# ---------------------------------------------------------------- b26 CBDR / Model 5
def m26(X):
    o, h, l, c, hr, pip, D, R = X['o'], X['h'], X['l'], X['c'], X['hr'], X['pip'], X['D'], X['R']
    hit50 = {cell: {} for cell in 'ABC'}
    for T in D.index:
        dow = T.weekday()
        if dow not in (1, 2, 3): continue
        P = T - dtm.timedelta(days=1)
        if P not in R or T not in R: continue
        ps, pe = R[P]; ph = hr[ps:pe]; po, pc = o[ps:pe], c[ps:pe]
        bt, bb = np.maximum(po, pc), np.minimum(po, pc)
        cb = (ph >= 14) & (ph < 20); asn = (ph >= 20)
        if cb.sum() < 20 or asn.sum() < 12: continue
        ch, cl_ = bt[cb].max(), bb[cb].min(); ah, al = bt[asn].max(), bb[asn].min()
        if 15 * pip <= ch - cl_ <= 40 * pip: up0, dn0, sd = ch, cl_, ch - cl_
        elif 20 * pip <= ah - al <= 40 * pip: up0, dn0, sd = ah, al, ah - al
        else:
            fh, fl = max(ch, ah), min(cl_, al); mid = (fh + fl) / 2; up0 = dn0 = mid; sd = (fh - fl) / 2
            if sd <= 0: continue
        up1, up3, dn1, dn3 = up0 + sd, up0 + 3 * sd, dn0 - sd, dn0 - 3 * sd
        s, e = R[T]
        i2 = first_at(X, s, e, 2)
        if i2 >= e: continue
        eo = o[i2]
        b1, b2 = D.at[T, 'b1'], D.at[T, 'b2']
        for cell, bias in (('A', 0), ('B', b1), ('C', b2)):
            if bias != bias: continue
            if dow == 3:
                tu, we = hit50[cell].get(T - dtm.timedelta(days=2)), hit50[cell].get(P)
                if tu and we: continue
            done = False
            for (w0, w1, fl_hr) in ((2, 5, 7), (7, 10, 12)):
                if done: break
                ws, we_ = first_at(X, s, e, w0), first_at(X, s, e, w1)
                fe = first_at(X, s, e, fl_hr)
                whi, wlo = -np.inf, np.inf
                for i in range(ws, we_):
                    whi, wlo = max(whi, h[i]), min(wlo, l[i])
                    if i - 2 < ws: continue
                    sig = None
                    if bias in (0, -1) and whi >= up1 and l[i - 2] > h[i] and l[i - 2] <= up3 + 5 * pip and h[i] > eo:
                        sig = (-1, h[i], whi + 15 * pip)
                    elif bias in (0, 1) and wlo <= dn1 and h[i - 2] < l[i] and h[i - 2] >= dn3 - 5 * pip and l[i] < eo:
                        sig = (1, l[i], wlo - 15 * pip)
                    if sig is None: continue
                    side, E, stop = sig
                    for q in range(i + 1, we_):
                        if (h[q] >= E) if side == -1 else (l[q] <= E):
                            parts = [(0.2, E + side * 40 * pip), (0.8, E + side * 50 * pip)]
                            hits = add(X, 'b26', cell, T, side, E, stop, parts, q, fe)
                            hit50[cell][T] = bool(hits and hits[1])
                            if cell == 'A': control(X, 'b26', T, side, E, stop, parts, q, fe)
                            done = True; break
                    break   # first setup per window only
            if T not in hit50[cell]: hit50[cell][T] = False


# ---------------------------------------------------------------- b61 London close fade
def m61(X):
    o, h, l, c, hr, pip, D, R = X['o'], X['h'], X['l'], X['c'], X['hr'], X['pip'], X['D'], X['R']
    for T in D.index:
        adr = D.at[T, 'adr5']
        if adr != adr or T not in R: continue
        s, e = R[T]
        a, b, n0, n1 = first_at(X, s, e, 2), first_at(X, s, e, 5), first_at(X, s, e, 7), first_at(X, s, e, 10)
        if not (s < a < b and b < n0 < n1 < e): continue
        lon, ny = np.sign(c[b - 1] - o[a]), np.sign(c[n1 - 1] - o[n0])
        if lon == 0 or lon != ny: continue
        side = -int(lon)                                           # fade the day's direction
        w0, w1, fe = first_at(X, s, e, 10.5), first_at(X, s, e, 13), first_at(X, s, e, 15)
        sh = h if side == -1 else -l; sl = l if side == -1 else -h  # sign-flipped arrays: always "fade a high"
        so, sc = (o, c) if side == -1 else (-o, -c)
        j = max(w0, s + 1)
        while j + 1 < w1:
            if not (sh[j] > sh[j - 1] and sh[j] > sh[j + 1] and sh[j] >= sh[s:j + 1].max()): j += 1; continue
            H1 = sh[j]; lo = sl[s:j + 2].min()
            if H1 < lo + adr: j += 1; continue
            ob = next((t for t in range(j, s - 1, -1) if sc[t] > so[t]), None)
            if ob is None: j += 1; continue
            E = so[ob]; obl = sl[ob]; state = 'pull'; nxt = None
            for q in range(j + 1, w1):
                if state == 'pull':
                    if sh[q] > H1: nxt = q; break
                    if sc[q] < obl: state = 'lim'
                elif sh[q] >= E:
                    dlo = sl[s:q + 1].min()
                    st = H1 + 10 * pip
                    for cell, x in (('A', 0.25), ('B', 0.20)):
                        tg = H1 - x * (H1 - dlo)
                        if tg >= E: continue
                        # back to real prices
                        rE, rst, rtg = (E, st, tg) if side == -1 else (-E, -st, -tg)
                        add(X, 'b61', cell, T, side, rE, rst, [(1.0, rtg)], q, fe)
                        if cell == 'A': control(X, 'b61', T, side, rE, rst, [(1.0, rtg)], q, fe)
                    nxt = -1; break
            if nxt == -1 or nxt is None: break                     # filled, or setup expired at 13:00
            j = nxt                                                # new high cancelled the setup: look for the next fractal


# ---------------------------------------------------------------- b63 Asian sweep -> stop at the opposite extreme
def m63(X):
    o, h, l, c, hr, pip, D, R = X['o'], X['h'], X['l'], X['c'], X['hr'], X['pip'], X['D'], X['R']
    for T in D.index:
        P = T - dtm.timedelta(days=1)
        if P not in R or T not in R: continue
        ps, pe = R[P]; am = hr[ps:pe] >= 19
        if am.sum() < 40: continue
        ahi, alo = h[ps:pe][am].max(), l[ps:pe][am].min()
        if ahi - alo > 40 * pip: continue
        s, e = R[T]; d5, d11, fe = first_at(X, s, e, 5), first_at(X, s, e, 11), first_at(X, s, e, 12)
        for cell, bias, tR in (('A', D.at[T, 'b1'], 2.0), ('B', D.at[T, 'b2'], 2.0), ('C', D.at[T, 'b2'], None)):
            if bias != bias or bias == 0: continue
            side = int(bias)
            sw = next((i for i in range(s, d5) if ((l[i] < alo) if side == 1 else (h[i] > ahi))), None)
            if sw is None: continue
            lvl = ahi if side == 1 else alo
            for q in range(sw + 1, d11):
                if (h[q] >= lvl) if side == 1 else (l[q] <= lvl):
                    E = max(lvl, o[q]) if side == 1 else min(lvl, o[q])
                    if side == 1: st = min(l[s:q + 1].min() - pip, E - 10 * pip)
                    else: st = max(h[s:q + 1].max() + pip, E + 10 * pip)
                    tg = None if tR is None else E + side * tR * abs(E - st)
                    add(X, 'b63', cell, T, side, E, st, [(1.0, tg)], q, fe)
                    if cell == 'A': control(X, 'b63', T, side, E, st, [(1.0, tg)], q, fe)
                    break


# ---------------------------------------------------------------- b64 Liquidity purge and revert (3-day)
def m64(X):
    o, h, l, c, hr, pip, D, R = X['o'], X['h'], X['l'], X['c'], X['hr'], X['pip'], X['D'], X['R']
    idx = list(D.index)
    for k in range(2, len(idx) - 1):
        T = idx[k]
        if T not in R or idx[k + 1] not in R: continue
        H1, H2, L1, L2 = D.h.iloc[k - 1], D.h.iloc[k - 2], D.l.iloc[k - 1], D.l.iloc[k - 2]
        oh, ol = max(H1, H2), min(L1, L2)
        s, e = R[T]; end = R[idx[k + 1]][1]                        # time stop: end of day+1 (24:00 NY)
        pi = next((i for i in range(s, e) if l[i] < ol or h[i] > oh), None)
        if pi is None or (l[pi] < ol and h[pi] > oh): continue
        side = 1 if l[pi] < ol else -1
        if side == 1:
            Pv = h[s:pi].max() if pi > s else o[pi]; lvl = Pv + pip
            mid = (max(oh, Pv) + min(ol, l[s:pi].min() if pi > s else ol)) / 2; tgt_far = oh
        else:
            Pv = l[s:pi].min() if pi > s else o[pi]; lvl = Pv - pip
            mid = (max(oh, h[s:pi].max() if pi > s else oh) + min(ol, Pv)) / 2; tgt_far = ol
        for q in range(pi + 1, e):
            if (h[q] >= lvl) if side == 1 else (l[q] <= lvl):
                E = max(lvl, o[q]) if side == 1 else min(lvl, o[q])
                st = (l[pi:q].min() - pip) if side == 1 else (h[pi:q].max() + pip)
                disc = (E < mid) if side == 1 else (E > mid)
                for cell, filt, tg in (('A', True, tgt_far), ('B', True, mid), ('C', False, tgt_far)):
                    if filt and not disc: continue
                    if side * (tg - E) <= 0: continue
                    add(X, 'b64', cell, T, side, E, st, [(1.0, tg)], q, end)
                    if cell == 'A': control(X, 'b64', T, side, E, st, [(1.0, tg)], q, end)
                break


if __name__ == '__main__':
    for m in L.FX_PAIRS:
        X15 = prep(m, '15m'); m26(X15); m64(X15)
        X5 = prep(m, '5m'); m61(X5); m63(X5)
        print(m, 'trades so far', len(ROWS), flush=True)
    T = pd.DataFrame(ROWS)
    assert pd.to_datetime(T.date).dt.year.max() <= L.FX_LEARN_END
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'X13b-results.md')
    L.report(T, 'X13 batch b (ranks 26, 61, 63, 64) - Broker FX costs - LEARN 2012-2019', L.FX_LEARN_END, path)
    out = ['\n## Verdicts (pass bar: NET >= +0.05R, maxDD <= 30R, >= 3 of 4 pairs positive)']
    for (mo, cell), g in T.groupby(['model', 'cell']):
        g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values); pos = int((g.groupby('m').net.mean() > 0).sum())
        ok = net >= 0.05 and dd <= 30 and pos >= 3
        out.append(f'{mo}/{cell}: n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R pairs+ {pos}/4 -> {"PASS" if ok else "FAIL"}'
                   + (' (control)' if cell == 'RANDctl' else ''))
    out.append('\nImplementation notes (not rule changes): b26 takes only the first FVG setup per window; b61 restarts the search on a'
               ' new day high; b63/b64 stop entries fill at max(level, bar open); fill-bar targets are ignored (stop-first on the fill bar).')
    txt = '\n'.join(out); print(txt); open(path, 'a', encoding='utf-8').write(txt + '\n')
