# Built positional index: full Hindi Wikipedia

This is the data branch for the IR assignment A1 (PostIndex, M2026) of
Sachin Kumar, roll number S20240010206.
The source code is on the [`main` branch](https://github.com/sachiniiits/IR-final-assignment/tree/main).

| | |
|---|---|
| Collection | `hiwiki-latest-pages-articles.xml.bz2` (Hindi Wikipedia dump of 01-Nov-2026) |
| Documents / tokens / vocabulary | 176,041 / 51,604,144 / 1,054,110 terms |
| Archive | `S20240010206-hiwiki-index.zip` (300,868,581 bytes), split into 7 parts below 50 MB because GitHub limits file size |

## Contents of the extracted folder `S20240010206-hiwiki-index/`

| file | size | role |
|---|---:|---|
| `postings.bin` | 145.2 MiB | all positional postings lists (VByte gaps and skip pointers); stays on disk and is read on demand |
| `lexicon.bin` + `lexicon.blk` | 12.6 MiB | front-coded vocabulary with df, cf and postings offsets; loaded into RAM |
| `docs.tsv` | 20 MiB | docID, Wikipedia page id, document length, URL, title |
| `docstore.bin` + `docstore.idx` | 174 MiB | compressed document text, used for KWIC snippets and verification |
| `meta.json` | 8 KiB | analyzer configuration, collection statistics, Heaps curve |

## Download and use

Fetch only this branch, without the source code:

```
git clone --single-branch -b index-data https://github.com/sachiniiits/IR-final-assignment.git index-data
cd index-data
python join_index.py
```

You can also download the 7 `.part` files, `SHA256SUMS.txt` and
`join_index.py` from the GitHub web page, put them in one folder, and run
`python join_index.py`.

Or join the parts by hand:

```
copy /b S20240010206-hiwiki-index.zip.part00+S20240010206-hiwiki-index.zip.part01+S20240010206-hiwiki-index.zip.part02+S20240010206-hiwiki-index.zip.part03+S20240010206-hiwiki-index.zip.part04+S20240010206-hiwiki-index.zip.part05+S20240010206-hiwiki-index.zip.part06 S20240010206-hiwiki-index.zip
```

That is the Windows cmd command. On Linux or macOS use:

```
cat S20240010206-hiwiki-index.zip.part* > S20240010206-hiwiki-index.zip
```

Then query the index with the code from the `main` branch, run from its `src/` folder:

```
python -m indicindex term   path/to/S20240010206-hiwiki-index पार्थेनोकार्पी
python -m indicindex phrase path/to/S20240010206-hiwiki-index "भारत की राजधानी"
python -m indicindex verify path/to/S20240010206-hiwiki-index
python -m indicindex repl   path/to/S20240010206-hiwiki-index
```

To rebuild the same index from the dump instead (about 2 to 8 minutes):
`python -m indicindex build hiwiki-latest-pages-articles.xml.bz2 -o index_hiwiki`.
