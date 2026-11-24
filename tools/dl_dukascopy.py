"""Download Dukascopy free 1-minute BID candles (daily bi5 files) -> %LOCALAPPDATA%/TradeDeck/Data/dukascopy/<PAIR>_1m.parquet.
Usage: python dl_dukascopy.py USDCAD,USDCHF 2012-01-01 2026-09-30"""
import os, sys, struct, lzma, datetime as dt, urllib.request, concurrent.futures as cf, pandas as pd
OUT = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/dukascopy/'
def day(pair, d):
    url = f'https://datafeed.dukascopy.com/datafeed/{pair}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5'
    for _ in range(4):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30).read(); break
        except Exception: raw = None
    if not raw: return []
    b = lzma.decompress(raw); pt = 1e3 if pair.endswith('JPY') else 1e5; base = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()
    rows = []
    for i in range(0, len(b) - 23, 24):
        t, o, c, l, h, v = struct.unpack('>5if', b[i:i + 24])
        if v > 0: rows.append(((base + t) * 1000, o / pt, h / pt, l / pt, c / pt))
    return rows
if __name__ == '__main__':
    pairs = sys.argv[1].split(','); a, z = pd.Timestamp(sys.argv[2]).date(), pd.Timestamp(sys.argv[3]).date(); os.makedirs(OUT, exist_ok=True)
    days = [a + dt.timedelta(n) for n in range((z - a).days + 1)]; days = [d for d in days if d.weekday() != 5]
    for p in pairs:
        f = OUT + f'{p}_1m.parquet'
        if os.path.exists(f): print(p, 'exists'); continue
        with cf.ThreadPoolExecutor(16) as ex: res = list(ex.map(lambda d: day(p, d), days))
        df = pd.DataFrame([r for rr in res for r in rr], columns=['t', 'o', 'h', 'l', 'c']).sort_values('t')
        df.to_parquet(f, index=False); print(p, len(df), 'bars', flush=True)
