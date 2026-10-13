"""Vision worker process (ARCHITECTURE.md §4.1-4.2): owns the camera and the hand model, never decides actions.

Sends one frame dict per processed image over a multiprocessing pipe:
{'t', 'hand', 'conf', 'x', 'y', 'pinch', 'palm', 'speed', 'fps', 'hands', 'gap', 'unit', 'aspect', 'light'}
(x, y = mirrored index tip, 0..1; unit = palm width in frame widths; aspect = frame height / width)
Up to two hands are tracked: signs come from one main hand (the one nearest to where the main hand was),
and 'gap' (palm centre distance / hand size) is sent while both are seen, for the clap.

Landmark protocol (HandTracker, user's request 2026-09-28 "it didn't detect properly"): every frame's landmarks are
  1. checked: a main hand whose size jumps by more than GLITCH_SIZE in one frame is a misdetection and skipped
     (after GLITCH_FRAMES such frames in a row it is believed: the hand really moved);
  2. smoothed: One Euro filter on all 21 image and world points (steady when still, no lag when moving), so finger
     angles near a threshold stop flickering between poses;
  3. kept through gaps: a detection miss shorter than GAP_KEEP keeps the finger states and filters (a blurred frame
     used to reset the classifier, so the next frames started from scratch).
Dark frames are brightened (gamma) before detection, and 'light' (mean brightness 0..255) is reported.
"""
import math
import os
import threading
import time

from ..paths import HAND_MODEL, WINDOWS
from ..gestures.classic import OneEuro, PoseClassifier

# MediaPipe: detecting a new hand stays at .5 (fewer phantom hands), but keeping an already tracked hand is easier (.4),
# so fast or blurred movement keeps tracking instead of dropping the hand and re-detecting it from scratch.
DETECT_CONFIDENCE, PRESENCE_CONFIDENCE, TRACKING_CONFIDENCE = .5, .4, .4
GAP_KEEP = .3        # seconds without the hand that still count as the same hand (matches the engine's DROPOUT)
GLITCH_SIZE = .45    # a hand size change over 45 % in one frame is a misdetection, not movement
GLITCH_FRAMES = 3
SMOOTH_CUTOFF, SMOOTH_BETA = 2.0, 12.0          # image points (frame widths): 2 Hz when still, opens up with speed
WORLD_CUTOFF, WORLD_BETA = 2.0, 40.0            # world points (metres: smaller numbers, so a larger beta)
DARK = 80            # mean brightness below which the frame is brightened before detection
LIGHT_TARGET = 115


def dist(a, b):
    return math.dist((a.x, a.y, getattr(a, 'z', 0)), (b.x, b.y, getattr(b, 'z', 0)))


def bend(a, b, c):
    u = (a.x - b.x, a.y - b.y, getattr(a, 'z', 0) - getattr(b, 'z', 0))
    v = (c.x - b.x, c.y - b.y, getattr(c, 'z', 0) - getattr(b, 'z', 0))
    n = math.hypot(*u) * math.hypot(*v)
    return math.degrees(math.acos(max(-1, min(1, sum(p * q for p, q in zip(u, v)) / n)))) if n else 0


class PalmDetector:
    """Open palm = four fingers and thumb extended, with on/off hysteresis so it does not flicker."""

    def __init__(self):
        self.open = False

    def update(self, shape):
        scale = dist(shape[0], shape[9]) or 1
        angles = [bend(shape[m], shape[m + 1], shape[m + 3]) for m in (5, 9, 13, 17)]
        thumb = bend(shape[2], shape[3], shape[4]) > 140 and dist(shape[4], shape[5]) / scale > .45
        if all(a > 160 for a in angles) and thumb:
            self.open = True
        elif any(a < 140 for a in angles):
            self.open = False
        return self.open


def features(points, world, previous, palm, t):
    shape = world if world else points
    scale = dist(shape[0], shape[9]) or 1
    x, y = 1 - points[8].x, points[8].y
    cx, cy = 1 - points[9].x, points[9].y
    speed = 0.0
    if previous and t > previous[2]:
        speed = math.hypot(cx - previous[0], cy - previous[1]) / (t - previous[2])
    return {'hand': True, 'x': x, 'y': y, 'palm_x': cx, 'palm_y': cy, 'pinch': dist(shape[4], shape[8]) / scale, 'palm': palm.update(shape), 'speed': speed}, (cx, cy, t)


