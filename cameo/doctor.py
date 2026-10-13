"""Startup checks (ARCHITECTURE.md W1): green = ready, amber = degraded but usable, red = blocking."""
import hashlib
import os
import platform

from .paths import EXE, HAND_MODEL, LINUX, RUNTIME, WINDOWS

MODELS = {
    'hand model': (HAND_MODEL, 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'),
    'vosk en-us small': (RUNTIME / 'voice' / 'vosk' / 'vosk-model-small-en-us-0.15' / 'am' / 'final.mdl', None),
    'whisper small': (RUNTIME / 'voice' / 'whisper-model' / 'ggml-small-q8_0.bin', None),
    'wake word model': (RUNTIME / 'voice' / 'kws' / 'sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01' / 'tokens.txt', None),
}


def check(name, status, detail):
    return {'name': name, 'status': status, 'detail': detail}


def session():
    """Windows, or a Linux X11 session. Wayland blocks reading/moving other apps' windows and synthetic input."""
    name = f'{platform.system()} {platform.release()}'
    if WINDOWS:
        return check('platform', 'green', name)
    if not LINUX:
        return check('platform', 'red', f'{name}: only Windows and Linux are supported')
    kind = os.environ.get('XDG_SESSION_TYPE', '')
    if kind == 'wayland' or (os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('DISPLAY')):
        return check('platform', 'red', f'{name}, Wayland session: log out and pick "Ubuntu on Xorg" (gear icon on the login screen)')
    if not os.environ.get('DISPLAY'):
        return check('platform', 'red', f'{name}: no X display (DISPLAY is not set)')
    try:
        from Xlib import display
        d = display.Display()
        missing = [ext for ext in ('XTEST', 'SHAPE') if not d.has_extension(ext)]
        d.close()
    except Exception as error:
        return check('platform', 'red', f'{name}: cannot open the X display ({error})')
    if missing:
        return check('platform', 'red', f'{name}, X11: missing X extensions {", ".join(missing)}')
    return check('platform', 'green', f'{name}, X11 ({kind or "session type unknown"})')


def run(devices=True):
    from .control import browsers
    results = [session()]
    for name, (path, digest) in MODELS.items():
        if not path.exists():
            results.append(check(name, 'red' if name == 'hand model' else 'amber', f'missing: {path}'))
        elif digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            results.append(check(name, 'red', 'checksum mismatch'))
        else:
            results.append(check(name, 'green', 'present'))
    found = browsers.installed()
    linked = [name for name, _, root in found if browsers.bridge_profile(root)]
    names = ', '.join(dict.fromkeys(name for name, _, _ in found))
    if linked:
        results.append(check('browser', 'green', f"{names}; bridge in {', '.join(linked)}"))
    elif found:
        results.append(check('browser', 'amber', f'{names}; tab actions need the Cameo Bridge extension'))
    else:
        results.append(check('browser', 'red', 'no browser found; web and tab actions unavailable'))
    results.append(check('whisper-server', 'green' if (RUNTIME / 'voice' / 'whisper' / 'Release' / f'whisper-server{EXE}').exists() else 'amber',
                         'fallback recognizer'))
    results.append(link())
    results += levels()
    if devices:
        results.append(microphone())
        results.append(camera())
    return results


def levels():
    """Volume and brightness signs set levels and read them back; say which of them this computer allows."""
    from .control import system
    volume, brightness = system.volume(), system.brightness()
    return [check('volume control', 'green' if volume is not None else 'amber',
                  f'{round(volume * 100)} % (line left / right sets it)' if volume is not None else 'no playback device or sound server: volume signs fail'),
            check('brightness control', 'green' if brightness is not None else 'amber',
                  f'{brightness} % (line up / down sets it)' if brightness is not None else
                  'no built-in display brightness (external monitor?): brightness signs fail')]


def link():
    """Grab and drop between devices: encryption library, and on Windows the firewall rule and a Private network
    (Windows blocks incoming links and discovery on Public networks)."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return check('devices', 'amber', 'pip install cryptography (grab and drop between devices is off)')
    if not WINDOWS:
        return check('devices', 'green', 'ready (TCP 47820, UDP 47821 must be open in any firewall)')
    import subprocess
    quiet = {'capture_output': True, 'text': True, 'timeout': 15, 'creationflags': 0x08000000}  # CREATE_NO_WINDOW
    try:
        rule = subprocess.run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=Cameo Link TCP'], **quiet).returncode == 0
        profile = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-NetConnectionProfile | Select-Object -First 1).NetworkCategory'],
                                 **quiet).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return check('devices', 'amber', f'could not check the firewall: {error}')
    problems = []
    if not rule:
        problems.append('no firewall rule: run scripts\\setup_link.ps1 as administrator')
    if profile and profile != 'Private':
        problems.append(f'network is {profile}: set it to Private in Settings > Network')
    return check('devices', 'amber' if problems else 'green', '; '.join(problems) or 'ready (firewall open, Private network)')


def microphone():
    try:
        import numpy as np
        import sounddevice as sd
        audio = sd.rec(8000, samplerate=16000, channels=1, dtype='float32')
        sd.wait()
        level = float(np.sqrt(np.mean(audio ** 2)))
    except Exception as error:
        return check('microphone', 'red', f'unavailable: {error}')
    if level < .0004:
        where = 'Sound settings > Recording' if WINDOWS else 'Settings > Sound > Input'
        return check('microphone', 'amber', f'silent (level {level:.5f}): unmute it or raise its level in {where}')
    return check('microphone', 'green', f'level {level:.4f}')


def camera(index=0):
    try:
        import cv2
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW if WINDOWS else cv2.CAP_V4L2)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
    except Exception as error:
        return check('camera', 'red', f'unavailable: {error}')
    hint = 'close other camera apps' if WINDOWS else 'close other camera apps; your user must be in the "video" group'
    return check('camera', 'green' if ok else 'amber', 'delivers frames' if ok else f'busy or missing: {hint}')
