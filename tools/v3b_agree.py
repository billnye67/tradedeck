"""v3b trading readout: v3b entry candidates only where v2 agrees on direction (F1 rule, score >= 0), then the v2_trade rule (best per candle,
max 3 open, 1 per coin) at T in {0.3, 0.5, 0.7}; same for the scramble-test model. Usage: python v3b_agree.py learn|exam [T]"""
import os, sys, subprocess, numpy as np, pandas as pd
ERA = sys.argv[1]; LAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab')
P = pd.concat([pd.read_parquet(os.path.join(LAB, f'v2_K1.5_{e}_preds.parquet'))[['coin', 'dt', 'pL', 'pS']] for e in ('learn', 'exam')]).rename(columns={'pL': 'v2L', 'pS': 'v2S'})
P['dt'] = P.dt.astype('datetime64[us, UTC]'); P = P.sort_values('dt')
for tag in ('v3b', 'v3bplacebo'):
    f = os.path.join(LAB, f'v2_K{tag}_{ERA}_preds.parquet')
    if not os.path.exists(f): continue
    R = pd.read_parquet(f); R['dt'] = R.dt.astype('datetime64[us, UTC]')
    if tag == 'v3bplacebo':   # score the scramble-trained model on the REAL outcomes
        real = pd.read_parquet(os.path.join(LAB, f'v2_Kv3b_{ERA}_preds.parquet'))[['coin', 'bar', 'yLv3b', 'ySv3b', 'hLv3b', 'hSv3b']]
        R = R.drop(columns=[c for c in R.columns if c.startswith(('yL', 'yS', 'hL', 'hS'))]).merge(real, on=['coin', 'bar'])
        R = R.rename(columns={'yLv3b': 'yLv3bplacebo', 'ySv3b': 'ySv3bplacebo', 'hLv3b': 'hLv3bplacebo', 'hSv3b': 'hSv3bplacebo'})
    R = pd.concat([pd.merge_asof(x.sort_values('dt'), P[P.coin == c].drop(columns='coin'), on='dt', direction='backward', tolerance=pd.Timedelta(hours=2)) for c, x in R.groupby('coin')])
    R.loc[~(R.v2L >= 0), 'pL'] = -9; R.loc[~(R.v2S >= 0), 'pS'] = -9     # v2 must agree
    R = R.rename(columns={f'{p}{tag}': f'{p}{tag}agree' for p in ('yL', 'yS', 'hL', 'hS')})
    R.drop(columns=['v2L', 'v2S']).to_parquet(os.path.join(LAB, f'v2_K{tag}agree_{ERA}_preds.parquet'), index=False)
    for T in (sys.argv[2:] or ['0.3', '0.5', '0.7']):
        print(f'--- {tag} + v2 agrees, {ERA}, T={T}', flush=True)
        out = subprocess.run([sys.executable, 'v2_trade.py'], env=dict(os.environ, V2_K=f'{tag}agree', V2_ERA=ERA, V2_T=T), capture_output=True, text=True)
        print(out.stdout.strip() or out.stderr[-500:], flush=True)
