"""TU step 2c: parse every saved unlock-calendar snapshot (several page formats) -> lab/tu/events_raw.csv (one row per sighting) and
lab/tu/events.csv (one row per token+unlock, first sighting kept; only sightings taken BEFORE the unlock count). Cliff unlocks only
(linear daily releases are not one-off events). usd = the site's cliff value in USD at snapshot time."""
import os, re, json, glob, collections, pandas as pd
D = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\tu'); S = os.path.join(D, 'snaps')
DEC = json.JSONDecoder()

def rsc(h):
    out = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', h, re.S):
        try: out.append(json.loads(m.group(1)))
        except Exception: pass
    return ''.join(out)

def objs(t, key):
    """every JSON value that follows the literal key string"""
    i = 0
    while True:
        i = t.find(key, i)
        if i < 0: return
        try:
            o, _ = DEC.raw_decode(t[i + len(key):]); yield o
        except Exception: pass
        i += len(key)

def f_nextdata(h, snap):                       # 2022-23: __NEXT_DATA__ props.pageProps.info.data (old U1 harvester format)
    m = re.search(r'__NEXT_DATA__[^>]*>(.*?)</script>', h, re.S); rows = []
    if not m: return rows
    try: data = json.loads(m.group(1))['props']['pageProps']['info']['data']
    except Exception: return rows
    for d in data:
        tok = d.get('token', {}); ne = d.get('nextEventData') or {}
        if not ne.get('beginDate') or not ne.get('isCliff'): continue
        try: usd = float(tok.get('price')) * float(ne.get('amount'))
        except Exception: continue
        rows.append(dict(symbol=tok.get('symbol'), T=int(pd.Timestamp(ne['beginDate']).timestamp()), usd=usd,
                         pct_circ=usd / float(tok['marketCap']) * 100 if float(tok.get('marketCap') or 0) else None, fmt='nextdata', dated=True))
    return rows

def f_tokobj(t, snap):                         # 2023-24: {"token":{...},"unlockEvents":[{"data":{"unlockTime":...,"allocations":[...]}}]}
    rows = []; i = 0
    while True:
        i = t.find('{"token":{"id"', i)
        if i < 0: break
        try: o, _ = DEC.raw_decode(t[i:])
        except Exception: i += 10; continue
        i += 10
        if not isinstance(o, dict) or 'unlockEvents' not in o: continue
        tok = o['token']; price = tok.get('price') or 0; circ = tok.get('circulatingSupply')
        for ev in o['unlockEvents']:
            d = ev.get('data', {}); T = d.get('unlockTime')
            if not T: continue
            usd = sum((a.get('totalCliffUnlockAmountUsd') or 0) for a in d.get('allocations', []))
            if not usd and d.get('totalCliffUnlockAmount'): usd = d['totalCliffUnlockAmount'] * price
            if usd <= 0: continue
            amt = d.get('totalCliffUnlockAmount') or 0
            rows.append(dict(symbol=tok.get('symbol'), T=int(T), usd=float(usd), pct_circ=amt / circ * 100 if circ else None, fmt='tokobj', dated=True))
    return rows

def f_initial(t, snap):                        # 2024: "initialData":{"metadata":..,"data":[[[id,name,SYM,..],price,chg,mcap,circ,[..],[cliffUSD,T,pct,..,TYPE],[totalUSD,pctMcap]]]}
    rows = []
    for o in objs(t, '"initialData":'):
        if not isinstance(o, dict) or not isinstance(o.get('data'), list): continue
        for r in o['data']:
            if isinstance(r, dict):                # 2024-25 tokenomist list: dicts with upcomingDate / upcomingValue
                try:
                    if r.get('typeOfUpcoming') != 'CLIFF' or not r.get('upcomingDate'): continue
                    rows.append(dict(symbol=r['symbol'], T=int(r['upcomingDate']), usd=float(r.get('upcomingValue') or 0),
                                     pct_circ=r.get('upcomingPercentage'), fmt='initial_dict', dated=True))
                except Exception: pass
                continue
            try:
                sym = r[0][2]; ev = r[6]; typ = ev[-1]
                if typ != 'CLIFF': continue
                usd = float(ev[0]); T = int(ev[1]); mcap = float(r[3])
                rows.append(dict(symbol=sym, T=T, usd=usd, pct_circ=usd / mcap * 100 if mcap else None, fmt='initial', dated=True))
            except Exception: continue
    return rows

