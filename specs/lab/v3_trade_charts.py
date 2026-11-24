import os, glob, numpy as np, pandas as pd, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as mdates
LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab'); HERE = os.path.dirname(os.path.abspath(__file__))
P = pd.read_parquet(os.path.join(HERE, 'v2_K1.5_exam_preds.parquet'))[['coin','dt','pL','pS']]; P['dt']=P.dt.astype('datetime64[us, UTC]'); P=P.sort_values('dt')
T = pd.read_csv(os.path.join(HERE, 'v2_Kv3_exam_T0.5_trades.csv'), parse_dates=['dt','exit']); T['dt']=T.dt.astype('datetime64[us, UTC]')
M = pd.concat([pd.merge_asof(x.sort_values('dt'), P[P.coin==c].drop(columns='coin'), on='dt', direction='backward', tolerance=pd.Timedelta(hours=2)) for c,x in T.groupby('coin')]).dropna(subset=['pL'])
M['v2'] = np.where(M.side==1, M.pL, M.pS); M = M[(M.v2>=0) & ~((M.coin=='DYDXUSDT')&(M.dt>='2026-04-01')&(M.dt<'2026-05-01'))]
rng = np.random.default_rng(11); pick = M.iloc[rng.choice(len(M), 6, replace=False)].sort_values('dt')
fig, axs = plt.subplots(3, 2, figsize=(18, 15)); cache = {}
for ax, r in zip(axs.flat, pick.itertuples()):
    if r.coin not in cache:
        V = pd.read_parquet(os.path.join(LAB, 'v3', f'{r.coin}.parquet'), columns=['bar'])
        fl = sorted(glob.glob(os.path.join(LAB, 'flow', 'klines5m', f'{r.coin}-5m-*.csv')))
    mo = [f for f in fl if r.dt.strftime('%Y-%m') in f or (r.dt + pd.Timedelta(hours=14)).strftime('%Y-%m') in f or (r.dt - pd.Timedelta(hours=8)).strftime('%Y-%m') in f]
    K = pd.concat([pd.read_csv(f, header=None, low_memory=False) for f in mo]); K = K[K.iloc[:,0].astype(str).str.isdigit()].iloc[:, :5].astype(float); K.columns = ['t','o','h','l','c']
    K.index = pd.to_datetime(K.t.astype('int64'), unit='ms', utc=True); K = K.sort_index()
    w = K[(K.index >= r.dt - pd.Timedelta(hours=4)) & (K.index <= r.exit + pd.Timedelta(hours=2))]
    # rebuild the trade from the rules: decision candle closes at r.dt; entry = next candle open; stop beyond last 6 candles (+0.1 ATR5)
    dec = K.index.get_indexer([r.dt - pd.Timedelta(minutes=5)])[0]
    tr = np.maximum(K.h - K.l, np.maximum((K.h - K.c.shift()).abs(), (K.l - K.c.shift()).abs())).rolling(288).mean()
    e = K.o.iloc[dec + 1]; A = tr.iloc[dec]
    st = (K.l.iloc[dec-5:dec+1].min() - 0.1*A) if r.side == 1 else (K.h.iloc[dec-5:dec+1].max() + 0.1*A)
    st = min(st, e*(1-0.0015)) if r.side == 1 else max(st, e*(1+0.0015)); R = abs(e - st); tg = e + r.side*3*R
    width = pd.Timedelta(minutes=3.5)
    for t, b in w.iterrows():
        col = '#26a69a' if b.c >= b.o else '#ef5350'
        ax.vlines(t, b.l, b.h, color=col, lw=0.8); ax.add_patch(plt.Rectangle((mdates.date2num(t - width/2), min(b.o, b.c)), mdates.date2num(t + width/2) - mdates.date2num(t - width/2), max(abs(b.c - b.o), 1e-12), color=col))
    t0, t1 = K.index[dec + 1], r.exit
    ax.hlines(e, t0, t1, color='#2962ff', lw=1.5); ax.hlines(st, t0, t1, color='red', lw=1.5, ls='--'); ax.hlines(tg, t0, t1, color='green', lw=1.5, ls='--')
    ax.axvspan(K.index[dec-5], K.index[dec] + pd.Timedelta(minutes=5), color='yellow', alpha=.12)
    ax.scatter([t0], [e], marker='^' if r.side == 1 else 'v', s=160, color='#2962ff', zorder=5, edgecolor='k')
    res = 'WIN' if r.y > 2 else ('LOSS' if r.y < -0.9 else 'TIME EXIT')
    ax.set_title(f"{r.coin.replace('USDT','')} {'BUY' if r.side == 1 else 'SELL'}  {t0:%b %d %Y %H:%M} UTC  →  {res} {r.y:+.2f}R  (v3 score {r.p:.2f}, v2 agrees {r.v2:+.2f})", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M')); ax.grid(alpha=.2)
fig.suptitle('Brain 2 (entry) + Brain 1 (direction agrees): 6 random trades from the unseen years.  Blue = entry, red dashed = stop, green dashed = 3R target, yellow = the 30 min the stop is built from', fontsize=12)
fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig(r'C:\Users\you\Desktop\brain_trades.png', dpi=95)
print(pick[['coin','dt','side','y','p','v2','exit']].to_string())
