"""Download UTC daily candles for EVERY Binance USDT-M perpetual that ever existed (incl. delisted) from data.binance.vision monthly
1d archives, so lab tests are not survivor-biased. Output: %LOCALAPPDATA%/TradeDeck/Data/lab/daily_all.parquet (same columns as daily.parquet).
Writes progress to TradeDeck/Web/lab/live.json for the Lab card."""
import os, io, re, json, time, zipfile, urllib.request, pandas as pd
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); CACHE = os.path.join(OUT, 'raw1d'); os.makedirs(CACHE, exist_ok=True)
LIVE = [os.path.join(ROOT, 'TradeDeck', 'Web', 'lab', 'live.json'), os.path.join(ROOT, 'TradeDeck', 'bin', 'Debug', 'net8.0-windows', 'Web', 'lab', 'live.json')]
S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'; UA = {'User-Agent': 'Mozilla/5.0'}
log = []
def status(pct, msg, done=False):
    log.append(f"{time.strftime('%H:%M:%S')}  {msg}"); print(msg, flush=True)
    for p in LIVE:
        if os.path.isdir(os.path.dirname(p)): json.dump(dict(name='Downloading every coin that ever existed (incl. dead ones)', step='Download', pct=round(pct, 1), msg=msg, log=log[-14:], extra={}, done=done, t=time.strftime('%Y-%m-%d %H:%M:%S')), open(p, 'w'))
def get(url, tries=4):
    for k in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except Exception:
            time.sleep(2 * (k + 1))
    return None
def listing(prefix):
    keys, marker = [], ''
    while True:
        x = get(f'{S3}?delimiter=/&prefix={prefix}' + (f'&marker={marker}' if marker else ''))
        if x is None: return keys
        t = x.decode(); keys += re.findall(r'<(?:Prefix|Key)>([^<]+)</(?:Prefix|Key)>', t)
        if '<IsTruncated>true</IsTruncated>' not in t: return keys
        marker = re.findall(r'<NextMarker>([^<]+)</NextMarker>', t)[-1] if '<NextMarker>' in t else keys[-1]
status(1, 'Listing every USDT perpetual Binance ever had...')
syms = sorted({k.split('/')[-2] for k in listing('data/futures/um/monthly/klines/') if k.endswith('/') and k.split('/')[-2].endswith('USDT')})
status(3, f'{len(syms)} USDT perpetuals found (alive and dead). Listing their monthly files...')
def files(sym):
    return [k for k in listing(f'data/futures/um/monthly/klines/{sym}/1d/') if k.endswith('.zip')]
with ThreadPoolExecutor(12) as ex: allf = [f for fl in ex.map(files, syms) for f in fl]
status(10, f'{len(allf):,} monthly files to fetch.')
def fetch(key):
    dst = os.path.join(CACHE, key.split('/')[-1].replace('.zip', '.csv'))
    if os.path.exists(dst): return dst
    b = get(f'https://data.binance.vision/{key}')
    if b is None: return None
    with zipfile.ZipFile(io.BytesIO(b)) as z: open(dst, 'wb').write(z.read(z.namelist()[0]))
    return dst
done = 0
with ThreadPoolExecutor(12) as ex:
    for i, _ in enumerate(ex.map(fetch, allf)):
        if i % 500 == 0: status(10 + 85 * i / max(1, len(allf)), f'Fetched {i:,} / {len(allf):,} monthly files...')
parts = []
for f in os.listdir(CACHE):
    sym = f.split('-1d-')[0]
    d = pd.read_csv(os.path.join(CACHE, f), header=None)
    if not str(d.iloc[0, 0]).isdigit(): d = d.iloc[1:]
    d = d.iloc[:, :6].astype(float); d.columns = ['t', 'o', 'h', 'l', 'c', 'v']; d['coin'] = sym; parts.append(d)
A = pd.concat(parts).drop_duplicates(['coin', 't']); A['date'] = pd.to_datetime(A.t, unit='ms', utc=True)
A = A[['date', 'o', 'h', 'l', 'c', 'v', 'coin']].sort_values(['coin', 'date'])
A.to_parquet(os.path.join(OUT, 'daily_all.parquet'), index=False)
status(100, f'DONE: {A.coin.nunique()} coins, {len(A):,} coin-days saved (dead coins included).', done=True)
