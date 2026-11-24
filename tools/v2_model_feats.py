"""v2 feature list (copied from v2_model.py so the live bot uses the identical inputs)."""
PLAIN = {'ret1': 'move in the last hour', 'ret4': 'move in the last 4 hours', 'ret24': 'move in the last day', 'ret168': 'move in the last week',
 'd_mid': 'distance from the midnight open', 'd_ldn': 'distance from the London open', 'd_ny': 'distance from the New York open',
 'd_pdh': "distance from yesterday's high", 'd_pdl': "distance from yesterday's low", 'd_hi24': 'distance below the 24h high', 'd_lo24': 'distance above the 24h low',
 'd_hi168': 'distance below the weekly high', 'd_lo168': 'distance above the weekly low', 'disp_up': 'big green displacement candle', 'disp_dn': 'big red displacement candle',
 'wick_up1': 'upper wick (sellers rejecting)', 'wick_dn1': 'lower wick (buyers rejecting)', 'wick_up4': 'upper wick over 4h', 'wick_dn4': 'lower wick over 4h',
 'rng_x': 'size of the last hour vs normal', 'vol_z1': 'unusual volume this hour', 'vol_z4': 'unusual volume over 4h',
 'tshare1': 'aggressive buying share (1h)', 'tshare4': 'aggressive buying share (4h)', 'tshare24': 'aggressive buying share (24h)',
 'cvd1': 'buy/sell pressure (CVD) 1h', 'cvd4': 'buy/sell pressure (CVD) 4h', 'cvd24': 'buy/sell pressure (CVD) 24h', 'div4': 'price vs CVD disagree (4h)', 'div24': 'price vs CVD disagree (24h)',
 'fund': 'funding rate', 'fund7': 'funding over the week', 'fund_z': 'funding unusually high/low', 'oi_chg1': 'open interest change 1h', 'oi_chg4': 'open interest change 4h',
 'oi_chg24': 'open interest change 24h', 'oi_vs7d': 'open interest vs its week average', 'tt_ls': 'top traders long/short', 'tt_ls_chg24': 'top traders shifting long/short',
 'g_ls': 'crowd long/short', 'taker_ls': 'taker buy/sell ratio', 'hour': 'hour of day', 'dow': 'day of week', 'atrp': 'how volatile the coin is', 'atrp_z': 'volatility vs normal',
 'b_ret1': 'BTC last hour', 'b_ret4': 'BTC last 4h', 'b_ret24': 'BTC last day', 'b_tshare4': 'BTC aggressive buying 4h'}
FEATS = list(PLAIN)
