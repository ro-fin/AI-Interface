"""Levels: volume and brightness set to absolute values with read-back (docs/SIGN-PROTOCOL-PLAN.md, R2 and R4).

One session at a time. At engage the current level is read (the base); every control step sets base + delta (the
engine measures delta from the hand, in fractions of the range); the worker thread coalesces steps (the newest target
wins, at most RATE sets per second) and reads every set back. Commit keeps the target, cancel sets the base again;
either way the level is read back once more and reported: 'verified' when the device reports the target (within
TOLERANCE), 'unverified' when it reports something else, a failure when it has no such control.

The runtime calls engage / control / commit / cancel on the UI thread; snapshot() feeds the HUD bar; on_done(intent,
result) receives the final result on the worker thread (the runtime queues it to the UI).
"""
import threading
import time

from .contracts import ActionResult, Intent, failure
from .control import system

RATE = 10            # sets per second at most (Core Audio and WMI answer in 5-30 ms; the eye cannot follow faster)
MIN_STEP = .005      # a target change smaller than half a percent is not sent
TOLERANCE = {'volume': .011, 'brightness': .051}   # read-back within this of the target counts as reached
SETTLE = .6          # seconds to wait for a fading panel to report its final brightness

QUANTITIES = {
    # quantity: (read 0..1 or None, set 0..1 -> bool, intent action, label)
    'volume': (lambda: system.volume(), lambda v: system.set_volume(v), 'volume.set', 'volume'),
    'brightness': (lambda: _percent(system.brightness()), lambda v: system.set_brightness(round(v * 100)), 'display.brightness_set', 'brightness'),
}


def _percent(value):
    return None if value is None else value / 100


class Session:
    def __init__(self, quantity, delta):
        self.quantity, self.delta = quantity, delta
        self.base = self.actual = self.sent = None
        self.state = 'controlling'   # -> 'commit' | 'cancel' (asked) -> 'done'
        self.reason = None
        self.started = time.monotonic()
        self.ended_at = None
        self.error = None
        action = QUANTITIES[quantity][2]
        self.intent = Intent(action, None, 'default', args={'via': 'sign'})

    def target(self):
        if self.base is None:
            return None
        if self.state == 'cancel':
            return self.base
        return max(0.0, min(1.0, self.base + self.delta))


class Levels:
    def __init__(self, on_done, log=None):
        self.on_done, self.log = on_done, log
        self.lock = threading.Condition()
        self.sessions = []           # the current one last; earlier ones only while they finish
        self.stop = threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    # -- UI thread ---------------------------------------------------------------------------------
    def engage(self, quantity, delta):
        with self.lock:
            session = Session(quantity, delta)
            self.sessions.append(session)
            self.lock.notify()
        if self.log:
            self.log.write(session.intent)
        return session

    def control(self, delta):
        with self.lock:
            session = self.current()
            if session and session.state == 'controlling':
                session.delta = delta
                self.lock.notify()

    def end(self, commit, delta=None, reason=None):
        with self.lock:
            session = self.current()
            if session and session.state == 'controlling':
                if delta is not None and commit:
                    session.delta = delta
                session.state, session.reason = ('commit' if commit else 'cancel'), reason
                self.lock.notify()

    def current(self):
        return next((s for s in reversed(self.sessions) if s.state != 'done'), None)

    def snapshot(self):
        """For the HUD bar: {'quantity', 'base', 'target', 'actual', 'state'} of the newest session, or None."""
        with self.lock:
            session = self.sessions[-1] if self.sessions else None
            if session is None or (session.state == 'done' and time.monotonic() - session.ended_at > 1.2):
                return None
            return {'quantity': session.quantity, 'base': session.base, 'target': session.target(), 'actual': session.actual,
                    'state': session.state, 'error': session.error}

    def close(self):
        self.stop.set()
        with self.lock:
            self.lock.notify()

    # -- worker thread -----------------------------------------------------------------------------
    def run(self):
        last_set = 0.0
        while not self.stop.is_set():
            with self.lock:
                session = next((s for s in self.sessions if s.state != 'done'), None)
                if session is None:
                    self.lock.wait(.5)
                    continue
                state = session.state
            read, write, _, _ = QUANTITIES[session.quantity]
            if session.base is None:
                session.base = read()
                if session.base is None:
                    self.finish(session, failure(session.intent.intent_id, 'SESSION_UNSUPPORTED',
                                                 f'This computer has no {session.quantity} control Cameo can call'))
                    continue
                session.actual = session.base
            target = session.target()
            if state == 'controlling':
                wait = 1 / RATE - (time.monotonic() - last_set)
                if session.sent is not None and abs(target - session.sent) < MIN_STEP:
                    with self.lock:
                        if session.state == 'controlling':
                            self.lock.wait(.2)
                    continue
                if wait > 0:
                    time.sleep(wait)
                    continue
                self.apply(session, write, read, target)
                last_set = time.monotonic()
                continue
            # Commit or cancel: the final set, then the read-back that decides the reported status.
            if session.sent is None or abs(target - session.sent) >= MIN_STEP / 2:
                self.apply(session, write, read, target)
            self.finish(session, self.verify(session, read, target))

    def apply(self, session, write, read, target):
        if session.quantity == 'volume' and target > (session.base or 0) and system.muted():
            system.set_mute(False)  # raising the volume of a muted device: what the volume keys do too
        if write(target):
            session.sent = target
            actual = read()
            if actual is not None:
                session.actual = actual
        else:
            session.error = 'the device did not accept the level'

    def verify(self, session, read, target):
        tolerance = TOLERANCE[session.quantity]
        started = time.monotonic()
        actual = read()
        while actual is not None and abs(actual - target) > tolerance and time.monotonic() - started < SETTLE:
            time.sleep(.05)
            actual = read()  # some panels fade to the new brightness
        session.actual = actual
        evidence = {'source': system.SOURCE, session.quantity: None if actual is None else round(actual * 100),
                    'target': round(target * 100), 'base': round(session.base * 100), 'outcome': session.state, 'reason': session.reason}
        elapsed = int((time.monotonic() - session.started) * 1000)
        if session.error:
            return failure(session.intent.intent_id, 'NO_EFFECT', session.error, elapsed_ms=elapsed)
        status = 'verified' if actual is not None and abs(actual - target) <= tolerance else 'unverified'
        return ActionResult(session.intent.intent_id, status, evidence, elapsed_ms=elapsed)

    def finish(self, session, result):
        with self.lock:
            session.state, session.ended_at = 'done', time.monotonic()
            if result.error:
                session.error = result.error.get('message')
            self.sessions = [s for s in self.sessions if s.state != 'done' or s is self.sessions[-1]]
        self.on_done(session.intent, result)
