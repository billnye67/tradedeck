"""R05S: R05 with the swing-after-level filter (specs/ict-rebuild/R05S-swing-after-level.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r05_2022 as R
_orig = R.day_setups
def day_sal(x, *a, **k):
    t = _orig(x, *a, **k)
    if t is None or t['st_t'] < t['sweep']: return None
    return t
R.day_setups = day_sal
if __name__ == '__main__':
    R.EXAM = os.environ.get('EXAM') == '1'; T = []
    for m in ('NQ', 'ES'):
        d = L.load_cme_ict(m)
        T.append(R.run(m, d, ('cme', 'broker'), 0.25, 20 if m == 'NQ' else 5, {'AM': (8.5, 11, 16)}, L.CME_LEARN_END, L.ict_roll_dates())); print(m, flush=True)
    T = pd.concat(T); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R05S-results' + ('-exam' if R.EXAM else '-learn') + '.md'); lines = ['# R05S swing-after-level']
    for (m, cell), g in T.groupby(['m', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(g) > 5 else np.nan:+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk {g.risk.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
