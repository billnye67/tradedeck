"""Candidate 45 - long-liquidation rebound (Perplexity frozen spec, specs/hunt27-candidate45-long-liquidation-rebound.md).
stage dev  -> development period 2021-12 .. 2025-05 (gates)
stage hold -> holdout 2025-06 .. 2026-08 (only if dev passed)"""
import sqlite3, os, sys, json, time
import numpy as np, pandas as pd
from scipy import stats

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']; PRIO = {c: i for i, c in enumerate(COINS)}
BAR = 300_000; H = 3600_000; DAY = 86_400_000; WB = 12; WARM = 365
T0 = int(pd.Timestamp('2021-12-01', tz='UTC').value // 10**6); DEV1 = int(pd.Timestamp('2025-06-01', tz='UTC').value // 10**6); END = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)
COMM_BP = 3.0; STOP = 0.025; RISK = 0.0025; W_NOTIONAL = RISK / STOP; FIN_BP = 5.0; START_EQ = 50_000.0

def log(*a): print(*a, flush=True)

def load(c, t_end):
    con = sqlite3.connect(DB)
    px = pd.read_sql_query("select timestamp ts, open, high, low, close from candles where source='binance_um' and market=? and interval='5m' and timestamp>=? and timestamp<? order by timestamp", con, params=(c, T0, t_end))
    con.close()
    met = pd.read_parquet(os.path.join(SP, 'h25', f'metrics_{c}.parquet'))
    if met['ts'].max() < 1e12: met['ts'] = met['ts'] * 1000
    met['oi'] = met['sum_open_interest'].astype(float); met.loc[met['oi'] <= 0, 'oi'] = np.nan
    return px.merge(met[['ts', 'oi']].drop_duplicates('ts'), on='ts', how='left').sort_values('ts').reset_index(drop=True)

def signals(df):
    """exact D1 state-4 logic (1h window): price r <= trailing 25th pct, OI change <= trailing 2.5th pct; thresholds from the previous 365 days of non-overlapping hourly samples, recomputed daily."""
    ts = df['ts'].values; cl = df['close'].values; oi = df['oi'].values; n = len(df)
    idx = np.arange(n); prev = idx - WB
    ok = (idx >= WB) & (ts - np.roll(ts, WB) == WB * BAR)
    r = np.full(n, np.nan); d = np.full(n, np.nan)
    r[ok] = cl[ok] / cl[prev[ok]] - 1; d[ok] = oi[ok] / oi[prev[ok]] - 1
    # missing OI anywhere in the window invalidates: require all 13 snapshots present
    oi_ok = pd.Series(np.isnan(oi).astype(int)).rolling(WB + 1).sum().values == 0
    samp = np.arange(WB, n, WB); s_ts = ts[samp]; s_r = r[samp]; s_d = d[samp]
    qd = {}
    for g in np.arange((ts[0] // DAY) * DAY, ts[-1] + DAY, DAY):
        m = (s_ts >= g - WARM * DAY) & (s_ts < g) & ~np.isnan(s_r) & ~np.isnan(s_d)
        qd[g] = None if (m.sum() < 200 or (g - ts[0]) < WARM * DAY) else (np.quantile(s_r[m], 0.25), np.quantile(s_d[m], 0.025))
    sig = np.zeros(n, bool)
    for i in range(n):
        q = qd.get((ts[i] // DAY) * DAY)
        if q is None or np.isnan(r[i]) or np.isnan(d[i]) or not oi_ok[i]: continue
        sig[i] = (r[i] <= q[0]) and (d[i] <= q[1])
    return sig

def simulate_trade(df, i, slip_bp, use_stop=True):
    ts = df['ts'].values; op = df['open'].values; hi = df['high'].values; lo = df['low'].values
    k = i + 1
    if k >= len(ts) or ts[k] - ts[i] > BAR + 5 * 60_000: return None
    entry = op[k] * (1 + slip_bp / 1e4); stop = entry * (1 - STOP)
    t_x = ts[k] + 8 * H; kend = np.searchsorted(ts, t_x)
    if kend >= len(ts): return None
    how = 'time'; px = None; kx = kend
    if use_stop:
        for j in range(k + 1, kend):
            if op[j] <= stop: px = op[j] * (1 - slip_bp / 1e4); how = 'stop-gap'; kx = j; break
            if lo[j] <= stop: px = stop * (1 - slip_bp / 1e4); how = 'stop'; kx = j; break
    if px is None: px = op[kend] * (1 - slip_bp / 1e4)
    gross_bp = (op[kx] / op[k] - 1) * 1e4
    net_bp = (px / entry - 1) * 1e4 - 2 * COMM_BP
    crossed = (ts[kx] // DAY) != (ts[k] // DAY)
    fin_bp = FIN_BP if crossed else 0.0
    seg_hi = hi[k + 1:kx + 1]; seg_lo = lo[k + 1:kx + 1]
    return dict(t_sig=int(ts[i]), t_in=int(ts[k]), t_out=int(ts[kx]), entry=entry, exit=px, gross_bp=gross_bp, net_bp=net_bp - fin_bp, comm_bp=2 * COMM_BP, slip_bp=2 * slip_bp, fin_bp=fin_bp, how=how,
                mfe_bp=(seg_hi.max() / entry - 1) * 1e4 if len(seg_hi) else 0.0, mae_bp=(seg_lo.min() / entry - 1) * 1e4 if len(seg_lo) else 0.0)

def candidates(frames, t_lo, t_hi, slip_bp, use_stop=True):
    """all events with per-coin 8h cooldown; trade simulated independently (slot logic applied later)."""
    out = []; skipped = {'missing next bar': 0}
    for c, (df, sig) in frames.items():
        ts = df['ts'].values; cool = -1
        for i in np.where(sig)[0]:
            if ts[i] < t_lo or ts[i] >= t_hi or ts[i] < cool: continue
            cool = ts[i] + 8 * H
            t = simulate_trade(df, i, slip_bp, use_stop)
            if t is None: skipped['missing next bar'] += 1; continue
            t['coin'] = c; out.append(t)
    return sorted(out, key=lambda t: (t['t_sig'], PRIO[t['coin']])), skipped

def standalone(cands):
    open_ = []; taken = []; rejected = 0
    for t in cands:
        open_ = [o for o in open_ if o > t['t_in']]
        if len(open_) >= 2: rejected += 1; continue
        taken.append(t); open_.append(t['t_out'])
    return pd.DataFrame(taken), rejected

def load_raids():
    r = pd.read_csv(os.path.join(SP, 'global2_strict.csv')); r = r.sort_values('entryTs').reset_index(drop=True)
    return r

def combined(cands, raids, slip_bp):
    """#1 priority. C45 holds at most one slot; preempted (closed at next open) if a raid needs its slot."""
    raids = raids[['market', 'entryTs', 'exitTs', 'netR']].to_records(index=False)
    ri = 0; r_open = []; c_open = None; out = []; preempted = 0; blocked_raids = 0; skipped_slot = 0
    events = sorted([('c', t['t_sig'], t) for t in cands] + [('r', int(x.entryTs), x) for x in raids], key=lambda e: (e[1], 0 if e[0] == 'r' else 1))
    for kind, t, obj in events:
        r_open = [x for x in r_open if x > t]
        if c_open is not None and c_open['t_out'] <= t: c_open = None
        if kind == 'r':
            need = 1
            same_coin = c_open is not None and c_open['coin'] == obj.market
            if same_coin or (len(r_open) + (1 if c_open else 0) >= 2 and c_open is not None):
                # preempt C45 at the next executable price (approximate: raid entry time, exit at that bar's open with slippage)
                c_open = dict(c_open); c_open['preempted'] = True; c_open['t_out_eff'] = t; preempted += 1
                out.append(c_open); c_open = None
            if len(r_open) >= 2: blocked_raids += 1; continue      # raid-only would also block itself here; counted for info
            r_open.append(int(obj.exitTs))
        else:
            if c_open is not None or len(r_open) >= 2: skipped_slot += 1; continue
            if len(r_open) + 1 > 2: skipped_slot += 1; continue
            c_open = dict(obj); c_open['preempted'] = False
    if c_open is not None: out.append(c_open)
    return out, preempted, skipped_slot

def preempt_pnl(df_by_coin, tr, slip_bp):
    """recompute net for a preempted trade: exit at the open of the first bar at/after preemption time."""
    df = df_by_coin[tr['coin']]; ts = df['ts'].values; op = df['open'].values
    k = np.searchsorted(ts, tr['t_out_eff']); k = min(k, len(ts) - 1)
    # if the stop would have hit before preemption, keep the stop result
    if tr['how'].startswith('stop') and tr['t_out'] <= tr['t_out_eff']: return tr['net_bp']
    px = op[k] * (1 - slip_bp / 1e4); crossed = (ts[k] // DAY) != (tr['t_in'] // DAY)
    return (px / tr['entry'] - 1) * 1e4 - 2 * COMM_BP - (FIN_BP if crossed else 0.0)

def block_ci(x, t_in, n=5000, seed=7):
    blk = (np.array(t_in) // DAY) // 3; u = np.unique(blk); rng = np.random.default_rng(seed); groups = [x[blk == b] for b in u]; o = np.empty(n)
    for k in range(n):
        pick = rng.integers(0, len(groups), len(groups)); o[k] = np.concatenate([groups[j] for j in pick]).mean()
    return np.percentile(o, 2.5), np.percentile(o, 97.5)

def account_path(t):
    eq = START_EQ; eqs = []; times = []
    for r in t.sort_values('t_out').itertuples():
        eq *= 1 + W_NOTIONAL * r.net_bp / 1e4; eqs.append(eq); times.append(r.t_out)
    s = pd.Series(eqs, index=pd.to_datetime(times, unit='ms')); dd = (s / s.cummax() - 1).min()
    # worst rolling 24h
    worst24 = 0.0
    for i in range(len(s)):
        w = s[(s.index > s.index[i] - pd.Timedelta(hours=24)) & (s.index <= s.index[i])]
        if len(w) > 1: worst24 = min(worst24, w.iloc[-1] / w.iloc[0] - 1)
    yrs = (t['t_out'].max() - t['t_in'].min()) / DAY / 365.25
    daily = s.resample('1D').last().ffill().pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else np.nan
    return dict(final=eq, cagr=(eq / START_EQ) ** (1 / yrs) - 1, mdd=dd, worst24=worst24, sharpe=sharpe)

def report(tag, t, P, slip_bp):
    if len(t) == 0: P(f'### {tag}: no trades'); return None
    lo, hi = block_ci(t['net_bp'].values, t['t_in'].values)
    acc = account_path(t); ex10 = t['net_bp'].drop(t['net_bp'].nlargest(10).index).mean()
    P(f"### {tag}: {len(t)} trades on {pd.to_datetime(t['t_in'], unit='ms').dt.date.nunique()} days | gross {t['gross_bp'].mean():+.1f} bp (median {t['gross_bp'].median():+.1f}) | net {t['net_bp'].mean():+.1f} bp | 3-day block CI [{lo:+.1f}, {hi:+.1f}] | win {(t['net_bp']>0).mean()*100:.0f}% | payoff {t.loc[t['net_bp']>0,'net_bp'].mean()/-t.loc[t['net_bp']<=0,'net_bp'].mean():.2f} | stops {t['how'].str.startswith('stop').sum()} | ex-10-best {ex10:+.1f} bp | MFE med {t['mfe_bp'].median():+.0f} / MAE med {t['mae_bp'].median():+.0f}")
    P(f"  costs per trade: comm {t['comm_bp'].mean():.1f} + slip {t['slip_bp'].mean():.1f} + financing {t['fin_bp'].mean():.1f} bp (midnight crossings {int((t['fin_bp']>0).sum())}) | account (10% notional/trade): final {acc['final']:,.0f}, CAGR {acc['cagr']*100:+.2f}%, Sharpe {acc['sharpe']:+.2f}, maxDD {acc['mdd']*100:.2f}%, worst 24h {acc['worst24']*100:+.2f}%")
    for c, g in t.groupby('coin'): P(f"  {c}: {len(g)} trades, net {g['net_bp'].mean():+.1f} bp, win {(g['net_bp']>0).mean()*100:.0f}%")
    for y, g in t.groupby(pd.to_datetime(t['t_in'], unit='ms').dt.year): P(f"  {y}: {len(g)} trades, net {g['net_bp'].mean():+.1f} bp, total {g['net_bp'].sum()*W_NOTIONAL/1e4*100:+.2f}% of equity")
    return dict(n=len(t), net=t['net_bp'].mean(), lo=lo, coins_pos=int((t.groupby('coin')['net_bp'].mean() > 0).sum()), ex10=ex10, acc=acc)

def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    t_lo, t_hi = (T0, DEV1) if stage == 'dev' else (DEV1, END)
    t0 = time.time(); lines = []; P = lambda s='': (lines.append(s), log(s))
    frames = {}
    for c in COINS:
        df = load(c, END); sig = signals(df); frames[c] = (df, sig); log(f'{c}: {int(sig.sum())} state bars')
    dfs = {c: frames[c][0] for c in COINS}
    P(f'# Candidate 45 — {"DEVELOPMENT 2021-12 → 2025-05" if stage == "dev" else "HOLDOUT 2025-06 → 2026-08 (opened once)"} ({time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    base_c, sk = candidates(frames, t_lo, t_hi, 2.0); stress_c, _ = candidates(frames, t_lo, t_hi, 5.0); nostop_c, _ = candidates(frames, t_lo, t_hi, 2.0, use_stop=False)
    P(f"signals after 8h per-coin cooldown: {len(base_c)}; skipped for missing next bar: {sk['missing next bar']}")
    base, rej = standalone(base_c); stress, _ = standalone(stress_c); nostop, _ = standalone(nostop_c)
    P(f'standalone (2 slots): rejected because both slots busy: {rej}')
    B = report('STANDALONE base (3+2bp)', base, P, 2.0); S = report('STANDALONE stress (3+5bp)', stress, P, 5.0); N = report('STANDALONE no-stop diagnostic', nostop, P, 2.0)
    # combined with #1
    raids = load_raids(); raids = raids[(raids['entryTs'] >= t_lo) & (raids['entryTs'] < t_hi)]
    comb, preempted, skipped_slot = combined(base_c, raids, 2.0)
    ct = pd.DataFrame(comb)
    if len(ct):
        ct['net_bp'] = [preempt_pnl(dfs, r, 2.0) if r.get('preempted') else r['net_bp'] for r in comb]
    raid_R = raids['netR'].sum(); c45_pct = (ct['net_bp'].sum() * W_NOTIONAL / 1e4 * 100) if len(ct) else 0.0
    raid_pct = raid_R * 0.5                                           # 0.5% risk per R
    P(); P(f"COMBINED with #1 (raids first, C45 one slot, preempted when a raid needs it): C45 trades {len(ct)}, preempted {preempted}, skipped for slots {skipped_slot}; raids in period {len(raids)} ({raid_R:+.1f}R = {raid_pct:+.1f}% of equity at 0.5%); C45 adds {c45_pct:+.2f}% of equity; raids blocked by C45: 0 by construction (preemption)")
    if len(ct): P(f"  C45 in combined: net {ct['net_bp'].mean():+.1f} bp/trade, preempted trades net {ct.loc[ct['preempted'],'net_bp'].mean() if ct['preempted'].any() else float('nan'):+.1f} bp")
    # drawdown comparison raid-only vs combined (daily equity, 0.5% per R for raids, 10% notional for C45)
    def eq_curve(raid_df, c45_df):
        ev = []
        for r in raid_df.itertuples(): ev.append((int(r.exitTs), r.netR * 0.005))
        if c45_df is not None and len(c45_df):
            for r in c45_df.itertuples(): ev.append((int(r.t_out), W_NOTIONAL * r.net_bp / 1e4))
        ev.sort(); eq = START_EQ; eqs = []
        for _, ret in ev: eq *= 1 + ret; eqs.append(eq)
        s = pd.Series(eqs); return eq, (s / s.cummax() - 1).min() if len(s) else 0.0
    eq_r, dd_r = eq_curve(raids, None); eq_c, dd_c = eq_curve(raids, ct)
    P(f"  raid-only: final {eq_r:,.0f}, maxDD {dd_r*100:.2f}% | combined: final {eq_c:,.0f}, maxDD {dd_c*100:.2f}%")
    if stage == 'dev' and B:
        gates = {'>=500 trades': B['n'] >= 500, 'base net > 10bp': B['net'] > 10, 'stress net > 0': S and S['net'] > 0, 'CI lower > 0': B['lo'] > 0, '>=3 coins positive': B['coins_pos'] >= 3,
                 'ex-10-best > 0': B['ex10'] > 0, 'maxDD <= 4.5% and worst24h >= -2%': B['acc']['mdd'] >= -0.045 and B['acc']['worst24'] >= -0.02,
                 'combined improves raid-only, DD +<=1pt': eq_c > eq_r and (dd_c - dd_r) >= -0.01}
        P(); P('## DEVELOPMENT GATES: ' + ', '.join(f'{k}: {"ok" if v else "KILL"}' for k, v in gates.items()))
        P(f'verdict: {"PASS -> holdout may be opened once" if all(gates.values()) else "KILLED before holdout"} ({sum(1 for v in gates.values() if not v)} failures)')
        json.dump({k: bool(v) for k, v in gates.items()}, open(os.path.join(HERE, 'dev_gates.json'), 'w'))
    if stage == 'hold' and B:
        ex5 = base['net_bp'].drop(base['net_bp'].nlargest(5).index).mean()
        cp = base.groupby('coin')['net_bp'].agg(['mean', 'size']); elig = cp[cp['size'] >= 20]
        gates = {'>=100 trades & 50 days': B['n'] >= 100 and pd.to_datetime(base['t_in'], unit='ms').dt.date.nunique() >= 50, 'gross > 10bp': base['gross_bp'].mean() > 10, 'base net > 0': B['net'] > 0,
                 'stress net > 0': S and S['net'] > 0, 'CI lower > 0': B['lo'] > 0, 'ex-5-best > 0': ex5 > 0, '>=3 coins positive (>=20 trades)': int((elig['mean'] > 0).sum()) >= min(3, len(elig)), 'combined improves raid-only': eq_c > eq_r}
        P(); P('## HOLDOUT GATES: ' + ', '.join(f'{k}: {"ok" if v else "KILL"}' for k, v in gates.items()))
        P(f'verdict: {"PASS" if all(gates.values()) else "KILLED"} ({sum(1 for v in gates.values() if not v)} failures)')
    base.to_csv(os.path.join(HERE, f'c45_{stage}_trades.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, f'hunt27-results-{stage}.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
