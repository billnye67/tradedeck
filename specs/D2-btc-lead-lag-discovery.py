"""Discovery Study D2 - BTC 5-minute shock -> most under-reacting low-liquidity follower, 1-minute VWAP execution.
Frozen map: specs/D2-btc-lead-lag-discovery.md. Stage 'dev' = development (earliest .. 2023-12-31) all 9 cells;
confirmation 2024 opened for the primary cell only if dev gates pass. Holdout 2025+ never read here."""
import os, sys, json, time
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); K = os.path.join(HERE, 'k1m')
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
MIN = 60_000; DAY = 86_400_000
DEV1 = int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6); CONF1 = int(pd.Timestamp('2025-01-01', tz='UTC').value // 10**6)
DELAYS, HOLDS = [1, 2, 5], [5, 15, 30]; PRIMARY = (1, 15)
BASE_BP, STRESS_BP = 20.0, 40.0

def log(*a): print(*a, flush=True)

def load(sym, t_end):
    df = pd.read_parquet(os.path.join(K, f'{sym}.parquet')); df = df[df['ts'] < t_end]
    return df.set_index('ts')

def vwap_series(df):
    v = df['quote_volume'] / df['volume'].replace(0, np.nan)
    return v.fillna(df['close'])

def monthly_universe(symbols, data, months):
    """for each month start: eligible followers and the lower-volume half."""
    uni = {}
    for m0 in months:
        t0 = int(m0.value // 10**6); t30 = t0 - 30 * DAY; t180 = t0 - 180 * DAY
        rows = []
        for s in symbols:
            if s == 'BTCUSDT': continue
            d = data[s]
            if len(d) == 0 or d.index[0] > t180: continue
            w = d[(d.index >= t30) & (d.index < t0)]
            if len(w) < 0.99 * 30 * 1440: continue
            dq = w['quote_volume'].groupby(w.index // DAY).sum()
            if dq.median() < 25e6: continue
            rows.append((s, dq.median()))
        rows.sort(key=lambda x: x[1])
        uni[t0] = [s for s, _ in rows[:len(rows) // 2]]
    return uni

def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    t_end = DEV1 if stage == 'dev' else CONF1
    t0 = time.time(); lines = []; P = lambda s='': (lines.append(s), log(s))
    symbols = json.load(open(os.path.join(HERE, 'universe_binance.json')))
    data = {s: load(s, t_end) for s in symbols if os.path.exists(os.path.join(K, f'{s}.parquet'))}
    btc = data['BTCUSDT']
    log(f'{len(data)} symbols loaded; BTC {len(btc):,} minutes')
    # BTC 5-minute returns at every minute close, trailing 99.5th percentile of |r5| over the previous 30 days (before the window)
    c = btc['close']; r5 = c / c.shift(5) - 1
    thr = r5.abs().rolling(30 * 1440, min_periods=20 * 1440).quantile(0.995).shift(5)      # computed on data before the current window
    shock = (r5.abs() >= thr) & thr.notna()
    # first crossing + 60-min global cooldown
    ts = btc.index.values; sh = shock.values; events = []; cool = -1
    for i in np.where(sh)[0]:
        t = ts[i]
        if t < cool or (i > 0 and sh[i - 1]): continue
        events.append((int(t), float(r5.iat[i]))); cool = t + 60 * MIN
    log(f'BTC shock events: {len(events)}')
    first_month = pd.Timestamp(ts[0], unit='ms', tz='UTC').to_period('M').to_timestamp(how='start').tz_localize('UTC') + pd.offsets.MonthBegin(1)
    months = pd.date_range(first_month, pd.Timestamp(t_end, unit='ms', tz='UTC'), freq='MS')
    uni = monthly_universe(symbols, data, months); mkeys = sorted(uni)
    # precompute 5-minute close series and VWAP per symbol for beta/score and execution
    close5 = {s: data[s]['close'] for s in data}; vw = {s: vwap_series(data[s]) for s in data}
    rows = []; skip = dict(no_universe=0, no_positive_score=0, missing_bars=0)
    for t, rb in events:
        mk = [k for k in mkeys if k <= t]
        if not mk: skip['no_universe'] += 1; continue
        pool = uni[mk[-1]]
        if not pool: skip['no_universe'] += 1; continue
        # synchronized 5-minute returns over the trailing 30 days (excluding the event window)
        a = t - 30 * DAY; b = t - 5 * MIN
        grid = np.arange(a, b, 5 * MIN)
        rb_ser = btc['close'].reindex(grid); rb5 = (rb_ser / rb_ser.shift(1) - 1).dropna()
        best = None
        for s in pool:
            cs = close5[s]
            if t not in cs.index or (t - 5 * MIN) not in cs.index: continue
            ri_ser = cs.reindex(grid); ri5 = (ri_ser / ri_ser.shift(1) - 1)
            both = pd.concat([ri5, rb5], axis=1, join='inner').dropna()
            if len(both) < 0.9 * (len(grid) - 1): continue
            var_b = both.iloc[:, 1].var()
            if not (var_b > 0): continue
            beta = both.iloc[:, 0].cov(both.iloc[:, 1]) / var_b; sig = both.iloc[:, 0].std()
            if not (beta > 0) or not (sig > 0): continue
            ri = cs.at[t] / cs.at[t - 5 * MIN] - 1
            sgn = 1 if rb > 0 else -1
            score = sgn * (beta * rb - ri) / sig
            if score > 0 and (best is None or score > best[1]): best = (s, score, beta, ri)
        if best is None: skip['no_positive_score'] += 1; continue
        s, score, beta, ri = best; sgn = 1 if rb > 0 else -1
        rec = dict(t=t, follower=s, score=score, beta=beta, btc_shock_bp=rb * 1e4, dir=sgn, day=t // DAY)
        ok = True
        for dly in DELAYS:
            te = t + dly * MIN
            if te not in vw[s].index or not np.isfinite(vw[s].at[te]): ok = False; break
            pe = vw[s].at[te]
            for hold in HOLDS:
                tx = te + hold * MIN
                if tx not in vw[s].index or not np.isfinite(vw[s].at[tx]): ok = False; break
                px = vw[s].at[tx]; rec[f'g_{dly}_{hold}'] = sgn * (px / pe - 1) * 1e4
                seg = data[s].loc[te + MIN:tx]
                if dly == 1 and hold == 15 and len(seg):
                    rec['mfe_bp'] = (sgn * (seg['high'] / pe - 1)).max() * 1e4 if sgn == 1 else (sgn * (seg['low'] / pe - 1)).max() * 1e4
                    rec['mae_bp'] = (sgn * (seg['low'] / pe - 1)).min() * 1e4 if sgn == 1 else (sgn * (seg['high'] / pe - 1)).min() * 1e4
                    rec['entry_vol_usd'] = float(data[s].at[te, 'quote_volume'])
            if not ok: break
        if not ok: skip['missing_bars'] += 1; continue
        rows.append(rec)
    E = pd.DataFrame(rows); E.to_parquet(os.path.join(HERE, f'd2_events_{stage}.parquet'))
    P(f'# D2 results — {"DEVELOPMENT (earliest → 2023-12)" if stage == "dev" else "CONFIRMATION 2024 (opened once)"} ({time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P(f"symbols on both lists {len(symbols)}; BTC shock events {len(events)}; skipped: {skip}; accepted events {len(E)} on {E['day'].nunique() if len(E) else 0} days; distinct followers {E['follower'].nunique() if len(E) else 0}")
    if len(E) == 0: P('no events'); open(os.path.join(OUT, f'D2-results-{stage}.md'), 'w').write('\n'.join(lines)); return
    def block_ci(x, days, n=5000, seed=7):
        blk = days // 3; u = np.unique(blk); rng = np.random.default_rng(seed); g = [x[blk == b] for b in u]; o = np.empty(n)
        for k in range(n): pick = rng.integers(0, len(g), len(g)); o[k] = np.concatenate([g[j] for j in pick]).mean()
        return np.percentile(o, 2.5), np.percentile(o, 97.5)
    P(); P('## Lag grid: mean gross bp (median) | net after 20bp | events')
    P('| delay \\ hold | 5m | 15m | 30m |'); P('|---|---|---|---|')
    for dly in DELAYS: P(f'| {dly}m | ' + ' | '.join(f"{E[f'g_{dly}_{h}'].mean():+.1f} ({E[f'g_{dly}_{h}'].median():+.1f}) | net {E[f'g_{dly}_{h}'].mean()-BASE_BP:+.1f}" for h in HOLDS) + ' |')
    g = E['g_1_15']; lo, hi = block_ci(g.values, E['day'].values)
    up = E[E['dir'] == 1]['g_1_15']; dn = E[E['dir'] == -1]['g_1_15']
    ex10 = g.drop(g.nlargest(10).index).mean()
    prof = E.groupby('follower')['g_1_15'].sum(); share = prof.max() / prof[prof > 0].sum() if (prof > 0).any() else np.nan
    yrs = E.assign(y=pd.to_datetime(E['t'], unit='ms').dt.year).groupby('y')['g_1_15'].agg(['size', 'mean'])
    P(); P(f"### PRIMARY 1m delay / 15m hold: {len(E)} events | gross {g.mean():+.1f} bp (median {g.median():+.1f}) | 3-day block CI [{lo:+.1f}, {hi:+.1f}] | net base {g.mean()-BASE_BP:+.1f} / stress {g.mean()-STRESS_BP:+.1f} | win {(g>0).mean()*100:.0f}% | up-shocks {len(up)} {up.mean():+.1f} bp, down-shocks {len(dn)} {dn.mean():+.1f} bp | ex-10-best {ex10:+.1f} | MFE med {E['mfe_bp'].median():+.0f} / MAE med {E['mae_bp'].median():+.0f} | top follower share of profit {share:.2f} | median entry-minute volume ${E['entry_vol_usd'].median():,.0f}")
    P('year | events | mean gross bp'); [P(f'{y} | {int(r["size"])} | {r["mean"]:+.1f}') for y, r in yrs.iterrows()]
    P('followers (events, mean bp): ' + ', '.join(f"{k.replace('USDT','')} ({int(v['size'])}, {v['mean']:+.0f})" for k, v in E.groupby('follower')['g_1_15'].agg(['size', 'mean']).sort_values('size', ascending=False).head(15).iterrows()))
    if stage == 'dev':
        gates = {'>=400 events': len(E) >= 400, 'mean gross >= 40bp': g.mean() >= 40, 'CI lower > 20bp': lo > 20, 'median > 0': g.median() > 0,
                 'up and down each > 20bp': up.mean() > BASE_BP and dn.mean() > BASE_BP, 'ex-10-best > 20bp': ex10 > BASE_BP,
                 '>=10 followers, none >35%, >=3 years positive': E['follower'].nunique() >= 10 and (share <= 0.35) and int((yrs['mean'] > 0).sum()) >= 3,
                 '2m-delay/15m > 20bp': E['g_2_15'].mean() > BASE_BP}
        P(); P('## DEVELOPMENT GATES: ' + ', '.join(f'{k}: {"ok" if v else "FAIL"}' for k, v in gates.items()))
        P(f'verdict: {"PASS -> confirmation 2024 may be opened once" if all(gates.values()) else "FAIL, confirmation not opened, holdout untouched"} ({sum(1 for v in gates.values() if not v)} failures)')
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, f'D2-results-{stage}.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
