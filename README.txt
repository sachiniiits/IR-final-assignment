INDICINDEX: A POSITIONAL INVERTED INDEX FOR HINDI

IR Assignment A1 (PostIndex), M2026
Name    : Sachin Kumar
Roll No : S20240010206


OVERVIEW

IndicIndex builds a positional inverted index for an Indian-language
collection, here the complete Hindi Wikipedia (176,041 articles, 51.6 million
tokens, 1,054,110 distinct terms). Each postings list stores the positions of
the term together with its collection frequency (cf) and document frequency
(df). The vocabulary lives in main memory, in compressed form. The postings
lists live on secondary storage: the query side memory-maps them and the
build side streams them through run files.

The code is pure Python 3 (version 3.8 or newer) and uses only the standard
library, so nothing has to be installed.

Example commands:

    python -m indicindex build hiwiki-latest-pages-articles.xml.bz2 -o index/
    python -m indicindex term index/ पार्थेनोकार्पी
    python -m indicindex phrase index/ "भारत की राजधानी"
    python -m indicindex search index/ "~tajmahal ~agra"

The first builds the index (2 min 20 s on 4 cores), the second prints the
positional postings of a word, the third runs an exact phrase query and the
fourth runs a query typed in Roman letters.


CONTENTS

    1.  Novel ideas and highlights
    2.  Dataset and links to the full data and index
    3.  How to run
    4.  Architecture and description of each module
    5.  Index file formats
    6.  Query processing
    7.  Results on the full Hindi Wikipedia
    8.  Output and test cases
    9.  Folder layout
    10. References


1. NOVEL IDEAS AND HIGHLIGHTS

1.1 Script-block tokenizer for Indic text.
    Python's \w does not match matras or the virama, so the usual
    re.findall(r"\w+") breaks हिन्दी into fragments. Our tokenizer works on
    Unicode script blocks. It covers 9 Indic scripts plus Latin, and splits
    mixed-script strings at the script boundary (BJPके becomes bjp + के).

1.2 Hindi-aware normalisation, stored inside the index.
    Unicode NFC; removal of ZWJ, ZWNJ and the soft hyphen; native digits
    mapped to ASCII (१९४७ = 1947); nukta folding (ज़िला = जिला); chandrabindu
    folding (गाँधी = गांधी). The analyzer configuration is written to
    meta.json, so queries are always normalised exactly as the documents
    were.

1.3 Parallel SPIMI and a multi-pass external merge.
    Worker processes invert batches of documents within a fixed memory
    budget and write sorted run files. A k-way heap merge with bounded
    fan-in (multi-pass if needed) then produces the final index. Batches
    cover contiguous docID ranges, so merging a term's postings is plain
    concatenation.

1.4 Each document's position list is prefixed with its byte length (plen).
    A postings entry is (d-gap, tf, plen, position gaps). The merge copies
    positions as opaque bytes, which is why merging 51.6 million positions
    takes about 30 s in pure Python. Phrase queries, AND queries and BM25
    can skip a document's positions without decoding them.

1.5 Skip pointers every √df documents.
    Phrase, proximity and AND queries use a leap-frog intersection driven by
    the rarest term. The other cursors jump ahead through the skip table.

1.6 Front-coded "dictionary as a string" lexicon.
    The 1.05 million term vocabulary takes 18 MiB of RAM, against about
    284 MiB for a Python dict. It loads in 0.04 s. A lookup is one binary
    search plus decoding one block of 16 terms. Because the terms are
    sorted, prefix (wildcard) queries come for free.

1.7 Lexicon-guided transliteration from Roman letters to Devanagari.
    Typing bharat, hindi or pradhanmantri gives ranked Devanagari words that
    actually exist in the index (प्रधानमंत्री, प्रधानमन्त्री, ...). A beam
    search over the ambiguous readings is pruned with prefix lookups against
    the sorted lexicon, so the index itself acts as the language model. As a
    side effect it surfaces spelling variants (हिन्दी and हिंदी). Strict ITRANS
    input is also supported.

