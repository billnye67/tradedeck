"""X17c: rerun the bias-using models of tools/x13_d.py and tools/x13_e.py with ICT bias definitions S and I12 (tools/bias_ict.py)
in place of B1/B2. Everything else identical: the original model functions are called unchanged. Pre-registered in
specs/X17-bias-rerun.md (X17c section). LEARN era only: both original loaders drop every 2020+ bar."""
import sys, inspect
sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools')
import numpy as np, pandas as pd
import ictlib as L, bias_ict as BI, x13_d as XD, x13_e as XE

WHICH = {'v': 'S'}

# ---- x13_d: same daily table as bias_tables (ictlib.daily, Sunday stubs dropped); B1 and B2 replaced by the shifted ICT bias
def btab_d(d):
    B = XD.bias_tables(d)
    D = L.daily(d); D = D[pd.to_datetime(pd.Series(D.index)).dt.dayofweek.values != 6]
    b = BI.bias(D)[WHICH['v']]
    assert (b.index == B.index).all()
    B['B1'] = b.values; B['B2'] = b.values
    return B

# ---- x13_e: its daily table holds UNshifted b1/b2 (scripts shift or read row k-1 themselves), so put the raw (through-day-d) bias there
_orig_daily_of = XE.daily_of
def daily_of_ict(d, nmin):
    D = _orig_daily_of(d, nmin)
    S, I = BI._raw(D); v = S if WHICH['v'] == 'S' else I
    D['b1'] = v; D['b2'] = v
    return D
XE.daily_of = daily_of_ict

# M75: bias 0 means no trade under S/I12 (original B2 is never exactly 0). Only that condition changes.
src = inspect.getsource(XE.m75)
old = 'if bb != bb or sgn * bb > 0: continue'
assert src.count(old) == 1
exec(src.replace(old, 'if bb != bb or bb == 0 or sgn * bb > 0: continue'), XE.__dict__)

ORIG = {  # from specs/X13d-results.md and specs/X13e-results.md (LEARN): n, gross, NET, maxDD, pairs+
    ('M34-mitblock', '2R'): ('B2', 9735, 0.066, -0.125, 1261, 0), ('M34-mitblock', 'lowx'): ('B2', 6286, 0.056, -0.129, 834, 0),
    ('M54-swingOTE', 'mmB2'): ('B2', 1825, -0.024, -0.245, 448, 0),
    ('M70-Charter11', 'MonFri'): ('B2', 1713, -0.047, -0.126, 225, 0), ('M70-Charter11', 'MonWed'): ('B2', 941, -0.039, -0.118, 121, 1),
    ('M71-Charter12', 'B1'): ('B1', 504, -0.000, -0.076, 42, 0),
    ('M57', 'OTE'): ('B1 / B2', '567 / 545', '+0.047 / -0.064', '-0.084 / -0.192', '74 / 118', '0 / 0'),
    ('M57', 'OC'): ('B2', 701, -0.052, -0.146, 112, 0),
    ('M74', 'fade'): ('B1 / B2', '3221 / 3066', '+0.006 / +0.019', '-0.250 / -0.232', '806 / 712', '0 / 0'),
    ('M75', 'P3'): ('B2', 297, 0.023, -0.155, 52, 0), ('M75', 'P1'): ('B2', 297, 0.132, -0.072, 36, 2),
}

