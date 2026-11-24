"""X20 ICT Silver Bullet, rebuilt from his quoted rules (specs/ict-examples/silver-bullet.md R1-R29) + chart reads.
Usage: python tools/x20_sb.py check | learn | exam"""
import os, sys, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L

WINDOWS = ((3, 4), (10, 11), (14, 15))                                   # R2-R4 (lecture)
FRAME = {'ES': 10.0, 'NQ': 40.0}                                        # R14 10 pts ES; NQ = 4x ES (R15b: 5 ES = 20 NQ)
TAKE = {'ES': 5.0, 'NQ': 20.0}                                          # R15 / R15b
TICK = 0.25

def draws(d_day, d_prev, w0):
    """Liquidity pools known before the window (R7): previous day H/L, previous session H/L for this window."""
    pools = [d_prev.h.max(), d_prev.l.min()]
    if w0 == 3: sess = d_prev[d_prev.hr >= 13.5]                          # prior PM session (chart read May 9: prior PM low)
    elif w0 == 10: sess = d_day[(d_day.hr >= 2) & (d_day.hr < 5)]          # London session (R7: "high or low of the London session")
    else: sess = d_day[(d_day.hr >= 9.5) & (d_day.hr < 13.5)]              # AM + lunch (chart read May 11: lunch high)
    if len(sess): pools += [sess.h.max(), sess.l.min()]
    return pools

