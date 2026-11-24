"""Hunt 24 - four-hour FX net-payoff model v1 (ChatGPT frozen spec 2026-10-04).
Stage 'coverage' reports data coverage only. Stage 'run' builds labels, walk-forward fits and evaluates."""
import sqlite3, os, sys, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\fx\fx.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = ['AUDUSD', 'EURUSD', 'GBPUSD', 'USDJPY']
COMM_BP = 0.4; START_EQ = 50_000.0; RISK = 0.005; LOT = 1000; LEV = 30.0
NY = 'America/New_York'; HOURS = [8, 9, 10, 11, 12]
H = 3600_000; MIN = 60_000; HOLD = 4 * H
T_HIST = pd.Timestamp('2012-01-01', tz='UTC'); T_LEARN0 = pd.Timestamp('2015-01-01', tz='UTC'); T_LEARN1 = pd.Timestamp('2024-01-01', tz='UTC'); T_END = pd.Timestamp('2026-08-29', tz='UTC')
WS, TS = [12, 24, 36], [0.05, 0.10, 0.15]; PRIMARY = (24, 0.10)

def log(*a): print(*a, flush=True)

def load(sym):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select c.ts, c.open, c.high, c.low, c.close, s.spread from candles c left join spread s on s.symbol=c.symbol and s.ts=c.ts where c.symbol=? and c.ts < ? order by c.ts", con, params=(sym, int(T_END.value // 10**6)))
    con.close(); df['spread'] = df['spread'].ffill().bfill(); return df

