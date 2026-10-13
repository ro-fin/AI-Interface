"""Message shapes shared by every AETHER component (ARCHITECTURE.md §5).

Standard-library dataclasses instead of pydantic (docs/AETHER-WINDOWS.md): same fields, same
JSON, validated on construction. Every message serialises to {"type": ..., "v": 1, ...}.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import itertools
import time

V = 1
TARGET_KINDS = {'window', 'browser_window', 'tab', 'terminal'}
SOURCES = {'voice', 'gesture', 'hud', 'cli', 'replay'}
STATUSES = {'requested', 'sent', 'verified', 'unverified', 'pending_user', 'failed', 'blocked', 'cancelled'}
ERROR_CODES = {'TARGET_GONE', 'TARGET_AMBIGUOUS', 'NO_EFFECT', 'PAUSED', 'BUSY_RUNNING', 'BUFFER_MISMATCH', 'BRIDGE_OFFLINE',
               'SESSION_UNSUPPORTED', 'DEVICE_UNAVAILABLE', 'NOT_A_COMMAND', 'CONFLICT', 'TIMEOUT', 'INTERNAL'}
ACTIONS = {'browser.focus_or_open', 'tab.new', 'tab.next', 'tab.prev', 'tab.close',
           'window.maximize', 'window.minimize', 'window.restore', 'window.activate',
           'term.open', 'term.draft.set', 'term.draft.append', 'term.draft.undo_word', 'term.draft.discard',
           'term.insert', 'term.execute', 'term.interrupt', 'term.clear_line',
           'control.pause', 'control.resume', 'control.cancel', 'control.confirm',
           'message.draft', 'message.send', 'app.open', 'media.play', 'web.search', 'web.open',
           'browser.edge_open', 'browser.edge_close', 'screen.capture', 'volume.up', 'volume.down', 'session.lock', 'session.sleep',
           'display.brightness_up', 'display.brightness_down', 'volume.set', 'display.brightness_set'}

_ids = itertools.count(1)


def new_id(prefix):
    return f'{prefix}-{next(_ids):06d}'


class ContractError(ValueError):
    pass


def _check(condition, message):
    if not condition:
        raise ContractError(message)


@dataclass
class Target:
    id: str
    kind: str
    app: str
    title: str
    geometry: dict
    state: dict
    parent: str | None = None
    seen_at: float = field(default_factory=time.time)
    type: str = 'target'
    v: int = V

    def __post_init__(self):
        _check(self.kind in TARGET_KINDS, f'unknown target kind {self.kind!r}')
        _check(self.id.split(':', 1)[0] in ('win', 'tab', 'term'), f'bad target id {self.id!r}')


@dataclass
class InputEvent:
    source: str
    kind: str
    text: str = ''
    confidence: float = 1.0
    gesture: dict | None = None
    pointer: dict | None = None
    command: dict | None = None  # additive (§5.9): parsed voice command {'action', 'target', 'via'}
    live: bool = True
    t_start: float = field(default_factory=time.time)
    t_end: float = field(default_factory=time.time)
    input_id: str = ''
    type: str = 'input'
    v: int = V

    def __post_init__(self):
        _check(self.source in SOURCES, f'unknown input source {self.source!r}')
        _check(self.kind in ('command', 'dictation', 'gesture', 'ui'), f'unknown input kind {self.kind!r}')
        self.input_id = self.input_id or new_id(self.source)


@dataclass
class Intent:
    action: str
    target: str | None
    target_basis: str = 'explicit'
    args: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    requires_confirm: bool = False
    intent_id: str = ''
    type: str = 'intent'
    v: int = V

    def __post_init__(self):
        _check(self.action in ACTIONS, f'unknown action {self.action!r}')
        _check(self.target_basis in ('explicit', 'gesture_lock', 'context', 'default'), 'bad target basis')
        self.intent_id = self.intent_id or new_id('int')


@dataclass
class ActionResult:
    intent_id: str
    status: str
    evidence: dict = field(default_factory=dict)
    error: dict | None = None
    elapsed_ms: int = 0
    action_id: str = ''
    type: str = 'action_result'
    v: int = V

    def __post_init__(self):
        _check(self.status in STATUSES, f'unknown status {self.status!r}')
        if self.error is not None:
            _check(self.error.get('code') in ERROR_CODES, f'unknown error code {self.error.get("code")!r}')
        # Honesty rule: "verified" always cites evidence.
        _check(self.status != 'verified' or bool(self.evidence), 'verified results must carry evidence')
        self.action_id = self.action_id or new_id('act')


def failure(intent_id, code, message, recovery='', evidence=None, elapsed_ms=0):
    return ActionResult(intent_id, 'failed', evidence or {}, {'code': code, 'message': message, 'recovery': recovery}, elapsed_ms)


def to_json(message):
    return asdict(message)
