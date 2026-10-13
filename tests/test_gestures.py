"""ARCHITECTURE.md §5.4 gesture safety rules, replayed as timed frame sequences (live=False)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.engine import GestureEngine

SCREEN = (1000, 1000)
DOCK = {'dock:window.maximize': (900, 100, 1000, 200), 'dock:tab.close': (900, 300, 1000, 400)}


def targets_at(x, y):
    for name, (x0, y0, x1, y1) in DOCK.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return 'win:0x00000abc' if x < 500 else 'win:0x00000def'


def cam(sx, sy):
    """Screen position -> camera coordinates inside the active box (inner 60 %)."""
    return .2 + .6 * sx / (SCREEN[0] - 1), .2 + .6 * sy / (SCREEN[1] - 1)


class Rig:
    def __init__(self):
        self.events = []
        self.engine = GestureEngine(self.events.append, targets_at, SCREEN, live=False)
        self.t = 0.0

    def frames(self, seconds, at=(250, 500), pinch=1.0, palm=False, hand=True, speed=0.0, fps=20):
        x, y = cam(*at)
        for _ in range(round(seconds * fps)):
            self.t += 1 / fps
            self.engine.update({'t': self.t, 'hand': hand, 'conf': .9, 'x': x, 'y': y, 'pinch': pinch, 'palm': palm, 'speed': speed})

    def names(self):
        return [e.gesture['name'] for e in self.events]

    def engage(self, at=(250, 500)):
        self.frames(.5, at)
        assert 'engaged' in self.names()


class Gestures(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_engage_needs_eight_frames_inside_the_active_box(self):
        self.rig.engine.update({'t': 0, 'hand': True, 'conf': .9, 'x': .05, 'y': .5})
        self.rig.frames(.35)  # 7 frames
        self.assertNotIn('engaged', self.rig.names())
        self.rig.frames(.05)
        self.assertIn('engaged', self.rig.names())

    def test_dwell_locks_after_300_ms_only(self):
        self.rig.engage()
        self.rig.frames(.25, at=(700, 500))
        self.assertNotIn('dwell_lock', self.rig.names())
        self.rig.frames(.2, at=(700, 500))
        locks = [e.gesture['target'] for e in self.rig.events if e.gesture['name'] == 'dwell_lock']
        self.assertEqual(locks[-1], 'win:0x00000def')

    def test_pinch_tap_presses_a_dock_button(self):
        self.rig.engage(at=(950, 150))
        self.rig.frames(.2, at=(950, 150), pinch=.1)
        self.rig.frames(.1, at=(950, 150), pinch=.8)
        taps = [e.gesture for e in self.rig.events if e.gesture['name'] == 'pinch_tap']
        self.assertEqual(taps, [{'name': 'pinch_tap', 'on': 'dock:window.maximize'}])

    def test_consequential_button_needs_a_600_ms_hold_released_over_it(self):
        self.rig.engage(at=(950, 350))
        self.rig.frames(.2, at=(950, 350), pinch=.1)
        self.rig.frames(.1, at=(950, 350), pinch=.8)
        self.assertNotIn('pinch_tap', self.rig.names(), 'a quick tap never closes a tab')
        self.rig.frames(.7, at=(950, 350), pinch=.1)
        self.rig.frames(.1, at=(950, 350), pinch=.8)
        holds = [e.gesture for e in self.rig.events if e.gesture['name'] == 'pinch_hold']
        self.assertEqual(holds, [{'name': 'pinch_hold', 'on': 'dock:tab.close'}])

    def test_hold_released_away_cancels_and_never_taps(self):
        self.rig.engage(at=(950, 350))
        self.rig.frames(.8, at=(950, 350), pinch=.1)
        self.rig.frames(.4, at=(200, 350), pinch=.1)
        self.rig.frames(.1, at=(200, 350), pinch=.8)
        names = self.rig.names()
        self.assertNotIn('pinch_hold', names)
        self.assertNotIn('pinch_tap', names)
        self.assertIn('cancelled', names)

    def test_dead_band_between_tap_and_hold_does_nothing(self):
        self.rig.engage(at=(950, 150))
        before = len(self.rig.events)
        self.rig.frames(.42, at=(950, 150), pinch=.1)
        self.rig.frames(.1, at=(950, 150), pinch=.8)
        self.assertEqual([n for n in self.rig.names()[before:] if n in ('pinch_tap', 'pinch_hold')], [])

    def test_pinch_hysteresis_ignores_jitter_between_thresholds(self):
        self.rig.engage(at=(950, 150))
        self.rig.frames(.1, at=(950, 150), pinch=.1)
        for value in (.3, .27, .33, .29):
            self.rig.frames(.05, at=(950, 150), pinch=value)
        self.assertTrue(self.rig.engine.pinched)

    def test_held_palm_pauses_exactly_once_then_resume_needs_release_and_1_5_s(self):
        self.rig.engage()
        self.rig.frames(5, palm=True)
        self.assertEqual(self.rig.names().count('palm_pause'), 1, 'a continuously held palm toggles once')
        self.assertTrue(self.rig.engine.paused)
        self.rig.frames(.6)                   # release (latch clears after 0.5 s)
        self.rig.frames(1.2, palm=True)
        self.assertTrue(self.rig.engine.paused, 'resume needs 1.5 s')
        self.rig.frames(.4, palm=True)
        self.assertFalse(self.rig.engine.paused)
        self.assertEqual(self.rig.names().count('palm_resume'), 1)

    def test_moving_palm_does_not_pause(self):
        self.rig.engage()
        self.rig.frames(2, palm=True, speed=.5)
        self.assertNotIn('palm_pause', self.rig.names())

    def test_paused_ignores_pinches(self):
        self.rig.engage(at=(950, 150))
        self.rig.frames(1.1, at=(950, 150), palm=True)
        self.rig.frames(.2, at=(950, 150), pinch=.1)
        self.rig.frames(.1, at=(950, 150), pinch=.8)
        self.assertNotIn('pinch_tap', self.rig.names())

    def test_tracking_loss_freezes_then_cancels_the_hold(self):
        self.rig.engage(at=(950, 350))
        self.rig.frames(.4, at=(950, 350), pinch=.1)
        self.rig.frames(1.0, hand=False)
        self.assertTrue(self.rig.engine.pinched, 'short loss freezes, it is not a release')
        self.rig.frames(.7, hand=False)
        self.assertFalse(self.rig.engine.pinched)
        self.assertIn('cancelled', self.rig.names())
        self.assertNotIn('pinch_hold', self.rig.names())
        self.rig.frames(.2, at=(950, 350))
        self.assertFalse(self.rig.engine.engaged, 're-engagement needs 8 frames')


if __name__ == '__main__':
    unittest.main()
