"""F5-OWN step 2 (spec specs/F5-own-trades-filter.md): quarterly walk-forward gradient boosting on #1's own setups (110 coins),
judged on BTC/ETH/XRP against the current v2 filter (score >= 0, row 171). Env F5_EXAM=1 opens the exam (once). F5_LIVE=1 scores
the user's live Oct-Nov 2026 trades (info only) with a model trained on everything through Aug 2026."""
import os, sys, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import s1_evalsim as E, f5_build as B
EXAM = os.environ.get('F5_EXAM') == '1'; LIVE = os.environ.get('F5_LIVE') == '1'
G = ['g_stop_pct', 'g_stop_atr', 'g_bars_sweep_entry', 'g_breach_atr', 'g_breach_bp', 'g_level_age_h', 'g_react_atr', 'g_disp_body_atr', 'g_disp_ratio',
     'g_fvg_atr', 'g_fvg_count', 'g_retrace_depth', 'g_sweep_wick', 'g_sweep_vol_z', 'g_sweep_taker', 'g_hour', 'g_dow', 'g_session']
I = ['i_ob_dist_atr', 'i_in_ob', 'i_ifvg_n', 'i_ifvg_dist_atr', 'i_breaker_n', 'i_breaker_dist', 'i_pd_prevday', 'i_pd_today', 'i_d_mid', 'i_d_ldn', 'i_d_nyo',
     'i_d_pdh', 'i_d_pdl', 'i_h1_fvg_in', 'i_h1_fvg_dist', 'i_h4_fvg_in', 'i_h4_fvg_dist', 'i_h1_opp_in']
M = B.V2F + B.BF + ['m_v2_dir', 'm_v2_opp', 'm_btc_v2_dir', 'm_btc_volpct', 'm_btc_trend', 'm_coin_trend', 'm_atr_pct']
FEATS = G + I + M
PRM = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0, random_state=7)
KEY = ['market', 'entryTs', 'side']; DAY = 86400000

F = pd.read_parquet(os.path.join(B.OUT, 'feats.parquet'))
for f in FEATS:
    if f not in F.columns: F[f] = np.nan
F[FEATS] = F[FEATS].astype(float)
TR = F[F.pool == 'train'].sort_values('exitTs'); JF = F[F.pool == 'judge'].set_index(KEY)
J = pd.read_csv(os.path.join(ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv'))          # as every registry row uses it (duplicates kept)
J['et'] = pd.to_datetime(J.entryTs, unit='ms', utc=True)
print(f'{len(FEATS)} inputs; train pool {len(TR):,} setups on {TR.market.nunique()} coins; judge {len(J):,} rows', flush=True)

def fit(cut_ms):
    tr = TR[TR.exitTs < cut_ms - 2 * DAY]; fs = [f for f in FEATS if tr[f].notna().any()]
    return HistGradientBoostingRegressor(**PRM).fit(tr[fs], tr.netR), fs, tr

def walk(q0, q1):
    out = []
    for q in pd.period_range(q0, q1, freq='Q'):
        a = q.start_time.tz_localize('UTC'); b = (q.end_time + pd.Timedelta(microseconds=1)).tz_localize('UTC')
        x = JF[(JF.index.get_level_values('entryTs') >= a.value // 10**6) & (JF.index.get_level_values('entryTs') < b.value // 10**6)]
        if not len(x): continue
        m, fs, tr = fit(a.value // 10**6); out.append(pd.Series(m.predict(x[fs]), index=x.index, name='pred'))
        print(f'  {q}: trained on {len(tr):,} past setups, predicted {len(x)} judge setups', flush=True)
    return pd.concat(out)

def maxdd(x):
    c = x.sort_values('exitTs').netR.cumsum(); return float((c.cummax() - c).max())

def line(name, x):
    f = os.path.join(B.OUT, '_tmp.csv'); x[['market', 'entryTs', 'exitTs', 'netR']].to_csv(f, index=False); K = E.portfolio(f); p, bl, mo = E.sim(K)
    print(f'{name:34s} {len(x):5d} trades {x.netR.mean():+.3f}R total {x.netR.sum():+6.1f}R maxDD(GLOBAL2) {maxdd(K):5.1f}R | PASS {p:.0%} BLOW {bl:.0%} median {mo} mo', flush=True)
    return dict(n=len(x), r=x.netR.mean(), dd=maxdd(K), p=p)

def judge(lo, hi, preds, era):
    X = J[(J.et >= lo) & (J.et < hi)].join(JF[['m_v2_dir']], on=KEY).join(preds, on=KEY).dropna(subset=['m_v2_dir', 'pred'])
    print(f'\n{era} {lo[:4]}-{int(hi[:4]) - 1}: {len(X)} judge rows with both scores')
    line('unfiltered #1', X); v = line('CURRENT v2 filter (score >= 0)', X[X.m_v2_dir >= 0]); n = line('F5-OWN (pred >= 0)', X[X.pred >= 0])
    for c in (-0.05, 0.05): line(f'  info: F5-OWN pred >= {c:+.2f}', X[X.pred >= c])
    ok = (n['p'] >= v['p'] + 0.02) and (n['r'] > v['r']) and (n['dd'] <= v['dd'])
    print(f'{era} gate (PASS +2 pts, R/trade higher, maxDD no worse): {"MET" if ok else "MISSED"}'); return ok

if LIVE:
    L = pd.read_parquet(os.path.join(B.OUT, 'live_feats.parquet'))
    for f in FEATS:
        if f not in L.columns: L[f] = np.nan
    m, fs, tr = fit(pd.Timestamp('2026-09-01', tz='UTC').value // 10**6); L['pred'] = m.predict(L[fs].astype(float))
    L['F5'] = np.where(L.pred >= 0, 'TAKE', 'SKIP'); L['v2'] = np.where(L.m_v2_dir.isna(), '-', np.where(L.m_v2_dir >= 0, 'TAKE', 'SKIP'))
    print(L.sort_values('when')[['when', 'market', 'side', 'netR', 'm_v2_dir', 'v2', 'pred', 'F5']].round(3).to_string(index=False))
    for k in ('F5', 'v2'): print(k, L.groupby(k).netR.agg(['count', 'sum']).round(2).to_dict('index'))
    sys.exit()

P = walk('2021Q1', '2023Q4'); ok_l = judge('2021-01-01', '2024-01-01', P, 'LEARN')
m, fs, tr = fit(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6)        # model trained through 2023: importance (learn only)
s = tr.sample(min(20000, len(tr)), random_state=7); base = ((m.predict(s[fs]) - s.netR) ** 2).mean(); imp = {}
rng = np.random.default_rng(7)
for f in fs:
    z = s[fs].copy(); z[f] = rng.permutation(z[f].values); imp[f] = ((m.predict(z) - s.netR) ** 2).mean() - base
imp = pd.Series(imp).sort_values(ascending=False); imp.to_csv(os.path.join(ROOT, 'specs', 'lab', 'f5own_importance_learn.csv'))
print('\ntop 15 inputs (learn model, fit-error increase when shuffled):'); print(imp.head(15).round(5).to_string())
if EXAM:
    Q = walk('2024Q1', '2026Q3'); ok_e = judge('2024-01-01', '2026-09-01', Q, 'EXAM')
    print(f'\nVERDICT: {"PASS" if (ok_l and ok_e) else "FAIL"} (learn {"met" if ok_l else "missed"}, exam {"met" if ok_e else "missed"})')
