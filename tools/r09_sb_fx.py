"""R09: Silver Bullet (x20 rules) on FX 5m + gold 5m (specs/ict-rebuild/R09-silver-bullet-fx-gold.md). LEARN unless EXAM=1."""
import os, sys, sqlite3, numpy as np, pandas as pd, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'
src = open(os.path.join(os.path.dirname(__file__), 'x20_sb.py'), encoding='utf-8').read()
src = src.replace("if len(w) < (100 if tf == 1 else 20): return None", "if len(w) < 20: return None")
ns = {'__file__': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'x20_sb.py')}; exec(compile(src.split("if __name__ == '__main__' and sys.argv[1] == 'check'")[0], 'x20mod', 'exec'), ns)

def run(m, d, venue, pip, frame, take, learn_end):
    ns['FRAME'][m] = frame; ns['TAKE'][m] = take; ns['TICK'] = pip / 10 if venue == 'broker_fx' else 0.01
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else learn_end)]
    days = sorted(z for z in set(d.date) if pd.Timestamp(z).dayofweek < 5); by = dict(tuple(d.groupby('date'))); rows = []
    random.seed(9)
    for i, day in enumerate(days[1:], 1):
        x = by[day]; dp = by[days[i - 1]]
        for w0 in (3, 10, 14):
            for cell, want in (('ICT', 0), ('COIN', random.choice((-1, 1)))):
                v = ns['sb_window'](x, dp, w0, m, 1, want)
                if not v: continue
                R = abs(v['entry'] - v['stop'])
                if R <= 0: continue
                for tn, tgt in (('T10', v['take']), ('Tdraw', v['draw'])):
                    r = ns['outcome'](v, tgt)
                    cr = L.cost_R(venue, m, v['entry'], R, slips=1) if venue != 'broker_fx' else L.cost_R(venue, m, v['entry'], R)
                    rows.append(dict(model='R09', cell=f'{w0}h/{tn}/{cell}', m=m, date=day, gross=r, net=r - cr, Rp=R / pip))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        p = L.pip_of(m); d = L.load_fx(m, '5m'); T.append(run(m, d, 'broker_fx', p, 15 * p, 10 * p, L.FX_LEARN_END)); print(m, flush=True)
    c = sqlite3.connect(L.DB)
    g = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
    g = L._ny(pd.concat([g, b[b.t > g.t.max()]]).reset_index(drop=True))
    G = run('XAUUSD', g, 'broker_cmdty', 1.0, 15.0, 10.0, 2023); T.append(G)
    T = pd.concat(T); T['grp'] = np.where(T.m == 'XAUUSD', 'GOLD', 'FX')
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R09-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R09 Silver Bullet FX + gold']
    for (grp, cell), gg in T.groupby(['grp', 'cell']):
        s5 = gg.sort_values('net'); lines.append(f"{grp} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(gg.sort_values('date').net.values):.0f} medR {gg.Rp.median():.1f}")
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
