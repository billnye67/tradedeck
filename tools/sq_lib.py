"""SQ sequence search (specs/SQ-sequence-search.md): level swept -> confirmation -> entry, BTC/ETH/XRP 5m.
Levels/sweeps follow TradeDeck.Research SweepDetector (#1); the 5m chain follows SetupDetector (#1). LAB ONLY."""
import os, glob, numpy as np, pandas as pd
from numba import njit

KL = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\flow\klines5m')
COINS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT']
TICK = {'BTCUSDT': 0.1, 'ETHUSDT': 0.01, 'XRPUSDT': 0.0001}
COST_BP = 8.5            # 2 x (0.25 half-spread + 3 commission + 1 slip)
NIGHT_BP = 5.0           # per 00:30 UTC crossing
LEVELS = ['PDHL', 'ASIA', 'LONDON', 'WEEKLY', 'MIDNIGHT', 'SWING4H', 'ROUND', 'S1SWING']
CONFS = ['D', 'MSS', 'DV']
ENTRIES = ['FVG', 'CE', 'OB', 'BRK', 'IFVG']
H = {'1h': 12, '4h': 48, '12h': 144}
# #1 constants
MIN_BEFORE, RETURN_W, DISP_AWAY, ATR_LEN = 8, 3, 2.0, 20
PIV, PROM, EXPIRY = 3, 8, 672
TRIG_PIV, DISP_W, FVG_AFTER, RETRACE_W = 2, 24, 3, 36
MIN_BREACH, MIN_FVG_RATIO, VOL_MULT = 0.0013, 0.35, 2.0


def load5(coin):
    fs = sorted(glob.glob(os.path.join(KL, f'{coin}-5m-*.csv')))
    parts = []
    for f in fs:
        d = pd.read_csv(f, header=None, low_memory=False)
        d = d[d.iloc[:, 0].astype(str).str.isdigit()].iloc[:, :6].astype(float)
        parts.append(d)
    d = pd.concat(parts); d.columns = ['t', 'o', 'h', 'l', 'c', 'v']
    d = d.drop_duplicates('t').sort_values('t').reset_index(drop=True)
    d['t'] = d.t.astype('int64')
    return d


