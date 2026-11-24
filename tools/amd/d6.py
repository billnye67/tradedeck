"""D6 costed: breakout of the user's close-band box. Entry at the next 1m open after the break close; stop = far edge of the band (and 1 ATR
reported); time exits 8h / 24h; a close back inside the band within 3 bars also exits (the poke case) at that bar's close.
Costs 10/16bp RT + 5bp per UTC midnight. 1h box from 1m Binance; BTC/ETH/XRP; learn 2021-2023 (box fitted on these marks)."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
K = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\d2\k1m'
H = 3_600_000; MIN = 60_000; DAY = 86_400_000; N, QLO, QHI, HCAP = 36, 0.05, 0.95, 3.0
ERA = (sys.argv[1] if len(sys.argv) > 1 else 'learn'); L0, L1 = (('2021-01-01', '2024-01-01') if ERA == 'learn' else ('2024-01-01', '2026-09-01'))
L0 = int(pd.Timestamp(L0, tz='UTC').value // 10**6); L1 = int(pd.Timestamp(L1, tz='UTC').value // 10**6)
rows = []
for coin in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT'):
    bn = pd.read_parquet(f'{K}/{coin}.parquet'); bn = bn[(bn.ts >= L0 - 40 * H) & (bn.ts < L1)].set_index('ts').sort_index()
    b = (bn.index.values // H) * H; g = bn.groupby(b); o = pd.DataFrame({'h': g['high'].max(), 'l': g['low'].min(), 'c': g['close'].last()})
    pc = o.c.shift(1); tr = np.maximum(o.h - o.l, np.maximum((o.h - pc).abs(), (o.l - pc).abs())); o['atr'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    ht = o.index.values; c = o.c.values; atr = o.atr.values
    op = bn['open'].values; hi = bn['high'].values; lo = bn['low'].values; cl = bn['close'].values; idx = bn.index.values; pos = {t: i for i, t in enumerate(idx)}
    mon = pd.Series(pd.to_datetime(idx, unit='ms').to_period('M'), index=idx)
    last = -10**9
    for i in range(N + 3, len(c) - 30):
        if ht[i] < L0 or i < last + 12: continue
        w = c[i - N:i]; top = np.quantile(w, QHI); bot = np.quantile(w, QLO); a = atr[i]
        if not a > 0 or (top - bot) / a > HCAP: continue
        if c[i] > top: s = 1
        elif c[i] < bot: s = -1
        else: continue
        last = i
        t_in = ht[i] + H                      # the 1m bar opening right after the hourly close
        k0 = pos.get(t_in)
        if k0 is None or k0 + 24 * 60 + 2 >= len(idx): continue
        E = op[k0]; far = bot if s == 1 else top
        for stopKind, stop in (('far-edge', far), ('1 ATR', E - s * a)):
            r = dict(coin=coin, ts=t_in, side=s, stopKind=stopKind, R=abs(E - stop) / E * 1e4, cheat8=s * (cl[k0 + 8 * 60 - 1] / E - 1) * 1e4)
            for hold in (8, 24):
                x = cl[k0 + hold * 60 - 1]; kx = k0 + hold * 60 - 1; how = 'time'
                for k in range(k0, k0 + hold * 60):
                    if (lo[k] <= stop) if s == 1 else (hi[k] >= stop):
                        x = op[k] if ((op[k] <= stop) if s == 1 else (op[k] >= stop)) else stop; kx = k; how = 'stop'; break
                    # poke rule: an hourly close back inside the band within 3 bars exits at that close
                    if k > k0 and (idx[k] + MIN) % H == 0 and (idx[k] - t_in) // H < 3 and bot < cl[k] < top:
                        x = cl[k]; kx = k; how = 'back-inside'; break
                gr = s * (x / E - 1) * 1e4; n = (idx[kx] // DAY) - (idx[k0] // DAY)
                r[f'gross{hold}'] = gr; r[f'net{hold}'] = gr - 10 - 5 * n; r[f'stress{hold}'] = gr - 16 - 5 * n; r[f'how{hold}'] = how; r[f'r{hold}'] = (gr - 10 - 5 * n) / r['R']
            rows.append(r)
d = pd.DataFrame(rows)
def pf(x): w = x[x > 0].sum(); L = -x[x < 0].sum(); return w / L if L > 0 else np.inf
lines = []
def P(s=''): lines.append(s); print(s, flush=True)
months = (L1 - L0) / (30.44 * DAY)
P(f"# D6 costed — {ERA.upper()} era, breakout of the user's box, 1h, BTC/ETH/XRP, costs 10/16bp + 5bp/night")
for stopKind in ('far-edge', '1 ATR'):
    g = d[d.stopKind == stopKind]
    P(f"\n## stop = {stopKind} (median {g.R.median():.0f}bp) | n={len(g)} = {len(g) / months:.1f} trades/month | cheat check 8h: lookahead {g.cheat8.mean():+.1f} vs gross-no-stop proxy")
    for hold in (8, 24):
        x = g[f'net{hold}']; yrs = pd.to_datetime(g.ts, unit='ms').dt.year; lo_, hi_, p = d4.boot(x.values, g.ts.values); ex10 = x.sort_values().iloc[:-10].mean()
        eq = g.sort_values('ts')[f'r{hold}'].cumsum(); dd = (eq - eq.cummax()).min()
        P(f"hold {hold:>2}h: gross {g[f'gross{hold}'].mean():+6.1f} | net {x.mean():+6.1f} [{lo_:+.0f},{hi_:+.0f}] stress {g[f'stress{hold}'].mean():+6.1f} | win {(x > 0).mean() * 100:.0f}% PF {pf(x):.2f} | exits " + ' '.join(f"{k} {v:.0f}%" for k, v in (g[f'how{hold}'].value_counts(normalize=True) * 100).items()) + f" | ex10 {ex10:+.1f} | mean R {g[f'r{hold}'].mean():+.3f} maxDD {dd:.1f}R | R/mo {g[f'r{hold}'].sum() / months:+.2f}")
        P("          by year " + ' '.join(f"{y}:{v:+.0f}" for y, v in x.groupby(yrs).mean().items()) + " | by coin " + ' '.join(f"{k[:3]} {v:+.0f}" for k, v in g.groupby('coin')[f'net{hold}'].mean().items()) + " | by side " + ' '.join(f"{'long' if k == 1 else 'short'} {v:+.0f}" for k, v in g.groupby('side')[f'net{hold}'].mean().items()))
        gates = {'net>=15': x.mean() >= 15, 'CI>0': lo_ > 0, '3/3 coins': (g.groupby('coin')[f'net{hold}'].mean() > 0).all(), 'all years': (x.groupby(yrs).mean() > 0).all(), 'both sides': (g.groupby('side')[f'net{hold}'].mean() > 0).all(), 'ex10>0': ex10 > 0, 'n>=300': len(g) >= 300}
        P("          GATES: " + ' | '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in gates.items()) + f" -> {'PASS' if all(gates.values()) else 'FAIL'}")
open(rf'C:\Users\you\Desktop\TradeDeck\specs\D6-results-{ERA}.md', 'w', encoding='utf-8').write('\n'.join(lines) + '\n'); d.to_csv(f'd6_{ERA}_trades.csv', index=False)
