"""EDGE LAB v1 (declared 2026-10-14 before any run). 110 Binance USDT-M perps, UTC daily bars, 2020-01 -> 2026-08.
Event on day t (known at t's close) -> enter at t's close, hold H = 1 or 3 days, exit at close. Measured for BOTH directions; the
direction is chosen on LEARN data only. Net = signed return - Broker costs (3bp+3bp fee, 2bp+2bp slip, 5bp per night held).
Luck correction: 24 ideas x 2 holds x 2 directions ~ 96 looks -> a survivor needs, on LEARN 2020-23: date-clustered t >= 3.5,
net >= +30bp per trade, >= 200 events over >= 20 coins, same sign in >= 3 of 4 years, AND it must beat the same-direction move on
ALL coin-days (so it is not just market drift). EXAM 2024-26 is opened (--exam) only for survivors, once.
Each event is clustered by date (all coins on one day count as one observation) so a single crash day cannot fake significance."""
import os, sys, numpy as np, pandas as pd
P = pd.read_parquet(os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\daily.parquet'))
P['date'] = pd.to_datetime(P.date).dt.tz_localize(None); P = P.sort_values(['coin', 'date']).reset_index(drop=True)
EXAM = '--exam' in sys.argv; FEE = 0.0010   # 6bp fee + 4bp slip round trip
g = P.groupby('coin', group_keys=False)
P['ret'] = g.c.pct_change()
for H in (1, 3): P[f'f{H}'] = g.c.shift(-H) / P.c - 1
P['hi20'] = g.h.transform(lambda s: s.shift(1).rolling(20).max()); P['lo20'] = g.l.transform(lambda s: s.shift(1).rolling(20).min())
P['hi55'] = g.h.transform(lambda s: s.shift(1).rolling(55).max()); P['lo55'] = g.l.transform(lambda s: s.shift(1).rolling(55).min())
P['ph'] = g.h.shift(1); P['pl'] = g.l.shift(1)
tr = np.maximum(P.h - P.l, np.maximum((P.h - g.c.shift(1)).abs(), (P.l - g.c.shift(1)).abs())); P['atr'] = tr.groupby(P.coin).transform(lambda s: s.shift(1).rolling(20).mean())
P['vmed'] = g.v.transform(lambda s: s.shift(1).rolling(30).median())
P['cl'] = (P.c - P.l) / (P.h - P.l).replace(0, np.nan)
P['r3'] = g.ret.transform(lambda s: s.rolling(3).max()); P['r3m'] = g.ret.transform(lambda s: s.rolling(3).min())
P['hi7'] = g.h.transform(lambda s: s.shift(1).rolling(7).max()); P['lo7'] = g.l.transform(lambda s: s.shift(1).rolling(7).min())
rng7 = P.hi7 / P.lo7 - 1; P['sq'] = rng7 <= 0.4 * rng7.groupby(P.coin).transform(lambda s: s.rolling(90).median())
P['dd30'] = P.c / g.h.transform(lambda s: s.rolling(30).max()) - 1; P['dd30p'] = g.dd30.shift(1)
P['age'] = g.cumcount()
P['m30'] = g.c.pct_change(30); P['m30rk'] = P.groupby('date').m30.rank(pct=True); P['ncoins'] = P.groupby('date').c.transform('size')
btc = P[P.coin == 'BTCUSDT'].set_index('date').ret; P['btc'] = P.date.map(btc)
alt = P.coin != 'BTCUSDT'
SIG = {
 'S01 close above 20d high': P.c > P.hi20, 'S02 close below 20d low': P.c < P.lo20,
 'S03 swept 20d high, closed back below': (P.h > P.hi20) & (P.c < P.hi20), 'S04 swept 20d low, closed back above': (P.l < P.lo20) & (P.c > P.lo20),
 'S05 day <= -10%': P.ret <= -0.10, 'S06 day >= +10%': P.ret >= 0.10,
 'S07 huge range, closed at low': (P.h - P.l >= 3 * P.atr) & (P.cl <= 0.2), 'S08 huge range, closed at high': (P.h - P.l >= 3 * P.atr) & (P.cl >= 0.8),
 'S09 3 days each <= -3%': P.r3 <= -0.03, 'S10 3 days each >= +3%': P.r3m >= 0.03,
 'S11 close above 55d high': P.c > P.hi55, 'S12 close below 55d low': P.c < P.lo55,
 'S13 volume 5x, up day': (P.v >= 5 * P.vmed) & (P.ret > 0), 'S14 volume 5x, down day': (P.v >= 5 * P.vmed) & (P.ret < 0),
 'S15 squeeze, close above 7d high': P.sq & (P.c > P.hi7), 'S16 squeeze, close below 7d low': P.sq & (P.c < P.lo7),
 'S17 first day 30% under 30d high': (P.dd30 <= -0.30) & (P.dd30p > -0.30),
 'S18 alts on BTC day <= -5%': alt & (P.btc <= -0.05), 'S19 alts on BTC day >= +5%': alt & (P.btc >= 0.05),
 'S20 30d momentum top 10% of coins': (P.m30rk >= 0.9) & (P.ncoins >= 20), 'S21 30d momentum bottom 10%': (P.m30rk <= 0.1) & (P.ncoins >= 20),
 'S22 first 30 days after listing': (P.age >= 1) & (P.age <= 30) & (P.date >= '2020-03-01'),
 'S23 swept yesterday high, closed below': (P.h > P.ph) & (P.c < P.ph) & (P.c < P.o), 'S24 swept yesterday low, closed above': (P.l < P.pl) & (P.c > P.pl) & (P.c > P.o),
}
P['yr'] = P.date.dt.year
era = (P.yr >= 2024) if EXAM else (P.yr <= 2023)
base = {H: P[era][f'f{H}'].mean() for H in (1, 3)}
def nw_t(b, H):
    """t-stat of the mean on a CALENDAR-daily series (days without events = no observation) with Newey-West lag H-1, so overlapping
    multi-day holds on consecutive days are not counted as independent (fix added before any exam look)."""
    if len(b) < 3: return 0.0
    full = b.reindex(pd.date_range(b.index.min(), b.index.max(), freq='D')); v = full.dropna(); x = full - v.mean()
    n = len(v); g0 = (x ** 2).sum() / n; s2 = g0
    for k in range(1, H):
        gk = (x * x.shift(k)).sum() / n; s2 += 2 * (1 - k / H) * gk
    return float(v.mean() / np.sqrt(max(s2, 1e-18) / n))
rows = []
for name, mask in SIG.items():
    E = P[era & mask.fillna(False)]
    for H in (1, 3):
        x = E.dropna(subset=[f'f{H}'])
        if len(x) < 30: continue
        for d, lab in ((1, 'LONG'), (-1, 'SHORT')):
            net = d * x[f'f{H}'] - FEE - 0.0005 * H
            byday = net.groupby(x.date).mean(); t = nw_t(byday, H)
            yrs = net.groupby(x.yr).mean(); same = int((np.sign(yrs) == np.sign(net.mean())).sum())
            excess = d * (x[f'f{H}'].mean() - base[H])
            ok = (t >= 3.5 and net.mean() >= 0.003 and len(x) >= 200 and x.coin.nunique() >= 20 and same >= min(3, len(yrs)) and excess > 0)
            rows.append(dict(signal=name, hold=f'{H}d', side=lab, n=len(x), coins=x.coin.nunique(), days=len(byday), net_bp=net.mean() * 1e4,
                             vs_all_bp=excess * 1e4, t=t, win=(net > 0).mean(), yrs=' '.join(f'{y % 100}:{v*1e4:+.0f}' for y, v in yrs.items()), PASS=ok))
R = pd.DataFrame(rows).sort_values('t', ascending=False)
pd.set_option('display.width', 250); pd.set_option('display.max_colwidth', 40); pd.set_option('display.max_rows', 200)
print(f"# EDGE LAB v1 {'EXAM 2024-26' if EXAM else 'LEARN 2020-23'} | all coin-days drift: 1d {base[1]*1e4:+.0f}bp, 3d {base[3]*1e4:+.0f}bp")
print(R.round({'net_bp': 0, 'vs_all_bp': 0, 't': 1, 'win': 2}).head(30).to_string(index=False))
print('\nSURVIVORS:', R[R.PASS][['signal', 'hold', 'side']].values.tolist())
R.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab', f"lab_v1_{'exam' if EXAM else 'learn'}.csv"), index=False)
