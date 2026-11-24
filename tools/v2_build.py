"""Engine v2 step 1 (spec: specs/V2-engine.md): per coin, hourly decision rows with price + order-flow features and
long/short net-R labels from the 5m path (stop 1.0/1.5/2.0 x 1h ATR24, target 3R, 24h time exit, stop-first, Broker costs).
Input: %LOCALAPPDATA%/TradeDeck/Data/lab/flow/{klines5m,funding,metrics}. Output: .../lab/v2/<COIN>.parquet + all.parquet."""
import os, sys, glob, json, time, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); FLOW = os.path.join(LAB, 'flow'); OUT = os.path.join(LAB, 'v2'); os.makedirs(OUT, exist_ok=True)
LIVE = [os.path.join(ROOT, 'TradeDeck', 'Web', 'lab', 'live.json'), os.path.join(ROOT, 'TradeDeck', 'bin', 'Debug', 'net8.0-windows', 'Web', 'lab', 'live.json')]
END = pd.Timestamp('2026-09-01', tz='UTC'); KS = (1.0, 1.5, 2.0); TGT = 3.0; HOLD = 288; FEE = 0.0005; SWAP = 0.0005
log = []
def status(pct, msg, done=False):
    log.append(f"{time.strftime('%H:%M:%S')}  {msg}"); print(msg, flush=True)
    for p in LIVE:
        if os.path.isdir(os.path.dirname(p)): json.dump(dict(name='Engine v2: turning 6.5 years of 5-minute data into hourly setups and their outcomes', step='Build setups', pct=round(pct, 1), msg=msg, log=log[-14:], extra={}, done=done, t=time.strftime('%Y-%m-%d %H:%M:%S')), open(p, 'w'))

def read_csvs(files, names):
    parts = []
    for f in files:
        d = pd.read_csv(f, header=None, low_memory=False)
        if not str(d.iloc[0, 0]).replace('.', '').isdigit(): d = d.iloc[1:]
        d = d.iloc[:, :len(names)]; d.columns = names; parts.append(d)
    return pd.concat(parts) if parts else None

@njit(cache=True)
def labels(o, h, l, c, t_ms, starts, atr, k):
    n = len(starts); outL = np.full(n, np.nan); outS = np.full(n, np.nan); hL = np.full(n, np.nan); hS = np.full(n, np.nan); N = len(o)
    for i in range(n):
        j = starts[i]
        if j < 0 or j + HOLD > N or not atr[i] > 0: continue
        e = o[j]; R = k * atr[i]
        for side in (1, -1):
            stop = e - side * R; tgt = e + side * TGT * R; x = c[j + HOLD - 1]; xe = j + HOLD - 1
            for m in range(j, j + HOLD):
                if m > j and ((side == 1 and o[m] <= stop) or (side == -1 and o[m] >= stop)): x = o[m]; xe = m; break
                if (side == 1 and l[m] <= stop) or (side == -1 and h[m] >= stop): x = stop; xe = m; break
                if (side == 1 and h[m] >= tgt) or (side == -1 and l[m] <= tgt): x = tgt; xe = m; break
            # swaps: count 00:30 UTC instants in (entry time, exit bar close]
            t0 = t_ms[j]; t1 = t_ms[xe] + 300000; day = 86400000; off = 1800000
            nsw = (t1 - off) // day - (t0 - off) // day
            r = side * (x - e) / R - (2 * FEE + SWAP * nsw) * e / R
            r = min(max(r, -1.5), 3.0)
            if side == 1: outL[i] = r; hL[i] = xe - j + 1
            else: outS[i] = r; hS[i] = xe - j + 1
    return outL, outS, hL, hS

