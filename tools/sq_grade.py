"""Grade SQ Amendment-B cells. usage: python sq_grade.py learn | exam"""
import os, sys, json, numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sq_lib as S
from sq_run import OUT

MONTHS = {'learn': 48.0, 'exam': 33.0}
YEARS = {'learn': [2020, 2021, 2022, 2023], 'exam': [2024, 2025, 2026]}
KEY = ['lev', 'conf', 'entry', 'stop', 'side']


def tclust(d, day):
    d = np.asarray(d); n = len(d)
    if n < 3: return np.nan
    m = d.mean(); g = pd.Series(d - m).groupby(np.asarray(day)).sum().values
    se = np.sqrt((g ** 2).sum()) / n
    return m / se if se > 0 else np.nan


def maxdd(r):
    c = np.cumsum(r); return float((np.maximum.accumulate(np.r_[0, c])[1:] - c).max()) if len(r) else 0.0


def grade(A, era):
    real = A[~A.fake].copy(); fake = A[A.fake]
    real['day'] = real.t // 86400000; real['yr'] = pd.to_datetime(real.t, unit='ms', utc=True).dt.year
    fk = fake.groupby(KEY).R.mean()
    out = []
    for key, g in real.groupby(KEY):
        g = g.sort_values('t'); r = g.R.values
        rs = np.sort(r)
        row = dict(zip(KEY, key), n=len(g), tpm=len(g) / MONTHS[era], R=r.mean(), Rmo=r.sum() / MONTHS[era],
                   ex5=rs[:-5].mean() if len(rs) > 5 else np.nan, worst=maxdd(r),
                   ctrl=g.R_ctrl.mean(), opp=g.R_opp.mean(), fakeR=fk.get(key, np.nan),
                   t=tclust(r - g.R_ctrl.values, g.day.values))
        for y in YEARS[era]: row[f'y{y}'] = g[g.yr == y].R.mean() if (g.yr == y).any() else np.nan
        for c in S.COINS: row[c[:3]] = g[g.coin == c].R.mean() if (g.coin == c).any() else np.nan
        out.append(row)
    G = pd.DataFrame(out)
    G['p'] = norm.sf(G.t.fillna(-9))
    m = 480; order = np.argsort(G.p.values); pv = G.p.values[order]
    qv = np.minimum.accumulate((pv * m / np.arange(1, len(pv) + 1))[::-1])[::-1]
    q = np.empty(len(pv)); q[order] = np.minimum(qv, 1); G['q'] = q
    yc = [f'y{y}' for y in YEARS[era]]
    G['all_years_pos'] = (G[yc] > 0).all(axis=1)
    G['pass'] = (((G.t >= 3.75) | (G.q < 0.05)) & G.all_years_pos & (G.ex5 > 0) & (G.R > G.opp)
                 & (G.R > G.fakeR.fillna(-9)) & (G.n >= 40))
    return G.sort_values('t', ascending=False)


if __name__ == '__main__':
    era = sys.argv[1]
    A = pd.read_parquet(os.path.join(OUT, f'sq2_trades_{era}.parquet'))
    G = grade(A, era)
    G.to_csv(os.path.join(OUT, f'sq_grid_{era}.csv'), index=False)
    s = A[(~A.fake) & (A.lev == 'S1SWING') & (A.conf == 'D') & (A.entry == 'FVG') & (A.stop == 'S1')]
    print(f'SANITY #1 cell (S1, both sides): n={len(s)}  R={s.R.mean():+.3f}  R_nomin={s.R_nomin.mean():+.3f}  '
          + '  '.join(f'{c[:3]} {s[s.coin == c].R.mean():+.3f}' for c in S.COINS))
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    cols = KEY + ['n', 'tpm', 'R', 'ex5', 'Rmo', 'worst', 'ctrl', 'opp', 'fakeR', 't', 'q'] + [c for c in G.columns if c.startswith('y')] + ['BTC', 'ETH', 'XRP', 'pass']
    print(G[cols].head(25).round(3).to_string(index=False))
    print('cells with trades:', len(G), ' passing:', int(G['pass'].sum()))
