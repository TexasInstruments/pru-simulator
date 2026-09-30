"""Generate the two figures of the pif_eth_100 docs as hand-written SVG.

    python3 source/pif_eth_100/make_figures.py

Writes figures/frame_timeline.svg and figures/datapath.svg. The timeline is
plotted from a real run of the project code (run_100.run_tx_only /
run_loopback / throughput, default seed, default frames, base and fast RX),
not from typed-in numbers. All figures are simulator results, not silicon.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from pif_eth_100 import run_100                          # noqa: E402

FIG_DIR = _HERE / "figures"
FONT = "DejaVu Sans, Arial, Helvetica, sans-serif"

# Okabe-Ito colour-blind-safe palette; every segment also carries a text label.
C_BURST, C_DECODE, C_PREP, C_RESID = "#0072B2", "#E69F00", "#CC79A7", "#999999"
INK, MUTED, GRID = "#1a1a1a", "#555555", "#d0d0d0"


def _t(x, y, s, size=12, anchor="start", weight="normal", fill=INK, style="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" font-style="{style}" fill="{fill}">{escape(s)}</text>')


def _lines(cx, y, lines, size=11, anchor="middle", weight="normal", fill=INK, lh=None):
    lh = lh or size + 3
    return "\n".join(_t(cx, y + i * lh, s, size, anchor, weight if i else "bold", fill)
                     for i, s in enumerate(lines))


def _svg(w, h, title, desc, body) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="{FONT}" role="img" aria-labelledby="t d">\n'
            f'<title id="t">{escape(title)}</title>\n<desc id="d">{escape(desc)}</desc>\n'
            f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            f'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{INK}"/></marker></defs>\n'
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>\n{body}\n</svg>\n')


# --- measured data ------------------------------------------------------------

def measure() -> dict:
    """Run the same loops as `python3 source/pif_eth_100/run_100.py --rx both`."""
    seed = run_100.DEFAULT_SEED
    tx = run_100.run_tx_only(seed, num_frames=4)
    loops = {rx: run_100.run_loopback(seed, num_frames=3, rx=rx) for rx in ("base", "fast")}
    return {"tx": tx, "loops": loops,
            "tp": {rx: run_100.throughput(lp, tx) for rx, lp in loops.items()}}


def _gated_post_us(loop) -> float:
    """Mean PRU1 post-frame time of the frames that gate the plotted gaps.

    The mean e2e gap covers frames 1..n-1, so the last frame's post-frame is
    excluded; this is what makes the segments sum to the measured period."""
    c = loop.rx_post_cycles[:-1]
    return sum(c) / len(c) / run_100.CORE_MHZ


# --- figure 1: frame timeline --------------------------------------------------

def timeline_svg(tp: dict, loops: dict) -> str:
    W, H = 960, 470
    x0, x1, tmax = 130.0, 780.0, 180.0
    px = lambda us: x0 + us * (x1 - x0) / tmax
    burst = tp["base"]["T_burst_ns"] / 1e3
    prep = tp["base"]["tx_gap_ns"] / 1e3
    bars = []
    for rx, name in (("base", "Baseline RX"), ("fast", "Fast RX")):
        t = tp[rx]
        post = _gated_post_us(loops[rx])
        resid = t["e2e_gap_ns"] / 1e3 - prep - post
        bars.append((name, t["F3_e2e_mbps"], "end-to-end goodput",
                     [("burst", burst, C_BURST, "#fff"), ("post", post, C_DECODE, INK),
                      ("prep", prep, C_PREP, INK), ("resid", resid, C_RESID, INK)]))
    txl = tp["base"]
    bars.append(("TX alone", txl["F3_tx_mbps"], "TX-limited reference",
                 [("burst", burst, C_BURST, "#fff"), ("prep", prep, C_PREP, INK)]))

    b = [_t(W / 2, 26, "One frame period, start of burst to start of next burst", 15, "middle", "bold"),
         _t(W / 2, 44, "simulator measurement (default seed), not silicon", 11, "middle", fill=MUTED, style="italic")]
    by0, bh, step = 100, 50, 80
    for i in range(0, int(tmax) + 1, 20):                      # grid + axis ticks
        b.append(f'<line x1="{px(i):.1f}" y1="{by0 - 12}" x2="{px(i):.1f}" y2="{by0 + 2 * step + bh + 12}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
    ay = by0 + 2 * step + bh + 50
    b.append(f'<line x1="{x0}" y1="{ay}" x2="{x1}" y2="{ay}" stroke="{INK}" stroke-width="1.5"/>')
    for i in range(0, int(tmax) + 1, 20):
        b.append(f'<line x1="{px(i):.1f}" y1="{ay}" x2="{px(i):.1f}" y2="{ay + 5}" stroke="{INK}"/>')
        b.append(_t(px(i), ay + 19, str(i), 11, "middle"))
    b.append(_t((x0 + x1) / 2, ay + 38, "time within one frame period (us)", 12, "middle", fill=MUTED))

    for k, (name, mbps, sub, segs) in enumerate(bars):
        y = by0 + k * step
        b.append(_t(x0 - 10, y + bh / 2 - 2, name, 13, "end", "bold"))
        b.append(_t(x0 - 10, y + bh / 2 + 14, f"{sum(s[1] for s in segs):.1f} us", 11, "end", fill=MUTED))
        x = 0.0
        for key, dur, col, txt in segs:
            wpx = dur * (x1 - x0) / tmax
            b.append(f'<rect x="{px(x):.1f}" y="{y}" width="{wpx:.1f}" height="{bh}" fill="{col}" '
                     f'stroke="#ffffff" stroke-width="1"/>')
            if key == "resid":
                pass
            elif wpx > 70:
                b.append(_t(px(x) + wpx / 2, y + bh / 2 + 4, f"{dur:.2f} us", 12, "middle", "bold", txt))
            else:
                b.append(_t(px(x) + wpx / 2, y + bh / 2 + 4, f"{dur:.2f}", 11, "middle", "bold", txt))
            x += dur
        b.append(_t(x1 + 18, y + bh / 2 - 2, f"{mbps:.2f} Mbit/s", 17, "start", "bold"))
        b.append(_t(x1 + 18, y + bh / 2 + 15, sub, 11, "start", fill=MUTED))

    # direct segment names above the first bar (same order in every bar)
    n0 = by0 - 18
    xs, segs0 = 0.0, bars[0][3]
    cx = {}
    for key, dur, _, _ in segs0:
        cx[key] = px(xs + dur / 2)
        xs += dur
    b.append(_t(cx["burst"] - 38, n0, "burst on the wire", 11, "start", "bold", C_BURST))
    b.append(_t(cx["post"], n0, "RX post-frame decode (PRU1)", 11, "middle", "bold", "#8a5a00"))
    b.append(_t(cx["prep"] + 20, n0, "TX preparation", 11, "end", "bold", "#8a2f6a"))

    # wire-rate bracket under the reference bar
    yb = by0 + 2 * step + bh + 8
    xa, xb = px(0), px(burst)
    b.append(f'<path d="M{xa:.1f},{yb} v6 H{xb:.1f} v-6" fill="none" stroke="{C_BURST}" stroke-width="2"/>')
    b.append(_t(xb + 8, yb + 12,
                f"inside the burst: {tp['base']['F1_mbaud']:.0f} Mbaud on the wire = "
                f"{tp['base']['F2_mbps']:.0f} Mbit/s (8b/10b)", 11, "start", "bold", C_BURST))

    lg = ay + 62
    b.append(_t(x0, lg, "Grey sliver: mean "
                f"{min(x[1] for x in bars[0][3] if x[0] == 'resid') * 1e3:.0f} ns (baseline) / "
                f"{min(x[1] for x in bars[1][3] if x[0] == 'resid') * 1e3:.0f} ns (fast) of host/arming time, "
                "not itemised. RX decode = mean of the post-frames that gate the gaps.", 10.5, fill=MUTED))
    desc = ("Horizontal stacked bars of one frame period. "
            + " ".join(f"{n}: {mb:.2f} Mbit/s over {sum(s[1] for s in sg):.1f} us." for n, mb, _, sg in bars)
            + f" Burst {burst:.2f} us at {tp['base']['F1_mbaud']:.0f} Mbaud, TX preparation {prep:.2f} us.")
    return _svg(W, H, "Frame timeline: baseline RX, fast RX and TX-limited reference", desc, "\n".join(b))


# --- figure 2: data path -------------------------------------------------------

def _box(x, y, w, h, lines, fill="#f4f8fc", stroke="#1f4e79", num=None, dash=False, size=11):
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" '
         f'stroke-width="1.5"{" stroke-dasharray=\"6 4\"" if dash else ""}/>')
    s += "\n" + _lines(x + w / 2, y + 40 if num else y + 22, lines, size)
    if num:
        s += (f'\n<circle cx="{x + 16}" cy="{y + 16}" r="11" fill="{stroke}"/>'
              + _t(x + 16, y + 20, str(num), 12, "middle", "bold", "#ffffff"))
    return s


def _arrow(x1, y1, x2, y2, dash=False):
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" stroke-width="2" '
            f'marker-end="url(#ah)"{" stroke-dasharray=\"5 4\"" if dash else ""}/>')


def datapath_svg(tp: dict, tx) -> str:
    W, H = 960, 690
    t = tp["base"]
    pushed = tx.burst_pushed[0]
    symbols = pushed * 8 // 10
    txcfg, rxcfg = f"0x{run_100.TXCFG_100:08X}", f"0x{run_100.RXCFG_100:08X}"
    bw, bh, gap, x0 = 155, 138, 28, 36
    col = [x0 + i * (bw + gap) for i in range(5)]
    y1, y2 = 76, 382
    b = [_t(W / 2, 26, "pif_eth_100 data path: PRU0 TX, loopback wire, PRU1 RX", 15, "middle", "bold"),
         _t(W / 2, 44, "numbers are simulator results, not silicon", 11, "middle", fill=MUTED, style="italic")]
    # PRU0 lane
    b.append(f'<rect x="20" y="58" width="920" height="{bh + 62}" rx="10" fill="#ffffff" stroke="{C_BURST}" stroke-width="2"/>')
    b.append(_t(30, 73, "PRU0 (TX core, 300 MHz)", 12, "start", "bold", C_BURST))
    tx_boxes = [
        ["PRNG fill", "200 B xorshift32", "payload in DRAM0", "core buffer"],
        ["CRC-32", "CRC16/32 broadside", "accelerator", "+4 B FCS"],
        ["8b/10b encode", "256-entry LUT", "in DRAM0, running", "disparity"],
        ["TX FIFO", "4 deep, packed", f"{pushed} FIFO bytes,", "K28.5 commas", "bracket the frame"],
        ["perif serialiser", f"TXCFG {txcfg}", "n = 3, 10.000 ns/bit", f"= {t['F1_mbaud']:.0f} Mbaud"],
    ]
    for i, ln in enumerate(tx_boxes):
        b.append(_box(col[i], y1 + 6, bw, bh - 6, ln, num=i + 1))
        if i:
            b.append(_arrow(col[i - 1] + bw, y1 + bh / 2 + 10, col[i] - 2, y1 + bh / 2 + 10))
    b.append(_t(col[4] + bw / 2 - 12, y1 + bh + 18,
                f"{symbols} symbols, {pushed} FIFO bytes, {t['T_burst_ns'] / 1e3:.2f} us burst per frame",
                11, "end", "bold", C_BURST))
    # wire
    wx = col[4] + bw / 2
    b.append(_arrow(wx, y1 + bh, wx, 266))
    b.append(f'<rect x="{col[4]}" y="268" width="{bw}" height="44" rx="22" fill="#fff7e6" '
             f'stroke="{C_DECODE}" stroke-width="2"/>')
    b.append(_lines(wx, 287, ["loopback wire (ch0)", "5/6 ns modelled latency"], 11))
    # PRU1 lane
    b.append(f'<rect x="20" y="{y2 - 18}" width="920" height="{H - 38 - (y2 - 18)}" rx="10" fill="#ffffff" stroke="#b04a00" stroke-width="2"/>')
    b.append(_t(30, y2 - 3, "PRU1 (RX core, 300 MHz)", 12, "start", "bold", "#b04a00"))
    b.append(_arrow(wx, 312, wx, y2 + 6))
    c = list(reversed(col))     # flow runs right to left
    rx_boxes = [
        ["perif RX sampler", f"RXCFG {rxcfg}", "fractional divider 1.5", "5.000 ns samples =", "exact 2x oversampling"],
        ["realtime capture", "raw samples to DRAM1", "8 cycles per byte in", "a 12-cycle budget"],
        ["post-frame (1)", "comma alignment,", "2:1 decimation,", "8b/10b decode"],
        ["post-frame (2)", "CRC-32 check,", "PRNG bit-error", "check"],
        ["stats block", "DRAM1: frames, crc_ok,", "symbol_errors, bit_err,", "tot_bits, eof_status"],
    ]
    for i, ln in enumerate(rx_boxes):
        fill = "#fdf3e6" if i < 4 else "#eef7ee"
        b.append(_box(c[i], y2 + 6, bw, bh - 6, ln, fill=fill, stroke="#b04a00" if i < 4 else "#2e7d32", num=i + 6))
        if i:
            b.append(_arrow(c[i - 1] - 2, y2 + bh / 2 + 10, c[i] + bw + 2, y2 + bh / 2 + 10))
    # fast-RX note
    ny = y2 + bh + 20
    b.append(f'<rect x="{c[3] - 60}" y="{ny}" width="{c[2] + bw - c[3] + 120}" height="64" rx="6" fill="#ffffff" '
             f'stroke="{INK}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    b.append(_lines((c[3] + c[2] + bw) / 2, ny + 20,
                    ["Fast RX variant (same capture loop)",
                     "LUT decimation, 4 bytes per load",
                     f"post-frame {tp['base']['rx_post_cycles']:.0f} -> {tp['fast']['rx_post_cycles']:.0f} cycles"],
                    11))
    b.append(f'<line x1="{(c[2] + c[3] + bw) / 2:.1f}" y1="{ny}" x2="{(c[2] + c[3] + bw) / 2:.1f}" y2="{y2 + bh + 4}" '
             f'stroke="{INK}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    b.append(_t(30, H - 10, "Step numbers 1-10 follow the data; PRU1 flow runs right to left.", 10.5, fill=MUTED))
    desc = ("Block diagram. PRU0 fills a 200 byte PRNG payload, computes CRC-32 on the accelerator, "
            f"encodes 8b/10b with a 256-entry LUT, packs a 4-deep TX FIFO and serialises at {t['F1_mbaud']:.0f} Mbaud "
            f"(TXCFG {txcfg}). The loopback wire feeds PRU1, which samples at exact 2x (RXCFG {rxcfg}), captures raw "
            "samples in real time, then decodes, checks CRC-32 and bit errors and writes a stats block.")
    return _svg(W, H, "pif_eth_100 data path at 100 Mbaud", desc, "\n".join(b))


def write_figures(data: dict | None = None) -> list[Path]:
    data = data or measure()
    FIG_DIR.mkdir(exist_ok=True)
    out = []
    for name, svg in (("frame_timeline.svg", timeline_svg(data["tp"], data["loops"])),
                      ("datapath.svg", datapath_svg(data["tp"], data["tx"]))):
        p = FIG_DIR / name
        p.write_text(svg, encoding="utf-8")
        out.append(p)
    return out


def main() -> int:
    for p in write_figures():
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
