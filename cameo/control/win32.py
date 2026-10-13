"""Windows desktop backend: typed Win32 bindings for windows, input (SendInput) and hotkeys.

Exposes the same functions as x11.py; everything else imports them through control/system.py.
"""
import ctypes as C
from ctypes import wintypes as W
import os
import time

SOURCE = 'win32'
user32 = C.WinDLL('user32', use_last_error=True)
kernel32 = C.WinDLL('kernel32', use_last_error=True)
dwmapi = C.WinDLL('dwmapi')


def bind(lib, name, restype, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = restype, list(args)
    return fn


ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
enum_windows = bind(user32, 'EnumWindows', W.BOOL, ENUM, W.LPARAM)
is_window = bind(user32, 'IsWindow', W.BOOL, W.HWND)
is_visible = bind(user32, 'IsWindowVisible', W.BOOL, W.HWND)
is_iconic = bind(user32, 'IsIconic', W.BOOL, W.HWND)
is_zoomed = bind(user32, 'IsZoomed', W.BOOL, W.HWND)
foreground = bind(user32, 'GetForegroundWindow', W.HWND)
set_foreground = bind(user32, 'SetForegroundWindow', W.BOOL, W.HWND)
show_window = bind(user32, 'ShowWindow', W.BOOL, W.HWND, C.c_int)
get_owner = bind(user32, 'GetWindow', W.HWND, W.HWND, W.UINT)
get_long = bind(user32, 'GetWindowLongW', C.c_long, W.HWND, C.c_int)
set_long = bind(user32, 'SetWindowLongW', C.c_long, W.HWND, C.c_int, C.c_long)
get_rect = bind(user32, 'GetWindowRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
get_text = bind(user32, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
get_class = bind(user32, 'GetClassNameW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
get_pid = bind(user32, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
window_at = bind(user32, 'WindowFromPoint', W.HWND, W.POINT)
ancestor = bind(user32, 'GetAncestor', W.HWND, W.HWND, W.UINT)
keybd_event = bind(user32, 'keybd_event', None, W.BYTE, W.BYTE, W.DWORD, C.c_size_t)
set_window_pos = bind(user32, 'SetWindowPos', W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
system_info = bind(user32, 'SystemParametersInfoW', W.BOOL, W.UINT, W.UINT, C.c_void_p, W.UINT)
open_process = bind(kernel32, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
close_handle = bind(kernel32, 'CloseHandle', W.BOOL, W.HANDLE)
image_name = bind(kernel32, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
dwm_attribute = bind(dwmapi, 'DwmGetWindowAttribute', C.c_long, W.HWND, W.DWORD, C.c_void_p, W.DWORD)

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SHELL_CLASSES = {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'}


# -- windows ----------------------------------------------------------------------------------
def title(hwnd):
    buffer = C.create_unicode_buffer(512)
    get_text(hwnd, buffer, 512)
    return buffer.value


def class_name(hwnd):
    buffer = C.create_unicode_buffer(256)
    get_class(hwnd, buffer, 256)
    return buffer.value


def pid(hwnd):
    value = W.DWORD()
    get_pid(hwnd, C.byref(value))
    return value.value


def app_name(process_id):
    handle = open_process(0x1000, False, process_id)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        buffer, size = C.create_unicode_buffer(1024), W.DWORD(1024)
        return os.path.basename(buffer.value).lower().removesuffix('.exe') if image_name(handle, 0, buffer, C.byref(size)) else ''
    finally:
        close_handle(handle)


def geometry(hwnd):
    rect = W.RECT()
    get_rect(hwnd, C.byref(rect))
    return {'x': rect.left, 'y': rect.top, 'w': rect.right - rect.left, 'h': rect.bottom - rect.top}


def cloaked(hwnd):
    value = C.c_int(0)
    dwm_attribute(hwnd, 14, C.byref(value), C.sizeof(value))  # DWMWA_CLOAKED: suspended/hidden Store apps
    return bool(value.value)


def state(hwnd):
    return {'hidden': bool(is_iconic(hwnd)), 'maximized': bool(is_zoomed(hwnd)), 'active': foreground() == hwnd}


def top_level_windows():
    """Front to back."""
    found = []

    @ENUM
    def each(hwnd, _):
        found.append(hwnd)
        return True

    enum_windows(each, 0)
    return found


def app_window(hwnd):
    """A normal, visible application window: not the desktop, the taskbar, a tool window, an owned
    dialog or a cloaked (suspended) Store app."""
    if not is_window(hwnd) or not is_visible(hwnd) or cloaked(hwnd):
        return False
    if class_name(hwnd) in SHELL_CLASSES or not title(hwnd).strip():
        return False
    return not (get_long(hwnd, -20) & 0x80 or get_owner(hwnd, 4))  # WS_EX_TOOLWINDOW, owned window


def maximize(hwnd):
    show_window(hwnd, SW_MAXIMIZE)


def minimize(hwnd):
    show_window(hwnd, SW_MINIMIZE)


def restore(hwnd):
    """One SW_RESTORE step: minimized -> its previous state, maximized -> normal."""
    show_window(hwnd, SW_RESTORE)


def move_resize(hwnd, rect):
    set_window_pos(hwnd, None, rect['x'], rect['y'], rect['w'], rect['h'], 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE


def bring_to_front(hwnd):
    """Windows refuses SetForegroundWindow from background processes unless input was just seen;
    a synthetic Alt tap is the documented-compatible way to satisfy that rule."""
    if is_iconic(hwnd):
        show_window(hwnd, SW_RESTORE)
    keybd_event(0x12, 0, 0, 0)
    keybd_event(0x12, 0, 2, 0)
    return bool(set_foreground(hwnd))


def close(hwnd):
    user32.PostMessageW(W.HWND(hwnd), 0x0010, 0, 0)  # WM_CLOSE


def shell_at(x, y):
    """'desktop' or 'taskbar' when the shell is under a screen point (those icons open with their own click rules)."""
    hwnd = ancestor(window_at(W.POINT(x, y)), 2)
    kind = class_name(hwnd) if hwnd else ''
    if kind in ('Progman', 'WorkerW'):
        return 'desktop'
    if kind in ('Shell_TrayWnd', 'Shell_SecondaryTrayWnd'):
        return 'taskbar'
    return None


# -- screen -----------------------------------------------------------------------------------
metric = bind(user32, 'GetSystemMetrics', C.c_int, C.c_int)
cursor_pos = bind(user32, 'GetCursorPos', W.BOOL, C.POINTER(W.POINT))
set_cursor = bind(user32, 'SetCursorPos', W.BOOL, C.c_int, C.c_int)


def cursor():
    point = W.POINT()
    cursor_pos(C.byref(point))
    return point.x, point.y


def screen_size():
    return metric(0), metric(1)


def work_area():
    """Screen area without the taskbar: left, top, right, bottom."""
    rect = W.RECT()
    system_info(0x30, 0, C.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))  # per-monitor v2: real pixels, like the pointer
    except (AttributeError, OSError):
        pass


# -- input ------------------------------------------------------------------------------------
ULONG_PTR = C.c_size_t


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]


class INPUTUNION(C.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', W.DWORD), ('u', INPUTUNION)]


send_input = bind(user32, 'SendInput', W.UINT, W.UINT, C.POINTER(INPUT), C.c_int)
key_state = bind(user32, 'GetAsyncKeyState', W.SHORT, C.c_int)
open_desktop = bind(user32, 'OpenInputDesktop', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
close_desktop = bind(user32, 'CloseDesktop', W.BOOL, W.HANDLE)
desktop_info = bind(user32, 'GetUserObjectInformationW', W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD))

VK = {'left': 0x01, 'right': 0x02, 'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12, 'super': 0x5B, 'super_r': 0x5C,
      'enter': 0x0D, 'escape': 0x1B, 'end': 0x23, 'delete': 0x2E, 'volume_down': 0xAE, 'volume_up': 0xAF}
BUTTONS = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010)}


def vk(name):
    return VK.get(name) or ord(name.upper())


def interactive_desktop():
    """False on the secure desktop (UAC prompt, Ctrl+Alt+Del) or a locked session."""
    desktop = open_desktop(0, False, 1)
    if not desktop:
        return False
    try:
        name, needed = C.create_unicode_buffer(256), W.DWORD()
        return bool(desktop_info(desktop, 2, name, C.sizeof(name), C.byref(needed))) and name.value == 'Default'
    finally:
        close_desktop(desktop)


def held(names):
    """Which of these mouse buttons / keys are physically down right now."""
    found = {name for name in names if key_state(vk(name)) & 0x8000}
    if 'super' in names and key_state(VK['super_r']) & 0x8000:
        found.add('super')
    return found


def mouse(flags, data=0, x=0, y=0):
    event = INPUT(type=0)
    event.mi = MOUSEINPUT(x, y, data & 0xffffffff, flags, 0, 0)
    return event


def key(code, up=False, unicode=False):
    event = INPUT(type=1)
    event.ki = KEYBDINPUT(0, code, 0x0004 | (2 if up else 0), 0, 0) if unicode else KEYBDINPUT(code, 0, 2 if up else 0, 0, 0)
    return event


def send(events):
    """Plays ('move', x, y) / ('down'|'up', button) / ('wheel', amount) / ('key', name, down) /
    ('char', ch) as one SendInput batch, so nothing else can interleave."""
    width, height = screen_size()
    batch = []
    for event in events:
        kind = event[0]
        if kind == 'move':
            batch.append(mouse(0x8001, x=round(event[1] * 65535 / max(1, width - 1)), y=round(event[2] * 65535 / max(1, height - 1))))
        elif kind in ('down', 'up'):
            batch.append(mouse(BUTTONS[event[1]][kind == 'up']))
        elif kind == 'wheel':
            batch.append(mouse(0x0800, event[1]))  # 120 = one notch
        elif kind == 'key':
            batch.append(key(vk(event[1]), up=not event[2]))
        elif kind == 'char':
            batch += [key(ord(event[1]), unicode=True), key(ord(event[1]), up=True, unicode=True)]
    if not batch:
        return
    data = (INPUT * len(batch))(*batch)
    if send_input(len(batch), data, C.sizeof(INPUT)) != len(batch):
        raise RuntimeError('Windows blocked input; administrator windows are not supported')


def press_ctrl(letter):
    """Ctrl+<letter> in one burst; releases are always sent so Ctrl cannot stay held."""
    keybd_event(0x11, 0, 0, 0)
    try:
        keybd_event(vk(letter), 0, 0, 0)
        keybd_event(vk(letter), 0, 2, 0)
    finally:
        keybd_event(0x11, 0, 2, 0)


def hotkeys(bindings, stop):
    """bindings {'p': callback, ...} for Ctrl+Alt+<letter>, called on this thread until `stop` is set."""
    ids = {}
    for ident, (letter, callback) in enumerate(bindings.items(), 1):
        if user32.RegisterHotKey(None, ident, 0x4000 | 0x2 | 0x1, vk(letter)):  # NOREPEAT | CONTROL | ALT
            ids[ident] = callback
    msg = W.MSG()
    try:
        while not stop.is_set():
            while user32.PeekMessageW(C.byref(msg), None, 0x0312, 0x0312, 1):  # WM_HOTKEY
                if msg.wParam in ids:
                    ids[msg.wParam]()
            time.sleep(.02)
    finally:
        for ident in ids:
            user32.UnregisterHotKey(None, ident)
    return len(ids) == len(bindings)


def lock_screen():
    """What Win+L does. Windows ignores a synthetic Win+L (the lock key belongs to the secure desktop),
    so this calls LockWorkStation directly. True when Windows accepted the request."""
    return bool(user32.LockWorkStation())


class LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('luid', C.c_int64), ('attributes', W.DWORD)]


class TOKEN_PRIVILEGES(C.Structure):
    _fields_ = [('count', W.DWORD), ('privilege', LUID_AND_ATTRIBUTES)]


class LASTINPUTINFO(C.Structure):
    _fields_ = [('size', W.UINT), ('time', W.DWORD)]


def shutdown_privilege():
    """SetSuspendState needs SeShutdownPrivilege switched on in this process's token (users hold it, disabled)."""
    advapi32 = C.WinDLL('advapi32', use_last_error=True)
    current = bind(kernel32, 'GetCurrentProcess', W.HANDLE)
    open_token = bind(advapi32, 'OpenProcessToken', W.BOOL, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE))
    lookup = bind(advapi32, 'LookupPrivilegeValueW', W.BOOL, W.LPCWSTR, W.LPCWSTR, C.POINTER(C.c_int64))
    adjust = bind(advapi32, 'AdjustTokenPrivileges', W.BOOL, W.HANDLE, W.BOOL, C.POINTER(TOKEN_PRIVILEGES), W.DWORD, C.c_void_p, C.c_void_p)
    token, luid = W.HANDLE(), C.c_int64()
    if not open_token(current(), 0x28, C.byref(token)):  # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
        return False
    try:
        if not lookup(None, 'SeShutdownPrivilege', C.byref(luid)):
            return False
        wanted = TOKEN_PRIVILEGES(1, LUID_AND_ATTRIBUTES(luid.value, 2))  # SE_PRIVILEGE_ENABLED
        return bool(adjust(token, False, C.byref(wanted), 0, None, None))
    finally:
        close_handle(token)


def sleep_computer():
    """What Start > Power > Sleep does. Modern Standby laptops (S0 low power idle, no S3) sleep when the display
    goes off, and SetSuspendState there would hibernate instead; classic S3 machines suspend with SetSuspendState.
    True when Windows accepted the request."""
    powrprof = C.WinDLL('powrprof', use_last_error=True)
    caps = (C.c_ubyte * 128)()  # SYSTEM_POWER_CAPABILITIES: byte 5 = SystemS3, byte 20 = AoAc (Modern Standby)
    s3 = powrprof.GetPwrCapabilities(caps) and caps[5] and not caps[20]
    if s3:
        shutdown_privilege()
        if powrprof.SetSuspendState(False, False, False):
            return True
    # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = off. Posted, so a hung window cannot block it.
    post = bind(user32, 'PostMessageW', W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    return bool(post(0xFFFF, 0x0112, 0xF170, 2))


def idle_seconds():
    """Seconds since the last keyboard or mouse input (Cameo's own injected input counts too)."""
    info = LASTINPUTINFO(C.sizeof(LASTINPUTINFO), 0)
    if not bind(user32, 'GetLastInputInfo', W.BOOL, C.POINTER(LASTINPUTINFO))(C.byref(info)):
        return None
    return ((bind(kernel32, 'GetTickCount', W.DWORD)() - info.time) & 0xFFFFFFFF) / 1000


def _wmi():
    """The root/WMI namespace for this thread (COM objects cannot cross threads); None without comtypes."""
    import threading
    local = _wmi.__dict__.setdefault('local', threading.local())
    if getattr(local, 'service', None) is None:
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            local.service = comtypes.client.CoGetObject('winmgmts:root/WMI', dynamic=True)
        except Exception:
            return None
    return local.service


def brightness():
    """The built-in display's brightness in percent (what the Settings slider shows), or None when Windows gives
    no brightness control (external monitors, desktops). Laptop panels answer through WMI's WmiMonitorBrightness."""
    service = _wmi()
    try:
        for item in service.ExecQuery('SELECT CurrentBrightness FROM WmiMonitorBrightness'):
            return int(item.Properties_('CurrentBrightness').Value)
    except Exception:
        return None
    return None


def set_brightness(percent):
    """What the Settings brightness slider does, for the built-in display. True when Windows accepted it."""
    service = _wmi()
    done = False
    try:
        for item in service.ExecQuery('SELECT * FROM WmiMonitorBrightnessMethods'):
            params = item.Methods_('WmiSetBrightness').InParameters.SpawnInstance_()
            params.Properties_('Brightness').Value = max(0, min(100, int(percent)))
            params.Properties_('Timeout').Value = 1
            item.ExecMethod_('WmiSetBrightness', params)
            done = True
    except Exception:
        return False
    return done


# -- levels: master volume (Core Audio), read back after every set ----------------------------------------
_audio = {}


def _endpoint_volume():
    """IAudioEndpointVolume of the default playback device, for this thread (COM objects cannot cross threads).
    Asked for on every call: the default device changes when headphones are plugged in. None without comtypes."""
    try:
        import comtypes
        from comtypes import COMMETHOD, GUID, IUnknown
    except ImportError:
        return None
    if 'types' not in _audio:
        hr = C.HRESULT
        guid = C.POINTER(GUID)

        class IAudioEndpointVolume(IUnknown):
            _iid_ = GUID('{5CDF2C82-841E-4546-9722-0CF74078229A}')
            _methods_ = (
                COMMETHOD([], hr, 'RegisterControlChangeNotify', (['in'], C.c_void_p, 'notify')),
                COMMETHOD([], hr, 'UnregisterControlChangeNotify', (['in'], C.c_void_p, 'notify')),
                COMMETHOD([], hr, 'GetChannelCount', (['out'], C.POINTER(C.c_uint), 'count')),
                COMMETHOD([], hr, 'SetMasterVolumeLevel', (['in'], C.c_float, 'db'), (['in'], guid, 'context')),
                COMMETHOD([], hr, 'SetMasterVolumeLevelScalar', (['in'], C.c_float, 'level'), (['in'], guid, 'context')),
                COMMETHOD([], hr, 'GetMasterVolumeLevel', (['out'], C.POINTER(C.c_float), 'db')),
                COMMETHOD([], hr, 'GetMasterVolumeLevelScalar', (['out'], C.POINTER(C.c_float), 'level')),
                COMMETHOD([], hr, 'SetChannelVolumeLevel', (['in'], C.c_uint, 'channel'), (['in'], C.c_float, 'db'), (['in'], guid, 'context')),
                COMMETHOD([], hr, 'SetChannelVolumeLevelScalar', (['in'], C.c_uint, 'channel'), (['in'], C.c_float, 'level'), (['in'], guid, 'context')),
                COMMETHOD([], hr, 'GetChannelVolumeLevel', (['in'], C.c_uint, 'channel'), (['out'], C.POINTER(C.c_float), 'db')),
                COMMETHOD([], hr, 'GetChannelVolumeLevelScalar', (['in'], C.c_uint, 'channel'), (['out'], C.POINTER(C.c_float), 'level')),
                COMMETHOD([], hr, 'SetMute', (['in'], W.BOOL, 'mute'), (['in'], guid, 'context')),
                COMMETHOD([], hr, 'GetMute', (['out'], C.POINTER(W.BOOL), 'mute')),
            )

        class IMMDevice(IUnknown):
            _iid_ = GUID('{D666063F-1587-4E43-81F1-21F9DC53D8FA}')
            _methods_ = (
                COMMETHOD([], hr, 'Activate', (['in'], guid, 'iid'), (['in'], W.DWORD, 'context'), (['in'], C.c_void_p, 'params'),
                          (['out'], C.POINTER(C.c_void_p), 'interface')),
            )

        class IMMDeviceEnumerator(IUnknown):
            _iid_ = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
            _methods_ = (
                COMMETHOD([], hr, 'EnumAudioEndpoints', (['in'], W.DWORD, 'flow'), (['in'], W.DWORD, 'mask'), (['out'], C.POINTER(C.c_void_p), 'devices')),
                COMMETHOD([], hr, 'GetDefaultAudioEndpoint', (['in'], W.DWORD, 'flow'), (['in'], W.DWORD, 'role'),
                          (['out'], C.POINTER(C.POINTER(IMMDevice)), 'device')),
            )

        _audio['types'] = (IAudioEndpointVolume, IMMDeviceEnumerator, GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}'))
    endpoint, enumerator, clsid = _audio['types']
    try:
        comtypes.CoInitialize()
        devices = comtypes.CoCreateInstance(clsid, enumerator, comtypes.CLSCTX_INPROC_SERVER)
        device = devices.GetDefaultAudioEndpoint(0, 1)  # eRender, eMultimedia
        raw = device.Activate(endpoint._iid_, comtypes.CLSCTX_INPROC_SERVER, None)
        return comtypes.cast(raw, C.POINTER(endpoint))
    except Exception:
        return None  # no playback device (a desktop without speakers) or the audio service is stopped


def volume():
    """Master volume of the default playback device, 0.0-1.0 (what the Windows volume flyout shows / 100), or None."""
    control = _endpoint_volume()
    try:
        return round(float(control.GetMasterVolumeLevelScalar()), 4) if control else None
    except Exception:
        return None


def muted():
    control = _endpoint_volume()
    try:
        return bool(control.GetMute()) if control else None
    except Exception:
        return None


def set_volume(level):
    """Sets the master volume (0.0-1.0). True when Windows accepted it; read it back with volume()."""
    control = _endpoint_volume()
    try:
        control.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level))), None)
        return True
    except Exception:
        return False


def set_mute(on):
    control = _endpoint_volume()
    try:
        control.SetMute(bool(on), None)
        return True
    except Exception:
        return False


def message(text, caption='Cameo'):
    user32.MessageBoxW(None, text, caption, 0x40)


# -- HUD windows ------------------------------------------------------------------------------
NOACTIVATE, TOOLWINDOW, LAYERED, TRANSPARENT, TOPMOST = 0x08000000, 0x80, 0x80000, 0x20, 0x8
HUD_STYLE = {'overlay': NOACTIVATE | TOOLWINDOW | LAYERED | TRANSPARENT | TOPMOST, 'rail': NOACTIVATE | TOOLWINDOW, 'ball': NOACTIVATE | TOOLWINDOW}


def style(window, add):
    window.update_idletasks()
    hwnd = ancestor(window.winfo_id(), 2)
    set_long(hwnd, -20, get_long(hwnd, -20) | add)
    return hwnd


def hud_prepare(window, kind, key):
    """Overlay and ball: `key` colour is see-through; overlay also lets clicks pass. None of them takes focus."""
    if kind in ('overlay', 'ball'):
        window.attributes('-transparentcolor', key)
    return style(window, HUD_STYLE[kind])


def hud_shape(window, canvas, key):
    """Windows needs no shape: the transparent colour key already shows only what is drawn."""


# -- files (grab and drop between devices) ----------------------------------------------------
def _shell_pick(name, selected, folder):
    """The files a grab means: the whole selection when the pointed item is part of it (or nothing is pointed at),
    else the pointed item alone. A shortcut stands for the document or folder it points to; an app's shortcut
    stands for nothing (sending the program itself to another device is never what a grab means)."""
    if name is None or any(p.name == name or p.stem == name for p in selected):
        return [p for p in selected if p.exists()]
    items = folder.Items()
    for i in range(items.Count):
        item = items.Item(i)
        if item.Name != name or not item.IsFileSystem:
            continue
        from pathlib import Path
        path = item.Path
        if item.IsLink:
            try:
                path = item.GetLink.Path
            except Exception:
                return []
            if not path or Path(path).suffix.lower() in ('.exe', '.com', '.bat', '.cmd', '.msi', '.lnk', '.url'):
                return []
        return [Path(path)] if path and Path(path).exists() else []
    return []


def files_at(x, y):
    """Files under a screen point in the shell: the item under the pointer on the desktop or in an Explorer window
    (the whole selection when that item is part of it, or when no item is under the pointer). [] when there is none.
    UI Automation gives the item's name; the Shell's own window list gives the folder and the selection."""
    import comtypes
    import comtypes.client
    import uiautomation as auto
    from pathlib import Path
    hwnd = ancestor(window_at(W.POINT(x, y)), 2)
    kind = class_name(hwnd) if hwnd else ''
    if kind not in ('Progman', 'WorkerW', 'CabinetWClass'):
        return []
    name = None
    with auto.UIAutomationInitializerInThread():
        control = auto.ControlFromPoint(x, y)
        for _ in range(4):
            if control is None:
                break
            if control.ControlTypeName == 'ListItemControl':
                name = control.Name
                break
            control = control.GetParentControl()
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject('Shell.Application', dynamic=True)
        if kind != 'CabinetWClass':
            return _shell_pick(name, [], shell.NameSpace(0)) if name else []  # 0 = the desktop (user's and public, merged)
        windows = shell.Windows()
        for i in range(windows.Count):
            try:
                window = windows.Item(i)
                if window is None or int(window.HWND) != hwnd:
                    continue
                document = window.Document  # Windows 11: one entry per tab, all with the same window handle
                chosen = document.SelectedItems()
                selected = [Path(chosen.Item(j).Path) for j in range(chosen.Count)]
                picked = _shell_pick(name, selected, document.Folder)
            except Exception:
                continue
            if picked:
                return picked
        return []
    finally:
        comtypes.CoUninitialize()


def open_path(path):
    """Opens a file or folder with its default app."""
    os.startfile(str(path))


def reveal(path):
    """Shows a file selected in its folder (for anything Cameo will not open by itself, such as programs)."""
    import subprocess
    subprocess.Popen(['explorer.exe', '/select,', str(path)])
