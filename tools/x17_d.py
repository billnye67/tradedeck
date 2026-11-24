"""X17d: rerun of the B1/B2-biased models in x15_a, x15_b, x15_f, x15_g with ONLY the bias swapped to S and I12 (tools/bias_ict.py).
Pre-registered in specs/X17-bias-rerun.md section X17d. LEARN ERA ONLY (the original loaders/truncation are reused unchanged).
Usage: python x17_d.py a|b|f|g   (writes %TEMP%/x17d_<part>.pkl);  python x17_d.py report"""
import os, sys, re, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import ictlib as L, bias_ict as BI
TMP = os.environ.get('TEMP', '.'); OUT = os.path.join(HERE, '..', 'specs', 'X17d-results.md')
LE = {'NQ': 2023, 'ES': 2023, 'XAUUSD': 2022, 'BTCUSDT': 2023, 'ETHUSDT': 2023, **{p: 2019 for p in L.FX_PAIRS}}
COLS = ['src', 'model', 'bias', 'grp', 'm', 'date', 'gross', 'net']

def wk_bias(d):
    """S/I12 (shifted) from weekday NY-date daily bars of d; index = date."""
    return BI.bias(L.daily(d[d.dow <= 4]))

# ------------------------------------------------------------------ x15_a
def part_a():
    import x15_a as XA
    orig = XA.daily_info; cur = {}
    def di(d):
        I = orig(d); B = wk_bias(d).reindex(I.index)
        I['b1'] = B[cur['b1']].fillna(0).values; I['b2'] = B[cur['b2']].fillna(0).values; return I
    XA.daily_info = di; out = []
    def go(m, d, venue, tick, rolls, models, b1, b2, keep):
        cur.update(b1=b1, b2=b2); XA.MODELS = models
        for r in XA.run_market(m, d, venue, tick, rolls):
            lab = keep.get((r['model'], r['cell']))
            if lab: out.append(dict(r, bias=lab))
    MB = ('M01 Venom', 'M11 EqWindow', 'M12 AM0830', 'M13 NoDrawStop')
    K1 = {(mo, 'B1'): 'S' for mo in MB}; K1.update({(mo, 'B2'): 'I12' for mo in MB}); K1[('M09 PMfvg', 'B')] = 'I12'
    K2 = {('M09 PMfvg', 'B'): 'S'}
    def both(m, d, venue, tick, rolls, models):
        go(m, d, venue, tick, rolls, models, 'S', 'I12', K1)
        if 'P' in models: go(m, d, venue, tick, rolls, 'P', 'S', 'S', K2)
    for m in ('NQ', 'ES'):
        d1 = XA.learn(L.load_cme(m, '1min'), L.CME_LEARN_END); rolls = L.roll_dates(d1)
        both(m, d1, 'cme', 0.25, rolls, 'VPN'); del d1
        d5 = XA.learn(L.load_cme(m, '5min'), L.CME_LEARN_END); both(m, d5, 'cme', 0.25, rolls, 'EA'); del d5
        print(m, len(out), flush=True)
    both('XAUUSD', XA.learn(XA.load_gold(), 2022), 'broker_cmdty', 0.01, frozenset(), 'VPEAN')
    for m in ('BTCUSDT', 'ETHUSDT'): both(m, XA.learn(L.load_crypto(m, '5m'), L.CRYPTO_LEARN_END), 'broker', 0.0, frozenset(), 'VPEAN')
    for m in L.FX_PAIRS: both(m, XA.learn(L.load_fx(m, '5m'), L.FX_LEARN_END), 'broker_fx', 0.0, frozenset(), 'VPEAN')
    T = pd.DataFrame(out)
    grp = {'NQ': 'NQ', 'ES': 'ES', 'XAUUSD': 'GOLD', 'BTCUSDT': 'CRYPTO', 'ETHUSDT': 'CRYPTO'}
    T['grp'] = T.m.map(grp).fillna('FX') + '@' + T.venue; T['src'] = 'x15_a'
    return T[COLS]

# ------------------------------------------------------------------ x15_b (top-level script: exec the model code only)
def part_b():
    src = open(os.path.join(HERE, 'x15_b.py'), encoding='utf-8').read()
    ns = {'__file__': os.path.join(HERE, 'x15_b.py'), '__name__': 'x15_b_defs'}
    exec(compile(src.split('# ---------------------------------------------------------------- run')[0], 'x15_b.py', 'exec'), ns)
    rows = []
    for mk in ns['MK']():
        d = mk['d1'](); A = ns['prep'](d, mk)
        dd = pd.to_datetime(pd.Series(d.date)); dc = d[((dd >= mk['start']) & (dd < mk['end'])).values]
        B = wk_bias(dc); keys = pd.to_datetime(pd.Series(B.index)).values.astype('datetime64[D]').astype(np.int64)
        for b in ('S', 'I12'):
            A['b2'] = dict(zip(keys, B[b].values)); n0 = len(rows); ns['m14'](A, mk, rows, 'B')
            for r in rows[n0:]: r['bias'] = b
        print(mk['name'], len(rows), flush=True); del A, d, dc
    T = pd.DataFrame(rows); T['grp'] = T.model; T['model'] = 'b14 Dynamic canvas'; T['src'] = 'x15_b'
    return T[COLS]

