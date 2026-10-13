"""The sign machine (docs/SIGN-PROTOCOL-PLAN.md): one lifecycle for measured signs, one sign in focus at a time.

    IDLE -> ARMED -> ENGAGED -> CONTROLLING -> COMMITTED | CANCELLED -> (focus kept until the arm pose ends) -> COOLDOWN

A spec says what arms it, what engages it, what it controls and how it ends; the machine runs every spec the same
way (R1), and while one spec holds the focus no other sign acts and the pointer holds still (R6). Every end is named:
a spec without a commit and a cancel rule does not exist (R7).

Specs here:
  LevelLine  a straight line drawn with index + middle sets an absolute level: target = base + gain x displacement in
             palm widths along the locked axis (R2, R3). The base is read by the runtime at engage; the engine only
             says how far the hand has moved, never guesses a level.
  Circle     a stroke that turns three quarters of a circle, fits a circle closely and is large enough.
  Hold       a pose held still, with a visible countdown between engage and commit (the sleep sign).

Events: ('level', (phase, quantity, delta, reason)) with phase 'engage' | 'control' | 'commit' | 'cancel';
('circle', +1 | -1); ('sleep', None); ('sign-cancel', (sign, reason)). Pure: no I/O, time is passed in.
"""
import collections
import math

from .strokes import Stroke, UNIT_DEFAULT

IDLE, ARMED, ENGAGED, CONTROLLING, COMMITTED, CANCELLED = 'idle', 'armed', 'engaged', 'controlling', 'committed', 'cancelled'
STROKE_POSES = ('two', 'three')    # index + middle up, thumb in or out
DROPOUT = .3                       # a detection gap shorter than this is not the hand leaving (as the engine)
IDLE_RESTART = .4                  # seconds the fingers rest before a new stroke starts from where they rest
IDLE_TRAVEL = .15                  # palm widths that still count as resting


class LevelLine:
    """A level controlled by a straight line: volume along x, brightness along y."""
    kind = 'stroke'

    def __init__(self, name, quantity, axis, gain, engage=.6, slant=.5, bend=.2, turn=math.radians(35), still=.5,
                 still_travel=.15, lost_within=.3, cancel_pose='fist', grace=0.0, cooldown=.4, min_change=.005):
        self.name, self.quantity, self.axis, self.gain = name, quantity, axis, gain
        self.engage_pw, self.slant, self.bend, self.turn = engage, slant, bend, turn
        self.still, self.still_travel, self.lost_within = still, still_travel, lost_within
        self.cancel_pose, self.grace, self.cooldown, self.min_change = cancel_pose, grace, cooldown, min_change

    def engages(self, stroke):
        axis, along, across, bend = stroke.line_fit()
        return (axis == self.axis and along >= self.engage_pw and across <= self.slant * along and abs(stroke.turn) <= self.turn
                and bend <= self.bend * along)

    def direction(self, delta):
        return ('right' if delta > 0 else 'left') if self.axis == 'x' else ('up' if delta > 0 else 'down')

    def delta(self, stroke):
        return self.gain * stroke.displacement(self.axis)


class Circle:
    kind = 'stroke'

    def __init__(self, name='circle', turn=1.5 * math.pi, radius=.35, residual=.2, cooldown=1.5):
        self.name, self.turn, self.radius, self.residual, self.cooldown = name, turn, radius, residual, cooldown

    def engages(self, stroke):
        if abs(stroke.turn) < self.turn:
            return False
        radius, residual = stroke.circle_fit()
        return radius >= self.radius and residual <= self.residual * radius


class Hold:
    """A pose held still: engage (countdown shown) after `engage`, commit after `commit`; moving or another pose cancels."""
    kind = 'hold'

    def __init__(self, name, pose, event, engage=1.0, commit=1.5, travel=.2, cooldown=1.0):
        self.name, self.pose, self.event = name, pose, event
        self.engage_s, self.commit_s, self.travel, self.cooldown = engage, commit, travel, cooldown