1.8 Positions you can verify.
    Every document is kept, zlib-compressed, in a document store. Any
    (document, position) pair can be turned back into a KWIC snippet. The
    verify command re-tokenises sampled documents and proves that the index
    is sound (each stored position holds its term) and complete (no posting
    is missing).

1.9 Language-focused web crawler.
    Respects robots.txt and waits between requests to a host. A page is
    kept only if enough of its letters are in the target script. The
    frontier is best-first, so the crawl drifts towards Hindi pages.
    Near-duplicates are caught with SimHash and 4 LSH bands of 16 bits.

1.10 Corpus analytics.
    Zipf and Heaps fits. The Heaps curve is reconstructed exactly from the
    first docID of every term, which the merge provides for free. The report
    also compares VByte with raw integers and with Elias gamma and delta
    codes, measures memory, and draws SVG charts without any libraries.

1.11 Proximity-aware ranking.
    BM25 with re-ranking by the minimum distance between query terms (Tao
    and Zhai, SIGIR 2007).


2. DATASET AND LINKS TO THE FULL DATA AND INDEX

Main collection
    Hindi Wikipedia dump hiwiki-latest-pages-articles.xml.bz2 (dated
    01-Nov-2026, 242 MB compressed) from
    https://dumps.wikimedia.org/hiwiki/latest/
    Only main-namespace articles are used; redirects are skipped and wiki
    markup is removed.

Crawled collection (demo)
    25 pages crawled live from hi.wikipedia.org with the crawl command,
    stored in sample_data/crawl_sample.jsonl.gz.

Bundled sample
    500 articles in sample_data/hiwiki_sample.jsonl.gz (0.9 MB), so the
    whole pipeline can be tried in seconds.

FIRE collections
    TREC-style files (<DOC><DOCNO>...<TEXT>...) are supported as they are:
    give the build command a file or a directory; .gz files are accepted.

Links to the full data and the built index (too large to submit):

    Wikipedia dump:
    https://dumps.wikimedia.org/hiwiki/latest/hiwiki-latest-pages-articles.xml.bz2

    Built index (postings.bin, lexicon files, docs.tsv, document store,
    meta.json):
    https://github.com/sachiniiits/IR-final-assignment/tree/index-data

    This is a separate index-data branch holding a 287 MB zip, split into 7
    parts below 50 MB because of GitHub's file-size limit. Download and
    extract it with these three commands:

        git clone --single-branch -b index-data https://github.com/sachiniiits/IR-final-assignment.git index-data
        cd index-data
        python join_index.py

    The script joins the parts, checks the SHA-256 checksums and extracts
    the folder S20240010206-hiwiki-index, which works with every indicindex
    command. The identical index can also be rebuilt from the dump with the
    build command in section 3.2 (2 to 8 minutes).

Wikipedia text is copyright Wikipedia contributors, licensed CC BY-SA 4.0.


3. HOW TO RUN

Run each command on its own line. This works the same in Windows
PowerShell, cmd, and Linux or macOS terminals. Older Windows PowerShell does
not accept "&&", so do not chain commands with it. On Windows use python (or
py); on Linux and macOS you may need python3.

First extract S20240010206-src.zip into a folder and open a terminal in
that folder. All indicindex commands run from its src subfolder.
Alternatively, set the environment variable PYTHONPATH to src and run them
from the extracted folder.

    cd src
    python -m indicindex -h

3.1 Quick start with the bundled sample (about 10 seconds)

    python -m indicindex build ../sample_data/hiwiki_sample.jsonl.gz -o ../index_sample
    python -m indicindex verify ../index_sample
    python -m indicindex term ../index_sample हिमालय
    python -m indicindex repl ../index_sample

If Hindi shows as boxes in an old Windows console, change the console font
to Nirmala UI or use Windows Terminal. The results themselves are correct.

3.2 Full Hindi Wikipedia

