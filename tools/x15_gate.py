"""X15 gate (declared in specs/X15-ict-rest.md "X15 gate before any exam"). Reruns the 6 candidate cells with the gate
filters, model code imported/copied unchanged:  (1) skip trades whose stop distance < NQ 4 pts / ES 1 pt / crypto 0.15% of
entry; (2) NET with the 5 best trades removed; (3) the cell vs its coin-flip control (same min-stop filter).
Usage: python x15_gate.py learn M09 M11 ...   |  python x15_gate.py exam M09 ...  (exam only for cells passing the gate)
Exam era = 2024-01-01 to end of data; bars from 2023-09-01 are loaded only as indicator warm-up, no trade before 2024 kept."""
import os, sys, pickle, datetime as dt, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); import ictlib as L
TMP = os.path.join(os.environ.get('TEMP', '.'), 'x15gate'); os.makedirs(TMP, exist_ok=True)
MINSTOP = {'NQ': lambda E: 4.0, 'ES': lambda E: 1.0, 'BTCUSDT': lambda E: 0.0015 * abs(E), 'ETHUSDT': lambda E: 0.0015 * abs(E)}
if os.environ.get('GATE_NOFILTER'): MINSTOP = {k: (lambda E: 0.0) for k in MINSTOP}   # reproduction check only
EX0 = dt.date(2024, 1, 1); WARM = '2023-09-01'

def era_cut(d, era):
    dd = pd.to_datetime(pd.Series(d.date))
    keep = (dd < '2024-01-01') if era == 'learn' else (dd >= WARM)
    return d[keep.values].reset_index(drop=True)

def in_era(date, era): return (date < EX0) if era == 'learn' else (date >= EX0)

# ---------------------------------------------------------------- X15a: M09 (NQ cell A), M11 (NQ B1), M12 (ES B1)
def run_a(code, era):
    import x15_a as A
    m, tf, model, cell, ctl = {'M09': ('NQ', '1min', 'M09 PMfvg', 'A', 'COIN'), 'M11': ('NQ', '5min', 'M11 EqWindow', 'B1', 'COIN'),
                               'M12': ('ES', '5min', 'M12 AM0830', 'B1', 'COIN')}[code]
    d = era_cut(L.load_cme(m, tf), era)
    rolls = L.roll_dates(d if tf == '1min' else era_cut(L.load_cme(m, '1min'), era))
    I = A.daily_info(d); out = {cell: [], ctl: []}; skip_next = {}; tick = 0.25
    for date, g in d.groupby('date'):   # copy of x15_a.run_market restricted to one model + the min-stop skip
        if date in rolls or g.dow.iloc[0] > 4 or date not in I.index: continue
        st = I.loc[date]
        if not (np.isfinite(st.b1) and np.isfinite(st.b2) and np.isfinite(st.pdh)): continue
        x = {k: g[k].values for k in ('o', 'h', 'l', 'c', 'hr')}
        b1, b2, rs = int(st.b1), int(st.b2), A.coin(date, 0); cand = []
        if code == 'M09':
            for c in (cell, ctl): cand.append((c, A.pm_fvg(x, c, tick, st, b2, rs)))
        else:
            for c, b in ((cell, b1), (ctl, rs)):
                if b == 0: continue
                if code == 'M11':
                    for w0, w1 in ((9.5, 11.0), (13.0, 16.0)): cand.append((c, A.eq_window(x, b, tick, st, w0, w1)))
                else:
                    if skip_next.pop(c, False): pass
                    else: cand.append((c, A.am830(x, b, tick, st)))
        for c, tr in cand:
            if tr is None: continue
            q, side, E, stop, tgt, flat = tr; risk = abs(E - stop)
            if risk <= 0: continue
            if risk < MINSTOP[m](E): continue                                   # GATE (1)
            cst = L.cost_R('cme', m, E, risk)
            if cst > A.MAXCOST: continue
            if code in ('M11', 'M12'):
                if not np.isfinite(tgt) or (tgt - E) * side < A.MINROOM * risk: continue
            gross = A.finish(x, q, side, E, stop, tgt, flat)
            if code == 'M12' and gross >= (tgt - E) * side / risk - 1e-9: skip_next[c] = True
            if in_era(date, era): out[c].append(dict(date=date, m=m, gross=gross, net=gross - cst, risk=risk, E=E))
    return {'cell': out[cell], 'ctrl': out[ctl]}

