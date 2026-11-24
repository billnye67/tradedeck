"""Hunt 11 - BTC daily Donchian trend ensemble (Zarattini/Pagani/Barbon 2025), frozen spec 2026-10-03."""
import sqlite3, os, sys, time, json
import numpy as np, pandas as pd

DB = r'C:\Users\you\AppData\Local\TradeDeck\Data\history.sqlite'
OUT = r'C:\Users\you\Desktop\TradeDeck\specs'
SP = r'C:\Users\you\AppData\Local\Temp\claude\C--Users-user\10af9477-13da-497a-91cb-b6aa2e41fe42\scratchpad'
LOOKS = [5, 10, 20, 30, 60, 90, 150, 250, 360]
START_EQ = 50_000.0
COMM_BP = 3.0
SLIP_BP = float(os.environ.get('H11_SLIP_BP', '2.0'))
FIN_BP = 5.0
DAY = 86_400_000
END_MS = int(pd.Timestamp('2026-08-29', tz='UTC').value // 10**6)
DELAY_BARS = int(os.environ.get('H11_DELAY', '0'))      # 0 = 00:00 open, 1 = 00:05 open

def log(*a): print(*a, flush=True)

def load(source='binance_um'):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select timestamp ts, open, high, low, close, volume from candles where source=? and market='BTCUSDT' "
                           "and interval='5m' and timestamp < ? order by timestamp", con, params=(source, END_MS))
    con.close()
    return df

