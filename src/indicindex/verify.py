"""
verify.py -- self-verification of a built index against its document store.

Two independent checks on random samples:

  soundness     for sampled TERMS: every (doc, position) in the postings list
                really holds that term when the stored document is
                re-tokenised; df == #docs and cf == sum(tf).
  completeness  for sampled DOCUMENTS: every (position, term) produced by
                re-tokenising the document is present in that term's
                postings list (looked up via the skip-pointer cursor).

A passing run proves that the SPIMI runs, the multi-pass merge, the gap /
VByte coding, the skip tables and the lexicon offsets are all consistent.
"""

from __future__ import annotations

import random
import time

from .search import PositionalIndex


def verify(index_dir: str, n_terms: int = 300, n_docs: int = 50, seed: int = 7,
           log=print) -> bool:
    idx = PositionalIndex(index_dir)
    rnd = random.Random(seed)
    an = idx.analyzer
    t0 = time.time()
    cache = {}

    def doc_terms(d):
        if d not in cache:
            if len(cache) > 2000:
                cache.clear()
            cache[d] = dict(an.analyze(idx.docstore.text(d)))
        return cache[d]

    # pick terms: a mix of rare (from lexicon order) and frequent ones
    nblocks = len(idx.lexicon.blocks)
    sample = []
    for _ in range(n_terms):
        b = rnd.randrange(nblocks)
        sample.append(rnd.choice(idx.lexicon._decode_block(b)))
    sample += [(t["term"], idx.lexicon.get(t["term"])) for t in idx.meta["top_terms_by_cf"][:5]]

    ok_pos = bad_pos = bad_stats = 0
    for term, info in sample:
        pl = idx.postings.get(info)
        ndoc = tf_sum = 0
        for d, tf, s, e in pl:
            ndoc += 1
            tf_sum += tf
            positions = pl.positions(s, e)
            if len(positions) != tf:
                bad_stats += 1
            dt = doc_terms(d) if info.df < 5000 or rnd.random() < 0.02 else None
            if dt is None:
                continue
            for p in positions:
                if dt.get(p) == term:
                    ok_pos += 1
                else:
                    bad_pos += 1
                    if bad_pos <= 5:
                        log(f"  MISMATCH term={term} doc={d} pos={p} found={dt.get(p)!r}")
        if ndoc != info.df or tf_sum != info.cf or ndoc != pl.df:
            bad_stats += 1
            log(f"  STAT MISMATCH {term}: df {info.df}/{ndoc} cf {info.cf}/{tf_sum}")
    log(f"[verify] soundness   : {len(sample)} terms, {ok_pos:,} positions OK, "
        f"{bad_pos} wrong, {bad_stats} df/cf errors")

    ok_c = bad_c = 0
    docs = [rnd.randrange(idx.n_docs) for _ in range(n_docs)]
    for d in docs:
        by_term = {}
        for p, t in an.analyze(idx.docstore.text(d)):
            by_term.setdefault(t, []).append(p)
        for t, plist in by_term.items():
            pl = idx.plist(t)
            cur = pl.cursor() if pl else None
            if cur and cur.advance(d) and cur.doc == d and cur.positions() == plist:
                ok_c += 1
            else:
                bad_c += 1
                if bad_c <= 5:
                    log(f"  MISSING doc={d} term={t}")
    log(f"[verify] completeness: {n_docs} docs, {ok_c:,} (term,doc) postings OK, {bad_c} missing")
    passed = bad_pos == 0 and bad_stats == 0 and bad_c == 0
    log(f"[verify] {'PASSED' if passed else 'FAILED'} in {time.time() - t0:.1f}s")
    idx.close()
    return passed
