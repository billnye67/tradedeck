"""Hunt 21 - post-FOMC delayed USD continuation (+12h -> +24h), short EURUSD + long USDJPY. Perplexity candidate 38, frozen 2026-10-04."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
COMM_BP = 0.4; START_EQ = 50_000.0; RISK_LEG = 0.0025; STOP = 0.01
NY = 'America/New_York'
EXAM_END = pd.Timestamp('2026-08-29', tz='UTC')
PRESS_2012 = {'20120125', '20120425', '20120620', '20120913', '20121212'}
EXCLUDE = {'20191011', '20200303', '20200315', '20200323', '20200331', '20200827', '20250822'}

def log(*a): print(*a, flush=True)

def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts "
                           "where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, int(EXAM_END.value // 10**6)))
    con.close(); df['spread'] = df['spread'].ffill().bfill()
    df.index = pd.to_datetime(df['ts'], unit='ms', utc=True); return df

def events(eur):
    raw = json.load(open(os.path.join(HERE, 'fomc_raw.json')))
    rows = []
    for code, v in sorted(raw.items()):
        if code in EXCLUDE or not v['statement']: continue
        d = pd.Timestamp(code[:4] + '-' + code[4:6] + '-' + code[6:])
        if v['time']: hm = v['time']; src = 'page'
        else:
            if code[:4] == '2012': hm = '12:30' if code in PRESS_2012 else '14:15'
            elif code == '20130130': hm = '14:15'
            else: hm = '14:00'
            src = 'schedule'
        t0 = pd.Timestamp(f'{d:%Y-%m-%d} {hm}', tz=NY).tz_convert('UTC')
        # cross-check: minute with the largest EURUSD range 11:00-16:00 ET that day
        a = pd.Timestamp(f'{d:%Y-%m-%d} 11:00', tz=NY).tz_convert('UTC'); b = pd.Timestamp(f'{d:%Y-%m-%d} 16:00', tz=NY).tz_convert('UTC')
        seg = eur.loc[a:b]
        spike = (seg['high'] - seg['low']).idxmax() if len(seg) else None
        diff_min = (spike - t0).total_seconds() / 60 if spike is not None else None
        rows.append(dict(code=code, t0=t0, src=src, spike_min=diff_min))
    return pd.DataFrame(rows)

def q(df, when):
    i = df.index.searchsorted(when)
    if i >= len(df) or (df.index[i] - when) > pd.Timedelta(minutes=5): return None
    r = df.iloc[i]; return dict(ts=df.index[i], mid=float(r['open']), spread=float(r['spread']))

def leg(df, side, t_in, t_out, slip_bp, use_stop):
    """side -1 short (EURUSD), +1 long (USDJPY). returns dict or None."""
    qi, qo = q(df, t_in), q(df, t_out)
    if qi is None or qo is None: return None
    entry = (qi['mid'] - qi['spread'] / 2) * (1 - slip_bp / 1e4) if side == -1 else (qi['mid'] + qi['spread'] / 2) * (1 + slip_bp / 1e4)
    stop = entry * (1 + STOP) if side == -1 else entry * (1 - STOP)
    how = 'time'; exit_px = None; exit_ts = qo['ts']
    seg = df.loc[qi['ts'] + pd.Timedelta(minutes=1):qo['ts'] - pd.Timedelta(microseconds=1)]
    mae = 0.0; mfe = 0.0
    if len(seg):
        if side == -1:
            ask_hi = seg['high'] + seg['spread'] / 2; ask_lo = seg['low'] + seg['spread'] / 2
            mae = float((entry / ask_hi.max() - 1)); mfe = float((entry / ask_lo.min() - 1))
            if use_stop:
                hit = np.where(ask_hi.values >= stop)[0]
                if len(hit):
                    r = seg.iloc[hit[0]]; o_ask = r['open'] + r['spread'] / 2
                    exit_px = (max(stop, o_ask) if o_ask > stop else stop) * (1 + slip_bp / 1e4); exit_ts = seg.index[hit[0]]; how = 'stop'
        else:
            bid_lo = seg['low'] - seg['spread'] / 2; bid_hi = seg['high'] - seg['spread'] / 2
            mae = float((bid_lo.min() / entry - 1)); mfe = float((bid_hi.max() / entry - 1))
            if use_stop:
                hit = np.where(bid_lo.values <= stop)[0]
                if len(hit):
                    r = seg.iloc[hit[0]]; o_bid = r['open'] - r['spread'] / 2
                    exit_px = (min(stop, o_bid) if o_bid < stop else stop) * (1 - slip_bp / 1e4); exit_ts = seg.index[hit[0]]; how = 'stop'
    if exit_px is None:
        exit_px = (qo['mid'] + qo['spread'] / 2) * (1 + slip_bp / 1e4) if side == -1 else (qo['mid'] - qo['spread'] / 2) * (1 - slip_bp / 1e4)
    gross_bp = (qi['mid'] / qo['mid'] - 1) * 1e4 if side == -1 else (qo['mid'] / qi['mid'] - 1) * 1e4
    net_bp = ((entry / exit_px - 1) if side == -1 else (exit_px / entry - 1)) * 1e4 - 2 * COMM_BP
    return dict(gross_bp=gross_bp, net_bp=net_bp, how=how, mae_bp=mae * 1e4, mfe_bp=mfe * 1e4, exit_ts=exit_ts)

def run(ev, eur, jpy, slip_bp, use_stop):
    rows = []; skipped = []
    for _, e in ev.iterrows():
        t_in = e['t0'] + pd.Timedelta(hours=12); t_out = e['t0'] + pd.Timedelta(hours=24)
        a = leg(eur, -1, t_in, t_out, slip_bp, use_stop); b = leg(jpy, +1, t_in, t_out, slip_bp, use_stop)
        if a is None or b is None: skipped.append(e['code']); continue
        rows.append(dict(code=e['code'], t0=e['t0'], eur_gross=a['gross_bp'], eur_net=a['net_bp'], eur_how=a['how'], jpy_gross=b['gross_bp'], jpy_net=b['net_bp'], jpy_how=b['how'],
                         event_net=0.5 * (a['net_bp'] + b['net_bp']), event_gross=0.5 * (a['gross_bp'] + b['gross_bp']), mae=min(a['mae_bp'], b['mae_bp']), mfe=max(a['mfe_bp'], b['mfe_bp'])))
    return pd.DataFrame(rows), skipped

def boot(t, n=10000, seed=7):
    x = t['event_net'].values; rng = np.random.default_rng(seed)
    m = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)]); return np.percentile(m, 2.5), np.percentile(m, 97.5)

def account(t):
    eq = START_EQ; peak = eq; mdd = 0; worst = 0; eqs = []
    for _, r in t.iterrows():
        # each leg ~25% notional (0.25% / 1%); P&L in $ = notional x net return
        pnl = eq * (RISK_LEG / STOP) * (r['eur_net'] + r['jpy_net']) / 1e4
        worst = min(worst, pnl / eq); eq += pnl; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak); eqs.append(eq)
    yrs = (t['t0'].iloc[-1] - t['t0'].iloc[0]).days / 365.25
    return dict(final=eq, cagr=(eq / START_EQ) ** (1 / yrs) - 1, mdd=mdd, worst=worst)

def main():
    t0 = time.time(); eur = load('EURUSD'); jpy = load('USDJPY'); ev = events(eur)
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 21 results (FXCM minute bid/ask, short EURUSD + long USDJPY, t0+12h -> t0+24h, 1% stop per leg, comm {COMM_BP}bp/side, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P(f"events: {len(ev)} scheduled statements 2012-2026 ({(ev['src']=='page').sum()} times read from the page, {(ev['src']=='schedule').sum()} from the documented schedule)")
    chk = ev.dropna(subset=['spike_min'])
    P(f"clock cross-check: EURUSD's biggest minute that afternoon lands within 5 min of t0 on {(chk['spike_min'].abs()<=5).mean()*100:.0f}% of events; schedule-dated events: {(chk[chk['src']=='schedule']['spike_min'].abs()<=5).mean()*100:.0f}%; mismatches: {', '.join(f'{r.code}({r.spike_min:+.0f}m)' for r in chk.itertuples() if abs(r.spike_min) > 5)}")
    base, sk = run(ev, eur, jpy, 0.5, True); stress, _ = run(ev, eur, jpy, 1.0, True); nostop, _ = run(ev, eur, jpy, 0.5, False)
    P(f"traded events {len(base)}, skipped (missing quotes) {len(sk)} {sk}")
    lo, hi = boot(base)
    P(f"BASE (stopped): event mean {base['event_net'].mean():+.2f} bp net ({base['event_gross'].mean():+.2f} gross), event-bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}] | win {(base['event_net']>0).mean()*100:.0f}% | median {base['event_net'].median():+.2f} | sd {base['event_net'].std():.1f} | EURUSD short leg {base['eur_net'].mean():+.2f} bp (stops {(base['eur_how']=='stop').sum()}) | USDJPY long leg {base['jpy_net'].mean():+.2f} bp (stops {(base['jpy_how']=='stop').sum()})")
    P(f"STRESS (1bp slip): {stress['event_net'].mean():+.2f} bp | NO-STOP diagnostic: {nostop['event_net'].mean():+.2f} bp")
    ex5 = base.drop(base['event_net'].nlargest(5).index)
    P(f"ex-5-best events: {ex5['event_net'].mean():+.2f} bp | MAE mean {base['mae'].mean():+.1f} / worst {base['mae'].min():+.0f} bp | MFE mean {base['mfe'].mean():+.1f} bp | worst event {base['event_net'].min():+.0f} bp")
    P('period | events | gross bp | net bp | win %')
    for nm, a, b in [('2012-18', '2012', '2019'), ('2019-23', '2019', '2024'), ('2024-26', '2024', '2027'), ('full', '2012', '2027')]:
        s = base[(base['t0'] >= pd.Timestamp(a, tz='UTC')) & (base['t0'] < pd.Timestamp(b, tz='UTC'))]
        P(f"{nm} | {len(s)} | {s['event_gross'].mean():+.2f} | {s['event_net'].mean():+.2f} | {(s['event_net']>0).mean()*100:.0f}")
    P('year | events | net bp | $ at frozen sizing')
    for y, g in base.assign(y=base['t0'].dt.year).groupby('y'): P(f"{y} | {len(g)} | {g['event_net'].mean():+.2f} | {(g['eur_net']+g['jpy_net']).sum()/1e4*START_EQ*0.25:+,.0f}")
    acc = account(base)
    P(f"frozen sizing (0.25% per leg at 1% stop): final {acc['final']:,.0f} | CAGR {acc['cagr']*100:+.2f}% | maxDD {acc['mdd']*100:.2f}% | worst event {acc['worst']*100:+.2f}% of equity")
    # overlap with raid trades
    try:
        r = pd.read_csv(os.path.join(SP, 'global2_strict.csv')); wins = r[r['netR'] > 0]
        windows = [(int((e + pd.Timedelta(hours=12)).value // 10**6), int((e + pd.Timedelta(hours=24)).value // 10**6)) for e in base['t0']]
        nb = sum(any(a <= x < b for a, b in windows) for x in wins['entryTs']); nl = sum(any(a <= x < b for a, b in windows) for x in r[r['netR'] <= 0]['entryTs'])
        P(f"slot overlap: {nb} winning and {nl} losing raid trades (of {len(r)}) entered inside FOMC windows")
    except Exception as e: P(f'overlap failed: {e}')
    p1923 = base[(base['t0'] >= pd.Timestamp('2019', tz='UTC')) & (base['t0'] < pd.Timestamp('2024', tz='UTC'))]['event_net'].mean()
    p2426 = base[base['t0'] >= pd.Timestamp('2024', tz='UTC')]['event_net'].mean()
    kills = {'base mean <= 0': base['event_net'].mean() <= 0, 'CI lower <= 0': lo <= 0, '2019-23 <= 0': p1923 <= 0, '2024-26 <= 0': p2426 <= 0,
             'a pair <= 0': base['eur_net'].mean() <= 0 or base['jpy_net'].mean() <= 0, 'stress <= 0': stress['event_net'].mean() <= 0,
             'ex-5-best <= 0': ex5['event_net'].mean() <= 0, 'maxDD > 3.5% or worst < -1.5%': acc['mdd'] > 0.035 or acc['worst'] < -0.015}
    P('## KILLS: ' + ', '.join(f'{k}: {"KILL" if v else "ok"}' for k, v in kills.items()))
    P(f'verdict: {"KILLED" if any(kills.values()) else "SURVIVES"} ({sum(kills.values())} of 8)')
    base.to_csv(os.path.join(HERE, 'hunt21_events.csv'), index=False); ev.to_csv(os.path.join(HERE, 'fomc_events.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt21-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
