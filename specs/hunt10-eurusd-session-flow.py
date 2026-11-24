"""Hunt 10 - Breedon & Ranaldo EURUSD session schedule, FXCM minute replication (frozen spec 2026-10-03)."""
import sqlite3, os, sys, time, json
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
SLIP_BP = float(os.environ.get('H10_SLIP_BP', '0.5'))
COMM_BP = 0.4
START_EQ = 50_000.0
LEARN_END = pd.Timestamp('2024-01-01', tz='UTC')
EXAM_END = pd.Timestamp('2026-08-29', tz='UTC')
TOL = 5  # minutes a boundary quote may be late before the leg is skipped

def log(*a): print(*a, flush=True)

def load():
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s "
                           "on s.symbol=c.symbol and s.ts=c.ts where c.symbol='EURUSD' and c.ts < ? order by c.ts",
                           con, params=(int(EXAM_END.value // 10**6),))
    con.close()
    df['spread'] = df['spread'].ffill().bfill()
    df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df

def quote_at(df, when):
    """open mid + spread at the first minute >= when, within TOL minutes; else None."""
    i = df.index.searchsorted(when)
    if i >= len(df): return None
    t = df.index[i]
    if (t - when) > pd.Timedelta(minutes=TOL): return None
    return dict(ts=t, mid=float(df['open'].iat[i]), spread=float(df['spread'].iat[i]), late=(t - when).total_seconds() / 60)

def schedule(days):
    """For each weekday: London 07:00 open, NY 08:00, NY 16:00 (UTC timestamps) with each zone's own DST."""
    rows = []
    for d in days:
        lon = pd.Timestamp(d.strftime('%Y-%m-%d') + ' 07:00', tz='Europe/London').tz_convert('UTC')
        ny8 = pd.Timestamp(d.strftime('%Y-%m-%d') + ' 08:00', tz='America/New_York').tz_convert('UTC')
        ny16 = pd.Timestamp(d.strftime('%Y-%m-%d') + ' 16:00', tz='America/New_York').tz_convert('UTC')
        ny17 = pd.Timestamp(d.strftime('%Y-%m-%d') + ' 17:00', tz='America/New_York').tz_convert('UTC')
        rows.append((d, lon, ny8, ny16, ny17))
    return rows

def leg(df, t_in, t_out, side, eq):
    """side +1 long / -1 short, notional 1x equity. Returns dict or None if a boundary quote is missing."""
    qi = quote_at(df, t_in); qo = quote_at(df, t_out)
    if qi is None or qo is None: return None
    slip_in = qi['mid'] * SLIP_BP / 1e4; slip_out = qo['mid'] * SLIP_BP / 1e4
    if side == 1:
        px_in = qi['mid'] + qi['spread'] / 2 + slip_in; px_out = qo['mid'] - qo['spread'] / 2 - slip_out
    else:
        px_in = qi['mid'] - qi['spread'] / 2 - slip_in; px_out = qo['mid'] + qo['spread'] / 2 + slip_out
    units = eq / qi['mid']                       # EUR units for 1x USD equity
    gross_bp = side * (qo['mid'] - qi['mid']) / qi['mid'] * 1e4
    spread_bp = (qi['spread'] / 2 + qo['spread'] / 2) / qi['mid'] * 1e4
    slip_bp = 2 * SLIP_BP
    comm_bp = 2 * COMM_BP
    net_bp = side * (px_out - px_in) / qi['mid'] * 1e4 - comm_bp
    pnl = net_bp / 1e4 * eq
    # minute mark-to-market path on mid for drawdown
    seg = df.loc[qi['ts']:qo['ts'], 'close'].values
    mtm = side * (seg - qi['mid']) / qi['mid'] * eq
    return dict(t_in=qi['ts'], t_out=qo['ts'], side=side, gross_bp=gross_bp, net_bp=net_bp, spread_bp=spread_bp,
                slip_bp=slip_bp, comm_bp=comm_bp, pnl=pnl, mtm=mtm, late=max(qi['late'], qo['late']))

def run(df, sched, mode):
    """mode: 'short' (morning only), 'long' (afternoon only), 'combined'. Compounded daily."""
    eq = START_EQ; peak = eq; maxdd = 0.0
    trades = []; daily = []; skipped = 0; crossed = 0
    for d, lon, ny8, ny16, ny17 in sched:
        day_pnl = 0.0; day_legs = []
        eq0 = eq
        if mode in ('short', 'combined'):
            L = leg(df, lon, ny8, -1, eq0)
            if L is None: skipped += 1
            else: day_legs.append(L)
        if mode in ('long', 'combined'):
            L = leg(df, ny8, ny16, +1, eq0)
            if L is None: skipped += 1
            else: day_legs.append(L)
        if not day_legs: continue
        path = eq
        for L in day_legs:
            if L['t_out'] > ny17: crossed += 1; L['pnl'] -= eq0 * 5 / 1e4
            for v in L['mtm']:
                x = path + v; peak = max(peak, x); maxdd = max(maxdd, (peak - x) / peak)
            path += L['pnl']; day_pnl += L['pnl']
            trades.append(dict(day=d, side=L['side'], gross_bp=L['gross_bp'], net_bp=L['net_bp'], spread_bp=L['spread_bp'],
                               slip_bp=L['slip_bp'], comm_bp=L['comm_bp'], pnl=L['pnl'], ret=L['pnl'] / eq0))
        eq += day_pnl; peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak)
        daily.append(dict(day=d, ret=day_pnl / eq0, pnl=day_pnl, eq=eq))
    return pd.DataFrame(trades), pd.DataFrame(daily), dict(final_eq=eq, maxdd=maxdd, skipped=skipped, crossed=crossed)

def boot_weeks(daily, n=10000, seed=7):
    wk = daily.groupby(pd.to_datetime(daily['day']).dt.to_period('W'))['ret'].apply(list)
    weeks = [np.array(w) for w in wk.values]
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(weeks), len(weeks))
        means[i] = np.concatenate([weeks[j] for j in pick]).mean()
    return np.percentile(means, 2.5), np.percentile(means, 97.5)

