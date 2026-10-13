"""Pose classifier: 21 hand landmarks -> finger states (with hysteresis) -> a pose label.

Moved out of classic.py (docs/SIGN-PROTOCOL-PLAN.md, phase 3): the pose layer knows finger shapes only; which sign a
pose starts is decided by the gesture engine and the sign machine.
"""
import math


def distance(a, b):
    return math.dist((a.x, a.y, getattr(a, 'z', 0)), (b.x, b.y, getattr(b, 'z', 0)))


def angle(a, b, c):
    u = (a.x - b.x, a.y - b.y, getattr(a, 'z', 0) - getattr(b, 'z', 0))
    v = (c.x - b.x, c.y - b.y, getattr(c, 'z', 0) - getattr(b, 'z', 0))
    length = math.hypot(*u) * math.hypot(*v)
    return math.degrees(math.acos(max(-1, min(1, sum(p * q for p, q in zip(u, v)) / length)))) if length else 0


# Finger states switch on and off at different thresholds, so a finger near the limit does not flicker.
# Measured live (2026-09-25): MediaPipe's depth estimate made straight fingers read as bent, so finger
# shape uses the flat image position only; the "on" values are the original, proven 2-D thresholds.
# Approximate signs (user's request, 2026-09-26): in the live trace 24 % of hand frames matched no sign, most
# of them a half-bent pointing index (110-155 deg). The angle limits are looser; the tip/knuckle ratio stays,
# because a curled fist finger often looks straight to the camera (20 % of fist frames over 130 deg).
FINGER_ON, FINGER_OFF = (145, 1.1), (130, 1.0)        # (joint angle, tip/knuckle distance-from-wrist ratio)
# The raise stays strict (13 % of relaxed pointing frames already have the middle over 155 deg: clicks would
# misfire); only the release is looser, so a raised middle finger does not drop out mid-sign.
MIDDLE_UP_ON, MIDDLE_UP_OFF = (162, 1.2), (142, 1.08)  # a clearly raised middle finger for clicks
THUMB_ON, THUMB_OFF = (145, .48), (130, .38)           # (angle, thumb-tip to index-knuckle / palm size)
# The grab sign (user's request, 2026-09-26): four fingers closed with the thumb clearly held out. A loose fist often
# has the thumb a little out, so this needs a straighter thumb held farther from the hand than THUMB_ON.
THUMB_UP_ON, THUMB_UP_OFF = (155, .65), (140, .5)

class Flat:
    __slots__ = ('x', 'y')

    def __init__(self, x, y):
        self.x, self.y = x, y


class PoseClassifier:
    """Keeps finger states between frames. Uses MediaPipe's 3-D world landmarks (metres) when
    given, so tilting the hand toward the camera does not change the measured angles."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fingers = [False] * 4
        self.middle_up = False
        self.thumb = False
        self.thumb_up = False

    @staticmethod
    def switch(previous, value, on, off):
        if value[0] > on[0] and value[1] > on[1]:
            return True
        if value[0] < off[0] or value[1] < off[1]:
            return False
        return previous

    def classify(self, points, world=None):
        if len(points) != 21:
            return None
        if distance(points[0], points[9]) < .035:
            return None  # too far away or degenerate
        # Finger and thumb shape: image x/y only (no noisy depth). Pinch distance: 3-D world landmarks when available.
        flat = [Flat(p.x, p.y) for p in points]
        shape = flat
        scale = distance(shape[0], shape[9]) or 1
        for i, m in enumerate((5, 9, 13, 17)):
            value = (angle(shape[m], shape[m+1], shape[m+3]), distance(shape[m+3], shape[0]) / (distance(shape[m+1], shape[0]) or 1))
            self.fingers[i] = self.switch(self.fingers[i], value, FINGER_ON, FINGER_OFF)
        middle = (angle(shape[9], shape[10], shape[12]), distance(shape[12], shape[0]) / (distance(shape[10], shape[0]) or 1))
        self.middle_up = self.fingers[1] and self.switch(self.middle_up, middle, MIDDLE_UP_ON, MIDDLE_UP_OFF)
        thumb = (angle(shape[2], shape[3], shape[4]), distance(shape[4], shape[5]) / scale)
        self.thumb = self.switch(self.thumb, thumb, THUMB_ON, THUMB_OFF)
        self.thumb_up = self.switch(self.thumb_up, thumb, THUMB_UP_ON, THUMB_UP_OFF)
        self.debug = {'angles': [round(angle(shape[m], shape[m+1], shape[m+3])) for m in (5, 9, 13, 17)],
                      'thumb': [round(thumb[0]), round(thumb[1], 2)]}
        solid = world if world is not None and len(world) == 21 else flat
        pinch = distance(solid[4], solid[8]) / (distance(solid[0], solid[9]) or 1)
        index, middle_ext, ring, little = self.fingers
        if all(self.fingers):
            pose = 'palm' if self.thumb else 'four'
        elif index and middle_ext and not ring and not little:
            pose = ('three' if self.thumb else 'two') if self.middle_up else 'point'
        elif index and not middle_ext and not ring and not little:
            pose = 'zoom' if self.thumb else 'point'
        elif not middle_ext and not ring and not little and pinch < .3 and angle(shape[5], shape[6], shape[8]) > 115:
            pose = 'zoom'  # thumb and a partly straight index pinched together (a fist curls the index fully)
        elif little and not index and not middle_ext and not ring and not self.thumb:
            pose = 'pinky'
        elif not any(self.fingers):
            # Thumb clearly held out = the grab sign; a thumb only a little out is still a (loose) fist.
            pose = 'thumb' if self.thumb_up else 'fist'
        else:
            pose = 'neutral'
        # Pointer position always comes from the image (screen mapping), not the world model.
        return {'pose': pose, 'x': points[8].x, 'y': points[8].y, 'pinch': pinch, 'roll': roll(shape)}


def roll(shape):
    """Wrist rotation in degrees: the direction from the wrist to the middle knuckle, in the mirrored image the
    user sees (x = 1 - x, y down), so turning the hand clockwise as the user sees it increases it. The knuckles
    stay visible in a pinch, so this holds steady while the thumb rests on the index."""
    return math.degrees(math.atan2(shape[9].y - shape[0].y, -(shape[9].x - shape[0].x)))


def describe(points, world=None):
    """Classify a single frame without history (tests and one-off checks)."""
    return PoseClassifier().classify(points, world)
