"""Grab here, drop there: the carry protocol between paired Cameo devices.

Sender (the device where the hand closed):
  grab          content is resolved in the background: the browser page, the files under the pointer, or a
                picture of the grabbed window. Nothing is announced yet: most grabs just move a window.
  hand leaves   arm(): CARRY to every online paired device. A package up to PRESEND bytes is streamed to all of
                them straight away (staged, hidden), so the one that gets the drop can show it at once.
  CLAIM         a device's camera saw the drop. Claims arriving within CLAIM_WINDOW of the first are compared
                (confidence, then the side the hand left towards); COMMIT goes to the best, CARRY_END to the rest.
  DELIVERED     the receiver's receipt; for files its SHA-256 of every file must match what was read here.
  Nothing claimed within CARRY_TTL, or the hand comes back and opens here: cancelled, nothing moved anywhere.
Receiver:
  CARRY         armed: the camera watches for the drop (a closed hand that opens) until CARRY_TTL
  drop seen     CLAIM with a confidence (how clearly the fist and the opening were seen, how close the hand is)
  COMMIT        a page opens at once; files are unpacked as they arrive (already staged when small), moved into
                the inbox and shown; the receipt goes back to the sender
  CARRY_END     staged bytes are deleted

Frames: carry, carry_end, commit, claim, delivered, xfail, and the stream: open, data (1 MB payloads), end.
Threads: frames arrive on each link's reader thread, grab/arm/tick come from the UI thread, streams run on their
own threads. One lock guards the state; nothing slow runs while it is held.
"""
import shutil
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .package import Packer, PackageError, Unpacker, install_into

CARRY_TTL = 10.0       # seconds the drop may take after the hand leaves the sender
CLAIM_WINDOW = .15     # later claims than this after the first one are too late to compete
PRESEND = 25 * 1024 * 1024
CHUNK = 1 << 20
FIST_FRAMES, OPEN_FRAMES, FIST_RECENT = 2, 2, 2.5
OPEN_POSES = ('palm', 'four', 'three')
CLOSED_POSES = ('fist', 'thumb')  # the thumb-out grab sign arrives closed too
SAFE_OPEN = ('image/', 'video/', 'audio/', 'application/pdf', 'text/plain')


class DropDetector:
    """The drop as this device's camera sees it: a closed hand that opens. A fist seen shortly before the carry
    arrives still counts (the hand can reach this camera before the network message does)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fist_frames, self.fist_at, self.open_frames, self.size = 0, -100.0, 0, 0.0

    def update(self, pose, now, size=None):
        """Returns a confidence 0..1 on the frame the drop completes, else None. pose None = no hand."""
        if now - self.fist_at > FIST_RECENT:
            self.fist_frames = 0
        if pose in CLOSED_POSES:
            self.fist_frames += 1
            self.fist_at, self.open_frames = now, 0
            self.size = max(self.size * .8, size or 0)
            return None
        if pose in OPEN_POSES and self.fist_frames >= FIST_FRAMES:
            self.open_frames += 1
            if self.open_frames >= OPEN_FRAMES:
                # A clear fist and a hand close to this camera (larger in the picture) mean "it is dropped HERE".
                conf = round(.5 * min(1.0, self.fist_frames / 5) + .5 * min(1.0, (self.size or .1) / .2), 3)
                self.reset()
                return conf
        elif pose not in (None, 'neutral'):
            self.open_frames = 0
        return None


def web_url(url):
    """Only ordinary web pages cross devices: never file:, javascript:, data: or an app's own scheme."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not parts.netloc or len(str(url)) > 8192:
        return None
    return str(url)


