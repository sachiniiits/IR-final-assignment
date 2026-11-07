"""
search.py -- query processing on top of the on-disk positional index.

    idx = PositionalIndex("index_dir")       # loads ONLY the vocabulary into RAM
    idx.term("भारत")                          # df, cf and the positional postings
    idx.phrase("भारत की राजधानी")            # exact phrase via positional intersection
    idx.near("गंगा", "यमुना", k=5)          # proximity  (|p1 - p2| <= k)
    idx.wildcard("हिमाल*")                   # prefix search on the sorted lexicon
    idx.ranked("ताजमहल आगरा")                # BM25 + term-proximity re-ranking

Phrase / proximity evaluation
  * query terms are ordered by df; the rarest list drives a "leap-frog"
    intersection, the other cursors jump with ``advance()`` using the skip
    pointers stored in each postings list - most documents are never decoded;
  * positions are only decoded for documents that contain *all* terms
    (thanks to the per-document ``plen`` field);
  * query offsets come from the same Analyzer used at index time, so
    removed stop-words leave the correct gap.

Ranking: Okapi BM25 (k1=1.2, b=0.75) on the top candidates, re-ranked with
the MinDist proximity factor of Tao & Zhai (SIGIR 2007):
    score += log(alpha + exp(-MinDist))  where MinDist is the smallest
    distance between two different query terms in the document.
"""

from __future__ import annotations

import heapq
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .index_format import (DocStore, DocTable, Lexicon, PostingCursor,
                           PostingsFile, TermInfo, load_meta)
from .textproc import Analyzer, AnalyzerConfig


@dataclass
class Hit:
    docid: int
    score: float = 0.0
    positions: List[int] = field(default_factory=list)   # match positions


