"""R11: 2025 NY lunch macro rule (specs/ict-rebuild/R11-lunch-macro.md). EXAMPLE=1 prints NQ 2025-02-24. LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; TICK = 0.25

def to5(x):
    y = x[(x.hr >= 9.5) & (x.hr < 16)].set_index('ts').resample('5min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    y['hr'] = y.index.hour + y.index.minute / 60; return y.reset_index()

def day_trade(x):
    b = to5(x)
    if len(b) < 40: return None
    o, h, l, c, hr = b.o.values, b.h.values, b.l.values, b.c.values, b.hr.values; n = len(h)
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    i10 = int(np.argmax(hr >= 10)); side = 1 if c[i10 - 1] < o[0] else -1          # down AM -> long retracement (side +1)
    # one-directional AM: no 5m swing high (down day) formed 09:30-10:00 was exceeded before 10:00
    for k in range(1, i10 - 1):
        if (sh[k] if side == 1 else sl[k]) and k + 1 < i10:
            lvl = h[k]
            if side == 1 and h[k + 2:i10].size and h[k + 2:i10].max() > h[k]: return None
            if side == -1 and l[k + 2:i10].size and l[k + 2:i10].min() < l[k]: return None
    # first swing low L1 after 10:00 (down day), then first swing high H1 after it = the draw
    L1 = next((k for k in range(i10, n - 1) if (sl[k] if side == 1 else sh[k])), None)
    if L1 is None: return None
    H1 = next((k for k in range(L1 + 1, n - 1) if (sh[k] if side == 1 else sl[k])), None)
    if H1 is None or hr[H1] >= 12: return None
    tgt = h[H1] if side == 1 else l[H1]; lo1 = l[L1] if side == 1 else h[L1]
    # pullback that holds: a swing low RL after H1 that does not break L1; then MSS above the short-term high between H1 and RL
    for k in range(H1 + 1, n - 1):
        if hr[k] >= 12: return None
        if (l[k] < lo1) if side == 1 else (h[k] > lo1): return None              # broke the swing low -> other strategy / PM
        if (sl[k] if side == 1 else sh[k]) and k > H1 + 1:
            cands = [j for j in range(H1 + 1, k) if (sh[j] if side == 1 else sl[j])]          # most recent minor swing before RL
            st_hi_k = cands[-1] if cands else k - 1
            trig = h[st_hi_k] + TICK if side == 1 else l[st_hi_k] - TICK
            if (trig >= tgt) if side == 1 else (trig <= tgt): return None
            q = next((j for j in range(k + 1, n) if hr[j] < 13.5 and ((h[j] >= trig) if side == 1 else (l[j] <= trig))), None)
            if q is None: return None
            stop = l[k] - TICK if side == 1 else h[k] + TICK
            end = int(np.argmax(hr >= 13.5)) if (hr >= 13.5).any() else n
            if q >= end: return None
            return dict(side=side, E=trig, stop=stop, tgt=tgt, q=q, end=end, b=b, H1=b.ts.iloc[H1], L1=b.ts.iloc[L1], RL=b.ts.iloc[k])
    return None

def run(m):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates()
    d = d[d.date.astype(str) <= ('2026-12-31' if EXAM else '2023-12-31')]
    rows = []; rng = np.random.default_rng(11)
    for dt, x in d.groupby('date'):
        if dt in rolls or x.dow.iloc[0] > 4: continue
        t = day_trade(x)
        if t is None: continue
        b = t['b']; h, l, c, o = b.h.values, b.l.values, b.c.values, b.o.values; hr = b.hr.values; risk = abs(t['E'] - t['stop'])
        if risk <= 0: continue
        for kind, s in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
            st = t['E'] - s * risk; tg = t['E'] + s * abs(t['tgt'] - t['E'])
            xp, _, how = L.walk_stop_entry(o[:t['end']], h[:t['end']], l[:t['end']], c[:t['end']], hr[:t['end']], t['q'], s, t['E'], st, tg)
            gR = s * (xp - t['E']) / risk
            for v in ('cme', 'broker'):
                cr = L.cost_R(v, m, t['E'], risk, slips=1 if how == 'tgt' else 2)
                rows.append(dict(model='R11', cell=f'{v}/{kind}', m=m, date=dt, gross=gR, net=gR - cr, risk=risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_cme_ict('NQ'); t = day_trade(d[d.date == pd.Timestamp('2025-02-24').date()])
        print({k: v for k, v in (t or {}).items() if k != 'b'}); sys.exit()
    T = pd.concat([run('NQ'), run('ES')]); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R11-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R11 lunch macro']
    for (m, cell), gg in T.groupby(['m', 'cell']):
        s5 = gg.sort_values('net'); gg = gg.sort_values('date')
        lines.append(f"{m} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(gg) > 5 else np.nan:+.3f} maxDD {L.maxdd(gg.net.values):.0f} medrisk {gg.risk.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in gg.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
