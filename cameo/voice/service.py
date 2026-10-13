"""Voice input (ARCHITECTURE.md W3, §4.4 with the Windows fallback of docs/AETHER-WINDOWS.md W2).

audio -> Vosk grammar recognizer (commands, wake words) + Vosk free recognizer (cross-check)
      -> final results only -> wake window (6 s, extended by each command) or push-to-talk
      -> command InputEvent, or, for rejected speech inside the window, Whisper -> Tanglish match.
Speech outside a wake window or push-to-talk never becomes an action and is never sent to Whisper.
"""
import json
import threading
import time

from ..contracts import InputEvent
from ..paths import RUNTIME
from . import free, grammar, messaging, tanglish

MODEL = RUNTIME / 'voice' / 'vosk' / 'vosk-model-small-en-us-0.15'
WAKE_WINDOW = 6.0
# Pieces of one utterance split by a short pause are merged before one Whisper call.
MERGE_GAP = 1.2
RATE = 16000
# "message Kavinesh" may come before a pause; the message text is taken from the next few seconds.
MESSAGE_PAUSE = 6.0


class VoiceService:
    def __init__(self, emit, fallback=None, model_dir=MODEL, clock=time.monotonic, live=True):
        import vosk
        vosk.SetLogLevel(-1)
        self.emit = emit
        self.fallback = fallback
        self.clock = clock
        self.live = live
        model = vosk.Model(str(model_dir))
        self.commands = vosk.KaldiRecognizer(model, RATE, json.dumps(grammar.vosk_grammar()))
        self.free = vosk.KaldiRecognizer(model, RATE)
        self.window_until = 0.0
        self.push_to_talk = False
        self.audio = bytearray()
        self.started = None
        self.partial = ''
        self.pending = None  # (audio, started, deadline) awaiting the fallback
        self.lock = threading.Lock()

    @property
    def listening(self):
        return self.push_to_talk or self.clock() < self.window_until

    def feed(self, pcm):
        """16 kHz mono PCM16 bytes, any block size."""
        if self.started is None:
            self.started = time.time()
        self.audio += pcm
        del self.audio[:-RATE * 2 * 15]
        self.free.AcceptWaveform(pcm)
        if self.commands.AcceptWaveform(pcm):
            text = json.loads(self.commands.Result()).get('text', '')
            free_text = json.loads(self.free.FinalResult()).get('text', '')
            audio, self.audio, started, self.started = bytes(self.audio), bytearray(), self.started, None
            if text:
                self.on_final(text, free_text, audio, started)
            self.partial = ''
        else:
            partial = json.loads(self.commands.PartialResult()).get('partial', '')
            if partial and partial != self.partial:
                self.partial = partial
                self.emit({'kind': 'partial', 'text': partial})
        if self.pending and self.clock() >= self.pending[2]:
            self.flush()

    def flush(self, expected=None):
        """Starts the merged fallback once; called by the audio stream or by the merge timer."""
        with self.lock:
            pending = self.pending
            if pending is None or (expected is not None and pending is not expected):
                return
            self.pending = None
        threading.Thread(target=self.run_fallback, args=(pending[0], pending[1]), daemon=True).start()

    def on_final(self, text, free_text, audio, started):
        woke, command, unknown = grammar.parse(text)
        now = self.clock()
        if woke:
            self.window_until = now + WAKE_WINDOW
            self.emit({'kind': 'wake', 'until': self.window_until})
        if not (woke or self.listening):
            return  # no wake word, no push-to-talk: never an action
        if command and grammar.corroborated(command['phrase'], free_text):
            self.pending = None
            self.window_until = now + WAKE_WINDOW
            self.emit(self.event(command['phrase'], {'action': command['action'], 'target': command['target'], 'via': 'grammar'}, started))
        elif command or unknown:
            if self.fallback is None or len(audio) < RATE * 2 * .4:
                self.emit({'kind': 'rejected', 'text': free_text or text})
                return
            self.emit({'kind': 'fallback', 'text': free_text})
            with self.lock:
                if self.pending:
                    audio, started = self.pending[0] + audio, self.pending[1]
                self.pending = pending = (audio, started, now + MERGE_GAP)
            # Also fire on real time, in case audio stops arriving (e.g. the stream ends).
            timer = threading.Timer(MERGE_GAP + .3, self.flush, kwargs={'expected': pending})
            timer.daemon = True
            timer.start()

    def run_fallback(self, audio, started):
        try:
            text = self.fallback.transcribe(audio)
        except Exception as error:
            self.emit({'kind': 'rejected', 'text': f'fallback unavailable: {error}'})
            return
        now = self.clock()
        head, self.message_head = getattr(self, 'message_head', None), None
        if head and now - head[1] <= MESSAGE_PAUSE and not messaging.parse(text):
            text = f'{head[0]}, {text}'  # "message Kavinesh" ... pause ... "hi da how are you"
        message = messaging.parse(text)
        if message is not None:
            # A spoken message: prepared as a draft, sent only after "send it".
            last = getattr(self, 'last_message', None)
            if last and last[0] == message and now - last[1] < MESSAGE_PAUSE:
                return  # the same message heard again from an overlapping piece of audio
            self.last_message = (message, now)
            self.window_until = now + WAKE_WINDOW * 3  # time to read the draft card and confirm
            self.emit(self.event(text, {'action': 'message.draft', 'target': None, 'args': message, 'via': 'whisper'}, started))
            return
        if messaging.head(text):
            self.message_head = (text.strip(), now)
            self.window_until = now + WAKE_WINDOW
            self.emit({'kind': 'partial', 'text': f'{text.strip()} \u2026 (say the message)'})
            return
        match = tanglish.match(text) or free.parse(text)
        if match is None:
            self.emit({'kind': 'rejected', 'text': text})
            return
        self.window_until = self.clock() + WAKE_WINDOW
        self.emit(self.event(text, {**match, 'via': 'whisper'}, started))

    def event(self, text, command, started):
        return InputEvent(source='voice', kind='command', text=text, command=command, live=self.live,
                          t_start=started or time.time(), t_end=time.time())


def microphone(service, stop, device=None):
    """Feeds the default microphone into the service until `stop` is set. Holding Ctrl+Alt+A is push-to-talk."""
    import sounddevice as sd
    from ..control import system
    talk = ('ctrl', 'alt', 'a')
    with sd.RawInputStream(samplerate=RATE, blocksize=1600, dtype='int16', channels=1, device=device) as stream:
        while not stop.is_set():
            data, _ = stream.read(1600)
            service.push_to_talk = len(system.held(talk)) == len(talk)
            service.feed(bytes(data))
