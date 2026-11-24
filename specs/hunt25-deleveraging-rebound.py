"""Hunt 25 - deleveraging rebound v1 (ChatGPT frozen spec 2026-10-04): price drop + falling OI + perp discount -> 2h long."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
COMM_BP = 3.0; START_EQ = 50_000.0; RISK = 0.005; LEV = 10.0
BAR = 300_000; MIN = 60_000; H = 3600_000; DAY = 86_400_000
T0 = int(pd.Timestamp('2021-12-01', tz='UTC').value // 10**6); L0 = int(pd.Timestamp('2022-01-01', tz='UTC').value // 10**6); L1 = int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6); END = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)
QS, DS = [1, 2, 4], [10, 20, 40]; PRIMARY = (2, 20)

def log(*a): print(*a, flush=True)

def load_coin(c):
    con = sqlite3.connect(DB)
    px = pd.read_sql_query("select timestamp ts, open, high, low, close from candles where source='binance_um' and market=? and interval='5m' and timestamp>=? and timestamp<? order by timestamp", con, params=(c, T0 - 7 * DAY, END))
    con.close()
    idx = pd.read_parquet(os.path.join(HERE, f'index_{c}.parquet')); met = pd.read_parquet(os.path.join(HERE, f'metrics_{c}.parquet'))
    if met['ts'].max() < 1e12: met['ts'] = met['ts'] * 1000                       # archive timestamps are seconds
    met['sum_open_interest'] = met['sum_open_interest'].astype(float)
    met.loc[met['sum_open_interest'] <= 0, 'sum_open_interest'] = np.nan           # zero readings = missing, never interpolated
    return px, idx, met

def audit(c, px, idx, met, P):
    i = idx.drop_duplicates('ts'); m = met.drop_duplicates('ts')
    gaps_i = int(((i['ts'].diff() != BAR) & i['ts'].diff().notna()).sum()); gaps_m = int(((m['ts'].diff() != BAR) & m['ts'].diff().notna()).sum())
    oi = m['sum_open_interest'].astype(float); jumps = int((oi.dropna().pct_change().abs() > 0.5).sum())
    P(f"  {c}: index rows {len(idx)} (dups {len(idx)-len(i)}, 5m gaps {gaps_i}); metrics rows {len(met)} (dups {len(met)-len(m)}, gaps {gaps_m}, zero-or-missing OI {int(oi.isna().sum())}, >50% OI jumps {jumps}); missing days {open(os.path.join(HERE, f'metrics_{c}_missing.txt')).read().count(chr(10))+ (1 if open(os.path.join(HERE, f'metrics_{c}_missing.txt')).read().strip() else 0)}")
    return i, m

def prep(c, px, idx, met):
    df = px.merge(idx[['ts', 'close']].rename(columns={'close': 'index'}), on='ts', how='left').merge(met[['ts', 'sum_open_interest']].rename(columns={'sum_open_interest': 'oi'}), on='ts', how='left')
    df['oi'] = df['oi'].astype(float)
    ts = df['ts'].values
    # 15m ATR20 (Wilder) and 1h ATR20 from completed bars; value stamped at the bar END time
    def atr_at(step):
        b = (df['ts'] // step) * step; g = df.groupby(b)
        bb = pd.DataFrame({'t': g['ts'].first().index.values, 'high': g['high'].max().values, 'low': g['low'].min().values, 'close': g['close'].last().values, 'n': g['ts'].size().values})
        bb = bb[bb['n'] == step // BAR]
        pc = bb['close'].shift(1); tr = np.maximum(bb['high'] - bb['low'], np.maximum((bb['high'] - pc).abs(), (bb['low'] - pc).abs()))
        return pd.Series(tr.ewm(alpha=1 / 20, adjust=False).mean().values, index=bb['t'].values + step)       # available from bar end
    a15 = atr_at(15 * MIN); a60 = atr_at(H)
    pos = pd.Series(np.arange(len(df)), index=ts)
    df['atr15_lag'] = pd.Series(ts - 60 * MIN).map(lambda t: a15[a15.index <= t].iloc[-1] if (a15.index <= t).any() else np.nan).values if False else np.nan
    # faster: asof merges
    df = df.sort_values('ts')
    tmp = pd.DataFrame({'ts': ts - 60 * MIN})
    df['atr15_lag'] = pd.merge_asof(tmp, pd.DataFrame({'ts': a15.index.values, 'v': a15.values}), on='ts')['v'].values
    df['atr60_lag'] = pd.merge_asof(tmp, pd.DataFrame({'ts': a60.index.values, 'v': a60.values}), on='ts')['v'].values
    close_map = pd.Series(df['close'].values, index=ts); oi_map = pd.Series(df['oi'].values, index=ts)
    df['close_60'] = pd.Series(ts - 60 * MIN).map(close_map).values
    df['oi_65'] = pd.Series(ts - 65 * MIN).map(oi_map).values; df['oi_5'] = pd.Series(ts - 5 * MIN).map(oi_map).values
    df['drop'] = df['close_60'] - df['close']
    df['disc_bp'] = (df['close'] / df['index'] - 1) * 1e4
    df['oi_chg'] = df['oi_5'] / df['oi_65'] - 1
    df['coin'] = c
    return df

def signals(df, q, d, control=False):
    c1 = df['drop'] >= 2 * df['atr15_lag']
    c2 = df['oi_chg'] <= -q / 100; c3 = df['disc_bp'] <= -d
    tod = df['ts'] % DAY
    ok = c1 & df['atr60_lag'].notna() & (tod + BAR >= 5 * MIN) & (tod + BAR <= 21 * H + 50 * MIN)
    if not control: ok &= c2 & c3 & df['oi_5'].notna() & df['oi_65'].notna() & df['index'].notna()
    return df[ok]

def walk(df, i, slip_bp):
    """signal at row i (bar start T-5m.. T boundary = ts[i]+BAR). entry at open of bar starting T+5m = row i+2? T = close of bar i -> bar i+1 starts at T; 'open at T+5' = bar i+2 open."""
    ts = df['ts'].values; op = df['open'].values; lo = df['low'].values
    k = i + 2
    if k >= len(ts) or ts[k] != ts[i] + 2 * BAR: return None
    entry = op[k] * (1 + slip_bp / 1e4); dist = 2 * df['atr60_lag'].values[i]; stop = entry - dist
    kend = k + 24                                                 # exactly 2h after entry
    if kend >= len(ts) or ts[kend] != ts[k] + 2 * H: return None
    seg = lo[k + 1:kend]; hit = np.where(seg <= stop)[0]
    if len(hit):
        kx = k + 1 + hit[0]; fill = min(stop, op[kx]) if op[kx] < stop else stop; px = fill * (1 - slip_bp / 1e4); how = 'stop'
    else: kx = kend; px = op[kx] * (1 - slip_bp / 1e4); how = 'time'
    r = (px - entry) / dist - 2 * COMM_BP / 1e4 * entry / dist
    fin = 5 / 1e4 * entry / dist if (ts[kx] // DAY) != (ts[k] // DAY) else 0.0
    return dict(t_sig=int(ts[i] + BAR), t_in=int(ts[k]), t_out=int(ts[kx]), r=r - fin, how=how, disc=float(df['disc_bp'].values[i]), lev=entry / dist * RISK)

def book(frames, q, d, slip_bp, control=False):
    sig = []
    for c, df in frames.items():
        s = signals(df, q, d, control)
        for i in s.index.values: sig.append((int(df['ts'].values[i] + BAR), c, i, float(df['disc_bp'].values[i])))
    sig.sort(key=lambda x: (x[0], x[3], x[1]))
    cooldown = {c: -1 for c in frames}; busy_until = -1; trades = []; blocked = 0
    for t_sig, c, i, disc in sig:
        if t_sig < cooldown[c]: continue
        cooldown[c] = t_sig + 6 * H                                # starts at any qualifying signal, blocked or not
        if t_sig < busy_until: blocked += 1; continue
        tr = walk(frames[c], i, slip_bp)
        if tr is None: continue
        tr['coin'] = c; trades.append(tr); busy_until = tr['t_out']
    return pd.DataFrame(trades), blocked

def summ(t):
    if len(t) == 0: return '0 trades'
    return f"{len(t)} trades on {pd.to_datetime(t['t_in'], unit='ms').dt.date.nunique()} days | {t['r'].mean():+.3f}R | total {t['r'].sum():+.1f}R | hit {(t['r']>0).mean()*100:.0f}% | stops {(t['how']=='stop').sum()}"

def boot_weeks(t, n=10000, seed=7):
    g = t.groupby(pd.to_datetime(t['t_in'], unit='ms').dt.to_period('W'))['r'].apply(list); ws = [np.array(x) for x in g.values]
    rng = np.random.default_rng(seed); o = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(ws), len(ws)); o[i] = np.concatenate([ws[j] for j in pick]).mean()
    return np.percentile(o, 2.5), np.percentile(o, 97.5)

def main():
    t0 = time.time(); lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 25 results (Binance perps 5m, OI from daily metrics, index klines, {COMM_BP}bp/side + 2bp/fill, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P('## Data audit'); frames = {}
    for c in COINS:
        px, idx, met = load_coin(c); i, m = audit(c, px, idx, met, P); df = prep(c, px, i, m)
        frames[c] = df[(df['ts'] >= T0) & (df['ts'] < L1)].reset_index(drop=True)          # learn + warm-up only; exam sealed
        P(f"  {c}: learn rows with OI+index present {frames[c]['oi'].notna().mean()*100:.1f}% / {frames[c]['index'].notna().mean()*100:.1f}%; median discount {frames[c]['disc_bp'].median():+.1f}bp, 1st pct {frames[c]['disc_bp'].quantile(.01):+.1f}bp")
    cells = {}
    P(); P('## LEARN 2022-2023, net R per trade (base), one position, per-coin 6h cooldown')
    P('| q \\ d | 10bp | 20bp | 40bp |'); P('|---|---|---|---|')
    for q in QS:
        row = []
        for d in DS:
            t, b = book(frames, q, d, 2.0); t = t[t['t_in'] >= L0] if len(t) else t; cells[(q, d)] = t
            row.append(f"{t['r'].mean():+.3f}R ({len(t)})" if len(t) else 'n/a (0)')
        P(f'| {q}% | ' + ' | '.join(row) + ' |')
    prim = cells[PRIMARY]
    P(); P(f'### primary q=2%, d=20bp: {summ(prim)}')
    if len(prim):
        for y in (2022, 2023):
            g = prim[pd.to_datetime(prim['t_in'], unit='ms').dt.year == y]; P(f'  {y}: {summ(g)}')
        for c, g in prim.groupby('coin'): P(f'  {c}: {summ(g)}')
        best_day = prim.assign(d=pd.to_datetime(prim['t_in'], unit='ms').dt.date).groupby('d')['r'].sum()
        P(f"  best day {best_day.max():+.1f}R on {best_day.idxmax()}; without it total {prim['r'].sum()-best_day.max():+.1f}R")
    st, _ = book(frames, *PRIMARY, 5.0); st = st[st['t_in'] >= L0] if len(st) else st
    ctl, _ = book(frames, *PRIMARY, 2.0, control=True); ctl = ctl[ctl['t_in'] >= L0] if len(ctl) else ctl
    P(f'stress 5bp/fill: {summ(st)}'); P(f'control (price drop only, no OI/discount): {summ(ctl)}')
    n_days = pd.to_datetime(prim['t_in'], unit='ms').dt.date.nunique() if len(prim) else 0
    thin = len(prim) < 100 or n_days < 30
    lo, hi = boot_weeks(prim) if len(prim) > 10 else (np.nan, np.nan)
    neigh = [cells[(1, 20)], cells[(4, 20)], cells[(2, 10)], cells[(2, 40)]]; npos = sum(1 for t in neigh if len(t) and t['r'].mean() > 0)
    y22 = prim[pd.to_datetime(prim['t_in'], unit='ms').dt.year == 2022]['r'].sum() if len(prim) else 0; y23 = prim[pd.to_datetime(prim['t_in'], unit='ms').dt.year == 2023]['r'].sum() if len(prim) else 0
    passed = (not thin) and y22 > 0 and y23 > 0 and len(st) and st['r'].mean() > 0 and lo > 0 and npos >= 3
    P(); P(f"## GATE: trades {len(prim)} / days {n_days} ({'THIN' if thin else 'ok'}); 2022 {y22:+.1f}R; 2023 {y23:+.1f}R; stress {st['r'].mean() if len(st) else float('nan'):+.3f}R; weekly bootstrap CI [{lo:+.3f}, {hi:+.3f}]R; neighbours {npos}/4 -> {'PASS' if passed else ('THIN' if thin else 'FAIL')}")
    P('## EXAM: ' + ('to run' if passed else 'NOT INSPECTED, sealed.'))
    if len(prim): prim.to_csv(os.path.join(HERE, 'hunt25_primary.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt25-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
