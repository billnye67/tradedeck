"""FT triggers (spec specs/FT-forced-triggers.md + addenda). Each function returns a signals DataFrame for ft_lab.simulate:
[coin, i, side, stop, stop_pct, tgt, tgt_R, hold]. Rules frozen in the spec; nothing here is tuned."""
import os, numpy as np, pandas as pd
from numba import njit
import ft_lab as L
H1 = np.int64(3_600_000_000_000); M5 = L.M5; DAY = L.DAY; NAN = np.nan
COLS = ['coin', 'i', 'side', 'stop', 'stop_pct', 'tgt', 'tgt_R', 'hold']
def S(rows): return pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame(columns=COLS)
@njit(cache=True)
def wilder(h, l, c, n):
    m = len(c); out = np.full(m, np.nan); tr = np.empty(m); tr[0] = h[0] - l[0]
    for k in range(1, m): tr[k] = max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1]))
    if m <= n: return out
    a = tr[:n].mean(); out[n - 1] = a
    for k in range(n, m): a = (a * (n - 1) + tr[k]) / n; out[k] = a
    return out
def bars(X, minutes):
    """Complete higher-TF bars from 5m: dict start (ns), O H L C V TB OI_end, end (5m index of the bar's last 5m candle)."""
    t = X['t']; per = np.int64(minutes) * 60_000_000_000; k = t // per; need = minutes // 5
    u, i0, cnt = np.unique(k, return_index=True, return_counts=True); ok = cnt == need; i0 = i0[ok]; u = u[ok]; e = i0 + need - 1
    o, h, l, c, v, tb, oi = X['o'], X['h'], X['l'], X['c'], X['v'], X['tb'], X['oi']
    H = np.array([h[a:b + 1].max() for a, b in zip(i0, e)])
    Lo = np.array([l[a:b + 1].min() for a, b in zip(i0, e)])
    V = np.array([v[a:b + 1].sum() for a, b in zip(i0, e)])
    TB = np.array([tb[a:b + 1].sum() for a, b in zip(i0, e)])
    B = dict(start=u * per, O=o[i0], H=H, L=Lo, C=c[e], V=V, TB=TB, OIs=oi[i0], OIe=oi[e], end=e)
    B['ATR20'] = wilder(B['H'], B['L'], B['C'], 20); B['ATR14'] = wilder(B['H'], B['L'], B['C'], 14); return B
_bc = {}
def hb(coin, minutes=60):
    key = (coin, minutes)
    if key not in _bc: _bc[key] = bars(L.load(coin), minutes)
    return _bc[key]
def atr_at(coin, i, minutes=60, n='ATR20'):
    """ATR of the last higher-TF bar completed at or before 5m bar i's close."""
    B = hb(coin, minutes); k = np.searchsorted(B['end'], i, side='right') - 1; return B[n][k] if k >= 0 else NAN
def pos(X, ts):
    p = np.searchsorted(X['t'], ts); return p if p < len(X['t']) and X['t'][p] == ts else -1
def utc_minute(X): return (X['t'] % DAY) // 60_000_000_000
def cooldown(idx, bars_):
    out = []; last = -10**12
    for i in idx:
        if i - last >= bars_: out.append(i); last = i
    return out
# ----------------------------------------------------------------- T1 long-liquidation rebound (row 45)
def T1(coin):
    X = L.load(coin); c, oi, t = X['c'], X['oi'], X['t']; n = len(c)
    if np.isnan(oi).mean() > 0.9: return S([])
    r1 = np.full(n, NAN); r1[12:] = c[12:] / c[:-12] - 1
    oc = np.full(n, NAN); oc[13:] = oi[12:-1] / oi[:-13] - 1          # 5-min lag: snapshot at bar open = T-5
    m = utc_minute(X); hsel = np.where(m % 60 == 55)[0]                # non-overlapping hourly samples
    ts = pd.to_datetime(t[hsel], utc=True)
    q = {}
    for name, arr, p in (('r', r1, .25), ('o', oc, .025)):
        s = pd.Series(arr[hsel], index=ts).dropna()
        qq = s.rolling('365D', min_periods=200).quantile(p); qd = qq.groupby(qq.index.floor('D')).last().shift(1)   # recomputed daily, yesterday's value
        q[name] = qd.reindex(pd.to_datetime(t, utc=True).floor('D')).to_numpy()
    st = (r1 <= q['r']) & (oc <= q['o'])
    idx = cooldown(np.where(st)[0], 96)
    return S([(coin, i, 1, NAN, 0.025, NAN, NAN, 96) for i in idx])
# ----------------------------------------------------------------- T2 deleveraging rebound (row 43)
def T2(coin):
    X = L.load(coin); c, oi, pr, t = X['c'], X['oi'], X['prem'], X['t']; n = len(c)
    if np.isnan(pr).mean() > 0.9 or np.isnan(oi).mean() > 0.9: return S([])
    B15 = hb(coin, 15); BH = hb(coin, 60); m = utc_minute(X); rows = []; last = -10**9
    k15 = np.searchsorted(B15['end'], np.arange(n) - 12, side='right') - 1      # 15m bars completed by T-60
    kH = np.searchsorted(BH['end'], np.arange(n) - 12, side='right') - 1
    for i in range(13, n - 1):
        if k15[i] < 0 or kH[i] < 0: continue
        a15 = B15['ATR20'][k15[i]]
        if not (c[i - 12] - c[i] >= 2 * a15): continue
        if not (oi[i] <= 0.98 * oi[i - 12]): continue
        if not (pr[i] <= -0.0020): continue
        if i - last < 72: continue
        last = i; em = m[i + 1]
        if em < 5 or em > 21 * 60 + 50: continue
        rows.append((coin, i, 1, c[i] - 2 * BH['ATR20'][kH[i]], NAN, NAN, NAN, 24))
    return S(rows)
