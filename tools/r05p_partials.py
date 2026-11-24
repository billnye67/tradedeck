"""R05P: 2022 model with his partial management (specs/ict-rebuild/R05P-partials.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r05_2022 as R
EXAM = os.environ.get('EXAM') == '1'

def manage(h, l, c, q, end, s, E, risk, P, T, cell):
    """two halves. Half 1 exits at P (the fulcrum short-term swing). Half 2 to T. Stop-first.
    P1: after P is hit, half 2's stop -> E. P2: as P1, and once price covers half the way to P, both stops -> E."""
    st1 = st2 = E - s * risk; out1 = out2 = None; half = E + s * abs(P - E) / 2
    for k in range(q, end):
        if out1 is None and ((l[k] <= st1) if s == 1 else (h[k] >= st1)): out1 = st1
        if out2 is None and ((l[k] <= st2) if s == 1 else (h[k] >= st2)): out2 = st2
        if out1 is not None and out2 is not None: break
        if out1 is None and ((h[k] >= P) if s == 1 else (l[k] <= P)): out1 = P; st2 = E
        if out2 is None and ((h[k] >= T) if s == 1 else (l[k] <= T)): out2 = T
        if out1 is not None and out2 is not None: break
        if cell == 'P2' and ((h[k] >= half) if s == 1 else (l[k] <= half)):
            st1 = E if out1 is None else st1; st2 = E if out2 is None else st2
    if out1 is None: out1 = c[end - 1]
    if out2 is None: out2 = c[end - 1]
    return 0.5 * s * (out1 - E) / risk + 0.5 * s * (out2 - E) / risk

def run(m, d, fixed_t):
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.CME_LEARN_END)]
    rolls = L.ict_roll_dates(); rows = []; rng = np.random.default_rng(55)
    for dt, x in d.groupby('date'):
        if dt in rolls: continue
        x = x.reset_index(drop=True)
        if x.dow.iloc[0] > 4: continue
        mo = x[x.hr >= 0].o.values[0]; h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
        end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else len(h)
        for sc in ('S1', 'SW'):
            for prem, ms in ((True, 'C'), (False, 'C'), (False, 'W')):
                t = R.day_setups(x, 8.5, 11, 0.25, fixed_t, mo, sc, 'TL', prem, ms)
                if t is None or t['q'] >= end: continue
                E, side, risk = t['E'], t['side'], abs(t['E'] - t['stop'])
                if risk <= 0 or (E - t['st']) * side >= 0: continue          # fulcrum must sit beyond the entry
                for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                    P = E + s * abs(t['st'] - E); T = E + s * abs(t['tgt'] - E)
                    if abs(P - E) >= abs(T - E): P = T
                    for cell in ('P0', 'P1', 'P2'):
                        if cell == 'P0':
                            xp, _, _ = L.walk(h[:end], l[:end], c[:end], hr[:end], t['q'], s, E, E - s * risk, T); gR = s * (xp - E) / risk
                        else: gR = manage(h, l, c, t['q'], end, s, E, risk, P, T, cell)
                        for v in ('cme', 'broker'):
                            cr = L.cost_R(v, m, E, risk, slips=1)
                            rows.append(dict(m=m, cell=f"{sc}/{'P' if prem else 'any'}{ms}/{cell}/{v}/{kind}", date=dt, gross=gR, net=gR - cr, risk=risk, pR=abs(P - E) / risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = pd.concat([run('NQ', L.load_cme_ict('NQ'), 20), run('ES', L.load_cme_ict('ES'), 5)]); T['yr'] = pd.to_datetime(T.date).dt.year
    if EXAM: T = T[T.yr >= 2024]
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R05P-results' + ('-exam' if EXAM else '-learn') + '.md'); lines = ['# R05P partials']
    for (m, cell), g in T.groupby(['m', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} medP {g.pR.median():.2f}R | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
