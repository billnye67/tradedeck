"""Hunt 16 - BTC high-vol 21:00-23:00 UTC clock-window long (Perplexity candidate 34), frozen 2026-10-04."""
import sqlite3, os, json, time
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
HERE = os.path.dirname(os.path.abspath(__file__))
START_EQ = 50_000.0
H = 3600_000; DAY = 86_400_000
END_MS = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)

def log(*a): print(*a, flush=True)

def load_binance():
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select timestamp ts, open, high, low, close from candles where source='binance_um' and market='BTCUSDT' and interval='5m' and timestamp < ? order by timestamp", con, params=(END_MS,))
    con.close(); return df

def load_kucoin():
    k = json.load(open(os.path.join(SP, 'kucoin', 'kc_BTCUSDT_5m.json')))
    return pd.DataFrame(k, columns=['ts', 'open', 'high', 'low', 'close', 'volume']).sort_values('ts').reset_index(drop=True)

def build(df):
    """hourly closes (complete hours only), daily vol state at 00:00, and the 21:00/23:00 opens per day."""
    ts = df['ts'].values
    hour = (ts // H) * H
    g = df.groupby(hour)
    hc = pd.DataFrame({'h': g['ts'].first().index.values, 'close': g['close'].last().values, 'n': g['ts'].size().values})
    hc = hc[hc['n'] >= 10]                       # an hour needs most of its 5m bars
    hc = hc.set_index('h')['close']
    full = pd.Series(hc.values, index=hc.index)
    # log returns on consecutive hours only
    idx = full.index.values
    lr = pd.Series(np.log(full.values[1:] / full.values[:-1]), index=idx[1:])
    gap = (idx[1:] - idx[:-1]) != H
    lr[gap] = np.nan
    days = np.arange((ts[0] // DAY + 1) * DAY, ts[-1] // DAY * DAY, DAY)
    rows = []
    byts = dict(zip(ts, df['open'].values))
    lo5 = dict(zip(ts, df['low'].values)); hi5 = dict(zip(ts, df['high'].values))
    for d in days:
        # 720 hourly returns ending at 00:00 of day d: returns stamped (d-720h, d]
        w = lr[(lr.index > d - 720 * H) & (lr.index <= d)]
        vol = float(w.std(ddof=1)) if len(w) == 720 and not w.isna().any() else np.nan
        e_in = byts.get(d + 21 * H); e_out = byts.get(d + 23 * H)
        path = [(k, lo5.get(k), hi5.get(k)) for k in range(d + 21 * H, d + 23 * H, 300_000)]
        rows.append(dict(day=d, vol=vol, p_in=e_in, p_out=e_out, lo=min([p[1] for p in path if p[1] is not None], default=np.nan), hi=max([p[2] for p in path if p[2] is not None], default=np.nan), nbars=sum(1 for p in path if p[1] is not None)))
    D = pd.DataFrame(rows)
    D['bench'] = D['vol'].shift(1).rolling(365, min_periods=365).median()
    D['hv'] = D['vol'] > D['bench']
    D['ok'] = D['vol'].notna() & D['bench'].notna() & D['p_in'].notna() & D['p_out'].notna() & (D['nbars'] == 24)
    return D

def stats(D, name, cost_bp, P, gated=True, sized=False):
    t = D[D['ok'] & (D['hv'] if gated else True)].copy()
    if len(t) == 0: P(f'### {name}: no trades'); return None
    t['gross_bp'] = (t['p_out'] / t['p_in'] - 1) * 1e4
    t['net_bp'] = t['gross_bp'] - cost_bp
    t['mae_bp'] = (t['lo'] / t['p_in'] - 1) * 1e4; t['mfe_bp'] = (t['hi'] / t['p_in'] - 1) * 1e4
    if sized:
        risk_dist = 3.0 * t['vol'] * np.sqrt(2)
        t['size'] = np.minimum(1.0, 0.0025 / risk_dist)
    else: t['size'] = 1.0
    t['ret'] = t['size'] * t['net_bp'] / 1e4
    eq = START_EQ * (1 + t['ret']).cumprod(); peak = eq.cummax(); dd = (eq / peak - 1)
    # longest drawdown in days
    under = (dd < 0).values; longest = 0; cur = 0
    for u in under:
        cur = cur + 1 if u else 0; longest = max(longest, cur)
    yrs = (t['day'].iloc[-1] - t['day'].iloc[0]) / DAY / 365.25
    cagr = (eq.iloc[-1] / START_EQ) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sharpe = t['ret'].mean() / t['ret'].std(ddof=1) * np.sqrt(len(t) / yrs) if t['ret'].std(ddof=1) > 0 else np.nan
    rng = np.random.default_rng(7); m = np.array([t['gross_bp'].values[rng.integers(0, len(t), len(t))].mean() for _ in range(10000)])
    roll = t['net_bp'].rolling(30).sum()
    ex10 = t['net_bp'].drop(t['net_bp'].nlargest(10).index)
    P(f'### {name}')
    P(f"eligible/traded days {len(t)} of {int(D['ok'].sum())} usable | gross mean {t['gross_bp'].mean():+.2f} bp (median {t['gross_bp'].median():+.2f}, 95% CI [{np.percentile(m,2.5):+.2f}, {np.percentile(m,97.5):+.2f}]) | net {t['net_bp'].mean():+.2f} bp at {cost_bp}bp | win {(t['gross_bp']>0).mean()*100:.0f}% | payoff {t.loc[t['gross_bp']>0,'gross_bp'].mean()/-t.loc[t['gross_bp']<=0,'gross_bp'].mean():.2f}")
    P(f"compounded {START_EQ:,.0f} -> {eq.iloc[-1]:,.0f} | CAGR {cagr*100:+.1f}% | Sharpe {sharpe:+.2f} | maxDD {dd.min()*100:.1f}% | longest DD {longest} trades | worst trade {t['net_bp'].min():+.0f} bp | worst 30-trade block {roll.min():+.0f} bp | MAE mean {t['mae_bp'].mean():+.1f} / worst {t['mae_bp'].min():+.0f} bp | MFE mean {t['mfe_bp'].mean():+.1f} bp | net excl. 10 best {ex10.mean():+.2f} bp")
    y = t.assign(y=pd.to_datetime(t['day'], unit='ms').dt.year).groupby('y')
    P('year | trades | gross bp | net bp | year net %')
    for yy, g in y: P(f"{yy} | {len(g)} | {g['gross_bp'].mean():+.2f} | {g['net_bp'].mean():+.2f} | {((1+g['ret']).prod()-1)*100:+.2f}")
    return t

def main():
    t0 = time.time(); df = load_binance(); D = build(df)
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 16 results (Binance BTC perp 5m, long 21:00 -> 23:00 UTC, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P(f"days with full warm-up and both execution bars: {int(D['ok'].sum())}; first eligible {pd.Timestamp(D.loc[D['ok'],'day'].iloc[0], unit='ms').date()}; high-vol share {D.loc[D['ok'],'hv'].mean()*100:.0f}%")
    P()
    g10 = stats(D, 'HIGH-VOL days, 1x, base 10bp round trip (primary)', 10.0, P); P()
    g16 = stats(D, 'HIGH-VOL days, 1x, stress 16bp', 16.0, P); P()
    u10 = stats(D, 'UNCONDITIONAL (every day), 1x, 10bp — reference', 10.0, P, gated=False); P()
    lv = stats(D, 'LOW-VOL days only, 1x, 10bp — reference', 10.0, P, gated=False) if False else None
    acc = stats(D, 'HIGH-VOL, account-sized (3 x 2h-vol = 0.25% equity, cap 1x), 10bp', 10.0, P, sized=True); P()
    # gross by hour-of-day for context (unconditional), 2020-2026
    # kills
    gross = g10['gross_bp'].mean(); recent = g10[g10['day'] >= int(pd.Timestamp('2024-01-01', tz='UTC').value // 10**6)]
    kills = {
        'gross <= 10bp': gross <= 10, 'net <= 0 at 10bp': g10['net_bp'].mean() <= 0, 'net <= 0 at 16bp': g16['net_bp'].mean() <= 0,
        'negative net 2024-26': recent['net_bp'].sum() <= 0 if len(recent) else True,
    }
    P(f'## KILLS: ' + ', '.join(f'{k}: {"KILL" if v else "ok"}' for k, v in kills.items()))
    # kucoin transfer (reported regardless, labelled)
    try:
        kc = load_kucoin(); Dk = build(kc)
        # warm-up on KuCoin alone is impossible (needs 365+30 days); borrow the Binance vol state by day
        state = D.set_index('day')[['vol', 'bench', 'hv']]
        Dk = Dk.drop(columns=['vol', 'bench', 'hv']).merge(state, left_on='day', right_index=True, how='left')
        Dk['ok'] = Dk['p_in'].notna() & Dk['p_out'].notna() & Dk['hv'].notna() & (Dk['nbars'] == 24)
        tk = Dk[Dk['ok'] & Dk['hv']]; gb = (tk['p_out'] / tk['p_in'] - 1) * 1e4
        P(f"## KuCoin transfer (Dec-2024 -> Aug-2026, Binance vol state, KuCoin prices): {len(tk)} trades, gross {gb.mean():+.2f} bp, net {gb.mean()-10:+.2f} bp at 10bp")
    except Exception as e: P(f'KuCoin transfer failed: {e}')
    # overlap with raid trades
    try:
        r = pd.read_csv(os.path.join(SP, 'global2_strict.csv'))
        r['day'] = (r['entryTs'] // DAY) * DAY
        at21 = r[((r['entryTs'] % DAY) >= 21 * H) & ((r['entryTs'] % DAY) < 23 * H)]
        P(f"overlap: {len(at21)} of {len(r)} raid trades (all coins) entered inside 21-23 UTC; {int(r['day'].isin(set(g10['day'])).sum())} raid entries fall on traded days")
    except Exception as e: P(f'overlap check failed: {e}')
    P(f'run time {time.time()-t0:.0f}s')
    g10.to_csv(os.path.join(HERE, 'hunt16_trades.csv'), index=False)
    open(os.path.join(OUT, 'hunt16-results.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
