"""Integrated runtime without camera/microphone: injected gesture/HUD inputs drive real window actions
(Win32 or X11), and the HUD windows are click-through / non-activating (ARCHITECTURE.md §4.8)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

os.environ['CAMEO_IPC_NAME'] = f'cameo-test-{os.getpid()}'
os.environ['CAMEO_STATE_DIR'] = tempfile.mkdtemp(prefix='cameo-runtime-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.contracts import InputEvent
from cameo.control import system
from cameo.main import Runtime
from cameo.paths import WINDOWS

TITLE = 'AETHER runtime test window'
WINDOW = f"import tkinter as t; r=t.Tk(); r.title({TITLE!r}); r.geometry('500x320+200+200'); r.after(90000, r.destroy); r.mainloop()"


class RuntimeLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([sys.executable, '-c', WINDOW])
        cls.rt = Runtime(voice=False, hands=False, live=False)
        for _ in range(100):
            found = cls.rt.registry.find(TITLE)
            if found:
                break
            time.sleep(.1)
        cls.target = found[0].id
        cls.hwnd = int(cls.target[6:], 16)

    @classmethod
    def tearDownClass(cls):
        cls.rt.quit()
        cls.process.kill()

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.rt.root.update()
            time.sleep(.01)

    def gesture(self, **g):
        self.rt.events.put(InputEvent(source='gesture', kind='gesture', gesture=g, live=False))

    def test_1_hud_windows_never_take_focus_and_overlay_is_click_through(self):
        if WINDOWS:
            for window, expected in ((self.rt.hud.overlay, 0x08000000 | 0x20 | 0x80000), (self.rt.hud.rail, 0x08000000)):
                hwnd = system.ancestor(window.winfo_id(), 2)
                self.assertEqual(system.get_long(hwnd, -20) & expected, expected)
        else:
            frame = system.frame_of(self.rt.hud.overlay)
            self.assertEqual(list(frame.shape_get_rectangles(system.SHAPE_INPUT).rectangles), [], 'clicks pass through the overlay')
            for window in (self.rt.hud.overlay, self.rt.hud.rail, self.rt.hud.ball):
                self.assertTrue(window.overrideredirect(), 'the window manager never gives HUD windows focus')
        # AETHER's own HUD windows are never offered as targets; only the separate test window matches.
        self.assertEqual([w.id for w in self.rt.registry.find('AETHER')], [self.target])
        own = [t for t in self.rt.registry.find('CAMEO') if system.pid(int(t.id[6:], 16)) == os.getpid()]
        self.assertEqual(own, [], "Cameo's own rail is never a target")  # other apps may have 'Cameo' in a title

    def test_2_point_lock_then_dock_pinch_maximizes_with_evidence(self):
        g = system.geometry(self.hwnd)
        self.assertEqual(self.rt.targets_at(g['x'] + 100, g['y'] + 100), self.target, 'reticle over the window finds it through the overlay')
        self.gesture(name='dwell_lock', target=self.target)
        self.gesture(name='pinch_tap', on='dock:window.maximize')
        self.pump(1.5)
        self.assertTrue(system.is_zoomed(self.hwnd))
        self.assertIn('VERIFIED', self.rt.hud.log_rows[0].cget('text'))

    def test_3_dock_button_hit_test_and_mouse_press(self):
        button = self.rt.hud.buttons['dock:window.restore']
        self.pump(.2)
        x, y = button.winfo_rootx() + 5, button.winfo_rooty() + 5
        self.assertEqual(self.rt.targets_at(x, y), 'dock:window.restore')
        button.invoke()
        self.pump(1.5)
        self.assertFalse(system.is_zoomed(self.hwnd) or system.is_iconic(self.hwnd))

    def test_4_pause_blocks_actions_until_resumed(self):
        self.rt.ui('control.toggle')
        self.pump(.2)
        self.gesture(name='pinch_tap', on='dock:window.minimize')
        self.pump(1)
        self.assertFalse(system.is_iconic(self.hwnd))
        self.assertIn('paused', self.rt.hud.log_rows[0].cget('text').lower())
        self.rt.ui('control.toggle')
        self.gesture(name='pinch_tap', on='dock:window.minimize')
        self.pump(1.5)
        self.assertTrue(system.is_iconic(self.hwnd))
        self.rt.events.put(InputEvent(source='voice', kind='command', text='restore that', command={'action': 'window.restore', 'target': 'minimized', 'via': 'grammar'}, live=False))
        self.pump(1.5)
        self.assertFalse(system.is_iconic(self.hwnd))

    def test_6_rail_minimizes_to_a_ball_and_back(self):
        hud = self.rt.hud
        hud.minimize_button.invoke()
        self.pump(.3)
        self.assertEqual((hud.rail.state(), hud.ball.state()), ('withdrawn', 'normal'))
        button = hud.buttons['dock:window.restore']
        self.assertIsNone(hud.dock_at(button.winfo_rootx() + 5, button.winfo_rooty() + 5), 'no dock hits while collapsed')
        x, y = hud.ball.winfo_rootx(), hud.ball.winfo_rooty()
        self.assertGreater(x, hud.width - 120)
        self.assertGreater(y, hud.height - 200)
        hud.ball_canvas.event_generate('<Button-1>', x=20, y=20)
        self.pump(.3)
        self.assertEqual((hud.rail.state(), hud.ball.state()), ('normal', 'withdrawn'))

    def test_5_event_log_records_inputs_intents_and_results(self):
        lines = self.rt.log.path.read_text(encoding='utf-8').splitlines()
        kinds = {__import__('json').loads(line).get('type') for line in lines}
        self.assertTrue({'input', 'intent', 'action_result'} <= kinds, kinds)


if __name__ == '__main__':
    unittest.main()
