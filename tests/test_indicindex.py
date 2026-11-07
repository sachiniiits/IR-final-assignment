"""
Unit + oracle tests.   Run from the repository root:

    python -m unittest discover -s tests -v

The central "oracle" test builds an index with a deliberately tiny SPIMI
memory budget and fan-in (forcing many runs and a multi-pass merge, and
lists long enough to carry skip pointers) and checks that EVERY term's
on-disk positional postings equal those of a brute-force in-memory index.
"""

import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from indicindex.builder import build_index                       # noqa: E402
from indicindex.codec import (vbyte_encode, vbyte_decode, to_gaps,  # noqa: E402
                              from_gaps, gamma_bits, delta_bits)
from indicindex.corpus import read_trec, detect_format            # noqa: E402
from indicindex.search import PositionalIndex                     # noqa: E402
from indicindex.textproc import Analyzer, AnalyzerConfig, hindi_light_stem  # noqa: E402
from indicindex.translit import itrans_to_devanagari, suggest     # noqa: E402
from indicindex.wikitext import clean_wikitext                    # noqa: E402

WORDS = ("भारत हिन्दी भाषा गंगा यमुना नदी पर्वत हिमालय राजधानी दिल्ली मुंबई "
         "इतिहास संस्कृति विज्ञान गणित कविता लेखक राजा रानी युद्ध शांति "
         "के का की में है और से को पर ने भी").split()
EXTRA = ["ज़िला", "गाँधी", "computer", "Science", "१९४७", "2024", "BJPके"]


def make_corpus(path, n_docs=400, seed=1):
    rnd = random.Random(seed)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_docs):
            n = rnd.randint(5, 120)
            words = [rnd.choice(WORDS) for _ in range(n)]
            if rnd.random() < 0.3:
                words.insert(rnd.randrange(len(words) + 1), rnd.choice(EXTRA))
            if i % 7 == 0:                      # a guaranteed phrase
                words[0:0] = ["भारत", "की", "राजधानी", "दिल्ली"]
            text = " ".join(words) + "। अंत"
            f.write(json.dumps({"id": f"d{i}", "title": f"लेख {i}", "text": text},
                               ensure_ascii=False) + "\n")


class TestCodec(unittest.TestCase):
    def test_vbyte_roundtrip(self):
        rnd = random.Random(3)
        nums = [0, 1, 127, 128, 255, 16383, 16384, 2**21, 2**32, 2**40]
        nums += [rnd.randrange(2**rnd.randrange(1, 35)) for _ in range(5000)]
        self.assertEqual(vbyte_decode(vbyte_encode(nums)), nums)
        self.assertEqual(len(vbyte_encode([127])), 1)
        self.assertEqual(len(vbyte_encode([128])), 2)

    def test_gaps(self):
        v = [3, 7, 8, 100, 101]
        self.assertEqual(from_gaps(to_gaps(v)), v)

    def test_elias_estimates(self):
        self.assertEqual(gamma_bits(0), 1)        # gamma(1) = "1"
        self.assertEqual(gamma_bits(4), 5)        # gamma(5) = 00101
        self.assertEqual(delta_bits(0), 1)


class TestText(unittest.TestCase):
    def setUp(self):
        self.a = Analyzer()

    def test_devanagari_word_not_split(self):
        self.assertEqual(self.a.tokens("हिन्दी भाषा"), ["हिन्दी", "भाषा"])

    def test_script_boundary_and_punct(self):
        self.assertEqual(self.a.tokens("BJPके नेता। (2024)"), ["bjp", "के", "नेता", "2024"])

    def test_native_digits(self):
        self.assertEqual(self.a.tokens("१९४७ ২০২৪"), ["1947", "2024"])

    def test_nukta_chandrabindu_zwj(self):
        self.assertEqual(self.a.tokens("ज़िला"), self.a.tokens("जिला"))
        self.assertEqual(self.a.tokens("गाँधी"), self.a.tokens("गांधी"))
        self.assertEqual(self.a.tokens("क्‍ष"), self.a.tokens("क्ष"))
        # precomposed U+0958 (क़) == क + nukta
        self.assertEqual(self.a.tokens("क़"), self.a.tokens("क़"))

    def test_positions_survive_stopword_removal(self):
        a = Analyzer(AnalyzerConfig(remove_stopwords=True))
        self.assertEqual(list(a.analyze("भारत की राजधानी")), [(0, "भारत"), (2, "राजधानी")])

    def test_spans_match_analyze(self):
        text = "ज़िला‍ गाँधी १९४७ abc"
        from indicindex.textproc import display_normalize
        t = display_normalize(text)
        spans = list(self.a.spans(t))
        toks = self.a.tokens(t)
        self.assertEqual([self.a.normalize_term(t[s:e]) for _p, s, e in spans], toks)

    def test_stemmer(self):
        self.assertEqual(hindi_light_stem("लड़कियों"), "लड़क")
        self.assertEqual(hindi_light_stem("किताबें"), "किताब")


class TestWikitext(unittest.TestCase):
    def test_clean(self):
        raw = ("{{Infobox|a={{b|c}}}}'''भारत''' [[दक्षिण एशिया|एशिया]] में "
               "[[चित्र:x.jpg|thumb|cap [[y]]]]स्थित<ref>r</ref> है। [[श्रेणी:देश]] [[en:India]]")
        self.assertEqual(clean_wikitext(raw), "भारत एशिया में स्थित है।")

    def test_table_removed(self):
        self.assertEqual(clean_wikitext("क {| class=x\n|a||b\n|} ख").split(), ["क", "ख"])


