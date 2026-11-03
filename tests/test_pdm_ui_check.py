"""source/pdm/tools/ui_check.py: websocket flow against a server on a temp copy of the tree."""
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "source" / "pdm" / "tools"))

import ui_check  # noqa: E402


def test_ws_flow_runs_on_a_temp_copy_and_leaves_memory_cfg_alone(capsys):
    cfg = REPO / "memory.cfg"
    before = hashlib.md5(cfg.read_bytes()).hexdigest() if cfg.exists() else None
    assert ui_check.main(["ws", "--chunks", "2"]) == 0
    after = hashlib.md5(cfg.read_bytes()).hexdigest() if cfg.exists() else None
    assert before == after
    out = capsys.readouterr().out
    assert "history after reconnect equals the streamed one" in out
    assert "run continued" in out
