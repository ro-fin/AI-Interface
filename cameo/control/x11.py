"""Linux desktop backend (X11): EWMH window management, XTest input and XShape HUD windows, through python-xlib.

Same functions as win32.py. Needs an X11 session ("Ubuntu on Xorg" on the login screen's gear menu):
Wayland does not let one app read or move other apps' windows or send them input.
"""
import os
import select
import shutil
import subprocess
import sys
import threading
import time

from Xlib import X, XK, Xatom, display, error as xerror
from Xlib.ext import shape, xtest
from Xlib.protocol import event as xevent

SOURCE = 'x11'
SHAPE_INPUT = getattr(shape.SK, 'Input', 2)
_local = threading.local()


def dpy():
    """One connection per thread: python-xlib matches replies per connection."""
    d = getattr(_local, 'display', None)
    if d is None:
        d = _local.display = display.Display()
        _local.atoms = {}
    return d


def atom(name):
    d = dpy()
    if name not in _local.atoms:
        _local.atoms[name] = d.intern_atom(name)
    return _local.atoms[name]


def root():
    return dpy().screen().root


def win(wid):
    return dpy().create_resource_object('window', int(wid))


def prop(wid, name, kind=X.AnyPropertyType):
    try:
        value = win(wid).get_full_property(atom(name), kind)
    except (xerror.XError, ValueError):
        return None
    return value.value if value is not None else None


def text(value):
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else (value or '')


def wait(check, timeout=.6):
    """X requests are asynchronous: poll the read-back until it shows the change (or time runs out)."""
    end = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() >= end:
            return result
        time.sleep(.02)


def client_message(wid, name, data):
    message = xevent.ClientMessage(window=win(wid), client_type=atom(name), data=(32, (list(data) + [0] * 5)[:5]))
    root().send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    dpy().flush()


# -- windows ----------------------------------------------------------------------------------
def top_level_windows():
    """Front to back (EWMH keeps the stacking list bottom to top)."""
    ids = prop(root().id, '_NET_CLIENT_LIST_STACKING') or prop(root().id, '_NET_CLIENT_LIST') or []
    return [int(w) for w in reversed(ids)]


def is_window(wid):
    if not wid:
        return False
    try:
        win(wid).get_attributes()
        return True
    except xerror.XError:
        return False


def title(wid):
    name = prop(wid, '_NET_WM_NAME', atom('UTF8_STRING'))
    if name:
        return text(name)
    try:
        return text(win(wid).get_wm_name())
    except xerror.XError:
        return ''


def class_name(wid):
    try:
        names = win(wid).get_wm_class()
    except xerror.XError:
        return ''
    return names[1] if names else ''


def pid(wid):
    """_NET_WM_PID, or, for toolkits that do not set it (Tk), the X server's own record of the client
    (X-Resource extension; local clients only)."""
    value = prop(wid, '_NET_WM_PID', Xatom.CARDINAL)
    if value:
        return int(value[0])
    d = dpy()
    if not d.has_extension('X-Resource'):
        return 0
    from Xlib.ext import res
    try:
        reply = d.res_query_client_ids([{'client': int(wid), 'mask': res.LocalClientPIDMask}])
    except xerror.XError:
        return 0
    return next((int(i.value[0]) for i in reply.ids if i.spec.mask & res.LocalClientPIDMask and i.value), 0)


def app_name(process_id):
    """Executable name, like Windows' image name: 'msedge', 'chrome', 'firefox', 'code'."""
    if not process_id:
        return ''
    try:
        name = os.path.basename(os.readlink(f'/proc/{process_id}/exe')).removesuffix(' (deleted)')
    except OSError:
        try:
            with open(f'/proc/{process_id}/comm', encoding='utf-8') as comm:
                name = comm.read().strip()
        except OSError:
            return ''
    return name.lower().removesuffix('-bin')


def types(wid):
    return set(prop(wid, '_NET_WM_WINDOW_TYPE', Xatom.ATOM) or [])


def states(wid):
    return set(prop(wid, '_NET_WM_STATE', Xatom.ATOM) or [])


def extents(wid):
    value = prop(wid, '_NET_FRAME_EXTENTS', Xatom.CARDINAL)
    return tuple(int(v) for v in value[:4]) if value and len(value) >= 4 else (0, 0, 0, 0)  # left, right, top, bottom


def geometry(wid):
    """Outer rectangle including the title bar and borders, like GetWindowRect."""
    try:
        w = win(wid)
        size = w.get_geometry()
        origin = root().translate_coords(w, 0, 0)
    except xerror.XError:
        return {'x': 0, 'y': 0, 'w': 0, 'h': 0}
    left, right, top, bottom = extents(wid)
    return {'x': origin.x - left, 'y': origin.y - top, 'w': size.width + left + right, 'h': size.height + top + bottom}


