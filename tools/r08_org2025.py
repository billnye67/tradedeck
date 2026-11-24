"""R01: ORG + first presented FVG to mid-gap (specs/ict-rebuild/R01-org-fpfvg.md). EXAMPLE=1 prints his Sep 17 2024 trade.
LEARN ONLY unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
TICK = 0.25; EXAM = os.environ.get('EXAM') == '1'

def day_trades(x, S, ratio, cellA, stop_c1, band=(40, 200), room=20, pm=False):
    """x: one NY date of 1m bars. Returns trade dict or None."""
    mn = np.round(x.hr.values * 60).astype(int); o, h, l, c = x.o.values, x.h.values, x.l.values, x.c.values
    io = np.where(mn == 570)[0]
    if not len(io): return None
    i0 = io[0]; O = o[i0]; gap = O - S; ag = abs(gap) * ratio            # in NQ handles
    if not (band[0] <= ag <= band[1]): return None
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
    if abs(CE - E) * ratio < room: return None                           # room filter (2024: >= 20 NQ handles)
    if pm:                                                               # 2025: premium gap runs the pre-market high first
        pre = (mn >= 360) & (mn < 570)
        if not pre.any(): return None
        lvl = h[pre].max() if side == -1 else l[pre].min()
        seg = slice(i0, fv - 1)
        if not ((h[seg] > lvl).any() if side == -1 else (l[seg] < lvl).any()): return None
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
            for cell, cA, rm, pm in (('A/R0', True, 0, False), ('B/R0', False, 0, False), ('A/R20', True, 20, False), ('A/R0/PM', True, 0, True), ('B/R0/PM', False, 0, True)):
                t = day_trades(x, S, r, cA, False, (20, 75), rm, pm)
                if t is None: continue
                risk = abs(t['E'] - t['stop']); g_ = t['side'] * (t['x'] - t['E']) / risk
                # coin control: same fill, random side, same stop/target distances (target = CE distance)
                cs = rng.choice([1, -1]); i = int(np.where(x.ts.values == np.datetime64(t['fill']))[0][0])
                st = t['E'] - cs * risk; tg = t['E'] + cs * abs(t['tgt'] - t['E']); end = np.where(mn >= 960)[0]; end = end[0] if len(end) else len(x)
                xp, _, _ = L.walk(x.h.values[:end], x.l.values[:end], x.c.values[:end], x.hr.values[:end], i, cs, t['E'], st, tg)
                cg = cs * (xp - t['E']) / risk
                st2 = t['E'] + t['side'] * risk; tg2 = t['E'] - t['side'] * abs(t['tgt'] - t['E'])
                xo, _, how_o = L.walk(x.h.values[:end], x.l.values[:end], x.c.values[:end], x.hr.values[:end], i, -t['side'], t['E'], st2, tg2)
                og = -t['side'] * (xo - t['E']) / risk
                for venue in ('cme', 'broker'):
                    cr = L.cost_R(venue, m, t['E'], risk, slips=0 if t['how'] == 'tgt' else 1)
                    rows.append(dict(model='R08', cell=f'{cell}/{venue}', m=m, date=dt, gross=g_, net=g_ - cr, ctrl=cg - cr, opp=og - cr, how=t['how'], risk=risk))
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
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R08-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R08 ORG 2025 bands', L.CME_LEARN_END, out, exam=EXAM)
    lines = ['\n## Control (coin side, same fill/stop/target distance) and ex-top-5']
    for (cell, m), g in T.groupby(['cell', 'm']):
        g = g.sort_values('net'); ex5 = g.net.iloc[:-5].mean() if len(g) > 5 else np.nan
        lines.append(f'{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f}R ctrl {g.ctrl.mean():+.3f}R OPP {g.opp.mean():+.3f}R ex-top5 {ex5:+.3f}R maxDD {L.maxdd(g.sort_values("date").net.values):.0f}R median risk {g.risk.median():.2f}pt')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n')
    T.to_csv(out.replace('.md', '.csv'), index=False)
