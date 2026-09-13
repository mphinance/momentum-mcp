"""edgar_tools.py — MCP wrappers around the root-level edgar.py SEC client.

edgar.py is sync (SEC throttling is a blocking time.sleep()), same shape as
fundamentals.py's yfinance calls, so these wrappers call it directly rather than
via asyncio.to_thread, matching that existing convention. `smart_cache` is applied
because filings/financials/stakes are far slower-moving than yfinance fundamentals,
so both TTLs are longer than fundamentals.py's default.

Every function returns {"ticker": ..., "error": ...} rather than raising, including
when SEC_USER_AGENT is unset — a raised exception from a tool call is a worse user
experience than a clear error dict naming the missing env var.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from mcp_server.cache import smart_cache

logger = logging.getLogger(__name__)


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> JSON-safe list of dicts. Timestamps and NaN don't survive
    FastMCP's JSON encoding as-is.
    """
    if df.empty:
        return []
    out = (df.reset_index() if df.index.name else df).copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(pd.notnull(out), None)
    return out.to_dict(orient="records")


@smart_cache(open_ttl=3600, closed_ttl=86400)
async def get_sec_filings(
    ticker: str,
    months: int = 12,
    forms: list[str] | None = None,
) -> dict[str, Any]:
    """SEC EDGAR filing index for a ticker: the recent-events sweep.

    `forms` narrows to specific form types, e.g. ["8-K", "10-Q"]. Every row
    carries `source_url` pointing at the primary document.
    """
    import mcp_server.edgar as edgar

    try:
        df = edgar.filings(ticker, months=months, forms=forms)
    except Exception as e:
        logger.error("get_sec_filings failed for %s: %s", ticker, e)
        return {"ticker": ticker.upper(), "error": str(e)}

    rows = _df_to_records(df)
    return {"ticker": ticker.upper(), "count": len(rows), "filings": rows}


@smart_cache(open_ttl=3600, closed_ttl=86400)
async def get_sec_financials(
    ticker: str,
    periods: int = 8,
    annual: bool = True,
) -> dict[str, Any]:
    """Multi-period financials straight from SEC XBRL, including the accrual gap
    (net_income minus operating_cash_flow — positive means reported profit exceeds
    cash actually generated).
    """
    import mcp_server.edgar as edgar

    try:
        wide = edgar.financials(ticker, periods=periods, annual=annual)
    except Exception as e:
        logger.error("get_sec_financials failed for %s: %s", ticker, e)
        return {"ticker": ticker.upper(), "error": str(e)}

    if wide.empty:
        return {"ticker": ticker.upper(), "periods": [], "note": "no matching XBRL facts found"}

    source_url = wide.attrs.get("source_url")
    periods_out = _df_to_records(wide.rename_axis("period_end").reset_index())
    return {"ticker": ticker.upper(), "periods": periods_out, "source_url": source_url}


@smart_cache(open_ttl=3600, closed_ttl=86400)
async def get_shares_outstanding(ticker: str) -> dict[str, Any]:
    """Cover-page share count straight from the 10-Q/10-K, not an aggregator's
    derived figure. Flags (rather than silently returning) an implausible diluted
    count, including the "reported in thousands" mixup seen in the wild.
    """
    import mcp_server.edgar as edgar

    try:
        return {"ticker": ticker.upper(), **edgar.shares_outstanding(ticker)}
    except Exception as e:
        logger.error("get_shares_outstanding failed for %s: %s", ticker, e)
        return {"ticker": ticker.upper(), "error": str(e)}


@smart_cache(open_ttl=3600, closed_ttl=86400)
async def get_stakes_held(ticker: str, months: int = 24) -> dict[str, Any]:
    """AS-FILER 13D/13G: stakes this company holds in OTHER public companies —
    e.g. GME's 9.8% stake in eBay. Distinct from who owns THIS ticker; EDGAR
    indexes a 13D/G under both parties and this keeps only the filings where the
    ticker itself is the reporting person, not the issuer.
    """
    import mcp_server.edgar as edgar

    try:
        df = edgar.stakes_held(ticker, months=months)
    except Exception as e:
        logger.error("get_stakes_held failed for %s: %s", ticker, e)
        return {"ticker": ticker.upper(), "error": str(e)}

    rows = _df_to_records(df)
    return {"ticker": ticker.upper(), "count": len(rows), "stakes": rows}