def is_iconic(wid):
    wm_state = prop(wid, 'WM_STATE', atom('WM_STATE'))
    return bool(wm_state and wm_state[0] == 3) or atom('_NET_WM_STATE_HIDDEN') in states(wid)  # IconicState


def is_zoomed(wid):
    now = states(wid)
    return atom('_NET_WM_STATE_MAXIMIZED_VERT') in now and atom('_NET_WM_STATE_MAXIMIZED_HORZ') in now


def foreground():
    value = prop(root().id, '_NET_ACTIVE_WINDOW', Xatom.WINDOW)
    return int(value[0]) if value and value[0] else 0


def state(wid):
    return {'hidden': is_iconic(wid), 'maximized': is_zoomed(wid), 'active': foreground() == int(wid)}


def on_this_desktop(wid):
    desk, current = prop(wid, '_NET_WM_DESKTOP', Xatom.CARDINAL), prop(root().id, '_NET_CURRENT_DESKTOP', Xatom.CARDINAL)
    return not desk or not current or desk[0] in (current[0], 0xFFFFFFFF)


def app_window(wid):
    """A normal application window on this workspace: not the desktop, a dock/panel, a dialog owned by
    another window, a window kept off the taskbar, or a window on another workspace."""
    if not is_window(wid):
        return False
    kinds = types(wid)
    if kinds and atom('_NET_WM_WINDOW_TYPE_NORMAL') not in kinds:
        return False
    if prop(wid, 'WM_TRANSIENT_FOR', Xatom.WINDOW) or atom('_NET_WM_STATE_SKIP_TASKBAR') in states(wid):
        return False
    return on_this_desktop(wid) and bool(title(wid).strip())


def set_maximized(wid, on):
    client_message(wid, '_NET_WM_STATE', [1 if on else 0, atom('_NET_WM_STATE_MAXIMIZED_VERT'), atom('_NET_WM_STATE_MAXIMIZED_HORZ'), 2])


def activate(wid):
    client_message(wid, '_NET_ACTIVE_WINDOW', [2, X.CurrentTime, 0])  # source 2: a pager/tool acting for the user


def maximize(wid):
    if is_iconic(wid):
        activate(wid)
    set_maximized(wid, True)
    wait(lambda: is_zoomed(wid) and not is_iconic(wid))


def minimize(wid):
    client_message(wid, 'WM_CHANGE_STATE', [3])  # ICCCM IconicState
    wait(lambda: is_iconic(wid))


def restore(wid):
    """One restore step, like SW_RESTORE: minimized -> its previous state, maximized -> normal."""
    if is_iconic(wid):
        activate(wid)
        wait(lambda: not is_iconic(wid))
    elif is_zoomed(wid):
        set_maximized(wid, False)
        wait(lambda: not is_zoomed(wid))


def move_resize(wid, rect):
    """Outer rectangle, like SetWindowPos. Called every animation frame, so it never waits."""
    left, right, top, bottom = extents(wid)
    flags = 1 | (0xF << 8) | (2 << 12)  # NorthWest gravity (x, y = outer corner), set x/y/w/h, from a tool
    client_message(wid, '_NET_MOVERESIZE_WINDOW', [flags, rect['x'], rect['y'], max(1, rect['w'] - left - right), max(1, rect['h'] - top - bottom)])


def bring_to_front(wid):
    activate(wid)
    return bool(wait(lambda: foreground() == int(wid)))


def close(wid):
    client_message(wid, '_NET_CLOSE_WINDOW', [X.CurrentTime, 2])


def shell_at(x, y):
    """'desktop' over desktop icons, 'taskbar' over a dock/panel window, else None."""
    for wid in top_level_windows():
        if is_iconic(wid) or not on_this_desktop(wid):
            continue
        g = geometry(wid)
        if g['x'] <= x < g['x'] + g['w'] and g['y'] <= y < g['y'] + g['h']:
            kinds = types(wid)
            if atom('_NET_WM_WINDOW_TYPE_DESKTOP') in kinds:
                return 'desktop'
            return 'taskbar' if atom('_NET_WM_WINDOW_TYPE_DOCK') in kinds else None
    return None


# -- screen -----------------------------------------------------------------------------------
def cursor():
    pointer = root().query_pointer()
    return pointer.root_x, pointer.root_y


def screen_size():
    screen = dpy().screen()
    return screen.width_in_pixels, screen.height_in_pixels


