"""Window executor (ARCHITECTURE.md W7, adapted; Win32 or X11): act by window handle, never by focus,
then read the real state back. "verified" is only returned when the read-back shows the result."""
import time

from ..contracts import ActionResult, failure
from ..targets import hwnd_of
from . import system

EXPECTED = {
    'window.maximize': lambda s: s['maximized'] and not s['hidden'],
    'window.minimize': lambda s: s['hidden'],
    'window.restore': lambda s: not s['hidden'] and not s['maximized'],
    'window.activate': lambda s: s['active'] and not s['hidden'],
}


class WindowBackend:
    def __init__(self, registry, settle=1.0):
        self.registry = registry
        self.settle = settle
        self.minimized = []  # stack for "restore that" (ARCHITECTURE.md §4.5)

    def act(self, intent):
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)
        if intent.action not in EXPECTED:
            return failure(intent.intent_id, 'INTERNAL', f'{intent.action} is not a window action')
        try:
            hwnd = hwnd_of(intent.target)
        except ValueError as error:
            return failure(intent.intent_id, 'TARGET_GONE', str(error), 'Point at a window or name it')
        if not system.is_window(hwnd) or self.registry.get(intent.target) is None:
            return failure(intent.intent_id, 'TARGET_GONE', 'That window is no longer available', 'Point at another window')
        before = system.state(hwnd)
        check = EXPECTED[intent.action]
        if check(before):
            return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'before': before, 'after': before, 'note': 'already in that state'}, elapsed_ms=elapsed())
        if intent.action == 'window.maximize':
            system.maximize(hwnd)
        elif intent.action == 'window.minimize':
            system.minimize(hwnd)
        elif intent.action == 'window.restore':
            system.restore(hwnd)
            if system.is_zoomed(hwnd):  # restored from minimized back to maximized: one more step to normal
                system.restore(hwnd)
        else:
            system.bring_to_front(hwnd)
        deadline = time.monotonic() + self.settle
        after = system.state(hwnd)
        while not check(after) and time.monotonic() < deadline:
            time.sleep(.05)
            if not system.is_window(hwnd):
                return failure(intent.intent_id, 'TARGET_GONE', 'The window closed during the action', elapsed_ms=elapsed())
            after = system.state(hwnd)
        evidence = {'source': system.SOURCE, 'before': before, 'after': after}
        if not check(after):
            return failure(intent.intent_id, 'NO_EFFECT', 'The window did not change state', 'The app may refuse this action', evidence, elapsed())
        if intent.action == 'window.minimize':
            self.minimized = [t for t in self.minimized if t != intent.target][-9:] + [intent.target]
        elif intent.action in ('window.restore', 'window.maximize', 'window.activate'):
            self.minimized = [t for t in self.minimized if t != intent.target]
        return ActionResult(intent.intent_id, 'verified', evidence, elapsed_ms=elapsed())

    def last_minimized(self):
        while self.minimized:
            target = self.minimized.pop()
            if self.registry.get(target) is not None:
                return target
        return None
