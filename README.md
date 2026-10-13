# Cameo (Windows + Linux)

Control a PC with hand signs in front of a webcam and with voice commands, on **Windows 10/11** and **Ubuntu Linux** (X11 session). Moves the pointer, clicks, scrolls, zooms, minimizes/restores and moves windows, opens apps and websites, and switches browser tabs. A HUD shows what Cameo sees and does.

```
webcam → vision worker (MediaPipe, 21 hand points) → classic signs engine ─┐
microphone → Vosk (+ Whisper fallback) → voice grammar / Tanglish ─────────┼→ intent pipeline → actions → HUD + event log
HUD rail buttons ──────────────────────────────────────────────────────────┘        │
                                                         control/system.py: win32.py (Windows) or x11.py (Linux)
```

## Install and start

| | Windows 10/11 | Ubuntu 22.04 / 24.04 |
| --- | --- | --- |
| Install | right-click `install.ps1` → Run with PowerShell | `./install.sh` |
| Start | **Cameo** shortcut on the desktop, or `Start Cameo.bat` | **Cameo** in the applications menu / desktop icon, or `./start-cameo.sh` |
| Quit | Ctrl+Alt+Q, or *Force stop* on the rail | same |

- Python **3.10 – 3.12** (MediaPipe has no wheels for newer versions). Ubuntu 22.04 ships 3.10 and 24.04 ships 3.12.
- **Linux needs an X11 session.** Ubuntu logs in to Wayland by default, and Wayland doesn't let one app read or move other apps' windows or send them input. Log out, click the gear icon on the login screen, choose **Ubuntu on Xorg**, and log in. `python -m cameo doctor` tells you which session you're in.
- Linux camera access needs the `video` group (`install.sh` adds you; log out and in once).
- Voice models live in `runtime/` (about 40 MB for the default model). To keep them on another drive, make `runtime` a link or set `CAMEO_RUNTIME`.
- The hand model isn't in the repository (7.8 MB). Download [hand_landmarker.task](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task) into `cameo/models/` before the first run.

