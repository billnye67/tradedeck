"""X15c: ORG add-ons (queue ranks 15-20) on NQ/ES 1-min + gold 5m COMEX-pit analog. LEARN ERA ONLY. Pre-reg: specs/X15-ict-rest.md (X15c)."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd, sqlite3
import ictlib as L

def load_gold():   # copied from tools/x14_sb.py
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

def ladder(o, h, l, c, t, i0, side, E, stop, parts, be_after=None, close_rule=None, tend=390, ibar='both'):
    """Stop-first walker with partials. parts: [(price, weight)]. be_after: part index after which stop -> E.
    close_rule: (level, newstop): a bar close beyond level moves the stop (tighten only, from the next bar)."""
    risk = abs(E - stop); done = [None] * len(parts); x_rem = None
    for q in range(i0, len(h)):
        if t[q] >= tend: x_rem = o[q]; break
        # entry bar: ibar says which touches can be trusted to come after the fill (bug fix: no intrabar lookahead)
        if (q > i0 or ibar in ('both', 'stop')) and ((l[q] <= stop) if side == 1 else (h[q] >= stop)): x_rem = stop; break
        if q == i0 and ibar in ('stop', 'none'): continue
        for k, (p, w) in enumerate(parts):
            if done[k] is None and ((h[q] >= p) if side == 1 else (l[q] <= p)): done[k] = p
        if all(dn is not None for dn in done): break
        if be_after is not None and done[be_after] is not None: stop = max(stop, E) if side == 1 else min(stop, E)
        if close_rule is not None and ((c[q] > close_rule[0]) if side == 1 else (c[q] < close_rule[0])):
            stop = max(stop, close_rule[1]) if side == 1 else min(stop, close_rule[1])
    else:
        x_rem = c[-1]
    g = 0.0
    for k, (p, w) in enumerate(parts):
        x = done[k] if done[k] is not None else x_rem
        g += w * side * (x - E) / risk
    return g

def mirror(E, stop, parts, close_rule):
    m = lambda p: 2 * E - p
    return m(stop), [(m(p), w) for p, w in parts], (None if close_rule is None else (m(close_rule[0]), m(close_rule[1])))

def first_fvg(h, l, t, side, c1_min_idx, c1_tmin, c3_tmax):
    for k in range(max(2, c1_min_idx + 2), len(h)):
        if t[k] > c3_tmax: return None
        if t[k - 2] < c1_tmin: continue
        if (side == -1 and l[k - 2] > h[k]) or (side == 1 and h[k - 2] < l[k]): return k
    return None

def limit_fill(h, l, t, fv, side, near, cancel_lvl, t_end):
    for q in range(fv + 1, len(h)):
        if t[q] >= t_end: return None
        if (side == -1 and l[q] <= cancel_lvl) or (side == 1 and h[q] >= cancel_lvl): return None
        if (side == -1 and h[q] >= near) or (side == 1 and l[q] <= near): return q
    return None

def a1(o, h, l, c, t, i_open, side, tick, BAR, cancel_lvl, c1_tmin=None, c3_tmax=29, fill_end=30, c1_min_idx=None):
    """A1 entry: first FVG toward the trade side, limit 1 tick inside its near edge, stop 1 tick beyond candle 2."""
    fv = first_fvg(h, l, t, side, i_open if c1_min_idx is None else c1_min_idx, BAR if c1_tmin is None else c1_tmin, c3_tmax)
    if fv is None: return None
    stop = h[fv - 1] + tick if side == -1 else l[fv - 1] - tick
    near = h[fv] + tick if side == -1 else l[fv] - tick
    q = limit_fill(h, l, t, fv, side, near, cancel_lvl, fill_end)
    if q is None: return None
    if (stop - near) * -side <= 0: return None
    return q, near, stop

def run_market(mkt, d, tick, BAR, OPEN, SETTLE, band_scale, venues, rolls, learn_end, gold=False):
    rng = np.random.default_rng(15)
    d = d[pd.to_datetime(d.date).dt.year <= learn_end].copy()   # LEARN ERA ONLY: exam rows never enter
    d['mn'] = np.round(d.hr * 60).astype(int)
    keep = ((d.mn >= OPEN - 210) & (d.mn <= OPEN + 400)) | (d.mn == SETTLE)
    d = d[keep]; g = dict(tuple(d.groupby('date'))); rows = []
    prev_S = prev_date = last_rth = None
    full = not gold   # gold runs cell a + control only
    for dt in sorted(g):
        x = g[dt]; mn = x.mn.values
        has_open = (mn == OPEN).any(); has_set = (mn == SETTLE).any()
        if prev_S is not None and has_open and prev_date == last_rth and prev_date not in rolls and dt not in rolls and x.dow.iloc[0] < 5:
            y = x[(x.mn >= OPEN - 210) & (x.mn <= OPEN + 400)]
            o, h, l, c = y.o.values, y.h.values, y.l.values, y.c.values; t = y.mn.values - OPEN
            i_open = int(np.argmax(t >= 0)); O = o[i_open]; S = prev_S; gap = O - S; ag = abs(gap)
            hand = band_scale(dt, O)       # NQ handles -> this market's points
            if not (hand == hand) or gap == 0: hand = np.nan
            CE = S + gap / 2; fade = -1 if gap > 0 else 1; cont = -fade
            sd = lambda k: S - k * gap     # G18 fib 1=O, 0=S; -k = k*gap beyond S
            def B(lo, hi=np.inf): return hand == hand and lo * hand <= ag <= hi * hand
            def add(model, cell, side, q, E, stop, parts, be=None, cr=None, ctrl=True, ibar='both'):
                gr = ladder(o, h, l, c, t, q, side, E, stop, parts, be, cr, ibar=ibar); risk = abs(E - stop)
                for v in venues:
                    rows.append(dict(model=f'{mkt} {model}', cell=f'{cell}|{v}', m=mkt, date=dt, gross=gr,
                                     net=gr - L.cost_R(v, 'XAUUSD' if gold else mkt, E, risk), risk=risk))
                if ctrl:
                    flip = rng.random() >= 0.5
                    s2, p2, c2 = mirror(E, stop, parts, cr) if flip else (stop, parts, cr)
                    # flipped side: a stop touch on the fill bar may come before the fill -> two bounds:
                    # CTRL = counts it (pessimistic), CTRLo = ignores it (optimistic); the flipped target touch is always after the fill
                    for cn, fb in (('CTRL', 'both'), ('CTRLo', 'tgt')):
                        gr2 = ladder(o, h, l, c, t, q, -side if flip else side, E, s2, p2, be, c2, ibar=fb if flip else ibar)
                        for v in venues:
                            rows.append(dict(model=f'{mkt} {model}', cell=f'{cn}|{v}', m=mkt, date=dt, gross=gr2,
                                             net=gr2 - L.cost_R(v, 'XAUUSD' if gold else mkt, E, risk), risk=risk))
            # REF #106 (replication + G16 robustness), not new looks
            if B(40, 200):
                for cell, c1t in (('E16', 0), ('G16', BAR)):
                    r = a1(o, h, l, c, t, i_open, fade, tick, BAR, CE, c1_tmin=c1t)
                    if r and (CE - r[1]) * fade > 0: add('REF106', cell, fade, r[0], r[1], r[2], [(CE, 1.0)], ctrl=False)
            # 15a continuation (stop at CE, ladder -0.5/-1.0 SD beyond the open); 15b fade with the full exit ladder
            if B(40, 200):
                lv5 = O + cont * 0.5 * ag; lv10 = O + cont * 1.0 * ag
                r = a1(o, h, l, c, t, i_open, cont, tick, BAR, lv5)
                if r:
                    q, E, _ = r
                    if (CE - E) * cont < 0 and (lv5 - E) * cont > 0:
                        add('15', 'a', cont, q, E, CE - cont * tick, [(lv5, 0.5), (lv10, 0.5)], be=0)
                if full:
                    r = a1(o, h, l, c, t, i_open, fade, tick, BAR, CE)
                    if r and (CE - r[1]) * fade > 0:
                        parts = [(CE, .25), (S, .45), (sd(0.2), .1), (sd(0.5), .1), (sd(1.0), .1)]
                        add('15', 'b', fade, r[0], r[1], r[2], parts, be=0, cr=(S, CE - fade * tick), ctrl=False)
            # 16 turtle-soup stop entry
            for cell, lo in (('a', 40), ('b', 75)):
                if (cell == 'b' and not full) or not B(lo, 200): continue
                side = fade; idx = np.where(t >= -30)[0]
                if not len(idx): continue
                i_s = idx[0]; ent = None
                for q in range(i_open, len(h)):
                    if t[q] >= 30: break
                    hit = False
                    for k in range(max(i_s, 1), q - 1):     # fractal at k, confirmed by bar k+1 <= q-1
                        if side == 1:
                            if l[k] < l[k - 1] and l[k] < l[k + 1] and l[k + 1:q].min() > l[k] and l[q] < l[k]: hit = True; break
                        else:
                            if h[k] > h[k - 1] and h[k] > h[k + 1] and h[k + 1:q].max() < h[k] and h[q] > h[k]: hit = True; break
                    if not hit: continue
                    if (side == 1 and c[q] < o[q]) or (side == -1 and c[q] > o[q]):
                        st = o[q]
                        for r_ in range(q + 1, len(h)):
                            if t[r_] >= 60: break
                            if (side == 1 and h[r_] >= st) or (side == -1 and l[r_] <= st):
                                E = max(st, o[r_]) if side == 1 else min(st, o[r_])
                                sl = (l[q:r_].min() - tick) if side == 1 else (h[q:r_].max() + tick)
                                ent = (r_, E, sl); break
                        break
                if ent and (CE - ent[1]) * side > 0 and (ent[1] - ent[2]) * side > 0:
                    add('16', cell, side, ent[0], ent[1], ent[2], [(CE, 1.0)], ctrl=(cell == 'a'))
            # 17 size classes
            if B(20, 75):
                r = a1(o, h, l, c, t, i_open, fade, tick, BAR, CE)
                if r and (CE - r[1]) * fade > 0: add('17', 'a', fade, r[0], r[1], r[2], [(CE, 1.0)])
            if full and B(120):
                qd = O + fade * 0.25 * ag
                r = a1(o, h, l, c, t, i_open, fade, tick, BAR, qd)
                if r and (qd - r[1]) * fade > 0: add('17', 'b', fade, r[0], r[1], r[2], [(qd, 1.0)], ctrl=False)
            # 18 Judas sweep of the 06:00-09:29 high/low
            pre = (t >= -210) & (t < 0)
            if pre.sum() >= 6:
                pmh, pml = h[pre].max(), l[pre].min()
                for cell, lo, hi, win in (('a', 20, 75, 30), ('b', 40, 200, 15)):
                    if (cell == 'b' and not full) or not B(lo, hi): continue
                    sw = None
                    for q in range(i_open, len(h)):
                        if t[q] >= win: break
                        if (fade == -1 and h[q] > pmh) or (fade == 1 and l[q] < pml): sw = q; break
                    if sw is None: continue
                    r = a1(o, h, l, c, t, i_open, fade, tick, BAR, CE, c3_tmax=44, fill_end=60, c1_min_idx=sw)
                    if r and (CE - r[1]) * fade > 0: add('18', cell, fade, r[0], r[1], r[2], [(CE, 1.0)], ctrl=(cell == 'a'))
            # 19a runner to full fill; 19b very large gap continues
            if B(40, 200):
                r = a1(o, h, l, c, t, i_open, fade, tick, BAR, CE)
                if r and (CE - r[1]) * fade > 0: add('19', 'a', fade, r[0], r[1], r[2], [(CE, .75), (S, .25)], be=0)
            if full and hand == hand and ag > 200 * hand:
                lim = O - cont * 0.25 * ag; tg = O + cont * 0.5 * ag; sl = CE - cont * tick
                for q in range(i_open, len(h)):
                    if t[q] >= 90: break
                    if t[q] < BAR: continue
                    if (cont == 1 and h[q] >= tg) or (cont == -1 and l[q] <= tg): break
                    if (cont == 1 and l[q] <= lim) or (cont == -1 and h[q] >= lim):
                        E = min(lim, o[q]) if cont == 1 else max(lim, o[q])
                        if (E - sl) * cont > 0: add('19', 'b', cont, q, E, sl, [(tg, 1.0)], ctrl=False)
                        break
            # 20 coin-flip ORG (itself the control)
            if B(40, 200):
                orr = (t >= 0) & (t < 30); w59 = np.where(t < 30)[0]
                if orr.sum() and len(w59) and c[w59[-1]] != O:
                    orh, orl = h[orr].max(), l[orr].min()
                    for q in range(w59[-1] + 1, len(h)):
                        if t[q] >= 330: break
                        if l[q] <= O <= h[q]:
                            side = 1 if rng.random() < 0.5 else -1
                            sl = orl - tick if side == 1 else orh + tick; risk = (O - sl) * side
                            if risk >= 4 * tick:
                                for cell, k in (('a', 2), ('b', 1)):
                                    if cell == 'b' and not full: continue
                                    frm_tgt = (c[q - 1] - O) * side > 0   # came from the target side: target touch may precede entry
                                    add('20', cell, side, q, O, sl, [(O + side * k * risk, 1.0)], ctrl=False, ibar='stop' if frm_tgt else 'both')
                            break
        if has_set: prev_S = x.c.values[mn == SETTLE][0]; prev_date = dt
        if has_open: last_rth = dt
    return pd.DataFrame(rows)

if __name__ == '__main__':
    scratch = os.environ.get('X15_SCRATCH', '.')
    nq = L.load_cme('NQ', '1min'); es = L.load_cme('ES', '1min')
    for d in (nq, es): d['mn'] = np.round(d.hr * 60).astype(int)
    ratio = (nq[nq.mn == 570].groupby('date').o.first() / es[es.mn == 570].groupby('date').o.first()).to_dict()
    T_nq = run_market('NQ', nq, 0.25, 1, 570, 974, lambda dt, O: 1.0, ('cme', 'broker'), L.roll_dates(nq), L.CME_LEARN_END)
    T_es = run_market('ES', es, 0.25, 1, 570, 974, lambda dt, O: 1.0 / ratio[dt] if dt in ratio else np.nan, ('cme', 'broker'), L.roll_dates(es), L.CME_LEARN_END)
    del nq, es
    gd = load_gold()
    T_au = run_market('XAU', gd, 0.1, 5, 500, 805, lambda dt, O: O / 12000 * 0.6, ('broker_cmdty',), set(), 2022, gold=True)
    T = pd.concat([T_nq, T_es]); T.to_pickle(scratch + '/x15c_cme.pkl'); T_au.to_pickle(scratch + '/x15c_gold.pkl')
    a = L.report(T, 'X15c learn - NQ/ES 1-min (2020-2023)', L.CME_LEARN_END, scratch + '/r1.md')
    b = L.report(T_au, 'X15c learn - gold 5m COMEX-pit analog (2019-2022)', 2022, scratch + '/r2.md')
    summ = ['\n# Pass table (learn): PASS = NET >= +0.05R and maxDD <= 30R',
            'market model cell|venue | n | win | gross R | NET R | maxDD R | median risk pts | verdict']
    for TT in (T, T_au):
        for (mo, ce), gg in TT.groupby(['model', 'cell'], sort=True):
            gg = gg.sort_values('date'); dd = L.maxdd(gg.net.values); nt = gg.net.mean()
            summ.append(f'{mo} {ce} | {len(gg)} | {100*(gg.gross>0).mean():.0f}% | {gg.gross.mean():+.3f} | {nt:+.3f} | {dd:.0f} | '
                        f'{gg.risk.median():.2f} | {"PASS" if nt >= 0.05 and dd <= 30 else "FAIL"}')
    txt = a + '\n\n' + b + '\n' + '\n'.join(summ); print('\n'.join(summ))
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'X15c-results.md'), 'w', encoding='utf-8').write(txt + '\n')
