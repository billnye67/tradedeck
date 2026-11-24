"""X12b: ICT forex batch 2 at Broker costs (specs/X12-ict-forex.md section X12b). EXAM=1 opens 2020-2026 once."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)
PAIRS = ('EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD')

def load(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='fxcm' and market='{m}' and interval='5m' order by t", c)
    ts = pd.to_datetime(d.t, unit='ms', utc=True).dt.tz_convert('America/New_York')
    d['ts'] = ts.values; d['date'] = ts.dt.date.values; d['hr'] = (ts.dt.hour + ts.dt.minute / 60).values; d['dow'] = ts.dt.dayofweek.values
    return d.drop(columns='t').reset_index(drop=True)

def walk(h, l, cl, hr, i0, side, E, stop, tgt, flat):
    for q in range(i0, len(h)):
        if hr[q] >= flat: return cl[q - 1] if q > i0 else E
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop
        if (h[q] >= tgt) if side == 1 else (l[q] <= tgt): return tgt
    return cl[-1]

def run(m):
    pip = 0.01 if m.endswith('JPY') else 0.0001
    d = load(m); H, L, C, O, HR, DOW = d.h.values, d.l.values, d.c.values, d.o.values, d.hr.values, d.dow.values
    TS = d.ts.values; DATE = d.date.values
    days = {k: v for k, v in d.groupby('date')}
    D = d.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    D = D[D.n >= 200]; D['sma20'] = D.c.rolling(20).mean()
    D['b1'] = np.sign(D.c - D.o); D['b2'] = np.sign(D.c - D.sma20)
    D['lo20'] = D.l.rolling(20).min(); D['hi20'] = D.h.rolling(20).max()
    idx = list(D.index); pos = {dt: k for k, dt in enumerate(idx)}
    rows = []
    def add(model, cell, dt, side, E, stop, gross_R, nights=0):
        risk = abs(E - stop)
        if risk <= 0: return
        cost = (0.8e-4 * E + 0.6 * pip + nights * 5e-4 * E) / risk
        rows.append(dict(m=m, model=model, cell=cell, era='learn' if dt.year <= 2019 else 'exam', date=dt, gross=gross_R, net=gross_R - cost, risk_pips=risk / pip))
    first_bar = {dt: g.index[0] for dt, g in days.items()}
    for dt in idx:
        k = pos[dt]
        if k < 25: continue
        prev = D.iloc[k - 1]; x = days[dt]; h, l, cl, hr, o = x.h.values, x.l.values, x.c.values, x.hr.values, x.o.values
        dow = x.dow.values[0]
        if dow > 4: continue
        # ---------- M3 Charter Model 2: Tuesday 00:00 -> Thursday 00:00 ----------
        if dow == 1:
            i0 = first_bar[dt]; E = O[i0]
            for cell, b in (('B1', prev.b1), ('B2', prev.b2)):
                if b == 0 or b != b: continue
                side = int(b); stop = E - side * 50 * pip
                for tpn, tp in (('TP50', 50), ('TP100', 100)):
                    tgt = E + side * tp * pip; xp = None; nights = 0
                    for q in range(i0, min(i0 + 600, len(H))):
                        if DOW[q] >= 3:
                            xp = O[q]; break
                        if q > i0 and HR[q] >= 17 and HR[q - 1] < 17: nights += 1
                        if (L[q] <= stop) if side == 1 else (H[q] >= stop): xp = stop; break
                        if (H[q] >= tgt) if side == 1 else (L[q] <= tgt): xp = tgt; break
                    if xp is None: continue
                    add('M3', f'{cell}/{tpn}', dt, side, E, stop, side * (xp - E) / abs(E - stop), nights)
        # ---------- M5 NY-open counter-retrace OTE ----------
        s2 = np.where(hr >= 2)[0]; s7 = np.where(hr >= 7)[0]
        if len(s2) and len(s7):
            lon_up = o[s7[0]] > o[s2[0]]
            for cell, b in (('B1', prev.b1), ('B2', prev.b2)):
                if b == 0 or b != b: continue
                side = int(b)
                if (side == 1) != lon_up: continue
                pre = slice(0, s7[0])
                if s7[0] < 10: continue
                if side == 1: sl, shh = l[pre].min(), h[pre].max()
                else: sl, shh = h[pre].max(), l[pre].min()
                sw = abs(shh - sl)
                if sw <= 0: continue
                lim = shh - side * 0.62 * sw; ok = False; filled = None
                for q in range(s7[0], len(h)):
                    if hr[q] >= 11: break
                    if hr[q] < 9 and ((shh - l[q]) if side == 1 else (h[q] - shh)) >= 20 * pip: ok = True
                    if not ok and hr[q] >= 9: break
                    if ok and ((l[q] <= lim) if side == 1 else (h[q] >= lim)): filled = q; break
                if filled is None: continue
                stop = sl - side * 5 * pip
                if (lim - stop) * side <= 0: continue
                for tn, tgt in (('T1', shh), ('T2', prev.h if side == 1 else prev.l)):
                    if (tgt - lim) * side <= 0: continue
                    xp = stop if ((l[filled] <= stop) if side == 1 else (h[filled] >= stop)) else walk(h, l, cl, hr, filled + 1, side, lim, stop, tgt, 16)
                    add('M5', f'{cell}/{tn}', dt, side, lim, stop, side * (xp - lim) / abs(lim - stop))
        # ---------- M6 OTE scalping drill ----------
        for cell, b in (('B2', prev.b2), ('B1', prev.b1)):
            if b == 0 or b != b: continue
            side = int(b)   # bias direction
            tgt = (prev.lo20 - pip) if side == -1 else (prev.hi20 + pip)
            if (tgt - cl[0]) * side <= 0: continue
            for a, z in ((2, 5), (8.5, 11)):
                w = np.where((hr >= a) & (hr < z))[0]
                if len(w) < 6: continue
                ext = None; sw_lo = None; q = w[0]
                while q <= w[-1]:
                    if side == -1:
                        if ext is None or h[q] > ext: ext, sw_lo = h[q], l[q]
                        sw_lo = min(sw_lo, l[q]); sw = ext - sw_lo; lim = sw_lo + 0.62 * sw
                        if sw >= 15 * pip and q + 1 <= w[-1] and h[q + 1] >= lim and h[q + 1] < ext:
                            E = lim; stop = ext + 5 * pip
                            xp = walk(h, l, cl, hr, q + 2, -1, E, stop, tgt, z) if h[q + 1] < stop else stop
                            add('M6', cell, dt, -1, E, stop, (E - xp) / (stop - E))
                            # re-arm after the exit bar
                            ex = q + 2
                            while ex <= w[-1] and not (h[ex] >= stop or l[ex] <= tgt): ex += 1
                            q = ex + 1; ext = None; continue
                    else:
                        if ext is None or l[q] < ext: ext, sw_lo = l[q], h[q]
                        sw_lo = max(sw_lo, h[q]); sw = sw_lo - ext; lim = sw_lo - 0.62 * sw
                        if sw >= 15 * pip and q + 1 <= w[-1] and l[q + 1] <= lim and l[q + 1] > ext:
                            E = lim; stop = ext - 5 * pip
                            xp = walk(h, l, cl, hr, q + 2, 1, E, stop, tgt, z) if l[q + 1] > stop else stop
                            add('M6', cell, dt, 1, E, stop, (xp - E) / (E - stop))
                            ex = q + 2
                            while ex <= w[-1] and not (l[ex] <= stop or h[ex] >= tgt): ex += 1
                            q = ex + 1; ext = None; continue
                    q += 1
    # ---------- M7 daily OB + 1H turtle soup (multi-day) ----------
    hh = d.set_index('ts')[['o', 'h', 'l', 'c']].resample('1h').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    hT = hh.index; hH, hL, hC = hh.h.values, hh.l.values, hh.c.values
    hdate = np.array([t.date() for t in hT]); hpos = {}
    for i, dd_ in enumerate(hdate): hpos.setdefault(dd_, i)
    obs = []
    Dk = D.reset_index()
    for k in range(1, len(Dk) - 3):
        r = Dk.iloc[k]
        for side in (1, -1):
            if side == 1 and r.c < r.o and (Dk.c.iloc[k + 1:k + 4] > r.h).any(): obs.append((k, 1, r.o, r.h))
            if side == -1 and r.c > r.o and (Dk.c.iloc[k + 1:k + 4] < r.l).any(): obs.append((k, -1, r.o, r.l))
    for k, side, zo, zx in obs:
        conf = Dk.iloc[k + 1:k + 4]; ck = k + 1 + int(np.argmax((conf.c > zx).values if side == 1 else (conf.c < zx).values))
        lo_zone, hi_zone = (zo, zx) if side == 1 else (zx, zo)
        start = Dk.date.iloc[ck + 1] if ck + 1 < len(Dk) else None
        if start is None or start not in hpos: continue
        i = hpos[start]; end = min(len(hH), i + 20 * 24)
        # first return into the zone
        ret = None
        for q in range(i, end):
            if hL[q] <= hi_zone and hH[q] >= lo_zone: ret = q; break
        if ret is None: continue
        trig = None
        for q in range(max(ret, 10), min(ret + 48, len(hH))):
            inz = hL[q] <= hi_zone and hH[q] >= lo_zone
            if not inz: continue
            if side == 1 and hL[q] < hL[q - 10:q].min() and hC[q] > hL[q - 10:q].min(): trig = q; break
            if side == -1 and hH[q] > hH[q - 10:q].max() and hC[q] < hH[q - 10:q].max(): trig = q; break
        if trig is None: continue
        E = zo + side * 5 * pip; stop = E - side * 20 * pip; R = 20 * pip
        fill = None
        for q in range(trig + 1, min(trig + 25, len(hH))):
            if (hL[q] <= E) if side == 1 else (hH[q] >= E): fill = q; break
        if fill is None: continue
        half_done = False; be = False; realized = 0.0; xp = None; cur_stop = stop; nights = 0
        for q in range(fill, min(fill + 240, len(hH))):
            if q > fill and hT[q].hour == 17: nights += 1
            if (hL[q] <= cur_stop) if side == 1 else (hH[q] >= cur_stop):
                realized += (0.5 if half_done else 1.0) * side * (cur_stop - E) / R; xp = 1; break
            if not half_done and ((hH[q] >= E + side * R) if side == 1 else (hL[q] <= E + side * R)): realized += 0.5; half_done = True
            if not be and ((hH[q] >= E + 2 * R) if side == 1 else (hL[q] <= E - 2 * R)): cur_stop = E; be = True
            if (hH[q] >= E + 5 * R) if side == 1 else (hL[q] <= E - 5 * R):
                realized += (0.5 if half_done else 1.0) * 5; xp = 1; break
        if xp is None: realized += (0.5 if half_done else 1.0) * side * (hC[min(fill + 239, len(hC) - 1)] - E) / R
        add('M7', 'half1R_BE2R_5R', hdate[fill], side, E, stop, realized, nights)
    return rows

allr = []
for m in PAIRS: allr += run(m)
T = pd.DataFrame(allr)
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
P('# X12b results — ICT forex batch 2, Broker costs (0.4bp/side + 0.6 pip + 5bp/night), FXCM 5m')
for era in (['learn', 'exam'] if os.environ.get('EXAM') == '1' else ['learn']):
    P(f'\n## {era.upper()} ({"2012-2019" if era == "learn" else "2020-2026"})')
    for (mo, cell), g in T[T.era == era].groupby(['model', 'cell']):
        g = g.sort_values('date'); per = g.groupby('m').net.mean(); yr = g.groupby(pd.to_datetime(g.date).dt.year).net.sum()
        P(f'{mo}/{cell}: n={len(g):5d} win {100*(g.net>0).mean():.0f}% gross {g.gross.mean():+.3f}R NET {g.net.mean():+.3f}R maxDD {dd(g.net.values):.0f}R '
          f'stop {g.risk_pips.median():.0f}p | ' + ' '.join(f'{k[:3]}{v:+.2f}' for k, v in per.items()) + ' | yrs ' + ' '.join(f'{str(y)[2:]}:{v:+.0f}' for y, v in yr.items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X12b-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
