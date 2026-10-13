"""Whisper fallback (docs/AETHER-WINDOWS.md W2): a resident whisper.cpp server on loopback, started on
first use and stopped when idle. Audio is posted in memory; nothing is written to disk."""
import io
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
import wave

from ..paths import EXE, RUNTIME

SERVER = RUNTIME / 'voice' / 'whisper' / 'Release' / f'whisper-server{EXE}'  # Linux: built from whisper.cpp, see README
MODEL = RUNTIME / 'voice' / 'whisper-model' / 'ggml-small-q8_0.bin'
PROMPT = 'Hey Cameo. new tab, adutha tab, munthaya tab, tab close pannu, browser thira, full screen pannu, minimize pannu, restore, niruthu.'
MAX_SECONDS = 10


def installed():
    return SERVER.exists() and MODEL.exists()


def collapse_repeats(text):
    parts = [p.strip() for p in re.split(r'(?<=[.,!?])\s+', text) if p.strip()]
    key = lambda p: re.sub(r'[^\w]+', '', p.lower())
    if len(parts) > 1 and all(key(p) == key(parts[0]) for p in parts):
        return parts[0]
    return text.strip()


def wav_bytes(pcm):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        w.writeframes(pcm)
    return out.getvalue()


class WhisperFallback:
    def __init__(self, idle_seconds=120):
        self.idle_seconds = idle_seconds
        self.process = None
        self.port = None
        self.lock = threading.Lock()
        self.last_used = 0

    def ensure(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                self.port = probe.getsockname()[1]
            threads = str(max(2, (os.cpu_count() or 4) - 2))
            self.process = subprocess.Popen([str(SERVER), '-m', str(MODEL), '--host', '127.0.0.1', '--port', str(self.port), '-t', threads,
                                             '-ng', '-ac', str(MAX_SECONDS * 50), '-l', 'auto'],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{self.port}/', timeout=1)
                    break
                except OSError:
                    time.sleep(.25)
            threading.Thread(target=self.idle_watch, daemon=True).start()

    def idle_watch(self):
        while self.process and self.process.poll() is None:
            time.sleep(10)
            if time.monotonic() - self.last_used > self.idle_seconds:
                self.stop()

    def transcribe(self, pcm):
        pcm = pcm[-MAX_SECONDS * 32000:]
        self.ensure()
        self.last_used = time.monotonic()
        boundary = uuid.uuid4().hex
        fields = {'response_format': 'verbose_json', 'temperature': '0', 'prompt': PROMPT}
        body = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
        body += wav_bytes(pcm) + f'\r\n--{boundary}--\r\n'.encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/inference', data=body,
                                         headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
        segments = result.get('segments') or []
        # Whisper invents phrases from noise; segments it marks as non-speech are dropped.
        text = ''.join(s.get('text', '') for s in segments if s.get('no_speech_prob', 0) <= .6) if segments else result.get('text', '')
        self.last_used = time.monotonic()
        return collapse_repeats(text)[:300]

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.process = None
