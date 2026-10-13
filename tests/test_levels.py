"""The levels controller (volume / brightness from signs): base read at engage, absolute sets, read-back, commit keeps,
cancel reverts, rate limit, honest failure. A fake device stands in for Core Audio / WMI, so no real level changes."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo import levels
from cameo.control import system


class FakeDevice:
    def __init__(self, level=.4, snap=None):
        self.level, self.snap, self.sets, self.muted = level, snap, [], False

    def read(self):
        return self.level

    def write(self, value):
        self.sets.append(round(value, 4))
        self.level = round(value / self.snap) * self.snap if self.snap else value
        return True


class LevelsTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.saved = dict(levels.QUANTITIES), system.muted, system.set_mute
        levels.QUANTITIES['volume'] = (self.device.read, self.device.write, 'volume.set', 'volume')
        system.muted = lambda: self.device.muted
        system.set_mute = lambda on: setattr(self.device, 'muted', on) or True
        self.results = []
        self.done = threading.Event()
        self.controller = levels.Levels(on_done=lambda intent, result: (self.results.append((intent, result)), self.done.set()))

    def tearDown(self):
        self.controller.close()
        quantities, system.muted, system.set_mute = self.saved
        levels.QUANTITIES.clear()
        levels.QUANTITIES.update(quantities)

    def wait(self):
        self.assertTrue(self.done.wait(3), 'no result')
        return self.results[-1]

    def test_commit_sets_base_plus_delta_and_verifies_by_read_back(self):
        self.controller.engage('volume', .1)
        for delta in (.12, .15, .2, .25):
            time.sleep(.05)
            self.controller.control(delta)
        self.controller.end(True, .25, 'released')
        intent, result = self.wait()
        self.assertEqual(intent.action, 'volume.set')
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.evidence['volume'], 65)   # base 40 % + 25 %
        self.assertEqual(result.evidence['base'], 40)
        self.assertAlmostEqual(self.device.level, .65)

    def test_cancel_goes_back_to_the_base(self):
        self.controller.engage('volume', .3)
        time.sleep(.15)
        self.controller.end(False, None, 'fist')
        _, result = self.wait()
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.evidence['volume'], 40)
        self.assertEqual(result.evidence['outcome'], 'cancel')
        self.assertAlmostEqual(self.device.level, .4)

    def test_target_is_clamped_to_the_range(self):
        self.controller.engage('volume', .9)
        self.controller.end(True, .9, 'released')
        _, result = self.wait()
        self.assertEqual(result.evidence['volume'], 100)
        self.assertTrue(all(0 <= value <= 1 for value in self.device.sets))

    def test_sets_are_rate_limited_and_coalesced(self):
        self.controller.engage('volume', 0)
        started = time.monotonic()
        while time.monotonic() - started < .5:
            self.controller.control((time.monotonic() - started) * .4)  # a new target every millisecond
            time.sleep(.001)
        self.controller.end(True, .2, 'released')
        self.wait()
        self.assertLessEqual(len(self.device.sets), levels.RATE * .5 + 3)

    def test_raising_a_muted_device_unmutes_it(self):
        self.device.muted = True
        self.controller.engage('volume', .1)
        self.controller.end(True, .1, 'released')
        self.wait()
        self.assertFalse(self.device.muted)

    def test_device_that_rounds_the_level_is_unverified(self):
        self.device.snap = .1  # the device only has 10 % steps: 40 + 25 = 65 reads back as 70 (or 60)
        self.controller.engage('volume', .25)
        self.controller.end(True, .25, 'released')
        _, result = self.wait()
        self.assertEqual(result.status, 'unverified')

    def test_no_control_fails_honestly(self):
        levels.QUANTITIES['volume'] = (lambda: None, lambda v: False, 'volume.set', 'volume')
        self.controller.engage('volume', .1)
        _, result = self.wait()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.error['code'], 'SESSION_UNSUPPORTED')

    def test_snapshot_shows_base_target_and_actual(self):
        self.controller.engage('volume', .1)
        time.sleep(.3)
        snap = self.controller.snapshot()
        self.assertAlmostEqual(snap['base'], .4)
        self.assertAlmostEqual(snap['target'], .5)
        self.assertAlmostEqual(snap['actual'], .5)
        self.controller.end(True, .1, 'still')
        self.wait()


if __name__ == '__main__':
    unittest.main()