Download the dump, for example with curl (on Windows PowerShell type
curl.exe instead of curl):

    curl -L -o hiwiki-latest-pages-articles.xml.bz2 https://dumps.wikimedia.org/hiwiki/latest/hiwiki-latest-pages-articles.xml.bz2
    python -m indicindex build hiwiki-latest-pages-articles.xml.bz2 -o ../index_hiwiki --workers 4

Build options (default value in brackets):

    --format auto/wiki/trec/jsonl/text
        input format [auto-detected]
    --workers N
        number of worker processes [all CPUs]
    --max-positions N
        SPIMI memory budget: positions held in RAM before a run file is
        written [3,000,000]
    --batch-docs N, --batch-chars N
        size of the batch handed to each worker [2000 docs, 8 million chars]
    --fan-in F
        maximum number of runs merged at once; with more runs the merge
        takes several passes [64]
    --stopwords
        drop Hindi and English stop-words; positions are preserved, so
        phrase queries still work
    --stem
        Hindi light suffix stemmer (Ramanathan and Rao)
    --no-fold-nukta, --no-fold-chandrabindu
        turn off these normalisations
    --limit N
        index only the first N documents
    --no-docstore
        do not store document text (disables KWIC snippets and verify)

3.3 Querying

In these commands IDX is the index folder, for example ../index_hiwiki.

    python -m indicindex term IDX पार्थेनोकार्पी भारत --max-docs 5
        df, cf, positional postings and KWIC snippets for each word
    python -m indicindex phrase IDX "संयुक्त राष्ट्र संघ"
        exact phrase query
    python -m indicindex near IDX गंगा यमुना -w 3
        proximity query (add --ordered for ordered matches)
    python -m indicindex wildcard IDX "हिमाल*"
        prefix query
    python -m indicindex search IDX "ताजमहल आगरा"
        ranked search (BM25 with proximity)
    python -m indicindex suggest IDX bharat hindi pradhanmantri
        Roman letters to Devanagari candidates
    python -m indicindex term IDX bhArata --itrans
        strict ITRANS input
    python -m indicindex search IDX "~tajmahal ~agra"
        a word starting with ~ is transliterated
    python -m indicindex rare IDX --max-df 1 --script -k 20
        list rare terms
    python -m indicindex info IDX
        index metadata
    python -m indicindex repl IDX
        interactive shell

Inside the interactive shell, type one query per line:

    "exact phrase"      exact phrase query
    w1 NEAR/5 w2        proximity query
    prefix*             wildcard query
    /term w             positional postings of a word
    /and w1 w2          boolean AND
    /suggest roman      transliteration candidates
    plain words         ranked search
    /quit               leave the shell

A word written as ~roman is transliterated anywhere in a query.

3.4 Analysis, verification, test cases and crawling

    python -m indicindex stats IDX -o ../output/report
        statistics report with Zipf and Heaps charts
    python -m indicindex verify IDX --terms 400 --docs 100
        soundness and completeness check of the index
    python -m indicindex testcases IDX -o ../output
        writes the test-case output file
    python -m indicindex crawl "https://hi.wikipedia.org/wiki/हिमालय" -o crawl.jsonl --max-pages 100
        focused crawl of Hindi web pages
    python -m indicindex build crawl.jsonl -o ../index_crawl
        crawled pages are indexed the same way

3.5 Unit tests

From the extracted folder (not from src):

    python -m unittest discover -s tests -v

There are 24 tests. They cover the codec, the tokenizer and normalisation,
the wiki-markup cleaner, ITRANS and the TREC reader. The most important one
is an oracle test. It builds an index with a tiny memory budget and a
fan-in of 3, which forces many runs, a multi-pass merge and skip pointers.
It then checks that every term's on-disk postings equal those of a
brute-force in-memory index. It also checks phrase, NEAR and AND results
against brute force.

3.6 Packaging the submission

Run this from the extracted folder. It works on Windows, Linux and macOS and
needs only Python (make_submission.sh is a bash equivalent):

    python make_submission.py S20240010206

