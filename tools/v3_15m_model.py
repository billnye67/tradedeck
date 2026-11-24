"""Combo on 15m candles, step 2 (spec specs/V3-15m.md): quarterly walk-forward HistGradientBoosting (same settings as v3) on the 15m
tick-safe net-R labels, long and short models. Usage: python v3_15m_model.py learn|exam  ->  specs/lab/v3_15m_<era>_preds.parquet"""
import os, sys, glob, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'); LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab')
ERA = sys.argv[1]
P = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(LAB, 'v3_15m', '*USDT.parquet')))], ignore_index=True)
P['dt'] = pd.to_datetime(P.dec, utc=True)                 # decision time (15m close)
FEATS = [c for c in P.columns if c not in ('bar', 'dec', 'dt', 'coin', 'yL', 'yS', 'hL', 'hS', 'tick')]
P['q'] = P.dt.dt.tz_localize(None).dt.to_period('Q')
qs = pd.period_range('2021Q1', '2023Q4', freq='Q') if ERA == 'learn' else pd.period_range('2024Q1', '2026Q3', freq='Q')
prm = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500, l2_regularization=1.0, random_state=7)
out = []
for q in qs:
    cut = q.to_timestamp().tz_localize('UTC') - pd.Timedelta(days=1)
    tr = P[P.dt < cut].dropna(subset=['yL', 'yS'])
    if len(tr) > 1_500_000: tr = tr.sample(1_500_000, random_state=7)
    te = P[(P.q == q) & (P.dt < pd.Timestamp('2026-08-29', tz='UTC'))]
    fs = [f for f in FEATS if tr[f].notna().any()]
    o = te[['coin', 'bar', 'dt', 'yL', 'yS', 'hL', 'hS']].copy()
    for side, y in (('L', 'yL'), ('S', 'yS')): o['p' + side] = HistGradientBoostingRegressor(**prm).fit(tr[fs], tr[y]).predict(te[fs])
    out.append(o); print(f'{q}: trained on {len(tr):,}, predicted {len(te):,}; candles with a score > 0.5: {((o.pL > .5) | (o.pS > .5)).sum():,}', flush=True)
pd.concat(out).to_parquet(os.path.join(ROOT, 'specs', 'lab', f'v3_15m_{ERA}_preds.parquet'), index=False)
print(f'DONE {ERA}', flush=True)
