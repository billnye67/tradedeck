"""Download Binance UM futures daily metrics (5-min open interest etc.) and monthly 5m indexPriceKlines, Dec 2021 -> Aug 2026."""
import os, sys, io, zipfile, time, urllib.request, concurrent.futures as cf
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
BASE = 'https://data.binance.vision/data/futures/um'
def fetch(url):
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(2 + 2 * k)
        except Exception: time.sleep(2 + 2 * k)
    return b'ERR'
def metrics(coin):
    out = os.path.join(HERE, f'metrics_{coin}.parquet')
    if os.path.exists(out): return coin, 'cached'
    days = pd.date_range('2021-12-01', '2026-08-28'); frames = []; missing = []
    def one(d):
        b = fetch(f'{BASE}/daily/metrics/{coin}/{coin}-metrics-{d:%Y-%m-%d}.zip')
        if b is None or b == b'ERR': return d, None
        z = zipfile.ZipFile(io.BytesIO(b)); return d, pd.read_csv(z.open(z.namelist()[0]), usecols=['create_time', 'symbol', 'sum_open_interest', 'sum_open_interest_value'])
    with cf.ThreadPoolExecutor(8) as ex:
        for d, df in ex.map(one, days):
            if df is None: missing.append(str(d.date()))
            else: frames.append(df)
    m = pd.concat(frames, ignore_index=True); m['ts'] = pd.to_datetime(m['create_time'], utc=True).astype('int64') // 10**6
    m = m.sort_values('ts'); m.to_parquet(out)
    open(os.path.join(HERE, f'metrics_{coin}_missing.txt'), 'w').write('\n'.join(missing))
    return coin, f'{len(m)} rows, {len(missing)} missing days'
def index(coin):
    out = os.path.join(HERE, f'index_{coin}.parquet')
    if os.path.exists(out): return coin, 'cached'
    months = pd.period_range('2021-12', '2026-08', freq='M'); frames = []; missing = []
    for mth in months:
        b = fetch(f'{BASE}/monthly/indexPriceKlines/{coin}/5m/{coin}-5m-{mth}.zip')
        if b is None or b == b'ERR': missing.append(str(mth)); continue
        z = zipfile.ZipFile(io.BytesIO(b)); df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4])
        df.columns = ['ts', 'open', 'high', 'low', 'close']
        for col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(); df['ts'] = df['ts'].astype('int64')
        if df['ts'].max() > 1e14: df['ts'] = df['ts'] // 1000          # some archives are in microseconds
        frames.append(df)
    i = pd.concat(frames, ignore_index=True).sort_values('ts'); i.to_parquet(out)
    open(os.path.join(HERE, f'index_{coin}_missing.txt'), 'w').write('\n'.join(missing))
    return coin, f'{len(i)} rows, {len(missing)} missing months'
if __name__ == '__main__':
    for c in COINS: print('index', index(c), flush=True)
    for c in COINS: print('metrics', metrics(c), flush=True)
