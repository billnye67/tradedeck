"""R03: 00:00 GMT entry after a daily OB reaction, ADR5 stop (specs/ict-rebuild/R03-midnight-gmt-adr.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; XH = float(os.environ.get('XH', '17'))

def run(m, d, venue, pip):
    D = L.daily_fx(d); D['adr5'] = (D.h - D.l).rolling(5).mean()
    D['ob'] = L.daily_ob_bias(D, 40); obs = L.daily_ob_list(D)
    hi20 = D.h.rolling(20).max().shift(1).values; lo20 = D.l.rolling(20).min().shift(1).values
    ts = pd.to_datetime(d.ts.values)                         # naive UTC
    d = d.assign(utc=ts); idx0 = np.where((ts.hour == 0) & (ts.minute == 0))[0]
    h, l, c, hr = d.h.values, d.l.values, d.c.values, d.hr.values; o = d.o.values
    fxd = L.daily_fx  # noqa
    # map each 00:00 UTC bar to the FX day it belongs to (FX day closes 17:00 NY after it)
    Dpos = {k: i for i, k in enumerate(D.index)}; rows = []; rng = np.random.default_rng(3)
    dates = d.date.values
    for i0 in idx0:
        nyd = dates[i0]; hrv = hr[i0]
        fxday = (pd.Timestamp(nyd) + pd.Timedelta(days=1)).date() if hrv >= 17 else nyd
        if fxday not in Dpos: continue
        t = Dpos[fxday]; y = t - 1
        if y < 25: continue
        bias = {}
        bias['OB'] = D.ob.iloc[y]
        if os.environ.get('PLACEBO'):
            col = int(np.sign(D.c.iloc[y] - D.o.iloc[y])); bias = {'COLOR': col, 'COLOR_OBDAYS': col if D.ob.iloc[y] != 0 else 0, 'ANTI_OB': -D.ob.iloc[y]}
        Y = D.iloc[y]; ts_ = 0
        if Y.h > hi20[y] and Y.c < hi20[y]: ts_ = -1
        if Y.l < lo20[y] and Y.c > lo20[y]: ts_ = 1 if ts_ == 0 else 0
        if not os.environ.get('PLACEBO'): bias['OBTS'] = bias['OB'] if bias['OB'] != 0 else ts_
        adr = D.adr5.iloc[y]
        if not adr == adr or adr <= 0: continue
        dow = pd.Timestamp(fxday).dayofweek
        end_day = i0 + int(np.argmax((dates[i0:] == fxday) & (hr[i0:] >= XH)) if ((dates[i0:] == fxday) & (hr[i0:] >= XH)).any() else 0)
        if end_day <= i0: continue
        for bc, s in bias.items():
            if s == 0: continue
            for dc in ('ALL', 'MTW'):
                if dc == 'MTW' and dow > 2: continue
                for xc in (('DAY', 'HTF') if XH == 17 else ('DAY',)):
                    res = []; csr = []
                    E1 = o[i0]; E2 = E1 - s * 20 * pip
                    q2 = next((q for q in range(i0, end_day) if ((l[q] <= E2) if s == 1 else (h[q] >= E2))), None)
                    HALF = os.environ.get('HALF', 'B')
                    for E, q in (((E1, i0), (E2, q2)) if HALF == 'B' else (((E1, i0),) if HALF == 'M' else ((E2, q2),))):
                        if q is None: continue
                        stop = E - s * adr
                        if xc == 'DAY': lim = end_day; tgt = None
                        else:
                            lv = L.live_obs(D, obs, t, 40); cands = [op for (_, sd, op, _) in lv if sd == -s and (op - E) * s > 0]
                            cands.append(hi20[t] if s == 1 else lo20[t]); cands = [x for x in cands if (x - E) * s > 0]
                            tgt = min(cands, key=lambda x: abs(x - E)) if cands else None
                            lim = min(len(h), i0 + 20 * 288)
                        xp, xq, how = L.walk(h[:lim], l[:lim], c[:lim], hr[:lim], q, s, E, stop, tgt)
                        nights = L.rollovers(ts[q], ts[min(xq + 1, len(ts) - 1)])
                        cr = L.cost_R('broker_fx', m, E, adr, nights=nights) if venue == 'fx' else L.cost_R('broker_cmdty', m, E, adr, nights=nights, slips=1)
                        res.append((s * (xp - E) / adr, cr))
                        cs = rng.choice([1, -1]); cx = L.walk(h[:lim], l[:lim], c[:lim], hr[:lim], q, cs, E, E - cs * adr, None if tgt is None else E + cs * abs(tgt - E))
                        csr.append(cs * (cx[0] - E) / adr - cr)
                    if not res: continue
                    # FIXED 2026-10-09: each half is HALF the position; an unfilled half contributes 0 (the old np.mean over filled
                    # halves counted a lone market half at full size = false pass). R = ADR on the full position.
                    w = 0.5 if os.environ.get('HALF', 'B') == 'B' else 1.0
                    g = w * sum(r[0] for r in res); n_ = w * sum(r[0] - r[1] for r in res); csr = [w * sum(csr) / max(1, len(csr)) * len(csr)]
                    rows.append(dict(model='R03', cell=f'{bc}/{dc}/{xc}', m=m, date=fxday, gross=g, net=n_, ctrl=np.mean(csr), halves=len(res)))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    T = []
    for m in L.FX_PAIRS:
        d = L.load_fx(m, '5m'); d = d[pd.to_datetime(d.date).dt.year <= (2026 if EXAM else L.FX_LEARN_END)].reset_index(drop=True)
        T.append(run(m, d, 'fx', L.pip_of(m))); print(m, flush=True)
    import sqlite3
    c = sqlite3.connect(L.DB)
    g = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='dukascopy' and market='XAUUSD' and interval='5m' order by t", c)
    if EXAM:
        b = pd.read_sql("select timestamp t,open o,high h,low l,close c from candles where source='polygon' and market='XAUUSD' and interval='5m' order by t", c)
        g = pd.concat([g, b[b.t > g.t.max()]]).reset_index(drop=True)
    g = L._ny(g); g = g[pd.to_datetime(g.date).dt.year <= (2026 if EXAM else 2023)].reset_index(drop=True)
    G = run('XAUUSD', g, 'gold', 0.1); G['m'] = 'XAU'; T.append(G)
    T = pd.concat(T)
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', (('R03b-placebo' + os.environ.get('HALF', '') if os.environ.get('PLACEBO') else 'R03b-results' + os.environ.get('HALF', '')) if XH != 17 else 'R03-results') + ('-exam' if EXAM else '-learn') + '.md')
    L.report(T, 'R03 00:00 GMT after daily OB reaction, ADR5 stop', L.FX_LEARN_END if not EXAM else L.FX_LEARN_END, out, exam=EXAM)
    lines = ['\n## Control and ex-top-5']
    for (cell, mk), gg in T.groupby(['cell', T.m == 'XAU']):
        gg = gg.sort_values('net'); lines.append(f"{'GOLD' if mk else 'FX'} {cell}: n={len(gg)} net {gg.net.mean():+.3f}R ctrl {gg.ctrl.mean():+.3f}R ex-top5 {gg.net.iloc[:-5].mean():+.3f}R")
    txt = '\n'.join(lines); print(txt); open(out, 'a', encoding='utf-8').write(txt + '\n'); T.to_csv(out.replace('.md', '.csv'), index=False)
