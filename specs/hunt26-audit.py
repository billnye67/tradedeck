"""Hunt 26 data audit (ChatGPT): (1) Tardis pre-expiry OI vs Deribit delivery position for 2024-03-01 expiries;
(2) daily delivery-record coverage 2020-2026 from Deribit's public settlements endpoint."""
import os, sys, json, gzip, time, io, urllib.request, datetime as dt
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
def get(u):
    for k in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as r: return r.read()
        except Exception as e:
            time.sleep(3 * (k + 1))
    raise RuntimeError('gave up: ' + u[:120])

def settlements():
    p = os.path.join(HERE, 'deribit_settlements.parquet')
    if os.path.exists(p): return pd.read_parquet(p)
    rows = []; cont = None; pages = 0
    ck = os.path.join(HERE, 'settle_checkpoint.json')
    if os.path.exists(ck):
        c = json.load(open(ck)); rows = c['rows']; cont = c['cont']; pages = c['pages']; print('resuming at page', pages, flush=True)
    while True:
        u = 'https://www.deribit.com/api/v2/public/get_last_settlements_by_currency?currency=BTC&type=delivery&count=1000' + (f'&continuation={cont}' if cont else '')
        d = json.loads(get(u))['result']; pages += 1
        for s in d['settlements']:
            n = s['instrument_name']; parts = n.split('-')
            if len(parts) == 4: rows.append(dict(instrument=n, expiry=parts[1], strike=float(parts[2]), cp=parts[3], position=s.get('position'), index_price=s.get('index_price'), ts=s['timestamp']))
        cont = d.get('continuation')
        if pages % 25 == 0: json.dump(dict(rows=rows, cont=cont, pages=pages), open(ck, 'w')); print('page', pages, 'records', len(rows), flush=True)
        if not cont or pages > 2000: break
        time.sleep(0.35)
    df = pd.DataFrame(rows); df.to_parquet(p); print('settlement pages', pages, 'option records', len(df), flush=True); return df

def coverage(df):
    df['date'] = pd.to_datetime(df['ts'], unit='ms', utc=True).dt.date
    daily = df.groupby('date').agg(n=('instrument', 'size'), oi_sum=('position', 'sum'), zero=('position', lambda x: (x == 0).sum()))
    full = pd.date_range(daily.index.min(), daily.index.max()).date
    missing = [d for d in full if d not in daily.index]
    print(f'delivery records {df["date"].min()} .. {df["date"].max()}: {len(daily)} days with records, {len(missing)} calendar days with NO record at all (first 10: {missing[:10]})')
    yr = daily.groupby(pd.to_datetime(pd.Series(daily.index)).dt.year.values).agg(days=('n', 'size'), options_per_day=('n', 'mean'), zero_oi_options=('zero', 'sum'))
    print(yr.to_string())
    return daily, missing

def tardis_vs_delivery(settle):
    """stream the free 2024-03-01 options_chain file; keep the last pre-08:00 OI per BTC-1MAR24 option; compare to delivery position."""
    p = os.path.join(HERE, 'tardis_1mar24_last.parquet')
    if not os.path.exists(p):
        u = 'https://datasets.tardis.dev/v1/deribit/options_chain/2024/03/01/OPTIONS.csv.gz'
        last = {}; first_ts = {}; n = 0
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120) as r:
            g = gzip.GzipFile(fileobj=r); hdr = g.readline().decode().strip().split(',')
            i_sym = hdr.index('symbol'); i_ts = hdr.index('timestamp'); i_oi = hdr.index('open_interest'); i_up = hdr.index('underlying_price')
            cutoff = int(dt.datetime(2024, 3, 1, 8, 0, tzinfo=dt.timezone.utc).timestamp() * 1e6)
            for line in g:
                if not line.startswith(b'deribit,BTC-1MAR24'): continue
                f = line.decode().split(','); ts = int(f[i_ts]); n += 1
                if ts < cutoff: last[f[i_sym]] = (ts, f[i_oi], f[i_up])
        df = pd.DataFrame([dict(instrument=k, last_ts=v[0], oi=float(v[1]) if v[1] else None, underlying=float(v[2]) if v[2] else None) for k, v in last.items()])
        df.to_parquet(p); print('tardis BTC-1MAR24 rows streamed', n, flush=True)
    t = pd.read_parquet(p)
    d = settle[settle['expiry'] == '1MAR24'][['instrument', 'position', 'index_price']]
    m = t.merge(d, on='instrument', how='outer', indicator=True)
    print(f"BTC-1MAR24 options: tardis {len(t)}, deribit delivery {len(d)}, matched {int((m['_merge']=='both').sum())}, tardis-only {int((m['_merge']=='left_only').sum())}, deribit-only {int((m['_merge']=='right_only').sum())}")
    b = m[m['_merge'] == 'both'].copy(); b['diff'] = b['position'] - b['oi']; b['ratio'] = b['position'] / b['oi'].replace(0, float('nan'))
    print(f"last pre-08:00 Tardis OI vs delivery position: exact match {int((b['diff'].abs() < 1e-6).sum())}/{len(b)}; median ratio {b['ratio'].median():.4f}; mean |diff| {b['diff'].abs().mean():.3f} contracts; max |diff| {b['diff'].abs().max():.1f}")
    print(f"sum OI tardis {b['oi'].sum():,.1f} vs sum position {b['position'].sum():,.1f}; underlying at last tick {t['underlying'].median():,.1f} vs delivery index {d['index_price'].iloc[0]:,.1f}")
    print('largest discrepancies:'); print(b.reindex(b['diff'].abs().sort_values(ascending=False).index).head(5)[['instrument', 'oi', 'position', 'last_ts']].to_string(index=False))
    last_tick = pd.to_datetime(t['last_ts'].max(), unit='us', utc=True); print('latest pre-08:00 Tardis tick for these options:', last_tick)

if __name__ == '__main__':
    s = settlements(); daily, missing = coverage(s)
    daily.to_csv(os.path.join(HERE, 'delivery_daily.csv'))
    tardis_vs_delivery(s)
