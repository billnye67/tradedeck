"""Combo paper bot diagnosis (2026-11-20, queue step 1): why 0 paper trades since 2026-10-16? Read-only.
(1) backtest exam preds (specs/lab/v2_Kv3_exam_preds.parquet): bars with max(pL,pS) > 0.5 / 0.7 per month, 6 paper coins vs all 23;
(2) backtest combo trades per month (specs/lab/combo_trades_ticksafe.parquet, exam); (3) live scores.csv best entry score per week and
bars passing both rules (v3 > 0.5 and v2 >= 0). Live-vs-history score match = combo_check.py's daily checks.json (summarised here)."""
import os, json, collections, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'); LIVE = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\live_combo')
SIX = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT']
P = pd.read_parquet(os.path.join(ROOT, 'specs', 'lab', 'v2_Kv3_exam_preds.parquet'), columns=['coin', 'bar', 'pL', 'pS']).reset_index(drop=True)
P['m'] = pd.to_datetime(P.bar, utc=True).dt.strftime('%Y-%m'); P['mx'] = P[['pL', 'pS']].max(axis=1)
g = lambda X: X.groupby('m').mx.agg(over05=lambda x: (x > 0.5).sum(), over07=lambda x: (x > 0.7).sum(), best='max')
print(g(P[P.coin.isin(SIX)]).add_prefix('six_').join(g(P).add_prefix('all_')).round(3).tail(14).to_string())
C = pd.read_parquet(os.path.join(ROOT, 'specs', 'lab', 'combo_trades_ticksafe.parquet')).reset_index(drop=True); C = C[C.era == 'exam']
C['m'] = pd.to_datetime(C.dt, utc=True).dt.strftime('%Y-%m')
print(pd.concat([C[C.coin.isin(SIX)].groupby('m').size().rename('six_trades'), C.groupby('m').size().rename('all_trades')], axis=1).fillna(0).astype(int).sort_index().tail(14).to_string())
S = pd.read_csv(os.path.join(LIVE, 'scores.csv')); S['bar'] = pd.to_datetime(S.bar, utc=True, format='mixed'); S['mx'] = S[['pL', 'pS']].max(axis=1)
S['w'] = S.bar.dt.strftime('%G-W%V')
print(S.groupby('w').mx.agg(best='max', over03=lambda x: (x > 0.3).sum(), over05=lambda x: (x > 0.5).sum()).round(3).to_string())
print('live bars passing v3 > 0.5 AND v2 >= 0:', int((((S.pL > 0.5) & (S.v2L >= 0)) | ((S.pS > 0.5) & (S.v2S >= 0))).sum()))
d = json.load(open(os.path.join(LIVE, 'checks.json')))['days']; print('daily live-vs-history checks:', dict(collections.Counter(x['status'] for x in d)),
      'trade flips total', sum(x.get('trade_flips', 0) for x in d))
