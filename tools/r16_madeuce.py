"""R16: Ma Deuce (specs/ict-rebuild/R16-ma-deuce.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; TICK = 0.25

def daily_context(D, t, p):
    """bias from unfilled daily BISI/SIBI (last 40 sessions) containing p in its premium/discount half. Returns (side, CE, LQ) or None."""
    o, h, l, c = D.o.values, D.h.values, D.l.values, D.c.values
    for k in range(t - 2, max(1, t - 40), -1):                         # most recent first; gap needs candles k-1, k, k+1 <= t-1
        if k + 1 > t - 1: continue
        if l[k + 1] > h[k - 1]:                                        # BISI [h[k-1], l[k+1]]
            bot, top = h[k - 1], l[k + 1]
            if (c[k + 2:t] < bot).any(): continue
            if (bot + top) / 2 <= p <= top: return (-1, (bot + top) / 2, bot + 0.25 * (top - bot))
        if h[k + 1] < l[k - 1]:                                        # SIBI [h[k+1], l[k-1]]
            bot, top = h[k + 1], l[k - 1]
            if (c[k + 2:t] > top).any(): continue
            if bot <= p <= (bot + top) / 2: return (1, (bot + top) / 2, top - 0.25 * (top - bot))
    return None

def intraday(x, side, tgt, CE, stopcell):
    """R16b: overnight-or-later 1m FVG in the premium (discount) half, inverted (latest outside close beyond it), retested 08:00-10:00."""
    w = x[x.hr < 16].reset_index(drop=True); h, l, c, hr = w.h.values, w.l.values, w.c.values, w.hr.values; n = len(h)
    gaps = []
    for g in range(2, n):
        if hr[g] >= 10: break
        if side == -1 and l[g] > h[g - 2] and h[g - 2] >= CE: gaps.append((g, h[g - 2], l[g]))
        if side == 1 and h[g] < l[g - 2] and l[g - 2] <= CE: gaps.append((g, h[g], l[g - 2]))
    if not gaps: return None
    i7 = int(np.argmax(hr >= 7)) if (hr >= 7).any() else n
    for k in range(1, n):
        if hr[k] < 8: continue
        if hr[k] >= 10: return None
        best = None
        for g, bot, top in gaps:
            if g >= k - 1: continue
            outs = [j for j in range(g + 1, k) if c[j] < bot or c[j] > top]
            if not outs: continue
            j = outs[-1]
            if side == -1 and not (c[j] < bot and c[k - 1] < bot and h[k] >= bot - TICK): continue
            if side == 1 and not (c[j] > top and c[k - 1] > top and l[k] <= top + TICK): continue
            E = bot - TICK if side == -1 else top + TICK
            if (tgt - E) * side <= 0: continue
            if best is None or (E < best[0] if side == -1 else E > best[0]): best = (E, g, bot, top)
        if best is None: continue
        E, g, bot, top = best
        if (l[i7:k].min() <= tgt) if side == -1 else (h[i7:k].max() >= tgt): return None
        if stopcell == 'G': stop = top + TICK if side == -1 else bot - TICK
        else:
            held = [j for j in range(g + 1, k) if (c[j] > top if side == -1 else c[j] < bot)]
            a = held[-1] if held else g
            stop = h[a:k].max() + TICK if side == -1 else l[a:k].min() - TICK
        if (stop - E) * side >= 0: return None
        end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else n
        return dict(E=E, stop=stop, q=k, w=w, end=end, fill_at_k=True)
    return None

def run(m):
    d = L.load_cme_ict(m); rolls = L.ict_roll_dates(); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else 2023)]
    D = L.cme_session_daily(d); Dpos = {k: i for i, k in enumerate(D.index)}; rows = []; rng = np.random.default_rng(16)
    for dt, x in d.groupby('date'):
        if dt in rolls or x.dow.iloc[0] > 4 or dt not in Dpos: continue
        p0 = x[x.hr >= 7]
        if not len(p0): continue
        ctx = daily_context(D, Dpos[dt], p0.o.values[0])
        if ctx is None: continue
        side, CE, LQ = ctx
        for tn, tgt, sc in (('CE', CE, 'G'), ('LQ', LQ, 'G'), ('CE', CE, 'H'), ('LQ', LQ, 'H')):
            t = intraday(x, side, tgt, CE, sc); tn2 = tn + sc
            if t is None: continue
            w = t['w']; h, l, c, hr = w.h.values, w.l.values, w.c.values, w.hr.values; risk = abs(t['E'] - t['stop'])
            for kind, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
                xp, _, how = L.walk(h[:t['end']], l[:t['end']], c[:t['end']], hr[:t['end']], t['q'], s, t['E'], t['E'] - s * risk, t['E'] + s * abs(tgt - t['E']))
                gR = s * (xp - t['E']) / risk
                for v in ('cme', 'broker'):
                    cr = L.cost_R(v, m, t['E'], risk, slips=0 if how == 'tgt' else 1)
                    rows.append(dict(cell=f'{tn2}/{v}/{kind}', m=m, date=dt, gross=gR, net=gR - cr, risk=risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = pd.concat([run('NQ'), run('ES')]); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R16-results' + ('-exam' if EXAM else '-learn') + '.md'); lines = ['# R16 Ma Deuce']
    for (m, cell), g in T.groupby(['m', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(g) > 5 else np.nan:+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk {g.risk.median():.2f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
