"""Cameo runtime: `python -m cameo run` (ARCHITECTURE.md §4.1, adapted for Windows and Linux X11).

Threads: Tk (HUD, gesture engine, pipeline), microphone + Vosk, executor (serial actions), bridge,
hotkeys. Process: vision worker (camera + hand model). Every input, intent and result is logged.
"""
import multiprocessing as mp
import os
import collections
import queue
import subprocess
import threading
import time
import tkinter as tk

from .contracts import InputEvent, Intent, ActionResult, failure
from .control import browsers, system
from .control.browser import BridgeServer, BrowserController
from .control.windows import WindowBackend
from .eventlog import EventLog
from .gestures.engine import GestureEngine, CONFIRM_HOLD
from .gestures import classic
from .control.pointer import Pointer, cursor, screen_size
from .hud import Hud, CYAN, AMBER, GREEN, RED, DIM, STATUS_COLOUR
from .pipeline import IntentPipeline
from .targets import TargetRegistry
from .vision.worker import DARK
from .levels import Levels

# Rail buttons and voice ("volume up", "brightness down"): one step, set absolutely and read back (levels.py for signs).
BRIGHTNESS_STEP = 10
VOLUME_STEP = .05
# A recognised circle or line is drawn clean (a true circle, a straight line) in green for this long (seconds).
SHAPE_SHOW = 1.0


