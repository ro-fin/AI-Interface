"""Lightweight HUD (docs/AETHER-WINDOWS.md W3; visual language of ARCHITECTURE.md §8).

Overlay: full-screen, transparent, click-through, never focused: reticle, lock-on brackets, result toasts.
Rail: right edge, always on top, never takes focus: status, transcript, target chip, action dock, log.
Motion only follows real events; nothing animates idly.

Windows makes the overlay see-through with a transparent colour key; X11 has none, so there the overlay
window is cut to the shape of what is drawn (XShape) after every change, and clicks always pass through.
"""
import json
import time
import tkinter as tk

from .control import system
from .control.system import work_area  # noqa: F401  (screen area without the taskbar / panels)
from .gestures import shapes
from .paths import WINDOWS, state_dir

CYAN, AMBER, RED, GREEN = '#38E1FF', '#FFB547', '#FF5D5D', '#5DFFB0'
GLASS, EDGE, TEXT, DIM = '#080C12', '#1E3A48', '#E8F6FF', '#7F95A3'
KEY = '#010203'  # transparent colour key for the overlay
FONT, MONO = ('Segoe UI', 'Consolas') if WINDOWS else ('Ubuntu', 'Ubuntu Mono')  # Tk falls back if missing
RAIL_WIDTH = 340
TIPS = (4, 8, 12, 16, 20)  # thumb, index, middle, ring, little fingertips: the only hand marks shown (user's choice)
BALL = 56
LEVEL_WIDTH = 360  # pixels of the level bar
STATE_FILE = state_dir() / 'hud.json'
DOCK = [  # (section, buttons, columns, compact)
    ('WINDOW', [('window.maximize', 'Maximize'), ('window.minimize', 'Minimize'), ('window.restore', 'Restore'), ('window.activate', 'Focus')], 2, False),
    ('BROWSER', [('browser.focus_or_open', 'Open browser'), ('tab.new', 'New tab'), ('tab.prev', 'Prev tab'), ('tab.next', 'Next tab'), ('tab.close', 'Close tab (hold)')], 2, False),
    # Each also has a sign: circle drawn with index + middle, both hands clap, a line drawn right / left (volume, set and
    # read back), pinky held still. Brightness has only its sign: a line drawn up / down with index + middle.
    ('SHORTCUTS', [('browser.edge_open', 'Edge ↻'), ('browser.edge_close', 'Close Edge ↺'), ('screen.capture', 'Screenshot'),
                   ('volume.down', 'Vol −'), ('volume.up', 'Vol +'), ('session.sleep', 'Sleep')], 3, True),
    # Grab something (three fingers, close), move the closed hand to another paired device, open it there.
    ('DEVICES', [('link.devices', 'Devices'), ('link.inbox', 'Inbox')], 2, True),
]
STATUS_COLOUR = {'verified': GREEN, 'failed': RED, 'blocked': RED, 'unverified': AMBER, 'pending_user': AMBER, 'sent': AMBER, 'cancelled': DIM}


