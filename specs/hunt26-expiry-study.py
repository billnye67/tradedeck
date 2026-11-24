"""Hunt 26 exploratory study (ChatGPT): BTC long at 08:15 UTC after a large same-day options expiry. LEARN 2021-2023 only.
Signal: summed delivery OI (calls+puts) of options expiring that day with strikes within +-2.5% of the delivery index, above the
trailing percentile of the previous 250 complete daily observations (today excluded). EXPLORATORY: delivery OI is stamped 08:00,
15 minutes before entry, and the public archive lags ~2 months, so historical publication time is NOT established."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
COMM_BP = 3.0; SLIP = 2.0; SLIP_S = 5.0; RISK = 0.005; LEV = 10.0
BAR = 300_000; H = 3600_000; DAY = 86_400_000
L0 = pd.Timestamp('2021-01-01'); L1 = pd.Timestamp('2024-01-01')
PCTS, EXITS = [85, 90, 95], ['09:00', '10:00', '11:00']; PRIMARY = (90, '10:00')

def log(*a): print(*a, flush=True)

def load_px():
    con = sqlite3.connect(DB)
    px = pd.read_sql_query("select timestamp ts, open, high, low, close from candles where source='binance_um' and market='BTCUSDT' and interval='5m' and timestamp>=? and timestamp<? order by timestamp", con, params=(int(pd.Timestamp('2020-06-01', tz='UTC').value // 10**6), int(pd.Timestamp('2024-01-02', tz='UTC').value // 10**6)))
    con.close(); return px

def hourly_atr(px):
    hb = (px['ts'] // H) * H; g = px.groupby(hb)
    b = pd.DataFrame({'t': g['ts'].first().index.values, 'high': g['high'].max().values, 'low': g['low'].min().values, 'close': g['close'].last().values})
    pc = b['close'].shift(1); tr = np.maximum(b['high'] - b['low'], np.maximum((b['high'] - pc).abs(), (b['low'] - pc).abs()))
    b['atr'] = tr.ewm(alpha=1 / 14, adjust=False).mean(); return pd.Series(b['atr'].values, index=b['t'].values + H)   # available from bar end

def daily_signal():
    d = pd.read_csv(os.path.join(OUT, 'hunt26-data-deribit-option-deliveries.csv'))
    d['date'] = pd.to_datetime(d['date']); d['tod'] = (d['ts'] % DAY) // 60000
    d = d[d['tod'] == 480]                                             # 08:00 settlements only; later-stamped ones excluded
    rows = []
    for dt_, g in d.groupby('date'):
        idx = g['index_price'].iloc[0]; near = g[(g['strike'] >= idx * 0.975) & (g['strike'] <= idx * 1.025)]
        rows.append(dict(date=dt_, idx=idx, n_opts=len(g), near_oi=near['position'].sum(), all_oi=g['position'].sum()))
    s = pd.DataFrame(rows).set_index('date').sort_index()
    full = pd.date_range(s.index.min(), s.index.max()); s = s.reindex(full)          # days with no expiring options = NaN (no signal possible)
    for p in PCTS: s[f'thr{p}'] = s['near_oi'].shift(1).rolling(250, min_periods=250).apply(lambda x: np.nanpercentile(x, p) if np.isfinite(x).sum() >= 250 else np.nan, raw=True)
    return s

def trade(px, ts, op, hi, lo, atr, day, exit_hm, slip_bp, use_stop=True):
    t_in = int(pd.Timestamp(f'{day:%Y-%m-%d} 08:15', tz='UTC').value // 10**6); t_x = int(pd.Timestamp(f'{day:%Y-%m-%d} {exit_hm}', tz='UTC').value // 10**6)
    k = np.searchsorted(ts, t_in)
    if k >= len(ts) or ts[k] != t_in: return None
    a = atr[atr.index <= t_in]
    if len(a) == 0 or not (a.iloc[-1] > 0): return None
    dist = 3 * float(a.iloc[-1]); entry = op[k] * (1 + slip_bp / 1e4); stop = entry - dist
    kend = np.searchsorted(ts, t_x)
    if kend >= len(ts) or ts[kend] != t_x: return None
    px_out = None; how = 'time'; kx = kend
    if use_stop:
        for j in range(k + 1, kend):
            if op[j] <= stop: px_out = op[j] * (1 - slip_bp / 1e4); how = 'stop-gap'; kx = j; break
            if lo[j] <= stop: px_out = stop * (1 - slip_bp / 1e4); how = 'stop'; kx = j; break
    if px_out is None: px_out = op[kend] * (1 - slip_bp / 1e4)
    gross_bp = (op[kx] / op[k] - 1) * 1e4; net_bp = (px_out / entry - 1) * 1e4 - 2 * COMM_BP
    r = (px_out - entry) / dist - 2 * COMM_BP / 1e4 * entry / dist
    return dict(day=day, gross_bp=gross_bp, net_bp=net_bp, r=r, how=how, lev=entry / dist * RISK)

def main():
    t0 = time.time(); px = load_px(); atr = hourly_atr(px); s = daily_signal()
    ts = px['ts'].values; op = px['open'].values; hi = px['high'].values; lo = px['low'].values
    lines = []; P = lambda s_='': (lines.append(s_), log(s_))
    P(f'# Hunt 26 exploratory study — BTC long 08:15 UTC after a large same-day options expiry. LEARN 2021-2023 only. ({time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P('EXPLORATORY: signal uses delivery OI stamped 08:00 (15 min before entry); historical public availability at 08:15 is NOT established (archive lags ~2 months today). Not an executable backtest.')
    learn = s[(s.index >= L0) & (s.index < L1)]
    P(f"learn days {len(learn)}, days with expiring options {int(learn['near_oi'].notna().sum())}, days with a valid 250-day threshold {int(learn['thr90'].notna().sum())}; near-money OI median {learn['near_oi'].median():,.0f} contracts, 90th pct of its own history ~{learn['thr90'].median():,.0f}")
    cells = {}; ctl = {}
    for p in PCTS:
        for ex in EXITS:
            days = learn[(learn['near_oi'] > learn[f'thr{p}']) & learn[f'thr{p}'].notna()].index
            tr = [t for t in (trade(px, ts, op, hi, lo, atr, d, ex, SLIP) for d in days) if t]
            cells[(p, ex)] = pd.DataFrame(tr)
    for ex in EXITS:
        days = learn[learn['thr90'].notna()].index
        ctl[ex] = pd.DataFrame([t for t in (trade(px, ts, op, hi, lo, atr, d, ex, SLIP) for d in days) if t])
    P(); P('## Grid, LEARN, net R per trade (3+2bp, 3x hourly ATR14 stop) | control = every day with a valid threshold, same exit')
    P('| pct \\ exit | 09:00 | 10:00 | 11:00 |'); P('|---|---|---|---|')
    for p in PCTS: P(f'| {p} | ' + ' | '.join((f"{cells[(p,ex)]['r'].mean():+.3f}R ({len(cells[(p,ex)])}, {cells[(p,ex)]['net_bp'].mean():+.1f}bp)" if len(cells[(p, ex)]) else 'n/a') for ex in EXITS) + ' |')
    P('| control | ' + ' | '.join(f"{ctl[ex]['r'].mean():+.3f}R ({len(ctl[ex])}, {ctl[ex]['net_bp'].mean():+.1f}bp)" for ex in EXITS) + ' |')
    prim = cells[PRIMARY]
    if len(prim):
        stress = pd.DataFrame([t for t in (trade(px, ts, op, hi, lo, atr, d, '10:00', SLIP_S) for d in prim['day']) if t])
        P(); P(f"### primary 90th pct / 10:00: {len(prim)} trades | gross {prim['gross_bp'].mean():+.1f} bp | net {prim['net_bp'].mean():+.1f} bp | {prim['r'].mean():+.3f}R | hit {(prim['r']>0).mean()*100:.0f}% | stops {prim['how'].str.startswith('stop').sum()} | stress {stress['r'].mean():+.3f}R | control same exit {ctl['10:00']['net_bp'].mean():+.1f} bp on {len(ctl['10:00'])}")
        for y, g in prim.groupby(pd.to_datetime(prim['day']).dt.year): P(f"  {y}: {len(g)} trades, net {g['net_bp'].mean():+.1f} bp, {g['r'].sum():+.1f}R")
        ex5 = prim['r'].drop(prim['r'].nlargest(5).index).mean(); P(f"  ex-5-best {ex5:+.3f}R | $ at 0.5% risk: {prim['r'].sum()*250:+,.0f} over 3 years")
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt26-expiry-study-learn.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
