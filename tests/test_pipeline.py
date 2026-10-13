"""Intent pipeline rules: deixis, context, pause, fusion, conflicts, idempotency (ARCHITECTURE.md §4.5, §5.5)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.contracts import InputEvent, Target
from cameo.pipeline import IntentPipeline


class Registry:
    def __init__(self):
        self.windows = {'win:0x1': 'window', 'win:0x2': 'window', 'win:0xb': 'browser_window'}

    def get(self, target):
        return target if target in self.windows else None

    def list(self):
        return [Target(id=k, kind=v, app='msedge' if v == 'browser_window' else 'app', title=k, geometry={}, state={'active': k == 'win:0xb'}) for k, v in self.windows.items()]


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


def voice(action, target=None):
    return InputEvent(source='voice', kind='command', text=action, command={'action': action, 'target': target, 'via': 'grammar'}, live=False)


def gesture(name, **fields):
    return InputEvent(source='gesture', kind='gesture', gesture={'name': name, **fields}, live=False)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.clock, self.minimized = Clock(), []
        self.pipe = IntentPipeline(Registry(), minimized=lambda: self.minimized[-1] if self.minimized else None, clock=self.clock)

    def runs(self, decisions):
        return [(d[1].action, d[1].target, d[1].target_basis) for d in decisions if d[0] == 'run']

    def test_this_uses_a_fresh_gesture_lock_over_context(self):
        self.pipe.submit(gesture('dwell_lock', target='win:0x1'))
        self.assertEqual(self.runs(self.pipe.submit(voice('window.maximize', 'deictic'))), [('window.maximize', 'win:0x1', 'gesture_lock')])
        self.clock.now += 5
        self.assertEqual(self.runs(self.pipe.submit(voice('window.minimize', 'deictic'))), [('window.minimize', 'win:0x1', 'context')])

    def test_this_without_any_target_asks(self):
        decision = self.pipe.submit(voice('window.maximize', 'deictic'))[0]
        self.assertEqual(decision[:2], ('reject', 'TARGET_AMBIGUOUS'))

    def test_restore_that_uses_the_minimize_stack(self):
        self.assertEqual(self.pipe.submit(voice('window.restore', 'minimized'))[0][1], 'TARGET_GONE')
        self.minimized.append('win:0x2')
        self.assertEqual(self.runs(self.pipe.submit(voice('window.restore', 'minimized'))), [('window.restore', 'win:0x2', 'context')])

    def test_the_browser_resolves_to_the_browser_window(self):
        self.assertEqual(self.runs(self.pipe.submit(voice('window.maximize', 'browser'))), [('window.maximize', 'win:0xb', 'explicit')])

    def test_pause_blocks_everything_but_resume(self):
        self.pipe.submit(voice('control.pause'))
        self.assertEqual(self.pipe.submit(voice('tab.new'))[0][1], 'PAUSED')
        self.assertEqual(self.pipe.submit(gesture('pinch_tap', on='dock:tab.new'))[0][1], 'PAUSED')
        self.pipe.submit(gesture('palm_resume'))
        self.assertEqual(self.runs(self.pipe.submit(voice('tab.new'))), [('tab.new', None, 'default')])

    def test_same_modality_repeats_run_twice(self):
        self.assertEqual(len(self.runs(self.pipe.submit(voice('tab.next')))), 1)
        self.assertEqual(len(self.runs(self.pipe.submit(voice('tab.next')))), 1)

    def test_voice_and_gesture_for_the_same_action_merge_into_one(self):
        self.pipe.submit(gesture('dwell_lock', target='win:0x1'))
        first = self.pipe.submit(voice('window.maximize', 'deictic'))
        self.clock.now += .5
        second = self.pipe.submit(gesture('pinch_tap', on='dock:window.maximize'))
        self.assertEqual(len(self.runs(first)), 1)
        self.assertEqual(self.runs(second), [])
        self.assertIn('merged', second[0][1])

    def test_conflicting_modalities_wait_for_do_it_or_cancel(self):
        self.pipe.submit(gesture('dwell_lock', target='win:0x1'))
        self.pipe.submit(voice('window.minimize', 'deictic'))
        self.clock.now += .4
        card = self.pipe.submit(gesture('pinch_tap', on='dock:window.maximize'))
        self.assertEqual(card[0][0], 'card')
        self.assertEqual(self.runs(self.pipe.submit(voice('control.confirm'))), [('window.maximize', 'win:0x1', 'gesture_lock')])
        self.assertEqual(self.pipe.submit(voice('control.confirm'))[0][1], 'NOT_A_COMMAND')

    def test_an_input_never_executes_twice(self):
        event = voice('tab.new')
        self.assertEqual(len(self.runs(self.pipe.submit(event))), 1)
        self.assertEqual(self.pipe.submit(event), [])


if __name__ == '__main__':
    unittest.main()