class Hud:
    def __init__(self, root, on_ui, on_quit=None):
        """on_ui(action) is called for dock buttons pressed with the mouse (source 'hud').
        on_quit() is called by the rail's Force stop button."""
        self.root, self.on_ui, self.on_quit = root, on_ui, on_quit
        self.width, self.height = root.winfo_screenwidth(), root.winfo_screenheight()
        self.buttons = {}
        self.toasts = []
        self.log_rows = []
        self.collapsed = False
        self.build_overlay()
        self.build_rail()
        self.build_ball()
        if self.load_state().get('collapsed'):
            self.collapse()

    # -- overlay -----------------------------------------------------------------------------
    def build_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.geometry(f'{self.width}x{self.height}+0+0')
        self.overlay.attributes('-topmost', True)
        self.overlay.configure(bg=KEY)
        self.canvas = tk.Canvas(self.overlay, bg=KEY, highlightthickness=0, width=self.width, height=self.height)
        self.canvas.pack()
        system.hud_prepare(self.overlay, 'overlay', KEY)
        self.reticle = self.canvas.create_oval(0, 0, 0, 0, outline=CYAN, width=2, state='hidden')
        self.dot = self.canvas.create_oval(0, 0, 0, 0, fill=CYAN, outline='', state='hidden')
        self.arc = self.canvas.create_arc(0, 0, 0, 0, start=90, extent=0, style='arc', outline=AMBER, width=3, state='hidden')
        self.brackets = [self.canvas.create_line(0, 0, 0, 0, 0, 0, fill=CYAN, width=3, state='hidden') for _ in range(4)]
        self.bracket_label = self.canvas.create_text(0, 0, text='', fill=CYAN, anchor='sw', font=(FONT, 11, 'bold'), state='hidden')
        self.tips = [self.canvas.create_oval(0, 0, 0, 0, fill='#D6FFF4', outline='', state='hidden') for _ in TIPS]
        self.sign_label = self.canvas.create_text(0, 0, text='', fill=GREEN, anchor='sw', font=(FONT, 12, 'bold'), state='hidden')
        self.trail_line = self.canvas.create_line(0, 0, 0, 0, fill=CYAN, width=4, smooth=True, capstyle='round', state='hidden')
        self.paused_glyph = self.canvas.create_text(self.width // 2, 40, text='CONTROLS PAUSED', fill=AMBER, font=(FONT, 16, 'bold'), state='hidden')
        # Level bar (docs/SIGN-PROTOCOL-PLAN.md, R5): base tick, target marker, read-back fill, value text.
        cx, top, w, h = self.width // 2, 78, LEVEL_WIDTH, 14
        self.level_box = (cx - w // 2, top, w, h)
        c = self.canvas
        self.level_items = {
            'back': c.create_rectangle(cx - w // 2, top, cx + w // 2, top + h, fill='#0B1A22', outline=DIM, width=1, state='hidden'),
            'fill': c.create_rectangle(cx - w // 2, top, cx - w // 2, top + h, fill=CYAN, outline='', state='hidden'),
            'base': c.create_line(0, top - 5, 0, top + h + 5, fill='#D6FFF4', width=2, state='hidden'),
            'target': c.create_polygon(0, 0, 0, 0, 0, 0, fill=AMBER, outline='', state='hidden'),
            'text': c.create_text(cx, top - 16, text='', fill=CYAN, font=(FONT, 13, 'bold'), state='hidden'),
        }
        # Hold progress arc at the fingertip (sleep countdown, right click, minimize, grab).
        self.hold_arc = c.create_arc(0, 0, 0, 0, start=90, extent=0, style='arc', outline=GREEN, width=4, state='hidden')
        self.hold_text = c.create_text(0, 0, text='', fill=GREEN, anchor='s', font=(FONT, 11, 'bold'), state='hidden')

    def pointer(self, x, y, engaged, progress=0.0):
        r = 14
        self.canvas.coords(self.reticle, x - r, y - r, x + r, y + r)
        self.canvas.coords(self.dot, x - 3, y - 3, x + 3, y + 3)
        self.canvas.coords(self.arc, x - r - 5, y - r - 5, x + r + 5, y + r + 5)
        self.canvas.itemconfigure(self.reticle, state='normal')
        self.canvas.itemconfigure(self.dot, state='normal' if engaged else 'hidden')
        self.canvas.itemconfigure(self.arc, state='normal' if progress > 0 else 'hidden', extent=-360 * min(1, progress))

    def skeleton(self, points, margin, label=''):
        """Draws the five fingertips (mirrored camera landmarks) with the same camera-to-screen mapping as the pointer."""
        if not points or len(points) != 21:
            for item in self.tips + [self.sign_label]:
                self.canvas.itemconfigure(item, state='hidden')
            return
        span = 1 - 2 * margin
        coords = [(max(0, min(self.width - 1, (x - margin) / span * (self.width - 1))),
                   max(0, min(self.height - 1, (y - margin) / span * (self.height - 1)))) for x, y in points]
        for item, index in zip(self.tips, TIPS):
            x, y = coords[index]
            r = 7 if index == 8 else 5  # the index tip is the pointer: a little larger, in amber
            self.canvas.coords(item, x - r, y - r, x + r, y + r)
            self.canvas.itemconfigure(item, state='normal', fill=AMBER if index == 8 else '#D6FFF4')
        self.canvas.coords(self.sign_label, coords[8][0] + 12, coords[8][1] - 10)
        self.canvas.itemconfigure(self.sign_label, text=label, state='normal' if label else 'hidden')

    def trail(self, points, margin, colour=CYAN, shape=None):
        """The shortcut being drawn with index + middle (mirrored camera points, same mapping as the pointer).
        shape 'circle' or 'line' shows the recognised shape cleanly (a true circle, a straight line) instead of the path."""
        if not points or len(points) < 2:
            self.canvas.itemconfigure(self.trail_line, state='hidden')
            return
        span = 1 - 2 * margin
        screen = [(max(0, min(1, (x - margin) / span)) * (self.width - 1), max(0, min(1, (y - margin) / span)) * (self.height - 1))
                  for x, y in points]
        if shape == 'circle' and len(screen) >= 3:
            screen = shapes.circle(screen)
        elif shape == 'line':
            screen = shapes.line(screen)
        self.canvas.coords(self.trail_line, *[value for point in screen for value in point])
        self.canvas.itemconfigure(self.trail_line, state='normal', fill=colour)

    def level(self, state):
        """The level bar: state = {'quantity', 'base', 'target', 'actual', 'state', 'error'} (levels.Levels.snapshot) or None."""
        items = self.level_items
        if not state or state.get('base') is None and not state.get('error'):
            for item in items.values():
                self.canvas.itemconfigure(item, state='hidden')
            return
        left, top, w, h = self.level_box
        x = lambda value: left + max(0, min(1, value)) * w
        name = state['quantity'].upper()
        colour = RED if state.get('error') else {'commit': GREEN, 'cancel': AMBER, 'done': GREEN}.get(state['state'], CYAN)
        if state.get('error'):
            text = f"{name}: {state['error']}"
        else:
            actual, target, base = state['actual'], state['target'], state['base']
            shown = actual if actual is not None else base
            text = f"{name} {round(shown * 100)} %"
            if target is not None and abs(target - shown) >= .01:
                text += f"  →  {round(target * 100)} %"
            self.canvas.coords(items['fill'], left, top, x(shown), top + h)
            self.canvas.itemconfigure(items['fill'], fill=colour)
            self.canvas.coords(items['base'], x(base), top - 5, x(base), top + h + 5)
            if target is not None:
                tx = x(target)
                self.canvas.coords(items['target'], tx - 7, top - 10, tx + 7, top - 10, tx, top - 1)
        self.canvas.itemconfigure(items['text'], text=text, fill=colour)
        for item in items.values():
            self.canvas.itemconfigure(item, state='normal')

    def progress(self, point, name=None, fraction=0.0):
        """An arc at `point` (screen pixels) filling to `fraction` for a hold in progress; point None hides it."""
        if point is None or not name:
            self.canvas.itemconfigure(self.hold_arc, state='hidden')
            self.canvas.itemconfigure(self.hold_text, state='hidden')
            return
        x, y, r = point[0], point[1], 30
        self.canvas.coords(self.hold_arc, x - r, y - r, x + r, y + r)
        self.canvas.itemconfigure(self.hold_arc, extent=-359.9 * max(0, min(1, fraction)), state='normal')
        self.canvas.coords(self.hold_text, x, y - r - 6)
        self.canvas.itemconfigure(self.hold_text, text=name.upper().replace('-', ' '), state='normal')

    def visible(self, shown):
        """Hides every HUD window for a screenshot (by opacity: showing a window again could take the focus)."""
        self.overlay.attributes('-alpha', 1.0 if shown else 0.0)
        self.rail.attributes('-alpha', .94 if shown else 0.0)
        self.ball.attributes('-alpha', 1.0 if shown else 0.0)
        self.root.update_idletasks()

    def hide_pointer(self):
        for item in (self.reticle, self.dot, self.arc):
            self.canvas.itemconfigure(item, state='hidden')

    def lock(self, geometry, label):
        """Corner brackets on the real window edges."""
        if not geometry:
            for item in self.brackets + [self.bracket_label]:
                self.canvas.itemconfigure(item, state='hidden')
            return
        x0, y0 = geometry['x'], geometry['y']
        x1, y1 = x0 + geometry['w'], y0 + geometry['h']
        n = 28
        corners = [(x0, y0 + n, x0, y0, x0 + n, y0), (x1 - n, y0, x1, y0, x1, y0 + n),
                   (x1, y1 - n, x1, y1, x1 - n, y1), (x0 + n, y1, x0, y1, x0, y1 - n)]
        for item, coords in zip(self.brackets, corners):
            self.canvas.coords(item, *coords)
            self.canvas.itemconfigure(item, state='normal')
        self.canvas.coords(self.bracket_label, x0 + 4, max(18, y0 - 4))
        self.canvas.itemconfigure(self.bracket_label, text=label, state='normal')

    def toast(self, x, y, text, colour):
        item = self.canvas.create_text(x, y, text=text, fill=colour, font=(FONT, 13, 'bold'), anchor='nw')
        self.toasts.append((item, time.monotonic() + 3))

    def paused(self, value):
        self.canvas.itemconfigure(self.paused_glyph, state='normal' if value else 'hidden')
        self.mode.configure(text='PAUSED' if value else 'ACTIVE', fg=AMBER if value else CYAN)
        self.ball_canvas.itemconfigure(self.ball_ring, outline=AMBER if value else CYAN)
        self.ball_canvas.itemconfigure(self.ball_text, fill=AMBER if value else CYAN)

    # -- collapsed ball ------------------------------------------------------------------------
    def build_ball(self):
        self.ball = tk.Toplevel(self.root)
        self.ball.overrideredirect(True)
        self.ball.attributes('-topmost', True)
        self.ball.configure(bg=KEY)
        left, top, right, bottom = work_area()
        self.ball.geometry(f'{BALL}x{BALL}+{right - BALL - 16}+{bottom - BALL - 16}')
        self.ball_canvas = tk.Canvas(self.ball, width=BALL, height=BALL, bg=KEY, highlightthickness=0, cursor='hand2')
        self.ball_canvas.pack()
        self.ball_ring = self.ball_canvas.create_oval(3, 3, BALL - 3, BALL - 3, fill=GLASS, outline=CYAN, width=3)
        self.ball_text = self.ball_canvas.create_text(BALL // 2, BALL // 2, text='CM', fill=CYAN, font=(FONT, 15, 'bold'))
        self.ball_canvas.bind('<Button-1>', lambda event: self.expand())
        system.hud_prepare(self.ball, 'ball', KEY)
        system.hud_shape(self.ball, self.ball_canvas, KEY)  # X11: a round window
        self.ball.withdraw()

    def collapse(self):
        self.collapsed = True
        self.rail.withdraw()
        self.ball.deiconify()
        self.ball.lift()
        self.save_state()

    def expand(self):
        self.collapsed = False
        self.ball.withdraw()
        self.rail.deiconify()
        self.rail.lift()
        self.save_state()

    @staticmethod
    def load_state():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({'collapsed': self.collapsed}), encoding='utf-8')
        except OSError:
            pass

    # -- rail --------------------------------------------------------------------------------
    def build_rail(self):
        self.rail = tk.Toplevel(self.root)
        self.rail.overrideredirect(True)
        self.rail.attributes('-topmost', True)
        self.rail.attributes('-alpha', .94)
        left, top, right, bottom = work_area()
        self.rail.geometry(f'{RAIL_WIDTH}x{bottom - top}+{right - RAIL_WIDTH}+{top}')
        self.rail.configure(bg=GLASS, highlightthickness=1, highlightbackground=EDGE)
        system.hud_prepare(self.rail, 'rail', KEY)
        head = tk.Frame(self.rail, bg=GLASS)
        head.pack(fill='x', padx=16, pady=(14, 6))
        tk.Label(head, text='CAMEO', bg=GLASS, fg=TEXT, font=(FONT, 20, 'bold')).pack(side='left')
        # Minimize the rail to a small ball in the bottom-right corner.
        self.minimize_button = tk.Button(head, text='\u2014', command=self.collapse, bg=GLASS, fg=TEXT, activebackground='#17324A',
                                         activeforeground=TEXT, relief='flat', font=(FONT, 12, 'bold'), width=3)
        self.minimize_button.pack(side='right', padx=(8, 0))
        self.mode = tk.Label(head, text='ACTIVE', bg=GLASS, fg=CYAN, font=(FONT, 11, 'bold'))
        self.mode.pack(side='right')
        chips = tk.Frame(self.rail, bg=GLASS)
        chips.pack(fill='x', padx=16)
        self.chips = {}
        for name in ('mic', 'hand', 'bridge'):
            label = tk.Label(chips, text=f'● {name}', bg=GLASS, fg=DIM, font=(FONT, 10))
            label.pack(side='left', padx=(0, 12))
            self.chips[name] = label
        self.chips['link'] = tk.Label(self.rail, text='● devices', bg=GLASS, fg=DIM, font=(FONT, 10), anchor='w')
        self.chips['link'].pack(fill='x', padx=16)  # its own row: the three above already fill the rail's width
        # Connection details of each paired device (filled by devices()); empty when the link is off.
        self.device_panel = tk.Frame(self.rail, bg='#0B141C')
        self.device_panel.pack(fill='x', padx=16, pady=(4, 0))
        self.device_rows = []
        self.ticker = tk.Label(self.rail, text='Say "Hey Cameo" or raise your hand', bg=GLASS, fg=DIM, font=(MONO, 11), wraplength=RAIL_WIDTH - 32, justify='left', anchor='w')
        self.ticker.pack(fill='x', padx=16, pady=(12, 4))
        self.target_chip = tk.Label(self.rail, text='No target', bg='#102030', fg=CYAN, font=(FONT, 11), anchor='w', padx=8, pady=6, wraplength=RAIL_WIDTH - 40, justify='left')
        self.target_chip.pack(fill='x', padx=16, pady=(6, 10))
        # The footer is packed before the dock so that on a short screen the action log shrinks, not the Pause button.
        foot = tk.Frame(self.rail, bg=GLASS)
        foot.pack(side='bottom', fill='x', padx=12, pady=12)
        for section, buttons, columns, compact in DOCK:
            tk.Label(self.rail, text=section, bg=GLASS, fg=DIM, font=(FONT, 9, 'bold')).pack(anchor='w', padx=16, pady=(8, 2))
            grid = tk.Frame(self.rail, bg=GLASS)
            grid.pack(fill='x', padx=12)
            for index, (action, label) in enumerate(buttons):
                button = tk.Button(grid, text=label, bg='#0F1B26', fg=TEXT, activebackground='#17324A', activeforeground=TEXT, relief='flat',
                                   font=(FONT, 10 if compact else 12), height=1 if compact else 2, command=lambda a=action: self.on_ui(a))
                button.grid(row=index // columns, column=index % columns, sticky='ew', padx=4, pady=3 if compact else 4)
                grid.columnconfigure(index % columns, weight=1, uniform=section)
                self.buttons[f'dock:{action}'] = button
        self.card = tk.Frame(self.rail, bg='#2A2110')
        self.card_text = tk.Label(self.card, text='', bg='#2A2110', fg=AMBER, font=(FONT, 11), wraplength=RAIL_WIDTH - 50, justify='left')
        self.card_text.pack(fill='x', padx=8, pady=6)
        row = tk.Frame(self.card, bg='#2A2110')
        row.pack(fill='x', padx=6, pady=(0, 6))
        tk.Button(row, text='Do it', command=lambda: self.on_ui('control.confirm'), bg='#3A2E12', fg=TEXT, relief='flat').pack(side='left', expand=True, fill='x', padx=2)
        tk.Button(row, text='Cancel', command=lambda: self.on_ui('control.cancel'), bg='#3A2E12', fg=TEXT, relief='flat').pack(side='left', expand=True, fill='x', padx=2)
        tk.Label(self.rail, text='ACTIONS', bg=GLASS, fg=DIM, font=(FONT, 9, 'bold')).pack(anchor='w', padx=16, pady=(12, 2))
        self.log = tk.Frame(self.rail, bg=GLASS)
        self.log.pack(fill='x', padx=16)
        self.pause_button = tk.Button(foot, text='Pause  (Ctrl+Alt+P)', bg='#2A2110', fg=AMBER, relief='flat', font=(FONT, 11), command=lambda: self.on_ui('control.toggle'))
        self.pause_button.pack(fill='x')
        if self.on_quit:
            # Stops everything: camera, microphone, and the Cameo process itself.
            tk.Button(foot, text='Force stop', bg='#2A1010', fg=RED, relief='flat', font=(FONT, 11), command=self.on_quit).pack(fill='x', pady=(6, 0))
        tk.Label(foot, text='Quit: Ctrl+Alt+Q   Talk: hold Ctrl+Alt+A', bg=GLASS, fg=DIM, font=(FONT, 9)).pack(pady=(6, 0))

    def dock_at(self, x, y):
        """Screen point -> 'dock:<action>' when over a dock button (for gesture hit-testing)."""
        if self.collapsed:
            return None
        for name, button in self.buttons.items():
            bx, by = button.winfo_rootx(), button.winfo_rooty()
            if bx <= x < bx + button.winfo_width() and by <= y < by + button.winfo_height():
                return name
        return None

    def over_rail(self, x, y):
        return not self.collapsed and x >= self.width - RAIL_WIDTH

    def hover(self, name):
        for key, button in self.buttons.items():
            button.configure(bg='#17324A' if key == name else '#0F1B26')

    def chip(self, name, text, colour):
        self.chips[name].configure(text=f'● {text}', fg=colour)

    def devices(self, rows):
        """rows: [(text, colour)], one per line of the connection panel. Labels are reused while the number of
        lines stays the same, so the once-a-second refresh does not flicker the rail."""
        if len(rows) != len(self.device_rows):
            for label in self.device_rows:
                label.destroy()
            self.device_rows = [tk.Label(self.device_panel, bg='#0B141C', font=(MONO, 9), anchor='w', justify='left',
                                         wraplength=RAIL_WIDTH - 44) for _ in rows]
            for label in self.device_rows:
                label.pack(fill='x', padx=6)
        for label, (text, colour) in zip(self.device_rows, rows):
            if label.cget('text') != text or label.cget('fg') != colour:
                label.configure(text=text, fg=colour)

    def transcript(self, text, final=False):
        self.ticker.configure(text=text, fg=TEXT if final else DIM)

    def target(self, text):
        self.target_chip.configure(text=text or 'No target')

    def show_card(self, text):
        if text:
            self.card_text.configure(text=text)
            self.card.pack(fill='x', padx=16, pady=6, before=self.log)
        else:
            self.card.pack_forget()

    def add_log(self, text, status):
        row = tk.Label(self.log, text=f'{status.upper():9} {text}', bg=GLASS, fg=STATUS_COLOUR.get(status, TEXT), font=(MONO, 10), anchor='w')
        row.pack(fill='x', before=self.log_rows[0] if self.log_rows else None)
        self.log_rows.insert(0, row)
        for old in self.log_rows[7:]:
            old.destroy()
        self.log_rows = self.log_rows[:7]

    def tick(self):
        now = time.monotonic()
        for item, until in list(self.toasts):
            if now > until:
                self.canvas.delete(item)
                self.toasts.remove((item, until))
        system.hud_shape(self.overlay, self.canvas, KEY)
        if not WINDOWS and now - getattr(self, 'raised', 0) > 2:
            # X11 ignores "topmost" for these override-redirect windows; raising them keeps them above new windows.
            self.raised = now
            self.overlay.lift()
            (self.ball if self.collapsed else self.rail).lift()
