"""Y02: a social-media account's 'IV walls' (IG short). Pre-registered 2026-10-11 (specs/yt/Y02-iv-walls.md). LEARN unless EXAM=1.
His words: expected range from "historical volatility plus contract pricing"; "90% chance we stay within this range"; "when we test
the IV walls, we take our entry, stops above and below, and trade the reversal". Range formula, stop size and target NOT given ->
the stop/target grid below is MINE (labelled), so a fade result is NOT a verdict on his guide."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; Z = 1.645; CB = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/cboe/'

def vol_index(name):
    v = pd.read_csv(CB + f'{name}_History.csv'); v.index = pd.to_datetime(v.DATE).dt.date; return v.CLOSE

def run(m, volname):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates(); vi = vol_index(volname)
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.CME_LEARN_END)]
    r = d[(d.hr >= 9.5) & (d.hr < 16)]
    days = r.groupby('date').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('o', 'size'))
    days = days[days.n >= 300]; ret = np.log(days.c).diff()
    hv = ret.rolling(20).std().shift(1)                                    # known before today's open
    vprev = pd.Series(vi).reindex(days.index).ffill().shift(1) / 100 / np.sqrt(252)
    g = dict(tuple(r.groupby('date'))); rows = []; claim = []; rng = np.random.default_rng(22)
    for dt in days.index:
        if dt in rolls: continue
        D = days.loc[dt]
        for src, sig in (('IV', vprev.get(dt)), ('HV', hv.get(dt))):
            if sig is None or not np.isfinite(sig): continue
            up, lo = D.o * (1 + Z * sig), D.o * (1 - Z * sig)
            claim.append(dict(src=src, inside=(D.h <= up) and (D.l >= lo), close_in=lo <= D.c <= up, touch_up=D.h >= up, touch_lo=D.l <= lo))
            x = g[dt]; h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
            k = next((i for i in range(len(h)) if hr[i] < 15.5 and (h[i] >= up or l[i] <= lo)), None)
            if k is None: continue
            side = -1 if h[k] >= up else 1
            if h[k] >= up and l[k] <= lo: continue                            # both walls in one bar: skip
            E = up if side == -1 else lo; half = abs(E - D.o)
            for ks in (0.10, 0.25):
                risk = ks * half
                for tn in ('OPEN', '2R'):
                    for kind, s in (('FADE', side), ('BREAK', -side), ('COIN', rng.choice([1, -1]))):
                        tgt_dist = half if tn == 'OPEN' else 2 * risk
                        xp, _, how = L.walk(h, l, c, hr, k, s, E, E - s * risk, E + s * tgt_dist)
                        gR = s * (xp - E) / risk
                        for v in ('cme', 'broker'):
                            cr = L.cost_R(v, m, E, risk, slips=0 if how == 'tgt' else 1)
                            rows.append(dict(m=m, cell=f'{src}/S{int(ks*100)}/{tn}/{v}/{kind}', date=dt, gross=gR, net=gR - cr, riskpts=risk))
    return pd.DataFrame(rows), pd.DataFrame(claim)

if __name__ == '__main__':
    out = []; lines = ['# Y02 IV walls' + (' EXAM' if EXAM else ' learn')]
    for m, vn in (('ES', 'VIX'), ('NQ', 'VXN')):
        T, C = run(m, vn); T['yr'] = pd.to_datetime(T.date).dt.year; out.append(T)
        for src, cc in C.groupby('src'):
            lines.append(f"{m} {src} 90%-range claim: whole day inside {cc.inside.mean():.1%} | close inside {cc.close_in.mean():.1%} | upper wall touched {cc.touch_up.mean():.1%} lower {cc.touch_lo.mean():.1%} (n={len(cc)})")
        for cell, g in T.groupby('cell'):
            g = g.sort_values('date'); s5 = g.sort_values('net')
            lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk {g.riskpts.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    p = os.path.join(os.path.dirname(__file__), '..', 'specs', 'yt', 'Y02-results' + ('-exam' if EXAM else '-learn') + '.md')
    txt = chr(10).join(lines); open(p, 'w', encoding='utf-8').write(txt + chr(10)); pd.concat(out).to_csv(p.replace('.md', '.csv'), index=False); print(txt)
