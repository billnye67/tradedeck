"""X13c: ICT daily models on FX, Broker costs. Queue ranks 28, 29, 33, 35, 45. Pre-registered in specs/X13-ict-broker.md
(section X13c). LEARN ERA ONLY: rows after 2019 are dropped at load."""
import sys; sys.path.insert(0, r'C:\Users\you\Desktop\TradeDeck\tools')
import numpy as np, pandas as pd, ictlib as L
OUT = r'C:\Users\you\Desktop\TradeDeck\specs\X13c-results.md'
RNG = np.random.default_rng(13)

class P:
    def __init__(s, m):
        d = L.load_fx(m, '15m'); d = d[pd.to_datetime(d.date) < '2020-01-01'].reset_index(drop=True)   # exam sealed
        s.m = m; s.pip = L.pip_of(m); s.tick = 0.1 * s.pip
        s.o, s.h, s.l, s.c, s.hr = (d[k].values.astype(float) for k in ('o', 'h', 'l', 'c', 'hr'))
        key = (d.ts + pd.Timedelta(hours=7)).dt.date            # session rolls at 17:00 NY
        s.rc = np.cumsum((key != key.shift()).values); s.N = len(d)
        g = d.groupby('date').indices; rows = []
        for dt, ix in g.items():
            if len(ix) >= 40: rows.append((dt, ix[0], ix[-1] + 1))
        rows.sort()
        s.dates = [r[0] for r in rows]; s.a = np.array([r[1] for r in rows]); s.b = np.array([r[2] for r in rows])
        s.O = s.o[s.a]; s.C = s.c[s.b - 1]
        s.H = np.array([s.h[x:y].max() for x, y in zip(s.a, s.b)]); s.Lo = np.array([s.l[x:y].min() for x, y in zip(s.a, s.b)])
        pc = np.r_[np.nan, s.C[:-1]]
        tr = np.nanmax(np.vstack([s.H - s.Lo, np.abs(s.H - pc), np.abs(s.Lo - pc)]), axis=0)
        s.atr20 = pd.Series(tr).rolling(20).mean().values; s.atr14 = pd.Series(tr).rolling(14).mean().values
        sma = pd.Series(s.C).rolling(20).mean().values
        s.B2 = np.where(np.isnan(sma), 0, np.sign(s.C - sma))   # known at close of day k
        s.ND = len(s.dates)

def resolve(p, i, side, E, stop, tgt, maxdays):
    risk = abs(E - stop)
    if risk <= 0 or side * (stop - E) >= 0 or (tgt is not None and side * (tgt - E) <= 0): return None
    if (p.l[i] <= stop) if side == 1 else (p.h[i] >= stop): X, q = stop, i
    elif i + 1 >= p.N: X, q = p.c[i], i
    else: X, q, _ = L.walk(p.h, p.l, p.c, p.hr, i + 1, side, E, stop, tgt, max_bars=maxdays * 96)
    g = side * (X - E) / risk
    return q, g, g - L.cost_R('broker_fx', p.m, E, risk, int(p.rc[q] - p.rc[i]))

def scan(p, a, b, down, level, void=None):
    """First bar in [a,b) hitting level. down=True: order fills when price falls to level (buy limit / sell stop).
    void=(dir, lvl): dir +1 -> void on h > lvl, -1 -> l < lvl, only on bars strictly before the fill bar."""
    if b <= a: return None
    hit = (p.l[a:b] <= level) if down else (p.h[a:b] >= level)
    fi = int(np.argmax(hit)) if hit.any() else None
    if void is not None:
        vh = (p.h[a:b] > void[1]) if void[0] == 1 else (p.l[a:b] < void[1])
        if vh.any():
            vi = int(np.argmax(vh))
            if fi is None or vi < fi: return ('void', a + vi)
    if fi is None: return None
    i = a + fi; E = min(level, p.o[i]) if down else max(level, p.o[i])
    return ('fill', i, E)

