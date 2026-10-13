"""Classic hand signs (the user's own set, moved from Code Cameo desktop/gestures.py; see GESTURES.md).

Workflow (one camera frame at a time):
  landmarks -> PoseClassifier (3-D finger states with hysteresis) -> pose label
  pose label -> GestureEngine (debounce, dropout tolerance, timed state machine) -> events
Events are handled by the controller: move, click, right-click, open-target, arm-target,
minimize, restore, zoom, scroll, grab / drag / drop, the pinch key (thumb taps the index = open) and clap (both hands meet).
The measured signs (a line drawn with index + middle sets volume or brightness, the circle, the two-stage sleep) run in
the sign machine (signs.py); while one of them holds the focus nothing else acts (docs/SIGN-PROTOCOL-PLAN.md, R6).
Distances are in palm widths (frame 'unit'), so a sign means the same near the camera and far from it (R3).
"""
import math

from .pose import (FINGER_OFF, FINGER_ON, MIDDLE_UP_OFF, MIDDLE_UP_ON, THUMB_OFF, THUMB_ON, THUMB_UP_OFF,  # noqa: F401
                   THUMB_UP_ON, Flat, PoseClassifier, angle, describe, distance, roll)
from .signs import STROKE_POSES, SignMachine
from .strokes import UNIT_DEFAULT


