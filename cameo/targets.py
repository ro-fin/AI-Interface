"""Target registry: the real windows Cameo may act on (ARCHITECTURE.md §4.2, §4.8)."""
import os

from .contracts import Target
from .control import system

BROWSERS = {'msedge', 'brave', 'chrome', 'chromium', 'firefox'}  # Windows image names / Linux executable names


def target_id(hwnd):
    return f'win:0x{int(hwnd):08x}'


def hwnd_of(target):
    if not isinstance(target, str) or not target.startswith('win:0x'):
        raise ValueError(f'not a window target: {target!r}')
    return int(target[6:], 16)


def eligible(hwnd, own_pid=None):
    """Normal, visible application windows only: never Cameo itself, the desktop, the taskbar,
    tool windows, owned dialogs or hidden (cloaked / other-workspace) windows."""
    return system.app_window(hwnd) and system.pid(hwnd) != (own_pid or os.getpid())


def describe(hwnd):
    app = system.app_name(system.pid(hwnd))
    return Target(id=target_id(hwnd), kind='browser_window' if app in BROWSERS else 'window', app=app,
                  title=system.title(hwnd)[:200], geometry=system.geometry(hwnd), state=system.state(hwnd))


class TargetRegistry:
    def __init__(self, own_pid=None):
        self.own_pid = own_pid or os.getpid()

    def list(self):
        return [describe(hwnd) for hwnd in system.top_level_windows() if eligible(hwnd, self.own_pid)]

    def get(self, target):
        hwnd = hwnd_of(target)
        return describe(hwnd) if eligible(hwnd, self.own_pid) or (system.is_window(hwnd) and system.is_iconic(hwnd) and system.pid(hwnd) != self.own_pid) else None

    def active(self):
        hwnd = system.foreground()
        return describe(hwnd) if hwnd and eligible(hwnd, self.own_pid) else None

    def at(self, x, y):
        """The topmost eligible application window containing a screen point (for gesture target lock).
        EnumWindows returns top-level windows front to back; Cameo's own windows are skipped."""
        for hwnd in system.top_level_windows():
            if system.is_iconic(hwnd) or not eligible(hwnd, self.own_pid):
                continue
            g = system.geometry(hwnd)
            if g['x'] <= x < g['x'] + g['w'] and g['y'] <= y < g['y'] + g['h']:
                return describe(hwnd)
        return None

    def find(self, text):
        """Explicit selection by app or title words, e.g. 'notepad' or 'edge'. Ambiguity is reported."""
        needle = text.casefold()
        return [t for t in self.list() if needle in t.app.casefold() or needle in t.title.casefold()]
