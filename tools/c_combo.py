"""Combine the 4 NQ-futures passes (specs/combo/README.md). Every trade = 1R risk. Daily R summed across strategies.
Selection rule (declared before running): pick the combination with the best learn-era (2020-23) return/maxDD; judge it on 2024-26."""
import os, itertools, numpy as np, pandas as pd
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs')
def load():
    org = pd.read_csv(S + '/combo/org_nq_e16.csv').rename(columns={'cme': 'r'})[['date', 'r']]
    sb = pd.read_csv(S + '/combo/sb_nq.csv').rename(columns={'net': 'r'})[['date', 'r']]
    p = pd.concat([pd.read_csv(S + '/ict-rebuild/R05P-results-learn.csv'), pd.read_csv(S + '/ict-rebuild/R05P-results-exam.csv')])
    p = p[(p.m == 'NQ') & (p.cell == 'S1/anyC/P1/cme/ICT')].drop_duplicates(['date']).rename(columns={'net': 'r'})[['date', 'r']]
    y = pd.concat([pd.read_csv(S + '/yt/Y03-results-learn.csv'), pd.read_csv(S + '/yt/Y03-results-exam.csv')])
    y = y[(y.m == 'NQ') & (y.cell == '15min/cme/ORB')].drop_duplicates(['date']).rename(columns={'net': 'r'})[['date', 'r']]
    out = {}
    for k, t in (('ORG', org), ('SB', sb), ('R05P', p), ('ORB', y)):
        t['date'] = pd.to_datetime(t.date); out[k] = t.groupby('date').r.sum()
    return out
def stats(s):
    if not len(s): return dict(n=0)
    yrs = (s.index.max() - s.index.min()).days / 365.25 or 1
    cum = s.cumsum().values; dd = float((np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max())
    mon = s.groupby(s.index.to_period('M')).sum()
    return dict(R_yr=s.sum() / yrs, maxDD=dd, ratio=(s.sum() / yrs) / dd if dd else np.nan, worst_day=s.min(), pos_months=(mon > 0).mean())
if __name__ == '__main__':
    D = load(); idx = sorted(set().union(*[set(v.index) for v in D.values()])); A = pd.DataFrame({k: v.reindex(idx).fillna(0) for k, v in D.items()}, index=pd.DatetimeIndex(idx))
    learn, exam = A[A.index.year <= 2023], A[A.index.year >= 2024]
    print('daily correlation (learn):'); print(learn[(learn != 0).any(axis=1)].corr().round(2))
    rows = []
    for n in range(1, 5):
        for combo in itertools.combinations(A.columns, n):
            L_ = stats(learn[list(combo)].sum(axis=1)); E_ = stats(exam[list(combo)].sum(axis=1))
            rows.append(dict(combo='+'.join(combo), **{f'L_{k}': v for k, v in L_.items()}, **{f'E_{k}': v for k, v in E_.items()}))
    T = pd.DataFrame(rows).sort_values('L_ratio', ascending=False)
    pd.set_option('display.width', 250)
    print(T.round(2).to_string(index=False))
    best = T.iloc[0]; print('\nPICK by learn ratio:', best.combo, '| exam R/yr', round(best.E_R_yr, 1), 'maxDD', round(best.E_maxDD, 1))
    T.round(3).to_csv(S + '/combo/combos.csv', index=False)
