"""The landmark protocol in the vision worker (HandTracker): smoothing, glitch rejection, gap tolerance, low-light lift.
Synthetic MediaPipe-shaped landmarks, no camera or model."""
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as P

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.classic import PoseClassifier
from cameo.vision.worker import GAP_KEEP, HandTracker, brighten

POSES = {'point': (1, 0, 0, 0), 'two': (1, 1, 0, 0), 'four': (1, 1, 1, 1), 'fist': (0, 0, 0, 0)}


def hand(pose, dx=0.0, dy=0.0, scale=.6):
    points = [P(x=.5, y=.9) for _ in range(21)]
    points[1] = P(x=.35, y=.8)
    for i, (x, y) in enumerate(((.28, .72), (.3, .6), (.4, .65)), 2):  # thumb folded
        points[i] = P(x=x, y=y)
    for m, x, extended in zip((5, 9, 13, 17), (.35, .45, .55, .65), POSES[pose]):
        for i, y in enumerate((.65, .5, .35, .2) if extended else (.65, .5, .58, .68)):
            points[m + i] = P(x=x, y=y)
    return [P(x=.5 + (p.x - .5) * scale + dx, y=.9 + (p.y - .9) * scale + dy, z=0.0) for p in points]


def noisy(points, rng, sigma):
    return [P(x=p.x + rng.gauss(0, sigma), y=p.y + rng.gauss(0, sigma), z=0.0) for p in points]


class HandTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = HandTracker()
        self.t = 0.0

    def feed(self, points, dt=.04):
        self.t += dt
        return self.tracker.update([points] if points else [], None, None, self.t)

    def test_jittery_landmarks_keep_one_pose(self):
        # Heavy jitter (2 % of the frame per point): the raw classifier misreads many frames, the tracker few.
        for pose in ('two', 'four', 'point'):
            rng = random.Random(7)
            raw, tracker = PoseClassifier(), HandTracker()
            wrong_raw = wrong = 0
            for i in range(250):
                points = noisy(hand(pose), rng, .02)
                wrong_raw += (raw.classify(points) or {}).get('pose') != pose
                wrong += tracker.update([points], None, None, i * .04).get('pose') != pose
            self.assertLessEqual(wrong, 10, pose)
            self.assertLessEqual(wrong, wrong_raw, pose)

    def test_moving_hand_is_followed_closely(self):
        # Moving at half a frame width per second the smoothed fingertip trails by about a hundredth, and catches up
        # within a few frames once the hand stops.
        for i in range(40):
            dx = min(i, 20) * .02
            message = self.feed(hand('point', dx=dx))
            lag = abs(message['x'] - (1 - hand('point', dx=dx)[8].x))
            self.assertLess(lag, .015 if i <= 20 else .01 if i < 23 else .002, i)

    def test_quick_pose_change_shows_within_two_frames(self):
        # A click is a middle-finger flick under half a second: smoothing must not hide it.
        for _ in range(10):
            self.feed(hand('point'))
        poses = [self.feed(hand('two'))['pose'] for _ in range(5)]
        self.assertIn('two', poses[:2])
        poses = [self.feed(hand('point'))['pose'] for _ in range(5)]
        self.assertIn('point', poses[:2])

    def test_one_frame_glitch_is_skipped_and_keeps_the_hand(self):
        for _ in range(10):
            self.feed(hand('two'))
        glitch = self.feed(hand('fist', scale=.2))  # landmarks collapsed onto something small
        self.assertFalse(glitch['hand'])
        fingers = list(self.tracker.classifier.fingers)
        self.assertEqual(fingers[:2], [True, True], 'finger states survive the glitch')
        self.assertEqual(self.feed(hand('two'))['pose'], 'two')

    def test_real_move_toward_the_camera_is_believed(self):
        for _ in range(10):
            self.feed(hand('point'))
        results = [self.feed(hand('point', scale=1.0)) for _ in range(6)]
        self.assertTrue(any(r['hand'] for r in results[:4]), 'a lasting size change is accepted after a few frames')
        self.assertEqual(results[-1]['pose'], 'point')

    def test_short_gap_keeps_state_and_long_gap_resets(self):
        for _ in range(10):
            self.feed(hand('two'))
        self.feed(None)
        self.feed(None)  # 80 ms: a blurred frame or two
        self.assertEqual(self.tracker.classifier.fingers[:2], [True, True])
        self.assertEqual(self.feed(hand('two'))['pose'], 'two')
        for _ in range(int(GAP_KEEP / .04) + 2):
            self.feed(None)
        self.assertEqual(self.tracker.classifier.fingers, [False] * 4, 'the hand left: nothing carries over')

    def test_second_hand_does_not_steal_the_main_hand(self):
        for _ in range(5):
            self.feed(hand('point', dx=-.2))
        message = self.tracker.update([hand('four', dx=.25), hand('point', dx=-.2)], None, None, self.t + .04)
        self.assertEqual(message['pose'], 'point')
        self.assertEqual(message['hands'], 2)

    def test_dark_frame_is_brightened_and_bright_frame_untouched(self):
        import cv2
        import numpy as np
        dark = np.full((480, 640, 3), 35, dtype='uint8')
        lifted, light = brighten(cv2, dark)
        self.assertAlmostEqual(light, 35, delta=1)
        self.assertGreater(lifted.mean(), 90)
        normal = np.full((480, 640, 3), 130, dtype='uint8')
        same, light = brighten(cv2, normal)
        self.assertIs(same, normal)


if __name__ == '__main__':
    unittest.main()
