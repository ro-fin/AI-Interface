"""Browsers Cameo can open, on Windows and Linux. A Chromium browser whose profile has the Cameo Bridge
extension is preferred, so a window Cameo opens is linked for tab actions. Firefox is only a fallback
for opening pages (the Bridge extension is Chromium-only)."""
import os
from pathlib import Path
import shutil

from ..paths import WINDOWS

BRIDGE_EXTENSION = 'nknmgfbahbomajobgpmihhlomngpfmga'

if WINDOWS:
    BROWSERS = [  # (name, executable candidates, profile folder)
        ('Microsoft Edge', [r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe', r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'],
         r'%LOCALAPPDATA%\Microsoft\Edge\User Data'),
        ('Google Chrome', [r'%ProgramFiles%\Google\Chrome\Application\chrome.exe', r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
                           r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'], r'%LOCALAPPDATA%\Google\Chrome\User Data'),
        ('Brave', [r'%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe', r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe'],
         r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data'),
        ('Firefox', [r'%ProgramFiles%\Mozilla Firefox\firefox.exe'], None),
    ]
else:
    BROWSERS = [
        ('Microsoft Edge', ['microsoft-edge', 'microsoft-edge-stable'], '~/.config/microsoft-edge'),
        ('Google Chrome', ['google-chrome', 'google-chrome-stable'], '~/.config/google-chrome'),
        ('Brave', ['brave-browser', 'brave'], '~/.config/BraveSoftware/Brave-Browser'),
        ('Chromium', ['chromium', 'chromium-browser'], '~/snap/chromium/common/chromium'),
        ('Chromium', ['chromium', 'chromium-browser'], '~/.config/chromium'),
        ('Firefox', ['firefox'], None),
    ]


def expand(path):
    return Path(os.path.expanduser(os.path.expandvars(path)))


def executable(candidates):
    for candidate in candidates:
        if WINDOWS:
            path = expand(candidate)
            if path.exists():
                return path
        else:
            found = shutil.which(candidate)
            if found:
                return Path(found)
    return None


def installed():
    """[(name, executable, profile folder or None)], in order of preference."""
    found = []
    for name, candidates, profiles in BROWSERS:
        exe = executable(candidates)
        if exe:
            found.append((name, exe, expand(profiles) if profiles else None))
    return found


def bridge_profile(root):
    """The profile folder name ('Default', 'Profile 1', ...) that has the Cameo Bridge extension."""
    if root is None:
        return None
    for folder in sorted(root.glob('*')):
        for settings in ('Secure Preferences', 'Preferences'):
            try:
                if BRIDGE_EXTENSION in (folder / settings).read_text(encoding='utf-8', errors='ignore'):
                    return folder.name
            except OSError:
                continue
    return None


def find():
    """(executable, profile) to open: the browser with the Bridge first, else the first installed one.
    (None, None) when no browser is installed."""
    first = None
    for _, exe, root in installed():
        profile = bridge_profile(root)
        if profile:
            return exe, profile
        first = first or (exe, None)
    return first or (None, None)


def edge():
    """(executable, profile) for Microsoft Edge (the Bridge profile when it has one), or (None, None)."""
    for name, exe, root in installed():
        if name == 'Microsoft Edge':
            return exe, bridge_profile(root)
    return None, None


def command(exe, profile, *args):
    return [str(exe)] + ([f'--profile-directory={profile}'] if profile else []) + list(args)
