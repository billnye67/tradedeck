"""TU: Binance USDT-M perp 1h klines (data.binance.vision monthly + daily) for the unlock tokens -> lab/tu/k1h/<SYM>.parquet."""
import os, sys, io, zipfile, time, requests, pandas as pd
from concurrent.futures import ThreadPoolExecutor
OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\tu\k1h'); os.makedirs(OUT, exist_ok=True); H = {'User-Agent': 'Mozilla/5.0'}
def get(url):
    for k in range(4):
        try:
            r = requests.get(url, headers=H, timeout=60)
            if r.status_code == 404: return None
            if r.ok: return r.content
        except Exception: pass
        time.sleep(3)
    return None
def one(sym):
    dst = os.path.join(OUT, f'{sym}.parquet')
    if os.path.exists(dst): return sym, 'cached'
    parts = []
    months = pd.period_range('2022-01', '2026-09', freq='M')
    for m in months:
        b = get(f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip')
        if b: parts.append(pd.read_csv(io.BytesIO(zipfile.ZipFile(io.BytesIO(b)).read(zipfile.ZipFile(io.BytesIO(b)).namelist()[0])), header=None, low_memory=False))
    for d in pd.date_range('2026-10-01', '2026-10-16'):
        b = get(f'https://data.binance.vision/data/futures/um/daily/klines/{sym}/1h/{sym}-1h-{d:%Y-%m-%d}.zip')
        if b: parts.append(pd.read_csv(io.BytesIO(zipfile.ZipFile(io.BytesIO(b)).read(zipfile.ZipFile(io.BytesIO(b)).namelist()[0])), header=None, low_memory=False))
    if not parts: return sym, 'none'
    k = pd.concat(parts); k = k[k.iloc[:, 0].astype(str).str.isdigit()].iloc[:, :6].astype(float); k.columns = ['ts', 'open', 'high', 'low', 'close', 'vol']
    k['ts'] = k.ts.astype('int64'); k = k.drop_duplicates('ts').sort_values('ts'); k.to_parquet(dst, index=False); return sym, len(k)
if __name__ == '__main__':
    syms = [s.strip() for s in open(sys.argv[1]).read().split() if s.strip()]
    with ThreadPoolExecutor(8) as ex:
        for s, n in ex.map(one, syms): print(s, n, flush=True)
