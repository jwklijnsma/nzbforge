import threading

import pytest

from app import scanner


@pytest.mark.parametrize("mode,host,expected", [
    ("auto", "", False),
    ("auto", "news.example.com", True),
    ("1", "", True),
    ("off", "news.example.com", False),
])
def test_enabled(monkeypatch, mode, host, expected):
    monkeypatch.setenv("SCANNER", mode)
    monkeypatch.setattr(scanner, "HOST", host)
    assert scanner.enabled() is expected


def test_background_loop_runs_and_stops(monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(scanner, "scan_pass", ran.set)
    monkeypatch.setattr(scanner, "INTERVAL", 3600)
    stop = scanner.start_background()
    assert ran.wait(5), "scan pass did not run"
    stop.set()
    t = next(t for t in threading.enumerate() if t.name == "scanner")
    t.join(5)
    assert not t.is_alive()


def test_failing_pass_does_not_kill_loop(monkeypatch):
    calls = []
    stop = threading.Event()

    def boom():
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("nntp down")

    monkeypatch.setattr(scanner, "scan_pass", boom)
    monkeypatch.setattr(scanner, "INTERVAL", 0)
    scanner.run(stop)
    assert len(calls) == 2
