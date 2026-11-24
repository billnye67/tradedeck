"""Harness self-test (run before any rebuild verdict). 1) unit cases for walk / walk_stop_entry, 2) NEGATIVE control: random
entries on real data with symmetric 1R stop / 1R target must give gross ~0R, 3) POSITIVE control: synthetic random walk with a
planted drift after a marker must be detected with the expected size, 4) cost_R sanity."""
import numpy as np, pandas as pd, sys, ictlib as L
ok = True
def chk(name, cond, info=''):
    global ok; print(('PASS ' if cond else 'FAIL ') + name, info); ok &= bool(cond)
# 1) unit cases
h = np.array([10., 11, 12, 13]); l = np.array([9., 9.5, 10.5, 11]); c = np.array([9.8, 10.8, 11.8, 12.8]); hr = np.zeros(4)
chk('walk long hits target', L.walk(h, l, c, hr, 0, 1, 10, 8, 12.5)[2] == 'tgt')
chk('walk stop-first when both on one bar', L.walk(np.array([13.]), np.array([7.]), np.array([10.]), np.zeros(1), 0, 1, 10, 8, 12)[2] == 'stop')
o = np.array([9.5, 10.2]); h2 = np.array([10.4, 11]); l2 = np.array([9.0, 10.1]); c2 = np.array([10.3, 10.9])
r = L.walk_stop_entry(o, h2, l2, c2, np.zeros(2), 0, 1, 10.0, 9.2, 10.8)
chk('stop-entry: fill-bar low before fill ignored', r[2] == 'tgt' and r[1] == 1, r)
r = L.walk_stop_entry(o, h2, np.array([9.0, 10.1]), np.array([9.1, 10.9]), np.zeros(2), 0, 1, 10.0, 9.2, 10.8)
chk('stop-entry: fill-bar CLOSE below stop counts', r[2] == 'stop', r)
# 2) negative control on real NQ 1m (learn era), random entries, 20-pt stop, 20-pt target, 1 day max
d = L.load_cme('NQ'); d = d[d.ts.dt.year <= 2023].reset_index(drop=True)
H, Lo, C, HR = d.h.values, d.l.values, d.c.values, d.hr.values
rng = np.random.default_rng(7); idx = rng.choice(np.arange(100, len(d) - 2000), 4000, replace=False); res = []
for i in idx:
    s = rng.choice([1, -1]); E = C[i]; st = E - s * 20; tg = E + s * 20
    x, q, how = L.walk(H, Lo, C, HR, i + 1, s, E, st, tg, max_bars=1400); res.append(s * (x - E) / 20)
m = np.mean(res); se = np.std(res) / np.sqrt(len(res))
chk('negative control: random NQ entries gross ~0R', abs(m) < 3 * se + 0.02, f'mean {m:+.3f}R se {se:.3f}')
# 3) positive control: random walk, after marker bars price drifts +0.3 sd/bar for 20 bars; long 1R=2sd stop/target at marker
n = 400000; ret = rng.normal(0, 1, n); mk = rng.random(n) < 0.002
for i in np.where(mk)[0]: ret[i + 1:i + 21] += 0.3
p = 1000 + np.cumsum(ret); hh = p + np.abs(rng.normal(0, .3, n)); ll = p - np.abs(rng.normal(0, .3, n))
hits = []; ctrl = []
for i in np.where(mk)[0][:-5]:
    for arr, j in ((hits, i), (ctrl, i + 50)):
        if j + 25 >= n: continue
        E = p[j]; x, q, how = L.walk(hh, ll, p, np.zeros(n), j + 1, 1, E, E - 4, E + 4, max_bars=20); arr.append((x - E) / 4)
chk('positive control: planted edge detected', np.mean(hits) > 0.25 and abs(np.mean(ctrl)) < 0.1, f'edge {np.mean(hits):+.3f}R control {np.mean(ctrl):+.3f}R')
# 4) costs
chk('cost broker crypto 1% stop ~0.10R', abs(L.cost_R('broker', 'BTC', 100, 1.0) - 0.10) < 1e-9)
chk('cost cme NQ 10pt stop', abs(L.cost_R('cme', 'NQ', 15000, 10) - (0.5 + 0.25) / 10) < 1e-9)
print('ALL PASS' if ok else 'SELFTEST FAILED'); sys.exit(0 if ok else 1)