def palm_size(points):
    """Hand size from five palm distances (wrist to each knuckle, and across the knuckles). The palm does not bend,
    and averaging five distances keeps landmark jitter from looking like a size jump."""
    return (dist(points[0], points[5]) + dist(points[0], points[9]) + dist(points[0], points[13]) + dist(points[0], points[17])
            + dist(points[5], points[17])) / 5


def main_hand(hands, previous):
    """Index of the hand to read signs from: the one whose wrist is nearest the last main hand's wrist,
    so a second hand coming into view never takes the pointer away."""
    if previous is None or len(hands) == 1:
        return 0
    return min(range(len(hands)), key=lambda i: math.hypot(hands[i][0].x - previous[0], hands[i][0].y - previous[1]))


def gap(a, b):
    size = (dist(a[0], a[9]) + dist(b[0], b[9])) / 2 or 1
    return dist(a[9], b[9]) / size


class Point:
    __slots__ = ('x', 'y', 'z')

    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class Steady:
    """One Euro filter over a whole set of landmarks."""

    def __init__(self, cutoff, beta):
        self.cutoff, self.beta = cutoff, beta
        self.filters = None

    def reset(self):
        self.filters = None

    def __call__(self, points, t):
        if self.filters is None or len(self.filters) != len(points):
            self.filters = [[OneEuro(self.cutoff, self.beta) for _ in range(3)] for _ in points]
        return [Point(f[0](p.x, t), f[1](p.y, t), f[2](getattr(p, 'z', 0.0), t)) for f, p in zip(self.filters, points)]


class HandTracker:
    """Landmarks of one frame -> the frame message (see the landmark protocol above). No camera or model here."""

    def __init__(self):
        self.classifier = PoseClassifier()
        self.palm = PalmDetector()
        self.image, self.world = Steady(SMOOTH_CUTOFF, SMOOTH_BETA), Steady(WORLD_CUTOFF, WORLD_BETA)
        self.reset()

    def reset(self):
        self.classifier.reset()
        self.image.reset()
        self.world.reset()
        self.previous = self.wrist = self.size = None
        self.seen_at = -100
        self.glitches = 0

    def update(self, hands, worlds, handedness, t, aspect=1.0):
        """hands: MediaPipe hand_landmarks (lists of 21 points); worlds / handedness may be shorter or None.
        aspect: frame height / width, so 'unit' (the palm width signs are measured in) is in frame widths."""
        if not hands:
            return self.missing(t)
        main = main_hand(hands, self.wrist)
        points = hands[main]
        size = palm_size(points)
        if self.size and abs(size / self.size - 1) > GLITCH_SIZE and self.glitches < GLITCH_FRAMES and t - self.seen_at < GAP_KEEP:
            self.glitches += 1
            return self.missing(t)
        if self.glitches >= GLITCH_FRAMES or t - self.seen_at >= GAP_KEEP:
            self.image.reset()  # a new hand (or a real jump): start the filters from where it is now
            self.world.reset()
        # The reference size follows the hand smoothly (a real move toward the camera takes several frames).
        steady = self.size and self.glitches < GLITCH_FRAMES and t - self.seen_at < GAP_KEEP
        self.size = self.size + .3 * (size - self.size) if steady else size
        self.glitches, self.seen_at = 0, t
        self.wrist = (points[0].x, points[0].y)
        world = worlds[main] if len(worlds or []) > main else None
        points = self.image(points, t)
        world = self.world(world, t) if world is not None and len(world) == 21 else None
        message, self.previous = features(points, world, self.previous, self.palm, t)
        message['conf'] = handedness[main][0].score if len(handedness or []) > main else 1
        message['hands'] = len(hands)
        if len(hands) >= 2:
            message['gap'] = round(gap(hands[0], hands[1]), 3)
        pose = self.classifier.classify(points, world)
        message['pose'] = pose['pose'] if pose else None
        message['roll'] = round(pose['roll'], 1) if pose else None
        angles = getattr(self.classifier, 'debug', {}).get('angles')
        if angles:
            # 0 = open palm (straight fingers, 180 deg joints) .. 1 = fist (about 30 deg joints).
            message['curl'] = round(sum(min(1, max(0, (180 - a) / 150)) for a in angles) / 4, 3)
        if os.environ.get('CAMEO_TRACE'):
            message['fingers'] = getattr(self.classifier, 'debug', None)
        # Mirrored image landmarks for the on-screen skeleton, and hand size (wrist to middle knuckle).
        message['points'] = [(round(1 - p.x, 4), round(p.y, 4)) for p in points]
        message['size'] = round(dist(points[0], points[9]), 3)
        # The palm width every sign distance is measured in (docs/SIGN-PROTOCOL-PLAN.md, R3), in frame widths.
        message['unit'] = round(palm_size([Point(p.x, p.y * aspect) for p in points]), 4)
        message['aspect'] = round(aspect, 4)
        return message

    def missing(self, t):
        if t - self.seen_at >= GAP_KEEP:
            self.reset()  # the hand has left: nothing carries over to the next hand
        # A short miss keeps finger states and filters; the engine's own dropout rule keeps the signs alive.
        return {'hand': False, 'hands': 0}