# The user's choices (2026-09-28): volume 25 % and brightness 40 % per palm width; revert on early loss or a fist;
# the line is the only volume sign (the pinch-hold wrist dial is gone); sleep is two-stage.
SPECS = (
    LevelLine('volume-line', 'volume', 'x', .25),
    LevelLine('brightness-line', 'brightness', 'y', .40),
    Circle(),
    Hold('sleep', 'pinky', 'sleep', engage=1.0, commit=1.5),
)


class SignMachine:
    def __init__(self, specs=SPECS):
        self.specs = list(specs)
        self.cooldown = {}     # spec name -> time before which it cannot engage again
        self.last_seen = -100
        self.reset()

    def reset(self):
        self.active = None     # the spec in focus
        self.phase = IDLE
        self.stroke = None
        self.hold = None       # {'since', 'anchor'} while a Hold spec's pose is held
        self.engaged_at = -100
        self.sent = None       # last delta sent for a level
        self.recent = collections.deque()  # (t, tip) while controlling, for the stillness commit
        self.break_at = None   # when the stroke pose was left while controlling (grace)
        self.arming = None     # (sign, 0..1) for the HUD's progress arc

    @property
    def focus(self):
        return self.active is not None

    def update(self, hand, now):
        """hand: {'pose', 'tip': (x, y), 'palm': (x, y), 'unit', 'aspect'} or None when no hand is seen."""
        events = []
        if hand is None:
            if now - self.last_seen > DROPOUT and (self.active or self.stroke or self.hold):
                self.lost(events)
            return events
        self.last_seen = now
        unit = hand.get('unit') or UNIT_DEFAULT
        aspect = hand.get('aspect') or 1.0
        pose = hand['pose']
        self.arming = None
        if self.active is not None:
            self.follow(pose, hand, unit, now, events)
            return events
        if pose in STROKE_POSES:
            if self.stroke is None:
                # Start where the pose first appeared: the engine's two-frame debounce must not shorten every line.
                origin = hand.get('origin') or hand['tip']
                self.stroke = Stroke(origin, unit, aspect, now)
                self.stroke.add(hand['tip'], unit)
                self.stroke.rest = (hand['tip'], now)
            else:
                self.stroke.add(hand['tip'], unit)
                self.rest(now)
            self.phase = ARMED
            for spec in self.specs:
                if spec.kind == 'stroke' and now >= self.cooldown.get(spec.name, -100) and spec.engages(self.stroke):
                    self.engage(spec, now, events)
                    break
        else:
            self.stroke = None
        self.track_holds(pose, hand, unit, aspect, now, events)
        if self.active is None and self.stroke is None and self.hold is None:
            self.phase = IDLE
        return events

    def rest(self, now):
        """Fingers resting before a stroke: the stroke restarts from where they rest, so a drift is not part of the line."""
        s = self.stroke
        point, since = s.rest
        if s.pw(point, s.tip) > IDLE_TRAVEL:
            s.rest = (s.tip, now)
        elif now - since >= IDLE_RESTART and s.pw(s.start, s.tip) > IDLE_TRAVEL:
            s.restart(s.tip)
            s.rest = (s.tip, now)

    # -- stroke signs --------------------------------------------------------------------------
    def engage(self, spec, now, events):
        self.active, self.engaged_at = spec, now
        if isinstance(spec, Circle):
            self.stroke.done = list(self.stroke.path)
            self.phase = COMMITTED
            events.append(('circle', 1 if self.stroke.turn > 0 else -1))
            return
        self.phase = CONTROLLING
        delta = spec.delta(self.stroke)
        self.sent = delta
        self.stroke.line = spec.direction(delta)
        self.stroke.done = self.stroke.path
        self.recent = collections.deque([(now, self.stroke.tip)])
        events.append(('level', ('engage', spec.quantity, round(delta, 4), None)))

    def follow(self, pose, hand, unit, now, events):
        spec = self.active
        if isinstance(spec, Hold):
            self.follow_hold(spec, pose, hand, unit, now, events)
            return
        if self.phase in (COMMITTED, CANCELLED):
            # One sign per gesture: the value is frozen until the fingers leave the stroke pose.
            if pose not in STROKE_POSES:
                self.release(now)
            elif self.stroke:
                self.stroke.tip = hand['tip']
            return
        if pose not in STROKE_POSES:
            if pose == spec.cancel_pose:
                self.end(CANCELLED, 'fist', events)
                self.release(now)
                return
            # A lowered middle finger for a moment is still the line (the same grace as clicks and circles).
            if self.break_at is None:
                self.break_at = now
            if now - self.break_at < spec.grace:
                return
            self.end(COMMITTED, 'released', events)
            self.release(now)
            return
        self.break_at = None
        self.stroke.add(hand['tip'], unit)
        delta = spec.delta(self.stroke)
        if abs(delta - self.sent) >= spec.min_change:
            self.sent = delta
            self.stroke.line = spec.direction(delta) if delta else self.stroke.line
            events.append(('level', ('control', spec.quantity, round(delta, 4), None)))
        self.recent.append((now, self.stroke.tip))
        while self.recent and now - self.recent[0][0] > spec.still:
            self.recent.popleft()
        moved = sum(self.stroke.pw(a, b) for (_, a), (_, b) in zip(self.recent, list(self.recent)[1:]))
        if now - self.engaged_at >= spec.still and now - self.recent[0][0] >= spec.still - .05 and moved <= spec.still_travel:
            self.end(COMMITTED, 'still', events)

    def end(self, phase, reason, events):
        spec = self.active
        self.phase = phase
        if isinstance(spec, LevelLine):
            events.append(('level', ('commit' if phase == COMMITTED else 'cancel', spec.quantity, round(self.sent or 0, 4), reason)))

    def release(self, now):
        if self.active is not None:
            self.cooldown[self.active.name] = now + self.active.cooldown
        self.active, self.stroke, self.hold, self.break_at = None, None, None, None
        self.phase = IDLE

    def lost(self, events):
        spec = self.active
        if isinstance(spec, LevelLine) and self.phase == CONTROLLING:
            # R7 and the user's choice: lost straight after engaging = revert; later = keep the value.
            if self.last_seen - self.engaged_at < spec.lost_within:
                self.end(CANCELLED, 'hand lost', events)
            else:
                self.end(COMMITTED, 'hand left', events)
        elif isinstance(spec, Hold) and self.phase == ENGAGED:
            events.append(('sign-cancel', (spec.name, 'hand lost')))
        self.release(self.last_seen)
        self.reset()

    # -- hold signs ----------------------------------------------------------------------------
    def track_holds(self, pose, hand, unit, aspect, now, events):
        spec = next((s for s in self.specs if s.kind == 'hold' and s.pose == pose), None)
        if spec is None or now < self.cooldown.get(spec.name, -100):
            self.hold = None
            return
        palm = hand.get('palm') or hand['tip']
        if self.hold is None or self.hold['spec'] is not spec or self.moved(self.hold['anchor'], palm, unit, aspect) > spec.travel:
            self.hold = {'spec': spec, 'since': now, 'anchor': palm, 'unit': unit, 'aspect': aspect}
            return
        held = now - self.hold['since']
        self.arming = (spec.name, min(1, held / spec.commit_s))
        if held >= spec.engage_s:
            self.active, self.engaged_at, self.phase = spec, now, ENGAGED
            self.follow_hold(spec, pose, hand, unit, now, events)

    @staticmethod
    def moved(a, b, unit, aspect):
        return math.hypot(b[0] - a[0], (b[1] - a[1]) * aspect) / unit

    def follow_hold(self, spec, pose, hand, unit, now, events):
        if self.phase == COMMITTED:
            if pose != spec.pose:
                self.release(now)
            return
        h = self.hold
        palm = hand.get('palm') or hand['tip']
        if pose != spec.pose or self.moved(h['anchor'], palm, unit, h['aspect']) > spec.travel:
            events.append(('sign-cancel', (spec.name, 'moved' if pose == spec.pose else 'pose changed')))
            self.release(now)
            self.hold = None
            return
        held = now - h['since']
        self.arming = (spec.name, min(1, held / spec.commit_s))
        if held >= spec.commit_s:
            self.phase = COMMITTED
            self.arming = None
            events.append((spec.event, None))
