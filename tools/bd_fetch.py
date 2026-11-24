"""Download Binance Vision free futures order-book depth (bookDepth) daily files, 2023-01-01 .. yesterday, BTC/ETH/XRP.
Saves the zips under %LOCALAPPDATA%/TradeDeck/Data/lab/bookdepth/<COIN>/ (skips files already there). Read-only public data."""
import os, sys, urllib.request, datetime as dt
OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\bookdepth')
COINS = sys.argv[1:] or ['BTCUSDT', 'ETHUSDT', 'XRPUSDT']
d0, d1 = dt.date(2023, 1, 1), dt.date.today() - dt.timedelta(days=1)
for c in COINS:
    os.makedirs(os.path.join(OUT, c), exist_ok=True); got = miss = 0; d = d0
    while d <= d1:
        f = f'{c}-bookDepth-{d}.zip'; p = os.path.join(OUT, c, f)
        if not os.path.exists(p):
            try:
                urllib.request.urlretrieve(f'https://data.binance.vision/data/futures/um/daily/bookDepth/{c}/{f}', p + '.part'); os.replace(p + '.part', p); got += 1
            except Exception:
                miss += 1
        d += dt.timedelta(days=1)
    print(c, 'downloaded', got, 'missing', miss, flush=True)