This creates S20240010206-A1-PostIndex-IR-M2026.zip with exactly the three
files the assignment asks for:

    README.txt              this document
    S20240010206-src.zip    source code, tests, sample data, and the
                            supplementary output folder (statistics report,
                            JSON copy, logs)
    output.txt              the test cases: 10 rare terms with their
                            positional index, plus 5 functional tests
                            (a copy of output/test_cases.txt)


4. ARCHITECTURE AND DESCRIPTION OF EACH MODULE

4.1 Data flow

Build time:

    (1) Main process: corpus.py streams documents (Wikipedia XML, FIRE,
        JSONL or text) and cuts them into batches with contiguous docIDs.
        The batches go to a pool of worker processes.
    (2) Each worker (spimi.process_batch): wikitext.py cleans the markup,
        textproc.Analyzer normalises and tokenises the text, and an
        in-memory dictionary term -> [docID, [positions]] is built. When the
        memory budget is exceeded, the dictionary is written to disk as a
        sorted run file.
    (3) The main process writes docs.tsv and docstore.bin in docID order
        from the metadata and compressed text returned by the workers.
    (4) spimi.reduce_runs: multi-pass k-way heap merge of the run files
        (fan-in F, preserves docID order).
    (5) builder.py: final merge into postings.bin (with skip tables) and
        lexicon.bin / lexicon.blk, plus meta.json (statistics, Heaps curve,
        analyzer configuration).

Query time:

    Lexicon in RAM -> (offset, length) -> postings.bin on disk, memory-mapped
    and read on demand -> search.py

4.2 Modules (folder src/indicindex)

textproc.py
    Text analysis for Indic scripts. Script-block token regex,
    display_normalize (NFC and removal of invisible characters), digit
    mapping, nukta and chandrabindu folding, Hindi and English stop-word
    lists, Hindi light stemmer. Analyzer.analyze() yields (position, term)
    pairs; Analyzer.spans() gives exact character offsets for KWIC.

wikitext.py
    MediaWiki markup cleaner. Removes templates {{...}} and tables {|...|}
    in one linear pass, even when nested. Resolves [[link]] and
    [[link|label]] with nesting. Drops File, Category and interwiki links,
    using both English and Hindi namespace names (चित्र:, श्रेणी:, ...).
    Strips references, comments, HTML tags and entities.

corpus.py
    Streaming corpus readers: MediaWiki XML (iterparse, plain or bz2/gz),
    FIRE/TREC SGML (file or directory), JSONL, and a folder of .txt files,
    with automatic format detection.

crawler.py
    Language-focused web crawler: robots.txt, politeness delay, HTML text
    extraction without boilerplate, script-ratio language filter,
    best-first frontier, exact (SHA-1) and near-duplicate (SimHash with LSH)
    detection. Writes JSONL.

codec.py
    Integer compression: VByte encoding and decoding (bulk, single,
    n-at-a-time), gap helpers, Elias gamma and delta bit-length estimators.

spimi.py
    SPIMI inversion and external merge. process_batch is the worker; it
    builds in-memory postings within a memory budget and writes run files.
    Also contains run-file I/O, the k-way heap merge, the multi-pass
    reduce_runs, and concat_payloads (which rebases d-gaps without decoding
    positions).

index_format.py
    On-disk layout and readers: postings entry builder with skip tables,
    the front-coded LexiconWriter and the in-memory Lexicon, PostingList and
    PostingCursor (memory-mapped, with skip-accelerated advance()),
    DocTable and DocStore.

builder.py
    Build orchestration: batches, worker pool, document store, merge
    passes, final postings and lexicon, statistics, exact Heaps curve,
    meta.json.

search.py
    Query engine. PositionalIndex offers term lookup, phrase queries
    (positional leap-frog intersection), NEAR/k (ordered or unordered),
    boolean AND, wildcard, BM25 with proximity re-ranking, and KWIC
    snippets.

translit.py
    Roman-letter input: strict ITRANS to Devanagari with Hindi schwa
    handling, and the lexicon-guided beam-search suggest().

stats.py
    Analytics: Zipf and Heaps least-squares fits, compression comparison,
    lexicon memory measurement, SVG plots, report.md.

