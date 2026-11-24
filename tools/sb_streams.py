"""Scoreboard step 1 (spec specs/SB-scoreboard.md): build every trade stream variant from the EXISTING walk-forward predictions.
#1 (all / v2-filtered), v2 standalone (T x max open x coin set), combo v3+v2 (T x max open x coin set), tick-safe labels, real spreads.
Usage: python sb_streams.py learn|exam. Output: %LOCALAPPDATA%/TradeDeck/Data/lab/sb/streams_<era>.pkl  {key: DataFrame(coin, dt, exit, side, p, r, rd, era)}"""
import os, sys, pickle, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'); SL = os.path.join(ROOT, 'specs', 'lab')
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); OUT = os.path.join(LAB, 'sb'); os.makedirs(OUT, exist_ok=True)
BIG6 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT']
HALF = {'BTC': 0, 'ETH': 0, 'XRP': 0, 'SOL': 0, 'BNB': .1, 'DOGE': .6, 'ADA': .9, 'FIL': 1.3, 'LINK': 1.6, 'ICP': 1.6, 'NEAR': 1.9, 'WLD': 2, 'ARB': 2.5, 'ATOM': 2.9,
        'TIA': 3.3, 'OP': 3.6, 'INJ': 3.8, 'DYDX': 4, 'DOT': 4.3, 'ORDI': 4.8, 'AVAX': 4.9, 'JTO': 5.2, 'SEI': 8}
ALL23 = [k + 'USDT' for k in HALF]; GREEN12 = [k + 'USDT' for k, v in HALF.items() if v <= 2]
SETS = {'BIG6': BIG6, 'green12': GREEN12, 'all23': ALL23}
ERA = {'learn': (pd.Timestamp('2021-01-01', tz='UTC'), pd.Timestamp('2024-01-01', tz='UTC')), 'exam': (pd.Timestamp('2024-01-01', tz='UTC'), pd.Timestamp('2026-09-01', tz='UTC'))}
def extra_R(coins, rd): return 2 * (coins.str[:-4].map(HALF) - 2).clip(lower=0).values / 1e4 / rd
def v2_scores():
    P = pd.concat([pd.read_parquet(os.path.join(SL, f'v2_K1.5_{e}_preds.parquet'))[['coin', 'dt', 'pL', 'pS']].assign(era=e) for e in ('learn', 'exam')])
    P['dt'] = P.dt.astype('datetime64[us, UTC]'); return P.sort_values('dt')
def select(C, maxopen):
    """C sorted by dt then p desc: each decision time take the best coin x side not already open; max `maxopen` open, 1 per coin."""
    rows = []; open_ = []; dts = C.dt.values; coins = C.coin.values; exits = C.exit.values; n = len(C); i = 0
    while i < n:
        dt = dts[i]; j = i
        while j < n and dts[j] == dt: j += 1
        open_ = [o for o in open_ if o[1] > dt]
        if len(open_) < maxopen:
            for k in range(i, j):
                if any(o[0] == coins[k] for o in open_): continue
                open_.append((coins[k], exits[k])); rows.append(k); break
        i = j
    return C.iloc[rows]
def candidates_combo(era):
    R = pd.read_parquet(os.path.join(SL, f'v2_Kv3_{era}_preds.parquet'), columns=['coin', 'bar', 'dt', 'pL', 'pS'])
    R = R[R.coin.isin(ALL23) & ((R.pL > 0.3) | (R.pS > 0.3))].copy(); R['bar'] = R.bar.astype('datetime64[us, UTC]'); R['dt'] = R.dt.astype('datetime64[us, UTC]')
    out = []
    for coin, x in R.groupby('coin'):
        L = pd.read_parquet(os.path.join(LAB, 'v3b', f'{coin}.parquet'), columns=['bar', 'yL', 'yS', 'hL', 'hS', 'stopL_p', 'stopS_p', 'tick_p'])
        L['bar'] = pd.to_datetime(L.bar, utc=True).astype('datetime64[us, UTC]'); out.append(x.merge(L, on='bar', how='inner'))
    R = pd.concat(out); P = v2_scores()
    R = pd.concat([pd.merge_asof(x.sort_values('dt'), P[P.coin == c][['dt', 'pL', 'pS']].rename(columns={'pL': 'v2L', 'pS': 'v2S'}), on='dt', direction='backward', tolerance=pd.Timedelta(hours=2)) for c, x in R.groupby('coin')])
    tp = R.tick_p.fillna(0)
    C = pd.concat([R.assign(side=1, p=np.where(R.v2L >= 0, R.pL, -9), y=R.yL, hold=R.hL, rd=np.maximum(np.maximum(R.stopL_p, 0.0015), 5 * tp), v2=R.v2L),
                   R.assign(side=-1, p=np.where(R.v2S >= 0, R.pS, -9), y=R.yS, hold=R.hS, rd=np.maximum(np.maximum(R.stopS_p, 0.0015), 5 * tp), v2=R.v2S)])
    C = C[(C.p > 0.3)].dropna(subset=['y', 'hold'])[['coin', 'dt', 'side', 'p', 'v2', 'y', 'hold', 'rd']]
    C['exit'] = C.dt + pd.to_timedelta(C.hold * 5, unit='min'); C['r'] = C.y - extra_R(C.coin, C.rd)
    a, b = ERA[era]; return C[(C.dt >= a) & (C.dt < b)].sort_values(['dt', 'p'], ascending=[True, False]).reset_index(drop=True)
