"""Full-screen screenshot (the clap sign), saved as PNG. Pillow's ImageGrab reads the screen through GDI on
Windows (every monitor) and XCB on Linux X11, so no OS calls are needed here."""
import time

from ..paths import WINDOWS, screenshots_dir


def capture(folder=None):
    """Saves the whole screen; returns (path, (width, height))."""
    from PIL import ImageGrab
    image = ImageGrab.grab(all_screens=True) if WINDOWS else ImageGrab.grab()
    folder = folder or screenshots_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime('cameo-%Y%m%d-%H%M%S.png')
    image.save(path)
    return path, image.size
