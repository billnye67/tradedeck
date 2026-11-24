"""Hunt 12 - FX fixing reversal (Krohn/Mueller/Whelan), EURUSD Europe + USDJPY Tokyo modules, FXCM minute bid/ask."""
import sqlite3, os, sys, time, json, urllib.request
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
SLIP_BP = float(os.environ.get('H12_SLIP_BP', '0.5'))
SWAP_BP = float(os.environ.get('H12_SWAP_BP', '0'))
COMM_BP = 0.4
START_EQ = 50_000.0
LEARN_END = pd.Timestamp('2024-01-01', tz='UTC')
VAL_START = pd.Timestamp('2023-01-01', tz='UTC')
EXAM_END = pd.Timestamp('2026-08-29', tz='UTC')
TOL = 5

def log(*a): print(*a, flush=True)

def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts "
                           "where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, int(EXAM_END.value // 10**6)))
    con.close()
    df['spread'] = df['spread'].ffill().bfill()
    df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df

def jp_holidays():
    p = os.path.join(HERE, 'jp_holidays.json')
    if os.path.exists(p): return set(json.load(open(p)))
    out = set()
    for y in range(2012, 2027):
        try:
            with urllib.request.urlopen(f'https://date.nager.at/api/v3/PublicHolidays/{y}/JP', timeout=30) as r:
                for h in json.load(r): out.add(h['date'])
        except Exception as e: log('holiday fetch failed', y, e)
    json.dump(sorted(out), open(p, 'w'))
    return out

def quote_at(df, when):
    i = df.index.searchsorted(when)
    if i >= len(df): return None
    t = df.index[i]
    if (t - when) > pd.Timedelta(minutes=TOL): return None
    return dict(ts=t, mid=float(df['open'].iat[i]), spread=float(df['spread'].iat[i]))

def leg(df, t_in, t_out, side, eq, jpy, swap_touch):
    qi = quote_at(df, t_in); qo = quote_at(df, t_out)
    if qi is None or qo is None: return None
    si = qi['mid'] * SLIP_BP / 1e4; so = qo['mid'] * SLIP_BP / 1e4
    if side == 1: px_in = qi['mid'] + qi['spread'] / 2 + si; px_out = qo['mid'] - qo['spread'] / 2 - so
    else: px_in = qi['mid'] - qi['spread'] / 2 - si; px_out = qo['mid'] + qo['spread'] / 2 + so
    gross_bp = side * (qo['mid'] - qi['mid']) / qi['mid'] * 1e4
    spread_bp = (qi['spread'] / 2 + qo['spread'] / 2) / qi['mid'] * 1e4
    swap_bp = SWAP_BP * swap_touch
    net_bp = side * (px_out - px_in) / qi['mid'] * 1e4 - 2 * COMM_BP - swap_bp
    seg = df.loc[qi['ts']:qo['ts'], 'close'].values
    mtm = side * (seg - qi['mid']) / qi['mid'] * eq
    return dict(t_in=qi['ts'], t_out=qo['ts'], side=side, gross_bp=gross_bp, net_bp=net_bp, spread_bp=spread_bp, slip_bp=2 * SLIP_BP,
                comm_bp=2 * COMM_BP, swap_bp=swap_bp, pnl=net_bp / 1e4 * eq, mtm=mtm)

def ny(d, hm): return pd.Timestamp(f'{d:%Y-%m-%d} {hm}', tz='America/New_York').tz_convert('UTC')

def schedule_eur(days):
    return [(d, [(ny(d, '02:00'), ny(d, '08:15'), -1, 0), (ny(d, '11:00'), ny(d, '17:00'), +1, 1)]) for d in days]

def schedule_jpy(days, hol):
    rows = []
    for d in days:
        # the Tokyo fix that follows 17:00 ET of day d is on the next Tokyo calendar date
        tk_date = (ny(d, '17:00').tz_convert('Asia/Tokyo') + pd.Timedelta(hours=10)).date()
        if str(tk_date) in hol: continue
        fix = pd.Timestamp(f'{tk_date:%Y-%m-%d} 09:55', tz='Asia/Tokyo').tz_convert('UTC')
        nxt = d + pd.Timedelta(days=1)
        rows.append((d, [(ny(d, '17:00'), fix, +1, 1), (fix, ny(nxt, '02:00'), -1, 0)]))
    return rows

def run(df, sched, jpy):
    eq = START_EQ; peak = eq; maxdd = 0.0; trades = []; daily = []; skipped = 0
    for d, legs in sched:
        eq0 = eq; day_pnl = 0.0; got = []
        for t_in, t_out, side, swap_touch in legs:
            L = leg(df, t_in, t_out, side, eq0, jpy, swap_touch)
            if L is None: skipped += 1
            else: got.append(L)
        if not got: continue
        path = eq
        for L in got:
            for v in L['mtm']:
                x = path + v; peak = max(peak, x); maxdd = max(maxdd, (peak - x) / peak)
            path += L['pnl']; day_pnl += L['pnl']
            trades.append(dict(day=d, side=L['side'], gross_bp=L['gross_bp'], net_bp=L['net_bp'], spread_bp=L['spread_bp'],
                               slip_bp=L['slip_bp'], comm_bp=L['comm_bp'], swap_bp=L['swap_bp'], pnl=L['pnl']))
        eq += day_pnl; peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak)
        daily.append(dict(day=d, ret=day_pnl / eq0, eq=eq))
    return pd.DataFrame(trades), pd.DataFrame(daily), dict(final_eq=eq, maxdd=maxdd, skipped=skipped)