class OneEuro:
    """One Euro filter: steady when the hand is still, low lag when it moves quickly."""

    def __init__(self, min_cutoff=1.0, beta=.01, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.value = self.speed = self.at = None

    @staticmethod
    def alpha(cutoff, dt):
        tau = 1 / (2 * math.pi * cutoff)
        return 1 / (1 + tau / dt)

    def __call__(self, value, now):
        if self.at is None or now <= self.at:
            self.value, self.speed, self.at = value, 0.0, now
            return value
        dt = now - self.at
        speed = (value - self.value) / dt
        a = self.alpha(self.d_cutoff, dt)
        self.speed = a * speed + (1 - a) * self.speed
        a = self.alpha(self.min_cutoff + self.beta * abs(self.speed), dt)
        self.value = a * value + (1 - a) * self.value
        self.at = now
        return self.value


# A new pose must be seen on this many consecutive frames before it replaces the current one.
STABLE_FRAMES = 2
# Missing the hand for less than this is a detection gap, not the hand leaving.
DROPOUT = .3
POINTING = ('point', 'zoom')
TWO_FINGERS = ('two', 'three')  # index + middle up, thumb folded or out: one state for timing
# Grab and move: open thumb + index + middle ('three'), then close them (fist) = pick up what is under
# the pointer; move the hand = carry it; open the fingers again = drop it there. Losing the hand drops too, marked
# 'lost': with paired devices the runtime carries it on, and the device whose camera sees the hand open receives it.
GRAB_MIN_OPEN = .12
# The second grab sign: four fingers closed, thumb out ('thumb'), held THUMB_GRAB_HOLD = pick up; open the hand
# (open palm) = drop, on this screen or, after carrying the hand to another paired device, on that one.
THUMB_GRAB_HOLD = .35
GRAB_CLOSED = ('fist', 'neutral', 'thumb')

# Left click: keep the index finger pointing and flick the middle finger up and down quickly
# (point -> two -> point). Right click: hold index and middle up for RIGHT_CLICK_HOLD.
TAP_MIN_POINT = .25
TAP_MAX_FLICK = .45
RIGHT_CLICK_HOLD = .6
# An open palm arms the window under the pointer for palm -> fist = minimize. (Opening is the pinch key's tap.)
PALM_HOLD = .45
FIST_HOLD = .5
RESTORE_WINDOW = 1.5
# Palm-closing dial: after the palm arms a window, curling the fingers (curl 0 = open .. 1 = fist,
# shown as 0..270 degrees) shrinks it; a full fist minimizes. Small natural bends below RESIZE_START are ignored.
RESIZE_ENABLED = False  # paused by the user on 2026-09-25; to be continued later (set True to bring the shrink dial back)
RESIZE_START = .12
RESIZE_STEP = .03
DIAL_POSES = ('palm', 'four', 'neutral')
# Live trace (2026-09-25): natural pointing keeps the thumb out, and moving the hand changes the
# thumb-index distance, so zoom needs a settled, still hand and a large deliberate pinch change.
ZOOM_SETTLE = .5
ZOOM_STEP = .3
ZOOM_STILL = .2      # palm widths (was .04 frame widths at a typical .2 palm width)
ZOOM_MAX_SPEED = .5  # palm widths per second above which the hand is pointing, not zooming
ZOOM_CONFIRM = 3  # consecutive frames beyond the threshold (a one-frame glitch is not a pinch)
# Stillness is measured on the palm centre: a pinch moves the fingertips but not the palm.
# Scroll: four fingers up (thumb folded), held SCROLL_HOLD, then move the hand up/down from where it
# started, like a joystick. Farther from the start = faster. Not after an open palm (that is the shrink dial).
PINCHED = .35       # thumb tip touching the index tip (pinch = distance / palm size)
SCROLL_HOLD = .3
SCROLL_DEAD = .15    # palm widths of free play around the start
# Rate by offset from the start (palm widths -> notches per second), straight lines between the points: farther = faster.
SCROLL_RATES = ((.15, 2), (.5, 6), (1.0, 14))
SCROLL_MAX = 14      # notches per second at most
# Live trace (2026-09-25): while circling, the moving middle finger read as down for 2-7 frames in 16 of 65
# drawings, which ended the drawing half way. Once a drawing is under way, a lowered middle finger shorter
# than DRAW_GRACE is still the drawing (and never pointing, so it cannot click either). Replaying that trace:
# 5 circles recognised before, 12 with this; pointing for longer (.3 s and up) still ends a drawing on purpose.
DRAW_GRACE = .2
DRAW_MOVED = .3      # palm widths of stroke travel after which two fingers are drawing, never a right click
# Pinch key (replaces point -> open palm): from pointing, the thumb closes onto the index (pinch below
# KEY_CLOSED) and opens again (above KEY_OPEN). Let go before KEY_HOLD with a still hand = a tap = open what is
# under the pointer; held past KEY_HOLD it is not a tap and nothing happens (the wrist-turn volume dial was removed on
# 2026-09-28: the line drawn left / right is the one volume sign). The pointer holds still while the thumb is down.
# Live trace (2026-09-25): the index curls to meet the thumb, so during a tap the hand often reads as a fist or
# neutral; the key is found from the pinch dipping quickly out of pointing, whatever shape is read meanwhile.
# Pointing measured .5-.7, taps dipped to .10-.25 and took .4-.9 s; fists sit at .25-.45 but come without the dip.
KEY_CLOSED = .33
KEY_OPEN = .45
KEY_CLOSE = .4       # the thumb must come from open this recently: a thumb drifting onto the index is not a key
KEY_GRACE = .4       # pointing this recently still counts (the shape may already read as a fist)
KEY_HOLD = 1.0       # held longer, the thumb on the index is not a tap: nothing opens (the wrist dial was removed)
KEY_STILL = .4       # palm widths of palm travel allowed during a tap: moving hands are pointing
KEY_MAX_SPEED = .75  # palm widths per second above which a closing thumb is just the hand travelling
KEY_BREAKS = ('palm', 'four', 'two', 'three', 'pinky')  # clearly another sign: the key is off
# Clap: two hands seen apart, then touching (or merged into one detection) within CLAP_WINDOW.
# Gap = distance between the two palm centres / hand size.
CLAP_APART = 2.0
CLAP_TOUCH = 1.0
CLAP_MERGED = 1.6    # hands this close that turn into one detection have met
CLAP_WINDOW = .6
CLAP_REPEAT = 1.5
# The main hand keeps working with a second hand in view (live trace: a second hand is often in the picture).
# Only while both are close together (about to clap) and just after, its palm / fist / key actions are held back.
TWO_HANDS_QUIET = .8
QUIET_KEEPS = ('clap', 'move', 'drag', 'drop', 'resize-end', 'level', 'sign-cancel')


class Clap:
    def __init__(self):
        self.apart_at = self.fired_at = self.last_at = -100
        self.last = None

    def update(self, hands, gap, now):
        if hands >= 2 and gap is not None:
            touching = gap < CLAP_TOUCH
            if gap > CLAP_APART:
                self.apart_at = now
            self.last, self.last_at = gap, now
        else:
            # Palms pressed together often read as one hand: that counts only straight after they were close.
            touching = self.last is not None and now - self.last_at < .25 and self.last < CLAP_MERGED
            self.last = None
        if touching and now - self.apart_at < CLAP_WINDOW and now - self.fired_at > CLAP_REPEAT:
            self.fired_at, self.apart_at = now, -100
            return True
        return False


def scroll_rate(offset):
    """Notches per second for a scroll offset in palm widths (SCROLL_RATES, linear between the points)."""
    (a, ra), = SCROLL_RATES[:1]
    for b, rb in SCROLL_RATES[1:]:
        if offset <= b:
            return ra + (rb - ra) * (offset - a) / (b - a)
        a, ra = b, rb
    return min(SCROLL_MAX, ra)


def family(pose):
    """Pointing with or without the thumb is one state for timing (holds, clicks, debounce)."""
    return 'point' if pose in POINTING else 'two' if pose in TWO_FINGERS else pose


class GestureEngine:
    def __init__(self):
        self.clap = Clap()
        self.machine = SignMachine()  # measured signs: level lines, circle, sleep (one lifecycle, one in focus)
        self.unit, self.aspect = UNIT_DEFAULT, 1.0  # palm width (frame widths) and frame height / width
        self.near_at = -100  # two hands close together, or a clap: one-hand actions wait
        self.reset()

    def reset(self):
        self.pose = 'none'
        self.since = 0
        self.last_seen = -100
        self.point_at = -100
        self.palm_at = -100
        self.fired = False
        self.baseline = None
        self.step_at = -100
        self.tap_started = None
        self.tap_raised = False
        self.candidate = None
        self.candidate_since = 0
        self.candidate_frames = 0
        self.zooming = False
        self.fist_at = -100
        self.fist_used = False  # the fist that minimized cannot also be the start of a restore
        self.pinch_open_at = -100
        self.key_prev = None
        self.key = None      # the pinch key while the thumb touches the index: {'at', 'palm'}
        self.dial_armed = False
        self.resizing = False
        self.resize_last = None
        self.zoom_since = 0
        self.zoom_anchor = None
        self.zoom_over = 0
        self.zoom_prev = None
        self.scroll_anchor = None
        self.scroll_at = -100
        self.scroll_owed = 0.0
        self.three_frames = self.two_frames = 0
        self.grabbing = False
        self.grab_pending = False
        self.arming = None
        self.origin = None   # where the fingertip was when the current pose first appeared (before the debounce)   # (sign, 0..1) of a hold in progress, for the HUD's progress arc

    @property
    def stroke(self):
        """The index + middle stroke in progress (strokes.Stroke) or None; the HUD draws it."""
        return self.machine.stroke

    def pw(self, value):
        """Frame widths -> palm widths."""
        return value / self.unit

    def update(self, frame, now):
        events = []
        hands = frame.get('hands', 1) if frame else 0
        gap = frame.get('gap') if frame else None
        if self.clap.update(hands, gap, now):
            events.append(('clap', None))
            self.near_at = now
        elif hands >= 2 and gap is not None and gap < CLAP_APART:
            self.near_at = now
        self.step(frame, now, events)
        if now - self.near_at < TWO_HANDS_QUIET:
            if any(name not in QUIET_KEEPS for name, _ in events):
                self.palm_at = self.fist_at = -100  # a clap is not a palm or a fist
            events = [event for event in events if event[0] in QUIET_KEEPS]
        return events

    def step(self, frame, now, events):
        if frame is None:
            events.extend(self.machine.update(None, now))
            # A short detection gap keeps every hold and sequence alive.
            if now - self.last_seen > DROPOUT:
                if self.resizing:
                    events.append(('resize-end', None))
                if self.grabbing:
                    # 'lost': the hand left still closed. The runtime may carry it on to another device.
                    events.append(('drop', 'lost'))
                self.reset()
            return events
        self.unit = frame.get('unit') or UNIT_DEFAULT
        self.aspect = frame.get('aspect') or 1.0
        self.arming = None
        pose = self.hold_drawing(self.key_pose(frame, frame['pose'], now, events), now)
        if now - self.last_seen > DROPOUT:
            if self.grabbing:
                events.append(('drop', 'lost'))
            self.reset()
        self.last_seen = now
        if pose != self.pose and family(pose) == family(self.pose):
            # Thumb in <-> thumb out while pointing: same state, only the zoom anchor restarts.
            self.pose, self.candidate = pose, None
        elif pose != self.pose:
            # Debounce: a single misclassified frame must not interrupt pointing or start a click.
            if pose != self.candidate:
                self.candidate, self.candidate_since, self.candidate_frames = pose, now, 0
                self.candidate_tip = (frame['x'], frame['y'])
            self.candidate_frames += 1
            if self.candidate_frames >= STABLE_FRAMES or self.pose == 'none':
                self.leave_pose(pose, events)
                self.track_tap(pose, self.candidate_since, events)
                if pose == 'fist':
                    self.fist_used = False  # a new fist
                self.pose, self.since, self.fired, self.baseline = pose, self.candidate_since, False, None
                self.origin = getattr(self, 'candidate_tip', None)
                self.zooming, self.zoom_since, self.zoom_anchor = False, self.candidate_since, None
            else:
                pose = self.pose
        else:
            self.candidate = None
        held = now - self.since
        if self.grabbing:
            if pose in GRAB_CLOSED:
                events.append(('drag', (frame.get('palm_x', frame['x']), frame.get('palm_y', frame['y']))))
            return events  # while carrying, nothing else acts (no minimize, restore, clicks)
        if self.key:
            return events  # the thumb is on the index: only the key acts, and the pointer holds still
        events.extend(self.machine.update({'pose': pose, 'tip': (frame['x'], frame['y']), 'unit': self.unit, 'aspect': self.aspect,
                                           'origin': self.origin,
                                           'palm': (frame.get('palm_x', frame['x']), frame.get('palm_y', frame['y']))}, now))
        if self.machine.focus:
            self.arming = self.machine.arming
            self.palm_at = self.point_at = -100
            return events  # R6: one measured sign at a time; the pointer holds still and nothing else acts
        self.arming = self.machine.arming
        if pose == 'thumb':
            if not self.fired:
                self.arming = ('grab', min(1, held / THUMB_GRAB_HOLD))
            if held >= THUMB_GRAB_HOLD and not self.fired:
                self.fired = True
                self.start_grab(events)
            return events  # the grab sign does nothing else (no minimize or restore from its closed fingers)
        if pose in TWO_FINGERS:
            if pose == 'three':
                self.three_frames += 1
            else:
                self.two_frames += 1
        if (family(pose) == 'point' and not self.zooming) or pose == 'three':
            # Thumb-out pointing still moves the pointer until a deliberate pinch starts zooming.
            self.point_at = now
            events.append(('move', (frame['x'], frame['y'])))
        if pose == 'fist' and not self.fist_used:
            self.fist_at = now
        if RESIZE_ENABLED and self.dial_armed and pose in DIAL_POSES and frame.get('curl') is not None:
            self.dial(frame['curl'], now, events)
        if pose == 'palm':
            self.on_palm(held, now, events)
        elif pose == 'fist' and now - self.palm_at < 4 and not self.fired and held < FIST_HOLD:
            self.arming = ('minimize', held / FIST_HOLD)
        elif pose == 'fist' and held >= FIST_HOLD and not self.fired:
            self.fired = True
            if now - self.palm_at < 4:
                events.append(('minimize', None))
                self.palm_at = -100
                # Relaxing this fist into an open hand must not bring the window straight back.
                self.fist_used, self.fist_at = True, -100
        elif pose == 'four':
            # Scroll. It is also the in-between shape while a palm closes into a fist (the thumb folds
            # first), so after an open palm it only belongs to the shrink dial and never cancels minimize.
            if not self.dial_armed and held >= SCROLL_HOLD:
                self.scroll(frame, now, events)
        elif family(pose) == 'two':
            self.palm_at = self.point_at = -100
            # Right click is the thumb-folded sign held still; with the thumb out the same hold is the grab
            # sign, and moving fingers are drawing (the sign machine follows the stroke).
            drawing = self.stroke is not None and self.stroke.travel >= DRAW_MOVED
            if not self.fired and not drawing and self.two_frames > self.three_frames:
                self.arming = ('right-click', min(1, held / RIGHT_CLICK_HOLD))
            if held >= RIGHT_CLICK_HOLD and not self.fired and self.two_frames > self.three_frames and not drawing:
                self.fired = True
                events.append(('right-click', self.since))
        elif pose == 'pinky':
            self.palm_at = self.point_at = -100  # the sign machine runs the two-stage sleep
        elif family(pose) == 'point' and (pose == 'zoom' or self.zooming):
            # A zoom is the thumb-out sign narrowed or widened (the fingertips not touching), and continues while
            # the thumb folds (it then reads as plain pointing). An ordinary tucked thumb is far from the tip.
            self.palm_at = -100
            value = frame['pinch']
            position = (frame.get('palm_x', frame['x']), frame.get('palm_y', frame['y']))
            speed = 0.0
            if self.zoom_prev and now > self.zoom_prev[2]:
                speed = self.pw(max(abs(position[0] - self.zoom_prev[0]), abs(position[1] - self.zoom_prev[1]) * self.aspect)) / (now - self.zoom_prev[2])
            self.zoom_prev = (position[0], position[1], now)
            moved = speed > ZOOM_MAX_SPEED or (self.zoom_anchor and self.pw(max(abs(position[0] - self.zoom_anchor[0]),
                                                                                abs(position[1] - self.zoom_anchor[1]) * self.aspect)) > ZOOM_STILL)
            if self.baseline is None or now - self.zoom_since < ZOOM_SETTLE or moved:
                # Still settling, or the hand is travelling: that is pointing, so re-anchor and let the pointer follow.
                self.baseline, self.zoom_anchor, self.zoom_over = value, position, 0
                if moved:
                    self.zooming, self.zoom_since = False, now  # travelling: settle again before a zoom
            elif abs(value - self.baseline) < ZOOM_STEP:
                self.zoom_over = 0
            elif (over := self.zoom_over + 1) < ZOOM_CONFIRM:
                self.zoom_over = over
            else:
                delta = value - self.baseline
                if abs(delta) >= ZOOM_STEP and now - self.step_at >= .13:
                    if not self.zooming:
                        # Pointing ends once zooming begins, so the pointer holds still while zooming.
                        self.zooming = True
                        self.point_at = -100
                    events.append(('zoom', 1 if delta > 0 else -1))
                    self.baseline, self.step_at, self.zoom_over = value, now, 0
        return events

    def hold_drawing(self, pose, now):
        """A drawing under way stays a drawing through a brief lowered middle finger (see DRAW_GRACE)."""
        s = self.stroke
        if s is None:
            return pose
        # Only a drawing longer than a click flick: a flick made with a drifting hand must still click.
        if s.travel < DRAW_MOVED or now - s.at < TAP_MAX_FLICK or family(self.pose) != 'two' or pose not in POINTING + ('neutral',):
            s.break_at = None
            return pose
        if getattr(s, 'break_at', None) is None:
            s.break_at = now
        return self.pose if now - s.break_at < DRAW_GRACE else pose

    def scroll(self, frame, now, events):
        y = frame.get('palm_y', frame['y'])
        if self.scroll_anchor is None:
            self.scroll_anchor, self.scroll_at = y, now
            return
        offset = self.pw((self.scroll_anchor - y) * self.aspect)  # hand raised = positive = scroll up, palm widths
        if abs(offset) <= SCROLL_DEAD:
            self.scroll_at, self.scroll_owed = now, 0.0
            return
        # Calculated, not frame-bound: notches owed = rate x elapsed time, so the rate is exact at any frame rate
        # (one notch per frame at most used to cap 14 / s at the camera's rate). At most 3 per frame (a stall is not a fling).
        owed = getattr(self, 'scroll_owed', 0.0) + scroll_rate(abs(offset)) * (now - self.scroll_at)
        self.scroll_at = now
        notches = min(3, int(owed))
        self.scroll_owed = owed - int(owed)
        events.extend([('scroll', 1 if offset > 0 else -1)] * notches)

    def on_palm(self, held, now, events):
        if held < PALM_HOLD:
            return
        if not self.fired:
            self.fired = True
            if self.since - self.fist_at < RESTORE_WINDOW:
                # A new fist opening again restores what palm -> fist minimized. The same open palm is
                # armed at once, so palm -> fist minimizes again: a clean minimize/restore toggle.
                self.point_at = self.fist_at = -100
                self.palm_at = now
                self.dial_armed = True
                events.append(('restore', None))
                events.append(('arm-target', 'restored'))  # arm on the window being restored, not whatever is under the pointer
                return
            self.palm_at = now
            self.dial_armed = True
            events.append(('arm-target', None))

    def dial(self, curl, now, events):
        if not self.resizing and curl < RESIZE_START:
            return
        if not self.resizing:
            self.resizing = True
        self.palm_at = now  # keep minimize armed while the hand is slowly closing
        if self.resize_last is None or abs(curl - self.resize_last) >= RESIZE_STEP:
            self.resize_last = curl
            events.append(('resize', round(curl, 3)))

    def key_pose(self, frame, pose, now, events):
        """The pinch key. Returns the pose to use: while the key is down a fist / neutral reading is still pointing."""
        pinch, palm = frame['pinch'], (frame.get('palm_x', frame['x']), frame.get('palm_y', frame['y']))
        pointing = family(self.pose) == 'point' or now - self.point_at < KEY_GRACE
        speed = 0.0
        if self.key_prev and now > self.key_prev[2]:
            speed = self.pw(math.dist(palm, self.key_prev[:2])) / (now - self.key_prev[2])
        self.key_prev = palm + (now,)
        if pinch > KEY_OPEN and family(pose) == 'point':
            self.pinch_open_at = now
        k = self.key
        if k is None:
            if (pinch >= KEY_CLOSED or not pointing or speed > KEY_MAX_SPEED or self.zooming or self.grabbing or now - self.pinch_open_at > KEY_CLOSE
                    or pose not in POINTING + ('fist', 'neutral')):
                return pose
            self.key = k = {'at': now, 'palm': palm}
            self.tap_started = None
        if pose in KEY_BREAKS or pinch > KEY_OPEN:
            self.key = None
            if pinch > KEY_OPEN and now - k['at'] < KEY_HOLD and self.pw(math.dist(palm, k['palm'])) < KEY_STILL:
                events.append(('open-target', k['at']))
                self.palm_at = -100  # opening an app must not arm an immediate accidental minimize
            self.point_at = now  # released back to pointing: a new tap may follow straight away
            return pose
        return 'zoom' if pose in ('fist', 'neutral') else pose

    def leave_pose(self, new_pose, events):
        self.scroll_anchor = None
        drew = bool(self.stroke and self.stroke.done)  # a circle or a line was drawn with these fingers
        if self.grabbing and new_pose not in GRAB_CLOSED:
            # Fingers open again: drop here. Opening must not count as a restore or an open.
            self.grabbing = False
            self.fist_at = self.point_at = self.palm_at = -100
            events.append(('drop', None))
            return
        # A hand that just drew a circle or a line is ending a drawing, not opening for a grab.
        grab_open = (family(self.pose) == 'two' and self.three_frames > self.two_frames and self.three_frames * .04 >= GRAB_MIN_OPEN
                     and not drew)
        closes_from_half = self.grab_pending and self.pose == 'neutral' and new_pose == 'fist'
        self.grab_pending = grab_open and new_pose == 'neutral'  # closing often passes a half-closed shape
        if (grab_open and new_pose == 'fist') or closes_from_half:
            self.start_grab(events)
        if family(new_pose) == 'two' and family(self.pose) != 'two':
            self.three_frames = self.two_frames = 0
        if family(new_pose) != 'point' or family(self.pose) != 'point':
            self.baseline, self.zoom_anchor, self.zooming = None, None, False
        if self.dial_armed and self.pose in DIAL_POSES and new_pose not in DIAL_POSES:
            if self.resizing and new_pose != 'fist':
                events.append(('resize-end', None))
            self.dial_armed = self.resizing = False
            self.resize_last = None

    def start_grab(self, events):
        self.grabbing = True
        self.fist_used, self.fist_at, self.palm_at = True, -100, -100
        self.tap_started = None
        events.append(('grab', None))

    def track_tap(self, pose, now, events):
        if self.tap_started is None:
            if self.pose in POINTING and not self.zooming and pose in TWO_FINGERS + ('neutral',) and now - self.since >= TAP_MIN_POINT:
                self.tap_started, self.tap_raised = now, pose in TWO_FINGERS
            return
        if now - self.tap_started > TAP_MAX_FLICK or pose not in TWO_FINGERS + ('neutral',) + POINTING:
            self.tap_started = None
            return
        self.tap_raised = self.tap_raised or pose in TWO_FINGERS
        if pose in POINTING:
            # A brief unclassified frame is not a click; the middle finger must actually rise.
            if self.tap_raised:
                events.append(('click', self.tap_started))
            self.tap_started = None
