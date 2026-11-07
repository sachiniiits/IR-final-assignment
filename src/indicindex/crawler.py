"""
crawler.py -- a polite, *language-focused* web crawler for building an
Indian-language collection from the WWW.

    python -m indicindex crawl https://hi.wikipedia.org/wiki/भारत \
           -o crawl.jsonl --max-pages 500 --script devanagari

Features
  * robots.txt compliance (urllib.robotparser) and a per-host politeness delay
  * script-ratio language identification: a page is kept only if at least
    ``min_ratio`` of its letters belong to the target script (Devanagari,
    Bengali, Tamil, ...).  Cheap, robust and needs no language model.
  * focused, best-first frontier: links are prioritised by the script ratio
    of the page they were found on, so the crawler drifts *towards* pages in
    the target language
  * duplicate removal: exact (SHA-1 of normalised text) and near-duplicate
    detection with 64-bit SimHash (Charikar 2002) over word 3-shingles, using
    4 x 16-bit LSH bands so each new page is compared only with candidates
    sharing a band (Manku et al., WWW 2007); Hamming distance <= 3 = duplicate
  * boilerplate stripping: script/style/nav/header/footer/aside are ignored
  * output: JSONL (id, url, title, text) -> directly indexable
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set, Tuple

from .textproc import SCRIPTS, display_normalize

USER_AGENT = "IndicIndexCrawler/1.0 (academic IR assignment; polite crawler)"

SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|gz|mp3|mp4|ogg|ogv|webm|css|js|ico|xml|json)$", re.I)
# Wikipedia/MediaWiki pages that are not articles (any language): Special:, File:, ...
WIKI_NONARTICLE = re.compile(r"/wiki/[^/]*:|[?&](action|oldid|diff|printable|curid|veaction)=", re.I)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside",
            "form", "button", "svg", "sup", "table"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.links: List[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = re.sub(r"[ \t ]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n", t).strip()


def script_ratio(text: str, script: str) -> float:
    lo, hi = next((l, h) for n, l, h, _d in SCRIPTS if n == script)
    letters = target = 0
    for ch in text:
        if ch.isalpha() or "ऀ" <= ch <= "෿":
            letters += 1
            if lo <= ord(ch) <= hi:
                target += 1
    return target / letters if letters else 0.0


# ---------------------------------------------------------------------------
# SimHash near-duplicate detector
# ---------------------------------------------------------------------------
def simhash(text: str, k: int = 3) -> int:
    words = text.split()
    v = [0] * 64
    shingles = [" ".join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))]
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


class NearDupIndex:
    def __init__(self, max_dist: int = 3):
        self.max_dist = max_dist
        self.bands: List[Dict[int, List[int]]] = [{} for _ in range(4)]

    def add_if_new(self, h: int) -> bool:
        cands: Set[int] = set()
        for b in range(4):
            key = (h >> (16 * b)) & 0xFFFF
            cands.update(self.bands[b].get(key, ()))
        for c in cands:
            if bin(c ^ h).count("1") <= self.max_dist:
                return False
        for b in range(4):
            self.bands[b].setdefault((h >> (16 * b)) & 0xFFFF, []).append(h)
        return True


# ---------------------------------------------------------------------------
class Crawler:
    def __init__(self, seeds: List[str], out_path: str, max_pages: int = 200,
                 script: str = "devanagari", min_ratio: float = 0.5,
                 delay: float = 1.0, allowed_domains: Optional[List[str]] = None,
                 min_chars: int = 300, timeout: float = 15.0, log=print):
        self.seeds = seeds
        self.out_path = out_path
        self.max_pages = max_pages
        self.script = script
        self.min_ratio = min_ratio
        self.delay = delay
        self.allowed = set(allowed_domains or [urllib.parse.urlsplit(s).hostname for s in seeds])
        self.min_chars = min_chars
        self.timeout = timeout
        self.log = log
        self.robots: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_hit: Dict[str, float] = {}
        self.seen: Set[str] = set()
        self.hashes: Set[str] = set()
        self.neardup = NearDupIndex()
        self.stats = {"fetched": 0, "kept": 0, "lang_rejected": 0, "dup_exact": 0,
                      "dup_near": 0, "robots_blocked": 0, "errors": 0, "too_short": 0}

    @staticmethod
    def normalize_url(url: str) -> str:
        url, _frag = urllib.parse.urldefrag(url)
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:%()_-.,~!$&'*+;=@")
        return urllib.parse.urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower(),
                                        path, parts.query, ""))

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self.robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    rp.parse(r.read().decode("utf-8", errors="ignore").splitlines())
            except Exception:
                rp.parse([])  # no robots.txt -> everything allowed
            self.robots[base] = rp
        return rp.can_fetch(USER_AGENT, url)

    def _polite_wait(self, host: str):
        last = self.last_hit.get(host, 0.0)
        wait = self.delay - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self.last_hit[host] = time.time()

    def _fetch(self, url: str) -> Optional[str]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if "html" not in (r.headers.get("Content-Type") or ""):
                return None
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read(5_000_000).decode(charset, errors="replace")

    def _accept_link(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or p.hostname not in self.allowed:
            return False
        if SKIP_EXT.search(p.path) or WIKI_NONARTICLE.search(urllib.parse.unquote(url)):
            return False
        return True

    def run(self) -> dict:
        frontier: List[Tuple[float, int, str]] = []
        counter = 0
        for s in self.seeds:
            u = self.normalize_url(s)
            heapq.heappush(frontier, (-1.0, counter, u))
            self.seen.add(u)
            counter += 1
        with open(self.out_path, "w", encoding="utf-8") as out:
            while frontier and self.stats["kept"] < self.max_pages:
                _prio, _c, url = heapq.heappop(frontier)
                if not self._allowed_by_robots(url):
                    self.stats["robots_blocked"] += 1
                    continue
                host = urllib.parse.urlsplit(url).hostname
                self._polite_wait(host)
                try:
                    html = self._fetch(url)
                except Exception as e:  # network errors, 404, ...
                    self.stats["errors"] += 1
                    self.log(f"  ! {url}: {e}")
                    continue
                self.stats["fetched"] += 1
                if not html:
                    continue
                ex = _TextExtractor()
                try:
                    ex.feed(html)
                except Exception:
                    self.stats["errors"] += 1
                    continue
                text = display_normalize(ex.text())
                ratio = script_ratio(text, self.script)
                # enqueue links (even from rejected pages, at lower priority)
                for href in ex.links:
                    nu = self.normalize_url(urllib.parse.urljoin(url, href))
                    if nu not in self.seen and self._accept_link(nu):
                        self.seen.add(nu)
                        heapq.heappush(frontier, (-ratio, counter, nu))
                        counter += 1
                if len(text) < self.min_chars:
                    self.stats["too_short"] += 1
                    continue
                if ratio < self.min_ratio:
                    self.stats["lang_rejected"] += 1
                    continue
                digest = hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()
                if digest in self.hashes:
                    self.stats["dup_exact"] += 1
                    continue
                self.hashes.add(digest)
                if not self.neardup.add_if_new(simhash(text)):
                    self.stats["dup_near"] += 1
                    continue
                self.stats["kept"] += 1
                title = ex.title.strip()
                out.write(json.dumps({"id": f"web:{self.stats['kept']}", "url": url,
                                      "title": title, "text": text,
                                      "script_ratio": round(ratio, 3)},
                                     ensure_ascii=False) + "\n")
                out.flush()
                self.log(f"  [{self.stats['kept']:4d}] ratio={ratio:.2f} {title[:60]}  <{urllib.parse.unquote(url)[:80]}>")
        self.log(f"[crawl] {self.stats}")
        return self.stats
