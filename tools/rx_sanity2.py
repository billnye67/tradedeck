"""Isolate why the RX sanity cell failed: #1's entries with (a) #1's OWN stop price at next-open entry, (b) wick-of-N for N in 6/12/24/48/96."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft_lab as L, ft_triggers as T, rx_run as R, ft_run as F
K = pd.read_csv(os.path.join(L.ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv')); K['t'] = pd.to_datetime(K.entryTs, unit='ms', utc=True)
def sig_rows(stop_from_file):
    rows = []
    for coin, g in K.groupby('market'):
        X = L.load(coin); tt = X['t']
        for r in g.itertuples():
            p = int(np.searchsorted(tt, r.t.value))
            if p >= len(tt) or p == 0: continue
            i = p - 1
            rows.append((coin, i, int(r.side), r.stop if stop_from_file else np.nan, np.nan, np.nan, 3.0, 480))
    return T.S(rows)
def report(tag, t):
    for era in ('learn', 'exam'):
        x = F.in_era(t, era)
        print(f'{tag:12s} {era}: n={len(x)} {x.R.mean():+.4f}R stop med {100*x.rd.median():.2f}% cost/risk {100*(x.cost/x.rd).median():.0f}% | ' + str(x.groupby('coin').R.mean().round(3).to_dict()), flush=True)
S = sig_rows(True); report('own-stop', L.simulate(S))
for N in (6, 12, 24, 48, 96):
    R.LOOK = N; report(f'wick{N}', R._sim(sig_rows(False)))