def tokmap(t):                                 # tokenId -> (symbol, price, circ) from any embedded token dicts
    m = {}
    for mm in re.finditer(r'"id":"([^"]+)","slug":"[^"]*","name":"[^"]*","symbol":"([^"]+)"', t):
        j = t.find('"price":', mm.end()); k = t.find('"circulatingSupply":', mm.end())
        try:
            price = float(re.match(r'[-0-9.eE]+', t[j + 8:j + 40]).group()) if 0 < j - mm.end() < 600 else None
            circ = float(re.match(r'[-0-9.eE]+', t[k + 20:k + 60]).group()) if 0 < k - mm.end() < 900 else None
        except Exception: price = circ = None
        m.setdefault(mm.group(1), (mm.group(2), price, circ))
    return m

def f_chart(t, snap):                          # 2025: chart of upcoming top unlocks, daily cumulative % of circulating supply per token
    rows = []; tm = tokmap(t)
    for o in objs(t, '{"data":{"data":'):
        if not (isinstance(o, list) and o and isinstance(o[0], dict) and 'chartTokenDataList' in o[0]): continue
        ser = collections.defaultdict(list)
        for day in o:
            for c in day['chartTokenDataList']: ser[c['tokenId']].append((day['unlockDate'], c['amountToCirculatingSupplyPercent'] or 0))
        for tid, s in ser.items():
            s.sort(); inc = [(s[k][0], s[k][1] - (s[k - 1][1] if k else 0)) for k in range(len(s))]
            for k, (T, x) in enumerate(inc):
                prev0 = k == 0 or inc[k - 1][1] == 0; next0 = k == len(inc) - 1 or inc[k + 1][1] == 0
                if x > 0 and prev0 and next0:                     # one-off jump = cliff (linear tokens rise every day)
                    sym, price, circ = tm.get(tid, (None, None, None))
                    usd = x / 100 * circ * price if price and circ else None
                    rows.append(dict(symbol=sym or tid, T=int(T), usd=usd, pct_circ=x, fmt='chart_day', dated=True))
    return rows

def money(s):
    if not isinstance(s, str): return None
    m = re.match(r'\$*([0-9.]+)([KMB]?)', s.replace(',', ''))
    return float(m.group(1)) * {'': 1, 'K': 1e3, 'M': 1e6, 'B': 1e9}[m.group(2)] if m else None

def f_cliff7(t, snap):                         # 2025-26: "cliff unlocks next 7D" list (value + % but NO date)
    rows = []
    for o in objs(t, '"cliff":'):
        if not (isinstance(o, dict) and isinstance(o.get('tokenList'), list)): continue
        for r in o['tokenList']:
            if not isinstance(r, dict): continue
            try: pct = float(str(r.get('percentage', '')).rstrip('%'))
            except Exception: pct = None
            rows.append(dict(symbol=r.get('symbol'), T=None, usd=money(r.get('value', '')), pct_circ=pct, fmt='cliff7', dated=False))
    return rows

def parse(f):
    site, ts = os.path.basename(f)[:-5].split('_'); h = open(f, encoding='utf-8', errors='ignore').read(); t = rsc(h)
    rows = []
    for fn in (f_nextdata, f_tokobj, f_initial, f_chart, f_cliff7):
        try: rows += fn(h if fn is f_nextdata else t, ts)
        except Exception as e: print('  parse error', fn.__name__, os.path.basename(f), str(e)[:80])
    snap_ts = int(pd.Timestamp(ts, tz='UTC').timestamp())
    for r in rows: r.update(site=site, snap=ts, snap_ts=snap_ts)
    return rows

if __name__ == '__main__':
    R = []
    for f in sorted(glob.glob(os.path.join(S, '*_*.html'))):
        rows = parse(f); R += rows
        c = collections.Counter(r['fmt'] for r in rows); print(os.path.basename(f), dict(c), flush=True)
    E = pd.DataFrame(R); E['symbol'] = E.symbol.astype(str).str.upper(); E.to_csv(os.path.join(D, 'events_raw.csv'), index=False)
    dated = E[E.dated & E['T'].notna()].copy(); dated['T'] = dated['T'].astype('int64')
    dated = dated[dated.snap_ts < dated['T']]                                       # known before it happened
    dated['day'] = dated['T'] // 86400
    dated = dated.sort_values('snap_ts').drop_duplicates(['symbol', 'day'], keep='first')
    dated.to_csv(os.path.join(D, 'events.csv'), index=False)
    print('sightings', len(E), '| dated unique events', len(dated), '| undated cliff7 sightings', int((~E.dated).sum()))
    print(dated.assign(m=pd.to_datetime(dated['T'], unit='s').dt.to_period('Q')).groupby('m').agg(n=('symbol', 'size'), big=('usd', lambda x: (x >= 5e6).sum())).to_string())
