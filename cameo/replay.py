"""Replay a recorded session through the current sign engine (docs/SIGN-PROTOCOL-PLAN.md, phase 0 and R8).

Record:   python -m cameo run --trace          (every hand frame the engine saw goes into the session's events.jsonl)
Label:    write <session>/labels.jsonl, one line per sign you meant, e.g.
              {"t": 1790581524.1, "sign": "volume-line"}
              {"t": 1790581530.8, "sign": "click", "note": "on the Recycle Bin"}
          't' is the time (seconds since 1970, as in the trace) at or just before the sign; a sign counts as matched when
          the engine fires it within WINDOW seconds after. Sign names: see SIGN_NAMES.
Replay:   python -m cameo replay <session folder or name>   [--window 2.0]

Without labels the replay still checks that the engine reproduces the recorded events exactly (same code, same
frames = same signs) and lists every sign it fires, so a threshold change can be judged by what it would have done.
"""
import json
from pathlib import Path

from .eventlog import log_root
from .gestures.classic import GestureEngine

WINDOW = 2.0
# Engine events -> the sign a user means. Continuous events (move, drag, scroll notches, zoom notches, level control)
# collapse into the sign that started them.
SIGN_NAMES = {'click': 'click', 'right-click': 'right-click', 'open-target': 'open', 'minimize': 'minimize', 'restore': 'restore',
              'grab': 'grab', 'drop': 'drop', 'circle': 'circle', 'sleep': 'sleep', 'clap': 'clap', 'scroll': 'scroll', 'zoom': 'zoom'}
BURSTS = ('scroll', 'zoom')    # a run of these closer than BURST_GAP is one sign
BURST_GAP = .5


def session_path(name):
    path = Path(name)
    if not path.exists():
        path = log_root() / name
    if path.is_dir():
        path = path / 'events.jsonl'
    if not path.exists():
        raise FileNotFoundError(f'no session log at {path}')
    return path


def frames(path):
    """(t, feature, recorded event names) for every traced frame that has the engine input."""
    out = []
    with open(path, encoding='utf-8') as lines:
        for line in lines:
            record = json.loads(line)
            if record.get('type') == 'trace' and 'feature' in record:
                out.append((record['ft'], record['feature'], record.get('events', [])))
    return out


def run_engine(recorded):
    """Feed the frames to a fresh engine: [(t, event, value)] and whether it reproduced the recorded event names."""
    engine = GestureEngine()
    fired, same = [], True
    for t, feature, names in recorded:
        events = engine.update(feature, t)
        same = same and [name for name, _ in events] == names
        fired.extend((t, name, value) for name, value in events)
    return fired, same


def signs(fired):
    """Engine events -> [(t, sign)] as a user would name them."""
    out, last = [], {}
    for t, name, value in fired:
        if name == 'level' and value[0] == 'engage':
            out.append((t, f'{value[1]}-line'))
        elif name in BURSTS:
            if t - last.get(name, -100) > BURST_GAP:
                out.append((t, SIGN_NAMES[name]))
            last[name] = t
        elif name in SIGN_NAMES and name != 'drop':
            out.append((t, SIGN_NAMES[name]))
    return out


def score(found, labels, window=WINDOW):
    """Per sign: intended, matched, missed, misfired. A fired sign matches the first unmatched label of the same name
    that it follows within `window` seconds; everything else fired is a misfire."""
    table, used = {}, set()
    for label in labels:
        table.setdefault(label['sign'], {'intended': 0, 'matched': 0, 'missed': 0, 'misfired': 0, 'lag': []})['intended'] += 1
    for t, sign in found:
        row = table.setdefault(sign, {'intended': 0, 'matched': 0, 'missed': 0, 'misfired': 0, 'lag': []})
        match = next((i for i, label in enumerate(labels) if i not in used and label['sign'] == sign and 0 <= t - label['t'] <= window), None)
        if match is None:
            row['misfired'] += 1
        else:
            used.add(match)
            row['matched'] += 1
            row['lag'].append(t - labels[match]['t'])
    for row in table.values():
        row['missed'] = row['intended'] - row['matched']
    return table


def report(name, window=WINDOW, labels_path=None):
    path = session_path(name)
    recorded = frames(path)
    if not recorded:
        return f'{path}: no traced frames (record with: python -m cameo run --trace)', 1
    fired, same = run_engine(recorded)
    found = signs(fired)
    lines = [f'{path.parent.name}: {len(recorded)} frames, {recorded[-1][0] - recorded[0][0]:.1f} s',
             'engine reproduces the recording exactly' if same else 'engine DIFFERS from the recording (the code or thresholds changed since)']
    labels_file = Path(labels_path) if labels_path else path.parent / 'labels.jsonl'
    if not labels_file.exists():
        lines.append(f'no labels ({labels_file.name}); signs fired:')
        lines += [f'  {t - recorded[0][0]:7.2f} s  {sign}' for t, sign in found]
        return '\n'.join(lines), 0
    labels = [json.loads(line) for line in labels_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    table = score(found, labels, window)
    lines.append(f"{'sign':16} {'intended':>8} {'matched':>8} {'missed':>7} {'misfired':>9} {'lag s':>6}")
    for sign, row in sorted(table.items()):
        lag = f"{sum(row['lag']) / len(row['lag']):.2f}" if row['lag'] else '-'
        lines.append(f"{sign:16} {row['intended']:8} {row['matched']:8} {row['missed']:7} {row['misfired']:9} {lag:>6}")
    bad = sum(row['missed'] + row['misfired'] for row in table.values())
    return '\n'.join(lines), 0 if bad == 0 else 2
