"""Hunt 22 - FX weekend-gap reversal v1 (ChatGPT frozen spec 2026-10-04)."""
import sqlite3, os, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = ['AUDUSD', 'EURUSD', 'GBPUSD', 'USDJPY']
COMM_BP = 0.4; FIN_BP = 5.0; START_EQ = 50_000.0; RISK = 0.005; LOT = 1000; LEV = 30.0
NY = 'America/New_York'
LEARN_END = pd.Timestamp('2024-01-01', tz='UTC'); EXAM_END = pd.Timestamp('2026-08-29', tz='UTC')
QS, MS = [0.025, 0.05, 0.10], [1, 2, 3]; PRIMARY = (0.05, 2)
TOL = os.environ.get('H22_TOL') == '1'

def log(*a): print(*a, flush=True)

def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts where c.symbol=? and c.ts < ? order by c.ts",
                           con, params=(sym, int(EXAM_END.value // 10**6)))
    con.close(); df['spread'] = df['spread'].ffill().bfill(); df.index = pd.to_datetime(df['ts'], unit='ms', utc=True); return df

def daily_atr(df):
    """daily bars on NY 17:00 boundaries; Wilder ATR14; value indexed by the Friday date whose bar it completes."""
    loc = df.index.tz_convert(NY) + pd.Timedelta(hours=7)        # shift so a 'day' runs 17:00 -> 17:00
    day = loc.normalize().tz_localize(None)
    g = df.groupby(day)
    d = pd.DataFrame({'high': g['high'].max(), 'low': g['low'].min(), 'close': g['close'].last()})
    pc = d['close'].shift(1); tr = np.maximum(d['high'] - d['low'], np.maximum((d['high'] - pc).abs(), (d['low'] - pc).abs()))
    d['atr'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return d

def weekends(df, sym):
    """for each Friday: Fri 16:59 close, Sun 17:00 open, Sun 17:05 open, Mon 16:55 open, ATR through Friday."""
    atr = daily_atr(df)
    fridays = pd.bdate_range('2012-01-06', EXAM_END.tz_localize(None), freq='W-FRI')
    rows = []
    for f in fridays:
        t_fri = pd.Timestamp(f'{f:%Y-%m-%d} 16:59', tz=NY).tz_convert('UTC'); sun = f + pd.Timedelta(days=2); mon = f + pd.Timedelta(days=3)
        t_sun = pd.Timestamp(f'{sun:%Y-%m-%d} 17:00', tz=NY).tz_convert('UTC'); t_ent = pd.Timestamp(f'{sun:%Y-%m-%d} 17:05', tz=NY).tz_convert('UTC'); t_x = pd.Timestamp(f'{mon:%Y-%m-%d} 16:55', tz=NY).tz_convert('UTC')
        if TOL:
            fr = df.loc[t_fri - pd.Timedelta(minutes=9):t_fri]; su = df.loc[t_sun:t_sun + pd.Timedelta(minutes=10)]
            if len(fr) == 0 or len(su) == 0: rows.append(dict(fri=f, ok=False)); continue
            t_fri = fr.index[-1]; t_sun = su.index[0]
            en = df.loc[t_ent:t_ent + pd.Timedelta(minutes=10)]
            if len(en): t_ent = en.index[0]
        elif t_fri not in df.index or t_sun not in df.index: rows.append(dict(fri=f, ok=False)); continue
        # ATR: daily bar keyed by shifted date; Friday's bar (Thu 17:00 -> Fri 17:00) is keyed by Saturday's date after +7h shift... use last bar ending <= Fri 17:00
        a_key = (t_fri.tz_convert(NY) + pd.Timedelta(hours=7)).normalize().tz_localize(None)
        a = atr['atr'].get(a_key, np.nan)
        rows.append(dict(fri=f, ok=True, fri_close=float(df.loc[t_fri, 'close']), sun_open=float(df.loc[t_sun, 'open']), t_ent=t_ent, t_x=t_x, atr=float(a) if pd.notna(a) else np.nan,
                         ent_ok=t_ent in df.index, x_ok=len(df.loc[t_x:t_x + pd.Timedelta(minutes=5)]) > 0))
    w = pd.DataFrame(rows)
    w['gap'] = np.log(w['sun_open'] / w['fri_close'])
    return w

def trade(df, r, side, m, slip_bp, jpy):
    k = df.index.searchsorted(r['t_ent']); e = df.iloc[k]
    if df.index[k] != r['t_ent']: return None
    entry = (e['open'] + e['spread'] / 2) * (1 + slip_bp / 1e4) if side == 1 else (e['open'] - e['spread'] / 2) * (1 - slip_bp / 1e4)
    dist = m * r['atr']; stop = entry - side * dist
    seg = df.loc[r['t_ent'] + pd.Timedelta(minutes=1):r['t_x'] - pd.Timedelta(microseconds=1)]
    how = 'time'; px = None; t_out = None
    if side == 1:
        bid_lo = (seg['low'] - seg['spread'] / 2).values; hit = np.where(bid_lo <= stop)[0]
        if len(hit):
            rr = seg.iloc[hit[0]]; o = rr['open'] - rr['spread'] / 2; px = (min(stop, o) if o < stop else stop) * (1 - slip_bp / 1e4); how = 'stop'; t_out = seg.index[hit[0]]
    else:
        ask_hi = (seg['high'] + seg['spread'] / 2).values; hit = np.where(ask_hi >= stop)[0]
        if len(hit):
            rr = seg.iloc[hit[0]]; o = rr['open'] + rr['spread'] / 2; px = (max(stop, o) if o > stop else stop) * (1 + slip_bp / 1e4); how = 'stop'; t_out = seg.index[hit[0]]
    if px is None:
        xx = df.loc[r['t_x']:r['t_x'] + pd.Timedelta(minutes=5)]
        if len(xx) == 0: return None
        x = xx.iloc[0]; t_out = xx.index[0]
        px = (x['open'] - x['spread'] / 2) * (1 - slip_bp / 1e4) if side == 1 else (x['open'] + x['spread'] / 2) * (1 + slip_bp / 1e4)
    gross_r = side * (px - entry) / dist
    cost_r = (2 * COMM_BP + FIN_BP) / 1e4 * entry / dist
    return dict(fri=r['fri'], t_in=r['t_ent'], t_out=t_out, side=side, entry=entry, stop=stop, exit=px, how=how, r=gross_r - cost_r, gap_bp=r['gap'] * 1e4, atr_pct=r['atr'] / entry * 100, lev=entry / dist * RISK)

def run_cell(W, data, q, m, slip_bp, learn_only=True):
    trades = []
    for sym, w in W.items():
        ok = w[w['ok'] & w['gap'].notna()].reset_index(drop=True)
        gaps = ok['gap'].values
        for i in range(len(ok)):
            if i < 260: continue
            r = ok.iloc[i]
            if learn_only and r['t_ent'] >= LEARN_END: continue
            if not learn_only and r['t_ent'] < LEARN_END: continue
            prev = gaps[i - 260:i]
            lo_q, hi_q = np.quantile(prev, q), np.quantile(prev, 1 - q)
            g = gaps[i]
            if not (r['ent_ok'] and pd.notna(r['atr']) and r['atr'] > 0): continue
            if g > hi_q and g > 0: side = -1
            elif g < lo_q and g < 0: side = 1
            else: continue
            t = trade(data[sym], r, side, m, slip_bp, sym.endswith('JPY'))
            if t: t['sym'] = sym; trades.append(t)
    return pd.DataFrame(trades).sort_values('t_in').reset_index(drop=True) if trades else pd.DataFrame()

def boot_months(t, n=10000, seed=7):
    g = t.groupby(pd.to_datetime(t['t_in']).dt.to_period('M'))['r'].apply(list); ms = [np.array(x) for x in g.values]
    rng = np.random.default_rng(seed); o = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(ms), len(ms)); o[i] = np.concatenate([ms[j] for j in pick]).mean()
    return np.percentile(o, 2.5), np.percentile(o, 97.5)

def main():
    t0 = time.time(); data = {p: load(p) for p in PAIRS}; W = {p: weekends(data[p], p) for p in PAIRS}
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 22 results ({"DIAGNOSTIC: 10-min boundary tolerance" if TOL else "strict boundaries as specified"}; FXCM minute bid/ask, comm {COMM_BP}bp/side, slip 0.5bp base, one {FIN_BP}bp financing debit per trade, 0.5% risk, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    for p in PAIRS:
        w = W[p]; P(f"{p}: {len(w)} weekends, {int(w['ok'].sum())} with both boundary candles ({w['ok'].mean()*100:.0f}%), median |gap| {w['gap'].abs().median()*1e4:.1f}bp, 95th pct {w['gap'].abs().quantile(.95)*1e4:.1f}bp")
    cells = {}
    P(); P('## LEARN (eligible trades 2017-2023 after the 260-weekend warm-up), net R per trade, base costs')
    P('| q \\ m | 1 | 2 | 3 |'); P('|---|---|---|---|')
    for q in QS:
        row = []
        for m in MS:
            cells[(q, m)] = run_cell(W, data, q, m, 0.5); t = cells[(q, m)]
            row.append(f"{t['r'].mean():+.3f}R ({len(t)})" if len(t) else 'n/a')
        P(f'| {q*100:g}% | ' + ' | '.join(row) + ' |')
    prim = cells[PRIMARY]
    P(); P(f"### primary q=5%, m=2: {len(prim)} trades | net {prim['r'].mean():+.3f}R/trade | total {prim['r'].sum():+.1f}R | hit {(prim['r']>0).mean()*100:.0f}% | stops {(prim['how']=='stop').sum()} | avg |gap| {prim['gap_bp'].abs().mean():.1f}bp | $ {prim['r'].sum()*250:+,.0f} over {prim['t_in'].dt.year.nunique()} years")
    for s, nm in ((-1, 'shorts after up-gaps'), (1, 'longs after down-gaps')):
        g = prim[prim['side'] == s]; P(f"  {nm}: {len(g)}, {g['r'].mean():+.3f}R" if len(g) else f'  {nm}: 0')
    for p, g in prim.groupby('sym'): P(f"  {p}: {len(g)} trades, {g['r'].mean():+.3f}R, {g['r'].sum():+.1f}R")
    P('year | trades | R/trade | total R')
    for y, g in prim.groupby(prim['t_in'].dt.year): P(f"{y} | {len(g)} | {g['r'].mean():+.3f} | {g['r'].sum():+.1f}")
    stress = run_cell(W, data, *PRIMARY, 1.0)
    lo, hi = boot_months(prim) if len(prim) > 10 else (np.nan, np.nan)
    neigh = [cells[(0.025, 2)], cells[(0.10, 2)], cells[(0.05, 1)], cells[(0.05, 3)]]
    npos = sum(1 for t in neigh if len(t) and t['r'].mean() > 0)
    passed = len(stress) and stress['r'].mean() > 0 and lo > 0 and npos >= 3
    P(); P(f"stress 1bp: {stress['r'].mean():+.3f}R ({len(stress)}) | best 5 trades = {prim['r'].nlargest(5).sum():+.1f}R of {prim['r'].sum():+.1f}R total")
    P(f"## GATE: stress positive {bool(len(stress) and stress['r'].mean() > 0)}; whole-month bootstrap CI [{lo:+.3f}, {hi:+.3f}]R; neighbours positive {npos}/4; trades {len(prim)} -> {'PASS' if passed else ('THIN' if len(prim) < 100 else 'FAIL')}")
    if passed:
        ex = run_cell(W, data, *PRIMARY, 0.5, learn_only=False); P(f"## EXAM 2024-2026: {len(ex)} trades, {ex['r'].mean():+.3f}R, {ex['r'].sum():+.1f}R")
    else: P('## EXAM: NOT INSPECTED, sealed.')
    prim.to_csv(os.path.join(HERE, 'hunt22_primary.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt22-results' + ('-tolerant-diagnostic' if TOL else '') + '.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
