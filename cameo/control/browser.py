"""Browser tabs through the AETHER Bridge (ARCHITECTURE.md §4.6; Edge, Chrome or Brave on Windows and Linux).
A tab action is "verified" only when the browser's own tab event names the expected tab."""
import itertools
from multiprocessing import AuthenticationError
import threading
import time

from ..contracts import ActionResult, failure
from ..ipc import listen, new_token
from . import system


class BridgeServer:
    """Accepts the bridge host on the authenticated pipe; routes replies and tab events."""

    def __init__(self, token=None):
        self.listener = listen(token or new_token())
        self.conn = None
        self.snapshot = None
        self.events = []
        self.replies = {}
        self.changed = threading.Condition()
        self.ids = itertools.count(1)
        threading.Thread(target=self.accept, daemon=True).start()

    @property
    def connected(self):
        return self.conn is not None

    def accept(self):
        while not getattr(self, 'closed', False):
            try:
                conn = self.listener.accept()
                hello = conn.recv()
            except (OSError, EOFError, AuthenticationError):
                continue  # a wrong key or a probe that hung up (ipc.listen checks the socket this way)
            if not isinstance(hello, dict) or hello.get('role') != 'browser':
                conn.close()
                continue
            self.conn = conn
            threading.Thread(target=self.receive, args=(conn,), daemon=True).start()

    def receive(self, conn):
        try:
            while True:
                message = conn.recv()
                if not isinstance(message, dict):
                    continue
                with self.changed:
                    if message.get('event') == 'hello':
                        self.snapshot = message
                    elif message.get('event'):
                        self.events.append((time.monotonic(), message))
                        self.events = self.events[-200:]
                    elif 'id' in message:
                        # A browser message is only ever a fact or a reply; it can never request an action.
                        self.replies[message['id']] = message
                    self.changed.notify_all()
        except (OSError, EOFError):
            if self.conn is conn:
                self.conn = None

    def wait_connected(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.connected and time.monotonic() < deadline:
            time.sleep(.05)
        return self.connected

    def request(self, op, args=None, timeout=3):
        if self.conn is None:
            raise ConnectionError('bridge offline')
        request_id = f'rq-{next(self.ids)}'
        self.conn.send({'id': request_id, 'op': op, 'args': args or {}})
        with self.changed:
            if not self.changed.wait_for(lambda: request_id in self.replies, timeout):
                raise TimeoutError(f'bridge did not answer {op}')
            reply = self.replies.pop(request_id)
        if not reply.get('ok'):
            raise RuntimeError(reply.get('error', 'bridge error'))
        return reply.get('result')

    def wait_event(self, since, predicate, timeout):
        with self.changed:
            found = self.changed.wait_for(lambda: next((e for t, e in self.events if t >= since and predicate(e)), None), timeout)
        return found

    def close(self):
        self.closed = True
        self.listener.close()


class BrowserController:
    def __init__(self, bridge, registry=None):
        self.bridge = bridge
        self.registry = registry

    def keyboard_close(self, window, tab, since):
        """Guarded fallback (ARCHITECTURE.md §4.6 b) for tabs the browser will not let extensions close,
        such as Edge's New Tab page: Ctrl+W only after confirming that exact browser window is in front
        and the tab is still active. Evidence is still the browser's own tab.removed event."""
        if self.registry is None:
            return None, 'no window registry'
        candidates = [t for t in self.registry.list() if t.kind == 'browser_window' and t.title.startswith(tab['title'][:40])]
        if len(candidates) != 1:
            return None, f'{len(candidates)} browser windows match the tab title'
        hwnd = int(candidates[0].id[6:], 16)
        system.bring_to_front(hwnd)
        time.sleep(.15)
        current = self.focused_window()
        still_active = current and current['id'] == window['id'] and any(t['id'] == tab['id'] and t['active'] for t in current['tabs'])
        if system.foreground() != hwnd or not still_active:
            return None, 'the browser window or tab changed before the key could be sent'
        system.press_ctrl("w")
        if system.foreground() != hwnd:
            return None, 'focus moved while sending the key'
        return self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.removed' and e.get('tabId') == tab['id'], 2), 'keyboard'

    def focused_window(self):
        state = self.bridge.request('snapshot')
        windows = {w['id']: w for w in state['windows']}
        window = windows.get(state['focusedWindowId']) or (state['windows'][0] if state['windows'] else None)
        return window

    def act(self, intent):
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)
        if not self.bridge.connected:
            return failure(intent.intent_id, 'BRIDGE_OFFLINE', 'The browser bridge is not connected', 'Open Edge with the Cameo Bridge extension')
        try:
            window = self.focused_window()
            if window is None:
                return failure(intent.intent_id, 'TARGET_GONE', 'No browser window is open', 'Say "open the browser"')
            tabs = sorted(window['tabs'], key=lambda t: t['index'])
            active = next((t for t in tabs if t['active']), None)
            before = {'windowId': window['id'], 'tabs': len(tabs), 'active': active and {'tabId': active['id'], 'index': active['index']}}
            since = time.monotonic()
            if intent.action == 'tab.new':
                tab_id = self.bridge.request('create', {'windowId': window['id']})['tabId']
                created = self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.created' and e.get('tabId') == tab_id, 2)
                activated = self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.activated' and e.get('tabId') == tab_id, 2)
                ok, after = bool(created and activated), {'created': created, 'activated': activated}
            elif intent.action in ('tab.next', 'tab.prev'):
                if len(tabs) < 2 or active is None:
                    return failure(intent.intent_id, 'NO_EFFECT', 'There is no other tab', evidence={'source': 'bridge', 'before': before}, elapsed_ms=elapsed())
                step = 1 if intent.action == 'tab.next' else -1
                target = tabs[(active['index'] + step) % len(tabs)]
                self.bridge.request('activate', {'tabId': target['id']})
                event = self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.activated' and e.get('tabId') == target['id'], 2)
                ok, after = bool(event and event.get('index') == target['index']), {'activated': event}
            elif intent.action == 'tab.close':
                if active is None:
                    return failure(intent.intent_id, 'TARGET_GONE', 'No active tab')
                method = 'tabs.remove'
                try:
                    self.bridge.request('remove', {'tabId': active['id']})
                    event = self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.removed' and e.get('tabId') == active['id'], 2)
                except RuntimeError as error:
                    if 'Cannot remove' not in str(error):
                        raise
                    event, method = self.keyboard_close(window, active, since)
                    if event is None and method != 'keyboard':
                        return failure(intent.intent_id, 'NO_EFFECT', f'The browser refused to close this tab ({method})', 'Close it with Ctrl+W', {'source': 'bridge', 'before': before}, elapsed())
                before['method'] = method
                if not event:
                    return ActionResult(intent.intent_id, 'pending_user', {'source': 'bridge', 'before': before, 'note': 'close requested; the page is asking to confirm'}, elapsed_ms=elapsed())
                ok, after = True, {'removed': event}
            else:
                return failure(intent.intent_id, 'INTERNAL', f'{intent.action} is not a tab action')
        except (ConnectionError, TimeoutError, RuntimeError) as error:
            code = 'BRIDGE_OFFLINE' if isinstance(error, ConnectionError) else 'TIMEOUT' if isinstance(error, TimeoutError) else 'NO_EFFECT'
            return failure(intent.intent_id, code, str(error), elapsed_ms=elapsed())
        evidence = {'source': 'bridge', 'before': before, 'after': after}
        if not ok:
            return failure(intent.intent_id, 'NO_EFFECT', 'The browser did not confirm the tab change', evidence=evidence, elapsed_ms=elapsed())
        return ActionResult(intent.intent_id, 'verified', evidence, elapsed_ms=elapsed())
