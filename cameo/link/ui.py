"""The Devices window (pair, place, forget) and the pairing prompt. Tk, on the UI thread only."""
import tkinter as tk

from ..hud import AMBER, CYAN, DIM, EDGE, FONT, GLASS, GREEN, MONO, RED, TEXT
from .node import PORT

BUTTON = {'bg': '#0F1B26', 'fg': TEXT, 'activebackground': '#17324A', 'activeforeground': TEXT, 'relief': 'flat', 'font': (FONT, 10)}


class PairPrompt:
    """Numeric comparison: both devices show the same 6 digits; the person confirms here only if they match."""

    def __init__(self, root, peer, code, answer):
        self.answer, self.closed = answer, False
        self.top = top = tk.Toplevel(root)
        top.title('Cameo - pair a device')
        top.configure(bg=GLASS, highlightthickness=1, highlightbackground=EDGE)
        top.attributes('-topmost', True)
        top.resizable(False, False)
        tk.Label(top, text=f'Pair with {peer}?', bg=GLASS, fg=TEXT, font=(FONT, 13, 'bold')).pack(padx=24, pady=(18, 6))
        tk.Label(top, text=f'{code[:3]} {code[3:]}', bg=GLASS, fg=CYAN, font=(MONO, 30, 'bold')).pack(padx=24, pady=6)
        tk.Label(top, text='Pair only if the other device shows the same code.\nConfirm on both devices.', bg=GLASS, fg=DIM,
                 font=(FONT, 10), justify='center').pack(padx=24, pady=(0, 12))
        row = tk.Frame(top, bg=GLASS)
        row.pack(fill='x', padx=18, pady=(0, 16))
        tk.Button(row, text='Same code - pair', command=lambda: self.done(True), **{**BUTTON, 'fg': GREEN}).pack(side='left', expand=True, fill='x', padx=4)
        tk.Button(row, text='Cancel', command=lambda: self.done(False), **BUTTON).pack(side='left', expand=True, fill='x', padx=4)
        top.protocol('WM_DELETE_WINDOW', lambda: self.done(False))
        top.after(120_000, lambda: self.done(False))  # the code expires with the link's pairing timeout
        top.lift()
        top.focus_force()

    def done(self, ok):
        if self.closed:
            return
        self.closed = True
        self.top.destroy()
        self.answer(ok)