class Share:
    def __init__(self, node, inbox, post, deliver, now=time.monotonic):
        """node: send(peer, header, payload) / online() / name_of(peer) / caps_of(peer) / name.
        post(event): a status for the UI (called from any thread). deliver(kind, info) -> (status, evidence):
        presents what arrived (opens the page or the files); runs on a worker thread."""
        self.node, self.post, self.deliver, self.now = node, post, deliver, now
        self.inbox = Path(inbox)
        self.staging = self.inbox / '.staging'
        self.lock = threading.RLock()
        self.out = None   # the carry this device is sending
        self.inc = {}     # carries offered to this device: x -> state
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.clean_staging()

    # -- sender --------------------------------------------------------------------------------
    def grab(self, resolve):
        """The hand closed on something here. resolve() -> content dict or None, run in the background:
        {'kind': 'link', 'url', 'title', 'label'} or {'kind': 'files', 'paths', 'label'}."""
        x = 'x-' + secrets.token_hex(6)
        with self.lock:
            self._drop_out('regrabbed')
            self.out = {'x': x, 'content': None, 'resolved': False, 'armed': False, 'announced': False, 'peers': {},
                        'claims': [], 'deadline': None, 'winner': None, 'side': '', 'at': self.now()}

        def work():
            try:
                content = resolve()
                if content and content['kind'] == 'files':
                    packer = Packer(content['paths'], label=content.get('label', ''), sender=self.node.name)
                    content.update(packer=packer, size=packer.size, count=packer.count)
            except (PackageError, OSError) as error:
                content = None
                self.post({'type': 'carry_failed', 'reason': f'Cannot share that: {error}'})
            with self.lock:
                out = self.out
                if not out or out['x'] != x:
                    return
                out['content'], out['resolved'] = content, True
                if out['armed']:
                    self._announce()
        threading.Thread(target=work, name='share-resolve', daemon=True).start()
        return x

    def dropped_here(self):
        """The hand opened on this device: an unarmed grab was just a local move; an armed carry is taken back."""
        with self.lock:
            out = self.out
            if out and out['armed'] and not out['winner']:
                self.cancel('dropped back on this device')
            elif out and not out['armed']:
                self.out = None

    def arm(self, side=''):
        """The hand left this camera still closed: offer the carry to the other devices. False when nothing is held."""
        with self.lock:
            out = self.out
            if not out or out['armed']:
                return False
            out['armed'], out['side'], out['armed_at'] = True, side, self.now()
            if out['resolved']:
                self._announce()
            return True

    def carrying(self):
        with self.lock:
            return bool(self.out and self.out['armed'])

    def _announce(self):
        out = self.out
        content = out['content']
        if content is None:
            self.out = None
            self.post({'type': 'carry_failed', 'reason': 'Nothing to share here (point at a page, a file or a window)'})
            return
        peers = self.node.online()
        if not peers:
            self.out = None
            self.post({'type': 'carry_failed', 'reason': 'No paired device is online to drop on'})
            return
        out['announced'] = True
        head = {'t': 'carry', 'x': out['x'], 'kind': content['kind'], 'label': content.get('label', '')[:200],
                'ttl': CARRY_TTL, 'from': self.node.name}
        if content['kind'] == 'link':
            head.update(url=content['url'], title=content.get('title', '')[:300])
        else:
            head.update(size=content['size'], count=content['count'], stage=content['size'] <= PRESEND)
        for p in peers:
            if self.node.send(p['id'], head):
                out['peers'][p['id']] = {'cancel': threading.Event(), 'stream': None, 'hashes': {}, 'side': p.get('side', ''),
                                         'camera': 'camera' in (p.get('caps') or [])}
        if not out['peers']:
            self.out = None
            self.post({'type': 'carry_failed', 'reason': 'The paired devices did not answer'})
            return
        if content['kind'] == 'files' and content['size'] <= PRESEND:
            for pid in out['peers']:
                self._start_stream(pid)
        self.post({'type': 'carry_armed', 'label': content.get('label', ''), 'peers': [self.node.name_of(p) for p in out['peers']]})
        # A device without a camera cannot see the drop: the side the hand left towards chooses it instead.
        blind = [pid for pid, p in out['peers'].items() if not p['camera'] and p['side'] and p['side'] == out['side']]
        if len(blind) == 1:
            self._commit(blind[0])

    def _start_stream(self, pid):
        peer = self.out['peers'][pid]
        if peer['stream']:
            return
        peer['stream'] = threading.Thread(target=self._stream, args=(pid, self.out['x'], self.out['content'], peer),
                                          name='share-stream', daemon=True)
        peer['stream'].start()

    def _stream(self, pid, x, content, peer):
        packer = content['packer']
        sent, shown = 0, 0.0
        try:
            if not self.node.send(pid, {'t': 'open', 'x': x, 'size': packer.size, 'count': packer.count}):
                raise PackageError('the device went offline')
            for chunk in packer.chunks(CHUNK, peer['hashes']):
                if peer['cancel'].is_set():
                    return
                if not self.node.send(pid, {'t': 'data', 'x': x, 'off': sent}, chunk):
                    raise PackageError('the device went offline')
                sent += len(chunk)
                now = self.now()
                if now - shown > .25:
                    shown = now
                    self._progress(x, pid, sent / packer.size)
            self.node.send(pid, {'t': 'end', 'x': x, 'sha256': peer['hashes']})
        except (PackageError, OSError) as error:
            self.node.send(pid, {'t': 'carry_end', 'x': x, 'why': 'failed'})
            with self.lock:
                out = self.out
                if out and out['x'] == x and out['winner'] == pid:
                    self.out = None
                    self.post({'type': 'carry_failed', 'reason': f'Sending stopped: {error}. Nothing was changed here.'})
                elif out and out['x'] == x and not out['winner']:
                    out['peers'].pop(pid, None)  # its staged copy is gone: a claim from it could not be delivered
                    if not out['peers']:
                        self.out = None
                        self.post({'type': 'carry_failed', 'reason': f'Sending stopped: {error}. Nothing was changed here.'})

    def _progress(self, x, pid, frac):
        with self.lock:
            if self.out and self.out['x'] == x and self.out['winner'] == pid:
                self.post({'type': 'progress', 'peer': self.node.name_of(pid), 'frac': round(frac, 3)})

    def _commit(self, pid):
        out = self.out
        out['winner'] = pid
        self.node.send(pid, {'t': 'commit', 'x': out['x']})
        for other, peer in out['peers'].items():
            if other != pid:
                peer['cancel'].set()
                self.node.send(other, {'t': 'carry_end', 'x': out['x'], 'why': 'taken'})
        if out['content']['kind'] == 'files':
            self._start_stream(pid)
        self.post({'type': 'carry_committed', 'peer': self.node.name_of(pid), 'label': out['content'].get('label', '')})

    def cancel(self, reason='cancelled'):
        with self.lock:
            out, self.out = self.out, None
            if not out:
                return
            for pid, peer in out['peers'].items():
                peer['cancel'].set()
                self.node.send(pid, {'t': 'carry_end', 'x': out['x'], 'why': 'cancelled'})
            if out['armed']:
                self.post({'type': 'carry_cancelled', 'reason': reason})

    def _drop_out(self, reason):
        if self.out and self.out['armed'] and not self.out['winner']:
            self.cancel(reason)
        elif self.out and not self.out['winner']:
            self.out = None

    def _claimed(self, pid, h):
        out = self.out
        if not out or out['x'] != h.get('x') or out['winner'] or pid not in out['peers']:
            return
        conf = float(h.get('conf', 0))
        out['claims'].append((conf, pid))
        if len(out['peers']) == 1:
            self._commit(pid)  # the only device armed: no need to wait for competing claims
        elif out['deadline'] is None:
            out['deadline'] = self.now() + CLAIM_WINDOW

    def tick(self):
        """Called often from the UI thread: settles competing claims and expires carries."""
        now = self.now()
        with self.lock:
            out = self.out
            if out and out['armed'] and not out['winner']:
                claims = [c for c in out['claims'] if c[1] in out['peers']]
                if out['deadline'] is not None and now >= out['deadline'] and claims:
                    side = out['side']
                    best = max(claims, key=lambda c: (c[0] + (.2 if side and out['peers'][c[1]]['side'] == side else 0), -claims.index(c)))
                    self._commit(best[1])
                elif now - out.get('armed_at', now) > CARRY_TTL:
                    self.cancel('nothing was dropped within 10 s')
            for x, st in list(self.inc.items()):
                if not st['committed'] and now - st['at'] > st['ttl'] + 3:
                    self._end_incoming(x, 'expired')

    def _delivered(self, pid, h):
        out = self.out
        if not out or out['x'] != h.get('x') or out['winner'] != pid:
            return
        self.out = None
        content, name = out['content'], self.node.name_of(pid)
        ok, detail = h.get('status') in ('verified', 'sent'), h.get('detail', '')
        if content['kind'] == 'files':
            ours = out['peers'][pid]['hashes']
            match = bool(ours) and h.get('sha256') == ours
            ok = ok and match
            detail = f"{len(ours)} file(s) · SHA-256 match" if match else 'checksums do not match'
        self.post({'type': 'delivered' if ok else 'carry_failed', 'peer': name, 'label': content.get('label', ''),
                   'status': h.get('status'), 'detail': detail, 'reason': f"{name}: {h.get('detail') or detail}"})

    # -- receiver -------------------------------------------------------------------------------
    def armed(self):
        """The carry this device's camera should watch for, or None."""
        with self.lock:
            live = [st for st in self.inc.values() if not st['claimed'] and not st['committed']]
            return max(live, key=lambda st: st['at']) if live else None

    def claim(self, conf, x=None):
        """The drop happened here (camera, or the Drop here button)."""
        with self.lock:
            st = self.inc.get(x) if x else self.armed()
            if not st or st['claimed']:
                return False
            st['claimed'] = True
            self.node.send(st['peer'], {'t': 'claim', 'x': st['x'], 'conf': conf})
            self.post({'type': 'claimed', 'peer': st['name'], 'label': st['label']})
            return True

    def decline(self):
        """Not here (the card's Cancel): this device stops watching; another device may still take it."""
        with self.lock:
            st = self.armed()
            if st:
                self._end_incoming(st['x'], 'declined', quiet=True)

    def on_frame(self, pid, h, payload):
        t = h.get('t')
        with self.lock:
            if t == 'claim':
                self._claimed(pid, h)
            elif t == 'delivered':
                self._delivered(pid, h)
            elif t == 'xfail':
                out = self.out
                if out and out['x'] == h.get('x') and out['winner'] == pid:
                    self.out = None
                    self.post({'type': 'carry_failed', 'reason': f"{self.node.name_of(pid)}: {str(h.get('reason', 'failed'))[:200]}"})
            elif t == 'carry':
                self._carry(pid, h)
            elif t in ('commit', 'open', 'data', 'end', 'carry_end'):
                st = self.inc.get(h.get('x'))
                if not st or st['peer'] != pid:
                    return
                if t == 'carry_end':
                    self._end_incoming(st['x'], h.get('why', 'cancelled'))
                elif t == 'commit':
                    st['committed'] = True
                    self.post({'type': 'receiving', 'peer': st['name'], 'label': st['label'], 'frac': 0.0})
                    self._maybe_finish(st)
                else:
                    self._stream_frame(st, t, h, payload)

    def _carry(self, pid, h):
        x = str(h.get('x', ''))[:40]
        kind = h.get('kind')
        if not x or x in self.inc or kind not in ('link', 'files'):
            return
        if kind == 'link' and not web_url(h.get('url')):
            self.node.send(pid, {'t': 'xfail', 'x': x, 'reason': 'only web pages (http/https) can be opened'})
            return
        for other in [k for k, st in self.inc.items() if not st['committed']]:
            self._end_incoming(other, 'replaced')  # one carry at a time: the newest wins
        name = self.node.name_of(pid)
        self.inc[x] = {'x': x, 'peer': pid, 'name': name, 'kind': kind, 'label': str(h.get('label', ''))[:200], 'at': self.now(),
                       'ttl': min(float(h.get('ttl', CARRY_TTL)), 30.0), 'claimed': False, 'committed': False, 'meta': h,
                       'unpacker': None, 'complete': False, 'failed': False, 'finishing': False, 'shown': 0.0}
        self.post({'type': 'incoming', 'peer': name, 'label': self.inc[x]['label'], 'kind': kind})

    def _stream_frame(self, st, t, h, payload):
        if st['failed']:
            return
        try:
            if t == 'open':
                size = int(h.get('size', -1))
                free = shutil.disk_usage(self.inbox if self.inbox.exists() else self.inbox.anchor).free
                if size < 0 or size + 256 * 1024 * 1024 > free:
                    raise PackageError('not enough free space on this device')
                st['size'], st['unpacker'] = size, Unpacker(self.staging / st['x'])
            elif t == 'data':
                u = st['unpacker']
                if u is None or int(h.get('off', -1)) != u.received or u.received + len(payload) > st['size']:
                    raise PackageError('data arrived out of order')
                u.feed(payload)
                now = self.now()
                if st['committed'] and now - st['shown'] > .25:
                    st['shown'] = now
                    self.post({'type': 'receiving', 'peer': st['name'], 'label': st['label'], 'frac': round(u.received / max(1, st['size']), 3)})
            elif t == 'end':
                u = st['unpacker']
                if u is None or u.received != st['size']:
                    raise PackageError('the package ended early')
                if u.finish() != h.get('sha256'):
                    raise PackageError('SHA-256 mismatch')
                st['complete'] = True
                self._maybe_finish(st)
        except (PackageError, OSError, ValueError, TypeError) as error:
            st['failed'] = True
            if st['unpacker']:
                st['unpacker'].abort()
            self.node.send(st['peer'], {'t': 'xfail', 'x': st['x'], 'reason': str(error)[:200]})
            if st['committed']:
                self.post({'type': 'receive_failed', 'peer': st['name'], 'reason': str(error)[:200]})
            self._end_incoming(st['x'], 'failed', quiet=True)

    def _maybe_finish(self, st):
        if not st['committed'] or st['finishing']:
            return
        if st['kind'] == 'files' and not st['complete']:
            return  # the rest is still arriving; 'end' finishes it
        st['finishing'] = True
        threading.Thread(target=self._finish, args=(st,), name='share-deliver', daemon=True).start()

    def _finish(self, st):
        """Presents what arrived (not under the lock: opening a page or a folder can take a moment)."""
        receipt = {'t': 'delivered', 'x': st['x']}
        try:
            if st['kind'] == 'link':
                meta = st['meta']
                status, evidence = self.deliver('link', {'url': meta['url'], 'title': meta.get('title', ''), 'peer': st['name']})
                receipt.update(status=status, detail=evidence.get('detail', ''))
                self.post({'type': 'received', 'peer': st['name'], 'label': st['label'], 'kind': 'link', 'status': status,
                           'url': meta['url']})
            else:
                u = st['unpacker']
                placed = install_into(u, self.inbox)
                status, evidence = self.deliver('files', {'paths': placed, 'mimes': {e['path']: e.get('mime', '') for e in u.manifest['entries']},
                                                          'peer': st['name']})
                receipt.update(status='verified', sha256=u.hashes, detail=f'{len(u.hashes)} file(s) saved in the inbox',
                               names=[p.name for p in placed])
                self.post({'type': 'received', 'peer': st['name'], 'label': st['label'], 'kind': 'files', 'status': 'verified',
                           'paths': [str(p) for p in placed], 'opened': status})
        except Exception as error:  # the sender must hear about it; nothing is left half-installed in staging
            receipt.update(status='failed', detail=str(error)[:200])
            self.post({'type': 'receive_failed', 'peer': st['name'], 'reason': str(error)[:200]})
        self.node.send(st['peer'], receipt)
        with self.lock:
            self.inc.pop(st['x'], None)
        shutil.rmtree(self.staging / st['x'], ignore_errors=True)

    def _end_incoming(self, x, why, quiet=False):
        st = self.inc.pop(x, None)
        if not st:
            return
        if st['unpacker']:
            st['unpacker'].abort()
        shutil.rmtree(self.staging / x, ignore_errors=True)
        if not quiet:
            self.post({'type': 'incoming_end', 'peer': st['name'], 'label': st['label'], 'why': why})

    def clean_staging(self):
        """Staged bytes of carries that never landed (Cameo quit mid-carry) are deleted at start."""
        shutil.rmtree(self.staging, ignore_errors=True)
