"""R15: intraweek high/low raid into a daily PD array (specs/ict-rebuild/R15-intraweek-raid.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def arrays(D, t, side, look=40):
    """live daily discount (side=1) / premium (side=-1) zones at the start of day index t."""
    o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values; z = []
    for k in range(max(2, t - look), t):
        if side == 1 and l[k] > h[k - 2]:                              # bullish FVG [h[k-2], l[k]]
            lo, hi = h[k - 2], l[k]
            if not (c[k + 1:t] < lo).any() if k + 1 < t else True: z.append((lo, hi))
        if side == -1 and h[k] < l[k - 2]:
            lo, hi = h[k], l[k - 2]
            if not (c[k + 1:t] > hi).any() if k + 1 < t else True: z.append((lo, hi))
    for (k, s, op, far) in L.live_obs(D, L.daily_ob_list(D), t, look):
        if s == side: z.append((min(op, far), max(op, far)))
    return z

def run(m, d):
    pip = L.pip_of(m); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)]
    D = L.daily_fx(d); Dpos = {k: i for i, k in enumerate(D.index)}
    by = dict(tuple(d.groupby('date'))); keys = sorted(by); rows = []; rng = np.random.default_rng(15)
    for i, dt in enumerate(keys):
        dow = pd.Timestamp(dt).dayofweek
        if dow < 1 or dow > 3 or dt not in Dpos: continue
        wk = [k for k in keys[max(0, i - 4):i] if pd.Timestamp(k).isocalendar()[1] == pd.Timestamp(dt).isocalendar()[1]]
        if not wk: continue
        prior = pd.concat([by[k] for k in wk]); x = by[dt].reset_index(drop=True)
        h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
        end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else len(h)
        t = Dpos[dt]
        for side in (1, -1):
            old = prior.l.min() if side == 1 else prior.h.max()
            zs = arrays(D, t, side)
            if not any(lo - 5 * pip <= old <= hi + 5 * pip for lo, hi in zs): continue
            q = next((j for j in range(end) if hr[j] < 12 and ((l[j] <= old) if side == 1 else (h[j] >= old))), None)
            if q is None: continue
            E = old; risk = 40 * pip
            for T in (30, 60):
                for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                    xp, _, how = L.walk(h[:end], l[:end], c[:end], hr[:end], q, s, E, E - s * risk, E + s * T * pip)
                    gR = s * (xp - E) / risk; cr = L.cost_R('broker_fx', m, E, risk)
                    rows.append(dict(model='R15', cell=f'T{T}/{kind}', m=m, date=dt, gross=gR, net=gR - cr))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = pd.concat([run(m, L.load_fx(m, '5m')) for m in L.FX_PAIRS]); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R15-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R15 intraweek raid']
    for cell, gg in T.groupby('cell'):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(gg.net.values):.0f} | " + ' '.join(f'{k[:3]}{v:+.2f}' for k, v in gg.groupby('m').net.mean().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
