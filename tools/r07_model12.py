"""R07: Charter Model 12 (specs/ict-rebuild/R07-charter-model12.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'
KZ = ((2, 5), (7, 10))
def inkz(hr): return any(a <= hr < b for a, b in KZ)

def mwalk(h, l, c, q, end, side, E, pip):
    """20 stop / 20 target; at +10 stop -> E-10; at +15 stop -> E. Returns R (R = 20 pips)."""
    stop = E - side * 20 * pip; tgt = E + side * 20 * pip; best = 0.0
    for j in range(q, end):
        if (l[j] <= stop) if side == 1 else (h[j] >= stop): return side * (stop - E) / (20 * pip)
        if (h[j] >= tgt) if side == 1 else (l[j] <= tgt): return 1.0
        fav = (h[j] - E) if side == 1 else (E - l[j]); best = max(best, fav / pip)
        if best >= 15: stop = E if side == 1 else E
        elif best >= 10: stop = E - side * 10 * pip
    return side * (c[end - 1] - E) / (20 * pip)

def setups(x, pip):
    """Yield (side, fill_idx, E) — first per side per day."""
    h, l, c, o, hr = x.h.values, x.l.values, x.c.values, x.o.values, x.hr.values; n = len(h)
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else n
    out = []
    for side in (1, -1):
        found = None; k = 3
        while k < end - 3 and found is None:
            # impulse: close breaks the most recent confirmed swing high (bull) / low (bear)
            prev = [j for j in range(max(0, k - 48), k - 1) if (sh[j] if side == 1 else sl[j])]
            if not prev: k += 1; continue
            lvl = h[prev[-1]] if side == 1 else l[prev[-1]]
            if not ((c[k] > lvl) if side == 1 else (c[k] < lvl)): k += 1; continue
            seg = range(prev[-1], k + 1)
            ext = min(seg, key=lambda j: l[j]) if side == 1 else max(seg, key=lambda j: h[j])
            ob = next((j for j in range(ext, max(prev[-1], ext - 6) - 1, -1) if ((c[j] < o[j]) if side == 1 else (c[j] > o[j]))), None)
            if ob is None: k += 1; continue
            top, bot = max(o[ob], c[ob]), min(o[ob], c[ob]); mt = (top + bot) / 2
            imp_ext = h[k] if side == 1 else l[k]
            # retest of the OB inside a killzone, bodies not closing beyond the mean threshold
            r = None
            for j in range(k + 1, end):
                imp_ext = max(imp_ext, h[j]) if side == 1 else min(imp_ext, l[j])
                if (c[j] < mt) if side == 1 else (c[j] > mt): break
                if ((l[j] <= top) if side == 1 else (h[j] >= bot)) and inkz(hr[j]): r = j; break
            if r is None: k += 1; continue
            # expansion: break of the impulse extreme after the retest; first FVG inside, in discount (bull) / premium (bear)
            e = next((j for j in range(r + 1, end) if ((h[j] > imp_ext) if side == 1 else (l[j] < imp_ext))), None)
            if e is None: k = r + 1; continue
            lo_ = l[r:e + 1].min() if side == 1 else l[e]; hi_ = h[e] if side == 1 else h[r:e + 1].max(); mid = (lo_ + hi_) / 2
            fv = next((j for j in range(r + 2, e + 1) if ((h[j - 2] < l[j]) if side == 1 else (l[j - 2] > h[j]))), None)
            if fv is None: k = e + 1; continue
            gap_near = l[fv] if side == 1 else h[fv]
            if (gap_near > mid) if side == 1 else (gap_near < mid): k = e + 1; continue
            E = gap_near + side * 5 * pip                     # "IOFED level +5" (bull) / "-5" (bear)
            q = next((j for j in range(e + 1, end) if inkz(hr[j]) and ((l[j] <= E) if side == 1 else (h[j] >= E))), None)
            if q is None: k = e + 1; continue
            found = (side, q, E, end)
        if found: out.append(found)
    return out

def run(m, d):
    pip = L.pip_of(m); D = L.daily_fx(d); ob40 = L.daily_ob_bias(D, 40); rows = []; rng = np.random.default_rng(7)
    for dt, x in d.groupby('date'):
        if pd.Timestamp(dt).dayofweek > 4: continue
        x = x.reset_index(drop=True); h, l, c = x.h.values, x.l.values, x.c.values
        prevk = [k for k in ob40.index if k < dt][-1:] if len(ob40) else []
        b = ob40[prevk[0]] if prevk else 0
        for side, q, E, end in setups(x, pip):
            for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                g = mwalk(h, l, c, q, end, s, E, pip); cr = L.cost_R('broker_fx', m, E, 20 * pip)
                rows.append(dict(model='R07', cell=f'NB/{kind}', m=m, date=dt, gross=g, net=g - cr))
                if b == side: rows.append(dict(model='R07', cell=f'OB40/{kind}', m=m, date=dt, gross=g, net=g - cr))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)].reset_index(drop=True)
        T.append(run(m, d)); print(m, flush=True)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R07-results' + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R07 Charter Model 12 (ICT vs OPP vs COIN)', L.FX_LEARN_END, out, exam=EXAM)
    lines = ['\n## ex-top-5']
    for cell, g in T.groupby('cell'):
        g = g.sort_values('net'); lines.append(f'{cell}: n={len(g)} net {g.net.mean():+.3f}R ex-top5 {g.net.iloc[:-5].mean():+.3f}R')
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
