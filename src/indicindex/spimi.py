"""
spimi.py -- Single-Pass In-Memory Indexing (Heinz & Zobel, 2003) with
parallel workers and a multi-pass external k-way merge.

Pipeline
--------
           (main process)                 (N worker processes)
  corpus --> batches of RawDocs --imap--> clean + tokenise + build an
             with contiguous docIDs        in-memory dict term -> postings;
                                           flush a sorted *run file* to disk
                                           whenever the memory budget
                                           (#positions) is exceeded
                         <-- doc metadata + compressed text for docstore
  ...then  runs --(k-way heap merge, fan-in F, multi-pass if needed)-->
           postings.bin  +  lexicon.bin  (see index_format.py)

Because each batch covers a *contiguous* docID range and run files are
named <batch>_<sub>.run, sorting run files by name sorts them by docID.
Merging the postings of one term is therefore a simple concatenation in run
order - no re-sorting of postings is ever needed.

Run-file record (one per term, records sorted by term):
    struct '<HIIII'  term_len, df, cf, last_doc, payload_len
    term (UTF-8) | payload
Payload (identical layout to the final postings data, see index_format):
    per doc:  vbyte(d-gap) vbyte(tf) vbyte(plen) <plen bytes of position gaps>
    (the first d-gap of a payload is the absolute docID)
"""

from __future__ import annotations

import heapq
import os
import struct
import zlib
from typing import Dict, Iterator, List, NamedTuple, Tuple

from .codec import vbyte_encode_int, vbyte_decode_one
from .textproc import Analyzer, AnalyzerConfig, display_normalize
from .wikitext import clean_wikitext

REC = struct.Struct("<HIIII")


# ---------------------------------------------------------------------------
# Run file I/O
# ---------------------------------------------------------------------------
def encode_doc_positions(positions: List[int]) -> bytes:
    out = bytearray()
    prev = 0
    for p in positions:
        vbyte_encode_int(p - prev, out)
        prev = p
    return bytes(out)


