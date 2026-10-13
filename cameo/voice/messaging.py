"""Spoken messages: "go to teams and message Kavinesh, hi da how are you" -> who and what.

Only the Whisper transcript is used (the message is free speech, outside the closed grammar). The
result is a draft; nothing is sent until the user confirms with "send it" (see main.Runtime).
"""
import re

WAKE = r'^\s*(?:(?:hey|hi|ok)\s+)?(?:cameo|ether|aether)\b[\s,.!:-]*'
APP = r'(?:microsoft\s+)?teams'
VERB = r'(?:send\s+(?:a\s+)?(?:message|msg|text)|message|msg|text|ping|chat)'
LEAD = rf'(?:(?:go\s+to|open|in|on|using)\s+{APP}\s*(?:and|,|then)?\s*)?'
SEP = r'(?:\s*[,:.;-]\s*|\s+)(?:(?:saying|say|that|like|as)\b[\s,:]*)?'
NAME = r'(?P<to>[A-Za-z][\w.\'-]*)'

PATTERNS = [
    # English: "[go to teams and] message [to] Kavinesh[ on teams], hi da how are you"
    re.compile(rf'^{LEAD}{VERB}\s+(?:to\s+)?{NAME}(?:\s+(?:on|in)\s+{APP})?{SEP}(?P<body>.+)$', re.I),
    # Tanglish: "[teams la] Kavinesh ku message anuppu, hi da ..."
    re.compile(rf'^(?:{APP}\s+(?:la|le|il)\s+)?{NAME}\s*(?:ku|kku|kitta|kitte)\s+(?:{APP}\s+(?:la|le|il)\s+)?'
               rf'(?:message|msg)\s+(?:anuppu|anupu|pannu|podu|send\s+pannu|send)\b{SEP}(?P<body>.+)$', re.I),
]
NOT_NAMES = {'to', 'the', 'a', 'my', 'teams', 'microsoft', 'on', 'in', 'and'}


def parse(text):
    """-> {'app': 'teams', 'to': name, 'text': message} or None."""
    if not isinstance(text, str) or len(text) > 500:
        return None
    spoken = re.sub(WAKE, '', text.strip(), flags=re.I)
    for pattern in PATTERNS:
        found = pattern.match(spoken)
        if not found:
            continue
        to = found.group('to').strip(".,'-")
        body = found.group('body').strip(' ,:;-')
        body = re.sub(r'[.]$', '', body)  # Whisper adds a full stop; keep ? and !
        if to.lower() in NOT_NAMES or not body:
            return None
        return {'app': 'teams', 'to': to[:1].upper() + to[1:], 'text': body}
    return None


HEAD = re.compile(rf'^{LEAD}{VERB}\s+(?:to\s+)?{NAME}(?:\s+(?:on|in)\s+{APP})?[\s,.:;!-]*$', re.I)


def head(text):
    """"go to teams and message Kavinesh" said before a pause: the message itself follows."""
    if not isinstance(text, str):
        return False
    found = HEAD.match(re.sub(WAKE, '', text.strip(), flags=re.I))
    return bool(found) and found.group('to').lower() not in NOT_NAMES
