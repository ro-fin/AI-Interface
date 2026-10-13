"""Web actions by voice: play on YouTube, search Google, open a website. Pages open in the browser that
has the Cameo Bridge (Edge, Chrome or Brave), else in the default browser. Verified by the browser
window title (Win32 / X11 read-back)."""
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser

from ..contracts import ActionResult, failure
from . import browsers, system

SITES = {
    'youtube': 'https://www.youtube.com/', 'google': 'https://www.google.com/', 'gmail': 'https://mail.google.com/',
    'whatsapp': 'https://web.whatsapp.com/', 'chatgpt': 'https://chatgpt.com/', 'github': 'https://github.com/',
    'instagram': 'https://www.instagram.com/', 'facebook': 'https://www.facebook.com/', 'linkedin': 'https://www.linkedin.com/',
    'maps': 'https://maps.google.com/', 'google maps': 'https://maps.google.com/', 'netflix': 'https://www.netflix.com/',
    'amazon': 'https://www.amazon.in/', 'flipkart': 'https://www.flipkart.com/', 'spotify': 'https://open.spotify.com/',
    'wikipedia': 'https://www.wikipedia.org/', 'outlook': 'https://outlook.live.com/', 'drive': 'https://drive.google.com/',
    'google drive': 'https://drive.google.com/', 'translate': 'https://translate.google.com/', 'claude': 'https://claude.ai/',
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0'


def first_video(query, timeout=6):
    """The top YouTube result for a search (its video id), or None when YouTube cannot be reached."""
    url = 'https://www.youtube.com/results?' + urllib.parse.urlencode({'search_query': query})
    request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-IN,en;q=0.9,ta;q=0.8'})
    try:
        page = urllib.request.urlopen(request, timeout=timeout).read().decode('utf-8', 'ignore')
    except Exception:
        return None
    # Search results carry "videoRenderer":{"videoId":"..."}; ads and shorts use other renderers.
    found = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page) or re.search(r'"videoId":"([\w-]{11})"', page)
    return found.group(1) if found else None


class Web:
    def __init__(self, registry, find=browsers.find):
        self.registry, self.find = registry, find

    def open(self, url):
        exe, profile = self.find()
        if exe is None:
            if not webbrowser.open(url):
                raise RuntimeError('No web browser is installed')
            return
        subprocess.Popen(browsers.command(exe, profile, url))

    def wait_title(self, intent, words, evidence, started, wait=15):
        """Verified when a browser window shows a title with one of `words` (the page really loaded)."""
        end = time.monotonic() + wait
        while time.monotonic() < end:
            time.sleep(.5)
            for t in self.registry.list():
                if t.kind == 'browser_window' and any(w.lower() in t.title.lower() for w in words):
                    return ActionResult(intent.intent_id, 'verified', {**evidence, 'source': system.SOURCE, 'title': t.title[:100]},
                                        elapsed_ms=int((time.monotonic() - started) * 1000))
        return ActionResult(intent.intent_id, 'sent', {**evidence, 'source': 'shell', 'note': 'page opened; title not seen yet'},
                            elapsed_ms=int((time.monotonic() - started) * 1000))

    def act(self, intent):
        started = time.monotonic()
        a = intent.args
        if intent.action == 'media.play':
            query = a.get('query', '').strip()
            if not query:
                return failure(intent.intent_id, 'NOT_A_COMMAND', 'Say what to play')
            video = first_video(query)
            if video:
                self.open(f'https://www.youtube.com/watch?v={video}')
                return self.wait_title(intent, ['YouTube'], {'query': query, 'video': video}, started)
            self.open('https://www.youtube.com/results?' + urllib.parse.urlencode({'search_query': query}))
            return self.wait_title(intent, ['YouTube'], {'query': query, 'video': None, 'note': 'showing search results'}, started)
        if intent.action == 'web.search':
            query = a.get('query', '').strip()
            site = a.get('site') or 'google'
            if site == 'youtube':
                url = 'https://www.youtube.com/results?' + urllib.parse.urlencode({'search_query': query})
            else:
                url = 'https://www.google.com/search?' + urllib.parse.urlencode({'q': query})
            self.open(url)
            return self.wait_title(intent, [query.split()[0] if query else site, 'Google', 'YouTube'], {'query': query, 'site': site}, started)
        if intent.action == 'web.open':
            site = a.get('site', '').lower().strip()
            url = SITES.get(site) or (f'https://{site}' if re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?', site) else None)
            if not url:
                return failure(intent.intent_id, 'TARGET_GONE', f'Unknown website "{site}"')
            self.open(url)
            return self.wait_title(intent, [site.split('.')[0]], {'site': site, 'url': url}, started)
        return failure(intent.intent_id, 'INTERNAL', f'{intent.action} is not a web action')
