"""Classic hand-sign rules (same cases as Code Cameo tests/desktop_test.py), now in AETHER."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as P

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.classic import GestureEngine, describe, RESIZE_ENABLED


def frame(pose, **values):
    return {'pose': pose, 'x': .5, 'y': .5, 'pinch': .3, **values}


class GesturesTest(unittest.TestCase):
    def setUp(self):
        self.engine = GestureEngine()
        self.now = 10

    def hold(self, pose, duration=1, **values):
        events = []
        for _ in range(round(duration/.05)):
            self.now += .05
            events.extend(self.engine.update(frame(pose, **values), self.now))
        return events

    def test_pointing_then_palm_no_longer_opens(self):
        # Opening moved to the thumb-index tap (user's choice); the palm only arms minimize.
        self.hold('point')
        events = self.hold('palm', 2) + self.hold('point', .2)
        self.assertNotIn('open-target', [event[0] for event in events])

    def test_open_palm_then_fist_minimizes_once(self):
        self.hold('palm')
        events = self.hold('fist', 2)
        self.assertEqual([event[0] for event in events].count('minimize'), 1)
        self.assertNotIn('close', [event[0] for event in events])

    def test_fist_then_palm_restores_after_minimize(self):
        self.hold('palm')
        self.assertIn('minimize', [event[0] for event in self.hold('fist', 1)])
        self.hold('neutral', .3)
        self.hold('fist', .6)  # restore starts from a new fist, not the one that minimized
        self.assertIn('restore', [event[0] for event in self.hold('palm', 1)])

    def test_palm_without_recent_fist_does_not_restore(self):
        self.hold('fist', 1)
        self.hold('neutral', 2)
        self.assertNotIn('restore', [event[0] for event in self.hold('palm', 1)])
        self.hold('point', .6)
        self.assertNotIn('restore', [event[0] for event in self.hold('palm', 1) + self.hold('point', .2)])

    def test_loss_invalidates_minimize_and_open_targets(self):
        self.hold('palm')
        self.now += .4
        self.engine.update(None, self.now)
        self.assertNotIn('minimize', [event[0] for event in self.hold('fist')])
        self.hold('point')
        self.now += .4
        self.engine.update(None, self.now)
        self.assertNotIn('open-target', [event[0] for event in self.hold('palm')])

    def test_brief_detection_gaps_keep_holds_and_sequences(self):
        self.hold('palm', .3)
        for _ in range(3):  # three missed frames (0.15 s) in the middle of a hold
            self.now += .05
            self.assertEqual(self.engine.update(None, self.now), [])
        self.assertIn('arm-target', [event[0] for event in self.hold('palm', .3)])
        self.now += .1
        self.engine.update(None, self.now)
        self.assertIn('minimize', [event[0] for event in self.hold('fist', 1)])

    def test_point_palm_fist_minimizes_without_double_click(self):
        self.hold('point', .6)
        events = self.hold('palm', .6) + self.hold('fist', 1)
        names = [event[0] for event in events]
        self.assertIn('minimize', names)
        self.assertNotIn('open-target', names)

    def test_holding_the_palm_never_opens(self):
        self.hold('point', .6)
        self.assertEqual([event for event in self.hold('palm', 1.2) if event[0] == 'open-target'], [])

    def test_finger_states_do_not_flicker_near_thresholds(self):
        from cameo.gestures.classic import PoseClassifier
        def hand(index_angle_tip_y):
            points = [P(x=.5, y=.9, z=0) for _ in range(21)]
            points[1] = P(x=.35, y=.8, z=0)
            for i, (x, y) in enumerate(((.28, .72), (.3, .6), (.4, .65)), 2):
                points[i] = P(x=x, y=y, z=0)
            for m, x in zip((9, 13, 17), (.45, .55, .65)):
                for i, y in enumerate((.65, .5, .58, .68)):
                    points[m+i] = P(x=x, y=y, z=0)
            for i, y in enumerate((.65, .5, .35)):
                points[5+i] = P(x=.35, y=y, z=0)
            points[8] = P(x=.35+index_angle_tip_y, y=.2, z=0)
            return points
        classifier = PoseClassifier()
        self.assertEqual(classifier.classify(hand(0))['pose'], 'point')
        # A slightly bent index (between the on and off thresholds) stays 'point'.
        self.assertEqual(classifier.classify(hand(.28))['pose'], 'point')  # about 137 deg: between FINGER_OFF and FINGER_ON
        self.assertEqual(PoseClassifier().classify(hand(.28))['pose'], 'fist', 'a fresh classifier needs a clearly straight finger')

    def test_four_fingers_is_reserved_and_does_nothing(self):
        self.assertEqual(self.hold('four', 2), [])
        self.hold('point', .6)
        self.assertEqual([event for event in self.hold('four', 2) if event[0] != 'move'], [])

    def test_holding_two_fingers_right_clicks_once(self):
        self.hold('point', .6)
        start = self.now + .05
        events = self.hold('two', 1.5)
        self.assertEqual([event for event in events if event[0] == 'right-click'], [('right-click', start)])
        self.assertNotIn('click', [event[0] for event in events + self.hold('point', .3)])

    def test_two_fingers_never_scroll(self):
        events = self.hold('two', 1.5)
        self.assertNotIn('scroll', [event[0] for event in events])

    def test_zoom_direction_and_noise(self):
        zooms = lambda events: [event for event in events if event[0] == 'zoom']
        self.hold('zoom')
        self.assertEqual(zooms(self.hold('zoom', .5, pinch=.45)), [], 'small changes are not a zoom')
        self.assertEqual(zooms(self.hold('zoom', .3, pinch=.8)), [('zoom', 1)])
        self.assertEqual(zooms(self.hold('zoom', .3, pinch=.45)), [('zoom', -1)])

    # Cases taken from the live trace of 2026-09-25.
    def test_thumb_flicker_while_pointing_keeps_one_pointing_state_and_click_works(self):
        for pose in ('point', 'zoom', 'point', 'zoom', 'zoom', 'point'):
            self.hold(pose, .06)
        self.hold('zoom', .2)
        self.hold('two', .15)
        self.assertIn('click', [event[0] for event in self.hold('zoom', .2)])

    def test_moving_hand_with_thumb_out_does_not_zoom(self):
        events = []
        for step in range(40):  # the hand travels across the camera while the pinch distance drifts
            self.now += .05
            events += self.engine.update(frame('zoom', x=.3 + step * .01, y=.5, pinch=.3 + (step % 10) * .05), self.now)
        self.assertNotIn('zoom', [event[0] for event in events])
        self.assertGreater([event[0] for event in events].count('move'), 30)

    # The minimize/restore loop reported on 2026-09-25 (two windows).
    def test_relaxing_the_minimize_fist_does_not_restore(self):
        self.hold('palm', .6)
        self.assertIn('minimize', [event[0] for event in self.hold('fist', .8)])
        self.assertNotIn('restore', [event[0] for event in self.hold('palm', 1)])

    def test_new_fist_then_palm_restores_and_rearms_minimize(self):
        self.hold('palm', .6)
        self.hold('fist', .8)          # minimize
        self.hold('neutral', .3)
        self.hold('fist', .6)          # a new fist
        events = [event[0] for event in self.hold('palm', .6)]
        self.assertEqual([e for e in events if e in ('restore', 'arm-target')], ['restore', 'arm-target'])
        self.assertIn('minimize', [event[0] for event in self.hold('fist', .8)], 'palm -> fist minimizes again')

    def test_minimize_restore_toggles_repeatedly_without_extra_restores(self):
        names = []
        self.hold('palm', .6)
        for _ in range(3):
            names += [e[0] for e in self.hold('fist', .8)]      # minimize
            names += [e[0] for e in self.hold('neutral', .3)]
            names += [e[0] for e in self.hold('fist', .6)]      # new fist
            names += [e[0] for e in self.hold('palm', .6)]      # restore (and re-arm)
        self.assertEqual(names.count('minimize'), 3)
        self.assertEqual(names.count('restore'), 3)

    # Palm-closing dial (2026-09-25): curl 0 = open palm .. 1 = fist.
    def dial_hold(self, pose, seconds, curl):
        events = []
        for _ in range(round(seconds / .05)):
            self.now += .05
            events += self.engine.update(frame(pose, curl=curl), self.now)
        return events

    @unittest.skipIf(RESIZE_ENABLED, 'shrink dial active')
    def test_shrink_is_paused_but_slow_close_still_minimizes(self):
        self.hold('palm', .6, curl=0)
        events = [e[0] for e in self.hold('neutral', 1.5, curl=.5)]
        self.assertNotIn('resize', events)
        self.assertIn('minimize', [e[0] for e in self.hold('fist', .8, curl=1)])

    @unittest.skipUnless(RESIZE_ENABLED, 'shrink dial paused')
    def test_partly_closing_the_palm_shrinks_instead_of_opening(self):
        self.hold('point', .6)
        self.dial_hold('palm', .6, 0)
        events = self.dial_hold('palm', .2, .3) + self.dial_hold('neutral', .3, .5) + self.dial_hold('neutral', .2, .6)
        values = [value for name, value in events if name == 'resize']
        self.assertTrue(values and values == sorted(values) and values[-1] >= .5, values)
        grow = [value for name, value in self.dial_hold('palm', .3, .05) if name == 'resize']
        self.assertTrue(grow and grow[-1] < .1, 'opening the palm again grows the window back')
        end = [name for name, _ in self.hold('point', .3)]
        self.assertIn('resize-end', end)
        self.assertNotIn('open-target', end, 'a shrink is not an open')

    @unittest.skipUnless(RESIZE_ENABLED, 'shrink dial paused')
    def test_small_natural_bend_does_not_shrink(self):
        self.dial_hold('palm', .6, 0)
        self.assertNotIn('resize', [name for name, _ in self.dial_hold('palm', 1, .08)])

    @unittest.skipUnless(RESIZE_ENABLED, 'shrink dial paused')
    def test_closing_all_the_way_minimizes_even_after_a_slow_shrink(self):
        self.dial_hold('palm', .6, 0)
        for curl in (.2, .35, .5, .65, .8):
            self.dial_hold('neutral', 1.2, curl)  # a slow close, longer than the 4 s minimize window in total
        self.assertIn('minimize', [name for name, _ in self.dial_hold('fist', .8, 1)])

    def test_palm_closing_through_four_fingers_still_minimizes(self):
        self.hold('palm', .6)
        self.hold('four', .15)
        self.assertIn('minimize', [event[0] for event in self.hold('fist', .8)])

    def test_thumb_out_pointing_moves_until_zoom_starts(self):
        moves = self.hold('zoom', .5)
        self.assertTrue(moves and all(event[0] == 'move' for event in moves))
        events = self.hold('zoom', .3, pinch=.8)
        self.assertIn(('zoom', 1), events)
        self.assertNotIn('move', [event[0] for event in self.hold('zoom', .3, pinch=.8)])

    # Zoom and scroll fixes (2026-09-25): a pinch moves the fingertip, not the palm.
    def test_pinch_zooms_although_the_fingertip_moves(self):
        self.hold('zoom', .6, palm_x=.5, palm_y=.5)
        events = []
        for step in range(8):  # the index tip travels toward the thumb; the palm stays put
            self.now += .05
            events += self.engine.update(frame('zoom', x=.5 - step * .01, pinch=1.0 - step * .1, palm_x=.5, palm_y=.5), self.now)
        self.assertIn(('zoom', -1), events)

    def test_zoom_out_continues_when_the_thumb_folds_into_the_pinch(self):
        self.hold('zoom', .6, pinch=1.0)
        first = self.hold('zoom', .25, pinch=.65)
        self.assertIn(('zoom', -1), first)
        self.assertIn(('zoom', -1), self.hold('point', .3, pinch=.2), 'the pinch reads as pointing but keeps zooming')

    def test_zoom_in_from_a_narrow_thumb_out_sign(self):
        # Fingertips touching are the pinch key now, so zoom in starts from a narrowed (not closed) L.
        self.hold('zoom', .7, pinch=.45)
        self.assertIn(('zoom', 1), self.hold('zoom', .3, pinch=.8))

    def test_still_pointing_with_thumb_flicker_does_not_zoom(self):
        events = []
        for pose, pinch in [('zoom', .9), ('point', .7), ('zoom', .9), ('point', .7)] * 5:
            events += self.hold(pose, .15, pinch=pinch)
        self.assertNotIn('zoom', [event[0] for event in events])

    def test_four_fingers_scroll_like_a_joystick(self):
        self.hold('four', .5, palm_y=.5)
        self.assertNotIn('scroll', [e[0] for e in self.hold('four', .5, palm_y=.51)], 'free play around the start')
        down = [e for e in self.hold('four', 1, palm_y=.65) if e[0] == 'scroll']
        self.assertTrue(len(down) >= 5 and all(e == ('scroll', -1) for e in down), down)
        up = [e for e in self.hold('four', 1, palm_y=.35) if e[0] == 'scroll']
        self.assertTrue(len(up) >= 5 and all(e == ('scroll', 1) for e in up), up)
        fast = [e for e in self.hold('four', 1, palm_y=.2) if e[0] == 'scroll']
        self.assertGreater(len(fast), len(up), 'farther = faster')

    def test_palm_closing_through_four_does_not_scroll(self):
        self.hold('palm', .6)
        events = self.hold('four', .6, palm_y=.7, curl=.3)
        self.assertNotIn('scroll', [e[0] for e in events])
        self.assertIn('minimize', [e[0] for e in self.hold('fist', .8)])

    # Grab and move (2026-09-25): thumb + index + middle open -> close = pick up; open again = drop.
    def names(self, events):
        return [e[0] for e in events]

    def test_three_fingers_close_grabs_moves_and_opening_drops(self):
        self.hold('point', .5)
        self.hold('three', .3)
        events = self.hold('fist', .6, palm_x=.4, palm_y=.5) + self.hold('fist', .4, palm_x=.6, palm_y=.4)
        names = self.names(events)
        self.assertEqual(names.count('grab'), 1)
        drags = [e[1] for e in events if e[0] == 'drag']
        self.assertTrue(len(drags) > 10 and drags[-1] == (.6, .4), drags[-3:])
        self.assertNotIn('minimize', names)
        dropped = self.names(self.hold('three', .3))
        self.assertEqual(dropped.count('drop'), 1)
        self.assertNotIn('restore', dropped + self.names(self.hold('palm', 1)))

    def test_grab_through_a_half_closed_shape(self):
        self.hold('three', .3)
        self.hold('neutral', .1)
        self.assertIn('grab', self.names(self.hold('fist', .3)))

    def test_losing_the_hand_while_carrying_drops(self):
        self.hold('three', .3)
        self.hold('fist', .3)
        self.now += .5
        self.assertEqual(self.names(self.engine.update(None, self.now)), ['drop'])

    # Thumb-out grab (2026-09-26): four fingers closed, thumb out = pick up; open palm = drop (here or on another device).
    def test_thumb_sign_grabs_carries_and_open_palm_drops(self):
        self.hold('point', .5)
        events = self.hold('thumb', .6, palm_x=.4, palm_y=.5) + self.hold('thumb', .4, palm_x=.6, palm_y=.4)
        names = self.names(events)
        self.assertEqual(names.count('grab'), 1)
        self.assertEqual([e[1] for e in events if e[0] == 'drag'][-1], (.6, .4))
        dropped = self.hold('palm', 1)
        self.assertEqual([e for e in dropped if e[0] == 'drop'], [('drop', None)])
        self.assertNotIn('restore', self.names(dropped))

    def test_thumb_sign_left_the_camera_is_carried_on(self):
        self.hold('thumb', .5)
        self.now += .5
        self.assertEqual(self.engine.update(None, self.now), [('drop', 'lost')])

    def test_brief_thumb_sign_does_nothing(self):
        self.assertEqual(self.names(self.hold('thumb', .2) + self.hold('palm', .3)), [])

    def test_fist_during_a_thumb_grab_keeps_carrying(self):
        self.hold('thumb', .5)
        self.assertNotIn('drop', self.names(self.hold('fist', .5) + self.hold('thumb', .3)))
        self.assertIn('drop', self.names(self.hold('four', .2)))

    def test_flick_click_with_thumb_out_still_clicks_and_never_grabs(self):
        self.hold('zoom', .5)
        self.hold('three', .15)
        names = self.names(self.hold('zoom', .3))
        self.assertIn('click', names)
        self.assertNotIn('grab', names)

    def test_right_click_is_the_thumb_folded_hold_only(self):
        self.hold('point', .5)
        self.assertIn('right-click', self.names(self.hold('two', .8)))
        self.hold('point', .5)
        self.assertNotIn('right-click', self.names(self.hold('three', .8)))

    def test_plain_fist_after_pointing_is_not_a_grab(self):
        self.hold('point', .5)
        self.assertNotIn('grab', self.names(self.hold('fist', .8)))

    def test_single_misread_frame_does_not_interrupt_pointing(self):
        self.hold('point', .6)
        self.now += .05
        events = self.engine.update(frame('two'), self.now)
        self.assertEqual([event[0] for event in events], ['move'])
        self.assertNotIn('click', [event[0] for event in self.hold('point', .3)])

    def test_thumb_tap_opens_at_tap_time(self):
        self.hold('point', .6, pinch=.9)
        start = self.now + .05
        events = [event for event in self.hold('point', .15, pinch=.2) + self.hold('point', .2, pinch=.9) if event[0] == 'open-target']
        self.assertEqual(events, [('open-target', start)])

    def test_half_raised_middle_finger_is_still_pointing(self):
        points = [P(x=.5, y=.9) for _ in range(21)]
        points[1] = P(x=.35, y=.8)
        for i, (x, y) in enumerate(((.28, .72), (.3, .6), (.4, .65)), 2):
            points[i] = P(x=x, y=y)
        for m, x in zip((5, 13, 17), (.35, .55, .65)):
            for i, y in enumerate((.65, .5, .35, .2) if m == 5 else (.65, .5, .58, .68)):
                points[m+i] = P(x=x, y=y)
        # Middle finger only partly raised: bent at the middle joint.
        for i, (x, y) in enumerate(((.45, .65), (.45, .5), (.49, .4), (.55, .33))):
            points[9+i] = P(x=x, y=y)
        self.assertEqual(describe(points)['pose'], 'point')

    def test_middle_finger_flick_clicks_once_at_flick_time(self):
        self.hold('point', .6)
        flick = self.now + .05
        events = self.hold('two', .2)
        events += self.hold('point', .3)
        self.assertEqual([event for event in events if event[0] == 'click'], [('click', flick)])
        self.assertNotIn('right-click', [event[0] for event in events])

    def test_double_flick_gives_two_clicks(self):
        self.hold('point', .6)
        self.hold('two', .15)
        self.hold('point', .3)
        self.hold('two', .15)
        self.assertEqual([event[0] for event in self.hold('point', .3)].count('click'), 1)

    def test_flick_between_click_and_right_click_windows_does_nothing(self):
        self.hold('point', .6)
        events = self.hold('two', .5)
        events += self.hold('point', .3)
        self.assertEqual([event[0] for event in events if event[0] in ('click', 'right-click')], [])

    def test_flick_requires_middle_finger_and_steady_point(self):
        self.hold('point', .6)
        self.hold('neutral', .15)
        self.assertNotIn('click', [event[0] for event in self.hold('point', .3)])
        self.hold('point', .6)
        self.hold('fist', .15)
        self.assertNotIn('click', [event[0] for event in self.hold('point', .3)])
        self.engine.reset()
        self.hold('point', .1)
        self.hold('two', .15)
        self.assertNotIn('click', [event[0] for event in self.hold('point', .3)])

    def test_flick_through_neutral_frames_still_clicks(self):
        self.hold('point', .6)
        self.hold('neutral', .05)
        self.hold('two', .1)
        self.hold('neutral', .05)
        self.assertIn('click', [event[0] for event in self.hold('point', .2)])

    def test_one_euro_filter_steadies_jitter_and_follows_motion(self):
        from cameo.gestures.classic import OneEuro
        steady, moving = OneEuro(), OneEuro()
        noisy = [100 + (3 if i % 2 else -3) for i in range(30)]
        out = [steady(value, i / 15) for i, value in enumerate(noisy)]
        self.assertLess(max(out[10:]) - min(out[10:]), 3)
        track = [moving(i * 40.0, i / 15) for i in range(30)]
        self.assertGreater(track[-1], 29 * 40 * .9)

    def test_no_actions_from_brief_poses(self):
        for pose in ('palm', 'fist', 'four'):
            self.assertEqual(self.hold(pose, .1), [])

    def test_degenerate_landmarks_do_not_activate(self):
        self.assertIsNone(describe([]))
        self.assertIsNone(describe([P(x=.5, y=.5)] * 21))

    def test_landmark_pose_classification(self):
        def hand(fingers, thumb):
            points = [P(x=.5, y=.9) for _ in range(21)]
            points[1] = P(x=.35, y=.8)
            shapes = {True: ((.28, .72), (.18, .63), (.09, .54)),          # held well out
                      'loose': ((.28, .72), (.22, .66), (.2, .56)),        # a little out, as in a loose fist
                      False: ((.28, .72), (.3, .6), (.4, .65))}
            for i, (x, y) in enumerate(shapes[thumb], 2):
                points[i] = P(x=x, y=y)
            for m, x, extended in zip((5, 9, 13, 17), (.35, .45, .55, .65), fingers):
                for i, y in enumerate((.65, .5, .35, .2) if extended else (.65, .5, .58, .68)):
                    points[m+i] = P(x=x, y=y)
            return points
        for expected, fingers, thumb in [('point', (1,0,0,0), False), ('two', (1,1,0,0), False),
                                          ('palm', (1,1,1,1), True), ('four', (1,1,1,1), False),
                                          ('fist', (0,0,0,0), False), ('zoom', (1,0,0,0), True),
                                          ('thumb', (0,0,0,0), True), ('fist', (0,0,0,0), 'loose')]:
            self.assertEqual(describe(hand(fingers, thumb))['pose'], expected)


if __name__ == '__main__':
    unittest.main()