Other commands (run them from this folder with the environment's Python):

```
python -m cameo doctor              # checks: platform/session, models, browser, microphone, camera
python -m cameo run --no-voice      # hands only (also --no-hands, --camera 1)
python -m cameo targets             # the windows Cameo can act on
python -m cameo act window.maximize --target notepad
python -m unittest discover tests   # tests (some drive a real test window and the real pointer)
```

## Signs

See [GESTURES.md](GESTURES.md). Summary: **point** moves the pointer; a middle-finger **flick** clicks; **index + middle** held = right click; a **thumb tap** on the index = open; **palm → fist** minimizes; **fist → palm** restores; **pinch/spread** zooms; **four fingers** + move scrolls; with **index + middle** up, a straight line drawn **up / down** sets brightness and **right / left** sets volume (measured: one palm width = 40 % / 25 %, shown on a bar and read back) and a **circle** opens / closes Edge; the **little finger** held still sleeps after a countdown; a **clap** takes a screenshot; **four fingers closed with the thumb out** (or **thumb+index+middle → fist**) grabs and **open palm** drops: drag files and move windows (carry the closed hand to another paired device and open it there to send what you grabbed). Ctrl+Alt+P pauses/resumes. `python -m cameo signs` prints every sign with its exact rule.

Tuning is done by replay, not by feel: `python -m cameo run --trace` records every hand frame, and
`python -m cameo replay <session>` runs a recording through the current engine and, with a `labels.jsonl` of what you
meant, scores each sign (intended, matched, missed, misfired). See `docs/SIGN-PROTOCOL-PLAN.md`.

## Voice

Say **"Hey Cameo"**, then a command, or hold **Ctrl+Alt+A** while speaking: "maximize", "minimize", "restore that", "open the browser", "new tab", "next tab", "open notepad", "play anirudh songs", "search weather in chennai" (English and Tanglish).

## What works where

| Feature | Windows | Linux (X11) |
| --- | --- | --- |
| Hand signs: pointer, click, right click, scroll, zoom, grab | ✓ SendInput | ✓ XTest |
| Minimize / maximize / restore / focus / move windows (verified by read-back) | ✓ Win32 | ✓ EWMH (GNOME/Mutter, KDE, Xfce, Openbox…) |
| HUD overlay: transparent, click-through, never takes focus | ✓ colour key + extended styles | ✓ XShape + override-redirect windows |
| Hotkeys Ctrl+Alt+P / Q, push-to-talk Ctrl+Alt+A | ✓ | ✓ |
| Voice (Vosk) | ✓ | ✓ |
| Whisper fallback (Tanglish / free speech) | ✓ `whisper-server.exe` | ✓ build [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and copy `whisper-server` to `runtime/voice/whisper/Release/` |
| Open apps by name | ✓ Start menu apps | ✓ applications menu (`.desktop`, snaps, flatpaks); "explorer", "notepad", "task manager"… map to Files, Text Editor, System Monitor |
| Websites / YouTube / search | ✓ | ✓ (Firefox is used if no Chromium browser is installed) |
| Browser tabs (Cameo Bridge extension) | ✓ Edge, Chrome, Brave | ✓ Edge, Chrome, Brave, Chromium |
| Teams messages | ✓ UI Automation | ✗ reported as unsupported (no Teams desktop app or UI Automation on Linux) |
| Refuses input on the lock screen | ✓ secure-desktop check | ✓ GNOME/freedesktop screensaver check |

### Browser tabs (optional)

1. `python scripts/install_bridge.py` registers the native-messaging host (Windows registry or `~/.config/<browser>/NativeMessagingHosts`).
2. In the browser, open `edge://extensions` (or `chrome://extensions`) → Developer mode → **Load unpacked** → `bridge/extension`.

`python scripts/install_bridge.py --remove` undoes step 1.

## Grab and drop between devices

Grab something on one computer, carry your closed hand to another computer running Cameo, open your hand there: it arrives.

1. **Once per Windows device**: run `scripts\setup_link.ps1` (install.ps1 does it). It opens TCP 47820 / UDP 47821 / mDNS in Windows Firewall on *Private* networks only and makes `.cameo` files open with Cameo. Your network must be **Private** (Settings > Network & internet); on Public, Windows blocks it. On Linux, open the same ports in any firewall. `python -m cameo doctor` checks all of this.
2. **Pair** (once): rail > **Devices**. Other Cameo devices on the same network appear under *Nearby*; press **Pair** (or type the other device's address). Both screens show a 6-digit code: confirm on **both** if they match. Optionally say which side of this screen the other device stands on (left/right).
3. **Grab**: four fingers closed with the thumb out, held a moment (or thumb + index + middle open, then close to a fist), over:
   - a **browser window**: the page itself goes (address, not a picture). A page on `localhost` is sent with this device's network address, so the other device opens this device's running web app.
   - a **file, folder or selection** on the desktop or in Explorer: the files go (the whole selection if you grab a selected item).
   - **any other window**: a picture of it goes.
4. **Carry**: move the closed hand out of this camera's view. Every paired device online now waits for the drop ("DROP HERE" on its HUD); small files (up to 25 MB) are already sent to them in the background.
5. **Drop**: in front of the other device's camera, open your closed hand to an open palm (or press **Do it** on its card). The device whose camera sees it opens the page in a new tab, or saves the files in its **inbox** and shows them (pictures, videos, audio, PDFs open; programs and scripts are only shown in their folder, never run).

Opening the hand back on the first computer takes it back; nothing dropped within 10 s cancels it. Nothing moves or is deleted on the sending side, ever.

**Inbox**: `D:\Cameo\Inbox` when there is a D: drive, else `~/Cameo/Inbox` (set `CAMEO_INBOX` to change it), sorted into Pictures, Videos, Music, Documents, Files and Folders.

**Security**: only paired devices connect (the pairing key is stored in the state folder; *Forget* removes it). Every connection runs a fresh key exchange proven with that key, and every frame is AES-256-GCM encrypted, so it cannot be read, forged or replayed. The receiver checks the SHA-256 of every file and the sender compares them before it says *delivered*. Only `http`/`https` pages are opened.

**`.cameo` files**: files and folders as one package. `python -m cameo pack <files/folders> -o x.cameo` makes one; opening it (or `python -m cameo unpack x.cameo`) puts its contents in the inbox. The same format is what travels between devices.

## Files

```
cameo/
  __main__.py         command line: run / doctor / targets / act / pack / unpack
  main.py             the runtime: HUD loop, signs → actions, executor, hotkeys
  paths.py            model and state folders per OS (runtime/, %LOCALAPPDATA%\Cameo, ~/.local/state/cameo)
  hud.py              rail, overlay (reticle, skeleton, toasts), collapsed ball
  pipeline.py         inputs → intents (confirmations, pause, voice + gesture merge)
  targets.py          which windows may be acted on
  ipc.py              authenticated local channel: named pipe (Windows) / Unix socket (Linux)
  doctor.py           startup checks
  control/
    system.py         picks the backend for this OS
    win32.py          Windows: windows, SendInput, hotkeys, HUD window styles
    x11.py            Linux: EWMH windows, XTest input, XGrabKey hotkeys, XShape HUD
    pointer.py        pointer moves/clicks/wheel with the safety guards (both OSes)
    windows.py        window actions with read-back evidence
    browsers.py       finds Edge / Chrome / Brave / Chromium / Firefox and the Bridge profile
    browser.py        tab actions through the Bridge
    apps.py           open apps by spoken name
    web.py            YouTube / search / websites
    teams.py          Teams messages (Windows only)
  gestures/           classic signs engine, dock engine, filters
  link/               grab and drop between devices: node (discovery, pairing, encrypted links), share (carry,
                      claim, commit, drop detector), package (.cameo), content (what a grab picks up), ui (Devices)
  vision/worker.py    camera + MediaPipe process
  voice/              Vosk service, grammar, Tanglish, messaging, Whisper fallback
  models/hand_landmarker.task
bridge/               browser extension + native-messaging host
scripts/              setup_models.py (voice model), install_bridge.py, setup_link.ps1 (firewall, .cameo files)
tests/
install.ps1 / Start Cameo.bat      Windows
install.sh / start-cameo.sh        Linux
```

Logs: each session writes `sessions/<time>/events.jsonl` in the state folder (`%LOCALAPPDATA%\Cameo` or `~/.local/state/cameo`). Set `CAMEO_TRACE=1` for per-frame hand traces.

## Limits

- One hand, one camera; the camera can't be used by another app at the same time.
- Windows: windows of apps running as administrator can't be controlled from a normal-user process.
- Linux: X11 only (see above). Multi-monitor setups use the whole X screen for pointer mapping.
