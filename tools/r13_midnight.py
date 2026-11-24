"""R13: midnight-open OB buys/sells (specs/ict-rebuild/R13-midnight-open-ob.md). LEARN unless EXAM=1."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'
KZ = ((2, 5), (7, 10))

def resample(x, rule):
    y = x.set_index(pd.to_datetime(x.ts)).resample(rule).agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'hr': 'first', 'date': 'first'}).dropna()
    return y.reset_index(drop=True)

def day_trade(prev, day, side, pip, rule):
    x = pd.concat([prev[prev.hr >= 14], day[day.hr < 17]])
    b = resample(x, rule); o, h, l, c, hr = b.o.values, b.h.values, b.l.values, b.c.values, b.hr.values
    today = (b.date.values == day.date.iloc[0])
    if not today.any(): return None
    i0 = int(np.argmax(today)); mo = day.o.values[0]                      # 00:00 NY open
    obs = []                                                               # (validated_at, level, far)
    for k in range(len(h) - 1):
        down = c[k] < o[k] if side == 1 else c[k] > o[k]
        if not down: continue
        j = next((j for j in range(k + 1, len(h)) if ((h[j] > h[k]) if side == 1 else (l[j] < l[k]))), None)
        if j is not None: obs.append((j, k))
    end = len(h) - 1
    for q in range(i0, len(h)):
        if not any(a <= hr[q] < b_ for a, b_ in KZ): continue
        live = [(j, k) for (j, k) in obs if j < q]
        for j, k in sorted(live, key=lambda z: -z[0]):
            E = h[k] + 5 * pip if side == 1 else l[k] - 5 * pip
            if (E >= mo) if side == 1 else (E <= mo): continue            # must be below (above) the midnight open
            if (l[q] <= E) if side == 1 else (h[q] >= E):
                stop = l[k] - pip if side == 1 else h[k] + pip
                if (E - stop) * side <= 0: continue
                if any(((l[m] < stop) if side == 1 else (h[m] > stop)) for m in range(j, q)): continue   # OB already broken
                return dict(E=E, stop=stop, q=q, b=b, i0=i0)
    return None

def run(m, d, venue, pip, learn_end):
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else learn_end)]
    D = L.daily_fx(d); ob = L.daily_ob_bias(D, 40); keys = sorted(set(d.date)); by = dict(tuple(d.groupby('date')))
    rows = []; rng = np.random.default_rng(13)
    for i in range(1, len(keys)):
        dt = keys[i]
        if pd.Timestamp(dt).dayofweek > 4: continue
        prevk = [k for k in D.index if k < dt]
        b_ = ob.get(prevk[-1], 0) if prevk else 0
        for rule in ('15min', '60min'):
            for bc in ('OB40', 'NB'):
                sides = [b_] if bc == 'OB40' else [1, -1]
                for side in sides:
                    if side == 0: continue
                    t = day_trade(by[keys[i - 1]], by[dt], side, pip, rule)
                    if t is None: continue
                    b = t['b']; h, l, c = b.h.values, b.l.values, b.c.values; hr = b.hr.values
                    ee = [k for k in range(t['i0'], len(h)) if hr[k] >= 16.9]; end = ee[0] if ee else len(h)
                    if end <= t['q']: continue
                    risk = abs(t['E'] - t['stop'])
                    for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                        xp, _, how = L.walk(h[:end], l[:end], c[:end], hr[:end], t['q'], s, t['E'], t['E'] - s * risk, None)
                        gR = s * (xp - t['E']) / risk
                        cr = L.cost_R(venue, m, t['E'], risk) if venue == 'broker_fx' else L.cost_R(venue, m, t['E'], risk, slips=1)
                        rows.append(dict(model='R13', cell=f"T{rule.replace('min','')}/{bc}/{kind}", m=m, date=dt, gross=gR, net=gR - cr, Rp=risk / pip, side=s))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        p = L.pip_of(m); T.append(run(m, L.load_fx(m, '5m'), 'broker_fx', p, L.FX_LEARN_END)); print(m, flush=True)
    c = sqlite3.connect(L.DB)
    g = L._ny(pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c))
    T.append(run('XAUUSD', g, 'broker_cmdty', 0.1, 2023))
    T = pd.concat(T); T['grp'] = np.where(T.m == 'XAUUSD', 'GOLD', 'FX'); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R13-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R13 midnight-open OB']
    for (grp, cell), gg in T.groupby(['grp', 'cell']):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{grp} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(gg.net.values):.0f} medR {gg.Rp.median():.1f}")
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
