BUILT POSITIONAL INDEX: FULL HINDI WIKIPEDIA

IR Assignment A1 (PostIndex), M2026 - Sachin Kumar, S20240010206

Collection : hiwiki-latest-pages-articles.xml.bz2 (Hindi Wikipedia dump of
             01-Nov-2026)
Size       : 176,041 documents, 51,604,144 tokens, 1,054,110 terms
Archive    : S20240010206-hiwiki-index.zip (300,868,581 bytes), split into
             7 parts below 50 MB because of GitHub's file-size limit


EXTRACTING THE INDEX

Run this inside this folder:

    python join_index.py

The script joins the 7 parts, checks the SHA-256 checksums in
SHA256SUMS.txt and extracts the folder S20240010206-hiwiki-index.

Alternatively, join the parts by hand and unzip the result.

On Windows (cmd):

    copy /b S20240010206-hiwiki-index.zip.part00+S20240010206-hiwiki-index.zip.part01+S20240010206-hiwiki-index.zip.part02+S20240010206-hiwiki-index.zip.part03+S20240010206-hiwiki-index.zip.part04+S20240010206-hiwiki-index.zip.part05+S20240010206-hiwiki-index.zip.part06 S20240010206-hiwiki-index.zip

On Linux or macOS:

    cat S20240010206-hiwiki-index.zip.part* > S20240010206-hiwiki-index.zip


CONTENTS OF THE EXTRACTED FOLDER

    postings.bin            145.2 MiB   all positional postings lists (VByte
                                        gaps and skip pointers); stays on
                                        disk, read on demand
    lexicon.bin, .blk       12.6 MiB    front-coded vocabulary with df, cf
                                        and postings offsets; loaded into RAM
    docs.tsv                20 MiB      docID, Wikipedia page id, length,
                                        URL, title
    docstore.bin, .idx      174 MiB     compressed document text (KWIC
                                        snippets and verification)
    meta.json               8 KiB       analyzer configuration, collection
                                        statistics, Heaps curve


USING THE INDEX

From the src folder of the repository:

    python -m indicindex term ../built_index/S20240010206-hiwiki-index पार्थेनोकार्पी
    python -m indicindex phrase ../built_index/S20240010206-hiwiki-index "भारत की राजधानी"
    python -m indicindex verify ../built_index/S20240010206-hiwiki-index
    python -m indicindex repl ../built_index/S20240010206-hiwiki-index

The same index can also be rebuilt from the dump with:

    python -m indicindex build hiwiki-latest-pages-articles.xml.bz2 -o ../index_hiwiki