# ----------------------------------------------------------------- funding helpers
def fund_events(coin):
    F = L.fund(coin)
    if F is None or not len(F): return None
    F = F.sort_values('t'); r = F.rate.to_numpy(); ts = F.t
    s = pd.Series(r, index=ts)
    def tq(p): return s.rolling('365D', min_periods=100).quantile(p).shift(1).to_numpy()
    F['p95'], F['p05'], F['p99'], F['p01'] = tq(.95), tq(.05), tq(.99), tq(.01); return F
def T3a(coin):
    X = L.load(coin); F = fund_events(coin); rows = []; last = -10**9
    if F is None: return S([])
    for f in F.itertuples():
        p = pos(X, f.t.value)
        if p <= 0: continue
        i = p - 1; side = -1 if f.rate >= f.p95 else (1 if f.rate <= f.p05 else 0)
        if side == 0 or f.p95 != f.p95 or i - last < 288: continue
        last = i; a = atr_at(coin, i)
        if a == a: rows.append((coin, i, side, X['c'][i] - side * 2 * a, NAN, NAN, NAN, 288))
    return S(rows)
def T3b(coin):
    X = L.load(coin); F = fund_events(coin); rows = []; last = -10**9
    if F is None: return S([])
    for f in F.itertuples():
        side = -1 if f.rate >= f.p99 else (1 if f.rate <= f.p01 else 0)
        if side == 0 or f.p99 != f.p99: continue
        p0 = pos(X, f.t.value)
        if p0 <= 0 or p0 + 288 >= len(X['c']): continue
        i = p0 + 287; c0 = X['c'][p0 - 1]; c1 = X['c'][i]
        failed = c1 < c0 if side == -1 else c1 > c0
        if not failed or i - last < 288: continue
        last = i; ext = X['h'][p0:i + 1].max() if side == -1 else X['l'][p0:i + 1].min()
        rows.append((coin, i, side, ext, NAN, NAN, NAN, 288))
    return S(rows)
# ----------------------------------------------------------------- T4 D4 1h structure-break fade (row 70)
def swings_break(B, need_disp=True, disp=2.0):
    """strength-2 pivots; returns list of (k break bar, dir (+1 up-break/-1 down-break), level L, leg)."""
    Hh, Ll, C, A = B['H'], B['L'], B['C'], B['ATR14']; n = len(C); out = []
    sh = sl = None; shk = slk = -1; last_ev = -10**9
    for k in range(4, n):
        j = k - 2                                                       # pivot at j confirmed at k
        if Hh[j] > max(Hh[j - 2], Hh[j - 1], Hh[j + 1], Hh[k]): sh, shk = Hh[j], j
        if Ll[j] < min(Ll[j - 2], Ll[j - 1], Ll[j + 1], Ll[k]): sl, slk = Ll[j], j
        if sh is not None and C[k] > sh and k > shk + 2:
            leg = C[k] - (Ll[slk:k + 1].min() if slk >= 0 else Ll[k])
            if (not need_disp or leg >= disp * A[k]) and k - last_ev >= 8: out.append((k, 1, sh, leg)); last_ev = k
            sh = None
        if sl is not None and C[k] < sl and k > slk + 2:
            leg = (Hh[shk:k + 1].max() if shk >= 0 else Hh[k]) - C[k]
            if (not need_disp or leg >= disp * A[k]) and k - last_ev >= 8: out.append((k, -1, sl, leg)); last_ev = k
            sl = None
    return out
def T4(coin):
    B = hb(coin, 60); rows = []
    for k, d, lev, leg in swings_break(B):
        if not B['ATR14'][k] == B['ATR14'][k]: continue
        side = -d; ext = B['H'][k] if d == 1 else B['L'][k]           # stop = impulse extreme (break bar)
        rows.append((coin, B['end'][k], side, ext, NAN, lev, NAN, 96))
    return S(rows)
# ----------------------------------------------------------------- sweep triggers
def S05(coin):
    B = hb(coin, 60); C, A = B['C'], B['ATR20']; rows = []
    for k in range(21, len(C)):
        mv = C[k] - C[k - 1]
        if A[k - 1] == A[k - 1] and abs(mv) >= 3 * A[k - 1]:
            side = -int(np.sign(mv)); rows.append((coin, B['end'][k], side, B['H'][k] if side < 0 else B['L'][k], NAN, NAN, NAN, 48))
    return S(rows)
def _daily_xs(universe, hour_ns, score_fn, top=3, hold=288):
    """At each day's hour_ns (UTC), rank coins by score_fn(coin, X, p) at bar p (bar opening at that time) -> long lowest/short highest per rule."""
    days = pd.date_range(L.ERA['learn'][0] - pd.Timedelta(days=10), L.ERA['exam'][1], freq='D')
    sc = {c: {} for c in universe}
    for c in universe:
        X = L.load(c)
        for d in days:
            ts = d.value + hour_ns; p = pos(X, ts)
            if p > 0:
                v = score_fn(c, X, p)
                if v == v: sc[c][d] = (p, v)
    return days, sc
def S08(universe):
    rows = []
    fev = {c: fund_events(c) for c in universe}
    byt = {}
    for c, F in fev.items():
        if F is None: continue
        for f in F[F.t.dt.hour == 0].itertuples(): byt.setdefault(f.t.value, []).append((f.rate, c))
    for ts, lst in byt.items():
        if len(lst) < 12: continue
        lst.sort()
        for side, grp in ((1, lst[:3]), (-1, lst[-3:])):
            for _r, c in grp:
                X = L.load(c); p = pos(X, ts)
                if p <= 0: continue
                a = atr_at(c, p - 1)
                if a == a: rows.append((c, p - 1, side, X['c'][p - 1] - side * 2 * a, NAN, NAN, NAN, 288))
    return S(rows)
