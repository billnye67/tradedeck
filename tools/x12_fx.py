"""X12: ICT forex models at Broker costs (specs/X12-ict-forex.md). EXAM=1 opens the 2020-2026 exam once."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)
PAIRS = ('EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD')

def load(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='fxcm' and market='{m}' and interval='5m' order by t", c)
    ts = pd.to_datetime(d.t, unit='ms', utc=True).dt.tz_convert('America/New_York')
    d['date'] = ts.dt.date.values; d['hr'] = (ts.dt.hour + ts.dt.minute / 60).values; d['dow'] = ts.dt.dayofweek.values
    return d.drop(columns='t')

def walk(h, l, cl, hr, i0, side, E, stop, tgt, flat):
    """from bar i0 (first bar after fill); stop first if both hit; returns exit price"""
    for q in range(i0, len(h)):
        if hr[q] >= flat: return cl[q - 1] if q > i0 else E
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop
        if (h[q] >= tgt) if side == 1 else (l[q] <= tgt): return tgt
    return cl[-1]

def run(m):
    pip = 0.01 if m.endswith('JPY') else 0.0001
    d = load(m); days = dict(tuple(d.groupby('date'))); dates = sorted(days)
    D = d.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    D = D[D.n >= 200]
    D['sma20'] = D.c.rolling(20).mean(); D['adr5'] = (D.h - D.l).rolling(5).mean()
    D['b1'] = np.sign(D.c - D.o); D['b2'] = np.sign(D.c - D.sma20)
    fr = (D.h > D.h.shift(1)) & (D.h > D.h.shift(-1)); D['swh'] = np.where(fr, D.h, np.nan)
    frl = (D.l < D.l.shift(1)) & (D.l < D.l.shift(-1)); D['swl'] = np.where(frl, D.l, np.nan)
    idx = list(D.index); pos = {dt: k for k, dt in enumerate(idx)}
    rows = []
    def add(model, cell, dt, side, E, stop, xp):
        risk = abs(E - stop)
        if risk <= 0: return
        gross = side * (xp - E) / risk
        cost = (0.8e-4 * E + 0.6 * pip) / risk
        rows.append(dict(m=m, model=model, cell=cell, era='learn' if dt.year <= 2019 else 'exam', date=dt, gross=gross, net=gross - cost, risk_pips=risk / pip))
    for dt in idx:
        k = pos[dt]
        if k < 25: continue
        prev = D.iloc[k - 1]; x = days[dt]; h, l, cl, hr, o = x.h.values, x.l.values, x.c.values, x.hr.values, x.o.values
        dow = x.dow.values[0]
        if dow > 4: continue
        # ---------------- M2 OTE after PDH/PDL run ----------------
        pre = (hr >= 7) & (hr < 8.5)
        if pre.any():
            for side in (1, -1):
                lvl = prev.h if side == 1 else prev.l
                anchor = l[pre].min() if side == 1 else h[pre].max()
                taken = False; ext = None; filled = None
                for q in range(len(h)):
                    if hr[q] >= 11: break
                    if not taken and ((h[q] > lvl) if side == 1 else (l[q] < lvl)): taken = True
                    if hr[q] < 8.5 and not taken: continue
                    if taken and hr[q] >= 7:
                        ext = max(ext or h[q], h[q]) if side == 1 else min(ext or l[q], l[q])
                        sw = abs(ext - anchor)
                        if sw < 15 * pip or hr[q] < 8.5: continue
                        lim = ext - 0.62 * sw if side == 1 else ext + 0.62 * sw
                        if q + 1 < len(h) and hr[q + 1] < 11 and ((l[q + 1] <= lim) if side == 1 else (h[q + 1] >= lim)):
                            filled = (q + 1, lim, ext, sw); break
                if filled:
                    q, E, ext, sw = filled; stop = anchor - side * 10 * pip
                    for cell, mult in (('T1', 0.5), ('T2', 1.0)):
                        tgt = ext + side * mult * sw
                        xp = walk(h, l, cl, hr, q, side, E, stop, tgt, 16)
                        add('M2', cell, dt, side, E, stop, xp)
                    break   # one trade per day
        # ---------------- M4 Charter Model 1 ----------------
        if dow <= 2:
            win = D.iloc[k - 20:k]; sh_old = D.swh.iloc[k - 20:k - 5].max(); sl_old = D.swl.iloc[k - 20:k - 5].min()
            c5 = D.c.iloc[k - 6]; mid20 = (win.h.max() + win.l.min()) / 2
            seven = np.where(hr >= 7)[0]
            if len(seven) and sh_old == sh_old and sl_old == sl_old:
                p7 = o[seven[0]]
                for side in (1, -1):
                    if side == 1: ok = prev.c > sh_old and c5 < sh_old and p7 < mid20; old = win.h.max()
                    else: ok = prev.c < sl_old and c5 > sl_old and p7 > mid20; old = win.l.min()
                    if not ok: continue
                    lon = (hr >= 2) & (hr < 7)
                    if not lon.any(): continue
                    anchor = l[lon].min() if side == 1 else h[lon].max(); ext = None; filled = None
                    for q in range(len(h)):
                        if hr[q] < 2: continue
                        if hr[q] >= 10: break
                        ext = max(ext or h[q], h[q]) if side == 1 else min(ext or l[q], l[q])
                        if hr[q] < 7: continue
                        sw = abs(ext - anchor)
                        if sw <= 0: continue
                        lim = (ext - 0.62 * sw + 5 * pip) if side == 1 else (ext + 0.62 * sw - 5 * pip)
                        if q + 1 < len(h) and hr[q + 1] < 10 and ((l[q + 1] <= lim) if side == 1 else (h[q + 1] >= lim)):
                            filled = (q + 1, lim, ext); break
                    if filled:
                        q, E, ext = filled; stop = anchor - side * 5 * pip
                        if (E - stop) * side <= 0: continue
                        add('M4', 'T1', dt, side, E, stop, walk(h, l, cl, hr, q, side, E, stop, ext, 16))
                        if (old - E) * side > 0: add('M4', 'T2', dt, side, E, stop, walk(h, l, cl, hr, q, side, E, stop, old, 16))
                    break
        # ---------------- M8 midnight-open fade ----------------
        z = np.where(hr >= 0)[0]
        if len(z):
            mo = o[z[0]]
            for cell, b in (('B1', prev.b1), ('B2', prev.b2)):
                if b == 0 or b != b: continue
                side = -1 if b < 0 else 1   # bearish bias -> short above the open
                for q in range(len(h)):
                    if hr[q] >= 11: break
                    inkz = (2 <= hr[q] < 5) or (8.5 <= hr[q] < 11)
                    if not inkz: continue
                    if (side == -1 and h[q] >= mo + pip) or (side == 1 and l[q] <= mo - pip):
                        E = mo + pip if side == -1 else mo - pip; stop = E - side * 40 * pip; tgt = E + side * 80 * pip
                        if (side == -1 and h[q] >= stop) or (side == 1 and l[q] <= stop): xp = stop
                        else: xp = walk(h, l, cl, hr, q + 1, side, E, stop, tgt, 11)
                        add('M8', cell, dt, side, E, stop, xp); break
        # ---------------- M62 London Judas swing ----------------
        px = days.get(idx[k - 1])
        if px is not None:
            asia = px[px.hr >= 20]
            if len(asia) >= 30 and len(z):
                ah, al = asia.h.max(), asia.l.min(); mo = o[z[0]]
                for cell, b in (('B1', prev.b1), ('B2', prev.b2)):
                    if b == 0 or b != b: continue
                    side = 1 if b > 0 else -1
                    stage = 0
                    for q in range(len(h)):
                        if hr[q] >= 5: break
                        if side == 1:
                            if stage == 0 and h[q] > ah: stage = 1
                            if stage == 1 and l[q] < mo and l[q] < al: stage = 2
                            lim = al - 10 * pip
                            if stage == 2 and l[q] <= lim:
                                E = lim; stop = E - 30 * pip; tgt = al + prev.adr5
                                xp = stop if l[q] <= stop else walk(h, l, cl, hr, q + 1, 1, E, stop, tgt, 11)
                                add('M62', cell, dt, 1, E, stop, xp); break
                        else:
                            if stage == 0 and l[q] < al: stage = 1
                            if stage == 1 and h[q] > mo and h[q] > ah: stage = 2
                            lim = ah + 10 * pip
                            if stage == 2 and h[q] >= lim:
                                E = lim; stop = E + 30 * pip; tgt = ah - prev.adr5
                                xp = stop if h[q] >= stop else walk(h, l, cl, hr, q + 1, -1, E, stop, tgt, 11)
                                add('M62', cell, dt, -1, E, stop, xp); break
    return rows

allr = []
for m in PAIRS: allr += run(m)
T = pd.DataFrame(allr)
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
P('# X12 results — ICT forex models, Broker costs (0.4bp/side + 0.6 pip round trip), FXCM 5m')
for era in (['learn', 'exam'] if os.environ.get('EXAM') == '1' else ['learn']):
    P(f'\n## {era.upper()} ({"2012-2019" if era == "learn" else "2020-2026"})')
    for (mo, cell), g in T[T.era == era].groupby(['model', 'cell']):
        g = g.sort_values('date'); per = g.groupby('m').net.mean(); yr = g.groupby(pd.to_datetime(g.date).dt.year).net.sum()
        P(f'{mo}/{cell}: n={len(g):5d} win {100*(g.net>0).mean():.0f}% gross {g.gross.mean():+.3f}R NET {g.net.mean():+.3f}R maxDD {dd(g.net.values):.0f}R '
          f'stop {g.risk_pips.median():.0f}p | ' + ' '.join(f'{k[:3]}{v:+.2f}' for k, v in per.items()) + ' | yrs ' + ' '.join(f'{str(y)[2:]}:{v:+.0f}' for y, v in yr.items()))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X12-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
