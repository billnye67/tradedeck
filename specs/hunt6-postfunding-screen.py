"""Hunt 6 — post-funding price-pressure SCREEN. Spec: specs/hunt6-postfunding-screen.md
Direction frozen: sign(settled funding). Enter at 5m open 5 min after settlement; control = +2h."""
import sqlite3, datetime as dt, collections, sys
import numpy as np

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
DAY = 86_400_000
END = int(dt.datetime(2026, 8, 28, tzinfo=dt.UTC).timestamp() * 1000)
SPLIT = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
STRESS = '--stress' in sys.argv
COST = 0.0006 + 2 * (0.0005 if STRESS else 0.0002)
SWAP = 0.0005
THR = (0.0001, 0.0003, 0.0005); HOLD = (15, 30, 60); DELAYS = {'t+5m': 5, 't+2h': 120}

con = sqlite3.connect(DB)
res = collections.defaultdict(list)   # (coin, thr, hold, delay, key) -> list of net returns ; key in era/year
gross = collections.defaultdict(list)
missing = 0
for coin in COINS:
    a = np.array(con.execute("select timestamp,open from candles where source='binance_um' and market=? and interval='5m' and timestamp<=? order by timestamp", (coin, END)).fetchall(), dtype=float)
    ts = a[:, 0].astype(np.int64); op = a[:, 1]; pos = {int(t): i for i, t in enumerate(ts)}
    fr = con.execute("select ts,rate from funding_rates where market=? and ts<=? order by ts", (coin, END)).fetchall()
    for t0, rate in fr:
        t0 = int(round(t0 / 60000.0)) * 60000  # snap to minute (some rows carry +1ms)
        if abs(rate) < THR[0]: continue
        side = 1 if rate > 0 else -1
        for dname, dmin in DELAYS.items():
            te = t0 + dmin * 60_000
            if te not in pos: missing += 1; continue
            pe = op[pos[te]]
            for hold in HOLD:
                tx = te + hold * 60_000
                if tx not in pos: missing += 1; continue
                raw = side * (op[pos[tx]] / pe - 1)
                swap = SWAP if tx // DAY != te // DAY else 0.0
                net = raw - COST - swap
                era = 'exam' if te >= SPLIT else 'learn'; yr = dt.datetime.fromtimestamp(te / 1000, dt.UTC).year
                for thr in THR:
                    if abs(rate) >= thr:
                        for key in (era, yr):
                            res[(coin, thr, hold, dname, key)].append(net); gross[(coin, thr, hold, dname, key)].append(raw)

def st(lst):
    if not lst: return "      n=0"
    e = np.array(lst); return f"n={len(e):5d} {1e4*e.mean():+6.1f}bp hit {100*(e>0).mean():4.1f}%"

print(f"Post-funding price pressure | direction = sign(funding) | costs {COST*1e4:.0f}bp{' STRESS' if STRESS else ''} | missing bars {missing}")
print("\nPRIMARY CELL 3bp / 30min, per coin (net of costs):")
print(f"{'coin':8s} | {'LEARN t+5m':>32s} | {'EXAM t+5m':>32s} | {'LEARN t+2h ctrl':>32s} | {'EXAM t+2h ctrl':>32s}")
for coin in COINS:
    print(f"{coin[:-4]:8s} | {st(res[(coin,0.0003,30,'t+5m','learn')]):>32s} | {st(res[(coin,0.0003,30,'t+5m','exam')]):>32s} | {st(res[(coin,0.0003,30,'t+2h','learn')]):>32s} | {st(res[(coin,0.0003,30,'t+2h','exam')]):>32s}")
print("\nALL NINE CELLS, pooled across coins, entry t+5m (gross / net):")
for thr in THR:
    for hold in HOLD:
        row = []
        for era in ('learn', 'exam'):
            g = [x for c in COINS for x in gross[(c, thr, hold, 't+5m', era)]]; n = [x for c in COINS for x in res[(c, thr, hold, 't+5m', era)]]
            row.append(f"{era.upper()} n={len(n):6d} gross {1e4*np.mean(g):+6.1f}bp net {1e4*np.mean(n):+6.1f}bp hit {100*np.mean(np.array(n)>0):4.1f}%")
        ctrl = [x for c in COINS for x in gross[(c, thr, hold, 't+2h', 'exam')]]
        print(f"thr {thr*1e4:.0f}bp hold {hold:2d} | {row[0]} | {row[1]} | ctrl t+2h exam gross {1e4*np.mean(ctrl):+6.1f}bp")
print("\nPRIMARY BY YEAR (pooled, t+5m, gross bp / net bp): ", end='')
for yr in range(2020, 2027):
    g = [x for c in COINS for x in gross[(c, 0.0003, 30, 't+5m', yr)]]; n = [x for c in COINS for x in res[(c, 0.0003, 30, 't+5m', yr)]]
    if n: print(f"{yr}: n={len(n)} {1e4*np.mean(g):+.1f}/{1e4*np.mean(n):+.1f};", end=' ')
print()
