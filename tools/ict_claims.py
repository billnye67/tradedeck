"""ICT frequency claims C1, C1c, C1d, C2, C4, C6, C7, C8 (specs/ict-cards/00-master-queue.md section 1).
Statistics, not trades. Each claim is reported with a base rate / control; split 2012-2019 / 2020-2026 (FX) to show stability."""
import os, sqlite3, numpy as np, pandas as pd
DB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/history.sqlite'
DBN = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/databento/'
c = sqlite3.connect(DB); out = []
def P(s=''): out.append(s); print(s, flush=True)
def pct(x): return f'{100*np.mean(x):.0f}%' if len(x) else 'n/a'

def fx(m):
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='fxcm' and market='{m}' and interval='15m' order by t", c)
    d.index = pd.to_datetime(d.t, unit='ms', utc=True).dt.tz_convert('America/New_York'); return d.drop(columns='t')
def cme(m):
    d = pd.read_parquet(DBN + f'cme_{m}.parquet', columns=['ts_event', 'open', 'high', 'low', 'close'])
    d.index = pd.to_datetime(d.ts_event).dt.tz_convert('America/New_York'); d = d.drop(columns='ts_event')
    d.columns = ['o', 'h', 'l', 'c']; return d.resample('15min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()

FX = {m: fx(m) for m in ('EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD')}
IDX = {m: cme(m) for m in ('NQ', 'ES')}

# ---------- weekly claims (FX week Sun 17:00 -> Fri 17:00 NY; shift +7h so it starts Monday 00:00) ----------
def weekly(d):
    s = d.copy(); s.index = s.index.tz_localize(None) + pd.Timedelta(hours=7)
    s['wk'] = s.index.to_period('W-SUN'); s['dow'] = s.index.dayofweek; s['nyhour'] = (s.index - pd.Timedelta(hours=7)).hour
    rows = []
    for wk, g in s.groupby('wk'):
        if len(g) < 300 or g.dow.max() < 4: continue
        lo_i, hi_i = g.l.idxmin(), g.h.idxmax()
        o, cl = g.o.iloc[0], g.c.iloc[-1]
        mw = g[g.dow <= 2]   # Mon-Wed
        rows.append(dict(wk=wk, year=g.index[0].year, bull=cl > o, lo_dow=g.loc[lo_i, 'dow'] if not isinstance(g.loc[lo_i, 'dow'], pd.Series) else g.loc[lo_i, 'dow'].iloc[0],
                         hi_dow=g.loc[hi_i, 'dow'] if not isinstance(g.loc[hi_i, 'dow'], pd.Series) else g.loc[hi_i, 'dow'].iloc[0],
                         lo_ts=lo_i, hi_ts=hi_i, mw_down=mw.c.iloc[-1] < o if len(mw) else False,
                         lo_nyh=(lo_i - pd.Timedelta(hours=7)).hour))
    w = pd.DataFrame(rows); w['prev_bull'] = w.bull.shift(1); return w

P('# ICT frequency claims — measured (FX 2012-2026 15m FXCM; NQ/ES 2020-2026 CME)')
P('\n## C1: "Tuesday makes the low of a bullish week ~70%"  (and C1c "low by Wednesday ~70%", C1d "Thursday NY low in bearish weeks 65-70%")')
for m, d in FX.items():
    w = weekly(d); bull = w[w.bull]; bear = w[~w.bull]
    lo_by_wed = lambda x: ((x.lo_dow < 2) | ((x.lo_dow == 2) & (x.lo_ts.dt.hour < 19))).values   # before Wed 12:00 NY
    pb = w[w.prev_bull == True]
    P(f'{m}: weeks {len(w)} | Tuesday low in BULLISH weeks (hindsight) {pct(bull.lo_dow == 1)} vs all weeks {pct(w.lo_dow == 1)} | '
      f'ex-ante (last week bullish) {pct(pb.lo_dow == 1)} | low before Wed noon: bullish {pct(lo_by_wed(bull))} vs all {pct(lo_by_wed(w))} | '
      f'Tue HIGH of bearish week {pct(bear.hi_dow == 1)} | bearish+Mon-Wed down: Thu 07-11 NY low {pct(((bear.lo_dow == 3) & bear.lo_nyh.between(7, 10))[bear.mw_down])}')
    P(f'      by day, low of bullish week: ' + ' '.join(f"{'MTWTF'[k]}:{pct(bull.lo_dow == k)}" for k in range(5)) +
      f' | 2012-19 Tue {pct(bull[bull.year < 2020].lo_dow == 1)} / 2020-26 Tue {pct(bull[bull.year >= 2020].lo_dow == 1)}')

# ---------- daily claims ----------
def days(d, rth=False):
    s = d.copy(); s['date'] = s.index.date; s['hr'] = s.index.hour + s.index.minute / 60
    if rth: s = s[(s.hr >= 9.5) & (s.hr < 16)]
    s = s[s.index.dayofweek < 5]
    g = s.groupby('date')
    D = pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last(), 'n': g.size(),
                      'hi_hr': g.apply(lambda x: x.hr.iloc[x.h.values.argmax()]), 'lo_hr': g.apply(lambda x: x.hr.iloc[x.l.values.argmin()])})
    return D[D.n >= (24 if rth else 90)]

