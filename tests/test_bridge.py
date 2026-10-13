"""Bridge path without a browser: real native host process + real pipe + BrowserController,
with a simulated extension speaking the native-messaging protocol on the host's stdin/stdout."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.environ['CAMEO_IPC_NAME'] = f'cameo-test-{os.getpid()}'
os.environ['CAMEO_STATE_DIR'] = tempfile.mkdtemp(prefix='cameo-bridge-')
sys.path.insert(0, str(ROOT))
from cameo.contracts import Intent
from cameo.control.browser import BridgeServer, BrowserController


class FakeExtension:
    """Answers like background.js. Tab 13 ('guarded') ignores removal, like a beforeunload prompt."""

    def __init__(self, process):
        self.process = process
        self.tabs = [{'id': 10, 'index': 0, 'active': True, 'title': 'One'}, {'id': 11, 'index': 1, 'active': False, 'title': 'Two'}]
        self.forwarded = []
        threading.Thread(target=self.serve, daemon=True).start()

    def send(self, message):
        data = json.dumps(message).encode()
        self.process.stdin.write(struct.pack('<I', len(data)) + data)
        self.process.stdin.flush()

    def snapshot(self):
        return {'focusedWindowId': 1, 'windows': [{'id': 1, 'focused': True, 'tabs': [dict(t) for t in self.tabs]}]}

    def activate(self, tab_id):
        for tab in self.tabs:
            tab['active'] = tab['id'] == tab_id
        tab = next(t for t in self.tabs if t['id'] == tab_id)
        self.send({'event': 'tab.activated', 'tabId': tab_id, 'windowId': 1, 'index': tab['index']})

    def serve(self):
        while True:
            raw = self.process.stdout.read(4)
            if len(raw) < 4:
                return
            request = json.loads(self.process.stdout.read(struct.unpack('<I', raw)[0]))
            self.forwarded.append(request['op'])
            op, args = request['op'], request.get('args', {})
            if op == 'snapshot':
                result = self.snapshot()
            elif op == 'create':
                tab = {'id': 12 + len(self.tabs), 'index': len(self.tabs), 'active': False, 'title': 'New'}
                self.tabs.append(tab)
                result = {'tabId': tab['id']}
            elif op == 'activate':
                result = {'tabId': args['tabId']}
            else:
                result = {'tabId': args['tabId']}
            self.send({'id': request['id'], 'ok': True, 'result': result})
            if op == 'create':
                self.send({'event': 'tab.created', 'tabId': result['tabId'], 'windowId': 1, 'index': len(self.tabs) - 1})
                self.activate(result['tabId'])
            elif op == 'activate':
                self.activate(args['tabId'])
            elif op == 'remove' and args['tabId'] != 13:
                self.tabs = [t for t in self.tabs if t['id'] != args['tabId']]
                for index, tab in enumerate(self.tabs):
                    tab['index'] = index
                if self.tabs:
                    self.tabs[0]['active'] = True
                self.send({'event': 'tab.removed', 'tabId': args['tabId'], 'windowId': 1})


class Bridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = BridgeServer()
        cls.host = subprocess.Popen([sys.executable, '-u', str(ROOT / 'bridge' / 'host' / 'cameo_bridge_host.py')],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=os.environ.copy())
        cls.browser = FakeExtension(cls.host)
        cls.browser.send({'event': 'hello', **cls.browser.snapshot()})
        assert cls.server.wait_connected(10), 'host did not connect to the core pipe'
        cls.controller = BrowserController(cls.server)

    @classmethod
    def tearDownClass(cls):
        cls.host.kill()
        cls.server.close()

    def act(self, action):
        return self.controller.act(Intent(action, None, 'default'))

    def test_1_new_tab_verified_by_created_and_activated_events(self):
        result = self.act('tab.new')
        self.assertEqual(result.status, 'verified', result.error)
        self.assertEqual(result.evidence['after']['created']['event'], 'tab.created')

    def test_2_next_and_previous_wrap_and_verify_index(self):
        active = lambda: next(t['index'] for t in self.browser.tabs if t['active'])
        start = active()
        self.assertEqual(self.act('tab.next').status, 'verified')
        self.assertEqual(active(), (start + 1) % len(self.browser.tabs))
        self.assertEqual(self.act('tab.prev').status, 'verified')
        self.assertEqual(active(), start)

    def test_3_close_verified_or_pending_when_page_asks(self):
        self.browser.tabs.append({'id': 13, 'index': len(self.browser.tabs), 'active': False, 'title': 'Unsaved form'})
        self.browser.activate(13)
        result = self.act('tab.close')
        self.assertEqual(result.status, 'pending_user')
        self.browser.activate(10)
        self.assertEqual(self.act('tab.close').status, 'verified')

    def test_4_single_tab_next_is_no_effect(self):
        self.browser.tabs = [{'id': 20, 'index': 0, 'active': True, 'title': 'Only'}]
        result = self.act('tab.next')
        self.assertEqual(result.error['code'], 'NO_EFFECT')

    def test_5_host_forwards_only_tab_operations(self):
        before = list(self.browser.forwarded)
        self.server.conn.send({'id': 'x', 'op': 'executeScript', 'args': {}})
        self.server.request('snapshot')
        self.assertNotIn('executeScript', self.browser.forwarded[len(before):])

    def test_6_browser_messages_cannot_request_actions(self):
        self.browser.send({'op': 'create', 'args': {}, 'event': None})
        self.assertIsNotNone(self.server.request('snapshot'))


if __name__ == '__main__':
    unittest.main()
