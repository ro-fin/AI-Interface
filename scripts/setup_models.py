"""Downloads the voice model into runtime/ (Windows and Linux). The hand model ships with the package.

    python scripts/setup_models.py

Set CAMEO_RUNTIME first to keep the models somewhere else (for example on a bigger drive).
"""
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.paths import RUNTIME  # noqa: E402

VOSK = 'vosk-model-small-en-us-0.15'
VOSK_URL = f'https://alphacephei.com/vosk/models/{VOSK}.zip'


def vosk():
    target = RUNTIME / 'voice' / 'vosk'
    if (target / VOSK / 'am' / 'final.mdl').exists():
        print(f'voice model present: {target / VOSK}')
        return
    target.mkdir(parents=True, exist_ok=True)
    print(f'downloading {VOSK_URL} (about 40 MB)...')
    with tempfile.TemporaryDirectory(dir=target) as temp:
        archive = Path(temp) / 'model.zip'
        with urllib.request.urlopen(VOSK_URL, timeout=60) as response, open(archive, 'wb') as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    print(f'voice model ready: {target / VOSK}')


if __name__ == '__main__':
    vosk()
    print('Optional: the Whisper fallback recognizer (Tanglish, free speech) needs whisper.cpp in runtime/voice/whisper/Release/ '
          'and runtime/voice/whisper-model/ggml-small-q8_0.bin; see README.')
