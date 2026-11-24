"""X19 OTE after a PDH/PDL run (OTE Pattern Recognition Series), rule frozen from Vol. 01 (specs/ict-examples/ote-pdh-run-CHART-READ.md).
Usage: python tools/x19_ote.py day PAIR YYYY-MM-DD   -> what the frozen rule does that day (for checking his Vol. 02-20 examples)
       python tools/x19_ote.py learn|exam"""
import os, sys, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L

def fx_day(d, day):
    """FX trading day = 17:00 NY previous calendar day -> 17:00 NY `day`."""
    prev = day - dt.timedelta(days=1 if day.weekday() != 0 else 3)
    return d[((d.date == prev) & (d.hr >= 17)) | ((d.date == day) & (d.hr < 17))].reset_index(drop=True)

def rnd_down10(p, side, pip):
    """'rounding down to the next 10 level' (toward entry): nearest 10-pip level not beyond p."""
    step = 10 * pip
    return np.floor(p / step) * step if side == 1 else np.ceil(p / step) * step

def ote_day(x, pdh, pdl, pip):
    """Frozen Vol. 01 rule. Returns dict or None. Side chosen by which previous-day extreme is taken first before 08:30
    ('wait for the chart to show you ... does it take out the previous day's high or the previous day's low')."""
    is_day = np.r_[np.zeros(0)]
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]
    pre = np.where((dates == day) & (hr < 8.5))[0]               # midnight -> 08:30 NY ('here's midnight in New York and during
                                                                 # the London session it created a run higher ... above the PDH')
    if len(pre) < 60: return None
    side = None; t0 = None
    for i in pre:
        if h[i] > pdh: side, t0 = 1, i; break
        if l[i] < pdl: side, t0 = -1, i; break
    if side is None: return None
    seg = pre[pre >= t0]
    if side == 1:
        hi_i = seg[np.argmax(h[seg])]; H = h[hi_i]
        low_seg = seg[(seg > t0) & (seg <= hi_i)]                # pullback AFTER the breaking candle ('blows out the PDH not by
        if len(low_seg) == 0: return None                         # much ... then it gyrates down ... then there's a displacement')
        Lo = l[low_seg].min()
        A, B = Lo, H
    else:
        lo_i = seg[np.argmin(l[seg])]; Lw = l[lo_i]
        hi_seg = seg[(seg > t0) & (seg <= lo_i)]
        if len(hi_seg) == 0: return None
        Hi = h[hi_seg].max()
        A, B = Hi, Lw
    rng = abs(B - A)
    if rng <= 0: return None
    E = B - side * 0.62 * rng                                     # 62% retracement limit
    stop = rnd_down10(A - side * 1e-12, -side, pip) if False else (np.floor(A / (10 * pip)) * 10 * pip if side == 1 else np.ceil(A / (10 * pip)) * 10 * pip)
    if (E - stop) * side <= 0: stop = A - side * 5 * pip         # A sits on a 10-level: go one level beyond? keep 5 pips [CHOSEN fallback]
    tp1 = B + side * 0.5 * rng; tp2 = rnd_down10(B + side * 1.0 * rng, side, pip)
    return dict(side=side, A=A, B=B, entry=E, stop=stop, tp1=tp1, tp2=tp2, tp1_pips=abs(tp1 - E) / pip)

def simulate(x, v, pip):
    """limit fill 08:30-11:00; stop-first; TP1 half if >= 15 pips (else skip TP1), rest to TP2; flat 16:00 NY."""
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]; side, E, stop = v['side'], v['entry'], v['stop']
    win = np.where((dates == day) & (hr >= 8.5) & (hr < 11))[0]
    fill = next((q for q in win if ((l[q] <= E) if side == 1 else (h[q] >= E))), None)
    if fill is None: return None
    R = abs(E - stop); half = v['tp1_pips'] >= 15; got = 0.0; left = 1.0
    if (l[fill] <= stop) if side == 1 else (h[fill] >= stop): return dict(R=-1.0, how='stop')
    for q in range(fill + 1, len(h)):
        if dates[q] == day and hr[q] >= 16: return dict(R=got + left * side * (c[q - 1] - E) / R, how='time')
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return dict(R=got + left * side * (stop - E) / R, how='stop')
        if half and left == 1.0 and ((h[q] >= v['tp1']) if side == 1 else (l[q] <= v['tp1'])):
            got += 0.5 * side * (v['tp1'] - E) / R; left = 0.5
        if (h[q] >= v['tp2']) if side == 1 else (l[q] <= v['tp2']):
            return dict(R=got + left * side * (v['tp2'] - E) / R, how='tp2')
    return dict(R=got + left * side * (c[-1] - E) / R, how='end')

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'day':
        pair, day = sys.argv[2], dt.date.fromisoformat(sys.argv[3]); pip = L.pip_of(pair)
        d = L.load_fx(pair, '5m'); D = L.daily_fx(d)
        prev = D[D.index < day].iloc[-1]; x = fx_day(d, day)
        v = ote_day(x, prev.h, prev.l, pip); print(pair, day, 'PDH', prev.h, 'PDL', prev.l, '->', v)
        if v: print('   result', simulate(x, v, pip))

