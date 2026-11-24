"""G1 busy-level failed retest (spec: specs/G1-busy-level-retest.md). Env: G1_LB (5), G1_TGT (3), G1_EXAM=1 to open the exam."""
import os, sqlite3, numpy as np, pandas as pd
LB = int(os.environ.get('G1_LB', 5)); TGT = float(os.environ.get('G1_TGT', 3)); EXAM = os.environ.get('G1_EXAM') == '1'
DB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\history.sqlite'); EXAM_END = pd.Timestamp('2026-08-29', tz='UTC'); SPLIT = pd.Timestamp('2024-01-01', tz='UTC')
MK = [('metals', 'XAUUSD', 1, 1)] + [('fxcm', m, 0.4, 0.5) for m in ('EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY')] + \
     [('binance_um', m, 3, 2) for m in ('BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT')] + [('databento', m, 3, 1) for m in ('NQ', 'ES')] + [('databento', 'CL', 1, 1)]
HOLD = 192; SWAP = 0.0005; rng = np.random.default_rng(7)
c = sqlite3.connect(DB)
def outcome(o, h, l, cl, t, j, side, e, stop, cost):
    R = abs(e - stop); tgt = e + side * TGT * R; x, xe = cl[min(j + HOLD, len(o)) - 1], min(j + HOLD, len(o)) - 1
    for m in range(j, min(j + HOLD, len(o))):
        if m > j and (o[m] <= stop if side == 1 else o[m] >= stop): x, xe = o[m], m; break
        if (l[m] <= stop if side == 1 else h[m] >= stop): x, xe = stop, m; break
        if (h[m] >= tgt if side == 1 else l[m] <= tgt): x, xe = tgt, m; break
    off = pd.Timedelta(minutes=30); nsw = ((t[xe] + pd.Timedelta(minutes=15) - off).floor('D') - (t[j] - off).floor('D')).days
    return side * (x - e) / R - (cost + SWAP * nsw) * e / R
rows = []
for src, m, fee, slip in MK:
    d = pd.read_sql("select timestamp t, open o, high h, low l, close c from candles where source=? and market=? and interval='15m' order by t", c, params=(src, m))
    if d.empty: print(m, 'no data'); continue
    d.index = pd.to_datetime(d.t, unit='ms', utc=True); d = d[d.index < EXAM_END]
    D = d.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    tr = np.maximum(D.h - D.l, np.maximum((D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs())); atr = tr.rolling(14).mean().shift(1)
    o, h, l, cl = d.o.values, d.h.values, d.l.values, d.c.values; t = d.index; cost = 2 * (fee + slip) / 1e4
    day = t.floor('D'); starts = np.searchsorted(t, D.index); ends = np.searchsorted(t, D.index + pd.Timedelta(days=1))
    for k, dd in enumerate(D.index):
        A = atr.iloc[k]
        if not A > 0: continue
        lo = np.searchsorted(t, dd - pd.Timedelta(days=LB)); s, e_ = starts[k], ends[k]
        hist = cl[lo:s]
        if len(hist) < 20 * LB: continue
        w = 0.1 * A; b = np.floor(hist / w).astype(np.int64); vals, cnt = np.unique(b, return_counts=True); L = (vals[cnt.argmax()] + 0.5) * w
        broke = {1: False, -1: False}; done = {1: False, -1: False}
        for i in range(s, e_):
            # side -1 = short after break below; side 1 = long after break above
            for side in (-1, 1):
                if done[side]: continue
                if not broke[side]:
                    if (cl[i] < L - 0.5 * A) if side == -1 else (cl[i] > L + 0.5 * A): broke[side] = True
                    continue
                touch = (h[i] >= L - 0.05 * A and cl[i] < L) if side == -1 else (l[i] <= L + 0.05 * A and cl[i] > L)
                if touch and i + 1 < len(o):
                    en = o[i + 1]; stop = L - side * 0.25 * A
                    if (en >= stop) if side == -1 else (en <= stop): done[side] = True; continue
                    r = outcome(o, h, l, cl, t, i + 1, side, en, stop, cost); rv = outcome(o, h, l, cl, t, i + 1, -side, en, en + side * abs(en - stop), cost)
                    rows.append(dict(mkt=m, t=t[i + 1], side=side, R=r, Rflip=rv)); done[side] = True
T = pd.DataFrame(rows); T['era'] = np.where(T.t < SPLIT, 'learn', 'exam')
def rep(x, name):
    if len(x) < 5: print(f'{name:8s} {len(x)} trades'); return
    s = np.sort(x.R.values); both = np.c_[x.R.values, x.Rflip.values]
    rnd = np.array([both[np.arange(len(x)), rng.integers(0, 2, len(x))].mean() for _ in range(1000)])
    yrs = ' '.join(f'{y}:{v:+.0f}' for y, v in x.groupby(x.t.dt.year).R.sum().items())
    print(f'{name:8s} {len(x):5d} trades  {x.R.mean():+.3f}R/trade  ex-top5 {s[:-5].mean():+.3f}  win {100*(x.R>0).mean():.0f}%  '
          f'short {x[x.side==-1].R.mean():+.3f} long {x[x.side==1].R.mean():+.3f}  beats random {100*(rnd < x.R.mean()).mean():.0f}%  | {yrs}')
for era in (('learn', 'exam') if EXAM else ('learn',)):
    print(f'=== {era}  LB={LB}d target={TGT}R')
    X = T[T.era == era]
    for mm, x in X.groupby('mkt', sort=False): rep(x, mm)
    rep(X, 'ALL')
T.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab', f'g1_LB{LB}_T{TGT}.csv'), index=False)
