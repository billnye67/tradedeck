"""SQ runner, Amendment B (fixed exit: 3R target, stops S1 = sweep wick, S2 = entry structure far side; tick-safe min).
usage: python sq_run2.py ERA [cells.json]   ERA = learn | exam (exam needs specs/lab/sq_frozen.json)."""
import os, sys, json, time, numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sq_lib as S
from sq_run import swing_lookup, nights, ERAS, OUT

NCTRL = 20


def net_r(rg, entry, risk, stopped, t0, t1):
    cost_bp = S.COST_BP + (S.STOP_SLIP_BP if stopped else 0) + S.NIGHT_BP * nights(t0, t1)
    return rg - cost_bp / 1e4 * entry / risk


def trade(c5, fi, entry, dist, short):
    stop = entry + dist if short else entry - dist
    tgt = entry - 3 * dist if short else entry + 3 * dist
    rg, xi, st = S.sim_exit(c5['h'], c5['l'], c5['c'], fi, entry, stop, tgt, short, S.MAX_HOLD)
    return net_r(rg, entry, dist, st, c5['t'][fi], c5['t'][xi])


def run_coin(args):
    coin, era, only = args
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
            if only and not any(o[0] == fam for o in only): continue
            for j15, lv, hi in S.find_sweeps(fam, d15, d5, atr15, fake):
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
                    for en, (lim, rest, dist, far) in S.zones(c5, ch, short, cf).items():
                        if only and (fam, cf, en) not in only: continue
                        fi = S.fill(c5, lim, rest, dist, ch['sweep_ext'], short, tick)
                        if fi < 0 or T5[fi] < e0 or T5[fi] >= e1: continue
                        mind = max(5 * tick, 0.0015 * lim)
                        for sv, sp in (('S1', ch['sweep_ext']), ('S2', far)):
                            raw = (sp - lim) if short else (lim - sp)
                            if raw <= 0: continue
                            d = max(raw, mind)
                            r = dict(coin=coin, fake=fake, lev=fam, conf=cf, entry=en, stop=sv,
                                     side='S' if short else 'L', fill=fi, t=int(T5[fi]), px=lim, dpct=d / lim,
                                     R=trade(c5, fi, lim, d, short),
                                     R_opp=trade(c5, fi, lim, d, not short))
                            if sv == 'S1': r['R_nomin'] = trade(c5, fi, lim, raw, short)
                            rows.append(r)
    T = pd.DataFrame(rows)
    if not len(T): return coin, T, time.time() - t0
    T = T.drop_duplicates(['fake', 'lev', 'conf', 'entry', 'stop', 'side', 'fill'])
    # control (a) only for real-level trades
    rng = np.random.default_rng(17)
    hrs = (T5 // 3600000) % 24
    valid = np.where((T5 >= e0) & (T5 < e1) & (np.arange(n5) + 10 < n5))[0]
    pools = {hh: valid[hrs[valid] == hh] for hh in range(24)}
    ctrl = np.full(len(T), np.nan)
    for k, (fk, fi, side, dp) in enumerate(zip(T.fake.values, T['fill'].values, T.side.values, T.dpct.values)):
        if fk: continue
        p = pools[int(hrs[fi])]; idx = p[rng.integers(0, len(p), NCTRL)]
        sh = side == 'S'; v = 0.0
        for b in idx:
            e = c5['o'][b]; v += trade(c5, int(b), e, dp * e, sh)
        ctrl[k] = v / NCTRL
    T['R_ctrl'] = ctrl
    return coin, T, time.time() - t0


if __name__ == '__main__':
    era = sys.argv[1]
    only = None
    if era == 'exam':
        fz = os.path.join(OUT, 'sq_frozen.json')
        if not os.path.exists(fz): sys.exit('exam locked: freeze survivors first')
        only = {(c['lev'], c['conf'], c['entry']) for c in json.load(open(fz))['cells']}
    with ProcessPoolExecutor(3) as ex:
        parts = []
        for coin, T, sec in ex.map(run_coin, [(c, era, only) for c in S.COINS]):
            print(coin, len(T), 'rows', f'{sec:.0f}s', flush=True); parts.append(T)
    A = pd.concat(parts, ignore_index=True)
    A.to_parquet(os.path.join(OUT, f'sq2_trades_{era}.parquet'), index=False)
    print('saved', len(A))