def to5(x):
    y = x.copy(); y['b'] = (y.hr * 12).astype(int)
    g = y.groupby('b'); return pd.DataFrame({'hr': g.hr.first(), 'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last()}).reset_index(drop=True)

def sb_window(x, d_prev, w0, m, tf=1, want=0):
    """One window. Returns trade dict or None. 1-minute bars of the NY day in x."""
    w = x[(x.hr >= w0 - 1) & (x.hr < w0 + 2)].reset_index(drop=True)
    if tf == 5: w = to5(w)
    if len(w) < (100 if tf == 1 else 20): return None
    h, l, o, c, hr = w.h.values, w.l.values, w.o.values, w.c.values, w.hr.values
    pools = draws(x, d_prev, w0)
    i0 = int(np.argmax(hr >= w0)); i1 = int(np.argmax(hr >= w0 + 1)) if (hr >= w0 + 1).any() else len(h)
    for k in range(max(i0, 4 if tf == 1 else 2), i1):                                     # candle 3 of the FVG closes inside the window (R13)
        for side in ((-1, 1) if want == 0 else (want,)):
            gap = (h[k] < l[k - 2]) if side == -1 else (l[k] > h[k - 2])
            if not gap: continue
            # MSS: the displacement (candle 2) closes beyond the most recent 1m swing in its direction (R10, R10b)
            sw = [j for j in range(max(1, k - (40 if tf == 1 else 12)), k - 2) if ((l[j] < l[j - 1] and l[j] < l[j + 1]) if side == -1 else (h[j] > h[j - 1] and h[j] > h[j + 1]))]
            if not sw: continue
            lvl = l[sw[-1]] if side == -1 else h[sw[-1]]
            if not ((c[k - 1] < lvl) if side == -1 else (c[k - 1] > lvl)): continue
            near = h[k] if side == -1 else l[k]                            # R16: limit at the gap's near edge (candle 3 high for shorts)
            stop = (h[k - 2] + TICK) if side == -1 else (l[k - 2] - TICK) # R18: beyond FVG candle 1
            # draw: nearest untaken pool in the trade direction with >= framework room (R7, R14)
            cand = [p for p in pools if ((p < near) if side == -1 else (p > near))]
            cand = [p for p in cand if abs(p - near) >= FRAME[m]]
            if not cand: continue
            draw = max(cand) if side == -1 else min(cand)
            # fill: 1 tick through the limit (R17), inside the window (R6); cancel if the draw trades first (R20)
            fill = None
            for q in range(k + 1, i1):
                if (l[q] <= draw) if side == -1 else (h[q] >= draw): break
                if (h[q] >= near + TICK) if side == -1 else (l[q] <= near - TICK): fill = q; break
            if fill is None: continue
            return dict(side=side, fvg_t=hr[k], fill_t=hr[fill], entry=near, stop=stop, draw=draw, take=near + side * TAKE[m],
                        fill_i=fill, bars=w)
    return None

def outcome(v, tgt):
    w = v['bars']; h, l, c, hr = w.h.values, w.l.values, w.c.values, w.hr.values
    side, E, stop = v['side'], v['entry'], v['stop']; R = abs(E - stop)
    if (h[v['fill_i']] >= stop) if side == -1 else (l[v['fill_i']] <= stop): return -1.0
    for q in range(v['fill_i'] + 1, len(h)):                            # time exit: end of the hour after the window [CHOSEN; R28 not stated]
        if (h[q] >= stop) if side == -1 else (l[q] <= stop): return -1.0
        if (l[q] <= tgt) if side == -1 else (h[q] >= tgt): return side * (tgt - E) / R
    return side * (c[-1] - E) / R

def check():
    ex = [('ES', '2023-05-09', 3, -1, None), ('ES', '2023-05-12', 10, -1, 4146.0), ('ES', '2023-05-11', 14, 1, None),
          ('ES', '2023-04-13', 10, 1, 4133.75), ('ES', '2023-05-17', 10, 1, 4130.0), ('ES', '2023-05-22', 10, -1, 4206.25),
          ('ES', '2023-05-25', 10, 1, 4142.0), ('ES', '2023-05-25', 14, -1, 4169.0), ('ES', '2023-04-12', 10, -1, None),
          ('ES', '2023-03-30', 10, -1, None), ('NQ', '2025-04-01', 10, 1, 19430.0)]
    cache = {}
    for m, ds, w0, s_, E_ in ex:
        if m not in cache: cache[m] = L.load_cme_ict(m, '1min')
        d = cache[m]; day = dt.date.fromisoformat(ds)
        prevd = sorted(x for x in set(d.date) if x < day and pd.Timestamp(x).dayofweek < 5)[-1]
        x = d[d.date == day]; dp = d[d.date == prevd]
        for tf in (1, 5):
          v = sb_window(x, dp, w0, m, tf)
          if v: print(f"tf{tf} {m} {ds} {w0}:00 his {'LONG' if s_ == 1 else 'SHORT'} {E_} | code {'LONG' if v['side'] == 1 else 'SHORT'} FVG {v['fvg_t']:.2f} entry {v['entry']} fill {v['fill_t']:.2f} stop {v['stop']} draw {v['draw']} | +5pt {outcome(v, v['take']):+.2f}R draw {outcome(v, v['draw']):+.2f}R")
          else: print(f"tf{tf} {m} {ds} {w0}:00 his {'LONG' if s_ == 1 else 'SHORT'} {E_} | code: NO TRADE")

if __name__ == '__main__' and sys.argv[1] == 'check': check()


def draw_direction(x, d_prev, w0, D, day):
    """His IRL<->ERL rule (Core Content Month 4/12): if price has just raided an external pool (previous-day or previous-session high/
    low) in the hour before the window, the next draw is the other way; otherwise trade toward external liquidity in the daily bias.
    Daily bias = ICT 'daily FVG not closed through' rule (May 12 Venom video)."""
    import x18_venom as V
    pools_hi = [d_prev.h.max()]; pools_lo = [d_prev.l.min()]
    if w0 == 10: s_ = x[(x.hr >= 2) & (x.hr < 5)]
    elif w0 == 14: s_ = x[(x.hr >= 9.5) & (x.hr < 13)]
    else: s_ = d_prev[d_prev.hr >= 13.5]
    if len(s_): pools_hi.append(s_.h.max()); pools_lo.append(s_.l.min())
    pre = x[(x.hr >= w0 - 1) & (x.hr < w0)]
    if not len(pre): return 0
    took_hi = any(pre.h.max() > p and (x[(x.hr < w0 - 1) & (x.hr >= w0 - 6)].h.max() <= p if len(x[(x.hr < w0 - 1) & (x.hr >= w0 - 6)]) else True) for p in pools_hi)
    took_lo = any(pre.l.min() < p and (x[(x.hr < w0 - 1) & (x.hr >= w0 - 6)].l.min() >= p if len(x[(x.hr < w0 - 1) & (x.hr >= w0 - 6)]) else True) for p in pools_lo)
    if took_hi and not took_lo: return -1
    if took_lo and not took_hi: return 1
    return V.daily_bias(D, day)

def check2():
    ex = [('ES', '2023-05-09', 3, -1, None), ('ES', '2023-05-12', 10, -1, 4146.0), ('ES', '2023-05-11', 14, 1, None),
          ('ES', '2023-04-13', 10, 1, 4133.75), ('ES', '2023-05-17', 10, 1, 4130.0), ('ES', '2023-05-22', 10, -1, 4206.25),
          ('ES', '2023-05-25', 10, 1, 4142.0), ('ES', '2023-05-25', 14, -1, 4169.0), ('ES', '2023-04-12', 10, -1, None),
          ('ES', '2023-03-30', 10, -1, None), ('NQ', '2025-04-01', 10, 1, 19430.0)]
    cache = {}; okdir = 0
    for m, ds, w0, s_, E_ in ex:
        if m not in cache: cache[m] = (L.load_cme_ict(m, '1min'),)
        d = cache[m][0]; D = L.daily(d); day = dt.date.fromisoformat(ds)
        prevd = sorted(z for z in set(d.date) if z < day and pd.Timestamp(z).dayofweek < 5)[-1]
        x = d[d.date == day]; dp = d[d.date == prevd]
        want = draw_direction(x, dp, w0, D, day); okdir += want == s_
        line = f"{m} {ds} {w0}:00 his {'LONG' if s_ == 1 else 'SHORT'} {E_} | draw rule {'LONG' if want == 1 else 'SHORT' if want == -1 else 'none'}"
        for tf in (1, 5):
            v = sb_window(x, dp, w0, m, tf, want) if want else None
            line += f" | tf{tf}: " + (f"entry {v['entry']} @{v['fill_t']:.2f} +5pt {outcome(v, v['take']):+.2f}R" if v else 'none')
        print(line)
    print('direction matches', okdir, 'of', len(ex))

if __name__ == '__main__' and sys.argv[1] == 'check2': check2()

def run_learn(mode='learn'):
    rows = []
    for m in ('NQ', 'ES'):
        d = L.load_cme_ict(m, '1min'); rolls = L.ict_roll_dates()
        days = sorted(z for z in set(d.date) if pd.Timestamp(z).dayofweek < 5)
        byday = dict(tuple(d.groupby('date')))
        for i, day in enumerate(days[1:], 1):
            era = 'learn' if day.year <= 2023 else 'exam'
            if (mode == 'learn' and era != 'learn') or day in rolls: continue
            x = byday[day]; dp = byday[days[i - 1]]
            for w0, _ in WINDOWS:
                for tf in (1, 5):
                    v = sb_window(x, dp, w0, m, tf)
                    if not v: continue
                    R = abs(v['entry'] - v['stop'])
                    for tn, tgt in (('T5', v['take']), ('Tdraw', v['draw'])):
                        r = outcome(v, tgt)
                        for venue in ('cme', 'broker'):
                            rows.append(dict(model=f'{m}@{venue}', cell=f'tf{tf}/{w0}h/{tn}', m=m, date=day, gross=r,
                                             net=r - L.cost_R(venue, m, v['entry'], R), era=era))
    T = pd.DataFrame(rows)
    out = L.report(T.drop(columns='era'), f'X20 Silver Bullet ({mode})', L.CME_LEARN_END, f'C:/Users/you/Desktop/TradeDeck/specs/X20-results-{mode}.md', exam=mode == 'exam')
    for (mo, cell), g in T[(T.era == 'learn') & T.model.str.contains('cme')].groupby(['model', 'cell']):
        print(f'  ex-top5 {mo}/{cell}: {g.net.sort_values().iloc[:-5].mean():+.3f}R n={len(g)}')

if __name__ == '__main__' and sys.argv[1] in ('learn', 'exam'): run_learn(sys.argv[1])

def gate(mode='learn'):
    import random; random.seed(11)
    d = L.load_cme_ict('NQ', '1min'); rolls = L.ict_roll_dates()
    days = sorted(z for z in set(d.date) if pd.Timestamp(z).dayofweek < 5); byday = dict(tuple(d.groupby('date')))
    rows = []
    for i, day in enumerate(days[1:], 1):
        era = 'learn' if day.year <= 2023 else 'exam'
        if era != mode or day in rolls: continue
        x = byday[day]; dp = byday[days[i - 1]]
        for cell, want in (('real', 0), ('coin', random.choice((-1, 1)))):
            v = sb_window(x, dp, 10, 'NQ', 1, want)
            if not v: continue
            R = abs(v['entry'] - v['stop'])
            if R < 4.0: continue
            r = outcome(v, v['draw'])
            rows.append(dict(cell=cell, date=day, net=r - L.cost_R('cme', 'NQ', v['entry'], R), gross=r))
    T = pd.DataFrame(rows)
    for cell, g in T.groupby('cell'):
        g = g.sort_values('date'); s = g.net.cumsum(); dd = float((np.maximum.accumulate(np.r_[0, s.values])[1:] - s.values).max())
        yr = g.groupby(pd.to_datetime(g.date).dt.year).net.sum()
        print(f'{mode} {cell}: n={len(g)} NET {g.net.mean():+.3f}R ex-top5 {g.net.sort_values().iloc[:-5].mean():+.3f}R maxDD {dd:.0f}R | ' + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()))

if __name__ == '__main__' and sys.argv[1] in ('gate', 'gate_exam'): gate('learn' if sys.argv[1] == 'gate' else 'exam')