verify.py
    Self-verification: soundness and completeness checks of the index
    against the document store.

testcases.py
    Output generator: picks rare terms, prints their positional postings
    with KWIC proof, and runs the 5 functional test cases.

cli.py and __main__.py
    Command line interface and interactive shell.


5. INDEX FILE FORMATS

Files of an index folder:

    lexicon.bin, lexicon.blk   in RAM    vocabulary: front-coded blocks of
                                         16 terms with df, cf, postings
                                         offset and length
    postings.bin               on disk   all positional postings lists
                                         (memory-mapped, read on demand)
    docs.tsv                   in RAM    docID, source id, number of terms,
                                         number of tokens, URL, title
    docstore.bin, .idx         on disk   zlib-compressed text of each
                                         document (KWIC and verification)
    meta.json                  in RAM    analyzer configuration, collection
                                         statistics, Heaps curve, build info

Postings entry of one term. Every integer is VByte coded. Entries are
contiguous and in term order.

    df, cf, nskip
    nskip times (change in prev_docID, change in byte_offset)
        the skip table: one entry every ceil(√df) documents, only when
        df >= 32
    df times (d-gap, tf, plen, tf position gaps)
        plen is the byte length of the position gaps

Lexicon block. A block holds K = 16 terms, sorted by code point (the same
as UTF-8 byte order).

    first term:   length, term bytes, df, cf, offset, length
    other terms:  shared prefix length, suffix length, suffix bytes, df,
                  cf, length (offset = previous offset + previous length)

Hindi words share long prefixes (भारत, भारतीय, भारतीयों, ...), which is why
front coding works so well. Only the 65,882 block heads become Python
strings; everything else stays a single bytes object.

Run file record (written during the build): a fixed header (term length,
df, cf, last docID, payload length), followed by the term and the payload.
The payload uses the same per-document layout as postings.bin.


6. QUERY PROCESSING

Positions
    0-based token offsets inside a document; the title comes first. They
    are counted before stop-word removal, so removing a stop-word leaves a
    gap.

Phrase query
    The query is analysed with the same analyzer, which gives (term,
    offset) pairs. Terms are sorted by df and the rarest one drives the
    intersection. The other cursors call advance(doc), which uses the skip
    table. Positions are decoded only for documents that contain all
    terms, and a match means that position minus offset gives the same
    start for every term.

NEAR/k
    The same intersection, followed by a linear merge of the two position
    lists. It can be ordered or unordered.

Ranked search
    BM25 (k1 = 1.2, b = 0.75) scores documents using only df and tf,
    without decoding positions. The top 100 candidates get a bonus of
    log(alpha + e^(-MinDist)), where MinDist is the smallest distance
    between two different query terms.

Wildcard
    A prefix range scan over the sorted lexicon.

Transliteration
    Casual romanisation is split into units. Each unit has alternative
    readings: a/A, t/T, sh/Sh, n/ṇ/anusvara, and virama versus a hidden
    schwa. A beam search keeps a partial Devanagari string only if it is a
    prefix of some indexed word. The complete words left at the end are
    ranked by cf.


7. RESULTS ON THE FULL HINDI WIKIPEDIA

These numbers come from the 4-core cloud VM used for development. The same
index was also built on a Windows PC with 12 workers (7 min 26 s) and gave
identical counts and sizes.

    Documents                      176,041
    Tokens (positions stored)      51,604,144
    Vocabulary (distinct terms)    1,054,110 (863K Devanagari, 172K Latin,
                                   15K numbers, the rest in other Indic
                                   scripts)
    Postings, (term, doc) pairs    22,468,149
    Hapax legomena                 600,049 (56.9 percent of the vocabulary)
    Build time                     inversion 1 min 47 s (104 SPIMI runs,
                                   4 workers) + merge 33 s = 2 min 20 s
    postings.bin on disk           145.2 MiB; raw 32-bit integers would
                                   take about 451 MiB (3.1x compression)
    Vocabulary in RAM              18.0 MiB front-coded (a Python dict
                                   would take about 284 MiB); loads in
                                   0.04 s
    Zipf exponent                  s = 1.12 for ranks 10 to 10,000
                                   (R squared = 0.989)
    Heaps' law                     V = 3.44 x T^0.713 (R squared = 0.999).
                                   Beta is high because of rich
                                   morphology, spelling variants and
                                   English terms.
    Self-verification              163,511 positions sound, 17,176
                                   postings complete, 0 errors