def run_orders(p, gen, model, cell, rows, ctrl=None):
    """Generic daily engine. gen(p, k) -> list of new orders made at close of day k (worked from day k+1). Order dict: side,
    down, level, stop, tgt, exp (last day index valid), void, maxd, mt (daily-close cancel level or None), tag (replace key)."""
    pend = []; busy = -1
    for d in range(p.ND):
        a, b = p.a[d], p.b[d]; ev = []
        for o in pend:
            if o['armB'] is not None and not o['armed']:     # M35 entry B, intraday arming
                up = o['side'] == -1
                hit = (p.h[a:b] >= o['armB']) if up else (p.l[a:b] <= o['armB'])
                vh = (p.h[a:b] > o['void'][1]) if o['void'][0] == 1 else (p.l[a:b] < o['void'][1])
                if hit.any():
                    ai = int(np.argmax(hit))
                    if vh.any() and int(np.argmax(vh)) <= ai: ev.append(('void', a + int(np.argmax(vh)), o)); continue
                    o['armed'] = True; r = scan(p, a + ai + 1, b, o['down'], o['level'], o['void'])
                else:
                    r = ('void', a + int(np.argmax(vh))) if vh.any() else None
            else:
                r = scan(p, a, b, o['down'], o['level'], o['void'])
            if r is not None: ev.append((r[0], r[1], o) if r[0] == 'void' else ('fill', r[1], o, r[2]))
        ev.sort(key=lambda x: x[1]); gone = set()
        for e in ev:
            o = e[2]; gone.add(id(o))
            if e[0] == 'fill' and e[1] > busy:
                res = resolve(p, e[1], o['side'], e[3], o['stop'], o['tgt'], o['maxd'])
                if res is None: continue
                busy = res[0]; rows.append((model, cell, p.m, p.dates[d], res[1], res[2]))
                if ctrl is not None:
                    s = RNG.choice([1, -1]); E = e[3]
                    st, tg = (o['stop'], o['tgt']) if s == 1 else (2 * E - o['stop'], 2 * E - o['tgt'])
                    r2 = resolve(p, e[1], s * o['side'], E, st, tg, o['maxd'])
                    if r2 is not None: rows.append((model, ctrl, p.m, p.dates[d], r2[1], r2[2]))
        pend = [o for o in pend if id(o) not in gone and o['exp'] > d
                and not (o['mt'] is not None and o['side'] * (p.C[d] - o['mt']) < 0)]
        for n in gen(p, d):
            pend = [o for o in pend if o['tag'] != n['tag']] + [n]

def O_(side, down, level, stop, tgt, exp, maxd, tag, void=None, mt=None, armB=None):
    return dict(side=side, down=down, level=level, stop=stop, tgt=tgt, exp=exp, maxd=maxd, tag=tag, void=void, mt=mt,
                armB=armB, armed=False)

# ---------------- M28 ----------------
def g28A(p, k):
    if k < 20: return []
    if p.C[k] < p.O[k] and p.B2[k] == 1:
        E = p.O[k]; st = p.Lo[k - 4:k + 1].min() - p.pip
        return [O_(1, False, E, st, E + 2 * (E - st), k + 5, 20, 'x')]
    if p.C[k] > p.O[k] and p.B2[k] == -1:
        E = p.O[k]; st = p.H[k - 4:k + 1].max() + p.pip
        return [O_(-1, True, E, st, E - 2 * (st - E), k + 5, 20, 'x')]
    return []

def g28B(p, k):
    if k < 21: return []
    out = []
    if p.C[k] > p.O[k] and p.H[k] > p.H[k - 20:k].max():
        out.append(O_(-1, True, p.O[k], p.H[k] + p.pip, p.Lo[k - 20:k].min(), k + 5, 30, 's'))
    if p.C[k] < p.O[k] and p.Lo[k] < p.Lo[k - 20:k].min():
        out.append(O_(1, False, p.O[k], p.Lo[k] - p.pip, p.H[k - 20:k].max(), k + 5, 30, 'l'))
    return out

# ---------------- M29 ----------------
def g29(fib):
    cur = {}
    def g(p, k):
        if k < 25 or np.isnan(p.atr20[k]): return []
        j = k - 2; out = []; A = p.atr20[k]
        if p.H[j] > p.H[j - 1] and p.H[j] > p.H[j + 1] and p.H[k] < p.H[j]:
            lo_i = j - 9 + int(np.argmin(p.Lo[j - 9:j + 1])); lo = p.Lo[lo_i]; R = p.H[j] - lo
            if lo_i < j and R >= 1.5 * A:
                c0 = cur.get('l')
                if c0 is None or c0['exp'] <= k or p.H[j] > c0['fh']:
                    o = O_(1, True, p.H[j] - fib * R, lo - 0.1 * A, p.H[j] + 0.1 * A, k + 20, 40, 'l', void=(1, p.H[j]))
                    o['fh'] = p.H[j]; cur['l'] = o; out.append(o)
        if p.Lo[j] < p.Lo[j - 1] and p.Lo[j] < p.Lo[j + 1] and p.Lo[k] > p.Lo[j]:
            hi_i = j - 9 + int(np.argmax(p.H[j - 9:j + 1])); hi = p.H[hi_i]; R = hi - p.Lo[j]
            if hi_i < j and R >= 1.5 * A:
                c0 = cur.get('s')
                if c0 is None or c0['exp'] <= k or p.Lo[j] < c0['fh']:
                    o = O_(-1, False, p.Lo[j] + fib * R, hi + 0.1 * A, p.Lo[j] - 0.1 * A, k + 20, 40, 's', void=(-1, p.Lo[j]))
                    o['fh'] = p.Lo[j]; cur['s'] = o; out.append(o)
        return out
    return g

