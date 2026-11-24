"""D3 feasibility audit (Perplexity): can Binance USDT-M aggTrades archives support a frozen aggressive-order-flow study?
Audit only. No thresholds, no returns, no 2025-2026 analysis. Writes d3_audit_manifest.json."""
import os, io, json, zipfile, time, urllib.request, datetime as dt
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); K1 = os.path.join(HERE, '..', 'd2', 'k1m')
BASE = 'https://data.binance.vision/data/futures/um'
CORE = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT']
DEMO = set(CORE + ['AVAXUSDT', 'SUIUSDT', 'HYPEUSDT', 'LINKUSDT', 'DOGEUSDT'])   # app's own 9 markets; the extras must be non-demo overlaps
UA = {'User-Agent': 'Mozilla/5.0'}
def head(url):
    try:
        req = urllib.request.Request(url, headers=UA, method='HEAD')
        with urllib.request.urlopen(req, timeout=60) as r: return int(r.headers.get('Content-Length', 0))
    except urllib.error.HTTPError as e: return None if e.code == 404 else -1
    except Exception: return -1
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r: return r.read()

def pick_extras(n=6):
    """six liquid non-demo overlaps by trailing 30-day median daily quote volume at 2024-12-31 (development-era data only)."""
    t1 = int(pd.Timestamp('2025-01-01', tz='UTC').value // 10**6); t0 = t1 - 30 * 86_400_000
    rows = []
    for f in os.listdir(K1):
        s = f.replace('.parquet', '')
        if s in DEMO: continue
        d = pd.read_parquet(os.path.join(K1, f), columns=['ts', 'quote_volume']); w = d[(d['ts'] >= t0) & (d['ts'] < t1)]
        if len(w) < 0.9 * 30 * 1440: continue
        rows.append((s, float(w.groupby(w['ts'] // 86_400_000)['quote_volume'].sum().median())))
    rows.sort(key=lambda x: -x[1]); return [s for s, _ in rows[:n]], rows[:n]

def monthly_coverage(sym):
    months = pd.period_range('2019-09', '2026-08', freq='M'); sizes = {}
    for m in months:
        sizes[str(m)] = head(f'{BASE}/monthly/aggTrades/{sym}/{sym}-aggTrades-{m}.zip'); time.sleep(0.05)
    present = [m for m, v in sizes.items() if v and v > 0]
    return dict(first=present[0] if present else None, last=present[-1] if present else None, months_present=len(present),
                months_missing_inside=[m for m in sizes if present and present[0] <= m <= present[-1] and not (sizes[m] and sizes[m] > 0)],
                total_bytes=int(sum(v for v in sizes.values() if v and v > 0)), per_month=sizes)

def sample_check(sym, month):
    """download one monthly archive, read schema, timestamps, IDs, buyer-maker field, and time to build 1-minute flow bars."""
    t0 = time.time(); b = get(f'{BASE}/monthly/aggTrades/{sym}/{sym}-aggTrades-{month}.zip'); dl = time.time() - t0
    z = zipfile.ZipFile(io.BytesIO(b)); name = z.namelist()[0]
    with z.open(name) as f: first = f.readline().decode().strip()
    has_header = not first.split(',')[0].isdigit()
    t1 = time.time()
    df = pd.read_csv(z.open(name), header=0 if has_header else None)
    if not has_header: df.columns = ['agg_trade_id', 'price', 'quantity', 'first_trade_id', 'last_trade_id', 'transact_time', 'is_buyer_maker'][:df.shape[1]]
    df.columns = [c.strip() for c in df.columns]
    rd = time.time() - t1
    ids = df['agg_trade_id'].astype('int64').values; ts = df['transact_time'].astype('int64').values
    ts_unit = 'ms' if ts.max() < 1e14 else 'us'
    d_ids = np.diff(ids); gaps = int((d_ids > 1).sum()); missing_ids = int((d_ids[d_ids > 1] - 1).sum()); dups = int((d_ids == 0).sum())
    bm = df['is_buyer_maker']
    bm_vals = bm.astype(str).str.lower().unique().tolist()
    # 1-minute flow bars
    t2 = time.time()
    tsm = (ts // 1000 if ts_unit == 'us' else ts); minute = (tsm // 60000) * 60000
    q = df['price'].astype(float).values * df['quantity'].astype(float).values
    is_bm = bm.astype(str).str.lower().isin(['true', '1']).values
    g = pd.DataFrame({'m': minute, 'buy': np.where(~is_bm, q, 0.0), 'sell': np.where(is_bm, q, 0.0), 'q': q, 'base': df['quantity'].astype(float).values}).groupby('m').agg(buy=('buy', 'sum'), sell=('sell', 'sum'), q=('q', 'sum'), base=('base', 'sum'), n=('q', 'size'))
    g['vwap'] = g['q'] / g['base']; g['imb'] = (g['buy'] - g['sell']) / g['q']
    build = time.time() - t2
    expected_min = int((tsm.max() - tsm.min()) // 60000) + 1
    return dict(archive=name, bytes=len(b), download_s=round(dl, 1), rows=int(len(df)), has_header=has_header, columns=list(df.columns), ts_unit=ts_unit,
                first_ts=dt.datetime.fromtimestamp(tsm.min() / 1000, dt.UTC).isoformat(), last_ts=dt.datetime.fromtimestamp(tsm.max() / 1000, dt.UTC).isoformat(),
                id_monotonic=bool((d_ids >= 0).all()), id_gaps=gaps, missing_ids_in_gaps=missing_ids, duplicate_ids=dups,
                buyer_maker_values=bm_vals, buyer_maker_share=round(float(is_bm.mean()), 4), read_s=round(rd, 1), minute_bars=int(len(g)), expected_minutes=expected_min,
                minute_coverage=round(len(g) / expected_min, 4), build_1m_s=round(build, 1), sample_bar=g.iloc[1000].round(2).to_dict() if len(g) > 1000 else None)

def main():
    extras, ranked = pick_extras()
    syms = CORE + extras
    man = dict(generated=dt.datetime.now(dt.UTC).isoformat(), purpose='D3 feasibility audit: Binance USDT-M aggTrades archives', universe=syms,
               extras_selected_by='trailing 30-day median daily quote volume at 2024-12-31 from the already-downloaded 1m klines, non-demo overlaps only', extras_ranked=ranked, coverage={}, samples={})
    for s in syms:
        man['coverage'][s] = monthly_coverage(s); print(s, {k: v for k, v in man['coverage'][s].items() if k != 'per_month'}, flush=True)
    # schema/ID checks on two archives: an old month and a recent one (both inside development/confirmation, never 2025-26 returns)
    for s, m in [('BTCUSDT', '2020-03'), ('BTCUSDT', '2024-06'), ('XRPUSDT', '2024-06'), (extras[0], '2024-06')]:
        try: man['samples'][f'{s}-{m}'] = sample_check(s, m); print(s, m, {k: v for k, v in man['samples'][f'{s}-{m}'].items() if k not in ('columns', 'sample_bar')}, flush=True)
        except Exception as e: man['samples'][f'{s}-{m}'] = f'ERR {e}'; print(s, m, 'ERR', e, flush=True)
    total = sum(c['total_bytes'] for c in man['coverage'].values()); man['total_bytes_10_symbols'] = total
    json.dump(man, open(os.path.join(HERE, 'd3_audit_manifest.json'), 'w'), indent=1, default=str)
    print('TOTAL compressed bytes for 10 symbols:', f'{total/1e9:.1f} GB', flush=True)

if __name__ == '__main__':
    main()
