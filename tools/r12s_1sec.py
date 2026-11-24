"""R12 settled on 1-second data: the 07:00-09:30 relative-equal liquidity rule (L33) with the entry/stop resolved second by second
inside 09:25-10:35 (Databento ohlcv-1s, ICT-rolled), continued on 1-minute bars after 10:35. Learn era 2020-2023 only."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r12_gauntlet as R
TICK = 0.25; S1 = os.environ['LOCALAPPDATA'] + '/TradeDeck/Data/databento/nq1s/'

def pools(x, tol):
    w = x[(x.hr >= 7) & (x.hr < 16)].reset_index(drop=True)
    h, l, hr = w.h.values, w.l.values, w.hr.values
    pre = np.where(hr < 9.5)[0]; i930 = int(np.argmax(hr >= 9.5))
    sh = [k for k in pre[1:] if h[k] >= h[k - 1] and (k == i930 - 1 or h[k] >= h[k + 1])]
    sl = [k for k in pre[1:] if l[k] <= l[k - 1] and (k == i930 - 1 or l[k] <= l[k + 1])]
    return R.eq_pairs([l[k] for k in sl], sl, tol, 'low'), R.eq_pairs([h[k] for k in sh], sh, tol, 'high'), w

def day(dt, x, tol, RR):
    f = S1 + pd.Timestamp(dt).strftime('%Y%m%d') + '.parquet'
    if not os.path.exists(f): return None
    s = pd.read_parquet(f, columns=['ts_event', 'high', 'low', 'close'])
    if len(s) < 500: return None
    s['ts'] = pd.to_datetime(s.ts_event).dt.tz_convert('America/New_York'); s = s[s.ts >= s.ts.iloc[0].normalize() + pd.Timedelta(hours=9, minutes=30)]
    if len(s) < 500: return None
    REL, REH, w = pools(x, tol)
    if not REL and not REH: return None
    H, Lo, C = s.high.values, s.low.values, s.close.values; T = s.ts.values; n = len(H)
    lim = int(np.searchsorted(T, np.datetime64((pd.Timestamp(dt) + pd.Timedelta(hours=10, minutes=30)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)))) if False else None
    tt = s.ts.dt.hour.values + s.ts.dt.minute.values / 60
    for j in range(n):
        if tt[j] >= 10.5: return None
        for side, pl, opp in ((1, REL, REH), (-1, REH, REL)):
            sw = [(ib, far, near) for (ib, far, near) in pl if ((Lo[j] < far) if side == 1 else (H[j] > far))]
            if not sw: continue
            ib, far, near = max(sw)
            ext = Lo[j] if side == 1 else H[j]
            for r in range(j + 1, min(n, j + 1800)):                       # return within 30 minutes (same as the 1m version)
                if (H[r] >= near) if side == 1 else (Lo[r] <= near):
                    E = near; stop = ext - TICK if side == 1 else ext + TICK; risk = abs(E - stop)
                    tg = sorted([(ib2, f2) for (ib2, f2, n2) in opp if ((f2 - E) * side >= RR * risk)], reverse=True)
                    if not tg or risk <= 0: return None
                    return dict(side=side, E=E, stop=stop, tgt=tg[0][1], r=r, s=s, w=w, risk=risk)
                ext = min(ext, Lo[r]) if side == 1 else max(ext, H[r])     # the sweep keeps extending until the return
            return None
    return None

def outcome(t, s_side):
    """walk on 1s from the fill second to the end of the 1s window, then on 1m to 16:00. Stop-first within a second."""
    s = t['s']; H, Lo, C = s.high.values, s.low.values, s.close.values; E = t['E']; risk = t['risk']
    st = E - s_side * risk; tg = E + s_side * abs(t['tgt'] - E)
    for q in range(t['r'] + 1, len(H)):
        if (Lo[q] <= st) if s_side == 1 else (H[q] >= st): return -1.0
        if (H[q] >= tg) if s_side == 1 else (Lo[q] <= tg): return abs(tg - E) / risk
    w = t['w']; last = s.ts.iloc[-1]; m = w[w.ts > last]
    h, l, c, hr = m.h.values, m.l.values, m.c.values, m.hr.values
    if not len(h): return s_side * (C[-1] - E) / risk
    end = int(np.argmax(hr >= 16)) if (hr >= 16).any() else len(h)
    xp, _, how = L.walk(h[:end], l[:end], c[:end], hr[:end], 0, s_side, E, st, tg)
    return s_side * (xp - E) / risk

if __name__ == '__main__':
    d = L.load_cme_ict('NQ'); rolls = L.ict_roll_dates(); d = d[pd.to_datetime(d.date).dt.year <= 2023]
    rows = []; rng = np.random.default_rng(12)
    for dt, x in d.groupby('date'):
        if dt in rolls or x.dow.iloc[0] > 4: continue
        for tol in (2, 4, 8):
            for RR in (1.0, 2.0):
                t = day(dt, x, tol, RR)
                if t is None: continue
                for kind, sd in (('ICT', t['side']), ('OPP', -t['side']), ('COIN', rng.choice([1, -1]))):
                    g = outcome(t, sd)
                    cr = L.cost_R('cme', 'NQ', t['E'], t['risk'], slips=2)
                    rows.append(dict(cell=f'tol{tol}/RR{RR:.0f}/{kind}', date=dt, gross=g, net=g - cr, risk=t['risk']))
    T = pd.DataFrame(rows); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R12S-results-learn.md'); lines = ['# R12 on 1-second NQ (learn 2020-2023, CME costs)']
    for cell, g in T.groupby('cell'):
        g = g.sort_values('date'); s5 = g.sort_values('net'); g4 = g[g.risk >= 4]
        lines.append(f"{cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean():+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk {g.risk.median():.2f} | risk>=4 n={len(g4)} net {g4.net.mean():+.3f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
