"""Fake DXtrade REST for executor tests. Scenario knobs via POST /__scenario; state via GET /__state; reset via POST /__reset.
Reproduces the two live failures: (1) a LIMIT already through the market is REJECTED instantly; (2) nothing stops two orders."""
import json, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
S = dict(market=85000.0, reject_through_market=False, latency_ms=0, orders=[], positions=[], history={}, next_id=1000, log=[], logins=0, drop_protection=False, fail_close_n=0, fail_stop_n=0, fail_history=False, transfers=[], fail_delete_n=0, fail_positions_n=0)
ACCOUNTS = ['default:000001', 'default:000002', 'default:000003']
START_BALANCE = 100000.0   # starting equity of every test account


def acct_of(p):
    return p.split('/accounts/')[1].split('/')[0] if '/accounts/' in p else ACCOUNTS[0]
L = threading.Lock()


def book(acct, pc, typ, v):
    S['transfers'].append(dict(account=acct, positionCode=pc, type=typ, value=round(v, 2)))


def settle(pc, px):
    for x in S['positions']:
        if x['positionCode'] == pc:
            sg = 1 if x['side'] == 'BUY' else -1
            book(x.get('account', ACCOUNTS[0]), pc, 'SETTLEMENT', (px - x['openPrice']) * float(x['quantity']) * sg)
            book(x.get('account', ACCOUNTS[0]), pc, 'COMMISSION', -1.25)


def new_id():
    S['next_id'] += 1; return str(S['next_id'])


def maybe_fill():
    """a WORKING entry LIMIT fills when the market crosses it (BUY: market <= limit; SELL: market >= limit).
    A WORKING CLOSE order fills when crossed too (STOP: SELL at market <= stop, BUY at market >= stop; LIMIT: SELL at market >= limit, BUY at market <= limit) and removes the position."""
    for o in S['orders']:
        if o['status'] == 'WORKING' and o.get('positionEffect') == 'CLOSE':
            m = S['market']; hit = False
            if o['type'].startswith('STOP'): hit = (o['side'] == 'SELL' and m <= o['stopPrice']) or (o['side'] == 'BUY' and m >= o['stopPrice'])
            elif o['type'] == 'LIMIT': hit = (o['side'] == 'SELL' and m >= o['limitPrice']) or (o['side'] == 'BUY' and m <= o['limitPrice'])
            if hit:
                o['status'] = 'COMPLETED'; S['history'][o['orderId']]['status'] = 'COMPLETED'; o['fillPrice'] = o['stopPrice'] if o['type'].startswith('STOP') else o['limitPrice']
                settle(o.get('positionCode'), o['fillPrice'])
                S['positions'] = [x for x in S['positions'] if x['positionCode'] != o.get('positionCode')]
            continue
        if o['status'] != 'WORKING' or o.get('positionEffect') != 'OPEN' or o['type'] != 'LIMIT': continue
        lp = o['limitPrice']; m = S['market']
        if (o['side'] == 'BUY' and m <= lp) or (o['side'] == 'SELL' and m >= lp):
            o['status'] = 'COMPLETED'; S['history'][o['orderId']]['status'] = 'COMPLETED'
            S['positions'].append(dict(positionCode=o['orderId'], symbol=o['instrument'], openPrice=lp, quantity=o['quantity'], side=o['side'], account=o.get('account', ACCOUNTS[0])))
            book(o.get('account', ACCOUNTS[0]), o['orderId'], 'COMMISSION', -1.25)


