"""The desktop backend for this computer: Win32 on Windows, X11 on Linux. Same functions on both:

windows   top_level_windows() app_window() is_window() is_iconic() is_zoomed() foreground() title() pid()
          app_name() geometry() state() maximize() minimize() restore() move_resize() bring_to_front()
          close() shell_at()
screen    cursor() screen_size() work_area() dpi_aware()
input     interactive_desktop() held() send() press_ctrl() hotkeys()   (send key names include volume_up / volume_down)
session   lock_screen() sleep_computer() idle_seconds()
display   brightness() set_brightness()   (percent; brightness() is None where the display has no brightness control)
audio     volume() muted() set_volume() set_mute()   (0.0-1.0 of the default playback device; volume() is None without one)
HUD       hud_prepare() hud_shape() message()
files     files_at() open_path() reveal()   (files_at is [] on X11: a grab there shares a picture of the window)
"""
from ..paths import WINDOWS

if WINDOWS:
    from .win32 import *  # noqa: F401,F403
    from .win32 import SOURCE  # noqa: F401
else:
    from .x11 import *  # noqa: F401,F403
    from .x11 import SOURCE  # noqa: F401
