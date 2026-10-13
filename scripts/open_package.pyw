"""Opening a .cameo file in Explorer runs this (see scripts/setup_link.ps1): unpacks it into the Cameo inbox."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cameo.__main__ import main  # noqa: E402

sys.exit(main(['unpack'] + sys.argv[1:]))
