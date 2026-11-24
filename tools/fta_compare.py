"""FTA step 1: does each FT rebuild (lab/ft/trades/<id>.parquet, unfiltered, pre-portfolio) reproduce its ORIGINAL registry trigger
on the original's own coins and period? Compares trade count and gross bp per trade (cost-independent). Writes specs/lab/fta_compare.csv.
MATCH = n within ~20% AND gross within ~max(5bp, 35% of |orig|) with the same sign; otherwise MISMATCH; NOREF = the original recorded
no comparable number (rules/numbers not on disk) -> NOT REPRODUCIBLE. Original 'raw move' studies (no stop) are compared on n only."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft_run as RN
SL = RN.SL
B4 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']; B3 = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT']
S7 = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'DOGEUSDT', 'SOLUSDT', 'LINKUSDT', 'AVAXUSDT']
# id: (original coins, start, end, orig n, orig gross bp per trade or None, metric note)
REF = {
 'T1':  (B4, '2021-12-01', '2025-06-01', 1475, 46.0, 'hunt27 dev, 2.5% stop 8h'),
 'T2':  (B4, '2022-01-01', '2024-01-01', 37, None, 'hunt25 learn: 37 trades +0.160R net (no gross recorded)'),
 'T3a': (None, None, None, None, None, 'row 6: no rules/numbers on disk'),
 'T3b': (None, None, None, None, None, 'row 7: no rules/numbers on disk'),
 'T4':  (B3, '2017-01-01', '2024-01-01', None, 16.6, 'D4 1h short-break fade gross (raw-move study; n not recorded per cell)'),
 'S05': (None, None, None, None, None, 'row 5: only win rates recorded'),
 'S08': (None, None, None, None, None, 'row 8: +1.3R/4yr only, rules not on disk'),
 'S13': (S7, '2017-01-01', '2024-01-01', None, None, 'hunt4: daily %-returns only'),
 'S14': (['ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'], '2020-01-01', '2024-01-01', None, 0.0, 'hunt5 ~0 gross'),
 'S15': (S7, '2017-01-01', '2024-01-01', 4596, 3.1, 'hunt6 primary 3bp/30min'),
 'S16': (S7, '2020-01-01', '2024-01-01', 2102, None, 'hunt7 -0.12R net (R only)'),
 'S29': (['BTCUSDT'], '2020-01-01', '2024-01-01', None, -1.0, 'hunt13 per day'),
 'S30': (['BTCUSDT'], '2020-01-01', '2024-01-01', None, 1.5, 'hunt13 gated, per day'),
 'S34': (['BTCUSDT'], '2020-01-01', '2024-01-01', None, 7.8, 'hunt16'),
 'S37': (S7, '2021-01-01', '2026-09-01', 22661, 1.0, 'hunt19 all years, per leg'),
 'S38': (['BTCUSDT'], '2020-01-01', '2024-01-01', 865, None, 'hunt20 -0.015R net'),
 'S47': (['SOLUSDT', 'XRPUSDT', 'DOGEUSDT', '1000PEPEUSDT', 'WIFUSDT'], '2019-01-01', '2024-01-01', 42, None, 'hunt30 H2 net base -0.8bp'),
 'S48': (['SOLUSDT', 'XRPUSDT', 'DOGEUSDT', '1000PEPEUSDT', 'WIFUSDT'], '2019-01-01', '2024-01-01', 96, None, 'hunt30 H3 net base -3.7bp'),
 'S49': (['ETHUSDT'], '2020-01-01', '2024-01-01', 1459, 17.8, 'hunt31 net +7.8 base at 10bp -> gross ~+17.8'),
 'S56': (B4, '2020-01-01', '2024-01-01', 941, -2.9, 'hunt33 H3'),
 'S57': (B4, '2020-01-01', '2024-01-01', 65, 13.3, 'hunt34 H1'),
 'S58': (B4, '2021-01-01', '2024-01-01', 739, -1.3, 'hunt34 H2'),
 'S59': (None, None, None, None, None, 'row 59 was trade-level data; FT used a klines proxy -> not the same trigger'),
 'S61': (['BTCUSDT'], '2019-01-01', '2024-01-01', 910, -10.4, 'hunt35'),
 'S62': (['BTCUSDT'], '2019-01-01', '2024-01-01', 874, -9.0, 'hunt35'),
 'S63': (B4, '2020-01-01', '2024-01-01', 2186, -6.9, 'hunt36 H2'),
 'S64': (['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', '1000PEPEUSDT'], '2020-01-01', '2024-01-01', 4, None, 'hunt36 H3 fires 4x'),
 'S65': (['BTCUSDT'], '2019-01-01', '2024-01-01', 673, -6.7, 'hunt37'),
 'S71': (B3, '2017-01-01', '2024-01-01', 242, 12.0, 'D4f down-break long with poke stop'),
 'S72': (B3, '2017-01-01', '2024-01-01', 121, None, 'D4g long 60 + short 61 (raw 48h, no stop)'),
 'S73': (B3, '2017-01-01', '2024-01-01', 321, None, 'D5 long n=321 (raw move, no stop)'),
 'S76': (['BTCUSDT', 'ETHUSDT'], '2020-01-01', '2024-01-01', 1071, 43.0, 'C1 S2/3ATR net +33 (~+43 gross at ~10bp)'),
 'S79': (B3, '2021-01-01', '2024-01-01', None, None, 'D6 ~60 trades/mo, excess-based'),
 'S80': (B3, '2020-01-01', '2024-01-01', 2478, None, 'D7 excess-based'),
 'S82': (['BTCUSDT'], '2021-01-01', '2024-01-01', 247, None, 'O1 big-expiry pin net -9bp'),
 'S84': (['BTCUSDT'], '2019-01-01', '2024-01-01', 31, None, 'C3 5d excess'),
 'S89': (['BTCUSDT'], '2021-01-01', '2023-06-27', 646, 5.0, 'R1 PRE era last hour'),
 'S102': (B3, '2017-01-01', '2024-01-01', 22560, None, 'X9 learn inversions (R only)'),
 'S105': (B3, '2020-01-01', '2024-01-01', 3069, None, 'X10-A A2 SMT -0.206R net'),
 'S141': (None, '2020-01-01', '2024-01-01', None, None, 'R17 crypto 8 coins gross +0.04R (coin list not recorded here)'),
 'SD1': (B4, '2021-12-01', '2025-06-01', 536, None, 'D1 state-1 1h events (raw 4h mean +11.7bp)'),
}
def main():
    rows = []
    for k, (coins, a, b, n0, g0, note) in REF.items():
        t = RN.trades(k)
        if t is None: rows.append(dict(id=k, verdict='NO TRADES FILE', note=note)); continue
        if coins is None and a is None:
            rows.append(dict(id=k, verdict='NOT REPRODUCIBLE', note=note)); continue
        x = t[(t.entry >= pd.Timestamp(a, tz='UTC')) & (t.entry < pd.Timestamp(b, tz='UTC'))]
        if coins is not None: x = x[x.coin.isin(coins)]
        n1 = len(x); g1 = float(1e4 * x.gross.mean()) if n1 else np.nan; r1 = float(x.R.mean()) if n1 else np.nan
        okn = None if not n0 else (abs(n1 - n0) <= 0.2 * n0)
        okg = None if g0 is None else (n1 > 0 and abs(g1 - g0) <= max(5.0, 0.35 * abs(g0)) and (np.sign(g1) == np.sign(g0) or abs(g0) < 2 or abs(g1) < 2))
        checks = [c for c in (okn, okg) if c is not None]
        v = 'NOREF' if not checks else ('MATCH' if all(checks) else 'MISMATCH')
        rows.append(dict(id=k, row=RN.T.ROW.get(k), orig_n=n0, orig_gross_bp=g0, rebuild_n=n1, rebuild_gross_bp=round(g1, 1), rebuild_R=round(r1, 3),
                         n_ok=okn, gross_ok=okg, verdict=v, note=note))
    D = pd.DataFrame(rows); D.to_csv(os.path.join(SL, 'fta_compare.csv'), index=False); print(D.to_string(index=False))
if __name__ == '__main__': main()
