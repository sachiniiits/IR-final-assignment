IndicIndex: a positional inverted index for Hindi
==================================================

IR Assignment A1 (PostIndex), M2026. Roll No: `<YourRollNo>`

IndicIndex builds a **positional inverted index** for an Indian-language
collection, here the **complete Hindi Wikipedia** (176,041 articles, 51.6
million tokens, 1,054,110 distinct terms). Each postings list stores
**term positions** together with the **collection frequency (cf)** and
**document frequency (df)**. The **vocabulary lives in main memory**, in
compressed form. The **postings lists live on secondary storage**: the
query side memory-maps them and the build side streams them through run
files.

The code is pure Python 3 (3.8 or newer) and uses only the standard library,
so there is nothing to `pip install`.

```
python -m indicindex build  hiwiki-latest-pages-articles.xml.bz2 -o index/   # 2 min 20 s on 4 cores
python -m indicindex term   index/ पार्थेनोकार्पी                               # positional postings
python -m indicindex phrase index/ "भारत की राजधानी"                           # exact phrase
python -m indicindex search index/ "~tajmahal ~agra"                            # Roman-script query
```

---------------------------------------------------------------------------

## 1. Novel ideas and highlights

| # | Idea | Why it matters |
|---|------|----------------|
| 1 | **Script-block tokenizer for Indic text** | Python's `\w` does not match matras or virama, so the naive `re.findall(r"\w+")` breaks `हिन्दी` into fragments. Our tokenizer works on Unicode script blocks. It covers 9 Indic scripts plus Latin, and splits mixed-script strings at the script boundary (`BJPके` becomes `bjp` + `के`). |
| 2 | **Hindi-aware normalisation, stored inside the index** | NFC; removal of ZWJ, ZWNJ and soft hyphen; native digits mapped to ASCII (`१९४७` = `1947`); nukta folding (`ज़िला` = `जिला`); chandrabindu folding (`गाँधी` = `गांधी`). The analyzer configuration is written to `meta.json`, so queries are always normalised exactly as the documents were. |
| 3 | **Parallel SPIMI and a multi-pass external merge** | Worker processes invert batches of documents within a fixed memory budget and write sorted *run files*. A k-way heap merge with bounded fan-in (multi-pass if needed) then produces the final index. Batches cover contiguous docID ranges, so merging a term's postings is plain concatenation. |
| 4 | **Each document's position list is prefixed with its byte length (`plen`)** | Postings entries are `[d-gap, tf, plen, position-gaps…]`. The merge copies positions as opaque bytes, which is why merging 51.6M positions takes about 30 s in pure Python. Phrase and AND queries, and BM25, can skip a document's positions without decoding them. |
| 5 | **Skip pointers (every √df documents)** | Phrase, proximity and AND queries use a *leap-frog* intersection driven by the rarest term. The other cursors jump ahead through the skip table. |
| 6 | **Front-coded "dictionary as a string" lexicon** | The 1.05M-term vocabulary takes **18 MiB of RAM**, against about **284 MiB** for a Python `dict`. It loads in 0.04 s. A lookup is one binary search plus decoding one block of 16 terms. Because the terms are sorted, prefix (wildcard) queries come for free. |
| 7 | **Lexicon-guided transliteration (Roman to Devanagari)** | Typing `bharat`, `hindi` or `pradhanmantri` gives ranked Devanagari words **that actually exist in the index** (`प्रधानमंत्री`, `प्रधानमन्त्री` …). A beam search over the ambiguous readings is pruned with prefix lookups against the sorted lexicon, so *the index acts as the language model*. As a side effect it surfaces spelling variants (हिन्दी / हिंदी). Strict ITRANS is also supported. |
| 8 | **Positions you can verify** | Every document is kept (zlib) in a docstore. Any (doc, position) can be turned back into a KWIC snippet. `verify` re-tokenises sampled documents and proves that the index is **sound** (each stored position holds its term) and **complete** (no posting is missing). |
| 9 | **Language-focused web crawler** | Respects robots.txt and waits between requests to a host. A page is kept only if enough of its letters are in the target script. The frontier is best-first (it drifts towards Hindi pages). Near-duplicates are caught with **SimHash** and 4×16-bit LSH bands. |
| 10 | **Corpus analytics** | Zipf and Heaps fits. The Heaps curve is reconstructed exactly from the first docID of every term, which the merge provides for free. The report also compares VByte with raw integers and with Elias γ/δ, measures memory, and draws dependency-free SVG charts. |
| 11 | **Proximity-aware ranking** | BM25 with re-ranking by the minimum distance between query terms (Tao & Zhai, SIGIR 2007). |

