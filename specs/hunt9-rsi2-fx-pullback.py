"""Hunt 9 - RSI(2) trend-pullback reversion, hourly forex (GPT frozen spec 2026-10-03).
Research only. Reads scratchpad/fx/fx.sqlite (FXCM M1 mid + per-minute spread)."""
import sqlite3, sys, json, os, math, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']
QS, MS = [5, 10, 15], [1.5, 2.0, 2.5]
PRIMARY = (10, 2.0)
SLIP_BP = float(os.environ.get('H9_SLIP_BP', '0.5'))
COMM_BP = 0.4                      # per side, FXCM-class (Hunt 8)
RISK_PCT = 0.5
START_EQ = 50_000.0
LEVERAGE = 30.0
LOT = 1000
HOLD_MS = 8 * 3600_000
LEARN_END = pd.Timestamp('2024-01-01', tz='UTC').value // 10**6
EXAM_END = pd.Timestamp('2026-08-29', tz='UTC').value // 10**6
H = 3600_000

def log(*a):
    print(*a, flush=True)

# ---------------------------------------------------------------- data
def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts "
        "where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, EXAM_END))
    con.close()
    df['spread'] = df['spread'].ffill().bfill()
    return df

def ny_info(ts_ms):
    """For an array of UTC ms: NY weekday, NY hour (float), and ms until the next 17:00 NY."""
    t = pd.to_datetime(ts_ms, unit='ms', utc=True).tz_convert('America/New_York')
    wd = t.weekday.values
    hour = t.hour.values + t.minute.values / 60.0
    # next 17:00 NY
    day = t.normalize()
    roll = day + pd.Timedelta(hours=17)
    roll = roll.where(t < roll, roll + pd.Timedelta(days=1))
    roll_ms = roll.tz_convert('UTC').as_unit('ms').asi8
    return wd, hour, roll_ms

def market_open(wd, hour):
    # closed Fri >= 17:00 NY, all Sat, Sun < 17:00 NY
    return ~(((wd == 4) & (hour >= 17)) | (wd == 5) | ((wd == 6) & (hour < 17)))

