"""Closed voice grammar (ARCHITECTURE.md §4.4) for the Vosk command recognizer.

Utterances may chain a wake word and one command: "hey cameo new tab". Target words become a
target spec that the intent pipeline resolves: 'deictic' (this/that/it), 'browser', 'window', or None.
"""
import itertools

WAKE = ('hey cameo', 'cameo', 'hey ether', 'ether')

# (phrase alternatives, action, target spec). "(a|b)" alternates, "[x]" is optional.
RULES = [
    ('open [the] browser', 'browser.focus_or_open', 'browser'),
    ('focus [the] browser', 'browser.focus_or_open', 'browser'),
    ('new tab', 'tab.new', None),
    ('next tab', 'tab.next', None),
    ('previous tab', 'tab.prev', None),
    ('close [this] tab', 'tab.close', None),
    ('maximize (this|that|it) [window]', 'window.maximize', 'deictic'),
    ('maximize [the] window', 'window.maximize', 'deictic'),
    ('maximize [the] browser', 'window.maximize', 'browser'),
    ('minimize (this|that|it) [window]', 'window.minimize', 'deictic'),
    ('minimize [the] window', 'window.minimize', 'deictic'),
    ('minimize [the] browser', 'window.minimize', 'browser'),
    ('restore (that|it) [window]', 'window.restore', 'minimized'),
    ('restore [the] window', 'window.restore', 'minimized'),
    ('restore this [window]', 'window.restore', 'deictic'),
    ('restore [the] browser', 'window.restore', 'browser'),
    ('(open|focus) (this|that|it)', 'window.activate', 'deictic'),
    ('pause controls', 'control.pause', None),
    ('resume controls', 'control.resume', None),
    ('cancel', 'control.cancel', None),
    ('do it', 'control.confirm', None),
    ('send it', 'control.confirm', None),
    ('send [the] message', 'control.confirm', None),
]


def expand(pattern):
    """'maximize [the] (window|browser)' -> every concrete phrase."""
    options = []
    for part in pattern.replace('[', ' [').replace(']', '] ').replace('(', ' (').replace(')', ') ').split():
        if part.startswith('['):
            options.append(['', part[1:-1]])
        elif part.startswith('('):
            options.append(part[1:-1].split('|'))
        else:
            options.append([part])
    return [' '.join(word for word in combo if word) for combo in itertools.product(*options)]


COMMANDS = {}
for pattern, action, target in RULES:
    for phrase in expand(pattern):
        COMMANDS.setdefault(phrase, (action, target))


def vosk_grammar():
    return sorted(set(WAKE) | set(COMMANDS)) + ['[unk]']


def parse(text):
    """-> (woke, command or None, unknown). command = {'phrase', 'action', 'target'}."""
    words = text.split()
    woke = False
    for wake in sorted(WAKE, key=len, reverse=True):
        if text == wake or text.startswith(wake + ' '):
            woke, words = True, words[len(wake.split()):]
            break
    rest = ' '.join(words)
    unknown = '[unk]' in words
    if rest in COMMANDS:
        action, target = COMMANDS[rest]
        return woke, {'phrase': rest, 'action': action, 'target': target}, False
    return woke, None, unknown or bool(rest)


DEICTIC = {'this', 'that', 'it'}
STOP_WORDS = {'the', 'this', 'that', 'it', 'window', 'hey', 'cameo', 'ether', 'a'}


def corroborated(phrase, free_text):
    """The free (ungrammared) transcript must contain the command's key words, so a grammar match
    that was forced from unrelated speech (e.g. Tanglish) is not executed."""
    heard = set(free_text.split())
    if set(phrase.split()) & DEICTIC and not heard & DEICTIC:
        return False  # "open it" forced from "open teams": let the full transcript decide
    key = [word for word in phrase.split() if word not in STOP_WORDS]
    if not key:
        return True
    close = lambda word: word in heard or any(len(word) > 4 and (h.startswith(word[:4]) or word.startswith(h[:4])) for h in heard if len(h) > 3)
    return sum(close(word) for word in key) * 2 >= len(key) + (1 if len(key) > 1 else 0)
