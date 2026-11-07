"""
index_format.py -- on-disk layout of the positional index and its readers.

Files in an index directory
---------------------------
  postings.bin   all postings lists, one contiguous entry per term, in term order
  lexicon.bin    the vocabulary: front-coded blocks of K terms   (kept IN RAM)
  lexicon.blk    uint64 byte offset of every lexicon block       (kept IN RAM)
  docs.tsv       docID -> source id, length, url, title
  docstore.bin   zlib-compressed display text of each document  (KWIC, verify)
  docstore.idx   uint64 offsets into docstore.bin (N+1 entries)
  meta.json      analyzer configuration, collection statistics, build info

Postings entry of one term  (all integers VByte coded)
------------------------------------------------------
  df  cf  nskip
  nskip x ( Δ prev_docID , Δ byte_offset )            <- skip table
  df x  ( d-gap  tf  plen  [tf position-gaps: plen bytes] )

  * skip pointers are laid every ceil(sqrt(df)) documents for df >= SKIP_MIN
    and record (docID of the document just before the target, byte offset
    of the target relative to the start of the data section);
  * ``plen`` -- the byte length of the position list -- lets a reader hop
    over a document's positions without decoding them (used by the merge,
    by AND/phrase intersection and by df/tf-only scans such as BM25).

Lexicon (vocabulary) -- "dictionary as a string" with blocking + front coding
-------------------------------------------------------------------------------
  Terms are sorted by code point (== UTF-8 byte order).  Every block of K=16
  terms stores the first term in full and the rest as
  (shared-prefix length, suffix).  Per term:  df, cf, length (and, for the
  block head only, the absolute postings offset - later offsets are implied
  because entries are contiguous).  At load time only the K-th "head" terms
  are materialised as Python strings for binary search; a lookup decodes at
  most one 16-term block.  Hindi morphology (shared stems such as भारत,
  भारतीय, भारतीयों) makes front coding very effective.
"""

from __future__ import annotations

import bisect
import json
import math
import mmap
import os
import zlib
from array import array
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

from .codec import vbyte_decode_one, vbyte_decode_n, vbyte_encode_int

SKIP_MIN = 32
LEX_BLOCK = 16


def skip_step(df: int) -> int:
    return max(1, math.isqrt(df - 1) + 1) if df >= SKIP_MIN else 0


class TermInfo(NamedTuple):
    df: int
    cf: int
    offset: int
    length: int


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def build_entry(df: int, cf: int, data: bytes) -> bytes:
    """Prefix postings ``data`` with df, cf and a skip table."""
    out = bytearray()
    vbyte_encode_int(df, out)
    vbyte_encode_int(cf, out)
    step = skip_step(df)
    if not step:
        vbyte_encode_int(0, out)
        out += data
        return bytes(out)
    skips: List[Tuple[int, int]] = []
    p = 0
    doc = 0
    for i in range(df):
        if i and i % step == 0:
            skips.append((doc, p))
        g, p = vbyte_decode_one(data, p)
        _tf, p = vbyte_decode_one(data, p)
        plen, p = vbyte_decode_one(data, p)
        p += plen
        doc += g
    vbyte_encode_int(len(skips), out)
    pd = po = 0
    for d, o in skips:
        vbyte_encode_int(d - pd, out)
        vbyte_encode_int(o - po, out)
        pd, po = d, o
    out += data
    return bytes(out)


class LexiconWriter:
    def __init__(self, path: str):
        self.fh = open(path + ".bin", "wb", buffering=1 << 20)
        self.blk_path = path + ".blk"
        self.blocks = array("Q")
        self.pos = 0
        self.n = 0
        self.prev = b""
        self.buf = bytearray()

    def add(self, term: str, info: TermInfo) -> None:
        tb = term.encode("utf-8")
        buf = self.buf
        if self.n % LEX_BLOCK == 0:
            if buf:
                self.fh.write(buf)
                self.pos += len(buf)
                buf.clear()
            self.blocks.append(self.pos)
            vbyte_encode_int(len(tb), buf)
            buf += tb
            vbyte_encode_int(info.df, buf)
            vbyte_encode_int(info.cf, buf)
            vbyte_encode_int(info.offset, buf)
            vbyte_encode_int(info.length, buf)
        else:
            prev = self.prev
            k = 0
            m = min(len(prev), len(tb))
            while k < m and prev[k] == tb[k]:
                k += 1
            vbyte_encode_int(k, buf)
            vbyte_encode_int(len(tb) - k, buf)
            buf += tb[k:]
            vbyte_encode_int(info.df, buf)
            vbyte_encode_int(info.cf, buf)
            vbyte_encode_int(info.length, buf)
        self.prev = tb
        self.n += 1

    def close(self) -> None:
        if self.buf:
            self.fh.write(self.buf)
        self.fh.close()
        with open(self.blk_path, "wb") as f:
            self.blocks.tofile(f)


