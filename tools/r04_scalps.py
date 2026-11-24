"""R04: 20-pips-per-day scalps A (Asia) and B (NY) (specs/ict-rebuild/R04-20pip-scalps.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def run(m, d):
    pip = L.pip_of(m); R = 20 * pip
    h, l, c, o, hr = d.h.values, d.l.values, d.c.values, d.o.values, d.hr.values
    ts = pd.to_datetime(d.ts.values); dates = d.date.values; n = len(d)
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    D = L.daily_fx(d); adr = (D.h - D.l).rolling(5).mean().shift(1)     # prior 5 FX days
    rows = []; rng = np.random.default_rng(4)
    starts = np.r_[0, np.where(dates[1:] != dates[:-1])[0] + 1]; ends = np.r_[starts[1:], n]
    day_ix = {dates[s]: (s, e) for s, e in zip(starts, ends)}
    def resolve(q, side, E, tlimit_idx):
        x = L.walk(h[:tlimit_idx], l[:tlimit_idx], c[:tlimit_idx], hr[:tlimit_idx], q, side, E, E - side * R, E + side * R)
        return side * (x[0] - E) / R, x[1]
    def book(cell, q, side, E, lim):
        for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
            g, xq = resolve(q, s, E, lim)
            cr = L.cost_R('broker_fx', m, E, R, nights=L.rollovers(ts[q], ts[min(xq + 1, n - 1)]))
            rows.append(dict(model='R04', cell=f'{cell}/{kind}', m=m, date=dates[q], gross=g, net=g - cr))
    keys = sorted(day_ix)
    for di, dt in enumerate(keys):
        s, e = day_ix[dt]; H = hr[s:e]
        # ---------- Scalp A: levels 13:00-20:00, entries 20:00-24:00, exit by 05:00 next day ----------
        lv = np.arange(s, e)[(H >= 13) & (H < 20)]
        if len(lv) > 20 and di + 1 < len(keys):
            s2, e2 = day_ix[keys[di + 1]]
            lim = s2 + int(np.argmax(hr[s2:e2] >= 5)) if (hr[s2:e2] >= 5).any() else e2
            win = np.arange(s, e)[H >= 20]
            for cell in ('MR', 'EX'):
                cand_h = [k for k in lv if sh[k] and k + 1 < lv[-1] + 1]; cand_l = [k for k in lv if sl[k] and k + 1 < lv[-1] + 1]
                if not cand_h or not cand_l: continue
                kh = cand_h[-1] if cell == 'MR' else max(cand_h, key=lambda k: h[k]); kl = cand_l[-1] if cell == 'MR' else min(cand_l, key=lambda k: l[k])
                sE = h[kh] + 5 * pip; bE = l[kl] - 5 * pip
                for q in win:
                    hs, hb = h[q] >= sE, l[q] <= bE
                    if hs and hb: break
                    if hs or hb:
                        side = -1 if hs else 1; book(f'A-{cell}', q, side, sE if hs else bE, lim); break
        # ---------- Scalp B: 07:00-10:00, London made the day's low/high, ADR pending ----------
        if dt not in adr.index or not adr[dt] == adr[dt]: continue
        A = adr[dt]; idx = np.arange(s, e)
        lon = idx[(H >= 1) & (H < 5)]; pre = idx[H < 7]
        if len(lon) < 30 or len(pre) < 60: continue
        lim = s + int(np.argmax(H >= 15)) if (H >= 15).any() else e
        for X in (1, 5):
            for q in idx[(H >= 7) & (H < 10)]:
                lo_sofar = l[s:q].min(); hi_sofar = h[s:q].max()
                if hi_sofar - lo_sofar >= A: break                          # ADR already made -> no more setups today
                london_low = l[lon].min() <= lo_sofar + 1e-12; london_high = h[lon].max() >= hi_sofar - 1e-12
                nyk = np.arange(s, q)[(H[:q - s] >= 7)]
                swl = [k for k in nyk if sl[k] and k + 1 < q]; swh = [k for k in nyk if sh[k] and k + 1 < q]
                fired = False
                if london_low and swl:
                    E = l[swl[-1]] - X * pip
                    if l[q] <= E and E > lo_sofar: book(f'B-X{X}', q, 1, E, lim); fired = True
                if not fired and london_high and swh:
                    E = h[swh[-1]] + X * pip
                    if h[q] >= E and E < hi_sofar: book(f'B-X{X}', q, -1, E, lim); fired = True
                if fired: break
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)].reset_index(drop=True)
        T.append(run(m, d)); print(m, flush=True)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R04-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R04 20-pip scalps (ICT side vs OPPosite vs COIN)', L.FX_LEARN_END, out, exam=EXAM)
    lines = ['\n## ex-top-5']
    for cell, g in T.groupby('cell'):
        g = g.sort_values('net'); lines.append(f'{cell}: n={len(g)} net {g.net.mean():+.3f}R ex-top5 {g.net.iloc[:-5].mean():+.3f}R')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
