"""Hunt 17 - FX round-number momentum v1 (Schlossberg rules, ChatGPT frozen spec 2026-10-04)."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = ['AUDUSD', 'EURUSD', 'GBPUSD', 'USDJPY']
COMM_BP = 0.4
START_EQ = 50_000.0; RISK_PCT = 0.5; LOT = 1000; LEV = 30.0
LEARN_END = int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6); EXAM_END = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)
DS, HS = [10, 15, 20], [1, 2, 3]; PRIMARY = (15, 2)
MIN = 60_000; H = 3600_000

def log(*a): print(*a, flush=True)

def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts "
                           "where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, EXAM_END))
    con.close(); df['spread'] = df['spread'].ffill().bfill()
    return df

def prep(df):
    """adds sma200 (hourly, completed, carried forward) and NY-time window flags."""
    ts = df['ts'].values
    hour = (ts // H) * H
    hc = df.groupby(hour)['close'].last()
    sma = hc.rolling(200).mean()
    # value available from the NEXT hour onward
    sma_map = pd.Series(sma.values, index=sma.index.values + H)
    df['sma'] = pd.Series(hour).map(sma_map).values
    t = pd.to_datetime(ts, unit='ms', utc=True).tz_convert('America/New_York')
    mins = t.hour.values * 60 + t.minute.values
    df['entry_ok'] = (mins >= 9 * 60) & (mins < 15 * 60) & (t.weekday.values < 5)
    df['flat_ts'] = ((pd.Series(t.normalize().tz_convert('UTC').as_unit('ms').asi8) + 16 * H).values)  # placeholder, fixed below
    # 16:00 NY in UTC for each minute's NY date
    ny16 = (t.normalize() + pd.Timedelta(hours=16)).tz_convert('UTC').as_unit('ms').asi8
    df['flat_ts'] = ny16
    return df

def signals(df, d_pips, pip, step, shift):
    """vectorised crossing detection. returns indices of the minute that completes the confirmation."""
    c = df['close'].values; sma = df['sma'].values; ok = df['entry_ok'].values
    c2 = np.roll(c, 2); c1 = np.roll(c, 1)
    d = d_pips * pip; sh = shift * pip
    lup = np.floor((c2 - sh) / step) * step + step + sh          # first level strictly above c2
    ldn = np.ceil((c2 - sh) / step) * step - step + sh           # first level strictly below c2
    trig_l = lup - d; trig_s = ldn + d
    long_ = (c2 < trig_l) & (c1 >= trig_l) & (c >= trig_l) & (c < lup) & (c > sma) & ok
    short = (c2 > trig_s) & (c1 <= trig_s) & (c <= trig_s) & (c > ldn) & (c < sma) & ok
    long_[:2] = False; short[:2] = False
    return np.where(long_)[0], np.where(short)[0], lup, ldn

def walk(df, i, side, d_pips, pip, Hcap, slip_bp, jpy):
    """enter at minute i+1 open; returns trade dict or None."""
    k = i + 1
    if k >= len(df): return None
    ts = df['ts'].values; op = df['open'].values; hi = df['high'].values; lo = df['low'].values; sp = df['spread'].values
    if ts[k] - ts[i] > 5 * MIN: return None                      # next minute missing
    d = d_pips * pip; slip = op[k] * slip_bp / 1e4
    entry = op[k] + sp[k] / 2 + slip if side == 1 else op[k] - sp[k] / 2 - slip
    stop = entry - side * d; tp1 = entry + side * d; tp2 = entry + side * 2 * d
    t_end = min(ts[k] + Hcap * H, df['flat_ts'].values[k])
    half1 = None; half2 = None; stop_now = stop; moved = False
    j = k + 1
    n = len(df)
    while j < n and ts[j] < t_end:
        bid_lo = lo[j] - sp[j] / 2; ask_hi = hi[j] + sp[j] / 2; bid_hi = hi[j] - sp[j] / 2; ask_lo = lo[j] + sp[j] / 2
        o_bid = op[j] - sp[j] / 2; o_ask = op[j] + sp[j] / 2; sl = op[j] * slip_bp / 1e4
        if side == 1:
            hit_stop = bid_lo <= stop_now; hit_t1 = (half1 is None) and bid_hi >= tp1; hit_t2 = bid_hi >= tp2
            stop_fill = (min(stop_now, o_bid) if o_bid < stop_now else stop_now) - sl
        else:
            hit_stop = ask_hi >= stop_now; hit_t1 = (half1 is None) and ask_lo <= tp1; hit_t2 = ask_lo <= tp2
            stop_fill = (max(stop_now, o_ask) if o_ask > stop_now else stop_now) + sl
        if hit_stop:                                              # conservative: stop wins any conflict
            if half1 is None: half1 = (stop_fill, ts[j], 'stop')
            half2 = (stop_fill, ts[j], 'stop' if not moved else 'be-stop'); break
        if hit_t1:
            half1 = (tp1, ts[j], 'tp1'); stop_now = entry; moved = True
        if hit_t2 and half1 is not None:
            half2 = (tp2, ts[j], 'tp2'); break
        j += 1
    if half2 is None:
        if j >= n: return None
        px = (op[j] - sp[j] / 2 - op[j] * slip_bp / 1e4) if side == 1 else (op[j] + sp[j] / 2 + op[j] * slip_bp / 1e4)
        if half1 is None: half1 = (px, ts[j], 'time')
        half2 = (px, ts[j], 'time')
    exit_ts = max(half1[1], half2[1])
    return dict(side=side, t_in=int(ts[k]), t_out=int(exit_ts), entry=float(entry), stop=float(stop), d=d, jpy=jpy,
                h1=float(half1[0]), h2=float(half2[0]), how1=half1[2], how2=half2[2], rate=float(entry))

def run_pair(df, sym, d_pips, Hcap, slip_bp, shift=0):
    jpy = sym.endswith('JPY'); pip = 0.01 if jpy else 0.0001; step = 1.0 if jpy else 0.01
    li, si, _, _ = signals(df, d_pips, pip, step, shift)
    cand = sorted([(i, 1) for i in li] + [(i, -1) for i in si])
    trades = []; busy_until = -1
    ts = df['ts'].values
    for i, side in cand:
        if ts[i] < busy_until: continue
        t = walk(df, i, side, d_pips, pip, Hcap, slip_bp, jpy)
        if t is None: continue
        t['sym'] = sym; trades.append(t); busy_until = t['t_out'] + 2 * H
    return trades

def portfolio(trades):
    trades = sorted(trades, key=lambda t: (t['t_in'], t['sym']))
    eq = START_EQ; open_ = []; out = []; peak = eq; mdd = 0; blocked = 0
    for t in trades:
        open_ = [o for o in open_ if o > t['t_in']]
        if len(open_) >= 2: blocked += 1; continue
        risk = eq * RISK_PCT / 100
        units = np.floor(risk * (t['rate'] if t['jpy'] else 1) / t['d'] / LOT) * LOT
        units = min(units, np.floor(LEV * eq / (1 if t['jpy'] else t['entry']) / LOT) * LOT)
        if units < 2 * LOT: continue
        u = units / 2
        pnl_q = (t['h1'] - t['entry']) * u * t['side'] + (t['h2'] - t['entry']) * u * t['side']
        pnl = pnl_q / t['h2'] if t['jpy'] else pnl_q
        notional = units if t['jpy'] else units * t['entry']
        comm = 2 * notional * COMM_BP / 1e4
        net = pnl - comm; r = net / risk
        eq += net; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
        out.append(dict(t, r=r, net=net, eq=eq, risk=risk)); open_.append(t['t_out'])
    return pd.DataFrame(out), blocked, mdd

def boot_months(t, n=10000, seed=7):
    g = t.groupby(pd.to_datetime(t['t_in'], unit='ms').dt.to_period('M'))['r'].apply(list); ms = [np.array(x) for x in g.values]
    rng = np.random.default_rng(seed); o = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(ms), len(ms)); o[i] = np.concatenate([ms[j] for j in pick]).mean()
    return np.percentile(o, 2.5), np.percentile(o, 97.5)

def main():
    t0 = time.time()
    data = {}
    for p in PAIRS:
        df = load(p); df = df[df['ts'] < LEARN_END].reset_index(drop=True); data[p] = prep(df); log(f'{p} {len(df):,} minutes (learn)')
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 17 results (FXCM minute bid/ask, comm {COMM_BP}bp/side, slip 0.5bp base, 0.5% risk split in halves, GLOBAL-2, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    cells = {}
    for d in DS:
        for Hc in HS:
            tr = []
            for p in PAIRS: tr += run_pair(data[p], p, d, Hc, 0.5)
            cells[(d, Hc)] = portfolio(tr); log(f'  d={d} H={Hc}: {len(tr)} signals -> {len(cells[(d,Hc)][0])} taken')
    P(); P('## LEARN 2012-2023, net R per trade (base costs)')
    P('| pips \\ cap | 1h | 2h | 3h |'); P('|---|---|---|---|')
    for d in DS: P(f'| {d} | ' + ' | '.join((f"{cells[(d,Hc)][0]['r'].mean():+.3f}R ({len(cells[(d,Hc)][0])})" if len(cells[(d,Hc)][0]) else 'n/a') for Hc in HS) + ' |')
    prim, blocked, mdd = cells[PRIMARY]
    P(); P(f"### primary 15 pips / 2h: {len(prim)} trades | net {prim['r'].mean():+.3f}R/trade | total {prim['r'].sum():+.1f}R | hit {(prim['r']>0).mean()*100:.0f}% | blocked by slots {blocked} | maxDD {mdd*100:.1f}%")
    ex = prim.groupby(['how1', 'how2']).size().sort_values(ascending=False)
    P('exit outcomes (first half, second half): ' + ', '.join(f'{a}/{b} {n}' for (a, b), n in ex.items()))
    for key in ('sym',):
        for v, g in prim.groupby(key): P(f'  {v}: {len(g)} trades, {g["r"].mean():+.3f}R, {g["r"].sum():+.1f}R')
    P('year | trades | R/trade | total R | $ (R x 250)')
    for y, g in prim.assign(y=pd.to_datetime(prim['t_in'], unit='ms').dt.year).groupby('y'): P(f'{y} | {len(g)} | {g["r"].mean():+.3f} | {g["r"].sum():+.1f} | {g["r"].sum()*250:+,.0f}')
    # stress + controls
    st = []; c25 = []; c75 = []
    for p in PAIRS:
        st += run_pair(data[p], p, 15, 2, 1.0); c25 += run_pair(data[p], p, 15, 2, 0.5, shift=25); c75 += run_pair(data[p], p, 15, 2, 0.5, shift=75)
    stress, _, _ = portfolio(st); ctl25, _, _ = portfolio(c25); ctl75, _, _ = portfolio(c75)
    P(); P(f"stress 1bp slip: {stress['r'].mean():+.3f}R/trade ({len(stress)}), total {stress['r'].sum():+.1f}R")
    P(f"CONTROL levels +25 pips: {ctl25['r'].mean():+.3f}R/trade ({len(ctl25)}), total {ctl25['r'].sum():+.1f}R")
    P(f"CONTROL levels +75 pips: {ctl75['r'].mean():+.3f}R/trade ({len(ctl75)}), total {ctl75['r'].sum():+.1f}R")
    lo, hi = boot_months(prim) if len(prim) > 10 else (np.nan, np.nan)
    neigh = [cells[(10, 2)], cells[(20, 2)], cells[(15, 1)], cells[(15, 3)]]
    npos = sum(1 for t, _, _ in neigh if len(t) and t['r'].mean() > 0)
    passed = len(stress) and stress['r'].mean() > 0 and lo > 0 and npos >= 3
    P(); P(f'## GATE: stress positive {stress["r"].mean() > 0}; 95% whole-month bootstrap CI [{lo:+.3f}, {hi:+.3f}]R; neighbours positive {npos}/4 -> {"PASS" if passed else "FAIL"}')
    P('## EXAM: ' + ('to be run' if passed else 'NOT INSPECTED, sealed (learn failed).'))
    prim.to_csv(os.path.join(HERE, 'hunt17_primary.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt17-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
