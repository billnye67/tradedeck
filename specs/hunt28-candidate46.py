"""Candidate 46 - stop-free long-liquidation rebound, HOLDOUT ONLY (2025-06-01 .. 2026-08-28), opened once.
Reuses c45.py functions unchanged; use_stop=False; pass/fail sizing = half of #45 (5% notional)."""
import os, sys, json, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c45 as h

HOLD0 = h.DEV1; HOLD1 = h.END
HALF = h.W_NOTIONAL / 2

def account(t, w):
    eq = h.START_EQ; eqs = []; times = []
    for r in t.sort_values('t_out').itertuples(): eq *= 1 + w * r.net_bp / 1e4; eqs.append(eq); times.append(r.t_out)
    s = pd.Series(eqs, index=pd.to_datetime(times, unit='ms')); dd = float((s / s.cummax() - 1).min())
    worst24 = 0.0
    for i in range(len(s)):
        wdw = s[(s.index > s.index[i] - pd.Timedelta(hours=24)) & (s.index <= s.index[i])]
        if len(wdw) > 1: worst24 = min(worst24, wdw.iloc[-1] / wdw.iloc[0] - 1)
    daily = s.resample('1D').last().ffill().pct_change().dropna()
    return dict(final=eq, ret=eq / h.START_EQ - 1, mdd=dd, worst24=worst24, sharpe=float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else np.nan)

