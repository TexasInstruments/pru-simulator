"""Keep the mechanically-checkable capability inventory tied to the tree."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from pru_io.io_port import IOPort
from simulator import Simulator
from xfr.xfr_bus import XFRBus


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "capability_inventory.md"


def _document_set(name: str) -> set[str]:
    text = INVENTORY.read_text(encoding="utf-8")
    match = re.search(rf"^<!-- inventory:{re.escape(name)}=([^>]*) -->$", text, re.MULTILINE)
    assert match, f"missing inventory metadata for {name}"
    return {value for value in match.group(1).split(",") if value}


def test_documented_xfr_ids_equal_registered_ids():
    bus = XFRBus()
    core = PRUCore("inventory", MemoryBus(), bus, IOPort())
    registered = set(bus._pads) | set(core.accelerators)
    assert _document_set("xfr-supported") == {str(device_id) for device_id in registered}


def test_documented_device_models_equal_configured_models():
    registered = set()
    for config_path in (ROOT / "config").glob("memory_*.cfg"):
        config = configparser.ConfigParser()
        config.read(config_path)
        registered.add(config["device"]["target"])
        # Exercise the registration path, not only the file parser.
        Simulator(config_path=str(config_path))
    assert _document_set("device-models") == registered


def test_documented_cores_equal_simulator_registration():
    sim = Simulator(config_path="nonexistent.cfg")
    assert _document_set("cores") == set(sim.cores)


def _cited_test_targets() -> set[str]:
    """Every `tests/...` path or node id quoted in the inventory tables."""
    text = INVENTORY.read_text(encoding="utf-8")
    return set(re.findall(r"`(tests/[\w./]+\.py(?:::[\w:]+)?)`", text))


def test_every_cited_test_target_collects():
    """A row may only cite a test that exists.

    The three set comparisons above guard the machine-readable metadata. They
    say nothing about the citations, and a citation is the only thing making a
    "supported" row falsifiable at all - a row pointing at a test file that was
    renamed, or at a node id that never existed, reads exactly like evidence.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "--no-header", "-p", "no:cacheprovider", "tests"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"collection failed:\n{proc.stdout}\n{proc.stderr}"
    collected = {line.strip().replace("\\", "/")
                 for line in proc.stdout.splitlines() if "::" in line}

    missing = sorted(
        target for target in _cited_test_targets()
        if not (target in collected
                or any(node.startswith(target + "::") for node in collected))
    )
    assert not missing, "inventory cites test targets that do not collect: %s" % missing
