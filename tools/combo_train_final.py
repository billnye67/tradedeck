"""Combo paper bot, step 1: train the FINAL v2 (direction) and v3 (entry) models on all data through 2026-08, same fixed settings and
1.5M-row subsample as the walk-forward tests (registry 170/176-180). Saves joblib models + feature lists to Data/lab/models."""
import os, glob, json, joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); MD = os.path.join(LAB, 'models')
prm = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500, l2_regularization=1.0, random_state=7)
# v2
import v2_model_feats as F2
P = pd.read_parquet(os.path.join(LAB, 'v2', 'all.parquet')); P['hour_start'] = pd.to_datetime(P.hour_start, utc=True)
B = P[P.coin == 'BTCUSDT'].set_index('hour_start')[['ret1', 'ret4', 'ret24', 'tshare4']].add_prefix('b_'); P = P.join(B, on='hour_start')
f2 = [c for c in F2.FEATS if c in P.columns]; tr = P.dropna(subset=['yL1.5', 'yS1.5']); tr = tr.sample(min(1_500_000, len(tr)), random_state=7)
for side in ('L', 'S'):
    m = HistGradientBoostingRegressor(**prm).fit(tr[f2], tr[f'y{side}1.5']); joblib.dump(m, os.path.join(MD, f'v2_{side}.joblib')); print('v2', side, 'trained', flush=True)
# v3
Q = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(LAB, 'v3', '*USDT.parquet')))], ignore_index=True)
f3 = [c for c in Q.columns if c not in ('bar', 'dec', 'dt', 'coin', 'yL', 'yS', 'hL', 'hS')]
tr = Q.sample(min(1_500_000, len(Q)), random_state=7)
for side in ('L', 'S'):
    m = HistGradientBoostingRegressor(**prm).fit(tr[f3], tr[f'y{side}']); joblib.dump(m, os.path.join(MD, f'v3_{side}.joblib')); print('v3', side, 'trained', flush=True)
json.dump(dict(v2=f2, v3=f3, trained_through='2026-08-31', settings=prm, T=0.5, v2_agree=0.0), open(os.path.join(MD, 'combo_features.json'), 'w'), indent=1)
print('DONE')
