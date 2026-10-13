"""Cameo Link node: finds other Cameo devices on the network, pairs with them and keeps an encrypted link to each.

Ported from AETHER's node (aether/core/aether/net/aether_node.py), with the same pairing and trust rules:
- Discovery: UDP beacons every 2 s (and mDNS through zeroconf when installed). Discovery only lists devices;
  it never trusts or connects to an unpaired one.
- Pairing: Diffie-Hellman (RFC 3526 group 14) with the dialer committing to its share first; both screens show the
  same 6-digit code and a person confirms on EACH device. The pairing key is stored in the state folder.
- Every connection runs a fresh Diffie-Hellman exchange, proven with the stored pairing key, so an old key cannot
  decrypt past traffic. Every frame after that is AES-256-GCM encrypted with a per-direction key and a counter
  nonce: frames cannot be read, forged, replayed or reordered.
Frame: 4-byte length | body, body = 4-byte header length | JSON header | payload (encrypted once keyed).

Frames other than the link's own (hello, pairing, ping) go to on_frame(peer_id, header, payload) on the link's
reader thread; status changes go to on_event(dict).
"""
import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PORT = 47820        # TCP links
DISC_PORT = 47821   # UDP beacons
SERVICE = '_cameo-link._tcp.local.'
MAX_FRAME = 4 * 1024 * 1024
HEARTBEAT_S = 1.0
OFFLINE_S = 4.5     # no frame for this long: the peer is offline
CODE_TTL_S = 120.0

# RFC 3526 2048-bit MODP group 14.
_P = int('FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD'
         'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
         'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F'
         '83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B'
         'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510'
         '15728E5A8AACAA68FFFFFFFFFFFFFFFF', 16)
_G = 2


def _mac(key, *parts):
    return hmac.new(key, b'\x1f'.join(parts), hashlib.sha256).hexdigest()


def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def local_ip():
    """The address of the interface with the default route (no packet is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return '127.0.0.1'


def broadcast_targets(ip):
    """Limited broadcast plus the /24 subnet broadcast: Windows sends 255.255.255.255 on one interface only, and
    some routers drop it, so the directed one usually gets through."""
    out = ['255.255.255.255']
    parts = ip.split('.')
    if len(parts) == 4 and not ip.startswith('127.'):
        out.append('.'.join(parts[:3] + ['255']))
    return out


class LinkError(Exception):
    pass


class Link:
    """One framed TCP connection; encrypted in both directions once keys are set."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.tx = self.rx = None  # AESGCM once keyed
        self.tx_seq = self.rx_seq = 0
        self.wlock = threading.Lock()
        self.last_rx = time.monotonic()
        self.closed = False
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # small control frames go out at once
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        except OSError:
            pass

    def key(self, tx_key, rx_key):
        self.tx, self.rx = AESGCM(tx_key), AESGCM(rx_key)

    def send(self, header, payload=b''):
        raw = json.dumps(header, separators=(',', ':')).encode()
        body = struct.pack('>I', len(raw)) + raw + payload
        with self.wlock:
            if self.tx:
                body = self.tx.encrypt(self.tx_seq.to_bytes(12, 'big'), body, None)
                self.tx_seq += 1
            try:
                self.sock.sendall(struct.pack('>I', len(body)) + body)
            except OSError as e:
                self.close()
                raise LinkError(f'send failed: {e}') from e

    def _read(self, n):
        out = bytearray()
        while len(out) < n:
            try:
                b = self.sock.recv(min(n - len(out), 1 << 20))
            except socket.timeout as e:
                raise LinkError('timed out') from e
            except OSError as e:
                raise LinkError(str(e)) from e
            if not b:
                raise LinkError('connection closed')
            out += b
        return bytes(out)

    def recv(self):
        (n,) = struct.unpack('>I', self._read(4))
        if n > MAX_FRAME + 1024:
            raise LinkError('oversized frame')
        body = self._read(n)
        if self.rx:
            try:
                body = self.rx.decrypt(self.rx_seq.to_bytes(12, 'big'), body, None)
            except Exception as e:  # InvalidTag: forged, replayed, reordered or damaged
                raise LinkError('frame authentication failed') from e
            self.rx_seq += 1
        (hl,) = struct.unpack('>I', body[:4])
        if hl > len(body) - 4:
            raise LinkError('bad frame')
        header = json.loads(body[4:4 + hl])
        if not isinstance(header, dict):
            raise LinkError('bad frame')
        self.last_rx = time.monotonic()
        return header, body[4 + hl:]

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()