# ---------------------------------------------------------------- X15b: M22 last-hour run, NQ cell A (cme)
def run_b(code, era):
    src = open(os.path.join(HERE, 'x15_b.py'), encoding='utf-8').read()
    src = src.split('# ---------------------------------------------------------------- run')[0]
    ns = {'__file__': os.path.join(HERE, 'x15_b.py')}; exec(src, ns)
    mk = [x for x in ns['MK']() if x['name'] == 'NQ'][0]
    base = mk['floor']; mk['floor'] = lambda E: max(base(E), MINSTOP['NQ'](E))   # GATE (1); NQ floor 8 pts already >= 4
    mk['venues'] = [('cme', 'NQ')]
    if era == 'exam': mk['start'], mk['end'] = WARM, '2100-01-01'
    A1 = ns['prep'](mk['d1'](), mk)
    if era == 'learn':   # replay the RNG draws x15_b made before b22A on NQ (b14A, b21A controls) -> identical control
        junk = []; ns['m14'](A1, mk, junk, 'A'); ns['m21'](A1, mk, junk, 'A')
    rows = []; ns['m22'](A1, mk, rows, 'A')
    out = {'cell': [], 'ctrl': []}
    for r in rows:
        if not in_era(r['date'], era): continue
        out['ctrl' if r['cell'].endswith('_RANDOM') else 'cell'].append(dict(date=r['date'], m='NQ', gross=r['gross'], net=r['net']))
    return out

# ---------------------------------------------------------------- X15g: M72 Ma Deuce, CRYPTO @ broker
def run_g(code, era):
    import x15_g as G
    out = {'cell': [], 'ctrl': []}
    for name in ('BTCUSDT', 'ETHUSDT'):
        P = dict(G.MK[name]); d = era_cut(P['loader'](), era); P['rolls'] = set()
        sp = {1: G.space(d, 1, set()), -1: G.space(d, -1, set())}
        lst = [(sg, cd) for sg in (1, -1) for cd in G.m72md(sp[sg], sp[sg], P)]
        ex = []
        for sg, cd in lst:
            r = G.execute(sp[sg], cd, P)
            if r is None or r['risk'] < MINSTOP[name](r['E']): continue      # GATE (1), applied before the one-at-a-time slot
            r.update(sg=sg, other=sp[-sg]); ex.append(r)
        acc = []; last = -1
        for r in sorted(ex, key=lambda z: (z['q'], z['sg'])):
            if r['q'] > last: acc.append(r); last = r['xi']
        rng = np.random.default_rng(15 + sum(map(ord, name)))   # coin control built exactly like x15_g's COIN rows
        for r in acc:
            cst = lambda g, nights: g - L.cost_R('broker', name, r['E'], r['risk'], nights)
            if in_era(r['date'], era): out['cell'].append(dict(date=r['date'], m=name, gross=r['gross'], net=cst(r['gross'], r['nights'])))
            if rng.random() < 0.5: g2, n2 = r['gross'], r['nights']
            else:
                O = r['other']; E2 = -r['Eraw']; d2 = r['tgt'] - r['Eraw']
                xp, xi = G.sim(O, r['q'], E2, E2 - r['risk'], E2 + d2, r['end'], r['limit'], r['cexit'])
                g2, n2 = (xp - E2) / r['risk'], int(O['sess'][xi] - O['sess'][r['q']])
            if in_era(r['date'], era): out['ctrl'].append(dict(date=r['date'], m=name, gross=g2, net=cst(g2, n2)))
    return out