def hourly(df):
    hs = (df['ts'].values // H) * H
    g = df.groupby(hs)
    hb = pd.DataFrame({'start': g['ts'].first().index.values,
                       'open': g['open'].first().values, 'high': g['high'].max().values,
                       'low': g['low'].min().values, 'close': g['close'].last().values,
                       'n': g['ts'].size().values})
    hb = hb[hb['n'] >= 45].reset_index(drop=True)      # a bar needs most of its minutes
    c = hb['close'].values
    # Wilder RSI(2)
    d = np.diff(c, prepend=c[0]); up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    au = np.zeros(len(c)); ad = np.zeros(len(c))
    for i in range(1, len(c)):
        au[i] = (au[i-1] + up[i]) / 2.0 if i > 2 else (up[max(1, i-1)] + up[i]) / 2.0
        ad[i] = (ad[i-1] + dn[i]) / 2.0 if i > 2 else (dn[max(1, i-1)] + dn[i]) / 2.0
    rs = np.where(ad > 0, au / np.where(ad > 0, ad, 1), np.inf)
    rsi = np.where(ad > 0, 100 - 100 / (1 + rs), np.where(au > 0, 100.0, 50.0))
    hb['rsi'] = rsi
    hb['sma200'] = pd.Series(c).rolling(200).mean().values
    hb['sma5'] = pd.Series(c).rolling(5).mean().values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(hb['high'].values - hb['low'].values, np.maximum(abs(hb['high'].values - pc), abs(hb['low'].values - pc)))
    atr = np.zeros(len(c)); atr[19] = tr[:20].mean() if len(c) > 20 else 0
    for i in range(20, len(c)): atr[i] = (atr[i-1] * 19 + tr[i]) / 20.0
    atr[:19] = np.nan
    hb['atr'] = atr
    # data gaps: an hour missing while the market is open -> suppress signals for 250 bars
    wd, hr, _ = ny_info(hb['start'].values)
    prev_end = np.roll(hb['start'].values, 1) + H; prev_end[0] = hb['start'].values[0]
    gap = (hb['start'].values - prev_end) > 0
    # ignore gaps that start inside a closure window
    gwd, ghr, _ = ny_info(prev_end)
    gap &= market_open(gwd, ghr)
    ok = np.ones(len(hb), bool)
    last_gap = -10**9
    for i in range(len(hb)):
        if gap[i]: last_gap = i
        if i - last_gap < 250 or i < 250: ok[i] = False
    hb['ok'] = ok
    hb['gap'] = gap
    # next bar index where close > sma5 (long exit) / close < sma5 (short exit), strictly after i
    s5 = hb['sma5'].values
    nxt_up = np.full(len(c), -1); nxt_dn = np.full(len(c), -1)
    lu = -1; ld = -1
    for i in range(len(c) - 1, -1, -1):
        nxt_up[i] = lu; nxt_dn[i] = ld
        if c[i] > s5[i]: lu = i
        if c[i] < s5[i]: ld = i
    hb['nxt_up'] = nxt_up; hb['nxt_dn'] = nxt_dn
    return hb

# ---------------------------------------------------------------- one pair, all cells
def simulate_pair(sym, df, hb):
    ts = df['ts'].values; op = df['open'].values; hi = df['high'].values; lo = df['low'].values; sp = df['spread'].values
    jpy = sym.endswith('JPY')
    out = {}          # (q, m) -> list of trades
    starts = hb['start'].values
    wd, hr, roll_ms = ny_info(starts + H)          # at the execution minute (bar close)
    openm = market_open(wd, hr)
    for q in QS:
        long_sig = (hb['close'] > hb['sma200']) & (hb['close'] < hb['sma5']) & (hb['rsi'] < q) & hb['ok']
        short_sig = (hb['close'] < hb['sma200']) & (hb['close'] > hb['sma5']) & (hb['rsi'] > 100 - q) & hb['ok']
        idxs = np.where((long_sig | short_sig).values & openm & ((roll_ms - (starts + H)) >= HOLD_MS))[0]
        for m in MS:
            trades = []
            skipped = 0
            for i in idxs:
                side = 1 if long_sig.iat[i] else -1
                exec_ts = starts[i] + H
                k = np.searchsorted(ts, exec_ts)
                if k >= len(ts) or ts[k] != exec_ts: skipped += 1; continue      # next minute missing -> skip
                atr = hb['atr'].iat[i]
                if not (atr > 0): continue
                px = op[k]; half = sp[k] / 2.0; slip = px * SLIP_BP / 10_000
                entry = px + half + slip if side == 1 else px - half - slip
                stop = entry - m * atr if side == 1 else entry + m * atr
                # planned exits
                j = hb['nxt_up'].iat[i] if side == 1 else hb['nxt_dn'].iat[i]
                hour_exit = starts[j] + H if j > 0 else 10**18
                time_exit = exec_ts + HOLD_MS
                roll_exit = roll_ms[i] - 60_000
                plan_exit = min(hour_exit, time_exit, roll_exit)
                kend = np.searchsorted(ts, plan_exit)          # first minute >= plan_exit
                if kend >= len(ts): continue
                seg_lo = lo[k:kend]; seg_hi = hi[k:kend]; seg_sp = sp[k:kend]
                if side == 1:
                    hit = np.where(seg_lo - seg_sp / 2.0 <= stop)[0]
                else:
                    hit = np.where(seg_hi + seg_sp / 2.0 >= stop)[0]
                if len(hit):
                    kx = k + hit[0]
                    o_bid = op[kx] - sp[kx] / 2.0; o_ask = op[kx] + sp[kx] / 2.0
                    if side == 1: fill = min(stop, o_bid) if o_bid < stop else stop
                    else: fill = max(stop, o_ask) if o_ask > stop else stop
                    exit_px = fill - op[kx] * SLIP_BP / 10_000 if side == 1 else fill + op[kx] * SLIP_BP / 10_000
                    exit_ts = ts[kx]; how = 'stop'
                else:
                    kx = kend
                    exit_ts = ts[kx]
                    exit_px = (op[kx] - sp[kx] / 2.0 - op[kx] * SLIP_BP / 10_000) if side == 1 else (op[kx] + sp[kx] / 2.0 + op[kx] * SLIP_BP / 10_000)
                    how = 'sma5' if plan_exit == hour_exit else ('8h' if plan_exit == time_exit else 'rollover')
                    if exit_ts > plan_exit + 5 * 60_000: how += '-gapfill'
                risk_unit = m * atr
                crossed_roll = exit_ts > roll_ms[i]
                trades.append(dict(sym=sym, side=side, sig_ts=int(starts[i]), exec_ts=int(exec_ts), exit_ts=int(exit_ts),
                                   entry=float(entry), stop=float(stop), exit=float(exit_px), risk_unit=float(risk_unit),
                                   rate=float(px), jpy=jpy, how=how, crossed_roll=bool(crossed_roll), spread_in=float(sp[k]), spread_out=float(sp[kx])))
            out[(q, m)] = trades
            log(f'  {sym} q={q} m={m}: {len(trades)} candidate trades, {skipped} skipped (missing exec minute)')
    return out

# ---------------------------------------------------------------- portfolio GLOBAL-2
def portfolio(trades):
    """Two shared slots, first-come; one per pair; after close wait for a newly completed signal bar."""
    trades = sorted(trades, key=lambda t: (t['exec_ts'], t['sym']))
    eq = START_EQ; peak = eq; maxdd = 0.0
    open_ = []           # (exit_ts, sym)
    last_exit = {}
    taken = []
    for t in trades:
        open_ = [o for o in open_ if o[0] > t['exec_ts']]
        if t['sym'] in last_exit and t['sig_ts'] + H <= last_exit[t['sym']]: continue   # signal bar must complete after exit
        if any(o[1] == t['sym'] for o in open_): continue
        if len(open_) >= 2: continue
        risk_usd = eq * RISK_PCT / 100.0
        units_raw = risk_usd * (t['rate'] if t['jpy'] else 1.0) / t['risk_unit']
        units = math.floor(units_raw / LOT) * LOT
        notional = units if t['jpy'] else units * t['entry']
        if notional > LEVERAGE * eq:
            units = math.floor(LEVERAGE * eq / (1.0 if t['jpy'] else t['entry']) / LOT) * LOT
        if units < LOT: continue
        pnl_q = (t['exit'] - t['entry']) * units * t['side']
        pnl = pnl_q / t['exit'] if t['jpy'] else pnl_q
        notional_usd = units if t['jpy'] else units * t['entry']
        comm = 2 * notional_usd * COMM_BP / 10_000
        fin = notional_usd * 5 / 10_000 if t['crossed_roll'] else 0.0
        net = pnl - comm - fin
        r = net / risk_usd
        spread_cost = (t['spread_in'] / 2 + t['spread_out'] / 2) * units
        spread_usd = spread_cost / t['exit'] if t['jpy'] else spread_cost
        eq += net; peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak * 100)
        taken.append(dict(t, r=r, net=net, eq=eq, risk_usd=risk_usd, units=units,
                          cost_r=(comm + fin + spread_usd) / risk_usd, hold_h=(t['exit_ts'] - t['exec_ts']) / 3600_000))
        open_.append((t['exit_ts'], t['sym'])); last_exit[t['sym']] = t['exit_ts']
    return taken, maxdd

