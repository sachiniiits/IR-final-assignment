"""
builder.py -- end-to-end index construction.

    python -m indicindex build <corpus> -o <index_dir> [options]

Steps
  1. stream documents from the corpus reader (corpus.py)
  2. cut the stream into batches (bounded by #docs and #characters) and hand
     them to a multiprocessing pool -> each worker runs SPIMI (spimi.py),
     producing sorted run files on disk
  3. the main process writes doc metadata + compressed text in docID order
  4. multi-pass k-way merge of the runs (fan-in bounded) and final pass that
     writes postings.bin (+ skip pointers) and lexicon.bin
  5. Heaps' law curve is reconstructed *exactly* from the first docID of each
     term (free by-product of the merge) and saved with the statistics.

At no point is the full postings list held in main memory; only the
current batch (per worker) and one term's postings (during the merge).
"""

from __future__ import annotations

import heapq
import json
import multiprocessing as mp
import os
import shutil
import sys
import time
from array import array
from typing import Iterator, List

from .codec import vbyte_decode_one
from .corpus import open_corpus, detect_format
from .index_format import LexiconWriter, TermInfo, build_entry
from .spimi import BatchResult, concat_payloads, heap_merge, process_batch, reduce_runs
from .textproc import AnalyzerConfig, script_of

FORMAT_VERSION = 1


def _batches(docs, max_docs: int, max_chars: int) -> Iterator[list]:
    batch, chars = [], 0
    for d in docs:
        batch.append(d)
        chars += len(d.body)
        if len(batch) >= max_docs or chars >= max_chars:
            yield batch
            batch, chars = [], 0
    if batch:
        yield batch


def _fmt_secs(s: float) -> str:
    return f"{int(s // 60)}m{s % 60:04.1f}s"


