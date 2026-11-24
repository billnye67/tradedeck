"""Hunt 13 - Bitcoin close-window momentum (Shen/Urquhart/Wang 2022), frozen high-volume adaptation + unconditional diagnostic."""
import sqlite3, os, time, json
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
START_EQ = 50_000.0
COMM_BP, SLIP_BP = 3.0, float(os.environ.get('H13_SLIP_BP', '2.0'))
END_MS = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)

def log(*a): print(*a, flush=True)

def load():
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select timestamp ts, open, high, low, close, volume from candles where source='binance_um' and market='BTCUSDT' "
                           "and interval='5m' and timestamp < ? order by timestamp", con, params=(END_MS,))
    con.close()
    df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
    et = df.index.tz_convert('America/New_York')
    # ET trading day label: day starts 17:00 ET -> belongs to the NEXT calendar date
    shifted = et + pd.Timedelta(hours=7)
    df['tday'] = shifted.normalize().tz_localize(None)
    df['bin'] = ((shifted.hour * 60 + shifted.minute) // 30).astype(int)      # 0 = 17:00-17:30 ET ... 47 = 16:30-17:00 ET
    df['et'] = et
    return df

def calibrate(df):
    y = df[(df['tday'] >= '2020-01-01') & (df['tday'] < '2021-01-01')]
    v = y.groupby(['tday', 'bin'])['volume'].sum().groupby('bin').mean()
    b = int(v.idxmax())
    start_min = b * 30; et_h = (17 * 60 + start_min) % (24 * 60)
    return b, f'{et_h // 60:02d}:{et_h % 60:02d} ET', v

def build_days(df, open_bin):
    """Per trading day: 17:00 ET price, end-of-opening-bin price, bin volume, 16:30 open, 17:00 (next day start) open, path 16:30-17:00."""
    rows = []
    g = dict(tuple(df.groupby('tday')))
    days = sorted(g.keys())
    for i, d in enumerate(days):
        x = g[d]
        first = x.iloc[0]
        if first['et'].hour != 17 or first['et'].minute != 0: continue      # need the 17:00 ET bar that starts the day
        p0 = first['open']
        ob = x[x['bin'] == open_bin]
        if len(ob) < 6: continue
        after = x[x['bin'] == open_bin + 1]
        if len(after) == 0: continue
        p_open_end = after.iloc[0]['open']
        vol = ob['volume'].sum()
        last = x[x['bin'] == 47]
        if len(last) < 6: continue
        p_in = last.iloc[0]['open']
        if i + 1 >= len(days): continue
        nxt = g[days[i + 1]].iloc[0]
        if nxt['et'].hour != 17 or nxt['et'].minute != 0: continue
        p_out = nxt['open']
        rows.append(dict(tday=d, ret_sig=p_open_end / p0 - 1, vol=vol, p_in=p_in, p_out=p_out, hi=last['high'].max(), lo=last['low'].min(),
                         highs=last['high'].values, lows=last['low'].values))
    return pd.DataFrame(rows)

def simulate(days, gated, slip_bp=SLIP_BP, notional=1.0, stop=None):
    eq = START_EQ; out = []
    vols = days['vol'].values
    for i, r in days.iterrows():
        if gated:
            if i < 60: continue
            thresh = np.percentile(vols[i-60:i], 80)
            if r['vol'] <= thresh: continue
        side = np.sign(r['ret_sig'])
        if side == 0: continue
        px_in = r['p_in']; px_out = r['p_out']; how = 'time'
        if stop:
            if side > 0:
                k = np.where(r['lows'] <= px_in * (1 - stop))[0]
                if len(k): px_out = px_in * (1 - stop); how = 'stop'
            else:
                k = np.where(r['highs'] >= px_in * (1 + stop))[0]
                if len(k): px_out = px_in * (1 + stop); how = 'stop'
        gross_bp = side * (px_out / px_in - 1) * 1e4
        cost_bp = 2 * (COMM_BP + slip_bp)
        net_bp = gross_bp - cost_bp
        pnl = notional * eq * net_bp / 1e4
        eq += pnl
        out.append(dict(tday=r['tday'], side=side, gross_bp=gross_bp, net_bp=net_bp, pnl=pnl, ret=pnl / (eq - pnl), eq=eq, how=how))
    return pd.DataFrame(out)

def boot_weeks(t, n=10000, seed=7):
    wk = t.groupby(pd.to_datetime(t['tday']).dt.to_period('W'))['ret'].apply(list)
    weeks = [np.array(w) for w in wk.values]; rng = np.random.default_rng(seed); m = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(weeks), len(weeks)); m[i] = np.concatenate([weeks[j] for j in pick]).mean()
    return np.percentile(m, 2.5), np.percentile(m, 97.5)