# ---------------- v2: general rule from the whole OTE series (groundwork specs/ict-examples/ote-pdh-pdl-ny-session.md) -------------
def bias_v2(D, day, x, pip):
    """'what's the market been doing all week' / 'does it take out the previous day's high or low': yesterday's raid direction
    (took the prior high only -> bullish, prior low only -> bearish); if both/neither, London (00:00-08:30) taking PDH/PDL decides."""
    p = D[D.index < day]
    if len(p) < 3: return 0
    y, yy = p.iloc[-1], p.iloc[-2]
    up, dn = y.h > yy.h, y.l < yy.l
    if up and not dn: return 1
    if dn and not up: return -1
    lon = x[(x.date == day) & (x.hr < 8.5)]
    if len(lon) and lon.h.max() > y.h and not lon.l.min() < y.l: return 1
    if len(lon) and lon.l.min() < y.l and not lon.h.max() > y.h: return -1
    return 0

def ote_v2(x, side, pip, min_leg=10):
    """Scan 08:30-11:00. Leg = latest confirmed 5m swing high (long) and the lowest low since the last higher swing high before it;
    limit at 62% of the leg; fill before the leg's low is taken, inside the window. Stop = leg low rounded to the 5-pip level below."""
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]
    win = np.where((dates == day) & (hr >= 8.5) & (hr < 11))[0]
    if len(win) == 0: return None
    H_, L_ = (h, l) if side == 1 else (-l, -h)                   # mirror shorts
    for q in win:
        fr = [j for j in range(2, q - 1) if H_[j] > H_[j - 1] and H_[j] > H_[j + 1]]   # confirmed by bar j+1 <= q-1
        if not fr: continue
        jh = fr[-1]; top = H_[jh]
        prev_higher = [j for j in fr[:-1] if H_[j] > top]
        start = prev_higher[-1] if prev_higher else max(0, jh - 288)
        jl = start + int(np.argmin(L_[start:jh + 1])); bot = L_[jl]
        leg = top - bot
        if leg < min_leg * pip: continue
        if L_[jh:q].min() < bot: continue                        # leg low already taken -> leg void
        E = top - 0.62 * leg
        if L_[q] <= E:                                           # filled this bar
            stop_m = np.floor(bot / (5 * pip)) * 5 * pip
            if stop_m >= bot: stop_m -= 5 * pip
            to = lambda v: v if side == 1 else -v
            tp1 = top + 0.5 * leg; tp2 = np.floor((top + 1.0 * leg) / (10 * pip)) * 10 * pip
            return dict(side=side, fill_i=q, fill_hr=hr[q], entry=to(E), stop=to(stop_m), leg_lo=to(bot), leg_hi=to(top),
                        tp1=to(tp1), tp2=to(tp2), tp1_pips=(tp1 - E) / pip)
    return None