Query latency in a warm process (peak memory of the whole process:
120 MiB):

    Term lookup, rare term                       1 doc          < 0.1 ms
    Phrase "महात्मा गांधी"                         1,119 docs     7.6 ms
    Phrase "संयुक्त राष्ट्र संघ"                     223 docs       19.7 ms
    Phrase "भारत की राजधानी"                      159 docs       108 ms
    गंगा NEAR/3 यमुना                             194 docs       3.1 ms
    AND गंगा यमुना हिमालय                          73 docs        2.5 ms
    Wildcard हिमाल*                              50 terms       0.1 ms
    BM25 with proximity "ताजमहल आगरा"            top 10         3.1 ms
    Transliterate "pradhanmantri"               5 candidates   4.3 ms

The full analytics report with charts is in output/report/report.md
(inside the source zip).


8. OUTPUT AND TEST CASES

output.txt (in the submission; the same file as output/test_cases.txt)

    Part A: 10 rare terms with their complete positional index, written as
    term [df, cf] -> < docID: pos, pos; docID: pos >. Each position comes
    with a KWIC line regenerated from the stored text, which proves that
    the position is correct. The terms are 4 hapax legomena (df = 1),
    3 terms with df = 2 and 3 terms with df = 3.

    Part B: 5 functional test cases, each with input, expected result,
    actual result and PASS or FAIL:
        (1) rare-term lookup, with every position verified against the text
        (2) exact phrase query, showing how many skip pointers were used
        (3) proximity query NEAR/3
        (4) Roman-letter queries (ITRANS and lexicon-guided
            transliteration)
        (5) Unicode normalisation: nukta, chandrabindu, ZWJ, native digits

Supplementary files in the output folder (inside the source zip):

    rare_terms_index.json      the same 10 positional postings in a
                               machine-readable form
    report                     statistics report, Zipf and Heaps charts
    build_log.txt              log of the full build
    verify_log.txt             log of the self-verification

The 10 rare terms in the submitted output:

    एनवीएओ, जाग्ड, पार्थेनोकार्पी, स्ट्रकचरिंग, लागणी, हाइपरजंप, कटंगुलातूर,
    सम्पीडक, कोम्ते, साइकेडेलिया


9. FOLDER LAYOUT

    README.txt              this file
    make_submission.py      builds the submission zip (any OS)
    make_submission.sh      bash version of the same
    src/indicindex          source code (see section 4.2)
    tests                   unit and oracle tests
    sample_data             500-article Hindi Wikipedia sample and 25
                            crawled pages
    output                  test cases, rare-term positional index,
                            statistics report, logs


10. REFERENCES

    C. D. Manning, P. Raghavan, H. Schütze. Introduction to Information
    Retrieval. Chapter 2 (positional indexes, skip pointers), chapter 4
    (SPIMI, merging), chapter 5 (compression, front coding).

    S. Heinz, J. Zobel. Efficient single-pass index construction for text
    databases. JASIST 2003.

    T. Tao, C. Zhai. An exploration of proximity measures in information
    retrieval. SIGIR 2007.

    M. Charikar. Similarity estimation techniques from rounding algorithms
    (SimHash). STOC 2002.

    G. Manku, A. Jain, A. Das Sarma. Detecting near-duplicates for web
    crawling. WWW 2007.

    A. Ramanathan, D. Rao. A lightweight stemmer for Hindi. EACL 2003
    workshop.

    FIRE: Forum for Information Retrieval Evaluation.
    http://fire.irsi.res.in/
