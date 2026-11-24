"""X11: ICT opening range gap mid-gap model on NQ/ES 1-min (specs/X11-ict-org.md). EXAM=1 opens the exam once."""
import os, numpy as np, pandas as pd
D = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/databento/'
TICK = {'NQ': 0.25, 'ES': 0.25}; PV = {'NQ': 20.0, 'ES': 50.0}
out = []
def P(s=''): out.append(s); print(s, flush=True)

def load(m):
    d = pd.read_parquet(D + f'cme_{m}.parquet', columns=['ts_event', 'open', 'high', 'low', 'close'])
    d['ts'] = pd.to_datetime(d.ts_event).dt.tz_convert('America/New_York'); d = d.drop(columns='ts_event').set_index('ts').sort_index()
    return d

nq_raw, es_raw = load('NQ'), load('ES')
def days(d):
    d = d.copy(); d['date'] = d.index.date; d['hm'] = d.index.hour * 100 + d.index.minute
    return d
NQ, ES = days(nq_raw), days(es_raw)

def roll_dates(d):
    # maintenance-break jump: last bar <=16:59 vs first bar >=18:00 same date; big jump = contract roll
    last = d[d.hm <= 1659].groupby('date').close.last(); first = d[d.hm >= 1800].groupby('date').open.first()
    j = (first - last).abs() / last; med = j.median()
    return set(j[j > 5 * med].index)

def run(m, d, ratio=None):
    rolls = roll_dates(d); g = dict(tuple(d.groupby('date'))); dates = sorted(g)
    rows, claims = [], []
    prev_S = None; prev_date = None; last_rth = None
    for dt in dates:
        x = g[dt]
        s_bar = x[x.hm == 1614]
        o_bar = x[x.hm == 930]
        if prev_S is not None and len(o_bar) and prev_date == last_rth and prev_date not in rolls and dt not in rolls:
            S = prev_S; O = o_bar.open.iloc[0]; gap = O - S
            lo, hi = (40, 200) if m == 'NQ' else (40 / ratio[dt] if dt in ratio else np.nan, 200 / ratio[dt] if dt in ratio else np.nan)
            if lo == lo and lo <= abs(gap) <= hi:
                era = 'learn' if dt.year <= 2023 else 'exam'
                mid = S + gap / 2; mirror = O + gap / 2; side = -1 if gap > 0 else 1   # trade toward mid
                rth = x[(x.hm >= 930) & (x.hm < 1600)]; to10 = rth[rth.hm < 1000]
                def touched(df, lvl): return bool((df.low <= lvl).any()) if lvl < O else bool((df.high >= lvl).any())
                claims.append(dict(era=era, mid10=touched(to10, mid), mid16=touched(rth, mid), mir10=touched(to10, mirror), mir16=touched(rth, mirror)))
                # trade: first FVG in direction, c1 >= 09:30, c3 closes by 09:59 (c3 bar start <= 09:59)
                b = rth.reset_index(drop=False); h, l, c, hm = b.high.values, b.low.values, b.close.values, b.hm.values
                fv = None
                for k in range(2, len(b)):
                    if hm[k] > 959: break
                    if (side == -1 and l[k - 2] > h[k]) or (side == 1 and h[k - 2] < l[k]): fv = k; break
                if fv is not None:
                    tick = TICK[m]
                    stop = h[fv - 1] + tick if side == -1 else l[fv - 1] - tick
                    near = h[fv] + tick if side == -1 else l[fv] - tick
                    for cell in ('E16', 'E10', 'E11', 'M'):
                        if cell == 'M': E, i0 = c[fv], fv + 1
                        else:
                            E = near; i0 = None
                            for q in range(fv + 1, len(b)):
                                if hm[q] >= 1000: break
                                if (side == -1 and l[q] <= mid) or (side == 1 and h[q] >= mid): break   # target hit before fill
                                thr = TICK[m] * int(os.environ.get('FILLX', '0'))
                                if (side == -1 and h[q] >= near + thr) or (side == 1 and l[q] <= near - thr): i0 = q; break
                            if i0 is None: continue
                        risk = (stop - E) * -side
                        if risk <= 0 or (mid - E) * side <= 0: continue
                        if os.environ.get('MINSTOP') == '1' and risk < {'NQ': 4.0, 'ES': 1.0}[m]: continue
                        tend = {'E16': 1600, 'E10': 1000, 'E11': 1100, 'M': 1600}[cell]
                        res = None
                        for q in range(i0 if cell != 'M' else i0, len(b)):
                            if hm[q] >= tend: res = (b.open.values[q], 'time'); break
                            if (side == -1 and h[q] >= stop) or (side == 1 and l[q] <= stop): res = (stop, 'stop'); break
                            if (side == -1 and l[q] <= mid) or (side == 1 and h[q] >= mid): res = (mid, 'tgt'); break
                        if res is None: res = (c[-1], 'time')
                        xp = res[0]; gross = side * (xp - E) / risk
                        broker = (2 * 3 + 2 * 2) * E / 1e4 / risk
                        cme = (2 * tick + 5 / PV[m]) / risk
                        rows.append(dict(m=m, cell=cell, era=era, date=dt, gross=gross, broker=gross - broker, cme=gross - cme, risk_pts=risk, how=res[1]))
        if len(s_bar): prev_S = s_bar.close.iloc[0]; prev_date = dt
        if len(o_bar): last_rth = dt
    return pd.DataFrame(rows), pd.DataFrame(claims)

ratio = (NQ[NQ.hm == 930].groupby('date').open.first() / ES[ES.hm == 930].groupby('date').open.first()).to_dict()
res = {'NQ': run('NQ', NQ), 'ES': run('ES', ES, ratio)}
def dd(x): s = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0, s])[1:] - s).max()) if len(s) else 0
P('# X11 results — ICT opening range gap mid-gap model, NQ/ES 1-min')
for era in (['learn', 'exam'] if os.environ.get('EXAM') == '1' else ['learn']):
    P(f'\n## {era.upper()}')
    for m in ('NQ', 'ES'):
        T, C = res[m]; c = C[C.era == era]
        P(f'{m}: {len(c)} gap days 40-200 pts. Mid-gap touched by 10:00 {100*c.mid10.mean():.0f}% (his claim ~70%), by 16:00 {100*c.mid16.mean():.0f}% | '
          f'control mirror level (same distance, away) by 10:00 {100*c.mir10.mean():.0f}%, by 16:00 {100*c.mir16.mean():.0f}%')
        for cell in ('E16', 'E10', 'E11', 'M'):
            g = T[(T.era == era) & (T.cell == cell)].sort_values('date')
            if not len(g): continue
            yr = g.groupby(pd.to_datetime(g.date).dt.year).cme.sum()
            P(f'   {cell}: n={len(g):4d} win {100*(g.gross>0).mean():.0f}% gross {g.gross.mean():+.3f}R | NET broker {g.broker.mean():+.3f}R | NET cme {g.cme.mean():+.3f}R '
              f'ex-top5 {g.cme.sort_values().iloc[:-5].mean():+.3f}R | maxDD(cme) {dd(g.cme.values):.0f}R | median stop {g.risk_pts.median():.1f}pt | exits {g.how.value_counts().to_dict()} | cme by year ' + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()))
T=res['NQ'][0]; T[T.cell=='E16'].to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','specs','combo','org_nq_e16.csv'), index=False)
