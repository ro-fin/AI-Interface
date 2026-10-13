# CLAUDE.md

Guidance for Claude Code (and other contributors) working in this repository.

## What this is

`cameo`: the Cameo desktop assistant (hand signs + voice + HUD), running on **Windows** and **Linux (X11)**. Ported from the Windows-only AETHER core (`core/aether` in the smart-mirror project). Pipeline:

```
vision/worker.py (camera, MediaPipe) → gestures/classic.py + signs.py ┐
voice/service.py (Vosk, Whisper)     → voice/grammar, tanglish      ├→ pipeline.py → main.Runtime.execute → control/* → HUD + eventlog
hud.py (rail buttons)                                               ┘
```

Grab and drop between devices lives in `link/` (see its `__init__.py`): `main.Runtime` feeds it the grab / 'lost' drop
events and every hand frame (`carry_frame`); the carry protocol is in `share.py`, the encrypted transport in `node.py`.
`tests/test_link.py` covers it without a camera (in-memory devices and real links on loopback).

## Commands

```
python -m unittest discover tests      # all tests; live ones open a test window and move the real pointer
python -m cameo doctor                 # platform / session, models, devices
python -m cameo run                    # the app (Ctrl+Alt+Q quits)
```

Always run as a module from the project root. On the owner's Windows PC the Python with all packages is `runtime\controller-env\Scripts\python.exe` (`runtime` is a link to `D:\CodeCameo\mirror-runtime`); elsewhere `.venv`.

## The platform rule

Everything OS-specific goes through `control/system.py`, which re-exports `control/win32.py` on Windows and `control/x11.py` on Linux. **Both backends expose the same functions** (listed in `system.py`'s docstring) with the same meaning:

- Window ids are ints (HWND / X window id); targets are `win:0x…` strings either way.
- `geometry()` / `move_resize()` use the **outer** rectangle (title bar and borders included).
- `restore()` is one SW_RESTORE step: minimized → previous state, maximized → normal.
- Input is a list of events for `send()`: `('move', x, y)`, `('down'|'up', 'left'|'right')`, `('wheel', amount)` (120 = one notch), `('key', name, down)`.
- X11 is asynchronous: x11.py's state-changing calls wait (briefly) for the read-back, except `move_resize` (called every animation frame).

When you add an OS call: add it to **both** backends, list it in `system.py`, and never `import ctypes`/`windll` or `Xlib` outside the backends. Windows-only features (Teams UI Automation) must fail honestly on Linux with `SESSION_UNSUPPORTED`, never type blindly.

Paths come from `paths.py` (`RUNTIME`, `HAND_MODEL`, `state_dir()`); no `LOCALAPPDATA` or `parents[n]` path math elsewhere.

## Testing Linux from Windows

There is no Linux machine here; the Linux backend was verified in Docker (Ubuntu 24.04, Xvfb, Openbox and Mutter 46): `tests.test_windows_live`, `test_runtime`, `test_classic_runtime`, `test_free`, `test_bridge` all run there against real X11. Keep them passing on both OSes.

## Rules (from the original project)

- `gestures/` (classic, signs, strokes, pose, engine) stays free of I/O and OS calls; time is passed in.
- Signs follow docs/SIGN-PROTOCOL-PLAN.md: distances in palm widths, levels set absolutely and read back (`levels.py`),
  every measured sign has a named end, one sign in control at a time. Change thresholds only with `python -m cameo replay`
  evidence, and regenerate GESTURES.md's table (`python -m cameo signs`; `tests/test_catalog.py` checks it).
- Never weaken the input guards in `pointer.py` (no input while a physical mouse button or modifier is held, on the secure desktop / lock screen, or for 1.5 s after the real mouse moves). Always release a held button on drop.
- An action is `verified` only with read-back evidence (window state, browser tab event, UIA text).
- Match the existing style: short functions, comments explain *why*, no type-heavy boilerplate.
