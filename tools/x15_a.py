"""X15a: ICT queue ranks 1 (Venom), 9 (PM OR FVG -> 15:15 macro), 11 (equity-window last up-close candle), 12 (08:30-open AM
FVG), 13 (stop entry when draw not reached). Pre-registered in specs/X15-ict-rest.md. LEARN ERA ONLY - data truncated at load."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
OUT = r'C:\Users\you\Desktop\TradeDeck\specs\X15a-results.md'
MAXCOST, MINROOM = 0.25, 1.0
MODELS = 'VPEAN'

def load_gold():  # copied from x14_sb.py
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

def learn(d, le):
    y = np.array([x.year for x in d.date.values]); return d[y <= le].reset_index(drop=True)

def daily_info(d):
    D = L.daily(d[d.dow <= 4]); up = (D.c > D.o)
    I = pd.DataFrame(index=D.index)
    I['b1'] = np.sign(D.c - D.o).shift(1)
    I['b2'] = np.sign(D.c - D.c.rolling(20).mean()).shift(1)
    I['pdh'] = D.h.shift(1); I['pdl'] = D.l.shift(1)
    I['upo'] = D.o.where(up).ffill().shift(1); I['dno'] = D.o.where(D.c < D.o).ffill().shift(1)
    return I

def coin(dt, seed): return 1 if np.random.default_rng(dt.toordinal() * 13 + seed).random() < 0.5 else -1

def finish(x, q, side, E, stop, tgt, flat):
    h, l, c, hr = x['h'], x['l'], x['c'], x['hr']
    if (l[q] <= stop) if side == 1 else (h[q] >= stop): xp = stop
    else: xp, _, _ = L.walk(h, l, c, hr, q + 1, side, E, stop, tgt, flat_hr=flat)
    return side * (xp - E) / abs(E - stop)

def limit_fill(x, q0, side, E, until):
    h, l, hr = x['h'], x['l'], x['hr']
    for q in range(q0, len(h)):
        if hr[q] >= until: return None
        if (h[q] >= E) if side == -1 else (l[q] <= E): return q
    return None

# ---------- models: each returns (q, side, E, stop, tgt, flat) or None ----------
def venom(x, side, tick, st):
    h, l, c, hr = x['h'], x['l'], x['c'], x['hr']
    pre = (hr >= 8) & (hr < 9.5)
    if pre.sum() < 6: return None
    H, Lo = h[pre].max(), l[pre].min(); n = len(h); cnt = 0
    for k in np.where((hr >= 9.5) & (hr < 10.5))[0]:
        beyond = (h[k] > H) if side == -1 else (l[k] < Lo)
        if not beyond: continue
        cnt += 1
        if cnt > 2: return None
        if k < 3 or k + 2 >= n: continue
        if side == -1:
            ok = c[k] > H and ((l[k - 1] > h[k - 3]) or (l[k] > h[k - 2])) and h[k + 2] < l[k]
            E, stop, tgt = c[k], h[k] + tick, Lo
        else:
            ok = c[k] < Lo and ((h[k - 1] < l[k - 3]) or (h[k] < l[k - 2])) and l[k + 2] > h[k]
            E, stop, tgt = c[k], l[k] - tick, H
        if not ok: continue
        q = limit_fill(x, k + 3, side, E, 12.0)
        return None if q is None else (q, side, E, stop, tgt, 16.0)
    return None

def pm_fvg(x, mode, tick, st, b2=0, rs=1):
    h, l, c, o, hr = x['h'], x['l'], x['c'], x['o'], x['hr']
    idx = np.where((hr >= 13.5) & (hr < 14.0))[0]; f = None
    for j in idx:
        if j - 2 < 0 or hr[j - 2] < 13.5: continue
        if l[j] > h[j - 2]: f = (j, 1, h[j - 2], l[j]); break
        if h[j] < l[j - 2]: f = (j, -1, h[j], l[j - 2]); break
    if f is None: return None
    j, role, lo, hi = f; ce = (lo + hi) / 2; tlo, thi = l[j - 2:j + 1].min(), h[j - 2:j + 1].max()
    for q in range(j + 1, len(h)):
        if hr[q] >= 15.25: break
        if hr[q] < 14.0: continue
        if (role == 1 and c[q] < lo) or (role == -1 and c[q] > hi): role = -role; break
    side = role
    if mode == 'B' and side != b2: return None
    if mode == 'COIN': side = rs
    s = np.where(hr >= 15.25)[0]
    if not len(s) or hr[s[0]] >= 15.75: return None
    s = s[0]
    if (side == 1 and not o[s] > ce) or (side == -1 and not o[s] < ce): return None
    stop = tlo - tick if side == 1 else thi + tick
    q = limit_fill(x, s, side, ce, 15.75)
    if q is None: return None
    return (q, side, ce, stop, ce + side * 2 * abs(ce - stop), 16.0)

def eq_window(x, side, tick, st, w0, w1):
    h, l, c, o, hr = x['h'], x['l'], x['c'], x['o'], x['hr']
    lvl = st['upo'] if side == -1 else st['dno']
    if not np.isfinite(lvl): return None
    for k in np.where((hr >= w0) & (hr < w1))[0]:
        if k < 1: continue
        brk = (c[k] < lvl <= c[k - 1]) if side == -1 else (c[k] > lvl >= c[k - 1])
        if not brk: continue
        j = k - 1
        while j >= 0 and hr[j] >= 8 and not ((c[j] > o[j]) if side == -1 else (c[j] < o[j])): j -= 1
        if j < 0 or hr[j] < 8: return None
        E = o[j]
        if side == -1:
            if not E > c[k]: return None
            stop, tgt = h[j:k + 1].max() + tick, st['pdl']
        else:
            if not E < c[k]: return None
            stop, tgt = l[j:k + 1].min() - tick, st['pdh']
        q = limit_fill(x, k + 1, side, E, w1)
        return None if q is None else (q, side, E, stop, tgt, w1)
    return None

def am830(x, side, tick, st):
    h, l, o, hr = x['h'], x['l'], x['o'], x['hr']
    s = np.where(hr >= 8.5)[0]
    if not len(s) or hr[s[0]] >= 8.6: return None
    s0 = s[0]; O = o[s0]
    for k in np.where((hr >= 8.5) & (hr < 11.0))[0]:
        if k - 2 < s0: continue
        if side == -1 and h[k] < l[k - 2] and h[k] > O:
            E, stop, tgt = h[k], h[k - 2:k + 1].max() + tick, st['pdl']
        elif side == 1 and l[k] > h[k - 2] and l[k] < O:
            E, stop, tgt = l[k], l[k - 2:k + 1].min() - tick, st['pdh']
        else: continue
        q = limit_fill(x, k + 1, side, E, 11.0)
        return None if q is None else (q, side, E, stop, tgt, 16.0)
    return None

def nodraw(x, bias, tick, st):
    h, l, o, hr = x['h'], x['l'], x['o'], x['hr']
    am = np.where(hr < 12)[0]; pm = np.where(hr >= 12)[0]
    if len(am) < 20 or not len(pm): return None
    side = -bias
    if bias == 1:
        ix = am[np.argmax(h[am])]; Hx = h[ix]
        if not Hx < st['pdh'] or ix >= am[-1]: return None
        FL = l[ix + 1:am[-1] + 1].min(); E = FL - tick; stop = Hx + tick
        if o[pm[0]] < FL: return None
    else:
        ix = am[np.argmin(l[am])]; Lx = l[ix]
        if not Lx > st['pdl'] or ix >= am[-1]: return None
        FH = h[ix + 1:am[-1] + 1].max(); E = FH + tick; stop = Lx - tick
        if o[pm[0]] > FH: return None
    for q in pm:
        if hr[q] >= 15.5: return None
        if (l[q] <= E) if side == -1 else (h[q] >= E):
            F = min(E, o[q]) if side == -1 else max(E, o[q])
            if (stop - F) * side >= 0: return None
            return (q, side, F, stop, F + side * 2 * abs(F - stop), 16.0)
    return None

def run_market(m, d, venue, tick, rolls=frozenset()):
    I = daily_info(d); rows = []; skip_next = {}
    for dt, g in d.groupby('date'):
        if dt in rolls or g.dow.iloc[0] > 4 or dt not in I.index: continue
        st = I.loc[dt]
        if not (np.isfinite(st.b1) and np.isfinite(st.b2) and np.isfinite(st.pdh)): continue
        x = {k: g[k].values for k in ('o', 'h', 'l', 'c', 'hr')}
        b1, b2, rs = int(st.b1), int(st.b2), coin(dt, 0)
        cand = []
        for cell, b in (('B1', b1), ('B2', b2), ('COIN', rs)):
            if b == 0: continue
            if 'V' in MODELS: cand.append(('M01 Venom', cell, venom(x, -b, tick, st)))
            if 'E' in MODELS:
                for w0, w1 in ((9.5, 11.0), (13.0, 16.0)): cand.append(('M11 EqWindow', cell, eq_window(x, b, tick, st, w0, w1)))
            if 'A' in MODELS:
                if skip_next.pop(cell, False): pass
                else: cand.append(('M12 AM0830', cell, am830(x, b, tick, st)))
            if 'N' in MODELS: cand.append(('M13 NoDrawStop', cell, nodraw(x, b, tick, st)))
        if 'P' in MODELS:
            for cell in ('A', 'B', 'COIN'): cand.append(('M09 PMfvg', cell, pm_fvg(x, cell, tick, st, b2, rs)))
        for model, cell, tr in cand:
            if tr is None: continue
            q, side, E, stop, tgt, flat = tr; risk = abs(E - stop)
            if risk <= 0: continue
            cst = L.cost_R(venue, m, E, risk)
            if cst > MAXCOST: continue
            if model in ('M01 Venom', 'M11 EqWindow', 'M12 AM0830'):
                if not np.isfinite(tgt) or (tgt - E) * side < MINROOM * risk: continue
            gross = finish(x, q, side, E, stop, tgt, flat)
            if model == 'M12 AM0830' and gross >= (tgt - E) * side / risk - 1e-9: skip_next[cell] = True
            rows.append(dict(model=model, cell=cell, m=m, venue=venue, date=dt, gross=gross, net=gross - cst))
            if venue == 'cme':
                rows.append(dict(model=model, cell=cell, m=m, venue='broker', date=dt, gross=gross, net=gross - L.cost_R('broker', m, E, risk)))
    return rows

if __name__ == '__main__':
    rows = []
    for m in ('NQ', 'ES'):
        d1 = learn(L.load_cme(m, '1min'), L.CME_LEARN_END); rolls = L.roll_dates(d1)
        MODELS = 'VPN'; rows += run_market(m, d1, 'cme', 0.25, rolls); del d1
        d5 = learn(L.load_cme(m, '5min'), L.CME_LEARN_END)
        MODELS = 'EA'; rows += run_market(m, d5, 'cme', 0.25, rolls); del d5
        print(m, 'done', len(rows), flush=True)
    MODELS = 'VPEAN'
    rows += run_market('XAUUSD', learn(load_gold(), 2022), 'broker_cmdty', 0.01); print('gold done', flush=True)
    for m in ('BTCUSDT', 'ETHUSDT'): rows += run_market(m, learn(L.load_crypto(m, '5m'), L.CRYPTO_LEARN_END), 'broker', 0.0)
    print('crypto done', flush=True)
    for m in L.FX_PAIRS: rows += run_market(m, learn(L.load_fx(m, '5m'), L.FX_LEARN_END), 'broker_fx', 0.0)
    T = pd.DataFrame(rows); T.to_pickle(os.path.join(os.environ.get('TEMP', '.'), 'x15a_rows.pkl'))
    grp = {'NQ': 'NQ', 'ES': 'ES', 'XAUUSD': 'GOLD', 'BTCUSDT': 'CRYPTO', 'ETHUSDT': 'CRYPTO'}
    T['grp'] = T.m.map(grp).fillna('FX') + '@' + T.venue
    les = {'NQ': 2023, 'ES': 2023, 'GOLD': 2022, 'CRYPTO': 2023, 'FX': 2019}
    out = ['# X15a results (LEARN ERA ONLY; pre-registration in specs/X15-ict-rest.md)', '',
           '| model | cell | market@venue | n | win% | gross R | NET R | maxDD R | per market NET | verdict |', '|---|---|---|---|---|---|---|---|---|---|']
    for (mo, g, ce), t in T.groupby(['model', 'grp', 'cell']):
        assert max(x.year for x in t.date) <= les[g.split('@')[0]]
        t = t.sort_values('date'); net = t.net.mean(); dd = L.maxdd(t.net.values); per = t.groupby('m').net.mean()
        ok = net >= 0.05 and dd <= 30
        if g.startswith('FX'): ok = ok and (per > 0).sum() >= 3
        v = '-' if ce == 'COIN' else ('PASS' if ok else 'FAIL')
        out.append(f"| {mo} | {ce} | {g} | {len(t)} | {100*(t.net>0).mean():.0f} | {t.gross.mean():+.3f} | {net:+.3f} | {dd:.0f} | "
                   + ' '.join(f'{k[:3]}{v2:+.2f}' for k, v2 in per.items()) + f' | {v} |')
    txt = '\n'.join(out); print(txt); open(OUT, 'w', encoding='utf-8').write(txt + '\n')
