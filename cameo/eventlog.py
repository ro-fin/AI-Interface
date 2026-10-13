"""Per-session JSONL event log (ARCHITECTURE.md §3 Diagnostics). Every line carries `live`."""
import json
from pathlib import Path
import threading
import time

from .contracts import to_json
from .paths import state_dir


def log_root():
    return state_dir() / 'sessions'


class EventLog:
    def __init__(self, live=True, root=None):
        self.live = live
        self.dir = Path(root or log_root()) / time.strftime('%Y%m%d-%H%M%S')
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'events.jsonl'
        self.lock = threading.Lock()

    def write(self, message=None, **fields):
        record = {'t': round(time.time(), 3), 'live': self.live}
        if message is not None:
            record.update(to_json(message) if hasattr(message, '__dataclass_fields__') else message)
        record.update(fields)
        with self.lock, self.path.open('a', encoding='utf-8') as out:
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
        return record