class TestTranslit(unittest.TestCase):
    def test_itrans(self):
        self.assertEqual(itrans_to_devanagari("bhArata"), "भारत")
        self.assertEqual(itrans_to_devanagari("hindI"), "हिन्दी")
        self.assertEqual(itrans_to_devanagari("rAShTrapati"), "राष्ट्रपति")
        self.assertEqual(itrans_to_devanagari("kRRiShNa"), "कृष्ण")


class TestTrecReader(unittest.TestCase):
    def test_trec(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "fire.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("<DOC>\n<DOCNO>1040101_nation_1</DOCNO>\n<TEXT>भारत एक देश है।</TEXT>\n</DOC>\n"
                        "<DOC><DOCNO>X2</DOCNO><TITLE>शीर्षक</TITLE><TEXT>दूसरा</TEXT></DOC>")
            docs = list(read_trec(p))
            self.assertEqual(detect_format(p), "trec")
            self.assertEqual([x.source_id for x in docs], ["1040101_nation_1", "X2"])
            self.assertEqual(docs[1].title, "शीर्षक")


class TestIndexOracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        corpus = os.path.join(cls.tmp.name, "c.jsonl")
        make_corpus(corpus)
        cls.idx_dir = os.path.join(cls.tmp.name, "idx")
        build_index(corpus, cls.idx_dir, workers=2, batch_docs=37, max_positions=500,
                    fan_in=3, log=lambda *a: None)
        cls.idx = PositionalIndex(cls.idx_dir)
        # brute-force oracle
        an = Analyzer()
        cls.oracle = {}
        cls.texts = []
        with open(corpus, encoding="utf-8") as f:
            for d, line in enumerate(f):
                o = json.loads(line)
                from indicindex.textproc import display_normalize
                text = display_normalize(o["title"] + "\n" + o["text"])
                cls.texts.append(text)
                for p, t in an.analyze(text):
                    cls.oracle.setdefault(t, {}).setdefault(d, []).append(p)

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def test_vocabulary_equal(self):
        self.assertEqual(sorted(t for t, _ in self.idx.lexicon.items()), sorted(self.oracle))

    def test_all_postings_equal(self):
        for term, docs in self.oracle.items():
            r = self.idx.term(term, normalize=False)
            self.assertIsNotNone(r, term)
            _t, info, plist = r
            self.assertEqual(plist, sorted(docs.items()), term)
            self.assertEqual(info.df, len(docs))
            self.assertEqual(info.cf, sum(len(v) for v in docs.values()))

    def test_skip_pointers_used_and_correct(self):
        term = max(self.oracle, key=lambda t: len(self.oracle[t]))
        pl = self.idx.plist(term)
        self.assertTrue(pl.skip_docs, "frequent term should have skip pointers")
        docs = sorted(self.oracle[term])
        rnd = random.Random(5)
        used = 0
        for _ in range(300):
            target = rnd.randrange(0, len(self.texts) + 5)
            c = pl.cursor()
            ok = c.advance(target)
            exp = next((d for d in docs if d >= target), None)
            self.assertEqual(c.doc if ok else None, exp)
            if ok:
                self.assertEqual(c.positions(), self.oracle[term][exp])
            used += c.skips_used
        self.assertGreater(used, 0)

    def test_phrase_matches_bruteforce(self):
        q = ["भारत", "की", "राजधानी"]
        hits = {h.docid: h.positions for h in self.idx.phrase(" ".join(q))}
        exp = {}
        for t in set(self.oracle["भारत"]):
            starts = [p for p in self.oracle["भारत"][t]
                      if all(p + k in self.oracle[w].get(t, []) for k, w in enumerate(q))]
            if starts:
                exp[t] = starts
        self.assertEqual(hits, exp)
        self.assertGreater(len(hits), 0)

    def test_near(self):
        hits = {h.docid for h in self.idx.near("गंगा", "यमुना", k=2)}
        exp = set()
        for d in set(self.oracle["गंगा"]) & set(self.oracle["यमुना"]):
            if any(abs(a - b) <= 2 for a in self.oracle["गंगा"][d] for b in self.oracle["यमुना"][d]):
                exp.add(d)
        self.assertEqual(hits, exp)

    def test_conjunctive(self):
        exp = sorted(set(self.oracle["गंगा"]) & set(self.oracle["हिमालय"]) & set(self.oracle["कविता"]))
        self.assertEqual(self.idx.conjunctive(["गंगा", "हिमालय", "कविता"]), exp)

    def test_kwic_and_surface(self):
        d, pos = next(iter(self.oracle["राजधानी"].items()))
        self.assertEqual(self.idx.surface(d, pos[0]), "राजधानी")
        self.assertIn("[राजधानी]", self.idx.kwic(d, pos)[0])

    def test_normalised_lookup(self):
        self.assertEqual(self.idx.term("जिला")[0], self.idx.term("ज़िला")[0])

    def test_wildcard_and_suggest(self):
        self.assertIn("हिमालय", [t for t, _ in self.idx.wildcard("हिमा*")])
        self.assertEqual(suggest("bharat", self.idx.lexicon, k=1)[0][0], "भारत")

    def test_verify(self):
        from indicindex.verify import verify
        self.assertTrue(verify(self.idx_dir, 50, 20, log=lambda *a: None))


if __name__ == "__main__":
    unittest.main()