def daily(df):
    d = (df['ts'] // DAY) * DAY
    g = df.groupby(d)
    out = pd.DataFrame({'day': g['ts'].first().index.values, 'open': g['open'].first().values, 'high': g['high'].max().values,
                        'low': g['low'].min().values, 'close': g['close'].last().values, 'n': g['ts'].size().values})
    out = out[out['n'] >= 276].reset_index(drop=True)          # a day needs ~96% of its 5m bars
    return out

def sleeves(dd):
    """Per-sleeve active flag decided on each day's completed close (applies from the next execution)."""
    c = dd['close'].values; n = len(c)
    act = np.zeros((n, len(LOOKS)), bool)
    for j, L in enumerate(LOOKS):
        on = False; stop = -np.inf
        for i in range(L, n):
            prev_hi = c[i-L:i].max()
            win = c[i-L+1:i+1]
            mid = (win.max() + win.min()) / 2.0
            if not on:
                if c[i] > prev_hi:
                    on = True; stop = mid
            else:
                stop = max(stop, mid)
                if c[i] <= stop: on = False
            act[i, j] = on
    return act

def run(df, dd, act, target_vol, cap, thresh=0.0, emerg=None, slip_bp=SLIP_BP, label=''):
    """Daily loop. Position decided at day i close, executed at day i+1 first 5m open (plus DELAY_BARS)."""
    c = dd['close'].values; days = dd['day'].values; n = len(c)
    lr = np.diff(np.log(c), prepend=np.nan)
    vol = pd.Series(lr).rolling(90).std().values * np.sqrt(365)
    ts5 = df['ts'].values; op5 = df['open'].values; lo5 = df['low'].values
    eq = START_EQ; E = 0.0
    rows = []; sleeve_pnl = np.zeros(len(LOOKS)); changes = 0; turnover = 0.0
    fin_total = 0.0; cost_total = 0.0; gross_total = 0.0; emerg_hits = 0
    peak = eq; maxdd = 0.0; dd_start = None; longest_rec = 0; rec_start_day = days[0]
    for i in range(90, n - 1):
        # execution price: first 5m open of day i+1 (+ delay)
        k = np.searchsorted(ts5, days[i+1]) + DELAY_BARS
        if k >= len(ts5) or ts5[k] >= days[i+1] + DAY: continue
        px_in = op5[k]
        # target exposure from day i's completed close
        v = vol[i]
        base = min(target_vol / v, cap) if v and v > 0 else 0.0
        n_act = act[i].sum()
        E_target = base * n_act / len(LOOKS)
        if thresh > 0 and E > 0 and abs(E_target - E) < thresh * E: E_target = E
        # trade the change
        dE = E_target - E
        cost = abs(dE) * eq * (COMM_BP + slip_bp) / 1e4
        if abs(dE) > 1e-9: changes += 1; turnover += abs(dE)
        E = E_target
        cost_total += cost; eq -= cost
        # hold through day i+1: pnl on exposure from px_in to next execution price
        k2 = np.searchsorted(ts5, days[i+1] + DAY) + DELAY_BARS
        k2 = min(k2, len(ts5) - 1)
        px_out = op5[k2]
        eq_day_start = eq
        liquidated = False
        if emerg and E > 0:
            # emergency: 5m lows inside day i+1
            seg_lo = lo5[k:k2]
            dd_path = E * (seg_lo / px_in - 1.0)
            hit = np.where(dd_path <= -emerg)[0]
            if len(hit):
                kx = k + hit[0]
                px_out = px_in * (1 - emerg / E)     # fill at the trigger level
                liquidated = True; emerg_hits += 1
        ret = E * (px_out / px_in - 1.0)
        gross = eq_day_start * ret
        gross_total += gross
        # sleeve attribution: equal split of exposure among active sleeves
        if n_act > 0:
            for j in range(len(LOOKS)):
                if act[i, j]: sleeve_pnl[j] += gross / n_act
        # financing: position held across 00:00 UTC of day i+2 (end of day i+1) unless liquidated
        fin = 0.0 if liquidated else E * eq_day_start * FIN_BP / 1e4
        fin_total += fin
        eq = eq_day_start + gross - fin
        if liquidated:
            cost_l = E * eq_day_start * (COMM_BP + slip_bp) / 1e4
            cost_total += cost_l; eq -= cost_l; changes += 1; turnover += E; E = 0.0
        # drawdown / recovery
        if eq >= peak:
            if dd_start is not None:
                longest_rec = max(longest_rec, (days[i+1] - rec_start_day) / DAY); dd_start = None
            peak = eq; rec_start_day = days[i+1]
        else:
            if dd_start is None: dd_start = days[i+1]
            maxdd = max(maxdd, (peak - eq) / peak)
        rows.append(dict(day=days[i+1], E=E if not liquidated else 0.0, held=E > 0 or liquidated, gross=gross, fin=fin, cost=cost, eq=eq, n_act=int(n_act)))
    if dd_start is not None: longest_rec = max(longest_rec, (days[n-1] - rec_start_day) / DAY)
    r = pd.DataFrame(rows)
    return dict(label=label, rows=r, eq=eq, gross=gross_total, cost=cost_total, fin=fin_total, changes=changes, turnover=turnover,
                maxdd=maxdd, longest_rec=longest_rec, sleeve=sleeve_pnl, emerg=emerg_hits)

def period(r, a, b):
    m = (r['day'] >= int(pd.Timestamp(a, tz='UTC').value // 10**6)) & (r['day'] < int(pd.Timestamp(b, tz='UTC').value // 10**6))
    s = r[m]
    if len(s) == 0: return None
    eq0 = s['eq'].iloc[0] - (s['gross'].iloc[0] - s['fin'].iloc[0] - s['cost'].iloc[0])
    return dict(gross=s['gross'].sum(), net_pre=s['gross'].sum() - s['cost'].sum(), net=s['gross'].sum() - s['cost'].sum() - s['fin'].sum(),
                ret=(s['eq'].iloc[-1] / eq0 - 1) * 100, held=s['held'].mean() * 100, days=len(s))

def blocked(r):
    """Strict GLOBAL-2 raid trades on BTC whose entry falls on a day the ensemble holds a position."""
    try:
        g = pd.read_csv(os.path.join(SP, 'global2_strict.csv'))
    except Exception as e:
        return f'raid trade list not readable: {e}'
    b = g[g['market'] == 'BTCUSDT'].copy()
    held_days = set(r.loc[r['held'], 'day'].values)
    b['day'] = (b['entryTs'] // DAY) * DAY
    inb = b[b['day'].isin(held_days)]
    return f"BTC raid trades {len(b)} ({b['netR'].sum():+.1f}R); inside ensemble-held days {len(inb)} ({inb['netR'].sum():+.1f}R)"

def report(res, P, show_exam):
    r = res['rows']
    P(f"### {res['label']}")
    P(f"final equity {res['eq']:,.0f} ({(res['eq']/START_EQ-1)*100:+.1f}%) | gross {res['gross']:+,.0f} | trading costs {res['cost']:,.0f} | "
      f"net before financing {res['gross']-res['cost']:+,.0f} | financing {res['fin']:,.0f} | net after financing {res['gross']-res['cost']-res['fin']:+,.0f}")
    fin_share = res['fin'] / res['gross'] * 100 if res['gross'] > 0 else float('nan')
    P(f"financing = {fin_share:.0f}% of gross profit | exposure changes {res['changes']} | turnover {res['turnover']:.1f}x equity | "
      f"maxDD {res['maxdd']*100:.1f}% | longest recovery {res['longest_rec']:.0f} days | days holding a slot {r['held'].mean()*100:.0f}% | "
      f"avg exposure when held {r.loc[r['held'],'E'].mean():.2f}x | emergency liquidations {res['emerg']}")
    P('period | days | held % | gross $ | net pre-fin $ | net after fin $ | period return %')
    for a, b, nm in [('2020-01-01', '2022-01-01', '2020-21'), ('2022-01-01', '2023-01-01', '2022'), ('2023-01-01', '2024-01-01', '2023'),
                     ('2024-01-01', '2025-01-01', '2024'), ('2025-01-01', '2026-01-01', '2025'), ('2026-01-01', '2026-08-29', '2026')]:
        p = period(r, a, b)
        if p is None: continue
        if nm in ('2024', '2025', '2026') and not show_exam: P(f'{nm} | sealed'); continue
        P(f"{nm} | {p['days']} | {p['held']:.0f} | {p['gross']:+,.0f} | {p['net_pre']:+,.0f} | {p['net']:+,.0f} | {p['ret']:+.1f}")
    P('sleeve gross contribution $: ' + ', '.join(f'{L}d {v:+,.0f}' for L, v in zip(LOOKS, res['sleeve'])))
    P(blocked(r)); P()

def main():
    t0 = time.time()
    df = load(); dd = daily(df); log(f'{len(df):,} 5m bars -> {len(dd)} daily bars {pd.Timestamp(dd["day"].iloc[0], unit="ms").date()} .. {pd.Timestamp(dd["day"].iloc[-1], unit="ms").date()}')
    act = sleeves(dd)
    lines = []; P = lambda s='': (lines.append(s), log(s))
    P(f'# Hunt 11 results (BTC Binance perp, UTC daily from 5m, comm {COMM_BP}bp + slip {SLIP_BP}bp per execution, financing {FIN_BP}bp per 00:00 UTC crossing, delay {DELAY_BARS} bars, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})')
    P()
    pub = run(df, dd, act, 0.25, 2.0, label='PUBLISHED exposure: 25% vol target, 2x cap')
    # gate on construction + validation, net after financing
    r = pub['rows']
    con = period(r, '2020-01-01', '2023-01-01'); val = period(r, '2023-01-01', '2024-01-01')
    show = con is not None and val is not None and con['net'] > 0 and val['net'] > 0
    P(f"## GATE (published version, net after financing): construction 2020-22 {con['net']:+,.0f} $, validation 2023 {val['net']:+,.0f} $ -> {'both positive, exam shown' if show else 'FAIL, exam sealed'}")
    P()
    report(pub, P, show)
    acc = run(df, dd, act, 0.06, 0.5, thresh=0.10, emerg=0.01, label='ACCOUNT-SCALED: 6% vol target, 0.5x cap, 10% rebalance band, 1% day emergency')
    report(acc, P, show)
    stress = run(df, dd, act, 0.25, 2.0, slip_bp=5.0, label='PUBLISHED, stress costs 3+5bp')
    report(stress, P, show)
    nofin = dict(pub); nofin['fin'] = 0.0; nofin['label'] = '(reference) published version if financing were zero'
    P(f"reference: published net before financing {pub['gross']-pub['cost']:+,.0f} $ over the whole sample; financing alone {pub['fin']:,.0f} $")
    sealed = {k: period(pub['rows'], a, b) for k, (a, b) in {'2024': ('2024-01-01', '2025-01-01'), '2025': ('2025-01-01', '2026-01-01'), '2026': ('2026-01-01', '2026-08-29')}.items()}
    with open(os.path.join(SP, 'h11', 'hunt11_exam_sealed.json'), 'w') as f: json.dump(sealed, f, indent=1, default=float)
    P(f'run time {time.time()-t0:.0f}s')
    with open(os.path.join(OUT, f'hunt11-results-delay{DELAY_BARS}.md'), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
