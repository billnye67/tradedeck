"""R12: 2025 '07:00-09:30 liquidity' rule (L33 Gauntlet lecture). EXAMPLE=1 prints NQ 2025-05-06. LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; TICK = 0.25

def eq_pairs(vals, idx, tol, kind):
    """relative-equal pairs among swing points (vals at idx, chronological). Returns list of (i_last, level_far, level_near)."""
    out = []
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            if abs(vals[a] - vals[b]) <= tol:
                far = min(vals[a], vals[b]) if kind == 'low' else max(vals[a], vals[b]); near = max(vals[a], vals[b]) if kind == 'low' else min(vals[a], vals[b])
                out.append((idx[b], far, near))
    return out

def day_trade(x, tol, RR=1.0):
    w = x[(x.hr >= 7) & (x.hr < 16)].reset_index(drop=True)
    h, l, c, o, hr = w.h.values, w.l.values, w.c.values, w.o.values, w.hr.values; n = len(h)
    if n < 300: return None
    pre = np.where(hr < 9.5)[0]; i930 = int(np.argmax(hr >= 9.5))
    sh = [k for k in pre[1:] if h[k] >= h[k - 1] and (k == i930 - 1 or h[k] >= h[k + 1])]         # local highs, flat tops count; 09:29 may count
    sl = [k for k in pre[1:] if l[k] <= l[k - 1] and (k == i930 - 1 or l[k] <= l[k + 1])]
    REL = eq_pairs([l[k] for k in sl], sl, tol, 'low'); REH = eq_pairs([h[k] for k in sh], sh, tol, 'high')
    if not REL and not REH: return None
    end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else n
    for j in range(i930, end):
        if hr[j] >= 10.5: return None
        for side, pools, opp in ((1, REL, REH), (-1, REH, REL)):
            sw = [(ib, far, near) for (ib, far, near) in pools if ((l[j] < far) if side == 1 else (h[j] > far))]
            if not sw: continue
            ib, far, near = max(sw)                                             # the most recent pool swept
            # entry: "as soon as it trades back up into" the pool -> buy stop at the near level (L33 [640s])
            for r in range(j, min(end, j + 30)):
                if r == j and not (((o[j] > near) and (c[j] >= near)) if side == 1 else ((o[j] < near) and (c[j] <= near))): continue   # FIXED: same bar only if it opened beyond AND closed back (the return is proven)
                if (h[r] >= near) if side == 1 else (l[r] <= near):
                    E = near; ext = (l[j:r].min() if side == 1 else h[j:r].max()) if r > j else (l[j] if side == 1 else h[j])   # FIXED: no lows after the fill
                    stop = ext - TICK if side == 1 else ext + TICK; risk = abs(E - stop)
                    # target: first opposite relative-equal pool to the left with enough reward ("not enough reward for the risk")
                    tg = sorted([(ib2, f2) for (ib2, f2, n2) in opp if ((f2 - E) * side >= RR * risk)], reverse=True)
                    if not tg: return None
                    return dict(side=side, E=E, stop=stop, tgt=tg[0][1], q=r, end=end, w=w, sweep=w.ts.iloc[j], ret=w.ts.iloc[r])
            return None
    return None

def run(m, scale):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates()
    d = d[d.date.astype(str) <= ('2026-12-31' if EXAM else '2023-12-31')]
    rows = []; rng = np.random.default_rng(12)
    for dt, x in d.groupby('date'):
        if dt in rolls or x.dow.iloc[0] > 4: continue
        for tol in (2, 4, 8):
          for RR in (1.0, 2.0):
            t = day_trade(x, tol * scale, RR)
            if t is None or t['q'] >= t['end']: continue
            w = t['w']; h, l, c, o, hr = w.h.values, w.l.values, w.c.values, w.o.values, w.hr.values; risk = abs(t['E'] - t['stop'])
            if risk <= 0: continue
            for kind, s in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
                st = t['E'] - s * risk; tg = t['E'] + s * abs(t['tgt'] - t['E'])
                xp, _, how = L.walk_stop_entry(o[:t['end']], h[:t['end']], l[:t['end']], c[:t['end']], hr[:t['end']], t['q'], s, t['E'], st, tg)
                gR = s * (xp - t['E']) / risk
                for v in ('cme', 'broker'):
                    cr = L.cost_R(v, m, t['E'], risk, slips=1 if how == 'tgt' else 2)
                    for rmin in (0, 4):
                        if risk * (1 / scale) < rmin: continue
                        rows.append(dict(model='R12', cell=f'tol{tol}/RR{RR:.0f}/minrisk{rmin}/{v}/{kind}', m=m, date=dt, gross=gR, net=gR - cr, risk=risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_cme_ict('NQ'); x = d[d.date == pd.Timestamp('2025-05-06').date()]
        for tol in (2, 4, 8):
            for RR in (1.0, 2.0): t = day_trade(x, tol, RR); print(tol, RR, {k: v for k, v in (t or {}).items() if k != 'w'})
        sys.exit()
    T = pd.concat([run('NQ', 1.0), run('ES', 0.25)]); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R12-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R12 07:00-09:30 relative-equal liquidity rule']
    for (m, cell), gg in T.groupby(['m', 'cell']):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{m} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(gg) > 5 else np.nan:+.3f} maxDD {L.maxdd(gg.net.values):.0f} medrisk {gg.risk.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in gg.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
