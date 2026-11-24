"""R17: daily sweep-and-return of prior week/month/quarter/year highs/lows (specs/ict-rebuild/R17-htf-sweep.md). LEARN unless EXAM=1."""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import ictlib as L
EXAM = os.environ.get('EXAM') == '1'; CAP = 60
CRYPTO = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT', 'LINKUSDT', 'ADAUSDT']
STOCKS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD']

def load(src, m):
    c = sqlite3.connect(L.DB)
    d = pd.read_sql(f"select timestamp t,open o,high h,low l,close c from candles where source='{src}' and market='{m}' and interval='15m' order by t", c)
    return L._ny(d)

def load_index(m):
    """ICT-rolled 15m, back-adjusted: at each roll session start (Wed 18:00 NY) all earlier prices shift by the jump."""
    d = L.load_cme_ict(m, '15min'); ts = pd.to_datetime(d.ts); adj = np.zeros(len(d))
    for r in sorted(L.ict_roll_dates()):
        t0 = pd.Timestamp(r).tz_localize('America/New_York') - pd.Timedelta(hours=6)
        i = int((ts < t0).sum())
        if 0 < i < len(d): adj[:i] += d.o.values[i] - d.c.values[i - 1]
    for k in 'ohlc': d[k] = d[k].values + adj
    return d

def tradedate(d, kind):
    if kind == 'stock':
        d = d[(d.hr >= 9.5) & (d.hr < 16)].copy(); d['td'] = d.date; return d
    d = d.copy()
    if kind == 'crypto': d['td'] = d.date
    else: d['td'] = (pd.to_datetime(d.date.astype(str)) + pd.to_timedelta(d.hr + 7, unit='h')).dt.date
    return d

def run(m, d, kind, venue, learn_end):
    d = tradedate(d, kind).reset_index(drop=True)
    d = d[pd.to_datetime(d.td).dt.year <= (2026 if EXAM else learn_end)].reset_index(drop=True)
    d['i'] = np.arange(len(d))
    D = d.groupby('td').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), i0=('i', 'first'), n=('o', 'size'))
    D = D[D.n >= (8 if kind == 'stock' else 20)]
    idx = pd.to_datetime(D.index); P = {}
    for name, fr in (('W', 'W'), ('M', 'M'), ('Q', 'Q'), ('Y', 'Y')):
        per = idx.to_period(fr)
        g = D.groupby(per).agg(h=('h', 'max'), l=('l', 'min')); prev = g.shift(1)
        P[name] = (prev.h.reindex(per).values, prev.l.reindex(per).values)
    h, l, c, o = d.h.values, d.l.values, d.c.values, d.o.values
    tt = pd.to_datetime(d.ts); tsu = (tt.dt.tz_convert('UTC').dt.tz_localize(None) if tt.dt.tz is not None else tt).values
    rows = []; rng = np.random.default_rng(17); busy = -1
    Dh, Dl, Dc, Di = D.h.values, D.l.values, D.c.values, D.i0.values
    for t in range(len(D) - 1):
        if Di[t + 1] <= busy: continue
        his = np.array([P[k][0][t] for k in 'WMQY']); los = np.array([P[k][1][t] for k in 'WMQY'])
        if np.isnan(his).any() or np.isnan(los).any(): continue
        lg = ((los > Dl[t]) & (los < Dc[t])).any(); sh = ((his < Dh[t]) & (his > Dc[t])).any()
        if lg == sh: continue
        side = 1 if lg else -1; q = Di[t + 1]; E = o[q]; stop = Dl[t] if side == 1 else Dh[t]
        if (E - stop) * side <= 0: continue
        cand = his[his > E] if side == 1 else los[los < E]
        if not len(cand): continue
        tgt = cand.min() if side == 1 else cand.max(); risk = abs(E - stop)
        end = Di[t + 1 + CAP] if t + 1 + CAP < len(D) else len(h)
        for kind2, s in (('ICT', side), ('OPP', -side), ('COIN', rng.choice([1, -1]))):
            st = E - s * risk; tg = E + s * abs(tgt - E); xp, xq, how = c[end - 1], end - 1, 'time'
            for k in range(q, end):
                if (l[k] <= st) if s == 1 else (h[k] >= st): xp, xq, how = st, k, 'stop'; break
                if (h[k] >= tg) if s == 1 else (l[k] <= tg): xp, xq, how = tg, k, 'tgt'; break
            if kind2 == 'ICT': busy = xq
            gR = s * (xp - E) / risk; nts = L.rollovers(tsu[q], tsu[xq])
            for v in venue:
                cr = L.cost_R(v, m, E, risk, nights=nts if v != 'cme' else 0, slips=1 if how == 'tgt' else 2)
                rows.append(dict(m=m, kind=kind, cell=f'{v}/{kind2}', date=D.index[t], side=s, gross=gR, net=gR - cr, riskpct=risk / E * 100, nights=nts, how=how))
    return rows

if __name__ == '__main__':
    R = []
    for m in CRYPTO: R += run(m, load('binance_um', m), 'crypto', ('broker',), L.CRYPTO_LEARN_END)
    for m in L.FX_PAIRS: R += run(m, load('fxcm', m), 'fx', ('broker_fx',), L.FX_LEARN_END)
    R += run('XAUUSD', load('metals', 'XAUUSD'), 'gold', ('broker_cmdty',), 2023)
    for m in ('NQ', 'ES'): R += run(m, load_index(m), 'index', ('broker', 'cme'), L.CME_LEARN_END)
    for m in STOCKS: R += run(m, load('databento', m), 'stock', ('broker',), 2023)
    T = pd.DataFrame(R); T['yr'] = pd.to_datetime(T.date).dt.year
    out = os.path.join(os.path.dirname(__file__), '..', 'specs', 'ict-rebuild', 'R17-results' + ('-exam' if EXAM else '-learn') + '.md')
    lines = ['# R17 HTF sweep-and-return' + (' EXAM' if EXAM else ' learn')]
    for (k, cell), g in T.groupby(['kind', 'cell']):
        g = g.sort_values('date'); s5 = g.sort_values('net')
        lines.append(f"{k} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f} ex5 {s5.net.iloc[:-5].mean() if len(g) > 5 else np.nan:+.3f} maxDD {L.maxdd(g.net.values):.0f} medrisk% {g.riskpct.median():.2f} nights {g.nights.median():.0f} | " + ' '.join(f'{y}:{v:+.0f}' for y, v in g.groupby('yr').net.sum().items()))
    for (m, cell), g in T[T.cell.str.endswith('/ICT')].groupby(['m', 'cell']):
        lines.append(f"  {m} {cell}: n={len(g)} gross {g.gross.mean():+.3f} net {g.net.mean():+.3f}")
    txt = chr(10).join(lines); open(out, 'w', encoding='utf-8').write(txt + chr(10)); T.to_csv(out.replace('.md', '.csv'), index=False); print(txt)
