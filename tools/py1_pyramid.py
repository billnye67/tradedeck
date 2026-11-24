"""PY1: pyramiding overlay on #1, honest fills. Spec: specs/PY1-pyramid-honest.md. One run, no knobs."""
import os, sys, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from s1_evalsim import sim
T = pd.read_csv(os.path.join(ROOT, 'specs', 'coinflip', 's1_trades_nopeek.csv')).sort_values('entryTs').reset_index(drop=True)
EXAM = 1704067200000; EXAM_END = pd.Timestamp('2026-08-29', tz='UTC').value // 10**6

def replay(T, pyramid, max_open=2):
    open_ = []; keep = []
    for r in T.itertuples():
        open_ = [o for o in open_ if o.exitTs > r.entryTs]
        if len(open_) >= max_open: continue
        same = [o for o in open_ if o.market == r.market]
        if same:
            if not pyramid or len(same) >= 2: continue
            f = same[0]
            if f.side != r.side: continue
            short = f.side < 0 or f.side == 0  # side column: 1 = long (per csv), else short
            in_profit = (r.entry < f.entry) if short else (r.entry > f.entry)
            if not in_profit: continue
        open_.append(r); keep.append(r)
    K = pd.DataFrame(keep); K['day'] = pd.to_datetime(K.exitTs, unit='ms').dt.floor('D'); return K

def maxdd(K):
    c = K.sort_values('exitTs').netR.cumsum(); return float((c.cummax() - c).max())

def era(K, lo, hi): return K[(K.entryTs >= lo) & (K.entryTs < hi)]

print('side values:', sorted(T.side.unique()))
for lbl, pyr in (('BASELINE (live rule)', False), ('PYRAMID', True)):
    K = replay(T, pyr)
    adds = 0
    for name, lo, hi in (('learn 2020-23', 0, EXAM), ('exam 2024-26', EXAM, EXAM_END)):
        e = era(K, lo, hi); months = (hi - max(lo, T.entryTs.min())) / (30.44 * 86400000)
        print(f"{lbl:22s} {name:14s} n {len(e):5d} avg {e.netR.mean():+.4f}R total {e.netR.sum():+7.1f}R  R/mo {e.netR.sum()/months:+.2f}  maxDD {maxdd(e):.1f}R")
    for name, k in (('all', K), ('exam only', K[K.entryTs >= EXAM])):
        p, b, m = sim(k, start=53079, tgt=55000, floor=46500)
        print(f"{lbl:22s} evalsim {name:10s} PASS {p:.0%} BLOW {b:.0%} median {m} mo")
    if pyr:
        # count adds: trades that entered while same market already open
        open_=[]; n=0
        for r in K.itertuples():
            open_=[o for o in open_ if o.exitTs > r.entryTs]
            if any(o.market==r.market for o in open_): n+=1
            open_.append(r)
        print(f"PYRAMID adds taken: {n} of {len(K)}; add avg R: ", end='')
        open_=[]; adds=[]
        for r in K.itertuples():
            open_=[o for o in open_ if o.exitTs > r.entryTs]
            if any(o.market==r.market for o in open_): adds.append(r.netR)
            open_.append(r)
        a = pd.Series(adds); print(f"{a.mean():+.4f}R (n={len(a)}), exam adds avg {pd.Series([x for x,r in zip(adds,[r for r in K.itertuples() if True]) ]).mean() if False else 'see below'}")
