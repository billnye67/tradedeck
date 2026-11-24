"""MO build: the user's midnight-open rejection (spec specs/MO-midnight-rejection.md, written before this ran). Builds EVERY trade for
every market x timeframe x level hour (0 = midnight, 1 and 3 = fake-level controls) x side x first/every x target, plus the coin-flip
twin of each trade. Writes specs/lab/mo_trades_<MKT>.parquet. No era selection happens here. LAB ONLY."""
import os, sys, sqlite3, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ictlib as IL
from numba import njit
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = os.path.join(ROOT, 'specs', 'lab')
PROG = os.path.join(OUT, 'mo_progress.md')
TFS = {'5m': '5min', '10m': '10min', '15m': '15min'}; LEVELS = (0, 1, 3); TGTS = (1, 2, 3, 0)   # 0 = time exit 16:00 NY
MKTS = {  # market: (loader, tick, cost model, learn_end_year, trade weekends)
    'XAUUSD': ('metals', 0.1, 'cmdty', 2022, False),
    'EURUSD': ('fxcm', 0.0001, 'fx', 2019, False), 'GBPUSD': ('fxcm', 0.0001, 'fx', 2019, False),
    'USDJPY': ('fxcm', 0.01, 'fx', 2019, False), 'AUDUSD': ('fxcm', 0.0001, 'fx', 2019, False),
    'NQ': ('cme', 0.25, 'idx', 2023, False), 'ES': ('cme', 0.25, 'idx', 2023, False),
    'BTCUSDT': ('binance_um', 0.1, 'crypto', 2023, True), 'ETHUSDT': ('binance_um', 0.01, 'crypto', 2023, True)}

def prog(s):
    with open(PROG, 'a', encoding='utf-8') as f: f.write(f'- {time.strftime("%Y-%m-%d %H:%M")} {s}\n')

def load(m):
    src = MKTS[m][0]
    if src == 'cme':
        d = IL.load_cme(m, '5min'); rolls = IL.roll_dates(d); d = d[~d.date.isin(rolls)]
        return d[['ts', 'o', 'h', 'l', 'c']]
    c = sqlite3.connect(IL.DB)
    d = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source=? and market=? and interval='5m' order by t", c, params=(src, m))
    d = IL._ny(d); return d[['ts', 'o', 'h', 'l', 'c']]

def resample(d, tf):
    x = d.set_index(pd.DatetimeIndex(d.ts)).sort_index()
    if tf != '5min': x = x.resample(tf).agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    x = x[~x.index.duplicated()]
    out = pd.DataFrame({'o': x.o.values, 'h': x.h.values, 'l': x.l.values, 'c': x.c.values})
    out['date'] = x.index.date; out['hr'] = (x.index.hour + x.index.minute / 60).values; out['dow'] = x.index.dayofweek.values
    return out

@njit(cache=True)
def events(o, h, l, c, L, side):
    """Indices of rejection bars for one day (bars already start at the level bar). side -1 = short (from below), +1 = long."""
    out = np.empty(len(o), np.int64); n = 0; state = 0; broken = False
    for i in range(len(o)):
        if side == -1:
            if state == -1 and not broken and o[i] < L and h[i] >= L and c[i] < L:
                out[n] = i; n += 1
            if h[i] < L: state = -1; broken = False
            elif l[i] > L: state = 1
            if state == -1 and c[i] > L: broken = True
        else:
            if state == 1 and not broken and o[i] > L and l[i] <= L and c[i] > L:
                out[n] = i; n += 1
            if l[i] > L: state = 1; broken = False
            elif h[i] < L: state = -1
            if state == 1 and c[i] < L: broken = True
    return out[:n]

@njit(cache=True)
def walk(h, l, c, q0, side, E, stop, tgt):
    """Stop-first from the entry bar q0 to the last bar of the day (all bars < 16:00 NY). tgt <= 0 means no target (time exit)."""
    for q in range(q0, len(h)):
        if (l[q] <= stop) if side == 1 else (h[q] >= stop): return stop, q
        if tgt > 0 and ((h[q] >= tgt) if side == 1 else (l[q] <= tgt)): return tgt, q
    return c[len(h) - 1], len(h) - 1

def build(m):
    src, tick, cm, learn_end, wkend = MKTS[m]; t0 = time.time()
    d = load(m); rng = np.random.default_rng(20261017); rows = []
    for tfn, tf in TFS.items():
        b = resample(d, tf)
        if not wkend: b = b[b.dow < 5]
        bar_min = int(tfn[:-1]) / 60
        for date, g in b.groupby('date', sort=True):
            g = g[g.hr < 16]
            if len(g) < 4: continue
            for lv in LEVELS:
                x = g[g.hr >= lv]
                if not len(x) or x.hr.iloc[0] > lv + 1e-9: continue           # level bar must start exactly at the level hour
                o, h, l, c, hr = (x[k].values.astype(np.float64) for k in ('o', 'h', 'l', 'c', 'hr'))
                L = o[0]
                for side in (-1, 1):
                    ev = events(o, h, l, c, L, side)
                    for k in TGTS:
                        for var in ('first', 'every'):
                            last_exit = -1
                            for j in ev:
                                if var == 'first' and last_exit != -1: break
                                if j + 1 >= len(o) or j + 1 <= last_exit: continue
                                E = o[j + 1]
                                raw = (h[j] + tick) if side == -1 else (l[j] - tick)
                                if (E >= raw) if side == -1 else (E <= raw):
                                    rows.append((tfn, lv, side, k, var, date, hr[j], np.nan, np.nan, E, np.nan, np.nan, np.nan, 1))
                                    if var == 'first': last_exit = j + 1
                                    continue
                                risk = max(abs(raw - E), 3 * tick); stop = E - side * risk
                                tgt = E + side * k * risk if k > 0 else -1.0
                                xp, q = walk(h, l, c, j + 1, side, E, stop, tgt)
                                gR = side * (xp - E) / risk
                                cs = -side if rng.random() < 0.5 else side               # coin-flip twin: same bar, random side
                                cstop = E - cs * risk; ctgt = E + cs * k * risk if k > 0 else -1.0
                                cx, _ = walk(h, l, c, j + 1, cs, E, cstop, ctgt)
                                cR = cs * (cx - E) / risk
                                rows.append((tfn, lv, side, k, var, date, hr[j], hr[q] + bar_min, risk, E, gR, cR, abs(c[-1] - E), 0))
                                last_exit = q
                                if var == 'first': break
        prog(f'{m} {tfn} built ({len(rows):,} rows so far, {time.time()-t0:.0f}s)')
    T = pd.DataFrame(rows, columns=['tf', 'lv', 'side', 'tgt', 'var', 'date', 'ev_hr', 'exit_hr', 'risk', 'E', 'gR', 'coinR', 'move16', 'skip'])
    T['m'] = m; T['learn_end'] = learn_end; T['cost_model'] = cm
    T.to_parquet(os.path.join(OUT, f'mo_trades_{m}.parquet'), index=False)
    prog(f'{m} DONE: {len(T):,} rows -> specs/lab/mo_trades_{m}.parquet ({time.time()-t0:.0f}s)')
    return m, len(T)

if __name__ == '__main__':
    todo = sys.argv[1:] or list(MKTS)
    for m in todo:
        if os.path.exists(os.path.join(OUT, f'mo_trades_{m}.parquet')): prog(f'{m} already built, skipped'); continue
        print(build(m), flush=True)
