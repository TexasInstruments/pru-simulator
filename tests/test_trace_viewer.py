from tools.view_ssi_trace import series_for_core, zoom_limits


def test_series_for_core_keeps_every_exported_row():
    rows = [
        {"_step": 10, "core": "pru0", "gpo0": 1, "gpi8": 1},
        {"_step": 20, "core": "pru0", "gpo0": 0, "gpi8": 1},
        {"_step": 30, "core": "pru1", "gpo0": 1, "gpi16": 0},
    ]

    assert series_for_core(rows, "pru0", "gpo0", "gpi8") == (
        [10, 20], [1, 0], [1, 1]
    )


def test_zoom_limits_zoom_around_cursor_and_stay_in_trace():
    assert zoom_limits(0, 100, 25, 0.5) == (0, 50)
    assert zoom_limits(0, 100, 90, 0.5) == (50, 100)
    assert zoom_limits(10, 20, 15, 2.0) == (10, 20)
