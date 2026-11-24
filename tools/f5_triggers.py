"""F5 (spec specs/F5-v2-on-other-triggers.md)."""
import os, numpy as np, pandas as pd
LAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab')
P = {e: pd.read_parquet(os.path.join(LAB, f'v2_K1.5_{e}_preds.parquet'))[['coin', 'dt', 'pL', 'pS']] for e in ('learn', 'exam')}
P = pd.concat(P.values()); P['dt'] = P.dt.astype('datetime64[us, UTC]'); P = P.sort_values('dt')
def attach(T):
    T = T.copy(); T['et'] = T.et.astype('datetime64[us, UTC]')
    out = [pd.merge_asof(x.sort_values('et'), P[P.coin == c], left_on='et', right_on='dt', direction='backward', tolerance=pd.Timedelta(hours=2)) for c, x in T.groupby('coin')]
    M = pd.concat(out).dropna(subset=['pL']); M['score'] = np.where(M.side == 1, M.pL, M.pS); return M
def rep(name, M):
    print(f'== {name}')
    for era, x in (('learn 2021-23', M[M.et < '2024-01-01']), ('exam 2024-26', M[M.et >= '2024-01-01'])):
        for lab, y in (('unfiltered', x), ('v2 filtered', x[x.score >= 0])):
            s = np.sort(y.R.values); yrs = ' '.join(f'{k}:{v:+.0f}' for k, v in y.groupby(y.et.dt.year).R.sum().items())
            print(f'   {era} {lab:12s} {len(y):4d} trades {y.R.mean():+.3f}R ex-top5 {s[:-5].mean() if len(s) > 5 else float("nan"):+.3f} total {y.R.sum():+.0f}R | {yrs}')
B = pd.concat([pd.read_csv(os.path.join(LAB, f'b1_{e}.csv')) for e in ('learn', 'exam')])
B['et'] = pd.to_datetime(B.date).dt.tz_localize('UTC') + pd.Timedelta(days=1); B['side'] = 1
rep('B1 breakout (long, daily close entry)', attach(B[B.et >= '2021-01-01']))
S = pd.read_csv(os.path.join(LAB, '..', 'coinflip', 's4h_trades_p1.csv')).rename(columns={'market': 'coin', 'netR': 'R'})
S['et'] = pd.to_datetime(S.entryTs, unit='ms', utc=True)
rep('#1 4h raid / 1h entry (honest fills)', attach(S[S.et >= '2021-01-01']))