def work_area():
    """Screen area without panels and docks: left, top, right, bottom."""
    value = prop(root().id, '_NET_WORKAREA', Xatom.CARDINAL)
    if value and len(value) >= 4:
        x, y, w, h = (int(v) for v in value[:4])
        return x, y, x + w, y + h
    return (0, 0) + screen_size()


def dpi_aware():
    """X11 already reports real pixels."""


# -- input ------------------------------------------------------------------------------------
KEYSYMS = {'ctrl': 'Control_L', 'alt': 'Alt_L', 'shift': 'Shift_L', 'super': 'Super_L', 'enter': 'Return',
           'escape': 'Escape', 'end': 'End', 'delete': 'Delete', 'volume_down': 'XF86AudioLowerVolume',
           'volume_up': 'XF86AudioRaiseVolume'}
MASKS = {'left': X.Button1Mask, 'right': X.Button3Mask, 'shift': X.ShiftMask, 'ctrl': X.ControlMask, 'alt': X.Mod1Mask, 'super': X.Mod4Mask}
BUTTONS = {'left': 1, 'right': 3}
_locked = {'value': False, 'thread': None}
_wheel = {'rest': 0}


def keycode(name, d=None):
    d = d or dpy()
    return d.keysym_to_keycode(XK.string_to_keysym(KEYSYMS.get(name, name)))


