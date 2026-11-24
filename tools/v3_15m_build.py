"""Combo on 15m candles, step 1 (spec specs/V3-15m.md): every 15m candle on 23 coins -> v3 raw features + tick-safe long/short net-R
labels (stop behind the last 6 x 15m candles, 3R, 12h = 48 candles). Joins v2's hourly features as of the last completed hour.
Output: %LOCALAPPDATA%/TradeDeck/Data/lab/v3_15m/<COIN>.parquet. LAB ONLY (does not touch v3 / models / live_combo)."""
import os, sys, glob, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import v2_build as V2
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); OUT = os.path.join(LAB, 'v3_15m'); os.makedirs(OUT, exist_ok=True)
COINS = 'BTC ETH SOL ORDI AVAX DOGE XRP ADA OP TIA BNB LINK INJ ARB DOT SEI FIL NEAR JTO WLD ICP ATOM DYDX'.split()
HOLD = 48; FEE = 0.0005; SWAP = 0.0005; MINSTOP = 0.0015; BAR_MS = 900000; DAYN = 96

@njit(cache=True)
def labels(o, h, l, c, t_ms, stopL, stopS, mind):
    n = len(o); yL = np.full(n, np.nan); yS = np.full(n, np.nan); hL = np.full(n, np.nan); hS = np.full(n, np.nan)
    for i in range(n - HOLD - 1):
        j = i + 1; e = o[j]
        if t_ms[j + HOLD - 1] - t_ms[j] != (HOLD - 1) * BAR_MS: continue      # skip windows with missing candles
        for side in (1, -1):
            st = stopL[i] if side == 1 else stopS[i]
            if not st > 0: continue
            md = max(e * MINSTOP, mind[i])
            if side == 1: st = min(st, e - md)
            else: st = max(st, e + md)
            R = abs(e - st); tg = e + side * 3.0 * R; x = c[j + HOLD - 1]; xe = j + HOLD - 1
            for m in range(j, j + HOLD):
                if m > j and ((side == 1 and o[m] <= st) or (side == -1 and o[m] >= st)): x = o[m]; xe = m; break
                if (side == 1 and l[m] <= st) or (side == -1 and h[m] >= st): x = st; xe = m; break
                if (side == 1 and h[m] >= tg) or (side == -1 and l[m] <= tg): x = tg; xe = m; break
            day = 86400000; off = 1800000; nsw = (t_ms[xe] + BAR_MS - off) // day - (t_ms[j] - off) // day
            r = side * (x - e) / R - (2 * FEE + SWAP * nsw) * e / R; r = min(max(r, -1.5), 3.0)
            if side == 1: yL[i] = r; hL[i] = xe - j + 1
            else: yS[i] = r; hS[i] = xe - j + 1
    return yL, yS, hL, hS

