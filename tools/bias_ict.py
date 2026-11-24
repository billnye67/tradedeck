"""ICT bias definitions for X17 (specs/X17-bias-rerun.md). Input: a daily table D (index = date, columns o h l c), oldest first,
e.g. ictlib.daily(d) or a script's own daily table. Outputs are SHIFTED: the value on row k uses only rows 0..k-1, so it is
known before day k opens. +1 bullish, -1 bearish, 0 no trade.

Fractals: daily 3-bar fractal swing high at j when h[j] > h[j-1] and h[j] > h[j+1] (lows mirrored); known only after bar j+1 closes.

S  (market structure, the user): last two confirmed swing highs AND last two confirmed swing lows both rising -> +1; both falling -> -1;
   mixed or fewer than two of each -> 0.
I12 (ICT 'Essentials To Trading The Daily Bias', Card 12 bot-testable): a daily close above the last confirmed swing high (first
   close above that level after it is confirmed) turns bias bullish and records the low of that break bar; bullish lasts until a
   daily close below that low (-> 0) or a bearish break (close below the last confirmed swing low -> -1). Bearish mirrored.
   A later break of a newer swing high in the same direction refreshes the reference low to the newer break bar.
"""
import numpy as np, pandas as pd

def _raw(D):
    h, l, c = D.h.values.astype(float), D.l.values.astype(float), D.c.values.astype(float); n = len(h)
    S = np.zeros(n); I = np.zeros(n)
    highs, lows = [], []                     # confirmed swing levels, in order
    swh = swl = np.nan; swh_used = swl_used = True
    st = 0; ref = np.nan
    for t in range(n):                       # state after the close of bar t
        j = t - 1                            # bar t confirms a fractal at t-1
        if j >= 1:
            if h[j] > h[j - 1] and h[j] > h[t]: highs.append(h[j]); swh = h[j]; swh_used = False
            if l[j] < l[j - 1] and l[j] < l[t]: lows.append(l[j]); swl = l[j]; swl_used = False
        if len(highs) >= 2 and len(lows) >= 2:
            up = highs[-1] > highs[-2] and lows[-1] > lows[-2]; dn = highs[-1] < highs[-2] and lows[-1] < lows[-2]
            S[t] = 1 if up else (-1 if dn else 0)
        # I12: invalidation first (close beyond the break bar), then fresh breaks
        if st == 1 and c[t] < ref: st = 0; ref = np.nan
        elif st == -1 and c[t] > ref: st = 0; ref = np.nan
        bull = (not swh_used) and swh == swh and c[t] > swh
        bear = (not swl_used) and swl == swl and c[t] < swl
        if bull and not bear: st = 1; ref = l[t]; swh_used = True
        elif bear and not bull: st = -1; ref = h[t]; swl_used = True
        I[t] = st
    return S, I

def bias(D):
    """Returns DataFrame (index = D.index) with columns S and I12, shifted one day (row k uses data through row k-1)."""
    S, I = _raw(D)
    return pd.DataFrame({'S': pd.Series(S, index=D.index).shift(1), 'I12': pd.Series(I, index=D.index).shift(1)})

def add_bias(D):
    B = bias(D); D = D.copy(); D['S'] = B.S.values; D['I12'] = B.I12.values; return D
