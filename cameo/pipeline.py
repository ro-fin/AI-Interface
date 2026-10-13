"""Intent pipeline: parse -> resolve target (registry + context) -> fusion -> policy (ARCHITECTURE.md §4.5, §5.5, W5, W9).

Deterministic, no LLM. submit(InputEvent) returns decisions for the runtime:
  ('run', Intent)          execute through the matching executor
  ('reject', code, text)   show why nothing happened
  ('card', dict)           a pending conflict waiting for "do it" or "cancel"
  ('state', dict)          mode / selection changes for the HUD
"""
import time

from .contracts import Intent

LOCK_FRESH = 1.5
MERGE_WINDOW = 1.2
CONFLICT_WINDOW = .8
ALWAYS_ALLOWED = {'control.resume', 'control.pause', 'control.cancel'}


class IntentPipeline:
    def __init__(self, registry, minimized=lambda: None, clock=time.monotonic):
        self.registry = registry
        self.minimized = minimized          # peek at the executor's minimize stack
        self.clock = clock
        self.paused = False
        self.selected = None                # last locked or acted-on target (shown in the target chip)
        self.lock = None                    # (target, time) from the gesture dwell lock
        self.pending = None
        self.recent = []                    # (time, action, target, source, intent) for fusion
        self.seen = set()

    # -- entry -------------------------------------------------------------------------------
    def submit(self, event):
        if event.input_id in self.seen:
            return []                       # idempotency by input_id: one input, at most one action
        self.seen.add(event.input_id)
        now = self.clock()
        if event.source == 'gesture' and not event.command:
            return self.gesture(event, now)
        if event.command:
            return self.command(event.command['action'], event.command.get('target'), event, now)
        return []

    def gesture(self, event, now):
        g = event.gesture or {}
        name = g.get('name')
        if name == 'dwell_lock':
            self.lock = (g['target'], now)
            self.selected = g['target']
            return [('state', {'selected': self.selected, 'lock': g['target']})]
        if name == 'palm_pause':
            return self.command('control.pause', None, event, now)
        if name == 'palm_resume':
            return self.command('control.resume', None, event, now)
        if name == 'cancelled':
            return [('state', {'cancelled': g.get('reason', '')})]
        if name in ('pinch_tap', 'pinch_hold') and str(g.get('on', '')).startswith('dock:'):
            action = g['on'][5:]
            target = 'selected' if action.startswith('window.') else 'browser' if action == 'browser.focus_or_open' else None
            return self.command(action, target, event, now)
        return []

    # -- commands ----------------------------------------------------------------------------
    def command(self, action, spec, event, now):
        if action == 'control.pause':
            self.paused, self.pending = True, None
            return [('state', {'mode': 'paused'})]
        if action == 'control.resume':
            self.paused = False
            return [('state', {'mode': 'active'})]
        if action == 'control.cancel':
            had, self.pending = self.pending, None
            return [('state', {'pending': None, 'cancelled': 'pending action' if had else 'nothing pending'})]
        if self.paused:
            return [('reject', 'PAUSED', 'Controls are paused. Say "resume controls" or press Ctrl+Alt+P.')]
        if action == 'control.confirm':
            if not self.pending:
                return [('reject', 'NOT_A_COMMAND', 'Nothing pending.')]
            intent, self.pending = self.pending['intent'], None
            return self.accept(intent, event.source, now)
        target, basis, problem = self.resolve(spec, now)
        if problem:
            return [('reject',) + problem]
        args = dict((event.command or {}).get('args') or {})
        intent = Intent(action, target, basis, args=args, inputs=[event.input_id])
        return self.fuse(intent, event.source, now)

    def await_confirm(self, intent, message):
        """An outward action (sending a message) waits for "send it" / "do it" or "cancel"."""
        self.pending = {'intent': intent, 'conflicts_with': None}
        return [('card', {'kind': 'confirm', 'options': [intent.action], 'message': message})]

    def resolve(self, spec, now):
        """-> (target, basis, None) or (None, None, (code, message))."""
        if spec is None:
            return None, 'default', None
        if spec.startswith('win:'):
            return (spec, 'explicit', None) if self.registry.get(spec) else (None, None, ('TARGET_GONE', 'That window is no longer available.'))
        if spec in ('deictic', 'selected'):
            if self.lock and now - self.lock[1] <= LOCK_FRESH and self.registry.get(self.lock[0]):
                return self.lock[0], 'gesture_lock', None
            if self.selected and self.registry.get(self.selected):
                return self.selected, 'context', None
            return None, None, ('TARGET_AMBIGUOUS', 'Which window? Point at it first.')
        if spec == 'minimized':
            target = self.minimized()
            if target:
                return target, 'context', None
            return None, None, ('TARGET_GONE', 'Nothing minimized by Cameo. Point at a window.')
        if spec == 'browser':
            browsers = [t for t in self.registry.list() if t.kind == 'browser_window']
            active = [t for t in browsers if t.state.get('active')]
            chosen = (active or browsers)[:1]
            return (chosen[0].id if chosen else None), 'explicit', None
        return None, None, ('INTERNAL', f'unknown target spec {spec!r}')

    def fuse(self, intent, source, now):
        self.recent = [r for r in self.recent if now - r[0] <= MERGE_WINDOW]
        for then, action, target, other, earlier in self.recent:
            if other == source:
                continue                    # same-modality repeats are deliberate: always two actions
            if action == intent.action and target == intent.target:
                earlier.inputs.extend(intent.inputs)
                return [('state', {'merged': earlier.intent_id, 'inputs': earlier.inputs})]
            if now - then <= CONFLICT_WINDOW:
                self.pending = {'intent': intent, 'conflicts_with': earlier.intent_id}
                return [('card', {'kind': 'conflict', 'options': [earlier.action, intent.action], 'message': 'Say "do it" to run the second, or "cancel".'})]
        return self.accept(intent, source, now)

    def accept(self, intent, source, now):
        self.recent.append((now, intent.action, intent.target, source, intent))
        if intent.target and intent.target.startswith('win:'):
            self.selected = intent.target
        return [('run', intent)]