def h1_bars(df):
    hb = (df['ts'] // H) * H
    g = df.groupby(hb)
    b = pd.DataFrame({'t': g['ts'].first().index.values, 'open': g['open'].first().values, 'high': g['high'].max().values, 'low': g['low'].min().values, 'close': g['close'].last().values, 'spread': g['spread'].last().values, 'n': g['ts'].size().values})
    b = b[b['n'] >= 45].reset_index(drop=True)
    pc = b['close'].shift(1); tr = np.maximum(b['high'] - b['low'], np.maximum((b['high'] - pc).abs(), (b['low'] - pc).abs()))
    b['tr'] = tr; b['atr'] = tr.ewm(alpha=1 / 20, adjust=False).mean(); b['tr120'] = tr.rolling(120).mean()
    lc = np.log(b['close'])
    for k in (1, 2, 4, 8, 24): b[f'r{k}'] = lc - lc.shift(k)
    return b

def features_at(b, i):
    """features from completed bar i (bar i must have ended before decision)."""
    r = b.iloc[i]; atr = r['atr']; mid = r['close']
    if not (atr > 0) or i < 120 or pd.isna(r['tr120']) or pd.isna(r['r24']): return None
    rng = r['high'] - r['low']
    f = {f'ret{k}': r[f'r{k}'] / (atr / mid) for k in (1, 2, 4, 8, 24)}
    f.update(range_atr=rng / atr, body_atr=(r['close'] - r['open']) / atr, cloc=((r['close'] - r['low']) / rng) if rng > 0 else 0.5, atr_rel=atr / r['tr120'], spread_atr=r['spread'] / atr)
    return f, atr

def simulate(df, k_entry, side, atr, slip_bp, jpy):
    """entry at minute index k_entry (open), stop 2*atr, exit after 4h or stop. returns net R and exit info."""
    ts = df['ts'].values; op = df['open'].values; hi = df['high'].values; lo = df['low'].values; sp = df['spread'].values
    e = op[k_entry]; sl = e * slip_bp / 1e4
    entry = e + sp[k_entry] / 2 + sl if side == 1 else e - sp[k_entry] / 2 - sl
    dist = 2 * atr; stop = entry - side * dist
    t_x = ts[k_entry] + HOLD
    kend = np.searchsorted(ts, t_x)
    seg_lo = lo[k_entry + 1:kend] - sp[k_entry + 1:kend] / 2; seg_hi = hi[k_entry + 1:kend] + sp[k_entry + 1:kend] / 2
    if side == 1: hit = np.where(seg_lo <= stop)[0]
    else: hit = np.where(seg_hi >= stop)[0]
    if len(hit):
        kx = k_entry + 1 + hit[0]
        o = op[kx] - sp[kx] / 2 if side == 1 else op[kx] + sp[kx] / 2
        fill = (min(stop, o) if o < stop else stop) if side == 1 else (max(stop, o) if o > stop else stop)
        px = fill - op[kx] * slip_bp / 1e4 if side == 1 else fill + op[kx] * slip_bp / 1e4; how = 'stop'; t_out = ts[kx]
    else:
        if kend >= len(ts): return None
        kx = kend; late = (ts[kx] - t_x) / MIN
        px = op[kx] - sp[kx] / 2 - op[kx] * slip_bp / 1e4 if side == 1 else op[kx] + sp[kx] / 2 + op[kx] * slip_bp / 1e4; how = 'time' if late <= 5 else 'time-late'; t_out = ts[kx]
    r = side * (px - entry) / dist - 2 * COMM_BP / 1e4 * entry / dist
    return dict(r=r, how=how, t_out=int(t_out), entry=entry, dist=dist)

def build(df, b, sym, slip_bp):
    """all decisions for a pair: features, labels for both directions, coverage flags."""
    ts = df['ts'].values
    bt = b['t'].values
    days = pd.bdate_range(T_HIST.tz_localize(None), T_END.tz_localize(None))
    rows = []; cov = []
    jpy = sym.endswith('JPY')
    for d in days:
        for hr in HOURS:
            t = int(pd.Timestamp(f'{d:%Y-%m-%d} {hr:02d}:00', tz=NY).tz_convert('UTC').value // 10**6)
            c = dict(sym=sym, t=t, year=d.year, feat=False, entry=False, exit=False)
            i = np.searchsorted(bt, t) - 1                     # last bar starting before t
            while i >= 0 and bt[i] + H > t: i -= 1               # must have ENDED before t
            if i < 0: cov.append(c); continue
            if (t - (bt[i] + H)) > 2 * H: cov.append(c); continue   # stale: last completed bar more than 2h old
            fa = features_at(b, i)
            if fa is None: cov.append(c); continue
            c['feat'] = True
            f, atr = fa
            k = np.searchsorted(ts, t + MIN)
            if k >= len(ts) or ts[k] > t + 3 * MIN: cov.append(c); continue
            c['entry'] = True
            L = simulate(df, k, 1, atr, slip_bp, jpy); S = simulate(df, k, -1, atr, slip_bp, jpy)
            if L is None or S is None: cov.append(c); continue
            c['exit'] = True; cov.append(c)
            wd = d.weekday()
            row = dict(sym=sym, t=t, t_entry=int(ts[k]), hour=hr, wd=wd, atr=atr, rL=L['r'], rS=S['r'], howL=L['how'], howS=S['how'], t_outL=L['t_out'], t_outS=S['t_out'], entryL=L['entry'], entryS=S['entry'], dist=L['dist'])
            row.update(f); rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(cov)

def coverage_report(cov, P):
    P('## Coverage (decision slots Mon-Fri 08-12 NY): % with usable features / entry quote within t+1..t+3 / both exits simulable')
    P('pair | year | slots | features % | entry % | exit %')
    for (s, y), g in cov.groupby(['sym', 'year']): P(f"{s} | {y} | {len(g)} | {g['feat'].mean()*100:.0f} | {g['entry'].mean()*100:.0f} | {g['exit'].mean()*100:.0f}")
    P(f"overall: {len(cov)} slots, {cov['exit'].mean()*100:.1f}% fully usable")
    P('Frozen handling: decision skipped if the last completed H1 bar ended more than 2h before it, if any feature window is incomplete, if no quote exists in [t+1, t+3] min, or if either direction cannot be simulated to its exit. Late exits (>5 min after t+4h) take the next quote and are counted.')

def design(X):
    cols = ['ret1', 'ret2', 'ret4', 'ret8', 'ret24', 'range_atr', 'body_atr', 'cloc', 'atr_rel', 'spread_atr']
    M = X[cols].copy()
    for p in PAIRS: M[f'p_{p}'] = (X['sym'] == p).astype(float)
    for h in HOURS: M[f'h_{h}'] = (X['hour'] == h).astype(float)
    for w in range(5): M[f'w_{w}'] = (X['wd'] == w).astype(float)
    return M.values

def walk_forward(D, W, mode='model'):
    """monthly refits; returns per-decision predictions (predL, predS) for the learn period."""
    from sklearn.ensemble import GradientBoostingRegressor
    D = D.sort_values('t').reset_index(drop=True)
    tt = pd.to_datetime(D['t'], unit='ms', utc=True)
    months = pd.period_range(T_LEARN0.to_period('M'), (T_LEARN1 - pd.Timedelta(days=1)).to_period('M'), freq='M')
    predL = np.full(len(D), np.nan); predS = np.full(len(D), np.nan)
    Xall = design(D)
    done = np.maximum(D['t_outL'].values, D['t_outS'].values)
    for m in months:
        m0 = int(m.start_time.tz_localize('UTC').value // 10**6); m1 = int((m + 1).start_time.tz_localize('UTC').value // 10**6)
        w0 = int((m - W).start_time.tz_localize('UTC').value // 10**6)
        tr = (D['t'].values >= w0) & (done < m0)
        te = (D['t'].values >= m0) & (D['t'].values < m1)
        if tr.sum() < 500 or te.sum() == 0: continue
        if mode == 'model':
            gl = GradientBoostingRegressor(loss='squared_error', n_estimators=100, learning_rate=0.05, max_depth=2, min_samples_leaf=200, subsample=1.0, random_state=22).fit(Xall[tr], D['rL'].values[tr])
            gs = GradientBoostingRegressor(loss='squared_error', n_estimators=100, learning_rate=0.05, max_depth=2, min_samples_leaf=200, subsample=1.0, random_state=22).fit(Xall[tr], D['rS'].values[tr])
            predL[te] = gl.predict(Xall[te]); predS[te] = gs.predict(Xall[te])
        elif mode == 'mean':
            predL[te] = D['rL'].values[tr].mean(); predS[te] = D['rS'].values[tr].mean()
    return predL, predS

def select_and_book(D, predL, predS, T, force=None):
    """one position at a time; chronological; simultaneous -> highest prediction, alphabetical tie."""
    D = D.assign(pL=predL, pS=predS)
    if force == 'long': D['side'] = 1; D['pred'] = 1.0
    elif force == 'short': D['side'] = -1; D['pred'] = 1.0
    else:
        D['side'] = np.where(D['pL'] > D['pS'], 1, np.where(D['pS'] > D['pL'], -1, 0))
        D['pred'] = np.maximum(D['pL'], D['pS'])
        D.loc[~(D['pred'] > T), 'side'] = 0
    D = D[(D['side'] != 0) & D['pred'].notna()].sort_values(['t', 'pred', 'sym'], ascending=[True, False, True])
    busy = -1; out = []
    for r in D.itertuples():
        if r.t_entry < busy: continue
        rr = r.rL if r.side == 1 else r.rS; how = r.howL if r.side == 1 else r.howS; t_out = r.t_outL if r.side == 1 else r.t_outS
        out.append(dict(sym=r.sym, t=r.t, t_entry=r.t_entry, t_out=t_out, side=r.side, pred=r.pred, r=rr, how=how)); busy = t_out
    return pd.DataFrame(out)

def daily_equity(trades, t0, t1):
    days = pd.bdate_range(t0.tz_localize(None), t1.tz_localize(None) - pd.Timedelta(days=1))
    ret = pd.Series(0.0, index=days)
    if len(trades):
        d = pd.to_datetime(trades['t_out'], unit='ms', utc=True).dt.tz_convert(NY).dt.normalize().dt.tz_localize(None)
        s = trades.assign(d=d.values).groupby('d')['r'].sum() * RISK
        ret.loc[ret.index.intersection(s.index)] = s.reindex(ret.index).fillna(0.0).loc[ret.index.intersection(s.index)]
    return ret

def boot_months(ret, n=10000, seed=7):
    g = ret.groupby(ret.index.to_period('M')).apply(list); ms = [np.array(x) for x in g.values]
    rng = np.random.default_rng(seed); o = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(ms), len(ms)); o[i] = np.concatenate([ms[j] for j in pick]).mean()
    return np.percentile(o, 2.5), np.percentile(o, 97.5)

def summ(t):
    if len(t) == 0: return '0 trades'
    return f"{len(t)} trades | {t['r'].mean():+.3f}R | total {t['r'].sum():+.1f}R | hit {(t['r']>0).mean()*100:.0f}% | stops {(t['how']=='stop').sum()} | $ {t['r'].sum()*250:+,.0f}"

def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else 'coverage'
    t0 = time.time(); lines = []; P = lambda s='': (lines.append(s), log(s))
    cache = os.path.join(HERE, 'decisions.parquet')
    if stage == 'coverage' or not os.path.exists(cache):
        allD = []; allC = []
        for p in PAIRS:
            df = load(p); b = h1_bars(df); D, C = build(df, b, p, 0.5); allD.append(D); allC.append(C); log(f'{p}: {len(D)} usable decisions')
        D = pd.concat(allD, ignore_index=True); C = pd.concat(allC, ignore_index=True)
        D.to_parquet(cache); C.to_parquet(os.path.join(HERE, 'coverage.parquet'))
        if stage == 'coverage':
            coverage_report(C, P)
            open(os.path.join(OUT, 'hunt24-coverage.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n'); return
    D = pd.read_parquet(cache).sort_values('t', kind='mergesort').reset_index(drop=True)   # ONE canonical order for predictions and booking
    import sklearn
    P(f'# Hunt 24 results (sklearn {sklearn.__version__}, GBR 100/0.05/depth2/leaf200, monthly refit, 0.5% risk, one position, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    learn = (D['t'] >= int(T_LEARN0.value // 10**6)) & (D['t'] < int(T_LEARN1.value // 10**6))
    P(f"decisions with both labels: {len(D)} total, {int(learn.sum())} in learn 2015-2023 | unconditional mean label: long {D.loc[learn,'rL'].mean():+.3f}R, short {D.loc[learn,'rS'].mean():+.3f}R")
    cells = {}; preds = {}
    for W in WS:
        pL, pS = walk_forward(D, W, 'model'); preds[W] = (pL, pS)
        for T in TS:
            cells[(W, T)] = select_and_book(D, pL, pS, T); log(f'  W{W} T{T}: {summ(cells[(W,T)])}')
    P(); P('## LEARN 2015-2023, net R per trade (base), one position at a time')
    P('| W \\ T | 0.05 | 0.10 | 0.15 |'); P('|---|---|---|---|')
    for W in WS: P(f'| {W}m | ' + ' | '.join((f"{cells[(W,T)]['r'].mean():+.3f}R ({len(cells[(W,T)])})" if len(cells[(W,T)]) else 'n/a') for T in TS) + ' |')
    prim = cells[PRIMARY]; pL, pS = preds[24]
    assert prim['t'].min() >= int(T_LEARN0.value // 10**6) and prim['t'].max() < int(T_LEARN1.value // 10**6), 'trades outside the predicted months: alignment bug'
    P(); P(f'### primary W24/T0.10: {summ(prim)}')
    if len(prim):
        for nm, a, b in [('2015-18', '2015', '2019'), ('2019-23', '2019', '2024')]:
            g = prim[(prim['t'] >= int(pd.Timestamp(a, tz='UTC').value // 10**6)) & (prim['t'] < int(pd.Timestamp(b, tz='UTC').value // 10**6))]; P(f'  {nm}: {summ(g)}')
        for s, g in prim.groupby('sym'): P(f'  {s}: {summ(g)}')
        P(f"  predicted vs realised: mean prediction {prim['pred'].mean():+.3f}R, realised {prim['r'].mean():+.3f}R; corr {np.corrcoef(prim['pred'], prim['r'])[0,1]:+.3f}")
        P('year | trades | R/trade | total')
        for y, g in prim.groupby(pd.to_datetime(prim['t'], unit='ms').dt.year): P(f"{y} | {len(g)} | {g['r'].mean():+.3f} | {g['r'].sum():+.1f}")
    # controls
    mL, mS = walk_forward(D, 24, 'mean')
    c1 = select_and_book(D, mL, mS, 0.10); c2 = select_and_book(D, pL, pS, 0.10, force='long'); c3 = select_and_book(D, pL, pS, 0.10, force='short')
    c2 = c2[(c2['t'] >= int(T_LEARN0.value // 10**6)) & (c2['t'] < int(T_LEARN1.value // 10**6))]; c3 = c3[(c3['t'] >= int(T_LEARN0.value // 10**6)) & (c3['t'] < int(T_LEARN1.value // 10**6))]
    P(); P(f'control 1 (window-mean predictions): {summ(c1)}'); P(f'control 2 (always long): {summ(c2)}'); P(f'control 3 (always short): {summ(c3)}')
    # stress: relabel primary trades at 1bp slip approximately by re-simulating? labels were built at 0.5bp; stress = extra 0.5bp x 2 executions on entry notional / dist
    if len(prim):
        D_idx = D.set_index(['sym', 't'])
        extra = []
        for r in prim.itertuples():
            row = D_idx.loc[(r.sym, r.t)]; e = row['entryL'] if r.side == 1 else row['entryS']; extra.append(2 * 0.5 / 1e4 * e / row['dist'])
        stress_mean = (prim['r'] - np.array(extra)).mean()
    else: stress_mean = np.nan
    ret = daily_equity(prim, T_LEARN0, T_LEARN1); lo, hi = boot_months(ret)
    neigh = [cells[(12, 0.10)], cells[(36, 0.10)], cells[(24, 0.05)], cells[(24, 0.15)]]; npos = sum(1 for t in neigh if len(t) and t['r'].mean() > 0)
    def tot(t): return t['r'].sum() if len(t) else 0
    def per(a, b):
        g = prim[(prim['t'] >= int(pd.Timestamp(a, tz='UTC').value // 10**6)) & (prim['t'] < int(pd.Timestamp(b, tz='UTC').value // 10**6))]; return tot(g)
    passed = len(prim) >= 200 and per('2015', '2019') > 0 and per('2019', '2024') > 0 and stress_mean > 0 and lo > 0 and npos >= 3 and tot(prim) > max(tot(c1), tot(c2), tot(c3))
    P(); P(f"## GATE: trades {len(prim)}; 2015-18 {per('2015','2019'):+.1f}R; 2019-23 {per('2019','2024'):+.1f}R; stress mean {stress_mean:+.3f}R; daily-equity whole-month bootstrap CI [{lo*1e4:+.2f}, {hi*1e4:+.2f}] bp/day; neighbours {npos}/4; beats controls {tot(prim) > max(tot(c1), tot(c2), tot(c3))} -> {'PASS' if passed else 'FAIL'}")
    P('## EXAM: ' + ('to run' if passed else 'NOT INSPECTED, sealed.'))
    prim.to_csv(os.path.join(HERE, 'hunt24_primary.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt24-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
