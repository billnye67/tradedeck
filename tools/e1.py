"""E1: ETF flows. (a) diagnostic: price move in the US afternoon window on big-flow days. (b) tradeable: entry 02:00 UTC next day after a
big-flow day / streak; forward moves; excess over monthly drift. Learn half = first half of each series; exam half sealed (not printed).
Binance 1m from the scratchpad parquet (through 2026-08-31)."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\cbp'); import d4
K = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\d2\k1m'
H = 3_600_000; MIN = 60_000; DAY = 86_400_000; HZ = (4, 8, 18, 24, 48)
lines = []
def P(s=''): lines.append(s); print(s, flush=True)
P("# E1 results — ETF flows, LEARN HALF only (exam half sealed). bp, signed with the flow (long after inflow, short after outflow)")
for coin, ffile, sym in (('BTC', 'btc_flows.csv', 'BTCUSDT'), ('ETH', 'eth_flows.csv', 'ETHUSDT')):
    f = pd.read_csv(ffile, parse_dates=['date']).sort_values('date').reset_index(drop=True)
    f = f[f.date <= '2026-08-31']; half = f.date.iloc[len(f) // 2]; L = f[f.date < half].copy()
    bn = pd.read_parquet(f'{K}/{sym}.parquet'); bn = bn[bn.ts >= int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6)].set_index('ts').sort_index()
    cl = bn['close']; op = bn['open']
    mon = pd.Series(pd.to_datetime(bn.index, unit='ms').to_period('M'), index=bn.index); drift = {z: ((cl.shift(-60 * z) / cl - 1) * 1e4).groupby(mon).mean() for z in HZ}
    L['p90'] = L.total.shift(1).rolling(60).quantile(0.9); L['p10'] = L.total.shift(1).rolling(60).quantile(0.1); L['p75s'] = L.total.rolling(3).sum().shift(1).rolling(60).quantile(0.75)
    L['s3'] = L.total.rolling(3).sum(); L['streak'] = ((np.sign(L.total) == np.sign(L.total.shift(1))) & (np.sign(L.total) == np.sign(L.total.shift(2))) & (L.total != 0))
    def px(ts): return cl.get(ts, np.nan)
    P(f"\n## {coin}: learn half {L.date.min().date()} -> {L.date.max().date()}, {len(L)} trading days | flow lag-1 autocorr {L.total.autocorr(1):+.2f}, lag-2 {L.total.autocorr(2):+.2f}")
    # (a) diagnostic window on day T
    def win(d, h0, h1):
        t0 = int(pd.Timestamp(d, tz='UTC').value // 10**6) + h0 * H; t1 = int(pd.Timestamp(d, tz='UTC').value // 10**6) + h1 * H
        a, b = px(t0 - MIN), px(t1 - MIN); return (b / a - 1) * 1e4 if a == a and b == b else np.nan
    L['w1920'] = [win(d, 19, 20) for d in L.date]; L['w1821'] = [win(d, 18, 21) for d in L.date]; L['w1316'] = [win(d, 13, 16) for d in L.date]
    big_in = L[L.total >= L.p90]; big_out = L[L.total <= L.p10]; rest = L[(L.total < L.p90) & (L.total > L.p10) & L.p90.notna()]
    P(f"(a) US afternoon on the SAME day (not tradeable): 19-20 UTC move: big inflow days {big_in.w1920.mean():+.0f}bp (n={len(big_in)}) | big outflow {big_out.w1920.mean():+.0f} (n={len(big_out)}) | other days {rest.w1920.mean():+.0f}")
    P(f"    18-21 UTC: inflow {big_in.w1821.mean():+.0f} | outflow {big_out.w1821.mean():+.0f} | other {rest.w1821.mean():+.0f}   ·   13-16 UTC (morning, before the buy): inflow {big_in.w1316.mean():+.0f} | outflow {big_out.w1316.mean():+.0f} | other {rest.w1316.mean():+.0f}")
    P(f"    correlation of the day's flow with the 19-20 UTC move: {L[['total','w1920']].corr().iloc[0,1]:+.2f}; with the whole day 00-24 UTC move: {L.assign(d24=[win(d,0,24) for d in L.date])[['total','d24']].corr().iloc[0,1]:+.2f}")
    # (b) tradeable: entry 02:00 UTC on the next calendar day after the signal day
    rows = []
    for nm, sel, sgn in (('S1 big inflow', L.total >= L.p90, 1), ('S1 big outflow', L.total <= L.p10, -1), ('S2 streak in', L.streak & (L.total > 0) & (L.s3 >= L.p75s), 1), ('S2 streak out', L.streak & (L.total < 0) & (L.s3 <= -L.p75s), -1)):
        g = L[sel & L.p90.notna()]
        for _, r in g.iterrows():
            t_in = int((pd.Timestamp(r.date, tz='UTC') + pd.Timedelta(days=1)).value // 10**6) + 2 * H
            if t_in not in op.index: continue
            E = op[t_in]; rr = dict(coin=coin, sig=nm, ts=t_in, side=sgn)
            for z in HZ:
                x = px(t_in + z * H - MIN); rr[f'f{z}'] = sgn * (x / E - 1) * 1e4 if x == x else np.nan; rr[f'x{z}'] = rr[f'f{z}'] - sgn * drift[z][mon[t_in]]
            rows.append(rr)
    d = pd.DataFrame(rows)
    P("(b) tradeable, entry 02:00 UTC next day, excess over drift:")
    for nm, g in d.groupby('sig', sort=False):
        g = g.dropna(subset=[f'x{z}' for z in HZ])
        if len(g) < 10: P(f"  {nm:16s} n={len(g)} too few"); continue
        def ci(col): lo, hi, p = d4.boot(g[col].values, g.ts.values); return f"{g[col].mean():+5.0f} [{lo:+.0f},{hi:+.0f}]"
        P(f"  {nm:16s} n={len(g):3d}: " + ' | '.join(f"{z:>2}h {ci(f'x{z}')}" for z in HZ))
    both = d[d.sig.str.startswith('S1')].dropna(subset=['x18', 'x48'])
    if len(both) >= 20:
        def ci2(col): lo, hi, p = d4.boot(both[col].values, both.ts.values); return f"{both[col].mean():+5.0f} [{lo:+.0f},{hi:+.0f}]"
        P(f"  S1 both sides   n={len(both):3d}: " + ' | '.join(f"{z:>2}h {ci2(f'x{z}')}" for z in HZ))
open(r'C:\Users\you\Desktop\TradeDeck\specs\E1-results.md', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