class PositionalIndex:
    def __init__(self, index_dir: str, load_docs: bool = True):
        self.dir = index_dir
        t = time.time()
        self.meta = load_meta(index_dir)
        self.analyzer = Analyzer(AnalyzerConfig.from_dict(self.meta["analyzer"]))
        self.lexicon = Lexicon(os.path.join(index_dir, "lexicon"))   # in RAM
        self.postings = PostingsFile(os.path.join(index_dir, "postings.bin"))  # on disk (mmap)
        self.docstore = DocStore(os.path.join(index_dir, "docstore"))
        self._docs: Optional[DocTable] = None
        if load_docs:
            _ = self.docs
        self.load_seconds = time.time() - t
        self.stats = {"skips": 0, "docs_decoded": 0}

    # ------------------------------------------------------------------ util
    @property
    def docs(self) -> DocTable:
        if self._docs is None:
            self._docs = DocTable(os.path.join(self.dir, "docs.tsv"))
        return self._docs

    @property
    def n_docs(self) -> int:
        return self.meta["n_docs"]

    def norm(self, word: str) -> str:
        return self.analyzer.normalize_term(word)

    def info(self, term: str) -> Optional[TermInfo]:
        return self.lexicon.get(term)

    def plist(self, term: str):
        info = self.lexicon.get(term)
        return self.postings.get(info) if info else None

    # ------------------------------------------------------------ term lookup
    def term(self, word: str, normalize: bool = True):
        """Return (term, TermInfo, [(docID, [positions])]) or None."""
        t = self.norm(word) if normalize else word
        info = self.lexicon.get(t)
        if info is None:
            return None
        return t, info, self.postings.get(info).decode_all()

    # ---------------------------------------------------- core intersection
    def _intersect(self, items: Sequence[Tuple[str, int]], limit: int = 0,
                   window: Optional[int] = None, ordered: bool = True):
        """items = [(term, query_offset)].  If ``window`` is None this is an
        exact phrase match (term i must be at start + offset_i); otherwise a
        proximity match where all terms must fall in a span <= window.
        Yields (docid, [match start positions])."""
        lists = []
        for term, off in items:
            pl = self.plist(term)
            if pl is None:
                return
            lists.append((pl.df, off, pl))
        lists.sort(key=lambda x: x[0])           # rarest first
        cursors = [pl.cursor() for _df, _o, pl in lists]
        offs = [o for _df, o, _pl in lists]
        found = 0
        lead = cursors[0]
        while lead.doc is not None:
            target = lead.doc
            ok = True
            for c in cursors[1:]:
                if not c.advance(target):
                    self._collect(cursors)
                    return
                if c.doc != target:
                    ok = False
                    if c.doc > target and not lead.advance(c.doc):
                        self._collect(cursors)
                        return
                    break
            if not ok:
                continue
            self.stats["docs_decoded"] += 1
            pos_lists = [c.positions() for c in cursors]
            if window is None:
                matches = _phrase_match(pos_lists, offs)
            else:
                matches = _window_match(pos_lists, window, ordered, offs)
            if matches:
                yield target, matches
                found += 1
                if limit and found >= limit:
                    break
            lead.next()
        self._collect(cursors)

    def _collect(self, cursors: List[PostingCursor]):
        self.stats["skips"] += sum(c.skips_used for c in cursors)

    # -------------------------------------------------------------- phrase
    def _query_items(self, text: str) -> List[Tuple[str, int]]:
        return list((t, p) for p, t in self.analyzer.analyze(text))

    def phrase(self, text: str, limit: int = 0) -> List[Hit]:
        items = self._query_items(text)
        if not items:
            return []
        base = items[0][1]
        items = [(t, p - base) for t, p in items]
        if len(items) == 1:
            r = self.term(items[0][0], normalize=False)
            return [Hit(d, 0.0, pos) for d, pos in r[2]][: limit or None] if r else []
        return [Hit(d, 0.0, m) for d, m in self._intersect(items, limit)]

    # ----------------------------------------------------------- proximity
    def near(self, w1: str, w2: str, k: int = 5, ordered: bool = False,
             limit: int = 0) -> List[Hit]:
        t1, t2 = self.norm(w1), self.norm(w2)
        if not t1 or not t2:
            return []
        return [Hit(d, 0.0, m) for d, m in
                self._intersect([(t1, 0), (t2, 1)], limit, window=k, ordered=ordered)]

    # ------------------------------------------------------------ wildcard
    def wildcard(self, pattern: str, limit: int = 50) -> List[Tuple[str, TermInfo]]:
        prefix = pattern.rstrip("*")
        prefix = self.analyzer.normalize(prefix).lower() if prefix.isascii() else \
            self.analyzer.normalize(prefix)
        res = list(self.lexicon.prefix(prefix))
        res.sort(key=lambda x: -x[1].cf)
        return res[:limit]

    # ------------------------------------------------------- boolean AND
    def conjunctive(self, words: Sequence[str]) -> List[int]:
        terms = [self.norm(w) for w in words]
        lists = [self.plist(t) for t in terms if t]
        if not lists or any(l is None for l in lists):
            return []
        lists.sort(key=lambda pl: pl.df)
        cur = [pl.cursor() for pl in lists]
        out = []
        lead = cur[0]
        while lead.doc is not None:
            t = lead.doc
            agree = True
            for c in cur[1:]:
                if not c.advance(t):
                    self._collect(cur)
                    return out
                if c.doc != t:
                    agree = False
                    lead.advance(c.doc)
                    break
            if agree:
                out.append(t)
                lead.next()
        self._collect(cur)
        return out

    # ------------------------------------------------------------- ranked
    def ranked(self, query: str, k: int = 10, k1: float = 1.2, b: float = 0.75,
               rerank: int = 100, alpha: float = 0.3) -> List[Hit]:
        terms = []
        for _p, t in self.analyzer.analyze(query):
            if t not in terms:
                terms.append(t)
        N = self.n_docs
        avgdl = self.meta["avg_doc_len"] or 1.0
        lengths = self.docs.lengths
        scores: Dict[int, float] = {}
        plists = {}
        for t in terms:
            pl = self.plist(t)
            if pl is None:
                continue
            plists[t] = pl
            idf = math.log(1 + (N - pl.df + 0.5) / (pl.df + 0.5))
            for d, tf, _s, _e in pl:          # positions are NOT decoded here
                dl = lengths[d]
                s = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
                scores[d] = scores.get(d, 0.0) + s
        top = heapq.nlargest(max(k, rerank), scores.items(), key=lambda x: x[1])
        hits = []
        if len(plists) >= 2:
            # proximity re-ranking: decode positions only for the candidates
            cand = {d for d, _ in top}
            pos_by_doc: Dict[int, List[Tuple[int, int]]] = {d: [] for d in cand}
            for ti, (t, pl) in enumerate(plists.items()):
                cur = pl.cursor()
                for d in sorted(cand):
                    if cur.advance(d) and cur.doc == d:
                        pos_by_doc[d].extend((p, ti) for p in cur.positions())
                self.stats["skips"] += cur.skips_used
            for d, s in top:
                md = _min_dist(pos_by_doc[d])
                prox = math.log(alpha + math.exp(-md)) if md is not None else math.log(alpha)
                hits.append(Hit(d, s + prox - math.log(alpha),
                                sorted(p for p, _ in pos_by_doc[d])))
        else:
            for d, s in top:
                hits.append(Hit(d, s, []))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    # -------------------------------------------------------------- KWIC
    def kwic(self, docid: int, positions: Sequence[int], width: int = 6,
             mark: Tuple[str, str] = ("[", "]"), max_snippets: int = 2,
             span: int = 1) -> List[str]:
        """Key-Word-In-Context snippets reconstructed from the docstore by
        re-tokenising the document and mapping token positions -> chars."""
        text = self.docstore.text(docid)
        if not text or not positions:
            return []
        spans = [(s, e) for _p, s, e in self.analyzer.spans(text)]
        out = []
        for p in list(positions)[:max_snippets]:
            if p >= len(spans):
                continue
            lo = spans[max(0, p - width)][0]
            hi = spans[min(len(spans) - 1, p + width + span - 1)][1]
            a = spans[p][0]
            z = spans[min(len(spans) - 1, p + span - 1)][1]
            snip = (text[lo:a] + mark[0] + text[a:z] + mark[1] + text[z:hi])
            out.append(("…" if lo > 0 else "") + " ".join(snip.split()) +
                       ("…" if hi < len(text) else ""))
        return out

    def surface(self, docid: int, position: int) -> str:
        """The exact token of the stored document at ``position``."""
        text = self.docstore.text(docid)
        for p, s, e in self.analyzer.spans(text):
            if p == position:
                return text[s:e]
        return ""

    def close(self):
        self.postings.close()
        self.docstore.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# positional helpers
