"""Download FXCM public 1-minute candles (weekly csv.gz, bid+ask) -> MID candles
%LOCALAPPDATA%/TradeDeck/Data/fxcm_extra/<PAIR>_1m.parquet (t ms UTC, o h l c mid, spread = mean ask-bid close).
Usage: python dl_fxcm.py USDCAD,USDCHF 2012 2026"""
import os, sys, io, gzip, urllib.request, concurrent.futures as cf, pandas as pd
OUT = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/fxcm_extra/'
def week(pair, y, w):
    url = f'https://candledata.fxcorporate.com/m1/{pair}/{y}/{w}.csv.gz'
    for _ in range(3):
        try: raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'curl/8.5.0'}), timeout=60).read(); break
        except Exception as e:
            if '404' in str(e): return None
            raw = None
    if not raw: return None
    d = pd.read_csv(io.BytesIO(gzip.decompress(raw)))
    t = pd.to_datetime(d.DateTime, format='%m/%d/%Y %H:%M:%S.%f', utc=True)
    return pd.DataFrame({'t': t.dt.as_unit('ms').astype('int64'), 'o': (d.BidOpen + d.AskOpen) / 2, 'h': (d.BidHigh + d.AskHigh) / 2,
                         'l': (d.BidLow + d.AskLow) / 2, 'c': (d.BidClose + d.AskClose) / 2, 'spread': d.AskClose - d.BidClose})
if __name__ == '__main__':
    pairs = sys.argv[1].split(','); y0, y1 = int(sys.argv[2]), int(sys.argv[3]); os.makedirs(OUT, exist_ok=True)
    for p in pairs:
        f = OUT + f'{p}_1m.parquet'
        if os.path.exists(f): print(p, 'exists'); continue
        jobs = [(y, w) for y in range(y0, y1 + 1) for w in range(1, 54)]
        with cf.ThreadPoolExecutor(8) as ex: res = [r for r in ex.map(lambda j: week(p, *j), jobs) if r is not None]
        df = pd.concat(res).drop_duplicates('t').sort_values('t'); df.to_parquet(f, index=False)
        print(p, len(df), 'bars', pd.to_datetime(df.t.min(), unit='ms').date(), '->', pd.to_datetime(df.t.max(), unit='ms').date(), flush=True)
