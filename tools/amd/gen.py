"""AMD labelling set: LOOSE mechanical candidates (box -> poke out one side -> close back inside), shown up to the re-entry close
with the future hidden, for the user to grade Y/N/U. Learn era only (2021-2023), BTC/ETH/XRP, hourly and 15m boxes."""
import numpy as np, pandas as pd, json, random
K = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\d2\k1m'
OUT = r'C:\Users\you\Desktop\TradeDeck\reviews'
MIN = 60_000
L0 = int(pd.Timestamp('2021-01-01', tz='UTC').value // 10**6); L1 = int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6)
random.seed(11)


def bars(c, n):
    d = pd.read_parquet(f'{K}/{c}.parquet'); d = d[(d.ts >= L0 - 30 * 86_400_000) & (d.ts < L1)].set_index('ts').sort_index()
    b = (d.index.values // (n * MIN)) * n * MIN; g = d.groupby(b)
    o = pd.DataFrame({'open': g['open'].first(), 'high': g['high'].max(), 'low': g['low'].min(), 'close': g['close'].last()})
    pc = o['close'].shift(1); tr = np.maximum(o['high'] - o['low'], np.maximum((o['high'] - pc).abs(), (o['low'] - pc).abs()))
    o['atr'] = tr.ewm(alpha=1 / 14, adjust=False).mean(); return o


cands = []
for coin in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
    for tf, Ns, span_atr, REW in ((60, (12, 24, 48), 4.0, 24), (15, (32, 64, 128), 5.0, 48)):
        o = bars(coin, tf); h, l, cl, atr, ts = o['high'].values, o['low'].values, o['close'].values, o['atr'].values, o.index.values
        T = len(o); i = max(Ns) + 1; last = -10**9
        while i < T - REW - 200:
            if i < last + 8 or not atr[i] > 0 or ts[i] < L0: i += 1; continue
            for N in Ns:
                top = h[i - N:i].max(); bot = l[i - N:i].min()
                if (top - bot) / atr[i] <= span_atr and (cl[i] > top or cl[i] < bot):
                    poke = 1 if cl[i] > top else -1
                    seg = cl[i + 1:i + 1 + REW]; back = np.where((seg < top) & (seg > bot))[0]
                    if len(back):
                        j = i + 1 + back[0]
                        cands.append(dict(coin=coin, tf=tf, N=N, i=i, j=j, top=float(top), bot=float(bot), poke=poke,
                                          ext=float(h[i:j + 1].max() if poke == 1 else l[i:j + 1].min())))
                        last = i
                    break
            i += 1
print('loose candidates:', len(cands))
pick = []
for coin in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
    for tf in (60, 15):
        pool = [c for c in cands if c['coin'] == coin and c['tf'] == tf]; random.shuffle(pool); pick += pool[:10]
random.shuffle(pick)
payload = []
for k, c in enumerate(pick):
    o = bars(c['coin'], c['tf']); ts = o.index.values
    s0 = max(0, c['i'] - c['N'] - 40); s1 = min(len(o) - 1, c['j'] + 96)   # future shown only after grading
    sl = o.iloc[s0:s1 + 1]
    payload.append(dict(id=k, coin=c['coin'][:3], tf=f"{c['tf']}m", boxBars=int(c['N']), top=c['top'], bot=c['bot'], poke='UP' if c['poke'] == 1 else 'DOWN',
                        pokeExtreme=c['ext'], boxStartRel=int(c['i'] - c['N'] - s0), breakRel=int(c['i'] - s0), reentryRel=int(c['j'] - s0),
                        whenUtc=pd.Timestamp(int(ts[c['j']]), unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                        candles=[[int(t), float(a), float(b), float(d), float(e)] for t, a, b, d, e in zip(ts[s0:s1 + 1], sl.open, sl.high, sl.low, sl.close)]))
json.dump(payload, open(f'{OUT}/amd-candidates.json', 'w'))
print('picked', len(payload), 'by coin/tf:', pd.Series([f"{p['coin']} {p['tf']}" for p in payload]).value_counts().to_dict())
