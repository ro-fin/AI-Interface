"""CP2 voice path with synthetic speech (Windows voices) and a stand-in Whisper fallback."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.voice.service import VoiceService
from cameo.voice import grammar, tanglish

TEMP = Path(tempfile.mkdtemp(prefix='cameo-voice-'))


def speech(text, voice='Microsoft Zira Desktop'):
    path = TEMP / f'{abs(hash((text, voice)))}.wav'
    if not path.exists():
        script = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.SelectVoice('{voice}'); $f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
                  "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono); "
                  f"$s.SetOutputToWaveFile('{path}',$f); $s.Speak('{text}'); $s.Dispose()")
        subprocess.run(['powershell', '-NoProfile', '-Command', script], check=True, capture_output=True)
    with wave.open(str(path)) as audio:
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)


class FakeFallback:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, pcm):
        self.calls += 1
        return self.text


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class VoicePath(unittest.TestCase):
    def run_voice(self, *parts, fallback=None):
        clock, events = Clock(), []
        service = VoiceService(events.append, fallback=fallback, clock=clock, live=False)
        for part in parts:
            if isinstance(part, (int, float)):
                clock.now += part  # silence gap measured in seconds (time passes)
                audio = np.zeros(int(min(part, 1.5) * 16000), np.int16)
            else:
                audio = np.concatenate([np.zeros(3200, np.int16), speech(part), np.zeros(14000, np.int16)])
            for i in range(0, len(audio), 1600):
                service.feed(audio[i:i + 1600].tobytes())
                clock.now += .1
        for _ in range(20):  # a live microphone keeps delivering (silent) audio afterwards
            service.feed(np.zeros(1600, np.int16).tobytes())
            clock.now += .1
        deadline = time.time() + 3
        while fallback and any(e.get('kind') == 'fallback' for e in events if isinstance(e, dict)) and not self.commands(events) and time.time() < deadline:
            time.sleep(.05)
        return events

    @staticmethod
    def commands(events):
        return [(e.command['action'], e.command['target'], e.command['via']) for e in events if not isinstance(e, dict)]

    def test_wake_and_command_in_one_breath(self):
        self.assertEqual(self.commands(self.run_voice('Hey Cameo, new tab.')), [('tab.new', None, 'grammar')])
        self.assertEqual(self.commands(self.run_voice('Ether, maximize this window.')), [('window.maximize', 'deictic', 'grammar')])

    def test_command_without_wake_word_is_ignored(self):
        self.assertEqual(self.commands(self.run_voice('New tab.', 'Maximize this window.')), [])

    def test_wake_window_allows_follow_ups_then_expires(self):
        events = self.run_voice('Hey Cameo.', 'Next tab.', 'Previous tab.', 8, 'Close tab.')
        self.assertEqual([c[0] for c in self.commands(events)], ['tab.next', 'tab.prev'])

    def test_background_speech_does_nothing_and_is_never_sent_to_whisper(self):
        fallback = FakeFallback('adutha tab')
        events = self.run_voice('I went to the store yesterday with my friends.', 'Adutha tab.', fallback=fallback)
        self.assertEqual((self.commands(events), fallback.calls), ([], 0))

    def test_tanglish_after_wake_word_uses_whisper_fallback(self):
        fallback = FakeFallback('Hey Cameo, adutha tab.')
        events = self.run_voice('Hey Cameo, adutha tab.', fallback=fallback)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(self.commands(events), [('tab.next', None, 'whisper')])

    def test_spoken_teams_message_becomes_a_draft_not_a_send(self):
        heard = 'Hey Cameo, go to Teams and message Kavinesh, hi da how are you.'
        events = self.run_voice('Hey Cameo, go to Teams and message Kavinesh, hi da how are you.', fallback=FakeFallback(heard))
        drafts = [e.command for e in events if not isinstance(e, dict)]
        self.maxDiff = None
        self.assertEqual(drafts, [{'action': 'message.draft', 'target': None, 'via': 'whisper',
                                   'args': {'app': 'teams', 'to': 'Kavinesh', 'text': 'hi da how are you'}}])

    def test_send_it_is_a_confirmation(self):
        self.assertEqual(self.commands(self.run_voice('Hey Cameo, send it.')), [('control.confirm', None, 'grammar')])

    def test_unmatched_fallback_is_rejected(self):
        events = self.run_voice('Hey Cameo, volume koottu.', fallback=FakeFallback('volume koottu'))
        self.assertEqual(self.commands(events), [])
        self.assertTrue(any(isinstance(e, dict) and e['kind'] == 'rejected' for e in events))

    def test_partials_are_display_only(self):
        events = self.run_voice('Hey Cameo, minimize the browser.')
        partials = [e for e in events if isinstance(e, dict) and e['kind'] == 'partial']
        self.assertTrue(partials)
        self.assertEqual(self.commands(events), [('window.minimize', 'browser', 'grammar')])


class GrammarAndTanglish(unittest.TestCase):
    def test_grammar_expansion_and_cross_check(self):
        self.assertIn('maximize this window', grammar.COMMANDS)
        self.assertEqual(grammar.parse('hey cameo restore that')[1]['target'], 'minimized')
        self.assertTrue(grammar.corroborated('new tab', 'and new tab'))
        self.assertFalse(grammar.corroborated('new tab', 'adutha song podu'))

    def test_tanglish(self):
        cases = {'pudhu tab open pannu': 'tab.new', 'aduththa tab ku po': 'tab.next', 'munthaya tab': 'tab.prev', 'tab-ai moodu': 'tab.close',
                 'browser thira': 'browser.focus_or_open', 'full screen pannu': 'window.maximize', 'window minimize pannu': 'window.minimize',
                 'niruthu': 'control.pause', 'thodaru': 'control.resume', 'அடுத்த டேப்': 'tab.next',
                 'I went to the store yesterday': None, 'format the c drive': None, 'volume koottu': None}
        for text, action in cases.items():
            self.assertEqual((tanglish.match(text) or {}).get('action'), action, text)


if __name__ == '__main__':
    unittest.main()