---------------------------------------------------------------------------

## 2. Dataset

* **Main collection:** Hindi Wikipedia dump `hiwiki-latest-pages-articles.xml.bz2`
  (dated 01-Nov-2026, 242 MB compressed), from
  https://dumps.wikimedia.org/hiwiki/latest/. Only main-namespace articles are
  used; redirects are skipped and wiki markup is removed.
* **Crawled collection (demo):** 25 pages crawled live from hi.wikipedia.org
  with `indicindex crawl`, in `sample_data/crawl_sample.jsonl.gz`.
* **Bundled sample:** 500 articles in `sample_data/hiwiki_sample.jsonl.gz`
  (0.9 MB), so the whole pipeline can be tried in seconds.
* **FIRE collections** (TREC-style `<DOC><DOCNO>…<TEXT>…`) are supported
  as-is: give the build command a file or a directory, `.gz` files included.

**Links to the full data and the built index** (they are too large to submit):

* Wikipedia dump: https://dumps.wikimedia.org/hiwiki/latest/hiwiki-latest-pages-articles.xml.bz2
* Built index (`postings.bin`, `lexicon.*`, `docs.tsv`, `docstore.*`, `meta.json`, about 350 MB):
  **`<add your Google Drive link here>`**
* Source repository: https://github.com/sachiniiits/IR-final-assignment

Wikipedia text is © Wikipedia contributors, CC BY-SA 4.0.

---------------------------------------------------------------------------

## 3. How to run

All commands run from the `src/` directory. You can also set
`PYTHONPATH=src` and run them from the repository root.

```
cd src
python -m indicindex -h
```

### 3.1 Quick start with the bundled sample (about 10 seconds)

```
python -m indicindex build ../sample_data/hiwiki_sample.jsonl.gz -o ../index_sample
python -m indicindex verify ../index_sample
python -m indicindex term ../index_sample हिमालय
python -m indicindex repl ../index_sample
```

### 3.2 Full Hindi Wikipedia

```
wget https://dumps.wikimedia.org/hiwiki/latest/hiwiki-latest-pages-articles.xml.bz2
python -m indicindex build hiwiki-latest-pages-articles.xml.bz2 -o ../index_hiwiki --workers 4
```

Build options:

| option | meaning (default) |
|---|---|
| `--format auto/wiki/trec/jsonl/text` | input format (auto-detected) |
| `--workers N` | number of worker processes (all CPUs) |
| `--max-positions N` | SPIMI memory budget: positions held in RAM before a run is flushed (3,000,000) |
| `--batch-docs / --batch-chars` | size of the batch handed to each worker (2000 docs / 8 M chars) |
| `--fan-in F` | maximum number of runs merged at once; with more runs the merge takes several passes (64) |
| `--stopwords` | drop Hindi and English stop-words; **positions are preserved**, so phrases still work |
| `--stem` | Hindi light suffix stemmer (Ramanathan & Rao) |
| `--no-fold-nukta`, `--no-fold-chandrabindu` | turn off those normalisations |
| `--limit N` | index only the first N documents |
| `--no-docstore` | do not store document text (disables KWIC and verify) |

### 3.3 Querying

```
python -m indicindex term     IDX पार्थेनोकार्पी भारत --max-docs 5   # df, cf, positional postings, KWIC
python -m indicindex phrase   IDX "संयुक्त राष्ट्र संघ"
python -m indicindex near     IDX गंगा यमुना -w 3 [--ordered]
python -m indicindex wildcard IDX "हिमाल*"
python -m indicindex search   IDX "ताजमहल आगरा"                      # BM25 + proximity
python -m indicindex suggest  IDX bharat hindi pradhanmantri         # Roman -> Devanagari
python -m indicindex term     IDX bhArata --itrans                   # strict ITRANS input
python -m indicindex search   IDX "~tajmahal ~agra"                  # ~word = transliterate this word
python -m indicindex rare     IDX --max-df 1 --script -k 20          # list rare terms
python -m indicindex info     IDX                                    # metadata
python -m indicindex repl     IDX                                    # interactive shell
```

Syntax in the interactive shell: `"exact phrase"`, `w1 NEAR/5 w2`, `prefix*`,
`/term w`, `/and w1 w2`, `/suggest roman`, and plain words for a ranked
search. `~roman` works anywhere in a query.

