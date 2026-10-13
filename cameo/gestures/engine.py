"""Gesture state machine (ARCHITECTURE.md W4, §5.4, §8.3). Pure: frames in, InputEvents out.

Frame (from the vision worker): {'t', 'hand': bool, 'conf', 'x', 'y' (index tip, mirrored, 0..1),
'pinch' (thumb-index distance / hand scale), 'palm' (open palm pose), 'speed' (screen widths/s)}.

Vocabulary: engage (8 frames in the active box) - point moves the reticle - 300 ms dwell over a
window locks it - pinch tap over a dock button presses it - pinch hold 600 ms over a consequential
button confirms it - open palm held 1.0 s pauses, 1.5 s resumes (latched) - loss freezes, then
cancels after 1.5 s.
"""
from ..contracts import InputEvent
from .filters import OneEuro

ENGAGE_FRAMES = 8
ACTIVE_BOX = (.2, .2, .8, .8)          # inner 60 % of the camera frame maps to the whole screen
DWELL = .3
PINCH_ON, PINCH_OFF = .25, .35        # hysteresis
TAP_MAX, HOLD_MIN, CONFIRM_HOLD = .35, .5, .6
PAUSE_HOLD, RESUME_HOLD, LATCH_RELEASE = 1.0, 1.5, .5
PALM_STILL = .05                      # screen widths per second
LOSS_RESET = 1.5
MIN_CONFIDENCE = .6


class GestureEngine:
    def __init__(self, emit, targets_at, screen=(1920, 1080), consequential=('dock:tab.close',), live=True):
        """targets_at(x, y) -> 'dock:<action>' for a HUD button, 'win:0x..' for a window, or None."""
        self.emit, self.targets_at, self.screen = emit, targets_at, screen
        self.consequential, self.live = set(consequential), live
        self.fx, self.fy = OneEuro(1.5, .02), OneEuro(1.5, .02)
        self.paused = False
        self.reset()
        self.latched = False
        self.latch_clear_since = None

    def reset(self):
        self.engaged = False
        self.in_box = 0
        self.last_seen = None
        self.pointer = None
        self.hover = None
        self.hover_since = None
        self.locked = None
        self.pinched = False
        self.pinch_start = None
        self.pinch_on = None
        self.hold_reported = False
        self.palm_since = None
        self.fx.reset()
        self.fy.reset()

    def event(self, gesture, t):
        pointer = {'x': self.pointer[0], 'y': self.pointer[1], 'fresh': True} if self.pointer else None
        self.emit(InputEvent(source='gesture', kind='gesture', gesture=gesture, pointer=pointer, live=self.live, t_start=t, t_end=t))

    def update(self, frame):
        t = frame['t']
        if not frame.get('hand') or frame.get('conf', 1) < MIN_CONFIDENCE:
            return self.lost(t)
        self.last_seen = t
        x0, y0, x1, y1 = ACTIVE_BOX
        inside = x0 <= frame['x'] <= x1 and y0 <= frame['y'] <= y1
        if not self.engaged:
            self.in_box = self.in_box + 1 if inside else 0
            if self.in_box >= ENGAGE_FRAMES:
                self.engaged = True
                self.event({'name': 'engaged'}, t)
            return
        # Map the active box to the screen and smooth it.
        sx = min(1, max(0, (frame['x'] - x0) / (x1 - x0))) * (self.screen[0] - 1)
        sy = min(1, max(0, (frame['y'] - y0) / (y1 - y0))) * (self.screen[1] - 1)
        self.pointer = (round(self.fx(sx, t)), round(self.fy(sy, t)))
        self.track_palm(frame, t)
        if self.paused:
            return  # only the resume palm is honoured while paused
        self.track_pinch(frame, t)
        if not self.pinched:
            self.track_dwell(t)

    def lost(self, t):
        # Loss is never a release: holds freeze, then everything is cancelled after LOSS_RESET.
        if self.last_seen is not None and t - self.last_seen > LOSS_RESET:
            if self.engaged or self.pinched:
                self.event({'name': 'cancelled', 'reason': 'tracking lost'}, t)
            latched, paused = self.latched, self.paused
            self.reset()
            self.latched, self.paused = latched, paused
            self.last_seen = None
        if self.latched:
            self.latch_clear_since = self.latch_clear_since or t
            if t - self.latch_clear_since >= LATCH_RELEASE:
                self.latched = False

    def track_palm(self, frame, t):
        still_palm = frame.get('palm') and frame.get('speed', 0) < PALM_STILL and not self.pinched
        if not still_palm:
            self.palm_since = None
            if self.latched:
                self.latch_clear_since = self.latch_clear_since or t
                if t - self.latch_clear_since >= LATCH_RELEASE:
                    self.latched = False
            return
        self.latch_clear_since = None
        if self.latched:
            return  # the pose must be released before another toggle
        self.palm_since = self.palm_since or t
        if t - self.palm_since >= (RESUME_HOLD if self.paused else PAUSE_HOLD):
            self.paused = not self.paused
            self.latched = True
            self.palm_since = None
            self.cancel_pinch(t)
            self.event({'name': 'palm_resume' if not self.paused else 'palm_pause'}, t)

    def track_dwell(self, t):
        target = self.targets_at(*self.pointer)
        if target != self.hover:
            self.hover, self.hover_since = target, t
            return
        if target and target.startswith('win:') and target != self.locked and t - self.hover_since >= DWELL:
            self.locked = target
            self.event({'name': 'dwell_lock', 'target': target}, t)

    def track_pinch(self, frame, t):
        pinch = frame.get('pinch', 1)
        if not self.pinched and pinch < PINCH_ON:
            self.pinched, self.pinch_start, self.hold_reported = True, t, False
            self.pinch_on = self.targets_at(*self.pointer)
        elif self.pinched and pinch > PINCH_OFF:
            self.release_pinch(t)
        elif self.pinched and not self.hold_reported and t - self.pinch_start >= HOLD_MIN:
            self.hold_reported = True
            self.event({'name': 'hold_started', 'on': self.pinch_on}, t)

    def release_pinch(self, t):
        held = t - self.pinch_start
        on = self.pinch_on
        still_on = self.targets_at(*self.pointer) == on
        self.pinched, self.pinch_start, self.pinch_on = False, None, None
        if held <= TAP_MAX and not self.hold_reported:
            if on and on.startswith('dock:') and on not in self.consequential:
                self.event({'name': 'pinch_tap', 'on': on}, t)
        elif held >= HOLD_MIN:
            # A hold commits only on release, only over the same valid option, and never also taps.
            needed = CONFIRM_HOLD if on in self.consequential else HOLD_MIN
            if on and on.startswith('dock:') and still_on and held >= needed:
                self.event({'name': 'pinch_hold', 'on': on}, t)
            else:
                self.event({'name': 'cancelled', 'reason': 'hold released away from its button'}, t)
        # 0.35-0.5 s: the dead band does nothing.

    def cancel_pinch(self, t):
        if self.pinched:
            self.pinched, self.pinch_start, self.pinch_on = False, None, None
            self.event({'name': 'cancelled', 'reason': 'pinch cancelled'}, t)
