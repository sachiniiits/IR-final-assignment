"""
cli.py -- command line interface.   Run  ``python -m indicindex -h``.

Roman-script input: inside any query, a word prefixed with ``~`` is
transliterated with the lexicon-guided transliterator (``~bharat`` -> भारत);
``--itrans`` converts the whole query with strict ITRANS instead.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
import time

from .textproc import AnalyzerConfig


def _out(s: str = "") -> None:
    try:
        print(s, flush=True)
    except BrokenPipeError:
        sys.exit(0)


def _expand_roman(idx, text: str, itrans: bool = False) -> str:
    from .translit import itrans_to_devanagari, suggest
    if itrans:
        return itrans_to_devanagari(text)

    def rep(m):
        sug = suggest(m.group(1), idx.lexicon, k=1)
        return sug[0][0] if sug else m.group(1)
    return re.sub(r"~([A-Za-z]+)", rep, text)


def _print_term(idx, word: str, max_docs: int, kwic: bool) -> None:
    r = idx.term(word)
    if r is None:
        _out(f"'{word}' (normalised: '{idx.norm(word)}') is not in the vocabulary")
        return
    t, info, plist = r
    _out(f"term: {t}   df={info.df:,}   cf={info.cf:,}   "
         f"[postings @ byte {info.offset:,}, {info.length:,} bytes on disk]")
    shown = plist[:max_docs] if max_docs else plist
    _out("  " + t + ", " + str(info.df) + ": <" + "; ".join(
        f"{d}: {','.join(map(str, p))}" for d, p in shown) + (" ..." if len(shown) < len(plist) else "") + ">")
    if kwic:
        for d, p in shown:
            _out(f"  doc {d} \"{idx.docs.titles[d]}\" tf={len(p)} positions={p[:12]}{' ...' if len(p) > 12 else ''}")
            for s in idx.kwic(d, p, width=6, max_snippets=2):
                _out(f"      {s}")


def _print_hits(idx, hits, span: int, k: int, t: float) -> None:
    _out(f"{len(hits)} matching documents  ({(time.time() - t) * 1000:.1f} ms, "
         f"skip pointers used: {idx.stats['skips']})")
    for h in hits[:k]:
        _out(f"  doc {h.docid:<7} \"{idx.docs.titles[h.docid]}\"  matches at {h.positions[:8]}"
             f"{' ...' if len(h.positions) > 8 else ''}")
        for s in idx.kwic(h.docid, h.positions, width=6, max_snippets=1, span=span):
            _out(f"      {s}")


def cmd_build(a):
    from .builder import build_index
    cfg = AnalyzerConfig(fold_nukta=not a.no_fold_nukta,
                         fold_chandrabindu=not a.no_fold_chandrabindu,
                         remove_stopwords=a.stopwords, stem=a.stem)
    build_index(a.corpus, a.output, fmt=a.format, lang=a.lang, cfg=cfg,
                workers=a.workers, batch_docs=a.batch_docs, batch_chars=a.batch_chars,
                max_positions=a.max_positions, fan_in=a.fan_in, limit=a.limit,
                store_text=not a.no_docstore)


def _open(a):
    from .search import PositionalIndex
    return PositionalIndex(a.index)


def cmd_term(a):
    idx = _open(a)
    for w in a.words:
        _print_term(idx, _expand_roman(idx, w, a.itrans), a.max_docs, not a.no_kwic)
        _out()


def cmd_phrase(a):
    idx = _open(a)
    q = _expand_roman(idx, " ".join(a.text), a.itrans)
    _out(f"phrase: \"{q}\"  ->  terms {idx.analyzer.tokens(q)}")
    t = time.time()
    hits = idx.phrase(q)
    _print_hits(idx, hits, len(idx.analyzer.tokens(q)), a.k, t)


def cmd_near(a):
    idx = _open(a)
    w1, w2 = _expand_roman(idx, a.w1, a.itrans), _expand_roman(idx, a.w2, a.itrans)
    _out(f"{w1} NEAR/{a.window} {w2}{' (ordered)' if a.ordered else ''}")
    t = time.time()
    hits = idx.near(w1, w2, a.window, a.ordered)
    _print_hits(idx, hits, 1, a.k, t)


def cmd_wildcard(a):
    idx = _open(a)
    res = idx.wildcard(_expand_roman(idx, a.pattern, a.itrans), a.k)
    for t, info in res:
        _out(f"  {t:<30} df={info.df:<8,} cf={info.cf:,}")


def cmd_search(a):
    idx = _open(a)
    q = _expand_roman(idx, " ".join(a.query), a.itrans)
    t = time.time()
    hits = idx.ranked(q, k=a.k)
    _out(f"query: {q}   terms={idx.analyzer.tokens(q)}   ({(time.time() - t) * 1000:.1f} ms)")
    for r, h in enumerate(hits, 1):
        _out(f"{r:2d}. {h.score:7.3f}  doc {h.docid:<7} \"{idx.docs.titles[h.docid]}\"")
        for s in idx.kwic(h.docid, h.positions, width=6, max_snippets=1):
            _out(f"      {s}")


def cmd_suggest(a):
    from .search import PositionalIndex
    from .translit import suggest, itrans_to_devanagari
    idx = PositionalIndex(a.index, load_docs=False)
    for w in a.words:
        t = time.time()
        res = suggest(w, idx.lexicon, k=a.k)
        _out(f"{w}  (strict ITRANS: {itrans_to_devanagari(w)})  [{(time.time() - t) * 1000:.1f} ms]")
        for term, info in res:
            _out(f"    {term:<20} df={info.df:<8,} cf={info.cf:,}")


def cmd_stats(a):
    from .stats import analyze
    r = analyze(a.index, a.output, a.sample)
    import json
    _out(json.dumps(r, ensure_ascii=False, indent=1, default=str))


def cmd_verify(a):
    from .verify import verify
    sys.exit(0 if verify(a.index, a.terms, a.docs) else 1)


def cmd_testcases(a):
    from .testcases import run_testcases
    run_testcases(a.index, a.output, a.terms or None)


def cmd_rare(a):
    from .search import PositionalIndex
    idx = PositionalIndex(a.index, load_docs=False)
    n = 0
    for t, info in idx.lexicon.items():
        if info.df <= a.max_df and len(t) >= a.min_len and \
                (not a.script or all("ऀ" <= c <= "ॿ" for c in t)):
            _out(f"{t}\tdf={info.df}\tcf={info.cf}")
            n += 1
            if n >= a.k:
                break


def cmd_crawl(a):
    from .crawler import Crawler
    Crawler(a.seeds, a.output, max_pages=a.max_pages, script=a.script,
            min_ratio=a.min_ratio, delay=a.delay, allowed_domains=a.domains).run()


def cmd_info(a):
    import json
    from .index_format import load_meta
    m = load_meta(a.index)
    m.pop("heaps_curve", None)
    _out(json.dumps(m, ensure_ascii=False, indent=1))


REPL_HELP = """commands:
  <words>                 ranked BM25 + proximity search
  "<phrase>"              exact phrase query
  <w1> NEAR/<k> <w2>      proximity query (unordered)
  <prefix>*               wildcard (prefix) query
  /term <word>            show the positional postings of a word
  /suggest <roman>        transliteration candidates (e.g. /suggest bharat)
  /and <w1> <w2> ...      boolean AND
  ~roman                  transliterate a word inside any query (~bharat)
  /help  /quit"""


def cmd_repl(a):
    idx = _open(a)
    m = idx.meta
    _out(f"index: {m['n_docs']:,} docs, {m['n_terms']:,} terms "
         f"(vocabulary loaded in {idx.load_seconds:.2f}s)\n{REPL_HELP}")
    from .translit import suggest
    while True:
        try:
            line = input("query> ").strip()
        except (EOFError, KeyboardInterrupt):
            _out()
            break
        if not line:
            continue
        idx.stats = {"skips": 0, "docs_decoded": 0}
        try:
            if line in ("/quit", "/exit", "/q"):
                break
            if line == "/help":
                _out(REPL_HELP)
                continue
            if line.startswith("/suggest "):
                for t, i in suggest(line.split(None, 1)[1], idx.lexicon, k=8):
                    _out(f"    {t:<20} df={i.df:<8,} cf={i.cf:,}")
                continue
            line = _expand_roman(idx, line)
            t = time.time()
            if line.startswith("/term "):
                for w in line.split()[1:]:
                    _print_term(idx, w, 10, True)
            elif line.startswith("/and "):
                docs = idx.conjunctive(line.split()[1:])
                _out(f"{len(docs)} docs: " + ", ".join(f"{d}:{idx.docs.titles[d]}" for d in docs[:20]))
            elif line.startswith('"') and line.endswith('"') and len(line) > 2:
                q = line.strip('"')
                _print_hits(idx, idx.phrase(q), len(idx.analyzer.tokens(q)), 10, t)
            elif re.search(r"\bNEAR/\d+\b", line):
                l, k, r = re.split(r"\bNEAR/(\d+)\b", line, maxsplit=1)
                _print_hits(idx, idx.near(l.strip(), r.strip(), int(k)), 1, 10, t)
            elif line.endswith("*") and " " not in line:
                for term, info in idx.wildcard(line, 25):
                    _out(f"  {term:<30} df={info.df:<8,} cf={info.cf:,}")
            else:
                hits = idx.ranked(line, k=10)
                _out(f"terms={idx.analyzer.tokens(line)}  ({(time.time() - t) * 1000:.1f} ms)")
                for r, h in enumerate(hits, 1):
                    _out(f"{r:2d}. {h.score:7.3f}  doc {h.docid:<7} \"{idx.docs.titles[h.docid]}\"")
                    for s in idx.kwic(h.docid, h.positions, width=6, max_snippets=1):
                        _out(f"      {s}")
        except Exception as e:  # keep the shell alive
            _out(f"error: {e}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="indicindex",
                                description="Positional inverted index for Indian-language text")
    sp = p.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("build", help="build a positional index from a corpus")
    b.add_argument("corpus", help="wiki XML dump (.bz2), FIRE/TREC file or dir, JSONL, or dir of .txt")
    b.add_argument("-o", "--output", required=True, help="index directory")
    b.add_argument("--format", default="auto", choices=["auto", "wiki", "trec", "jsonl", "text"])
    b.add_argument("--lang", default="hi")
    b.add_argument("--workers", type=int, default=0, help="worker processes (default: all CPUs)")
    b.add_argument("--batch-docs", type=int, default=2000)
    b.add_argument("--batch-chars", type=int, default=8_000_000)
    b.add_argument("--max-positions", type=int, default=3_000_000,
                   help="SPIMI memory budget: positions per run before flushing")
    b.add_argument("--fan-in", type=int, default=64, help="max runs merged at once")
    b.add_argument("--limit", type=int, default=0, help="index only the first N documents")
    b.add_argument("--stopwords", action="store_true", help="drop stop-words (positions preserved)")
    b.add_argument("--stem", action="store_true", help="Hindi light stemming")
    b.add_argument("--no-fold-nukta", action="store_true")
    b.add_argument("--no-fold-chandrabindu", action="store_true")
    b.add_argument("--no-docstore", action="store_true", help="do not keep document text")
    b.set_defaults(fn=cmd_build)

    def q(name, help_, fn):
        x = sp.add_parser(name, help=help_)
        x.add_argument("index")
        x.add_argument("--itrans", action="store_true", help="input is ITRANS romanisation")
        x.set_defaults(fn=fn)
        return x

    x = q("term", "show the positional postings of words", cmd_term)
    x.add_argument("words", nargs="+")
    x.add_argument("--max-docs", type=int, default=20)
    x.add_argument("--no-kwic", action="store_true")
    x = q("phrase", "exact phrase query", cmd_phrase)
    x.add_argument("text", nargs="+")
    x.add_argument("-k", type=int, default=10)
    x = q("near", "proximity query", cmd_near)
    x.add_argument("w1")
    x.add_argument("w2")
    x.add_argument("-w", "--window", type=int, default=5)
    x.add_argument("--ordered", action="store_true")
    x.add_argument("-k", type=int, default=10)
    x = q("wildcard", "prefix query, e.g. हिमाल*", cmd_wildcard)
    x.add_argument("pattern")
    x.add_argument("-k", type=int, default=30)
    x = q("search", "ranked retrieval (BM25 + proximity)", cmd_search)
    x.add_argument("query", nargs="+")
    x.add_argument("-k", type=int, default=10)

    x = sp.add_parser("suggest", help="roman -> Devanagari candidates from the lexicon")
    x.add_argument("index")
    x.add_argument("words", nargs="+")
    x.add_argument("-k", type=int, default=5)
    x.set_defaults(fn=cmd_suggest)

    x = sp.add_parser("stats", help="Zipf/Heaps/compression/memory report + SVG charts")
    x.add_argument("index")
    x.add_argument("-o", "--output", default=None)
    x.add_argument("--sample", type=int, default=20000, help="#terms sampled for compression estimates")
    x.set_defaults(fn=cmd_stats)

    x = sp.add_parser("verify", help="self-check index against stored documents")
    x.add_argument("index")
    x.add_argument("--terms", type=int, default=300)
    x.add_argument("--docs", type=int, default=50)
    x.set_defaults(fn=cmd_verify)

    x = sp.add_parser("testcases", help="generate the output/ test-case file")
    x.add_argument("index")
    x.add_argument("-o", "--output", default="output")
    x.add_argument("--terms", nargs="*")
    x.set_defaults(fn=cmd_testcases)

    x = sp.add_parser("rare", help="list rare terms")
    x.add_argument("index")
    x.add_argument("--max-df", type=int, default=1)
    x.add_argument("--min-len", type=int, default=6)
    x.add_argument("--script", action="store_true", help="Devanagari only")
    x.add_argument("-k", type=int, default=50)
    x.set_defaults(fn=cmd_rare)

    x = sp.add_parser("crawl", help="focused crawler -> JSONL corpus")
    x.add_argument("seeds", nargs="+")
    x.add_argument("-o", "--output", required=True)
    x.add_argument("--max-pages", type=int, default=100)
    x.add_argument("--script", default="devanagari")
    x.add_argument("--min-ratio", type=float, default=0.5)
    x.add_argument("--delay", type=float, default=1.0, help="seconds between requests to a host")
    x.add_argument("--domains", nargs="*", help="allowed host names (default: seed hosts)")
    x.set_defaults(fn=cmd_crawl)

    x = sp.add_parser("info", help="print index metadata")
    x.add_argument("index")
    x.set_defaults(fn=cmd_info)

    x = sp.add_parser("repl", help="interactive query shell")
    x.add_argument("index")
    x.set_defaults(fn=cmd_repl)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
