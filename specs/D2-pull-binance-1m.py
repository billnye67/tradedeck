"""D2 data pull: Binance USDT-M 1-minute klines (monthly archives) for BTC and every Broker crypto symbol that exists on Binance USDT-M.
Fields kept: open high low close volume quote_volume trades taker_buy_base taker_buy_quote. Earliest archive month -> 2026-08."""
import os, io, zipfile, time, json, urllib.request, concurrent.futures as cf
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, 'k1m'); os.makedirs(OUTD, exist_ok=True)
BASE = 'https://data.binance.vision/data/futures/um/monthly/klines'
VT = 'AAVE ADA AERO ALGO APE APT ARB ARC AR ASTER ATOM AVAX AXS BARD BCH BEAT BERA BNB BSV BTC CAKE CC CHZ CRV DASH DOGE DOT DYDX EIGEN ENA ETC ETHFI ETH FARTCOIN FET FF FIL GRAM GRASS HBAR HYPE ICP INJ JTO JUP KAITO KAS KITE LAB LDO LINEA LINK LIT LTC MON MORPHO NEAR ONDO OP ORDI PENDLE PENGU PEPE PIEVERSE POL PUMP PYTH QNT RAVE RENDER RE SAND SEI SHIB SKYAI SKY SLX SOL STRK SUI SUN TAC TAO TIA TRUMP TRX UB UNI VELVET VET VIRTUAL VVV WIF WLD WLFI XLM XMR XPL XRP ZEC ZRO'.split()
# Binance uses 1000x prefixes for some tiny-price coins
ALIASES = {'PEPE': ['1000PEPEUSDT', 'PEPEUSDT'], 'SHIB': ['1000SHIBUSDT', 'SHIBUSDT'], 'FLOKI': ['1000FLOKIUSDT'], 'BONK': ['1000BONKUSDT'], 'SATS': ['1000SATSUSDT']}
MONTHS = pd.period_range('2019-09', '2026-08', freq='M')
def fetch(url):
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(2 + 3 * k)
        except Exception: time.sleep(2 + 3 * k)
    return b'ERR'
def exists(sym):
    """probe the newest month to see if the symbol exists on Binance USDT-M."""
    for m in ['2026-08', '2026-07']:
        b = fetch(f'{BASE}/{sym}/1m/{sym}-1m-{m}.zip')
        if b not in (None, b'ERR'): return True
    return False
def pull(sym):
    out = os.path.join(OUTD, f'{sym}.parquet')
    if os.path.exists(out): return sym, 'cached'
    frames = []; first = None
    def one(m):
        b = fetch(f'{BASE}/{sym}/1m/{sym}-1m-{m}.zip')
        if b is None or b == b'ERR': return m, None
        z = zipfile.ZipFile(io.BytesIO(b)); df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4, 5, 7, 8, 9, 10])
        df.columns = ['ts', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote']
        for c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['ts']); df['ts'] = df['ts'].astype('int64')
        if df['ts'].max() > 1e14: df['ts'] = df['ts'] // 1000
        return m, df
    with cf.ThreadPoolExecutor(6) as ex:
        for m, df in ex.map(one, [str(x) for x in MONTHS]):
            if df is not None and len(df): frames.append(df); first = first or m
    if not frames: return sym, 'no data'
    d = pd.concat(frames, ignore_index=True).sort_values('ts').drop_duplicates('ts'); d.to_parquet(out)
    return sym, f'{len(d):,} rows from {first}'
if __name__ == '__main__':
    syms = []
    for v in VT:
        cands = ALIASES.get(v, [f'{v}USDT'])
        hit = next((c for c in cands if exists(c)), None)
        print(f'{v}: {hit or "NOT on Binance USDT-M"}', flush=True)
        if hit: syms.append(hit)
    json.dump(syms, open(os.path.join(HERE, 'universe_binance.json'), 'w'))
    print(f'{len(syms)} symbols on both lists', flush=True)
    # BTC first, then the rest
    syms = ['BTCUSDT'] + [s for s in syms if s != 'BTCUSDT']
    for s in syms: print('pull', pull(s), flush=True)