### 3.4 Analysis, verification, test cases, crawling

```
python -m indicindex stats     IDX -o ../output/report     # report.md + zipf.svg + heaps.svg
python -m indicindex verify    IDX --terms 400 --docs 100   # soundness + completeness check
python -m indicindex testcases IDX -o ../output             # writes the output/ deliverable
python -m indicindex crawl "https://hi.wikipedia.org/wiki/हिमालय" -o crawl.jsonl --max-pages 100
python -m indicindex build crawl.jsonl -o ../index_crawl    # crawled pages index the same way
```

### 3.5 Unit tests

From the repository root:

```
python -m unittest discover -s tests -v
```

There are 24 tests. They cover the codec, the tokenizer and normalisation,
the wiki-markup cleaner, ITRANS and the TREC reader. The most important one
is an **oracle test**. It builds an index with a tiny memory budget and
fan-in of 3, which forces many runs, a multi-pass merge and skip pointers. It
then checks that **every** term's on-disk postings equal those of a
brute-force in-memory index. It also checks phrase, NEAR and AND results
against brute force.

### 3.6 Packaging the submission

```
./make_submission.sh <YourRollNo>
```

This creates `<YourRollNo>-A1-PostIndex-IR-M2026.zip`, which contains
README.txt, `<YourRollNo>-src.zip` and `output/`.

---------------------------------------------------------------------------

## 4. Architecture

```
                    ┌────────────────────────── main process ─────────────────────────┐
 corpus.py          │ stream RawDocs ─► batches (contiguous docIDs) ─► Pool.imap      │
 (wiki/FIRE/jsonl)  │                                                   │             │
                    │   docs.tsv, docstore.bin  ◄── doc metadata + zlib text (in order)
                    └───────────────────────────────────────────────────┼─────────────┘
                                                                        ▼
            ┌──────────── worker 1..N (spimi.process_batch) ──────────────┐
            │ wikitext.clean ─► textproc.Analyzer (normalise, tokenise)    │
            │ in-memory dict term -> [docID, [positions]] ...              │
            │ budget exceeded? ─► write sorted run file  _runs/BBBBBB_SSS.run
            └──────────────────────────────────────────────────────────────┘
                                                                        ▼
       spimi.reduce_runs: multi-pass k-way heap merge (fan-in F, preserves docID order)
                                                                        ▼
       builder: final merge ─► postings.bin (+ skip tables)  +  lexicon.bin/.blk
                               + meta.json (stats, Heaps curve, analyzer config)

 query time:  Lexicon (RAM)  ──offset,length──►  postings.bin (mmap, disk)  ──►  search.py
```

### 4.1 Modules (`src/indicindex/`)

| module | description |
|---|---|
| `textproc.py` | **Text analysis for Indic scripts.** Script-block token regex, `display_normalize` (NFC and removal of invisible characters), digit mapping, nukta and chandrabindu folding, Hindi and English stop-word lists, Hindi light stemmer. `Analyzer.analyze()` yields `(position, term)`; `Analyzer.spans()` gives exact character offsets for KWIC. |
| `wikitext.py` | **MediaWiki markup cleaner.** Removes `{{templates}}` and `{| tables |}` in one linear pass, even when nested. Resolves `[[links|labels]]` with nesting. Drops File, Category and interwiki links, using both English and Hindi namespace names (`चित्र:`, `श्रेणी:` …). Strips refs, comments, HTML and entities. |
| `corpus.py` | **Streaming corpus readers.** MediaWiki XML (`iterparse`, plain or bz2/gz), FIRE/TREC SGML (file or directory), JSONL, and a directory of `.txt` files, with format auto-detection. |
| `crawler.py` | **Language-focused crawler.** robots.txt, politeness delay, HTML text extraction without boilerplate, script-ratio language filter, best-first frontier, SHA-1 exact and SimHash/LSH near-duplicate detection. Writes JSONL. |
| `codec.py` | **Integer compression.** VByte encode and decode (bulk, single, n-at-a-time), gap helpers, Elias γ/δ bit-length estimators. |
| `spimi.py` | **SPIMI inversion and external merge.** `process_batch` is the worker; it builds in-memory postings within a memory budget and flushes run files. Also contains run-file I/O, the k-way heap merge, the multi-pass `reduce_runs`, and `concat_payloads` (which rebases d-gaps without decoding positions). |
| `index_format.py` | **On-disk layout and readers.** Postings entry builder with skip tables, the front-coded `LexiconWriter` and the in-memory `Lexicon`, `PostingList` and `PostingCursor` (memory-mapped, with skip-accelerated `advance()`), `DocTable`, `DocStore`. |
| `builder.py` | **Build orchestration.** Batches, worker pool, docstore, merge passes, final postings and lexicon, statistics and exact Heaps curve, `meta.json`. |
| `search.py` | **Query engine.** `PositionalIndex`: term lookup, phrase (positional leap-frog intersection), NEAR/k (ordered or unordered), boolean AND, wildcard, BM25 with proximity re-ranking, KWIC snippets. |
| `translit.py` | **Roman-script input.** Strict ITRANS to Devanagari with Hindi schwa handling, and the lexicon-guided beam-search `suggest()`. |
| `stats.py` | **Analytics.** Zipf and Heaps least-squares fits, compression comparison, lexicon memory measurement, SVG plots, `report.md`. |
| `verify.py` | **Self-verification.** Soundness and completeness checks of the index against the docstore. |
| `testcases.py` | **Output generator.** Picks rare terms, prints their positional postings with KWIC proof, and runs the 5 functional test cases. |
| `cli.py`, `__main__.py` | **Command line interface** and interactive shell. |