def boot_weeks(daily, n=10000, seed=7):
    wk = daily.groupby(pd.to_datetime(daily['day']).dt.to_period('W'))['ret'].apply(list)
    weeks = [np.array(w) for w in wk.values]; rng = np.random.default_rng(seed); means = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(weeks), len(weeks)); means[i] = np.concatenate([weeks[j] for j in pick]).mean()
    return np.percentile(means, 2.5), np.percentile(means, 97.5)

def report(name, tr, dy, info, P):
    if len(tr) == 0: P(f'### {name}: no trades'); return
    sharpe = dy['ret'].mean() / dy['ret'].std(ddof=1) * np.sqrt(252)
    P(f'### {name}')
    P(f"legs {len(tr)} | gross {tr['gross_bp'].mean():+.2f} bp/leg | net {tr['net_bp'].mean():+.2f} bp/leg | costs/leg: spread {tr['spread_bp'].mean():.2f} + slip {tr['slip_bp'].mean():.2f} + comm {tr['comm_bp'].mean():.2f} + swap {tr['swap_bp'].mean():.2f} bp")
    for s, nm in [(-1, 'short legs'), (1, 'long legs')]:
        t = tr[tr['side'] == s]
        if len(t): P(f"  {nm}: {len(t)}, gross {t['gross_bp'].mean():+.2f}, net {t['net_bp'].mean():+.2f} bp")
    P(f"days {len(dy)} | mean daily net {dy['ret'].mean()*1e4:+.2f} bp | daily Sharpe {sharpe:+.2f} | compounded {START_EQ:,.0f} -> {info['final_eq']:,.0f} ({(info['final_eq']/START_EQ-1)*100:+.1f}%) | minute MTM maxDD {info['maxdd']*100:.1f}% | legs skipped {info['skipped']}")
    yrs = dy.assign(y=pd.to_datetime(dy['day']).dt.year)
    P('year | days | mean daily bp | year return %')
    for y, g in yrs.groupby('y'): P(f'{y} | {len(g)} | {g["ret"].mean()*1e4:+.2f} | {((1+g["ret"]).prod()-1)*100:+.2f}')

def main():
    t0 = time.time(); hol = jp_holidays(); log(f'{len(hol)} Japanese holidays 2012-2026')
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 12 results (FXCM minute bid/ask, comm {COMM_BP}bp + slip {SLIP_BP}bp per execution, swap {SWAP_BP}bp per 17:00 ET boundary touched, 1x equity, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    sealed = {}
    for sym, name, build in [('EURUSD', 'EURUSD Europe module: short 02:00 -> 08:15 ET (ECB fix), long 11:00 (London fix) -> 17:00 ET', lambda days: schedule_eur(days)),
                             ('USDJPY', 'USDJPY Tokyo module: long 17:00 ET -> Tokyo fix 09:55 JST, reverse short -> 02:00 ET; JP holidays skipped', lambda days: schedule_jpy(days, hol))]:
        df = load(sym); log(f'{sym} {len(df):,} minutes')
        days = pd.bdate_range(df.index[0].normalize().tz_localize(None), EXAM_END.tz_localize(None) - pd.Timedelta(days=1))
        sched = build(days)
        learn = [r for r in sched if pd.Timestamp(r[0], tz='UTC') < LEARN_END]; exam = [r for r in sched if pd.Timestamp(r[0], tz='UTC') >= LEARN_END]
        P(); P(f'## {name}'); P('### LEARN 2012-2023')
        tr, dy, info = run(df, learn, sym.endswith('JPY')); report(name, tr, dy, info, P)
        lo, hi = boot_weeks(dy); mean = dy['ret'].mean()
        v = dy[pd.to_datetime(dy['day']) >= VAL_START.tz_localize(None)]['ret']
        val_pos = len(v) and v.mean() > 0
        passed = mean > 0 and lo > 0 and val_pos
        P(f'GATE: mean daily net {mean*1e4:+.2f} bp, 95% whole-week bootstrap CI [{lo*1e4:+.2f}, {hi*1e4:+.2f}] bp, 2023 alone {v.mean()*1e4 if len(v) else float("nan"):+.2f} bp/day -> {"PASS" if passed else "FAIL"}')
        tr2, dy2, info2 = run(df, exam, sym.endswith('JPY'))
        sealed[sym] = dict(legs=int(len(tr2)), net_bp=float(tr2['net_bp'].mean()) if len(tr2) else None, mean_daily_bp=float(dy2['ret'].mean() * 1e4) if len(dy2) else None, final_eq=info2['final_eq'])
        if passed: P('### EXAM 2024 -> 2026-08-28 (learn passed)'); report(name, tr2, dy2, info2, P)
        else: P('### EXAM: NOT INSPECTED (learn failed), sealed.')
    json.dump(sealed, open(os.path.join(HERE, f'hunt12_exam_sealed_slip{SLIP_BP}_swap{SWAP_BP}.json'), 'w'), indent=1)
    P(); P('Paper (G9, 1999-2019, midquotes): Tokyo reversal +4.09bp/day, Europe +3.51bp/day; USDJPY Tokyo +4.76bp, EURUSD Europe +6.26bp. Their own bid/ask test: significantly negative for liquidity demanders.')
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, f'hunt12-results-slip{SLIP_BP}-swap{SWAP_BP}.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
