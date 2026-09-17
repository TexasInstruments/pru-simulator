"""Tests for dashboard host/port selection and the in-use port diagnostic."""
import socket

import pytest

from ui.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    _parse_args,
    _port_is_free,
    start_dashboard,
)


@pytest.fixture
def bound_port():
    """Hold a real listening socket and yield the port it occupies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((DEFAULT_HOST, 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


@pytest.fixture
def free_port():
    """Return a port that was bindable and has been released."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((DEFAULT_HOST, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_defaults_match_documented_values(monkeypatch):
    monkeypatch.delenv("PRU_SIM_UI_HOST", raising=False)
    monkeypatch.delenv("PRU_SIM_UI_PORT", raising=False)
    args = _parse_args([])
    assert args.host == DEFAULT_HOST
    assert args.port == DEFAULT_PORT


def test_cli_flags_override_defaults(monkeypatch):
    monkeypatch.delenv("PRU_SIM_UI_HOST", raising=False)
    monkeypatch.delenv("PRU_SIM_UI_PORT", raising=False)
    args = _parse_args(["--host", "0.0.0.0", "--port", "9001"])
    assert args.host == "0.0.0.0"
    assert args.port == 9001


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("PRU_SIM_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("PRU_SIM_UI_PORT", "9002")
    args = _parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 9002


def test_cli_flag_beats_environment(monkeypatch):
    monkeypatch.setenv("PRU_SIM_UI_PORT", "9002")
    args = _parse_args(["--port", "9003"])
    assert args.port == 9003


def test_port_is_free_detects_an_occupied_port(bound_port):
    assert _port_is_free(DEFAULT_HOST, bound_port) is False


def test_port_is_free_accepts_an_unused_port(free_port):
    assert _port_is_free(DEFAULT_HOST, free_port) is True


def test_start_dashboard_refuses_an_occupied_port(bound_port, monkeypatch):
    """The diagnostic must fire *instead of* starting the server."""
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append((a, k)))

    with pytest.raises(SystemExit) as excinfo:
        start_dashboard(host=DEFAULT_HOST, port=bound_port)

    message = str(excinfo.value)
    assert str(bound_port) in message, "diagnostic must name the colliding port"
    assert "--port" in message, "diagnostic must show how to pick another port"
    assert started == [], "uvicorn must not be started on an occupied port"


def test_start_dashboard_serves_on_a_free_port(free_port, monkeypatch):
    """Control arm: the guard must not block an ordinary start."""
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k))

    start_dashboard(host=DEFAULT_HOST, port=free_port)

    assert len(started) == 1
    assert started[0]["host"] == DEFAULT_HOST
    assert started[0]["port"] == free_port