---------------------------------------------------------------------------

## 5. Index file formats

| file | where it lives at query time | content |
|---|---|---|
| `lexicon.bin` + `lexicon.blk` | **RAM** | vocabulary: front-coded blocks of 16 terms with df, cf, postings offset and length |
| `postings.bin` | **disk** (memory-mapped, read on demand) | all positional postings lists |
| `docs.tsv` | RAM (doc lengths and titles) | docID, source id, #terms, #tokens, url, title |
| `docstore.bin` + `.idx` | disk | zlib-compressed text of each document (for KWIC and verification) |
| `meta.json` | RAM | analyzer configuration, collection statistics, Heaps curve, build information |

**Postings entry for one term.** Every integer is VByte coded. Entries are
contiguous and in term order.

```
df  cf  nskip
nskip × (Δ prev_docID, Δ byte_offset)              skip table, one entry every ⌈√df⌉ docs (df ≥ 32)
df    × (d-gap, tf, plen, tf × position-gap)       plen = byte length of the position gaps
```

**Lexicon block** (K = 16 terms; terms sorted by code point, which is the same
as UTF-8 byte order):

```
head : len  term-bytes  df  cf  offset  length
rest : shared-prefix-len  suffix-len  suffix-bytes  df  cf  length   (offset = previous offset + previous length)
```

Hindi words share long prefixes (भारत, भारतीय, भारतीयों …), which is why
front coding works so well. Only the 65,882 block heads become Python
strings; everything else stays a single `bytes` blob.

**Run file record** (written during the build):
`struct '<HIIII'` (term_len, df, cf, last_doc, payload_len), followed by the
term and the payload. The payload uses the same per-document layout as
`postings.bin`.

---------------------------------------------------------------------------

## 6. Query processing

* **Positions** are 0-based token offsets inside a document; the title comes
  first. They are counted *before* stop-word removal, so removing a stop-word
  leaves a gap.
* **Phrase query.** The query is analysed with the same analyzer, which gives
  `(term, offset)` pairs. Terms are sorted by df and the rarest one drives the
  intersection. The other cursors call `advance(doc)`, which uses the skip
  table. Positions are decoded only for documents that contain *all* terms,
  and a match means `pos_i − offset_i` is the same start for every term.
* **NEAR/k.** The same intersection, followed by a linear merge of the two
  position lists. It can be ordered or unordered.
* **Ranked search.** BM25 (k1 = 1.2, b = 0.75) scores documents using only df
  and tf, with no position decoding. The top 100 candidates get
  `log(α + e^(−MinDist))`, where MinDist is the smallest distance between two
  different query terms.
* **Wildcard.** A prefix range scan over the sorted lexicon.
* **Transliteration.** Casual romanisation is split into units. Each unit has
  alternative readings: a/A, t/T, sh/Sh, n/ṇ/anusvara, and virama versus a
  hidden schwa. A beam search keeps a partial Devanagari string only if it is
  a prefix of some indexed word. The complete words left at the end are
  ranked by cf.

---------------------------------------------------------------------------

## 7. Results on the full Hindi Wikipedia

These numbers come from the 4-core cloud VM used for development.