def main():
    t0 = time.time(); lines = []; P = lambda s='': (lines.append(s), h.log(s))
    frames = {}
    for c in h.COINS:
        df = h.load(c, h.END); sig = h.signals(df); frames[c] = (df, sig)
    dfs = {c: frames[c][0] for c in h.COINS}
    P(f'# Candidate 46 — HOLDOUT 2025-06-01 → 2026-08-28, opened once ({time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}). No stop, 8h exit, half sizing (5% notional) for gates.')
    base_c, sk = h.candidates(frames, HOLD0, HOLD1, 2.0, use_stop=False); stress_c, _ = h.candidates(frames, HOLD0, HOLD1, 5.0, use_stop=False)
    base, rej = h.standalone(base_c); stress, _ = h.standalone(stress_c)
    P(f"signals after cooldown {len(base_c)}, rejected for slots {rej}, skipped missing bar {sk['missing next bar']}")
    if len(base) == 0: P('no trades'); return
    lo, hi = h.block_ci(base['net_bp'].values, base['t_in'].values)
    ex5 = base['net_bp'].drop(base['net_bp'].nlargest(5).index).mean()
    P(f"STANDALONE base: {len(base)} trades on {pd.to_datetime(base['t_in'], unit='ms').dt.date.nunique()} days | gross {base['gross_bp'].mean():+.1f} (median {base['gross_bp'].median():+.1f}) | net {base['net_bp'].mean():+.1f} bp | CI [{lo:+.1f}, {hi:+.1f}] | win {(base['net_bp']>0).mean()*100:.0f}% | payoff {base.loc[base['net_bp']>0,'net_bp'].mean()/-base.loc[base['net_bp']<=0,'net_bp'].mean():.2f} | ex-5-best {ex5:+.1f} | MFE med {base['mfe_bp'].median():+.0f} / MAE med {base['mae_bp'].median():+.0f} | worst trade {base['net_bp'].min():+.0f} bp | costs comm {base['comm_bp'].mean():.1f} slip {base['slip_bp'].mean():.1f} fin {base['fin_bp'].mean():.1f}")
    P(f"STANDALONE stress: net {stress['net_bp'].mean():+.1f} bp on {len(stress)}")
    by_coin = base.groupby('coin')['net_bp'].agg(['mean', 'size', 'sum'])
    for c, r in by_coin.iterrows(): P(f"  {c}: {int(r['size'])} trades, net {r['mean']:+.1f} bp")
    top = by_coin['sum'].idxmax(); wo_top = base[base['coin'] != top]['net_bp'].mean()
    P(f"  without the most profitable coin ({top}): net {wo_top:+.1f} bp")
    h2 = base[base['t_in'] < int(pd.Timestamp('2026-01-01', tz='UTC').value // 10**6)]; y26 = base[base['t_in'] >= int(pd.Timestamp('2026-01-01', tz='UTC').value // 10**6)]
    P(f"  2025-H2: {len(h2)} trades, net {h2['net_bp'].mean():+.1f} bp, total {h2['net_bp'].sum()*HALF/1e4*100:+.2f}% equity | 2026 Jan-Aug: {len(y26)} trades, net {y26['net_bp'].mean():+.1f} bp, total {y26['net_bp'].sum()*HALF/1e4*100:+.2f}% equity")
    for m, g in base.groupby(pd.to_datetime(base['t_in'], unit='ms').dt.to_period('M')): P(f"  {m}: {len(g)} trades, net {g['net_bp'].mean():+.1f} bp")
    acc_h = account(base, HALF); acc_f = account(base, h.W_NOTIONAL)
    P(f"account HALF sizing (5% notional): return {acc_h['ret']*100:+.2f}%, Sharpe {acc_h['sharpe']:+.2f}, maxDD {acc_h['mdd']*100:.2f}%, worst 24h {acc_h['worst24']*100:+.2f}% | diagnostic full sizing: return {acc_f['ret']*100:+.2f}%, maxDD {acc_f['mdd']*100:.2f}%, worst 24h {acc_f['worst24']*100:+.2f}%")
    # combined with raids (half sizing for C46)
    raids = h.load_raids(); raids = raids[(raids['entryTs'] >= HOLD0) & (raids['entryTs'] < HOLD1)]
    comb, preempted, skipped_slot = h.combined(base_c, raids, 2.0); ct = pd.DataFrame(comb)
    if len(ct): ct['net_bp'] = [h.preempt_pnl(dfs, r, 2.0) if r.get('preempted') else r['net_bp'] for r in comb]
    def eq_curve(raid_df, c_df, w):
        ev = [(int(r.exitTs), r.netR * 0.005) for r in raid_df.itertuples()]
        if c_df is not None and len(c_df): ev += [(int(r.t_out), w * r.net_bp / 1e4) for r in c_df.itertuples()]
        ev.sort(); eq = h.START_EQ; eqs = []
        for _, ret in ev: eq *= 1 + ret; eqs.append(eq)
        s = pd.Series(eqs); return eq, float((s / s.cummax() - 1).min()) if len(s) else 0.0
    eq_r, dd_r = eq_curve(raids, None, HALF); eq_c, dd_c = eq_curve(raids, ct, HALF)
    added = (eq_c - eq_r) / h.START_EQ
    P(f"COMBINED (raids first, one C46 slot, half sizing): C46 trades {len(ct)}, preempted {preempted}, skipped for slots {skipped_slot}; raids {len(raids)} ({raids['netR'].sum():+.1f}R); raid-only final {eq_r:,.0f} maxDD {dd_r*100:.2f}% | combined final {eq_c:,.0f} maxDD {dd_c*100:.2f}% | C46 adds {added*100:+.2f}% equity")
    gates = {'1 >=250 trades': len(base) >= 250, '2 base net > 10bp': base['net_bp'].mean() > 10, '3 CI lower > 0': lo > 0, '4 stress net > 0': stress['net_bp'].mean() > 0, '5 ex-5-best > 0': ex5 > 0,
             '6 both sub-periods positive': h2['net_bp'].sum() > 0 and y26['net_bp'].sum() > 0, '7 >=2 coins positive & positive w/o top coin': int((by_coin['mean'] > 0).sum()) >= 2 and wo_top > 0,
             '8 half-size DD<=4.5%, 24h>=-2%, combined adds>=1%, DD not above raid-only': acc_h['mdd'] >= -0.045 and acc_h['worst24'] >= -0.02 and added >= 0.01 and dd_c >= dd_r}
    P(); P('## HOLDOUT GATES: ' + ', '.join(f'{k}: {"ok" if v else "KILL"}' for k, v in gates.items()))
    P(f'verdict: {"PASS" if all(gates.values()) else "KILLED"} ({sum(1 for v in gates.values() if not v)} of 8 failed)')
    base.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'c46_holdout_trades.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(h.OUT, 'hunt28-results-holdout.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
