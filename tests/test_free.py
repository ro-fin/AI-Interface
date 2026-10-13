"""Free-speech voice commands: open any app or website, play on YouTube, search (English + Tanglish)."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ['CAMEO_STATE_DIR'] = tempfile.mkdtemp(prefix='cameo-free-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.contracts import Intent
from cameo.control import apps, system
from cameo.control.apps import AppIndex, open_app
from cameo.paths import WINDOWS
from cameo.targets import TargetRegistry
from cameo.voice import grammar
from cameo.voice.free import parse

APPS = [{'name': n, 'id': f'id.{i}'} for i, n in enumerate(
    ['Microsoft Teams', 'Visual Studio Code', 'Notepad', 'Calculator', 'File Explorer', 'Microsoft Edge', 'WhatsApp',
     'Uninstall Python', 'Python 3.12 (64-bit)', 'Microsoft Store', 'Settings', 'Snipping Tool'])]
LINUX_APPS = [{'name': n, 'id': f'/usr/share/applications/id{i}.desktop'} for i, n in enumerate(
    ['Files', 'Text Editor', 'Terminal', 'Firefox Web Browser', 'Visual Studio Code', 'System Monitor', 'App Center', 'Calculator'])]


class Parsing(unittest.TestCase):
    def test_open_any_app_english_and_tanglish(self):
        for spoken, name in [('Hey Cameo, open Teams.', 'teams'), ('hey cameo open team', 'team'), ('Launch Visual Studio Code', 'visual studio code'),
                             ('Teams open pannu', 'teams'), ('notepad ah open pannu', 'notepad'), ('open calculator app', 'calculator'),
                             ('Start WhatsApp', 'whatsapp'), ('open youtube', 'youtube')]:
            self.assertEqual(parse(spoken), {'action': 'app.open', 'target': None, 'args': {'name': name}}, spoken)

    def test_play_on_youtube(self):
        for spoken, query in [('Play Tamil kuthu song in YouTube.', 'Tamil kuthu song'), ('hey cameo play anirudh songs', 'anirudh songs'),
                              ('YouTube la kuthu song podu', 'kuthu song'), ('vijay songs podu', 'vijay songs')]:
            self.assertEqual(parse(spoken), {'action': 'media.play', 'target': None, 'args': {'query': query, 'site': 'youtube'}}, spoken)

    def test_search(self):
        self.assertEqual(parse('search weather in chennai')['args'], {'query': 'weather in chennai', 'site': 'google'})
        self.assertEqual(parse('search lofi songs on youtube')['args'], {'query': 'lofi songs', 'site': 'youtube'})

    def test_grammar_commands_stay_with_the_grammar(self):
        for spoken in ('open it', 'open the browser', 'new tab', 'adutha tab', 'maximize this window', ''):
            self.assertIsNone(parse(spoken), spoken)

    def test_open_it_is_not_accepted_from_open_teams(self):
        self.assertFalse(grammar.corroborated('open it', 'open teams'))
        self.assertTrue(grammar.corroborated('open it', 'open it'))
        self.assertTrue(grammar.corroborated('new tab', 'new tab'))


class Matching(unittest.TestCase):
    def setUp(self):
        self.index = AppIndex(load=False)
        self.index.apps = APPS

    def test_spoken_names_find_the_start_menu_app(self):
        cases = [('teams', 'Microsoft Teams'), ('team', 'Microsoft Teams'), ('vs code', 'Visual Studio Code'),
                 ('visual studio', 'Visual Studio Code'), ('vishual studio code', 'Visual Studio Code'),
                 ('calculater', 'Calculator'), ('whats app', 'WhatsApp'), ('python', 'Python 3.12 (64-bit)')]
        if WINDOWS:  # on Linux these spoken names mean the GNOME apps (next test)
            cases += [('explorer', 'File Explorer'), ('store', 'Microsoft Store'), ('snipping tool', 'Snipping Tool')]
        for spoken, name in cases:
            self.assertEqual((self.index.match(spoken) or {}).get('name'), name, spoken)

    @unittest.skipIf(WINDOWS, 'Linux names')
    def test_windows_words_find_the_linux_apps(self):
        self.index.apps = LINUX_APPS
        for spoken, name in [('explorer', 'Files'), ('file manager', 'Files'), ('notepad', 'Text Editor'), ('cmd', 'Terminal'),
                             ('browser', 'Firefox Web Browser'), ('task manager', 'System Monitor'), ('store', 'App Center'),
                             ('vs code', 'Visual Studio Code'), ('calculator', 'Calculator')]:
            self.assertEqual((self.index.match(spoken) or {}).get('name'), name, spoken)

    def test_unknown_names_are_not_guessed(self):
        for spoken in ('youtube', 'chrome', 'photoshop', ''):
            self.assertIsNone(self.index.match(spoken), spoken)


@unittest.skipIf(WINDOWS, 'Linux desktop entries')
class LinuxEntries(unittest.TestCase):
    TITLE = 'Cameo desktop entry test'

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        window = f"import tkinter as t; r=t.Tk(); r.title({self.TITLE!r}); r.after(20000, r.destroy); r.mainloop()"
        (self.folder / 'cameo-test.desktop').write_text(
            f'[Desktop Entry]\nType=Application\nName={self.TITLE}\nExec={sys.executable} -c "{window}" %U\n', encoding='utf-8')
        (self.folder / 'hidden.desktop').write_text('[Desktop Entry]\nType=Application\nName=Hidden\nExec=true\nNoDisplay=true\n', encoding='utf-8')
        self.saved = apps.DESKTOP_DIRS
        apps.DESKTOP_DIRS = [str(self.folder)]

    def tearDown(self):
        apps.DESKTOP_DIRS = self.saved

    def test_menu_entries_are_indexed_and_opened_with_a_verified_window(self):
        self.assertEqual([a['name'] for a in apps.linux_apps()], [self.TITLE], 'NoDisplay entries are left out')
        self.assertEqual(apps.linux_command(str(self.folder / 'cameo-test.desktop'))[-2:][0], '-c', 'the %U field code is dropped')
        index = AppIndex(load=False)
        index.refresh()
        result = open_app(index, TargetRegistry(), Intent('app.open', None, 'default', args={'name': 'desktop entry test'}))
        self.assertEqual(result.status, 'verified', result.evidence)
        system.close(int(result.evidence['window'][6:], 16))


class OpenLive(unittest.TestCase):
    @unittest.skipUnless(WINDOWS, 'Notepad is a Windows app')
    def test_open_notepad_is_verified_by_its_window(self):
        registry = TargetRegistry()
        before = {t.id for t in registry.list() if 'notepad' in (t.app + t.title).lower()}
        index = AppIndex(load=False)
        index.refresh()
        result = open_app(index, registry, Intent('app.open', None, 'default', args={'name': 'notepad'}))
        self.assertEqual(result.status, 'verified', result.evidence)
        opened = result.evidence['window']
        if opened not in before:
            system.close(int(opened[6:], 16))  # close the window this test opened
            time.sleep(.5)

    def test_unknown_app_fails_honestly(self):
        index = AppIndex(load=False)
        index.apps = APPS
        result = open_app(index, TargetRegistry(), Intent('app.open', None, 'default', args={'name': 'photoshop'}))
        self.assertEqual((result.status, result.error['code']), ('failed', 'TARGET_GONE'))


if __name__ == '__main__':
    unittest.main()