def brighten(cv2, frame):
    """Gamma lift for a dark frame (MediaPipe misses hands in dim rooms). Returns (frame, mean brightness)."""
    light = float(frame[::16, ::16].mean())
    if light >= DARK or light < 2:
        return frame, light
    import numpy as np
    gamma = max(.35, min(1.0, math.log(LIGHT_TARGET / 255) / math.log(light / 255)))
    table = (255 * (np.arange(256) / 255) ** gamma).astype('uint8')
    return cv2.LUT(frame, table), light


def run_worker(conn, camera=0, fps=25):
    from ..stdio import ensure_streams
    ensure_streams()  # the spawned worker has no console either when Cameo runs without one
    import cv2
    import mediapipe as mp
    cv2.setNumThreads(1)
    options = mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(HAND_MODEL)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=DETECT_CONFIDENCE, min_hand_presence_confidence=PRESENCE_CONFIDENCE,
        min_tracking_confidence=TRACKING_CONFIDENCE)
    detector = mp.tasks.vision.HandLandmarker.create_from_options(options)
    cap = cv2.VideoCapture(camera, cv2.CAP_DSHOW if WINDOWS else cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        conn.send({'error': 'DEVICE_UNAVAILABLE', 'message': 'Camera busy or missing: close other camera apps'})
        return
    newest = {'frame': None, 'at': 0.0, 'n': 0}
    stop = threading.Event()

    def grab():
        # The driver queues frames; always keep only the newest so tracking never lags behind.
        while not stop.is_set():
            ok, frame = cap.read()
            if ok:
                newest['frame'], newest['at'] = frame, time.monotonic()
                newest['n'] += 1
            else:
                time.sleep(.05)

    threading.Thread(target=grab, daemon=True).start()
    tracker = HandTracker()
    used, stamp, times = 0, 0, []
    try:
        while not (conn.poll() and conn.recv() == 'stop'):
            started = time.monotonic()
            if newest['n'] == used:
                time.sleep(.004)
                continue
            used, frame, captured = newest['n'], newest['frame'], newest['at']
            stamp = max(stamp + 1, int(captured * 1000))
            frame, light = brighten(cv2, frame)
            result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), stamp)
            t = time.time()
            times = (times + [started])[-25:]
            rate = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0
            aspect = frame.shape[0] / frame.shape[1]
            message = tracker.update(result.hand_landmarks, result.hand_world_landmarks, result.handedness, t, aspect)
            message['light'] = round(light)
            message.update(t=t, fps=round(rate, 1), delay_ms=round((time.monotonic() - captured) * 1000))
            conn.send(message)
            time.sleep(max(0, 1 / fps - (time.monotonic() - started)))
    finally:
        stop.set()
        time.sleep(.1)
        cap.release()
        detector.close()
