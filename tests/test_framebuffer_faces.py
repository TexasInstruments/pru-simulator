"""End-to-end tests for source/framebuffer_faces.asm (see docs/framebuffer.md).

The expected pictures are rebuilt from the mask rows and *_COLOR constants in
the firmware source itself (the ASCII art beside each LDI32), so editing a mask
or color in the .asm keeps these tests meaningful.
"""
import os
import re
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulator import Simulator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PATH = os.path.join(ROOT, "source", "framebuffer_faces.asm")
CONFIGS = [
    pytest.param("memory.cfg", id="am243x"),
    pytest.param("config/memory_am261x.cfg", id="am261x"),
]
FB_BASE = 0x11000
MOODS = ["HAPPY", "SAD", "SURPRISED", "ANGRY"]


# --- minimal FBUF decoder -------------------------------------------------

def parse_header(raw: bytes) -> dict:
    magic, wh, fmt, stride, data_off, pal_off, counter, mono = struct.unpack("<8I", raw[:32])
    return {"magic": magic, "width": wh & 0xFFFF, "height": wh >> 16, "format": fmt,
            "stride": stride, "data_offset": data_off, "palette_offset": pal_off,
            "counter": counter, "mono": mono}


def decode_rgb565(raw: bytes, hdr: dict) -> list[list[int]]:
    """Rows of RGB565 values (stride 0 = width*2 rounded up to 4 bytes)."""
    stride = hdr["stride"] or ((hdr["width"] * 2 + 3) & ~3)
    off = hdr["data_offset"] or 0x20
    return [list(struct.unpack_from(f"<{hdr['width']}H", raw, off + y * stride))
            for y in range(hdr["height"])]


# --- expected picture from the firmware source -----------------------------

def _read_source():
    with open(SRC_PATH) as f:
        return f.read()


def _consts(src):
    return {m.group(1): int(m.group(2), 0)
            for m in re.finditer(r"^(\w+)\s+\.set\s+(0x[0-9A-Fa-f]+|\d+)", src, re.M)}


def _masks(src):
    """Sections '; ---- title ----' -> 16 rows of 16 bools from the ASCII art."""
    sections, cur = {}, None
    for line in src.splitlines():
        m = re.match(r"\s*; ---- (.+?) ----", line)
        if m:
            cur = m.group(1)
            sections[cur] = []
            continue
        m = re.search(r"ldi32 r\d+, 0x[0-9A-Fa-f]+\s*; ([.#]{16})\s+row", line)
        if m and cur:
            sections[cur].append([c == "#" for c in m.group(1)])
    return sections


def expected_image(mood: int) -> list[list[int]]:
    src = _read_source()
    k, masks = _consts(src), _masks(src)
    disc = masks["disc mask (shared by all moods)"]
    feats = next(v for t, v in masks.items() if t.startswith(f"mood {mood}:"))
    color = k[f"{MOODS[mood]}_COLOR"]
    img = []
    for y in range(32):
        row = []
        for x in range(32):
            if feats[y // 2][x // 2]:
                row.append(k["FEATURE_COLOR"])
            elif disc[y // 2][x // 2]:
                row.append(color)
            else:
                row.append(k["BG_COLOR"])
        img.append(row)
    return img


# --- harness ---------------------------------------------------------------

class Run:
    def __init__(self, cfg):
        self.sim = Simulator(os.path.join(ROOT, cfg))
        errors = self.sim.load("pru0", _read_source())
        assert errors == []
        self.core = self.sim.cores["pru0"]

    def counter(self) -> int:
        return struct.unpack("<I", self.sim.memory_read(FB_BASE + 0x18, 4))[0]

    def run_to_counter(self, target: int, limit: int = 2_000_000):
        """Step one instruction at a time; stop right after the counter store."""
        for _ in range(limit):
            self.core.step()
            assert self.core.fault is None, self.core.fault
            if self.counter() >= target:
                return
        raise AssertionError(f"counter never reached {target}")

    def snapshot(self) -> bytes:
        return self.sim.memory_read(FB_BASE, 0x20 + 2048)


@pytest.mark.parametrize("cfg", CONFIGS)
def test_header_and_first_face(cfg):
    run = Run(cfg)
    run.run_to_counter(1)
    raw = run.snapshot()
    hdr = parse_header(raw)
    assert hdr["magic"] == 0x46554246
    assert (hdr["width"], hdr["height"]) == (32, 32)
    assert hdr["format"] == 16
    assert hdr["stride"] == 0
    assert hdr["data_offset"] == 0
    assert hdr["counter"] == 1
    pix = decode_rgb565(raw, hdr)
    k = _consts(_read_source())
    assert pix[14][16] == k["HAPPY_COLOR"]           # forehead/cheek area, face color
    assert pix[0][0] == pix[0][31] == pix[31][0] == pix[31][31] == k["BG_COLOR"]
    assert pix[8][8] == k["FEATURE_COLOR"]           # left eye


@pytest.mark.parametrize("cfg", CONFIGS)
def test_each_frame_is_complete_when_counter_changes(cfg):
    """At every counter change the whole buffer equals the expected mood image,
    and the moods advance happy -> sad -> surprised -> angry -> happy."""
    run = Run(cfg)
    colors = []
    for frame in range(1, 6):
        run.run_to_counter(frame)
        raw = run.snapshot()
        hdr = parse_header(raw)
        assert hdr["counter"] == frame
        mood = (frame - 1) % len(MOODS)
        assert decode_rgb565(raw, hdr) == expected_image(mood), f"frame {frame}"
        colors.append(decode_rgb565(raw, hdr)[14][16])
    assert colors[:4] == [_consts(_read_source())[f"{m}_COLOR"] for m in MOODS]
    assert colors[4] == colors[0]
    assert run.core.fault is None and not run.core.halted


@pytest.mark.parametrize("cfg", CONFIGS)
def test_frame_two_changes_face_color(cfg):
    run = Run(cfg)
    run.run_to_counter(1)
    first = decode_rgb565(run.snapshot(), parse_header(run.snapshot()))
    run.run_to_counter(2)
    raw = run.snapshot()
    second = decode_rgb565(raw, parse_header(raw))
    assert first[14][16] != second[14][16]
