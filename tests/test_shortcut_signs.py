"""Shortcut signs: circle drawn with index + middle (Edge), thumb-index tap (open), pinky (two-stage sleep), clap (screenshot).
Level lines (volume, brightness) are in test_sign_machine.py."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as P

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.classic import GestureEngine, describe


def frame(pose, **values):
    return {'pose': pose, 'x': .5, 'y': .5, 'pinch': .9, **values}


class ShortcutSignsTest(unittest.TestCase):
    def setUp(self):
        self.engine = GestureEngine()
        self.now = 10

    def feed(self, frames, dt=.04):
        events = []
        for f in frames:
            self.now += dt
            events.extend(self.engine.update(f, self.now))
        return events

    def hold(self, pose, duration=1, **values):
        return self.feed([frame(pose, **values)] * round(duration / .04))

    def circle(self, clockwise, turns=1.0, radius=.1, steps=40):
        # Image coordinates (y down): a growing angle runs right -> bottom -> left, clockwise on screen.
        sign = 1 if clockwise else -1
        return [frame('two', x=.5 + radius * math.cos(sign * 2 * math.pi * turns * i / steps),
                      y=.5 + radius * math.sin(sign * 2 * math.pi * turns * i / steps)) for i in range(steps + 1)]

    @staticmethod
    def names(events):
        return [event[0] for event in events]


    def test_clockwise_circle_opens_and_anticlockwise_closes(self):
        events = self.feed(self.circle(True))
        self.assertEqual([e for e in events if e[0] == 'circle'], [('circle', 1)])
        self.hold('point', .3)
        events = self.feed(self.circle(False))
        self.assertEqual([e for e in events if e[0] == 'circle'], [('circle', -1)])

    def test_circling_on_makes_one_shortcut(self):
        events = self.feed(self.circle(True, turns=3, steps=120))
        self.assertEqual(self.names(events).count('circle'), 1)

    def test_middle_finger_dipping_mid_circle_keeps_the_drawing(self):
        # Live trace: the moving middle finger reads as down for a few frames; the circle must still count.
        frames = self.circle(True)
        for i in range(15, 20):
            frames[i] = dict(frames[i], pose='point')
        events = self.feed(frames)
        self.assertEqual([e for e in events if e[0] == 'circle'], [('circle', 1)])
        self.assertNotIn('click', self.names(events))

    def test_hand_closing_after_a_circle_is_not_a_grab(self):
        events = self.feed([dict(f, pose='three') for f in self.circle(True)]) + self.hold('fist', .5)
        self.assertIn('circle', self.names(events))
        self.assertNotIn('grab', self.names(events))

    def test_drawing_is_not_a_right_click(self):
        self.assertNotIn('right-click', self.names(self.feed(self.circle(True, steps=60))))

    def test_half_circle_or_small_wobble_does_nothing(self):
        self.assertNotIn('circle', self.names(self.feed(self.circle(True, turns=.5))))
        self.hold('point', .3)
        self.assertNotIn('circle', self.names(self.feed(self.circle(True, radius=.02))))

    def test_still_two_fingers_still_right_click(self):
        self.assertEqual(self.names(self.hold('two', 1)).count('right-click'), 1)

    def tap(self, closed=.15, **values):
        return self.hold('point', .4, pinch=.9, **values) + self.hold('point', closed, pinch=.2, **values) + self.hold('point', .2, pinch=.9, **values)

    def test_thumb_tap_opens_once(self):
        self.assertEqual(self.names(self.tap()).count('open-target'), 1)

    def test_slow_tap_up_to_a_second_still_opens(self):
        # Live trace: real taps took .4-.9 s.
        self.assertEqual(self.names(self.tap(closed=.8)).count('open-target'), 1)

    def test_tap_read_as_a_fist_still_opens(self):
        # Live trace: the index curls to meet the thumb, so the tap itself often reads as a fist or neutral.
        events = self.hold('point', .4, pinch=.6) + self.feed([frame('fist', pinch=p) for p in (.5, .4, .3, .2, .2, .25, .35)])
        events += self.hold('point', .2, pinch=.6)
        self.assertEqual(self.names(events).count('open-target'), 1)
        self.assertFalse({'minimize', 'restore', 'arm-target'} & set(self.names(events)))

    def test_long_or_moving_tap_does_not_open(self):
        self.assertNotIn('open-target', self.names(self.tap(closed=1.2)))
        self.hold('point', .5, pinch=.9)
        moving = [frame('point', pinch=.2, palm_x=.5 + i * .04, palm_y=.5) for i in range(4)]
        self.assertNotIn('open-target', self.names(self.feed(moving) + self.hold('point', .2, pinch=.9, palm_x=.66)))

    def test_resting_thumb_is_not_a_key(self):
        # A thumb that drifts onto the index slowly (not a deliberate tap) neither opens nor starts the volume knob.
        events = (self.hold('point', .5, pinch=.9) + self.hold('point', .5, pinch=.45) + self.hold('point', 1, pinch=.3)
                  + self.hold('point', .2, pinch=.9))
        self.assertFalse({'open-target', 'volume-start', 'volume'} & set(self.names(events)))

    def test_long_pinch_hold_does_nothing(self):
        # The wrist-turn volume dial was removed (2026-09-28): holding the thumb on the index opens nothing, turns
        # nothing, and the pointer holds still until the thumb lets go.
        events = self.hold('point', .4, pinch=.9, roll=90) + self.hold('point', 1.5, pinch=.2, roll=90)
        events += self.feed([frame('point', pinch=.2, roll=a) for a in range(90, 150, 2)]) + self.hold('point', .3, pinch=.9)
        self.assertFalse({'open-target', 'volume', 'volume-start', 'level'} & set(self.names(events)))

    def test_rough_circle_is_shown_as_a_true_circle(self):
        from cameo.gestures import shapes
        # A wobbly three-quarter circle around (500, 400), radius 150 px: the fit finds the real centre and radius.
        rough = [(500 + (150 + 12 * math.sin(7 * a)) * math.cos(a), 400 + (150 - 10 * math.cos(5 * a)) * math.sin(a))
                 for a in (i * 1.5 * math.pi / 40 for i in range(41))]
        cx, cy, r = shapes.fit_circle(rough)
        self.assertLess(math.dist((cx, cy), (500, 400)), 12)
        self.assertLess(abs(r - 150), 12)
        clean = shapes.circle(rough)
        self.assertTrue(all(abs(math.dist(p, (cx, cy)) - r) < 1e-6 for p in clean))
        self.assertLess(math.dist(clean[0], clean[-1]), 1e-6, 'closed')

    def test_rough_line_is_shown_straight_along_its_axis(self):
        from cameo.gestures import shapes
        up = [(300 + (9 if i % 2 else -7) + i, 700 - 20 * i) for i in range(20)]
        (x0, y0), (x1, y1) = shapes.line(up)
        self.assertEqual(x0, x1)
        self.assertEqual((y0, y1), (700, 700 - 20 * 19))
        right = [(100 + 25 * i, 400 + (6 if i % 3 else -5)) for i in range(20)]
        (x0, y0), (x1, y1) = shapes.line(right)
        self.assertEqual(y0, y1)
        self.assertEqual((x0, x1), (100, 100 + 25 * 19))

    def test_pinky_held_sleeps_once_after_the_countdown(self):
        # Two-stage (user's choice): countdown from 1.0 s, sleep at 1.5 s of a still pinky; once per hold.
        self.assertNotIn('sleep', self.names(self.hold('pinky', 1.2)))
        self.assertEqual(self.names(self.hold('pinky', 2)).count('sleep'), 1)

    def test_moving_or_changing_pose_during_the_countdown_cancels_sleep(self):
        events = self.hold('pinky', 1.2) + self.hold('pinky', .3, palm_x=.7)
        self.assertNotIn('sleep', self.names(events))
        self.assertIn(('sign-cancel', ('sleep', 'moved')), events)
        self.hold('point', 1.5)
        events = self.hold('pinky', 1.2) + self.hold('fist', .5)
        self.assertNotIn('sleep', self.names(events))
        self.assertIn(('sign-cancel', ('sleep', 'pose changed')), events)

    def test_clap(self):
        events = self.hold('palm', .3, hands=2, gap=3.0) + self.hold('palm', .1, hands=2, gap=.7)
        self.assertEqual(self.names(events).count('clap'), 1)

    def test_clap_when_palms_merge_into_one_detection(self):
        events = self.hold('palm', .3, hands=2, gap=3.0) + self.hold('palm', .08, hands=2, gap=1.3) + self.hold('palm', .1)
        self.assertEqual(self.names(events).count('clap'), 1)

    def test_hands_resting_together_are_not_a_clap(self):
        self.assertNotIn('clap', self.names(self.hold('palm', 2, hands=2, gap=.7)))

    def test_hands_close_together_never_minimize_or_restore(self):
        self.hold('point', .5)
        events = self.hold('palm', 1, hands=2, gap=1.5) + self.hold('fist', 1, hands=2, gap=1.5) + self.hold('palm', .3)
        for name in ('open-target', 'arm-target', 'minimize', 'restore'):
            self.assertNotIn(name, self.names(events))

    def test_a_second_hand_far_away_does_not_stop_the_main_hand(self):
        # Live trace: a second hand in the picture used to freeze every sign.
        self.assertIn('sleep', self.names(self.hold('pinky', 1.7, hands=2, gap=4)))
        self.hold('point', .5, hands=2, gap=4)
        self.assertEqual(self.names(self.tap(hands=2, gap=4)).count('open-target'), 1)
        self.assertEqual(self.names(self.feed([dict(f, hands=2, gap=4) for f in self.circle(True)])).count('circle'), 1)

    def test_landmark_poses(self):
        pinky = [P(x=.5, y=.9) for _ in range(21)]
        pinky[1:5] = [P(x=.35, y=.8), P(x=.28, y=.72), P(x=.3, y=.6), P(x=.4, y=.65)]
        for m, x, extended in zip((5, 9, 13, 17), (.35, .45, .55, .65), (0, 0, 0, 1)):
            for i, y in enumerate((.65, .5, .35, .2) if extended else (.65, .5, .58, .68)):
                pinky[m + i] = P(x=x, y=y)
        self.assertEqual(describe(pinky)['pose'], 'pinky')

    def test_roll_grows_when_the_wrist_turns_clockwise_as_the_user_sees_it(self):
        def hand(degrees):
            # Mirrored view (what the user sees): middle knuckle at `degrees` around the wrist.
            a = math.radians(degrees)
            points = [P(x=.5, y=.5) for _ in range(21)]
            points[9] = P(x=1 - (.5 + .1 * math.cos(a)), y=.5 + .1 * math.sin(a))  # back to camera coordinates
            return points
        from cameo.gestures.classic import roll
        self.assertAlmostEqual(roll(hand(-70)) - roll(hand(-90)), 20, places=5)

if __name__ == '__main__':
    unittest.main()