# ------------------------------------------------------------------ x15_f
def part_f():
    import x15_f as XF
    MS = XF.markets(); byname = {M['m']: M for M in MS}
    for M in MS: M['BIAS'] = BI.bias(M['D'][['o', 'h', 'l', 'c']])
    def setb(b1, b2):
        for M in MS: M['D']['b1'] = M['BIAS'][b1].values; M['D']['b2'] = M['BIAS'][b2].values
    def sweep(do51_55):
        for M in MS:
            D = M['D']
            for date, a, b in XF.days(M['fine']):
                if date in M['rolls'] or date not in D.index: continue
                B = XF.Day.of(M['fine'], a, b); R = D.loc[date]
                if do51_55: XF.run51(M, B, date, R)
                XF.run58(M, B, date, R)
            if do51_55:
                for date, a, b in XF.days(M['d5']):
                    if date in M['rolls'] or date not in D.index: continue
                    XF.run55(M, XF.Day.of(M['d5'], a, b), date, D.loc[date])
            print(' f', M['m'], len(XF.ROWS), flush=True)
        for x, y in (('NQ', 'ES'), ('BTCUSDT', 'ETHUSDT'), ('EURUSD', 'GBPUSD')): XF.run56(byname[x], byname[y])
    out = []
    setb('S', 'I12'); sweep(True)
    for r in XF.ROWS:
        mo, cell = r['model'], r['cell'].split('|')[0]
        if cell == 'A-B1' and mo[:2] in ('51', '55'): out.append(dict(r, bias='S'))
        elif cell == 'B-B2' or (mo.startswith('56') and cell in ('A-swept', 'B-held')): out.append(dict(r, bias='I12'))
    XF.ROWS.clear(); setb('S', 'S'); sweep(False)
    for r in XF.ROWS:
        mo, cell = r['model'], r['cell'].split('|')[0]
        if (cell == 'B-B2' and mo.startswith('58')) or (mo.startswith('56') and cell in ('A-swept', 'B-held')): out.append(dict(r, bias='S'))
    T = pd.DataFrame(out); T = T[pd.to_datetime(T.date).dt.year <= 2023]
    cl = T.cell.str.split('|', expand=True); T['grp'] = cl[1].values
    T['model'] = np.where(T.model.str.startswith('56'), T.model + ' ' + cl[0], T.model); T['src'] = 'x15_f'
    return T[COLS]

# ------------------------------------------------------------------ x15_g (Pool; patches applied inside each worker)
def g_job(args):
    name, which = args
    import x15_g as XG
    orig = XG.space
    def space(d, sgn, rolls):
        S = orig(d, sgn, rolls); B = BI.bias(S['D']); S['D']['b1'] = B[which].values; S['D']['b2'] = B[which].values; return S
    XG.space = space
    for fn in ('m66r', 'm69', 'm72if', 'm73', 'm77', 'breaker_setups'): setattr(XG, fn, lambda *a, **k: [])
    XG.m65_66u = lambda *a, **k: ([], [])
    _, rs = XG.run_market(name)
    keep = {('59 Asia-FVG', 'B'), ('60 OR-fades', 'DR'), ('72 IFVG', 'MD'), ('76 RoundOTE', 'A'), ('76 RoundOTE', 'B')}
    return [dict(x, bias=which) for rr in rs for x in rr if (x['model'], x['cell']) in keep]

def part_g():
    from multiprocessing import Pool
    import x15_g as XG
    jobs = [(n, b) for n in XG.MK for b in ('S', 'I12')]
    with Pool(9) as p: res = p.map(g_job, jobs)
    T = pd.DataFrame([x for r in res for x in r]); gm = {k: v['grp'] for k, v in XG.MK.items()}
    T['grp'] = T.m.map(gm) + '@' + T.venue; T['model'] = T.model + '/' + T.cell; T['src'] = 'x15_g'
    return T[COLS]

