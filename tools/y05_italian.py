"""Y05 (a social-media account) reel (specs/yt/LOG.md), rules as on his slides, declared 2026-10-14 before any run.
Broker time = GMT+2/+3 = New York + 7h.
S1 ORB NQ: range 09:30-09:45 NY; 09:45-15:00 first 5m CLOSE beyond -> enter at that close; stop = other side +/- 0.25 x ATR14(5m);
   target = breakout level +/- 2 x range; flat 15:55.
S2 Daily long NQ/ES: buy 01:05 broker (18:05 NY prior evening), sell 23:50 broker (16:50 NY). No stop/target. Every weekday session.
S3 Turnaround Tuesday: Dow not in our data -> run on NQ and ES (DIFFERENT MARKET, labelled). Sunday 18:05 NY: if Friday close < SMA25
   of daily closes, buy; sell Tuesday 16:50 NY.
S4 Morning range USDJPY: range 20:00-23:00 NY (03-06 broker); at 23:00 buy stop at high / sell stop at low, OCO; stop = other side;
   no target; close 11:00 NY (18:00 broker). Honest fills: a bar that opens beyond the level fills at the open; a bar that trades
   both sides = filled and stopped (-1R). Commenter variants (info only, not his rules): fixed target 0.5R and 5R.
Gate (R strategies): n>=80, net>0, ex-top-5>0, maxDD<=30R, beats coin and opposite. No-stop strategies (S2/S3): net bp/trade after
costs, profit factor, years. Learn: CME 2020-23, FX 2012-19. Exam (--exam) only for learn passes."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
rng = np.random.default_rng(5); ROLLS = L.ict_roll_dates(); EXAM = '--exam' in sys.argv
ONLY = [a for a in sys.argv[1:] if a.startswith('S')]


def gate(lbl, T, col='net'):
    T = T.sort_values('date'); x = T[col].values
    if not len(x):
        print(f'  {lbl}: no trades'); return
    s5 = np.sort(x)[:-5].mean() if len(x) > 5 else np.nan
    yr = T.groupby(pd.to_datetime(T.date).dt.year)[col].sum()
    print(f"  {lbl:30s} n={len(x):5d} win {100*(x>0).mean():3.0f}% net {x.mean():+.3f}R ex5 {s5:+.3f} maxDD {L.maxdd(x):5.1f}R | "
          + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()), flush=True)


def era_ok(dt, learn_end):
    y = pd.Timestamp(dt).year
    return y >= learn_end + 1 if EXAM else y <= learn_end


def want(s): return not ONLY or s in ONLY


def hm_of(x):
    t = pd.to_datetime(x.ts); return (t.dt.hour * 100 + t.dt.minute).values


if want('S1'):
    print('S1 ORB NQ (5m)')
    nq = L.load_cme_ict('NQ', '5min')
    tr = np.maximum(nq.h - nq.l, np.maximum((nq.h - nq.c.shift()).abs(), (nq.l - nq.c.shift()).abs())); nq['atr'] = tr.rolling(14).mean()
    rows = []
    for dt, x in nq.groupby('date'):
        if dt in ROLLS or pd.Timestamp(dt).dayofweek > 4 or not era_ok(dt, 2023): continue
        x = x[(x.hr >= 9.5) & (x.hr < 16)]; orr = x[x.hr < 9.75]
        if len(orr) < 3: continue
        hi, lo = orr.h.max(), orr.l.min(); rg = hi - lo; b = x[x.hr >= 9.75]
        h, l, c, hr, atr = b.h.values, b.l.values, b.c.values, b.hr.values, b.atr.values
        k = next((i for i in range(len(c)) if hr[i] < 15 and (c[i] > hi or c[i] < lo)), None)
        if k is None or k + 1 >= len(c): continue
        side = 1 if c[k] > hi else -1; E = c[k]
        stop = (lo - 0.25 * atr[k]) if side == 1 else (hi + 0.25 * atr[k]); risk = abs(E - stop)
        tgt = (hi + 2 * rg) if side == 1 else (lo - 2 * rg)
        if risk <= 0 or (tgt - E) * side <= 0: continue
        for kind, s in (('ORB', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
            st, tg = (stop, tgt) if s == side else (E + side * risk, E - side * abs(tgt - E))
            xp, _, how = L.walk(h, l, c, hr, k + 1, s, E, st, tg, flat_hr=15 + 55 / 60)
            g = s * (xp - E) / risk
            for v in ('cme', 'broker'):
                rows.append(dict(cell=f'{kind}/{v}', date=dt, net=g - L.cost_R(v, 'NQ', E, risk, slips=1 if how == 'tgt' else 2)))
    T = pd.DataFrame(rows)
    for cell, g in T.groupby('cell'): gate(cell, g)

if want('S2') or want('S3'):
    for m in ('NQ', 'ES'):
        d = L.load_cme_ict(m, '1min'); d['sd'] = (pd.to_datetime(d.ts) + pd.Timedelta(hours=6)).dt.date   # 18:00 NY -> next trading date
        S = dict(tuple(d.groupby('sd'))); keys = sorted(S); rows2 = []; daily_close = {}
        cme_pts = 2 * 0.25 + 5 / {'NQ': 20, 'ES': 50}[m]
        for sd in keys:
            x = S[sd]; hm = hm_of(x)
            cl = x[hm <= 1659]
            if len(cl): daily_close[sd] = cl.c.iloc[-1]
            if pd.Timestamp(sd).dayofweek > 4 or sd in ROLLS or not era_ok(sd, 2023): continue
            a = x[hm == 1805]; z = x[hm == 1650]
            if len(a) and len(z):
                E, X = a.o.iloc[0], z.c.iloc[0]; r = (X - E) / E * 1e4
                rows2.append(dict(strat='S2 daily long', date=sd, gross_bp=r, cme_bp=r - cme_pts / E * 1e4, broker_bp=r - 10))
        dc = pd.Series(daily_close).sort_index(); sma = dc.rolling(25).mean()
        for i, sd in enumerate(keys):
            if pd.Timestamp(sd).dayofweek != 0 or sd in ROLLS or not era_ok(sd, 2023): continue
            prev = dc[dc.index < sd]
            if len(prev) < 25 or prev.iloc[-1] >= sma[prev.index[-1]]: continue
            x = S[sd]; a = x[hm_of(x) == 1805]
            tue = next((k for k in keys[i + 1:i + 3] if pd.Timestamp(k).dayofweek == 1), None)
            if tue is None or not len(a): continue
            y = S[tue]; z = y[hm_of(y) == 1650]
            if not len(z): continue
            E, X = a.o.iloc[0], z.c.iloc[0]; r = (X - E) / E * 1e4
            rows2.append(dict(strat='S3 turnaround Tue', date=sd, gross_bp=r, cme_bp=r - cme_pts / E * 1e4, broker_bp=r - 10 - 5))
        T2 = pd.DataFrame(rows2)
        for st, g in T2.groupby('strat'):
            g = g.sort_values('date'); yr = g.groupby(pd.to_datetime(g.date).dt.year).broker_bp.mean()
            def pf(v): return v[v > 0].sum() / -v[v < 0].sum()
            print(f"{st} {m}: n={len(g)} gross {g.gross_bp.mean():+.1f}bp | CME net {g.cme_bp.mean():+.1f}bp PF {pf(g.cme_bp):.2f} | "
                  f"BROKER net {g.broker_bp.mean():+.1f}bp PF {pf(g.broker_bp):.2f} win {100*(g.broker_bp>0).mean():.0f}% | broker bp/trade by yr "
                  + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()), flush=True)

if want('S4'):
    print('S4 morning range USDJPY (5m, Broker FX costs)')
    fx = L.load_fx('USDJPY', '5m')
    fx['sd'] = (pd.to_datetime(fx.ts) + pd.Timedelta(hours=4)).dt.date        # 20:00 NY -> next date
    rows4 = []
    for sd, x in fx.groupby('sd'):
        if pd.Timestamp(sd).dayofweek > 4 or not era_ok(sd, 2019): continue
        t = pd.to_datetime(x.ts); hrs = ((t.dt.hour + t.dt.minute / 60 - 20) % 24).values   # hours since 20:00 NY
        rg = x[hrs < 3]
        if len(rg) < 30: continue
        hi, lo = rg.h.max(), rg.l.min()
        if hi <= lo: continue
        b = x[(hrs >= 3) & (hrs < 15)]; o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
        k = next((i for i in range(len(h)) if h[i] >= hi or l[i] <= lo), None)
        if k is None: continue
        both = h[k] >= hi and l[k] <= lo
        if both: side = 1 if abs(o[k] - hi) <= abs(o[k] - lo) else -1
        else: side = 1 if h[k] >= hi else -1
        E = max(o[k], hi) if side == 1 else min(o[k], lo); stop = lo if side == 1 else hi; R = abs(E - stop)
        for tl, tR in (('his: no target', None), ('commenter 0.5R', 0.5), ('commenter 5R', 5.0)):
            for kind, s in (('MR', side), ('OPP', -side)):
                st = stop if s == side else E + side * R
                tg = None if tR is None else E + s * tR * R
                if both and s == side: g = -1.0
                elif k + 1 >= len(h): g = s * (c[-1] - E) / R
                else:
                    xp, _, _ = L.walk(h, l, c, np.zeros(len(h)), k + 1, s, E, st, tg)
                    g = s * (xp - E) / R
                rows4.append(dict(cell=f'{tl}/{kind}', date=sd, net=g - L.cost_R('broker_fx', 'USDJPY', E, R)))
    T4 = pd.DataFrame(rows4)
    for cell, g in T4.groupby('cell'): gate(cell, g)
