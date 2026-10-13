"""Microsoft Teams messages through Windows UI Automation (no blind key presses).

draft(): open Teams, find the person, open the chat, type the message into the compose box and read
it back. Nothing is sent. send(): press Enter in that compose box, then read back that the box is
empty and the message is in the conversation. Honesty rule: 'verified' only with that read-back.

Windows only: Linux has no Teams desktop app and no UI Automation to read the compose box back, so
there both actions fail with SESSION_UNSUPPORTED instead of typing blindly.
"""
import re
import subprocess
import time

from ..contracts import ActionResult, failure
from ..paths import WINDOWS

LAUNCH = r'shell:AppsFolder\MSTeams_8wekyb3d8bbwe!MSTeams'
COMPOSE = r'(?i).*(type a message|type a new message|type your message|reply|start a new conversation).*'
UNSUPPORTED = 'Teams messages need Windows (no Teams desktop app or UI Automation on Linux)'


def auto():
    import uiautomation
    return uiautomation


def type_text(text):
    """Unicode key events, one character at a time (the clipboard is never touched)."""
    from . import win32
    win32.send([('char', ch) for ch in text])


def press(*keys):
    from . import win32
    win32.send([('key', k, True) for k in keys] + [('key', k, False) for k in reversed(keys)])


def value_of(control):
    for getter in (lambda c: c.GetValuePattern().Value, lambda c: c.GetTextPattern().DocumentRange.GetText(-1),
                   lambda c: c.GetLegacyIAccessiblePattern().Value):
        try:
            text = getter(control)
            if text is not None:
                return text.replace('￼', '').strip()
        except Exception:
            continue
    return ''


class Teams:
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.chat = None  # {'to', 'text'} of the prepared draft

    # -- finding things ---------------------------------------------------------------------
    def window(self):
        ua = auto()
        w = ua.WindowControl(searchDepth=1, RegexName=r'.*Microsoft Teams.*')
        return w if w.Exists(0, 0) else None

    def find(self, root, types, regex, wait=0.0):
        ua = auto()
        pattern = re.compile(regex)
        end = time.monotonic() + wait
        while True:
            for control, _ in ua.WalkControl(root, maxDepth=45):
                if control.ControlTypeName in types and control.Name and pattern.match(control.Name):
                    return control
            if time.monotonic() >= end:
                return None
            time.sleep(.4)

    def open(self):
        w = self.window()
        if w is None:
            subprocess.Popen(['explorer.exe', LAUNCH])
            end = time.monotonic() + self.timeout
            while w is None and time.monotonic() < end:
                time.sleep(.5)
                w = self.window()
        if w is None:
            return None
        w.SetActive()
        w.SetFocus()
        return w

    def signed_in(self, w):
        return self.find(w, ('EditControl', 'ComboBoxControl'), r'(?i)^search.*', wait=8) is not None

    # -- actions ----------------------------------------------------------------------------
    def draft(self, intent):
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)
        to, text = intent.args.get('to', ''), intent.args.get('text', '')
        if not to or not text:
            return failure(intent.intent_id, 'NOT_A_COMMAND', 'Say who to message and what to say')
        if not WINDOWS:
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', UNSUPPORTED)
        w = self.open()
        if w is None:
            return failure(intent.intent_id, 'TIMEOUT', 'Microsoft Teams did not open', elapsed_ms=elapsed())
        search = self.find(w, ('EditControl', 'ComboBoxControl'), r'(?i)^search.*', wait=8)
        if search is None:
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', 'Teams is not signed in', 'Open Teams and sign in once', elapsed_ms=elapsed())
        search.Click(simulateMove=False)
        press('ctrl', 'a')  # Ctrl+A: replace any old search text
        type_text(to)
        name = re.escape(to[:5]) if len(to) > 5 else re.escape(to)
        person = self.find(w, ('ListItemControl', 'MenuItemControl', 'ButtonControl', 'TreeItemControl', 'DataItemControl'),
                           rf'(?i)^(?!search\b).*\b{name}.*', wait=6)
        if person is None:
            press('escape')  # Esc: close the search box
            return failure(intent.intent_id, 'TARGET_GONE', f'No one called {to} in Teams', 'Check the name', elapsed_ms=elapsed())
        display = person.Name.split(',')[0].strip()[:60]
        person.Click(simulateMove=False)
        compose = self.find(w, ('EditControl', 'DocumentControl', 'GroupControl'), COMPOSE, wait=8)
        if compose is None:
            return failure(intent.intent_id, 'NO_EFFECT', f'The chat with {display} did not open', elapsed_ms=elapsed())
        compose.Click(simulateMove=False)
        press('ctrl', 'a')
        press('delete')  # Delete: the compose box holds exactly this message
        type_text(text)
        time.sleep(.3)
        typed = value_of(compose)
        evidence = {'source': 'uia', 'to': display, 'text': text, 'compose': typed[:300]}
        if text.lower() not in typed.lower():
            return failure(intent.intent_id, 'NO_EFFECT', 'The message did not appear in the Teams box', evidence=evidence, elapsed_ms=elapsed())
        self.chat = {'to': display, 'text': text}
        return ActionResult(intent.intent_id, 'pending_user', evidence, elapsed_ms=elapsed())

    def send(self, intent):
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)
        chat, text = self.chat, intent.args.get('text', '')
        if not WINDOWS:
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', UNSUPPORTED)
        if not chat or chat['text'] != text:
            return failure(intent.intent_id, 'TARGET_GONE', 'No prepared message to send')
        w = self.open()
        compose = self.find(w, ('EditControl', 'DocumentControl', 'GroupControl'), COMPOSE, wait=3) if w else None
        if compose is None or text.lower() not in value_of(compose).lower():
            self.chat = None
            return failure(intent.intent_id, 'BUFFER_MISMATCH', 'The Teams box no longer holds that message; nothing was sent')
        compose.Click(simulateMove=False)
        press('end')  # End
        press('enter')  # Enter sends
        self.chat = None
        sent = None
        needle = re.escape(text[:40])
        end = time.monotonic() + 6
        while time.monotonic() < end:
            if not value_of(compose):
                sent = self.find(w, ('ListItemControl', 'GroupControl', 'TextControl', 'DocumentControl'), rf'(?is).*{needle}.*')
                if sent is not None and sent is not compose:
                    break
            time.sleep(.4)
        evidence = {'source': 'uia', 'to': chat['to'], 'text': text, 'compose_empty': not value_of(compose), 'in_conversation': sent is not None}
        return ActionResult(intent.intent_id, 'verified' if sent is not None else 'sent', evidence, elapsed_ms=elapsed())
