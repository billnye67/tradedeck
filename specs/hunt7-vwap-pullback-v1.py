"""Hunt 7 — VWAP pullback continuation v1. Spec: specs/hunt7-vwap-pullback-v1.md. Research only."""
import sqlite3, json, sys, os, csv, datetime as dt, collections
import numpy as np

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86_400_000; M5 = 300_000
END = int(dt.datetime(2026, 8, 28, tzinfo=dt.UTC).timestamp() * 1000)
SPLIT = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
COINS = ['AVAXUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']  # alphabetical
COMM = 0.0003; RISK = 0.005; SWAP = 0.0005

def load(m, kc=None):
    if kc:
        arr = json.load(open(kc)); a = np.array(arr, dtype=float)
    else:
        con = sqlite3.connect(DB)
        a = np.array(con.execute("select timestamp,open,high,low,close,volume from candles where source='binance_um' and market=? and interval='5m' and timestamp<=? order by timestamp", (m, END)).fetchall(), dtype=float)
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]

def prep(ts, o, h, l, c, v):
    n = len(ts)
    # ATR20 Wilder on 5m
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1)))); tr[0] = h[0] - l[0]
    atr = np.full(n, np.nan); a = tr[:20].mean(); atr[19] = a
    for i in range(20, n): a = (a * 19 + tr[i]) / 20; atr[i] = a
    # daily VWAP with gap invalidation
    day = ts // DAY; vwap = np.full(n, np.nan); valid = np.ones(n, bool)
    cum_pv = 0.0; cum_v = 0.0; cur = -1; broken = False
    tp = (h + l + c) / 3
    for i in range(n):
        if day[i] != cur:
            cur = day[i]; cum_pv = 0.0; cum_v = 0.0; broken = False
        elif ts[i] - ts[i - 1] != M5:
            broken = True
        if broken: valid[i] = False; continue
        cum_pv += tp[i] * v[i]; cum_v += v[i]
        vwap[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    valid &= ~np.isnan(vwap)
    first_ok = np.searchsorted(day, day[0] + 30)  # after 30 complete days (approx: 30 days of data)
    return dict(ts=ts, o=o, h=h, l=l, c=c, v=v, atr=atr, vwap=vwap, valid=valid, day=day, first=first_ok,
                mod=((ts % DAY) // 60_000).astype(int))

def signals(d, N):
    """Return list of (i_setup, side, stop_level) for long(+1)/short(-1) setups at bar i."""
    ts, o, h, l, c, vw, atr, valid, mod = d['ts'], d['o'], d['h'], d['l'], d['c'], d['vwap'], d['atr'], d['valid'], d['mod']
    n = len(ts); out = []
    above = (c > vw); below = (c < vw)
    rng = h - l
    for i in range(max(d['first'], N + 14), n - 1):
        if not valid[i] or np.isnan(atr[i]) or rng[i] <= 0: continue
        if mod[i] < 120 or mod[i] >= 1200: continue           # 02:00-20:00 UTC setup bar open
        if ts[i] - ts[i - N] != N * M5: continue               # contiguous
        if not (valid[i - N:i].all() and valid[i - 13]): continue
        win = slice(i - N, i)
        if not (l[i] <= vw[i] <= h[i]): continue
        pos_in_range = (c[i] - l[i]) / rng[i]
        # LONG
        if above[win].all() and vw[i - 1] > vw[i - 13] and ((c[win] - vw[win]) >= atr[win]).any() \
           and c[i] > vw[i] and c[i] > o[i] and pos_in_range >= 0.75:
            out.append((i, +1, l[i] - 0.25 * atr[i]))
        # SHORT
        elif below[win].all() and vw[i - 1] < vw[i - 13] and ((vw[win] - c[win]) >= atr[win]).any() \
           and c[i] < vw[i] and c[i] < o[i] and pos_in_range <= 0.25:
            out.append((i, -1, h[i] + 0.25 * atr[i]))
    return out

def simulate_coin(d, N, K, slip):
    """Per-coin trade list honoring 1 long + 1 short fill per day, one open per coin. Returns trades with entry/exit ts, R net."""
    ts, o, h, l, c = d['ts'], d['o'], d['h'], d['l'], d['c']
    trades = []; open_until = -1; filled_day = collections.defaultdict(set)
    for i, side, stop in signals(d, N):
        j = i + 1
        if ts[j] <= open_until: continue
        dkey = ts[j] // DAY
        if side in filled_day[dkey]: continue
        fill = o[j] * (1 + slip) if side > 0 else o[j] * (1 - slip)
        if (side > 0 and fill <= l[i]) or (side < 0 and fill >= h[i]): continue
        dist = (fill - stop) if side > 0 else (stop - fill)
        if dist <= 0 or dist / fill < 0.005 or dist / fill > 0.02: continue
        target = fill + side * K * dist
        t_entry = ts[j]; t_time = t_entry + 6 * 3_600_000; t_2300 = (t_entry // DAY) * DAY + 23 * 3_600_000
        t_cut = min(t_time, t_2300)
        exit_px = None; how = None; k = j
        while k < len(ts):
            if ts[k] >= t_cut:
                # time exit at this bar open; honour protective level breached by the open
                if (side > 0 and o[k] <= stop) or (side < 0 and o[k] >= stop): exit_px, how = o[k], 'STOP'
                elif (side > 0 and o[k] >= target) or (side < 0 and o[k] <= target): exit_px, how = o[k], 'TP'
                else: exit_px, how = o[k], 'TIME'
                break
            hit_stop = (l[k] <= stop) if side > 0 else (h[k] >= stop)
            hit_tp = (h[k] >= target) if side > 0 else (l[k] <= target)
            if hit_stop:  # stop first; gap -> worse open
                gap = (o[k] < stop) if side > 0 else (o[k] > stop)
                exit_px, how = (o[k] if gap else stop), 'STOP'; break
            if hit_tp: exit_px, how = target, 'TP'; break
            k += 1
        if exit_px is None: continue
        exit_eff = exit_px * (1 - slip) if side > 0 else exit_px * (1 + slip)
        gross = side * (exit_eff - fill) / fill
        costs = 2 * COMM + (SWAP if ts[k] // DAY != t_entry // DAY else 0)
        net_ret = gross - costs
        R = net_ret * fill / dist
        trades.append(dict(m=None, e=int(t_entry), x=int(ts[k]), R=R, how=how, side=side, i=i, fill=fill, stop=stop, target=target, exit=exit_eff))
        open_until = ts[k]; filled_day[dkey].add(side)
    return trades

def book(trades, max_open=2, s1=None):
    """GLOBAL-2 chronological. If s1 given: #1 has priority at simultaneous entries, never displaced."""
    allt = [dict(t, src='V') for t in trades] + ([dict(t, src='S1') for t in s1] if s1 else [])
    allt.sort(key=lambda t: (t['e'], 0 if t['src'] == 'S1' else 1, t['m']))
    open_ = []; taken = []; blocked_s1 = 0; blocked_v = 0
    for t in allt:
        open_ = [x for x in open_ if x['x'] > t['e']]
        if any(x['m'] == t['m'] for x in open_) or len(open_) >= max_open:
            if t['src'] == 'S1': blocked_s1 += 1
            else: blocked_v += 1
            continue
        open_.append(t); taken.append(t)
    return taken, blocked_s1, blocked_v

def stats(taken, era):
    lo, hi = (None, SPLIT) if era == 'learn' else (SPLIT, END)
    T = sorted([t for t in taken if (lo is None or t['e'] >= lo) and t['e'] < hi], key=lambda t: t['x'])
    if not T: return dict(n=0)
    R = np.array([t['R'] for t in T]); eq = pk = dd = 0.0
    for r in R: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    e = 1.0; pke = 1.0; ddp = 0.0
    for r in R: e *= (1 + RISK * r); pke = max(pke, e); ddp = max(ddp, (pke - e) / pke)
    wins = R[R > 0]; losses = R[R <= 0]
    years = collections.defaultdict(float)
    for t in T: years[dt.datetime.fromtimestamp(t['e'] / 1000, dt.UTC).year] += t['R']
    return dict(n=len(R), R=R.sum(), exp=R.mean(), win=100 * (R > 0).mean(), avgw=wins.mean() if len(wins) else 0, avgl=losses.mean() if len(losses) else 0,
                dd=dd, ddpct=100 * ddp, netpct=100 * (e - 1), time=sum(1 for t in T if t.get('how') == 'TIME'), years={k: round(v, 1) for k, v in sorted(years.items())})

def fmt(s, label):
    if s.get('n', 0) == 0: return f"{label}: no trades"
    return f"{label}: n={s['n']:5d} R={s['R']:+7.1f} exp={s['exp']:+.3f} win={s['win']:4.1f}% avgW={s['avgw']:+.2f} avgL={s['avgl']:+.2f} sumR-DD={s['dd']:5.1f} eqDD={s['ddpct']:4.1f}% net={s['netpct']:+6.1f}% time-exits={s.get('time',0)} years={s['years']}"

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'learn'
    slip = 0.0005 if '--stress' in sys.argv else 0.0002
    kc = mode == 'kucoin'
    coins = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'] if kc else COINS
    data = {}
    for m in coins:
        p = os.path.join(HERE, '..', 'kucoin', f'kc_{m}_5m.json') if kc else None
        data[m] = prep(*load(m, p)); print('prep', m, len(data[m]['ts']), flush=True)
    grid = [(N, K) for N in (9, 12, 15) for K in (2, 3, 4)]
    if mode in ('learn', 'exam', 'kucoin'):
        era = 'learn' if mode == 'learn' else 'exam'
        cells = grid if mode == 'learn' else [(12, 3), (9, 3), (15, 3), (12, 2), (12, 4)]
        for N, K in cells:
            allt = []
            for m in coins:
                tr = simulate_coin(data[m], N, K, slip)
                for t in tr: t['m'] = m
                allt += tr
            indep = stats(allt, era); taken, _, _ = book(allt); bk = stats(taken, era)
            print(f"{era.upper()} N={N:2d} K={K} | " + fmt(indep, 'INDEPENDENT'))
            print(f"{'':16s}| " + fmt(bk, 'GLOBAL-2   '), flush=True)
            if (N, K) == (12, 3) and mode == 'learn':
                print('\nSAMPLE 20 SETUPS (primary, chronological):')
                for t in sorted(allt, key=lambda t: t['e'])[:20]:
                    print(f"  {t['m'][:-4]:5s} {'LONG ' if t['side']>0 else 'SHORT'} entry {dt.datetime.fromtimestamp(t['e']/1000, dt.UTC):%Y-%m-%d %H:%M} fill {t['fill']:.4f} stop {t['stop']:.4f} tgt {t['target']:.4f} -> {t['how']:4s} exit {dt.datetime.fromtimestamp(t['x']/1000, dt.UTC):%H:%M} {t['exit']:.4f} R {t['R']:+.2f}")
                print()
    elif mode == 'merged':
        PB = {'BTCUSDT', 'ETHUSDT', 'XRPUSDT'}
        s1 = [dict(m=r[0], e=int(r[1]), x=int(r[2]), R=float(r[3])) for r in csv.reader(open(os.path.join(HERE, '..', 'ind_s1.csv'))) if r[0] in PB]
        allt = []
        for m in coins:
            tr = simulate_coin(data[m], 12, 3, slip)
            for t in tr: t['m'] = m
            allt += tr
        for era in ('learn', 'exam'):
            s1only, _, _ = book([], s1=s1); a = stats(s1only, era)
            merged, bs1, bv = book(allt, s1=s1); b = stats(merged, era)
            v_in = stats([t for t in merged if t['src'] == 'V'], era); s_in = stats([t for t in merged if t['src'] == 'S1'], era)
            print(f"{era.upper()} #1 ONLY : " + fmt(a, 'book'))
            print(f"{era.upper()} MERGED  : " + fmt(b, 'book') + f"\n           of which #1 n={s_in.get('n',0)} R={s_in.get('R',0):+.1f} | VWAP n={v_in.get('n',0)} R={v_in.get('R',0):+.1f} | #1 blocked by VWAP {bs1} | VWAP blocked {bv}")