def build(base):
    coin = base + 'USDT'; kf = sorted(glob.glob(os.path.join(V2.FLOW, 'klines5m', f'{coin}-5m-*.csv')))
    K5 = V2.read_csvs(kf, ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tbq']).astype(float).drop_duplicates('t').sort_values('t')
    K5.index = pd.to_datetime(K5.t.astype('int64'), unit='ms', utc=True); K5 = K5[K5.index < V2.END]
    g = K5.resample('15min', label='left', closed='left')
    K = pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last(), 'v': g.v.sum(), 'qv': g.qv.sum(), 'tb': g.tb.sum(), 'cnt': g.o.count()})
    K = K[K.cnt == 3].drop(columns='cnt')
    K['t'] = (K.index - pd.Timestamp('1970-01-01', tz='UTC')) // pd.Timedelta(milliseconds=1)
    o, h, l, c = K.o, K.h, K.l, K.c
    tr = np.maximum(h - l, np.maximum((h - c.shift()).abs(), (l - c.shift()).abs())); A = tr.rolling(DAYN, min_periods=50).mean()
    F = pd.DataFrame(index=K.index)
    for n in (1, 3, 6, 12, 48): F[f'm{n}'] = (c - c.shift(n)) / A
    for k in (0, 1, 2):
        F[f'body{k}'] = ((c - o) / A).shift(k); F[f'uw{k}'] = ((h - np.maximum(o, c)) / A).shift(k); F[f'lw{k}'] = ((np.minimum(o, c) - l) / A).shift(k)
    F['rng'] = (h - l) / A
    lq = np.log1p(K.qv); F['vz1'] = (lq - lq.rolling(DAYN).mean()) / lq.rolling(DAYN).std(); lq3 = np.log1p(K.qv.rolling(3).sum()); F['vz3'] = (lq3 - lq3.rolling(DAYN).mean()) / lq3.rolling(DAYN).std()
    for n in (1, 3, 12): F[f'tb{n}'] = K.tb.rolling(n).sum() / K.v.rolling(n).sum()
    dv = 2 * K.tb - K.v; vm = K.v.rolling(DAYN).mean(); F['d1'] = dv / vm; F['d12'] = dv.rolling(12).sum() / (12 * vm); F['ddiv12'] = -np.sign(F.m12) * np.sign(F.d12)
    for n, nm in ((12, 12), (48, 48), (DAYN, 288)): F[f'hi{nm}'] = (h.rolling(n).max() - c) / A; F[f'lo{nm}'] = (c - l.rolling(n).min()) / A
    ph, pl = h.shift(1).rolling(48).max(), l.shift(1).rolling(48).min()
    F['poke_hi'] = (h - ph) / A; F['close_vs_ph'] = (c - ph) / A; F['poke_lo'] = (pl - l) / A; F['close_vs_pl'] = (c - pl) / A
    sL = l.rolling(6).min() - 0.1 * A; sS = h.rolling(6).max() + 0.1 * A
    F['stopL_a'] = (c - sL) / A; F['stopS_a'] = (sS - c) / A; F['stopL_p'] = (c - sL) / c; F['stopS_p'] = (sS - c) / c
    F['mod'] = K.index.hour * 60 + K.index.minute; F['dow5'] = K.index.dayofweek
    # tick size per month from the 5m prices (same rule as v3b): smallest positive price step seen that month
    st5 = pd.concat([K5.c, K5.o, K5.h, K5.l]).sort_index(); dd = st5.groupby(st5.index.tz_localize(None).to_period('M')).apply(lambda s: np.diff(np.unique(np.round(s.values, 10))).min() if s.nunique() > 1 else np.nan)
    tick = pd.Series(K.index.tz_localize(None).to_period('M'), index=K.index).map(dd).astype(float).values
    yL, yS, hL, hS = labels(o.values, h.values, l.values, c.values, K.t.values.astype(np.int64), sL.values, sS.values, 5 * np.nan_to_num(tick))
    F['yL'] = yL; F['yS'] = yS; F['hL'] = hL; F['hS'] = hS; F['tick'] = tick
    H = pd.read_parquet(os.path.join(LAB, 'v2', f'{coin}.parquet'))
    keep = [x for x in H.columns if not x.startswith(('yL', 'yS', 'hL', 'hS')) and x not in ('entry', 'coin', 'age_d')]
    H = H[keep].copy(); H['known'] = pd.to_datetime(H.hour_start, utc=True) + pd.Timedelta(hours=1); H = H.drop(columns='hour_start').add_prefix('h_')
    F.index.name = 'bar'; F['dec'] = F.index + pd.Timedelta(minutes=15)
    F = F.reset_index(); F['dec'] = F.dec.astype(H.h_known.dtype) if str(H.h_known.dtype) != str(F.dec.dtype) else F.dec
    F = pd.merge_asof(F.sort_values('dec'), H.sort_values('h_known'), left_on='dec', right_on='h_known', direction='backward').drop(columns='h_known')
    F = F[F.bar >= F.bar.min() + pd.Timedelta(days=30)].dropna(subset=['yL', 'yS']); F['coin'] = coin
    num = F.select_dtypes('float64').columns; F[num] = F[num].astype('float32')
    F.to_parquet(os.path.join(OUT, f'{coin}.parquet'), index=False)
    return coin, len(F)

if __name__ == '__main__':
    coins = sys.argv[1:] or COINS; rows = 0
    with ProcessPoolExecutor(6) as ex:
        for k, (coin, n) in enumerate(ex.map(build, coins)):
            rows += n; print(f'{k+1}/{len(coins)} {coin}: {n:,} candles. Total {rows:,}.', flush=True)
    print(f'DONE: {rows:,} 15m candles x 2 directions.', flush=True)