def build(coin):
    kf = sorted(glob.glob(os.path.join(FLOW, 'klines5m', f'{coin}-5m-*.csv')))
    if not kf: return coin, 0
    K = read_csvs(kf, ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tbq']).astype(float).drop_duplicates('t').sort_values('t')
    K.index = pd.to_datetime(K.t.astype('int64'), unit='ms', utc=True); K = K[K.index < END]
    if len(K) < 288 * 60: return coin, 0
    # 5m-level anchors (forward-filled opens) and 5m displacement
    tr5 = np.maximum(K.h - K.l, np.maximum((K.h - K.c.shift()).abs(), (K.l - K.c.shift()).abs())); atr5 = tr5.rolling(288, min_periods=100).mean()
    hm = K.index.hour * 60 + K.index.minute
    anc = pd.DataFrame({'mid': K.o.where(hm == 0), 'ldn': K.o.where(hm == 420), 'ny': K.o.where(hm == 810)}, index=K.index).ffill()
    bup = ((K.c - K.o).clip(lower=0) / atr5).rolling(12).max(); bdn = ((K.o - K.c).clip(lower=0) / atr5).rolling(12).max()
    # hourly bars
    H = K.resample('1h').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum', 'tb': 'sum', 'qv': 'sum', 't': 'size'}).rename(columns={'t': 'nb'})
    H = H[H.nb >= 10]
    last5 = K.index.to_series().resample('1h').last().reindex(H.index)   # the last 5m bar of each hour
    tr = np.maximum(H.h - H.l, np.maximum((H.h - H.c.shift()).abs(), (H.l - H.c.shift()).abs())); atr = tr.rolling(24, min_periods=20).mean()
    F = pd.DataFrame(index=H.index); A = atr
    for n in (1, 4, 24, 168): F[f'ret{n}'] = (H.c - H.c.shift(n)) / A
    a = anc.reindex(pd.DatetimeIndex(last5)); a.index = H.index
    for col in ('mid', 'ldn', 'ny'): F[f'd_{col}'] = (H.c - a[col]) / A
    D = H.resample('1D').agg({'h': 'max', 'l': 'min'}).shift(1); D = D.reindex(H.index, method='ffill')
    F['d_pdh'] = (H.c - D.h) / A; F['d_pdl'] = (H.c - D.l) / A
    for n in (24, 168): F[f'd_hi{n}'] = (H.c - H.h.rolling(n).max()) / A; F[f'd_lo{n}'] = (H.c - H.l.rolling(n).min()) / A
    F['disp_up'] = bup.reindex(pd.DatetimeIndex(last5)).values; F['disp_dn'] = bdn.reindex(pd.DatetimeIndex(last5)).values
    rng = (H.h - H.l).replace(0, np.nan)
    F['wick_up1'] = (H.h - np.maximum(H.o, H.c)) / rng; F['wick_dn1'] = (np.minimum(H.o, H.c) - H.l) / rng
    h4, l4, o4 = H.h.rolling(4).max(), H.l.rolling(4).min(), H.o.shift(3); r4 = (h4 - l4).replace(0, np.nan)
    F['wick_up4'] = (h4 - np.maximum(o4, H.c)) / r4; F['wick_dn4'] = (np.minimum(o4, H.c) - l4) / r4
    F['rng_x'] = (H.h - H.l) / A
    lv = np.log1p(H.qv); F['vol_z1'] = (lv - lv.rolling(168).mean()) / lv.rolling(168).std()
    lv4 = np.log1p(H.qv.rolling(4).sum()); F['vol_z4'] = (lv4 - lv4.rolling(168).mean()) / lv4.rolling(168).std()
    dv = 2 * H.tb - H.v; vm = H.v.rolling(168).mean()
    for n in (1, 4, 24):
        F[f'tshare{n}'] = H.tb.rolling(n).sum() / H.v.rolling(n).sum(); F[f'cvd{n}'] = dv.rolling(n).sum() / (vm * n)
    F['div4'] = -np.sign(F.ret4) * np.sign(F.cvd4); F['div24'] = -np.sign(F.ret24) * np.sign(F.cvd24)
    # funding
    ff = sorted(glob.glob(os.path.join(FLOW, 'funding', f'{coin}-fundingRate-*.csv')))
    if ff:
        Fu = read_csvs(ff, ['t', 'iv', 'r']).astype(float).drop_duplicates('t').sort_values('t'); Fu.index = pd.to_datetime(Fu.t.astype('int64'), unit='ms', utc=True)
        fr = Fu.r; m7 = fr.rolling('7D').mean(); m30 = fr.rolling('30D').mean(); s30 = fr.rolling('30D').std()
        G = pd.DataFrame({'fund': fr, 'fund7': m7, 'fund_z': (fr - m30) / s30}).sort_index()
        F = pd.merge_asof(F.sort_index(), G, left_index=True, right_index=True, direction='backward')
    else: F['fund'] = F['fund7'] = F['fund_z'] = np.nan
    # metrics (OI, long/short)
    mf = sorted(glob.glob(os.path.join(FLOW, 'metrics', f'{coin}-metrics-*.csv')))
    if mf:
        M = read_csvs(mf, ['ts', 'sym', 'oi', 'oiv', 'tt_ls_acc', 'tt_ls_pos', 'g_ls', 'taker_ls'])
        M.index = pd.to_datetime(M.ts, utc=True, errors='coerce'); M = M[M.index.notna()].drop(columns=['ts', 'sym']).apply(pd.to_numeric, errors='coerce').sort_index()
        M = M[~M.index.duplicated()]
        Mh = M.resample('1h').last()   # value at the end of the hour (stamp hour start)
        Mh['taker_ls'] = M.taker_ls.resample('1h').mean()
        oi = Mh.oiv
        X = pd.DataFrame(index=Mh.index)
        for n in (1, 4, 24): X[f'oi_chg{n}'] = oi / oi.shift(n) - 1
        X['oi_vs7d'] = oi / oi.rolling(168, min_periods=48).mean() - 1
        X['tt_ls'] = Mh.tt_ls_pos; X['tt_ls_chg24'] = Mh.tt_ls_pos - Mh.tt_ls_pos.shift(24); X['g_ls'] = Mh.g_ls; X['taker_ls'] = Mh.taker_ls
        F = F.join(X, how='left')
    else:
        for col in ('oi_chg1', 'oi_chg4', 'oi_chg24', 'oi_vs7d', 'tt_ls', 'tt_ls_chg24', 'g_ls', 'taker_ls'): F[col] = np.nan
    F['hour'] = F.index.hour; F['dow'] = F.index.dayofweek
    F['atrp'] = A / H.c; F['atrp_z'] = (F.atrp - F.atrp.rolling(720).mean()) / F.atrp.rolling(720).std()
    F['age_d'] = (F.index - H.index[0]).days
    # labels: entry = first 5m bar of the next hour
    pos = np.searchsorted(K.index.values, (H.index + pd.Timedelta(hours=1)).values)
    ok = (pos < len(K)) & (K.index.values[np.minimum(pos, len(K) - 1)] == (H.index + pd.Timedelta(hours=1)).values)
    starts = np.where(ok, pos, -1).astype(np.int64)
    tms = K.t.values.astype(np.int64)
    for k in KS:
        L, S, hl, hs = labels(K.o.values, K.h.values, K.l.values, K.c.values, tms, starts, A.values.astype(float), k)
        F[f'yL{k}'] = L; F[f'yS{k}'] = S; F[f'hL{k}'] = hl; F[f'hS{k}'] = hs   # hold in 5m bars
    F['entry'] = np.where(ok, K.o.values[np.minimum(pos, len(K) - 1)], np.nan)
    F['coin'] = coin; F.index.name = 'hour_start'
    F = F.reset_index(); F = F[F.age_d >= 30]
    num = F.select_dtypes('float64').columns; F[num] = F[num].astype('float32')
    F.to_parquet(os.path.join(OUT, f'{coin}.parquet'), index=False)
    return coin, len(F)

if __name__ == '__main__':
    coins = sorted({os.path.basename(f).split('-5m-')[0] for f in glob.glob(os.path.join(FLOW, 'klines5m', '*.csv'))})
    if len(sys.argv) > 1: coins = sys.argv[1:]
    status(1, f'Building hourly setups for {len(coins)} coins (each setup gets a long and a short outcome in R)...')
    done = 0; rows = 0
    with ProcessPoolExecutor(8) as ex:
        for coin, n in ex.map(build, coins):
            done += 1; rows += n
            status(1 + 97 * done / len(coins), f'{done}/{len(coins)} coins done ({coin}: {n:,} hourly setups). Total {rows:,}.')
    parts = [pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(OUT, '*USDT.parquet')))]
    pd.concat(parts, ignore_index=True).to_parquet(os.path.join(OUT, 'all.parquet'), index=False)
    status(100, f'DONE: {rows:,} hourly setups across {done} coins saved.', done=True)
