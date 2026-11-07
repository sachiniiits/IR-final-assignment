"""
corpus.py -- streaming readers for every supported collection format.

All readers are *generators*: documents are streamed one at a time, so even a
multi-GB dump never has to fit in memory.  Each yields ``RawDoc`` records;
heavy work (markup cleaning, tokenising) is done later inside the parallel
indexing workers, which is why the raw markup is passed through untouched.

Supported formats (auto-detected by ``open_corpus``):

  wiki   MediaWiki XML dump, plain or .bz2/.gz compressed
         (e.g. hiwiki-latest-pages-articles.xml.bz2).  Only main-namespace
         (ns=0) articles are kept; redirects are skipped.
  trec   FIRE / TREC SGML collections:  <DOC><DOCNO>..</DOCNO>
         [<TITLE>..</TITLE>] <TEXT>..</TEXT></DOC>; a single file or a whole
         directory tree (files may be .gz).
  jsonl  One JSON object per line with keys id/title/text[/url]  -- the
         output format of ``indicindex crawl``.
  text   A directory of .txt files, one document per file.
"""

from __future__ import annotations

import bz2
import gzip
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Iterator, NamedTuple, Optional


class RawDoc(NamedTuple):
    source_id: str     # id in the source collection (wiki page id, DOCNO, ...)
    title: str
    url: str
    body: str          # raw body (wikitext or plain text)
    fmt: str           # "wikitext" | "plain"


def _open_maybe_compressed(path: str, mode: str = "rb"):
    # text mode is always UTF-8 (Windows would otherwise default to cp1252)
    enc = {} if "b" in mode else {"encoding": "utf-8", "errors": "replace"}
    if path.endswith(".bz2"):
        return bz2.open(path, mode, **enc)
    if path.endswith(".gz"):
        return gzip.open(path, mode, **enc)
    return open(path, mode, **enc)


# ---------------------------------------------------------------------------
# MediaWiki XML dump
# ---------------------------------------------------------------------------
def read_wiki_dump(path: str, lang: str = "hi") -> Iterator[RawDoc]:
    with _open_maybe_compressed(path, "rb") as fh:
        ctx = ET.iterparse(fh, events=("start", "end"))
        _ev, root = next(ctx)
        nsuri = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        T_PAGE, T_NS, T_TITLE = nsuri + "page", nsuri + "ns", nsuri + "title"
        T_ID, T_REDIRECT, T_TEXT = nsuri + "id", nsuri + "redirect", nsuri + "text"
        T_REV = nsuri + "revision"
        title = ns = pid = text = None
        redirect = False
        in_rev = False
        for ev, el in ctx:
            tag = el.tag
            if ev == "start":
                if tag == T_REV:
                    in_rev = True
                continue
            if tag == T_TITLE:
                title = el.text or ""
            elif tag == T_NS:
                ns = el.text
            elif tag == T_ID and not in_rev and pid is None:
                pid = el.text
            elif tag == T_REDIRECT:
                redirect = True
            elif tag == T_TEXT:
                text = el.text or ""
            elif tag == T_REV:
                in_rev = False
            elif tag == T_PAGE:
                if ns == "0" and not redirect and text:
                    yield RawDoc(
                        source_id=f"{lang}wiki:{pid}",
                        title=title,
                        url=f"https://{lang}.wikipedia.org/wiki?curid={pid}",
                        body=text,
                        fmt="wikitext",
                    )
                title = ns = pid = text = None
                redirect = False
                root.clear()   # free memory of processed pages


# ---------------------------------------------------------------------------
# FIRE / TREC SGML
# ---------------------------------------------------------------------------
_DOC_RE = re.compile(r"<DOC>(.*?)</DOC>", re.S | re.I)
_DOCNO_RE = re.compile(r"<DOCNO>\s*(.*?)\s*</DOCNO>", re.S | re.I)
_TITLE_RE = re.compile(r"<TITLE>(.*?)</TITLE>", re.S | re.I)
_TEXT_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.S | re.I)
_ANYTAG_RE = re.compile(r"<[^>]+>")


def _iter_files(path: str):
    if os.path.isfile(path):
        yield path
        return
    for dirpath, _dirs, files in os.walk(path):
        for f in sorted(files):
            yield os.path.join(dirpath, f)
        _dirs.sort()


def read_trec(path: str) -> Iterator[RawDoc]:
    for fp in _iter_files(path):
        with _open_maybe_compressed(fp, "rb") as fh:
            data = fh.read().decode("utf-8", errors="replace")
        for m in _DOC_RE.finditer(data):
            block = m.group(1)
            dn = _DOCNO_RE.search(block)
            docno = dn.group(1) if dn else f"{os.path.basename(fp)}#{m.start()}"
            tm = _TITLE_RE.search(block)
            title = _ANYTAG_RE.sub(" ", tm.group(1)).strip() if tm else ""
            texts = _TEXT_RE.findall(block)
            body = "\n".join(texts) if texts else _DOCNO_RE.sub("", block)
            body = _ANYTAG_RE.sub(" ", body)
            yield RawDoc(docno, title, "", body, "plain")


# ---------------------------------------------------------------------------
# JSONL (crawler output) and plain text
# ---------------------------------------------------------------------------
def read_jsonl(path: str) -> Iterator[RawDoc]:
    for fp in _iter_files(path):
        with _open_maybe_compressed(fp, "rt") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                yield RawDoc(str(o.get("id", f"{os.path.basename(fp)}:{i}")),
                             o.get("title", ""), o.get("url", ""),
                             o.get("text", ""), o.get("fmt", "plain"))


def read_textdir(path: str) -> Iterator[RawDoc]:
    for fp in _iter_files(path):
        if not fp.endswith(".txt"):
            continue
        with open(fp, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        name = os.path.splitext(os.path.basename(fp))[0]
        yield RawDoc(os.path.relpath(fp, path), name, "", body, "plain")


READERS = {"wiki": read_wiki_dump, "trec": read_trec, "jsonl": read_jsonl,
           "text": read_textdir}


def detect_format(path: str) -> str:
    probe: Optional[str] = None
    target = path
    if os.path.isdir(path):
        for fp in _iter_files(path):
            target = fp
            break
        if target.endswith(".txt"):
            return "text"
    if ".jsonl" in target or target.endswith(".json"):
        return "jsonl"
    with _open_maybe_compressed(target, "rb") as fh:
        probe = fh.read(4096).decode("utf-8", errors="ignore")
    if "<mediawiki" in probe:
        return "wiki"
    if re.search(r"<DOC>", probe, re.I):
        return "trec"
    if probe.lstrip().startswith("{"):
        return "jsonl"
    return "text"


def open_corpus(path: str, fmt: str = "auto", lang: str = "hi",
                limit: int = 0) -> Iterator[RawDoc]:
    if fmt == "auto":
        fmt = detect_format(path)
    reader = READERS[fmt]
    it = reader(path, lang) if fmt == "wiki" else reader(path)
    for i, doc in enumerate(it):
        if limit and i >= limit:
            break
        yield doc
