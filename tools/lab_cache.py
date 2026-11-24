"""Edge Lab data cache: 110 Binance USDT-M perps, 15m -> UTC daily OHLCV parquet (one file for the panel)."""
import os, sqlite3, pandas as pd
DB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\history.sqlite'); OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab')
os.makedirs(OUT, exist_ok=True)
c = sqlite3.connect(DB)
coins = [r[0] for r in c.execute("select distinct market from candles where source='binance_um' and interval='15m'")]
parts = []
for i, m in enumerate(coins):
    d = pd.read_sql("select timestamp t, open o, high h, low l, close c, volume v from candles where source='binance_um' and market=? and interval='15m' order by t", c, params=(m,))
    d.index = pd.to_datetime(d.t, unit='ms', utc=True)
    D = d.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum', 't': 'size'}).rename(columns={'t': 'nbars'})
    D = D[D.nbars >= 90].drop(columns='nbars'); D['coin'] = m; parts.append(D)
    print(i, m, len(D), flush=True)
P = pd.concat(parts); P.index.name = 'date'; P.reset_index().to_parquet(os.path.join(OUT, 'daily.parquet'), index=False)
print('saved', len(P))
