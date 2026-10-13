"""Direct pointer control for the classic hand signs (moved from Code Cameo desktop/windows.py).

Moves the real mouse pointer, clicks and scrolls (SendInput on Windows, XTest on Linux). Guards: never
while a physical mouse button or modifier is held, never on a secure/locked desktop, and physical mouse
movement pauses gesture movement for 1.5 s.
"""
import time

from ..gestures.filters import OneEuro
from . import system
from .system import cursor, screen_size  # noqa: F401  (re-exported for the runtime and tests)

MODIFIERS = ('shift', 'ctrl', 'alt', 'super')


class Pointer:
    def __init__(self, margin=.12, smoothing=.5):
        self.margin = margin
        self.filters = (OneEuro(), OneEuro())
        self.set_smoothing(smoothing)
        self.last = None
        self.pause_until = 0

    def set_smoothing(self, level):
        for axis in self.filters:
            axis.min_cutoff = 4.0 - 3.0 * max(0, min(1, level))
            axis.beta = .02

    dragging = False  # while a gesture drag holds the left button, that button is ours, not the user's

    def guard(self):
        if not system.interactive_desktop():
            raise RuntimeError('Secure desktop or locked session; input is disabled')
        buttons = ('right',) if self.dragging else ('left', 'right')
        if system.held(buttons + MODIFIERS):
            raise RuntimeError('Physical mouse button or modifier is active')

    def send(self, events):
        self.guard()
        system.send(events)

    def move(self, x, y):
        """Camera coordinates (0..1, mirrored) -> pointer. False while the physical mouse has priority."""
        self.guard()
        now = time.monotonic()
        current = cursor()
        if self.last and max(abs(current[i] - self.last[i]) for i in (0, 1)) > 15:
            self.pause_until = now + 1.5
        self.last = current
        if now < self.pause_until:
            for axis in self.filters:
                axis.reset()
            return False
        width, height = screen_size()
        span = 1 - 2 * self.margin
        desired = (max(0, min(1, (x - self.margin) / span)) * (width - 1), max(0, min(1, (y - self.margin) / span)) * (height - 1))
        self.send([('move',) + tuple(int(round(self.filters[i](desired[i], now))) for i in (0, 1))])
        self.last = cursor()
        return True

    def click(self, point=None, button='left', double=False):
        events = [('move',) + tuple(point)] if point else []
        for _ in range(2 if double else 1):
            events += [('down', button), ('up', button)]
        self.send(events)
        self.last = cursor()

    def wheel(self, direction, zoom=False, amount=120):
        events = [('wheel', amount * direction)]
        if zoom:
            events = [('key', 'ctrl', True)] + events + [('key', 'ctrl', False)]
        self.send(events)

    def press(self, point):
        self.send([('move',) + tuple(point), ('down', 'left')])
        self.dragging = True

    def drag_to(self, point):
        self.send([('move',) + tuple(point)])

    def release(self, point=None):
        try:
            self.send(([('move',) + tuple(point)] if point else []) + [('up', 'left')])
        finally:
            self.dragging = False
        self.last = cursor()

    def icon_at_pointer(self):
        """A desktop shortcut or taskbar button under the pointer (these open with their own click rules)."""
        return system.shell_at(*cursor())