P('\n## C2: "London (02:00-04:00 NY) makes the HIGH of down days / LOW of up days ~70%"   (NY midnight-midnight days)')
P('## C8: "London close 10:00-12:00 NY makes the opposite end of the day" (on days London made one extreme)')
for m, d in FX.items():
    D = days(d); up = D[D.c > D.o]; dn = D[D.c < D.o]
    inwin = lambda hr, a, b: (hr >= a) & (hr < b)
    hist = [pct(np.r_[inwin(dn.hi_hr, a, a + 2).values, inwin(up.lo_hr, a, a + 2).values]) for a in range(0, 24, 2)]
    lon = D[inwin(D.hi_hr, 2, 5) | inwin(D.lo_hr, 2, 5)]
    opp = np.where(inwin(lon.hi_hr, 2, 5), lon.lo_hr, lon.hi_hr)
    P(f'{m}: days {len(D)} | 02-04 NY: high of down days {pct(inwin(dn.hi_hr, 2, 4))}, low of up days {pct(inwin(up.lo_hr, 2, 4))} | 02-05: {pct(inwin(dn.hi_hr, 2, 5))} / {pct(inwin(up.lo_hr, 2, 5))}')
    P(f'      every 2h window (share of the "against" extreme): ' + ' '.join(f'{a:02d}h:{h}' for a, h in zip(range(0, 24, 2), hist)))
    P(f'      C8: London made an extreme on {pct(inwin(D.hi_hr, 2, 5) | inwin(D.lo_hr, 2, 5))} of days; then opposite end in 10-12 NY {pct(inwin(opp, 10, 12))}, '
      f'in 12-14 {pct(inwin(opp, 12, 14))}, 14-16 {pct(inwin(opp, 14, 16))}, 16-24 {pct(inwin(opp, 16, 24))}, 05-10 {pct(inwin(opp, 5, 10))}')

P('\n## C6: "Index day high or low forms 09:30-10:30; then 15:00-16:00 makes the other one"  (RTH 09:30-16:00)')
for m, d in IDX.items():
    D = days(d, rth=True)
    first = (D.hi_hr < 10.5) | (D.lo_hr < 10.5); last = (D.hi_hr >= 15) | (D.lo_hr >= 15)
    f_lo = D.lo_hr < 10.5; f_hi = D.hi_hr < 10.5
    opp_last = np.where(f_lo & ~f_hi, D.hi_hr >= 15, np.where(f_hi & ~f_lo, D.lo_hr >= 15, False))
    P(f'{m}: days {len(D)} | high or low in 09:30-10:30 {pct(first)} (control: in 15:00-16:00 {pct(last)}; any middle hour 11-12 {pct((D.hi_hr.between(11, 11.99)) | (D.lo_hr.between(11, 11.99)))}) | '
      f'given first hour made exactly one extreme, other one in 15:00-16:00 {pct(opp_last[(f_lo ^ f_hi).values])} vs any day {pct(((D.hi_hr >= 15) | (D.lo_hr >= 15)))}')

P('\n## C7: "Inside day -> large-range day" (next day range / average of the last 5 days)')
for m, d in list(FX.items()) + list(IDX.items()):
    D = days(d, rth=m in IDX); r = D.h - D.l; adr = r.rolling(5).mean().shift(1)
    inside = (D.h < D.h.shift(1)) & (D.l > D.l.shift(1)); nxt = (r.shift(-1) / adr)
    nr7 = r == r.rolling(7).min()
    P(f'{m}: next-day range/ADR5 after inside day {nxt[inside].mean():.2f} (n={inside.sum()}) vs all days {nxt.mean():.2f} | after NR7 {nxt[nr7].mean():.2f} | '
      f'share of next days > 1.5x ADR5: inside {pct(nxt[inside].dropna() > 1.5)} vs all {pct(nxt.dropna() > 1.5)}')

P('\n## C4 TGIF: "Thu 13:00 NY -> Fri close retraces 20-30% of the weekly range" on directional weeks')
for m, d in list(FX.items()) + list(IDX.items()):
    s = d.copy(); s.index = s.index.tz_localize(None) + pd.Timedelta(hours=7); s['wk'] = s.index.to_period('W-SUN')
    res = []
    for wk, g in s.groupby('wk'):
        thu = g.index[0].normalize() + pd.Timedelta(days=3, hours=20)       # Thu 13:00 NY in shifted time
        pre, post = g[g.index < thu], g[g.index >= thu]
        if len(pre) < 200 or len(post) < 20: continue
        H, L = pre.h.max(), pre.l.min(); R = H - L; cl = pre.c.iloc[-1]
        res.append(dict(R=R, pos=(cl - L) / R if R else .5, up=(cl - L) / R > 2 / 3, dn=(cl - L) / R < 1 / 3,
                        rt_up=(H - post.l.min()) / R, rt_dn=(post.h.max() - L) / R))
    w = pd.DataFrame(res); w['big'] = w.R > w.R.rolling(4).mean().shift(1)
    dirw = w[w.big & (w.up | w.dn)]; rt = np.where(dirw.up, dirw.rt_up, dirw.rt_dn)
    other = w[~(w.big & (w.up | w.dn))]; rt_o = np.where(other.pos >= .5, other.rt_up, other.rt_dn)
    P(f'{m}: directional weeks {len(dirw)} | Thu13->Fri retrace >=20% {pct(rt >= .2)}, >=30% {pct(rt >= .3)}, >=40% {pct(rt >= .4)} | '
      f'median {np.median(rt)*100:.0f}% | control (other weeks, from the nearer extreme) >=20% {pct(rt_o >= .2)}, >=30% {pct(rt_o >= .3)}')
open(r'C:\Users\you\Desktop\TradeDeck\specs\ict-claims-results.md', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
