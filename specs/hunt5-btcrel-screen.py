"""Hunt 5 — BTC-relative intraday reversion FEATURE SCREEN. Spec: specs/hunt5-btcrel-reversion-screen.md"""
import sqlite3, datetime as dt, collections, sys
import numpy as np

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
DAY = 86_400_000; HOUR = 3_600_000
END = int(dt.datetime(2026, 8, 28, tzinfo=dt.UTC).timestamp() * 1000)
SPLIT = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
COINS = ['ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
STRESS = '--stress' in sys.argv
COST = (0.0006 + 2 * (0.0005 if STRESS else 0.0002))   # round trip incl. slippage both sides
SWAP = 0.0005

def load(m):
    con = sqlite3.connect(DB)
    a = np.array(con.execute("select timestamp,open,close from candles where source='binance_um' and market=? and interval='5m' and timestamp<=? order by timestamp", (m, END)).fetchall(), dtype=float)
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2]

def hourly(ts, o, c):
    """completed hours (12 bars): hour_start -> (open of first bar, close of last bar)"""
    hrs = ts // HOUR; out = {}
    uniq, idx, cnt = np.unique(hrs, return_index=True, return_counts=True)
    for h, i0, n in zip(uniq, idx, cnt):
        if n == 12: out[int(h)] = (o[i0], c[i0 + 11])
    return out

btc_ts, btc_o, btc_c = load('BTCUSDT'); btc_h = hourly(btc_ts, btc_o, btc_c)
btc_idx = {int(t): i for i, t in enumerate(btc_ts)}

results = collections.defaultdict(lambda: collections.defaultdict(list))  # (coin, thr, hold) -> era/year -> list of (exec_ret, adj_ret)
for coin in COINS:
    ts, o, c = load(coin); ch = hourly(ts, o, c)
    pos = {int(t): i for i, t in enumerate(ts)}
    hours = sorted(h for h in ch if h in btc_h and (h - 1) in ch and (h - 1) in btc_h)
    # hourly returns
    r_c = {h: ch[h][1] / ch[h - 1][1] - 1 for h in hours}
    r_b = {h: btc_h[h][1] / btc_h[h - 1][1] - 1 for h in hours}
    hs = np.array(hours); rc = np.array([r_c[h] for h in hours]); rb = np.array([r_b[h] for h in hours])
    # daily refit: for each day, fit on the preceding 30 days (720 hours) ending before the day
    day_of = hs // 24
    fits = {}
    for d in np.unique(day_of):
        mask = (hs >= (d - 30) * 24) & (hs < d * 24)
        if mask.sum() < 500: continue
        X = np.column_stack([np.ones(mask.sum()), rb[mask]]); y = rc[mask]
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        fits[int(d)] = (beta[0], beta[1], resid.std())
    last_event = -10**18
    for k, h in enumerate(hours):
        d = int(h // 24)
        if d not in fits: continue
        a, b, s = fits[d]
        if s <= 0: continue
        z = (r_c[h] - (a + b * r_b[h])) / s
        for thr in (1.5, 2.0, 2.5):
            if abs(z) < thr: continue
            if thr == 1.5:
                if h * HOUR - last_event < 2 * HOUR: break  # cooldown applies to the loosest trigger; tighter are subsets
                last_event = h * HOUR
            side = -1 if z > 0 else 1  # reversal
            t_entry = (h + 1) * HOUR   # hour completed at (h+1)*HOUR; enter at that bar's open
            if t_entry not in pos or t_entry not in btc_idx: continue
            pe = o[pos[t_entry]]; be = btc_o[btc_idx[t_entry]]
            for hold in (30, 60, 120):
                t_exit = t_entry + hold * 60_000
                if t_exit not in pos or t_exit not in btc_idx: continue
                px = o[pos[t_exit]]; bx = btc_o[btc_idx[t_exit]]
                raw = side * (px / pe - 1)
                adj = raw - side * b * (bx / be - 1)
                swap = SWAP if (t_exit // DAY) != (t_entry // DAY) else 0.0
                net = raw - COST - swap
                era = 'exam' if t_entry >= SPLIT else 'learn'
                yr = dt.datetime.fromtimestamp(t_entry / 1000, dt.UTC).year
                results[(coin, thr, hold)][era].append((net, adj))
                results[(coin, thr, hold)][yr].append((net, adj))

print(f"BTC-relative reversion screen | costs {COST*1e4:.0f}bp round trip{' (STRESS)' if STRESS else ''} + 5bp swap if crossing 00:00 UTC")
print(f"{'coin':9s} {'|z|>=':5s} {'hold':4s} | {'LEARN n':>8s} {'exec bp':>8s} {'hit%':>5s} {'adj bp':>7s} | {'EXAM n':>7s} {'exec bp':>8s} {'hit%':>5s} {'adj bp':>7s}")
agg = collections.defaultdict(list)
for coin in COINS:
    for thr in (1.5, 2.0, 2.5):
        for hold in (30, 60, 120):
            r = results[(coin, thr, hold)]
            def st(lst):
                if not lst: return (0, float('nan'), float('nan'), float('nan'))
                e = np.array([x[0] for x in lst]); a = np.array([x[1] for x in lst])
                return (len(e), 1e4 * e.mean(), 100 * (e > 0).mean(), 1e4 * a.mean())
            L = st(r['learn']); E = st(r['exam'])
            agg[(thr, hold, 'learn')] += r['learn']; agg[(thr, hold, 'exam')] += r['exam']
            print(f"{coin[:-4]:9s} {thr:5.1f} {hold:4d} | {L[0]:8d} {L[1]:8.1f} {L[2]:5.1f} {L[3]:7.1f} | {E[0]:7d} {E[1]:8.1f} {E[2]:5.1f} {E[3]:7.1f}")
print("\nALL COINS POOLED")
for thr in (1.5, 2.0, 2.5):
    for hold in (30, 60, 120):
        row = []
        for era in ('learn', 'exam'):
            lst = agg[(thr, hold, era)]; e = np.array([x[0] for x in lst]); a = np.array([x[1] for x in lst])
            row.append(f"{len(e):7d} exec {1e4*e.mean():+6.1f}bp hit {100*(e>0).mean():4.1f}% adj {1e4*a.mean():+6.1f}bp")
        print(f"|z|>={thr:.1f} hold {hold:3d} | LEARN {row[0]} | EXAM {row[1]}")
print("\nBY YEAR (|z|>=2.0, hold 60, pooled): ", end='')
for yr in range(2020, 2027):
    lst = [x for coin in COINS for x in results[(coin, 2.0, 60)][yr]]
    if lst: print(f"{yr}: n={len(lst)} exec {1e4*np.mean([x[0] for x in lst]):+.1f}bp adj {1e4*np.mean([x[1] for x in lst]):+.1f}bp;", end=' ')
print()
