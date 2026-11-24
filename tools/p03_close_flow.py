"""P03-P05 (specs/papers/P03-P05.md). ES/NQ 1m ICT-rolled. LEARN unless EXAM=1. Results in bp per trade, CME + Broker costs.
P03 last half hour (Gao 2018 / Baltussen 2021): enter 15:30 open, exit 16:00 close, direction by
   r1  = 10:00 / prior 16:00 - 1 (done as P01), r12 = 15:30 / 15:00 - 1, rROD = 15:30 / prior 16:00 - 1, AGREE = sign(r1)==sign(r12).
P04 Shum 2016 leveraged-ETF rule: at 14:15 / 14:30 / 14:45, return since prior 16:00 close >= +2% -> long, <= -2% -> short, exit 16:00.
P05 overnight: LOU = long 16:00 close -> next 09:30 open (Lou/Polk/Skouras 2019); OD = long 02:00 -> 03:00, OD+ = long 01:30 -> 03:30
   (Boyarchenko/Larsen/Whelan 2023). Always long; control = same window on a random half of days is meaningless, so compare
   vs the average 1-hour/overnight drift of the whole sample (buy-and-hold share) and report by year."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'
def px(x, t0, t1, col):
    s = x[(x.hr >= t0) & (x.hr < t1)]; return s[col].iloc[0] if col == 'o' and len(s) else (s[col].iloc[-1] if len(s) else np.nan)
def cost(m, E, v): return (2 * 0.25 + 5 / (20 if m == 'NQ' else 50)) / E * 1e4 if v == 'cme' else 2 * 3 + 2 * 2
def run(m):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates(); g = {k: v for k, v in d.groupby('date')}
    keys = sorted(k for k in g if pd.Timestamp(k).dayofweek < 5); rows = []; rng = np.random.default_rng(11)
    for i in range(1, len(keys)):
        k, kp = keys[i], keys[i - 1]
        if k in rolls or kp in rolls: continue
        y = pd.Timestamp(k).year
        if (EXAM and y < 2024) or (not EXAM and y > 2023): continue
        x, xp = g[k], g[kp]
        c16p = px(xp, 15.98, 16.0, 'c'); c16 = px(x, 15.98, 16.0, 'c'); o1530 = px(x, 15.5, 15.52, 'o')
        p10 = px(x, 9.98, 10.0, 'c'); p15 = px(x, 14.98, 15.0, 'c'); p1530 = px(x, 15.48, 15.5, 'c')
        def add(model, side, E, X):
            if not (np.isfinite(E) and np.isfinite(X)) or side == 0: return
            for kind, s in (('SIG', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                bp = s * (X - E) / E * 1e4
                for v in ('cme', 'broker'): rows.append(dict(m=m, cell=f'{model}/{v}/{kind}', date=k, yr=y, bp=bp, net=bp - cost(m, E, v)))
        if all(np.isfinite([c16p, c16, o1530, p10, p15, p1530])):
            r1, r12, rrod = p10 / c16p - 1, p1530 / p15 - 1, p1530 / c16p - 1
            add('P03_r12', 1 if r12 > 0 else -1, o1530, c16); add('P03_rROD', 1 if rrod > 0 else -1, o1530, c16)
            if np.sign(r1) == np.sign(r12): add('P03_AGREE', 1 if r1 > 0 else -1, o1530, c16)
        for t in (14.25, 14.5, 14.75):
            pt = px(x, t - 0.02, t, 'c')
            if np.isfinite(pt) and np.isfinite(c16p) and np.isfinite(c16):
                r = pt / c16p - 1
                if abs(r) >= 0.02: add(f'P04_{int(t*100)}', 1 if r > 0 else -1, pt, c16)
        o930 = px(x, 9.5, 9.52, 'o')
        for model, E, X in (('P05_LOU', c16p, o930), ('P05_OD', px(x, 2.0, 2.02, 'o'), px(x, 2.98, 3.0, 'c')), ('P05_ODplus', px(x, 1.5, 1.52, 'o'), px(x, 3.48, 3.5, 'c'))):
            if np.isfinite(E) and np.isfinite(X):
                bp = (X - E) / E * 1e4
                for v in ('cme', 'broker'): rows.append(dict(m=m, cell=f'{model}/{v}/LONG', date=k, yr=y, bp=bp, net=bp - cost(m, E, v) - (5 if (v == 'broker' and model == 'P05_LOU') else 0)))
    return pd.DataFrame(rows)
if __name__ == '__main__':
    T = pd.concat([run('ES'), run('NQ')]); lines = ['# P03-P05' + (' EXAM' if EXAM else ' learn') + ' (bp per trade)']
    for (m, cell), g in T.groupby(['m', 'cell']):
        se = g.net.std() / np.sqrt(len(g)) if len(g) > 1 else np.nan
        lines.append(f"{m} {cell}: n={len(g)} gross {g.bp.mean():+.2f} net {g.net.mean():+.2f} (t={g.net.mean()/se:+.1f}) | " + ' '.join(f'{yy}:{v:+.1f}' for yy, v in g.groupby('yr').net.mean().items()))
    p = os.path.join(os.path.dirname(__file__), '..', 'specs', 'papers', 'P03-P05-results' + ('-exam' if EXAM else '-learn') + '.md')
    txt = chr(10).join(lines); open(p, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(p.replace('.md', '.csv'), index=False); print(txt)