def check_examples():
    ex = [('AUDUSD', '2020-05-07', 1, 0.6450, 0.6430), ('USDJPY', '2020-05-08', 1, 106.315, 106.215),
          ('GBPUSD', '2020-05-12', -1, None, None), ('GBPUSD', '2020-05-15', -1, None, None), ('GBPUSD', '2020-05-18', 1, None, 1.2168),
          ('AUDUSD', '2020-05-20', 1, 0.65776, 0.65676), ('GBPUSD', '2020-05-27', -1, 1.22681, 1.22881),
          ('AUDUSD', '2020-05-28', 1, 0.6619, None), ('GBPUSD', '2020-06-01', 1, 1.2388, 1.2373), ('USDJPY', '2020-06-03', 1, 108.70, 108.60),
          ('EURUSD', '2020-06-04', 1, 1.1225, None)]
    cache = {}
    for pair, ds, his_side, his_E, his_S in ex:
        if pair not in cache: dd_ = L.load_fx(pair, '5m'); cache[pair] = (dd_, L.daily_fx(dd_))
        d, D = cache[pair]; day = dt.date.fromisoformat(ds); pip = L.pip_of(pair); x = fx_day(d, day)
        b = bias_v2(D, day, x, pip); v = ote_v2(x, his_side, pip)
        e = f"entry {v['entry']:.5f} ({(v['entry'] - his_E) / pip:+.1f}p vs his)" if v and his_E else (f"entry {v['entry']:.5f}" if v else 'NO TRADE')
        print(f"{pair} {ds} his {'LONG' if his_side == 1 else 'SHORT'} | bias_v2 {b:+d} {'OK' if b == his_side else 'WRONG'} | "
              f"{e} fill {v['fill_hr'] if v else '-'} stop {v['stop'] if v else '-'} (his {his_S})")

if __name__ == '__main__' and sys.argv[1] == 'check2':
    check_examples()

def ote_v3(x, side, pip, min_leg=10):
    """Leg = the leg that made the latest new extreme of the FX day (high for longs), anchored at the lowest low between the
    previous new-day-high and that high (Vol 1: 0.64347; Vol 2: overnight low; Vol 14: NY-session high). Limit at 62% of the leg,
    filled 08:30-11:00 before the anchor is taken. Stop = anchor rounded to the 5-pip level beyond it."""
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]
    H_, L_ = (h, l) if side == 1 else (-l, -h)
    newext = []; best = -np.inf
    for i in range(len(H_)):
        if H_[i] > best: best = H_[i]; newext.append(i)
    win = np.where((dates == day) & (hr >= 8.5) & (hr < 11))[0]
    for q in win:
        prior = [i for i in newext if i < q]
        if len(prior) < 2: continue
        jh = prior[-1]
        # the impulse = the run of new highs that ends at jh; anchor search starts at the new high BEFORE that run, but a run
        # counts as continuous only while each new high comes within 3 bars of the last [CHOSEN to merge one push]
        k = len(prior) - 1
        while k > 0 and prior[k] - prior[k - 1] <= 3: k -= 1
        if k == 0: continue
        jprev = prior[k - 1]
        jl = jprev + int(np.argmin(L_[jprev:jh + 1])); top, bot = H_[jh], L_[jl]
        leg = top - bot
        if leg < min_leg * pip or L_[jh:q].min() < bot: continue
        E = top - 0.62 * leg
        if L_[q] <= E:
            stop_m = np.floor(bot / (5 * pip)) * 5 * pip
            if stop_m >= bot: stop_m -= 5 * pip
            to = lambda v: v if side == 1 else -v
            return dict(side=side, fill_hr=hr[q], entry=to(E), stop=to(stop_m), leg=(to(bot), to(top)),
                        tp1=to(top + 0.5 * leg), tp2=to(np.floor((top + leg) / (10 * pip)) * 10 * pip), tp1_pips=1.12 * leg / pip)
    return None

if __name__ == '__main__' and sys.argv[1] == 'check3':
    ex = [('AUDUSD', '2020-05-07', 1, 0.6450, 0.6430), ('USDJPY', '2020-05-08', 1, 106.315, 106.215),
          ('GBPUSD', '2020-05-12', -1, None, None), ('GBPUSD', '2020-05-15', -1, None, None), ('GBPUSD', '2020-05-18', 1, None, 1.2168),
          ('AUDUSD', '2020-05-20', 1, 0.65776, 0.65676), ('GBPUSD', '2020-05-27', -1, 1.22681, 1.22881),
          ('AUDUSD', '2020-05-28', 1, 0.6619, None), ('GBPUSD', '2020-06-01', 1, 1.2388, 1.2373), ('USDJPY', '2020-06-03', 1, 108.70, 108.60),
          ('EURUSD', '2020-06-04', 1, 1.1225, None)]
    cache = {}
    for pair, ds, s_, E_, S_ in ex:
        if pair not in cache: cache[pair] = L.load_fx(pair, '5m')
        d = cache[pair]; day = dt.date.fromisoformat(ds); pip = L.pip_of(pair); x = fx_day(d, day)
        v = ote_v3(x, s_, pip)
        if v: print(f"{pair} {ds} {'L' if s_ == 1 else 'S'}: leg {v['leg'][0]:.5f}->{v['leg'][1]:.5f} entry {v['entry']:.5f}" + (f" ({(v['entry'] - E_) / pip:+.1f}p vs his {E_})" if E_ else '') + f" fill {v['fill_hr']:.2f} stop {v['stop']:.5f} (his {S_})")
        else: print(f"{pair} {ds}: NO TRADE")

