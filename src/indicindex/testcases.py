"""
testcases.py -- generates the assignment "output" deliverable.

  python -m indicindex testcases <index_dir> -o output/

Writes
  test_cases.txt         human readable: 10 rare terms with their complete
                         positional index (+ a KWIC line proving every
                         position), followed by 5 functional test cases with
                         input / expected / actual / PASS-FAIL
  rare_terms_index.json  the same 10 positional postings, machine readable

Rare-term selection (deterministic, seed fixed): Devanagari terms with
df <= 3 that look like real words (5..16 characters, no trailing virama),
stratified as 4 hapax (df=1), 3 with df=2 and 3 with df=3.  Terms can also
be given explicitly with --terms.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import List, Optional

from .search import PositionalIndex
from .translit import itrans_to_devanagari, suggest

LINE = "=" * 78
SUB = "-" * 78


def pick_rare_terms(idx: PositionalIndex, seed: int = 2026) -> List[str]:
    rnd = random.Random(seed)
    buckets = {1: [], 2: [], 3: []}
    for term, info in idx.lexicon.items():
        if info.df > 3 or info.cf > 6:
            continue
        if not (5 <= len(term) <= 16):
            continue
        if not all("ऀ" <= ch <= "ॿ" for ch in term) or term.endswith("्"):
            continue
        buckets[info.df].append(term)
    want = {1: 4, 2: 3, 3: 3}
    chosen = []
    for df, n in want.items():
        pool = buckets[df]
        chosen += rnd.sample(pool, min(n, len(pool)))
    return chosen


def format_postings(idx: PositionalIndex, term: str) -> dict:
    t, info, plist = idx.term(term, normalize=False)
    docs = []
    for d, positions in plist:
        docs.append({
            "docid": d,
            "source_id": idx.docs.source_ids[d],
            "title": idx.docs.titles[d],
            "tf": len(positions),
            "positions": positions,
            "kwic": idx.kwic(d, positions, width=5, mark=("«", "»"), max_snippets=len(positions)),
            "surface_check": [idx.surface(d, p) for p in positions],
        })
    return {"term": t, "df": info.df, "cf": info.cf,
            "postings_offset": info.offset, "postings_bytes": info.length, "postings": docs}


def textbook_line(p: dict) -> str:
    inner = "; ".join(f"{d['docid']}: {', '.join(map(str, d['positions']))}" for d in p["postings"])
    return f"{p['term']}  [df={p['df']}, cf={p['cf']}]  ->  < {inner} >"


def run_testcases(index_dir: str, out_dir: str, terms: Optional[List[str]] = None,
                  log=print) -> None:
    os.makedirs(out_dir, exist_ok=True)
    idx = PositionalIndex(index_dir)
    m = idx.meta
    out: List[str] = []
    w = out.append
    w(LINE)
    w("POSITIONAL INDEX - TEST CASES AND RARE-TERM POSITIONAL POSTINGS")
    w(LINE)
    w(f"Collection : {os.path.basename(m['corpus'])}  ({m['corpus_format']}, language={m['language']})")
    w(f"Documents  : {m['n_docs']:,}    Tokens: {m['n_tokens']:,}    Vocabulary: {m['n_terms']:,}")
    w(f"Postings   : {m['postings_bytes'] / 2**20:,.1f} MiB on disk (postings.bin)   "
      f"Lexicon in RAM: {m['lexicon_bytes'] / 2**20:,.2f} MiB")
    w(f"Analyzer   : {m['analyzer']}")
    w(f"Generated  : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w("")
    w("Notation   : term [df, cf] -> < docID: pos1, pos2, ...; docID: ... >")
    w("             positions are 0-based token offsets inside the document")
    w("             (title tokens come first).  Each position is followed by a")
    w("             KWIC line re-generated from the stored document text, the")
    w("             matched token is shown in «guillemets».")

    # ------------------------------------------------------------------ A
    terms = terms or pick_rare_terms(idx)
    rare = []
    w("")
    w(LINE)
    w(f"PART A - {len(terms)} RARE TERMS WITH THEIR POSITIONAL INDEX")
    w(LINE)
    for i, term in enumerate(terms, 1):
        nt = idx.norm(term)
        if idx.info(nt) is None:
            w(f"\n[{i}] {term}: NOT IN VOCABULARY")
            continue
        p = format_postings(idx, nt)
        rare.append(p)
        w("")
        w(f"[{i:2d}] {textbook_line(p)}")
        for d in p["postings"]:
            w(f"      doc {d['docid']} ({d['source_id']}) \"{d['title']}\"  tf={d['tf']}")
            for pos, kw, sf in zip(d["positions"], d["kwic"], d["surface_check"]):
                ok = "ok" if idx.norm(sf) == p["term"] else "MISMATCH"
                w(f"        pos {pos:>6}: {kw}   [{ok}]")
    with open(os.path.join(out_dir, "rare_terms_index.json"), "w", encoding="utf-8") as f:
        json.dump(rare, f, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------ B
    w("")
    w(LINE)
    w("PART B - 5 FUNCTIONAL TEST CASES")
    w(LINE)
    results = []

    def case(no, title, inp, expected, fn):
        w("")
        w(SUB)
        w(f"TEST CASE {no}: {title}")
        w(SUB)
        w(f"Input    : {inp}")
        w(f"Expected : {expected}")
        t = time.time()
        lines, passed = fn()
        dt = (time.time() - t) * 1000
        for ln in lines:
            w(f"  {ln}")
        w(f"Result   : {'PASS' if passed else 'FAIL'}   ({dt:.1f} ms)")
        results.append((no, title, passed))

    # 1. rare term lookup: every stored position must hold the term
    def tc1():
        lines, ok = [], True
        for p in rare:
            good = sum(idx.norm(s) == p["term"] for d in p["postings"] for s in d["surface_check"])
            ok &= good == p["cf"] and len(p["postings"]) == p["df"]
            lines.append(f"{p['term']:<22} df={p['df']} cf={p['cf']}  positions verified {good}/{p['cf']}")
        return lines, ok and bool(rare)
    case(1, "Rare-term lookup + positional verification against the stored text",
         ", ".join(p["term"] for p in rare),
         "df == #docs, cf == #positions, every position re-tokenises to the term", tc1)

    # 2. phrase query
    phrase = "भारत की राजधानी"
    def tc2():
        idx.stats = {"skips": 0, "docs_decoded": 0}
        hits = idx.phrase(phrase)
        lines = [f"{len(hits)} documents contain the exact phrase; "
                 f"skip pointers used={idx.stats['skips']}, docs whose positions were decoded={idx.stats['docs_decoded']}"]
        ok = bool(hits)
        for h in hits[:5]:
            snip = idx.kwic(h.docid, h.positions, width=4, mark=("«", "»"), max_snippets=1, span=3)
            lines.append(f"doc {h.docid} \"{idx.docs.titles[h.docid]}\" starts={h.positions[:6]}")
            lines += [f"   {s}" for s in snip]
            ok &= all(idx.norm(idx.surface(h.docid, s + k)) == t
                      for s in h.positions[:3] for k, t in enumerate(idx.analyzer.tokens(phrase)))
        return lines, ok
    case(2, "Exact phrase query (positional intersection with skip pointers)",
         f'"{phrase}"', "documents where the 3 terms occur at consecutive positions", tc2)

    # 3. proximity query
    def tc3():
        hits = idx.near("गंगा", "यमुना", k=3)
        lines = [f"{len(hits)} documents with गंगा within 3 tokens of यमुना"]
        ok = bool(hits)
        for h in hits[:4]:
            lines += [f"doc {h.docid} \"{idx.docs.titles[h.docid]}\": " +
                      " | ".join(idx.kwic(h.docid, h.positions, width=4, mark=("«", "»"), max_snippets=1))]
        return lines, ok
    case(3, "Proximity query NEAR/3", "गंगा NEAR/3 यमुना",
         "documents where both terms occur within a window of 3 tokens", tc3)

    # 4. transliteration
    def tc4():
        lines, ok = [], True
        for roman, exp in [("bhArata", "भारत"), ("rAShTrapati", "राष्ट्रपति")]:
            dev = itrans_to_devanagari(roman)
            lines.append(f"ITRANS  {roman:<14} -> {dev}   (expected {exp})")
            ok &= dev == exp
        for roman in ["himalay", "pradhanmantri", "hindi"]:
            sug = suggest(roman, idx.lexicon, k=4)
            lines.append(f"loose   {roman:<14} -> " + ", ".join(f"{t}(cf={i.cf})" for t, i in sug))
            ok &= bool(sug)
        return lines, ok
    case(4, "Roman-script queries: ITRANS + lexicon-guided transliteration",
         "bhArata, rAShTrapati, himalay, pradhanmantri, hindi",
         "correct Devanagari; loose input ranked to words that exist in the index", tc4)

    # 5. normalisation robustness
    def tc5():
        pairs = [("ज़िला", "जिला"), ("गाँधी", "गांधी"), ("हिन्दी‍", "हिन्दी"), ("१९४७", "1947")]
        lines, ok = [], True
        for a, b in pairs:
            ra, rb = idx.term(a), idx.term(b)
            same = (ra is not None and rb is not None and ra[0] == rb[0] and ra[1] == rb[1])
            lines.append(f"{a!r:>14} vs {b!r:<10} -> term={ra[0] if ra else None} "
                         f"df={ra[1].df if ra else 0}  identical postings: {same}")
            ok &= same
        return lines, ok
    case(5, "Unicode normalisation (nukta, chandrabindu, ZWJ, native digits)",
         "ज़िला/जिला, गाँधी/गांधी, हिन्दी+ZWJ/हिन्दी, १९४७/1947",
         "both spellings map to the same vocabulary entry and postings", tc5)

    w("")
    w(LINE)
    w("SUMMARY: " + ", ".join(f"TC{n} {'PASS' if p else 'FAIL'}" for n, _t, p in results))
    w(LINE)
    path = os.path.join(out_dir, "test_cases.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    log("\n".join(out[-3:]))
    log(f"[testcases] written {path}")
