"""R02: London normal protraction + CBDR SD (specs/ict-rebuild/R02-london-cbdr.md). EXAMPLE=1 prints his Mar 28 / Mar 30 2017
GBPUSD days. LEARN ONLY unless EXAM=1."""
import os, sys, sqlite3, numpy as np, pandas as pd, datetime as dtm
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def prep(d):
    d = d.copy(); d['H'] = d.hr.astype(int)
    D = L.daily_fx(d)                                   # 17:00 NY candles, index = fx date (closing date)
    D['rng'] = D.h - D.l; D['adr5'] = D.rng.rolling(5).mean().shift(1)
    D['up'] = (D.c > D.o).astype(int); D['dn'] = (D.c < D.o).astype(int)
    D['up3'] = D.up.rolling(3).sum() == 3; D['dn3'] = D.dn.rolling(3).sum() == 3
    for n in (20, 40, 60): D[f'ob{n}'] = L.daily_ob_bias(D, n)
    return d, D

def day_trade(day, prevday, D, Dkey, pip, bias_cell, t_cell):
    """day: 5m bars of NY date (00:00-23:55); prevday: previous NY trading date bars. Dkey: index into D for 'yesterday'."""
    cb = prevday[(prevday.hr >= 14) & (prevday.hr < 20)]
    if len(cb) < 50: return None
    hb = cb.groupby('H').agg(o=('o', 'first'), c=('c', 'last'))
    top = max(hb.o.max(), hb.c.max()); bot = min(hb.o.min(), hb.c.min()); sd = top - bot
    if sd <= 0 or sd / pip >= 40: return None
    y = D.loc[Dkey]
    if not (y.adr5 == y.adr5) or y.rng > 2 * y.adr5: return None
    allow = {1: not y.up3, -1: not y.dn3}
    if bias_cell != 'NB':
        b = y[f'ob{bias_cell[2:]}']
        allow[1] &= b == 1; allow[-1] &= b == -1
        if b == 0: return None
    x = day[day.hr < 14]; h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
    sE, bE = top + sd, bot - sd
    q = None; side = None
    for j in range(len(h)):
        if hr[j] >= 5: break
        hs = allow[-1] and h[j] >= sE; hb_ = allow[1] and l[j] <= bE
        if hs or hb_:
            if hs and hb_: return None                      # both in one bar: ambiguous, skip
            side = -1 if hs else 1; q = j; break
    if q is None: return None
    E = sE if side == -1 else bE; stop = E - side * 30 * pip
    lon = x[x.hr < 5]
    ext = lon.h.max() if side == -1 else lon.l.min()
    N = max(1, int(round(((ext - top) if side == -1 else (bot - ext)) / sd)))
    blk = (1 + N) * sd; tgt = bot - 2 * blk if side == -1 else top + 2 * blk
    # no target before 05:00 (N is only known once the London extreme is in): stop-only walk to 05:00, then stop + target
    i5 = int(np.argmax(hr >= 5)) if (hr >= 5).any() else len(h)
    def two_stage(target):
        if q < i5:
            a = L.walk(h[:i5], l[:i5], c[:i5], hr[:i5], q, side, E, stop, None)
            if a[2] == 'stop': return a
            if i5 >= len(h): return a
            return L.walk(h, l, c, hr, i5, side, E, stop, target)
        return L.walk(h, l, c, hr, q, side, E, stop, target)
    R = 30 * pip
    if t_cell == 'T':
        xp, xq, how = two_stage(tgt)
        return dict(side=side, E=E, stop=stop, tgt=tgt, x=xp, how=how, g=side * (xp - E) / R, q=q, N=N, sd=sd)
    p1 = E + side * 25 * pip; a = L.walk(h, l, c, hr, q, side, E, stop, p1)       # +25 pips is fixed in advance: no lookahead
    if a[2] != 'tgt': return dict(side=side, E=E, stop=stop, tgt=tgt, x=a[0], how=a[2], g=side * (a[0] - E) / R, q=q, N=N, sd=sd)
    b = two_stage(tgt)
    g = 0.5 * side * (p1 - E) / R + 0.5 * side * (b[0] - E) / R
    return dict(side=side, E=E, stop=stop, tgt=tgt, x=b[0], how='half+' + b[2], g=g, q=q, N=N, sd=sd)

def run(m, d):
    pip = L.pip_of(m); d, D = prep(d); rng = np.random.default_rng(2); rows = []
    days = dict(tuple(d.groupby('date'))); keys = sorted(days); Didx = list(D.index)
    for i in range(1, len(keys)):
        dt = keys[i]; pv = keys[i - 1]
        if pd.Timestamp(dt).dayofweek > 2: continue          # Mon-Wed
        if (pd.Timestamp(dt) - pd.Timestamp(pv)).days > 4: continue
        yk = [k for k in Didx[max(0, Didx.index(dt) - 3) if dt in D.index else 0:] if k < dt] if dt in D.index else None
        if not yk: continue
        Dkey = yk[-1]
        for bc in ('OB20', 'OB40', 'OB60', 'NB'):
            for tc in ('T', 'T25'):
                t = day_trade(days[dt], days[pv], D, Dkey, pip, bc, tc)
                if t is None: continue
                x = days[dt][days[dt].hr < 14]; cs = rng.choice([1, -1]); risk = 30 * pip
                cx = L.walk(x.h.values, x.l.values, x.c.values, x.hr.values, t['q'], cs, t['E'], t['E'] - cs * risk, t['E'] + cs * abs(t['tgt'] - t['E']))
                cr = L.cost_R('broker_fx', m, t['E'], risk)
                rows.append(dict(model='R02', cell=f'{bc}/{tc}', m=m, date=dt, gross=t['g'], net=t['g'] - cr, ctrl=cs * (cx[0] - t['E']) / risk - cr, how=t['how']))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_fx('GBPUSD', '5m'); d, D = prep(d[(d.date >= dtm.date(2017, 1, 1)) & (d.date <= dtm.date(2017, 4, 30))])
        for a, b in ((dtm.date(2017, 3, 27), dtm.date(2017, 3, 28)), (dtm.date(2017, 3, 29), dtm.date(2017, 3, 30))):
            yk = max(k for k in D.index if k < b)
            print(b, 'ob40', D.loc[yk, 'ob40'], 'up3', D.loc[yk, 'up3'], 'dn3', D.loc[yk, 'dn3'], 'rng/adr', round(D.loc[yk, 'rng'] / D.loc[yk, 'adr5'], 2))
            for bc in ('NB', 'OB40'): print('  ', bc, day_trade(d[d.date == b], d[d.date == a], D, yk, 0.0001, bc, 'T'))
        sys.exit()
    T = []
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)]
        T.append(run(m, d)); print(m, 'done', flush=True)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R02-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R02 London normal protraction + CBDR', L.FX_LEARN_END, out, exam=EXAM)
    lines = ['\n## Control and ex-top-5 (all pairs)']
    for cell, g in T.groupby('cell'):
        g = g.sort_values('net'); lines.append(f'{cell}: n={len(g)} net {g.net.mean():+.3f}R ctrl {g.ctrl.mean():+.3f}R ex-top5 {g.net.iloc[:-5].mean():+.3f}R')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