def ote_v4(x, side, pip, min_leg=10):
    """His Month-1 rule: fib from the 3-candle swing low that started the push (unviolated) to the latest 3-candle swing high;
    on a new swing high with the low intact, keep the low and move the high. Swing low used = the most recent confirmed swing low
    before that swing high. Limit 62%, fill 08:30-11:00 before the low is violated; stop at the 5-pip level beyond the low."""
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]
    H_, L_ = (h, l) if side == 1 else (-l, -h)
    win = np.where((dates == day) & (hr >= 8.5) & (hr < 11))[0]
    for q in win:
        sh = [j for j in range(1, q - 1) if H_[j] > H_[j - 1] and H_[j] > H_[j + 1]]
        if not sh: continue
        jh = sh[-1]
        sl = [j for j in range(1, jh) if L_[j] < L_[j - 1] and L_[j] < L_[j + 1] and L_[j:jh + 1].min() == L_[j]]
        if not sl: continue
        jl = sl[-1]; top, bot = H_[jh], L_[jl]
        if top < H_[jl:jh + 1].max(): continue                  # jh must be the high of the push from jl
        leg = top - bot
        if leg < min_leg * pip or L_[jh:q].min() < bot: continue
        E = top - 0.62 * leg
        if L_[q] <= E:
            stop_m = np.floor(bot / (5 * pip)) * 5 * pip
            if stop_m >= bot: stop_m -= 5 * pip
            to = lambda v: v if side == 1 else -v
            return dict(side=side, fill_hr=hr[q], entry=to(E), stop=to(stop_m), leg=(to(bot), to(top)),
                        tp1=to(top + 0.5 * leg), tp2=to(np.floor((top + leg) / (10 * pip)) * 10 * pip), tp1_pips=1.12 * leg / pip)
    return None

if __name__ == '__main__' and sys.argv[1] == 'check4':
    ex = [('AUDUSD', '2020-05-07', 1, 0.6450, 0.6430), ('USDJPY', '2020-05-08', 1, 106.315, 106.215),
          ('GBPUSD', '2020-05-12', -1, None, None), ('GBPUSD', '2020-05-15', -1, None, None), ('GBPUSD', '2020-05-18', 1, None, 1.2168),
          ('AUDUSD', '2020-05-20', 1, 0.65776, 0.65676), ('GBPUSD', '2020-05-27', -1, 1.22681, 1.22881),
          ('AUDUSD', '2020-05-28', 1, 0.6619, None), ('GBPUSD', '2020-06-01', 1, 1.2388, 1.2373), ('USDJPY', '2020-06-03', 1, 108.70, 108.60),
          ('EURUSD', '2020-06-04', 1, 1.1225, None)]
    cache = {}
    for pair, ds, s_, E_, S_ in ex:
        if pair not in cache: cache[pair] = L.load_fx(pair, '5m')
        d = cache[pair]; day = dt.date.fromisoformat(ds); pip = L.pip_of(pair); x = fx_day(d, day)
        v = ote_v4(x, s_, pip)
        if v: print(f"{pair} {ds} {'L' if s_ == 1 else 'S'}: leg {v['leg'][0]:.5f}->{v['leg'][1]:.5f} entry {v['entry']:.5f}" + (f" ({(v['entry'] - E_) / pip:+.1f}p vs his {E_})" if E_ else '') + f" fill {v['fill_hr']:.2f} stop {v['stop']:.5f} (his {S_})")
        else: print(f"{pair} {ds}: NO TRADE")

