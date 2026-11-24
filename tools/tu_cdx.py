"""TU step 2a: list Wayback snapshots of the unlock calendars (token.unlocks.app, tokenomist.ai) with retries; Wayback is flaky."""
import os, time, json, requests
OUT = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\tu'); H = {'User-Agent': 'Mozilla/5.0'}
URLS = ['token.unlocks.app', 'token.unlocks.app/unlocks', 'tokenomist.ai', 'tokenomist.ai/unlocks', 'www.tokenomist.ai', 'www.tokenomist.ai/unlocks', 'tokenomist.ai/unlocks/upcoming']
def cdx(u):
    q = f'http://web.archive.org/cdx/search/cdx?url={u}&output=json&fl=timestamp,original,statuscode,length&filter=statuscode:200&from=202306&collapse=timestamp:8'
    for k in range(12):
        try:
            r = requests.get(q, headers=H, timeout=90)
            if r.text.lstrip().startswith('['): return r.json()[1:]
        except Exception: pass
        time.sleep(20 + 10 * k)
    return None
res = {}
for u in URLS:
    rows = cdx(u); res[u] = rows; print(u, 'FAILED' if rows is None else len(rows), flush=True); time.sleep(5)
json.dump(res, open(os.path.join(OUT, 'cdx.json'), 'w'), indent=0)
