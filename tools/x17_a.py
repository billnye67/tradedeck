"""X17a: rerun of X12 M8/M62 and X12b M3/M5/M6 with ICT bias definitions S and I12 (tools/bias_ict.py) replacing B1/B2.
Everything else copied verbatim from tools/x12_fx.py and tools/x12b_fx.py. Pre-registered in specs/X17-bias-rerun.md.
LEARN era (2012-2019) only: trade days after 2019 are skipped; bars up to 2020-01-10 are loaded only so a late-2019 trade
(e.g. M3 Tue 2019-12-31 -> Thu 2020-01-02) resolves exactly as in the original script."""
import os, sys, re, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bias_ict import add_bias
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)
PAIRS = ('EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD')
LEARN_END = 2019
LOAD_END_MS = int(pd.Timestamp('2020-01-10', tz='UTC').value // 10**6)

def load(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='fxcm' and market='{m}' and interval='5m' and timestamp < {LOAD_END_MS} order by t", c)
    ts = pd.to_datetime(d.t, unit='ms', utc=True).dt.tz_convert('America/New_York')
    d['ts'] = ts.values; d['date'] = ts.dt.date.values; d['hr'] = (ts.dt.hour + ts.dt.minute / 60).values; d['dow'] = ts.dt.dayofweek.values
    return d.drop(columns='t').reset_index(drop=True)

def walk(h, l, cl, hr, i0, side, E, stop, tgt, flat):
    for q in range(i0, len(h)):
        if hr[q] >= flat: return cl[q - 1] if q > i0 else E
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop
        if (h[q] >= tgt) if side == 1 else (l[q] <= tgt): return tgt
    return cl[-1]

BIAS = ('S', 'I12')

def run(m):
    pip = 0.01 if m.endswith('JPY') else 0.0001
    d = load(m); H, L, C, O, HR, DOW = d.h.values, d.l.values, d.c.values, d.o.values, d.hr.values, d.dow.values
    days = {k: v for k, v in d.groupby('date')}
    D = d.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    D = D[D.n >= 200]
    D['sma20'] = D.c.rolling(20).mean(); D['adr5'] = (D.h - D.l).rolling(5).mean()
    D['lo20'] = D.l.rolling(20).min(); D['hi20'] = D.h.rolling(20).max()
    D = add_bias(D)       # S, I12 on row k use daily rows through k-1 only
    idx = list(D.index); pos = {dt: k for k, dt in enumerate(idx)}
    rows = []
    def add(model, cell, dt, side, E, stop, gross_R, nights=0):
        risk = abs(E - stop)
        if risk <= 0: return
        cost = (0.8e-4 * E + 0.6 * pip + nights * 5e-4 * E) / risk
        rows.append(dict(m=m, model=model, cell=cell, date=dt, gross=gross_R, net=gross_R - cost, risk_pips=risk / pip))
    def addx(model, cell, dt, side, E, stop, xp):   # x12_fx.py style: exit price in
        risk = abs(E - stop)
        if risk <= 0: return
        add(model, cell, dt, side, E, stop, side * (xp - E) / risk)
    first_bar = {dt: g.index[0] for dt, g in days.items()}
    for dt in idx:
        if dt.year > LEARN_END: continue
        k = pos[dt]
        if k < 25: continue
        prev = D.iloc[k - 1]; cur = D.iloc[k]; x = days[dt]; h, l, cl, hr, o = x.h.values, x.l.values, x.c.values, x.hr.values, x.o.values
        dow = x.dow.values[0]
        if dow > 4: continue
        BC = tuple((nm, cur[nm]) for nm in BIAS)
        # ---------------- M8 midnight-open fade (x12_fx.py) ----------------
        z = np.where(hr >= 0)[0]
        if len(z):
            mo = o[z[0]]
            for cell, b in BC:
                if b == 0 or b != b: continue
                side = -1 if b < 0 else 1
                for q in range(len(h)):
                    if hr[q] >= 11: break
                    inkz = (2 <= hr[q] < 5) or (8.5 <= hr[q] < 11)
                    if not inkz: continue
                    if (side == -1 and h[q] >= mo + pip) or (side == 1 and l[q] <= mo - pip):
                        E = mo + pip if side == -1 else mo - pip; stop = E - side * 40 * pip; tgt = E + side * 80 * pip
                        if (side == -1 and h[q] >= stop) or (side == 1 and l[q] <= stop): xp = stop
                        else: xp = walk(h, l, cl, hr, q + 1, side, E, stop, tgt, 11)
                        addx('M8', cell, dt, side, E, stop, xp); break
        # ---------------- M62 London Judas swing (x12_fx.py) ----------------
        px = days.get(idx[k - 1])
        if px is not None:
            asia = px[px.hr >= 20]
            if len(asia) >= 30 and len(z):
                ah, al = asia.h.max(), asia.l.min(); mo = o[z[0]]
                for cell, b in BC:
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
                                addx('M62', cell, dt, 1, E, stop, xp); break
                        else:
                            if stage == 0 and l[q] < al: stage = 1
                            if stage == 1 and h[q] > mo and h[q] > ah: stage = 2
                            lim = ah + 10 * pip
                            if stage == 2 and h[q] >= lim:
                                E = lim; stop = E + 30 * pip; tgt = ah - prev.adr5
                                xp = stop if h[q] >= stop else walk(h, l, cl, hr, q + 1, -1, E, stop, tgt, 11)
                                addx('M62', cell, dt, -1, E, stop, xp); break
        # ---------- M3 Charter Model 2: Tuesday 00:00 -> Thursday 00:00 (x12b_fx.py) ----------
        if dow == 1:
            i0 = first_bar[dt]; E = O[i0]
            for cell, b in BC:
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
        # ---------- M5 NY-open counter-retrace OTE (x12b_fx.py) ----------
        s2 = np.where(hr >= 2)[0]; s7 = np.where(hr >= 7)[0]
        if len(s2) and len(s7):
            lon_up = o[s7[0]] > o[s2[0]]
            for cell, b in BC:
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
        # ---------- M6 OTE scalping drill (x12b_fx.py) ----------
        for cell, b in BC:
            if b == 0 or b != b: continue
            side = int(b)
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
    return rows

allr = []
for m in PAIRS: allr += run(m)
T = pd.DataFrame(allr)
assert pd.to_datetime(T.date).dt.year.max() <= LEARN_END
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0

# original B1/B2 learn rows, parsed from the original result files
orig = {}
for f in ('X12-results.md', 'X12b-results.md'):
    sec = None
    for ln in open(os.path.join(r'C:\Users\you\Desktop\TradeDeck\specs', f), encoding='utf-8'):
        if ln.startswith('## '): sec = ln[3:8]
        mt = re.match(r'(M\d+)/(\S+): n=\s*(\d+) .*gross (\S+)R NET (\S+)R maxDD (\d+)R .*\| (.*) \| yrs', ln)
        if mt and sec == 'LEARN':
            pp = sum(float(v[3:]) > 0 for v in mt.group(7).split())
            orig[(mt.group(1), mt.group(2))] = (int(mt.group(3)), mt.group(4), mt.group(5), mt.group(6), pp)
NAMES = {'M8': 'midnight-open fade', 'M62': 'London Judas swing', 'M3': 'Charter Model 2', 'M5': 'NY-open counter-retrace OTE', 'M6': 'OTE scalping drill'}
P('# X17a results: ICT bias rerun (S, I12) of X12 M8/M62 and X12b M3/M5/M6. FXCM 5m, Broker FX costs, LEARN 2012-2019 only')
P('Pass bar: NET >= +0.05R, maxDD <= 30R, >= 3/4 pairs positive, >= 80 trades (else THIN). Originals: specs/X12-results.md, specs/X12b-results.md.')
for mo in ('M8', 'M62', 'M3', 'M5', 'M6'):
    P(f'\n## {mo} {NAMES[mo]}')
    G = T[T.model == mo]
    for cell in sorted(G.cell.unique()):
        g = G[G.cell == cell].sort_values('date'); per = g.groupby('m').net.mean(); npos = int((per > 0).sum())
        yr = g.groupby(pd.to_datetime(g.date).dt.year).net.sum(); mdd = dd(g.net.values); net = g.net.mean()
        ok = net >= 0.05 and mdd <= 30 and npos >= 3
        verdict = ('PASS' if ok else 'FAIL') + (' (THIN)' if len(g) < 80 else '')
        P(f'{mo}/{cell}: n={len(g):5d} win {100*(g.net>0).mean():.0f}% gross {g.gross.mean():+.3f}R NET {net:+.3f}R maxDD {mdd:.0f}R '
          f'pairs+ {npos}/4 stop {g.risk_pips.median():.0f}p -> {verdict} | ' + ' '.join(f'{k[:3]}{v:+.2f}' for k, v in per.items())
          + ' | yrs ' + ' '.join(f'{str(y)[2:]}:{v:+.0f}' for y, v in yr.items()))
    P('original (X12/X12b, learn):')
    for (om, oc), (n, gr, ne, md, pp) in sorted(orig.items()):
        if om == mo: P(f'  {om}/{oc}: n={n} gross {gr}R NET {ne}R maxDD {md}R pairs+ {pp}/4')
open(r'C:\Users\you\Desktop\TradeDeck\specs\X17a-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