| quantity | value |
|---|---|
| documents | 176,041 |
| tokens (positions stored) | 51,604,144 |
| vocabulary (distinct terms) | 1,054,110 (863K Devanagari, 172K Latin, 15K numbers, others in other Indic scripts) |
| postings, i.e. (term, doc) pairs | 22,468,149 |
| hapax legomena | 600,049 (56.9 % of the vocabulary) |
| build time | inversion 1 min 47 s (104 SPIMI runs, 4 workers) + merge 33 s = **2 min 20 s** |
| `postings.bin` (on disk) | **145.2 MiB**; raw 32-bit integers would be about 451 MiB (**3.1× compression**) |
| vocabulary in RAM | **18.0 MiB** front-coded (a Python dict would be about 284 MiB); loads in 0.04 s |
| Zipf exponent (ranks 10 to 10^4) | s = 1.12 (R² = 0.989) |
| Heaps' law | V = 3.44 · T^0.713 (R² = 0.999). β is high because of rich morphology, spelling variants and English/Latin terms. |
| self-verification | 163,511 positions sound, 17,176 postings complete, **0 errors** |

Query latency in a warm process (peak RSS of the whole process: 120 MiB):

| query | results | time |
|---|---:|---:|
| term lookup, rare term | 1 doc | < 0.1 ms |
| phrase "महात्मा गांधी" | 1,119 docs | 7.6 ms |
| phrase "संयुक्त राष्ट्र संघ" | 223 docs | 19.7 ms |
| phrase "भारत की राजधानी" | 159 docs | 108 ms |
| गंगा NEAR/3 यमुना | 194 docs | 3.1 ms |
| AND गंगा यमुना हिमालय | 73 docs | 2.5 ms |
| wildcard हिमाल* | 50 terms | 0.1 ms |
| BM25 + proximity "ताजमहल आगरा" | top 10 | 3.1 ms |
| transliterate "pradhanmantri" | 5 candidates | 4.3 ms |

The full analytics report, with charts, is in `output/report/report.md`.

---------------------------------------------------------------------------

## 8. Output and test cases (`output/`)

* `output/test_cases.txt`
  * **Part A**: 10 rare terms with their complete positional index, written as
    `term [df, cf] -> < docID: pos, pos; docID: pos >`. Each position comes with
    a KWIC line regenerated from the stored text, which proves the position is
    correct. The terms are 4 hapax (df = 1), 3 with df = 2 and 3 with df = 3.
  * **Part B**: 5 functional test cases with input, expected result, actual
    result and PASS/FAIL:
    1. rare-term lookup, with every position verified against the text;
    2. exact phrase query, showing how many skip pointers were used;
    3. proximity query NEAR/3;
    4. Roman-script queries (ITRANS and lexicon-guided transliteration);
    5. Unicode normalisation: nukta, chandrabindu, ZWJ, native digits.
* `output/rare_terms_index.json`: the same 10 positional postings in a
  machine-readable form.
* `output/report/`: the statistics report and the Zipf and Heaps SVG charts.

The 10 rare terms in the submitted output:

एनवीएओ, जाग्ड, पार्थेनोकार्पी, स्ट्रकचरिंग, लागणी, हाइपरजंप, कटंगुलातूर, सम्पीडक, कोम्ते, साइकेडेलिया.

---------------------------------------------------------------------------

## 9. Repository layout

```
README.md               this file (submitted as README.txt)
make_submission.sh      builds the submission zip files
src/indicindex/         source code (see section 4.1)
tests/                  unit and oracle tests
sample_data/            500-article Hindi Wikipedia sample, 25 crawled pages
output/                 test cases, rare-term positional index, statistics report
```

## 10. References

* C. D. Manning, P. Raghavan, H. Schütze. *Introduction to Information Retrieval*, ch. 2 (positional indexes, skip pointers), 4 (SPIMI, merging), 5 (compression, front coding).
* S. Heinz, J. Zobel. Efficient single-pass index construction for text databases. JASIST 2003.
* T. Tao, C. Zhai. An exploration of proximity measures in information retrieval. SIGIR 2007.
* M. Charikar. Similarity estimation techniques from rounding algorithms (SimHash). STOC 2002; G. Manku et al. Detecting near-duplicates for web crawling. WWW 2007.
* A. Ramanathan, D. Rao. A lightweight stemmer for Hindi. EACL 2003 workshop.
* FIRE: Forum for Information Retrieval Evaluation, http://fire.irsi.res.in/
