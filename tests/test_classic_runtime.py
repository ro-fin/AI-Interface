"""The user's classic signs through the full Cameo runtime, with real input (Win32 or X11) on a disposable window."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

os.environ['CAMEO_IPC_NAME'] = f'cameo-test-{os.getpid()}'
os.environ['CAMEO_STATE_DIR'] = tempfile.mkdtemp(prefix='cameo-classic-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.control import system
from cameo.control.pointer import cursor, screen_size
from cameo.main import Runtime
from cameo.gestures.classic import RESIZE_ENABLED

TITLE = 'AETHER classic signs test window'
WINDOW = (f"import tkinter as t; r=t.Tk(); r.title({TITLE!r}); r.geometry('520x340+220+180'); "
          "clicks=[]; r.bind('<Button-1>', lambda e: open(r'{log}', 'a').write('click\\n')); "
          "r.bind('<ButtonPress-1>', lambda e: open(r'{log}', 'a').write('press\\n'), add='+'); "
          "r.bind('<B1-Motion>', lambda e: open(r'{log}', 'a').write('motion\\n')); "
          "r.bind('<ButtonRelease-1>', lambda e: open(r'{log}', 'a').write('release\\n')); "
          "r.bind('<MouseWheel>', lambda e: open(r'{log}', 'a').write(('zoom' if e.state & 4 else 'wheel') + ('+' if e.delta > 0 else '-') + '\\n')); "
          # X11 (Tk 8.6): the wheel arrives as buttons 4 (up) and 5 (down).
          "[r.bind(f'<Button-{b}>', lambda e, s=s: open(r'{log}', 'a').write(('zoom' if e.state & 4 else 'wheel') + s + '\\n')) for b, s in ((4, '+'), (5, '-'))]; "
          "r.after(90000, r.destroy); r.mainloop()")


class ClassicSigns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clicks = Path(tempfile.mkdtemp()) / 'clicks.txt'
        cls.process = subprocess.Popen([sys.executable, '-c', WINDOW.replace('{log}', str(cls.clicks))])
        cls.rt = Runtime(voice=False, hands=False, live=False, gestures='classic')
        for _ in range(100):
            found = cls.rt.registry.find(TITLE)
            if found:
                break
            time.sleep(.1)
        cls.target = found[0].id
        cls.hwnd = int(cls.target[6:], 16)
        cls.previous = cursor()
        cls.t = time.time()

    @classmethod
    def tearDownClass(cls):
        cls.rt.quit()
        cls.process.kill()
        system.send([('move',) + tuple(cls.previous)])

    def frames(self, pose, seconds, at=None, curl=None, palm_shift=0, pinch=.9, palm_rise=0):
        """Feeds classic-sign frames; `at` is a screen point the index finger points to."""
        g = system.geometry(self.hwnd)
        sx, sy = at or (g['x'] + g['w'] // 2, g['y'] + g['h'] // 2)
        margin = self.rt.pointer.margin
        span = 1 - 2 * margin
        x, y = margin + span * sx / (screen_size()[0] - 1), margin + span * sy / (screen_size()[1] - 1)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.t = time.time()
            px = x + palm_shift * span / (screen_size()[0] - 1)  # palm centre moved by palm_shift screen pixels
            py = y - palm_rise * span / (screen_size()[1] - 1)  # palm raised by palm_rise screen pixels
            self.rt.frames.put({'t': self.t, 'hand': pose is not None, 'pose': pose, 'x': x, 'y': y, 'palm_x': px, 'palm_y': py, 'pinch': pinch, 'fps': 25, 'curl': curl})
            self.rt.root.update()
            time.sleep(.03)

    def test_1_point_moves_the_real_pointer(self):
        self.pump_pointer()
        g = system.geometry(self.hwnd)
        position = cursor()
        self.assertTrue(abs(position[0] - (g['x'] + g['w'] // 2)) < 12 and abs(position[1] - (g['y'] + g['h'] // 2)) < 12, position)

    def settle(self):
        end = time.monotonic() + .15  # the runtime loop runs every 15 ms
        while time.monotonic() < end:
            self.rt.root.update()
            time.sleep(.01)

    def pump_pointer(self):
        self.frames('point', 1.0)

    def test_2_middle_flick_clicks(self):
        count = lambda: self.clicks.read_text().split().count('click') if self.clicks.exists() else 0
        self.pump_pointer()
        before = count()
        self.frames('two', .15)
        self.frames('point', .3)
        self.assertEqual(count() - before, 1)

    def test_3_palm_fist_minimizes_and_fist_palm_restores_with_evidence(self):
        self.frames(None, .5)
        self.frames('point', .6)
        self.frames(None, .5)
        self.frames('palm', .7)
        self.frames('fist', .8)
        self.frames(None, 1.2)
        self.assertTrue(system.is_iconic(self.hwnd), self.rt.hud.log_rows[0].cget('text') if self.rt.hud.log_rows else '')
        self.assertIn('VERIFIED', self.rt.hud.log_rows[0].cget('text'))
        self.frames('fist', .6)
        self.frames('palm', .7)
        self.frames('palm', .3)
        self.assertFalse(system.is_iconic(self.hwnd))
        # The reported loop: minimize the same window again right after restoring it -> it must minimize.
        self.frames('fist', .8)
        self.frames('palm', .7)   # relaxing the minimize fist must not restore
        self.frames(None, 1.2)
        self.assertTrue(system.is_iconic(self.hwnd), 'second minimize after restore')
        self.frames('fist', .6)
        self.frames('palm', .7)
        self.frames(None, 1.2)
        self.assertFalse(system.is_iconic(self.hwnd))

    def test_5_hand_skeleton_and_sign_name_are_drawn_on_screen(self):
        points = [(.4 + .01 * i, .5 - .01 * i) for i in range(21)]
        self.rt.frames.put({'t': time.time(), 'hand': True, 'pose': 'point', 'x': .6, 'y': .3, 'pinch': .9, 'fps': 25, 'points': points})
        self.settle()
        canvas = self.rt.hud.canvas
        self.assertTrue(all(canvas.itemcget(item, 'state') == 'normal' for item in self.rt.hud.tips))
        self.assertEqual(canvas.itemcget(self.rt.hud.sign_label, 'text'), 'POINT')
        self.rt.frames.put({'t': time.time(), 'hand': False, 'fps': 25})
        self.settle()
        self.assertEqual(canvas.itemcget(self.rt.hud.tips[0], 'state'), 'hidden')

    @unittest.skipUnless(RESIZE_ENABLED, 'shrink dial paused')
    def test_6_partly_closed_palm_shrinks_and_full_fist_minimizes_at_full_size(self):
        if system.is_zoomed(self.hwnd):  # an earlier restore left it maximized; start from its normal size
            system.restore(self.hwnd)
        self.frames(None, .5)
        full = system.geometry(self.hwnd)
        self.frames('point', .6)
        self.frames('palm', .7, curl=0)
        self.frames('neutral', .5, curl=.45)
        half = system.geometry(self.hwnd)
        self.assertLess(half['w'], full['w'] * .8, (full, half))
        self.assertFalse(system.is_iconic(self.hwnd), 'shrinking is not minimizing')
        self.frames('palm', .5, curl=0)
        grown = system.geometry(self.hwnd)
        self.assertGreater(grown['w'], half['w'])
        self.frames('neutral', .4, curl=.6)
        self.frames('fist', .8, curl=1)
        self.frames(None, 1.2)
        self.assertTrue(system.is_iconic(self.hwnd), 'a full fist minimizes')
        self.frames('fist', .6, curl=1)
        self.frames('palm', .7, curl=0)
        self.frames('palm', .4, curl=0)
        self.frames(None, 1.0)
        self.assertFalse(system.is_iconic(self.hwnd))
        self.assertTrue(system.is_zoomed(self.hwnd), 'restore brings the window back maximized')
        system.restore(self.hwnd)
        self.frames(None, .5)
        back = system.geometry(self.hwnd)
        self.assertLessEqual(abs(back['w'] - full['w']), 4, ('its normal size is still the full original', full, back))

    @unittest.skipUnless(RESIZE_ENABLED, 'shrink dial paused')
    def test_7_shrink_while_moving_places_the_window_where_it_is_dropped(self):
        self.frames(None, .5)
        start = system.geometry(self.hwnd)
        hand = (start['x'] + start['w'] // 2, start['y'] + start['h'] // 2)  # a fixed place in the air, not the moving window
        self.frames('point', .6, at=hand)
        self.frames('palm', .7, at=hand, curl=0)
        for shift in range(0, 151, 10):  # the palm carries the window 150 px to the right while closing a little
            self.frames('neutral', .05, at=hand, curl=.4, palm_shift=shift)
        self.frames('neutral', .5, at=hand, curl=.4, palm_shift=150)
        self.frames("point", .4, at=hand, palm_shift=150)  # drop: the palm is still where it carried the window
        self.frames(None, .6)
        placed = system.geometry(self.hwnd)
        moved = (placed['x'] + placed['w'] / 2) - (start['x'] + start['w'] / 2)
        self.assertLess(placed['w'], start['w'], (start, placed))
        self.assertTrue(110 <= moved <= 190, ('window centre follows the palm', start, placed, moved))
        self.assertIsNone(self.rt.anim, 'the glide has finished')
        self.frames(None, .5)
        self.assertEqual(system.geometry(self.hwnd), placed, 'it stays where it was dropped')

    def test_4_paused_hand_does_nothing(self):
        self.rt.ui('control.toggle')
        self.frames(None, .3)
        before = cursor()
        self.frames('point', .6, at=(before[0] + 200, before[1]))
        self.assertEqual(cursor(), before)
        self.rt.ui('control.toggle')
        self.frames(None, .3)


    def wheel_log(self):
        return [line for line in (self.clicks.read_text().split() if self.clicks.exists() else []) if line[:-1] in ('wheel', 'zoom')]

    def test_8_four_fingers_scroll_the_window_under_the_pointer(self):
        self.pump_pointer()
        before = len(self.wheel_log())
        self.frames('four', .5)
        self.frames('four', 1.0, palm_rise=-120)   # hand lowered: scroll down
        self.frames('four', 1.0, palm_rise=120)    # hand raised: scroll up
        self.frames('point', .3)
        log = self.wheel_log()[before:]
        self.assertGreaterEqual(log.count('wheel-'), 2, log)  # Tk joins half notches into whole ones
        self.assertGreaterEqual(log.count('wheel+'), 2, log)
        self.assertNotIn('zoom+', log)

    def test_9_pinch_zooms_in_and_out_with_ctrl_wheel(self):
        self.pump_pointer()
        before = len(self.wheel_log())
        self.frames('zoom', .7, pinch=1.0)
        self.frames('zoom', .3, pinch=.6)          # pinching: zoom out
        self.frames('point', .3, pinch=.2)         # the folded thumb reads as pointing; still the same pinch
        self.frames('zoom', .7, pinch=.2)
        self.frames('zoom', .4, pinch=.8)          # spreading: zoom in
        log = self.wheel_log()[before:]
        self.assertIn('zoom-', log)
        self.assertIn('zoom+', log)
        self.assertNotIn('wheel+', log)

    def lines(self):
        return self.clicks.read_text().split() if self.clicks.exists() else []

    def test_10_grab_carries_a_window_and_drops_it_there(self):
        if system.is_zoomed(self.hwnd):
            system.restore(self.hwnd)
        self.frames(None, .5)
        start = system.geometry(self.hwnd)
        hand = (start['x'] + start['w'] // 2, start['y'] + start['h'] // 2)
        self.frames('point', .6, at=hand)
        self.frames('three', .3, at=hand)
        self.frames('fist', .3, at=hand)
        for shift in range(0, 201, 20):
            self.frames('fist', .04, at=hand, palm_shift=shift)
        self.frames('fist', .5, at=hand, palm_shift=200)
        self.frames('three', .4, at=hand, palm_shift=200)   # open: drop
        self.frames('point', .6, at=hand, palm_shift=200)
        placed = system.geometry(self.hwnd)
        self.assertTrue(150 <= placed['x'] - start['x'] <= 230, (start, placed))
        self.assertEqual((placed['w'], placed['h']), (start['w'], start['h']), 'moving never resizes')
        self.assertFalse(system.is_iconic(self.hwnd), 'the grab fist never minimizes')
        self.assertIsNone(getattr(self.rt, 'grab', None))

    def test_11_grab_off_a_window_is_a_real_mouse_drag_and_always_releases(self):
        registry_at = self.rt.registry.at
        self.rt.registry.at = lambda x, y: None  # behave as on the desktop, but drag inside the test window
        try:
            before = len(self.lines())
            self.frames(None, .5)
            self.frames('point', .6)
            self.frames('three', .3)
            self.frames('fist', .3)
            for shift in range(0, 81, 10):
                self.frames('fist', .04, palm_shift=shift)
            self.frames('three', .4, palm_shift=80)
            log = self.lines()[before:]
            self.assertIn('press', log)
            self.assertGreater(log.count('motion'), 2, log)
            self.assertEqual(log[-1] if log else None, 'release', log)
            self.assertFalse(self.rt.pointer.dragging)
            # Losing the hand while dragging must also release the button.
            self.frames('point', .6)
            self.frames('three', .3)
            self.frames('fist', .4)
            self.frames(None, .8)
            self.assertFalse(system.held(('left',)), 'left button is not left held')
            self.assertEqual(self.lines()[-1], 'release')
        finally:
            self.rt.registry.at = registry_at

    def stroke(self, pose, dx, dy, steps=20):
        """A straight stroke of the index fingertip, in camera frame widths / heights (mirrored, as the user sees it)."""
        x, y = .5 - dx / 2, .5 - dy / 2
        for i in range(steps + 1):
            self.t = time.time()
            self.rt.frames.put({'t': self.t, 'hand': True, 'pose': pose, 'x': x + dx * i / steps, 'y': y + dy * i / steps, 'pinch': .9, 'fps': 25})
            self.rt.root.update()
            time.sleep(.03)

    def hold_at(self, pose, x, y, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.t = time.time()
            self.rt.frames.put({'t': self.t, 'hand': True, 'pose': pose, 'x': x, 'y': y, 'pinch': .9, 'fps': 25})
            self.rt.root.update()
            time.sleep(.03)

    def test_12_drawn_lines_set_brightness_and_volume_with_read_back(self):
        # Fake devices: the test must not change the real brightness or volume.
        device = {'volume': .4, 'brightness': 50, 'muted': False}
        names = ('volume', 'set_volume', 'muted', 'set_mute', 'brightness', 'set_brightness')
        saved = {name: getattr(system, name) for name in names}
        system.volume = lambda: device['volume']
        system.set_volume = lambda level: device.update(volume=level) or True
        system.muted = lambda: device['muted']
        system.set_mute = lambda on: device.update(muted=on) or True
        system.brightness = lambda: device['brightness']
        system.set_brightness = lambda percent: device.update(brightness=percent) or True
        results = []
        show = self.rt.show_result
        self.rt.show_result = lambda intent, result: (results.append((intent.action, result)), show(intent, result))
        try:
            self.frames(None, .5)
            self.stroke('two', 0, -.2)     # up one palm width (default .2 of the frame): brightness + 40 %
            coords = self.rt.hud.canvas.coords(self.rt.hud.trail_line)
            self.assertEqual(len(coords), 4, 'shown as one straight segment, not the fingertip path')
            self.assertEqual(coords[0], coords[2], 'vertical')
            self.assertEqual(self.rt.hud.canvas.itemcget(self.rt.hud.level_items['text'], 'state'), 'normal', 'level bar shown')
            self.hold_at('two', .5, .4, .7)  # the fingers rest where the line ended: committed, read back and reported
            self.frames(None, .8)
            self.assertEqual(device['brightness'], 90)
            self.stroke('two', .2, 0)      # right one palm width: volume + 25 %
            self.hold_at('two', .6, .5, .7)
            self.frames(None, .8)
            self.assertAlmostEqual(device['volume'], .65, places=3)
            actions = [(action, result.status, result.evidence) for action, result in results]
            self.assertIn('display.brightness_set', [a for a, _, _ in actions])
            self.assertIn('volume.set', [a for a, _, _ in actions])
            self.assertTrue(all(status == 'verified' for _, status, _ in actions), actions)
        finally:
            for name, value in saved.items():
                setattr(system, name, value)
            self.rt.show_result = show

if __name__ == '__main__':
    unittest.main()