def screen_locked():
    """GNOME / freedesktop screensaver state over D-Bus (the lock screen must never get synthetic input)."""
    if not shutil.which('gdbus'):
        return False
    for name, path in (('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver'), ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver')):
        try:
            out = subprocess.run(['gdbus', 'call', '--session', '--dest', name, '--object-path', path, '--method', f'{name}.GetActive'],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out.strip():
            return 'true' in out
    return False


def interactive_desktop():
    """False while the screen is locked. Checked by a background thread once a second, so the pointer never waits."""
    if _locked['thread'] is None:
        def watch():
            while True:
                _locked['value'] = screen_locked()
                time.sleep(1)
        _locked['value'] = screen_locked()
        _locked['thread'] = threading.Thread(target=watch, daemon=True)
        _locked['thread'].start()
    return not _locked['value']


def held(names):
    """Which of these mouse buttons / keys are down right now."""
    d = dpy()
    mask = root().query_pointer().mask
    keys = d.query_keymap()
    found = set()
    for name in names:
        if name in MASKS:
            if mask & MASKS[name]:
                found.add(name)
        else:
            code = keycode(name, d)
            if code and keys[code // 8] >> (code % 8) & 1:
                found.add(name)
    return found


def send(events):
    """Plays ('move', x, y) / ('down'|'up', button) / ('wheel', amount) / ('key', name, down) through XTest.
    Wheel amounts use Windows units (120 = one notch); smaller steps add up to whole notches."""
    d = dpy()
    if not d.has_extension('XTEST'):
        raise RuntimeError('This X server has no XTEST extension; input is unavailable')
    for event in events:
        kind = event[0]
        if kind == 'move':
            xtest.fake_input(d, X.MotionNotify, x=int(event[1]), y=int(event[2]))
        elif kind in ('down', 'up'):
            xtest.fake_input(d, X.ButtonPress if kind == 'down' else X.ButtonRelease, BUTTONS[event[1]])
        elif kind == 'wheel':
            _wheel['rest'] += event[1]
            notches = int(_wheel['rest'] / 120)
            _wheel['rest'] -= notches * 120
            for _ in range(abs(notches)):
                button = 4 if notches > 0 else 5  # 4 = up / away from the user, like a positive Windows delta
                xtest.fake_input(d, X.ButtonPress, button)
                xtest.fake_input(d, X.ButtonRelease, button)
        elif kind == 'key':
            xtest.fake_input(d, X.KeyPress if event[2] else X.KeyRelease, keycode(event[1], d))
        elif kind == 'char':
            raise RuntimeError('Typing text is not supported on Linux')
    d.sync()


def press_ctrl(letter):
    """Ctrl+<letter> in one burst; Ctrl is always released."""
    try:
        send([('key', 'ctrl', True), ('key', letter, True), ('key', letter, False)])
    finally:
        send([('key', 'ctrl', False)])


def hotkeys(bindings, stop):
    """bindings {'p': callback, ...} for Ctrl+Alt+<letter>, called on this thread until `stop` is set.
    Grabbed with and without Caps Lock / Num Lock, which X counts as modifiers."""
    d = display.Display()
    r = d.screen().root
    catch = xerror.CatchError(xerror.BadAccess)
    codes = {}
    for letter, callback in bindings.items():
        code = keycode(letter, d)
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            r.grab_key(code, X.ControlMask | X.Mod1Mask | extra, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
        codes[code] = callback
    d.sync()
    ok = catch.get_error() is None
    try:
        while not stop.is_set():
            if not select.select([d], [], [], .2)[0] and not d.pending_events():
                continue
            while d.pending_events():
                event = d.next_event()
                if event.type == X.KeyPress and event.detail in codes:
                    codes[event.detail]()
    finally:
        for code in codes:
            r.ungrab_key(code, X.AnyModifier)
        d.close()
    return ok


def lock_screen():
    """What Super+L does: asks the session to lock (logind first, then the desktop's screensaver).
    True when one of them accepted the request."""
    for command in (['loginctl', 'lock-session'], ['xdg-screensaver', 'lock'], ['gnome-screensaver-command', '--lock'],
                    ['dm-tool', 'lock']):
        if shutil.which(command[0]):
            try:
                if subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def sleep_computer():
    """What Power > Suspend does: asks logind to suspend. True when it accepted the request."""
    for command in (['systemctl', 'suspend'], ['loginctl', 'suspend']):
        if shutil.which(command[0]):
            try:
                if subprocess.run(command, capture_output=True, timeout=10).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


# -- levels: master volume (PipeWire wpctl, PulseAudio pactl), read back after every set ------------------
def _run(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=3)
        return done.stdout if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _volume_state():
    """(level 0.0-1.0, muted) of the default sink, or None. wpctl (PipeWire, Ubuntu 22.10+) first, then pactl."""
    if shutil.which('wpctl'):
        out = _run(['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'])  # "Volume: 0.40" or "Volume: 0.40 [MUTED]"
        if out and out.startswith('Volume:'):
            try:
                return float(out.split()[1]), '[MUTED]' in out
            except (IndexError, ValueError):
                pass
    if shutil.which('pactl'):
        out = _run(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'])  # "Volume: front-left: 26214 /  40% / ..."
        mute = _run(['pactl', 'get-sink-mute', '@DEFAULT_SINK@']) or ''
        if out and '%' in out:
            try:
                percent = next(part for part in out.split('/') if part.strip().endswith('%'))
                return int(percent.strip()[:-1]) / 100, 'yes' in mute
            except (StopIteration, ValueError):
                pass
    return None


def volume():
    """Master volume of the default playback device, 0.0-1.0, or None when no sound server answers."""
    state = _volume_state()
    return round(state[0], 4) if state else None


def muted():
    state = _volume_state()
    return state[1] if state else None


def set_volume(level):
    """Sets the master volume (0.0-1.0, never above 100 %). True when the sound server accepted it."""
    level = max(0.0, min(1.0, float(level)))
    if shutil.which('wpctl') and _run(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{level:.3f}']) is not None:
        return True
    return bool(shutil.which('pactl')) and _run(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{round(level * 100)}%']) is not None


def set_mute(on):
    if shutil.which('wpctl') and _run(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if on else '0']) is not None:
        return True
    return bool(shutil.which('pactl')) and _run(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if on else '0']) is not None


def _backlight():
    """(current, max) of the first backlight in /sys/class/backlight, or None (external monitors, VMs)."""
    try:
        for device in sorted(os.listdir('/sys/class/backlight')):
            base = f'/sys/class/backlight/{device}'
            with open(f'{base}/brightness') as now, open(f'{base}/max_brightness') as top:
                return int(now.read()), int(top.read())
    except (OSError, ValueError):
        return None
    return None


def brightness():
    """The built-in display's backlight in percent, or None when there is none Cameo can read."""
    level = _backlight()
    return round(level[0] * 100 / level[1]) if level and level[1] else None


def set_brightness(percent):
    """Sets the backlight: brightnessctl (works without root through logind), then xbacklight, then light.
    True when one of them accepted it. The /sys file itself needs root, so it is only read."""
    percent = max(0, min(100, int(percent)))
    for command in (['brightnessctl', '-q', 'set', f'{percent}%'], ['xbacklight', '-set', str(percent)], ['light', '-S', str(percent)]):
        if shutil.which(command[0]):
            try:
                if subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def idle_seconds():
    """Seconds since the last keyboard or mouse input (MIT-SCREEN-SAVER extension); None when X cannot tell."""
    try:
        return root().screensaver_query_info().idle / 1000
    except Exception:
        return None


def message(text_, caption='Cameo'):
    for command in (['zenity', '--info', '--title', caption, '--text', text_], ['notify-send', caption, text_]):
        if shutil.which(command[0]):
            subprocess.Popen(command)
            return
    print(f'{caption}: {text_}', file=sys.stderr)


# -- HUD windows ------------------------------------------------------------------------------
def frame_of(window):
    """The real top-level X window of a Tk toplevel (Tk's wrapper, a child of the root). `wm frame` can
    return the inner window instead, and a shape set there does not stop clicks."""
    window.update_idletasks()
    w = win(window.winfo_id())
    while True:
        tree = w.query_tree()
        if not tree.parent or tree.parent.id == tree.root.id:
            return w
        w = tree.parent


def hud_prepare(window, kind, key):
    """Overlay: clicks pass through (empty input shape) and nothing shows until hud_shape() draws.
    Rail and ball are override-redirect windows, so the window manager never gives them focus."""
    if not dpy().has_extension('SHAPE'):
        return None
    frame = frame_of(window)
    if kind == 'overlay':
        frame.shape_rectangles(shape.SO.Set, SHAPE_INPUT, X.Unsorted, 0, 0, [])
        frame.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, [])
    dpy().flush()
    return frame


def canvas_mask(canvas):
    """1-bit picture of what is drawn on a Tk canvas, as (mask, left, top); text keeps its box."""
    import numpy as np
    from PIL import Image, ImageDraw
    items = [i for i in canvas.find_all() if canvas.itemcget(i, 'state') != 'hidden']
    box = canvas.bbox(*items) if items else None
    if not box:
        return None, 0, 0
    left, top = max(0, box[0] - 4), max(0, box[1] - 4)
    picture = Image.new('1', (box[2] + 4 - left, box[3] + 4 - top), 0)
    draw = ImageDraw.Draw(picture)
    for item in items:
        kind = canvas.type(item)
        points = [c - (left if n % 2 == 0 else top) for n, c in enumerate(canvas.coords(item))]
        width = int(float(canvas.itemcget(item, 'width') or 1)) + 2 if kind != 'text' else 0
        if kind == 'line' and len(points) >= 4:
            draw.line(points, fill=1, width=width, joint='curve')
        elif kind == 'oval' and len(points) == 4:
            if canvas.itemcget(item, 'fill'):
                draw.ellipse(points, fill=1)
            else:
                draw.ellipse(points, outline=1, width=width)
        elif kind == 'arc' and len(points) == 4:
            # Tk: degrees counter-clockwise from 3 o'clock; Pillow: clockwise.
            start, extent = float(canvas.itemcget(item, 'start')), float(canvas.itemcget(item, 'extent'))
            a, b = sorted((-start, -(start + extent)))
            draw.arc(points, a, b, fill=1, width=width)
        else:
            x0, y0, x1, y1 = canvas.bbox(item)
            draw.rectangle((x0 - left - 3, y0 - top - 2, x1 - left + 3, y1 - top + 2), fill=1)
    return np.asarray(picture, dtype=bool), left, top


def mask_rectangles(mask, left, top):
    """Row runs of a boolean mask -> X rectangles (x, y, width, 1)."""
    import numpy as np
    if mask is None:
        return []
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    step = np.diff(padded, axis=1)
    rows, starts = np.nonzero(step == 1)
    _, ends = np.nonzero(step == -1)
    return [(int(x) + left, int(y) + top, int(e - x), 1) for y, x, e in zip(rows, starts, ends)]


def hud_shape(window, canvas, key):
    """Shows only the drawn items of a HUD canvas (X11 has no transparent colour key). Redrawn only when
    something on the canvas changed."""
    frame = getattr(window, '_cameo_frame', None)
    if frame is None:
        if not dpy().has_extension('SHAPE'):
            return
        frame = window._cameo_frame = frame_of(window)
    items = [i for i in canvas.find_all() if canvas.itemcget(i, 'state') != 'hidden']
    signature = tuple((i, tuple(canvas.coords(i)), canvas.itemcget(i, 'text') if canvas.type(i) == 'text' else '',
                       canvas.itemcget(i, 'extent') if canvas.type(i) == 'arc' else '') for i in items)
    if signature == getattr(window, '_cameo_shape', None):
        return
    window._cameo_shape = signature
    frame.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, mask_rectangles(*canvas_mask(canvas)))
    dpy().flush()


# -- files (grab and drop between devices) ----------------------------------------------------
def files_at(x, y):
    """File managers on X11 expose neither the item under the pointer nor the selection in a common way, so a grab
    there shares a picture of the window instead (the caller's fallback)."""
    return []


def open_path(path):
    """Opens a file or folder with the desktop's default app."""
    subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def reveal(path):
    """Shows the folder that holds a file (file managers differ in how to select one)."""
    open_path(os.path.dirname(str(path)))
