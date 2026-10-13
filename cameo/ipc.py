"""Local IPC (ARCHITECTURE.md §4.9): an HMAC-authenticated per-launch key over a per-user channel that
has no TCP port, so web pages and other users cannot connect.

Windows: a named pipe \\\\.\\pipe\\cameo-<user>.  Linux: a Unix socket in $XDG_RUNTIME_DIR, mode 0600.
One Cameo per user: a second one finds the channel taken and gets PermissionError.
"""
import getpass
import os
from pathlib import Path
import secrets
import socket
from multiprocessing.connection import Client, Listener

from .paths import WINDOWS, state_dir

NAME = os.environ.get('CAMEO_IPC_NAME') or 'cameo-' + ''.join(ch for ch in getpass.getuser() if ch.isalnum())
FAMILY = 'AF_PIPE' if WINDOWS else 'AF_UNIX'
ADDRESS = rf'\\.\pipe\{NAME}' if WINDOWS else str(Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp') / f'{NAME}.sock')


def token_path():
    return state_dir() / 'token'


def new_token():
    """Created at each launch; the file lives in the user's own profile, readable only by them."""
    token = secrets.token_bytes(32)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(token)
    if not WINDOWS:
        os.chmod(path, 0o600)
    return token


def read_token():
    return token_path().read_bytes()


def listen(authkey):
    """The single-instance server end. Windows refuses a second pipe owner by itself; a Unix socket file
    can outlive a crashed Cameo, so it is removed only when nothing answers on it."""
    if WINDOWS:
        return Listener(ADDRESS, family=FAMILY, authkey=authkey)
    if os.path.exists(ADDRESS):
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(ADDRESS)
            raise PermissionError(f'Cameo is already running ({ADDRESS})')
        except (ConnectionRefusedError, FileNotFoundError):
            os.unlink(ADDRESS)
        finally:
            probe.close()
    old = os.umask(0o177)
    try:
        return Listener(ADDRESS, family=FAMILY, authkey=authkey)
    finally:
        os.umask(old)


def connect():
    return Client(ADDRESS, family=FAMILY, authkey=read_token())