class Runtime:
    def __init__(self, voice=True, hands=True, camera=0, live=True, gestures='classic', link=True):
        system.dpi_aware()  # before Tk measures the screen
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = self.callback_error
        self.log = EventLog(live=live)
        self.registry = TargetRegistry()
        from .control.teams import Teams
        from .control.apps import AppIndex
        from .control.web import Web
        self.teams = Teams()
        self.apps = AppIndex(load=live)
        self.web = Web(self.registry)
        self.windows = WindowBackend(self.registry)
        self.bridge = BridgeServer()
        self.browser = BrowserController(self.bridge, self.registry)
        self.pipeline = IntentPipeline(self.registry, minimized=self.peek_minimized)
        self.events, self.frames, self.jobs = queue.Queue(), queue.Queue(), queue.Queue()
        self.stop = threading.Event()
        self.hud = Hud(self.root, on_ui=self.ui, on_quit=self.force_stop)
        self.gestures = gestures
        self.engine = GestureEngine(self.events.put, self.targets_at, (self.hud.width, self.hud.height), live=live)
        # The user's own signs drive the real mouse pointer; the plan's dock engine is the alternative.
        self.signs = classic.GestureEngine()
        # Volume and brightness from signs: absolute levels with read-back; results come back like executor results.
        self.levels = Levels(on_done=lambda intent, result: self.events.put(('result', intent, result)), log=self.log)
        self.pointer = Pointer()
        self.history = collections.deque(maxlen=45)
        self.palm_target = None
        self.restoring = None
        self.resize = None  # {'hwnd', 'original', 'hand0', ...} while the palm dial shrinks and places a window
        self.anim = None    # {'hwnd', 'cur', 'target'}: the window glides toward its target rectangle
        self.palm_pos = None
        self.curl_smooth = None
        self.shrink_label = ''
        self.live = live
        self.locked = None
        self.asleep = None  # (monotonic time, cursor) after the sleep sign: hand input stays off until the user is back
        self.last_frame = None
        self.edge_windows = []  # Edge windows this Cameo opened (executor thread only): the only ones "close Edge" closes
        self.circle_at = -100
        self.link = self.share = self.drop = self.devices = None
        self.carry_label = ''
        if link and live and os.environ.get('CAMEO_LINK', '1') != '0':
            self.start_link(hands)
        else:
            self.hud.chip('link', 'devices off', DIM)
        threading.Thread(target=self.executor, daemon=True).start()
        threading.Thread(target=self.hotkeys, daemon=True).start()
        self.vision = None
        if hands:
            self.start_vision(camera)
        if voice:
            threading.Thread(target=self.start_voice, daemon=True).start()
        else:
            self.hud.chip('mic', 'mic off', DIM)
        self.root.after(15, self.tick)

    # -- inputs ------------------------------------------------------------------------------
    def start_vision(self, camera):
        from .vision.worker import run_worker
        # 'spawn' everywhere: forking this process (Tk, X connection, threads) on Linux is unsafe.
        context = mp.get_context('spawn')
        parent, child = context.Pipe()
        self.vision = context.Process(target=run_worker, args=(child, camera), daemon=True)
        self.vision.start()
        self.vision_conn = parent

        def read():
            while not self.stop.is_set():
                try:
                    frame = parent.recv()
                    if not frame.get('error'):
                        self.camera_retries = 0
                    self.frames.put(frame)
                    if frame.get('error'):
                        raise EOFError  # the camera did not open: restart the worker after a pause
                except (EOFError, OSError):
                    self.log.write({'type': 'device', 'device': 'camera', 'error': f'vision worker ended (exit code {self.vision.exitcode})'})
                    self.events.put({'kind': 'camera-offline'})
                    self.restart_vision(camera)
                    return
        threading.Thread(target=read, daemon=True).start()

    def restart_vision(self, camera):
        self.camera_retries = getattr(self, 'camera_retries', 0) + 1
        wait = min(30, 2 * self.camera_retries)
        try:
            self.vision.kill()
        except Exception:
            pass
        if self.stop.wait(wait):
            return
        self.log.write({'type': 'device', 'device': 'camera', 'retry': self.camera_retries})
        self.events.put({'kind': 'camera-retry'})
        self.start_vision(camera)

    def start_voice(self):
        from .voice.service import VoiceService, microphone
        from .voice.whisper import WhisperFallback, installed
        tries = 0
        while not self.stop.is_set():
            try:
                if getattr(self, 'voice', None) is None:
                    self.voice = VoiceService(self.events.put, fallback=WhisperFallback() if installed() else None)
                self.events.put({'kind': 'mic', 'state': 'ready'})
                microphone(self.voice, self.stop)
                return
            except Exception as error:
                tries += 1
                self.log.write({'type': 'device', 'device': 'microphone', 'error': str(error)[:300], 'try': tries})
                self.events.put({'kind': 'mic', 'state': f'retrying: {error}'})
                try:  # an audio hiccup ("no driver", a device taken) clears after PortAudio re-reads the devices
                    import sounddevice as sd
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
                if self.stop.wait(min(30, 3 * tries)):
                    return

    def ui(self, action):
        if action.startswith('link.'):
            self.link_ui(action)
            return
        if self.share and action in ('control.confirm', 'control.cancel') and self.share.armed():
            # The card is showing an incoming carry: "Do it" drops it here without a camera, "Cancel" declines.
            if action == 'control.confirm':
                self.share.claim(1.0)
            else:
                self.share.decline()
            self.hud.show_card(None)
            return
        if action == 'control.toggle':
            action = 'control.resume' if self.pipeline.paused else 'control.pause'
        spec = 'selected' if action.startswith('window.') else 'browser' if action == 'browser.focus_or_open' else None
        self.events.put(InputEvent(source='hud', kind='ui', text=action, command={'action': action, 'target': spec, 'via': 'hud'}))

    def hotkeys(self):
        """Ctrl+Alt+P pause/resume, Ctrl+Alt+Q quit (RegisterHotKey on Windows, XGrabKey on X11)."""
        bindings = {'p': lambda: self.root.after(0, lambda: self.ui('control.toggle')), 'q': lambda: self.root.after(0, self.quit)}
        try:
            if not system.hotkeys(bindings, self.stop):
                self.log.write({'type': 'device', 'device': 'hotkeys', 'error': 'another app already uses Ctrl+Alt+P or Ctrl+Alt+Q'})
        except Exception as error:
            self.log.write({'type': 'device', 'device': 'hotkeys', 'error': str(error)[:300]})

    def targets_at(self, x, y):
        dock = self.hud.dock_at(x, y)
        if dock or self.hud.over_rail(x, y):
            return dock
        target = self.registry.at(x, y)
        return target.id if target else None

    def peek_minimized(self):
        for target in reversed(self.windows.minimized):
            if self.registry.get(target):
                return target
        return None

    # -- loop --------------------------------------------------------------------------------
    def tick(self):
        try:
            if self.bridge.connected != getattr(self, 'bridge_state', None):
                self.bridge_state = self.bridge.connected
                self.log.write({'type': 'bridge', 'connected': self.bridge_state})
                self.hud.chip('bridge', 'browser linked' if self.bridge_state else 'browser: no bridge', GREEN if self.bridge_state else AMBER)
                self.bridge_since = time.monotonic()
            self.bridge_watchdog()
            if self.share:
                self.share.tick()
                if time.monotonic() - getattr(self, 'panel_at', 0) >= 1:
                    self.panel_at = time.monotonic()
                    self.hud.devices(self.device_rows())
            self.drain_frames()
            self.drain_events()
            self.hud.level(self.levels.snapshot())  # R5: the level bar (base, target, read-back) while a level sign runs
            self.animate()
            self.hud.tick()
        finally:
            if not self.stop.is_set():
                self.root.after(15, self.tick)

    def drain_frames(self):
        frame = None
        if self.sleeping():
            while not self.frames.empty():
                self.frames.get_nowait()
            self.hud.chip('hand', 'asleep - touch mouse or keyboard to wake', DIM)
            return
        while not self.frames.empty():
            frame = self.frames.get_nowait()
            if frame.get('error'):
                self.hud.chip('hand', 'camera unavailable', RED)
                self.log.write({'type': 'device', 'error': frame})
                continue
            if self.gestures == 'classic':
                self.classic_frame(frame)
            else:
                self.engine.update(frame)
        if frame is None or frame.get('error'):
            return
        if self.gestures == 'classic':
            label = frame.get('pose') or 'no hand' if frame.get('hand') else 'no hand'
            # A dark room is the commonest reason hands are missed: say so (the worker already brightens the picture).
            dim = ' · low light: face a lamp' if frame.get('light') is not None and frame['light'] < DARK else ''
            self.hud.chip('hand', f"{label} {frame.get('fps', 0):.0f} fps{dim}", CYAN if frame.get('hand') else AMBER if dim else DIM)
            names = {'point': 'POINT', 'two': 'TWO', 'palm': 'PALM', 'fist': 'FIST', 'zoom': 'ZOOM', 'four': 'SCROLL', 'three': 'GRAB',
                     'pinky': 'SLEEP (hold)', 'thumb': 'GRAB (hold) · open palm drops'}
            flash = getattr(self, 'flash', None)
            flash = flash[0] if flash and time.monotonic() - flash[1] < .6 else ''
            stroke = self.signs.stroke
            drawing = bool(stroke and stroke.travel >= classic.DRAW_MOVED)
            if frame.get('hands', 0) >= 2:
                names = {frame.get('pose'): 'CLAP TO SCREENSHOT'}
            shown = 'PAUSED' if self.pipeline.paused else self.link_label() or self.shrink_label or flash or ('DRAW ↻ EDGE  ↕ LIGHT  ↔ VOLUME' if drawing else names.get(frame.get('pose'), ''))
            if stroke and stroke.line and not self.pipeline.paused:
                # A line in control is shown straight, following the fingers, and stays up briefly after they stop.
                self.circle_at = time.monotonic()
                self.hud.trail(stroke.path + [stroke.tip], self.pointer.margin, GREEN, 'line')
            elif time.monotonic() - self.circle_at > SHAPE_SHOW:  # a finished shape stays up briefly, in green
                self.hud.trail(stroke.path + [stroke.tip] if drawing and not self.pipeline.paused else None, self.pointer.margin)
            self.hud.skeleton(frame.get('points') if frame.get('hand') else None, self.pointer.margin, shown)
            # R5: a hold in progress fills an arc at the fingertip (sleep countdown, right click, minimize, grab).
            arming = self.signs.arming if frame.get('hand') and not self.pipeline.paused else None
            self.hud.progress(self.screen_of(frame['x'], frame['y']) if arming else None, *(arming or (None, 0)))
            return
        engine = self.engine
        self.hud.chip('hand', f"{'engaged' if engine.engaged else 'hand' if frame.get('hand') else 'no hand'} {frame.get('fps', 0):.0f} fps",
                      CYAN if engine.engaged else DIM)
        if engine.pointer and frame.get('hand'):
            progress = (frame['t'] - engine.pinch_start) / CONFIRM_HOLD if engine.pinched and engine.pinch_start else 0
            self.hud.pointer(*engine.pointer, engine.engaged, progress)
            self.hud.hover(engine.hover if str(engine.hover).startswith('dock:') else None)
        elif not frame.get('hand'):
            self.hud.hide_pointer()

    def drain_events(self):
        for _ in range(50):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, tuple) and event[0] == 'result':
                self.show_result(event[1], event[2])
            elif isinstance(event, dict) and event.get('kind') == 'link':
                self.link_event(event['event'])
            elif isinstance(event, dict):
                self.voice_status(event)
            else:
                self.log.write(event)
                if event.source == 'voice':
                    self.hud.transcript(event.text, final=True)
                for decision in self.pipeline.submit(event):
                    self.decide(decision)

    def callback_error(self, kind, error, trace):
        # Errors inside the UI loop are recorded and shown, never silently swallowed.
        import traceback
        detail = ''.join(traceback.format_exception(kind, error, trace))[-1500:]
        self.log.write({'type': 'error', 'where': 'ui loop', 'error': str(error)[:300], 'trace': detail})
        self.hud.transcript(f'Internal error: {str(error)[:120]}', final=True)

    # -- classic signs -----------------------------------------------------------------------
    def classic_frame(self, frame):
        t = frame['t']
        feature = {'pose': frame['pose'], 'x': frame['x'], 'y': frame['y'], 'pinch': frame.get('pinch', 1), 'curl': frame.get('curl'),
                   'palm_x': frame.get('palm_x', frame['x']), 'palm_y': frame.get('palm_y', frame['y']), 'roll': frame.get('roll'),
                   'hands': frame.get('hands', 1), 'gap': frame.get('gap'), 'unit': frame.get('unit'),
                   'aspect': frame.get('aspect')} if frame.get('hand') and frame.get('pose') else None
        if frame.get('hand') and frame.get('palm_x') is not None and frame.get('pose') in ('palm', 'four', 'neutral', 'fist', 'thumb'):
            # Only an open or closing hand carries the window: the drop (back to pointing) must not drag it again.
            self.palm_pos = (frame['palm_x'], frame['palm_y'])
            if self.resize:
                self.place()
        if feature is None and self.history and t - self.history[-1][0] > .3:
            self.history.clear()  # the hand left: old positions must never be clicked later
        self.sign_frames = getattr(self, 'sign_frames', 0) + 1
        if self.share and self.carry_frame(frame, t):
            return
        events = self.signs.update(feature, t)
        if os.environ.get('CAMEO_TRACE'):
            # Per-frame trace for live diagnosis (text only: pose, position, engine state, actions).
            self.log.write({'type': 'trace', 'pose': frame.get('pose'), 'hand': frame.get('hand'), 'x': round(frame.get('x', -1), 3),
                            'y': round(frame.get('y', -1), 3), 'pinch': round(frame.get('pinch', -1), 2), 'size': frame.get('size'),
                            'engine': self.signs.pose, 'events': [e[0] for e in events], 'paused': self.pipeline.paused,
                            'pause_left': round(self.pointer.pause_until - time.monotonic(), 2), 'cursor': cursor(), 'delay': frame.get('delay_ms'),
                            'fingers': frame.get('fingers'), 'hands': frame.get('hands'), 'gap': frame.get('gap'), 'roll': frame.get('roll'),
                            'key': bool(self.signs.key),
                            # Replayable (python -m cameo replay): the exact engine input, its time, and the landmarks.
                            'ft': t, 'feature': feature, 'points': frame.get('points'), 'light': frame.get('light')})
        for action, value in events:
            if self.pipeline.paused:
                continue  # paused: the hand never moves the pointer or acts
            if action != 'move':
                self.log.write({'type': 'sign', 'action': action})
            try:
                self.classic_action(action, value, t)
            except RuntimeError as error:
                self.hud.transcript(str(error), final=True)
                self.log.write({'type': 'sign', 'action': action, 'refused': str(error)[:200]})
        if self.sign_frames % 125 == 0:  # about every 5 s: what the hand pipeline is doing
            self.log.write({'type': 'hand', 'pose': frame.get('pose'), 'hand': frame.get('hand'), 'fps': frame.get('fps'),
                            'pointer': cursor(), 'moves': len(self.history), 'engine_pose': self.signs.pose, 'size': frame.get('size')})

    def history_point(self, moment):
        """Where the finger pointed just before a flick/palm began. Only the last second counts; with no
        recent position the click happens where the pointer is now (never at a stale, far-away spot)."""
        recent = [(stamp, position) for stamp, position in self.history if moment - 1.0 <= stamp <= moment]
        before = [position for stamp, position in recent if stamp <= moment - .12]
        if before:
            return before[-1]
        return recent[0][1] if recent else None

    def classic_action(self, action, value, t):
        if action == 'move':
            if self.pointer.move(*value):
                self.history.append((t, cursor()))
            else:
                self.hud.transcript('Paused 1.5 s: physical mouse moved', final=True)
            return
        if action == 'grab':
            self.grab_start()
            return
        if action == 'drag':
            self.grab_move(value)
            return
        if action == 'drop':
            self.grab_end(value)
            return
        if action == 'resize':
            self.shrink(value)
            return
        if action == 'resize-end':
            self.end_shrink()
            return
        if action == 'arm-target':
            if value == 'restored':
                # The restore is still running, so that window may be hidden right now: arm on it by id.
                self.palm_target = self.restoring
            elif self.resize:
                # Opening the palm again during a shrink keeps the same window and its true original size.
                self.palm_target = self.resize['target']
                return
            else:
                under = self.registry.at(*cursor())
                self.palm_target = under.id if under else None
            self.resize, self.shrink_label, self.curl_smooth = None, '', None
            return
        if action == 'level':
            # A line drawn with index + middle: the engine measured how far (delta, a fraction of the range); the levels
            # controller reads the base, sets base + delta and reads it back (docs/SIGN-PROTOCOL-PLAN.md).
            phase, quantity, delta, reason = value
            if phase == 'engage':
                self.levels.engage(quantity, delta)
            elif phase == 'control':
                self.levels.control(delta)
            else:
                self.levels.end(phase == 'commit', delta, reason)
            return
        if action == 'sign-cancel':
            sign, reason = value
            self.flash = (f'{sign.upper()} CANCELLED', time.monotonic())
            self.hud.add_log(f'{sign} cancelled: {reason}', 'cancelled')
            return
        if action == 'circle':
            self.circle_at = time.monotonic()
            self.hud.trail(self.signs.stroke.done if self.signs.stroke else None, self.pointer.margin, GREEN, 'circle')
            self.flash = ('↻ OPEN EDGE' if value > 0 else '↺ CLOSE EDGE', time.monotonic())
            self.sign_command('browser.edge_open' if value > 0 else 'browser.edge_close', 'circle_cw' if value > 0 else 'circle_ccw')
            return
        if action == 'clap':
            self.flash = ('SCREENSHOT', time.monotonic())
            self.sign_command('screen.capture', 'clap')
            return
        if action == 'sleep':
            self.flash = ('SLEEP', time.monotonic())
            self.sign_command('session.sleep', 'pinky')
            return
        if action == 'click':
            self.pointer.click(self.history_point(value))
        elif action == 'right-click':
            self.pointer.click(self.history_point(value), button='right')
        elif action == 'open-target':
            icon = self.pointer.icon_at_pointer()
            self.pointer.click(self.history_point(value), double=icon != 'taskbar')
        elif action == 'zoom':
            self.pointer.wheel(value, zoom=True)
            self.flash = ('ZOOM IN' if value > 0 else 'ZOOM OUT', time.monotonic())
        elif action == 'scroll':
            self.pointer.wheel(value, amount=60)  # half notches, many per second: smooth
            self.flash = ('SCROLL ↑' if value > 0 else 'SCROLL ↓', time.monotonic())
        elif action in ('minimize', 'restore'):
            if action == 'restore':
                self.restoring = self.peek_minimized()
                if self.restoring is None:
                    # Opening a closed hand is common; with nothing minimized by gesture it is simply not a restore.
                    self.log.write({'type': 'sign', 'action': 'restore', 'ignored': 'nothing minimized by gesture'})
                    return
            target = self.palm_target if action == 'minimize' else 'minimized'
            if action == 'minimize' and self.resize:
                # Put the shrunk window back to its full size first, so restoring it later returns it whole.
                self.anim = None
                self.set_rect(self.resize['hwnd'], self.resize['original'])
                self.resize, self.shrink_label, self.curl_smooth = None, '', None
            if action == 'minimize' and not target:
                self.hud.transcript('Minimize: point at an application window first', final=True)
                return
            # Restore brings the window back maximized (SW_MAXIMIZE also un-minimizes), verified by read-back.
            command = f'window.{action}' if action == 'minimize' else 'window.maximize'
            self.events.put(InputEvent(source='gesture', kind='gesture', gesture={'name': 'palm_fist' if action == 'minimize' else 'fist_palm'},
                                       command={'action': command, 'target': target, 'via': 'sign'}, live=self.live))
            self.palm_target = None
            return
        else:
            return
        self.hud.add_log(action.replace('-', ' '), 'sent')

    def sign_command(self, action, name):
        """A shortcut sign goes through the pipeline like a rail button: logged, refused while paused, verified."""
        self.events.put(InputEvent(source='gesture', kind='gesture', gesture={'name': name},
                                   command={'action': action, 'target': None, 'via': 'sign'}, live=self.live))

    # -- palm dial: shrink the armed window ---------------------------------------------------
    @staticmethod
    def set_rect(hwnd, rect):
        system.move_resize(hwnd, rect)

    def screen_of(self, x, y):
        """Camera coordinates -> screen pixels, with the same mapping as the pointer."""
        margin = self.pointer.margin
        span = 1 - 2 * margin
        width, height = screen_size()
        return (min(1, max(0, (x - margin) / span)) * (width - 1), min(1, max(0, (y - margin) / span)) * (height - 1))

    def shrink(self, curl):
        if not self.palm_target or not self.palm_target.startswith('win:'):
            return
        hwnd = int(self.palm_target[6:], 16)
        if not system.is_window(hwnd) or system.is_iconic(hwnd):
            return
        if self.resize is None or self.resize['hwnd'] != hwnd:
            if system.is_zoomed(hwnd):
                system.restore(hwnd)  # a maximized window is returned to normal size first
            original = system.geometry(hwnd)
            self.resize = {'hwnd': hwnd, 'original': original, 'target': self.palm_target, 'scale': 1.0,
                           'hand0': self.screen_of(*self.palm_pos) if self.palm_pos else None,
                           'center0': (original['x'] + original['w'] / 2, original['y'] + original['h'] / 2)}
            self.curl_smooth = curl
            self.log.write({'type': 'resize', 'target': self.palm_target, 'start': original})
        # Accuracy: smooth the finger curl and ignore wobbles smaller than 2 %.
        self.curl_smooth = self.curl_smooth + .35 * (curl - self.curl_smooth)
        norm = min(1, max(0, (self.curl_smooth - .12) / (.7 - .12)))
        scale = 1 - .7 * norm  # full size .. 30 %
        if abs(scale - self.resize['scale']) >= .02 or scale in (1.0, .3):
            self.resize['scale'] = scale
        self.place()

    def place(self):
        """Target rectangle: current scale, centred where the palm has carried it, kept on screen."""
        r = self.resize
        g = r['original']
        w = max(min(300, g['w']), round(g['w'] * r['scale']))
        h = max(min(200, g['h']), round(g['h'] * r['scale']))
        cx, cy = r['center0']
        if r['hand0'] and self.palm_pos:
            hx, hy = self.screen_of(*self.palm_pos)
            cx, cy = cx + hx - r['hand0'][0], cy + hy - r['hand0'][1]
        from .hud import work_area
        left, top, right, bottom = work_area()
        x = int(min(max(left, cx - w / 2), right - w))
        y = int(min(max(top, cy - h / 2), bottom - h))
        target = {'x': x, 'y': y, 'w': w, 'h': h}
        if self.anim is None or self.anim['hwnd'] != r['hwnd']:
            self.anim = {'hwnd': r['hwnd'], 'cur': {k: float(v) for k, v in g.items()}, 'target': target}
        else:
            self.anim['target'] = target
        self.shrink_label = f"RESIZE {round(r['scale'] * 100)}%  \u00b7 move to place \u00b7 fist = minimize"

    def animate(self):
        """Ease-out glide toward the target rectangle, one step per UI tick (about 60 per second)."""
        a = self.anim
        if not a:
            return
        cur, target = a['cur'], a['target']
        done = True
        for key in ('x', 'y', 'w', 'h'):
            delta = target[key] - cur[key]
            if abs(delta) > .5:
                cur[key] += delta * .35
                done = False
            else:
                cur[key] = target[key]
        self.set_rect(a['hwnd'], {k: int(round(v)) for k, v in cur.items()})
        if done and self.resize is None:
            self.anim = None

    def end_shrink(self):
        if self.resize:
            if self.anim:  # land exactly where it was dropped
                self.set_rect(self.anim['hwnd'], self.anim['target'])
                self.anim = None
            after = system.geometry(self.resize['hwnd'])
            self.log.write({'type': 'resize', 'target': self.resize['target'], 'before': self.resize['original'], 'after': after, 'evidence': f'{system.SOURCE} window rect'})
            self.hud.add_log(f"placed here {after['w']}x{after['h']}", 'verified')
            self.hud.toast(after['x'] + 12, after['y'] + 12, f"Placed here \u00b7 {after['w']}\u00d7{after['h']}", GREEN)
        self.resize, self.shrink_label, self.curl_smooth = None, '', None

    def voice_status(self, event):
        kind = event.get('kind')
        if kind == 'partial':
            self.hud.transcript(event['text'])
        elif kind == 'wake':
            self.hud.chip('mic', 'listening', CYAN)
            self.root.after(6000, lambda: self.hud.chip('mic', 'ready', GREEN))
        elif kind == 'fallback':
            self.hud.transcript(f'…checking "{event["text"]}"')
        elif kind == 'rejected':
            self.hud.transcript(f'Not a command: "{event["text"]}"', final=True)
        elif kind == 'mic':
            ready = event['state'] == 'ready'
            self.hud.chip('mic', 'ready' if ready else 'mic retrying...', GREEN if ready else AMBER)
        elif kind == 'camera-offline':
            self.hud.chip('hand', 'camera offline - retrying', RED)
        elif kind == 'camera-retry':
            self.hud.chip('hand', 'camera starting...', AMBER)
        self.hud.chip('bridge', 'browser linked' if self.bridge.connected else 'browser: no bridge', GREEN if self.bridge.connected else AMBER)

    def decide(self, decision):
        kind = decision[0]
        if kind == 'run':
            self.hud.show_card(None)
            self.log.write(decision[1])
            if decision[1].action == 'screen.capture':
                self.capture(decision[1])  # on the UI thread: the HUD must be hidden while the screen is read
            else:
                self.jobs.put(decision[1])
        elif kind == 'reject':
            self.hud.add_log(decision[2], 'failed' if decision[1] != 'PAUSED' else 'blocked')
            self.hud.transcript(decision[2], final=True)
            self.log.write({'type': 'rejected', 'code': decision[1], 'message': decision[2]})
        elif kind == 'card':
            self.hud.show_card(f"Conflict: {' / '.join(decision[1]['options'])}. {decision[1]['message']}")
        elif kind == 'state':
            state = decision[1]
            if 'mode' in state:
                self.hud.paused(state['mode'] == 'paused')
                self.engine.paused = state['mode'] == 'paused'
            if 'lock' in state:
                self.locked = state['lock']
                self.refresh_lock()
            if 'merged' in state:
                self.hud.add_log('merged voice + gesture', 'verified')
            if 'pending' in state:
                self.hud.show_card(None)

    def refresh_lock(self):
        target = self.registry.get(self.locked) if self.locked else None
        if target and not target.state.get('hidden'):
            self.hud.lock(target.geometry, f'{target.app.upper()}  {target.kind.replace("_", " ").upper()}')
        else:
            self.hud.lock(None, '')
        self.hud.target(f'{target.app}: {target.title[:60]}' if target else None)

    # -- actions -----------------------------------------------------------------------------
    def executor(self):
        while not self.stop.is_set():
            try:
                intent = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                result = self.execute(intent)
            except Exception as error:
                result = failure(intent.intent_id, 'INTERNAL', str(error)[:200])
            self.events.put(('result', intent, result))

    def execute(self, intent):
        if intent.action.startswith('window.'):
            if not intent.target:
                return failure(intent.intent_id, 'TARGET_AMBIGUOUS', 'Point at a window first')
            return self.windows.act(intent)
        if intent.action.startswith('tab.'):
            return self.browser.act(intent)
        if intent.action == 'app.open':
            from .control.apps import normal, open_app
            from .control.web import SITES
            name = normal(intent.args.get('name', ''))
            if self.apps.match(name) is None and (name in SITES or '.' in intent.args.get('name', '')):
                return self.web.act(Intent('web.open', None, 'default', args={'site': intent.args['name']}, intent_id=intent.intent_id))
            return open_app(self.apps, self.registry, intent)
        if intent.action in ('media.play', 'web.search', 'web.open'):
            return self.web.act(intent)
        if intent.action == 'message.draft':
            return self.teams.draft(intent)
        if intent.action == 'message.send':
            return self.teams.send(intent)
        if intent.action == 'browser.focus_or_open':
            if intent.target:
                return self.windows.act(Intent('window.activate', intent.target, intent_id=intent.intent_id))
            return self.launch_browser(intent)
        if intent.action == 'browser.edge_open':
            return self.open_edge(intent)
        if intent.action == 'browser.edge_close':
            return self.close_edge(intent)
        if intent.action in ('volume.up', 'volume.down'):
            return self.step_volume(intent)
        if intent.action in ('display.brightness_up', 'display.brightness_down'):
            return self.step_brightness(intent)
        if intent.action == 'session.lock':
            return self.lock_session(intent)
        if intent.action == 'session.sleep':
            return self.sleep_computer(intent)
        return failure(intent.intent_id, 'INTERNAL', f'{intent.action} is not available in this release')

    # -- grab and move: thumb + index + middle open, close to pick up, open to drop --------------
    def grab_start(self):
        """Pick up what is under the pointer: an application window moves by its handle (grab it
        anywhere, not only the title bar); anything else (desktop icons, files) is a real mouse drag."""
        at = cursor()
        under = self.registry.at(*at)
        icon = self.pointer.icon_at_pointer()
        self.grab = {'start': at, 'hand0': None, 'smooth': None}
        if under and under.id.startswith('win:') and not icon:
            hwnd = int(under.id[6:], 16)
            if system.is_zoomed(hwnd):
                # Like dragging a maximized title bar: it becomes a normal window under the hand.
                system.restore(hwnd)
                g = system.geometry(hwnd)
                self.set_rect(hwnd, {'x': int(at[0] - g['w'] / 2), 'y': int(at[1] - 20), 'w': g['w'], 'h': g['h']})
            self.grab.update(kind='window', hwnd=hwnd, target=under.id, rect=system.geometry(hwnd), title=under.title[:40])
            system.bring_to_front(hwnd)
        else:
            self.pointer.press(at)
            self.grab.update(kind='mouse', title=icon or 'item')
        if self.share and self.link.online():
            # What another device would receive is worked out now, while the hand is still here; it is only
            # offered to them if the hand leaves this camera closed (grab_end 'lost').
            from .link.content import resolve
            from .paths import state_dir
            info = {'kind': self.grab['kind'], 'at': at, 'app': under.app if under else '', 'hwnd': self.grab.get('hwnd'),
                    'title': self.grab['title'], 'icon': icon}
            self.share.grab(lambda: resolve(info, self.bridge, self.link.ip, state_dir() / 'link' / 'outbox'))
        self.log.write({'type': 'grab', 'kind': self.grab['kind'], 'at': at, 'target': self.grab.get('target')})
        self.shrink_label = 'MOVE \u00b7 open fingers to drop'
        self.hud.toast(at[0] + 14, at[1] + 14, f"Picked up {self.grab['title']}", CYAN)

    def grab_move(self, palm):
        g = getattr(self, 'grab', None)
        if not g:
            return
        hand = self.screen_of(*palm)
        if g['hand0'] is None:
            g['hand0'] = g['smooth'] = hand
            return
        # Smooth the palm a little: steady when still, follows quickly when moving.
        g['smooth'] = tuple(s + .5 * (h - s) for s, h in zip(g['smooth'], hand))
        dx, dy = g['smooth'][0] - g['hand0'][0], g['smooth'][1] - g['hand0'][1]
        if g['kind'] == 'window':
            from .hud import work_area
            left, top, right, bottom = work_area()
            r = g['rect']
            x = int(min(max(left - r['w'] + 80, r['x'] + dx), right - 80))  # keep a grip visible on screen
            y = int(min(max(top, r['y'] + dy), bottom - 40))
            self.set_rect(g['hwnd'], {'x': x, 'y': y, 'w': r['w'], 'h': r['h']})
        else:
            width, height = screen_size()
            point =(int(min(max(0, g['start'][0] + dx), width - 1)), int(min(max(0, g['start'][1] + dy), height - 1)))
            self.pointer.drag_to(point)
            g['last'] = point

    def grab_end(self, how=None):
        """how: None = the fingers opened here; 'lost' = the hand left the camera still closed, which with paired
        devices online carries the grabbed content on to whichever device sees the hand open."""
        g = getattr(self, 'grab', None)
        self.grab, self.shrink_label = None, ''
        if not g:
            return
        carried = False
        if self.share:
            if how == 'lost':
                x = self.palm_pos[0] if self.palm_pos else .5
                carried = self.share.arm('left' if x < .25 else 'right' if x > .75 else '')
            else:
                self.share.dropped_here()
        if g['kind'] == 'window':
            after = system.geometry(g['hwnd'])
            self.log.write({'type': 'grab', 'drop': after, 'before': g['rect'], 'target': g['target'], 'evidence': f'{system.SOURCE} window rect',
                            'carried': carried})
            if not carried:
                self.hud.add_log(f"moved {g['title']}", 'verified')
                self.hud.toast(after['x'] + 12, after['y'] + 12, 'Dropped here', GREEN)
        else:
            if carried:
                # Escape before letting go cancels the shell's own drag, so no file is moved on this device.
                self.pointer.send([('key', 'escape', True), ('key', 'escape', False)])
            self.pointer.release(g.get('last'))
            at = cursor()
            self.log.write({'type': 'grab', 'drop': at, 'kind': 'mouse', 'carried': carried})
            if not carried:
                self.hud.toast(at[0] + 14, at[1] + 14, 'Dropped here', GREEN)

    # -- devices: grab here, open the hand at another paired device ------------------------------
    def start_link(self, camera):
        try:
            from .link.node import LinkNode
            from .link.share import DropDetector, Share
            from .link.content import clean_outbox
        except ImportError as error:  # the link needs `cryptography`; the rest of Cameo runs without it
            self.log.write({'type': 'link', 'error': str(error)})
            self.hud.chip('link', 'devices: pip install cryptography', AMBER)
            return
        from .paths import inbox_dir, state_dir

        def post(event):  # any thread -> the UI thread
            self.events.put({'kind': 'link', 'event': event})
        self.link = LinkNode(state_dir() / 'link', caps=['camera'] if camera else [], on_event=post, confirm=self.confirm_pair)
        self.inbox = inbox_dir()
        self.share = Share(self.link, self.inbox, post, self.deliver)
        self.link.on_frame = self.share.on_frame
        self.drop = DropDetector()
        clean_outbox(state_dir() / 'link' / 'outbox')
        self.link.start()
        self.hud.chip('link', 'no devices', DIM)

    def confirm_pair(self, peer, code, role):
        """Called on the link's thread during pairing; waits for the person's answer in the pairing prompt."""
        answered, box = threading.Event(), []

        def reply(ok):
            box.append(ok)
            answered.set()
        self.events.put({'kind': 'link', 'event': {'type': 'pair_prompt', 'peer': peer, 'code': code, 'reply': reply}})
        answered.wait(125)
        return bool(box and box[0])

    def deliver(self, kind, info):
        """Shows what another device dropped here (worker thread). Returns (status, evidence)."""
        if kind == 'link':
            url = info['url']
            if self.bridge.connected:
                since = time.monotonic()
                try:
                    tab = self.bridge.request('open', {'url': url}, timeout=3) or {}
                    created = self.bridge.wait_event(since, lambda e: e.get('event') == 'tab.created' and e.get('tabId') == tab.get('tabId'), 2)
                    return ('verified' if created else 'sent'), {'detail': 'opened in a new tab', 'tab': tab}
                except (ConnectionError, TimeoutError, RuntimeError) as error:
                    self.log.write({'type': 'link', 'open_via_bridge': str(error)[:200]})
            import webbrowser
            webbrowser.open(url, new=2)
            return 'sent', {'detail': 'opened in the default browser'}
        import mimetypes
        from .link.share import SAFE_OPEN
        paths = info['paths']
        if len(paths) == 1 and paths[0].is_file():
            mime = mimetypes.guess_type(paths[0].name)[0] or ''
            if mime.startswith(SAFE_OPEN):
                system.open_path(paths[0])
                return 'sent', {'detail': f'opened {paths[0].name}'}
            system.reveal(paths[0])  # never run a program or script that came from another device
            return 'sent', {'detail': f'saved {paths[0].name}; shown in its folder, not opened'}
        system.open_path(paths[0] if len(paths) == 1 else paths[0].parent)
        return 'sent', {'detail': f'{len(paths)} item(s) saved'}

    def carry_frame(self, frame, t):
        """Every hand frame, before the signs. True = the frame belongs to a carry (the signs do not see it)."""
        conf = self.drop.update(frame.get('pose') if frame.get('hand') else None, t, frame.get('size'))
        if self.share.armed():
            # Another device's carry waits here: the hand can only drop it (no pointer, clicks or minimize meanwhile).
            self.signs.reset()
            if conf and not self.pipeline.paused:
                self.share.claim(conf)
            return True
        if conf and self.share.carrying():
            self.share.dropped_here()  # the hand came back and opened here: take it back
        return False

    def device_rows(self):
        """The rail's connection panel: this device, then each paired device (state, round trip, address, OS,
        placement, camera, how long connected), then devices seen nearby but not paired."""
        link = self.link
        rows = []
        if link.listening is False:
            rows.append((f'This PC: NOT listening ({link.listen_error[:60]})', RED))
        else:
            rows.append((f'This PC: {link.name} · {link.ip}:{link.port}', DIM))
        peers = sorted(link.peers().values(), key=lambda p: (not p['paired'], not p['online'], p['name'].lower()))
        paired = [p for p in peers if p['paired']]
        for p in paired[:4]:
            os_name = {'win32': 'Windows', 'linux': 'Linux', 'darwin': 'macOS'}.get(p['platform'], p['platform'] or '?')
            if p['online']:
                rtt = f"{p['rtt_ms']:.0f} ms" if p['rtt_ms'] is not None else '... ms'
                up = int(time.time() - p['since']) if p['since'] else 0
                age = f'{up // 3600}h{up % 3600 // 60:02d}m' if up >= 3600 else f'{up // 60}m{up % 60:02d}s'
                extras = [os_name, 'camera' if 'camera' in p['caps'] else 'no camera'] + ([f"{p['side']} side"] if p['side'] else [])
                rows.append((f"● {p['name']}  connected · {rtt} · {age}", GREEN))
                rows.append((f"   {p['addr']}:{p['port']} · encrypted · {' · '.join(extras)}", DIM))
            else:
                rows.append((f"○ {p['name']}  offline (last at {p['addr'] or '?'})", AMBER))
        if len(paired) > 4:
            rows.append((f'   +{len(paired) - 4} more paired: see Devices', DIM))
        if not paired:
            rows.append(('No paired devices: Devices > Pair', DIM))
        nearby = [p for p in peers if not p['paired']]
        if nearby:
            names = ', '.join('%s (%s)' % (p['name'], p['addr']) for p in nearby[:3])
            rows.append((f'Nearby, not paired: {names}', CYAN))
        return rows

    def link_label(self):
        if not self.share:
            return ''
        if self.share.armed():
            return 'DROP HERE · open your hand'
        return self.carry_label if self.share.carrying() else ''

    def link_ui(self, action):
        if not self.link:
            self.hud.transcript('Devices are off (Cameo was started without the link, or cryptography is missing)', final=True)
            return
        if action == 'link.devices':
            if self.devices and self.devices.alive:
                self.devices.lift()
            else:
                from .link.ui import DevicesWindow
                self.inbox.mkdir(parents=True, exist_ok=True)
                self.devices = DevicesWindow(self.root, self.link, self.inbox, lambda: system.open_path(self.inbox))
        elif action == 'link.inbox':
            self.inbox.mkdir(parents=True, exist_ok=True)
            system.open_path(self.inbox)

    def link_event(self, ev):
        """Link and carry status on the UI thread: HUD, log, pairing prompt."""
        kind = ev.get('type')
        if kind == 'net_log':
            self.log.write({'type': 'link', 'event': ev})
            return
        if kind == 'pair_prompt':
            from .link.ui import PairPrompt
            PairPrompt(self.root, ev['peer'], ev['code'], ev['reply'])
            return
        if kind not in ('progress', 'receiving'):
            self.log.write({'type': 'link', 'event': ev})
        if kind in ('peers', 'peer_online', 'peer_offline'):
            online = self.link.online()
            self.hud.chip('link', f"{len(online)} device{'s' if len(online) != 1 else ''}" if online else 'no devices', GREEN if online else DIM)
            if kind != 'peers':
                self.hud.add_log(f"{ev['peer']} {'online' if kind == 'peer_online' else 'offline'}", 'verified' if kind == 'peer_online' else 'cancelled')
        elif kind == 'pair_result':
            text = f"Paired with {ev['peer']}" if ev['ok'] else f"Pairing with {ev['peer']} failed: {ev.get('reason', '')}"
            self.hud.add_log(text[:60], 'verified' if ev['ok'] else 'failed')
            self.hud.transcript(text, final=True)
            if self.devices:
                self.devices.show_result(ev['ok'], text)
        elif kind == 'carry_armed':
            self.carry_label = 'CARRYING · open your hand at the other device'
            self.hud.transcript(f"Carrying {ev['label']}: open your hand at {' or '.join(ev['peers'])}", final=True)
        elif kind == 'carry_committed':
            self.hud.transcript(f"Dropping {ev['label']} on {ev['peer']}...", final=True)
        elif kind == 'progress':
            self.hud.transcript(f"Sending to {ev['peer']}: {ev['frac']:.0%}")
        elif kind == 'delivered':
            self.carry_label = ''
            self.hud.add_log(f"{ev['label'][:30]} -> {ev['peer']}", 'verified' if ev['status'] == 'verified' or 'match' in ev['detail'] else 'sent')
            self.hud.transcript(f"Delivered to {ev['peer']} · {ev['detail']}", final=True)
        elif kind in ('carry_failed', 'carry_cancelled'):
            self.carry_label = ''
            self.hud.add_log(ev['reason'][:60], 'failed' if kind == 'carry_failed' else 'cancelled')
            self.hud.transcript(ev['reason'], final=True)
        elif kind == 'incoming':
            what = 'page' if ev['kind'] == 'link' else 'files'
            self.hud.show_card(f"{ev['peer']} is carrying {what}: {ev['label'][:80]}\nOpen your hand here to drop it (or press Do it).")
        elif kind == 'claimed':
            self.hud.show_card(None)
            self.hud.transcript(f"Dropped here: receiving {ev['label']}...", final=True)
        elif kind == 'receiving':
            self.hud.transcript(f"Receiving {ev['label'][:40]} from {ev['peer']}: {ev['frac']:.0%}")
        elif kind == 'received':
            self.hud.show_card(None)
            self.hud.add_log(f"{ev['label'][:30]} from {ev['peer']}", ev['status'])
            where = ev.get('url') or (ev.get('paths') or [''])[0]
            self.hud.transcript(f"From {ev['peer']}: {where}", final=True)
            self.hud.toast(self.hud.width // 2 - 120, self.hud.height // 2, f"Received from {ev['peer']}", GREEN)
        elif kind == 'receive_failed':
            self.hud.show_card(None)
            self.hud.add_log(f"from {ev['peer']}: {ev['reason']}"[:60], 'failed')
            self.hud.transcript(f"Receiving from {ev['peer']} failed: {ev['reason']}", final=True)
        elif kind == 'incoming_end':
            self.hud.show_card(None)
            why = {'taken': 'dropped on another device', 'cancelled': 'taken back', 'expired': 'not dropped in time'}.get(ev['why'], ev['why'])
            self.hud.transcript(f"{ev['peer']}'s {ev['label'][:40]}: {why}", final=True)

    def bridge_watchdog(self):
        """Keeps the browser bridge up: when it has been down for a while (browser closed, or only another
        profile open), the browser is started for the bridge profile without opening a window. At most once a minute."""
        if not self.live or self.bridge.connected:
            return
        now = time.monotonic()
        if now - getattr(self, 'bridge_since', now) < 8 or now - getattr(self, 'bridge_kick', -100) < 60:
            return
        self.bridge_kick = now
        exe, profile = browsers.find()
        if exe and profile:
            subprocess.Popen(browsers.command(exe, profile, '--no-startup-window'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log.write({'type': 'bridge', 'restart': profile})

    def launch_browser(self, intent):
        exe, profile = browsers.find()
        if exe is None:
            return failure(intent.intent_id, 'TARGET_GONE', 'No web browser is installed')
        before = {t.id for t in self.registry.list() if t.kind == 'browser_window'}
        started = time.monotonic()
        subprocess.Popen(browsers.command(exe, profile, '--new-window'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while time.monotonic() - started < 8:
            time.sleep(.25)
            new = [t.id for t in self.registry.list() if t.kind == 'browser_window' and t.id not in before]
            if new:
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'new_window': new[0]}, elapsed_ms=int((time.monotonic() - started) * 1000))
        return failure(intent.intent_id, 'TIMEOUT', 'No browser window appeared within 8 s', elapsed_ms=8000)

    # -- shortcuts: Edge circle, clap screenshot, pinky sleep ----------------------------------------
    def open_edge(self, intent):
        exe, profile = browsers.edge()
        if exe is None:
            return failure(intent.intent_id, 'TARGET_GONE', 'Microsoft Edge is not installed')
        before = {t.id for t in self.registry.list() if t.kind == 'browser_window'}
        started = time.monotonic()
        subprocess.Popen(browsers.command(exe, profile, '--new-window'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while time.monotonic() - started < 8:
            time.sleep(.25)
            new = [t.id for t in self.registry.list() if t.kind == 'browser_window' and t.id not in before]
            if new:
                self.edge_windows.append(new[0])
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'new_window': new[0], 'opened_by_cameo': len(self.edge_windows)},
                                    elapsed_ms=int((time.monotonic() - started) * 1000))
        return failure(intent.intent_id, 'TIMEOUT', 'No Edge window appeared within 8 s', elapsed_ms=8000)

    def close_edge(self, intent):
        """Closes only the Edge windows this Cameo opened; Edge windows the user opened are never touched."""
        mine = [(target, int(target[6:], 16)) for target in self.edge_windows]
        mine = [(target, hwnd) for target, hwnd in mine if system.is_window(hwnd)]
        self.edge_windows = []
        if not mine:
            return failure(intent.intent_id, 'TARGET_GONE', 'No Edge window opened by Cameo is open')
        started = time.monotonic()
        for _, hwnd in mine:
            system.close(hwnd)
        while time.monotonic() - started < 4:
            left = [(target, hwnd) for target, hwnd in mine if system.is_window(hwnd)]
            if not left:
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'closed': [t for t, _ in mine]},
                                    elapsed_ms=int((time.monotonic() - started) * 1000))
            time.sleep(.2)
        self.edge_windows = [target for target, _ in left]  # still open (a "close all tabs?" prompt): try again later
        return failure(intent.intent_id, 'NO_EFFECT', f'{len(left)} Edge window(s) did not close', elapsed_ms=4000)

    def step_volume(self, intent):
        """One VOLUME_STEP up or down, set absolutely and verified by reading the level back (no guessed key presses)."""
        before = system.volume()
        if before is None:
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', 'This computer has no volume control Cameo can call')
        wanted = max(0.0, min(1.0, before + (VOLUME_STEP if intent.action == 'volume.up' else -VOLUME_STEP)))
        if intent.action == 'volume.up' and system.muted():
            system.set_mute(False)
        if abs(wanted - before) > .001 and not system.set_volume(wanted):
            return failure(intent.intent_id, 'NO_EFFECT', 'The sound device did not accept the volume change')
        after = system.volume()
        status = 'verified' if after is not None and abs(after - wanted) <= .011 else 'unverified'
        return ActionResult(intent.intent_id, status, {'source': system.SOURCE, 'volume': None if after is None else round(after * 100)})

    def step_brightness(self, intent):
        """One BRIGHTNESS_STEP up or down, verified by reading the level back."""
        before = system.brightness()
        if before is None:
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', 'This display has no brightness control Cameo can call')
        wanted = max(0, min(100, before + (BRIGHTNESS_STEP if intent.action == 'display.brightness_up' else -BRIGHTNESS_STEP)))
        if wanted == before:
            return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'brightness': before, 'limit': True})
        started = time.monotonic()
        if not system.set_brightness(wanted):
            return failure(intent.intent_id, 'NO_EFFECT', 'The display did not accept the brightness change')
        while time.monotonic() - started < 1:
            # Some panels fade to the new level or round it to their own steps: any move the right way counts.
            now = system.brightness()
            if now is not None and now != before and (now > before) == (wanted > before):
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'brightness': now},
                                    elapsed_ms=int((time.monotonic() - started) * 1000))
            time.sleep(.05)
        return ActionResult(intent.intent_id, 'sent', {'source': system.SOURCE, 'brightness': wanted}, elapsed_ms=1000)

    def lock_session(self, intent):
        if not system.lock_screen():
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', 'This session has no screen locker Cameo can call')
        started = time.monotonic()
        while time.monotonic() - started < 3:
            if not system.interactive_desktop():
                return ActionResult(intent.intent_id, 'verified', {'source': system.SOURCE, 'locked': True})
            time.sleep(.2)
        return ActionResult(intent.intent_id, 'sent', {'source': system.SOURCE})

    def sleep_computer(self, intent):
        # Set first: on a classic S3 machine the call only returns after the computer wakes again.
        self.asleep = (time.monotonic(), cursor())
        if not system.sleep_computer():
            self.asleep = None
            return failure(intent.intent_id, 'SESSION_UNSUPPORTED', 'This computer has no sleep Cameo can call')
        return ActionResult(intent.intent_id, 'sent', {'source': system.SOURCE, 'sleep': True})

    def sleeping(self):
        """True from the sleep sign until the user is back at the keyboard or mouse. Any hand move would be injected
        input, which wakes the display (Modern Standby) straight away, so the camera is ignored meanwhile."""
        if self.asleep is None:
            return False
        since, where = self.asleep
        idle = system.idle_seconds()
        back = idle < time.monotonic() - since - .5 if idle is not None else cursor() != where
        if back:
            self.asleep = None
            self.log.write({'type': 'sign', 'action': 'wake'})
        return not back

    def capture(self, intent):
        from .control.screenshot import capture
        self.hud.visible(False)

        def shoot():
            try:
                path, size = capture()
                result = ActionResult(intent.intent_id, 'verified', {'path': str(path), 'size': list(size), 'bytes': path.stat().st_size})
            except Exception as error:
                result = failure(intent.intent_id, 'DEVICE_UNAVAILABLE', f'Screenshot failed: {str(error)[:150]}')
            finally:
                self.hud.visible(True)
            self.show_result(intent, result)
        self.root.after(150, shoot)  # let the compositor draw the screen without the HUD first

    def show_result(self, intent, result):
        self.log.write(result)
        if intent.action == 'message.draft' and result.status == 'pending_user':
            to, text = result.evidence['to'], result.evidence['text']
            confirm = Intent('message.send', None, 'default', args={'to': to, 'text': text}, inputs=list(intent.inputs))
            for decision in self.pipeline.await_confirm(confirm, f'Say "send it" to send, or "cancel".'):
                self.hud.show_card(f'Teams \u2192 {to}: "{text[:80]}"  \u00b7  say "send it" or "cancel"')
            self.hud.add_log(f'draft for {to} ready', 'pending_user')
            self.hud.transcript(f'To {to}: "{text}" \u2014 say "send it" or "cancel"', final=True)
            return
        if intent.action in ('app.open', 'media.play', 'web.search', 'web.open'):
            e = result.evidence or {}
            if result.status in ('verified', 'sent'):
                text = {'app.open': f"Opened {e.get('app') or e.get('site', '')}",
                        'media.play': f"Playing: {(e.get('title') or e.get('query', '')).replace(' - YouTube', '')[:70]}",
                        'web.search': f"Searched: {e.get('query', '')}", 'web.open': f"Opened {e.get('site', '')}"}[intent.action]
                text += ' \u2713' if result.status == 'verified' else ' (not confirmed yet)'
            else:
                text = result.error['message'] if result.error else result.status
            self.hud.add_log(text, result.status)
            self.hud.transcript(text, final=True)
            return
        if intent.action.startswith('message.'):
            ok = result.status in ('verified', 'sent')
            text = (f"Sent to {result.evidence.get('to')} \u2713" if ok else f"Message: {result.error['message'] if result.error else result.status}")
            self.hud.add_log(text, result.status)
            self.hud.transcript(text, final=True)
            return
        label = {'browser.edge_open': 'open Edge', 'browser.edge_close': 'close Edge', 'volume.up': 'volume up', 'volume.down': 'volume down',
                 'session.lock': 'lock', 'session.sleep': 'sleep'}.get(intent.action) or intent.action.split('.', 1)[1].replace('_', ' ')
        if intent.action.startswith('display.brightness') and result.evidence.get('brightness') is not None:
            label = f"brightness {result.evidence['brightness']}%"
        if intent.action.startswith('volume.') and result.evidence.get('volume') is not None:
            label = f"volume {result.evidence['volume']}%"
        if result.evidence.get('outcome') == 'cancel':
            # A level sign that was cancelled went back to where it started; say so, with the read-back.
            label = f"{label.split(' ')[0]} back to {result.evidence.get(label.split(' ')[0])}%"
        if intent.action == 'screen.capture' and result.status == 'verified':
            label = f"screenshot saved: {result.evidence['path'].rsplit(os.sep, 1)[-1]}"
            self.hud.transcript(f"Screenshot → {result.evidence['path']}", final=True)
        text = {'verified': f'{label[0].upper()}{label[1:]} ✓', 'sent': f'{label[0].upper()}{label[1:]}', 'pending_user': f'{label}: page is asking to confirm'}.get(
            result.status, f'{label}: {result.error["message"] if result.error else result.status}')
        self.hud.add_log(text, result.status)
        target = self.registry.get(intent.target) if intent.target and intent.target.startswith('win:') else None
        x, y = (target.geometry['x'] + 20, target.geometry['y'] + 40) if target and not target.state.get('hidden') else (40, 60)
        self.hud.toast(x, y, text, STATUS_COLOUR.get(result.status, CYAN))
        if intent.target == self.locked:
            self.refresh_lock()

    def quit(self):
        self.stop.set()
        self.levels.close()
        if self.vision is not None:
            try:
                self.vision_conn.send('stop')
            except OSError:
                pass
            self.vision.join(2)
            if self.vision.is_alive():
                self.vision.terminate()
        self.bridge.close()
        if self.link:
            self.link.stop()
        self.root.destroy()

    def force_stop(self):
        """Rail button: shut down cleanly, then end the process even if a thread or device hangs."""
        try:
            self.quit()
        except Exception:
            pass
        finally:
            if self.vision is not None and self.vision.is_alive():
                self.vision.kill()  # release the camera
            os._exit(0)

    def run(self):
        self.root.mainloop()


def run(**options):  # gestures='classic' (the user's signs) or 'dock' (ARCHITECTURE.md §8.3); link=False: no devices
    Runtime(**options).run()
