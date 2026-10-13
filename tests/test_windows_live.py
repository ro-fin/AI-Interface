"""CP1 evidence: real window actions (Win32 or X11) on a disposable window owned by a separate process."""
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.contracts import ActionResult, ContractError, Intent
from cameo.control import system
from cameo.control.windows import WindowBackend
from cameo.targets import TargetRegistry

TITLE = 'AETHER CP1 disposable test window'
WINDOW = f"import tkinter as t; r=t.Tk(); r.title({TITLE!r}); r.geometry('420x260+300+200'); r.after(60000, r.destroy); r.mainloop()"


class WindowBackendLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([sys.executable, '-c', WINDOW])
        cls.registry = TargetRegistry()
        for _ in range(100):
            matches = cls.registry.find(TITLE)
            if matches:
                break
            time.sleep(.1)
        assert matches, 'test window did not appear'
        cls.target = matches[0].id
        cls.backend = WindowBackend(cls.registry)

    @classmethod
    def tearDownClass(cls):
        cls.process.kill()

    def act(self, action):
        result = self.backend.act(Intent(action, self.target))
        self.assertEqual(result.status, 'verified', result.error)
        self.assertEqual(result.evidence['source'], system.SOURCE)
        return result

    def test_1_maximize_minimize_restore_activate_with_readback(self):
        self.assertTrue(self.act('window.maximize').evidence['after']['maximized'])
        self.assertTrue(self.act('window.minimize').evidence['after']['hidden'])
        self.assertEqual(self.backend.last_minimized(), self.target)
        after = self.act('window.restore').evidence['after']
        self.assertFalse(after['hidden'] or after['maximized'])
        self.assertTrue(self.act('window.activate').evidence['after']['active'])
        self.assertEqual(self.act('window.restore').evidence.get('note'), 'already in that state')

    def test_2_own_windows_are_never_targets(self):
        import tkinter
        root = tkinter.Tk()
        root.title('AETHER own window')
        root.update()
        try:
            self.assertEqual(self.registry.find('AETHER own window'), [])
        finally:
            root.destroy()

    def test_3_closed_window_is_target_gone(self):
        self.process.kill()
        self.process.wait()
        time.sleep(.3)
        result = self.backend.act(Intent('window.maximize', self.target))
        self.assertEqual((result.status, result.error['code']), ('failed', 'TARGET_GONE'))


class Contracts(unittest.TestCase):
    def test_honesty_and_validation(self):
        with self.assertRaises(ContractError):
            ActionResult('int-1', 'verified')  # verified without evidence
        with self.assertRaises(ContractError):
            Intent('shell.run', None)
        with self.assertRaises(ContractError):
            ActionResult('int-1', 'failed', error={'code': 'OOPS'})
        self.assertTrue(Intent('window.maximize', 'win:0x1').intent_id.startswith('int-'))


if __name__ == '__main__':
    unittest.main()
