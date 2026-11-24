import cryptohftdata as chd, pandas as pd, time, os, sys, logging
logging.disable(logging.CRITICAL)
key=open('key.txt').read().strip(); c=chd.CryptoHFTDataClient(api_key=key)
months=pd.period_range('2025-07','2026-10',freq='M')
for s in ('BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','LINKUSDT'):
    for m in months:
        f=f'liq/{s}_{m}.parquet'
        if os.path.exists(f): continue
        a=m.start_time.strftime('%Y-%m-%d'); b=min(m.end_time,pd.Timestamp('2026-10-05')).strftime('%Y-%m-%d')
        if a>b: continue
        for attempt in range(3):
            try:
                df=c.get_liquidations(symbol=s,exchange=chd.exchanges.BINANCE_FUTURES,start_date=a,end_date=b)
                keep=[k for k in ('event_time','trade_time','side','price','average_price','quantity','filled_quantity','order_status') if k in df.columns]
                df[keep].to_parquet(f); print(s,m,len(df),flush=True); break
            except Exception as e:
                print(s,m,'ERR',str(e)[:80],flush=True); time.sleep(5)
print('DONE')