class DevicesWindow:
    """This device, paired devices (online state, which side of this screen they stand, forget), devices seen
    nearby (pair) and pairing by address. Refreshes itself once a second while open."""

    def __init__(self, root, node, inbox, open_inbox):
        self.node = node
        self.top = top = tk.Toplevel(root)
        top.title('Cameo - devices')
        top.configure(bg=GLASS)
        top.attributes('-topmost', True)
        top.geometry('460x520')
        self.status = tk.StringVar(value='')
        head = tk.Frame(top, bg=GLASS)
        head.pack(fill='x', padx=16, pady=(14, 4))
        tk.Label(head, text='DEVICES', bg=GLASS, fg=CYAN, font=(FONT, 13, 'bold')).pack(side='left')
        self.me = tk.Label(top, text='', bg=GLASS, fg=DIM, font=(MONO, 10), anchor='w', justify='left')
        self.me.pack(fill='x', padx=16)
        self.body = tk.Frame(top, bg=GLASS)
        self.body.pack(fill='both', expand=True, padx=16, pady=6)
        add = tk.Frame(top, bg=GLASS)
        add.pack(fill='x', padx=16, pady=4)
        tk.Label(add, text='Pair by address', bg=GLASS, fg=DIM, font=(FONT, 9, 'bold')).pack(anchor='w')
        row = tk.Frame(add, bg=GLASS)
        row.pack(fill='x', pady=2)
        self.address = tk.Entry(row, bg='#0F1B26', fg=TEXT, insertbackground=TEXT, relief='flat', font=(MONO, 11))
        self.address.pack(side='left', fill='x', expand=True, ipady=4)
        self.address.bind('<Return>', lambda e: self.pair_address())
        tk.Button(row, text='Pair', command=self.pair_address, **BUTTON).pack(side='left', padx=(6, 0))
        tk.Label(top, textvariable=self.status, bg=GLASS, fg=AMBER, font=(FONT, 10), wraplength=420, justify='left', anchor='w').pack(fill='x', padx=16, pady=4)
        foot = tk.Frame(top, bg=GLASS)
        foot.pack(fill='x', padx=16, pady=(0, 12))
        tk.Label(foot, text=f'Inbox: {inbox}', bg=GLASS, fg=DIM, font=(FONT, 9), anchor='w').pack(side='left', fill='x', expand=True)
        tk.Button(foot, text='Open inbox', command=open_inbox, **BUTTON).pack(side='right')
        self.shown = None
        self.refresh()

    @property
    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def lift(self):
        self.top.deiconify()
        self.top.lift()

    def refresh(self):
        if not self.alive:
            return
        node = self.node
        listening = {True: f'listening on port {node.port}', False: f'NOT listening: {node.listen_error}', None: 'starting...'}[node.listening]
        self.me.configure(text=f'This device: {node.name}\n{node.ip}  ·  {listening}', fg=RED if node.listening is False else DIM)
        peers = sorted(node.peers().values(), key=lambda p: (not p['paired'], p['name'].lower()))
        signature = [(p['id'], p['paired'], p['online'], p['side'], p['addr']) for p in peers]
        if signature != self.shown:
            self.shown = signature
            self.build(peers)
        self.top.after(1000, self.refresh)

    def build(self, peers):
        for child in self.body.winfo_children():
            child.destroy()
        paired = [p for p in peers if p['paired']]
        nearby = [p for p in peers if not p['paired']]
        tk.Label(self.body, text='PAIRED', bg=GLASS, fg=DIM, font=(FONT, 9, 'bold')).pack(anchor='w', pady=(6, 2))
        if not paired:
            tk.Label(self.body, text='None yet. Start Cameo on the other device, then pair it below.', bg=GLASS, fg=DIM,
                     font=(FONT, 10)).pack(anchor='w')
        for p in paired:
            row = tk.Frame(self.body, bg=GLASS)
            row.pack(fill='x', pady=2)
            colour = GREEN if p['online'] else DIM
            tk.Label(row, text=f"● {p['name']}", bg=GLASS, fg=colour, font=(FONT, 11), anchor='w', width=18).pack(side='left')
            tk.Label(row, text=p['addr'] or '', bg=GLASS, fg=DIM, font=(MONO, 9), width=15, anchor='w').pack(side='left')
            side = tk.StringVar(value=p['side'] or 'any side')
            menu = tk.OptionMenu(row, side, 'any side', 'left', 'right', command=lambda v, pid=p['id']: self.node.set_side(pid, '' if v == 'any side' else v))
            menu.configure(bg='#0F1B26', fg=TEXT, activebackground='#17324A', relief='flat', highlightthickness=0, font=(FONT, 9), width=7)
            menu.pack(side='left', padx=4)
            tk.Button(row, text='Forget', command=lambda pid=p['id']: self.node.forget(pid), **{**BUTTON, 'fg': RED}).pack(side='right')
        tk.Label(self.body, text='NEARBY', bg=GLASS, fg=DIM, font=(FONT, 9, 'bold')).pack(anchor='w', pady=(12, 2))
        if not nearby:
            tk.Label(self.body, text='No other Cameo seen on this network. You can pair by address.', bg=GLASS, fg=DIM,
                     font=(FONT, 10)).pack(anchor='w')
        for p in nearby:
            row = tk.Frame(self.body, bg=GLASS)
            row.pack(fill='x', pady=2)
            tk.Label(row, text=f"{p['name']}  {p['addr']}", bg=GLASS, fg=TEXT, font=(FONT, 11), anchor='w').pack(side='left')
            tk.Button(row, text='Pair', command=lambda p=p: self.pair(p['addr'], p.get('port') or PORT, p['name']), **BUTTON).pack(side='right')

    def pair(self, host, port, name=None):
        self.node.connect(host, int(port), allow_pair=True)
        self.status.set(f'Pairing with {name or host}: compare the 6-digit code on both devices and confirm on both.')

    def pair_address(self):
        text = self.address.get().strip()
        if not text:
            return
        host, _, port = text.partition(':')
        if port and not port.isdigit():
            self.status.set('Type an address like 192.168.1.20 or 192.168.1.20:47820')
            return
        self.pair(host, int(port or PORT))

    def show_result(self, ok, text):
        if self.alive:
            self.status.set(text)