def S13(universe):
    rows = []; days, sc = _daily_xs(universe, 0, lambda c, X, p: X['c'][p - 1] / X['c'][p - 1 - 2016] - 1 if p > 2017 else NAN)
    for d in days:
        lst = sorted((v[1], c) for c in universe for dd, v in [(d, sc[c].get(d))] if v)
        if len(lst) < 12: continue
        for side, grp in ((1, lst[-3:]), (-1, lst[:3])):
            for _v, c in grp:
                p = sc[c][d][0]; X = L.load(c); a = atr_at(c, p - 1)
                if a == a: rows.append((c, p - 1, side, X['c'][p - 1] - side * 2 * a, NAN, NAN, NAN, 288))
    return S(rows)
def S14(universe):
    Bb = hb('BTCUSDT'); bt = pd.Series(Bb['C'], index=Bb['start']); rb = bt.pct_change(); rows = []
    for c in universe:
        if c == 'BTCUSDT': continue
        B = hb(c); rc = pd.Series(B['C'], index=B['start']).pct_change(); j = pd.concat([rc.rename('c'), rb.rename('b')], axis=1).dropna()
        if len(j) < 800: continue
        idx = j.index.to_numpy(); y = j.c.to_numpy(); x = j.b.to_numpy(); day = idx // DAY; endmap = dict(zip(B['start'], B['end'])); last = -10**12
        for d in np.unique(day):
            fit = (idx >= (d - 30) * DAY) & (idx < d * DAY)
            if fit.sum() < 500: continue
            A = np.column_stack([np.ones(fit.sum()), x[fit]]); beta = np.linalg.lstsq(A, y[fit], rcond=None)[0]; sd = (y[fit] - A @ beta).std()
            for k in np.where(day == d)[0]:
                res = y[k] - beta[0] - beta[1] * x[k]
                if abs(res) >= 2 * sd and idx[k] - last >= 2 * H1:
                    last = idx[k]; i = endmap[idx[k]]; side = -int(np.sign(res)); a = atr_at(c, i); X = L.load(c)
                    if a == a: rows.append((c, i, side, X['c'][i] - side * 2 * a, NAN, NAN, NAN, 24))
    return S(rows)
def S15(coin):
    X = L.load(coin); F = L.fund(coin); rows = []
    if F is None: return S([])
    for f in F.itertuples():
        if abs(f.rate) < 1e-4: continue
        p = pos(X, f.t.value)
        if p <= 0: continue
        i = p; side = 1 if f.rate > 0 else -1; a = atr_at(coin, i)
        if a == a: rows.append((coin, i, side, X['c'][i] - side * 2 * a, NAN, NAN, NAN, 12))
    return S(rows)
