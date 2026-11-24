"""FTA step 2: faithful rebuilds of FT triggers that did NOT match their original rules (see specs/lab/fta_compare.csv).
Each fix restores the ORIGINAL rule that the FT rebuild had changed or dropped; everything else (honest fills, costs, v2 filter,
gates, eras) is FT's frozen pipeline. Fixed trades -> lab/ft/trades_fix/<id>.parquet.
  T2f  deleveraging rebound: perp/INDEX discount (h25 index klines, the original data) instead of the premium-index proxy; 4 coins.
  S15f post-funding pressure: the original PRIMARY cell 3bp / 30 min (FT ran the 1bp / 60 min cell).
  S57f Seoul trap fade: restores the exhaustion-bar condition (doji body < 30% of range, or close in the back half against the break)
       and the 'back to the 23:59 price' target the FT rebuild dropped.
  S71f D4f failed breakout: restores the box condition (24 hourly bars inside 3 x ATR14) and D4f's primary time exit C (48 bars).
  S72f D4g second break: restores the same 3 x ATR14 box condition.
python fta_fix.py build | compare | learn | exam ID SET"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft_lab as L, ft_triggers as T, ft_run as RN
from ft_triggers import S, hb, pos, utc_minute, wilder, cooldown, _window, _days, NAN, H1, DAY
OUT = os.path.join(L.FT, 'trades_fix'); os.makedirs(OUT, exist_ok=True); SL = L.SL
IDX = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad\h25'
B4 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']; B3 = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT']
S7 = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'DOGEUSDT', 'SOLUSDT', 'LINKUSDT', 'AVAXUSDT']
def T2f(coin):
    if coin not in B4: return S([])
    X = L.load(coin); c, oi, t = X['c'], X['oi'], X['t']; n = len(c)
    ix = pd.read_parquet(os.path.join(IDX, f'index_{coin}.parquet')); ix['t'] = pd.to_datetime(ix.ts, unit='ms').astype('datetime64[ns]').astype(np.int64)
    icl = pd.Series(ix.close.to_numpy(), index=ix.t).reindex(t).to_numpy()
    disc = 1e4 * (c / icl - 1)
    B15 = hb(coin, 15); BH = hb(coin, 60); m = utc_minute(X); rows = []; last = -10**9
    k15 = np.searchsorted(B15['end'], np.arange(n) - 12, side='right') - 1
    kH = np.searchsorted(BH['end'], np.arange(n) - 12, side='right') - 1
    for i in range(13, n - 1):
        if k15[i] < 0 or kH[i] < 0 or not disc[i] == disc[i]: continue
        if not (c[i - 12] - c[i] >= 2 * B15['ATR20'][k15[i]]): continue
        if not (oi[i] <= 0.98 * oi[i - 12]): continue
        if not (disc[i] <= -20): continue
        if i - last < 72: continue
        last = i; em = m[i + 1]
        if em < 5 or em > 21 * 60 + 50: continue
        rows.append((coin, i, 1, c[i] - 2 * BH['ATR20'][kH[i]], NAN, NAN, NAN, 24))
    return S(rows)
def S15f(coin):
    X = L.load(coin); F = L.fund(coin); rows = []
    if F is None: return S([])
    for f in F.itertuples():
        if abs(f.rate) < 3e-4: continue                     # original PRIMARY threshold 3bp
        p = pos(X, f.t.value)
        if p <= 0: continue
        side = 1 if f.rate > 0 else -1; a = T.atr_at(coin, p)   # unstopped original: FT's common 2 x ATR20(1h) disaster stop
        if a == a: rows.append((coin, p, side, X['c'][p] - side * 2 * a, NAN, NAN, NAN, 6))   # original PRIMARY hold 30 min
    return S(rows)
def S57f(coin):
    X = L.load(coin); rows = []
    for d in _days(X)[1:]:
        a0, b0 = _window(X, d - DAY, 20, 0, 24, 0); a, b = _window(X, d, 0, 0, 0, 15)
        if a0 is None or a is None: continue
        hi, lo = X['h'][a0:b0].max(), X['l'][a0:b0].min(); vv = X['v'][a:b].sum()
        if vv <= 0: continue
        sh = X['tb'][a:b].sum() / vv; ph, pl = X['h'][a:b].max(), X['l'][a:b].min(); po, pc = X['o'][a], X['c'][b - 1]; rg = ph - pl
        if rg <= 0: continue
        doji = abs(pc - po) < 0.3 * rg
        side = -1 if (ph > hi and sh > 0.583) else (1 if (pl < lo and sh < 0.417) else 0)
        if not side: continue
        rev = (pc < pl + 0.5 * rg) if side < 0 else (pc > pl + 0.5 * rg)          # closes in the back half against the break
        if not (doji or rev): continue
        i = b; ent = X['c'][i]; ext = ph if side < 0 else pl; dist = 1.2 * abs(ext - ent); p2359 = X['c'][b0 - 1]
        tgt = ent - side * 2.5 * dist                                               # 2.5R, or the 23:59 price if it comes first
        tgt = max(tgt, p2359) if side < 0 else min(tgt, p2359)
        if (side < 0 and tgt >= ent) or (side > 0 and tgt <= ent): tgt = NAN
        rows.append((coin, i, side, NAN, dist / ent, tgt, NAN, 14))
    return S(rows)
def _d4_events(B, N=24):
    """original d4f.py/d4g.py event loop: box = prior N hourly bars, span <= 3 x Wilder ATR14 at the break bar, close beyond,
    8-bar cooldown from every qualifying break; re-entry = first close strictly back inside within 24 bars."""
    Hh, Ll, C, A = B['H'], B['L'], B['C'], B['ATR14']; T_ = len(C); i = N + 1; last = -10**9; ev = []
    while i < T_ - 24 - 49:
        if i < last + 8 or not A[i] > 0: i += 1; continue
        top, bot = Hh[i - N:i].max(), Ll[i - N:i].min()
        if (top - bot) / A[i] <= 3.0 and (C[i] > top or C[i] < bot):
            last = i; poke = 1 if C[i] > top else -1
            seg = C[i + 1:i + 25]; back = np.where((seg < top) & (seg > bot))[0]
            ev.append((i, poke, top, bot, (i + 1 + back[0]) if len(back) else None))
        i += 1
    return ev
def S71f(coin):
    B = hb(coin); Hh, Ll = B['H'], B['L']; rows = []
    for i, poke, top, bot, j in _d4_events(B):
        if j is None: continue
        s = -poke; stop = Ll[i:j + 1].min() if s == 1 else Hh[i:j + 1].max(); tgt = top if s == 1 else bot   # poke-extreme stop, exit A far side
        rows.append((coin, B['end'][j], s, stop, NAN, tgt, NAN, 576))                                       # time exit C = 48 bars
    return S(rows)
def S72f(coin):
    B = hb(coin); C = B['C']; rows = []
    for i, poke, top, bot, j in _d4_events(B):
        if j is None: continue
        s = -poke; seg2 = C[j + 1:j + 49]
        brk = np.where(seg2 > top)[0] if s == 1 else np.where(seg2 < bot)[0]
        brk0 = np.where(seg2 < bot)[0] if s == 1 else np.where(seg2 > top)[0]
        if len(brk) and (not len(brk0) or brk[0] < brk0[0]):
            m = j + 1 + brk[0]; rows.append((coin, B['end'][m], s, bot if s == 1 else top, NAN, NAN, NAN, 576))   # FT's frozen stop/exit
    return S(rows)
FIX = {'T2f': T2f, 'S15f': S15f, 'S57f': S57f, 'S71f': S71f, 'S72f': S72f}
BASE = {'T2f': 'T2', 'S15f': 'S15', 'S57f': 'S57', 'S71f': 'S71', 'S72f': 'S72'}
# original reference on its own coins/period: (coins, start, end, n, gross bp or None, side filter or None, note)
REF = {'T2f': (B4, '2022-01-01', '2024-01-01', 37, None, None, 'hunt25 primary: 37 trades (ONE position global), +0.160R net'),
       'S15f': (S7, '2017-01-01', '2024-01-01', 4596, 3.1, None, 'hunt6 primary 3bp/30min, unstopped gross'),
       'S57f': (B4, '2020-01-01', '2024-01-01', 65, 13.3, None, 'hunt34 H1, two slots; R -0.383'),
       'S71f': (B3, '2017-01-01', '2024-01-01', 242, 12.2, 1, 'D4f down-break LONG with poke stop'),
       'S72f': (B3, '2017-01-01', '2024-01-01', 60, None, 1, 'D4g long n=60 (orig raw 48h no stop -16bp; FT adds its frozen stop)')}
def _coin(coin):
    res = {}
    for k, fn in FIX.items():
        try: s = fn(coin); res[k] = L.simulate(s) if len(s) else None
        except Exception as e: res[k] = f'ERR {type(e).__name__}: {e}'
    return coin, res
def build():
    from concurrent.futures import ProcessPoolExecutor
    acc = {k: [] for k in FIX}
    with ProcessPoolExecutor(6) as ex:
        for coin, res in ex.map(_coin, L.COINS):
            for k, v in res.items():
                if isinstance(v, str): print('ERR', k, coin, v, flush=True)
                elif v is not None and len(v): acc[k].append(v)
    for k, v in acc.items():
        if v: pd.concat(v).sort_values(['entry', 'coin']).reset_index(drop=True).to_parquet(os.path.join(OUT, f'{k}.parquet'), index=False)
        print('built', k, sum(len(x) for x in v), flush=True)
def trades(k):
    f = os.path.join(OUT, f'{k}.parquet')
    if not os.path.exists(f): return None
    t = pd.read_parquet(f)
    for c in ('sig', 'entry', 'exit'): t[c] = pd.to_datetime(t[c], utc=True)
    return t
def compare():
    rows = []
    for k, (coins, a, b, n0, g0, side, note) in REF.items():
        t = trades(k); x = t[t.coin.isin(coins) & (t.entry >= pd.Timestamp(a, tz='UTC')) & (t.entry < pd.Timestamp(b, tz='UTC'))]
        if side: x = x[x.side == side]
        lx = RN.in_era(t, 'learn'); mv, cs = float(np.median(lx.move)), float(np.median(lx.cost))
        rows.append(dict(id=k, orig_n=n0, orig_gross_bp=g0, fixed_n=len(x), fixed_gross_bp=round(1e4 * x.gross.mean(), 1), fixed_R=round(x.R.mean(), 3),
                         learn_n_all=len(lx), five_x_ratio=round(mv / cs, 2), five_x='PASS' if mv / cs >= 5 else 'fail', note=note))
    D = pd.DataFrame(rows); D.to_csv(os.path.join(SL, 'fta_fix_compare.csv'), index=False); print(D.to_string(index=False)); return D
def learn():
    D = pd.read_csv(os.path.join(SL, 'fta_fix_compare.csv')); res = {}
    for k in D[D.five_x == 'PASS'].id:
        t = L.add_scores(trades(k)); t.to_parquet(os.path.join(OUT, f'{k}.parquet'), index=False)
        for cs, coins in (('ALL', L.COINS), ('BIG6', L.BIG6)):
            r = RN.evaluate(t, 'learn', coins); print(RN.fmt(k, cs, r, 'learn'), flush=True)
            res[f'{k}|{cs}'] = dict(st=r['st'], ok=r['ok'], gates=r['gates'], ev=r['ev'], ev_s1=r['ev_s1'], rnd=r['rnd'])
    json.dump(res, open(os.path.join(SL, 'fta_fix_learn.json'), 'w'), indent=1, default=str)
def exam(k, cs):
    t = trades(k)
    if 'v2' not in t.columns: t = L.add_scores(t)
    r = RN.evaluate(t, 'exam', L.COINS if cs == 'ALL' else L.BIG6); print(RN.fmt(k, cs, r, 'EXAM-A'), flush=True)
    r['P'].to_csv(os.path.join(SL, f'fta_exam_{k}_{cs}.csv'), index=False)
if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'build': build()
    elif cmd == 'compare': compare()
    elif cmd == 'learn': learn()
    elif cmd == 'exam': exam(sys.argv[2], sys.argv[3])
