"""Y01: Markov-state strategy from a YouTuber's 'Markov 2.0' video (specs/yt/Y01-markov-states.md). LEARN unless EXAM=1."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L, r17_htf as H
EXAM = os.environ.get('EXAM') == '1'; W = 20; TH = 0.05; MINB = 30

def daily_close(m, kind):
    if kind == 'crypto': d = H.load('binance_um', m)
    elif kind == 'fx': d = H.load('fxcm', m)
    elif kind == 'gold': d = H.load('metals', m)
    elif kind == 'index': d = H.load_index(m)
    else: d = H.load('databento', m)
    d = H.tradedate(d, 'stock' if kind == 'stock' else ('crypto' if kind == 'crypto' else 'fx'))
    D = d.groupby('td').agg(c=('c', 'last'), n=('c', 'size')); D = D[D.n >= (8 if kind == 'stock' else 20)]
    D.index = pd.to_datetime(D.index); return D.c

def state(r):
    return np.where(r > TH, 1, np.where(r < -TH, -1, 0))

def positions(c):
    c = c.values; n = len(c); r20 = np.full(n, np.nan); r20[W:] = c[W:] / c[:-W] - 1
    st = np.where(np.isnan(r20), 9, state(np.nan_to_num(r20)))
    ends = np.arange(W, n, W)                                        # non-overlapping block ends
    cnt = np.zeros((3, 3)); posD = np.zeros(n); posB = np.zeros(n); trend = np.zeros(n); k = 0; nb = 0; curB = 0.0
    for t in range(W, n):
        while k + 1 < len(ends) and ends[k + 1] <= t:                 # add transitions whose second block has closed by t
            a, b = st[ends[k]], st[ends[k + 1]]
            if a != 9 and b != 9: cnt[a + 1, b + 1] += 1; nb += 1
            k += 1
        trend[t] = np.sign(r20[t])
        if nb < MINB: continue
        row = cnt[st[t] + 1]; p = row / row.sum() if row.sum() else np.zeros(3)
        posD[t] = p[2] - p[0]
        if t in set(ends): curB = posD[t]
        posB[t] = curB
    return posD, posB, trend

def pnl(c, pos, kind, m):
    ret = c.pct_change().fillna(0).values; held = np.r_[0, pos[:-1]]   # decided at close t, earns t+1
    turn = np.abs(np.diff(np.r_[0, pos]))
    if kind == 'fx': tc = (0.4e-4 + 0.3 * L.pip_of(m) / c.values) ; sw = 7 * 5e-4 / 5
    elif kind == 'gold': tc = np.full(len(c), (1 + 2) * 1e-4); sw = 7 * 5e-4 / 5
    else: tc = np.full(len(c), (3 + 2) * 1e-4); sw = 7 * 5e-4 / (7 if kind == 'crypto' else 5)
    gross = held * ret; net = gross - turn * tc - np.abs(held) * sw
    return gross, net

GROUPS = {'crypto': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT', 'LINKUSDT', 'ADAUSDT'], 'fx': list(L.FX_PAIRS),
          'gold': ['XAUUSD'], 'index': ['NQ', 'ES'], 'stock': ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD']}

if __name__ == '__main__':
    rows = []
    for kind, ms in GROUPS.items():
        end = L.FX_LEARN_END if kind == 'fx' else 2023
        for m in ms:
            c = daily_close(m, kind); pD, pB, tr = positions(c)
            yr = c.index.year.values; sel = (yr > end) if EXAM else (yr <= end)
            act = sel & (np.r_[0, pD[:-1]] != 0)                       # days after the model started trading
            if act.sum() < 100: continue
            ny = act.sum() / (365 if kind == 'crypto' else 252)
            bh = c.pct_change().fillna(0).values
            out = dict(kind=kind, m=m, years=round(ny, 1), buyhold=bh[act].sum() / ny)
            for nm, p in (('markovD', pD), ('markovB', pB), ('trend', tr)):
                g, nt = pnl(c, p, kind, m)
                out[nm + '_gross'] = g[act].sum() / ny; out[nm + '_net'] = nt[act].sum() / ny
                out[nm + '_sharpe'] = nt[act].mean() / (nt[act].std() + 1e-12) * np.sqrt(365 if kind == 'crypto' else 252)
            rows.append(out)
    T = pd.DataFrame(rows)
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
    cols = ['kind', 'm', 'years', 'buyhold', 'markovD_gross', 'markovD_net', 'markovB_net', 'trend_gross', 'trend_net', 'markovD_sharpe']
    txt = T[cols].round(3).to_string(index=False)
    g = T.groupby('kind')[cols[3:]].mean().round(3).to_string()
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'yt', 'Y01-results' + ('-exam' if EXAM else '-learn') + '.md')
    open(out, 'w', encoding='utf-8').write('# Y01 Markov states (annual return as a fraction of capital; net = Broker costs incl. swap)\n' + txt + '\n\nGroup means\n' + g + '\n')
    print(txt); print(); print(g)
