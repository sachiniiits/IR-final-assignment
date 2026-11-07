"""
wikitext.py -- turn MediaWiki markup into plain running text.

A dependency-free, *linear-time* cleaner.  Nested constructs ({{templates}}
inside {{templates}}, [[File:..|caption with [[links]]]], {| tables |}) are
removed with a single bracket-matching scan rather than repeated regex
substitution (which is quadratic on the deeply nested infoboxes common in
Hindi Wikipedia).

Namespace prefixes are recognised in English *and* Hindi, e.g.
  File: / Image: / चित्र: / संचिका: / Category: / श्रेणी: / साँचा:
so that captions and category links do not pollute the vocabulary.
"""

from __future__ import annotations

import html
import re

# Link targets in these namespaces are dropped entirely (with their caption).
DROP_NAMESPACES = {
    "file", "image", "media", "category", "template", "wikipedia", "help",
    "portal", "user", "talk", "module",
    "चित्र", "संचिका", "फ़ाइल", "फाइल", "छवि", "श्रेणी", "साँचा", "सांचा",
    "विकिपीडिया", "सहायता", "प्रवेशद्वार", "सदस्य", "वार्ता", "मॉड्यूल",
}

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_REF_RE = re.compile(r"<ref[^>/]*?/>|<ref[^>]*?>.*?</ref>", re.S | re.I)
_DROP_TAGS_RE = re.compile(
    r"<(math|code|syntaxhighlight|source|pre|gallery|timeline|score|chem|hiero|graph|mapframe|templatedata|imagemap)"
    r"\b[^>]*>.*?</\1\s*>", re.S | re.I)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*?/?>")
_EXTLINK_RE = re.compile(r"\[(?:https?:|ftp:)?//[^\s\]]+\s*([^\]]*)\]")
_URL_RE = re.compile(r"(?:https?|ftp)://\S+")
_HEADING_RE = re.compile(r"^\s*(=+)\s*(.*?)\s*\1\s*$", re.M)
_BOLD_ITALIC_RE = re.compile(r"'{2,5}")
_MAGIC_RE = re.compile(r"__[A-Z]+__")
_LIST_RE = re.compile(r"^[*#:;]+\s*", re.M)
_WS_RE = re.compile(r"[ \t ]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n+")
_TOKEN_RE = re.compile(r"\{\{|\}\}|\{\||\|\}")


def _strip_nested(text: str) -> str:
    """Remove {{...}} and {|...|} (tables) with correct nesting, O(n)."""
    out = []
    depth = 0
    last = 0
    for m in _TOKEN_RE.finditer(text):
        tok = m.group()
        if tok in ("{{", "{|"):
            if depth == 0:
                out.append(text[last:m.start()])
            depth += 1
        else:  # closing
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last = m.end()
            # an unmatched closer at depth 0 is just left in the text
    if depth == 0:
        out.append(text[last:])
    return "".join(out)


_LINK_TOKEN_RE = re.compile(r"\[\[|\]\]")


def _resolve_link(inner: str) -> str:
    target = inner.split("|", 1)[0]
    if ":" in target:
        ns = target.split(":", 1)[0].strip().lower()
        # file / category / template links, and interwiki links like [[en:X]]
        if ns in DROP_NAMESPACES or (len(ns) in (2, 3) and ns.isascii() and ns.isalpha()):
            return ""
    label = inner.rsplit("|", 1)[-1] if "|" in inner else target
    return _replace_links(label) if "[[" in label else label


def _replace_links(text: str) -> str:
    """Resolve [[...]] links with nesting in one pass:
    [[a|b]] -> b,  [[a]] -> a,  [[File:x|caption [[y]]]] -> '' ."""
    out = []
    depth = 0
    start = 0
    last = 0
    for m in _LINK_TOKEN_RE.finditer(text):
        if m.group() == "[[":
            if depth == 0:
                out.append(text[last:m.start()])
                start = m.start()
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                out.append(_resolve_link(text[start + 2:m.start()]))
                last = m.end()
    out.append(text[last:] if depth == 0 else text[start + 2:])
    return "".join(out)


def clean_wikitext(text: str) -> str:
    """Return readable plain text from raw wikitext."""
    if not text:
        return ""
    text = _COMMENT_RE.sub("", text)
    text = _REF_RE.sub("", text)
    text = _DROP_TAGS_RE.sub("", text)
    text = _strip_nested(text)
    text = _replace_links(text)
    text = _EXTLINK_RE.sub(r"\1", text)
    text = _URL_RE.sub("", text)
    text = _HEADING_RE.sub(r"\2", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _MAGIC_RE.sub("", text)
    text = html.unescape(text)          # &nbsp; &amp; ...
    text = _TAG_RE.sub("", text)        # leftover <br>, <span ...>, ...
    text = _LIST_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n", text)
    return text.strip()


def is_redirect(text: str) -> bool:
    head = text[:40].lstrip().lower()
    return head.startswith("#redirect") or head.startswith("#अनुप्रेषित")
