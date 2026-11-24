"""ORB (Y03 15min/cme) + ORG (X11 E16) audit, pre-declared 2026-10-14 before any run. Same tests as sb_audit.py.
ORB LUCK: same days, same stop distance, 2R, entry at a random 15m bar open between 09:45 and 15:45 (C1 random side, C2 real side), walk to 16:00.
ORB WIGGLE: opening range 10 / 20 min (live 15), bar 10 / 20 min (live 15), target 1.5R / 2.5R (live 2R).
ORG LUCK: same days, same stop and target (mid-gap) distance, entry at a random 1-min open 09:31-09:59 (C1 random side, C2 real side), walk to 16:00.
ORG WIGGLE: gap 30-200 / 50-200 pt (live 40-200), last FVG candle by 09:54 / 10:04 (live 09:59), stop 0 / 2 ticks beyond (live 1).
PASS: luck = real beats >= 95% of 1000 runs both eras; wiggle = all neighbours positive both eras, none below half the live unseen number."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
rng = np.random.default_rng(20261014); TICK = 0.25
d1 = L.load_cme_ict('NQ', '1min'); ROLLS = L.ict_roll_dates(); BY = dict(tuple(d1.groupby('date')))
def era(day): return 'learn' if day.year <= 2023 else 'unseen'
def walk_net(h, l, c, i, side, E, stop, tgt):
    xp, _, how = L.walk(h, l, c, np.zeros(len(h)), i, side, E, stop, tgt)
    return side * (xp - E) / abs(E - stop) - L.cost_R('cme', 'NQ', E, abs(E - stop), slips=1 if how == 'tgt' else 2)
def summary(lbl, T):
    print(f"  {lbl:24s} " + ' | '.join(f"{e} n={sum(1 for t in T if era(t['day']) == e)} {np.mean([t['net'] for t in T if era(t['day']) == e]):+.3f}R" for e in ('learn', 'unseen')), flush=True)
def luck(T, make):
    for e in ('learn', 'unseen'):
        S = [t for t in T if era(t['day']) == e]; real = np.mean([t['net'] for t in S]); c1, c2 = [], []
        for _ in range(1000):
            c1.append(np.mean([make(t, rng.choice([-1, 1])) for t in S])); c2.append(np.mean([make(t, t['side']) for t in S]))
        c1, c2 = np.array(c1), np.array(c2)
        print(f"  {e}: n={len(S)} REAL {real:+.3f}R | C1 random side avg {c1.mean():+.3f} best {c1.max():+.3f} -> beats {np.mean(real > c1):.1%} | C2 real side avg {c2.mean():+.3f} best {c2.max():+.3f} -> beats {np.mean(real > c2):.1%}", flush=True)

# ---------------- ORB (y03_orb.run, NQ, cme) ----------------
def orb(orm=15, tf='15min', tR=2.0):
    out = []
    for dt, x in BY.items():
        if dt in ROLLS or pd.Timestamp(dt).dayofweek > 4: continue
        x = x[(x.hr >= 9.5) & (x.hr < 16)].set_index('ts')
        if len(x) < 300: continue
        orr = x[x.hr < 9.5 + orm / 60]; hi, lo = orr.h.max(), orr.l.min(); rest = x[x.hr >= 9.5 + orm / 60]
        b = rest.resample(tf, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
        o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
        k = next((i for i in range(len(c)) if c[i] > hi or c[i] < lo), None)
        if k is None or k + 1 >= len(c): continue
        side = 1 if c[k] > hi else -1; E = c[k]; stop = lo if side == 1 else hi; risk = abs(E - stop)
        if risk <= 0: continue
        out.append(dict(day=dt, side=side, risk=risk, tR=tR, o=o, h=h, l=l, c=c, net=walk_net(h, l, c, k + 1, side, E, E - side * risk, E + side * tR * risk)))
    return out
def orb_rand(t, side):
    i = int(rng.integers(0, len(t['o']) - 1)); E = t['o'][i]
    return walk_net(t['h'], t['l'], t['c'], i, side, E, E - side * t['risk'], E + side * t['tR'] * t['risk'])
print('ORB'); live = orb(); luck(live, orb_rand); summary('LIVE', live)
for lbl, kw in (('range 10 min', dict(orm=10)), ('range 20 min', dict(orm=20)), ('bars 10 min', dict(tf='10min')), ('bars 20 min', dict(tf='20min')), ('target 1.5R', dict(tR=1.5)), ('target 2.5R', dict(tR=2.5))):
    summary(lbl, orb(**kw))

# ---------------- ORG (c_x11_dump.run, NQ E16) ----------------
def org(lo_gap=40, last=959, stick=1):
    out = []; days = sorted(BY); prev_S = prev_date = last_rth = None
    for dt in days:
        x = BY[dt]; hm = (x.ts.dt.hour * 100 + x.ts.dt.minute).values
        s_bar = x[hm == 1614]; o_bar = x[hm == 930]
        if prev_S is not None and len(o_bar) and prev_date == last_rth and prev_date not in ROLLS and dt not in ROLLS and pd.Timestamp(dt).dayofweek < 5:
            S = prev_S; O = o_bar.o.iloc[0]; gap = O - S
            if lo_gap <= abs(gap) <= 200:
                mid = S + gap / 2; side = -1 if gap > 0 else 1
                r = x[(hm >= 930) & (hm < 1600)]; rhm = (r.ts.dt.hour * 100 + r.ts.dt.minute).values
                o, h, l, c = r.o.values, r.h.values, r.l.values, r.c.values; fv = None
                for k in range(2, len(r)):
                    if rhm[k] > last: break
                    if (side == -1 and l[k - 2] > h[k]) or (side == 1 and h[k - 2] < l[k]): fv = k; break
                if fv is not None:
                    stop = h[fv - 1] + stick * TICK if side == -1 else l[fv - 1] - stick * TICK
                    E = h[fv] + TICK if side == -1 else l[fv] - TICK; i0 = None
                    for q in range(fv + 1, len(r)):
                        if rhm[q] >= 1000: break
                        if (side == -1 and l[q] <= mid) or (side == 1 and h[q] >= mid): break
                        if (side == -1 and h[q] >= E) or (side == 1 and l[q] <= E): i0 = q; break
                    risk = (stop - E) * -side
                    if i0 is not None and risk > 0 and (mid - E) * side > 0:
                        out.append(dict(day=dt, side=side, risk=risk, D=abs(mid - E), o=o, h=h, l=l, c=c, rhm=rhm, net=walk_net(h, l, c, i0, side, E, stop, mid)))
        if len(s_bar): prev_S = s_bar.c.iloc[0]; prev_date = dt
        if len(o_bar): last_rth = dt
    return out
def org_rand(t, side):
    idx = np.where((t['rhm'] >= 931) & (t['rhm'] <= 959))[0]; i = int(rng.choice(idx)); E = t['o'][i]
    return walk_net(t['h'], t['l'], t['c'], i, side, E, E - side * t['risk'], E + side * t['D'])
print('ORG'); live = org(); luck(live, org_rand); summary('LIVE', live)
for lbl, kw in (('gap 30-200', dict(lo_gap=30)), ('gap 50-200', dict(lo_gap=50)), ('FVG by 09:54', dict(last=954)), ('FVG by 10:04', dict(last=1004)), ('stop 0 ticks', dict(stick=0)), ('stop 2 ticks', dict(stick=2))):
    summary(lbl, org(**kw))
