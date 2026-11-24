"""FT data (spec specs/FT-forced-triggers.md): one 5m table per coin from the raw lab/flow files ->
%LOCALAPPDATA%/TradeDeck/Data/lab/ft/<COIN>.parquet  [t (bar open, UTC), o h l c v qv tb (taker-buy base), oi (sum_open_interest, NaN = missing), prem]
and <COIN>_fund.parquet [t (settlement time), rate]. Universe = coins with v2 predictions (lab/v2/*.parquet)."""
import os, glob, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); FLOW = os.path.join(LAB, 'flow'); OUT = os.path.join(LAB, 'ft'); os.makedirs(OUT, exist_ok=True)
COINS = sorted(os.path.basename(f)[:-8] for f in glob.glob(os.path.join(LAB, 'v2', '*.parquet')))
def _csv(f, **kw):
    try: return pd.read_csv(f, **kw)
    except Exception: return None
def klines(coin):
    out = []
    for f in sorted(glob.glob(os.path.join(FLOW, 'klines5m', f'{coin}-5m-*.csv'))):
        d = _csv(f, header=None, low_memory=False)
        if d is None or not len(d): continue
        d = d[d.iloc[:, 0].astype(str).str.isdigit()]
        out.append(d.iloc[:, [0, 1, 2, 3, 4, 5, 7, 9]].astype(float))
    K = pd.concat(out); K.columns = ['t', 'o', 'h', 'l', 'c', 'v', 'qv', 'tb']
    K['t'] = pd.to_datetime(K.t.astype('int64'), unit='ms', utc=True); return K.drop_duplicates('t').set_index('t').sort_index()
def metrics(coin):
    out = []
    for f in sorted(glob.glob(os.path.join(FLOW, 'metrics', f'{coin}-metrics-*.csv'))):
        d = _csv(f, usecols=['create_time', 'sum_open_interest'])
        if d is None or not len(d): continue
        out.append(d)
    if not out: return None
    M = pd.concat(out); ct = M.create_time.astype(str)
    num = ct.str.fullmatch(r'\d+')
    t = pd.Series(pd.NaT, index=M.index, dtype='datetime64[ns, UTC]')
    if num.any():
        v = ct[num].astype('int64'); t[num] = pd.to_datetime(np.where(v > 1e11, v, v * 1000), unit='ms', utc=True)
    if (~num).any(): t[~num] = pd.to_datetime(ct[~num], utc=True, errors='coerce')
    M['t'] = t; M['oi'] = pd.to_numeric(M.sum_open_interest, errors='coerce'); M.loc[M.oi <= 0, 'oi'] = np.nan
    return M.dropna(subset=['t']).drop_duplicates('t').set_index('t').oi.sort_index()
def premium(coin):
    fs = sorted(glob.glob(os.path.join(FLOW, 'premium5m', f'{coin}-5m-*.csv')))
    if not fs: return None
    P = pd.concat([_csv(f, header=None, low_memory=False) for f in fs]); P = P[P.iloc[:, 0].astype(str).str.isdigit()].iloc[:, [0, 4]].astype(float)
    P.columns = ['t', 'prem']; P['t'] = pd.to_datetime(P.t.astype('int64'), unit='ms', utc=True); return P.drop_duplicates('t').set_index('t').prem.sort_index()
def funding(coin):
    fs = sorted(glob.glob(os.path.join(FLOW, 'funding', f'{coin}-fundingRate-*.csv')))
    if not fs: return None
    F = pd.concat([_csv(f) for f in fs]); F = F[pd.to_numeric(F.calc_time, errors='coerce').notna()]
    F['t'] = pd.to_datetime(F.calc_time.astype('int64') // 1000 * 1000, unit='ms', utc=True).dt.floor('min'); F['rate'] = F.last_funding_rate.astype(float)
    return F.drop_duplicates('t').set_index('t').rate.sort_index()
def build(coin):
    try:
        K = klines(coin); oi = metrics(coin); pr = premium(coin); fu = funding(coin)
        # OI snapshot stamped at time X describes the market at X; attach it to the bar that OPENS at X (known at that bar's open)
        K['oi'] = oi.reindex(K.index) if oi is not None else np.nan
        K['prem'] = pr.reindex(K.index) if pr is not None else np.nan   # premium bar opening at t, its close known at t+5m
        K = K.astype('float32'); K.reset_index().to_parquet(os.path.join(OUT, f'{coin}.parquet'), index=False)
        if fu is not None: fu.reset_index().to_parquet(os.path.join(OUT, f'{coin}_fund.parquet'), index=False)
        return coin, len(K), int(K.oi.notna().sum()), int(K.prem.notna().sum()), 0 if fu is None else len(fu)
    except Exception as e: return coin, f'ERR {e}', 0, 0, 0
if __name__ == '__main__':
    with ProcessPoolExecutor(6) as ex:
        for r in ex.map(build, COINS): print(*r, flush=True)
