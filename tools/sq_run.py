"""SQ runner: builds every chain for one coin, real + fake levels, for one era; writes trades + random-control means.
usage: python sq_run.py ERA   (ERA = learn | exam). Exam is only run after specs/lab/sq_frozen.json exists."""
import os, sys, time, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sq_lib as S

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'specs', 'lab')
ERAS = {'learn': (pd.Timestamp('2020-01-01', tz='UTC'), pd.Timestamp('2024-01-01', tz='UTC')),
        'exam': (pd.Timestamp('2024-01-01', tz='UTC'), pd.Timestamp('2026-10-01', tz='UTC'))}
NCTRL = 20


def swing_lookup(d15):
    """15m swings, strength 2 each side; returns f(ts, short) -> most recent opposite swing level confirmed before ts."""
    h, l, t = d15.h.values, d15.l.values, d15.t.values
    hi_t, hi_v, lo_t, lo_v = [], [], [], []
    for i in range(2, len(h) - 2):
        if h[i] > max(h[i-1], h[i-2], h[i+1], h[i+2]): hi_t.append(t[i + 2] + 900000); hi_v.append(h[i])
        if l[i] < min(l[i-1], l[i-2], l[i+1], l[i+2]): lo_t.append(t[i + 2] + 900000); lo_v.append(l[i])
    hi_t, hi_v, lo_t, lo_v = map(np.array, (hi_t, hi_v, lo_t, lo_v))
    def f(ts, short):
        tt, vv = (lo_t, lo_v) if short else (hi_t, hi_v)      # short after a HIGH sweep needs the last swing LOW
        k = np.searchsorted(tt, ts, side='right') - 1
        return None if k < 0 else float(vv[k])
    return f


def nights(t0, t1):
    off = 1800000; day = 86400000
    return (t1 - off) // day - (t0 - off) // day


def run_coin(args):
    coin, era = args
    t0 = time.time()
    d5 = S.load5(coin); d15 = S.to15(d5)
    atr15 = S.atr_arr(d15.h.values, d15.l.values, d15.c.values, S.ATR_LEN)
    c5 = {k: d5[k].values for k in ('t', 'o', 'h', 'l', 'c', 'v')}
    T5 = c5['t']; n5 = len(T5)
    e0, e1 = (int(x.timestamp() * 1000) for x in ERAS[era])
    swp = swing_lookup(d15); tick = S.TICK[coin]
    vol = c5['v']; vavg = pd.Series(vol).shift(1).rolling(20).mean().values
    rows = []
    for fake in (False, True):
        for fam in S.LEVELS:
            sweeps = S.find_sweeps(fam, d15, d5, atr15, fake)
            for j15, lv, hi in sweeps:
                ts = d15.t.values[j15]
                if ts < e0 - 86400000 or ts >= e1: continue
                s5 = int(np.searchsorted(T5, ts)); short = bool(hi)
                chains = {}
                ch = S.chain_d(c5, s5, lv, short)
                if ch is not None and ch['fvg'] is not None and S.fvg_ratio(c5['h'], c5['l'], ch['conf'], ch['fvg']) >= S.MIN_FVG_RATIO:
                    chains['D'] = ch
                    if vavg[ch['conf']] > 0 and vol[ch['conf']] >= S.VOL_MULT * vavg[ch['conf']]: chains['DV'] = ch
                cm = S.chain_mss(c5, s5, lv, short, swp)
                if cm is not None: chains['MSS'] = cm
                for cf, ch in chains.items():
                    for en, (lim, rest, dist) in S.zones(c5, ch, short, cf).items():
                        fi = S.fill(c5, lim, rest, dist, ch['sweep_ext'], short, tick)
                        if fi < 0 or T5[fi] < e0 or T5[fi] >= e1 or fi + S.H['12h'] >= n5: continue
                        sg = -1.0 if short else 1.0; r = dict(coin=coin, fake=fake, lev=fam, conf=cf, entry=en,
                                                              side='S' if short else 'L', fill=fi, t=int(T5[fi]), px=lim)
                        for hn, hb in S.H.items():
                            j = fi + hb; g = sg * (c5['c'][j] / lim - 1) * 1e4
                            cost = S.COST_BP + S.NIGHT_BP * nights(T5[fi], T5[j])
                            r[f'net_{hn}'] = g - cost; r[f'opp_{hn}'] = -g - cost
                            if hn != '1h':
                                hh = c5['h'][fi + 1:j + 1].max(); ll = c5['l'][fi + 1:j + 1].min()
                                r[f'mfe_{hn}'] = ((hh / lim - 1) if not short else (1 - ll / lim)) * 1e4
                                r[f'mae_{hn}'] = ((1 - ll / lim) if not short else (hh / lim - 1)) * 1e4
                        rows.append(r)
    T = pd.DataFrame(rows)
    if not len(T): return coin, T, time.time() - t0
    T = T.drop_duplicates(['fake', 'lev', 'conf', 'entry', 'side', 'fill'])
    # control (a): random bars, same coin, same UTC hour, same side, same era; entry at the bar's open
    rng = np.random.default_rng(17)
    hrs = (T5 // 3600000) % 24
    valid = np.where((T5 >= e0) & (T5 < e1) & (np.arange(n5) + S.H['12h'] < n5))[0]
    pools = {hh: valid[hrs[valid] == hh] for hh in range(24)}
    for hn, hb in S.H.items():
        cm = np.zeros(len(T))
        for k, (fi, side) in enumerate(zip(T['fill'].values, T['side'].values)):
            p = pools[int(hrs[fi])]; idx = p[rng.integers(0, len(p), NCTRL)]
            sg = -1.0 if side == 'S' else 1.0
            g = sg * (c5['c'][idx + hb] / c5['o'][idx] - 1) * 1e4
            cm[k] = np.mean(g - S.COST_BP - S.NIGHT_BP * nights(T5[idx], T5[idx + hb]))
        T[f'ctrl_{hn}'] = cm
    return coin, T, time.time() - t0


if __name__ == '__main__':
    era = sys.argv[1]
    if era == 'exam' and not os.path.exists(os.path.join(OUT, 'sq_frozen.json')):
        sys.exit('exam locked: freeze survivors first')
    with ProcessPoolExecutor(3) as ex:
        parts = []
        for coin, T, sec in ex.map(run_coin, [(c, era) for c in S.COINS]):
            print(coin, len(T), 'trades', f'{sec:.0f}s', flush=True); parts.append(T)
    A = pd.concat(parts, ignore_index=True)
    A.to_parquet(os.path.join(OUT, f'sq_trades_{era}.parquet'), index=False)
    print('saved', len(A))
