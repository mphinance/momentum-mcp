"""Pytest config for momentum-mcp.

Registers the `live` marker (tests that hit TradingView over the network) and
isolates the SCANLINE real-time cookie so no test can read or overwrite the
developer's real TradingView credential.

Run offline:  python -m pytest tests/ -q -m "not live"
Run all:      python -m pytest tests/ -q
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the real tradingview endpoint over the network")


@pytest.fixture(autouse=True)
def _isolate_cookie_config(monkeypatch, tmp_path):
    """Never let a test read or write the developer's real TradingView cookie."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCANLINE_TV_SESSIONID", raising=False)
    monkeypatch.delenv("SCANLINE_TV_SESSIONID_SIGN", raising=False)
    from mcp_server.scanline import realtime

    realtime._reset_rejection()
    yield
    realtime._reset_rejection()