def summarize(taken):
    if not taken: return dict(n=0)
    r = np.array([t['r'] for t in taken])
    return dict(n=len(r), wins=int((r > 0).sum()), losses=int((r <= 0).sum()), exp=float(r.mean()), total=float(r.sum()),
                cost_r=float(np.mean([t['cost_r'] for t in taken])), hold_h=float(np.mean([t['hold_h'] for t in taken])),
                stops=int(sum(1 for t in taken if t['how'].startswith('stop'))))

def era_split(taken):
    learn = [t for t in taken if t['exec_ts'] < LEARN_END]
    exam = [t for t in taken if LEARN_END <= t['exec_ts'] < EXAM_END]
    return learn, exam

def by_key(taken, keyf):
    d = {}
    for t in taken: d.setdefault(keyf(t), []).append(t['r'])
    return {k: (len(v), float(np.mean(v)), float(np.sum(v))) for k, v in sorted(d.items())}

# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    allc = {(q, m): [] for q in QS for m in MS}
    frames = {}
    for sym in PAIRS:
        log(f'loading {sym} ...'); df = load(sym); hb = hourly(df)
        log(f'  {len(df):,} minutes, {len(hb):,} hourly bars, {int(hb["gap"].sum())} open-market gaps')
        frames[sym] = (df, hb)
        res = simulate_pair(sym, df, hb)
        for k, v in res.items(): allc[k].extend(v)
    lines = []
    P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 9 results  (slip {SLIP_BP}bp, comm {COMM_BP}bp/side, spread actual, 0.5% risk, GLOBAL-2, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    cells = {}
    for k, tr in allc.items():
        taken, maxdd = portfolio(tr)
        learn, exam = era_split(taken)
        # per-era drawdown
        _, dd_l = portfolio([t for t in tr if t['exec_ts'] < LEARN_END])
        _, dd_e = portfolio([t for t in tr if LEARN_END <= t['exec_ts'] < EXAM_END])
        cells[k] = dict(learn=summarize(learn), exam=summarize(exam), dd_learn=dd_l, dd_exam=dd_e, learn_tr=learn, exam_tr=exam)
    prim = cells[PRIMARY]
    learn_pass = prim['learn']['n'] > 0 and prim['learn']['exp'] > 0.05
    P()
    P('## LEARN era (2012 -> 2023), all nine cells, GLOBAL-2 standalone')
    P('| q | m | trades | W | L | exp R | total R | maxDD % | cost R | hold h | stops |')
    P('|---|---|---|---|---|---|---|---|---|---|---|')
    for q in QS:
        for m in MS:
            c = cells[(q, m)]; L = c['learn']
            if L['n'] == 0: P(f'| {q} | {m} | 0 |'); continue
            P(f"| {q} | {m} | {L['n']} | {L['wins']} | {L['losses']} | {L['exp']:+.3f} | {L['total']:+.1f} | {c['dd_learn']:.1f} | {L['cost_r']:.3f} | {L['hold_h']:.1f} | {L['stops']} |")
    P()
    P('### primary (q=10, m=2) learn by pair / by year')
    for k, v in by_key(prim['learn_tr'], lambda t: t['sym']).items(): P(f'  {k}: {v[0]} trades, {v[1]:+.3f}R/trade, {v[2]:+.1f}R')
    for k, v in by_key(prim['learn_tr'], lambda t: pd.Timestamp(t['exec_ts'], unit="ms").year).items(): P(f'  {k}: {v[0]} trades, {v[1]:+.3f}R/trade, {v[2]:+.1f}R')
    how = {}
    for t in prim['learn_tr']: how[t['how']] = how.get(t['how'], 0) + 1
    P(f'  exits: {how}')
    P()
    P(f"## GATE, learn: primary {prim['learn'].get('exp', float('nan')):+.3f}R vs hurdle +0.050R -> {'PASS' if learn_pass else 'FAIL'}")
    neigh = [(5, 2.0), (15, 2.0), (10, 1.5), (10, 2.5)]
    npos = sum(1 for k in neigh if cells[k]['learn']['n'] and cells[k]['learn']['exp'] > 0)
    P(f'   neighbours positive in learn: {npos}/4')
    sealed = {f'{q}|{m}': dict(exam=cells[(q, m)]['exam'], dd_exam=cells[(q, m)]['dd_exam']) for q in QS for m in MS}
    sealed['primary_by_pair'] = by_key(prim['exam_tr'], lambda t: t['sym'])
    sealed['primary_by_year'] = {str(k): v for k, v in by_key(prim['exam_tr'], lambda t: pd.Timestamp(t['exec_ts'], unit="ms").year).items()}
    with open(os.path.join(os.path.dirname(DB), f'hunt9_exam_sealed_slip{SLIP_BP}.json'), 'w') as f: json.dump(sealed, f, indent=1, default=str)
    if learn_pass and npos >= 3:
        P()
        P('## EXAM era (2024 -> 2026-08-28), inspected because learn passed')
        P('| q | m | trades | W | L | exp R | total R | maxDD % | cost R | hold h |')
        P('|---|---|---|---|---|---|---|---|---|---|')
        for q in QS:
            for m in MS:
                c = cells[(q, m)]; E = c['exam']
                if E['n'] == 0: P(f'| {q} | {m} | 0 |'); continue
                P(f"| {q} | {m} | {E['n']} | {E['wins']} | {E['losses']} | {E['exp']:+.3f} | {E['total']:+.1f} | {c['dd_exam']:.1f} | {E['cost_r']:.3f} | {E['hold_h']:.1f} |")
        for k, v in by_key(prim['exam_tr'], lambda t: t['sym']).items(): P(f'  {k}: {v[0]} trades, {v[1]:+.3f}R/trade, {v[2]:+.1f}R')
        for k, v in by_key(prim['exam_tr'], lambda t: pd.Timestamp(t['exec_ts'], unit="ms").year).items(): P(f'  {k}: {v[0]} trades, {v[1]:+.3f}R/trade, {v[2]:+.1f}R')
    else:
        P()
        P('## EXAM era: NOT INSPECTED (learn gate failed). Numbers sealed in scratchpad hunt9_exam_sealed json.')
    # charts: 20 chronological primary-cell trades spread across the learn era
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        cd = os.path.join(OUT, 'hunt9-charts'); os.makedirs(cd, exist_ok=True)
        trs = prim['learn_tr']
        pick = [trs[int(i)] for i in np.linspace(0, len(trs) - 1, min(20, len(trs)))] if trs else []
        for n, t in enumerate(pick):
            df, hb = frames[t['sym']]
            a = np.searchsorted(hb['start'].values, t['sig_ts']) - 60; b = a + 90
            seg = hb.iloc[max(0, a):b]
            fig, ax = plt.subplots(figsize=(10, 4))
            x = pd.to_datetime(seg['start'], unit='ms')
            ax.plot(x, seg['close'], color='#888', lw=1, label='1h close')
            ax.plot(x, seg['sma5'], color='#6F8CFF', lw=1, label='SMA5'); ax.plot(x, seg['sma200'], color='#F2C14E', lw=1, label='SMA200')
            ax.axhline(t['entry'], color='#2ecc71', lw=.8); ax.axhline(t['stop'], color='#e74c3c', lw=.8)
            ax.axvline(pd.Timestamp(t['exec_ts'], unit='ms'), color='#2ecc71', lw=.8, ls='--'); ax.axvline(pd.Timestamp(t['exit_ts'], unit='ms'), color='k', lw=.8, ls='--')
            ax.set_title(f"{n+1:02d} {t['sym']} {'LONG' if t['side']==1 else 'SHORT'} {pd.Timestamp(t['exec_ts'], unit='ms'):%Y-%m-%d %H:%M} exit {t['how']} {t['r']:+.2f}R")
            ax.legend(fontsize=7); fig.tight_layout(); fig.savefig(os.path.join(cd, f'{n+1:02d}-{t["sym"]}.png'), dpi=90); plt.close(fig)
        P(f'charts: {len(pick)} written to specs/hunt9-charts/')
    except Exception as e:
        P(f'charts: failed ({e})')
    P(f'run time {time.time()-t0:.0f}s')
    with open(os.path.join(OUT, f'hunt9-results-slip{SLIP_BP}.md'), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
