"""X14: ICT Silver Bullet on NQ/ES/gold/BTC/ETH/FX (specs/X14-silver-bullet.md). EXAM=1 opens the exam once."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'
WINDOWS = {'W1': 3, 'W2': 10, 'W3': 14}

def load_gold():
    c = sqlite3.connect(L.DB)
    a = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    b = b[b.t > a.t.max()]; return L._ny(pd.concat([a, b]).reset_index(drop=True))

def run(m, d, venue, learn_end, tick, min_room):
    rows = []; rolls = L.roll_dates(d) if venue == 'cme' else set()
    for dt, x in d.groupby('date'):
        if dt in rolls or x.dow.iloc[0] > 4: continue
        h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
        for wn, w0 in WINDOWS.items():
            pre = (hr >= w0 - 2) & (hr < w0)
            if pre.sum() < 6: continue
            ph, pl = h[pre].max(), l[pre].min()
            inw = np.where((hr >= w0) & (hr < w0 + 1))[0]
            if len(inw) < 6: continue
            side = 0; trade = None
            for k in inw:
                if side == 0:
                    if h[k] > ph: side = -1
                    elif l[k] < pl: side = 1
                    continue
                if k < 2: continue
                if side == -1 and l[k - 2] > h[k]: near, ext = h[k] + tick, max(h[k - 2:k + 1]) + tick; tgt = pl
                elif side == 1 and h[k - 2] < l[k]: near, ext = l[k] - tick, min(l[k - 2:k + 1]) - tick; tgt = ph
                else: continue
                risk = abs(ext - near); room = (near - tgt) * -side * -1 if False else abs(tgt - near)
                if risk <= 0 or (tgt - near) * side <= 0 or room < max(min_room, 0) or (min_room == 0 and room < risk): break
                for q in range(k + 1, len(h)):
                    if hr[q] >= w0 + 1: break
                    if (h[q] >= near) if side == -1 else (l[q] <= near): trade = (q, near, ext, tgt); break
                break
            if trade is None: continue
            q, E, stop, tgt = trade
            if (h[q] >= stop) if side == -1 else (l[q] <= stop): xp = stop
            else: xp, _, _ = L.walk(h, l, c, hr, q + 1, side, E, stop, tgt, flat_hr=w0 + 2)
            risk = abs(E - stop); gross = side * (xp - E) / risk
            mm = m if venue == 'cme' else m
            rows.append(dict(model=f'{m}', cell=wn, m=m, date=dt, gross=gross, net=gross - L.cost_R(venue, mm, E, risk)))
            if venue == 'cme':
                rows.append(dict(model=f'{m}@broker', cell=wn, m=m, date=dt, gross=gross, net=gross - L.cost_R('broker', mm, E, risk)))
    return rows

groups = []
for m in ('NQ', 'ES'):
    groups.append(('CME', L.CME_LEARN_END, run(m, L.load_cme(m), 'cme', L.CME_LEARN_END, 0.25, 10 if m == 'NQ' else 2.5)))
groups.append(('GOLD', 2022, run('XAUUSD', load_gold(), 'broker_cmdty', 2022, 0.01, 0)))
for m in ('BTCUSDT', 'ETHUSDT'):
    groups.append(('CRYPTO', L.CRYPTO_LEARN_END, run(m, L.load_crypto(m, '5m'), 'broker', L.CRYPTO_LEARN_END, 0, 0)))
for m in L.FX_PAIRS:
    groups.append(('FX', L.FX_LEARN_END, run(m, L.load_fx(m, '5m'), 'broker_fx', L.FX_LEARN_END, 0, 0)))
txt = []
for name in ('CME', 'GOLD', 'CRYPTO', 'FX'):
    rows = sum([g[2] for g in groups if g[0] == name], []); le = [g[1] for g in groups if g[0] == name][0]
    T = pd.DataFrame(rows)
    if name in ('CRYPTO', 'FX'): T['model'] = name
    txt.append(L.report(T, f'X14 Silver Bullet — {name}', le, os.devnull, exam=EXAM))
open(r'C:\Users\you\Desktop\TradeDeck\specs\X14-results.md', 'w', encoding='utf-8').write('\n\n'.join(txt) + '\n')