# ---------------- M33 ----------------
def g33(tmode):
    def g(p, k):
        if k < 25: return []
        out = []
        for side in (1, -1):
            dn = (lambda x: p.C[x] < p.O[x]) if side == 1 else (lambda x: p.C[x] > p.O[x])
            for e in range(k - 1, k - 11, -1):
                if not dn(e) or dn(e + 1): continue
                s = e
                while s - 1 >= 0 and dn(s - 1) and e - s + 1 <= 5: s -= 1
                if e - s + 1 > 5: continue
                if side == 1:
                    obH, obL = p.H[s:e + 1].max(), p.Lo[s:e + 1].min(); E = p.O[s:e + 1].max(); mt = (E + p.C[s:e + 1].min()) / 2
                    goal = obH + 2 * (obH - obL); mx = p.H[e + 1:k + 1]
                    if not (mx.max() >= goal and (k == e + 1 or p.H[e + 1:k].max() < goal)): continue
                    if not any(p.Lo[t] > p.H[t - 2] for t in range(e + 1, k + 1)): continue
                    if k >= e + 2 and p.Lo[e + 2:k + 1].min() <= E: continue
                    if p.B2[k] != 1: continue
                    st = obL - p.pip; tg = mx.max() if tmode == 'ERL' else E + 3 * (E - st)
                    out.append(O_(1, True, E, st, tg, k + 20, 40, 'l', mt=mt))
                else:
                    obH, obL = p.H[s:e + 1].max(), p.Lo[s:e + 1].min(); E = p.O[s:e + 1].min(); mt = (E + p.C[s:e + 1].max()) / 2
                    goal = obL - 2 * (obH - obL); mn = p.Lo[e + 1:k + 1]
                    if not (mn.min() <= goal and (k == e + 1 or p.Lo[e + 1:k].min() > goal)): continue
                    if not any(p.H[t] < p.Lo[t - 2] for t in range(e + 1, k + 1)): continue
                    if k >= e + 2 and p.H[e + 2:k + 1].max() >= E: continue
                    if p.B2[k] != -1: continue
                    st = obH + p.pip; tg = mn.min() if tmode == 'ERL' else E - 3 * (st - E)
                    out.append(O_(-1, False, E, st, tg, k + 20, 40, 's', mt=mt))
                break
        return out
    return g

# ---------------- M35 ----------------
def g35(entry):
    def g(p, k):
        if k < 25: return []
        j = k - 1; out = []
        bt = lambda x: max(p.O[x], p.C[x]); bb = lambda x: min(p.O[x], p.C[x])
        if p.B2[k] == -1 and p.H[j] > p.H[j - 1] and p.H[j] > p.H[k] and p.H[j] - bt(j) >= 0.5 * (p.H[j] - p.Lo[j]) > 0:
            top = p.H[j]; bot = max(bt(x) for x in (j - 1, j, k)); ht = top - bot
            fl = [i for i in range(j - 2, 1, -1) if p.Lo[i] < p.Lo[i - 1] and p.Lo[i] < p.Lo[i + 1]][:1]
            if fl and ht > 0:
                i = fl[0]; tg = min(bb(x) for x in (i - 1, i, i + 1)) - p.pip
                if tg < bot:
                    if entry == 'A': out.append(O_(-1, False, bot, top + p.pip, tg, k + 10, 30, 's', void=(1, top)))
                    else: out.append(O_(-1, True, bot - p.pip, top + p.pip, tg, k + 10, 30, 's', void=(1, top), armB=bot + 0.25 * ht))
        if p.B2[k] == 1 and p.Lo[j] < p.Lo[j - 1] and p.Lo[j] < p.Lo[k] and bb(j) - p.Lo[j] >= 0.5 * (p.H[j] - p.Lo[j]) > 0:
            bot = p.Lo[j]; top = min(bb(x) for x in (j - 1, j, k)); ht = top - bot
            fh = [i for i in range(j - 2, 1, -1) if p.H[i] > p.H[i - 1] and p.H[i] > p.H[i + 1]][:1]
            if fh and ht > 0:
                i = fh[0]; tg = max(bt(x) for x in (i - 1, i, i + 1)) + p.pip
                if tg > top:
                    if entry == 'A': out.append(O_(1, True, top, bot - p.pip, tg, k + 10, 30, 'l', void=(-1, bot)))
                    else: out.append(O_(1, False, top + p.pip, bot - p.pip, tg, k + 10, 30, 'l', void=(-1, bot), armB=top - 0.25 * ht))
        return out
    return g