def to15(d5):
    g = d5.assign(k=(d5.t // 900000) * 900000).groupby('k')
    d = pd.DataFrame({'t': g.t.first().index.values, 'o': g.o.first().values, 'h': g.h.max().values,
                      'l': g.l.min().values, 'c': g.c.last().values})
    return d.reset_index(drop=True)


def resample(d5, ms):
    g = d5.assign(k=(d5.t // ms) * ms).groupby('k')
    return pd.DataFrame({'t': g.t.first().index.values, 'h': g.h.max().values, 'l': g.l.min().values,
                         'o': g.o.first().values, 'c': g.c.last().values}).reset_index(drop=True)


@njit(cache=True)
def atr_arr(h, l, c, n):
    out = np.zeros(len(h)); trs = np.zeros(len(h)); s = 0.0
    for i in range(1, len(h)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs[i] = tr; s += tr
        if i > n: s -= trs[i - n]
        if i >= n: out[i] = s / n
    return out


@njit(cache=True)
def disp_away(h, l, atr, known, breach, level, is_high, from_off):
    a = atr[known]
    if a <= 0: return 0.0
    f = known + from_off; to = breach - 1
    if to < f: return 0.0
    if is_high:
        ext = 1e300
        for i in range(f, to + 1): ext = min(ext, l[i])
        return (level - ext) / a
    ext = -1e300
    for i in range(f, to + 1): ext = max(ext, h[i])
    return (ext - level) / a


@njit(cache=True)
def walk_known(h, l, c, atr, known, level, is_high, valid_until, from_off):
    """SweepDetector.WalkKnownLevel: returns sweep 15m index or -1."""
    last = min(valid_until, len(h)) - 1
    for j in range(known + 1, last + 1):
        touched = h[j] > level if is_high else l[j] < level
        if not touched: continue
        if j - known < MIN_BEFORE: return -1
        if disp_away(h, l, atr, known, j, level, is_high, from_off) < DISP_AWAY: return -1
        rl = min(j + RETURN_W - 1, len(h) - 1)
        for r in range(j, rl + 1):
            if (c[r] < level) if is_high else (c[r] > level): return j
        return -1
    return -1


@njit(cache=True)
def s1_pivots(h, l, is_high):
    """SweepDetector pivot + prominence test; returns pivot indices."""
    n = len(h); out = np.empty(n, np.int64); k = 0
    for i in range(max(PIV, PROM), n - PIV - 1):
        lv = h[i] if is_high else l[i]; ok = True
        for q in range(1, PIV + 1):
            if is_high:
                if h[i - q] >= lv or h[i + q] >= lv: ok = False; break
            else:
                if l[i - q] <= lv or l[i + q] <= lv: ok = False; break
        if not ok: continue
        for q in range(PIV + 1, PROM + 1):
            idx = i - q
            if idx < 0: break
            if is_high and h[idx] > lv: ok = False; break
            if (not is_high) and l[idx] < lv: ok = False; break
        if ok: out[k] = i; k += 1
    return out[:k]


@njit(cache=True)
def walk_pivot(h, l, c, atr, piv, level, is_high):
    """SweepDetector.TryAddCandidate steps 3-5 (D_ONLY) from a pivot at `piv` with a given level."""
    for j in range(piv + PIV + 1, len(h)):
        if j - piv > EXPIRY: return -1
        touched = h[j] > level if is_high else l[j] < level
        if not touched: continue
        if j - piv < MIN_BEFORE: return -1
        if disp_away(h, l, atr, piv, j, level, is_high, PIV) < DISP_AWAY: return -1
        rl = min(j + RETURN_W - 1, len(h) - 1)
        for r in range(j, rl + 1):
            if (c[r] < level) if is_high else (c[r] > level): return j
        return -1
    return -1


def ny_offset_ms(ts_ms):
    t = pd.to_datetime(ts_ms, unit='ms', utc=True).tz_convert('America/New_York')
    return np.array([x.utcoffset().total_seconds() * 1000 for x in t])


def make_levels(fam, d15, d5, atr15, fake=False):
    """List of (known_idx15, level, is_high, valid_until_idx15, kind) where kind 'known' or 'pivot'."""
    t = d15.t.values; h, l, o, c = d15.h.values, d15.l.values, d15.o.values, d15.c.values
    out = []
    idx_of = {int(x): i for i, x in enumerate(t)}
    day = t // 86400000
    def first_idx_ge(ts):
        return int(np.searchsorted(t, ts))
    def add(k, lv, hi, vu, kind='known'):
        if k < ATR_LEN + 1 or k >= len(t): return
        if fake:
            a = atr15[k]
            lv = lv + a if hi else lv - a
        out.append((k, lv, hi, vu, kind))
    if fam in ('PDHL', 'ASIA', 'LONDON', 'ROUND'):
        udays = np.unique(day)
        starts = np.searchsorted(day, udays)
        for n in range(1, len(udays)):
            s0, s1 = starts[n - 1], starts[n]
            e1 = starts[n + 1] if n + 1 < len(udays) else len(t)
            if fam == 'PDHL':
                add(s1, h[s0:s1].max(), True, e1); add(s1, l[s0:s1].min(), False, e1)
            elif fam in ('ASIA', 'LONDON'):
                a0, a1 = (0, 7) if fam == 'ASIA' else (7, 12)
                base = udays[n] * 86400000
                i0, i1 = first_idx_ge(base + a0 * 3600000), first_idx_ge(base + a1 * 3600000)
                if i1 - i0 < 4: continue
                add(i1, h[i0:i1].max(), True, e1); add(i1, l[i0:i1].min(), False, e1)
            else:
                px = o[s1]
                if px <= 0: continue
                step = 10 ** (np.floor(np.log10(px)) - 1)
                up, dn = np.ceil(px / step) * step, np.floor(px / step) * step
                if up == px: up += step
                if dn == px: dn -= step
                for lv in (up, dn):
                    add(s1, lv, True, e1); add(s1, lv, False, e1)
    elif fam == 'WEEKLY':
        wk = (t // 86400000 + 3) // 7          # weeks starting Monday (epoch day 0 = Thu)
        uw = np.unique(wk); st = np.searchsorted(wk, uw)
        for n in range(1, len(uw)):
            s0, s1 = st[n - 1], st[n]; e1 = st[n + 1] if n + 1 < len(uw) else len(t)
            add(s1, h[s0:s1].max(), True, e1); add(s1, l[s0:s1].min(), False, e1)
            add(s1, o[s1], True, e1); add(s1, o[s1], False, e1)
    elif fam == 'MIDNIGHT':
        off = ny_offset_ms(t)
        loc = t + off
        locmin = (loc // 60000) % 1440
        for i in np.where(locmin == 0)[0]:
            # valid until 17:00 NY = +68 15m bars
            add(int(i), o[i], True, int(i) + 68); add(int(i), o[i], False, int(i) + 68)
    elif fam == 'SWING4H':
        d4 = resample(d5, 4 * 3600000); h4, l4 = d4.h.values, d4.l.values; t4 = d4.t.values
        for i in range(2, len(d4) - 2):
            for hi in (True, False):
                lv = h4[i] if hi else l4[i]
                if hi and not (h4[i] > max(h4[i-1], h4[i-2], h4[i+1], h4[i+2])): continue
                if (not hi) and not (l4[i] < min(l4[i-1], l4[i-2], l4[i+1], l4[i+2])): continue
                k = first_idx_ge(t4[i + 2] + 4 * 3600000)       # known after the confirming bar closes
                add(k, lv, hi, k + 7 * 96)
    elif fam == 'S1SWING':
        for hi in (True, False):
            for p in s1_pivots(h, l, hi):
                lv = h[p] if hi else l[p]
                if fake:
                    a = atr15[p]; lv = lv + a if hi else lv - a
                out.append((int(p), lv, hi, int(p) + EXPIRY + 1, 'pivot'))
    return out


def find_sweeps(fam, d15, d5, atr15, fake=False):
    h, l, c = d15.h.values, d15.l.values, d15.c.values
    res = []
    for k, lv, hi, vu, kind in make_levels(fam, d15, d5, atr15, fake):
        if kind == 'pivot':
            j = walk_pivot(h, l, c, atr15, k, lv, hi)
        else:
            j = walk_known(h, l, c, atr15, k, lv, hi, vu, 0)
        if j >= 0: res.append((j, lv, hi))
    return res


# ---------------- 5m chain (SetupDetector port) ----------------
def _deeper(H5, L5, i, than, short):
    return H5[i] > H5[than] if short else L5[i] < L5[than]

def _deeper_within(H5, L5, ext, upto, short):
    best = -1; lim = min(upto, len(H5) - 1)
    for k in range(ext + 1, lim + 1):
        if not _deeper(H5, L5, k, ext, short): continue
        if best < 0 or _deeper(H5, L5, k, best, short): best = k
    return best

def _is_pivot(H5, L5, i, s, opp_short):
    if i - s < 0 or i + s >= len(H5): return False
    for k in range(1, s + 1):
        if opp_short:
            if L5[i - k] < L5[i] or L5[i + k] < L5[i]: return False
        else:
            if H5[i - k] > H5[i] or H5[i + k] > H5[i]: return False
    return True

def _find_trigger(H5, L5, ext, short, breach, level):
    for i in range(ext - 1, TRIG_PIV - 1, -1):
        if i >= breach: continue                                 # PreBreachTriggerOnly
        if not _is_pivot(H5, L5, i, TRIG_PIV, short): continue
        tp = L5[i] if short else H5[i]
        if short and tp >= level: continue                       # hard invariant (bug #4)
        if (not short) and tp <= level: continue
        broken = False
        for k in range(i + 1, ext):
            if (L5[k] < tp) if short else (H5[k] > tp): broken = True; break
        if not broken: return i
    return -1

def _reclaimed(C5, ext, level, upto, short):
    for k in range(ext + 1, min(upto, len(C5) - 1) + 1):
        if (C5[k] < level) if short else (C5[k] > level): return True
    return False


def chain_d(c5, s5, level, short):
    """#1's chain up to displacement + FVG. Returns dict or None."""
    H5, L5, C5 = c5['h'], c5['l'], c5['c']; n = len(H5)
    breach = -1
    for i in range(s5, min(s5 + 3, n)):
        if (H5[i] > level) if short else (L5[i] < level): breach = i; break
    if breach < 0: breach = s5
    reclaim = breach
    for i in range(breach, min(breach + 12, n)):
        reclaim = i
        if (C5[i] < level) if short else (C5[i] > level): break
    ext = breach
    for i in range(breach, reclaim + 1):
        if _deeper(H5, L5, i, ext, short): ext = i
    for _ in range(200):
        if not _reclaimed(C5, ext, level, ext + DISP_W, short):
            d = _deeper_within(H5, L5, ext, ext + DISP_W, short)
            if d >= 0: ext = d; continue
            return None
        trig = _find_trigger(H5, L5, ext, short, breach, level)
        if trig < 0:
            d = _deeper_within(H5, L5, ext, ext + DISP_W, short)
            if d >= 0: ext = d; continue
            return None
        tp = L5[trig] if short else H5[trig]
        disp = -1
        for i in range(ext + 1, min(ext + DISP_W, n - 1) + 1):
            if (C5[i] < tp) if short else (C5[i] > tp): disp = i; break
        if disp < 0:
            d = _deeper_within(H5, L5, ext, ext + DISP_W, short)
            if d >= 0: ext = d; continue
            return None
        d2 = _deeper_within(H5, L5, ext, disp - 1, short)
        if d2 >= 0: ext = d2; continue
        break
    else:
        return None
    sweep_ext = H5[ext] if short else L5[ext]
    if abs(sweep_ext - level) / level < MIN_BREACH: return None
    fvg = find_fvg(H5, L5, disp, min(disp + FVG_AFTER, n - 2), short)
    return dict(breach=breach, ext=ext, sweep_ext=sweep_ext, trig=trig, conf=disp, fvg=fvg)


def find_fvg(H5, L5, a, b, short):
    """first 3-candle FVG with mid in [a, b] in the move direction: (mid, proximal, distal)."""
    for mid in range(max(a, 1), b + 1):
        if mid + 1 >= len(H5): break
        if short:
            top, bot = L5[mid - 1], H5[mid + 1]
            if top > bot: return (mid, bot, top)
        else:
            bot, top = H5[mid - 1], L5[mid + 1]
            if top > bot: return (mid, top, bot)
    return None


def fvg_ratio(H5, L5, disp, fvg):
    rng = [H5[disp - k] - L5[disp - k] for k in range(1, 21) if disp - k >= 0]
    base = np.mean(rng) if rng else 0
    return abs(fvg[2] - fvg[1]) / base if base > 0 else 0


def chain_mss(c5, s5, level, short, sw_prev):
    """MSS: close beyond the most recent opposite 15m swing within 24 bars of the extreme."""
    H5, L5, C5, T5 = c5['h'], c5['l'], c5['c'], c5['t']; n = len(H5)
    breach = -1
    for i in range(s5, min(s5 + 3, n)):
        if (H5[i] > level) if short else (L5[i] < level): breach = i; break
    if breach < 0: breach = s5
    swl = sw_prev(T5[breach], short)          # most recent opposite 15m swing confirmed before the breach
    if swl is None or ((swl >= level) if short else (swl <= level)): return None
    ext = breach
    for i in range(breach, min(breach + 12 + DISP_W, n)):
        if _deeper(H5, L5, i, ext, short): ext = i
        if i - ext > DISP_W: return None
        if (C5[i] < swl) if short else (C5[i] > swl):      # beyond swl => back inside the level too
            sweep_ext = H5[ext] if short else L5[ext]
            if abs(sweep_ext - level) / level < MIN_BREACH: return None
            fvg = find_fvg(H5, L5, ext + 1, min(i + FVG_AFTER, n - 2), short)
            return dict(breach=breach, ext=ext, sweep_ext=sweep_ext, trig=None, conf=i, fvg=fvg)
    return None


def zones(c5, ch, short, conf_kind):
    """entry zones per entry type: {name: (limit, rest_from, distal_or_None)}"""
    H5, L5, O5, C5 = c5['h'], c5['l'], c5['o'], c5['c']; n = len(H5)
    ext, conf = ch['ext'], ch['conf']; z = {}
    f = ch['fvg']
    if f is not None:
        mid, prox, dist = f
        rest = max(mid + 2, conf + 1)
        z['FVG'] = (prox, rest, dist, dist)
        z['CE'] = ((prox + dist) / 2, rest, dist, dist)
    # OB: last opposite-colour candle in [ext-3, conf-1]
    for i in range(conf - 1, max(ext - 3, 0) - 1, -1):
        if (C5[i] > O5[i]) if short else (C5[i] < O5[i]):
            z['OB'] = (O5[i], conf + 1, None, H5[i] if short else L5[i]); break
    # Breaker: last same-direction candle before the extreme
    lo = (ch['trig'] - 3) if ch['trig'] is not None else ext - 12
    for i in range(ext - 1, max(lo, 0) - 1, -1):
        if (C5[i] < O5[i]) if short else (C5[i] > O5[i]):
            z['BRK'] = (L5[i] if short else H5[i], conf + 1, None, H5[i] if short else L5[i]); break
    # IFVG: opposite FVG with mid in [trig, ext] (MSS: [ext-24, ext]) inverted by conf+3
    a = ch['trig'] if ch['trig'] is not None else ext - 24
    best = None
    for mid in range(max(a, 1), ext + 1):
        if mid + 1 >= n: break
        if short:       # bullish FVG formed on the way up, inverted by a close below its bottom
            bot, top = H5[mid - 1], L5[mid + 1]
            if top > bot:
                for k in range(mid + 2, min(conf + 3, n - 1) + 1):
                    if C5[k] < bot: best = (bot, max(k, conf) + 1, top); break
        else:           # bearish FVG on the way down, inverted by a close above its top
            top, bot = L5[mid - 1], H5[mid + 1]
            if top > bot:
                for k in range(mid + 2, min(conf + 3, n - 1) + 1):
                    if C5[k] > top: best = (top, max(k, conf) + 1, bot); break
    if best is not None: z['IFVG'] = (best[0], best[1], None, best[2])
    # limit must be on the retrace side of the confirmation close
    # Amendment C: the limit must sit on the retrace side of the close of the bar before the order rests
    return {k: v for k, v in z.items() if v[1] - 1 < n and ((v[0] > C5[v[1] - 1]) if short else (v[0] < C5[v[1] - 1]))}


def fill(c5, limit, rest, dist, sweep_ext, short, tick):
    H5, L5, C5 = c5['h'], c5['l'], c5['c']; n = len(H5)
    for i in range(rest, min(rest + RETRACE_W, n)):
        if (H5[i] >= limit + tick) if short else (L5[i] <= limit - tick): return i
        if (H5[i] > sweep_ext) if short else (L5[i] < sweep_ext): return -1
        if dist is not None and ((C5[i] > dist) if short else (C5[i] < dist)): return -1
    return -1


MAX_HOLD = 2016
STOP_SLIP_BP = 2.0


def stop_dist(entry, stop, tick):
    """tick-safe minimum: max(5 ticks, 0.15% of entry). Returns the (possibly widened) distance, or -1 if wrong side."""
    return max(5 * tick, 0.0015 * entry)


@njit(cache=True)
def sim_exit(h, l, c, fi, entry, stop, tgt, short, maxhold):
    """returns (R_gross, exit_index, stopped). stop on the fill bar counts; target only after it; tie = stop."""
    n = len(h)
    if (h[fi] >= stop) if short else (l[fi] <= stop):
        return -1.0, fi, 1
    risk = abs(entry - stop)
    last = min(fi + maxhold, n - 1)
    for i in range(fi + 1, last + 1):
        hs = (h[i] >= stop) if short else (l[i] <= stop)
        if hs: return -1.0, i, 1
        ht = (l[i] <= tgt) if short else (h[i] >= tgt)
        if ht: return 3.0, i, 0
    r = ((entry - c[last]) if short else (c[last] - entry)) / risk
    return r, last, 0
