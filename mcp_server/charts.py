"""
Chart generation module using mplfinance.

Renders candlestick + volume charts with optional EMA overlays as static
PNGs and returns both the file path and a base64-encoded string.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Headless rendering — must be set before importing pyplot

import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from mcp_server.data import get_historical_data  # noqa: E402

logger = logging.getLogger(__name__)

# Default output directory for saved charts
CHARTS_DIR = Path("./charts")

# Clean dark style for chart rendering
_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#22c55e",
        down="#ef4444",
        wick={"up": "#22c55e", "down": "#ef4444"},
        edge={"up": "#22c55e", "down": "#ef4444"},
        volume={"up": "#22c55e80", "down": "#ef444480"},
    ),
    facecolor="#0f0f0f",
    figcolor="#0f0f0f",
    gridcolor="#1a1a1a",
    gridstyle="--",
    y_on_right=True,
    rc={
        "font.size": 9,
        "axes.labelcolor": "#cccccc",
        "xtick.color": "#888888",
        "ytick.color": "#888888",
    },
)

# EMA overlay configuration: (period, color, label)
_EMA_STACK = [
    (8,  "#00d4ff", "EMA 8"),   # cyan
    (21, "#22c55e", "EMA 21"),  # green
    (34, "#eab308", "EMA 34"),  # yellow
    (55, "#f97316", "EMA 55"),  # orange
    (89, "#ef4444", "EMA 89"),  # red
]


async def generate_chart(
    ticker: str,
    period: str = "6mo",
    interval: str = "1d",
    style: str = "dark",
    show_emas: bool = True,
) -> dict[str, str]:
    """Generate a candlestick chart with EMA overlays for a ticker symbol.

    Fetches OHLCV data, renders a candlestick chart with volume panel
    and stacked EMA overlays (8/21/34/55/89) using ``mplfinance``, saves
    the PNG to the ``./charts/`` directory, and returns both the file
    path and a base64-encoded representation.

    Args:
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        period: Lookback period (e.g. ``"3mo"``, ``"1y"``).
            Defaults to ``"6mo"``.
        interval: Bar interval (e.g. ``"1d"``, ``"1h"``).
            Defaults to ``"1d"``.
        style: Chart colour theme. Currently only ``"dark"`` is
            supported. Reserved for future expansion.
        show_emas: Whether to overlay the EMA stack (8/21/34/55/89).
            Defaults to ``True``.

    Returns:
        A dict with:

        - ``ticker`` — The symbol charted.
        - ``period`` — The period used.
        - ``interval`` — The interval used.
        - ``bars`` — Number of bars rendered.
        - ``emas`` — List of EMA periods overlaid (e.g. [8, 21, 34, 55, 89]).
        - ``path`` — Absolute path to the saved PNG file.
        - ``base64`` — Base64-encoded PNG string (UTF-8).

    Raises:
        ValueError: If the ticker is invalid or returns no data.
    """
    ticker = ticker.strip().upper()

    # Fetch data
    records = await get_historical_data(ticker, period=period, interval=interval)

    if len(records) < 5:
        raise ValueError(
            f"Not enough data to chart '{ticker}': got {len(records)} bars."
        )

    # Build DataFrame in mplfinance-expected format
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        },
        inplace=True,
    )

    # Coerce to numeric (yfinance occasionally returns strings)
    for col_name in ("Open", "High", "Low", "Close", "Volume"):
        df[col_name] = pd.to_numeric(df[col_name], errors="coerce")

    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    # Build EMA overlay lines
    ema_plots: list[mpf.make_addplot] = []
    ema_periods_used: list[int] = []

    if show_emas:
        for ema_len, color, _label in _EMA_STACK:
            if len(df) >= ema_len:
                ema_series = df["Close"].ewm(span=ema_len, adjust=False).mean()
                ema_plots.append(
                    mpf.make_addplot(
                        ema_series,
                        color=color,
                        width=1.2,
                        panel=0,
                    )
                )
                ema_periods_used.append(ema_len)

    # Render in a background thread
    def _render() -> tuple[str, str]:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        # Sanitize ticker/period/interval before using them in a filename —
        # user-supplied strings must not be able to escape CHARTS_DIR via "/" or "..".
        safe_ticker = "".join(c for c in ticker if c.isalnum() or c in "-.") or "UNKNOWN"
        safe_period = "".join(c for c in period if c.isalnum()) or "period"
        safe_interval = "".join(c for c in interval if c.isalnum()) or "interval"
        filename = f"{safe_ticker}_{safe_period}_{safe_interval}.png"
        filepath = CHARTS_DIR / filename

        plot_kwargs: dict[str, Any] = {
            "type": "candle",
            "style": _STYLE,
            "volume": True,
            "figsize": (14, 8),
            "tight_layout": True,
            "warn_too_much_data": 500,
            "returnfig": True,  # Get fig/axes back for legend
        }

        if ema_plots:
            plot_kwargs["addplot"] = ema_plots

        fig, axes = mpf.plot(df, **plot_kwargs)

        # Add EMA legend to price panel (axes[0])
        if ema_periods_used:
            import matplotlib.lines as mlines
            legend_handles = []
            for ema_len, color, label in _EMA_STACK:
                if ema_len in ema_periods_used:
                    legend_handles.append(
                        mlines.Line2D([], [], color=color, linewidth=1.2, label=label)
                    )
            if legend_handles:
                axes[0].legend(
                    handles=legend_handles,
                    loc="upper left",
                    fontsize=8,
                    framealpha=0.3,
                    facecolor="#1a1a1a",
                    edgecolor="#333333",
                    labelcolor="#cccccc",
                )

        # Save to buffer
        buf = io.BytesIO()
        fig.savefig(buf, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        buf.seek(0)
        raw_bytes = buf.read()
        import matplotlib.pyplot as plt
        plt.close(fig)

        # Write the same bytes to disk for download
        filepath.write_bytes(raw_bytes)

        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return str(filepath.resolve()), b64

    path, b64_str = await asyncio.to_thread(_render)

    logger.info("Chart saved: %s (%d bars, EMAs: %s)", path, len(df), ema_periods_used)

    return {
        "ticker": ticker,
        "period": period,
        "interval": interval,
        "bars": len(df),
        "emas": ema_periods_used,
        "path": path,
        "base64": b64_str,
    }
