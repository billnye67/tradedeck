"""R06: Charter Model 1 NY OTE scalps (specs/ict-rebuild/R06-charter-model1.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def pwalk(h, l, c, q, end, side, E, stop, parts, lock):
    """Stop-first walker with partials; after part 0 fills, stop -> E + side*lock. Returns R-weighted P&L in price units."""
    done = [False] * len(parts); pnl = 0.0; rem = 1.0
    for j in range(q, end):
        if (l[j] <= stop) if side == 1 else (h[j] >= stop): return pnl + rem * side * (stop - E)
        for k, (p, w) in enumerate(parts):
            if not done[k] and ((h[j] >= p) if side == 1 else (l[j] <= p)):
                done[k] = True; pnl += w * side * (p - E); rem -= w
                if k == 0: stop = E + side * lock
        if rem <= 1e-9: return pnl
    return pnl + rem * side * (c[end - 1] - E)

def run(m, d):
    pip = L.pip_of(m); D = L.daily_fx(d); Di = list(D.index); dpos = {k: i for i, k in enumerate(Di)}
    H, Lo = D.h.values, D.l.values; rows = []; rng = np.random.default_rng(6)
    sh = np.zeros(len(D), bool); sl = np.zeros(len(D), bool)
    sh[1:-1] = (H[1:-1] > H[:-2]) & (H[1:-1] > H[2:]); sl[1:-1] = (Lo[1:-1] < Lo[:-2]) & (Lo[1:-1] < Lo[2:])
    for dt, x in d.groupby('date'):
        if dt not in dpos or pd.Timestamp(dt).dayofweek > 2: continue
        t = dpos[dt]
        if t < 22: continue
        w0 = t - 20                                   # prior 20 FX days = [w0, t-1]
        hi20, lo20 = H[w0:t].max(), Lo[w0:t].min(); eq = (hi20 + lo20) / 2
        buy_prog = any(sh[k] and H[k + 1:t].max() > H[k] for k in range(w0, t - 1))
        sell_prog = any(sl[k] and Lo[k + 1:t].min() < Lo[k] for k in range(w0, t - 1))
        if not (buy_prog or sell_prog): continue
        x = x[(x.hr >= 7) & (x.hr < 16)].reset_index(drop=True)
        h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
        end = len(h)
        for minleg in (15, 30):
            trade = None
            iL = iH = 0; aL = l[0]; aH = h[0]; iH_ = 0; jL = 0; jH = 0; bL = h[0]; bH = l[0]   # running anchors
            for j in range(1, end):
                if hr[j] >= 10: break
                # long: anchor low = lowest low since 07:00, anchor high = highest high after it
                if l[j - 1] < aL: aL = l[j - 1]; iL = j - 1; aH = l[j - 1]
                aH = max(aH, h[j - 1]) if j - 1 >= iL else aH
                if buy_prog and aH - aL >= minleg * pip:
                    E = aH - 0.62 * (aH - aL) + 5 * pip
                    if E <= eq and l[j] <= E and h[j - 1] > E: trade = (1, j, E, aL - 5 * pip, aH, aL); break
                # short mirror
                if h[j - 1] > bH: bH = h[j - 1]; jH = j - 1; bL = h[j - 1]
                bL = min(bL, l[j - 1]) if j - 1 >= jH else bL
                if sell_prog and bH - bL >= minleg * pip:
                    E = bL + 0.62 * (bH - bL) - 5 * pip
                    if E >= eq and h[j] >= E and l[j - 1] < E: trade = (-1, j, E, bH + 5 * pip, bL, bH); break
            if trade is None: continue
            side, q, E, stop, ahi, alo = trade; leg = abs(ahi - alo); risk = abs(E - stop)
            parts = [(ahi - side * pip, .25), (ahi + side * 0.27 * leg, .25), (ahi + side * 0.62 * leg, .25), (ahi + side * 1.0 * leg, .25)]
            for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                ps = parts if s == side else [(2 * E - p, w) for p, w in parts]
                st = E - s * risk
                g = pwalk(h, l, c, q, end, s, E, st, ps, 5 * pip) / risk
                cr = L.cost_R('broker_fx', m, E, risk)
                rows.append(dict(model='R06', cell=f'L{minleg}/{kind}', m=m, date=dt, gross=g, net=g - cr, risk_pips=risk / pip))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)].reset_index(drop=True)
        T.append(run(m, d)); print(m, flush=True)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R06-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R06 Charter Model 1 (ICT vs OPP vs COIN)', L.FX_LEARN_END, out, exam=EXAM)
    lines = ['\n## ex-top-5 / risk']
    for cell, g in T.groupby('cell'):
        g = g.sort_values('net'); lines.append(f'{cell}: n={len(g)} net {g.net.mean():+.3f}R ex-top5 {g.net.iloc[:-5].mean():+.3f}R median risk {g.risk_pips.median():.1f} pips')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