def run_era(pairs, version, era_years):
    rows = []
    for pair in pairs:
        d = L.load_fx(pair, '5m'); D = L.daily_fx(d); pip = L.pip_of(pair)
        days = sorted(set(d.date))
        for day in days:
            if day.weekday() > 4 or day.year not in era_years: continue
            x = fx_day(d, day)
            if len(x) < 200: continue
            b = bias_v2(D, day, x, pip)
            if b == 0: continue
            v = {'V4': ote_v4, 'V3': ote_v3, 'V5': ote_v5}[version](x, b, pip)
            if not v: continue
            r = simulate_v(x, v, pip)
            if r is None: continue
            rows.append(dict(model='OTE', cell=version, m=pair, date=day, gross=r, net=r - L.cost_R('broker_fx', pair, v['entry'], abs(v['entry'] - v['stop']))))
    return pd.DataFrame(rows)

def simulate_v(x, v, pip):
    hr, h, l, c, dates = x.hr.values, x.h.values, x.l.values, x.c.values, x.date.values
    day = dates[-1]; side, E, stop = v['side'], v['entry'], v['stop']; R = abs(E - stop)
    st = int(np.argmax((dates == day) & (hr >= v['fill_hr'] - 1e-9)))
    if (l[st] <= stop) if side == 1 else (h[st] >= stop): return -1.0
    half = v['tp1_pips'] >= 15; got, left = 0.0, 1.0
    for q in range(st + 1, len(h)):
        if dates[q] == day and hr[q] >= 16: return got + left * side * (c[q - 1] - E) / R
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return got + left * side * (stop - E) / R
        if half and left == 1.0 and ((h[q] >= v['tp1']) if side == 1 else (l[q] <= v['tp1'])): got += 0.5 * side * (v['tp1'] - E) / R; left = 0.5
        if (h[q] >= v['tp2']) if side == 1 else (l[q] <= v['tp2']): return got + left * side * (v['tp2'] - E) / R
    return got + left * side * (c[-1] - E) / R

def main_era():
    yrs = range(2012, 2020) if sys.argv[1] == 'learn' else range(2020, 2027)
    T = pd.concat([run_era(L.FX_PAIRS, v_, yrs) for v_ in ('V5', 'V4', 'V3')])
    L.report(T, f'X19 ICT OTE series ({sys.argv[1]})', L.FX_LEARN_END, f'C:/Users/you/Desktop/TradeDeck/specs/X19-results-{sys.argv[1]}.md', exam=sys.argv[1] == 'exam')
    for cell, g in T.groupby('cell'): print(f'  ex-top5 {cell}: {g.net.sort_values().iloc[:-5].mean():+.3f}R  n={len(g)}')

def ote_v5(x, side, pip, disp_mult=2.0, min_leg=10):
    """Vol. 14 rule: the leg is the most recent CLEAR leg showing displacement ('one single candle ... real clear indication'),
    not a muddy one. Displacement candle = body >= disp_mult x median body of the previous 48 bars [CHOSEN 2.0], in the trade
    direction. Leg = 3-candle swing low before that candle -> 3-candle swing high after it. 62% limit, filled 08:30-11:00 before the
    leg low is broken; stop at the 5-pip level beyond the low."""
    hr, h, l, o, c, dates = x.hr.values, x.h.values, x.l.values, x.o.values, x.c.values, x.date.values
    day = dates[-1]
    H_, L_, O_, C_ = (h, l, o, c) if side == 1 else (-l, -h, -o, -c)
    body = C_ - O_; absb = np.abs(c - o)
    win = np.where((dates == day) & (hr >= 8.5) & (hr < 11))[0]
    for q in win:
        best = None
        for k in range(q - 2, 50, -1):                               # most recent displacement candle before q
            med = np.median(absb[k - 48:k])
            if body[k] > 0 and body[k] >= disp_mult * max(med, 1e-12):
                best = k; break
        if best is None: continue
        k = best
        sl = [j for j in range(k, max(k - 60, 1), -1) if L_[j] < L_[j - 1] and L_[j] <= L_[j + 1]]
        if not sl: continue
        jl = sl[0]
        sh = [j for j in range(k, q - 1) if H_[j] > H_[j - 1] and H_[j] >= H_[j + 1]]
        if not sh: continue
        jh = sh[0]
        while jh + 1 < q - 1 and H_[jh + 1] > H_[jh]: jh += 1
        top, bot = H_[jl:jh + 1].max(), L_[jl]
        leg = top - bot
        if leg < min_leg * pip or L_[jh:q].min() < bot: continue
        E = top - 0.62 * leg
        if L_[q] <= E:
            stop_m = np.floor(bot / (5 * pip)) * 5 * pip
            if stop_m >= bot: stop_m -= 5 * pip
            to = lambda v: v if side == 1 else -v
            return dict(side=side, fill_hr=hr[q], entry=to(E), stop=to(stop_m), leg=(to(bot), to(top)),
                        tp1=to(top + 0.5 * leg), tp2=to(np.floor((top + leg) / (10 * pip)) * 10 * pip), tp1_pips=1.12 * leg / pip)
    return None

