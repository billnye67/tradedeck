"""Search every ICT transcript for model/concept names. Usage: python tools/ict_search.py <subs dir> <titles files...>
Writes specs/ict-concept-map.md: per concept, every video + minute where it is said, and a count of uncaptioned videos."""
import json, os, re, sys, glob, collections
SUBS = sys.argv[1]; TITLES = {}
for f in sys.argv[2:]:
    for line in open(f, encoding='utf-8'):
        p = line.rstrip('\n').split('|')
        if len(p) >= 2: TITLES[p[0]] = p[1]

# concept -> regexes (auto-caption mishearings included)
C = {
 'Judas swing': [r'juda\'?s\'?\s*swing', r'judah\'?s\s*swing', r'judas'],
 'Silver Bullet': [r'silver\s*bullet'],
 'Turtle soup': [r'turtle\s*soup'],
 'Power of three / AMD': [r'power\s*of\s*(three|3)', r'accumulation,?\s*manipulation', r'\bamd\b'],
 'OTE (optimal trade entry)': [r'optimal\s*trade\s*entry', r'\bo\s*t\s*e\b', r'\bote\b'],
 'Breaker block': [r'breaker'],
 'Mitigation block': [r'mitigation\s*block'],
 'Order block': [r'order\s*block'],
 'Rejection block': [r'rejection\s*block'],
 'Propulsion block': [r'propulsion\s*block'],
 'Vacuum block': [r'vacuum\s*block'],
 'Reclaimed order block': [r'reclaimed\s*order\s*block'],
 'Fair value gap': [r'fair\s*value\s*gap', r'\bfvg\b'],
 'Inversion FVG': [r'inversion\s*fair\s*value', r'inverse\s*fair\s*value', r'inversion\s*fvg'],
 'Balanced price range (BPR)': [r'balanced\s*price\s*range', r'\bbpr\b'],
 'Volume imbalance': [r'volume\s*imbalance'],
 'Liquidity void': [r'liquidity\s*void'],
 'Unicorn': [r'unicorn'],
 'CISD (change in state of delivery)': [r'change\s*in\s*(the\s*)?state\s*of\s*delivery', r'\bcisd\b'],
 'Market structure shift / MSS': [r'market\s*structure\s*shift', r'\bmss\b', r'shift\s*in\s*market\s*structure'],
 'Smart money technique / SMT': [r'smart\s*money\s*technique', r'\bsmt\b', r'smt\s*divergence'],
 'Displacement': [r'displacement'],
 'Premium / discount / equilibrium': [r'premium\s*(and|or)\s*discount', r'equilibrium'],
 'Standard deviation projections': [r'standard\s*deviation'],
 'Kill zones': [r'kill\s*zone', r'killzone'],
 'Macros (time)': [r'\bmacro'],
 'London open / London reversal': [r'london\s*(open|close|reversal|kill)'],
 'New York open / reversal': [r'new\s*york\s*(open|reversal|kill|am|pm|lunch)'],
 'Asian range': [r'asian?\s*range', r'asia\s*range'],
 'Central bank dealers range (CBDR)': [r'central\s*bank\s*dealer', r'\bcbdr\b'],
 'Flout': [r'\bflout\b'],
 'Opening range gap / ORG': [r'opening\s*range\s*gap'],
 'NWOG / NDOG': [r'new\s*week\s*opening\s*gap', r'new\s*day\s*opening\s*gap', r'\bnwog\b', r'\bndog\b'],
 'IPDA data ranges (20/40/60)': [r'\bipda\b', r'interbank\s*price\s*delivery', r'(20|40|60|twenty|forty|sixty)\s*day\s*(look\s*back|range)'],
 'Quarterly shifts': [r'quarterly\s*shift'],
 'Seasonal tendencies': [r'seasonal\s*tenden'],
 'Weekly profiles': [r'weekly\s*profile'],
 'Daily bias': [r'daily\s*bias'],
 'Midnight opening price': [r'midnight\s*open'],
 'True day / true week open': [r'true\s*(day|week|daily|weekly|month)'],
 'Market maker buy/sell model (MMXM)': [r'market\s*maker\s*(buy|sell)\s*model', r'\bmmxm\b', r'\bmmbm\b', r'\bmmsm\b'],
 'Sick Sister': [r'sick\s*sister'],
 'Ma Deuce': [r'ma\s*deuce', r'mod\s*deuce'],
 'TGIF setup': [r't\.?\s*g\.?\s*i\.?\s*f', r'thank\s*god\s*it\'?s\s*friday'],
 'One setup for life': [r'one\s*(trading\s*)?setup\s*for\s*life'],
 'Reaper PD array': [r'reaper'],
 'Immediate rebalance': [r'immediate\s*rebalance'],
 'Consequent encroachment / mean threshold': [r'consequent\s*encroachment', r'mean\s*threshold'],
 'Liquidity raid / stop hunt': [r'stop\s*(hunt|run)', r'liquidity\s*(raid|run|sweep|purge)', r'raid'],
 'Relative equal highs/lows': [r'relative(ly)?\s*equal', r'equal\s*(highs|lows)'],
 'Swing failure / failure swing': [r'failure\s*swing', r'swing\s*failure'],
 'Inducement': [r'inducement'],
 'Turtle soup plus one': [r'turtle\s*soup\s*plus'],
 'Algorithm / IPDA price delivery': [r'algorithmic\s*price\s*delivery'],
 'Model numbers ("model 1", "model 2"...)': [r'\bmodel\s*(number\s*)?(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\b'],
}
INTRO = re.compile(r"(i call (it|this|that)|this (is (a|the|my) )?(model|setup|pattern)|the \w+ (model|setup)|i refer to (it|this) as|it'?s called)", re.I)
COMP = {k: re.compile('|'.join(v), re.I) for k, v in C.items()}

