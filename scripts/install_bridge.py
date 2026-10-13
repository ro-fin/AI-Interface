"""Registers the Cameo Bridge native-messaging host for Edge, Chrome, Brave and Chromium (current user only).

    python scripts/install_bridge.py            register
    python scripts/install_bridge.py --remove   undo

Windows: registry keys under HKCU\\Software\\<browser>\\NativeMessagingHosts.
Linux:   a manifest in ~/.config/<browser>/NativeMessagingHosts/ (and the Chromium snap's folder).
The extension itself is loaded once by the user: edge://extensions (or chrome://extensions) >
Developer mode > Load unpacked > bridge/extension.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_DIR = ROOT / 'bridge' / 'host'
NAME = 'dev.cameo.bridge'
WINDOWS = sys.platform == 'win32'
REGISTRY = [rf'Software\Microsoft\Edge\NativeMessagingHosts\{NAME}', rf'Software\Google\Chrome\NativeMessagingHosts\{NAME}',
            rf'Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{NAME}']
LINUX_DIRS = ['~/.config/microsoft-edge', '~/.config/google-chrome', '~/.config/BraveSoftware/Brave-Browser', '~/.config/chromium',
              '~/snap/chromium/common/chromium']


def launcher():
    """The file the browser starts: it runs the host with this same Python."""
    if WINDOWS:
        path = HOST_DIR / 'cameo_bridge_host.bat'
        path.write_text(f'@"{sys.executable}" -u "{HOST_DIR / "cameo_bridge_host.py"}" %*\r\n', encoding='ascii')
    else:
        path = HOST_DIR / 'cameo_bridge_host.sh'
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{HOST_DIR / "cameo_bridge_host.py"}" "$@"\n', encoding='utf-8')
        path.chmod(0o755)
    return path


def manifest(path):
    extension = (ROOT / 'bridge' / 'extension' / 'id.txt').read_text(encoding='utf-8').strip()
    return {'name': NAME, 'description': 'Cameo Bridge host', 'path': str(path), 'type': 'stdio',
            'allowed_origins': [f'chrome-extension://{extension}/']}


def install():
    data = json.dumps(manifest(launcher()), indent=2)  # browsers expect UTF-8 without a byte-order mark
    if WINDOWS:
        import winreg
        target = HOST_DIR / f'{NAME}.json'
        target.write_text(data, encoding='utf-8')
        for key in REGISTRY:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
                winreg.SetValueEx(handle, '', 0, winreg.REG_SZ, str(target))
        print(f'Cameo Bridge host registered: {target}')
    else:
        for folder in LINUX_DIRS:
            base = Path(os.path.expanduser(folder))
            if not base.exists():
                continue  # that browser has never run for this user
            target = base / 'NativeMessagingHosts' / f'{NAME}.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data, encoding='utf-8')
            print(f'registered: {target}')
    print(f'Now load the extension once: <browser>://extensions > Developer mode > Load unpacked > {ROOT / "bridge" / "extension"}')


def remove():
    if WINDOWS:
        import winreg
        for key in REGISTRY:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
        (HOST_DIR / f'{NAME}.json').unlink(missing_ok=True)
    else:
        for folder in LINUX_DIRS:
            (Path(os.path.expanduser(folder)) / 'NativeMessagingHosts' / f'{NAME}.json').unlink(missing_ok=True)
    print('Cameo Bridge host unregistered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--remove', action='store_true')
    remove() if parser.parse_args().remove else install()
