import os, numpy as np, pandas as pd, hunt25 as h
frames = {}
for c in h.COINS:
    px, idx, met = h.load_coin(c); i = idx.drop_duplicates('ts'); m = met.drop_duplicates('ts'); df = h.prep(c, px, i, m)
    frames[c] = df[(df['ts'] >= h.T0) & (df['ts'] < h.L1)].reset_index(drop=True)
lines = ['# Hunt 25 full learn table (all nine declared cells, no new rules). Base 3+2bp; stress 3+5bp.', '',
         '| q | d | trades | dates | 2022 n / R | 2023 n / R | base R/trade | stress R/trade | weekly CI | total R | ex-best-day R | best day |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
for q in h.QS:
    for d in h.DS:
        t, _ = h.book(frames, q, d, 2.0); t = t[t['t_in'] >= h.L0]
        s, _ = h.book(frames, q, d, 5.0); s = s[s['t_in'] >= h.L0]
        if len(t) == 0: lines.append(f'| {q}% | {d}bp | 0 |'); continue
        yr = pd.to_datetime(t['t_in'], unit='ms').dt.year
        g22 = t[yr == 2022]; g23 = t[yr == 2023]
        day = t.assign(dd=pd.to_datetime(t['t_in'], unit='ms').dt.date).groupby('dd')['r'].sum()
        lo, hi = h.boot_weeks(t) if len(t) > 10 else (np.nan, np.nan)
        mark = ' **' if (q, d) in ((1, 10), (2, 10)) else ''
        lines.append(f"| {q}%{mark} | {d}bp | {len(t)} | {day.shape[0]} | {len(g22)} / {g22['r'].mean():+.3f} | {len(g23)} / {g23['r'].mean():+.3f} | {t['r'].mean():+.3f} | {s['r'].mean():+.3f} | [{lo:+.3f}, {hi:+.3f}] | {t['r'].sum():+.1f} | {t['r'].sum()-day.max():+.1f} | {day.idxmax()} {day.max():+.1f}R |")
        print(lines[-1], flush=True)
open(os.path.join(h.OUT, 'hunt25-full-table.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
