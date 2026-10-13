"""Where Cameo finds its models and keeps its state, on Windows and on Linux.

runtime/  voice models and engines (Vosk, whisper.cpp, wake word). Large, so it can live on another
          drive: set CAMEO_RUNTIME, or make `runtime` next to this package a link.
state     logs, caches, the IPC token: %LOCALAPPDATA%\\Cameo on Windows, ~/.local/state/cameo on Linux.
"""
import os
from pathlib import Path
import sys

WINDOWS = sys.platform == 'win32'
LINUX = sys.platform.startswith('linux')

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parent
HAND_MODEL = PACKAGE / 'models' / 'hand_landmarker.task'
RUNTIME = Path(os.environ.get('CAMEO_RUNTIME') or PROJECT / 'runtime')
EXE = '.exe' if WINDOWS else ''


def screenshots_dir():
    """Pictures/Screenshots, where Windows' own Win+PrtScn saves too (Pictures may be redirected to OneDrive)."""
    home = Path.home()
    candidates = [home / 'Pictures']
    if WINDOWS and os.environ.get('OneDrive'):
        candidates.append(Path(os.environ['OneDrive']) / 'Pictures')
    pictures = next((path for path in candidates if path.is_dir()), home)
    return pictures / 'Screenshots'


def inbox_dir():
    """Where files and folders dropped from other devices land (CAMEO_INBOX overrides). Received media can be
    large, so on Windows a second drive D: is preferred over the system drive when there is one."""
    if os.environ.get('CAMEO_INBOX'):
        return Path(os.environ['CAMEO_INBOX'])
    if WINDOWS and Path('D:/').is_dir():
        return Path('D:/Cameo/Inbox')
    return Path.home() / 'Cameo' / 'Inbox'


def state_dir():
    if os.environ.get('CAMEO_STATE_DIR'):
        return Path(os.environ['CAMEO_STATE_DIR'])
    if WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Cameo'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'cameo'
