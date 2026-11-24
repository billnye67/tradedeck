"""Engine v2 data: for the 110 Binance USDT-M perps in history.sqlite, download from data.binance.vision
  (1) 5m klines monthly (has taker-buy volume = aggressive buyers vs sellers, the CVD/delta input),
  (2) funding rate monthly, (3) daily 'metrics' files (5-min open interest, long/short ratios, taker buy/sell ratio).
Raw CSVs cached under %LOCALAPPDATA%/TradeDeck/Data/lab/flow/<kind>/; resumable. Progress -> Web/lab/live.json (plain words)."""
import os, io, re, json, time, zipfile, sqlite3, urllib.request
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); FLOW = os.path.join(LAB, 'flow')
LIVE = [os.path.join(ROOT, 'TradeDeck', 'Web', 'lab', 'live.json'), os.path.join(ROOT, 'TradeDeck', 'bin', 'Debug', 'net8.0-windows', 'Web', 'lab', 'live.json')]
S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'; UA = {'User-Agent': 'Mozilla/5.0'}
log = []
def status(pct, msg, done=False):
    log.append(f"{time.strftime('%H:%M:%S')}  {msg}"); print(msg, flush=True)
    for p in LIVE:
        if os.path.isdir(os.path.dirname(p)): json.dump(dict(name='Downloading order-book depth (heat map data) + premium for 23 coins', step='Download', pct=round(pct, 1), msg=msg, log=log[-14:], extra={}, done=done, t=time.strftime('%Y-%m-%d %H:%M:%S')), open(p, 'w'))
def get(url, tries=4):
    for k in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except Exception: time.sleep(2 * (k + 1))
    return None
def listing(prefix):
    keys, marker = [], ''
    while True:
        x = get(f'{S3}?prefix={prefix}' + (f'&marker={marker}' if marker else ''))
        if x is None: return keys
        t = x.decode(); ks = re.findall(r'<Key>([^<]+)</Key>', t); keys += ks
        if '<IsTruncated>true</IsTruncated>' not in t or not ks: return keys
        marker = ks[-1]
c = sqlite3.connect(os.path.join(os.path.dirname(LAB), 'history.sqlite'))
coins = [x + 'USDT' for x in 'BTC ETH SOL ORDI AVAX DOGE XRP ADA OP TIA BNB LINK INJ ARB DOT SEI FIL NEAR JTO WLD ICP ATOM DYDX'.split()]; c.close()
KINDS = [('bookDepth', 'data/futures/um/daily/bookDepth/{s}/'), ('premium5m', 'data/futures/um/monthly/premiumIndexKlines/{s}/5m/')]
status(1, f'Listing files for {len(coins)} coins...')
jobs = []
for kind, pre in KINDS:
    os.makedirs(os.path.join(FLOW, kind), exist_ok=True)
    with ThreadPoolExecutor(12) as ex:
        for fl in ex.map(lambda s: listing(pre.format(s=s)), coins): jobs += [(kind, k) for k in fl if k.endswith('.zip')]
    status(2 + 3 * len({k for k, _ in jobs}), f'{kind}: listed. {len(jobs):,} files so far.')
def fetch(job):
    kind, key = job; dst = os.path.join(FLOW, kind, key.split('/')[-1].replace('.zip', '.csv'))
    if os.path.exists(dst): return 1
    b = get(f'https://data.binance.vision/{key}')
    if b is None: return 0
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z: open(dst + '.tmp', 'wb').write(z.read(z.namelist()[0]))
        os.replace(dst + '.tmp', dst); return 1
    except Exception: return 0
ok = 0; t0 = time.time()
with ThreadPoolExecutor(16) as ex:
    for i, r in enumerate(ex.map(fetch, jobs)):
        ok += r
        if i % 2000 == 0:
            el = time.time() - t0; eta = el / max(1, i) * (len(jobs) - i) / 60
            status(12 + 87 * i / len(jobs), f'Downloaded {i:,} of {len(jobs):,} files (about {eta:.0f} min left).')
status(100, f'DONE: {ok:,} of {len(jobs):,} files saved (5-min buy/sell flow, funding, open interest) for {len(coins)} coins.', done=True)