# ---------------- M45 ----------------
def run45(p, cell, N, NA, valid, bias, rows, ctrl=None):
    busy = -1
    for d in range(max(N, NA) + 1, p.ND):
        A = p.atr14[d - 1]
        if np.isnan(A): continue
        a = p.a[d]; b = p.b[min(d + valid - 1, p.ND - 1)]
        if a <= busy: continue
        cands = []
        for side in (1, -1):
            if bias and p.B2[d - 1] != side: continue
            if side == 1:
                old = p.Lo[d - N:d].min()
                if not p.O[d] > old: continue
                sw = p.l[a:p.b[d]] < old
            else:
                old = p.H[d - N:d].max()
                if not p.O[d] < old: continue
                sw = p.h[a:p.b[d]] > old
            if not sw.any(): continue                          # sweep must happen on day d
            s = a + int(np.argmax(sw))
            lvl = p.O[d] + side * p.tick
            r = scan(p, s + 1, b, side == -1, lvl)
            if r is None: continue
            i, E = r[1], r[2]
            if side == 1:
                lo = p.l[a:i].min(); st = lo - 0.1 * A; anc = p.H[d - NA:d].max(); tg = lo + 0.705 * (anc - lo)
            else:
                hi = p.h[a:i].max(); st = hi + 0.1 * A; anc = p.Lo[d - NA:d].min(); tg = hi - 0.705 * (hi - anc)
            cands.append((i, side, E, st, tg))
        for i, side, E, st, tg in sorted(cands):
            if i <= busy: continue
            res = resolve(p, i, side, E, st, tg, 20)
            if res is None: continue
            busy = res[0]; rows.append(('M45', cell, p.m, p.dates[d], res[1], res[2]))
            if ctrl is not None:
                s2 = RNG.choice([1, -1])
                st2, tg2 = (st, tg) if s2 == 1 else (2 * E - st, 2 * E - tg)
                r2 = resolve(p, i, s2 * side, E, st2, tg2, 20)
                if r2 is not None: rows.append(('M45', ctrl, p.m, p.dates[d], r2[1], r2[2]))
            break

rows = []
for m in L.FX_PAIRS:
    p = P(m); print(m, p.ND, 'days', p.dates[0], p.dates[-1], flush=True)
    run_orders(p, g28A, 'M28', 'A_LDC_B2', rows, ctrl='A_rand')
    run_orders(p, g28B, 'M28', 'B_Model4', rows)
    run_orders(p, g29(0.62), 'M29', 'A_E62', rows, ctrl='A_rand')
    run_orders(p, g29(0.79), 'M29', 'B_E79', rows)
    run_orders(p, g33('ERL'), 'M33', 'A_ERL', rows, ctrl='A_rand')
    run_orders(p, g33('3R'), 'M33', 'B_3R', rows)
    run_orders(p, g35('A'), 'M35', 'A_EntryA', rows, ctrl='A_rand')
    run_orders(p, g35('B'), 'M35', 'B_EntryB', rows)
    run45(p, 'A_N10', 10, 5, 1, False, rows, ctrl='A_rand')
    run45(p, 'B_N20', 20, 20, 3, False, rows)
    run45(p, 'C_N10_B2', 10, 5, 1, True, rows)
T = pd.DataFrame(rows, columns=['model', 'cell', 'm', 'date', 'gross', 'net'])
assert pd.to_datetime(T.date).dt.year.max() <= L.FX_LEARN_END
L.report(T, 'X13c learn 2012-2019: ICT daily models on FX (Broker costs)', L.FX_LEARN_END, OUT)
ver = ['\n## Verdicts (pass bar: NET >= +0.05R, maxDD <= 30R, >= 3/4 pairs positive)']
for (mo, cell), g in T.groupby(['model', 'cell']):
    g = g.sort_values('date'); net = g.net.mean(); dd = L.maxdd(g.net.values); pp = int((g.groupby('m').net.mean() > 0).sum())
    v = 'control' if 'rand' in cell else ('PASS' if net >= 0.05 and dd <= 30 and pp >= 3 else 'FAIL')
    ver.append(f'{mo}/{cell}: n={len(g)} NET {net:+.3f}R maxDD {dd:.0f}R pairs+ {pp}/4 -> {v}')
txt = '\n'.join(ver); print(txt); open(OUT, 'a', encoding='utf-8').write(txt + '\n')
