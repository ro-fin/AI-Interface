"""What a grab picks up for another device (runs on a background thread while the hand carries it):

1. a browser window: the page itself (its address and title from the bridge), never a picture of it
2. the desktop or an Explorer window: the files under the pointer (or the selection they belong to)
3. any other window: a picture of that window

A page served from this device's own loopback (a web app under development at localhost:3000) would open the OTHER
device's localhost, so its address is rewritten to this device's network address.
"""
from pathlib import Path
import time
from urllib.parse import urlsplit, urlunsplit

from ..control import system
from ..paths import WINDOWS
from ..targets import BROWSERS
from .share import web_url

LOOPBACK = {'localhost', '0.0.0.0', '::1'}


def reachable(url, ip):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if host in LOOPBACK or host.startswith('127.'):
        return urlunsplit(parts._replace(netloc=ip + (f':{parts.port}' if parts.port else '')))
    return url


def window_picture(hwnd, title, folder):
    from PIL import ImageGrab
    g = system.geometry(hwnd)
    box = (g['x'], g['y'], g['x'] + g['w'], g['y'] + g['h'])
    image = ImageGrab.grab(bbox=box, all_screens=True) if WINDOWS else ImageGrab.grab(bbox=box)
    folder.mkdir(parents=True, exist_ok=True)
    name = ''.join(c for c in title if c.isalnum() or c in ' -_').strip()[:40] or 'window'
    path = folder / f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.png"
    image.save(path)
    return path


def resolve(grab, bridge, ip, outbox):
    """grab: {'kind': 'window' | 'mouse', 'at', 'app', 'hwnd', 'title', 'icon'} as Runtime.grab_start recorded it."""
    at, app = grab['at'], grab.get('app', '')
    if grab['kind'] == 'window' and app in BROWSERS and bridge is not None and bridge.connected:
        try:
            page = bridge.request('page', timeout=1.5)
        except (ConnectionError, TimeoutError, RuntimeError):
            page = None
        if page and web_url(page.get('url')):
            url = reachable(page['url'], ip)
            return {'kind': 'link', 'url': url, 'title': page.get('title', ''), 'label': page.get('title') or url}
    if (grab['kind'] == 'window' and app == 'explorer') or grab.get('icon') == 'desktop':
        files = system.files_at(*at)
        if files:
            return {'kind': 'files', 'paths': files, 'label': files[0].name if len(files) == 1 else f'{files[0].name} and {len(files) - 1} more'}
    if grab['kind'] == 'window' and grab.get('hwnd'):
        path = window_picture(grab['hwnd'], grab.get('title', ''), Path(outbox))
        return {'kind': 'files', 'paths': [path], 'label': f"picture of {grab.get('title') or 'the window'}"}
    return None


def clean_outbox(outbox, days=2):
    """Window pictures are only needed until they are sent."""
    cutoff = time.time() - days * 86400
    for p in Path(outbox).glob('*.png'):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
