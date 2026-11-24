"""COMBO PAPER BOT (registry 178-180): v3 entry brain + v2 direction brain, paper only, NO orders anywhere.
Each run (every 5 min): refresh Binance data for 23 coins -> write it in the SAME file format as the history archives -> run the SAME
builders (v2_build.build, v3_build.build) -> final models -> rule: best coin x side with v3 score > 0.5 AND v2 agreeing (>= 0),
max 3 open, 1 per coin -> paper trade: entry = next 5m open, stop behind last 6 candles (+0.1 ATR5, >= 0.15% and >= 5 ticks),
target 3R, out after 12h. Writes Web/lab/combo_paper.json for the Lab card and logs every score for the daily match check."""
import os; os.environ.setdefault('LOKY_MAX_CPU_COUNT', '11')   # stops joblib launching PowerShell (visible window) to count CPU cores
import os, sys, json, time, glob, joblib, urllib.request, numpy as np, pandas as pd
os.environ['V3_KEEP_UNLABELED'] = '1'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_build as V2, v3_build as V3, v2_model_feats as F2
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); MD = os.path.join(LAB, 'models'); LIVE = os.path.join(LAB, 'live_combo')
FLOW = os.path.join(LIVE, 'flow'); CACHE = os.path.join(LIVE, 'cache')
for d in ('klines5m', 'funding', 'metrics'): os.makedirs(os.path.join(FLOW, d), exist_ok=True)
for d in ('v2', 'v3', 'cache'): os.makedirs(os.path.join(LIVE, d), exist_ok=True)
WEB = [os.path.join(ROOT, 'TradeDeck', 'Web', 'lab'), os.path.join(ROOT, 'TradeDeck', 'bin', 'Debug', 'net8.0-windows', 'Web', 'lab')]
COINS = [c + 'USDT' for c in ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE')]   # the user 2026-10-16: big liquid coins only (registry 181)
T = 0.5; MAXOPEN = 3; FEE = 0.0005; SWAP = 0.0005; DAYS = 75
STATE = os.path.join(LIVE, 'paper_state.json'); SCORES = os.path.join(LIVE, 'scores.csv')
# point the shared builders at the live folder (same code, different files)
V2.FLOW = FLOW; V2.END = pd.Timestamp('2100-01-01', tz='UTC'); V2.OUT = os.path.join(LIVE, 'v2'); V2.LIVE = []; V3.LAB = LIVE; V3.OUT = os.path.join(LIVE, 'v3')
UA = {'User-Agent': 'Mozilla/5.0'}
RELAY = os.environ.get('TD_BINANCE_RELAY', 'https://your-relay.example.com')   # optional relay for the exchange's public API; set TD_BINANCE_RELAY to your own
def get(u):
    last = None
    for k in range(4):
        for base in ((u,) if k == 0 else (u.replace('https://fapi.binance.com', RELAY), u)):   # direct first; from the second try go through the relay
            try: return json.load(urllib.request.urlopen(urllib.request.Request(base, headers=UA), timeout=60))
            except Exception as e: last = e
        time.sleep(2 + 2 * k)
    raise last
KCOLS = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'count', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore']
def refresh(coin, now_ms):
    # klines: cache + only what's new; drop the candle that is still forming
    f = os.path.join(CACHE, f'{coin}_k.parquet'); K = pd.read_parquet(f) if os.path.exists(f) else pd.DataFrame(columns=KCOLS)
    start = int(K.open_time.max()) + 300000 if len(K) else now_ms - DAYS * 86400000
    while start < now_ms - 300000:
        r = get(f'https://fapi.binance.com/fapi/v1/klines?symbol={coin}&interval=5m&startTime={start}&limit=1500')
        if not r: break
        K = pd.concat([K, pd.DataFrame(r, columns=KCOLS)]); start = int(r[-1][0]) + 300000
        if len(r) < 1500: break
    K = K.astype({'open_time': 'int64', 'close_time': 'int64'}); K = K[K.close_time < now_ms].drop_duplicates('open_time').sort_values('open_time')
    K = K[K.open_time >= now_ms - DAYS * 86400000]; K.to_parquet(f, index=False)
    K.to_csv(os.path.join(FLOW, 'klines5m', f'{coin}-5m-live.csv'), index=False)
    # funding (enough for the 30-day stats even on 4h-interval coins)
    fr = get(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={coin}&startTime={now_ms - 40 * 86400000}&limit=1000')
    pd.DataFrame({'calc_time': [int(x['fundingTime']) for x in fr], 'funding_interval_hours': 8, 'last_funding_rate': [float(x['fundingRate']) for x in fr]}).to_csv(
        os.path.join(FLOW, 'funding', f'{coin}-fundingRate-live.csv'), index=False)
    # metrics (OI, long/short, taker ratio): hourly refresh is enough for the hourly v2 inputs; keep 10 days
    fm = os.path.join(CACHE, f'{coin}_m.parquet'); M = pd.read_parquet(fm) if os.path.exists(fm) else pd.DataFrame()
    if not len(M) or int(M.ts.max()) < now_ms - 3600000 or time.gmtime(now_ms / 1000).tm_min < 5:
        st = now_ms - 10 * 86400000 if not len(M) else int(M.ts.max()) - 3600000; parts = {}
        for name, ep, key in (('oi', 'openInterestHist', None), ('acc', 'topLongShortAccountRatio', 'longShortRatio'), ('pos', 'topLongShortPositionRatio', 'longShortRatio'),
                              ('glob', 'globalLongShortAccountRatio', 'longShortRatio'), ('tk', 'takerlongshortRatio', 'buySellRatio')):
            rows, s = [], st
            while s < now_ms:
                r = get(f'https://fapi.binance.com/futures/data/{ep}?symbol={coin}&period=5m&startTime={s}&endTime={min(now_ms, s + 499 * 300000)}&limit=500')
                rows += r; s = s + 500 * 300000
            d = pd.DataFrame(rows)
            if d.empty: continue
            d['ts'] = d.timestamp.astype('int64')
            parts[name] = d.set_index('ts')[['sumOpenInterest', 'sumOpenInterestValue']].astype(float) if name == 'oi' else d.set_index('ts')[[key]].astype(float).rename(columns={key: name})
        if 'oi' in parts:
            N = parts['oi'].join([parts[k] for k in ('acc', 'pos', 'glob', 'tk') if k in parts], how='left').reset_index()
            M = pd.concat([M, N]).drop_duplicates('ts', keep='last').sort_values('ts'); M = M[M.ts >= now_ms - 10 * 86400000]; M.to_parquet(fm, index=False)
    if len(M):
        pd.DataFrame({'create_time': pd.to_datetime(M.ts, unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S'), 'symbol': coin, 'sum_open_interest': M.sumOpenInterest,
                      'sum_open_interest_value': M.sumOpenInterestValue, 'count_toptrader_long_short_ratio': M.get('acc'), 'sum_toptrader_long_short_ratio': M.get('pos'),
                      'count_long_short_ratio': M.get('glob'), 'sum_taker_long_short_vol_ratio': M.get('tk')}).to_csv(os.path.join(FLOW, 'metrics', f'{coin}-metrics-live.csv'), index=False)
    return K
def tick_of(K):
    v = np.unique(np.round(pd.concat([K.open, K.high, K.low, K.close]).astype(float).values[-8640:], 10)); d = np.diff(v); return float(d[d > 0].min()) if len(d) else np.nan
START = '2026-10-16'; GRAD_DAYS = 14
def checks(state):
    r = [t['R'] for t in state['closed']]; n = len(r); f = os.path.join(LIVE, 'checks.json')
    days = (json.load(open(f)) if os.path.exists(f) else {'days': []})['days']
    clean = 0
    for d in sorted(days, key=lambda d: d['day'], reverse=True):
        if d['status'] == 'ok': clean += 1
        elif d['status'] in ('waiting',): continue
        else: break
    last = sorted(days, key=lambda d: d['day'])[-1] if days else None
    floor = 0.15 * n - 2.5 * 1.7 * np.sqrt(n) if n else None             # backtest +0.15R/trade, ~1.7R spread per trade
    safety_bad = n >= 10 and sum(r) < floor
    match = dict(state='red' if last and last['status'] in ('mismatch', 'error') else ('green' if clean >= GRAD_DAYS else 'yellow'),
                 text=(f"{clean} of {GRAD_DAYS} clean days" + (f" · last: {last['day']} {last['status']} ({last['note']})" if last else ' · first check runs tomorrow morning')))
    cost = dict(state='yellow', text='waiting: reading Broker prices at each signal is not set up yet')
    safety = dict(state='red' if safety_bad else 'green', text=(f"{n} paper trades, total {sum(r):+.1f}R" + (f" (floor {floor:+.1f}R)" if n >= 10 else ' (needs 10 trades before it can trip)')))
    grad = (pd.Timestamp(START) + pd.Timedelta(days=GRAD_DAYS)).strftime('%b %d')
    light = 'red' if 'red' in (match['state'], safety['state']) else ('green' if match['state'] == cost['state'] == safety['state'] == 'green' else 'yellow')
    head = {'red': 'PAUSED: ' + (safety['text'] if safety_bad else match['text']), 'green': 'READY TO GRADUATE: your call', 'yellow': f'testing · earliest graduation {grad}'}[light]
    return dict(light=light, headline=head, match=match, cost=cost, safety=safety, grad_date=grad, paused=light == 'red')
def write_web(state, scan):
    closed = state['closed']; r = [t['R'] for t in closed]
    obj = dict(checks=checks(state), updated=time.strftime('%Y-%m-%d %H:%M:%S'), status=state.get('status', ''), open=state['open'], closed=closed[-200:], last_scan=scan,
               stats=dict(trades=len(r), R=round(float(np.sum(r)), 2) if r else 0, avg=round(float(np.mean(r)), 3) if r else None, wins=int(sum(x > 2 for x in r)),
                          dollars_at_125=round(float(np.sum(r)) * 125, 0) if r else 0),
               rules=['Every 5 min: score every 5-min candle on 23 coins (entry brain) and read the hourly direction brain',
                      'Trade only if the entry score > 0.5 AND the direction brain agrees', 'Entry next candle open; stop behind the last 30 min of price; target 3x the stop; out after 12h',
                      'Max 3 open, 1 per coin. PAPER ONLY: no orders are sent anywhere'])
    for w in WEB:
        if os.path.isdir(w): json.dump(obj, open(os.path.join(w, 'combo_paper.json'), 'w'), separators=(',', ':'))
def run():
    now_ms = int(time.time() * 1000); state = json.load(open(STATE)) if os.path.exists(STATE) else dict(open=[], closed=[], pending=[])
    Ks = {c: refresh(c, now_ms) for c in COINS}
    # 1) settle pending entries and open trades with the newly closed candles
    for p in state.pop('pending', []):
        K = Ks[p['coin']]; nx = K[K.open_time == p['entry_bar']]
        if len(nx): e = float(nx.open.iloc[0]); R = abs(e - p['stop']) if (p['side'] == 1 and p['stop'] < e) or (p['side'] == -1 and p['stop'] > e) else None
        else: state.setdefault('pending', []).append(p); continue
        md = max(0.0015 * e, 5 * p['tick']); st = min(p['stop'], e - md) if p['side'] == 1 else max(p['stop'], e + md); R = abs(e - st)
        state['open'].append(dict(p, entry=e, stop=st, target=e + p['side'] * 3 * R, R_dist=R, bars=0))
    still = []
    for t in state['open']:
        K = Ks[t['coin']]; new = K[K.open_time >= t['entry_bar'] + t['bars'] * 300000]; done = None
        for row in new.itertuples():
            o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close); t['bars'] += 1; s = t['side']
            if t['bars'] > 1 and ((s == 1 and o <= t['stop']) or (s == -1 and o >= t['stop'])): done = (o, 'stop (gap)')
            elif (s == 1 and l <= t['stop']) or (s == -1 and h >= t['stop']): done = (t['stop'], 'stop')
            elif (s == 1 and h >= t['target']) or (s == -1 and l <= t['target']): done = (t['target'], 'target')
            elif t['bars'] >= 144: done = (c, '12h time exit')
            if done: t['exit_ms'] = int(row.open_time) + 300000; break
        if done:
            nsw = ((t['exit_ms'] - 1800000) // 86400000) - ((t['entry_bar'] - 1800000) // 86400000)
            t.update(exit=done[0], why=done[1], R=round(s * (done[0] - t['entry']) / t['R_dist'] - (2 * FEE + SWAP * nsw) * t['entry'] / t['R_dist'], 3)); state['closed'].append(t)
        else: still.append(t)
    state['open'] = still
    # 2) rebuild inputs with the SAME builders, then score
    for c in COINS: V2.build(c)
    for c in COINS: V3.build(c[:-4])
    feats = json.load(open(os.path.join(MD, 'combo_features.json'))); m2 = {s: joblib.load(os.path.join(MD, f'v2_{s}.joblib')) for s in 'LS'}; m3 = {s: joblib.load(os.path.join(MD, f'v3_{s}.joblib')) for s in 'LS'}
    H = pd.concat([pd.read_parquet(os.path.join(LIVE, 'v2', f'{c}.parquet')) for c in COINS], ignore_index=True); H['hour_start'] = pd.to_datetime(H.hour_start, utc=True)
    B = H[H.coin == 'BTCUSDT'].set_index('hour_start')[['ret1', 'ret4', 'ret24', 'tshare4']].add_prefix('b_'); H = H.join(B, on='hour_start')
    H = H[H.hour_start + pd.Timedelta(hours=1) <= pd.Timestamp(now_ms, unit='ms', tz='UTC')].sort_values('hour_start').groupby('coin').tail(1)
    H['v2L'] = m2['L'].predict(H[feats['v2']]); H['v2S'] = m2['S'].predict(H[feats['v2']])
    Q = pd.concat([pd.read_parquet(os.path.join(LIVE, 'v3', f'{c}.parquet')) for c in COINS], ignore_index=True)
    Q = Q.sort_values('bar').groupby('coin').tail(1).copy(); Q['pL'] = m3['L'].predict(Q[feats['v3']]); Q['pS'] = m3['S'].predict(Q[feats['v3']])
    Q = Q.merge(H[['coin', 'hour_start', 'v2L', 'v2S']], on='coin', how='left')
    Q['dec'] = pd.to_datetime(Q.dec, utc=True); fresh = Q.dec >= pd.Timestamp(now_ms - 600000, unit='ms', tz='UTC')
    Q['candL'] = np.where((Q.pL > T) & (Q.v2L >= 0) & fresh, Q.pL, -9); Q['candS'] = np.where((Q.pS > T) & (Q.v2S >= 0) & fresh, Q.pS, -9)
    Q[['coin', 'bar', 'dec', 'pL', 'pS', 'v2L', 'v2S']].assign(run=pd.Timestamp(now_ms, unit='ms', tz='UTC')).to_csv(SCORES, mode='a', header=not os.path.exists(SCORES), index=False)
    # 3) rule: best candidate across coins and sides, one new trade per candle, max 3 open, 1 per coin
    cands = sorted([(r.candL, r.coin, 1, r) for r in Q.itertuples() if r.candL > -9] + [(r.candS, r.coin, -1, r) for r in Q.itertuples() if r.candS > -9], key=lambda x: -x[0])
    busy = {t['coin'] for t in state['open']} | {p['coin'] for p in state.get('pending', [])}; seen_dec = state.get('last_dec')
    if cands and len(busy) < MAXOPEN and str(Q.dec.max()) != seen_dec and not checks(state)['paused']:
        for p, coin, side, r in cands:
            if coin in busy: continue
            K = Ks[coin]; k = K.astype({'open': float, 'high': float, 'low': float, 'close': float}); tr = np.maximum(k.high - k.low, np.maximum((k.high - k.close.shift()).abs(), (k.low - k.close.shift()).abs())).rolling(288).mean()
            stop = float(k.low.tail(6).min() - 0.1 * tr.iloc[-1]) if side == 1 else float(k.high.tail(6).max() + 0.1 * tr.iloc[-1])
            state.setdefault('pending', []).append(dict(coin=coin, side=side, score=round(float(p), 3), v2=round(float(r.v2L if side == 1 else r.v2S), 3), decided=str(r.dec),
                                                        entry_bar=int(K.open_time.max()) + 300000, stop=stop, tick=tick_of(k)))
            break
    state.pop('feed_error_since', None)
    state['last_dec'] = str(Q.dec.max()); state['status'] = f"scanned {len(Q)} coins at {pd.Timestamp(now_ms, unit='ms', tz='UTC'):%Y-%m-%d %H:%M} UTC"
    json.dump(state, open(STATE, 'w'), default=str, indent=1)
    top = Q.assign(best=Q[['pL', 'pS']].max(axis=1)).sort_values('best', ascending=False).head(6)
    scan = [dict(coin=r.coin[:-4], buy_score=round(float(r.pL), 2), sell_score=round(float(r.pS), 2), dir_buy=round(float(r.v2L), 2), dir_sell=round(float(r.v2S), 2)) for r in top.itertuples()]
    write_web(state, dict(time=time.strftime('%H:%M:%S'), top=scan, pending=state.get('pending', [])))
    return state
if __name__ == '__main__':
    import traceback
    LOG = os.path.join(LIVE, 'run.log')
    try:
        t0 = time.time(); s = run()
        open(LOG, 'a').write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ok {time.time() - t0:.0f}s open {len(s['open'])} closed {len(s['closed'])}\n")
        # 2026-10-29: refresh the app's Engines tab after every scan, in its own process so it can never touch the scores above
        try: import subprocess; subprocess.Popen([sys.executable, '-I', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'engines_json.py')], creationflags=0x08000000)
        except Exception: pass
    except Exception:
        tb = traceback.format_exc()
        open(LOG, 'a').write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR\n{tb}\n")
        # 2026-10-22: say it on the Lab card instead of dying silently (Binance 451 = VPN exit is in the US)
        try:
            st = json.load(open(STATE)) if os.path.exists(STATE) else {'open': [], 'closed': []}
            st['status'] = ('DATA FEED DOWN: Binance is geo-blocking this PC (HTTP 451). The VPN is on a US server; switch ProtonVPN to a non-US country.'
                            if '451' in tb else 'DATA FEED ERROR: ' + tb.strip().splitlines()[-1][:160])
            st['feed_error_since'] = st.get('feed_error_since') or time.strftime('%Y-%m-%d %H:%M:%S')
            json.dump(st, open(STATE, 'w'), default=str, indent=1)
            write_web(st, dict(time=time.strftime('%H:%M:%S'), top=[], pending=st.get('pending', [])))
        except Exception: pass
        raise
    print(f"ok {time.time() - t0:.0f}s | open {len(s['open'])} pending {len(s.get('pending', []))} closed {len(s['closed'])} | {s['status']}")
