"""R13Y: R13X on more forex pairs (specs/ict-rebuild/R13Y-more-pairs.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r13_midnight as R
EXAM = os.environ.get('EXAM') == '1'; DK = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/fxcm_extra/'
PAIRS = sys.argv[1].split(',') if len(sys.argv) > 1 else ['USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'EURGBP', 'GBPJPY', 'AUDJPY']
def load(m):
    d = pd.read_parquet(DK + f'{m}_1m.parquet', columns=['t', 'o', 'h', 'l', 'c']); d.index = pd.to_datetime(d.t, unit='ms')
    d = d.resample('5min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna().reset_index()
    d['t'] = d['t'].dt.as_unit('ms').astype('int64'); return L._ny(d[['t', 'o', 'h', 'l', 'c']])
if __name__ == '__main__':
    R.EXAM = EXAM; rows = []
    for m in PAIRS:
        if not os.path.exists(DK + f'{m}_1m.parquet'): print(m, 'no data'); continue
        T = R.run(m, load(m), 'broker_fx', L.pip_of(m), L.FX_LEARN_END); T = T[T.cell.str.startswith('T15/NB/')]
        T['yr'] = pd.to_datetime(T.date).dt.year; T = T[T.yr >= 2020] if EXAM else T[T.yr <= L.FX_LEARN_END]
        for cell, g in T.groupby('cell'):
            g = g.sort_values('date'); s5 = g.sort_values('net')
            print(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()), flush=True)
        rows.append(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R13Y-' + ('exam' if EXAM else 'learn') + '.csv')
    pd.concat(rows).to_csv(out, index=False)