def dx_row(o, hist=False):
    """2026-10-29: serve orders the way 127.0.0.1:8899 does (read off the live account): orderCode prefixed with the session, positionCode /
    positionEffect / the order price only inside legs[] (top level absent), fill price in legs[0].averagePrice and the last execution's
    lastPrice (executions carry no 'price'); no top-level limitPrice/stopPrice."""
    px = o.get('stopPrice') if o['type'].startswith('STOP') else o.get('limitPrice') or 0
    done = o['status'] == 'COMPLETED'; fp = o.get('fillPrice') or px if done else 0.0
    row = dict(orderId=o['orderId'], orderCode='dxsca-integration-session-code:' + o.get('orderCode', ''), clientOrderId=o.get('orderCode', ''), type=o['type'],
               instrument=o['instrument'], status=o['status'], finalStatus=o['status'] != 'WORKING', side=o['side'], tif='GTC', account=o.get('account', ACCOUNTS[0]),
               legs=[dict(instrument=o['instrument'], positionEffect=o.get('positionEffect'), positionCode=o.get('positionCode') if o.get('positionEffect') == 'CLOSE' else o['orderId'],
                          price=px, quantity=o.get('quantity'), filledQuantity=o.get('quantity') if done else 0.0, averagePrice=fp)])
    if hist: row['executions'] = [dict(status='WORKING', finalStatus=False, filledQuantity=0.0)] + ([dict(status='COMPLETED', finalStatus=True, lastPrice=fp, averagePrice=fp)] if done else [])
    return row


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'   # 2026-10-29: like the real broker; HTTP/1.0 closed every connection and .NET's pooled client hit 'An error occurred while sending the request' under three concurrent executors
    def log_message(self, *a): pass

    def _send(self, code, obj=None):
        b = json.dumps(obj).encode() if obj is not None else b''
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}') if n else {}

    def do_POST(self):
        p = urllib.parse.unquote(self.path.split('?')[0]); body = self._body()
        if not p.startswith('/__') and S['latency_ms']: time.sleep(S['latency_ms'] / 1000.0)
        with L:
            S['log'].append(('POST', p, body))
            if p == '/__scenario':
                S.update({k: v for k, v in body.items() if k in ('market', 'reject_through_market', 'latency_ms', 'drop_protection', 'fail_close_n', 'fail_stop_n', 'fail_history', 'fail_delete_n', 'fail_positions_n')})
                if body.get('seed_position'):   # a position that existed before the bot started, with its STOP and TP resting (restart scenario)
                    sp = body['seed_position']; pc = new_id()
                    S['positions'].append(dict(positionCode=pc, symbol=sp['symbol'], openPrice=sp['openPrice'], quantity=sp['qty'], side=sp['side'], account=sp.get('account', ACCOUNTS[0])))
                    opp = 'BUY' if sp['side'] == 'SELL' else 'SELL'
                    for kind, typ, px in (() if sp.get('naked') else (('stop', 'STOP', sp['stop']), ('tp', 'LIMIT', sp['tp']))):   # naked: a position with no STOP/TP at all
                        oid = new_id(); code = f"td-{kind}-seed-{oid}"
                        o = dict(orderId=oid, orderCode=code, type=typ, instrument=sp['symbol'], side=opp, positionEffect='CLOSE', positionCode=pc, status='WORKING', account=sp.get('account', ACCOUNTS[0]),
                                 limitPrice=px if typ == 'LIMIT' else 0, stopPrice=px if typ == 'STOP' else 0, quantity=sp['qty'])
                        S['orders'].append(o); S['history'][oid] = dict(orderId=oid, orderCode=code, status='WORKING')
                if body.get('close_position'):   # manual close in the platform: position gone, its CLOSE orders cancelled, no executions
                    pc = str(body['close_position'])
                    settle(pc, S['market'])   # 2026-11-22: the broker books the hand close as SETTLEMENT + COMMISSION cash rows on /transfers
                    S['positions'] = [x for x in S['positions'] if x['positionCode'] != pc]
                    for o in S['orders']:
                        if o.get('positionCode') == pc and o['status'] == 'WORKING': o['status'] = 'CANCELED'; S['history'][o['orderId']]['status'] = 'CANCELED'
                maybe_fill(); return self._send(200, {'ok': True})
            if p == '/__reset':
                S.update(orders=[], positions=[], history={}, log=[], market=85000.0, reject_through_market=False, latency_ms=0, logins=0, drop_protection=False, fail_close_n=0, fail_stop_n=0, fail_history=False, transfers=[], fail_delete_n=0, fail_positions_n=0); return self._send(200, {'ok': True})
            if p == '/info': return self._send(200, {c: str(S['market']) for c in ('BTC', 'ETH', 'XRP')})   # Hyperliquid allMids stand-in: one price for every coin
            if p.endswith('/login'): S['logins'] += 1; return self._send(200, {'sessionToken': 'harness-token'})
            if p.endswith('/logout'): return self._send(200, {})
            if p.endswith('/orders'):
                oid = new_id(); o = dict(body); o['orderId'] = oid; o['status'] = 'WORKING'; o['account'] = acct_of(p)
                o['limitPrice'] = float(body.get('limitPrice') or 0); o['stopPrice'] = float(body.get('stopPrice') or 0)
                if o['type'] == 'MARKET' and o.get('positionEffect') == 'CLOSE' and S['fail_close_n'] > 0:
                    S['fail_close_n'] -= 1; return self._send(500, {'error': 'simulated close failure'})
                if o['type'] == 'STOP' and o.get('positionEffect') == 'CLOSE' and S['fail_stop_n'] > 0:   # 2026-10-25: transient error on the protective STOP
                    S['fail_stop_n'] -= 1; return self._send(500, {'error': 'simulated stop failure'})
                if o['type'] in ('STOP', 'LIMIT') and o.get('positionEffect') == 'CLOSE' and S['drop_protection']:
                    o['status'] = 'REJECTED'; S['orders'].append(o); S['history'][oid] = dict(o); return self._send(200, {'orderId': str(oid)})
                if o['type'] == 'MARKET' and o.get('positionEffect') == 'CLOSE':
                    o['status'] = 'COMPLETED'; o['fillPrice'] = S['market']; settle(o.get('positionCode'), S['market'])
                    S['positions'] = [x for x in S['positions'] if x['positionCode'] != o.get('positionCode')]
                elif o['type'] == 'MARKET' and o.get('positionEffect') == 'OPEN':
                    o['status'] = 'COMPLETED'
                    S['positions'].append(dict(positionCode=oid, symbol=o['instrument'], openPrice=S['market'], quantity=o['quantity'], side=o['side'], account=o['account']))
                    book(o['account'], oid, 'COMMISSION', -1.25)
                elif o['type'] == 'LIMIT' and o.get('positionEffect') == 'OPEN':
                    m = S['market']; through = (o['side'] == 'BUY' and o['limitPrice'] >= m) or (o['side'] == 'SELL' and o['limitPrice'] <= m)
                    if through and S['reject_through_market']: o['status'] = 'REJECTED'
                S['orders'].append(o); S['history'][oid] = dict(orderId=oid, orderCode=o.get('orderCode'), status=o['status'])
                return self._send(200, {'orderId': oid, 'updateOrderId': oid})
            return self._send(404, {'error': p})

    def do_GET(self):
        if not self.path.startswith('/__') and S['latency_ms']: time.sleep(S['latency_ms'] / 1000.0)
        raw = self.path; p = urllib.parse.unquote(raw.split('?')[0]); q = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        with L:
            if p == '/__state': return self._send(200, dict(market=S['market'], latency_ms=S['latency_ms'], reject=S['reject_through_market'], orders=S['orders'], positions=S['positions'], history=S['history'], log=S['log'][-50:], logins=S['logins']))
            if p.endswith('/users'): return self._send(200, {'userDetails': [{'accounts': [{'account': a, 'accountStatus': 'FULL_TRADING'} for a in ACCOUNTS]}]})
            if p.endswith('/metrics'):
                eq = START_BALANCE
                return self._send(200, {'metrics': [{'equity': eq, 'balance': eq}]})
            if p.endswith('/instruments/query'):
                sym = q.get('symbols', ['BTCUSD'])[0]
                return self._send(200, {'instrumentDetails': [{'symbol': sym, 'tradingStatus': 'FULL', 'minOrderSize': '0.001', 'minOrderSizeIncrement': '0.001', 'maxOrderSize': '100'}]})
            if p.endswith('/transfers'):
                return self._send(200, {'cashTransfers': [{'cashTransactions': [dict(type=c['type'], value=c['value'], positionCode=c['positionCode']) for c in S['transfers'] if c['account'] == acct_of(p)]}]})
            if p.endswith('/positions') and S['fail_positions_n'] > 0: S['fail_positions_n'] -= 1; return self._send(500, {'error': 'simulated positions failure'})
            if p.endswith('/positions'): maybe_fill(); return self._send(200, {'positions': [x for x in S['positions'] if x.get('account', ACCOUNTS[0]) == acct_of(p)]})
            if p.endswith('/orders/history'):
                oid = q.get('with-order-id', [None])[0]; code = q.get('with-order-code', [None])[0]
                if oid: return self._send(200, {'orders': [S['history'][oid]] if oid in S['history'] else []})
                if S['fail_history']: return self._send(500, {'error': 'simulated history failure'})
                # the real endpoint ignores with-order-code and returns the whole history, oldest first
                return self._send(200, {'orders': [dx_row(o, hist=True) for o in S['orders'] if o.get('account', ACCOUNTS[0]) == acct_of(p)]})
            if p.endswith('/orders'):
                return self._send(200, {'orders': [dx_row(o) for o in S['orders'] if o['status'] == 'WORKING' and o.get('account', ACCOUNTS[0]) == acct_of(p)]})
            return self._send(404, {'error': p})

    def do_DELETE(self):
        p = urllib.parse.unquote(self.path)
        with L:
            if S['fail_delete_n'] > 0: S['fail_delete_n'] -= 1; return self._send(500, {'error': 'simulated cancel failure'})
            S['log'].append(('DELETE', p, None)); code = p.rsplit('/', 1)[-1]; code = code.split(':', 1)[1] if ':' in code else code   # the bot deletes by the prefixed code GET /orders returned
            for o in S['orders']:
                if o.get('orderCode') == code and o['status'] == 'WORKING': o['status'] = 'CANCELED'; S['history'][o['orderId']]['status'] = 'CANCELED'
            return self._send(200, {})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8899), H).serve_forever()
