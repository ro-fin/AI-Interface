"""Spoken Teams messages: parsing, and the rule that nothing is sent without "send it"."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.contracts import InputEvent, Intent
from cameo.pipeline import IntentPipeline
from cameo.voice.messaging import parse


class Registry:
    def get(self, target):
        return None

    def list(self):
        return []


def voice(action, **args):
    return InputEvent(source='voice', kind='command', text=action, live=False,
                      command={'action': action, 'target': None, 'via': 'grammar', **({'args': args} if args else {})})


class Parsing(unittest.TestCase):
    def test_english_tanglish_and_the_users_own_words(self):
        cases = {
            'Hey Cameo, go to Teams and message Kavinesh, hi da how are you.': ('Kavinesh', 'hi da how are you'),
            'go to teams and msg to kavinesh like hi da how are you': ('Kavinesh', 'hi da how are you'),
            'Message Kavinesh on Teams saying hi da, how are you?': ('Kavinesh', 'hi da, how are you?'),
            'Send a message to Kavinesh: hi da how are you': ('Kavinesh', 'hi da how are you'),
            'Teams la Kavinesh ku message anuppu, hi da epdi irukka': ('Kavinesh', 'hi da epdi irukka'),
        }
        for spoken, (to, text) in cases.items():
            self.assertEqual(parse(spoken), {'app': 'teams', 'to': to, 'text': text}, spoken)

    def test_other_speech_is_not_a_message(self):
        for spoken in ('open teams', 'message the team', 'new tab', 'maximize this window', 'message kavinesh', ''):
            self.assertIsNone(parse(spoken), spoken)


class SplitSpeech(unittest.TestCase):
    def test_message_said_after_a_pause_joins_the_name(self):
        from cameo.voice.messaging import head
        from cameo.voice.service import VoiceService
        self.assertTrue(head('Hey Cameo, go to Teams and message Kavinesh.'))
        self.assertFalse(head('Hey Cameo, new tab.'))
        emitted = []
        service = VoiceService.__new__(VoiceService)  # only the fallback step, no audio model
        service.emit, service.clock, service.live, service.window_until = emitted.append, lambda: 50.0, False, 0

        class Fallback:
            texts = ['Go to Teams and message Kavinesh.', 'Hi da, how are you?']

            def transcribe(self, audio):
                return self.texts.pop(0)
        service.fallback = Fallback()
        service.run_fallback(b'', None)
        service.run_fallback(b'', None)
        drafts = [e.command['args'] for e in emitted if not isinstance(e, dict)]
        self.assertEqual(drafts, [{'app': 'teams', 'to': 'Kavinesh', 'text': 'Hi da, how are you?'}])


class ConfirmBeforeSending(unittest.TestCase):
    def setUp(self):
        self.pipe = IntentPipeline(Registry())

    def draft(self):
        decisions = self.pipe.submit(voice('message.draft', app='teams', to='Kavinesh', text='hi da'))
        self.assertEqual(decisions[0][0], 'run')
        self.assertEqual(decisions[0][1].args, {'app': 'teams', 'to': 'Kavinesh', 'text': 'hi da'})
        send = Intent('message.send', None, 'default', args={'to': 'Kavinesh', 'text': 'hi da'})
        card = self.pipe.await_confirm(send, 'Say "send it" to send, or "cancel".')
        self.assertEqual(card[0][0], 'card')
        return send

    def test_send_it_runs_the_send(self):
        send = self.draft()
        decisions = self.pipe.submit(voice('control.confirm'))
        self.assertEqual(decisions, [('run', send)])

    def test_cancel_sends_nothing(self):
        self.draft()
        self.pipe.submit(voice('control.cancel'))
        self.assertEqual(self.pipe.submit(voice('control.confirm'))[0][:2], ('reject', 'NOT_A_COMMAND'))

    def test_paused_controls_never_send(self):
        self.draft()
        self.pipe.submit(voice('control.pause'))
        self.assertEqual(self.pipe.submit(voice('control.confirm'))[0][1], 'PAUSED')


if __name__ == '__main__':
    unittest.main()
