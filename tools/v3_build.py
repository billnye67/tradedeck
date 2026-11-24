"""Engine v3 step 1 (spec specs/V3-entry-engine.md): every 5m candle on 23 coins -> raw features + long/short net-R labels
(tight stop behind the last 6 candles, 3R, 12h). Joins v2's hourly features as of the last completed hour.
Output: %LOCALAPPDATA%/TradeDeck/Data/lab/v3/<COIN>.parquet. Progress -> Web/lab/live.json."""
import os, sys, glob, json, time, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import v2_build as V2
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); OUT = os.path.join(LAB, 'v3'); os.makedirs(OUT, exist_ok=True)
COINS = 'BTC ETH SOL ORDI AVAX DOGE XRP ADA OP TIA BNB LINK INJ ARB DOT SEI FIL NEAR JTO WLD ICP ATOM DYDX'.split()
HOLD = 144; FEE = 0.0005; SWAP = 0.0005; MINSTOP = 0.0015
log = []
def status(pct, msg, done=False):
    log.append(f"{time.strftime('%H:%M:%S')}  {msg}"); print(msg, flush=True)
    for p in V2.LIVE:
        if os.path.isdir(os.path.dirname(p)): json.dump(dict(name='Engine v3: every 5-minute candle on 23 coins as a possible entry (blind search)', step='Build', pct=round(pct, 1), msg=msg, log=log[-14:], extra={}, done=done, t=time.strftime('%Y-%m-%d %H:%M:%S')), open(p, 'w'))

@njit(cache=True)
def labels(o, h, l, c, t_ms, stopL, stopS):
    n = len(o); yL = np.full(n, np.nan); yS = np.full(n, np.nan); hL = np.full(n, np.nan); hS = np.full(n, np.nan)
    for i in range(n - HOLD - 1):
        j = i + 1; e = o[j]
        for side in (1, -1):
            st = stopL[i] if side == 1 else stopS[i]
            if not st > 0: continue
            if side == 1: st = min(st, e * (1 - MINSTOP))
            else: st = max(st, e * (1 + MINSTOP))
            R = abs(e - st); tg = e + side * 3.0 * R; x = c[j + HOLD - 1]; xe = j + HOLD - 1
            for m in range(j, j + HOLD):
                if m > j and ((side == 1 and o[m] <= st) or (side == -1 and o[m] >= st)): x = o[m]; xe = m; break
                if (side == 1 and l[m] <= st) or (side == -1 and h[m] >= st): x = st; xe = m; break
                if (side == 1 and h[m] >= tg) or (side == -1 and l[m] <= tg): x = tg; xe = m; break
            day = 86400000; off = 1800000; nsw = (t_ms[xe] + 300000 - off) // day - (t_ms[j] - off) // day
            r = side * (x - e) / R - (2 * FEE + SWAP * nsw) * e / R; r = min(max(r, -1.5), 3.0)
            if side == 1: yL[i] = r; hL[i] = xe - j + 1
            else: yS[i] = r; hS[i] = xe - j + 1
    return yL, yS, hL, hS