def build_index(corpus: str, out_dir: str, fmt: str = "auto", lang: str = "hi",
                cfg: AnalyzerConfig | None = None, workers: int = 0,
                batch_docs: int = 2000, batch_chars: int = 8_000_000,
                max_positions: int = 3_000_000, fan_in: int = 64,
                limit: int = 0, store_text: bool = True, keep_runs: bool = False,
                log=print) -> dict:
    cfg = cfg or AnalyzerConfig()
    workers = workers or max(1, (os.cpu_count() or 2))
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    run_dir = os.path.join(out_dir, "_runs")
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)
    if fmt == "auto":
        fmt = detect_format(corpus)
    log(f"[build] corpus={corpus} format={fmt} workers={workers}")
    log(f"[build] analyzer={cfg.to_dict()}")

    # ---------------- phase 1: parallel SPIMI ------------------------------
    docs_tsv = open(os.path.join(out_dir, "docs.tsv"), "w", encoding="utf-8")
    docs_tsv.write("docid\tsource_id\tn_terms\tn_tokens\turl\ttitle\n")
    ds_bin = open(os.path.join(out_dir, "docstore.bin"), "wb", buffering=1 << 20)
    ds_off = array("Q", [0])
    doc_lengths = array("I")
    runs: List[str] = []
    n_docs = n_tokens = n_postings = 0

    def tasks():
        next_id = 0
        for b, batch in enumerate(_batches(open_corpus(corpus, fmt, lang, limit),
                                           batch_docs, batch_chars)):
            yield (b, next_id, batch, cfg.to_dict(), run_dir, max_positions, store_text)
            next_id += len(batch)

    def consume(res: BatchResult):
        nonlocal n_docs, n_tokens, n_postings
        runs.extend(res.runs)
        for (sid, title, url, nterms, ntok), blob in zip(res.docs, res.texts):
            clean = lambda s: s.replace("\t", " ").replace("\n", " ")
            docs_tsv.write(f"{n_docs}\t{clean(sid)}\t{nterms}\t{ntok}\t{clean(url)}\t{clean(title)}\n")
            ds_bin.write(blob)
            ds_off.append(ds_off[-1] + len(blob))
            doc_lengths.append(nterms)
            n_docs += 1
        n_tokens += res.n_tokens
        n_postings += res.n_postings
        el = time.time() - t0
        log(f"  batch {res.batch_no:5d}: docs={n_docs:,} tokens={n_tokens:,} "
            f"runs={len(runs)}  [{_fmt_secs(el)}, {n_tokens / max(el, 1e-9):,.0f} tok/s]")

    if workers == 1:
        for t in tasks():
            consume(process_batch(t))
    else:
        method = os.environ.get("INDICINDEX_START_METHOD") or \
            ("spawn" if sys.platform == "win32" else "fork")
        ctx = mp.get_context(method)
        with ctx.Pool(workers, maxtasksperchild=50) as pool:
            for res in pool.imap(process_batch, tasks()):  # imap keeps docID order
                consume(res)
    docs_tsv.close()
    ds_bin.close()
    with open(os.path.join(out_dir, "docstore.idx"), "wb") as f:
        ds_off.tofile(f)
    t_invert = time.time() - t0
    log(f"[build] inversion done: {n_docs:,} docs, {n_tokens:,} tokens, "
        f"{len(runs)} runs in {_fmt_secs(t_invert)}")

    # ---------------- phase 2: external merge -----------------------------
    runs.sort()  # file names encode batch/sub-block -> docID order
    n_runs_initial = len(runs)
    runs = reduce_runs(runs, run_dir, fan_in, log)
    log(f"[build] final merge of {len(runs)} runs -> postings.bin + lexicon")
    lex = LexiconWriter(os.path.join(out_dir, "lexicon"))
    first_doc_hist = array("I", [0]) * (n_docs + 1)
    script_counts = {}
    df_hist = {}
    top_cf = []
    offset = 0
    n_terms = 0
    with open(os.path.join(out_dir, "postings.bin"), "wb", buffering=1 << 22) as pf:
        for term, recs in heap_merge(runs):
            data, _last = concat_payloads(recs)
            df = sum(r.df for r in recs)
            cf = sum(r.cf for r in recs)
            entry = build_entry(df, cf, data)
            pf.write(entry)
            lex.add(term, TermInfo(df, cf, offset, len(entry)))
            offset += len(entry)
            n_terms += 1
            # statistics by-products ------------------------------------
            first, _ = vbyte_decode_one(recs[0].payload, 0)
            first_doc_hist[first] += 1
            sc = script_of(term)
            script_counts[sc] = script_counts.get(sc, 0) + 1
            bucket = 1 if df == 1 else 2 if df == 2 else 3 if df <= 5 else \
                10 if df <= 10 else 100 if df <= 100 else 1000 if df <= 1000 else 10 ** 9
            df_hist[bucket] = df_hist.get(bucket, 0) + 1
            if len(top_cf) < 50:
                heapq.heappush(top_cf, (cf, term, df))
            elif cf > top_cf[0][0]:
                heapq.heapreplace(top_cf, (cf, term, df))
            if n_terms % 200_000 == 0:
                log(f"  merged {n_terms:,} terms, postings {offset / 2**20:,.1f} MiB")
    lex.close()
    if not keep_runs:
        shutil.rmtree(run_dir, ignore_errors=True)

    # ---------------- Heaps' law curve (exact, doc granularity) -----------
    heaps = []
    cum_tok = cum_voc = 0
    next_sample = 1
    for d in range(n_docs):
        cum_tok += doc_lengths[d]
        cum_voc += first_doc_hist[d]
        if cum_tok >= next_sample or d == n_docs - 1:
            heaps.append((cum_tok, cum_voc))
            next_sample = max(cum_tok + 1, int(cum_tok * 1.15))

    total = time.time() - t0
    meta = {
        "format_version": FORMAT_VERSION,
        "corpus": os.path.abspath(corpus),
        "corpus_format": fmt,
        "language": lang,
        "analyzer": cfg.to_dict(),
        "n_docs": n_docs,
        "n_tokens": n_tokens,
        "n_terms": n_terms,
        "n_postings": n_postings,
        "avg_doc_len": n_tokens / max(n_docs, 1),
        "postings_bytes": offset,
        "lexicon_bytes": os.path.getsize(os.path.join(out_dir, "lexicon.bin")),
        "docstore_bytes": ds_off[-1],
        "n_runs": n_runs_initial,
        "workers": workers,
        "max_positions_per_run": max_positions,
        "fan_in": fan_in,
        "seconds_invert": round(t_invert, 2),
        "seconds_total": round(total, 2),
        "terms_by_script": dict(sorted(script_counts.items(), key=lambda x: -x[1])),
        "df_histogram": {("df>1000" if k == 10 ** 9 else f"df<={k}"): v
                         for k, v in sorted(df_hist.items())},
        "top_terms_by_cf": [{"term": t, "cf": c, "df": d}
                            for c, t, d in sorted(top_cf, reverse=True)],
        "heaps_curve": heaps,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log(f"[build] done: {n_terms:,} terms, postings={offset / 2**20:,.1f} MiB, "
        f"lexicon={meta['lexicon_bytes'] / 2**20:,.2f} MiB, total {_fmt_secs(total)}")
    return meta
