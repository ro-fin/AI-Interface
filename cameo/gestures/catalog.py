"""Every hand sign in one table, built from the live constants (docs/SIGN-PROTOCOL-PLAN.md, phase 5).

GESTURES.md's sign table is this output (python -m cameo signs); tests/test_catalog.py fails when they differ, so the
document cannot drift from the code. Distances are palm widths (pw, about 8 cm), times seconds.
"""
from . import classic as c
from .signs import SPECS, Circle, Hold, LevelLine

VERIFIED = 'read back'
SENT = 'sent (no read-back exists)'


def _spec(kind, name=None):
    return next(s for s in SPECS if isinstance(s, kind) and (name is None or s.name == name))


def rows():
    volume, brightness = _spec(LevelLine, 'volume-line'), _spec(LevelLine, 'brightness-line')
    circle, sleep = _spec(Circle), _spec(Hold, 'sleep')

    def line(spec, directions, what):
        return (f'Index + middle up, draw a straight line {directions}',
                f'{spec.engage_pw:g} pw along the {"horizontal" if spec.axis == "x" else "vertical"}, drift ≤ {spec.slant:g} of the length, '
                f'turning ≤ {round(spec.turn * 57.2958)}°; then continuous. Ends: fingers released or still {spec.still:g} s = keep; '
                f'fist, or hand lost within {spec.lost_within:g} s of starting = back to where it was',
                f'{what}: {round(spec.gain * 100)} % per palm width from the level at the start, both ways; bar shows start, target, actual',
                VERIFIED)

    return [
        ('Point', 'every frame', 'Move the pointer (One Euro filtered); held still while a measured sign is in control', 'guarded input'),
        ('Point, flick the middle finger up and down', f'point ≥ {c.TAP_MIN_POINT:g} s, middle up < {c.TAP_MAX_FLICK:g} s',
         'Left click where the index pointed before the flick; two flicks = double-click', SENT),
        ('Hold index + middle still', f'{c.RIGHT_CLICK_HOLD:g} s, travel < {c.DRAW_MOVED:g} pw (arc fills)', 'Right click where the index pointed', SENT),
        ('Point, tap the thumb on the index', f'pinch closes below {c.KEY_CLOSED:g} and opens above {c.KEY_OPEN:g} within {c.KEY_HOLD:g} s, '
         f'palm travel < {c.KEY_STILL:g} pw', 'Open what is under the pointer (double-click; taskbar buttons single)', SENT),
        ('Open palm, then fist', f'palm {c.PALM_HOLD:g} s, fist {c.FIST_HOLD:g} s (arc fills), within 4 s', 'Minimize the window under the pointer', VERIFIED),
        ('Fist, then open palm', f'palm within {c.RESTORE_WINDOW:g} s of a new fist, held {c.PALM_HOLD:g} s', 'Restore the window last minimized by sign', VERIFIED),
        ('Thumb + index, pinch or spread', f'settle {c.ZOOM_SETTLE:g} s, hand still (< {c.ZOOM_STILL:g} pw), change ≥ {c.ZOOM_STEP:g}',
         'Zoom in / out (Ctrl+wheel) under the pointer', SENT),
        ('Four fingers, then move the hand up / down', f'four fingers {c.SCROLL_HOLD:g} s; free play {c.SCROLL_DEAD:g} pw',
         'Scroll under the pointer: ' + ', '.join(f'{rate:g}/s at {offset:g} pw' for offset, rate in c.SCROLL_RATES) + ' (exact at any frame rate)', SENT),
        line(volume, 'right / left', 'Volume up / down'),
        line(brightness, 'up / down', 'Brightness up / down'),
        ('Index + middle up, draw a circle', f'turn ≥ {round(circle.turn * 57.2958)}°, fitted radius ≥ {circle.radius:g} pw, '
         f'RMS off the circle ≤ {round(circle.residual * 100)} % of the radius',
         f'Clockwise opens a new Edge window, anticlockwise closes the ones Cameo opened; once per gesture, {circle.cooldown:g} s apart', VERIFIED),
        ('Thumb + index + middle out, then close to a fist', f'open ≥ {c.GRAB_MIN_OPEN:g} s', 'Grab the window or icon under the pointer; open the fingers to drop', VERIFIED),
        ('Four fingers closed, thumb out; open palm drops', f'thumb sign {c.THUMB_GRAB_HOLD:g} s (arc fills)', 'Grab and drop here or on a paired device', VERIFIED),
        ('Grab, then move the closed hand out of view', f'hand gone {c.DROPOUT:g} s while closed', 'Carry it to a paired device', VERIFIED),
        ('Little finger alone, held still', f'countdown from {sleep.engage_s:g} s, sleep at {sleep.commit_s:g} s; moving > {sleep.travel:g} pw or '
         'another pose cancels', 'The computer sleeps', SENT),
        ('Clap (two hands meet)', f'apart > {c.CLAP_APART:g} hand sizes, then touching within {c.CLAP_WINDOW:g} s',
         f'Screenshot to Pictures/Screenshots; other signs wait {c.TWO_HANDS_QUIET:g} s', VERIFIED),
    ]


def markdown():
    lines = ['| Sign | Rule | Action | Checked by |', '| --- | --- | --- | --- |']
    lines += [f'| {sign} | {rule} | {action} | {check} |' for sign, rule, action, check in rows()]
    return '\n'.join(lines)
