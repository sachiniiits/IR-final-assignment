"""
codec.py -- integer compression for postings lists.

Postings are stored as *gaps* (d-gaps between successive docIDs, and
position-gaps between successive positions inside a document) and every gap
is written with **Variable-Byte (VByte)** coding: 7 payload bits per byte,
the high bit marks the *last* byte of an integer (Manning et al., IIR ch. 5).

Small gaps (the overwhelmingly common case thanks to Zipf's law) take one
byte instead of four.

The module also contains exact bit-length estimators for Elias-gamma and
Elias-delta codes so that the statistics report can compare the actual VByte
index size against what bit-level codes would have achieved, without paying
the (pure-Python) cost of bit-level I/O at build time.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

# Pre-computed single-byte encodings for 0..127 (the hot path).
_ONE_BYTE = [bytes([0x80 | i]) for i in range(128)]


def vbyte_encode_int(n: int, out: bytearray) -> None:
    """Append the VByte encoding of non-negative int ``n`` to ``out``."""
    if n < 128:
        out += _ONE_BYTE[n]
        return
    chunks = []
    while True:
        chunks.append(n & 0x7F)
        n >>= 7
        if n == 0:
            break
    chunks.reverse()
    chunks[-1] |= 0x80
    out += bytes(chunks)


def vbyte_encode(numbers: Iterable[int]) -> bytes:
    out = bytearray()
    enc = vbyte_encode_int
    for n in numbers:
        enc(n, out)
    return bytes(out)


def vbyte_decode(buf, start: int = 0, end: int | None = None) -> List[int]:
    """Decode *all* integers in ``buf[start:end]``."""
    if end is None:
        end = len(buf)
    res = []
    append = res.append
    n = 0
    for i in range(start, end):
        b = buf[i]
        if b & 0x80:
            append((n << 7) | (b & 0x7F))
            n = 0
        else:
            n = (n << 7) | b
    return res


def vbyte_decode_one(buf, pos: int) -> Tuple[int, int]:
    """Decode one integer starting at ``pos``; returns (value, next_pos)."""
    n = 0
    while True:
        b = buf[pos]
        pos += 1
        if b & 0x80:
            return (n << 7) | (b & 0x7F), pos
        n = (n << 7) | b


def vbyte_decode_n(buf, pos: int, count: int) -> Tuple[List[int], int]:
    """Decode exactly ``count`` integers starting at ``pos``."""
    res = []
    append = res.append
    n = 0
    while count:
        b = buf[pos]
        pos += 1
        if b & 0x80:
            append((n << 7) | (b & 0x7F))
            n = 0
            count -= 1
        else:
            n = (n << 7) | b
    return res, pos


def vbyte_len(n: int) -> int:
    """Number of bytes VByte needs for ``n``."""
    return max(1, (n.bit_length() + 6) // 7)


# ---------------------------------------------------------------------------
# Gap helpers
# ---------------------------------------------------------------------------
def to_gaps(sorted_values: List[int]) -> List[int]:
    prev = 0
    out = []
    for v in sorted_values:
        out.append(v - prev)
        prev = v
    return out


def from_gaps(gaps: Iterable[int]) -> List[int]:
    acc = 0
    out = []
    for g in gaps:
        acc += g
        out.append(acc)
    return out


# ---------------------------------------------------------------------------
# Bit-length estimators (for the compression report)
#   Elias codes are defined for n >= 1, so we encode (gap + 1).
# ---------------------------------------------------------------------------
def gamma_bits(n: int) -> int:
    n += 1
    return 2 * (n.bit_length() - 1) + 1


def delta_bits(n: int) -> int:
    n += 1
    L = n.bit_length()
    return (L - 1) + gamma_bits(L - 1)
