"""Strategy #1 wiggle test (pre-declared 2026-10-14, before any run). Honest fills (TD_NO_PEEK=1), strict costs, BTC/ETH/XRP.
Move ONE number at a time, everything else at the live value. PASS = every neighbour positive in both eras and no cliff
(no neighbour below half the live value's exam number)."""
import os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'TradeDeck.Research', 'bin', 'Debug', 'net8.0', 'TradeDeck.Research.exe')
BASE = dict(TD_NO_PEEK='1', TD_MAKER_BP='3', TD_TAKER_BP='3', TD_SLIP_BP='2', TD_SLIP_MODE='stops', TD_SWAP_BP='5', TD_MARKETS='BTCUSDT,ETHUSDT,XRPUSDT')
GRID = [('live', None, None),
        ('displacement ATR', 'TD_W_DISP', ['1.5', '1.75', '2.25', '2.5']),
        ('sweep size %', 'TD_W_BREACH', ['0.0008', '0.0010', '0.0016', '0.0020']),
        ('gap size x', 'TD_W_FVG', ['0.25', '0.30', '0.40', '0.45']),
        ('return bars', 'TD_W_RETURN', ['2', '4', '5']),
        ('level age bars', 'TD_W_AGE', ['6', '10', '12']),
        ('trigger strength', 'TD_W_TRIG', ['1', '3'])]
jobs = [(lbl, k, v) for lbl, k, vs in GRID for v in (vs or [None])]
def run(j):
    lbl, k, v = j; env = dict(os.environ, **BASE, **({k: v} if k else {}))
    out = subprocess.run([EXE, 'scale', '15m'], env=env, capture_output=True, text=True).stdout
    rows = re.findall(r'playbook CHECKLIST\s+\| trades\s+([\d,]+) .*?NET exp\s+(-?[\d.]+)R .*?maxDD\s+([\d.]+)R', out)
    return lbl, v, rows
with ThreadPoolExecutor(4) as ex:
    for lbl, v, rows in ex.map(run, jobs):
        (nl, el, dl), (ne, ee, de) = rows
        print(f"{lbl:17s} {str(v or 'LIVE'):7s} | learn n={nl:>5s} {float(el):+.3f}R DD {dl:>5s} | exam n={ne:>5s} {float(ee):+.3f}R DD {de:>5s}", flush=True)
