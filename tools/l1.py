"""L1: liquidation cascades, tick-level, six coins, LEARN 2025-07-01 -> 2026-02-15 (exam sealed). Spec: specs/L1-liquidation-cascades-alts.md"""
import numpy as np, pandas as pd, glob, sys, os
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
K = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\d2\k1m'; MIN = 60_000; H = 3_600_000
L0 = int(pd.Timestamp('2025-07-01', tz='UTC').value // 10**6); L1 = int(pd.Timestamp('2026-02-15', tz='UTC').value // 10**6)
HZ = (5, 15, 30, 60, 240, 1440)
lines = []
def P(s=''): lines.append(s); print(s, flush=True)
P("# L1 results — liquidation cascades, Binance futures forced orders, LEARN 2025-07-01 -> 2026-02-15 (exam sealed). bp")
allev = []; allctl = []
for coin in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT'):
    fs = sorted(glob.glob(f'liq/{coin}_*.parquet')); liq = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    liq['ts'] = pd.to_numeric(liq['event_time']); liq['notional'] = pd.to_numeric(liq['price']) * pd.to_numeric(liq['quantity'])
    liq['m'] = (liq.ts // MIN) * MIN
    sell = liq[liq.side == 'SELL'].groupby('m').notional.sum(); buy = liq[liq.side == 'BUY'].groupby('m').notional.sum()   # SELL = longs liquidated
    bn = pd.read_parquet(f'{K}/{coin}.parquet'); bn = bn[(bn.ts >= L0 - 35 * 86_400_000) & (bn.ts < L1 + 3 * 86_400_000)].set_index('ts').sort_index()
    if len(bn) == 0: P(f"{coin}: no price data"); continue
    idx = bn.index; cl = bn['close']; op = bn['open']; hi = bn['high']; lo = bn['low']
    S = sell.reindex(idx, fill_value=0.0); B = buy.reindex(idx, fill_value=0.0)
    S15 = S.rolling(15).sum(); B15 = B.rolling(15).sum()
    b = (idx.values // H) * H; g = bn.groupby(b); o1 = pd.DataFrame({'h': g['high'].max(), 'l': g['low'].min(), 'c': g['close'].last()}); pc = o1.c.shift(1)
    tr = np.maximum(o1.h - o1.l, np.maximum((o1.h - pc).abs(), (o1.l - pc).abs())); atr1 = tr.ewm(alpha=1 / 14, adjust=False).mean(); atr1.index = atr1.index + H
    atr = atr1.reindex(idx, method='ffill')
    mv15 = cl / cl.shift(15) - 1
    thrS = S15.shift(1).rolling(30 * 1440, min_periods=10 * 1440).quantile(0.99); thrB = B15.shift(1).rolling(30 * 1440, min_periods=10 * 1440).quantile(0.99)
    week = pd.Series(pd.to_datetime(idx, unit='ms').to_period('W'), index=idx)
    drift = {z: ((cl.shift(-z) / cl - 1) * 1e4).groupby(week).mean() for z in HZ}
    clv = cl.values; opv = op.values; hiv = hi.values; lov = lo.values; pos = {t: i for i, t in enumerate(idx.values)}
    ev = []; ctl = []; lastS = lastB = -10**15; lastC = -10**15
    bigmove = (mv15.abs() >= 1.5 * atr / cl)
    for i in range(30 * 1440, len(idx) - 1500):
        t = idx[i]
        if t < L0 or t >= L1: continue
        a = atr.iloc[i]
        if not a > 0: continue
        down = S15.iloc[i] >= thrS.iloc[i] and mv15.iloc[i] <= -1.5 * a / clv[i]   # longs liquidated, price fell
        up = B15.iloc[i] >= thrB.iloc[i] and mv15.iloc[i] >= 1.5 * a / clv[i]      # shorts liquidated, price rose
        if down and t >= lastS + 2 * H: side = -1; lastS = t
        elif up and t >= lastB + 2 * H: side = 1; lastB = t
        else:
            if bigmove.iloc[i] and not (S15.iloc[i] >= thrS.iloc[i] or B15.iloc[i] >= thrB.iloc[i]) and t >= lastC + 2 * H:   # control: big move, no liq spike
                lastC = t; s0 = -1 if mv15.iloc[i] < 0 else 1; E = opv[i + 1]
                r = dict(coin=coin[:-4], ts=t, side=s0)
                for z in HZ: r[f'reb{z}'] = -s0 * (clv[i + z] / E - 1) * 1e4 + s0 * drift[z][week.iloc[i]]
                ctl.append(r)
            continue
        E = opv[i + 1]; r = dict(coin=coin[:-4], ts=t, side=side, size=S15.iloc[i] if side == -1 else B15.iloc[i], move=mv15.iloc[i] * 1e4)
        for z in HZ: r[f'reb{z}'] = -side * (clv[i + z] / E - 1) * 1e4 + side * drift[z][week.iloc[i]]   # REBOUND = against the cascade, excess over weekly drift
        ev.append(r)
    e = pd.DataFrame(ev); c = pd.DataFrame(ctl); allev.append(e); allctl.append(c)
    def ci(g, col): lo_, hi_, p = d4.boot(g[col].values, g.ts.values); return f"{g[col].mean():+5.0f} [{lo_:+.0f},{hi_:+.0f}]"
    P(f"\n## {coin[:-4]}: cascades {len(e)} (longs liquidated {int((e.side==-1).sum())}, shorts {int((e.side==1).sum())}) | median 15-min liquidation ${e['size'].median()/1e6:.1f}M, median move {e.move.abs().median():.0f}bp | control big-moves-without-spike {len(c)}")
    if len(e) >= 15:
        P("  REBOUND (against the cascade), excess: " + ' | '.join(f"{z:>4}m {ci(e, f'reb{z}')}" for z in HZ))
        if len(c) >= 15: P("  control, same measure:              " + ' | '.join(f"{z:>4}m {c[f'reb{z}'].mean():+5.0f}" for z in HZ))
E = pd.concat(allev); C = pd.concat(allctl)
def ci(g, col): lo_, hi_, p = d4.boot(g[col].values, g.ts.values); return f"{g[col].mean():+5.0f} [{lo_:+.0f},{hi_:+.0f}]"
P(f"\n## POOLED six coins: cascades {len(E)} | control {len(C)}")
P("  REBOUND excess: " + ' | '.join(f"{z:>4}m {ci(E, f'reb{z}')}" for z in HZ))
P("  control       : " + ' | '.join(f"{z:>4}m {ci(C, f'reb{z}')}" for z in HZ))
P("  CONTINUATION (sign flipped) = negatives of the above.")
for lab, g in (('longs liquidated (buy the dip)', E[E.side == -1]), ('shorts liquidated (sell the rip)', E[E.side == 1])):
    P(f"  {lab:32s} n={len(g):4d}: " + ' | '.join(f"{z:>4}m {ci(g, f'reb{z}')}" for z in HZ))
E['szq'] = pd.qcut(E['size'].rank(method='first'), 3, labels=['small', 'mid', 'large'])
P("  by cascade size tercile, rebound 60m / 240m: " + ' | '.join(f"{k}: {v60:+.0f} / {v240:+.0f}" for (k, v60), (_, v240) in zip(E.groupby('szq', observed=True).reb60.mean().items(), E.groupby('szq', observed=True).reb240.mean().items())))
P("  by coin, rebound 60m: " + ' '.join(f"{k} {v:+.0f}" for k, v in E.groupby('coin').reb60.mean().items()) + " | 240m: " + ' '.join(f"{k} {v:+.0f}" for k, v in E.groupby('coin').reb240.mean().items()))
E.to_csv('l1_events.csv', index=False); C.to_csv('l1_control.csv', index=False)
open(r'C:\Users\you\Desktop\TradeDeck\specs\L1-results.md', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
