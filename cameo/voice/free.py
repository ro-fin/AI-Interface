"""Free-speech commands from the Whisper transcript (after the wake word): open any app or website,
play on YouTube, search. English first, then Tanglish forms. Deterministic patterns, no model.

  "open teams" / "launch visual studio code" / "teams open pannu"      -> app.open  {name}
  "play tamil kuthu song in youtube" / "youtube la kuthu song podu"     -> media.play {query}
  "search weather in chennai" / "search lofi songs on youtube"          -> web.search {query, site}
"""
import re

from .messaging import WAKE

END = r'[\s.!?,]*$'
PLAY = [
    re.compile(rf'^(?:play|put|start\s+playing)\s+(?:me\s+)?(?:a\s+|the\s+|some\s+)?(?P<q>.+?)(?:\s+(?:on|in|from|at)\s+you\s?tube)?{END}', re.I),
    re.compile(rf'^you\s?tube\s*(?:la|le|il|ile|la\s+oru)?\s+(?P<q>.+?)\s+(?:podu|poodu|play\s+pannu|pottu\s+vidu|potu|play){END}', re.I),
    re.compile(rf'^(?P<q>.+?\b(?:song|songs|paattu|pattu|paatu|video))\s+(?:podu|poodu|play\s+pannu|pottu\s+vidu|potu)(?:\s+you\s?tube\s*(?:la|le|il))?{END}', re.I),
]
SEARCH = [
    re.compile(rf'^(?:search|google|look\s+up|find)\s+(?:for\s+|about\s+)?(?P<q>.+?)(?:\s+(?:on|in)\s+(?P<site>google|you\s?tube))?{END}', re.I),
    re.compile(rf'^(?:(?P<site>google|you\s?tube)\s*(?:la|le|il)\s+)?(?P<q>.+?)\s+(?:search\s+pannu|thedu|theadu|search){END}', re.I),
]
OPEN = [
    re.compile(rf'^(?:open|launch|start|run|go\s+to)\s+(?:the\s+|my\s+)?(?P<name>.+?)(?:\s+(?:app|application))?{END}', re.I),
    re.compile(rf'^(?P<name>.+?)\s+(?:ah?\s+|ai\s+|ya\s+)?(?:open\s+pannu|open\s+panu|thira|thiraa|thirappu|thiranthu\s+vidu|launch\s+pannu|open\s+pannunga){END}', re.I),
]
# Left to the closed grammar (it already knows these: "open it", "open the browser", "new tab").
GRAMMAR_OWNED = {'it', 'this', 'that', 'this window', 'that window', 'browser', 'new tab', 'tab', 'the browser'}


def clean(text):
    return re.sub(WAKE, '', text.strip(), flags=re.I).strip(' ,.')


def parse(text):
    """-> {'action', 'target', 'args'} or None."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        return None
    spoken = clean(text)
    for pattern in PLAY:
        found = pattern.match(spoken)
        if found and found.group('q').strip():
            return {'action': 'media.play', 'target': None, 'args': {'query': found.group('q').strip(' ,.'), 'site': 'youtube'}}
    for pattern in SEARCH:
        found = pattern.match(spoken)
        if found and found.group('q').strip():
            site = (found.group('site') or 'google').lower().replace(' ', '')
            return {'action': 'web.search', 'target': None, 'args': {'query': found.group('q').strip(' ,.'), 'site': site}}
    for pattern in OPEN:
        found = pattern.match(spoken)
        if found:
            name = found.group('name').strip(' ,.').lower()
            if name and name not in GRAMMAR_OWNED and len(name) <= 60:
                return {'action': 'app.open', 'target': None, 'args': {'name': name}}
    return None
