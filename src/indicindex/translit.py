"""
translit.py -- type Hindi queries on an English keyboard.

Two modes
---------
1. ``itrans_to_devanagari("bhArata")  -> "भारत"``
   Deterministic, case-sensitive ITRANS (the standard used by FIRE topics and
   Sanskrit/Hindi e-texts).  Hindi-friendly schwa handling: a consonant at the
   end of a word does NOT get a virama, so "bhArat" also gives "भारत".

2. ``suggest("bharat", lexicon)  -> [("भारत", info), ("भरत", info), ...]``
   *Lexicon-guided transliteration* for casual, ambiguous romanisation.
   Casual Roman Hindi is massively ambiguous ("a" = अ/आ, "t" = त/ट,
   "sh" = श/ष, "n" = न/ण/ं, an implicit schwa may or may not hide a
   conjunct ...).  We enumerate the readings with a beam search in which
   every partial Devanagari string is checked against the **sorted,
   front-coded lexicon** (a prefix lookup = one binary search + one block
   decode).  Branches that are not a prefix of any indexed word are pruned
   immediately, so the search space collapses from exponential to a handful
   of states, and the surviving complete words are ranked by collection
   frequency.  The index itself acts as the language model.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

VIRAMA = "्"
ANUSVARA = "ं"

# ---------------------------------------------------------------------------
# Strict ITRANS tables
# ---------------------------------------------------------------------------
VOWELS = {  # itrans: (independent, matra)
    "a": ("अ", ""), "aa": ("आ", "ा"), "A": ("आ", "ा"),
    "i": ("इ", "ि"), "ii": ("ई", "ी"), "I": ("ई", "ी"), "ee": ("ई", "ी"),
    "u": ("उ", "ु"), "uu": ("ऊ", "ू"), "U": ("ऊ", "ू"), "oo": ("ऊ", "ू"),
    "RRi": ("ऋ", "ृ"), "R^i": ("ऋ", "ृ"),
    "e": ("ए", "े"), "ai": ("ऐ", "ै"), "o": ("ओ", "ो"), "au": ("औ", "ौ"),
    "AUM": ("ॐ", "ॐ"), "OM": ("ॐ", "ॐ"),
}
CONSONANTS = {
    "k": "क", "kh": "ख", "g": "ग", "gh": "घ", "~N": "ङ", "N^": "ङ",
    "ch": "च", "c": "च", "Ch": "छ", "chh": "छ", "j": "ज", "jh": "झ",
    "~n": "ञ", "JN": "ञ",
    "T": "ट", "Th": "ठ", "D": "ड", "Dh": "ढ", "N": "ण",
    "t": "त", "th": "थ", "d": "द", "dh": "ध", "n": "न",
    "p": "प", "ph": "फ", "b": "ब", "bh": "भ", "m": "म",
    "y": "य", "r": "र", "l": "ल", "v": "व", "w": "व",
    "sh": "श", "Sh": "ष", "shh": "ष", "s": "स", "h": "ह",
    "x": "क्ष", "kSh": "क्ष", "GY": "ज्ञ", "j~n": "ज्ञ", "dny": "ज्ञ",
    "q": "क़", "K": "ख़", "G": "ग़", "z": "ज़", "f": "फ़", ".D": "ड़", ".Dh": "ढ़",
}
SIGNS = {"M": "ं", ".n": "ं", ".N": "ँ", "H": "ः", ".a": "ऽ", ".h": VIRAMA, "|": "।", "||": "॥"}

_ALL = sorted(set(VOWELS) | set(CONSONANTS) | set(SIGNS), key=len, reverse=True)


def itrans_to_devanagari(text: str, schwa_deletion: bool = True) -> str:
    out: List[str] = []
    i = 0
    prev_cons = False
    n = len(text)
    while i < n:
        for key in _ALL:
            if text.startswith(key, i):
                break
        else:
            key = None
        if key is None:
            if prev_cons and not schwa_deletion:
                out.append(VIRAMA)
            out.append(text[i])
            prev_cons = False
            i += 1
            continue
        if key in CONSONANTS:
            if prev_cons:
                out.append(VIRAMA)
            out.append(CONSONANTS[key])
            prev_cons = True
        elif key in VOWELS:
            ind, mat = VOWELS[key]
            out.append(mat if prev_cons else ind)
            prev_cons = False
        else:
            out.append(SIGNS[key])
            prev_cons = False
        i += len(key)
    if prev_cons and not schwa_deletion:
        out.append(VIRAMA)
    return "".join(out)


# ---------------------------------------------------------------------------
# Loose romanisation -> phoneme alternatives
#   each unit maps to a list of alternatives; an alternative is a tuple of
#   phonemes ("C", consonant) | ("V", vowel-key) | ("N", anusvara)
#   The first alternative is the "expected" one (cost 0), others cost 1.
# ---------------------------------------------------------------------------
_V = lambda k: ("V", k)
_C = lambda c: ("C", c)
_NAS = ("N", ANUSVARA)

LOOSE: Dict[str, List[tuple]] = {
    # vowels
    "a": [(_V("a"),), (_V("aa"),)], "aa": [(_V("aa"),)], "i": [(_V("i"),), (_V("ii"),)],
    "ee": [(_V("ii"),)], "ii": [(_V("ii"),)], "u": [(_V("u"),), (_V("uu"),)],
    "oo": [(_V("uu"),)], "uu": [(_V("uu"),)], "e": [(_V("e"),), (_V("ai"),)],
    "ai": [(_V("ai"),), (_V("e"),)], "ae": [(_V("ai"),)], "o": [(_V("o"),), (_V("au"),)],
    "au": [(_V("au"),)], "ou": [(_V("au"),)],
    "ri": [(_C("र"), _V("i")), (_V("RRi"),), (_C("र"), _V("ii"))],
    # consonants
    "k": [(_C("क"),)], "kh": [(_C("ख"),)], "g": [(_C("ग"),)], "gh": [(_C("घ"),)],
    "c": [(_C("च"),), (_C("क"),)], "ch": [(_C("च"),), (_C("छ"),)], "chh": [(_C("छ"),)],
    "j": [(_C("ज"),)], "jh": [(_C("झ"),)], "z": [(_C("ज"),)],
    "t": [(_C("त"),), (_C("ट"),)], "th": [(_C("थ"),), (_C("ठ"),)],
    "d": [(_C("द"),), (_C("ड"),)], "dh": [(_C("ध"),), (_C("ढ"),)],
    "n": [(_C("न"),), (_NAS,), (_C("ण"),)], "m": [(_C("म"),), (_NAS,)],
    "p": [(_C("प"),)], "ph": [(_C("फ"),)], "f": [(_C("फ"),)],
    "b": [(_C("ब"),)], "bh": [(_C("भ"),)], "y": [(_C("य"),)], "r": [(_C("र"),)],
    "l": [(_C("ल"),)], "v": [(_C("व"),)], "w": [(_C("व"),)],
    "sh": [(_C("श"),), (_C("ष"),)], "s": [(_C("स"),), (_C("श"),)], "h": [(_C("ह"),)],
    "x": [(_C("क"), _C("ष")), (_C("क"), _C("स"))], "ksh": [(_C("क"), _C("ष"))],
    "q": [(_C("क"),)], "gy": [(_C("ज"), _C("ञ")), (_C("ग"), _C("य"))],
}
_LOOSE_KEYS = sorted(LOOSE, key=len, reverse=True)


def _segment(word: str) -> Optional[List[str]]:
    units = []
    i = 0
    while i < len(word):
        for k in _LOOSE_KEYS:
            if word.startswith(k, i):
                units.append(k)
                i += len(k)
                break
        else:
            return None
    return units


def _expand(state: Tuple[str, str], ph: tuple) -> List[Tuple[str, str, int]]:
    """Apply one phoneme to (devanagari_so_far, last_type).  Returns a list of
    (new_string, new_last_type, extra_cost)."""
    s, last = state
    kind, val = ph
    if kind == "C":
        if last == "C":   # conjunct (virama) vs. hidden schwa
            return [(s + VIRAMA + val, "C", 0), (s + val, "C", 1)]
        return [(s + val, "C", 0)]
    if kind == "V":
        ind, mat = VOWELS[val]
        if last == "C":
            return [(s + mat, "V", 0)]
        return [(s + ind, "V", 0)]
    # anusvara: needs a preceding vowel or a consonant (inherent a)
    if last in ("V", "C"):
        return [(s + ANUSVARA, "N", 0)]
    return []


def suggest(roman: str, lexicon, k: int = 5, beam: int = 64) -> List[Tuple[str, object]]:
    """Rank Devanagari words in the index that match a casual romanisation."""
    word = roman.strip().lower()
    units = _segment(word)
    if not units:
        return []
    cache: Dict[str, bool] = {}

    def alive(s: str) -> bool:
        if s not in cache:
            cache[s] = lexicon.has_prefix(s)
        return cache[s]

    states: Dict[Tuple[str, str], int] = {("", ""): 0}
    for u in units:
        new: Dict[Tuple[str, str], int] = {}
        for (s, last), cost in states.items():
            for ai, alt in enumerate(LOOSE[u]):
                partial = [(s, last, cost + (1 if ai else 0))]
                for ph in alt:
                    nxt = []
                    for ps, pl, pc in partial:
                        for ns, nl, c in _expand((ps, pl), ph):
                            nxt.append((ns, nl, pc + c))
                    partial = nxt
                for ns, nl, c in partial:
                    if ns and alive(ns):
                        key = (ns, nl)
                        if key not in new or new[key] > c:
                            new[key] = c
        if not new:
            return []
        states = dict(sorted(new.items(), key=lambda x: x[1])[:beam])
    results = {}
    for (s, _last), cost in states.items():
        info = lexicon.get(s)
        if info is not None:
            results[s] = (info, cost)
    ranked = sorted(results.items(), key=lambda x: (-x[1][0].cf, x[1][1]))
    return [(t, inf) for t, (inf, _c) in ranked[:k]]


def looks_roman(text: str) -> bool:
    return text.isascii() and any(c.isalpha() for c in text)
