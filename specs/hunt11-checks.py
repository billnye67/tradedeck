"""Hunt 11 follow-ups: buy-and-hold benchmark at matched exposure, Sharpe, KuCoin feed agreement."""
import json, os, numpy as np, pandas as pd
import hunt11 as h

df = h.load(); dd = h.daily(df); act = h.sleeves(dd)
pub = h.run(df, dd, act, 0.25, 2.0, label='pub')
r = pub['rows']
avgE_all = r['E'].mean()                      # average exposure over ALL days (incl. flat)
c = dd['close'].values; days = dd['day'].values
i0 = np.searchsorted(days, r['day'].iloc[0]); i1 = np.searchsorted(days, r['day'].iloc[-1])
btc_ret = c[i1] / c[i0] - 1
# constant-exposure benchmark with the same financing, no trading costs beyond one entry
eq = h.START_EQ; peak = eq; mdd = 0
for i in range(i0, i1):
    ret = avgE_all * (c[i+1] / c[i] - 1)
    eq = eq * (1 + ret) - avgE_all * eq * h.FIN_BP / 1e4
    peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
# strategy daily returns -> Sharpe
dr = (r['gross'] - r['cost'] - r['fin']) / r['eq'].shift(1).fillna(h.START_EQ)
sharpe = dr.mean() / dr.std(ddof=1) * np.sqrt(365)
by = r.assign(y=pd.to_datetime(r['day'], unit='ms').dt.year).groupby('y')
print(f'strategy: final {pub["eq"]:,.0f} ({pub["eq"]/h.START_EQ-1:+.1%}), maxDD {pub["maxdd"]:.1%}, daily Sharpe (net after fin, x sqrt365) {sharpe:+.2f}')
print(f'BTC itself {c[i0]:,.0f} -> {c[i1]:,.0f} = {btc_ret:+.0%} over the same days')
print(f'benchmark: constant {avgE_all:.3f}x long BTC (strategy average exposure incl. flat days) with same 5bp financing: final {eq:,.0f} ({eq/h.START_EQ-1:+.1%}), maxDD {mdd:.1%}')
print('strategy exposure by year (avg incl flat days):', {int(y): round(g['E'].mean(), 3) for y, g in by})
# KuCoin feed agreement over the overlap
try:
    kc = json.load(open(os.path.join(h.SP, 'kucoin', 'kc_BTCUSDT_5m.json')))
    k = pd.DataFrame(kc, columns=['ts', 'open', 'high', 'low', 'close', 'volume']).sort_values('ts')
    kd = h.daily(k)
    m = pd.merge(dd[['day', 'close']], kd[['day', 'close']], on='day', suffixes=('_bn', '_kc'))
    diff_bp = ((m['close_kc'] / m['close_bn'] - 1) * 1e4)
    print(f'KuCoin overlap {len(m)} days {pd.Timestamp(m["day"].iloc[0], unit="ms").date()}..{pd.Timestamp(m["day"].iloc[-1], unit="ms").date()}: daily close difference mean {diff_bp.mean():+.1f}bp, abs mean {diff_bp.abs().mean():.1f}bp, max {diff_bp.abs().max():.1f}bp')
    # signal agreement: recompute sleeves on a Binance series whose overlap closes are swapped for KuCoin closes
    dd2 = dd.copy(); idx = dd2['day'].isin(m['day']); dd2.loc[idx, 'close'] = dd2.loc[idx, 'day'].map(dict(zip(m['day'], m['close_kc']))).values
    act2 = h.sleeves(dd2)
    sel = dd['day'].isin(m['day']).values
    agree = (act[sel] == act2[sel]).mean()
    print(f'sleeve on/off decisions identical on {agree:.1%} of sleeve-days over the overlap')
except Exception as e:
    print('KuCoin check failed:', e)
