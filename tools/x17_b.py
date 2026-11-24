"""X17b: rerun of x13_a models M27, M25, M42 and x13_b models b26, b63 with ONLY the bias input changed to S / I12
(tools/bias_ict.py). Pre-registered in specs/X17-bias-rerun.md (X17b). LEARN ERA ONLY (FX 2012-2019); the original prep()
functions drop all bars after 2019 at load. The original model functions are called unchanged; only the b1/b2 columns of
each script's own daily table are overwritten."""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ictlib as L, bias_ict, x13_a as XA, x13_b as XB

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'X17b-results.md')
ORIG = {  # original X13 learn results (specs/X13a-results.md, X13b-results.md): label, n, NET, maxDD, pairs+
    'M27/S': ('B1', 1165, -0.261, 328, 0), 'M27/I12': ('B2', 1189, -0.244, 297, 0),
    'M25/A_S': ('A_B1', 255, -0.290, 83, 0), 'M25/A_I12': ('A_B2', 285, -0.190, 76, 1),
    'M25/B_I12': ('B_B2', 877, -0.169, 155, 0),
    'M42/S': ('B1', 535, -0.114, 63, 1), 'M42/I12': ('B2', 563, -0.114, 81, 0),
    'b26/S': ('B=B1', 536, -0.048, 35, 1), 'b26/I12': ('C=B2', 545, -0.063, 37, 1),
    'b63/S_2R': ('A=B1 2R', 2061, +0.016, 54, 2), 'b63/I12_2R': ('B=B2 2R', 1992, -0.000, 68, 2),
    'b63/S_open': ('(no B1 open cell; B2 open shown) C', 1992, +0.031, 80, 2), 'b63/I12_open': ('C=B2 open', 1992, +0.031, 80, 2),
}

rows = []
# ---------------- x13_a: daily5 table; bias_at() reads the last row strictly before the day -> store END-OF-DAY state
RA = {'B1': 'S', 'B2': 'I12', 'B2_RANDOM': 'I12_RANDOM', 'A_B1': 'A_S', 'A_B2': 'A_I12', 'A_B2_RANDOM': 'A_I12_RANDOM', 'B_B2': 'B_I12'}
for m in L.FX_PAIRS:
    d5, A5 = XA.prep(m, '5m'); D = XA.daily5(d5)
    S, I = bias_ict._raw(D); D['b1'] = S; D['b2'] = I
    XA.m27(m, d5, A5, D); XA.m42(m, d5, A5, D); XA.m25b(m, A5, D)
    d15, A15 = XA.prep(m, '15m'); XA.m25a(m, A15, D)
    print(m, 'x13_a rows', len(XA.ROWS), flush=True)
TA = pd.DataFrame(XA.ROWS); TA['cell'] = TA.cell.map(RA); rows.append(TA)

# ---------------- x13_b: prep() daily table is pre-shifted -> store SHIFTED state, 0 -> NaN (= no trade)
def setb(X, c1, c2):
    B = bias_ict.bias(X['D']).replace(0, np.nan); X['D']['b1'] = B[c1].values; X['D']['b2'] = B[c2].values

for m in L.FX_PAIRS:
    X15 = XB.prep(m, '15m'); setb(X15, 'S', 'I12'); n0 = len(XB.ROWS); XB.m26(X15)
    t = pd.DataFrame(XB.ROWS[n0:]); t = t[t.cell.isin(['B', 'C'])].copy(); t['cell'] = t.cell.map({'B': 'S', 'C': 'I12'}); rows.append(t)
    X5 = XB.prep(m, '5m'); setb(X5, 'S', 'I12'); n0 = len(XB.ROWS); XB.m63(X5)
    t = pd.DataFrame(XB.ROWS[n0:]); t['cell'] = t.cell.map({'A': 'S_2R', 'B': 'I12_2R', 'C': 'I12_open', 'RANDctl': 'S_2R_RANDOM'}); rows.append(t)
    setb(X5, 'I12', 'S'); n0 = len(XB.ROWS); XB.m63(X5)   # second pass only for the S no-target cell
    t = pd.DataFrame(XB.ROWS[n0:]); t = t[t.cell == 'C'].copy(); t['cell'] = 'S_open'; rows.append(t)
    print(m, 'x13_b rows', len(XB.ROWS), flush=True)

T = pd.DataFrame(pd.concat(rows, ignore_index=True))
assert pd.to_datetime(T.date).dt.year.max() <= L.FX_LEARN_END
L.report(T, 'X17b learn (FX 2012-2019, Broker costs): x13_a/x13_b models rerun with ICT bias S / I12', L.FX_LEARN_END, OUT)
out = ['\n## Verdicts vs original (pass bar: NET >= +0.05R, maxDD <= 30R, >= 3/4 pairs positive, >= 80 trades else thin)',
       '| cell | n | gross | NET | maxDD | pairs+ | verdict | original cell | orig n | orig NET | orig maxDD | orig pairs+ |', '|' + '---|' * 12]
for (mo, cell), g in T.groupby(['model', 'cell']):
    g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values); pos = int((g.groupby('m').net.mean() > 0).sum())
    ok = net >= 0.05 and dd <= 30 and pos >= 3; thin = len(g) < 80
    v = ('control' if 'RANDOM' in cell else ('PASS' if ok else 'FAIL')) + (' (thin)' if thin else '')
    o = ORIG.get(f'{mo}/{cell}', ('-', '', '', '', ''))
    out.append(f'| {mo}/{cell} | {len(g)} | {g.gross.mean():+.3f} | {net:+.3f} | {dd:.0f} | {pos}/4 | {v} | {o[0]} | {o[1]} | '
               + (f'{o[2]:+.3f} | {o[3]} | {o[4]}/4 |' if o[1] != '' else ' | | |'))
out.append('\nNotes: original pairs+ for x13_a cells counted from the per-pair NET in X13a-results.md. b63 originally had no B1 no-target'
           ' cell, so S_open is compared with the B2 no-target cell. Random controls (same entry, coin-flip side) are not judged.')
txt = '\n'.join(out); print(txt); open(OUT, 'a', encoding='utf-8').write(txt + '\n')