def report(name, t, P):
    if len(t) == 0: P(f'### {name}: no trades'); return None
    y = t.assign(y=pd.to_datetime(t['tday']).dt.year)
    sh = t['ret'].mean() / t['ret'].std(ddof=1) * np.sqrt(252) if t['ret'].std(ddof=1) > 0 else float('nan')
    P(f'### {name}')
    P(f"traded days {len(t)} | hit rate {(t['gross_bp']>0).mean()*100:.0f}% | gross {t['gross_bp'].mean():+.1f} bp/day | net {t['net_bp'].mean():+.1f} bp/day | "
      f"worst day {t['net_bp'].min():+.0f} bp | Sharpe (traded days, x sqrt252) {sh:+.2f} | equity {t['eq'].iloc[-1]:,.0f} ({(t['eq'].iloc[-1]/START_EQ-1)*100:+.1f}%) | stops {(t['how']=='stop').sum()}")
    P('year | traded days | gross bp/day | net bp/day | year P&L $')
    for yy, g in y.groupby('y'): P(f"{yy} | {len(g)} | {g['gross_bp'].mean():+.1f} | {g['net_bp'].mean():+.1f} | {g['pnl'].sum():+,.0f}")
    return y

def main():
    t0 = time.time(); df = load(); log(f'{len(df):,} bars')
    ob, label, v = calibrate(df)
    days = build_days(df, ob).reset_index(drop=True)
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 13 results (Binance BTC perp 5m, {COMM_BP}+{SLIP_BP}bp per execution, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P(f'Calibration 2020: highest-mean-volume 30-min bin = bin {ob} = {label} (frozen). Next three: ' + ', '.join(f'bin {int(b)}' for b in v.sort_values(ascending=False).index[1:4]))
    P(f'Usable trading days {len(days)} ({days["tday"].iloc[0].date()} .. {days["tday"].iloc[-1].date()})')
    learn = days[(days['tday'] >= '2021-01-01') & (days['tday'] < '2024-01-01')].reset_index(drop=True)
    # the gate needs 60 prior observations: give it 2020 as warm-up but score 2021-2023 only
    warm = days[(days['tday'] >= '2020-01-01') & (days['tday'] < '2024-01-01')].reset_index(drop=True)
    P(); P('## LEARN 2021-2023 (2020 = calibration/warm-up only)')
    g = simulate(warm, gated=True); g = g[g['tday'] >= '2021-01-01']
    u = simulate(warm, gated=False); u = u[u['tday'] >= '2021-01-01']
    report('FROZEN HIGH-VOLUME ADAPTATION, 1x, no stop (primary)', g, P); P()
    gs = simulate(warm, gated=True, slip_bp=5.0); gs = gs[gs['tday'] >= '2021-01-01']
    report('same, stress costs 3+5bp', gs, P); P()
    gr = simulate(warm, gated=True, notional=0.25, stop=0.015); gr = gr[gr['tday'] >= '2021-01-01']
    report('risk version: 0.25x, 1.5% emergency stop', gr, P); P()
    report('UNCONDITIONAL DIAGNOSTIC (every day, 1x, no stop)', u, P); P()
    gross = g['gross_bp'].mean() if len(g) else float('nan')
    con = g[g['tday'] < '2023-01-01']; val = g[g['tday'] >= '2023-01-01']
    lo, hi = boot_weeks(g) if len(g) > 10 else (float('nan'), float('nan'))
    kill = not (gross >= 20)
    passed = (not kill) and gs['net_bp'].mean() > 0 and con['net_bp'].mean() > 0 and val['net_bp'].mean() > 0 and lo > 0
    P(f"## GATE: gross {gross:+.1f} bp/traded day vs kill line 20 -> {'KILLED' if kill else 'alive'}; stress net {gs['net_bp'].mean():+.1f}; construction net {con['net_bp'].mean():+.1f} ({len(con)} d), validation net {val['net_bp'].mean():+.1f} ({len(val)} d); 95% weekly-bootstrap CI of daily net [{lo*1e4:+.1f}, {hi*1e4:+.1f}] bp -> {'PASS' if passed else 'FAIL'}")
    exam = days[days['tday'] >= '2023-10-01'].reset_index(drop=True)      # warm-up tail then 2024+
    ge = simulate(exam, gated=True); ge = ge[ge['tday'] >= '2024-01-01']
    json.dump(dict(traded=int(len(ge)), gross=float(ge['gross_bp'].mean()) if len(ge) else None, net=float(ge['net_bp'].mean()) if len(ge) else None), open(os.path.join(HERE, 'hunt13_exam_sealed.json'), 'w'))
    if passed: P(); P('## EXAM 2024 -> 2026-08-28'); report('gated, 1x, no stop', ge, P)
    else: P('## EXAM: NOT INSPECTED, sealed.')
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, f'hunt13-results-slip{SLIP_BP}.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
