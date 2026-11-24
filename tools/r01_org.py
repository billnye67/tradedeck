"""R01: ORG + first presented FVG to mid-gap (specs/ict-rebuild/R01-org-fpfvg.md). EXAMPLE=1 prints his Sep 17 2024 trade.
LEARN ONLY unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
TICK = 0.25; EXAM = os.environ.get('EXAM') == '1'

def day_trades(x, S, ratio, cellA, stop_c1):
    """x: one NY date of 1m bars. Returns trade dict or None."""
    mn = np.round(x.hr.values * 60).astype(int); o, h, l, c = x.o.values, x.h.values, x.l.values, x.c.values
    io = np.where(mn == 570)[0]
    if not len(io): return None
    i0 = io[0]; O = o[i0]; gap = O - S; ag = abs(gap) * ratio            # in NQ handles
    if not (40 <= ag <= 200): return None
    CE = S + gap / 2; side = -1 if gap > 0 else 1
    fv = None
    for k in range(i0 + 2, len(h)):
        if mn[k] >= 600: break                                           # C3 must close before 10:00 (bar start <= 9:59)
        if mn[k - 1] < 571: continue                                     # C2 at 09:31 or later
        d = -1 if l[k - 2] > h[k] else (1 if h[k - 2] < l[k] else 0)
        if d == 0: continue
        if d == side: fv = k; break
        if cellA: return None                                            # first FVG points away from CE -> no trade (cell A)
    if fv is None: return None
    E = h[fv] - TICK if side == -1 else l[fv] + TICK
    if stop_c1: stop = h[fv - 2] + TICK if side == -1 else l[fv - 2] - TICK
    else: stop = h[fv - 1] + TICK if side == -1 else l[fv - 1] - TICK
    if (stop - E) * side >= 0: return None
    if abs(CE - E) * ratio < 20: return None                             # >= 20 NQ handles of room
    q = None
    for j in range(fv + 1, len(h)):
        if mn[j] >= 615: break                                           # 10:15 cut-off for the fill
        if (l[j] <= CE) if side == -1 else (h[j] >= CE): break           # target traded before fill -> cancel
        if (h[j] >= E) if side == -1 else (l[j] <= E): q = j; break
    if q is None: return None
    end = np.where(mn >= 960)[0]; end = end[0] if len(end) else len(h)
    # limit fill on bar q: the fill price was touched on q; stop/target touches on q are ambiguous -> stop-first (conservative)
    xp, xq, how = L.walk(h[:end], l[:end], c[:end], x.hr.values[:end], q, side, E, stop, CE)
    return dict(side=side, E=E, stop=stop, tgt=CE, x=xp, how=how, fill=x.ts.iloc[q], fv_c2=x.ts.iloc[fv - 1], O=O, S=S, gap=gap)

def run(m, ratio_of):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates()
    d = d[d.date.astype(str) <= ('2026-12-31' if EXAM else '2023-12-31')]
    g = dict(tuple(d.groupby('date'))); dates = sorted(g); rows = []; S = None; Sdate = None
    rng = np.random.default_rng(1)
    for dt in dates:
        x = g[dt]; mn = np.round(x.hr.values * 60).astype(int)
        if S is not None and dt not in rolls and x.dow.iloc[0] < 5 and (pd.Timestamp(dt) - pd.Timestamp(Sdate)).days <= 4:
            r = ratio_of(dt, x)
            for cell, cA, s1 in (('A', True, False), ('B', False, False), ('A_c1stop', True, True)):
                t = day_trades(x, S, r, cA, s1)
                if t is None: continue
                risk = abs(t['E'] - t['stop']); g_ = t['side'] * (t['x'] - t['E']) / risk
                # coin control: same fill, random side, same stop/target distances (target = CE distance)
                cs = rng.choice([1, -1]); i = int(np.where(x.ts.values == np.datetime64(t['fill']))[0][0])
                st = t['E'] - cs * risk; tg = t['E'] + cs * abs(t['tgt'] - t['E']); end = np.where(mn >= 960)[0]; end = end[0] if len(end) else len(x)
                xp, _, _ = L.walk(x.h.values[:end], x.l.values[:end], x.c.values[:end], x.hr.values[:end], i, cs, t['E'], st, tg)
                cg = cs * (xp - t['E']) / risk
                for venue in ('cme', 'broker'):
                    cr = L.cost_R(venue, m, t['E'], risk, slips=0 if t['how'] == 'tgt' else 1)
                    rows.append(dict(model='R01', cell=f'{cell}/{venue}', m=m, date=dt, gross=g_, net=g_ - cr, ctrl=cg - cr, how=t['how'], risk=risk))
        sb = np.where(mn == 974)[0]
        if len(sb): S = x.c.values[sb[0]]; Sdate = dt
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_cme_ict('NQ'); x = d[d.date == pd.Timestamp('2024-09-17').date()]; p = d[d.date == pd.Timestamp('2024-09-16').date()]
        S = p.c.values[np.round(p.hr.values * 60).astype(int) == 974][0]
        for cA in (True, False): print('cellA' if cA else 'cellB', day_trades(x, S, 1.0, cA, False))
        sys.exit()
    nq = L.load_cme_ict('NQ'); es = L.load_cme_ict('ES')
    nqc = nq.groupby('date').c.last(); esc = es.groupby('date').c.last(); ratio = (nqc / esc).to_dict()
    del nq, es
    T = pd.concat([run('NQ', lambda dt, x: 1.0), run('ES', lambda dt, x: ratio.get(dt, np.nan))])
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R01-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R01 ORG + FPFVG to mid-gap', L.CME_LEARN_END, out, exam=EXAM)
    lines = ['\n## Control (coin side, same fill/stop/target distance) and ex-top-5']
    for (cell, m), g in T.groupby(['cell', 'm']):
        g = g.sort_values('net'); ex5 = g.net.iloc[:-5].mean() if len(g) > 5 else np.nan
        lines.append(f'{m} {cell}: n={len(g)} net {g.net.mean():+.3f}R ctrl {g.ctrl.mean():+.3f}R ex-top5 {ex5:+.3f}R median risk {g.risk.median():.2f}pt')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n')
    T.to_csv(out.replace('.md', '.csv'), index=False)