def segs(path):
    j = json.load(open(path, encoding='utf-8')); out = []
    for e in j.get('events', []):
        if 'segs' not in e: continue
        txt = ''.join(s.get('utf8', '') for s in e['segs']).replace('\n', ' ').strip()
        if txt: out.append((e.get('tStartMs', 0) / 1000, txt))
    return out

hits = collections.defaultdict(lambda: collections.defaultdict(list)); intros = []; words = {}
files = sorted(glob.glob(os.path.join(SUBS, '*.en.json3')))
for f in files:
    vid = os.path.basename(f).split('.')[0]; S = segs(f)
    # join into ~30s windows so names split across caption lines still match
    win = []; cur, t0 = [], None
    for t, x in S:
        if t0 is None: t0 = t
        cur.append(x)
        if t - t0 >= 30: win.append((t0, ' '.join(cur))); cur, t0 = [], None
    if cur: win.append((t0, ' '.join(cur)))
    words[vid] = sum(len(x.split()) for _, x in S)
    prev = ''
    for t, x in win:
        both = prev[-80:] + ' ' + x
        for k, rx in COMP.items():
            if rx.search(both): hits[k][vid].append(int(t // 60))
        m = INTRO.search(x)
        if m: intros.append((vid, int(t // 60), x[max(0, m.start() - 120): m.end() + 160]))
        prev = x

allvids = set(TITLES); have = {os.path.basename(f).split('.')[0] for f in files}
missing = sorted(allvids - have)
L = ['# ICT concept map — every transcript searched', '',
     f'Videos on the channel: {len(allvids)} | with English captions downloaded: {len(have & allvids)} | NO captions / failed: {len(missing)}',
     f'Words searched: {sum(words.values()):,}', '']
L.append('## Concepts, by how many videos mention them')
for k in sorted(hits, key=lambda k: -len(hits[k])):
    v = hits[k]; tot = sum(len(x) for x in v.values())
    L.append(f'\n### {k} — {len(v)} videos, {tot} mentions')
    for vid in sorted(v, key=lambda z: -len(v[z]))[:12]:
        mins = sorted(set(v[vid])); L.append(f'- {len(v[vid]):3d}x  {TITLES.get(vid, vid)[:90]}  https://youtu.be/{vid}  min ' + ','.join(map(str, mins[:15])))
L.append('\n## Concepts with ZERO mentions: ' + ', '.join(k for k in C if k not in hits))
L.append('\n## Videos with no captions (send to Gemini or skip)')
for vid in missing: L.append(f'- {TITLES.get(vid, "?")}  https://youtu.be/{vid}')
open(r'C:\Users\you\Desktop\TradeDeck\specs\ict-concept-map.md', 'w', encoding='utf-8').write('\n'.join(L) + '\n')
with open(r'C:\Users\you\Desktop\TradeDeck\specs\ict-intros.txt', 'w', encoding='utf-8') as fo:
    for vid, m, x in intros: fo.write(f'{vid}\t{m}\t{TITLES.get(vid, "")[:60]}\t{x}\n')
print('\n'.join(L[:6])); print('intro lines:', len(intros))
