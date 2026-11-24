"""R10: a YouTube trader's inversion-FVG model (specs/ict-rebuild/R10-ytc-inversion.md). EXAMPLE=1 prints NQ 2024-08-22. LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; TICK = 0.25

def day_trade(x, S, ratio, W, band):
    mn = np.round(x.hr.values * 60).astype(int); o, h, l, c = x.o.values, x.h.values, x.l.values, x.c.values
    io = np.where(mn == 570)[0]
    if not len(io): return None
    i0 = io[0]; O = o[i0]; gap = O - S; ag = abs(gap) * ratio
    if not (band[0] <= ag <= band[1]): return None
    side = -1 if gap > 0 else 1                                          # trade toward the fill
    fv = None
    for k in range(i0 + 2, len(h)):
        if mn[k] >= 600: break
        if mn[k - 1] < 571: continue
        if l[k] > h[k - 2] and (l[k] - h[k - 2]) * ratio >= W: fv = (k, 1, h[k - 2], l[k]); break      # bullish gap [bot, top]
        if h[k] < l[k - 2] and (l[k - 2] - h[k]) * ratio >= W: fv = (k, -1, h[k], l[k - 2]); break     # bearish gap [bot, top]
    if fv is None: return None
    k, gdir, bot, top = fv
    if gdir != -side: return None                                        # need a gap AGAINST the fill direction, to invert
    edge = bot if side == -1 else top; far = top if side == -1 else bot
    q3 = O + side * 0.75 * abs(gap); full = S
    lim = np.where(mn >= 720)[0]; lim = lim[0] if len(lim) else len(h)
    inv = next((j for j in range(k + 1, lim) if ((c[j] < edge) if side == -1 else (c[j] > edge))), None)
    if inv is None: return None
    E = edge - TICK if side == -1 else edge + TICK
    stop = far + TICK if side == -1 else far - TICK
    if (q3 - E) * side <= 0: return None
    q = None
    for j in range(inv + 1, lim):
        if (l[j] <= q3) if side == -1 else (h[j] >= q3): return None     # 3/4 target traded before the fill
        if (h[j] > far) if side == -1 else (l[j] < far): return None     # invalid inversion
        if (h[j] >= E) if side == -1 else (l[j] <= E): q = j; break
    if q is None: return None
    end = np.where(mn >= 960)[0]; end = end[0] if len(end) else len(h)
    return dict(side=side, E=E, stop=stop, q3=q3, full=full, q=q, end=end, inv=x.ts.iloc[inv], fvg=(bot, top), fill=x.ts.iloc[q])

def walk2(h, l, c, q, end, side, E, stop, p1, p2):
    got1 = False; pnl = 0.0
    for j in range(q, end):
        if (l[j] <= stop) if side == 1 else (h[j] >= stop): return pnl + (0.5 if got1 else 1.0) * side * (stop - E)
        if not got1 and ((h[j] >= p1) if side == 1 else (l[j] <= p1)): got1 = True; pnl += 0.5 * side * (p1 - E)
        if got1 and ((h[j] >= p2) if side == 1 else (l[j] <= p2)): return pnl + 0.5 * side * (p2 - E)
    return pnl + (0.5 if got1 else 1.0) * side * (c[end - 1] - E)

def run(m, ratio_of):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates()
    d = d[d.date.astype(str) <= ('2026-12-31' if EXAM else '2023-12-31')]
    g = dict(tuple(d.groupby('date'))); rows = []; S = None; Sd = None; rng = np.random.default_rng(10)
    for dt in sorted(g):
        x = g[dt]; mn = np.round(x.hr.values * 60).astype(int)
        if S is not None and dt not in rolls and x.dow.iloc[0] < 5 and (pd.Timestamp(dt) - pd.Timestamp(Sd)).days <= 4:
            r = ratio_of(dt)
            for bn, band in (('G0', (20, 1e9)), ('G40', (40, 200))):
                for W in (1, 2, 4):
                    t = day_trade(x, S, r, W, band)
                    if t is None: continue
                    h, l, c = x.h.values, x.l.values, x.c.values; risk = abs(t['E'] - t['stop'])
                    for kind, s in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
                        if s == t['side']: st, p1, p2 = t['stop'], t['q3'], t['full']
                        else: st, p1, p2 = 2 * t['E'] - t['stop'], 2 * t['E'] - t['q3'], 2 * t['E'] - t['full']
                        gR = walk2(h, l, c, t['q'], t['end'], s, t['E'], st, p1, p2) / risk
                        for v in ('cme', 'broker'):
                            cr = L.cost_R(v, m, t['E'], risk, slips=1)
                            rows.append(dict(model='R10', cell=f'{bn}/W{W}/{v}/{kind}', m=m, date=dt, gross=gR, net=gR - cr, risk=risk))
        sb = np.where(mn == 974)[0]
        if len(sb): S = x.c.values[sb[0]]; Sd = dt
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_cme_ict('NQ'); p = d[d.date == pd.Timestamp('2024-08-21').date()]; S = p[np.round(p.hr * 60) == 974].c.values[0]
        x = d[d.date == pd.Timestamp('2024-08-22').date()]
        for W in (1, 2, 4): print(W, day_trade(x, S, 1.0, W, (20, 1e9)))
        sys.exit()
    nq = L.load_cme_ict('NQ'); es = L.load_cme_ict('ES'); ratio = (nq.groupby('date').c.last() / es.groupby('date').c.last()).to_dict(); del nq, es
    T = pd.concat([run('NQ', lambda dt: 1.0), run('ES', lambda dt: ratio.get(dt, np.nan))])
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R10-results' + ('-exam' if EXAM else '-learn') + '.md')
    T['yr'] = pd.to_datetime(T.date).dt.year; lines = ['# R10 YT-C inversion FVG']
    for (m, cell), gg in T.groupby(['m', 'cell']):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{m} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(gg) > 5 else np.nan:+.3f} maxDD {L.maxdd(gg.net.values):.0f} medrisk {gg.risk.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in gg.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