# ---------------------------------------------------------------- X15f: M55 body contraction, NQ+ES cell B-B2 (cme)
def run_f(code, era):
    import x15_f as F
    F.RNG = np.random.default_rng(1515); F.ROWS.clear()
    for m in ('NQ', 'ES'):
        y0, y1 = (2020, 2023) if era == 'learn' else (2023, 2100)
        d5 = F.cut(L.load_cme(m, '5min'), y0, y1); d1 = F.cut(L.load_cme(m, '1min'), y0, y1)
        if era == 'exam': d5 = d5[pd.to_datetime(pd.Series(d5.date)).values >= np.datetime64(WARM)].reset_index(drop=True)
        M = F.mk(m, 'NQES', ['cme'], 0.25, d1, d5, L.roll_dates(d1)); D = M['D']
        for date, a, b in F.days(M['d5']):
            if date in M['rolls'] or date not in D.index or not in_era(date, era): continue
            B = F.Day.of(M['d5'], a, b); R = D.loc[date]
            adr = R['adr']   # copy of F.run55, cell B-B2 only, control on B-B2, min-stop skip
            if not R['contr'] or not np.isfinite(adr) or adr <= 0: continue
            s = R['b2']
            if not np.isfinite(s) or s == 0: continue
            s = int(s); E = B.o[0] - s * 0.1 * adr; stop = E - s * 0.5 * adr; tg = E + s * adr
            if abs(E - stop) < MINSTOP[m](E): continue                          # GATE (1); stop = 0.5 ADR20, never binds
            for q in range(B.n):
                if B.hr[q] >= 16: break
                if (B.l[q] <= E) if s == 1 else (B.h[q] >= E):
                    F.emit('55 Body contraction', 'B-B2', M, date, B, (q, s, E, stop, [(tg, 1.0)], 16.917), ctrl=True); break
    out = {'cell': [], 'ctrl': []}
    for r in F.ROWS:
        out['ctrl' if r['cell'].startswith('COIN') else 'cell'].append(dict(date=r['date'], m=r['m'], gross=r['gross'], net=r['net']))
    return out

CANDS = {'M09': ('M09 PM-FVG, NQ@cme cell A', run_a), 'M11': ('M11 last up-close short, NQ@cme B1', run_a),
         'M12': ('M12 08:30 AM FVG, ES@cme B1', run_a), 'M22': ('M22 last-hour run, NQ@cme A', run_b),
         'M72': ('M72 IFVG/Ma Deuce, CRYPTO@broker MD', run_g), 'M55': ('M55 body contraction, NQES@cme B-B2', run_f)}

def stats(rows):
    if not rows: return dict(n=0, net=np.nan, tot=0, ex5=np.nan, ex5tot=0, dd=0, win=np.nan, top5=0, med=np.nan)
    T = pd.DataFrame(rows).sort_values('date', kind='stable'); x = T.net.values; s = np.sort(x)
    ex = s[:-5] if len(s) > 5 else s[:0]
    return dict(n=len(x), net=x.mean(), tot=x.sum(), ex5=ex.mean() if len(ex) else np.nan, ex5tot=ex.sum(), dd=L.maxdd(x),
                win=100 * (x > 0).mean(), top5=s[-5:].sum(), med=float(np.median(x)),
                yrs=' '.join(f'{str(y)[2:]}:{v:+.0f}' for y, v in T.groupby(pd.to_datetime(T.date).dt.year).net.sum().items()))

if __name__ == '__main__':
    era = sys.argv[1]; assert era in ('learn', 'exam')
    for code in sys.argv[2:]:
        res = CANDS[code][1](code, era)
        tag = '_nofilter' if os.environ.get('GATE_NOFILTER') else ''
        pickle.dump(res, open(os.path.join(TMP, f'{code}_{era}{tag}.pkl'), 'wb'))
        a, c = stats(res['cell']), stats(res['ctrl'])
        print(f"{code} {era}{tag}: n={a['n']} NET {a['net']:+.3f} ex-top5 {a['ex5']:+.3f} maxDD {a['dd']:.0f} top5 {a['top5']:+.1f}R | "
              f"control n={c['n']} NET {c['net']:+.3f}", flush=True)
