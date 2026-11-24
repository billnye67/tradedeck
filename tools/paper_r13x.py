raise SystemExit  # 2026-10-14: R13X dead (registry 148-149); timer awaiting admin delete
"""Paper run of R13X (registry 135) on a free live feed (Yahoo 5m). Records theoretical signals/outcomes only; no orders.
Rule = r13_midnight.day_trade on 15m bars, both sides, traded OPPOSITE (cell T15/NB/OPP); stop = entry +/- risk; exit 16:55 NY.
Pairs: EURUSD, USDJPY, AUDUSD (GBPUSD left out: its exam edge survives only ~0.2 extra pip of cost).
Log: specs/paper/r13x_log.csv, one row per (date, pair, side), rewritten each run. Compare later with the backtest (+0.103R/trade)."""
import os, sys, json, urllib.request, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r13_midnight as R
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'paper', 'r13x_log.csv')
PAIRS = ('EURUSD', 'USDJPY', 'AUDUSD')

def fetch(m):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{m}=X?interval=5m&range=5d'
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))
    r = j['chart']['result'][0]; q = r['indicators']['quote'][0]
    d = pd.DataFrame({'t': np.array(r['timestamp']) * 1000, 'o': q['open'], 'h': q['high'], 'l': q['low'], 'c': q['close']}).dropna()
    now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    d = d[d.t <= now - 5 * 60 * 1000]                                   # drop the bar still forming
    return L._ny(d)

def trades(m, d):
    pip = L.pip_of(m); keys = sorted(set(d.date)); by = dict(tuple(d.groupby('date'))); out = []
    for i in range(1, len(keys)):
        day = keys[i]
        if pd.Timestamp(day).dayofweek > 4 or by[day].hr.min() > 0.5: continue
        for side in (1, -1):
            t = R.day_trade(by[keys[i - 1]], by[day], side, pip, '15min')
            if t is None: continue
            b = t['b']; h, l, c, hr = b.h.values, b.l.values, b.c.values, b.hr.values
            s = -side; risk = abs(t['E'] - t['stop']); stop = t['E'] - s * risk
            ee = [k for k in range(t['i0'], len(h)) if hr[k] >= 16.9]; end = ee[0] if ee else None
            ny_now = pd.Timestamp.now(tz='America/New_York')
            if end is None and (pd.Timestamp(day) < ny_now.normalize().tz_localize(None) or ny_now.hour + ny_now.minute / 60 >= 16.9):
                end = len(h)                                             # day finished: exit at the last bar, as the backtest does
            res, xp = 'OPEN', None
            for k in range(t['q'], end if end is not None else len(h)):
                if (l[k] <= stop) if s == 1 else (h[k] >= stop): res, xp = 'STOP', stop; break
            if res == 'OPEN' and end is not None: res, xp = 'TIME', c[end - 1]
            row = dict(date=str(day), pair=m, side='BUY' if s == 1 else 'SELL', signal_ny=f"{hr[t['q']]:.2f}", entry=round(t['E'], 6), stop=round(stop, 6),
                       risk_pips=round(risk / pip, 1), status=res, exit=None if xp is None else round(xp, 6))
            if xp is not None:
                g = s * (xp - t['E']) / risk; row.update(gross_R=round(g, 3), net_R=round(g - L.cost_R('broker_fx', m, t['E'], risk), 3))
            out.append(row)
    return out

if __name__ == '__main__':
    rows = []
    for m in PAIRS:
        try: rows += trades(m, fetch(m))
        except Exception as e: print(m, 'fetch failed:', e)
    new = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    old = pd.read_csv(LOG) if os.path.exists(LOG) else pd.DataFrame()
    allr = pd.concat([old, new]).drop_duplicates(['date', 'pair', 'side'], keep='last').sort_values(['date', 'pair', 'side']) if len(new) else old
    allr.to_csv(LOG, index=False)
    done = allr[allr.status != 'OPEN'] if len(allr) else allr
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} trades logged {len(allr)}, closed {len(done)}, avg net R {done.net_R.mean() if len(done) else float('nan'):+.3f}")
