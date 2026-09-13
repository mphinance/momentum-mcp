"""TickerTrace client — daily ETF holdings intelligence.

`https://api.tickertrace.pro` is **deliberately open**: no key, no header, no
account. That is the product's own stated position ("fully open, no key
required"), not an oversight, so this client sends no credential and should not
grow one.

**Why this data matters more than it looks.** Actively-managed ETFs publish full
holdings daily; TickerTrace scrapes, normalises and diffs them every morning
across 71 funds and ~2,270 underlyings. So the signal is *yesterday vs today*,
not a 13F with a 90-day delay.

That makes it the one dataset in this estate whose value comes from
**accumulation rather than secrecy**. Dealer-gamma methodology is public and its
inputs are a licensable feed — a competitor with a chequebook reaches parity in
weeks. A cross-fund divergence or a layering pattern needs the whole panel
*through time*; someone starting today gets today forward and nothing behind it.
Tracking began 2026-02, and that history is not purchasable.

Practical consequence for anything built on this: it is the strongest free thing
we can hand a stranger, because it is simultaneously genuinely useful, costs
nothing to serve, and cannot be reproduced by forking the code that reads it.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tickertrace.pro"
TIMEOUT = 30.0

Category = Literal["active-equity", "option-income"]
Period = Literal["daily", "weekly", "monthly"]


def _fetch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET `path`, dropping None params so callers can pass optional filters through.

    Returns an `{"available": False, "error": ...}` envelope rather than raising:
    every caller is an MCP tool, and a tool that raises takes the whole call down
    where a tool that reports unavailability lets the model carry on with the
    other 60.
    """
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(f"{BASE_URL}{path}", params=clean)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("TickerTrace %s failed: %s", path, exc)
        return {"available": False, "error": f"TickerTrace unreachable: {exc}"}


# ── Signals and flow ─────────────────────────────────────────────────────────

def get_briefing() -> dict[str, Any]:
    return _fetch("/api/v1/briefing")


def get_signals(category: Category | None = None) -> dict[str, Any]:
    return _fetch("/api/v1/signals", {"category": category})


def get_institutional_flow(period: Period = "daily", limit: int = 25) -> dict[str, Any]:
    return _fetch("/api/v1/institutional", {"period": period, "limit": limit})


def get_institutional_trend(limit: int = 15) -> dict[str, Any]:
    return _fetch("/api/v1/institutional-trend", {"limit": limit})


def get_holdings_changes(
    provider: str | None = None,
    fund: str | None = None,
    direction: Literal["buying", "selling"] | None = None,
    period: Period = "daily",
    limit: int = 50,
    category: Category | None = None,
) -> dict[str, Any]:
    return _fetch(
        "/api/v1/changes",
        {
            "provider": provider,
            "fund": fund,
            "direction": direction,
            "period": period,
            "limit": limit,
            "category": category,
        },
    )


def get_divergences(category: Category | None = None) -> dict[str, Any]:
    return _fetch("/api/v1/divergences", {"category": category})


def get_layering_patterns(
    window_days: int = 7, min_funds: int = 3, limit: int = 20
) -> dict[str, Any]:
    return _fetch(
        "/api/v1/layering-patterns",
        {"window_days": window_days, "min_funds": min_funds, "limit": limit},
    )


def get_sector_flow(category: Category | None = None) -> dict[str, Any]:
    return _fetch("/api/v1/sectors", {"category": category})


# ── Lookups ──────────────────────────────────────────────────────────────────

def get_stock_activity(ticker: str) -> dict[str, Any]:
    return _fetch(f"/api/v1/stock/{ticker.upper()}")


def get_fund_detail(fund: str) -> dict[str, Any]:
    return _fetch(f"/api/v1/fund/{fund.upper()}")


def list_all_funds(category: Category | None = None) -> dict[str, Any]:
    return _fetch("/api/v1/funds", {"category": category})


def list_all_tickers(
    limit: int = 100,
    sort: Literal["funds", "weight"] = "funds",
    category: Category | None = None,
) -> dict[str, Any]:
    return _fetch("/api/v1/tickers", {"limit": limit, "sort": sort, "category": category})


# ── Option-income funds ──────────────────────────────────────────────────────

def get_income_overview() -> dict[str, Any]:
    return _fetch("/api/v1/income")


def get_income_fund_detail(fund: str) -> dict[str, Any]:
    return _fetch(f"/api/v1/income/{fund.upper()}")


def get_options_listings() -> dict[str, Any]:
    return _fetch("/api/v1/options-listings")


# ── Meta ─────────────────────────────────────────────────────────────────────

def get_signal_performance() -> dict[str, Any]:
    return _fetch("/api/v1/signal-performance")


def get_global_stats() -> dict[str, Any]:
    return _fetch("/api/v1/stats")