class Session:
    """An authenticated, encrypted link to one paired peer."""

    def __init__(self, node, link, info, dialer_id):
        self.node, self.link = node, link
        self.id = info['id']
        self.name = str(info.get('name', self.id))[:60]
        self.platform = info.get('platform', '')
        self.caps = [c for c in info.get('caps', []) if isinstance(c, str)]
        self.port = int(info.get('port', PORT))  # where the peer listens (several peers can share one address)
        self.dialer_id = dialer_id
        self.since = time.time()  # when this link came up
        self.rtt_ms = None        # round trip of the last heartbeat

    @property
    def online(self):
        return not self.link.closed and time.monotonic() - self.link.last_rx < OFFLINE_S

    def reader(self):
        try:
            while True:
                h, payload = self.link.recv()
                self.node._on_frame(self, h, payload)
        except (LinkError, ValueError, OSError) as e:
            self.node._log('link_closed', peer=self.name, reason=str(e))
        finally:
            self.link.close()
            self.node._session_gone(self)


class LinkNode:
    """The same node on every device: pairs, sends and receives. Device facts come from the state folder."""

    def __init__(self, state, name=None, caps=None, on_frame=None, on_event=None, confirm=None,
                 port=PORT, bind='0.0.0.0', discovery=True, disc_port=DISC_PORT):
        self.state = Path(state)
        ident_path = self.state / 'node.json'
        ident = json.loads(ident_path.read_text()) if ident_path.exists() else {}
        if not ident.get('node_id'):
            ident = {'node_id': 'n-' + secrets.token_hex(6), 'name': name or socket.gethostname()}
            _write_private(ident_path, ident)
        self.id = ident['node_id']
        self.name = name or ident.get('name') or socket.gethostname()
        self.caps = caps or []
        self.on_frame = on_frame or (lambda peer, h, payload: None)
        self.on_event = on_event or (lambda ev: None)
        self.confirm = confirm  # fn(peer_name, code, role) -> bool: the person here confirms the shared code
        self.port, self.bind, self.discovery, self.disc_port = port, bind, discovery, disc_port
        self.ip = local_ip() if bind == '0.0.0.0' else bind
        self.peers_path = self.state / 'peers.json'
        self.known = json.loads(self.peers_path.read_text()) if self.peers_path.exists() else {}
        self.sessions = {}
        self.discovered = {}  # node_id -> {name, addr, port, platform, seen}
        self.pairing = None
        self.lock = threading.RLock()
        self.dial_targets = set()
        self._stop = threading.Event()
        self.listening = None  # True / False once the listener tried to bind
        self.listen_error = ''
        self.srv = self.zc = None

    # -- lifecycle -------------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._accept_loop, name='link-accept', daemon=True).start()
        if self.discovery:
            threading.Thread(target=self._beacon_loop, name='link-beacon', daemon=True).start()
            threading.Thread(target=self._discover_loop, name='link-discover', daemon=True).start()
            threading.Thread(target=self._mdns, name='link-mdns', daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, name='link-heartbeat', daemon=True).start()
        for k in list(self.known.values()):  # paired devices reconnect by themselves (never re-pair silently)
            if k.get('addr'):
                self.connect(k['addr'], int(k.get('port', PORT)))

    def stop(self):
        self._stop.set()
        if self.srv:
            self.srv.close()  # frees the port now, not at the accept loop's next timeout
        for s in list(self.sessions.values()):
            s.link.close()
        if self.zc:
            try:
                self.zc.close()
            except Exception:
                pass

    def _log(self, kind, **kw):
        self._emit({'type': 'net_log', 'kind': kind, **kw})

    def _emit(self, ev):
        try:
            self.on_event(ev)
        except Exception as e:  # a UI problem must not break the link
            print(f'Cameo link: event handler failed: {e}', file=sys.stderr)

    # -- peers ---------------------------------------------------------------------------------
    def peers(self):
        """Paired devices (online or not) and devices seen nearby in the last 10 s that are not paired."""
        out = {}
        with self.lock:
            for pid, k in self.known.items():
                s = self.sessions.get(pid)
                out[pid] = {'id': pid, 'name': k.get('name', pid), 'paired': True, 'online': bool(s and s.online),
                            'side': k.get('side', ''), 'addr': s.link.addr if s else k.get('addr'),
                            'platform': s.platform if s else k.get('platform', ''), 'caps': s.caps if s else [],
                            'port': s.port if s else k.get('port', PORT), 'rtt_ms': s.rtt_ms if s and s.online else None,
                            'since': s.since if s else None, 'paired_at': k.get('paired_at')}
            for pid, d in self.discovered.items():
                if pid not in out and time.time() - d['seen'] < 10:
                    out[pid] = {'id': pid, 'name': d['name'], 'paired': False, 'online': True, 'side': '',
                                'addr': d['addr'], 'port': d['port'], 'platform': d.get('platform', ''), 'caps': []}
        return out

    def online(self):
        return [p for p in self.peers().values() if p['paired'] and p['online']]

    def set_side(self, peer_id, side):
        with self.lock:
            if peer_id in self.known and side in ('left', 'right', ''):
                self.known[peer_id]['side'] = side
                _write_private(self.peers_path, self.known)
        self._emit({'type': 'peers'})

    def forget(self, peer_id):
        with self.lock:
            self.known.pop(peer_id, None)
            _write_private(self.peers_path, self.known)
            s = self.sessions.get(peer_id)
        if s:
            s.link.close()
        self._emit({'type': 'peers'})

    def send(self, peer_id, header, payload=b''):
        """Sends one frame; False when the peer is offline or the link broke."""
        s = self.sessions.get(peer_id)
        if not s or not s.online:
            return False
        try:
            s.link.send(header, payload)
            return True
        except LinkError:
            return False

    # -- connections -----------------------------------------------------------------------
    def _accept_loop(self):
        for attempt in range(10):  # a restarted Cameo may find its old socket still closing: retry briefly
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform != 'win32':
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # on Windows this would let a second copy steal the port
            try:
                srv.bind((self.bind, self.port))
                srv.listen(8)
                break
            except OSError as e:
                srv.close()
                if attempt == 9 or self._stop.wait(.5):
                    self.listening, self.listen_error = False, str(e)
                    self._log('listen_failed', error=str(e))
                    return
        self.srv, self.listening = srv, True
        srv.settimeout(1.0)
        while not self._stop.is_set():
            try:
                sock, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_incoming, args=(sock, addr[0]), daemon=True).start()
        srv.close()

    def _handle_incoming(self, sock, addr):
        sock.settimeout(10)
        link = Link(sock, addr)
        try:
            self._handshake(link, dialer=False)
        except (LinkError, ValueError, KeyError, TypeError) as e:
            self._log('handshake_failed', addr=addr, reason=str(e))
            link.close()

    def connect(self, host, port=PORT, allow_pair=False):
        """Keeps dialing a peer while its link is down. Only an explicit pairing request (allow_pair) may start
        pairing, and only once; background redials never do."""
        with self.lock:
            if (host, port) in self.dial_targets and not allow_pair:
                return
            self.dial_targets.add((host, port))
        threading.Thread(target=self._dial_loop, args=(host, port, allow_pair), daemon=True).start()

    def _dial_loop(self, host, port, allow_pair):
        delay = 1.0
        while not self._stop.is_set():
            ok = self._dial_once(host, port, allow_pair)
            if ok == 'stop' or (allow_pair and not ok):
                with self.lock:
                    self.dial_targets.discard((host, port))
                return
            allow_pair = False
            delay = 1.0 if ok else min(delay * 1.6, 6.0)
            self._stop.wait(delay)

    def _dial_once(self, host, port, allow_pair=False):
        with self.lock:
            live = next((s for s in self.sessions.values() if s.link.addr == host and s.port == port and s.online), None)
        if live:
            while live.online and not self._stop.is_set():
                time.sleep(.5)
            return True
        try:
            sock = socket.create_connection((host, port), timeout=4)
        except OSError as e:
            self._log('dial_failed', addr=host, reason=str(e))
            if allow_pair:
                self._emit({'type': 'pair_result', 'ok': False, 'peer': host, 'reason': f'cannot reach {host}:{port} ({e})'})
            return False
        sock.settimeout(10)
        link = Link(sock, host)
        try:
            session = self._handshake(link, dialer=True, allow_pair=allow_pair, port=port)
        except (LinkError, ValueError, KeyError, TypeError) as e:
            self._log('handshake_failed', addr=host, reason=str(e))
            link.close()
            if allow_pair:
                self._emit({'type': 'pair_result', 'ok': False, 'peer': host, 'reason': str(e)})
            return 'stop' if any(w in str(e) for w in ('pairing', 'not paired', 'rejected', 'itself')) else False
        while self.sessions.get(session.id) is session and not self._stop.is_set():
            time.sleep(.5)
        return True

    def _hello(self, nonce, **kw):
        return {'t': 'hello', 'v': 1, 'app': 'cameo', 'id': self.id, 'name': self.name, 'platform': sys.platform,
                'caps': self.caps, 'nonce': nonce, 'port': self.port, **kw}

    def _handshake(self, link, dialer, allow_pair=False, port=PORT):
        priv = secrets.randbits(256)
        pub = format(pow(_G, priv, _P), 'x')
        nonce = secrets.token_hex(16)
        if dialer:  # commit to the key share first, so neither side can grind the confirmation code
            link.send(self._hello(nonce, commit=hashlib.sha256(f'{pub}|{nonce}'.encode()).hexdigest()))
            peer, _ = link.recv()
            if peer.get('t') != 'hello' or 'dh' not in peer or peer.get('app') != 'cameo':
                raise LinkError('not a Cameo device')
            link.send({'t': 'reveal', 'dh': pub})
            peer_pub = peer['dh']
        else:
            peer, _ = link.recv()
            if peer.get('t') != 'hello' or 'commit' not in peer or peer.get('app') != 'cameo':
                raise LinkError('not a Cameo device')
            link.send(self._hello(nonce, dh=pub))
            rv, _ = link.recv()
            peer_pub = rv.get('dh', '')
            if rv.get('t') != 'reveal' or hashlib.sha256(f"{peer_pub}|{peer['nonce']}".encode()).hexdigest() != peer['commit']:
                raise LinkError('key commitment mismatch')
        if peer.get('id') == self.id:
            raise LinkError('connected to itself')
        pv = int(peer_pub, 16)
        if not 1 < pv < _P - 1:
            raise LinkError('bad key share')
        shared = format(pow(pv, priv, _P), 'x').encode()
        pid, pname = str(peer['id'])[:40], str(peer.get('name', peer['id']))[:60]
        n_d, n_a = (nonce, peer['nonce']) if dialer else (peer['nonce'], nonce)
        id_d, id_a = (self.id, pid) if dialer else (pid, self.id)
        dh_d, dh_a = (pub, peer_pub) if dialer else (peer_pub, pub)
        transcript = f'{id_d}|{id_a}|{n_d}|{n_a}|{dh_d}|{dh_a}'.encode()
        stored = self.known.get(pid, {}).get('key')
        key = None
        if dialer:
            if stored:
                link.send({'t': 'auth', 'proof': _mac(bytes.fromhex(stored), b'dialer', transcript)})
                r, _ = link.recv()
                if r.get('t') == 'auth_ok' and hmac.compare_digest(r.get('proof', ''), _mac(bytes.fromhex(stored), b'acceptor', transcript)):
                    key = bytes.fromhex(stored)
                elif r.get('t') != 'need_pair':
                    raise LinkError('authentication rejected')
            if key is None:
                if not allow_pair:
                    raise LinkError('not paired (pair from the Devices window)')
                link.send({'t': 'pair'})
                r, _ = link.recv()
                if r.get('t') != 'pair_ready':
                    raise LinkError('pairing refused by the other device (busy)')
                key = self._confirm_pair(link, shared, transcript, pname, 'dialer')
        else:
            m, _ = link.recv()
            if m.get('t') == 'auth' and stored and hmac.compare_digest(m.get('proof', ''), _mac(bytes.fromhex(stored), b'dialer', transcript)):
                key = bytes.fromhex(stored)
                link.send({'t': 'auth_ok', 'proof': _mac(key, b'acceptor', transcript)})
            else:
                if m.get('t') == 'auth':
                    link.send({'t': 'need_pair'})
                    m, _ = link.recv()
                if m.get('t') != 'pair':
                    raise LinkError('unexpected message during handshake')
                with self.lock:
                    if self.pairing and time.monotonic() < self.pairing['until']:
                        link.send({'t': 'pair_busy'})
                        raise LinkError('another pairing is in progress')
                    self.pairing = {'until': time.monotonic() + CODE_TTL_S, 'peer': pname}
                try:
                    link.send({'t': 'pair_ready'})
                    key = self._confirm_pair(link, shared, transcript, pname, 'acceptor')
                finally:
                    with self.lock:
                        self.pairing = None
        with self.lock:
            fresh = self.known.get(pid, {}).get('key') != key.hex()
            self.known[pid] = {**self.known.get(pid, {}), 'name': pname, 'key': key.hex(), 'addr': link.addr,
                               'port': port if dialer else int(peer.get('port', PORT)), 'platform': peer.get('platform', ''),
                               **({'paired_at': time.time()} if fresh else {})}
            _write_private(self.peers_path, self.known)
        if fresh:
            self._emit({'type': 'pair_result', 'ok': True, 'peer': pname, 'id': pid, 'addr': link.addr})
        # Per-direction keys from this connection's fresh exchange, bound to the pairing key.
        base = hmac.new(key, b'cameo-session\x1f' + transcript + b'\x1f' + shared, hashlib.sha256).digest()
        k_d = hmac.new(base, b'dialer->acceptor', hashlib.sha256).digest()
        k_a = hmac.new(base, b'acceptor->dialer', hashlib.sha256).digest()
        link.key(k_d, k_a) if dialer else link.key(k_a, k_d)
        link.sock.settimeout(OFFLINE_S + 2)
        session = Session(self, link, peer, dialer_id=id_d)
        with self.lock:
            old = self.sessions.get(pid)
            if old and old.online and old is not session:
                # Both sides dialed at once: keep the link dialed by the smaller node id (same choice on both).
                winner = min(self.id, pid)
                if old.dialer_id == winner and session.dialer_id != winner:
                    link.close()
                    raise LinkError('duplicate link closed (the other one is kept)')
            self.sessions[pid] = session
        if old and old is not session:
            old.link.close()
        threading.Thread(target=session.reader, name=f'link-peer-{pid}', daemon=True).start()
        self._emit({'type': 'peer_online', 'peer': session.name, 'id': pid, 'addr': link.addr})
        self._emit({'type': 'peers'})
        return session

    def _confirm_pair(self, link, shared, transcript, pname, role):
        """Numeric comparison: both screens show the same 6 digits; a person confirms on EACH device."""
        digest = hashlib.sha256(b'cameo-sas\x1f' + shared + b'\x1f' + transcript).digest()
        code = f"{int.from_bytes(digest[:8], 'big') % 10**6:06d}"
        k = hashlib.sha256(b'cameo-pair\x1f' + shared + b'\x1f' + transcript).digest()
        self._emit({'type': 'pair_code', 'code': code, 'peer': pname, 'role': role, 'addr': link.addr})
        link.sock.settimeout(CODE_TTL_S + 10)
        try:
            ok = bool(self.confirm and self.confirm(pname, code, role))
            link.send({'t': 'pair_confirm', 'ok': ok, 'mac': _mac(k, role.encode(), transcript) if ok else ''})
            r, _ = link.recv()
        finally:
            self._emit({'type': 'pair_code_done', 'peer': pname})
        other = b'acceptor' if role == 'dialer' else b'dialer'
        theirs = r.get('t') == 'pair_confirm' and r.get('ok') and hmac.compare_digest(r.get('mac', ''), _mac(k, other, transcript))
        if not (ok and theirs):
            reason = 'not confirmed here' if not ok else 'not confirmed on the other device'
            self._emit({'type': 'pair_result', 'ok': False, 'peer': pname, 'reason': reason})
            raise LinkError('pairing rejected: ' + reason)
        return k

    def _session_gone(self, s):
        with self.lock:
            if self.sessions.get(s.id) is not s:
                return
            del self.sessions[s.id]
        self._emit({'type': 'peer_offline', 'peer': s.name, 'id': s.id})
        self._emit({'type': 'peers'})

    # -- discovery (best effort; typing the address is the guaranteed path) ---------------------
    def _beacon(self):
        return json.dumps({'cameo': 1, 'id': self.id, 'name': self.name, 'port': self.port, 'platform': sys.platform}).encode()

    def _beacon_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self._stop.is_set():
            self.ip = local_ip() if self.bind == '0.0.0.0' else self.bind  # follows a change of Wi-Fi
            for dst in broadcast_targets(self.ip):
                try:
                    s.sendto(self._beacon(), (dst, self.disc_port))
                except OSError:
                    pass
            self._stop.wait(2.0)

    def _discover_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('', self.disc_port))
        except OSError as e:
            self._log('discovery_unavailable', error=str(e))
            return
        s.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data, (addr, _) = s.recvfrom(2048)
                m = json.loads(data)
            except (socket.timeout, ValueError):
                continue
            except OSError:
                break
            if isinstance(m, dict) and m.get('cameo') == 1:
                self._seen(m.get('id'), m.get('name', ''), addr, m.get('port', PORT), m.get('platform', ''))

    def _seen(self, pid, name, addr, port, platform=''):
        if not isinstance(pid, str) or pid == self.id:
            return
        try:
            port = int(port)
        except (TypeError, ValueError):
            return
        new = pid not in self.discovered
        self.discovered[pid] = {'name': str(name)[:60], 'addr': addr, 'port': port, 'platform': str(platform)[:20], 'seen': time.time()}
        if new:
            self._emit({'type': 'discovered', 'peer': name, 'addr': addr})
            self._emit({'type': 'peers'})
        if pid in self.known and pid not in self.sessions and port:
            if self.known[pid].get('addr') != addr:  # the peer's address changed (DHCP): remember the new one
                with self.lock:
                    self.known[pid]['addr'], self.known[pid]['port'] = addr, port
                    _write_private(self.peers_path, self.known)
            self.connect(addr, port)

    def _mdns(self):
        """mDNS as a second discovery path (many routers drop broadcast but pass multicast). Optional."""
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
        except ImportError:
            return
        try:
            self.zc = Zeroconf()
            info = ServiceInfo(SERVICE, f'{self.id}.{SERVICE}', addresses=[socket.inet_aton(self.ip)], port=self.port,
                               properties={'id': self.id, 'name': self.name, 'platform': sys.platform})
            self.zc.register_service(info)
        except Exception as e:
            self._log('mdns_unavailable', error=str(e)[:200])
            return
        node = self

        class Listener:
            def add_service(self, zc, kind, name):
                self.update_service(zc, kind, name)

            def update_service(self, zc, kind, name):
                try:
                    found = zc.get_service_info(kind, name, 1500)
                except Exception:
                    return
                if not found or not found.parsed_addresses():
                    return
                props = {k.decode(): (v or b'').decode(errors='replace') for k, v in (found.properties or {}).items()}
                node._seen(props.get('id'), props.get('name', ''), found.parsed_addresses()[0], found.port, props.get('platform', ''))

            def remove_service(self, zc, kind, name):
                pass

        ServiceBrowser(self.zc, SERVICE, Listener())

    def _heartbeat_loop(self):
        while not self._stop.is_set():
            for s in list(self.sessions.values()):
                try:
                    s.link.send({'t': 'ping', 'ts': time.monotonic()})
                except LinkError:
                    pass
                if not s.online and not s.link.closed:
                    self._log('peer_timeout', peer=s.name)
                    s.link.close()
            self._stop.wait(HEARTBEAT_S)

    def _on_frame(self, s, h, payload):
        if h.get('t') == 'ping':
            try:
                s.link.send({'t': 'pong', 'ts': h.get('ts')})  # echoed: the sender times it on its own clock
            except LinkError:
                pass
            return
        if h.get('t') == 'pong':
            if isinstance(h.get('ts'), (int, float)):
                s.rtt_ms = round((time.monotonic() - h['ts']) * 1000, 1)
            return
        try:
            self.on_frame(s.id, h, payload)
        except Exception as e:  # a bad frame from a peer must not end the link
            self._log('frame_error', peer=s.name, kind=h.get('t'), error=str(e)[:200])

    def name_of(self, peer_id):
        return self.known.get(peer_id, {}).get('name', peer_id)

    def caps_of(self, peer_id):
        s = self.sessions.get(peer_id)
        return s.caps if s else []
