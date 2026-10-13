"""Console-less start (pythonw on Windows, a desktop launcher on Linux) gives a process no stdout/stderr.
Native libraries (MediaPipe, Vosk/Kaldi) still write status lines to file descriptors 1 and 2 and fail
when those are invalid. This points both the Python streams and the OS-level descriptors at a log file."""
import os
import sys

from .paths import state_dir


def ensure_streams():
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log = open(folder / 'cameo.log', 'a', encoding='utf-8', buffering=1)
    for fd in (1, 2):
        try:
            os.dup2(log.fileno(), fd)
        except OSError:
            pass
    sys.stdout = sys.stderr = log
