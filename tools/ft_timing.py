"""FT (specs/FT-filter-timing.md, pre-registered 2026-11-21): v2 filter judged at ARM (live today) vs FILL (as tested, row 171) vs
RECHECK (arm and every hourly close until fill). Judge rows / merge / sim as row 209."""
import os, sys, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import s1_evalsim as E, f5_build as B
H = 3600000
J = pd.read_csv(os.path.join(ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv'))
J = J[J.market.isin(['BTCUSDT', 'ETHUSDT', 'XRPUSDT']) & (J.entryTs >= 1609459200000) & (J.entryTs < 1788220800000)].copy()

def arm_times(x, K):
    t, h, l = K.t.values, K.h.values, K.l.values; out = []
    for r in x.itertuples():
        s = 1 if r.side == 1 else -1; Ep, S = r.entry, r.stop; tol = 1e-6 * Ep
        ie = int(np.searchsorted(t, r.entryTs, side='left')); arm = np.nan
        k = None                                                   # sweep bar = the bar whose wick is the stop
        for j in range(ie - 1, max(ie - 300, 2), -1):
            if abs((l[j] if s == 1 else h[j]) - S) <= 1e-7 * S: k = j; break
        if k is not None:
            for j in range(ie - 1, k + 1, -1):                    # latest FVG completed before the entry bar
                if t[j] + 300000 > r.entryTs: continue
                lo, hi = (h[j - 2], l[j]) if s == 1 else (h[j], l[j - 2])
                if hi > lo and lo - tol <= Ep <= hi + tol and (abs(Ep - lo) <= tol or abs(Ep - hi) <= tol):
                    arm = t[j] + 300000; break
        out.append(arm)
    return pd.Series(out, index=x.index)

parts = []
for c, x in J.groupby('market'):
    K = B.bars(c); x = x.copy(); x['armTs'] = arm_times(x, K); parts.append(x); print(c, len(x), 'arm found', x.armTs.notna().mean().round(3), flush=True)
J = pd.concat(parts)
S = pd.concat([pd.read_parquet(os.path.join(ROOT, 'specs', 'lab', f'v2_K1.5_{e}_preds.parquet'))[['coin', 'dt', 'pL', 'pS']] for e in ('learn', 'exam')])
S['ms'] = pd.to_datetime(S.dt, utc=True).astype('datetime64[ms, UTC]').astype('int64')
S = S.drop_duplicates(['coin', 'ms']).sort_values('ms')

def score_at(c, side, ts):        # latest hourly score with close <= ts (within 2h), as the row-171 merge
    s = SC[c]; i = np.searchsorted(s[0], ts, side='right') - 1
    if i < 0 or ts - s[0][i] > 2 * H: return np.nan
    return s[1][i] if side == 1 else s[2][i]
SC = {c: (g.ms.values, g.pL.values, g.pS.values) for c, g in S.groupby('coin')}

def recheck(r):
    c = SC[r.market]; a = np.searchsorted(c[0], r.armTs, side='right'); b = np.searchsorted(c[0], r.entryTs, side='right')
    v = (c[1] if r.side == 1 else c[2])[a:b]; return bool(len(v) == 0 or (v >= 0).all())

D = J.dropna(subset=['armTs']).copy()
D['sFill'] = [score_at(r.market, r.side, r.entryTs) for r in D.itertuples()]
D['sArm'] = [score_at(r.market, r.side, r.armTs) for r in D.itertuples()]
D = D.dropna(subset=['sFill', 'sArm']); D['wait_h'] = (D.entryTs - D.armTs) / H
D['FILL'] = D.sFill >= 0; D['ARM'] = D.sArm >= 0; D['RECHECK'] = D.ARM & np.array([recheck(r) for r in D.itertuples()])
print(f'rows with arm + both scores: {len(D)} of {len(J)}; median wait {D.wait_h.median():.2f}h; filled in a later hour {(D.armTs // H != D.entryTs // H).mean():.0%}')

def maxdd(K):
    c = K.sort_values('exitTs').netR.cumsum(); return float((c.cummax() - c).max())
def line(name, x):
    f = os.path.join(B.OUT, '_ft.csv'); x[['market', 'entryTs', 'exitTs', 'netR']].to_csv(f, index=False); K = E.portfolio(f); p, bl, mo = E.sim(K)
    print(f'  {name:9s} {len(x):5d} trades {x.netR.mean():+.3f}R total {x.netR.sum():+6.1f}R maxDD {maxdd(K):5.1f}R | PASS {p:.0%} BLOW {bl:.0%} median {mo} mo', flush=True)
    return p, maxdd(K)
ok = []
for era, lo, hi in (('LEARN', 1609459200000, 1704067200000), ('EXAM', 1704067200000, 1788220800000)):
    X = D[(D.entryTs >= lo) & (D.entryTs < hi)]
    a_only = X[X.ARM & ~X.FILL]; f_only = X[X.FILL & ~X.ARM]
    print(f'\n{era}: {len(X)} rows. ARM pass / FILL fail: {len(a_only)} trades {a_only.netR.sum():+.1f}R ({a_only.netR.mean():+.3f}) | FILL pass / ARM fail: {len(f_only)} trades {f_only.netR.sum():+.1f}R ({f_only.netR.mean():+.3f})')
    r = {k: line(k, X[X[k]]) for k in ('FILL', 'ARM', 'RECHECK')}
    ok.append(r['RECHECK'][0] >= r['ARM'][0] and r['RECHECK'][1] <= r['ARM'][1])
print('\nDECISION (RECHECK pass >= ARM and maxDD <= ARM, both eras):', 'RECOMMEND RECHECK' if all(ok) else 'KEEP LIVE RULE', ok)