def S16(coin, N=12, K=3):
    X = L.load(coin); o, h, l, c, v, t = X['o'], X['h'], X['l'], X['c'], X['v'], X['t']; n = len(c)
    a20 = wilder(h, l, c, 20); day = t // DAY; m = utc_minute(X); tp = (h + l + c) / 3
    vw = np.full(n, NAN); cpv = cv = 0.0; cur = -1; broken = False
    for k in range(n):
        if day[k] != cur: cur = day[k]; cpv = cv = 0.0; broken = False
        elif t[k] - t[k - 1] != M5: broken = True
        if broken: continue
        cpv += tp[k] * v[k]; cv += v[k]; vw[k] = cpv / cv if cv > 0 else NAN
    rows = []; done = set()
    for k in range(N + 13, n - 1):
        if not (2 * 60 <= m[k] < 20 * 60) or vw[k] != vw[k] or a20[k] != a20[k]: continue
        rng = h[k] - l[k]
        if rng <= 0: continue
        for side in (1, -1):
            if (day[k], side) in done: continue
            seg = slice(k - N, k)
            if side == 1:
                ok = (c[seg] > vw[seg]).all() and vw[k - 1] > vw[k - 13] and ((c[seg] - vw[seg]) >= a20[seg]).any() and l[k] <= vw[k] <= h[k] and c[k] > vw[k] and c[k] > o[k] and c[k] >= l[k] + 0.75 * rng
                stop = l[k] - 0.25 * a20[k]
            else:
                ok = (c[seg] < vw[seg]).all() and vw[k - 1] < vw[k - 13] and ((vw[seg] - c[seg]) >= a20[seg]).any() and l[k] <= vw[k] <= h[k] and c[k] < vw[k] and c[k] < o[k] and c[k] <= h[k] - 0.75 * rng
                stop = h[k] + 0.25 * a20[k]
            if not ok: continue
            ent = o[k + 1]; dist = abs(ent - stop) / ent
            if side * (ent - stop) <= 0 or not (0.005 <= dist <= 0.02): continue
            hold = min(72, int((23 * 60 - m[k + 1]) // 5))
            if hold <= 0: continue
            done.add((day[k], side)); rows.append((coin, k, side, stop, NAN, NAN, K, hold))
    return S(rows)
def _et(X): return pd.to_datetime(X['t'], utc=True).tz_convert('America/New_York')
def S29(gate=False):
    coin = 'BTCUSDT'; X = L.load(coin); et = _et(X); v = X['v']; n = len(v)
    tday = (et - pd.Timedelta(hours=17)).normalize(); mins = ((et - pd.Timedelta(hours=17)).hour * 60 + et.minute).to_numpy(); b = mins // 30
    y2020 = et.year == 2020; vb = pd.Series(v[y2020]).groupby(b[y2020]).mean(); ob = int(vb.idxmax())
    df = pd.DataFrame({'d': tday, 'b': b, 'm': mins, 'i': np.arange(n), 'v': v})
    rows = []; hist = []
    for d, g in df.groupby('d'):
        g0 = g[g.m == 0]; ge = g[g.m == (ob + 1) * 30]; gx = g[g.m == 23 * 60 + 30]; bv = g[g.b == ob].v.sum()
        if not len(g0) or not len(ge) or not len(gx): continue
        p80 = np.percentile(hist[-60:], 80) if len(hist) >= 60 else NAN; hist.append(bv)
        if gate and not (bv > p80): continue
        r = X['o'][ge.i.iat[0]] / X['o'][g0.i.iat[0]] - 1
        if r == 0: continue
        i = gx.i.iat[0] - 1; rows.append((coin, i, int(np.sign(r)), NAN, 0.015, NAN, NAN, 6))
    return S(rows)
def S34():
    coin = 'BTCUSDT'; X = L.load(coin); B = hb(coin); lr = np.log(B['C'][1:] / B['C'][:-1]); st = B['start'][1:]
    s = pd.Series(lr, index=pd.to_datetime(st, utc=True)); vol = s.rolling(720).std(); dv = vol[vol.index.hour == 0]
    med = dv.rolling(365).median().shift(1); rows = []
    for d, val in dv.items():
        if not (val > med.get(d, NAN)): continue
        p = pos(X, (d + pd.Timedelta(hours=21)).value)
        if p > 0:
            a = atr_at(coin, p - 1)
            if a == a: rows.append((coin, p - 1, 1, X['c'][p - 1] - 2 * a, NAN, NAN, NAN, 24))
    return S(rows)
def S37():
    U = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'LINKUSDT', 'DOGEUSDT', 'SOLUSDT', 'AVAXUSDT']; XS = {c: L.load(c) for c in U}; rows = []
    for d in pd.date_range(L.ERA['learn'][0], L.ERA['exam'][1], freq='D'):
        for hh in range(2, 23, 2):
            ts = d.value + hh * H1; r = []
            for c in U:
                p = pos(XS[c], ts)
                if p < 5: break
                r.append((XS[c]['c'][p - 1] / XS[c]['c'][p - 5] - 1, c, p - 1))
            if len(r) < 7: continue
            r.sort(); rows.append((r[0][1], r[0][2], 1, NAN, 0.015, NAN, NAN, 24)); rows.append((r[-1][1], r[-1][2], -1, NAN, 0.015, NAN, NAN, 24))
    return S(rows)
def S38():
    coin = 'BTCUSDT'; X = L.load(coin); B = hb(coin, 30); C, V = B['C'], B['V']
    obv = np.cumsum(np.r_[0, np.where(C[1:] >= C[:-1], V[1:], -V[1:])]); f = pd.Series(obv).rolling(6).mean().to_numpy(); s = pd.Series(obv).rolling(48).mean().to_numpy()
    up = np.where((f[1:] > s[1:]) & (f[:-1] <= s[:-1]))[0] + 1; dn = np.where((f[1:] < s[1:]) & (f[:-1] >= s[:-1]))[0] + 1; rows = []
    for k in up:
        if k < 96: continue
        i = B['end'][k]; mm = (X['t'][i + 1] % DAY) // 60_000_000_000
        if mm > 17 * 60 + 55: continue
        nd = dn[dn > k]; hold = 72 if not len(nd) else min(72, int(B['end'][nd[0]] - i))
        rows.append((coin, i, 1, C[k] - 2 * B['ATR20'][k], NAN, NAN, NAN, max(hold, 1)))
    return S(rows)
def _window(X, d, h0, m0, h1, m1):
    a = pos(X, d + h0 * H1 + m0 * 60_000_000_000); b = pos(X, d + h1 * H1 + m1 * 60_000_000_000)
    return (a, b) if a > 0 and b > a else (None, None)
def _days(X): return np.unique(X['t'] // DAY) * DAY
def S47(coin):
    X = L.load(coin); rows = []
    for d in _days(X):
        a, b = _window(X, d, 3, 0, 3, 45)
        if a is None: continue
        vv = X['v'][a:b].sum()
        if vv <= 0: continue
        sh = X['tb'][a:b].sum() / vv; r = X['c'][b - 1] / X['o'][a] - 1; side = -1 if (sh >= .6 and r > 0) else (1 if (sh <= .4 and r < 0) else 0)
        if not side: continue
        at = atr_at(coin, b - 1, 15); ext = X['h'][a:b].max() if side < 0 else X['l'][a:b].min()
        if at == at: rows.append((coin, b - 1, side, ext - side * at, NAN, NAN, 2.0, 15))
    return S(rows)
def S48(coin):
    X = L.load(coin); Bt = L.load('BTCUSDT'); rows = []
    for d in _days(X):
        a, b = _window(X, d, 0, 0, 0, 15); pa, pb = _window(Bt, d, 0, 0, 0, 15)
        if a is None or pa is None: continue
        vv = X['v'][a:b].sum()
        if vv <= 0 or abs(Bt['c'][pb - 1] / Bt['o'][pa] - 1) >= 0.002: continue
        sh = X['tb'][a:b].sum() / vv; side = 1 if sh >= .6 else (-1 if sh <= .4 else 0)
        if not side: continue
        at = atr_at(coin, b - 1, 15)
        if at == at: rows.append((coin, b - 1, side, NAN, at / X['c'][b - 1], NAN, 2.0, 15))
    return S(rows)
def S49(coin):
    X = L.load(coin); rows = []; ds = _days(X)
    for d in ds[1:]:
        a, b = _window(X, d - DAY, 5, 0, 17, 0); e = pos(X, d + 5 * H1 + 5 * 60_000_000_000); x = pos(X, d + 17 * H1)
        if a is None or e <= 0 or x <= e: continue
        r = X['o'][b] / X['o'][a] - 1
        if r == 0: continue
        side = -int(np.sign(r)); at = atr_at(coin, e - 1)
        if at == at: rows.append((coin, e - 1, side, X['c'][e - 1] - side * 3 * at, NAN, NAN, NAN, int(x - e)))
    return S(rows)
def S56(coin):
    X = L.load(coin); rows = []; rngs = []
    for d in _days(X):
        a, b = _window(X, d, 8, 0, 13, 30)
        if a is None: continue
        hi, lo = X['h'][a:b].max(), X['l'][a:b].min(); w = hi - lo; avg = np.mean(rngs[-20:]) if len(rngs) >= 20 else NAN; rngs.append(w)
        if not (w < 0.6 * avg): continue
        x = pos(X, d + 15 * H1 + 30 * 60_000_000_000)
        if x <= b: continue
        for k in range(b, x - 1):
            side = 1 if X['c'][k] > hi else (-1 if X['c'][k] < lo else 0)
            if side:
                rows.append((coin, k, side, (hi + lo) / 2, NAN, NAN, 2.0, int(x - k - 1))); break
    return S(rows)
def S57(coin):
    X = L.load(coin); rows = []
    for d in _days(X)[1:]:
        a0, b0 = _window(X, d - DAY, 20, 0, 24, 0); a, b = _window(X, d, 0, 0, 0, 15)
        if a0 is None or a is None: continue
        hi, lo = X['h'][a0:b0].max(), X['l'][a0:b0].min(); vv = X['v'][a:b].sum()
        if vv <= 0: continue
        sh = X['tb'][a:b].sum() / vv; ph, pl = X['h'][a:b].max(), X['l'][a:b].min()
        side = -1 if (ph > hi and sh > 0.583) else (1 if (pl < lo and sh < 0.417) else 0)
        if not side: continue
        i = b; ent = X['c'][i]; ext = ph if side < 0 else pl; dist = 1.2 * abs(ext - ent)
        rows.append((coin, i, side, NAN, dist / ent, NAN, 2.5, 14))
    return S(rows)
def S58(coin):
    X = L.load(coin); F = L.fund(coin); rows = []
    if F is None: return S([])
    F = F.sort_values('t'); ft = F.t.astype('int64').to_numpy(); fr = F.rate.to_numpy(); a5 = wilder(X['h'], X['l'], X['c'], 20)
    for d in _days(X):
        p = pos(X, d + 7 * H1 + 45 * 60_000_000_000)
        if p <= 0: continue
        k = np.searchsorted(ft, d + 7 * H1 + 45 * 60_000_000_000) - 1
        if k < 0 or abs(fr[k]) < 1e-4: continue
        side = 1 if fr[k] < 0 else -1; rows.append((coin, p, side, NAN, 1.5 * a5[p] / X['c'][p], NAN, 2.0, 11))
    return S(rows)
def S59(coin):
    X = L.load(coin); v, tb, c = X['v'], X['tb'], X['c']; sig = (2 * tb - v) * c; imb = np.where(v > 0, (2 * tb - v) / np.maximum(v, 1e-12), 0)
    q = pd.Series(np.abs(sig)).rolling(8640, min_periods=2000).quantile(.995).shift(1).to_numpy(); rows = []
    cand = np.where((np.abs(sig) >= q) & (np.abs(imb) >= .15))[0]
    for i in cooldown(cand, 6):
        if i + 2 >= len(c): continue
        side = int(np.sign(sig[i])); a = atr_at(coin, i + 1)
        if a == a: rows.append((coin, i + 1, side, c[i + 1] - side * 2 * a, NAN, NAN, NAN, 6))
    return S(rows)
def S63(coin):
    X = L.load(coin); rows = []
    for d in _days(X):
        a, b = _window(X, d, 13, 30, 15, 0); x = pos(X, d + 18 * H1); e2 = pos(X, d + 17 * H1)
        if a is None or x <= 0 or e2 <= b: continue
        hi, lo = X['h'][a:b].max(), X['l'][a:b].min(); mid = (hi + lo) / 2; out = 0; poke = NAN
        for k in range(b, e2):
            ck = X['c'][k]
            if out == 0:
                if ck > hi: out, poke = 1, X['h'][k]
                elif ck < lo: out, poke = -1, X['l'][k]
            else:
                poke = max(poke, X['h'][k]) if out == 1 else min(poke, X['l'][k])
                if (out == 1 and ck < hi) or (out == -1 and ck > lo):
                    side = -out; edge = hi if out == 1 else lo; stop = poke + out * 0.2 * abs(poke - edge); ent = ck
                    r25 = 2.5 * abs(ent - stop); tg = mid if abs(mid - ent) < r25 else ent + side * r25
                    rows.append((coin, k, side, stop, NAN, tg, NAN, int(x - k - 1))); break
    return S(rows)
def S64(coin):
    B = hb(coin); C, O, V, A, st = B['C'], B['O'], B['V'], B['ATR20'], B['start']; dow = pd.to_datetime(st, utc=True).dayofweek.to_numpy(); rows = []
    wk = pd.Series(np.where(dow < 5, V, np.nan)).rolling(120, min_periods=60).mean().shift(1).to_numpy()
    for k in range(21, len(C)):
        if dow[k] >= 5 and A[k - 1] == A[k - 1] and abs(C[k] - O[k]) > 2 * A[k - 1] and V[k] < 0.6 * wk[k]:
            side = -int(np.sign(C[k] - O[k])); rows.append((coin, B['end'][k], side, NAN, 1.5 * A[k - 1] / C[k], NAN, 2.0, 48))
    return S(rows)
def S71(coin):
    B = hb(coin); Hh, Ll, C = B['H'], B['L'], B['C']; rows = []; k = 25
    while k < len(C) - 1:
        hi, lo = Hh[k - 24:k].max(), Ll[k - 24:k].min(); d = 1 if C[k] > hi else (-1 if C[k] < lo else 0)
        if not d: k += 1; continue
        poke = Hh[k] if d == 1 else Ll[k]; fired = False
        for j in range(k + 1, min(k + 25, len(C))):
            poke = max(poke, Hh[j]) if d == 1 else min(poke, Ll[j])
            if (d == 1 and C[j] < hi) or (d == -1 and C[j] > lo):
                rows.append((coin, B['end'][j], -d, poke, NAN, NAN, NAN, 288)); fired = True; k = j + 24; break
        if not fired: k += 1
    return S(rows)
def S72(coin):
    B = hb(coin); Hh, Ll, C = B['H'], B['L'], B['C']; rows = []; k = 25
    while k < len(C) - 1:
        hi, lo = Hh[k - 24:k].max(), Ll[k - 24:k].min(); d = 1 if C[k] > hi else (-1 if C[k] < lo else 0)
        if not d: k += 1; continue
        back = False; fired = False
        for j in range(k + 1, min(k + 49, len(C))):
            if not back and ((d == 1 and C[j] < hi) or (d == -1 and C[j] > lo)): back = True; continue
            if back and ((d == 1 and C[j] < lo) or (d == -1 and C[j] > hi)):
                rows.append((coin, B['end'][j], -d, hi if d == 1 else lo, NAN, NAN, NAN, 576)); fired = True; k = j + 24; break
        if not fired: k += 1
    return S(rows)
def S73(coin):
    B = hb(coin); Hh, Ll, C, A = B['H'], B['L'], B['C'], B['ATR14']; rows = []; last = -10**9
    for k in range(25, len(C)):
        hi, lo = Hh[k - 24:k].max(), Ll[k - 24:k].min()
        if not (A[k - 1] == A[k - 1] and hi - lo <= 3 * A[k - 1]) or k - last < 24: continue
        if C[k] > hi: rows.append((coin, B['end'][k], 1, lo, NAN, NAN, NAN, 1152)); last = k
        elif C[k] < lo: rows.append((coin, B['end'][k], -1, hi, NAN, NAN, NAN, 1152)); last = k
    return S(rows)
def S79(coin):
    B = hb(coin); C, A = B['C'], B['ATR20']; rows = []; last = -10**9
    s = pd.Series(C); p5 = s.rolling(36).quantile(.05).shift(1).to_numpy(); p95 = s.rolling(36).quantile(.95).shift(1).to_numpy(); inside_prev = False
    for k in range(37, len(C)):
        box = A[k - 1] == A[k - 1] and (p95[k] - p5[k]) <= 3 * A[k - 1]
        if box and k - last >= 8:
            if C[k] > p95[k] and C[k - 1] <= p95[k]: rows.append((coin, B['end'][k], 1, p5[k] - A[k - 1], NAN, NAN, NAN, 96)); last = k
            elif C[k] < p5[k] and C[k - 1] >= p5[k]: rows.append((coin, B['end'][k], -1, p95[k] + A[k - 1], NAN, NAN, NAN, 96)); last = k
    return S(rows)
def S80(coin):
    B = hb(coin); C = B['C']; n = len(C); rows = []; last = -10**9; x = np.arange(168.0); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
    for k in range(168, n):
        y = C[k - 167:k + 1]; b = ((x - xm) * (y - y.mean())).sum() / sxx; a = y.mean() - b * xm; res = y - (a + b * x); sd = res.std()
        if sd <= 0: continue
        z = res[-1] / sd
        if abs(z) >= 2 and k - last >= 24:
            side = -int(np.sign(z)); i = B['end'][k]; at = atr_at(coin, i); last = k
            if at == at: rows.append((coin, i, side, C[k] - side * 2 * at, NAN, NAN, NAN, 288))
    return S(rows)
def S89():
    coin = 'BTCUSDT'; X = L.load(coin); et = _et(X); rows = []
    mins = (et.hour * 60 + et.minute).to_numpy(); d = et.normalize()
    idx15 = np.where(mins == 15 * 60)[0]; idx16 = {dd: i for dd, i in zip(d[mins == 16 * 60], np.where(mins == 16 * 60)[0])}
    for i in idx15:
        dd = d[i]; prev = [k for k in (dd - pd.Timedelta(days=1), dd - pd.Timedelta(days=3)) if k in idx16]
        if not prev or et.dayofweek[i] >= 5: continue
        r = X['o'][i] / X['o'][idx16[prev[0]]] - 1
        if r == 0: continue
        side = int(np.sign(r)); a = atr_at(coin, i - 1)
        if a == a: rows.append((coin, i - 1, side, X['c'][i - 1] - side * 2 * a, NAN, NAN, NAN, 12))
    return S(rows)
def S102(coin):
    X = L.load(coin); o, h, l, c = X['o'], X['h'], X['l'], X['c']; n = len(c); rng = h - l; avg = pd.Series(rng).rolling(20).mean().shift(1).to_numpy(); rows = []; k = 22
    while k < n - 30:
        bull = l[k] > h[k - 2] and rng[k - 1] >= 1.5 * avg[k - 1]; bear = h[k] < l[k - 2] and rng[k - 1] >= 1.5 * avg[k - 1]
        if not (bull or bear): k += 1; continue
        top, bot = (l[k], h[k - 2]) if bull else (l[k - 2], h[k]); held = False; fired = False
        for j in range(k + 1, min(k + 289, n - 26)):
            if bull:
                if not held and l[j] <= top and c[j] >= bot: held = True; continue
                if held and c[j] < bot:
                    for q in range(j + 1, min(j + 25, n - 26)):
                        if h[q] >= bot: rows.append((coin, q, -1, top, NAN, NAN, 3.0, 24)); break
                    fired = True; break
            else:
                if not held and h[j] >= bot and c[j] <= top: held = True; continue
                if held and c[j] > top:
                    for q in range(j + 1, min(j + 25, n - 26)):
                        if l[q] <= top: rows.append((coin, q, 1, bot, NAN, NAN, 3.0, 24)); break
                    fired = True; break
        k = j + 1 if fired else k + 1
    return S(rows)
def S105():
    ref = {'BTCUSDT': 'ETHUSDT', 'ETHUSDT': 'BTCUSDT', 'XRPUSDT': 'BTCUSDT'}; rows = []
    for coin, rc in ref.items():
        B = hb(coin); R = hb(rc); rmap = {s: k for k, s in enumerate(R['start'])}
        for k in range(25, len(B['C'])):
            kr = rmap.get(B['start'][k])
            if kr is None or kr < 24: continue
            plo, phi = B['L'][k - 24:k].min(), B['H'][k - 24:k].max(); rlo, rhi = R['L'][kr - 24:kr].min(), R['H'][kr - 24:kr].max()
            if B['L'][k] < plo and B['C'][k] > plo and R['L'][kr] >= rlo: rows.append((coin, B['end'][k], 1, B['L'][k], NAN, NAN, 3.0, 288))
            elif B['H'][k] > phi and B['C'][k] < phi and R['H'][kr] <= rhi: rows.append((coin, B['end'][k], -1, B['H'][k], NAN, NAN, 3.0, 288))
    return S(rows)
def S141(coin):
    D = hb(coin, 1440); st = pd.to_datetime(D['start'], utc=True); rows = []
    wk = pd.Series(D['H'], index=st).resample('W-SUN').max(); wl = pd.Series(D['L'], index=st).resample('W-SUN').min()
    mh = pd.Series(D['H'], index=st).resample('ME').max(); ml = pd.Series(D['L'], index=st).resample('ME').min()
    for k in range(40, len(D['C'])):
        t = st[k]; pw = wk[wk.index < t - pd.Timedelta(days=t.dayofweek)]; pm = mh[mh.index < t.replace(day=1)]
        if not len(pw) or not len(pm): continue
        levels = [(pw.iat[-1], wl[wl.index < t - pd.Timedelta(days=t.dayofweek)].iat[-1]), (pm.iat[-1], ml[ml.index < t.replace(day=1)].iat[-1])]
        for hi, lo in levels:
            if D['H'][k] > hi and D['C'][k] < hi: rows.append((coin, D['end'][k], -1, D['H'][k], NAN, NAN, 3.0, 2880)); break
            if D['L'][k] < lo and D['C'][k] > lo: rows.append((coin, D['end'][k], 1, D['L'][k], NAN, NAN, 3.0, 2880)); break
    return S(rows)
def SD1(coin):
    B = hb(coin); C, O, A = B['C'], B['O'], B['ATR20']; rows = []
    for k in range(21, len(C)):
        if A[k - 1] == A[k - 1] and abs(C[k] - O[k]) > A[k - 1] and B['OIe'][k] >= 1.015 * B['OIs'][k]:
            side = int(np.sign(C[k] - O[k])); rows.append((coin, B['end'][k], side, NAN, A[k - 1] / C[k], NAN, 2.5, 48))
    return S(rows)
# ----------------------------------------------------------------- external-data triggers (old scratchpad, read-only)
OLD = os.path.expandvars(r'%LOCALAPPDATA%\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad')
def _kimchi():
    U = pd.read_parquet(os.path.join(OLD, 'h34', 'upbit_KRW-BTC_1m.parquet')).sort_values('ts'); U['t'] = pd.to_datetime(U.ts, unit='ms', utc=True)
    u5 = U.set_index('t').resample('5min', label='left', closed='left').agg({'close': 'last', 'volume': 'sum'}).dropna()
    fx = pd.read_csv(os.path.join(OLD, 'h34', 'usdkrw_daily_yahoo.csv'))
    fx['d'] = pd.to_datetime(fx.ts, unit='s', utc=True).dt.normalize(); fxs = fx.drop_duplicates('d').set_index('d')['close'].astype(float).sort_index()
    X = L.load('BTCUSDT'); bt = pd.Series(X['c'], index=pd.to_datetime(X['t'], utc=True) + pd.Timedelta(minutes=5)); i_of = pd.Series(np.arange(len(X['c'])), index=bt.index)
    u5.index = u5.index + pd.Timedelta(minutes=5)                       # stamp at bar close
    j = pd.DataFrame({'u': u5.close, 'uv': u5.volume, 'b': bt, 'i': i_of}).dropna(subset=['b'])
    j['fx'] = fxs.shift(1).reindex(j.index.normalize()).to_numpy()   # yesterday's USDKRW close: no look-ahead
    j['fx'] = j.fx.ffill(); j['kp'] = j.u / (j.b * j.fx) - 1; return j
def S61():
    j = _kimchi().dropna(subset=['kp']); X = L.load('BTCUSDT'); kp = j.kp.to_numpy(); ii = j.i.to_numpy().astype(int); hr = j.index.hour.to_numpy(); b = j.b.to_numpy(); rows = []; last = -10**9
    for k in range(12, len(j) - 96):
        if not (0 <= hr[k] < 14) or ii[k] - last < 96: continue
        if kp[k] > .02 and kp[k] - kp[k - 12] >= .005 and abs(b[k] / b[k - 6] - 1) < .002:
            ex = np.where(kp[k + 1:k + 97] < .01)[0]; hold = int(ex[0] + 1) if len(ex) else 96
            rows.append(('BTCUSDT', ii[k], 1, NAN, 0.015, NAN, NAN, hold)); last = ii[k]
    return S(rows)
def S62():
    j = _kimchi().dropna(subset=['kp']); kp = j.kp.to_numpy(); ii = j.i.to_numpy().astype(int); tt = j.index; rows = []
    for k in np.where((tt.minute == 0) & np.isin(tt.hour, [0, 4, 8]))[0]:
        if k < 24 or k + 300 > len(j): continue
        if kp[k] > .015 and kp[k] > kp[k - 24]:
            end = np.where((tt[k + 1:k + 289].hour == 23) & (tt[k + 1:k + 289].minute == 0))[0]; ex = np.where(kp[k + 1:k + 289] < .005)[0]
            hold = int(min(end[0] + 1 if len(end) else 288, ex[0] + 1 if len(ex) else 288)); a = atr_at('BTCUSDT', ii[k])
            if a == a: rows.append(('BTCUSDT', ii[k], 1, NAN, 1.5 * a / L.load('BTCUSDT')['c'][ii[k]], NAN, 2.5, hold))
    return S(rows)
def S65():
    j = _kimchi(); uv = j.uv.to_numpy(); ii = j.i.to_numpy().astype(int); b = j.b.to_numpy(); q = pd.Series(uv).rolling(288).quantile(.99).shift(1).to_numpy(); rows = []; last = -10**9
    for k in range(300, len(j) - 72):
        if uv[k] > q[k] and b[k] / b[k - 48] - 1 >= .02 and ii[k] - last >= 72: rows.append(('BTCUSDT', ii[k], -1, NAN, 0.015, NAN, NAN, 72)); last = ii[k]
    return S(rows)
def S76():
    rows = []
    for sym, coin in (('BTC-USD', 'BTCUSDT'), ('ETH-USD', 'ETHUSDT')):
        P = pd.concat([pd.read_parquet(os.path.join(OLD, 'cbp', f)) for f in (f'{sym}.parquet', f'{sym}-exam.parquet') if os.path.exists(os.path.join(OLD, 'cbp', f))])
        P['t'] = pd.to_datetime(P.ts, unit='ms', utc=True)
        cbc = P.set_index('t').sort_index()['close'].astype(float); cbc = cbc[~cbc.index.duplicated()]
        cbc.index = cbc.index + pd.Timedelta(hours=1)                     # Coinbase candles are start-stamped: value known at hour end
        B = hb(coin); bc = pd.Series(B['C'], index=pd.to_datetime(B['start'], utc=True) + pd.Timedelta(hours=1)); emap = pd.Series(B['end'], index=bc.index)
        j = pd.DataFrame({'cb': cbc, 'bn': bc}).dropna(); prem = np.log(j.cb / j.bn); x = prem - prem.rolling(168).median()
        hi = x.rolling(720).quantile(.9).shift(1); lo = x.rolling(720).quantile(.1).shift(1); up = (x > hi).astype(int); dn = (x < lo).astype(int)
        p3u = up.rolling(3).sum() == 3; p3d = dn.rolling(3).sum() == 3; last = None
        for t in j.index[(p3u | p3d).to_numpy()]:
            if last is not None and t - last < pd.Timedelta(hours=24): continue
            last = t; side = 1 if p3u[t] else -1; i = int(emap.get(t, -1))
            if i < 0: continue
            a = atr_at(coin, i)
            if a == a: rows.append((coin, i, side, L.load(coin)['c'][i] - side * 3 * a, NAN, NAN, NAN, 288))
    return S(rows)
def S82():
    D = pd.read_parquet(os.path.join(OLD, 'h26', 'deribit_settlements.parquet'))
    D = D[D.instrument.str.startswith('BTC')]; D['exp'] = pd.to_datetime(D.expiry, format='%d%b%y', utc=True) + pd.Timedelta(hours=8)
    g = D.groupby(['exp', 'strike']).position.sum().reset_index(); tot = g.groupby('exp').position.sum(); big = tot[tot >= tot.rolling(20, min_periods=5).median().shift(1)]
    X = L.load('BTCUSDT'); rows = []
    for e in big.index:
        if e.dayofweek != 4: continue
        k = g[g.exp == e].sort_values('position').strike.iat[-1]; p = pos(X, (e - pd.Timedelta(hours=4)).value)
        if p <= 0: continue
        px = X['c'][p - 1]; side = 1 if k > px else -1; a = atr_at('BTCUSDT', p - 1)
        if a == a: rows.append(('BTCUSDT', p - 1, side, px - side * 2 * a, NAN, NAN, NAN, 48))
    return S(rows)
def S84():
    C = pd.read_csv(os.path.join(OLD, 'cot', 'cot_btc.csv')); C['d'] = pd.to_datetime(C.date, utc=True)   # report date (Tuesday); published Friday
    C = C.sort_values('d'); net = C.lev.astype(float); z = (net - net.rolling(52).mean()) / net.rolling(52).std()
    X = L.load('BTCUSDT'); D = hb('BTCUSDT', 1440); rows = []; last = None
    for d, zz in zip(C.d, z):
        if not (zz <= -1.5): continue
        fri = (d + pd.Timedelta(days=(4 - d.dayofweek) % 7)).normalize() + pd.Timedelta(hours=21)
        if last is not None and fri - last < pd.Timedelta(days=14): continue
        p = pos(X, fri.value)
        if p <= 0: continue
        kd = np.searchsorted(D['end'], p - 1, side='right') - 1
        if kd < 0: continue
        last = fri; rows.append(('BTCUSDT', p - 1, 1, X['c'][p - 1] - 2 * D['ATR20'][kd], NAN, NAN, NAN, 1440))
    return S(rows)
# ----------------------------------------------------------------- registry of runnable triggers
PER_COIN = dict(T1=T1, T2=T2, T3a=T3a, T3b=T3b, T4=T4, S05=S05, S15=S15, S16=S16, S47=S47, S48=S48, S49=S49, S56=S56, S57=S57, S58=S58, S59=S59,
                S63=S63, S64=S64, S71=S71, S72=S72, S73=S73, S79=S79, S80=S80, S102=S102, S141=S141, SD1=SD1)
UNIVERSE_FN = dict(S08=S08, S13=S13, S14=S14)
FIXED = dict(S29=lambda: S29(False), S30=lambda: S29(True), S34=S34, S37=S37, S38=S38, S89=S89, S105=S105, S61=S61, S62=S62, S65=S65, S76=S76, S82=S82, S84=S84)
ROW = dict(T1='45/46', T2='43', T3a='6', T3b='7', T4='70 (D4)', S05='5', S08='8', S13='13', S14='14', S15='15', S16='16', S29='29', S30='30', S34='34', S37='37', S38='38',
           S47='47', S48='48', S49='49', S56='56', S57='57', S58='58', S59='59', S61='61', S62='62', S63='63', S64='64', S65='65', S71='71', S72='72', S73='73', S76='76',
           S79='79', S80='80', S82='82', S84='84', S89='89', S102='102', S105='105', S141='141', SD1='D1 (line 273)')
