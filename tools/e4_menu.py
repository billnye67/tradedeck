"""Engine 4 step 1 (spec specs/E4-trade-manager.md): every frozen entry (scoreboard pick + all #1 entries, both eras) x 60 exit shapes
(stop 0.5/1/2x, target 2R/3R/5R/trail, hold 1/6/12/24/72h) simulated on the Binance 5m path with Broker costs.
Output: %LOCALAPPDATA%/TradeDeck/Data/lab/e4/menu.pkl (entries table + R[n,60] + exit time[n,60])."""
import os, sys, glob, json, pickle, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import v2_build as V2, sb_streams as SS
LAB = SS.LAB; OUT = os.path.join(LAB, 'e4'); os.makedirs(OUT, exist_ok=True)
STOPS = (0.5, 1.0, 2.0); TGTS = (2.0, 3.0, 5.0, 0.0); HOLDS = (12, 72, 144, 288, 864); FEE = 0.0005; SWAP = 0.0005
SHAPES = [(s, t, h) for s in STOPS for t in TGTS for h in HOLDS]
@njit(cache=True)
def sim(o, h, l, c, t_ms, j0, side, e, R, tgt, cost_rt):
    """one (entry, stop, target): returns R and exit index for each hold cap. tgt 0 = trailing 1R after +1R."""
    n = len(o); outR = np.full(5, np.nan); outX = np.full(5, -1)
    caps = np.array([12, 72, 144, 288, 864]); st = e - side * R; best = e; hit = -1; xp = 0.0
    last = min(j0 + 864, n)
    for m in range(j0, last):
        if m > j0 and ((side == 1 and o[m] <= st) or (side == -1 and o[m] >= st)): hit = m; xp = o[m]; break
        if (side == 1 and l[m] <= st) or (side == -1 and h[m] >= st): hit = m; xp = st; break
        if tgt > 0:
            tg = e + side * tgt * R
            if (side == 1 and h[m] >= tg) or (side == -1 and l[m] <= tg): hit = m; xp = tg; break
        else:
            if side == 1: best = max(best, h[m])
            else: best = min(best, l[m])
            if side * (best - e) >= R:
                ns = best - side * R
                if (side == 1 and ns > st) or (side == -1 and ns < st): st = ns
    for k in range(5):
        cap_end = j0 + caps[k] - 1
        if cap_end >= n: continue
        if hit >= 0 and hit <= cap_end: x = xp; xe = hit
        else: x = c[cap_end]; xe = cap_end
        day = 86400000; off = 1800000; nsw = (t_ms[xe] + 300000 - off) // day - (t_ms[j0] - off) // day
        r = side * (x - e) / R - (cost_rt + SWAP * nsw) * e / R
        outR[k] = max(r, -1.5); outX[k] = xe
    return outR, outX
def one_coin(args):
    coin, E = args
    K = V2.read_csvs(sorted(glob.glob(os.path.join(V2.FLOW, 'klines5m', f'{coin}-5m-*.csv'))), ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tbq']).astype(float).drop_duplicates('t').sort_values('t')
    t = K.t.values.astype(np.int64); o, h, l, c = K.o.values, K.h.values, K.l.values, K.c.values
    TP = pd.read_parquet(os.path.join(LAB, 'v3b', f'{coin}.parquet'), columns=['bar', 'tick_p']); TP['ms'] = pd.to_datetime(TP.bar, utc=True).astype('int64') // 10**6
    tickp = pd.Series(TP.tick_p.values, index=TP.ms.values); extra = 2 * max(SS.HALF[coin[:-4]] - 2, 0) / 1e4
    Rm = np.full((len(E), 60), np.nan); Xm = np.zeros((len(E), 60), np.int64)
    for i, r in enumerate(E.itertuples()):
        ms = int(r.dt.value // 10**6); bar = ms - ms % 300000
        if r.kind == 's1': j0 = np.searchsorted(t, bar) + 1; e = r.entry; d0 = abs(r.entry - r.stop)
        else: j0 = np.searchsorted(t, ms); e = o[j0] if j0 < len(o) else np.nan; d0 = r.rd * e
        if j0 >= len(o) or not d0 > 0: continue
        tk = tickp.get(bar, np.nan); tk = 0.0 if not tk == tk else tk * e; floor = max(0.0015 * e, 5 * tk)
        for si, sm in enumerate(STOPS):
            R = max(sm * d0, floor) if sm < 1 else sm * d0
            for ti, tg in enumerate(TGTS):
                rr, xx = sim(o, h, l, c, t, j0, r.side, e, R, tg, 2 * FEE + extra)
                k0 = (si * 4 + ti) * 5; Rm[i, k0:k0 + 5] = rr; Xm[i, k0:k0 + 5] = np.where(xx >= 0, t[np.maximum(xx, 0)] + 300000, 0)
    return E.index.values, Rm, Xm
if __name__ == '__main__':
    J = json.load(open(os.path.join(SS.OUT, 'pick.json'))); rows = []
    for era in ('learn', 'exam'):
        S = pickle.load(open(os.path.join(SS.OUT, f'streams_{era}.pkl'), 'rb'))
        for f, v in [('s1', {'variant': 'all'})] + [(f, v) for f, v in J['pick'].items() if v]:
            x = S[(f, v['variant'], era)].copy(); x['kind'] = f; x['era'] = era; x['tag'] = f'{f}:{v["variant"]}'; rows.append(x)
    A = pd.concat(rows, ignore_index=True)
    A['dt'] = pd.to_datetime(A.dt, utc=True)
    for col in ('entry', 'stop'):
        if col not in A: A[col] = np.nan
    # one row per unique entry (an #1 trade can be in both 'all' and the filtered list): keep membership tags
    A['key'] = A.coin + '|' + A.dt.astype(str) + '|' + A.side.astype(str) + '|' + A.kind
    tags = A.groupby('key').tag.apply(lambda s: ','.join(sorted(set(s))))
    E = A.drop_duplicates('key').set_index('key'); E['tags'] = tags; E = E.reset_index()
    print('entries', len(E), E.groupby(['era', 'kind']).size().to_dict(), flush=True)
    Rm = np.full((len(E), 60), np.nan); Xm = np.zeros((len(E), 60), np.int64)
    with ProcessPoolExecutor(6) as ex:
        for idx, r, x in ex.map(one_coin, [(c, g) for c, g in E.groupby('coin')]): Rm[idx] = r; Xm[idx] = x; print('coin done', flush=True)
    pickle.dump(dict(E=E, R=Rm, X=Xm, shapes=SHAPES), open(os.path.join(OUT, 'menu.pkl'), 'wb'))
    fx = SHAPES.index((1.0, 3.0, 144)); print('saved; fixed rule mean R by era/kind:', pd.Series(Rm[:, fx]).groupby([E.era, E.kind]).mean().round(3).to_dict(), flush=True)
