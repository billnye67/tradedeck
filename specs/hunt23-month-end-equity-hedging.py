"""Hunt 23 - month-end equity-hedging flow into the London 4pm fix (Perplexity candidate 40), frozen 2026-10-04."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
MAP = {'EURUSD': 'STOXX50E', 'GBPUSD': 'FTSE', 'AUDUSD': 'AXJO', 'USDJPY': 'N225'}
COMM_BP = 0.4; STOP = 0.005; RISK = 0.0025; START_EQ = 50_000.0
LON = 'Europe/London'
END = pd.Timestamp('2026-08-29', tz='UTC')

def log(*a): print(*a, flush=True)

def load_fx(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, int(END.value // 10**6)))
    con.close(); df['spread'] = df['spread'].ffill().bfill(); df.index = pd.to_datetime(df['ts'], unit='ms', utc=True); return df

def load_idx(name):
    j = json.load(open(os.path.join(HERE, f'y_{name}.json')))['chart']['result'][0]
    tz = j['meta']['exchangeTimezoneName']
    t = pd.to_datetime(j['timestamp'], unit='s', utc=True).tz_convert(tz).normalize().tz_localize(None)
    s = pd.Series(j['indicators']['quote'][0]['close'], index=t).dropna()
    return s[~s.index.duplicated()].sort_index()

def month_end_events():
    days = pd.bdate_range('2012-01-01', END.tz_localize(None))
    days = days[~((days.month == 12) & (days.day == 25)) & ~((days.month == 1) & (days.day == 1))]
    return [g.iloc[-1] for _, g in pd.Series(days).groupby([days.year, days.month])]

def q(df, when):
    i = df.index.searchsorted(when)
    if i >= len(df) or (df.index[i] - when) > pd.Timedelta(minutes=5): return None
    r = df.iloc[i]; return dict(ts=df.index[i], mid=float(r['open']), spread=float(r['spread']))

def run(fx, idx, slip_bp):
    rows = []; skipped = []
    for ev in month_end_events():
        t_in = pd.Timestamp(f'{ev:%Y-%m-%d} 15:00', tz=LON).tz_convert('UTC'); t_out = pd.Timestamp(f'{ev:%Y-%m-%d} 16:05', tz=LON).tz_convert('UTC')
        prev_month_end = (ev.replace(day=1) - pd.Timedelta(days=1))
        cands = []
        for pair, name in MAP.items():
            s = idx[name]
            before = s[s.index < ev]                      # no event-day prices
            if len(before) == 0: continue
            last = before.index[-1]
            if (ev - last).days > 4 or len(pd.bdate_range(last, ev)) - 1 > 2: continue   # close within previous two business days
            base = s[s.index <= prev_month_end]
            if len(base) == 0: continue
            mtd = float(before.iloc[-1] / base.iloc[-1] - 1)
            cands.append((abs(mtd), mtd, pair))
        if not cands: skipped.append((ev.date(), 'no index')); continue
        _, mtd, pair = max(cands)
        if mtd == 0: skipped.append((ev.date(), 'zero')); continue
        # direction: index up -> sell local currency. EURUSD/GBPUSD/AUDUSD: short; USDJPY: long (buy USD, sell JPY)
        side = (-1 if mtd > 0 else 1) if pair != 'USDJPY' else (1 if mtd > 0 else -1)
        df = fx[pair]; qi, qo = q(df, t_in), q(df, t_out)
        if qi is None or qo is None: skipped.append((ev.date(), f'{pair} quote')); continue
        entry = (qi['mid'] + qi['spread'] / 2) * (1 + slip_bp / 1e4) if side == 1 else (qi['mid'] - qi['spread'] / 2) * (1 - slip_bp / 1e4)
        stop = entry * (1 - STOP) if side == 1 else entry * (1 + STOP)
        seg = df.loc[qi['ts'] + pd.Timedelta(minutes=1):qo['ts'] - pd.Timedelta(microseconds=1)]
        how = 'time'; px = None; mae = 0.0
        if len(seg):
            if side == 1:
                bid = seg['low'] - seg['spread'] / 2; mae = float(bid.min() / entry - 1); hit = np.where(bid.values <= stop)[0]
                if len(hit): r = seg.iloc[hit[0]]; o = r['open'] - r['spread'] / 2; px = (min(stop, o) if o < stop else stop) * (1 - slip_bp / 1e4); how = 'stop'
            else:
                ask = seg['high'] + seg['spread'] / 2; mae = float(entry / ask.max() - 1); hit = np.where(ask.values >= stop)[0]
                if len(hit): r = seg.iloc[hit[0]]; o = r['open'] + r['spread'] / 2; px = (max(stop, o) if o > stop else stop) * (1 + slip_bp / 1e4); how = 'stop'
        if px is None: px = (qo['mid'] - qo['spread'] / 2) * (1 - slip_bp / 1e4) if side == 1 else (qo['mid'] + qo['spread'] / 2) * (1 + slip_bp / 1e4)
        gross_bp = (qo['mid'] / qi['mid'] - 1) * 1e4 * side
        net_bp = ((px / entry - 1) if side == 1 else (entry / px - 1)) * 1e4 - 2 * COMM_BP
        rows.append(dict(ev=ev, pair=pair, mtd=mtd, side=side, gross_bp=gross_bp, net_bp=net_bp, how=how, mae_bp=mae * 1e4, t_in=qi['ts'], t_out=qo['ts']))
    return pd.DataFrame(rows), skipped

def boot(t, n=10000, seed=7):
    x = t['net_bp'].values; rng = np.random.default_rng(seed); m = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)]); return np.percentile(m, 2.5), np.percentile(m, 97.5)

def main():
    t0 = time.time(); fx = {p: load_fx(p) for p in MAP}; idx = {n: load_idx(n) for n in MAP.values()}
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 23 results (FXCM minute bid/ask, 15:00 -> 16:05 London on the last FX business day, 50bp stop, comm {COMM_BP}bp/side, slip 0.5bp base, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    for n, s in idx.items(): P(f'  index {n}: {len(s)} closes {s.index[0].date()} .. {s.index[-1].date()}')
    base, sk = run(fx, idx, 0.5); stress, _ = run(fx, idx, 1.5)
    P(f"events traded {len(base)}, skipped {len(sk)}: {sk[:8]}{'...' if len(sk) > 8 else ''}")
    lo, hi = boot(base)
    P(f"BASE: gross {base['gross_bp'].mean():+.2f} bp/event | net {base['net_bp'].mean():+.2f} bp | month-bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}] | win {(base['net_bp']>0).mean()*100:.0f}% | median {base['net_bp'].median():+.2f} | stops {(base['how']=='stop').sum()} | MAE mean {base['mae_bp'].mean():+.1f} / worst {base['mae_bp'].min():+.0f} bp | worst event {base['net_bp'].min():+.0f} bp")
    P(f"STRESS 1.5bp: net {stress['net_bp'].mean():+.2f} bp")
    ex5 = base.drop(base['net_bp'].nlargest(5).index); P(f"ex-5-best: {ex5['net_bp'].mean():+.2f} bp")
    P('period | events | gross | net | win %')
    for nm, a, b in [('2012-14 paper', '2012-01-01', '2015-01-01'), ('Feb2015-19', '2015-02-01', '2020-01-01'), ('2020-23', '2020-01-01', '2024-01-01'), ('2024-26 exam', '2024-01-01', '2027-01-01'), ('Feb2015-26', '2015-02-01', '2027-01-01'), ('full', '2012-01-01', '2027-01-01')]:
        s = base[(base['ev'] >= a) & (base['ev'] < b)]
        if len(s): P(f"{nm} | {len(s)} | {s['gross_bp'].mean():+.2f} | {s['net_bp'].mean():+.2f} | {(s['net_bp']>0).mean()*100:.0f}")
    for p, g in base.groupby('pair'): P(f"  {p}: {len(g)} events, net {g['net_bp'].mean():+.2f} bp")
    for sgn, nm in ((1, 'index up'), (-1, 'index down')):
        g = base[np.sign(base['mtd']) == sgn]; P(f"  {nm}: {len(g)} events, net {g['net_bp'].mean():+.2f} bp")
    P('year | events | net bp')
    for y, g in base.groupby(base['ev'].dt.year): P(f"{y} | {len(g)} | {g['net_bp'].mean():+.2f}")
    # frozen sizing: notional = RISK/STOP = 50% equity
    eq = START_EQ; peak = eq; mdd = 0; worst = 0
    for v in base['net_bp']:
        pnl = eq * (RISK / STOP) * v / 1e4; worst = min(worst, pnl / eq); eq += pnl; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    yrs = (base['ev'].iloc[-1] - base['ev'].iloc[0]).days / 365.25; cagr = (eq / START_EQ) ** (1 / yrs) - 1
    P(f"frozen sizing (0.25% at 50bp stop = 50% notional): final {eq:,.0f} | CAGR {cagr*100:+.2f}% | maxDD {mdd*100:.2f}% | worst event {worst*100:+.2f}% equity")
    try:
        r = pd.read_csv(os.path.join(SP, 'global2_strict.csv')); wins = r[r['netR'] > 0]
        win_ts = [(int(a.value // 10**6), int(b.value // 10**6)) for a, b in zip(base['t_in'], base['t_out'])]
        nb = sum(any(a <= x < b for a, b in win_ts) for x in wins['entryTs']); P(f"slot overlap: {nb} of {len(wins)} winning raid trades ({nb/len(wins)*100:.1f}%) inside event windows")
        blocked_pct = nb / len(wins)
    except Exception as e: P(f'overlap failed: {e}'); blocked_pct = 0
    post = base[base['ev'] >= '2015-02-01']['net_bp'].mean()
    kills = {'<120 events': len(base) < 120, 'gross <= 10bp': base['gross_bp'].mean() <= 10, 'base net <= 0': base['net_bp'].mean() <= 0, 'stress <= 0': stress['net_bp'].mean() <= 0,
             'CI lower <= 0': lo <= 0, 'Feb2015-26 <= 0': post <= 0, 'ex-5-best <= 0': ex5['net_bp'].mean() <= 0,
             'account (CAGR<=1%, DD>3.5%, event<-1%, >5% raid blocked)': cagr <= 0.01 or mdd > 0.035 or worst < -0.01 or blocked_pct > 0.05}
    P('## KILLS: ' + ', '.join(f'{k}: {"KILL" if v else "ok"}' for k, v in kills.items()))
    P(f'verdict: {"KILLED" if any(kills.values()) else "SURVIVES"} ({sum(kills.values())} of 8)')
    base.to_csv(os.path.join(HERE, 'hunt23_events.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt23-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
