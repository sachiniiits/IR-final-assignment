"""
textproc.py -- Indic-aware text normalisation and tokenisation.

Why a custom tokenizer?
  Python's ``\\w`` does NOT match Indic combining marks (matras such as
  U+093E 'ा', virama U+094D '्', anusvara U+0902 'ं' ...), so the naive
  ``re.findall(r"\\w+")`` shatters a Hindi word like "हिन्दी" into
  fragments.  This module tokenises by *Unicode script blocks* instead, so a
  token is a maximal run of characters from ONE script (Devanagari, Bengali,
  Tamil, Telugu, ... or Latin, or digits).  Mixed-script strings such as
  "BJPके" are therefore split at the script boundary into "bjp" + "के".

Normalisation pipeline (applied identically at index time and query time;
the configuration is persisted inside the index's meta.json):

  1. Unicode NFC  -- canonical composition.  Note that the Devanagari
     pre-composed nukta letters (क़ ख़ ग़ ज़ ड़ ढ़ फ़ य़, U+0958..U+095F) are
     *composition exclusions*, so NFC always yields the decomposed
     consonant + U+093C form.  This makes both spellings identical.
  2. Strip invisible format characters: ZWJ, ZWNJ, soft hyphen, BOM, ...
  3. Map native digits of every supported Indic script to ASCII 0-9.
  4. (optional, default ON) nukta folding:   ज़ -> ज , फ़ -> फ
  5. (optional, default ON) chandrabindu folding: ँ -> ं  (हँसी == हंसी)
  6. Lower-case Latin text.

Additional optional stages: stop-word removal (positions of the remaining
tokens are *preserved*, so phrase/proximity queries stay correct) and a
light Hindi suffix-stripping stemmer (Ramanathan & Rao, 2003).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict, field
from typing import Iterator, List, Tuple

# ---------------------------------------------------------------------------
# Unicode script blocks
# ---------------------------------------------------------------------------
# (name, first, last, first_digit_codepoint or None)
SCRIPTS = [
    ("devanagari", 0x0900, 0x097F, 0x0966),
    ("bengali",    0x0980, 0x09FF, 0x09E6),
    ("gurmukhi",   0x0A00, 0x0A7F, 0x0A66),
    ("gujarati",   0x0A80, 0x0AFF, 0x0AE6),
    ("oriya",      0x0B00, 0x0B7F, 0x0B66),
    ("tamil",      0x0B80, 0x0BFF, 0x0BE6),
    ("telugu",     0x0C00, 0x0C7F, 0x0C66),
    ("kannada",    0x0C80, 0x0CFF, 0x0CE6),
    ("malayalam",  0x0D00, 0x0D7F, 0x0D66),
]

# Punctuation inside the Indic blocks that must NOT be part of a word.
_INDIC_PUNCT = {0x0964, 0x0965, 0x0970}  # danda, double danda, abbreviation sign

# Invisible "format" characters that only affect rendering.
_INVISIBLE = "​‌‍‎‏­﻿⁠"

NUKTA = "़"
CHANDRABINDU = "ँ"
ANUSVARA = "ं"


def _build_digit_table() -> dict:
    table = {}
    for _name, _lo, _hi, d0 in SCRIPTS:
        if d0 is not None:
            for i in range(10):
                table[d0 + i] = ord("0") + i
    return table


_DIGITS = _build_digit_table()                    # length preserving
_STRIP_INVISIBLE = {ord(c): None for c in _INVISIBLE}


def display_normalize(text: str) -> str:
    """NFC + removal of invisible format characters.

    This is the form in which documents are kept in the document store, so
    that character offsets returned by :meth:`Analyzer.spans` are exact.
    Every later normalisation step (digit mapping, nukta / chandrabindu
    folding, lower-casing) never moves a token boundary."""
    return unicodedata.normalize("NFC", text).translate(_STRIP_INVISIBLE)


def _script_class(lo: int, hi: int) -> str:
    """Character class for one Indic block, minus punctuation and digits."""
    excluded = set(_INDIC_PUNCT)
    for _n, l, h, d0 in SCRIPTS:
        if l == lo and d0 is not None:
            excluded.update(range(d0, d0 + 10))
    parts, start = [], None
    for cp in range(lo, hi + 2):
        ok = cp <= hi and cp not in excluded
        if ok and start is None:
            start = cp
        elif not ok and start is not None:
            parts.append(f"\\u{start:04x}-\\u{cp - 1:04x}" if cp - 1 > start
                         else f"\\u{start:04x}")
            start = None
    return "[" + "".join(parts) + "]+"


# One alternative per script; the regex engine therefore never produces a
# token that mixes scripts.  Devanagari Extended (U+A8E0-A8FF) is folded in.
_ALTERNATIVES = [_script_class(lo, hi) for _n, lo, hi, _d in SCRIPTS]
_ALTERNATIVES[0] = _ALTERNATIVES[0][:-2] + "\\ua8e0-\\ua8ff]+"
_ALTERNATIVES += [
    r"[a-zA-ZÀ-ÖØ-öø-ɏ]+",  # Latin (+ accented)
    r"[0-9]+",
]
TOKEN_RE = re.compile("|".join(_ALTERNATIVES))


def script_of(token: str) -> str:
    """Return the script name of a (single-script) token."""
    if not token:
        return "unknown"
    cp = ord(token[0])
    for name, lo, hi, _d in SCRIPTS:
        if lo <= cp <= hi:
            return name
    if 0xA8E0 <= cp <= 0xA8FF:
        return "devanagari"
    if token[0].isdigit():
        return "digit"
    return "latin"


# ---------------------------------------------------------------------------
# Stop-words and stemmer (Hindi)
# ---------------------------------------------------------------------------
HINDI_STOPWORDS = frozenset("""
और के का की को में से है हैं था थे थी थीं हो होता होती होते होना हुआ हुई हुए
पर ने तो भी ही यह वह ये वे इस उस इन उन इसे उसे इसका उसका इसकी उसकी इसके उसके
एक किया किए किये करने करना करते करता करती कर गया गई गए गये जा जाता जाती जाते
जो कि लिए लिये तक साथ बाद द्वारा अपने अपना अपनी कुछ कई सभी या एवं तथा अथवा
अगर यदि जब तब जहाँ जहां वहाँ वहां यहाँ यहां कहा रहा रही रहे रहता नहीं न बहुत
सकता सकती सकते दिया दी दिए दिये ना वाले वाला वाली प्रति अब फिर किसी कोई
""".split())

ENGLISH_STOPWORDS = frozenset("""
a an the and or of to in on at for by with from as is are was were be been
it its this that these those which who whom what not no but if then than so
""".split())

# Ramanathan & Rao (2003) light stemmer suffix list, longest first.
_HINDI_SUFFIXES = sorted(set("""
ाएंगी ाएंगे ाऊंगी ाऊंगा ाइयाँ ाइयों ाइयां
ाएगी ाएगा ाओगी ाओगे एंगी ेंगी एंगे ेंगे ूंगी ूंगा ातीं नाओं नाएं ताओं ताएं ियाँ ियों ियां
ाकर ाइए ाईं ाया ेगी ेगा ोगी ोगे ाने ाना ाते ाती ाता तीं ाओं ाएं ुओं ुएं ुआं
कर ाओ िए ाई ाए ने नी ना ते ीं ती ता ाँ ां ों ें
ो े ू ु ी ि ा
""".split()), key=len, reverse=True)


def hindi_light_stem(token: str) -> str:
    """Strip the longest matching inflectional suffix (keeps >= 2 chars)."""
    for suf in _HINDI_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 2:
            return token[: -len(suf)]
    return token


# ---------------------------------------------------------------------------
# Configurable analyser
# ---------------------------------------------------------------------------
@dataclass
class AnalyzerConfig:
    fold_nukta: bool = True
    fold_chandrabindu: bool = True
    lowercase_latin: bool = True
    remove_stopwords: bool = False   # positional index: keep them by default
    stem: bool = False
    min_len: int = 1
    max_len: int = 40                 # longer "words" are markup garbage
    stopwords: List[str] = field(default_factory=list)  # extra stop-words

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AnalyzerConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Analyzer:
    """Turns raw text into (position, term) pairs.

    Positions are *token ordinals* (0-based) computed BEFORE stop-word
    removal, so that removing a stop-word leaves a positional gap and phrase
    queries such as "भारत का इतिहास" can still be answered as
    ``भारत  ?  इतिहास`` with a distance of 2.
    """

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()
        c = self.config
        self._stop = (HINDI_STOPWORDS | ENGLISH_STOPWORDS | frozenset(c.stopwords)
                      if c.remove_stopwords else frozenset())
        fold = {}
        if c.fold_nukta:
            fold[ord(NUKTA)] = None
        if c.fold_chandrabindu:
            fold[ord(CHANDRABINDU)] = ord(ANUSVARA)
        self._fold = fold

    # -- normalisation ------------------------------------------------------
    def normalize(self, text: str) -> str:
        text = display_normalize(text).translate(_DIGITS)
        if self._fold:
            text = text.translate(self._fold)
        return text

    def normalize_term(self, raw: str) -> str:
        """Normalise a single query word exactly like indexed words."""
        toks = [t for _p, t in self.analyze(raw)]
        return toks[0] if toks else ""

    # -- tokenisation -------------------------------------------------------
    def spans(self, text: str) -> Iterator[Tuple[int, int, int]]:
        """Yield (position, char_start, char_end) for every token of a
        *display-normalised* text (see :func:`display_normalize`).  Used for
        KWIC snippets and for the index self-verification."""
        for pos, m in enumerate(TOKEN_RE.finditer(text.translate(_DIGITS))):
            yield pos, m.start(), m.end()

    def analyze(self, text: str) -> Iterator[Tuple[int, str]]:
        """Yield (position, term) for every indexable token in ``text``."""
        c = self.config
        stop, stem = self._stop, c.stem
        lower = c.lowercase_latin
        min_len, max_len = c.min_len, c.max_len
        pos = -1
        for m in TOKEN_RE.finditer(self.normalize(text)):
            pos += 1
            tok = m.group()
            if lower and tok[0] < "ऀ":
                tok = tok.lower()
            n = len(tok)
            if n < min_len or n > max_len:
                continue
            if tok in stop:
                continue
            if stem and "ऀ" <= tok[0] <= "ॿ":
                tok = hindi_light_stem(tok)
            yield pos, tok

    def tokens(self, text: str) -> List[str]:
        return [t for _p, t in self.analyze(text)]