def build(base):
    coin = base + 'USDT'; kf = sorted(glob.glob(os.path.join(V2.FLOW, 'klines5m', f'{coin}-5m-*.csv')))
    K = V2.read_csvs(kf, ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tbq']).astype(float).drop_duplicates('t').sort_values('t')
    K.index = pd.to_datetime(K.t.astype('int64'), unit='ms', utc=True); K = K[K.index < V2.END]
    o, h, l, c = K.o, K.h, K.l, K.c
    tr = np.maximum(h - l, np.maximum((h - c.shift()).abs(), (l - c.shift()).abs())); A = tr.rolling(288, min_periods=150).mean()
    F = pd.DataFrame(index=K.index)
    for n in (1, 3, 6, 12, 48): F[f'm{n}'] = (c - c.shift(n)) / A
    for k in (0, 1, 2):
        F[f'body{k}'] = ((c - o) / A).shift(k); F[f'uw{k}'] = ((h - np.maximum(o, c)) / A).shift(k); F[f'lw{k}'] = ((np.minimum(o, c) - l) / A).shift(k)
    F['rng'] = (h - l) / A
    lq = np.log1p(K.qv); F['vz1'] = (lq - lq.rolling(288).mean()) / lq.rolling(288).std(); lq3 = np.log1p(K.qv.rolling(3).sum()); F['vz3'] = (lq3 - lq3.rolling(288).mean()) / lq3.rolling(288).std()
    for n in (1, 3, 12): F[f'tb{n}'] = K.tb.rolling(n).sum() / K.v.rolling(n).sum()
    dv = 2 * K.tb - K.v; vm = K.v.rolling(288).mean(); F['d1'] = dv / vm; F['d12'] = dv.rolling(12).sum() / (12 * vm); F['ddiv12'] = -np.sign(F.m12) * np.sign(F.d12)
    for n in (12, 48, 288): F[f'hi{n}'] = (h.rolling(n).max() - c) / A; F[f'lo{n}'] = (c - l.rolling(n).min()) / A
    ph, pl = h.shift(1).rolling(48).max(), l.shift(1).rolling(48).min()
    F['poke_hi'] = (h - ph) / A; F['close_vs_ph'] = (c - ph) / A; F['poke_lo'] = (pl - l) / A; F['close_vs_pl'] = (c - pl) / A
    sL = l.rolling(6).min() - 0.1 * A; sS = h.rolling(6).max() + 0.1 * A
    F['stopL_a'] = (c - sL) / A; F['stopS_a'] = (sS - c) / A; F['stopL_p'] = (c - sL) / c; F['stopS_p'] = (sS - c) / c
    F['mod'] = K.index.hour * 60 + K.index.minute; F['dow5'] = K.index.dayofweek
    yL, yS, hL, hS = labels(o.values, h.values, l.values, c.values, K.t.values.astype(np.int64), sL.values, sS.values)
    F['yL'] = yL; F['yS'] = yS; F['hL'] = hL; F['hS'] = hS
    # hourly v2 features, as of the last completed hour (row hour_start H is known from H+1h)
    H = pd.read_parquet(os.path.join(LAB, 'v2', f'{coin}.parquet'))
    keep = [x for x in H.columns if not x.startswith(('yL', 'yS', 'hL', 'hS')) and x not in ('entry', 'coin', 'age_d')]
    H = H[keep].copy(); H['known'] = pd.to_datetime(H.hour_start, utc=True) + pd.Timedelta(hours=1); H = H.drop(columns='hour_start').add_prefix('h_')
    F.index.name = 'bar'; F['dec'] = F.index + pd.Timedelta(minutes=5)
    F = pd.merge_asof(F.reset_index().sort_values('dec'), H.sort_values('h_known'), left_on='dec', right_on='h_known', direction='backward').drop(columns='h_known')
    F = F[F.bar >= F.bar.min() + pd.Timedelta(days=30)]; F = F if os.environ.get('V3_KEEP_UNLABELED') == '1' else F.dropna(subset=['yL', 'yS']); F['coin'] = coin
    num = F.select_dtypes('float64').columns; F[num] = F[num].astype('float32')
    F.to_parquet(os.path.join(OUT, f'{coin}.parquet'), index=False)
    return coin, len(F)

if __name__ == '__main__':
    coins = sys.argv[1:] or COINS
    status(1, f'Building every 5-minute candle as a possible entry for {len(coins)} coins...'); rows = 0
    with ProcessPoolExecutor(6) as ex:
        for k, (coin, n) in enumerate(ex.map(build, coins)):
            rows += n; status(1 + 98 * (k + 1) / len(coins), f'{k+1}/{len(coins)} coins done ({coin}: {n:,} candles). Total {rows:,}.')
    status(100, f'DONE: {rows:,} candles x 2 directions = {2*rows:,} possible entries.', done=True)
