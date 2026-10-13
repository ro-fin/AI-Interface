"""Cameo Bridge native-messaging host. Started by the browser for the Cameo Bridge extension only
(allowed_origins). Relays browser messages to the Cameo core over the authenticated local channel
(named pipe on Windows, Unix socket on Linux)."""
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cameo.ipc import connect  # noqa: E402

if sys.platform == 'win32':
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
write_lock = threading.Lock()
state = {'core': None, 'hello': None}


def read_browser():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    size = struct.unpack('<I', raw)[0]
    if size > 1024 * 1024:
        return None
    return json.loads(sys.stdin.buffer.read(size).decode('utf-8'))


def write_browser(message):
    data = json.dumps(message).encode('utf-8')
    with write_lock:
        sys.stdout.buffer.write(struct.pack('<I', len(data)) + data)
        sys.stdout.buffer.flush()


def core_link():
    """Keeps a connection to the core; requests from the core go to the browser."""
    while True:
        try:
            core = connect()
            core.send({'role': 'browser'})
            state['core'] = core
            if state['hello']:
                core.send(state['hello'])
            while True:
                request = core.recv()
                # The core may only ask for the tab operations the extension implements.
                if isinstance(request, dict) and request.get('op') in ('snapshot', 'create', 'activate', 'remove', 'page', 'open'):
                    write_browser(request)
        except Exception:  # core not running yet, restarted, or a stale key: try again
            state['core'] = None
            time.sleep(1)


threading.Thread(target=core_link, daemon=True).start()
while True:
    message = read_browser()
    if message is None:
        break  # the browser closed the port
    if message.get('event') == 'hello':
        state['hello'] = message
    core = state['core']
    if core is not None:
        try:
            core.send(message)
        except OSError:
            state['core'] = None
