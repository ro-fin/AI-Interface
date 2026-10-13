"""Open any installed application by name.

Windows: Start menu apps (Get-StartApps), including Store apps like Teams. Linux: the desktop entries
(*.desktop) of the applications menu, including snaps and flatpaks. The index is read in the background
at start-up and cached, so a newly installed app is known after the next start. Names are matched
loosely (speech is never exact): aliases, then whole name, then words, then spelling similarity.
"""
import configparser
import difflib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time

from ..contracts import ActionResult, failure
from ..paths import WINDOWS, state_dir
from . import system

CACHE = state_dir() / 'apps.json'
NO_WINDOW = 0x08000000

# Spoken name -> Start menu / applications menu name (lower case). Also covers common mishearings.
ALIASES = {
    'team': 'microsoft teams', 'teams': 'microsoft teams', 'ms teams': 'microsoft teams',
    'edge': 'microsoft edge', 'browser': 'microsoft edge', 'vs code': 'visual studio code', 'vscode': 'visual studio code',
    'code': 'visual studio code', 'explorer': 'file explorer', 'files': 'file explorer', 'file manager': 'file explorer',
    'my computer': 'file explorer', 'this pc': 'file explorer', 'calc': 'calculator', 'settings': 'settings',
    'store': 'microsoft store', 'word': 'word', 'excel': 'excel', 'powerpoint': 'powerpoint', 'power point': 'powerpoint',
    'cmd': 'command prompt', 'command prompt': 'command prompt', 'terminal': 'terminal', 'paint': 'paint',
    'camera': 'camera', 'photos': 'photos', 'clock': 'clock', 'alarm': 'clock', 'mail': 'outlook', 'outlook': 'outlook',
    'task manager': 'task manager', 'control panel': 'control panel', 'snipping tool': 'snipping tool',
}
if WINDOWS:
    BUILTIN = {  # always available even if the Start menu list is missing them
        'task manager': ['taskmgr.exe'], 'command prompt': ['cmd.exe'], 'control panel': ['control.exe'], 'notepad': ['notepad.exe'],
        'paint': ['mspaint.exe'], 'file explorer': ['explorer.exe'], 'calculator': ['calc.exe'],
    }
else:
    # The same spoken names mean the GNOME / Ubuntu equivalents.
    ALIASES.update({'explorer': 'files', 'files': 'files', 'file manager': 'files', 'file explorer': 'files', 'my computer': 'files',
                    'this pc': 'files', 'nautilus': 'files', 'browser': 'firefox', 'command prompt': 'terminal', 'cmd': 'terminal',
                    'task manager': 'system monitor', 'control panel': 'settings', 'notepad': 'text editor', 'snipping tool': 'screenshot',
                    'store': 'app center', 'software': 'app center', 'camera': 'cheese', 'photos': 'image viewer'})
    BUILTIN = {
        'terminal': ['x-terminal-emulator'], 'files': ['nautilus', '--new-window'], 'text editor': ['gnome-text-editor'],
        'calculator': ['gnome-calculator'], 'system monitor': ['gnome-system-monitor'], 'settings': ['gnome-control-center'],
    }
DROP = re.compile(r'\b(app|application|the|my|please|program|software)\b')
DESKTOP_DIRS = ['~/.local/share/applications', '/usr/local/share/applications', '/usr/share/applications',
                '/var/lib/snapd/desktop/applications', '~/.local/share/flatpak/exports/share/applications',
                '/var/lib/flatpak/exports/share/applications']
FIELD_CODES = re.compile(r'%[fFuUdDnNickvm]')


def normal(name):
    name = re.sub(r'[^a-z0-9 ]+', ' ', name.lower().replace('+', ' plus '))
    return re.sub(r'\s+', ' ', DROP.sub(' ', name)).strip()


def windows_apps():
    out = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress'],
                         capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
    return [{'name': a['Name'], 'id': a['AppID']} for a in json.loads(out) if a.get('Name') and a.get('AppID')]


