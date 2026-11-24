"""TU step 2b: download every listed Wayback snapshot (raw id_ pages) of the unlock calendars -> lab/tu/snaps/<site>_<ts>.html (gunzipped)."""
import os, sys, json, time, gzip, requests
D = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab\tu'); S = os.path.join(D, 'snaps'); H = {'User-Agent': 'Mozilla/5.0'}
cdx = json.load(open(os.path.join(D, 'cdx.json')))
jobs = []
for key, site in (('token.unlocks.app', 'tu'), ('tokenomist.ai', 'tk'), ('tokenomist.ai/unlocks', 'tku')):
    for ts, orig, *_ in cdx.get(key) or []: jobs.append((site, ts, orig))
if 'rev' in sys.argv: jobs = jobs[::-1]
for site, ts, orig in jobs:
    dst = os.path.join(S, f'{site}_{ts}.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 20000: continue
    for k in range(8):
        try:
            r = requests.get(f'http://web.archive.org/web/{ts}id_/{orig}', headers=H, timeout=120)
            b = r.content
            if b[:2] == b'\x1f\x8b': b = gzip.decompress(b)
            if r.ok and len(b) > 20000 and b'Temporarily Offline' not in b[:3000]:
                open(dst, 'wb').write(b); print(site, ts, len(b), flush=True); break
        except Exception as e: pass
        time.sleep(15 + 15 * k)
    else: print(site, ts, 'FAILED', flush=True)
    time.sleep(2)
print('DONE', flush=True)
