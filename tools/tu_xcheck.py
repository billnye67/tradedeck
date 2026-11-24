"""TU cross-check: do exam unlock dates from our calendar match DefiLlama's independent schedules (Wayback /unlocks snapshots)?"""
import os, re, json, glob, pandas as pd
D = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\tu'); X = os.path.join(D, 'xcheck')
GECKO = {'ARB': 'arbitrum', 'OP': 'optimism', 'APT': 'aptos', 'SUI': 'sui', 'STRK': 'starknet', 'TIA': 'celestia', 'IMX': 'immutable-x',
         'ZK': 'zksync', 'WLD': 'worldcoin-wld', 'AVAX': 'avalanche-2', 'DYDX': 'dydx-chain', 'ENA': 'ethena', 'JTO': 'jito-governance-token',
         'W': 'wormhole', 'PIXEL': 'pixels', 'ZRO': 'layerzero', 'SEI': 'sei-network', 'STX': 'blockstack', 'ID': 'space-id', 'ALT': 'altlayer',
         'MANTA': 'manta-network', 'PYTH': 'pyth-network', 'NEON': 'neon', 'YGG': 'yield-guild-games', 'AXS': 'axie-infinity', 'APE': 'apecoin'}
sched = {}
for f in sorted(glob.glob(os.path.join(X, 'dl_*.html'))):
    h = open(f, encoding='utf-8', errors='ignore').read(); m = re.search(r'__NEXT_DATA__[^>]*>(.*?)</script>', h, re.S)
    if not m: continue
    try: data = json.loads(m.group(1))['props']['pageProps']['data']
    except Exception: continue
    for p in data:
        g = p.get('gecko_id') or str(p.get('token', '')).replace('coingecko:', '')
        for ev in p.get('events') or []:
            if ev.get('timestamp') and not any(w in ev.get('description', '').lower() for w in ('linear', 'increased', 'per day', 'per week')): sched.setdefault(g, set()).add(int(ev['timestamp']) // 86400)
E = pd.read_csv(os.path.join(D, 'events.csv')); E = E[(E.usd >= 5e6) & (E['T'] >= pd.Timestamp('2024-07-01', tz='UTC').timestamp())]
E = E[E.symbol.isin(GECKO)].sort_values('T')
rows = []
for r in E.itertuples():
    s = sched.get(GECKO[r.symbol])
    if not s: continue
    d = r.T // 86400; rows.append(dict(symbol=r.symbol, date=pd.to_datetime(r.T, unit='s').strftime('%Y-%m-%d %H:%M'), match=any(abs(d - x) <= 1 for x in s)))
R = pd.DataFrame(rows); out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab', 'tu_xcheck.log')
smp = R.drop_duplicates('symbol').head(10) if len(R) else R
txt = f'checkable exam events (token in DefiLlama snapshots): {len(R)}; date within +/-1 day: {int(R.match.sum()) if len(R) else 0}/{len(R)}\nsample of 10 (one per token):\n{smp.to_string(index=False)}'
open(out, 'w', encoding='utf-8').write(txt + '\n'); print(txt)