if __name__ == '__main__':
    cache = {}
    for m in L.FX_PAIRS:
        d = XD.load(m); R30, _ = XD.resample(d, 30); R60, _ = XD.resample(d, 60)
        d15 = XE.load(m, '15m'); d5e = XE.load(m, '5m') if m in ('EURUSD', 'GBPUSD') else None
        cache[m] = (d, R30, R60, d15, d5e); print('loaded', m, flush=True)
    out = []
    for which in ('S', 'I12'):
        WHICH['v'] = which; XE.ROWS = []
        for m in L.FX_PAIRS:
            d, R30, R60, d15, d5e = cache[m]
            B = btab_d(d)
            for fn, src_, cells in ((XD.m34, R30, ('2R', 'lowx')), (XD.m54, R60, ('mmB2',)), (XD.m70, d, ('MonFri', 'MonWed')), (XD.m71, d, ('B1',))):
                for cell in cells:
                    rows, _ = fn(m, src_, B, cell)
                    out += [dict(bias=which, model=r['model'], cell=r['cell'], m=r['m'], date=r['date'], gross=r['gross'], net=r['net']) for r in rows]
            D = XE.daily_of(d15, 64)
            XE.m74(m, d15, D); XE.m75(m, d15, D)
            print(which, m, flush=True)
        XE.m57({p: cache[p][4] for p in ('EURUSD', 'GBPUSD')})
        ren = {('M57', 'OTE_B2'): 'OTE', ('M57', 'OC_B2'): 'OC', ('M74', 'B2'): 'fade', ('M75', 'P3'): 'P3', ('M75', 'P1'): 'P1'}
        for r in XE.ROWS:
            k = (r['model'], r['cell'])
            if k in ren: out.append(dict(bias=which, model=r['model'], cell=ren[k], m=r['m'], date=r['date'], gross=r['gross'], net=r['net']))
    T = pd.DataFrame(out)
    assert pd.to_datetime(T.date).dt.year.max() <= L.FX_LEARN_END
    T.to_csv(r'C:\Users\you\AppData\Local\Temp\x17c_trades.csv', index=False)
    lines = ['# X17c results - x13_d / x13_e bias models rerun with ICT bias S and I12 (LEARN 2012-2019, Broker FX costs)', '',
             'Bar: NET >= +0.05R, maxDD <= 30R, pairs positive >= 3/4 (M57 2/2), n >= 80 else THIN. Original = X13d/X13e result with B1/B2.', '',
             '| model/cell | bias | n | gross R | NET R | maxDD R | pairs+ | verdict | original bias: n, gross, NET, maxDD, pairs+ |',
             '|---|---|---|---|---|---|---|---|---|']
    for (mo, cell), o in ORIG.items():
        for which in ('S', 'I12'):
            g = T[(T.bias == which) & (T.model == mo) & (T.cell == cell)].sort_values('date')
            npairs = 2 if mo == 'M57' else 4; need = 2 if mo == 'M57' else 3
            if len(g):
                net = g.net.mean(); dd = L.maxdd(g.net.values); pp = int((g.groupby('m').net.mean() > 0).sum())
                v = 'PASS' if (net >= 0.05 and dd <= 30 and pp >= need) else 'FAIL'
                if len(g) < 80: v += ' (THIN)'
                lines.append(f'| {mo}/{cell} | {which} | {len(g)} | {g.gross.mean():+.3f} | {net:+.3f} | {dd:.0f} | {pp}/{npairs} | {v} | '
                             f'{o[0]}: {o[1]}, {o[2]}, {o[3]}, {o[4]}, {o[5]}/{npairs} |')
            else:
                lines.append(f'| {mo}/{cell} | {which} | 0 | - | - | - | - | FAIL (no trades) | {o[0]}: {o[1]}, {o[2]}, {o[3]}, {o[4]}, {o[5]}/{npairs} |')
    lines += ['', 'Per pair NET (R):']
    for (b, mo, cell), g in T.groupby(['bias', 'model', 'cell']):
        lines.append(f'- {mo}/{cell} {b}: ' + ' '.join(f'{k[:3]} {v:+.3f} (n={c})' for (k, v), c in zip(g.groupby("m").net.mean().items(), g.groupby("m").size().values)))
    txt = '\n'.join(lines); print(txt)
    open(r'C:\Users\you\Desktop\TradeDeck\specs\X17c-results.md', 'w', encoding='utf-8').write(txt + '\n')