def report(name, tr, dy, info, P):
    if len(tr) == 0: P(f'### {name}: no trades'); return None
    yrs = dy.assign(y=pd.to_datetime(dy['day']).dt.year).groupby('y')['ret'].agg(['count', 'mean', 'sum'])
    sharpe = dy['ret'].mean() / dy['ret'].std(ddof=1) * np.sqrt(252) if dy['ret'].std(ddof=1) > 0 else float('nan')
    P(f'### {name}')
    P(f"trades {len(tr)}  | gross {tr['gross_bp'].mean():+.2f} bp/trade | net {tr['net_bp'].mean():+.2f} bp/trade | "
      f"costs: spread {tr['spread_bp'].mean():.2f} + slip {tr['slip_bp'].mean():.2f} + comm {tr['comm_bp'].mean():.2f} bp")
    P(f"days {len(dy)} | mean daily net {dy['ret'].mean()*1e4:+.2f} bp | daily Sharpe {sharpe:+.2f} | "
      f"compounded {START_EQ:,.0f} -> {info['final_eq']:,.0f} ({(info['final_eq']/START_EQ-1)*100:+.1f}%) | minute MTM maxDD {info['maxdd']*100:.1f}% | "
      f"legs skipped (missing boundary quote) {info['skipped']} | legs crossing 17:00 NY {info['crossed']}")
    P('year | days | mean daily bp | year return %')
    for y, r in yrs.iterrows(): P(f'{y} | {int(r["count"])} | {r["mean"]*1e4:+.2f} | {((1+dy[pd.to_datetime(dy["day"]).dt.year==y]["ret"]).prod()-1)*100:+.2f}')
    return sharpe

def main():
    t0 = time.time()
    df = load(); log(f'{len(df):,} EURUSD minutes')
    days = pd.bdate_range(df.index[0].normalize().tz_localize(None), EXAM_END.tz_localize(None) - pd.Timedelta(days=1))
    sched = schedule(days)
    learn_s = [r for r in sched if pd.Timestamp(r[0], tz='UTC') < LEARN_END]
    exam_s = [r for r in sched if pd.Timestamp(r[0], tz='UTC') >= LEARN_END]
    lines = []
    P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 10 results (slip {SLIP_BP}bp, comm {COMM_BP}bp/side, actual FXCM spread, 1x equity notional, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P('Clock: short 07:00 London -> 08:00 New York; long 08:00 -> 16:00 New York; each zone its own DST.')
    P()
    P('## LEARN 2012-2023')
    res = {}
    for mode, name in [('combined', 'COMBINED (primary): short morning, reverse long at 08:00 NY, flat 16:00 NY'),
                       ('short', 'Morning SHORT leg alone'), ('long', 'Afternoon LONG leg alone')]:
        tr, dy, info = run(df, learn_s, mode)
        res[mode] = (tr, dy, info)
        report(name, tr, dy, info, P); P()
    tr, dy, info = res['combined']
    lo, hi = boot_weeks(dy)
    mean = dy['ret'].mean()
    passed = mean > 0 and lo > 0
    P(f'## GATE (combined): mean daily net {mean*1e4:+.2f} bp; 95% CI from 10,000 whole-week bootstraps [{lo*1e4:+.2f}, {hi*1e4:+.2f}] bp -> {"PASS" if passed else "FAIL"}')
    P(f"Paper (EBS 1997-2007, interdealer costs): EUR session +6%/yr, USD session +7%/yr, Sharpe 1.3 / 0.9. Here, retail costs 2012-2023: see above.")
    # sealed exam
    sealed = {}
    for mode in ('combined', 'short', 'long'):
        tr2, dy2, info2 = run(df, exam_s, mode)
        sealed[mode] = dict(trades=int(len(tr2)), net_bp=float(tr2['net_bp'].mean()) if len(tr2) else None,
                            mean_daily_bp=float(dy2['ret'].mean() * 1e4) if len(dy2) else None, final_eq=info2['final_eq'], maxdd=info2['maxdd'],
                            years={int(y): float(v) for y, v in dy2.assign(y=pd.to_datetime(dy2['day']).dt.year).groupby('y')['ret'].sum().items()} if len(dy2) else {})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f'hunt10_exam_sealed_slip{SLIP_BP}.json'), 'w') as f: json.dump(sealed, f, indent=1)
    if passed:
        P(); P('## EXAM 2024 -> 2026-08-28 (inspected because learn passed)')
        for mode, name in [('combined', 'COMBINED'), ('short', 'Morning SHORT'), ('long', 'Afternoon LONG')]:
            tr2, dy2, info2 = run(df, exam_s, mode); report(name, tr2, dy2, info2, P); P()
    else:
        P(); P('## EXAM: NOT INSPECTED (learn failed). Sealed in scratchpad hunt10_exam_sealed json.')
    P(f'run time {time.time()-t0:.0f}s')
    with open(os.path.join(OUT, f'hunt10-results-slip{SLIP_BP}.md'), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
