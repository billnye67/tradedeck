"""Hunt 19 - short-horizon cross-sectional crypto reversal pair (Perplexity candidate 35), frozen 2026-10-04."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
COINS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'LINKUSDT', 'DOGEUSDT', 'SOLUSDT', 'AVAXUSDT']
START_EQ = 50_000.0; COMM_BP = 3.0
START = int(pd.Timestamp('2021-01-01', tz='UTC').value // 10**6); END = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)
BAR = 300_000; H2 = 7_200_000; DAY = 86_400_000
STOP = -0.015; RISK = 0.005; GROSS = RISK / 0.015

def log(*a): print(*a, flush=True)

def load_binance():
    con = sqlite3.connect(DB)
    frames = {}
    for c in COINS:
        d = pd.read_sql_query("select timestamp ts, open, close from candles where source='binance_um' and market=? and interval='5m' and timestamp>=? and timestamp<? order by timestamp",
                              con, params=(c, START - DAY, END))
        frames[c] = d.set_index('ts')
    con.close()
    op = pd.DataFrame({c: frames[c]['open'] for c in COINS}); cl = pd.DataFrame({c: frames[c]['close'] for c in COINS})
    return op.sort_index(), cl.sort_index()

def load_kucoin():
    op = {}; cl = {}
    for c in ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT']:
        k = pd.DataFrame(json.load(open(os.path.join(SP, 'kucoin', f'kc_{c}_5m.json'))), columns=['ts', 'open', 'high', 'low', 'close', 'volume']).set_index('ts').sort_index()
        op[c] = k['open']; cl[c] = k['close']
    return pd.DataFrame(op).sort_index(), pd.DataFrame(cl).sort_index()

def simulate(op, cl, slip_bp, coins, label, stop=True):
    idx = op.index.values
    pos = {t: i for i, t in enumerate(idx)}
    rows = []
    for t in idx:
        if t % H2 != 0 or t % DAY == 0: continue                    # 2h anchors, skip 00:00
        if t < START: continue
        i = pos[t]
        if i < 4 or i + 25 >= len(idx): continue
        if idx[i - 4] != t - 4 * BAR or idx[i + 1] != t + BAR or idx[i + 25] != t + 25 * BAR: continue
        c_now = cl.iloc[i][coins]; c_prev = cl.iloc[i - 4][coins]
        if c_now.isna().any() or c_prev.isna().any(): continue
        r20 = c_now / c_prev - 1
        lo, hi = r20.idxmin(), r20.idxmax()
        if lo == hi: continue
        e = op.iloc[i + 1]; x_end = op.iloc[i + 25]
        if pd.isna(e[lo]) or pd.isna(e[hi]) or pd.isna(x_end[lo]) or pd.isna(x_end[hi]): continue
        el = e[lo] * (1 + slip_bp / 1e4); es = e[hi] * (1 - slip_bp / 1e4)
        # path: completed bars i+2 .. i+25 (closes), stop check
        exit_k = i + 25; how = 'time'
        if stop:
            for k in range(i + 2, i + 25):
                cs = cl.iloc[k]
                pr = 0.5 * (cs[lo] / el - 1) + 0.5 * (1 - cs[hi] / es)
                if pr <= STOP:
                    exit_k = k + 1; how = 'stop'; break
        xo = op.iloc[exit_k]
        xl = xo[lo] * (1 - slip_bp / 1e4); xs = xo[hi] * (1 + slip_bp / 1e4)
        long_r = xl / el - 1; short_r = 1 - xs / es
        gross_pair = 0.5 * (op.iloc[exit_k][lo] / e[lo] - 1) + 0.5 * (1 - op.iloc[exit_k][hi] / e[hi])
        net_pair = 0.5 * long_r + 0.5 * short_r - 2 * COMM_BP / 1e4            # commission both legs, both sides, on gross allocation
        # unstopped diagnostic
        unst = 0.5 * (x_end[lo] * (1 - slip_bp / 1e4) / el - 1) + 0.5 * (1 - x_end[hi] * (1 + slip_bp / 1e4) / es) - 2 * COMM_BP / 1e4
        # MAE/MFE on completed closes
        path = [0.5 * (cl.iloc[k][lo] / el - 1) + 0.5 * (1 - cl.iloc[k][hi] / es) for k in range(i + 2, exit_k)]
        rows.append(dict(t=t, long=lo, short=hi, gross_bp=gross_pair * 1e4, net_bp=net_pair * 1e4, unst_bp=unst * 1e4, long_bp=long_r * 1e4, short_bp=short_r * 1e4,
                         how=how, mae_bp=min(path) * 1e4 if path else 0, mfe_bp=max(path) * 1e4 if path else 0, bars=exit_k - i - 1))
    return pd.DataFrame(rows)

def boot_days(t, col='net_bp', n=10000, seed=7):
    g = t.groupby(t['t'] // DAY)[col].apply(list); blocks = [np.array(x) for x in g.values]
    rng = np.random.default_rng(seed); out = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(blocks), len(blocks)); out[i] = np.concatenate([blocks[j] for j in pick]).mean()
    return np.percentile(out, 2.5), np.percentile(out, 97.5)

def account(t):
    """registered sizing: gross pair notional 33.3% of equity, net_bp applies to that notional."""
    eq = START_EQ; eqs = []; peak = eq; mdd = 0; worst_day = 0; day = None; day_start = eq
    for _, r in t.iterrows():
        d = r['t'] // DAY
        if d != day: day = d; day_start = eq
        eq *= 1 + GROSS * r['net_bp'] / 1e4
        peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak); worst_day = min(worst_day, eq / day_start - 1); eqs.append(eq)
    yrs = (t['t'].iloc[-1] - t['t'].iloc[0]) / DAY / 365.25
    under = pd.Series(eqs) < pd.Series(eqs).cummax(); longest = 0; cur = 0
    for u in under: cur = cur + 1 if u else 0; longest = max(longest, cur)
    return dict(final=eq, cagr=(eq / START_EQ) ** (1 / yrs) - 1, mdd=mdd, worst_day=worst_day, longest=longest)

def main():
    t0 = time.time(); op, cl = load_binance(); log(f'binance bars {len(op):,}')
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 19 results (Binance perps 5m, 7 coins, 20-min rank, 2h hold, 2h anchors ex 00:00, comm {COMM_BP}bp/side, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    base = simulate(op, cl, 2.0, COINS, 'base'); stress = simulate(op, cl, 5.0, COINS, 'stress')
    P(f"trades {len(base)} ({pd.Timestamp(base['t'].iloc[0], unit='ms').date()} .. {pd.Timestamp(base['t'].iloc[-1], unit='ms').date()}) | stops {(base['how']=='stop').sum()}")
    lo, hi = boot_days(base)
    P(f"BASE 10bp/leg: gross mean {base['gross_bp'].mean():+.2f} bp (median {base['gross_bp'].median():+.2f}) | net mean {base['net_bp'].mean():+.2f} bp, day-block 95% CI [{lo:+.2f}, {hi:+.2f}] | win {(base['net_bp']>0).mean()*100:.0f}% | payoff {base.loc[base['net_bp']>0,'net_bp'].mean()/-base.loc[base['net_bp']<=0,'net_bp'].mean():.2f} | long leg {base['long_bp'].mean():+.2f} bp, short leg {base['short_bp'].mean():+.2f} bp | unstopped diagnostic net {base['unst_bp'].mean():+.2f} bp")
    P(f"STRESS 16bp/leg: net mean {stress['net_bp'].mean():+.2f} bp")
    ex10 = base['net_bp'].drop(base['net_bp'].nlargest(10).index)
    P(f"ex-10-best net {ex10.mean():+.2f} bp | MAE mean {base['mae_bp'].mean():+.1f} / worst {base['mae_bp'].min():+.0f} bp | MFE mean {base['mfe_bp'].mean():+.1f} bp | worst trade {base['net_bp'].min():+.0f} bp")
    recent = base[base['t'] >= int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6)]
    P(f"2024-26 base net {recent['net_bp'].mean():+.2f} bp on {len(recent)}")
    P('year | trades | gross bp | base net bp | stress net bp')
    for y, g in base.assign(y=pd.to_datetime(base['t'], unit='ms').dt.year).groupby('y'):
        s = stress[stress['t'].isin(g['t'])]
        P(f"{y} | {len(g)} | {g['gross_bp'].mean():+.2f} | {g['net_bp'].mean():+.2f} | {s['net_bp'].mean():+.2f}")
    acc = account(base)
    P(f"account-sized (33% gross per trade): final {acc['final']:,.0f} | CAGR {acc['cagr']*100:+.2f}% | maxDD {acc['mdd']*100:.2f}% | worst day {acc['worst_day']*100:+.2f}% | longest DD {acc['longest']} trades")
    P('long-coin frequency: ' + ', '.join(f'{k.replace("USDT","")} {v}' for k, v in base['long'].value_counts().items()))
    P('short-coin frequency: ' + ', '.join(f'{k.replace("USDT","")} {v}' for k, v in base['short'].value_counts().items()))
    P('net by long coin: ' + ', '.join(f'{k.replace("USDT","")} {v:+.1f}' for k, v in base.groupby('long')['net_bp'].mean().items()))
    P('net by short coin: ' + ', '.join(f'{k.replace("USDT","")} {v:+.1f}' for k, v in base.groupby('short')['net_bp'].mean().items()))
    # KuCoin transfer
    try:
        kop, kcl = load_kucoin(); kc = simulate(kop, kcl, 2.0, list(kop.columns), 'kucoin')
        P(f"KuCoin 4-coin transfer: {len(kc)} trades, gross {kc['gross_bp'].mean():+.2f} bp, net {kc['net_bp'].mean():+.2f} bp")
        kc_net = kc['net_bp'].mean()
    except Exception as e: P(f'KuCoin transfer failed: {e}'); kc_net = -1
    # overlap with raid trades
    try:
        r = pd.read_csv(os.path.join(SP, 'global2_strict.csv'))
        wins = r[r['netR'] > 0]
        occupied = [(t, t + H2 + BAR) for t in base['t']]
        occ = np.array(occupied)
        def blocked(ts):
            j = np.searchsorted(occ[:, 0], ts) - 1
            return j >= 0 and occ[j, 0] <= ts < occ[j, 1]
        nb = sum(blocked(x) for x in wins['entryTs'])
        P(f"overlap: {nb} of {len(wins)} winning raid trades ({nb/len(wins)*100:.0f}%) would have found both slots taken")
    except Exception as e: P(f'overlap failed: {e}')
    kills = {
        'base net CI excludes 0 and positive': not (base['net_bp'].mean() > 0 and lo > 0),
        '2024-26 base net <= 0': recent['net_bp'].mean() <= 0,
        'stress net <= 0': stress['net_bp'].mean() <= 0,
        'kucoin net <= 0': kc_net <= 0,
        'ex-10-best <= 0': ex10.mean() <= 0,
        'maxDD > 5% or day < -3%': acc['mdd'] > 0.05 or acc['worst_day'] < -0.03,
        'median gross < 10bp': base['gross_bp'].median() < 10,
        'CAGR < 3%': acc['cagr'] < 0.03,
    }
    P('## KILLS: ' + ', '.join(f'{k}: {"KILL" if v else "ok"}' for k, v in kills.items()))
    P(f'verdict: {"KILLED" if any(kills.values()) else "SURVIVES"} ({sum(kills.values())} of 8 kills triggered)')
    base.to_csv(os.path.join(HERE, 'hunt19_trades.csv'), index=False)
    P(f'run time {time.time()-t0:.0f}s')
    open(os.path.join(OUT, 'hunt19-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
