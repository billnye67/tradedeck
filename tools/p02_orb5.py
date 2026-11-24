"""P02: Zarattini & Aziz (2023) 5-minute ORB on NQ/ES futures. Pre-registered in specs/papers/P02-orb5.md. LEARN unless EXAM=1.
Rule: first 5-min candle 09:30-09:35 up -> long at 09:35 open, down -> short; flat candle (close == open) -> no trade.
Stop = other side of the first candle; target = 10R; else exit at the 16:00 close. Walk on 1-min bars, stop-first."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def run(m):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates(); rows = []; rng = np.random.default_rng(9)
    for k, x in d.groupby('date'):
        if k in rolls or pd.Timestamp(k).dayofweek > 4: continue
        y = int(pd.Timestamp(k).year)
        if (EXAM and y < 2024) or (not EXAM and y > 2023): continue
        x = x[(x.hr >= 9.5) & (x.hr < 16)]
        f = x[x.hr < 9.5 + 5 / 60]; r = x[x.hr >= 9.5 + 5 / 60]
        if len(f) < 4 or len(r) < 300: continue
        o, c, hi, lo = f.o.iloc[0], f.c.iloc[-1], f.h.max(), f.l.min()
        if c == o: continue
        side = 1 if c > o else -1; E = r.o.iloc[0]; stop = lo if side == 1 else hi; risk = abs(E - stop)
        if risk <= 0 or (E - stop) * side <= 0: continue
        h, l, cl, hr = r.h.values, r.l.values, r.c.values, r.hr.values
        for kind, s in (('ORB', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
            xp, _, how = L.walk(h, l, cl, hr, 0, s, E, E - s * risk, E + s * 10 * risk)
            g = s * (xp - E) / risk
            for v in ('cme', 'broker'):
                rows.append(dict(m=m, cell=f'{v}/{kind}', date=k, yr=y, gross=g, net=g - L.cost_R(v, m, E, risk, slips=1 if how == 'tgt' else 2), risk=risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = pd.concat([run('NQ'), run('ES')]); lines = ['# P02 Zarattini 5-min ORB' + (' EXAM' if EXAM else ' learn')]
    for (m, cell), g in T.groupby(['m', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} win {(g.gross>0).mean():.0%} medrisk {g.risk.median():.2f} | " + ' '.join(f'{yy}:{v:+.0f}' for yy, v in g.groupby('yr').net.sum().items()))
    p = os.path.join(os.path.dirname(__file__), '..', 'specs', 'papers', 'P02-results' + ('-exam' if EXAM else '-learn') + '.md')
    txt = chr(10).join(lines); open(p, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(p.replace('.md', '.csv'), index=False); print(txt)
