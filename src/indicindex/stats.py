"""
stats.py -- collection & index analytics.

  * Zipf's law   : fit log(cf) = log(C) - s*log(rank)   (least squares)
  * Heaps' law   : fit V = K * T^beta on the exact curve recorded at build time
  * compression  : actual VByte size vs. uncompressed 32-bit integers vs.
                   (estimated) Elias-gamma / Elias-delta bit codes
  * memory       : RAM used by the compressed in-memory lexicon vs. a naive
                   Python dict of the same vocabulary
  * SVG charts   : dependency-free log-log plots written next to the report
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from typing import List, Sequence, Tuple

from .codec import delta_bits, gamma_bits, vbyte_len
from .index_format import Lexicon, PostingsFile, load_meta


def linfit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Least squares y = a + b x.  Returns (a, b, r^2)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - ss_res / ss_tot) if ss_tot else 1.0


# ---------------------------------------------------------------------------
# tiny SVG plotter (log-log scatter / line)
# ---------------------------------------------------------------------------
def svg_loglog(path: str, series: List[Tuple[str, List[Tuple[float, float]], str]],
               title: str, xlabel: str, ylabel: str) -> None:
    W, H, L, R, T, B = 640, 420, 70, 20, 40, 55
    pts = [(x, y) for _n, s, _c in series for x, y in s if x > 0 and y > 0]
    lx0, lx1 = math.floor(math.log10(min(p[0] for p in pts))), math.ceil(math.log10(max(p[0] for p in pts)))
    ly0, ly1 = math.floor(math.log10(min(p[1] for p in pts))), math.ceil(math.log10(max(p[1] for p in pts)))
    lx1 = max(lx1, lx0 + 1)
    ly1 = max(ly1, ly0 + 1)
    X = lambda x: L + (math.log10(x) - lx0) / (lx1 - lx0) * (W - L - R)
    Y = lambda y: H - B - (math.log10(y) - ly0) / (ly1 - ly0) * (H - T - B)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="sans-serif" font-size="12">',
         f'<rect width="{W}" height="{H}" fill="white"/>',
         f'<text x="{W / 2}" y="22" text-anchor="middle" font-size="15" font-weight="bold">{title}</text>']
    for e in range(lx0, lx1 + 1):
        x = X(10 ** e)
        o.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H - B}" stroke="#e5e5e5"/>')
        o.append(f'<text x="{x:.1f}" y="{H - B + 16}" text-anchor="middle">1e{e}</text>')
    for e in range(ly0, ly1 + 1):
        y = Y(10 ** e)
        o.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        o.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">1e{e}</text>')
    o.append(f'<rect x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="none" stroke="#444"/>')
    o.append(f'<text x="{W / 2}" y="{H - 12}" text-anchor="middle">{xlabel}</text>')
    o.append(f'<text x="16" y="{H / 2}" text-anchor="middle" transform="rotate(-90 16 {H / 2})">{ylabel}</text>')
    for i, (name, s, color) in enumerate(series):
        s = [(x, y) for x, y in s if x > 0 and y > 0]
        d = " ".join(f"{'M' if j == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for j, (x, y) in enumerate(s))
        dash = ' stroke-dasharray="6,4"' if "fit" in name else ""
        o.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
        o.append(f'<rect x="{L + 12}" y="{T + 10 + i * 18}" width="14" height="4" fill="{color}"/>')
        o.append(f'<text x="{L + 32}" y="{T + 16 + i * 18}">{name}</text>')
    o.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(o))


def _log_sample(items: List[Tuple[float, float]], n: int = 300) -> List[Tuple[float, float]]:
    if len(items) <= n:
        return items
    out, last = [], -1
    for k in range(n):
        i = min(len(items) - 1, int(round(math.exp(k / (n - 1) * math.log(len(items)))) - 1))
        if i != last:
            out.append(items[i])
            last = i
    return out


# ---------------------------------------------------------------------------
def analyze(index_dir: str, out_dir: str | None = None, sample_terms: int = 20000,
            log=print) -> dict:
    meta = load_meta(index_dir)
    out_dir = out_dir or os.path.join(index_dir, "report")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    lex = Lexicon(os.path.join(index_dir, "lexicon"))
    t_load = time.time() - t0
    items = list(lex.items())
    V = len(items)

    # ---- Zipf ----------------------------------------------------------
    cfs = sorted((info.cf for _t, info in items), reverse=True)
    ranks = list(range(1, V + 1))
    lo, hi = 10, max(11, min(V, 10_000))     # conventional fit range: ranks 10..10^4
    xs = [math.log(r) for r in ranks[lo:hi]]
    ys = [math.log(c) for c in cfs[lo:hi]]
    za, zs, zr2 = linfit(xs, ys)
    zipf_pts = _log_sample(list(zip(ranks, cfs)))
    svg_loglog(os.path.join(out_dir, "zipf.svg"),
               [("observed cf", zipf_pts, "#1f77b4"),
                (f"fit: s={-zs:.2f}", [(r, math.exp(za) * r ** zs) for r, _ in zipf_pts], "#d62728")],
               "Zipf's law: collection frequency vs rank", "rank", "collection frequency")

    # ---- Heaps ---------------------------------------------------------
    curve = [(t, v) for t, v in meta.get("heaps_curve", []) if t > 0 and v > 0]
    if len(curve) >= 3:
        ha, hb, hr2 = linfit([math.log(t) for t, _ in curve], [math.log(v) for _, v in curve])
        K = math.exp(ha)
        svg_loglog(os.path.join(out_dir, "heaps.svg"),
                   [("observed vocabulary", curve, "#2ca02c"),
                    (f"fit: V={K:.1f}*T^{hb:.3f}", [(t, K * t ** hb) for t, _ in curve], "#d62728")],
                   "Heaps' law: vocabulary growth", "tokens seen (T)", "distinct terms (V)")
    else:
        K = hb = hr2 = float("nan")

    # ---- compression (sampled, exact per sampled term) -----------------
    pf = PostingsFile(os.path.join(index_dir, "postings.bin"))
    rnd = random.Random(42)
    sample = items if len(items) <= sample_terms else rnd.sample(items, sample_terms)
    tot = {"ints": 0, "vbyte": 0, "gamma": 0, "delta": 0, "actual": 0}
    for _t, info in sample:
        pl = pf.get(info)
        tot["actual"] += info.length
        for d, tf, s, e in _iter_with_gaps(pl):
            for g in d:
                tot["ints"] += 1
                tot["vbyte"] += vbyte_len(g)
                tot["gamma"] += gamma_bits(g)
                tot["delta"] += delta_bits(g)
    scale = meta["postings_bytes"] / max(tot["actual"], 1)
    comp = {
        "sampled_terms": len(sample),
        "integers_sampled": tot["ints"],
        "est_uncompressed_32bit_MiB": tot["ints"] * 4 * scale / 2 ** 20,
        "est_vbyte_payload_MiB": tot["vbyte"] * scale / 2 ** 20,
        "est_gamma_MiB": tot["gamma"] / 8 * scale / 2 ** 20,
        "est_delta_MiB": tot["delta"] / 8 * scale / 2 ** 20,
        "actual_postings_MiB": meta["postings_bytes"] / 2 ** 20,
    }
    comp["vbyte_vs_32bit_ratio"] = comp["est_uncompressed_32bit_MiB"] / max(comp["actual_postings_MiB"], 1e-9)
    pf.close()

    # ---- memory: compressed lexicon vs python dict ---------------------
    lex_mem = lex.memory_bytes()
    d = {t: info for t, info in items}
    dict_mem = sys.getsizeof(d) + sum(sys.getsizeof(t) + sys.getsizeof(i) + 4 * 28
                                      for t, i in items)
    del d
    res = {
        "n_docs": meta["n_docs"], "n_tokens": meta["n_tokens"], "n_terms": V,
        "zipf": {"exponent_s": -zs, "r2": zr2},
        "heaps": {"K": K, "beta": hb, "r2": hr2},
        "compression": comp,
        "memory": {"lexicon_compressed_MiB": lex_mem / 2 ** 20,
                   "python_dict_estimate_MiB": dict_mem / 2 ** 20,
                   "lexicon_load_seconds": t_load},
        "hapax_legomena": sum(1 for c in cfs if c == 1),
    }
    _write_report(os.path.join(out_dir, "report.md"), meta, res)
    log(f"[stats] report written to {out_dir}")
    return res


def _iter_with_gaps(pl):
    """Yield per document the list of integers actually stored:
    [d-gap, tf, plen] + position gaps."""
    buf = pl.buf
    prev = 0
    for d, tf, s, e in pl:
        gaps = [d - prev, tf, e - s]
        prevp = 0
        for p in pl.positions(s, e):
            gaps.append(p - prevp)
            prevp = p
        prev = d
        yield gaps, tf, s, e


def _write_report(path: str, meta: dict, r: dict) -> None:
    c, m = r["compression"], r["memory"]
    L = [
        f"# Index statistics report",
        "",
        f"* corpus: `{os.path.basename(meta['corpus'])}` ({meta['corpus_format']}), language `{meta['language']}`",
        f"* documents: **{meta['n_docs']:,}**  |  tokens: **{meta['n_tokens']:,}**  |  "
        f"vocabulary: **{r['n_terms']:,}**  |  postings (term,doc pairs): **{meta['n_postings']:,}**",
        f"* average document length: {meta['avg_doc_len']:.1f} tokens",
        f"* hapax legomena (cf = 1): {r['hapax_legomena']:,} ({100 * r['hapax_legomena'] / max(r['n_terms'], 1):.1f}% of vocabulary)",
        f"* build: {meta['n_runs']} SPIMI runs, {meta['workers']} workers, inversion {meta['seconds_invert']}s, "
        f"total {meta['seconds_total']}s",
        "",
        "## Zipf's law",
        f"cf ∝ rank^-s (fitted on ranks 10..10,000) with **s = {r['zipf']['exponent_s']:.3f}** (R² = {r['zipf']['r2']:.4f}).  ![zipf](zipf.svg)",
        "",
        "## Heaps' law",
        f"V = K·T^β with **K = {r['heaps']['K']:.2f}, β = {r['heaps']['beta']:.3f}** (R² = {r['heaps']['r2']:.4f}).  ![heaps](heaps.svg)",
        "",
        "## Compression of postings (estimated from a sample of terms, scaled)",
        "",
        "| encoding | size (MiB) |",
        "|---|---:|",
        f"| uncompressed 32-bit integers | {c['est_uncompressed_32bit_MiB']:,.1f} |",
        f"| **VByte gaps (actual file, incl. skip tables)** | **{c['actual_postings_MiB']:,.1f}** |",
        f"| Elias-γ gaps (estimate) | {c['est_gamma_MiB']:,.1f} |",
        f"| Elias-δ gaps (estimate) | {c['est_delta_MiB']:,.1f} |",
        "",
        f"VByte gives a **{c['vbyte_vs_32bit_ratio']:.2f}x** reduction over raw 32-bit integers while "
        "staying byte-aligned (fast to decode in Python).",
        "",
        "## Memory: vocabulary in RAM",
        f"* front-coded lexicon (blocks of 16): **{m['lexicon_compressed_MiB']:.2f} MiB** "
        f"(loaded in {m['lexicon_load_seconds']:.2f}s)",
        f"* the same vocabulary as a Python dict: ~{m['python_dict_estimate_MiB']:.1f} MiB",
        "",
        "## Terms by script",
        "",
        "| script | terms |", "|---|---:|",
    ]
    L += [f"| {k} | {v:,} |" for k, v in meta.get("terms_by_script", {}).items()]
    L += ["", "## Document-frequency distribution", "", "| df bucket | terms |", "|---|---:|"]
    L += [f"| {k} | {v:,} |" for k, v in meta.get("df_histogram", {}).items()]
    L += ["", "## Most frequent terms", "", "| term | cf | df |", "|---|---:|---:|"]
    L += [f"| {t['term']} | {t['cf']:,} | {t['df']:,} |" for t in meta.get("top_terms_by_cf", [])[:25]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
