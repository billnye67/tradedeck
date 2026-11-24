import re, json, requests, time, pandas as pd
H={'User-Agent':'Mozilla/5.0'}
rows=[]
ts=requests.get("http://web.archive.org/cdx/search/cdx?url=token.unlocks.app&output=txt&fl=timestamp&filter=statuscode:200&collapse=timestamp:6",timeout=60).text.split()
for t in ts:
    try:
        h=requests.get(f"http://web.archive.org/web/{t}id_/https://token.unlocks.app/",headers=H,timeout=90).text
        m=re.search(r'__NEXT_DATA__[^>]*>(.*?)</script>',h,re.S)
        if not m: continue
        j=json.loads(m.group(1)); data=j['props']['pageProps']['info']['data']
        for d in data:
            tok=d.get('token',{}); ne=d.get('nextEventData') or {}
            rows.append(dict(source='tokenunlocks',snap=t,symbol=tok.get('symbol'),name=tok.get('name'),price=tok.get('price'),mcap=tok.get('marketCap'),fdv=tok.get('fullyDiluted'),
                             next_date=ne.get('beginDate'),next_amount=ne.get('amount'),next_type=ne.get('type'),isCliff=ne.get('isCliff'),groups=json.dumps(ne.get('data'))[:300] if ne.get('data') else None))
        print('tokenunlocks',t,len(data),flush=True); time.sleep(1)
    except Exception as e: print(t,'ERR',str(e)[:80],flush=True)
df=pd.DataFrame(rows); df.to_csv('unlock_events_raw.csv',index=False); print(df.shape); print(df.next_date.notna().mean())
ev=df.dropna(subset=['next_date']).copy(); ev['next_date']=pd.to_datetime(ev.next_date,errors='coerce',utc=True)
ev=ev.sort_values('snap').drop_duplicates(subset=['symbol','next_date'],keep='first')
ev['usd']=pd.to_numeric(ev.price,errors='coerce')*pd.to_numeric(ev.next_amount,errors='coerce'); ev['pct_mcap']=ev.usd/pd.to_numeric(ev.mcap,errors='coerce')*100
ev.to_csv('unlock_events.csv',index=False); print('unique events',len(ev),'| date range',ev.next_date.min(),ev.next_date.max()); print(ev.sort_values('usd',ascending=False).head(8)[['symbol','next_date','usd','pct_mcap','isCliff']].to_string())
