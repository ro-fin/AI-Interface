"""Tanglish / Tamil / English matching for Whisper fallback transcripts (ported from public/intents.js).

Tamil script is transliterated, words are compared by a spelling-insensitive key, and Tamil case
endings (-ai, -ku, -ah) are tolerated. Only the AETHER actions below can be selected.
"""
import re

VOWELS = {'அ': 'a', 'ஆ': 'aa', 'இ': 'i', 'ஈ': 'ii', 'உ': 'u', 'ஊ': 'uu', 'எ': 'e', 'ஏ': 'ee', 'ஐ': 'ai', 'ஒ': 'o', 'ஓ': 'oo', 'ஔ': 'au'}
CONSONANTS = {'க': 'k', 'ங': 'ng', 'ச': 's', 'ஞ': 'nj', 'ட': 't', 'ண': 'n', 'த': 'th', 'ந': 'n', 'ப': 'p', 'ம': 'm', 'ய': 'y', 'ர': 'r',
              'ல': 'l', 'வ': 'v', 'ழ': 'zh', 'ள': 'l', 'ற': 'r', 'ன': 'n', 'ஜ': 'j', 'ஷ': 'sh', 'ஸ': 's', 'ஹ': 'h'}
SIGNS = {'ா': 'aa', 'ி': 'i', 'ீ': 'ii', 'ு': 'u', 'ூ': 'uu', 'ெ': 'e', 'ே': 'ee', 'ை': 'ai', 'ொ': 'o', 'ோ': 'oo', 'ௌ': 'au'}
PULLI = '்'


def transliterate(text):
    out, chars, i = [], list(text), 0
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ''
        if c in CONSONANTS:
            if nxt == PULLI:
                out.append(CONSONANTS[c]); i += 1
            elif nxt in SIGNS:
                out.append(CONSONANTS[c] + SIGNS[nxt]); i += 1
            else:
                out.append(CONSONANTS[c] + 'a')
        else:
            out.append(VOWELS.get(c, 'h' if c == 'ஃ' else c))
        i += 1
    return ''.join(out)


def phonetic(word):
    w = word.lower().replace('zh', 'l')
    w = re.sub(r'ch|sh|j|z', 's', w).replace('h', '').replace('ck', 'k')
    w = re.sub(r'[cqg]', 'k', w).replace('x', 'ks').replace('d', 't').replace('b', 'p').replace('f', 'p').replace('w', 'v')
    w = w.replace('ee', 'i').replace('oo', 'u')
    return re.sub(r'(.)\1+', r'\1', w)


def tokenize(text):
    text = re.sub(r"['’]", '', transliterate(text).lower())
    return [phonetic(t) for t in re.sub(r'[^a-z0-9]+', ' ', text).split()]


FILLER = {phonetic(w) for w in ('hey ok okay cameo camio kameo ether please pls pannu pannunga panni konjam the a an to my this that it is '
                                'ah aa ai ku ukku le la da di ma pa bro now ippo seekiram can you could will for me enaku um uh just po poo va vaa podu kuda').split()}
SUFFIXES = [phonetic(s) for s in ('a', 'ai', 'ku', 'uku', 'ke', 'le', 'la', 'il', 'ile', 'ya', 'yai', 'ah', 'u')]


def distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    row = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        nxt = [i]
        for j in range(1, len(b) + 1):
            nxt.append(min(row[j] + 1, nxt[j - 1] + 1, row[j - 1] + (a[i - 1] != b[j - 1])))
        row = nxt
    return row[len(b)]


def word_matches(token, word):
    """2 = exact (or exact stem), 1 = tolerated spelling/recognition error, 0 = no match."""
    key, prefix = word
    if prefix:
        return 2 if token.startswith(key) else 1 if len(key) >= 5 and distance(token[:len(key)], key) <= 1 else 0
    if token == key or (token.startswith(key) and token[len(key):] in SUFFIXES):
        return 2
    allowed = 2 if len(key) >= 10 else 1 if len(key) >= 5 else 0
    return 1 if allowed and distance(token, key) <= allowed else 0


def compile_phrase(phrase):
    return [(phonetic(transliterate(part.rstrip('*'))), part.endswith('*')) for part in phrase.split()]


OPEN = ['open', 'launch', 'start', 'thira*', 'kaatu*', 'show']
TAB = ['tab', 'teep', 'tep', 'taab']  # English 'tab' written in Tamil script reads 'teep'
WINDOW = ['window', 'app', 'screen', 'this', 'that', 'saalara*']
DEFINITIONS = [
    ('tab.new', None, [['new', 'pudhu*', 'puthu*'], TAB], [OPEN]),
    ('tab.next', None, [['next', 'adutha*', 'aduththa*'], TAB], []),
    ('tab.prev', None, [['previous', 'prev', 'munthaya*', 'munthai*', 'back'], TAB], []),
    ('tab.close', None, [['close', 'mooda*', 'moodu*', 'mudu*'], TAB], []),
    ('browser.focus_or_open', 'browser', [['browser', 'edge']], [OPEN]),
    ('window.maximize', 'deictic', [['maximize', 'maximise', 'maximiz*', 'maximis*', 'full screen', 'fullscreen', 'periyatha*']], [WINDOW]),
    ('window.minimize', 'deictic', [['minimize', 'minimise', 'minimiz*', 'minimis*', 'chinnatha*']], [WINDOW]),
    ('window.restore', 'minimized', [['restore', 'palaiya*', 'pazhaya*']], [WINDOW]),
    ('control.pause', None, [['pause', 'stop', 'niruthu*', 'nirutha*', 'pothum', 'podhum']], [['controls', 'control']]),
    ('control.resume', None, [['resume', 'continue', 'thodar*', 'thodaru*']], [['controls', 'control']]),
    ('control.cancel', None, [['cancel', 'vendam', 'venda']], []),
]
INTENTS = [(action, target, [[compile_phrase(p) for p in g] for g in req], [[compile_phrase(p) for p in g] for g in opt])
           for action, target, req, opt in DEFINITIONS]


def find(tokens, used, group):
    best = None
    for phrase in group:
        for start in range(len(tokens) - len(phrase) + 1):
            quality = 0
            for offset, word in enumerate(phrase):
                q = 0 if start + offset in used else word_matches(tokens[start + offset], word)
                if not q:
                    quality = 0
                    break
                quality += q
            if quality and (best is None or quality > best[0]):
                best = (quality, range(start, start + len(phrase)))
    return best


def match(text):
    """-> {'action', 'target'} or None. At most one unexplained meaningful word is tolerated."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        return None
    tokens = tokenize(text)
    if not tokens or len(tokens) > 12:
        return None
    meaningful = [i for i, t in enumerate(tokens) if t not in FILLER]
    best = None
    for action, target, required, optional in INTENTS:
        used = {i for i, t in enumerate(tokens) if t in FILLER}
        matched, ok = 0, True
        for group in required:
            found = find(tokens, used, group)
            if not found:
                ok = False
                break
            used.update(found[1])
            matched += found[0]
        if not ok:
            continue
        for group in optional:
            while (found := find(tokens, used, group)):
                used.update(found[1])
                matched += found[0]
        leftover = sum(1 for i in meaningful if i not in used)
        if leftover > 1:
            continue
        score = matched * 5 - leftover * 3 + len(required)
        if best is None or score > best[0]:
            best = (score, action, target)
    return {'action': best[1], 'target': best[2]} if best else None