def linux_apps():
    """Visible desktop entries; a user's own entry hides a system one with the same file name."""
    apps, seen = [], set()
    for folder in DESKTOP_DIRS:
        for path in sorted(Path(os.path.expanduser(folder)).glob('*.desktop')):
            if path.name in seen:
                continue
            seen.add(path.name)
            entry = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                entry.read(path, encoding='utf-8')
                section = entry['Desktop Entry']
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if section.get('Type', 'Application') != 'Application' or section.get('NoDisplay', '').lower() == 'true' \
                    or section.get('Hidden', '').lower() == 'true' or not section.get('Exec') or not section.get('Name'):
                continue
            apps.append({'name': section['Name'], 'id': str(path), 'generic': section.get('GenericName', '')})
    return apps


def linux_command(desktop_file):
    """The Exec line of a desktop entry without its %-field codes (no files are passed)."""
    entry = configparser.ConfigParser(interpolation=None, strict=False)
    entry.read(desktop_file, encoding='utf-8')
    return [part for part in shlex.split(FIELD_CODES.sub('', entry['Desktop Entry']['Exec'])) if part]


class AppIndex:
    def __init__(self, load=True):
        self.apps = []  # [{'name', 'id'}]
        self.lock = threading.Lock()
        try:
            self.apps = json.loads(CACHE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if load:
            threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self):
        try:
            apps = windows_apps() if WINDOWS else linux_apps()
        except Exception:
            return
        with self.lock:
            self.apps = apps
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(apps), encoding='utf-8')
        except OSError:
            pass

    def match(self, spoken):
        """-> {'name', 'id'} or None."""
        want = normal(spoken)
        if not want:
            return None
        want = ALIASES.get(want, want)
        with self.lock:
            apps = list(self.apps)
        names = {normal(a['name']): a for a in apps if 'uninstall' not in a['name'].lower() and 'readme' not in a['name'].lower()}
        if want in names:
            return names[want]
        # Whole spoken words inside a name ("teams" -> "Microsoft Teams"); the shortest such name wins.
        words = want.split()
        inside = [n for n in names if all(re.search(rf'\b{re.escape(w)}', n) for w in words)]
        if inside:
            return names[min(inside, key=len)]
        close = difflib.get_close_matches(want, list(names), n=1, cutoff=.75)
        if close:
            return names[close[0]]
        for n in names:  # one misheard word: compare word by word ("vishual studio" -> "visual studio code")
            if len(words) >= 2 and sum(bool(difflib.get_close_matches(w, n.split(), cutoff=.75)) for w in words) == len(words):
                return names[n]
        if want in BUILTIN and (WINDOWS or shutil.which(BUILTIN[want][0])):
            return {'name': want.title(), 'id': None, 'exe': BUILTIN[want]}
        return None

    @staticmethod
    def launch(app):
        quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if app.get('exe'):
            subprocess.Popen(app['exe'], **quiet, **({} if WINDOWS else {'start_new_session': True}))
        elif WINDOWS:
            subprocess.Popen(['explorer.exe', f"shell:AppsFolder\\{app['id']}"])
        elif shutil.which('gio'):
            subprocess.Popen(['gio', 'launch', app['id']], **quiet)  # the desktop's own launcher: env, D-Bus activation
        else:
            subprocess.Popen(linux_command(app['id']), **quiet, start_new_session=True)  # keeps running after Cameo quits


def open_app(index, registry, intent, wait=12):
    started = time.monotonic()
    elapsed = lambda: int((time.monotonic() - started) * 1000)
    spoken = intent.args.get('name', '')
    app = index.match(spoken)
    if app is None:
        return failure(intent.intent_id, 'TARGET_GONE', f'No app called "{spoken}" on this PC', 'Say the name as in the Start menu or applications menu')
    key = [w for w in normal(app['name']).split() if len(w) > 2 and w not in ('microsoft', 'windows', 'gnome')] or normal(app['name']).split()
    before = {t.id for t in registry.list()}
    index.launch(app)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        time.sleep(.4)
        for t in registry.list():
            label = normal(f'{t.title} {t.app}')
            if any(w in label for w in key) and (t.id not in before or t.state.get('active')):
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'app': app['name'], 'window': t.id, 'title': t.title[:80]}, elapsed_ms=elapsed())
    return ActionResult(intent.intent_id, 'sent', {'source': 'shell', 'app': app['name'], 'note': 'started; no window seen yet'}, elapsed_ms=elapsed())
