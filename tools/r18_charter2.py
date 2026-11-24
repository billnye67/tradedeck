"""R18: Charter Model 2 Tuesday weekly-range trade (specs/ict-rebuild/R18-charter2-tuesday.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def legs(h, l, c, q, end, side, E, pip):
    """two half positions, stop 50 pips, targets +50/+100, exit at bar end-1 close. Stop-first. Returns [(exit_px, exit_idx, how)]*2."""
    st = E - side * 50 * pip; out = []
    for tp in (50, 100):
        tg = E + side * tp * pip; r = (c[end - 1], end - 1, 'time')
        for k in range(q, end):
            if (l[k] <= st) if side == 1 else (h[k] >= st): r = (st, k, 'stop'); break
            if (h[k] >= tg) if side == 1 else (l[k] <= tg): r = (tg, k, 'tgt'); break
        out.append(r)
    return out

def run(m):
    d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)].reset_index(drop=True)
    pip = L.pip_of(m); h, l, c, o, hr = d.h.values, d.l.values, d.c.values, d.o.values, d.hr.values
    tsu = pd.to_datetime(d.ts).values; dates = pd.to_datetime(d.date)
    D = L.daily_fx(d); Didx = pd.to_datetime(D.index)
    wk = dates.dt.to_period('W').values; rows = []; rng = np.random.default_rng(18)
    for w in pd.unique(wk):
        iw = np.where(wk == w)[0]
        dw = d.dow.values[iw]
        tue = iw[dw == 1]; wed = iw[dw == 2]; thu = iw[(dw == 3) & (hr[iw] >= 7)]
        if not len(tue) or not len(thu): continue
        end = thu[0] + 1
        t0 = tue[0] if hr[tue[0]] < 0.1 else None
        if t0 is None: continue
        prior = D[Didx < pd.Timestamp(d.date.values[t0])]
        if len(prior) < 25: continue
        r20 = prior.iloc[-20:]; mid = (r20.h.max() + r20.l.min()) / 2
        pw = prior[pd.to_datetime(prior.index).to_period('W') == (pd.Period(d.date.values[t0], 'W') - 1)]
        if not len(pw): continue
        if len(prior) < 50: continue
        bias = {'B20': 1 if o[t0] < mid else -1, 'BW': 1 if pw.c.iloc[-1] > pw.o.iloc[0] else -1,
                'T4': 1 if o[t0] > prior.c.iloc[-20] else -1, 'T10': 1 if o[t0] > prior.c.iloc[-50:].mean() else -1}
        for bn, side in bias.items():
            ents = {}
            ents['O'] = (t0, o[t0])
            for day in (tue, wed):
                if not len(day): continue
                asia = [k for k in range(day[0] - 60, day[0]) if 0 <= k and hr[k] >= 20]
                if len(asia) < 30: continue
                ah, al = h[asia].max(), l[asia].min(); lvl = ah + pip if side == 1 else al - pip
                live = [k for k in day if 2 <= hr[k] < 17]
                f = next((k for k in live if ((h[k] >= lvl) if side == 1 else (l[k] <= lvl))), None)
                if f is not None: ents['A'] = (f, max(lvl, o[f]) if side == 1 else min(lvl, o[f])); break
            for en, (q, E) in ents.items():
                for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                    if en == 'A' and kind != 'ICT': E2 = E; q2 = q          # placebo trades the same fill bar/price
                    else: E2, q2 = E, q
                    lg = legs(h, l, c, q2 + (1 if en == 'A' else 0), end, s, E2, pip) if q2 + 1 < end else None
                    if lg is None: continue
                    gR = sum(0.5 * s * (xp - E2) / (50 * pip) for xp, _, _ in lg)
                    cr = sum(0.5 * L.cost_R('broker_fx', m, E2, 50 * pip, nights=L.rollovers(tsu[q2], tsu[xq])) for _, xq, _ in lg)
                    rows.append(dict(m=m, cell=f'{bn}/{en}/{kind}', date=d.date.values[t0], gross=gR, net=gR - cr))
    return rows

if __name__ == '__main__':
    T = pd.DataFrame(sum((run(m) for m in L.FX_PAIRS), [])); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R18-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R18 Charter Model 2' + (' EXAM' if EXAM else ' learn')]
    for cell, g in T.groupby('cell'):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