# ------------------------------------------------------------------ originals (parsed from the original results files)
def originals():
    sp = os.path.join(HERE, '..', 'specs'); O = {}
    def put(src, model, grp, cell, n, net, v): O.setdefault((src, model, grp), []).append(f'{cell} n={n} {float(net):+.3f} {v}')
    for ln in open(os.path.join(sp, 'X15a-results.md'), encoding='utf-8'):
        p = [x.strip() for x in ln.split('|')]
        if len(p) > 10 and (p[2] in ('B1', 'B2') or (p[2] == 'B' and p[1].startswith('M09'))): put('x15_a', p[1], p[3], p[2], p[4], p[7], p[10])
    for ln in open(os.path.join(sp, 'X15b-results.md'), encoding='utf-8'):
        m = re.match(r'(\S+)\s+b14B\s+n=\s*(\d+).*NET ([-+.\d]+).*-> (\w+)', ln)
        if m: put('x15_b', 'b14 Dynamic canvas', m[1], 'B2', m[2], m[3], m[4])
    for ln in open(os.path.join(sp, 'X15f-results.md'), encoding='utf-8'):
        m = re.match(r'(.+?) / ([^|]+)\|(\S+): n=(\d+) NET ([-+.\d]+)R maxDD \d+R -> (\w+)', ln)
        if not m: continue
        if m[1].startswith('56') and m[2] in ('A-swept', 'B-held'): put('x15_f', m[1] + ' ' + m[2], m[3], 'B2', m[4], m[5], m[6])
        elif m[2] in ('A-B1', 'B-B2'): put('x15_f', m[1], m[3], m[2][2:], m[4], m[5], m[6])
    for ln in open(os.path.join(sp, 'X15g-results.md'), encoding='utf-8'):
        m = re.match(r'(PASS|fail) \| (\S+) \| (.+?) \| n=(\d+) NET ([-+.\d]+)R', ln)
        if m:
            b = {'59 Asia-FVG/B': 'B1', '72 IFVG/MD': 'B1', '60 OR-fades/DR': 'B2', '76 RoundOTE/A': 'B2', '76 RoundOTE/B': 'B2'}.get(m[3])
            if b: put('x15_g', m[3], m[2], b, m[4], m[5], m[1].upper())
    return O

def report():
    T = pd.concat([pd.read_pickle(os.path.join(TMP, f'x17d_{p}.pkl')) for p in 'abfg'], ignore_index=True)
    for m, g in T.groupby('m'): assert pd.to_datetime(pd.Series(g.date)).dt.year.max() <= LE[m], m   # learn era only
    O = originals()
    out = ['# X17d results: bias rerun with S and I12 (LEARN ERA ONLY; pre-registration specs/X17-bias-rerun.md section X17d)', '',
           'Only the bias input changed (tools/bias_ict.py; day d uses daily bars through d-1). Pass: NET >= +0.05R, maxDD <= 30R, FX >= 3/4',
           'pairs positive (56 SMT FX: 2/2), n >= 80 else "thin". "original" = the B1/B2 cell(s) the bias replaced, from the X15 results files.', '',
           '| script | model | market@venue | bias | n | gross R | NET R | maxDD R | pairs+ | verdict | original (cell n NET verdict) |',
           '|---|---|---|---|---|---|---|---|---|---|---|']
    best = {}
    for (src, mo, grp, b), g in T.groupby(['src', 'model', 'grp', 'bias'], sort=True):
        g = g.sort_values('date', kind='stable'); net = g.net.mean(); dd = L.maxdd(g.net.values); ok = net >= 0.05 and dd <= 30; pp = '-'
        if g.m.iloc[0] in L.FX_PAIRS:
            per = g.groupby('m').net.mean(); need = 2 if mo.startswith('56') else 3; pp = f'{int((per > 0).sum())}/{per.size}'
            ok = ok and (per > 0).sum() >= need
        v = ('PASS' if ok else 'FAIL') + (' (thin)' if len(g) < 80 else '')
        orig = '; '.join(O.get((src, mo, grp), ['?']))
        out.append(f'| {src} | {mo} | {grp} | {b} | {len(g)} | {g.gross.mean():+.3f} | {net:+.3f} | {dd:.0f} | {pp} | {v} | {orig} |')
        k = (src, mo); cand = (ok and len(g) >= 80, net, len(g), b, grp, v, orig)
        if k not in best or cand[:2] > best[k][:2]: best[k] = cand
    out += ['', '## Best cell per model (full passes with n >= 80 first, then highest NET)']
    for (src, mo), c in best.items(): out.append(f'- {src} {mo}: {c[3]} {c[4]} n={c[2]} NET {c[1]:+.3f} -> {c[5]} | original: {c[6]}')
    txt = '\n'.join(out); open(OUT, 'w', encoding='utf-8').write(txt + '\n'); print(txt)

if __name__ == '__main__':
    p = sys.argv[1]
    if p == 'report': report()
    else: {'a': part_a, 'b': part_b, 'f': part_f, 'g': part_g}[p]().to_pickle(os.path.join(TMP, f'x17d_{p}.pkl'))
