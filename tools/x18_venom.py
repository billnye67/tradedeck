"""X18 Venom, rebuilt from ICT's own words + charts (specs/X18-venom.md). Acceptance first: must take his 2025-04-03 short and
2025-05-12 long. Usage: python tools/x18_venom.py examples | learn | exam"""
import os, sys, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
ENTRY = os.environ.get('VENOM_ENTRY', 'ob')

def five(x):
    """1-min day frame -> 5-min bars (NY clock) with start hour."""
    x = x.copy(); x['b'] = (x.hr * 12).astype(int)
    g = x.groupby('b'); return pd.DataFrame({'hr': g.hr.first().round(4), 'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last()}).reset_index(drop=True)

def first_fvg_1m(x):
    """first presented 1-min FVG of the morning (candle 1 >= 09:30): (side, lo, hi)."""
    s = x[x.hr >= 9.5].reset_index(drop=True)
    for k in range(2, min(len(s), 120)):
        if s.l[k] > s.h[k - 2]: return (1, s.h[k - 2], s.l[k])
        if s.h[k] < s.l[k - 2]: return (-1, s.h[k], s.l[k - 2])
    return None

def daily_bias(D, day):
    """ICT (May 12 video): bullish while the latest daily bullish FVG has no daily close below it; bearish mirror.
    Uses daily bars before `day` only. Returns +1/-1/0."""
    p = D[D.index < day]
    if len(p) < 25: return 0
    H, Lw, C = p.h.values, p.l.values, p.c.values; last = None
    for k in range(len(p) - 20, len(p)):
        if Lw[k] > H[k - 2]: last = (1, H[k - 2], k)
        elif H[k] < Lw[k - 2]: last = (-1, Lw[k - 2], k)
    if last is None: return 0
    side, edge, k = last
    after = C[k + 1:]
    if side == 1 and (after < edge).any(): return 0
    if side == -1 and (after > edge).any(): return 0
    return side

def structure_bias(D, day):
    p = D[D.index < day].tail(60); H, Lw = p.h.values, p.l.values
    sh = [H[i] for i in range(1, len(H) - 1) if H[i] > H[i - 1] and H[i] > H[i + 1]]
    sl = [Lw[i] for i in range(1, len(Lw) - 1) if Lw[i] < Lw[i - 1] and Lw[i] < Lw[i + 1]]
    if len(sh) < 2 or len(sl) < 2: return 0
    if sh[-1] > sh[-2] and sl[-1] > sl[-2]: return 1
    if sh[-1] < sh[-2] and sl[-1] < sl[-2]: return -1
    return 0

def venom_day(x, side, tf=1, tick=0.25):
    """x: one NY day of 1-min bars. side +1 bullish (raid the sell-side low, buy), -1 bearish. tf 1 or 5 (minutes)."""
    bars = x[(x.hr >= 8) & (x.hr < 16)].reset_index(drop=True) if tf == 1 else five(x[(x.hr >= 8) & (x.hr < 16)])
    pre = x[(x.hr >= 8) & (x.hr < 9.5)]
    if len(pre) < 60: return None
    rng_hi, rng_lo = pre.h.max(), pre.l.min(); fv = first_fvg_1m(x)
    H, Lw, C, HR, O = bars.h.values, bars.l.values, bars.c.values, bars.hr.values, bars.o.values
    i930 = int(np.argmax(HR >= 9.5))
    for k in range(i930 + 2, len(H) - 4):
        if HR[k] >= 11.0: break                                    # raid inside 09:30-11:00 (his examples 09:39, 10:25)
        # pool = most extreme CONFIRMED swing since 09:30 (3-bar fractal, confirmed before candle k)
        sw = [j for j in range(i930 + 1, k - 1) if ((Lw[j] < Lw[j - 1] and Lw[j] < Lw[j + 1]) if side == 1 else (H[j] > H[j - 1] and H[j] > H[j + 1]))]
        if not sw: continue
        pool = min(Lw[j] for j in sw) if side == 1 else max(H[j] for j in sw)
        if not ((Lw[k] < pool) if side == 1 else (H[k] > pool)): continue
        # raid candles = consecutive candles CLOSING beyond the pool, 1 or 2 ("we have one, two")
        n = 1
        while n < 3 and k + n < len(H) and ((C[k + n] < pool) if side == 1 else (C[k + n] > pool)): n += 1
        if n > 2: return None
        last = k + n - 1; r_ext = Lw[k:last + 1].min() if side == 1 else H[k:last + 1].max()
        into = any((H[j] < Lw[j - 2]) if side == 1 else (Lw[j] > H[j - 2]) for j in range(max(2, k - 6), k + 1))
        # April tutorial (7wL2oyebbvU t=825-889): 'an inefficient run higher to it and then an inefficient run away from it ...
        # it quickly left ... these are the two fangs' -> the leaving FVG forms right away: last raid candle -> candle 2 after it.
        jl = last + 2
        leave = ((Lw[jl] > H[last]) if side == 1 else (H[jl] < Lw[last])) if jl < len(H) else False
        if not (into and leave): return None                        # first raid of the pool only
        E = O[last] if ENTRY == 'ob' else C[k + 1]                  # 'ob': 'an entry back to the opening price of the last candle,
                                                                    # which is the Venom order block' (t=1226); 'c2': May video
        stop = (r_ext - tick) if side == 1 else (r_ext + tick)
        return dict(raid_hr=HR[k], n=n, pool=pool, raid_ext=r_ext, entry=E, stop=stop, fill_from=HR[jl + 1] if jl + 1 < len(HR) else 99,  # NO LOOKAHEAD: fill only after the leaving FVG has closed
                    tgt_fvg=((fv[1] if side == 1 else fv[2]) if fv else None), tgt_rng=rng_hi if side == 1 else rng_lo, side=side)
    return None

def simulate(x, v, tgt):
    """limit at entry 'or less' (longs) from the candle after the second; stop-first; flat 16:00."""
    side = v['side']; E, stop = v['entry'], v['stop']
    if tgt is None or (tgt - E) * side <= 0 or (E - stop) * side <= 0: return None
    y = x[(x.hr >= v['fill_from']) & (x.hr < 16)]
    h, l, c, hr = y.h.values, y.l.values, y.c.values, y.hr.values
    fill = None
    for q in range(len(h)):
        if hr[q] >= 12: break                                       # unfilled by noon -> no trade [stated in X18 spec]
        if (l[q] <= E) if side == 1 else (h[q] >= E): fill = q; break
    if fill is None: return None
    if (l[fill] <= stop) if side == 1 else (h[fill] >= stop): return dict(exit=stop, R=-1.0, how='stop')
    xp, _, how = L.walk(h, l, c, hr, fill + 1, side, E, stop, tgt, flat_hr=16)
    return dict(exit=xp, R=side * (xp - E) / abs(E - stop), how=how)

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'examples'
    d = L.load_cme('NQ', '1min'); D = L.daily(d)
    if mode == 'examples':
        for day, side, note in ((dt.date(2025, 4, 3), -1, 'ICT short (7wL2oyebbvU)'), (dt.date(2025, 5, 12), 1, 'ICT long (FjSHAOI-Mok)')):
            x = d[d.date == day]; v = venom_day(x, side, tf=1 if side == -1 else 5)
            print(f'{day} {note} tf={1 if side == -1 else 5}: bias F={daily_bias(D, day)} S={structure_bias(D, day)} | detector: {v}')
            if v:
                for tn in ('tgt_fvg', 'tgt_rng'): print('   ', tn, v[tn], simulate(x, v, v[tn]))

    if mode in ('learn', 'exam'):
        rows = []
        for m in ('NQ', 'ES'):
            dm = d if m == 'NQ' else L.load_cme('ES', '1min'); Dm = L.daily(dm)
            for day, x in dm.groupby('date'):
                if pd.Timestamp(day).dayofweek > 4: continue
                era = 'learn' if day.year <= 2023 else 'exam'
                if mode == 'learn' and era != 'learn': continue
                for bname, bf in (('ICTbias', daily_bias), ('trend', structure_bias)):
                    b = bf(Dm, day)
                    if b == 0: continue
                    for tf in (1, 5):
                        v = venom_day(x, b, tf)
                        if not v: continue
                        for tn in ('rng', 'fvg'):
                            tgt = v['tgt_' + tn]
                            if tgt is None or (tgt - v['entry']) * b <= 0: continue
                            r = simulate(x, v, tgt)
                            if not r: continue
                            risk = abs(v['entry'] - v['stop'])
                            for venue in ('cme', 'broker'):
                                rows.append(dict(model=f'{m}@{venue}', cell=f'V{tf}/{bname}/T{tn}', m=m, date=day, gross=r['R'],
                                                 net=r['R'] - L.cost_R(venue, m, v['entry'], risk), era=era))
        T = pd.DataFrame(rows)
        L.report(T.drop(columns='era'), 'X18 Venom (AM, frozen rules) ' + mode, L.CME_LEARN_END,
                 'C:/Users/you/Desktop/TradeDeck/specs/X18-results-' + mode + '.md', exam=(mode == 'exam'))
        for (mo, cell, era), g in T.groupby(['model', 'cell', 'era']):
            print(f'  ex-top5 {era} {mo}/{cell}: {g.net.sort_values().iloc[:-5].mean():+.3f}R (n={len(g)})')
