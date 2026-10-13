"""Grab and drop between devices: the .cameo package, the drop detector, the carry protocol (in memory, several
receivers), and real encrypted links between nodes on this computer (pairing, reconnect, a file through the link)."""
import hashlib
import os
from pathlib import Path
import queue
import shutil
import socket
import tempfile
import threading
import time
import unittest

from cameo.gestures.classic import GestureEngine
from cameo.link.content import reachable
from cameo.link.node import LinkNode
from cameo.link.package import MAGIC, PackageError, Packer, Unpacker, install_into, pack_file, safe_rel, unpack_file
from cameo.link.share import CARRY_TTL, DropDetector, Share


def tree(root):
    """Sample content: a picture, a nested folder with an empty file and a non-ASCII name."""
    root = Path(root)
    (root / 'album' / 'inner').mkdir(parents=True)
    (root / 'photo.jpg').write_bytes(os.urandom(300_000))
    (root / 'album' / 'a.txt').write_text('hello')
    (root / 'album' / 'inner' / 'empty.bin').write_bytes(b'')
    (root / 'album' / 'inner' / 'வணக்கம்.mp4').write_bytes(os.urandom(70_000))
    (root / 'album' / 'hollow').mkdir()
    return [root / 'photo.jpg', root / 'album']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.src = tree(self.dir / 'src')

    def test_stream_in_odd_pieces_rebuilds_every_file_and_folder(self):
        packer = Packer(self.src, sender='A')
        data = b''.join(packer.chunks(4096))
        self.assertEqual(len(data), packer.size)
        unpacker = Unpacker(self.dir / 'stage')
        for i in range(0, len(data), 7777):  # frames never line up with the files
            unpacker.feed(data[i:i + 7777])
        self.assertEqual(unpacker.finish(), packer.hashes)
        self.assertEqual(unpacker.top, ['photo.jpg', 'album'])
        placed = install_into(unpacker, self.dir / 'inbox')
        self.assertEqual(placed, [self.dir / 'inbox' / 'Pictures' / 'photo.jpg', self.dir / 'inbox' / 'Folders' / 'album'])
        self.assertEqual(digest(placed[0]), digest(self.src[0]))
        self.assertEqual(digest(placed[1] / 'inner' / 'வணக்கம்.mp4'), digest(self.src[1] / 'inner' / 'வணக்கம்.mp4'))
        self.assertTrue((placed[1] / 'inner' / 'empty.bin').is_file() and (placed[1] / 'hollow').is_dir())
        # A second drop of the same things never overwrites the first.
        again = Unpacker(self.dir / 'stage2')
        again.feed(data)
        again.finish()
        self.assertEqual(install_into(again, self.dir / 'inbox')[0].name, 'photo (1).jpg')

    def test_saved_file_round_trip_and_damage_is_detected(self):
        out = self.dir / 'x.cameo'
        pack_file(self.src, out)
        self.assertTrue(out.read_bytes().startswith(MAGIC))
        self.assertEqual(unpack_file(out, self.dir / 'ok').hashes['photo.jpg'], digest(self.src[0]))
        raw = bytearray(out.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        out.write_bytes(bytes(raw))
        with self.assertRaises(PackageError):
            unpack_file(out, self.dir / 'bad')

    def test_paths_that_could_escape_are_refused(self):
        for bad in ('../evil.txt', 'a/../../evil', 'C:evil', 'con.txt', 'a/nul', ''):
            with self.assertRaises(PackageError, msg=bad):
                safe_rel(bad)
        self.assertEqual(safe_rel('/etc/passwd'), ['etc', 'passwd'])  # made relative, lands inside the staging folder
        import json
        import struct
        manifest = json.dumps({'v': 1, 'entries': [{'path': '../../evil.txt', 'size': 1}]}).encode()
        with self.assertRaises(PackageError):
            Unpacker(self.dir / 'stage').feed(MAGIC + struct.pack('>I', len(manifest)) + manifest + b'x')
        self.assertFalse((self.dir / 'evil.txt').exists())

    def test_a_file_changed_while_sending_stops_the_stream(self):
        packer = Packer([self.src[0]])
        chunks = packer.chunks(4096)
        next(chunks)
        self.src[0].write_bytes(b'different')
        with self.assertRaises(PackageError):
            list(chunks)


class DropDetectorTest(unittest.TestCase):
    def feed(self, detector, poses, start=0.0, step=.04):
        found = None
        for i, pose in enumerate(poses):
            found = detector.update(pose, start + i * step, .15) or found
        return found

    def test_closed_hand_that_opens_is_a_drop(self):
        self.assertGreater(self.feed(DropDetector(), ['fist'] * 4 + ['neutral', 'palm', 'palm']), .5)

    def test_thumb_grab_sign_that_opens_is_a_drop(self):
        self.assertIsNotNone(self.feed(DropDetector(), ['thumb'] * 4 + ['palm', 'palm']))

    def test_open_hand_alone_or_a_stale_fist_is_not(self):
        self.assertIsNone(self.feed(DropDetector(), ['palm'] * 10))
        d = DropDetector()
        self.feed(d, ['fist'] * 4)
        self.assertIsNone(self.feed(d, ['palm'] * 3, start=5.0))

    def test_fist_seen_before_the_hand_briefly_vanishes_still_counts(self):
        self.assertIsNotNone(self.feed(DropDetector(), ['fist'] * 3 + [None] * 5 + ['four'] * 2))


class GrabEngineTest(unittest.TestCase):
    def hold(self, engine, pose, seconds, now):
        events = []
        while seconds > 0:
            events += engine.update({'pose': pose, 'x': .5, 'y': .5, 'pinch': .6, 'palm_x': .5, 'palm_y': .5, 'hands': 1}, now[0])
            now[0] += .04
            seconds -= .04
        return events

    def test_hand_leaving_closed_is_marked_lost_and_opening_is_not(self):
        engine, now = GestureEngine(), [0.0]
        self.hold(engine, 'three', .3, now)
        self.hold(engine, 'fist', .3, now)
        now[0] += .5
        self.assertEqual(engine.update(None, now[0]), [('drop', 'lost')])
        engine, now = GestureEngine(), [0.0]
        self.hold(engine, 'three', .3, now)
        self.hold(engine, 'fist', .3, now)
        self.assertIn(('drop', None), self.hold(engine, 'three', .3, now))


class Net:
    """In-memory network between Share objects: frames queue up and are delivered by pump()."""

    def __init__(self):
        self.frames = queue.Queue()
        self.nodes = {}


class FakeNode:
    def __init__(self, net, pid, caps=('camera',), side=''):
        self.net, self.id, self.name, self.caps, self.side = net, pid, pid.upper(), list(caps), side
        net.nodes[pid] = self
        self.share = None

    def send(self, peer, header, payload=b''):
        if peer not in self.net.nodes:
            return False
        self.net.frames.put((self.id, peer, header, payload))
        return True

    def online(self):
        return [{'id': p, 'name': n.name, 'side': n.side, 'caps': n.caps} for p, n in self.net.nodes.items() if p != self.id]

    def name_of(self, pid):
        return pid.upper()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.net, self.clock, self.posts, self.delivered = Net(), Clock(), [], []
        self.shares = {}

    def device(self, pid, **kw):
        node = FakeNode(self.net, pid, **kw)
        share = Share(node, self.dir / pid / 'inbox', lambda ev, pid=pid: self.posts.append((pid, ev)), self.deliver_for(pid), now=self.clock)
        node.share = self.shares[pid] = share
        return share

    def deliver_for(self, pid):
        def deliver(kind, info):
            self.delivered.append((pid, kind, info))
            return 'verified', {'detail': 'shown'}
        return deliver

    def pump(self, until=lambda: False, timeout=5):
        """Delivers frames (streams run on their own threads) until the condition holds or the network is quiet."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                sender, peer, header, payload = self.net.frames.get(timeout=.05)
            except queue.Empty:
                if until():
                    return True
                continue
            self.shares[peer].on_frame(sender, header, payload)
            if until():
                return True
        return until()

    def posted(self, pid, kind):
        return [ev for p, ev in self.posts if p == pid and ev['type'] == kind]

    def wait_resolved(self, share):
        end = time.monotonic() + 5
        while share.out and not share.out['resolved'] and time.monotonic() < end:
            time.sleep(.01)

    def test_page_opens_on_the_device_that_sees_the_drop(self):
        a, b = self.device('a'), self.device('b')
        a.grab(lambda: {'kind': 'link', 'url': 'https://example.com/docs?page=2#top', 'title': 'Docs', 'label': 'Docs'})
        self.wait_resolved(a)
        self.assertTrue(a.arm('right'))
        self.assertTrue(self.pump(lambda: b.armed()))
        self.assertTrue(b.claim(.9))
        self.assertTrue(self.pump(lambda: self.posted('a', 'delivered')))
        self.assertEqual(self.delivered, [('b', 'link', {'url': 'https://example.com/docs?page=2#top', 'title': 'Docs', 'peer': 'A'})])
        self.assertIsNone(a.out)

    def test_small_files_are_staged_before_the_drop_and_verified_after(self):
        a, b = self.device('a'), self.device('b')
        src = tree(self.dir / 'src')
        a.grab(lambda: {'kind': 'files', 'paths': src, 'label': 'photo.jpg and 1 more'})
        self.wait_resolved(a)
        a.arm()
        # Before any drop the whole package is already on B, hidden in staging.
        self.assertTrue(self.pump(lambda: b.inc and next(iter(b.inc.values()))['complete']))
        self.assertFalse((self.dir / 'b' / 'inbox' / 'Pictures').exists())
        b.claim(.8)
        self.assertTrue(self.pump(lambda: self.posted('a', 'delivered')))
        delivered = self.posted('a', 'delivered')[0]
        self.assertIn('SHA-256 match', delivered['detail'])
        got = self.dir / 'b' / 'inbox' / 'Pictures' / 'photo.jpg'
        self.assertEqual(digest(got), digest(src[0]))
        self.assertEqual(digest(self.dir / 'b' / 'inbox' / 'Folders' / 'album' / 'a.txt'), digest(src[1] / 'a.txt'))
        self.assertFalse(any((self.dir / 'b' / 'inbox' / '.staging').iterdir()))

    def test_best_claim_wins_and_the_others_forget_their_copy(self):
        a, b, c = self.device('a'), self.device('b'), self.device('c')
        src = [tree(self.dir / 'src')[0]]
        a.grab(lambda: {'kind': 'files', 'paths': src, 'label': 'photo.jpg'})
        self.wait_resolved(a)
        a.arm()
        self.assertTrue(self.pump(lambda: b.armed() and c.armed()))
        b.claim(.55)
        c.claim(.9)
        self.pump(lambda: len(a.out['claims']) == 2)
        self.clock.t += .2  # the claim window closes
        a.tick()
        self.assertTrue(self.pump(lambda: self.posted('a', 'delivered')))
        self.assertEqual(self.posted('a', 'delivered')[0]['peer'], 'C')
        self.assertTrue((self.dir / 'c' / 'inbox' / 'Pictures' / 'photo.jpg').exists())
        self.assertFalse((self.dir / 'b' / 'inbox' / 'Pictures').exists())
        self.assertEqual(self.posted('b', 'incoming_end')[0]['why'], 'taken')
        self.assertEqual(b.inc, {})

    def test_nothing_dropped_in_time_cancels_everywhere(self):
        a, b = self.device('a'), self.device('b')
        a.grab(lambda: {'kind': 'link', 'url': 'https://example.com', 'title': '', 'label': 'example'})
        self.wait_resolved(a)
        a.arm()
        self.pump(lambda: b.armed())
        self.clock.t += CARRY_TTL + 1
        a.tick()
        self.assertEqual(self.posted('a', 'carry_cancelled')[0]['reason'], 'nothing was dropped within 10 s')
        self.assertTrue(self.pump(lambda: not b.inc))
        self.assertEqual(self.delivered, [])

    def test_opening_the_hand_back_here_takes_it_back(self):
        a, b = self.device('a'), self.device('b')
        a.grab(lambda: {'kind': 'link', 'url': 'https://example.com', 'title': '', 'label': 'example'})
        self.wait_resolved(a)
        a.arm()
        self.pump(lambda: b.armed())
        a.dropped_here()
        self.assertTrue(self.pump(lambda: not b.inc))
        self.assertFalse(b.claim(1.0))
        self.assertEqual(self.delivered, [])

    def test_a_grab_opened_on_the_same_device_is_never_offered(self):
        a, b = self.device('a'), self.device('b')
        a.grab(lambda: {'kind': 'link', 'url': 'https://example.com', 'title': '', 'label': 'example'})
        self.wait_resolved(a)
        a.dropped_here()  # a local window move: the fingers opened on this camera
        self.pump(timeout=.3)
        self.assertEqual(b.inc, {})
        self.assertFalse(a.arm())

    def test_a_device_without_camera_takes_it_by_the_side_the_hand_left(self):
        a, b, c = self.device('a'), self.device('b', caps=(), side='left'), self.device('c', side='right')
        a.grab(lambda: {'kind': 'link', 'url': 'https://example.com', 'title': '', 'label': 'example'})
        self.wait_resolved(a)
        a.arm('left')
        self.assertTrue(self.pump(lambda: self.posted('a', 'delivered')))
        self.assertEqual(self.delivered[0][0], 'b')

    def test_only_web_pages_are_opened(self):
        a, b = self.device('a'), self.device('b')
        a.out = None
        b.on_frame('a', {'t': 'carry', 'x': 'x-1', 'kind': 'link', 'url': 'file:///C:/Windows/System32/calc.exe', 'label': 'x'}, b'')
        self.assertEqual(b.inc, {})
        self.assertEqual(self.net.frames.get_nowait()[2]['t'], 'xfail')

    def test_loopback_pages_point_at_this_device(self):
        self.assertEqual(reachable('http://localhost:3000/app?x=1', '192.168.1.20'), 'http://192.168.1.20:3000/app?x=1')
        self.assertEqual(reachable('http://127.0.0.1/', '192.168.1.20'), 'http://192.168.1.20/')
        self.assertEqual(reachable('https://example.com/', '192.168.1.20'), 'https://example.com/')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class NodeTest(unittest.TestCase):
    """Real sockets on this computer: pairing, encryption, reconnecting, and a package through the link."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.nodes = []
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(lambda: [n.stop() for n in self.nodes])

    def node(self, name, port=None, confirm=True, on_frame=None):
        received = queue.Queue()
        node = LinkNode(self.dir / name, name=name, port=port or free_port(), bind='127.0.0.1', discovery=False, caps=['camera'],
                        confirm=lambda peer, code, role: received.put(('code', code)) or confirm,
                        on_frame=on_frame or (lambda peer, h, p: received.put((peer, h, p))))
        node.received = received
        node.start()
        self.nodes.append(node)
        return node

    def wait(self, condition, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(.02)
        return False

    def test_pair_send_encrypted_and_reconnect_without_pairing_again(self):
        a, b = self.node('A'), self.node('B')
        self.wait(lambda: a.listening and b.listening)
        b.connect('127.0.0.1', a.port, allow_pair=True)
        self.assertTrue(self.wait(lambda: a.online() and b.online()))
        code_a, code_b = a.received.get(timeout=2), b.received.get(timeout=2)
        self.assertEqual(code_a, code_b)  # both screens showed the same code
        self.assertTrue(b.send(a.id, {'t': 'hello-app', 'n': 1}, b'\x00payload'))
        peer, h, payload = a.received.get(timeout=2)
        self.assertEqual((peer, h['t'], payload), (b.id, 'hello-app', b'\x00payload'))
        # Restart both: the stored pairing is used, no code is asked for.
        a_port, a_id = a.port, a.id
        a.stop()
        b.stop()
        a2, b2 = self.node('A', port=a_port), self.node('B')
        self.assertEqual(a2.id, a_id)
        self.assertTrue(self.wait(lambda: a2.online() and b2.online()))
        self.assertTrue(a2.received.empty() and b2.received.empty())

    def test_pairing_declined_on_one_device_pairs_neither(self):
        a, b = self.node('A', confirm=False), self.node('B')
        self.wait(lambda: a.listening)
        b.connect('127.0.0.1', a.port, allow_pair=True)
        self.wait(lambda: not b.dial_targets, timeout=5)
        time.sleep(.3)
        self.assertEqual((a.known, b.known), ({}, {}))
        self.assertEqual(a.online() + b.online(), [])

    def test_an_unpaired_device_cannot_connect_silently(self):
        a, b = self.node('A'), self.node('B')
        self.wait(lambda: a.listening)
        b.connect('127.0.0.1', a.port)  # background dial, no pairing allowed
        time.sleep(1)
        self.assertEqual(a.online() + b.online(), [])

    def test_folder_through_the_real_link(self):
        shares, posts = {}, []
        a = self.node('A', on_frame=lambda peer, h, p: shares['A'].on_frame(peer, h, p))
        b = self.node('B', on_frame=lambda peer, h, p: shares['B'].on_frame(peer, h, p))
        shares['A'] = Share(a, self.dir / 'inA', posts.append, lambda kind, info: ('verified', {}))
        shares['B'] = Share(b, self.dir / 'inB', posts.append, lambda kind, info: ('verified', {}))
        self.wait(lambda: a.listening)
        b.connect('127.0.0.1', a.port, allow_pair=True)
        self.assertTrue(self.wait(lambda: a.online() and b.online()))
        src = self.dir / 'big'
        src.mkdir()
        for i in range(4):
            (src / f'part{i}.bin').write_bytes(os.urandom(12 * 1024 * 1024))  # 48 MB: over the pre-send limit
        started = time.monotonic()
        shares['A'].grab(lambda: {'kind': 'files', 'paths': [src], 'label': 'big'})
        self.wait(lambda: shares['A'].out['resolved'])
        shares['A'].arm()
        self.assertTrue(self.wait(lambda: shares['B'].armed()))
        shares['B'].claim(.9)
        self.assertTrue(self.wait(lambda: any(e['type'] == 'delivered' for e in posts), timeout=60), posts[-3:])
        seconds = time.monotonic() - started
        for i in range(4):
            self.assertEqual(digest(self.dir / 'inB' / 'Folders' / 'big' / f'part{i}.bin'), digest(src / f'part{i}.bin'))
        print(f'\n48 MB folder through the encrypted link: {seconds:.2f} s ({48 / seconds:.0f} MB/s on loopback)')


if __name__ == '__main__':
    unittest.main()
