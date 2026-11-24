"""MO learn step: LEARN-era table of all 216 cells per market (midnight level only) + the frozen choice per market
(highest learn net R/month among cells with >= 100 learn trades). Writes specs/lab/mo_learn_grid.csv, mo_learn.log and
mo_frozen.json. Exam rows are never read here (filtered out before any statistic). LAB ONLY."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mo_build import MKTS, OUT, prog
WINDOWS = (9.5, 12.0, 16.0); SIDES = {'S': (-1,), 'L': (1,), 'B': (-1, 1)}

def cost_px(T, stress=False):
    """Round-trip cost in price units per trade (spec): fx 0.4bp/side + 0.6 pip; cmdty 6bp; idx/crypto 10bp."""
    m = T.m.iloc[0]; cm = MKTS[m][2]; tick = MKTS[m][1]
    if cm == 'fx': return 0.8e-4 * T.E + 0.6 * tick + (1.0 * tick if stress else 0.0)
    if cm == 'cmdty': return 6e-4 * T.E
    return 10e-4 * T.E

def cme_cost_px(T):
    tick, pv = 0.25, {'NQ': 20.0, 'ES': 50.0}[T.m.iloc[0]]; return 2 * tick + 5 / pv

def load(m):
    T = pd.read_parquet(os.path.join(OUT, f'mo_trades_{m}.parquet')); T['date'] = pd.to_datetime(T.date)
    T['era'] = np.where(T.date.dt.year <= T.learn_end, 'learn', 'exam')
    return T

def months(T, era):
    x = T[T.era == era].date; return max(1, x.dt.to_period('M').nunique())

def cells(T):
    for tf in ('5m', '10m', '15m'):
        for k in (1, 2, 3, 0):
            for w in WINDOWS:
                for sn, ss in SIDES.items():
                    for var in ('first', 'every'):
                        yield dict(tf=tf, tgt=k, win=w, sides=sn, var=var), ss

def pick(T, c, ss, lv=0):
    x = T[(T.lv == lv) & (T.tf == c['tf']) & (T.tgt == c['tgt']) & (T['var'] == c['var']) & (T.ev_hr < c['win']) & T.side.isin(ss)]
    return x

def stats(x, mon):
    t = x[x.skip == 0]
    if not len(t): return dict(n=0, skips=int(x.skip.sum()))
    cost = cost_px(t); net = t.gR - cost / t.risk
    return dict(n=len(t), skips=int(x.skip.sum()), gross=t.gR.mean(), net=net.mean(), R_mo=net.sum() / mon, tr_mo=len(t) / mon,
                winrate=(net > 0).mean(), stop_bp=float((t.risk / t.E).median() * 1e4), cost_pct_risk=float((cost / t.risk).median() * 100),
                x5=float(((t.tgt.iloc[0] * t.risk).median() if t.tgt.iloc[0] > 0 else t.move16.median()) / cost.median()))

if __name__ == '__main__':
    rows, frozen, log = [], {}, []
    for m in MKTS:
        p = os.path.join(OUT, f'mo_trades_{m}.parquet')
        if not os.path.exists(p): log.append(f'{m}: not built'); continue
        T = load(m); L = T[T.era == 'learn']; mon = months(T[T.lv == 0], 'learn')
        best = None
        for c, ss in cells(L):
            s = stats(pick(L, c, ss), mon); r = dict(m=m, **c, **s); rows.append(r)
            if s['n'] >= 100 and (best is None or s['R_mo'] > best['R_mo']): best = r
        frozen[m] = {k: best[k] for k in ('tf', 'tgt', 'win', 'sides', 'var')} if best else None
        log.append(f'{m}: learn months {mon}; frozen best cell {frozen[m]} -> learn n {best["n"]} net {best["net"]:+.3f}R '
                   f'R/mo {best["R_mo"]:+.2f} tr/mo {best["tr_mo"]:.1f} stop {best["stop_bp"]:.1f}bp cost {best["cost_pct_risk"]:.0f}% of risk 5x-ratio {best["x5"]:.1f}' if best else f'{m}: no cell with >=100 learn trades')
        G = pd.DataFrame([r for r in rows if r['m'] == m]); g = G[G.n >= 100]
        log.append(f'   cells with >=100 learn trades: {len(g)} of {len(G)}; net>0: {(g.net > 0).sum()}; pass 5x: {(g.x5 >= 5).sum()}; '
                   f'median net {g.net.median():+.3f}R; median gross {g.gross.median():+.3f}R; median stop {g.stop_bp.median():.1f}bp')
        prog(f'learn grid done for {m}')
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'mo_learn_grid.csv'), index=False)
    json.dump(frozen, open(os.path.join(OUT, 'mo_frozen.json'), 'w'), indent=1)
    txt = '\n'.join(log); print(txt); open(os.path.join(OUT, 'mo_learn.log'), 'w', encoding='utf-8').write(txt + '\n')
    prog('FROZEN choices written to specs/lab/mo_frozen.json (learn only) BEFORE the exam')