def candidates_v2(era):
    R = pd.read_parquet(os.path.join(SL, f'v2_K1.5_{era}_preds.parquet')); R = R[R.coin.isin(ALL23)].copy()
    R['hour_start'] = R.hour_start.astype('datetime64[us, UTC]'); R['dt'] = R.dt.astype('datetime64[us, UTC]'); out = []
    for coin, x in R.groupby('coin'):
        H = pd.read_parquet(os.path.join(LAB, 'v2', f'{coin}.parquet'), columns=['hour_start', 'atrp']); H['hour_start'] = pd.to_datetime(H.hour_start, utc=True).astype('datetime64[us, UTC]')
        out.append(x.merge(H, on='hour_start', how='left'))
    R = pd.concat(out); R['rd'] = 1.5 * R.atrp
    C = pd.concat([R.assign(side=1, p=R.pL, y=R['yL1.5'], hold=R['hL1.5'], v2=R.pL), R.assign(side=-1, p=R.pS, y=R['yS1.5'], hold=R['hS1.5'], v2=R.pS)])
    C = C[C.p > 0.1].dropna(subset=['y', 'hold', 'rd'])[['coin', 'dt', 'side', 'p', 'v2', 'y', 'hold', 'rd']]
    C['exit'] = C.dt + pd.to_timedelta(C.hold * 5, unit='min'); C['r'] = C.y - extra_R(C.coin, C.rd)
    a, b = ERA[era]; return C[(C.dt >= a) & (C.dt < b)].sort_values(['dt', 'p'], ascending=[True, False]).reset_index(drop=True)
def s1_stream():
    T = pd.read_csv(os.path.join(ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv')).sort_values('entryTs')
    T['dt'] = pd.to_datetime(T.entryTs, unit='ms', utc=True).astype('datetime64[us, UTC]'); T['exit'] = pd.to_datetime(T.exitTs, unit='ms', utc=True).astype('datetime64[us, UTC]')
    P = v2_scores(); out = []
    for coin, x in T.groupby('market'):
        out.append(pd.merge_asof(x.sort_values('dt'), P[P.coin == coin][['dt', 'pL', 'pS']].rename(columns={'dt': 'pdt'}), left_on='dt', right_on='pdt', direction='backward', tolerance=pd.Timedelta(hours=2)))
    T = pd.concat(out).sort_values('dt'); T['v2'] = np.where(T.side == 1, T.pL, T.pS); T['coin'] = T.market; T['r'] = T.netR; T['rd'] = (T.entry - T.stop).abs() / T.entry; T['p'] = T.v2
    return T[['coin', 'dt', 'exit', 'side', 'p', 'v2', 'r', 'rd', 'entry', 'stop']].reset_index(drop=True)
def global2(T):
    keep = []; open_ = []
    for i, r in enumerate(T.itertuples()):
        open_ = [o for o in open_ if o[1] > r.dt]
        if len(open_) >= 2 or any(o[0] == r.coin for o in open_): continue
        open_.append((r.coin, r.exit)); keep.append(i)
    return T.iloc[keep]
if __name__ == '__main__':
    S = {}; S1 = s1_stream()
    for era in sys.argv[1:]:
        a, b = ERA[era]
        X = S1[(S1.dt >= a) & (S1.dt < b)]
        S[('s1', 'all', era)] = global2(X).assign(era=era)
        for th in (-0.05, 0.0, 0.05): S[('s1', f'v2>={th:+.2f}', era)] = global2(X[X.v2 >= th]).assign(era=era)
        print(era, '#1 done', {k[1]: len(v) for k, v in S.items() if k[0] == 's1' and k[2] == era}, flush=True)
        for fam, fn, Ts in (('v2', candidates_v2, (0.1, 0.2, 0.3, 0.4, 0.5)), ('combo', candidates_combo, (0.3, 0.4, 0.5, 0.6, 0.7))):
            C = fn(era); print(era, fam, 'candidates', len(C), flush=True)
            for sname, coins in SETS.items():
                Cs = C[C.coin.isin(coins)]
                for T in Ts:
                    Ct = Cs[Cs.p > T]
                    for mo in (1, 2, 3):
                        S[(fam, f'{sname}|T{T}|max{mo}', era)] = select(Ct, mo).assign(era=era)
                print(era, fam, sname, 'done', flush=True)
    pickle.dump(S, open(os.path.join(OUT, f'streams_{"_".join(sys.argv[1:])}.pkl'), 'wb')); print('saved', len(S), 'streams', flush=True)
