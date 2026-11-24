"""Hunt 14 - gotobi (Japanese payment day) conditional Tokyo-fix short, USDJPY. Frozen spec 2026-10-03."""
import sqlite3, os, time, json, calendar
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
HOL = os.path.join(HERE, '..', 'h12', 'jp_holidays.json')
SLIP_BP = float(os.environ.get('H14_SLIP_BP', '0.5'))
COMM_BP = 0.4
START_EQ = 50_000.0
RISK_PCT = 0.5; LOT = 1000; LEV = 30.0
LEARN_END = pd.Timestamp('2024-01-01'); EXAM_END = pd.Timestamp('2026-08-29')
TOL = pd.Timedelta(minutes=5)
TZ = 'Asia/Tokyo'

def log(*a): print(*a, flush=True)

def load():
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts "
                           "where c.symbol='USDJPY' and c.ts < ? order by c.ts", con, params=(int(pd.Timestamp(EXAM_END, tz='UTC').value // 10**6),))
    con.close(); df['spread'] = df['spread'].ffill().bfill()
    df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df

def hourly_atr(df):
    h = df['close'].resample('1h', label='right', closed='right').ohlc().dropna()
    pc = h['close'].shift(1)
    tr = np.maximum(h['high'] - h['low'], np.maximum((h['high'] - pc).abs(), (h['low'] - pc).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return atr                                              # indexed by bar END time (UTC)

def gotobi_dates(hol):
    out = set()
    for y in range(2012, 2027):
        for m in range(1, 13):
            last = calendar.monthrange(y, m)[1]
            for d in (5, 10, 15, 20, 25, min(30, last)):
                dt = pd.Timestamp(y, m, d)
                while dt.weekday() >= 5 or str(dt.date()) in hol: dt -= pd.Timedelta(days=1)
                if dt.weekday() == 0: continue            # Mondays excluded
                out.add(dt)
    return sorted(out)

def q(df, when):
    i = df.index.searchsorted(when)
    if i >= len(df) or (df.index[i] - when) > TOL: return None
    r = df.iloc[i]; return dict(ts=df.index[i], mid=float(r['open']), spread=float(r['spread']))

def jst(d, hm): return pd.Timestamp(f'{d:%Y-%m-%d} {hm}', tz=TZ).tz_convert('UTC')

def simulate(df, atr, dates, m=None, exit_hm='12:00', slip_bp=SLIP_BP, sized=False):
    eq = START_EQ; rows = []; skipped = 0; inel = 0
    for d in dates:
        q3, q9 = q(df, jst(d, '03:00')), q(df, jst(d, '09:00'))
        if q3 is None or q9 is None: skipped += 1; continue
        if not (q9['mid'] > q3['mid']): inel += 1; continue
        qi = q(df, jst(d, '09:55')); t_exit = jst(d, exit_hm); qo = q(df, t_exit)
        if qi is None or qo is None: skipped += 1; continue
        entry = qi['mid'] - qi['spread'] / 2 - qi['mid'] * slip_bp / 1e4         # sell at bid, slipped
        stop = None; a = None
        if m is not None:
            a_series = atr[atr.index <= jst(d, '09:00')]
            if len(a_series) == 0: skipped += 1; continue
            a = float(a_series.iloc[-1]); stop = entry + m * a
        # walk minutes for the stop
        how = 'time'; exit_px = qo['mid'] + qo['spread'] / 2 + qo['mid'] * slip_bp / 1e4; exit_ts = qo['ts']
        if stop is not None:
            seg = df.loc[qi['ts']:qo['ts']]
            ask_hi = seg['high'].values + seg['spread'].values / 2
            hit = np.where(ask_hi >= stop)[0]
            if len(hit):
                k = hit[0]; row = seg.iloc[k]
                ask_open = row['open'] + row['spread'] / 2
                fill = max(stop, ask_open) if ask_open > stop else stop
                exit_px = fill + row['open'] * slip_bp / 1e4; exit_ts = seg.index[k]; how = 'stop'
        gross_bp = (qi['mid'] - (qo['mid'] if how == 'time' else exit_px)) / qi['mid'] * 1e4
        net_bp = (entry - exit_px) / qi['mid'] * 1e4 - 2 * COMM_BP
        cost_bp = gross_bp - net_bp
        if sized:
            risk_usd = eq * RISK_PCT / 100; dist = stop - entry
            units = np.floor(risk_usd * qi['mid'] / dist / LOT) * LOT
            units = min(units, np.floor(LEV * eq / LOT) * LOT)
            if units < LOT: skipped += 1; continue
            pnl_jpy = (entry - exit_px) * units; pnl = pnl_jpy / exit_px - 2 * units * COMM_BP / 1e4
            r = pnl / risk_usd
            rows.append(dict(day=d, gross_bp=gross_bp, net_bp=net_bp, cost_bp=cost_bp, how=how, r=r, pnl=pnl, ret=pnl / eq, atr=a, entry=entry, stop=stop, exit=exit_px, exit_ts=exit_ts))
            eq += pnl
        else:
            pnl = eq * net_bp / 1e4
            rows.append(dict(day=d, gross_bp=gross_bp, net_bp=net_bp, cost_bp=cost_bp, how=how, pnl=pnl, ret=pnl / eq, entry=entry, exit=exit_px, exit_ts=exit_ts))
            eq += pnl
    return pd.DataFrame(rows), dict(eq=eq, skipped=skipped, ineligible=inel)

def boot_months(t, n=10000, seed=7):
    mk = t.groupby(pd.to_datetime(t['day']).dt.to_period('M'))['ret'].apply(list)
    ms = [np.array(x) for x in mk.values]; rng = np.random.default_rng(seed); out = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(ms), len(ms)); out[i] = np.concatenate([ms[j] for j in pick]).mean()
    return np.percentile(out, 2.5), np.percentile(out, 97.5)

def blocks(t, key):
    res = {}
    for nm, a, b in [('2012-17', '2012-01-01', '2018-01-01'), ('2018-20 paper', '2018-01-01', '2021-01-01'), ('2021-23', '2021-01-01', '2024-01-01')]:
        s = t[(t['day'] >= a) & (t['day'] < b)]
        res[nm] = (len(s), float(s[key].mean()) if len(s) else float('nan'), float(s[key].sum()) if len(s) else 0.0)
    return res

def main():
    t0 = time.time(); hol = set(json.load(open(HOL))); df = load(); atr = hourly_atr(df)
    dates = gotobi_dates(hol); learn = [d for d in dates if d < LEARN_END and d >= pd.Timestamp('2012-01-02')]; exam = [d for d in dates if d >= LEARN_END and d < EXAM_END]
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 14 results (USDJPY FXCM minute bid/ask, comm {COMM_BP}bp/side, slip {SLIP_BP}bp/execution, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P(f'gotobi dates 2012-2023 after holiday/weekend roll and Monday exclusion: {len(learn)}')
    P(); P('## RUN 1, source replication: 1x notional, short 09:55 -> 12:00 JST, no stop. LEARN 2012-2023')
    t1, i1 = simulate(df, atr, learn)
    P(f"eligible trades {len(t1)} (ineligible mornings {i1['ineligible']}, skipped for missing quotes {i1['skipped']}) | gross {t1['gross_bp'].mean():+.2f} bp | net {t1['net_bp'].mean():+.2f} bp | costs {t1['cost_bp'].mean():.2f} bp | hit {(t1['net_bp']>0).mean()*100:.0f}% | compounded {START_EQ:,.0f} -> {i1['eq']:,.0f} ({(i1['eq']/START_EQ-1)*100:+.2f}%)")
    for nm, (n, mu, tot) in blocks(t1, 'net_bp').items(): P(f'  {nm}: {n} trades, net {mu:+.2f} bp/trade')
    P('year | trades | gross bp | net bp')
    for y, g in t1.assign(y=pd.to_datetime(t1['day']).dt.year).groupby('y'): P(f"{y} | {len(g)} | {g['gross_bp'].mean():+.2f} | {g['net_bp'].mean():+.2f}")
    P(); P('## RUN 2, account adaptation: ATR14(1h) stop grid, 0.5% risk sizing. LEARN 2012-2023, net R per trade (base costs)')
    P('| stop m \\ exit | 11:00 | 12:00 | 13:00 |'); P('|---|---|---|---|')
    grid = {}
    for m in (2, 3, 4):
        cells = []
        for ex in ('11:00', '12:00', '13:00'):
            t, info = simulate(df, atr, learn, m=m, exit_hm=ex, sized=True); grid[(m, ex)] = t
            cells.append(f"{t['r'].mean():+.3f}R ({len(t)})" if len(t) else 'n/a')
        P(f'| {m} | ' + ' | '.join(cells) + ' |')
    prim = grid[(3, '12:00')]
    ps, _ = simulate(df, atr, learn, m=3, exit_hm='12:00', sized=True, slip_bp=1.0)
    lo, hi = boot_months(prim)
    bl = blocks(prim, 'r')
    neigh = [grid[(2, '12:00')], grid[(4, '12:00')], grid[(3, '11:00')], grid[(3, '13:00')]]
    npos = sum(1 for t in neigh if len(t) and t['r'].mean() > 0)
    P()
    P(f"### primary m=3, exit 12:00: {len(prim)} trades | net {prim['r'].mean():+.3f}R/trade | total {prim['r'].sum():+.1f}R | hit {(prim['r']>0).mean()*100:.0f}% | stops {(prim['how']=='stop').sum()} | time exits {(prim['how']=='time').sum()} | worst {prim['r'].min():+.2f}R | losses beyond -1R: {(prim['r']<-1).sum()} | avg cost {prim['cost_bp'].mean():.2f} bp | compounded {(prim['eq'] if 'eq' in prim else 0) if False else ''}{(1+prim['ret']).prod()*START_EQ:,.0f}")
    P(f"stress 1bp slip: {ps['r'].mean():+.3f}R/trade, total {ps['r'].sum():+.1f}R")
    for nm, (n, mu, tot) in bl.items(): P(f'  {nm}: {n} trades, {mu:+.3f}R/trade, {tot:+.1f}R')
    P('year | trades | net R/trade | total R')
    for y, g in prim.assign(y=pd.to_datetime(prim['day']).dt.year).groupby('y'): P(f"{y} | {len(g)} | {g['r'].mean():+.3f} | {g['r'].sum():+.1f}")
    passed = ps['r'].mean() > 0 and lo > 0 and bl['2012-17'][1] > 0 and bl['2021-23'][1] > 0 and npos >= 3
    P(f"## GATE: stress positive {ps['r'].mean() > 0}; 95% whole-month bootstrap CI of per-trade return [{lo*1e4:+.1f}, {hi*1e4:+.1f}] bp of equity, lower bound > 0: {lo > 0}; "
      f"non-paper blocks positive: 2012-17 {bl['2012-17'][1] > 0}, 2021-23 {bl['2021-23'][1] > 0}; neighbours positive {npos}/4 -> {'PASS' if passed else 'FAIL'}")
    # sealed exam
    te, ie = simulate(df, atr, exam, m=3, exit_hm='12:00', sized=True); t1e, i1e = simulate(df, atr, exam)
    json.dump(dict(rep=dict(n=int(len(t1e)), net_bp=float(t1e['net_bp'].mean()) if len(t1e) else None), prim=dict(n=int(len(te)), r=float(te['r'].mean()) if len(te) else None)),
              open(os.path.join(HERE, 'hunt14_exam_sealed.json'), 'w'))
    if passed:
        P(); P('## EXAM 2024 -> 2026-08-28')
        P(f"replication: {len(t1e)} trades, net {t1e['net_bp'].mean():+.2f} bp; primary: {len(te)} trades, {te['r'].mean():+.3f}R/trade, {te['r'].sum():+.1f}R")
    else: P('## EXAM: NOT INSPECTED, sealed.')
    # charts
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        cd = os.path.join(OUT, 'hunt14-charts'); os.makedirs(cd, exist_ok=True)
        pick = [prim.iloc[int(i)] for i in np.linspace(0, len(prim) - 1, min(20, len(prim)))] if len(prim) else []
        for n, t in enumerate(pick):
            d = t['day']; a = jst(d, '00:00'); b = jst(d, '16:00')
            seg = df.loc[a:b, 'close'].resample('5min').last().dropna()
            fig, ax = plt.subplots(figsize=(10, 4)); ax.plot(seg.index.tz_convert(TZ), seg.values, color='#888', lw=1)
            ax.axhline(t['entry'], color='#e74c3c', lw=.8); ax.axhline(t['stop'], color='#2ecc71', lw=.8)
            for hm, c in (('03:00', '#aaa'), ('09:00', '#aaa'), ('09:55', '#e74c3c'), ('12:00', 'k')): ax.axvline(jst(d, hm).tz_convert(TZ), color=c, lw=.8, ls='--')
            ax.set_title(f"{n+1:02d} {d.date()} short 09:55 JST, {t['how']} {t['r']:+.2f}R"); fig.tight_layout(); fig.savefig(os.path.join(cd, f'{n+1:02d}-{d.date()}.png'), dpi=90); plt.close(fig)
        P(f'charts: {len(pick)} in specs/hunt14-charts/')
    except Exception as e: P(f'charts failed: {e}')
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, f'hunt14-results-slip{SLIP_BP}.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
