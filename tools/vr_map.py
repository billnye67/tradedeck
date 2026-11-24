"""VR step 1 (spec specs/VR-broker-real-list.md): map Broker's real crypto list (specs/broker_instruments_2026-10-17.md,
read from 127.0.0.1:8899 2026-10-17) to Binance USDT perps in daily_all.parquet, with first/last Binance date, recent dollar volume,
and a per-coin half-spread: measured (row 180) where known, else a CONSERVATIVE estimate from Binance liquidity (log-log fit on the
23 measured coins + 1 residual sd, floor 1bp, cap 25bp). Output: specs/lab/vr_map.csv"""
import os, numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'); LAB = os.path.expandvars(r'%LOCALAPPDATA%\TradeDeck\Data\lab')
VT = ('AAVE ADA AERO ALGO APE APT ARB ARC AR ASTER ATOM AVAX AXS BARD BCH BEAT BERA BNB BSV BTC CAKE CC CHZ CRV DASH DOGE DOT DYDX EIGEN ENA '
      'ETC ETHFI ETH FARTCOIN FET FF FIL GRAM GRASS HBAR H HYPE ICP INJ JTO JUP KAITO KAS KITE LAB LDO LINEA LINK LIT LTC MON MORPHO M NEAR ONDO '
      'OP ORDI PAXG PENDLE PENGU PEPE PIEVERSE POL PUMP PYTH QNT RAVE RENDER RE SAND SEI SHIB SKYAI SKY SLX SOL STRK SUI SUN TAC TAO TIA TRUMP '
      'TRX UB UNI VELVET VET VIRTUAL VVV WIF WLD WLFI XAUT XLM XMR XPL XRP ZEC ZRO').split()
HALF = {'BTC': 0, 'ETH': 0, 'XRP': 0, 'SOL': 0, 'BNB': .1, 'DOGE': .6, 'ADA': .9, 'FIL': 1.3, 'LINK': 1.6, 'ICP': 1.6, 'NEAR': 1.9, 'WLD': 2, 'ARB': 2.5, 'ATOM': 2.9,
        'TIA': 3.3, 'OP': 3.6, 'INJ': 3.8, 'DYDX': 4, 'DOT': 4.3, 'ORDI': 4.8, 'AVAX': 4.9, 'JTO': 5.2, 'SEI': 8}
def build():
    P = pd.read_parquet(os.path.join(LAB, 'daily_all.parquet')); P['date'] = pd.to_datetime(P.date).dt.tz_localize(None); P['dv'] = P.c * P.v
    have = set(P.coin.unique()); end = P.date.max(); rows = []
    for c in VT:
        sym = next((x for x in (c + 'USDT', '1000' + c + 'USDT', '1000000' + c + 'USDT') if x in have), None)
        d = P[P.coin == sym]; rec = d[d.date > end - pd.Timedelta(days=90)]
        rows.append(dict(vt=c, binance=sym, first=d.date.min().date(), last=d.date.max().date(), days=len(d),
                         dv90=float(rec.dv.median()) if len(rec) else np.nan, measured=HALF.get(c, np.nan)))
    M = pd.DataFrame(rows)
    fit = M.dropna(subset=['measured', 'dv90']).query('measured > 0')
    b, a = np.polyfit(np.log(fit.dv90), np.log(fit.measured), 1); sd = float(np.std(np.log(fit.measured) - (a + b * np.log(fit.dv90))))
    est = np.exp(a + b * np.log(M.dv90) + sd).clip(1, 25)
    M['half_bp'] = np.where(M.measured.notna(), M.measured, est.fillna(25)); M['half_src'] = np.where(M.measured.notna(), 'measured row180', 'estimated (liquidity fit +1sd)')
    M.to_csv(os.path.join(ROOT, 'specs', 'lab', 'vr_map.csv'), index=False)
    print(f'fit: log(half) = {a:.2f} + {b:.2f} log(dv90), resid sd {sd:.2f}; {len(M)} coins mapped, {M.binance.isna().sum()} unmapped')
    return M
if __name__ == '__main__':
    M = build(); print(M.sort_values('half_bp').to_string(index=False))