# ---------------------------------------------------------------------------
# In-memory vocabulary
# ---------------------------------------------------------------------------
class Lexicon:
    """The vocabulary, held entirely in main memory in compressed form."""

    def __init__(self, path: str):
        with open(path + ".bin", "rb") as f:
            self.blob = f.read()
        self.blocks = array("Q")
        with open(path + ".blk", "rb") as f:
            self.blocks.frombytes(f.read())
        blob = self.blob
        heads = []
        for off in self.blocks:
            n, p = vbyte_decode_one(blob, off)
            heads.append(blob[p:p + n].decode("utf-8"))
        self.heads = heads

    # -- block decoding ----------------------------------------------------
    def _decode_block(self, b: int) -> List[Tuple[str, TermInfo]]:
        blob = self.blob
        p = self.blocks[b]
        end = self.blocks[b + 1] if b + 1 < len(self.blocks) else len(blob)
        out = []
        n, p = vbyte_decode_one(blob, p)
        tb = blob[p:p + n]
        p += n
        (df, cf, off, ln), p = vbyte_decode_n(blob, p, 4)
        out.append((tb.decode("utf-8"), TermInfo(df, cf, off, ln)))
        while p < end:
            (k, s), p = vbyte_decode_n(blob, p, 2)
            tb = tb[:k] + blob[p:p + s]
            p += s
            off += ln
            (df, cf, ln), p = vbyte_decode_n(blob, p, 3)
            out.append((tb.decode("utf-8"), TermInfo(df, cf, off, ln)))
        return out

    # -- public API --------------------------------------------------------
    def __len__(self) -> int:
        if not self.blocks:
            return 0
        return (len(self.blocks) - 1) * LEX_BLOCK + len(self._decode_block(len(self.blocks) - 1))

    def get(self, term: str) -> Optional[TermInfo]:
        b = bisect.bisect_right(self.heads, term) - 1
        if b < 0:
            return None
        for t, info in self._decode_block(b):
            if t == term:
                return info
        return None

    def __contains__(self, term: str) -> bool:
        return self.get(term) is not None

    def items(self, start_block: int = 0) -> Iterator[Tuple[str, TermInfo]]:
        for b in range(start_block, len(self.blocks)):
            yield from self._decode_block(b)

    def prefix(self, prefix: str, limit: int = 0) -> Iterator[Tuple[str, TermInfo]]:
        """All terms starting with ``prefix`` (wildcard queries)."""
        b = max(0, bisect.bisect_left(self.heads, prefix) - 1)
        n = 0
        for t, info in self.items(b):
            if t < prefix:
                continue
            if not t.startswith(prefix):
                return
            yield t, info
            n += 1
            if limit and n >= limit:
                return

    def has_prefix(self, prefix: str) -> bool:
        return next(self.prefix(prefix, 1), None) is not None

    def memory_bytes(self) -> int:
        import sys
        return (len(self.blob) + self.blocks.itemsize * len(self.blocks)
                + sum(sys.getsizeof(h) for h in self.heads) + sys.getsizeof(self.heads))

    def to_dict(self) -> Dict[str, TermInfo]:
        return dict(self.items())


# ---------------------------------------------------------------------------
# On-disk postings
# ---------------------------------------------------------------------------
class PostingList:
    """A lazily decoded postings list backed by the memory-mapped file."""

    __slots__ = ("buf", "df", "cf", "skip_docs", "skip_offs", "data", "end", "step")

    def __init__(self, buf, offset: int, length: int):
        self.buf = buf
        self.end = offset + length
        (df, cf, nskip), p = vbyte_decode_n(buf, offset, 3)
        self.df, self.cf = df, cf
        sd, so = [], []
        d = o = 0
        for _ in range(nskip):
            (a, b), p = vbyte_decode_n(buf, p, 2)
            d += a
            o += b
            sd.append(d)
            so.append(o)
        self.skip_docs, self.skip_offs = sd, so
        self.data = p
        self.step = skip_step(df)

    def __iter__(self) -> Iterator[Tuple[int, int, int, int]]:
        """Yield (docID, tf, pos_start, pos_end) without decoding positions."""
        buf = self.buf
        p = self.data
        doc = 0
        for _ in range(self.df):
            (g, tf, plen), p = vbyte_decode_n(buf, p, 3)
            doc += g
            yield doc, tf, p, p + plen
            p += plen

    def positions(self, start: int, end: int) -> List[int]:
        buf = self.buf
        out = []
        acc = n = 0
        for i in range(start, end):
            b = buf[i]
            if b & 0x80:
                acc += (n << 7) | (b & 0x7F)
                out.append(acc)
                n = 0
            else:
                n = (n << 7) | b
        return out

    def decode_all(self) -> List[Tuple[int, List[int]]]:
        return [(d, self.positions(s, e)) for d, _tf, s, e in self]

    def cursor(self) -> "PostingCursor":
        return PostingCursor(self)


