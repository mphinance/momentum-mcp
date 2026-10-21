"""The SCANLINE screen engine, vendored from https://github.com/mphinance/scanline.

Pure TradingView data over `tradingview-screener`: 6 markets, the 1000+ field
universe, cross-field filters, a sandboxed expression engine for computed
columns, cross-sectional stats, a factor model, 47 preset scans and the
market-study tools built on them. `tools.py` is the MCP layer;
`register_scanline_tools` mounts it on the momentum server under a `scan_`
prefix. Read-only, no account required, no data source other than TradingView.
"""

from .tools import register_scanline_tools  # noqa: F401

__all__ = ["register_scanline_tools"]