if __name__ == '__main__' and sys.argv[1] == 'check5':
    ex = [('AUDUSD', '2020-05-07', 1, 0.6450, 0.6430), ('USDJPY', '2020-05-08', 1, 106.315, 106.215),
          ('AUDUSD', '2020-05-20', 1, 0.65776, 0.65676), ('GBPUSD', '2020-05-27', -1, 1.22681, 1.22881),
          ('AUDUSD', '2020-05-28', 1, 0.6619, None), ('GBPUSD', '2020-06-01', 1, 1.2388, 1.2373), ('USDJPY', '2020-06-03', 1, 108.70, 108.60),
          ('EURUSD', '2020-06-04', 1, 1.1225, None)]
    cache = {}; ok = 0
    for pair, ds, s_, E_, S_ in ex:
        if pair not in cache: cache[pair] = L.load_fx(pair, '5m')
        d = cache[pair]; day = dt.date.fromisoformat(ds); pip = L.pip_of(pair); x = fx_day(d, day)
        v = ote_v5(x, s_, pip)
        if v:
            gap = (v['entry'] - E_) / pip; ok += abs(gap) <= 5
            print(f"{pair} {ds}: leg {v['leg'][0]:.5f}->{v['leg'][1]:.5f} entry {v['entry']:.5f} ({gap:+.1f}p vs his {E_}) fill {v['fill_hr']:.2f} stop {v['stop']:.5f} (his {S_})")
        else: print(f"{pair} {ds}: NO TRADE")
    print('within 5 pips:', ok, 'of', len(ex))

if __name__ == '__main__' and sys.argv[1] in ('learn', 'exam'):
    main_era()

def bias_clear(D, day, x, pip):
    """'a very discernible clear unambiguous directional bias' (Vol. 01 t=1147): yesterday raided only one side of the day before
    AND London (00:00-08:30) has taken that same side's previous-day extreme. Otherwise no trade."""
    p = D[D.index < day]
    if len(p) < 3: return 0
    y, yy = p.iloc[-1], p.iloc[-2]
    up, dn = y.h > yy.h, y.l < yy.l
    lon = x[(x.date == day) & (x.hr < 8.5)]
    if not len(lon): return 0
    if up and not dn and lon.h.max() > y.h: return 1
    if dn and not up and lon.l.min() < y.l: return -1
    return 0

def main_clear():
    rows = []
    for pair in L.FX_PAIRS:
        d = L.load_fx(pair, '5m'); D = L.daily_fx(d); pip = L.pip_of(pair)
        for day in sorted(set(d.date)):
            if day.weekday() > 4 or not (2012 <= day.year <= 2019): continue
            x = fx_day(d, day)
            if len(x) < 200: continue
            b = bias_clear(D, day, x, pip)
            if b == 0: continue
            v = ote_v5(x, b, pip)
            if not v: continue
            r = simulate_v(x, v, pip)
            rows.append(dict(model='OTE-clearbias', cell='V5', m=pair, date=day, gross=r, net=r - L.cost_R('broker_fx', pair, v['entry'], abs(v['entry'] - v['stop']))))
    T = pd.DataFrame(rows)
    L.report(T, 'X19 OTE V5, clear-bias days only (learn)', L.FX_LEARN_END, 'C:/Users/you/Desktop/TradeDeck/specs/X19-results-clearbias.md')

if __name__ == '__main__' and sys.argv[1] == 'clear':
    main_clear()
