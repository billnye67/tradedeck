"""Daily self-check for the Combo paper bot (runs by itself every morning). Answers: did the live bot compute the SAME scores the test
pipeline computes from Binance's official history files? Rebuilds yesterday from data.binance.vision daily archives (klines 5m + metrics;
funding from the same REST source), runs the SAME builders + final models, and compares with what the live bot logged (scores.csv).
Writes live_combo/checks.json (read by combo_paper.py for the status light). PAPER ONLY."""
import os; os.environ.setdefault('LOKY_MAX_CPU_COUNT', '11')   # stops joblib launching PowerShell (visible window) to count CPU cores
import os, sys, io, json, time, glob, zipfile, urllib.request, joblib, numpy as np, pandas as pd
os.environ['V3_KEEP_UNLABELED'] = '1'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_build as V2, v3_build as V3
import combo_paper as CP
LAB = CP.LAB; LIVE = CP.LIVE; CK = os.path.join(LIVE, 'check'); CFLOW = os.path.join(CK, 'flow')
for d in ('klines5m', 'funding', 'metrics', 'v2', 'v3'): os.makedirs(os.path.join(CFLOW if d in ('klines5m', 'funding', 'metrics') else CK, d), exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0'}; COINS = CP.COINS; DAYS = 75
def fetch(url):
    for k in range(4):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(3)
        except Exception: time.sleep(3)
    return None
def day_file(kind, coin, d):
    sub = f'klines/{coin}/5m/{coin}-5m-{d}' if kind == 'klines5m' else f'metrics/{coin}/{coin}-metrics-{d}'
    dst = os.path.join(CFLOW, kind, f'{os.path.basename(sub)}.csv')
    if os.path.exists(dst): return True
    b = fetch(f'https://data.binance.vision/data/futures/um/daily/{sub}.zip')
    if b is None: return False
    with zipfile.ZipFile(io.BytesIO(b)) as z: open(dst, 'wb').write(z.read(z.namelist()[0]))
    return True
def main():
    day = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=1)); D = day.strftime('%Y-%m-%d')
    for coin in COINS:
        for k in range(DAYS, -1, -1):
            dd = (day - pd.Timedelta(days=k)).strftime('%Y-%m-%d')
            if not day_file('klines5m', coin, dd) and k == 0: return dict(day=D, status='waiting', note='Binance has not published yesterday yet; retry later')
            if k <= 12: day_file('metrics', coin, dd)
        old = (day - pd.Timedelta(days=DAYS + 2)).strftime('%Y-%m-%d')                       # keep the folder small
        for f in glob.glob(os.path.join(CFLOW, '*', f'{coin}-*.csv')):
            if os.path.basename(f)[-14:-4] < old and os.path.basename(f)[-14:-4][:2] == '20': os.remove(f)
        now_ms = int((day + pd.Timedelta(days=1)).timestamp() * 1000)
        fr = CP.get(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={coin}&startTime={now_ms - 40 * 86400000}&endTime={now_ms}&limit=1000')
        pd.DataFrame({'calc_time': [int(x['fundingTime']) for x in fr], 'funding_interval_hours': 8, 'last_funding_rate': [float(x['fundingRate']) for x in fr]}).to_csv(
            os.path.join(CFLOW, 'funding', f'{coin}-fundingRate-check.csv'), index=False)
    V2.FLOW = CFLOW; V2.OUT = os.path.join(CK, 'v2'); V3.LAB = CK; V3.OUT = os.path.join(CK, 'v3')
    for c in COINS: V2.build(c)
    for c in COINS: V3.build(c[:-4])
    feats = json.load(open(os.path.join(CP.MD, 'combo_features.json'))); m3 = {s: joblib.load(os.path.join(CP.MD, f'v3_{s}.joblib')) for s in 'LS'}; m2 = {s: joblib.load(os.path.join(CP.MD, f'v2_{s}.joblib')) for s in 'LS'}
    Q = pd.concat([pd.read_parquet(os.path.join(CK, 'v3', f'{c}.parquet')) for c in COINS]); Q['bar'] = pd.to_datetime(Q.bar, utc=True)
    Q = Q[Q.bar.dt.strftime('%Y-%m-%d') == D].copy(); Q['pL_hist'] = m3['L'].predict(Q[feats['v3']]); Q['pS_hist'] = m3['S'].predict(Q[feats['v3']])
    H = pd.concat([pd.read_parquet(os.path.join(CK, 'v2', f'{c}.parquet')) for c in COINS]); H['hour_start'] = pd.to_datetime(H.hour_start, utc=True)
    B = H[H.coin == 'BTCUSDT'].set_index('hour_start')[['ret1', 'ret4', 'ret24', 'tshare4']].add_prefix('b_'); H = H.join(B, on='hour_start')
    H['v2L_hist'] = m2['L'].predict(H[feats['v2']]); H['v2S_hist'] = m2['S'].predict(H[feats['v2']])
    S = pd.read_csv(CP.SCORES); S['bar'] = pd.to_datetime(S.bar, utc=True); S = S[S.bar.dt.strftime('%Y-%m-%d') == D].drop_duplicates(['coin', 'bar'], keep='last')
    M = S.merge(Q[['coin', 'bar', 'pL_hist', 'pS_hist']], on=['coin', 'bar'])
    if not len(M): return dict(day=D, status='no-data', note='the live bot logged no candles for that day (was the PC off?)')
    dL, dS = (M.pL - M.pL_hist).abs(), (M.pS - M.pS_hist).abs()
    flips = int((((M.pL > 0.5) != (M.pL_hist > 0.5)) | ((M.pS > 0.5) != (M.pS_hist > 0.5))).sum())
    cover = len(M) / max(1, len(S)); uptime = len(S) / (len(COINS) * 288)
    ok = flips == 0 and float(np.percentile(np.r_[dL, dS], 95)) < 0.10 and cover > 0.9
    return dict(day=D, status='ok' if ok else 'mismatch', candles=len(M), coverage=round(cover, 3), uptime=round(uptime, 3), median_diff=round(float(np.median(np.r_[dL, dS])), 4),
                p95_diff=round(float(np.percentile(np.r_[dL, dS], 95)), 4), trade_flips=flips,
                note='live scores match the history files' if ok else ('decision would differ on %d candles' % flips if flips else ('scores drift (95th pct diff %.3f)' % np.percentile(np.r_[dL, dS], 95) if cover > 0.9 else 'history files are missing candles the live bot saw')) + ('' if uptime > 0.8 else ' (bot was off part of the day: %.0f%% of candles seen)' % (100 * uptime)))
if __name__ == '__main__':
    f = os.path.join(LIVE, 'checks.json'); hist = json.load(open(f)) if os.path.exists(f) else {'days': []}
    try: r = main()
    except Exception as e: r = dict(day=str(pd.Timestamp.utcnow().date()), status='error', note=f'{type(e).__name__}: {e}'[:200])
    hist['days'] = [d for d in hist['days'] if d['day'] != r['day']] + [r]; hist['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    json.dump(hist, open(f, 'w'), indent=1); print(r)
