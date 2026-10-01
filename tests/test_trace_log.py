"""Tests for the dashboard's disk-backed trace-log service."""

import os

import pytest

from ui.trace_log import (
    MAX_PAGE_SAMPLES,
    TRACE_RETENTION_SECONDS,
    TraceLogError,
    TraceLogManager,
)


def packed(step, run_step, r30=0, gpi=0, out=0, oe=0, clk=0):
    return [step, r30, gpi, out, oe, clk, run_step]


def test_trace_log_writes_schema_and_orders_multicore_rows(tmp_path):
    manager = TraceLogManager(tmp_path, now=lambda: 1_700_000_000.0)
    state = manager.start("socket-1")

    result = manager.append("socket-1", [
        {"core": "pru1", "mode": "gpio",
         "samples": [packed(2, 20, r30=1), packed(1, 10)],
         "captured_at_ms": [2.0, 1.0]},
        {"core": "pru0", "mode": "gpio",
         "samples": [packed(2, 20, r30=3), packed(1, 10, r30=2)],
         "captured_at_ms": [2.0, 1.0]},
    ])
    manager.stop("socket-1")

    assert result == {"sequences": [0, 1, 2, 3], "sample_count": 4}
    rows = manager.file_path(state["session_id"]).read_text().splitlines()
    assert rows[0].startswith("step,core,mode,gpo0")
    assert rows[0].endswith("run_step,captured_at_ms,sequence")
    assert [line.split(",")[1] for line in rows[1:]] == ["pru0", "pru1", "pru0", "pru1"]
    assert [line.split(",")[-1] for line in rows[1:]] == ["0", "1", "2", "3"]


def test_trace_log_pages_in_both_directions_and_returns_registered_channels(tmp_path):
    manager = TraceLogManager(tmp_path, now=lambda: 1_700_000_000.0)
    state = manager.start("socket-1")
    manager.append("socket-1", [{
        "core": "pru0", "mode": "gpio",
        "samples": [packed(i, i, r30=i & 1) for i in range(6)],
        "captured_at_ms": list(range(6)),
    }])
    manager.stop("socket-1")

    older = manager.page(state["session_id"], before=4, limit=2)
    newer = manager.page(state["session_id"], after=1, limit=2)

    assert [row["sequence"] for row in older["samples"]] == [2, 3]
    assert [row["sequence"] for row in newer["samples"]] == [2, 3]
    assert any(channel["type"] == "gpo" and channel["pin"] == 0
               for channel in older["observed_channels"])


def test_trace_log_rejects_ambiguous_or_oversized_pages(tmp_path):
    manager = TraceLogManager(tmp_path)
    state = manager.start("socket-1")
    manager.stop("socket-1")

    with pytest.raises(ValueError):
        manager.page(state["session_id"])
    with pytest.raises(ValueError):
        manager.page(state["session_id"], before=0, after=0)
    with pytest.raises(ValueError):
        manager.page(state["session_id"], before=0, limit=MAX_PAGE_SAMPLES + 1)


def test_trace_log_cleanup_removes_only_expired_trace_files(tmp_path):
    now = 1_700_000_000.0
    manager = TraceLogManager(tmp_path, now=lambda: now)
    expired = tmp_path / "trace-0123456789abcdef0123456789abcdef.csv"
    fresh = tmp_path / "trace-fedcba9876543210fedcba9876543210.csv"
    unrelated = tmp_path / "unrelated.csv"
    expired.write_text("x")
    fresh.write_text("x")
    unrelated.write_text("x")
    os.utime(expired, (now - TRACE_RETENTION_SECONDS - 1,) * 2)
    os.utime(fresh, (now - TRACE_RETENTION_SECONDS + 1,) * 2)
    os.utime(unrelated, (now - TRACE_RETENTION_SECONDS - 1,) * 2)

    manager.cleanup()

    assert not expired.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_trace_log_writer_failure_does_not_return_partial_success(tmp_path):
    manager = TraceLogManager(tmp_path)
    state = manager.start("socket-1")
    session = manager.session(state["session_id"])

    class BrokenWriter:
        def writerows(self, rows):
            raise OSError("disk full")

    session._writer = BrokenWriter()

    with pytest.raises(TraceLogError):
        manager.append("socket-1", [{
            "core": "pru0", "mode": "gpio",
            "samples": [packed(0, 0, r30=1)],
            "captured_at_ms": [0.0],
        }])

    assert session.sample_count == 0
    assert session.path.read_text().splitlines() == [
        ",".join(session.path.read_text().splitlines()[0].split(","))
    ]
