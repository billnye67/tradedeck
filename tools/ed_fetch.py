"""ED data: Nasdaq earnings calendar by date (2012-2026, cached one JSON per date) + Yahoo daily adjusted OHLC for the stock universe.
Out: %LOCALAPPDATA%/TradeDeck/Data/lab/ed/{cal/<date>.json, px/<SYM>.csv, events.csv}. Resumable (skips cached files)."""
import os, sys, json, time, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor
OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\ed'); CAL = os.path.join(OUT, 'cal'); PX = os.path.join(OUT, 'px')
for d in (CAL, PX): os.makedirs(d, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
CONFIRMED = 'NVDA AAPL AMZN AMD AVGO ASML NFLX NOW UBER TSLA MSFT META GOOGL COIN MSTR'.split()
NOT_US_STOCK = set('ANTHROPIC OPENAI MINIMAX MOONSHOT ZHIPU OURA SPCX CBRS KSTR INFQ BWET BOT BSP BBX BNC ACNX AGPU ARX CYPH DRAM FLNC GIGADEV '
                   'HANMI HYUNDAI KIOXIA KUAISHOU MEITUAN POPMART SAMSUNG SAMSUNGEM SKHY SKHYNIX SHEINHKD TENCENT TENCENTHKD XIAOMIHKD ZHONGJI '
                   'CSOPSAMSUNG2L CSOPSKHYNIX2L BYD EWJ EWY EWZ IWM KORU MUU MVLL QQQ SMH SOXL SOXS SPY SQQQ TQQQ TMF UVXY XLE SKDD SKUU SNXX '
                   'SECZ SHAZ STRC STXX USAR LYTE PENG INTW QNTX RAM'.split())
def universe():
    ks = open(os.path.join(os.path.dirname(__file__), '..', 'specs', 'lab', 'ed_kucoin_stock_perps.txt')).read().split()
    broker = open(os.path.join(os.path.dirname(__file__), '..', 'specs', 'lab', 'ed_broker_universe.txt')).read().split()   # Broker's real list (2026-10-17)
    return sorted(set(CONFIRMED) | set(broker) | {s for s in ks if s not in NOT_US_STOCK})
def get(url, tries=4):
    for k in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except Exception: time.sleep(2 + 3 * k)
    return None
def cal_day(d):
    f = os.path.join(CAL, d + '.json')
    if os.path.exists(f): return 0
    b = get(f'https://api.nasdaq.com/api/calendar/earnings?date={d}')
    if b is None: return 1
    rows = ((json.loads(b).get('data') or {}).get('rows')) or []
    json.dump(rows, open(f, 'w')); time.sleep(0.25); return 0
def px(sym):
    f = os.path.join(PX, sym + '.csv')
    if os.path.exists(f): return sym, 'cached'
    b = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace("BRKB", "BRK-B")}?period1=1293840000&period2={int(time.time())}&interval=1d&events=div,splits&includeAdjustedClose=true')
    try:
        r = json.loads(b)['chart']['result'][0]; q = r['indicators']['quote'][0]; ac = r['indicators']['adjclose'][0]['adjclose']
    except Exception: return sym, 'fail'
    with open(f, 'w') as w:
        w.write('date,open,high,low,close,adjclose,volume\n')
        for i, t in enumerate(r['timestamp']):
            if None in (q['open'][i], q['high'][i], q['low'][i], q['close'][i], ac[i]): continue
            w.write(f"{dt.datetime.utcfromtimestamp(t).strftime('%Y-%m-%d')},{q['open'][i]},{q['high'][i]},{q['low'][i]},{q['close'][i]},{ac[i]},{q['volume'][i]}\n")
    return sym, 'ok'
if __name__ == '__main__':
    U = universe(); print('universe', len(U), flush=True)
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(px, U))
    print('prices:', sum(r[1] != 'fail' for r in res), 'ok;', 'fail:', [r[0] for r in res if r[1] == 'fail'], flush=True)
    days = [d.strftime('%Y-%m-%d') for d in (dt.date(2012, 1, 1) + dt.timedelta(n) for n in range((dt.date.today() - dt.date(2012, 1, 1)).days)) if d.weekday() < 5]
    fails = 0
    with ThreadPoolExecutor(3) as ex:
        for i, r in enumerate(ex.map(cal_day, days)):
            fails += r
            if i % 250 == 0: print(f'calendar {i}/{len(days)} fails {fails}', flush=True)
    Us = set(U) | {'GOOG'}; out = []
    for d in days:
        f = os.path.join(CAL, d + '.json')
        if not os.path.exists(f): continue
        for r in json.load(open(f)):
            s = r.get('symbol')
            if s in Us: out.append((d, 'GOOGL' if s == 'GOOG' else s, r.get('time', ''), r.get('eps', ''), r.get('epsForecast', ''), r.get('surprise', '')))
    import csv
    with open(os.path.join(OUT, 'events.csv'), 'w', newline='') as w:
        c = csv.writer(w); c.writerow(['date', 'sym', 'time', 'eps', 'eps_fc', 'surprise']); c.writerows(sorted(set(out)))
    print('DONE events', len(set(out)), 'calendar fails', fails, flush=True)
