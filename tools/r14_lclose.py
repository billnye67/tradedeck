"""R14: London-close failure-swing scalp (specs/ict-rebuild/R14-london-close.md). LEARN unless EXAM=1."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def day_trade(x, adr, pip, k, tr):
    h, l, c, o, hr = x.h.values, x.l.values, x.c.values, x.o.values, x.hr.values; n = len(h)
    i1030 = int(np.argmax(hr >= 10.5)) if (hr >= 10.5).any() else None
    if i1030 is None or i1030 < 100: return None
    lon = x[(x.hr >= 1) & (x.hr < 5)]; ny = x[(x.hr >= 7) & (x.hr < 10)]
    if len(lon) < 30 or len(ny) < 30: return None
    dl = np.sign(lon.c.values[-1] - lon.o.values[0]); dn = np.sign(ny.c.values[-1] - ny.o.values[0]); dd = np.sign(c[i1030 - 1] - o[0])
    if not (dl == dn == dd) or dd == 0: return None
    side = -int(dd)                                                       # fade the day's move
    if h[:i1030].max() - l[:i1030].min() < k * adr: return None
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else n
    for q in range(i1030, end):
        if hr[q] >= 13: return None
        ext_i = int(np.argmax(h[:q])) if side == -1 else int(np.argmin(l[:q]))
        ext = h[ext_i] if side == -1 else l[ext_i]
        # lower swing high (up day) after the extreme, confirmed before q
        lh = [j for j in range(ext_i + 2, q - 1) if (sh[j] if side == -1 else sl[j])]
        if not lh: continue
        j = lh[-1]; mid = range(ext_i + 1, j)
        if not len(mid): continue
        lvl = min(l[m] for m in mid) if side == -1 else max(h[m] for m in mid)
        E = lvl - pip if side == -1 else lvl + pip
        if (l[q] <= E) if side == -1 else (h[q] >= E):
            stop = ext + 10 * pip if side == -1 else ext - 10 * pip
            lo, hi = l[:q].min(), h[:q].max(); rng = hi - lo
            tgt = hi - tr * rng if side == -1 else lo + tr * rng
            if (tgt - E) * side <= 0: return None
            return dict(side=side, E=E, stop=stop, tgt=tgt, q=q, end=end)
    return None

def run(m, d, venue, pip, learn_end):
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else learn_end)]
    D = L.daily_fx(d); adr5 = (D.h - D.l).rolling(5).mean(); idx = list(D.index)
    rows = []; rng = np.random.default_rng(14)
    for dt, x in d.groupby('date'):
        if pd.Timestamp(dt).dayofweek > 4: continue
        prev = [kk for kk in idx if kk < dt]
        if len(prev) < 6: continue
        a = adr5[prev[-1]]
        if not a == a: continue
        x = x.reset_index(drop=True)
        for k in (1.0, 1.25):
            for tr in (0.2, 0.3):
                t = day_trade(x, a, pip, k, tr)
                if t is None: continue
                h, l, c, o, hr = x.h.values, x.l.values, x.c.values, x.o.values, x.hr.values; risk = abs(t['E'] - t['stop'])
                for kind, s in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
                    st = t['E'] - s * risk; tg = t['E'] + s * abs(t['tgt'] - t['E'])
                    xp, _, how = L.walk_stop_entry(o[:t['end']], h[:t['end']], l[:t['end']], c[:t['end']], hr[:t['end']], t['q'], s, t['E'], st, tg)
                    gR = s * (xp - t['E']) / risk
                    cr = L.cost_R(venue, m, t['E'], risk) if venue == 'broker_fx' else L.cost_R(venue, m, t['E'], risk, slips=1)
                    rows.append(dict(model='R14', cell=f'k{k}/T{int(tr*100)}/{kind}', m=m, date=dt, gross=gR, net=gR - cr, Rp=risk / pip))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        T.append(run(m, L.load_fx(m, '5m'), 'broker_fx', L.pip_of(m), L.FX_LEARN_END)); print(m, flush=True)
    c = sqlite3.connect(L.DB)
    g = L._ny(pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c))
    T.append(run('XAUUSD', g, 'broker_cmdty', 0.1, 2023))
    T = pd.concat(T); T['grp'] = np.where(T.m == 'XAUUSD', 'GOLD', 'FX')
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R14-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R14 London-close scalp']
    for (grp, cell), gg in T.groupby(['grp', 'cell']):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{grp} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(gg.net.values):.0f} medR {gg.Rp.median():.1f}")
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
