"""R05: ICT 2022 model (specs/ict-rebuild/R05-2022-model.md). Liquidity = 15m swing high/low to the left at the window start;
sweep -> MSS (close through the last short-term swing before the sweep extreme) -> first FVG inside the displacement leg ->
limit at candle-3 edge (+1 tick toward the gap), stop beyond candle 1 (S1) or the sweep extreme (SW); target = opposite 15m
liquidity (TL) or fixed (TF: 5 ES pts / 20 NQ pts / 10 pips). EXAMPLE=1 prints NQ 2022-02-03. LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'

def swings(h, l):
    n = len(h); sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:]); sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:]); return sh, sl

def day_setups(x, w0, w1, tick, fixed_t, mid_open, stop_cell, tgt_cell, need_premium, mss='C'):
    """x: one NY date of bars (1m or 5m) incl. the overnight part; window [w0,w1) NY hours. Returns first trade or None."""
    h, l, c, o, hr = x.h.values, x.l.values, x.c.values, x.o.values, x.hr.values
    # 15m bars from the same day's data up to the window start
    pre = x[x.hr < w0]
    if len(pre) < 20: return None
    q15 = pre.set_index(pd.to_datetime(pre.ts)).resample('15min').agg({'h': 'max', 'l': 'min'}).dropna()
    H15, L15 = q15.h.values, q15.l.values
    sh15, sl15 = swings(H15, L15)
    ih = np.where(sh15[:-1])[0]; il = np.where(sl15[:-1])[0]            # swing confirmed by a later 15m bar before w0
    if not len(ih) or not len(il): return None
    BSL = H15[ih[-1]]; SSL = L15[il[-1]]                               # "look left for the first swing high/low"
    sh1, sl1 = swings(h, l); idx = np.where((hr >= w0) & (hr < w1))[0]
    if not len(idx): return None
    for side in (-1, 1):                                                # evaluate both; take the earlier fill
        pass
    best = None
    for side in (-1, 1):
        lvl = BSL if side == -1 else SSL
        sw = next((k for k in idx if ((h[k] > lvl) if side == -1 else (l[k] < lvl))), None)
        if sw is None: continue
        if need_premium and mid_open is not None and ((h[sw] <= mid_open) if side == -1 else (l[sw] >= mid_open)): continue
        ext_k = sw
        for k in range(sw, idx[-1] + 1):
            if (h[k] > h[ext_k]) if side == -1 else (l[k] < l[ext_k]): ext_k = k
            # short-term swing opposite to the run, formed before the current extreme
            prev = [j for j in range(max(0, ext_k - 60), ext_k) if (sl1[j] if side == -1 else sh1[j])]
            if not prev: continue
            st = l[prev[-1]] if side == -1 else h[prev[-1]]
            px = (c[k] if mss == 'C' else l[k]) if side == -1 else (c[k] if mss == 'C' else h[k])
            if (px < st) if side == -1 else (px > st):                   # MSS: close (C) or wick (W) through the short-term swing
                fv = None
                for j in range(ext_k + 2, k + 1):                         # first FVG inside the displacement leg
                    if side == -1 and l[j - 2] > h[j]: fv = j; break
                    if side == 1 and h[j - 2] < l[j]: fv = j; break
                if fv is None: break                                      # "no fair value gap ... you don't have a trade"
                E = h[fv] + tick if side == -1 else l[fv] - tick
                stop = (h[fv - 2] + tick if side == -1 else l[fv - 2] - tick) if stop_cell == 'S1' else (h[ext_k] + tick if side == -1 else l[ext_k] - tick)
                lo_, hi_ = (c[k], h[ext_k]) if side == -1 else (l[ext_k], c[k])
                if need_premium and ((E < (lo_ + hi_) / 2) if side == -1 else (E > (lo_ + hi_) / 2)): break   # cell P: premium of the leg (Ep 40)
                if tgt_cell == 'TL':                                      # ERL: the day's opposite extreme since 00:00 NY at MSS time
                    tgt = l[:k + 1].min() if side == -1 else h[:k + 1].max()
                    if (tgt >= E) if side == -1 else (tgt <= E): break
                else:
                    if fixed_t is None: break
                    tgt = E + side * fixed_t
                q = next((j for j in range(k + 1, len(h)) if hr[j] < w1 + 1 and ((h[j] >= E) if side == -1 else (l[j] <= E))
                          and not ((l[k + 1:j].min() <= tgt) if side == -1 and j > k + 1 else ((h[k + 1:j].max() >= tgt) if j > k + 1 else False))), None)
                if q is None or hr[q] >= w1 + 1: break
                cand = dict(side=side, E=E, stop=stop, tgt=tgt, q=q, sweep=x.ts.iloc[sw], mss=x.ts.iloc[k], fvg_c2=x.ts.iloc[fv - 1], liq=lvl, st=st, st_t=x.ts.iloc[prev[-1]], ext=(h[ext_k] if side == -1 else l[ext_k]), ext_t=x.ts.iloc[ext_k], gap=((h[fv], l[fv - 2]) if side == -1 else (h[fv - 2], l[fv])))
                if best is None or q < best['q']: best = cand
                break
    return best

def run(m, d, venue, tick, fixed_t, windows, learn_end, rolls=set()):
    d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else learn_end)]
    g = dict(tuple(d.groupby('date'))); rows = []; rng = np.random.default_rng(5)
    for dt in sorted(g):
        if dt in rolls: continue
        x = g[dt].reset_index(drop=True)
        if x.dow.iloc[0] > 4: continue
        mo = x[x.hr >= 0].o.values[0]
        for wname, (w0, w1, flat) in windows.items():
            for sc in ('S1', 'SW'):
                for tc in ('TL', 'TF'):
                    for prem, ms in ((True, 'C'), (False, 'C'), (False, 'W')):
                        t = day_setups(x, w0, w1, tick, fixed_t, mo, sc, tc, prem, ms)
                        if t is None: continue
                        h, l, c, hr = x.h.values, x.l.values, x.c.values, x.hr.values
                        end = int(np.argmax(hr >= flat)) if (hr >= flat).any() else len(h)
                        risk = abs(t['E'] - t['stop'])
                        if risk <= 0 or t['q'] >= end: continue
                        for kind, s in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
                            st = t['E'] - s * risk; tg = t['E'] + s * abs(t['tgt'] - t['E'])
                            xp, xq, how = L.walk(h[:end], l[:end], c[:end], hr[:end], t['q'], s, t['E'], st, tg)
                            gR = s * (xp - t['E']) / risk
                            for v in venue:
                                cr = L.cost_R(v, m, t['E'], risk, slips=0 if how == 'tgt' else 1)
                                rows.append(dict(model='R05', cell=f"{wname}/{sc}/{tc}/{'P' if prem else 'any'}{ms}/{v}/{kind}", m=m, date=dt, gross=gR, net=gR - cr, risk=risk))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    if os.environ.get('EXAMPLE') == '1':
        d = L.load_cme_ict('NQ')
        for dt in ('2022-02-03', '2022-02-08'):
            x = d[d.date == pd.Timestamp(dt).date()].reset_index(drop=True); mo = x.o.values[0]
            for sc in ('S1', 'SW'): print(dt, sc, [day_setups(x, 8.5, 11, 0.25, 20, mo, sc, 'TL', False, ms) for ms in ('C', 'W')])
        sys.exit()
    T = []
    if os.environ.get('CRYPTO') == '1':
        for m in ('BTCUSDT', 'ETHUSDT'):
            d = L.load_crypto(m, '5m'); tick = 0.1 if m == 'BTCUSDT' else 0.01
            T.append(run(m, d, ('broker',), tick, None, {'LO': (2, 5, 11), 'NY': (7, 10, 11)}, L.CRYPTO_LEARN_END)); print(m, flush=True)
        T = pd.concat(T); T = T[T.cell.str.contains('/TL/')]
        out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R05-results-crypto' + ('-exam' if EXAM else '-learn') + '.md')
        lines = ['# R05 crypto']
        for cell, gg in T.groupby('cell'):
            s5 = gg.sort_values('net'); lines.append(f"{cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(gg.sort_values('date').net.values):.0f} medrisk% {100*(gg.risk/1).median():.4g}")
        txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt); sys.exit()
    for m in ('NQ', 'ES'):
        d = L.load_cme_ict(m)
        T.append(run(m, d, ('cme', 'broker'), 0.25, 20 if m == 'NQ' else 5, {'AM': (8.5, 11, 16), 'PM': (13.5, 15.5, 16)}, L.CME_LEARN_END, L.ict_roll_dates()))
        print(m, flush=True)
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); p = L.pip_of(m)
        T.append(run(m, d, ('broker_fx',), p, 10 * p, {'LO': (2, 5, 11), 'NY': (7, 10, 11)}, L.FX_LEARN_END)); print(m, flush=True)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R05-results' + ('-exam' if EXAM else '-learn') + '.md')
    T['grp'] = np.where(T.m.isin(['NQ', 'ES']), T.m, 'FX')
    lines = ['# R05 ICT 2022 model — learn' if not EXAM else '# R05 exam']
    for (grp, cell), gg in T.groupby(['grp', 'cell']):
        s5 = gg.sort_values('net')
        lines.append(f"{grp} {cell}: n={len(gg)} gross {gg.gross.mean():+.3f} net {gg.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(gg) > 5 else np.nan:+.3f} maxDD {L.maxdd(gg.sort_values('date').net.values):.0f} medrisk {gg.risk.median():.4g}")
    txt = '\n'.join(lines); open(out, 'w', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
    print(txt)
