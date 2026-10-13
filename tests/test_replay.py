"""Replay (phase 0): a recorded session replays to exactly the recorded events, and labels are scored per sign."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.gestures.classic import GestureEngine
from cameo.replay import report, run_engine, frames, score, signs


def session(frames_in):
    """Writes a trace the way the runtime does (feature, ft, events per frame) and returns its folder."""
    folder = Path(tempfile.mkdtemp(prefix='cameo-replay-'))
    engine = GestureEngine()
    with open(folder / 'events.jsonl', 'w', encoding='utf-8') as out:
        for t, feature in frames_in:
            events = engine.update(feature, t)
            out.write(json.dumps({'type': 'trace', 'ft': t, 'feature': feature, 'events': [e[0] for e in events]}) + '\n')
    return folder


def recording():
    """10 s: point, a volume line to the right, rest, a click flick, a circle."""
    import math
    out, t = [], 1000.0

    def add(pose, x=.5, y=.5, n=1, **extra):
        nonlocal t
        for _ in range(n):
            t += .04
            out.append((round(t, 3), {'pose': pose, 'x': x, 'y': y, 'pinch': .9, 'palm_x': x, 'palm_y': y, 'unit': .2, 'aspect': 1.0, **extra}))

    add('point', n=15)
    for i in range(26):
        add('two', x=.4 + .2 * i / 25)
    add('two', x=.6, n=15)                       # rest: commit 'still'
    add('point', x=.6, n=15)
    add('two', x=.6, n=4)                        # middle flick = click
    add('point', x=.6, n=15)
    for i in range(49):
        a = 2 * math.pi * i / 48
        add('two', x=.5 + .1 * math.cos(a), y=.5 + .1 * math.sin(a))
    add('point', n=10)
    return out


class ReplayTest(unittest.TestCase):
    def test_replay_reproduces_the_recording_exactly(self):
        folder = session(recording())
        recorded = frames(folder / 'events.jsonl')
        fired, same = run_engine(recorded)
        self.assertTrue(same)
        self.assertEqual([sign for _, sign in signs(fired)], ['volume-line', 'click', 'circle'])

    def test_labels_are_scored_per_sign(self):
        folder = session(recording())
        start = recording()[0][0]
        labels = [{'t': start + .6, 'sign': 'volume-line'}, {'t': start + 2.8, 'sign': 'click'},
                  {'t': start + 3.5, 'sign': 'right-click'}]           # meant, but never made
        (folder / 'labels.jsonl').write_text('\n'.join(json.dumps(label) for label in labels), encoding='utf-8')
        text, code = report(str(folder))
        self.assertIn('engine reproduces the recording exactly', text)
        self.assertEqual(code, 2, text)                                  # a miss and a misfire
        fired, _ = run_engine(frames(folder / 'events.jsonl'))
        table = score(signs(fired), labels)
        self.assertEqual((table['volume-line']['matched'], table['click']['matched']), (1, 1))
        self.assertEqual(table['right-click']['missed'], 1)
        self.assertEqual(table['circle']['misfired'], 1, 'fired but not labelled')

    def test_changed_engine_is_reported(self):
        folder = session(recording())
        lines = (folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()
        record = json.loads(lines[20])
        record['events'] = ['click']                                     # pretend the old engine fired something else
        lines[20] = json.dumps(record)
        (folder / 'events.jsonl').write_text('\n'.join(lines), encoding='utf-8')
        text, _ = report(str(folder))
        self.assertIn('DIFFERS', text)


if __name__ == '__main__':
    unittest.main()