def write_run(path: str, index: Dict[str, list]) -> int:
    """Write an in-memory partial index ``{term: [docid, [pos...], docid, [pos...], ...]}``
    (flat list, docids ascending) as a sorted run file.  Returns #terms."""
    with open(path, "wb", buffering=1 << 20) as fh:
        for term in sorted(index):
            flat = index[term]
            payload = bytearray()
            prev = 0
            cf = 0
            for i in range(0, len(flat), 2):
                d = flat[i]
                pos = flat[i + 1]
                tf = len(pos)
                cf += tf
                pb = encode_doc_positions(pos)
                vbyte_encode_int(d - prev, payload)
                vbyte_encode_int(tf, payload)
                vbyte_encode_int(len(pb), payload)
                payload += pb
                prev = d
            tb = term.encode("utf-8")
            fh.write(REC.pack(len(tb), len(flat) // 2, cf, prev, len(payload)))
            fh.write(tb)
            fh.write(payload)
    return len(index)


class RunRecord(NamedTuple):
    term: str
    df: int
    cf: int
    last_doc: int
    payload: bytes


def read_run(path: str) -> Iterator[RunRecord]:
    with open(path, "rb", buffering=1 << 20) as fh:
        read = fh.read
        size = REC.size
        while True:
            hdr = read(size)
            if len(hdr) < size:
                return
            tl, df, cf, last, pl = REC.unpack(hdr)
            term = read(tl).decode("utf-8")
            yield RunRecord(term, df, cf, last, read(pl))


# ---------------------------------------------------------------------------
# Worker: one batch of documents -> one or more run files
# ---------------------------------------------------------------------------
class BatchResult(NamedTuple):
    batch_no: int
    runs: List[str]
    docs: List[Tuple[str, str, str, int, int]]  # source_id, title, url, n_terms, n_tokens
    texts: List[bytes]                          # zlib-compressed display text
    n_tokens: int
    n_postings: int


def _prepare_text(doc) -> str:
    body = clean_wikitext(doc.body) if doc.fmt == "wikitext" else doc.body
    text = f"{doc.title}\n{body}" if doc.title else body
    return display_normalize(text)


def process_batch(args) -> BatchResult:
    (batch_no, first_docid, docs, cfg_dict, run_dir, max_positions,
     store_text) = args
    analyzer = Analyzer(AnalyzerConfig.from_dict(cfg_dict))
    analyze = analyzer.analyze
    index: Dict[str, list] = {}
    runs: List[str] = []
    meta, texts = [], []
    in_mem = 0
    total_tokens = total_postings = 0
    sub = 0

    def flush():
        nonlocal index, in_mem, sub
        if not index:
            return
        path = os.path.join(run_dir, f"{batch_no:06d}_{sub:03d}.run")
        write_run(path, index)
        runs.append(path)
        sub += 1
        index = {}
        in_mem = 0

    for k, doc in enumerate(docs):
        docid = first_docid + k
        text = _prepare_text(doc)
        doc_terms: Dict[str, list] = {}
        n_terms = 0
        last_pos = -1
        for pos, term in analyze(text):
            lst = doc_terms.get(term)
            if lst is None:
                doc_terms[term] = [pos]
            else:
                lst.append(pos)
            n_terms += 1
            last_pos = pos
        for term, plist in doc_terms.items():
            entry = index.get(term)
            if entry is None:
                index[term] = [docid, plist]
            else:
                entry.append(docid)
                entry.append(plist)
        in_mem += n_terms
        total_tokens += n_terms
        total_postings += len(doc_terms)
        meta.append((doc.source_id, doc.title, doc.url, n_terms, last_pos + 1))
        texts.append(zlib.compress(text.encode("utf-8"), 6) if store_text else b"")
        if in_mem >= max_positions:     # SPIMI memory budget exceeded
            flush()
    flush()
    return BatchResult(batch_no, runs, meta, texts, total_tokens, total_postings)


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------
def _heap_merge(paths: List[str]) -> Iterator[Tuple[str, List[RunRecord]]]:
    """Yield (term, [records in run order]) across all runs, term-sorted."""
    iters = [read_run(p) for p in paths]
    heap = []
    for seq, it in enumerate(iters):
        rec = next(it, None)
        if rec is not None:
            heap.append((rec.term, seq, rec))
    heapq.heapify(heap)
    while heap:
        term = heap[0][0]
        group = []
        while heap and heap[0][0] == term:
            _t, seq, rec = heapq.heappop(heap)
            group.append((seq, rec))
            nxt = next(iters[seq], None)
            if nxt is not None:
                heapq.heappush(heap, (nxt.term, seq, nxt))
        group.sort(key=lambda x: x[0])     # run order == docID order
        yield term, [r for _s, r in group]


def concat_payloads(records: List[RunRecord]) -> Tuple[bytearray, int]:
    """Concatenate payloads of consecutive runs, re-basing the first d-gap of
    each payload on the last docID of the previous one.  O(#runs), positions
    are copied as opaque bytes."""
    out = bytearray()
    prev = 0
    for r in records:
        first, p = vbyte_decode_one(r.payload, 0)
        vbyte_encode_int(first - prev, out)
        out += memoryview(r.payload)[p:]
        prev = r.last_doc
    return out, prev


def merge_runs_to_run(paths: List[str], out_path: str) -> None:
    """One intermediate pass of the multi-pass external merge."""
    with open(out_path, "wb", buffering=1 << 20) as fh:
        for term, recs in _heap_merge(paths):
            payload, last = concat_payloads(recs)
            df = sum(r.df for r in recs)
            cf = sum(r.cf for r in recs)
            tb = term.encode("utf-8")
            fh.write(REC.pack(len(tb), df, cf, last, len(payload)))
            fh.write(tb)
            fh.write(payload)


def reduce_runs(paths: List[str], work_dir: str, fan_in: int, log=print) -> List[str]:
    """Multi-pass merge until at most ``fan_in`` runs remain.  Consecutive
    runs are grouped so the docID order is preserved."""
    level = 0
    while len(paths) > fan_in:
        level += 1
        new_paths = []
        groups = [paths[i:i + fan_in] for i in range(0, len(paths), fan_in)]
        log(f"  merge pass {level}: {len(paths)} runs -> {len(groups)} runs")
        for g, grp in enumerate(groups):
            if len(grp) == 1:
                new_paths.append(grp[0])
                continue
            out = os.path.join(work_dir, f"L{level}_{g:06d}.run")
            merge_runs_to_run(grp, out)
            for p in grp:
                os.remove(p)
            new_paths.append(out)
        paths = new_paths
    return paths


def heap_merge(paths: List[str]):
    return _heap_merge(paths)
