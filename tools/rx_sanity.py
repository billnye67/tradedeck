"""RX sanity cell: Strategy #1's own honest trades (specs/coinflip/s1_trades_nopeek.csv) pushed through the RX harness
(entry next 5m open, wick-of-6 stop floored 0.6%, 3R, 40h). Expected: same sign and similar size as #1's own grading."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft_lab as L, ft_triggers as T, rx_run as R, ft_run as F
K = pd.read_csv(os.path.join(L.ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv'))
K['t'] = pd.to_datetime(K.entryTs, unit='ms', utc=True)
rows = []
for coin, g in K.groupby('market'):
    X = L.load(coin); tt = X['t']
    for r in g.itertuples():
        ns = r.t.value; p = int(np.searchsorted(tt, ns))
        if p >= len(tt) or p == 0: continue
        i = p - 1 if tt[p] == ns else p - 1        # signal bar = the bar before the fill bar -> entry at that bar's open
        rows.append((coin, i, int(r.side), np.nan, np.nan, np.nan, 3.0, 480))
S = T.S(rows); S['sig_ts'] = S.i
t = R._sim(S)
for era in ('learn', 'exam'):
    a, b = L.ERA[era]; x = F.in_era(t, era); k = K[(K.t >= a) & (K.t < b)]
    P = L.portfolio(x, maxopen=2)
    print(f'{era}: #1 own grading n={len(k)} mean netR {k.netR.mean():+.4f} | RX harness raw n={len(x)} {x.R.mean():+.4f} | RX portfolio(2) n={len(P)} {P.R.mean():+.4f}'
          f' | RX stop med {100*x.rd.median():.2f}% cost/risk {100*(x.cost/x.rd).median():.0f}% | own stop med {100*(abs(k.entry-k.stop)/k.entry).median():.2f}%')
    print('   per coin own:', k.groupby('market').netR.mean().round(3).to_dict(), ' RX:', x.groupby('coin').R.mean().round(3).to_dict())