class PostingCursor:
    """Forward cursor with skip-pointer accelerated ``advance(target)``."""

    __slots__ = ("pl", "buf", "p", "prev", "i", "doc", "tf", "ps", "pe", "skips_used")

    def __init__(self, pl: PostingList):
        self.pl = pl
        self.buf = pl.buf
        self.p = pl.data
        self.prev = 0
        self.i = 0
        self.doc = -1
        self.tf = self.ps = self.pe = 0
        self.skips_used = 0
        self.next()

    def next(self) -> bool:
        if self.i >= self.pl.df:
            self.doc = None
            return False
        (g, tf, plen), p = vbyte_decode_n(self.buf, self.p, 3)
        self.prev += g
        self.doc = self.prev
        self.tf, self.ps, self.pe = tf, p, p + plen
        self.p = p + plen
        self.i += 1
        return True

    def advance(self, target: int) -> bool:
        """Move to the first document >= target. False when exhausted."""
        if self.doc is None:
            return False
        if self.doc >= target:
            return True
        pl = self.pl
        if pl.skip_docs:
            k = bisect.bisect_left(pl.skip_docs, target) - 1  # last prev_doc < target
            if k >= 0 and (k + 1) * pl.step > self.i - 1:
                self.p = pl.data + pl.skip_offs[k]
                self.prev = pl.skip_docs[k]
                self.i = (k + 1) * pl.step
                self.skips_used += 1
                self.next()
        while self.doc is not None and self.doc < target:
            self.next()
        return self.doc is not None

    def positions(self) -> List[int]:
        return self.pl.positions(self.ps, self.pe)


class PostingsFile:
    def __init__(self, path: str):
        self.fh = open(path, "rb")
        size = os.fstat(self.fh.fileno()).st_size
        self.buf = mmap.mmap(self.fh.fileno(), 0, access=mmap.ACCESS_READ) if size else b""

    def get(self, info: TermInfo) -> PostingList:
        return PostingList(self.buf, info.offset, info.length)

    def close(self):
        if isinstance(self.buf, mmap.mmap):
            self.buf.close()
        self.fh.close()


# ---------------------------------------------------------------------------
# Document table & store
# ---------------------------------------------------------------------------
class DocTable:
    def __init__(self, path: str):
        self.source_ids: List[str] = []
        self.titles: List[str] = []
        self.urls: List[str] = []
        self.lengths = array("I")
        self.ntokens = array("I")
        with open(path, encoding="utf-8") as f:
            next(f)  # header
            for line in f:
                _id, sid, nt, ntok, url, title = line.rstrip("\n").split("\t", 5)
                self.source_ids.append(sid)
                self.lengths.append(int(nt))
                self.ntokens.append(int(ntok))
                self.urls.append(url)
                self.titles.append(title)

    def __len__(self):
        return len(self.lengths)


class DocStore:
    def __init__(self, base: str):
        self.offsets = array("Q")
        idx = base + ".idx"
        if not os.path.exists(idx):
            self.fh = None
            return
        with open(idx, "rb") as f:
            self.offsets.frombytes(f.read())
        self.fh = open(base + ".bin", "rb")

    def text(self, docid: int) -> str:
        if self.fh is None:
            return ""
        a, b = self.offsets[docid], self.offsets[docid + 1]
        if a == b:
            return ""
        self.fh.seek(a)
        return zlib.decompress(self.fh.read(b - a)).decode("utf-8")

    def close(self):
        if self.fh is not None:
            self.fh.close()


def load_meta(index_dir: str) -> dict:
    with open(os.path.join(index_dir, "meta.json"), encoding="utf-8") as f:
        return json.load(f)
