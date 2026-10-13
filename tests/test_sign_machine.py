"""The sign machine's protocol (docs/SIGN-PROTOCOL-PLAN.md) through the gesture engine: level lines are measured in palm
widths and set absolute levels, every sign has a named end, one sign holds the focus. Synthetic frames, no camera."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.classic import GestureEngine

UNIT = .2  # palm width in frame widths used by these frames (also the engine's default)


def frame(pose, **values):
    return {'pose': pose, 'x': .5, 'y': .5, 'pinch': .9, 'unit': UNIT, **values}


class SignMachineTest(unittest.TestCase):
    def setUp(self):
        self.engine = GestureEngine()
        self.now = 10

    def feed(self, frames, dt=.04):
        events = []
        for f in frames:
            self.now += dt
            events.extend(self.engine.update(f, self.now))
        return events

    def hold(self, pose, duration, **values):
        return self.feed([frame(pose, **values)] * round(duration / .04))

    def stroke(self, dx, dy, start=(.5, .5), steps=25, pose='two', **values):
        """Fingertip from start by (dx, dy) frame units, as the user sees it (x right, y down)."""
        return self.feed([frame(pose, x=start[0] + dx * i / steps, y=start[1] + dy * i / steps, **values) for i in range(steps + 1)])

    @staticmethod
    def levels(events, phase=None):
        return [value for name, value in events if name == 'level' and (phase is None or value[0] == phase)]

    def last_delta(self, events):
        return self.levels(events)[-1][2]

    # -- ratio (R2, R3) --------------------------------------------------------------------------
    def test_one_palm_width_right_is_a_quarter_of_the_volume(self):
        events = self.stroke(UNIT, 0, start=(.4, .5))
        engage = self.levels(events, 'engage')
        self.assertEqual(len(engage), 1)
        self.assertEqual(engage[0][1], 'volume')
        self.assertAlmostEqual(self.last_delta(events), .25, delta=.01)
        events = self.hold('point', .4, x=.4 + UNIT)  # released where the line ended: committed at the value reached
        commit = self.levels(events, 'commit')
        self.assertEqual(len(commit), 1)
        self.assertAlmostEqual(commit[0][2], .25, delta=.01)
        self.assertEqual(commit[0][3], 'released')

    def test_left_lowers_and_vertical_lines_set_brightness(self):
        self.assertAlmostEqual(self.last_delta(self.stroke(-UNIT, 0, start=(.6, .5))), -.25, delta=.01)
        self.hold('point', .8)
        events = self.stroke(0, -UNIT, start=(.5, .6))
        self.assertEqual(self.levels(events, 'engage')[0][1], 'brightness')
        self.assertAlmostEqual(self.last_delta(events), .40, delta=.015)
        self.hold('point', .8)
        self.assertAlmostEqual(self.last_delta(self.stroke(0, UNIT, start=(.5, .4))), -.40, delta=.015)

    def test_same_hand_movement_means_the_same_near_and_far(self):
        # One palm width of travel at a small (far) and a large (near) hand: the same change.
        results = []
        for unit in (.1, .35):
            self.setUp()
            events = self.stroke(unit, 0, start=(.3, .5), unit=unit)
            results.append(self.last_delta(events))
        self.assertAlmostEqual(results[0], results[1], delta=.01)
        self.assertAlmostEqual(results[0], .25, delta=.01)

    def test_vertical_travel_is_measured_in_width_units(self):
        # A 640 x 480 camera: a frame height is .75 frame widths, so .2 width = .2667 of the frame height.
        events = self.stroke(0, -.2 / .75, start=(.5, .7), aspect=.75)
        self.assertAlmostEqual(self.last_delta(events), .40, delta=.015)

    def test_moving_back_lowers_the_value_again(self):
        events = self.stroke(UNIT, 0, start=(.3, .5))
        events += self.stroke(-UNIT / 2, 0, start=(.3 + UNIT, .5))
        self.assertAlmostEqual(self.last_delta(events), .125, delta=.015)
        self.assertEqual(len(self.levels(events, 'engage')), 1, 'one line, one engage')

    # -- ends (R7) -------------------------------------------------------------------------------
    def test_holding_still_commits_and_freezes_the_value(self):
        events = self.stroke(UNIT, 0, start=(.3, .5)) + self.hold('two', .7, x=.3 + UNIT)
        commit = self.levels(events, 'commit')
        self.assertEqual(len(commit), 1)
        self.assertEqual(commit[0][3], 'still')
        after = self.stroke(UNIT, 0, start=(.3 + UNIT, .5))
        self.assertEqual(self.levels(after), [], 'no destination change after the commit until the fingers leave')

    def test_fist_while_controlling_cancels(self):
        events = self.stroke(UNIT, 0, start=(.3, .5)) + self.hold('fist', .3, x=.3 + UNIT)
        cancel = self.levels(events, 'cancel')
        self.assertEqual(len(cancel), 1)
        self.assertEqual(cancel[0][3], 'fist')
        self.assertFalse({'minimize', 'grab'} & {name for name, _ in events})

    def test_hand_lost_right_after_engaging_cancels_later_keeps(self):
        # Engage exactly at the end of the stroke, then lose the hand.
        events = self.stroke(.6 * UNIT + .01, 0, start=(.3, .5), steps=8) + self.feed([None] * 12)
        self.assertEqual([e[3] for e in self.levels(events, 'cancel')], ['hand lost'])
        self.hold('point', .8)
        events = self.stroke(UNIT, 0, start=(.3, .5), steps=30) + self.feed([None] * 12)
        self.assertEqual([e[3] for e in self.levels(events, 'commit')], ['hand left'])

    def test_brief_middle_finger_dip_keeps_the_line(self):
        frames = [frame('two', x=.3 + 2 * UNIT * i / 40) for i in range(41)]
        for i in range(20, 23):
            frames[i] = dict(frames[i], pose='point')
        events = self.feed(frames)
        self.assertEqual(self.levels(events, 'commit'), [])
        self.assertAlmostEqual(self.last_delta(events), .5, delta=.02)
        self.assertNotIn('click', [name for name, _ in events])

    # -- what is not a line ----------------------------------------------------------------------
    def test_short_diagonal_or_pointing_strokes_do_nothing(self):
        self.assertEqual(self.levels(self.stroke(.5 * UNIT, 0, start=(.3, .5))), [], 'under 0.6 palm widths')
        self.hold('point', .8)
        self.assertEqual(self.levels(self.stroke(UNIT, -UNIT, start=(.3, .7))), [], 'diagonal')
        self.hold('point', .8)
        self.assertEqual(self.levels(self.stroke(2 * UNIT, 0, start=(.3, .5), pose='point')), [], 'pointing moves the pointer')

    def circle(self, radius, turns=1.0, steps=48, noise=0.0):
        return [frame('two', x=.5 + (radius + noise * math.sin(7 * i)) * math.cos(2 * math.pi * turns * i / steps),
                      y=.5 + (radius + noise * math.cos(5 * i)) * math.sin(2 * math.pi * turns * i / steps)) for i in range(steps + 1)]

    def test_circle_is_fitted_not_a_line(self):
        events = self.feed(self.circle(.1))
        self.assertEqual([e for e in events if e[0] == 'circle'], [('circle', 1)])
        self.assertEqual(self.levels(events), [])

    def test_small_or_ragged_loops_are_not_circles(self):
        self.assertNotIn('circle', [n for n, _ in self.feed(self.circle(.03))], 'radius .15 palm widths: a wobble')
        self.hold('point', 2)
        # A zig-zag that turns a lot but does not lie on a circle.
        zigzag = [frame('two', x=.4 + .02 * i, y=.5 + (.08 if i % 2 else -.08)) for i in range(30)]
        self.assertNotIn('circle', [n for n, _ in self.feed(zigzag)])

    # -- focus, drift, cooldown (R6) ---------------------------------------------------------------
    def test_one_sign_at_a_time_while_controlling(self):
        events = self.stroke(UNIT, 0, start=(.3, .5)) + self.feed([frame('two', x=.3 + UNIT + .001 * i) for i in range(10)])
        self.assertFalse({'move', 'right-click', 'click', 'circle'} & {name for name, _ in events})
        self.assertTrue(self.engine.machine.focus)

    def test_drift_before_the_line_is_not_part_of_it(self):
        # Fingers drift diagonally, rest, then draw a clean line: the line counts from where they rested.
        drift = [frame('two', x=.3 + .004 * i, y=.5 + .004 * i) for i in range(15)]
        rest = [frame('two', x=.36, y=.56)] * 12
        events = self.feed(drift + rest) + self.stroke(UNIT, 0, start=(.36, .56))
        self.assertEqual(len(self.levels(events, 'engage')), 1)
        self.assertAlmostEqual(self.last_delta(events), .25, delta=.02)

    def test_cooldown_after_a_line(self):
        self.stroke(UNIT, 0, start=(.3, .5))
        self.feed([frame('point')] * 2)  # released: cooldown 0.4 s starts
        events = self.stroke(UNIT, 0, start=(.3, .5), steps=5)  # 0.2 s later
        self.assertEqual(self.levels(events, 'engage'), [])


if __name__ == '__main__':
    unittest.main()
