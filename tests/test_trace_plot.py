from pathlib import Path

from tools.plot_ssi_trace import decode_ssi_frames, load_trace


def _row(step, clock, data, core="pru0"):
    values = {f"gpo{i}": 0 for i in range(20)}
    values.update({f"gpi{i}": 0 for i in range(20)})
    values.update({"step": str(step), "core": core, "mode": "gpio"})
    values["gpo0"] = str(clock)
    values["gpi8"] = str(data)
    return values


def test_decode_ssi_frames_reads_first_12_rising_bits():
    rows = [_row(0, 1, 1)]
    step = 10
    rows.append(_row(step, 0, 1))
    for bit in [1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0]:
        step += 10
        rows.append(_row(step, 1, bit))
        step += 10
        rows.append(_row(step, 0, bit))

    frames = decode_ssi_frames(rows)

    assert len(frames) == 1
    assert frames[0]["bits"] == "101001011010"
    assert frames[0]["value"] == 0xA5A
    assert frames[0]["rising_edges"] == 12


def test_load_trace_rejects_missing_signal_columns(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text("step,core,gpo0\n0,pru0,1\n")

    try:
        load_trace(path)
    except ValueError as exc:
        assert "gpi8" in str(exc)
    else:
        raise AssertionError("missing gpi8 column should be rejected")
