"""Y03: QuantLab 15-minute opening range breakout (specs/yt/Y03-orb.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r17_htf as H
EXAM = os.environ.get('EXAM') == '1'

def bars(m):
    if m in ('NQ', 'ES'): d = L.load_cme_ict(m); rolls = L.ict_roll_dates()
    else:
        import sqlite3; c = sqlite3.connect(L.DB)
        d = L._ny(pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='metals' and market='XAUUSD' and interval='5m' order by t", c)); rolls = set()
    d['ts'] = pd.to_datetime(d.ts); return d, rolls

def run(m, venue):
    d, rolls = bars(m); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else 2023)]
    rows = []; rng = np.random.default_rng(3)
    for dt, x in d.groupby('date'):
        if dt in rolls or pd.Timestamp(dt).dayofweek > 4: continue
        x = x[(x.hr >= 9.5) & (x.hr < 16)].set_index('ts')
        if len(x) < (300 if m != 'XAUUSD' else 60): continue
        orr = x[x.hr < 9.75]; hi, lo = orr.h.max(), orr.l.min()
        rest = x[x.hr >= 9.75]
        for tf in ((['1min', '5min', '15min']) if m != 'XAUUSD' else ['5min', '15min']):
            b = rest.resample(tf, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'hr': 'first'}).dropna()
            h, l, c, hr = b.h.values, b.l.values, b.c.values, b.hr.values
            k = next((i for i in range(len(c)) if c[i] > hi or c[i] < lo), None)
            if k is None or k + 1 >= len(c): continue
            side = 1 if c[k] > hi else -1; E = c[k]; stop = lo if side == 1 else hi; risk = abs(E - stop)
            if risk <= 0: continue
            for kind, s in (('ORB', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                xp, _, how = L.walk(h, l, c, hr, k + 1, s, E, E - s * risk, E + s * 2 * risk)
                gR = s * (xp - E) / risk
                for v in venue:
                    cr = L.cost_R(v, 'XAUUSD' if m == 'XAUUSD' else m, E, risk, slips=1 if how == 'tgt' else 2)
                    rows.append(dict(m=m, cell=f'{tf}/{v}/{kind}', date=dt, gross=gR, net=gR - cr, risk=risk, riskpct=risk / E * 100))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = pd.concat([run('NQ', ('cme', 'broker')), run('ES', ('cme', 'broker')), run('XAUUSD', ('broker_cmdty',))]); T['yr'] = pd.to_datetime(T.date).dt.year
    if EXAM: T = T[T.yr >= 2024]
    lines = ['# Y03 ORB' + (' EXAM' if EXAM else ' learn')]
    for (m, cell), g in T.groupby(['m', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} totalR {g.net.sum():+.0f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk {g.risk.median():.2f} ({g.riskpct.median():.2f}%) | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    p = os.path.join(os.path.dirname(__file__), '..', 'specs', 'yt', 'Y03-results' + ('-exam' if EXAM else '-learn') + '.md')
    txt = chr(10).join(lines); open(p, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(p.replace('.md', '.csv'), index=False); print(txt)