# ---------------------------------------------------------------------------
def _phrase_match(pos_lists: List[List[int]], offs: List[int]) -> List[int]:
    """Start positions s such that every term i occurs at s + offs[i]."""
    base = min(offs)
    cands = None
    for pos, o in zip(pos_lists, offs):
        shifted = {p - o + base for p in pos}
        cands = shifted if cands is None else cands & shifted
        if not cands:
            return []
    return sorted(cands)


def _window_match(pos_lists, window: int, ordered: bool, offs) -> List[int]:
    """Two-term proximity: positions of term A having term B within ``window``
    tokens (after B if ``ordered``).  Classic linear merge."""
    # restore the original query order (lists were re-ordered by df)
    order = sorted(range(len(offs)), key=lambda i: offs[i])
    a, bl = pos_lists[order[0]], pos_lists[order[1]]
    out = []
    j = 0
    for p in a:
        while j < len(bl) and bl[j] < p - (0 if ordered else window):
            j += 1
        k = j
        while k < len(bl) and bl[k] <= p + window:
            if bl[k] != p and (not ordered or bl[k] > p):
                out.append(p)
                break
            k += 1
    return out


def _min_dist(tagged: List[Tuple[int, int]]) -> Optional[int]:
    """Smallest distance between occurrences of two *different* terms."""
    if not tagged:
        return None
    tagged.sort()
    best = None
    for (p1, t1), (p2, t2) in zip(tagged, tagged[1:]):
        if t1 != t2:
            d = p2 - p1
            if best is None or d < best:
                best = d
    return best
