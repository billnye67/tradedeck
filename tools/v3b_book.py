"""v3b inputs: Binance bookDepth (order-book notional within 1/2/5% of price, ~30s snapshots, 2023+) and premium index (perp vs spot,
5m) -> one 5m table per coin: %LOCALAPPDATA%/TradeDeck/Data/lab/v3b_in/<COIN>.parquet (stamped at the snapshot time, merged as-of later)."""
import os, glob, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); FLOW = os.path.join(LAB, 'flow'); OUT = os.path.join(LAB, 'v3b_in'); os.makedirs(OUT, exist_ok=True)
COINS = [x + 'USDT' for x in 'BTC ETH SOL ORDI AVAX DOGE XRP ADA OP TIA BNB LINK INJ ARB DOT SEI FIL NEAR JTO WLD ICP ATOM DYDX'.split()]
def one_day(f):
    try:
        d = pd.read_csv(f)
        if 'percentage' not in d.columns: d.columns = ['timestamp', 'percentage', 'depth', 'notional']
        d['ts'] = pd.to_datetime(d.timestamp, utc=True, errors='coerce'); d = d.dropna(subset=['ts'])
        w = d.pivot_table(index='ts', columns='percentage', values='notional', aggfunc='last')
        w = w.rename(columns=lambda p: f'n{float(p):+g}'); w = w.loc[:, ~w.columns.duplicated()].resample('5min', label='right', closed='right').last()   # value known at the 5m mark
        return w
    except Exception: return None
def build(coin):
    fs = sorted(glob.glob(os.path.join(FLOW, 'bookDepth', f'{coin}-bookDepth-*.csv')))
    B = pd.concat([w for w in map(one_day, fs) if w is not None]).sort_index(); B = B[~B.index.duplicated()]
    X = pd.DataFrame(index=B.index)
    b1, a1, b2, a2, b5, a5 = (B.get(k) for k in ('n-1', 'n+1', 'n-2', 'n+2', 'n-5', 'n+5'))
    X['ob_imb1'] = (b1 - a1) / (b1 + a1); X['ob_imb2'] = (b2 - a2) / (b2 + a2); X['ob_imb5'] = (b5 - a5) / (b5 + a5)
    tot = np.log(b5 + a5); X['ob_depth_z'] = (tot - tot.rolling(288, min_periods=100).mean()) / tot.rolling(288, min_periods=100).std()
    X['ob_bid1_chg12'] = np.log(b1 / b1.shift(12)); X['ob_ask1_chg12'] = np.log(a1 / a1.shift(12))
    X['ob_near_share'] = (b1 + a1) / (b5 + a5)                     # how much of the book sits close to price
    pf = sorted(glob.glob(os.path.join(FLOW, 'premium5m', f'{coin}-5m-*.csv')))
    if pf:
        P = pd.concat([pd.read_csv(f, header=None, low_memory=False) for f in pf]); P = P[P.iloc[:, 0].astype(str).str.isdigit()].iloc[:, [0, 4]].astype(float)
        P.columns = ['t', 'prem']; P.index = pd.to_datetime(P.t.astype('int64'), unit='ms', utc=True) + pd.Timedelta(minutes=5); P = P[~P.index.duplicated()].sort_index().prem
        Q = pd.DataFrame({'prem': P, 'prem_chg12': P - P.shift(12), 'prem_z': (P - P.rolling(2016, min_periods=500).mean()) / P.rolling(2016, min_periods=500).std()})
        X = X.join(Q, how='outer')
    X = X.replace([np.inf, -np.inf], np.nan).astype('float32'); X.index.name = 'known'; X.reset_index().to_parquet(os.path.join(OUT, f'{coin}.parquet'), index=False)
    return coin, len(X)
if __name__ == '__main__':
    with ProcessPoolExecutor(6) as ex:
        for coin, n in ex.map(build, COINS): print(coin, n, flush=True)
