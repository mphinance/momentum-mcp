"""SCANLINE tool tests, ported from mphinance/scanline tests/test_mcp.py.

The discovery tools run fully offline (they read the catalog and preset tables,
no network). The screen path is marked 'live' since it hits the real
tradingview endpoint. Upstream drives the server through fastmcp's in-memory
Client; here the tool functions are plain module-level callables in
mcp_server/scanline/tools.py, so `_call` invokes them directly and the MCP
wiring (names, prefix, prompts, resources) is covered once by
test_tools_and_resources_registered against whichever FastMCP is installed.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from mcp_server.scanline import tools as mcp_server
from mcp_server.scanline.tools import (
    PREFIX,
    _compute_breadth,
    _compute_dividend_quality,
    _days_to_earnings,
    _compute_earnings_radar,
    _compute_ema_stack,
    _compute_gap_scanner,
    _compute_momentum_consistency,
    _compute_new_highs_lows,
    _compute_relative_strength,
    _compute_sector_rotation,
    _compute_volume_leaders,
    _otc_guard,
    _payout_health,
    normalize_interval,
    prompt_names,
    rating_label,
    register_scanline_tools,
    resource_uris,
    tool_names,
)


def _server():
    """A bare FastMCP of whichever flavor is installed, with the tools mounted."""
    try:
        from mcp.server.fastmcp import FastMCP  # the API momentum's server.py uses
    except ModuleNotFoundError:
        from fastmcp import FastMCP
    m = FastMCP("scanline-test")
    register_scanline_tools(m)
    return m


# Every america tool that scans a broad universe and must therefore exclude OTC
# by default. Adding a new one without the guard should fail the test below, the
# same way a new unprotected preset fails test_every_unprotected_america_preset_
# is_guarded in test_backend.py.
OTC_GUARDED_TOOLS = [
    "run_factor_preset",
    "sector_breakdown",
    "market_breadth",
    "sector_rotation",
    "new_highs_lows",
    "volume_leaders",
    "top_movers",
    "momentum_consistency",
    "relative_strength_leaders",
    "ema_stack_scan",
    "earnings_radar",
    "gap_scanner",
    "dividend_screen",
]


def _call(name: str, args: dict | None = None):
    """Invoke one tool function directly (unprefixed name) and return its result."""
    return getattr(mcp_server, name)(**(args or {}))


def _list():
    m = _server()

    async def run():
        tools = [t.name for t in await m.list_tools()]
        resources = [str(r.uri) for r in await m.list_resources()]
        prompts = [p.name for p in await m.list_prompts()]
        return tools, resources, prompts

    return asyncio.run(run())


# --- wiring --------------------------------------------------------------

def test_tools_and_resources_registered():
    tools, resources, prompts = _list()
    expected_tools = {
        "list_markets", "search_fields", "list_operators", "list_presets",
        "list_factor_presets", "screen", "run_preset", "run_factor_preset",
        "lookup_symbol", "server_stats",
        # Wave 2: symbol intelligence.
        "search_symbols", "compare", "technical_rating", "analyze", "chart",
        "sector_breakdown",
        # Nightly 2026-06-22: top movers.
        "top_movers",
        # Nightly 2026-06-23: market breadth.
        "market_breadth",
        # Nightly 2026-06-24: sector rotation.
        "sector_rotation",
        # Nightly 2026-06-25: new highs/lows breadth.
        "new_highs_lows",
        # Nightly 2026-06-26: volume leaders.
        "volume_leaders",
        # Nightly 2026-06-27: momentum consistency.
        "momentum_consistency",
        # Nightly 2026-06-28: sector-relative strength leaders.
        "relative_strength_leaders",
        # Nightly 2026-06-29: EMA stack alignment scan.
        "ema_stack_scan",
        # Nightly 2026-06-30: earnings catalyst radar.
        "earnings_radar",
        # Nightly 2026-07-01: gap scanner with fill tracking.
        "gap_scanner",
        # Nightly 2026-07-02: dividend quality screen.
        "dividend_screen",
        # analyze()'s fundamentals counterpart: valuation/profitability/growth/leverage/dividends.
        "fundamentals",
    }
    assert {PREFIX + n for n in expected_tools} == set(tools)
    assert set(tools) == set(tool_names())
    assert {"screener://fields", "screener://presets", "screener://operators"} == set(resources)
    assert set(resources) == set(resource_uris())
    assert {PREFIX + n for n in ("momentum_breakouts", "oversold_quality", "rank_by_factor", "read_symbol")} == set(prompts)
    assert set(prompts) == set(prompt_names())


def test_prefix_is_scan():
    """The prefix is the contract the README and docs/SCANLINE.md document."""
    assert PREFIX == "scan_"
    assert all(n.startswith("scan_") for n in tool_names())


def test_no_collision_with_the_rest_of_the_momentum_server():
    """A scan_ name must not shadow an existing momentum tool, and vice versa.

    server.py registers its own tools by bare function name. If a future tool
    there is ever called scan_something, or a scanline tool loses its prefix,
    FastMCP would either raise or silently keep one of the two. Check the
    source without importing server.py, whose other imports (yfinance,
    pandas-ta, chromadb) are not part of the offline suite.
    """
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "mcp_server" / "server.py").read_text()
    native = set(re.findall(r"@mcp\.tool\(\)\s*\n\s*(?:async )?def (\w+)\(", src))
    assert native, "could not find any natively registered tool in server.py"
    assert not native & set(tool_names())
    assert not {n for n in native if n.startswith(PREFIX)}


def test_call_through_the_server_matches_a_direct_call():
    """The MCP path returns what the function returns, prefix and all."""
    m = _server()

    async def run():
        return await m.call_tool(PREFIX + "list_markets", {})

    out = asyncio.run(run())
    text = str(out)
    for market in _call("list_markets"):
        assert market["id"] in text


# --- Wave 2 pure helpers (offline) --------------------------------------

def test_rating_label_bands():
    assert rating_label(0.7) == "Strong Buy"
    assert rating_label(0.2) == "Buy"
    assert rating_label(0.0) == "Neutral"
    assert rating_label(-0.3) == "Sell"
    assert rating_label(-0.8) == "Strong Sell"
    assert rating_label(None) == "Unknown"


def test_normalize_interval():
    assert normalize_interval("4h") == "240"
    assert normalize_interval("1h") == "60"
    assert normalize_interval("daily") == "D"
    assert normalize_interval("1W") == "W"
    assert normalize_interval("weekly") == "W"
    assert normalize_interval("nonsense") == "D"


# --- discovery (offline) -------------------------------------------------

def test_list_markets():
    markets = _call("list_markets")
    ids = {m["id"] for m in markets}
    assert {"america", "crypto", "forex", "futures", "bond", "cfd"} <= ids


def test_search_fields_finds_rsi():
    out = _call("search_fields", {"query": "rsi", "limit": 10})
    ids = {f["id"] for f in out["fields"]}
    assert "RSI" in ids
    assert out["count"] <= 10
    assert all("curated" in f for f in out["fields"])


def test_search_fields_group_filter():
    out = _call("search_fields", {"group": "Oscillators", "limit": 100})
    assert out["count"] > 0
    assert all(f["group"] == "Oscillators" for f in out["fields"])


def test_list_operators_includes_cross_field():
    ops = {o["op"] for o in _call("list_operators")}
    assert {"crosses_above", "crosses_below", "between", "above_pct"} <= ops


def test_list_presets_and_group_filter():
    allp = _call("list_presets")
    assert len(allp) >= 22
    momentum = _call("list_presets", {"group": "Momentum"})
    assert len(momentum) > 0
    assert all(p["group"] == "Momentum" for p in momentum)


def test_list_factor_presets():
    fps = _call("list_factor_presets")
    ids = {f["id"] for f in fps}
    assert {"momentum", "value", "quality", "growth", "low_vol"} <= ids


def test_server_stats_shape():
    s = _call("server_stats")
    assert s["markets"] == 6
    assert s["fields_indexed"] > 150
    assert "cache_hit_rate" in s


# --- error paths (offline) ----------------------------------------------

def test_run_preset_unknown_id():
    out = _call("run_preset", {"preset_id": "does_not_exist"})
    assert "error" in out


def test_run_factor_preset_unknown_id():
    out = _call("run_factor_preset", {"factor_preset_id": "does_not_exist"})
    assert "error" in out


# --- OTC guard on the tools (offline) ------------------------------------

def test_otc_guard_appends_the_exclusion_on_america():
    out, guard = _otc_guard("america", [], False)
    assert out == [{"field": "exchange", "op": "not_in", "value": ["OTC"]}]
    assert guard["active"] is True


def test_otc_guard_preserves_caller_filters():
    mine = [{"field": "market_cap_basic", "op": ">", "value": 1e9}]
    out, _ = _otc_guard("america", mine, False)
    assert out[0] == mine[0], "the caller's own filter must survive"
    assert len(out) == 2


def test_otc_guard_does_not_mutate_the_caller_list():
    mine = [{"field": "close", "op": ">", "value": 5}]
    out, _ = _otc_guard("america", mine, False)
    assert len(mine) == 1, "the caller's list was mutated in place"
    assert out is not mine


def test_otc_guard_lifted_by_include_otc():
    out, guard = _otc_guard("america", [], True)
    assert out == []
    assert guard["active"] is False


def test_otc_guard_skipped_off_america():
    # `exchange` carries US venue names. The condition is meaningless in crypto
    # and would silently return nothing there.
    for market in ("crypto", "forex", "futures", "bond", "cfd"):
        out, guard = _otc_guard(market, [], False)
        assert out == [], market
        assert guard is None, market


def test_otc_guard_defers_to_a_caller_supplied_exchange_filter():
    # Someone asking for OTC explicitly has already said what they want.
    mine = [{"field": "exchange", "op": "==", "value": "OTC"}]
    out, guard = _otc_guard("america", mine, False)
    assert out == mine
    assert guard["active"] is False


def test_every_broad_america_tool_takes_include_otc():
    """The point of the guard: a new broad scan must not arrive unguarded.

    top_movers and gap_scanner were measured at 100% OTC on every list they
    returned, because both rank on a percentage move and a sub-penny tick is a
    four figure percentage. Any future tool that scans the america universe has
    the same exposure, so the parameter is the thing to enforce.
    """
    import inspect

    for name in OTC_GUARDED_TOOLS:
        fn = getattr(mcp_server, name)
        params = inspect.signature(fn).parameters
        assert "include_otc" in params, f"{name} has no include_otc parameter"
        assert params["include_otc"].default is False, (
            f"{name} must exclude OTC by default"
        )


def test_unavailable_presets_are_refused_with_a_reason():
    """An unavailable preset must never reach the screen path.

    Running it can only return zero rows, and zero rows reads as "no matches
    today" rather than "this data does not exist". The refusal has to carry the
    reason, since that is the only thing distinguishing the two.
    """
    from mcp_server.scanline.presets import PRESETS

    unavailable = [p for p in PRESETS if p.get("unavailable")]
    assert unavailable, "expected at least one preset marked unavailable"
    for p in unavailable:
        out = _call("run_preset", {"preset_id": p["id"]})
        assert "error" in out, f"{p['id']} ran instead of being refused"
        assert out.get("reason"), f"{p['id']} was refused without a reason"
        assert out.get("count") == 0
        assert out.get("rows") == []


# --- live screen path ----------------------------------------------------

@pytest.mark.live
def test_screen_live_with_analytics():
    out = _call("screen", {
        "market": "america",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e10}],
        "columns": ["name", "close", "change", "volume"],
        "computed": [{"id": "dollar_vol", "expr": "close*volume"}],
        "stats": [{"fn": "zscore", "field": "change"}],
        "sort": [{"field": "volume", "dir": "desc"}],
        "limit": 5,
    })
    assert out["count"] > 0
    assert out["returned"] <= 5
    assert "dollar_vol" in out["columns"]
    assert "zscore(change)" in out["columns"]
    assert out["table"]


@pytest.mark.live
def test_lookup_symbol_live():
    out = _call("lookup_symbol", {"ticker": "AAPL"})
    assert out.get("row", {}).get("name") == "AAPL"


@pytest.mark.live
def test_search_symbols_live():
    out = _call("search_symbols", {"query": "apple", "limit": 5})
    names = {s.get("name") for s in out["symbols"]}
    assert "AAPL" in names


@pytest.mark.live
def test_compare_live():
    out = _call("compare", {"tickers": ["NVDA", "AMD"]})
    names = {r.get("name") for r in out["rows"]}
    assert {"NVDA", "AMD"} <= names


@pytest.mark.live
def test_technical_rating_live():
    out = _call("technical_rating", {"ticker": "AAPL", "timeframes": ["1d"]})
    assert "1d" in out["ratings"]
    assert out["ratings"]["1d"]["overall"] in {
        "Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell", "Unknown",
    }


@pytest.mark.live
def test_analyze_multi_timeframe_live():
    out = _call("analyze", {"ticker": "AAPL"})
    assert out["summary"]
    mtf = out["multi_timeframe"]
    assert "alignment" in mtf
    # At least the daily and weekly timeframes should populate.
    assert {"1d", "1w"} <= set(mtf["by_timeframe"].keys())
    for tf in mtf["by_timeframe"].values():
        assert tf["bias"] in {"bull", "bear", "mixed"}


@pytest.mark.live
def test_fundamentals_live():
    out = _call("fundamentals", {"ticker": "AAPL"})
    assert out["summary"]
    assert out["valuation"]["price_earnings_ttm"] > 0
    assert out["growth"]["total_revenue_yoy_growth_ttm"] is not None
    assert out["dividends"]["dividends_yield"] > 0
    assert out["earnings"]["days_to_earnings"] >= 0


@pytest.mark.live
def test_fundamentals_columns_are_populated_in_their_own_market():
    """Same dead-field regression check as
    test_backend.test_displayed_and_scored_fields_are_populated_in_their_own_market, for the
    columns fundamentals() reads. This is the test that would have caught
    revenue_growth_ttm_yoy / earnings_per_share_diluted_growth_percent_ttm_yoy /
    dividend_yield_recent / payout_ratio / days_to_earnings all being null on every US row before
    the tool shipped, rather than after.
    """
    from mcp_server.scanline.tools import _FUNDAMENTALS_COLUMNS
    from mcp_server.scanline.models import Filter, ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    cols = [c for c in _FUNDAMENTALS_COLUMNS if c not in ("name", "description", "sector",
                                                            "industry", "number_of_employees")]
    resp = run_screen(ScreenRequest(
        market="america",
        filters=[Filter(field="market_cap_basic", op=">", value=1e9)],
        columns=cols,
        limit=200,
    ))
    assert not resp["meta"].get("error"), resp["meta"].get("error")
    assert resp["rows"]
    for field in cols:
        populated = sum(1 for r in resp["rows"] if r.get(field) is not None)
        assert populated, (
            f"fundamentals() column '{field}' is null for all {len(resp['rows'])} sampled "
            f"large-cap rows, it validates against the catalog but returns nothing live"
        )


@pytest.mark.live
def test_chart_resolves_ticker_live():
    out = _call("chart", {"ticker": "NVDA", "interval": "4h"})
    assert out["symbol"] == "NASDAQ:NVDA"
    assert out["interval"] == "240"
    assert out["chart_url"].startswith("https://www.tradingview.com/chart/")
    assert "embed-widget-advanced-chart.js" in out["embed_html"]


# --- top_movers (offline wiring + live data) ----------------------------

def test_top_movers_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "top_movers" in tools


@pytest.mark.live
def test_top_movers_live():
    out = _call("top_movers", {"market": "america", "n": 5})
    assert "gainers" in out
    assert "losers" in out
    assert len(out["gainers"]) <= 5
    assert len(out["losers"]) <= 5
    assert out["gainers_table"]
    assert out["losers_table"]
    # Gainers should be sorted descending by change.
    changes = [r.get("change") for r in out["gainers"] if r.get("change") is not None]
    assert changes == sorted(changes, reverse=True)
    # Losers should be sorted ascending.
    changes_l = [r.get("change") for r in out["losers"] if r.get("change") is not None]
    assert changes_l == sorted(changes_l)


@pytest.mark.live
def test_top_movers_with_filter_live():
    out = _call("top_movers", {
        "market": "america",
        "n": 3,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e10}],
    })
    assert "gainers" in out and "losers" in out
    assert out["universe"] > 0


# --- market_breadth (offline wiring + math + live data) -----------------

def test_market_breadth_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "market_breadth" in tools


def test_compute_breadth_basic():
    rows = [
        {"close": 10, "change": 2.0,  "RSI": 65, "SMA50": 9,  "SMA200": 8},
        {"close": 20, "change": -1.0, "RSI": 28, "SMA50": 22, "SMA200": 18},
        {"close": 30, "change": 0.0,  "RSI": 55, "SMA50": 28, "SMA200": 25},
        {"close": 40, "change": 3.0,  "RSI": 72, "SMA50": 35, "SMA200": 30},
    ]
    b = _compute_breadth(rows)
    assert b["sample"] == 4
    assert b["advancers"] == 2
    assert b["decliners"] == 1
    assert b["unchanged"] == 1
    assert b["ad_ratio"] == pytest.approx(2.0)
    assert b["pct_advancers"] == pytest.approx(50.0)
    assert b["pct_decliners"] == pytest.approx(25.0)
    # Row0 close(10) > SMA50(9), Row2 close(30) > SMA50(28), Row3 close(40) > SMA50(35)
    # Row1 close(20) < SMA50(22)  -> 3/4 above = 75%
    assert b["pct_above_sma50"] == pytest.approx(75.0)
    # All rows have close > SMA200 -> 100%
    assert b["pct_above_sma200"] == pytest.approx(100.0)
    # RSI: 65, 28, 55, 72  -> avg = (65+28+55+72)/4 = 55.0
    assert b["avg_rsi"] == pytest.approx(55.0)
    # Overbought (>=70): row3(72) -> 1/4 = 25%
    assert b["pct_rsi_overbought"] == pytest.approx(25.0)
    # Oversold (<=30): row1(28) -> 1/4 = 25%
    assert b["pct_rsi_oversold"] == pytest.approx(25.0)


def test_compute_breadth_empty():
    b = _compute_breadth([])
    assert b == {"sample": 0}


def test_compute_breadth_no_decliners():
    # ad_ratio is None when there are no decliners.
    rows = [{"close": 5, "change": 1.0, "RSI": 60, "SMA50": 4, "SMA200": 3}]
    b = _compute_breadth(rows)
    assert b["decliners"] == 0
    assert b["ad_ratio"] is None
    assert b["advancers"] == 1


def test_compute_breadth_missing_fields():
    # Rows without SMA50/SMA200/RSI should produce None for those metrics.
    rows = [{"close": 10, "change": 1.0}, {"close": 20, "change": -1.0}]
    b = _compute_breadth(rows)
    assert b["pct_above_sma50"] is None
    assert b["pct_above_sma200"] is None
    assert b["avg_rsi"] is None


@pytest.mark.live
def test_market_breadth_live():
    out = _call("market_breadth", {"market": "america", "limit": 200})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 200
    assert out["advancers"] + out["decliners"] + out["unchanged"] == out["sample"]
    assert 0 <= out["pct_advancers"] <= 100
    assert 0 <= out["pct_decliners"] <= 100
    # With a broad US scan, SMA data should be present.
    assert out["pct_above_sma50"] is not None
    assert out["pct_above_sma200"] is not None


@pytest.mark.live
def test_market_breadth_with_filter_live():
    out = _call("market_breadth", {
        "market": "america",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e10}],
        "limit": 100,
    })
    assert "sample" in out
    assert out["sample"] > 0


# --- sector_rotation (offline math + live data) --------------------------

def test_sector_rotation_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "sector_rotation" in tools


def test_compute_sector_rotation_basic():
    rows = [
        {"sector": "Tech",    "change": 2.0,  "Perf.1M": 5.0,  "Perf.YTD": 20.0, "RSI": 60, "market_cap_basic": 1e12},
        {"sector": "Tech",    "change": 3.0,  "Perf.1M": 7.0,  "Perf.YTD": 25.0, "RSI": 65, "market_cap_basic": 5e11},
        {"sector": "Energy",  "change": -1.0, "Perf.1M": -2.0, "Perf.YTD": -5.0, "RSI": 40, "market_cap_basic": 3e11},
        {"sector": "Health",  "change": 0.5,  "Perf.1M": 1.0,  "Perf.YTD": 8.0,  "RSI": 52, "market_cap_basic": 2e11},
    ]
    result = _compute_sector_rotation(rows)
    secs = {s["sector"]: s for s in result}

    # Three sectors present.
    assert len(result) == 3
    assert set(secs) == {"Tech", "Energy", "Health"}

    # Tech should have the highest momentum_score.
    assert result[0]["sector"] == "Tech"
    # Energy (negative across all timeframes) should be last.
    assert result[-1]["sector"] == "Energy"

    # Per-sector averages.
    import pytest as _pt
    assert secs["Tech"]["avg_change"] == _pt.approx(2.5, abs=1e-3)
    assert secs["Tech"]["avg_perf_1m"] == _pt.approx(6.0, abs=1e-3)
    assert secs["Tech"]["count"] == 2

    # momentum_score is in [0, 1].
    for s in result:
        if s["momentum_score"] is not None:
            assert 0.0 <= s["momentum_score"] <= 1.0

    # Best scorer is 1.0 or close; worst is 0.0 or close.
    assert result[0]["momentum_score"] == _pt.approx(1.0, abs=1e-6)
    assert result[-1]["momentum_score"] == _pt.approx(0.0, abs=1e-6)


def test_compute_sector_rotation_empty():
    result = _compute_sector_rotation([])
    assert result == []


def test_compute_sector_rotation_missing_perf_fields():
    # Rows without Perf.1M and Perf.YTD: score is based on avg_change only.
    rows = [
        {"sector": "Tech",   "change": 3.0,  "RSI": 65},
        {"sector": "Energy", "change": -1.0, "RSI": 40},
    ]
    result = _compute_sector_rotation(rows)
    secs = {s["sector"]: s for s in result}

    assert secs["Tech"]["avg_perf_1m"] is None
    assert secs["Tech"]["avg_perf_ytd"] is None
    # Scoring should still work using avg_change alone.
    assert result[0]["sector"] == "Tech"
    assert result[0]["momentum_score"] == 1.0
    assert result[1]["momentum_score"] == 0.0


def test_compute_sector_rotation_single_sector():
    # Only one sector: normalization undefined, momentum_score is None.
    rows = [
        {"sector": "Tech", "change": 2.0, "Perf.1M": 5.0, "Perf.YTD": 20.0, "RSI": 60},
        {"sector": "Tech", "change": 3.0, "Perf.1M": 7.0, "Perf.YTD": 25.0, "RSI": 65},
    ]
    result = _compute_sector_rotation(rows)
    assert len(result) == 1
    # Single sector cannot be normalized against others, score is None.
    assert result[0]["momentum_score"] is None


def test_compute_sector_rotation_unknown_sector():
    # Rows without a sector field land in "Unknown".
    rows = [
        {"change": 1.0, "Perf.1M": 2.0, "Perf.YTD": 5.0},
        {"sector": None, "change": -1.0},
    ]
    result = _compute_sector_rotation(rows)
    secs = {s["sector"]: s for s in result}
    assert "Unknown" in secs
    assert secs["Unknown"]["count"] == 2


def test_compute_sector_rotation_sorted_desc():
    rows = [
        {"sector": "A", "change": 5.0,  "Perf.1M": 10.0, "Perf.YTD": 30.0},
        {"sector": "B", "change": 2.0,  "Perf.1M": 4.0,  "Perf.YTD": 10.0},
        {"sector": "C", "change": -2.0, "Perf.1M": -5.0, "Perf.YTD": -10.0},
    ]
    result = _compute_sector_rotation(rows)
    scores = [s["momentum_score"] for s in result if s["momentum_score"] is not None]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.live
def test_sector_rotation_live():
    out = _call("sector_rotation", {"market": "america", "limit": 500})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sampled"] <= 500
    sects = out["sectors"]
    assert len(sects) > 3
    # Each sector has required fields.
    for s in sects:
        assert "sector" in s
        assert "count" in s
        assert "avg_change" in s
        assert "momentum_score" in s
    # Sorted desc by momentum_score (None values trailing).
    scores = [s["momentum_score"] for s in sects]
    non_null = [v for v in scores if v is not None]
    assert non_null == sorted(non_null, reverse=True)


@pytest.mark.live
def test_sector_rotation_with_filter_live():
    out = _call("sector_rotation", {
        "market": "america",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 300,
    })
    assert "sectors" in out
    assert out["sampled"] > 0


# --- new_highs_lows (offline math + live data) ---------------------------

def test_new_highs_lows_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "new_highs_lows" in tools


def test_compute_new_highs_lows_basic():
    rows = [
        # At the 52w high exactly.
        {"name": "A", "close": 100.0, "price_52_week_high": 100.0, "price_52_week_low": 50.0, "change": 2.0, "sector": "Tech"},
        # Within 1% of the 52w high (counts as new high at default 2% threshold).
        {"name": "B", "close": 99.0,  "price_52_week_high": 100.0, "price_52_week_low": 40.0, "change": 1.5, "sector": "Tech"},
        # In the middle, neither high nor low.
        {"name": "C", "close": 70.0,  "price_52_week_high": 100.0, "price_52_week_low": 50.0, "change": 0.5, "sector": "Energy"},
        # At the 52w low exactly.
        {"name": "D", "close": 50.0,  "price_52_week_high": 100.0, "price_52_week_low": 50.0, "change": -3.0, "sector": "Energy"},
        # Within 1% of the 52w low (counts as new low at default 2% threshold).
        {"name": "E", "close": 50.4,  "price_52_week_high": 90.0,  "price_52_week_low": 50.0, "change": -1.0, "sector": "Finance"},
    ]
    result = _compute_new_highs_lows(rows)
    assert result["sample"] == 5
    assert result["new_highs_count"] == 2   # A and B
    assert result["new_lows_count"] == 2    # D and E
    assert result["nh_nl_ratio"] == pytest.approx(1.0, abs=1e-3)
    assert result["nh_nl_diff"] == 0
    assert result["pct_new_highs"] == pytest.approx(40.0)
    assert result["pct_new_lows"] == pytest.approx(40.0)
    # Sorted: new highs have pct_from_high closest to 0 first (A=0.0, B=-1.0)
    highs_names = [h["name"] for h in result["new_highs"]]
    assert highs_names[0] == "A"
    # Sorted: new lows have pct_from_low closest to 0 first (D=0.0, E=0.8)
    lows_names = [l["name"] for l in result["new_lows"]]
    assert lows_names[0] == "D"


def test_compute_new_highs_lows_empty():
    result = _compute_new_highs_lows([])
    assert result["sample"] == 0
    assert result["new_highs"] == []
    assert result["new_lows"] == []


def test_compute_new_highs_lows_no_new_highs_or_lows():
    # All stocks are mid-range, well away from 52w extremes.
    rows = [
        {"name": "X", "close": 60.0, "price_52_week_high": 100.0, "price_52_week_low": 40.0},
        {"name": "Y", "close": 70.0, "price_52_week_high": 100.0, "price_52_week_low": 40.0},
    ]
    result = _compute_new_highs_lows(rows)
    assert result["new_highs_count"] == 0
    assert result["new_lows_count"] == 0
    assert result["nh_nl_ratio"] is None
    assert result["nh_nl_diff"] == 0
    assert result["pct_new_highs"] == pytest.approx(0.0)
    assert result["pct_new_lows"] == pytest.approx(0.0)


def test_compute_new_highs_lows_threshold():
    # Stock is 3% below its 52w high. Default threshold (2%) excludes it;
    # a threshold of 5% should include it.
    rows = [{"name": "Z", "close": 97.0, "price_52_week_high": 100.0, "price_52_week_low": 50.0}]
    tight = _compute_new_highs_lows(rows, threshold=0.02)
    loose = _compute_new_highs_lows(rows, threshold=0.05)
    assert tight["new_highs_count"] == 0
    assert loose["new_highs_count"] == 1


def test_compute_new_highs_lows_missing_52w_fields():
    # Rows without price_52_week_high/low are silently skipped.
    rows = [
        {"name": "A", "close": 100.0},
        {"name": "B", "close": 50.0, "price_52_week_high": None, "price_52_week_low": None},
    ]
    result = _compute_new_highs_lows(rows)
    assert result["new_highs_count"] == 0
    assert result["new_lows_count"] == 0
    assert result["sample"] == 2


def test_compute_new_highs_lows_no_lows_ratio_none():
    # When there are no new lows the ratio is None to avoid division by zero.
    rows = [
        {"name": "A", "close": 100.0, "price_52_week_high": 100.0, "price_52_week_low": 30.0},
        {"name": "B", "close": 70.0,  "price_52_week_high": 100.0, "price_52_week_low": 30.0},
    ]
    result = _compute_new_highs_lows(rows)
    assert result["new_highs_count"] == 1
    assert result["new_lows_count"] == 0
    assert result["nh_nl_ratio"] is None
    assert result["nh_nl_diff"] == 1


def test_compute_new_highs_lows_pct_from_high_values():
    # pct_from_high must be 0 when at the exact high, negative when below it.
    rows = [{"name": "A", "close": 100.0, "price_52_week_high": 100.0, "price_52_week_low": 50.0}]
    result = _compute_new_highs_lows(rows)
    assert result["new_highs"][0]["pct_from_high"] == pytest.approx(0.0)


@pytest.mark.live
def test_new_highs_lows_live():
    out = _call("new_highs_lows", {"market": "america", "limit": 300})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 300
    assert "new_highs_count" in out
    assert "new_lows_count" in out
    assert "nh_nl_diff" in out
    assert isinstance(out["new_highs"], list)
    assert isinstance(out["new_lows"], list)
    # All returned new highs must have pct_from_high >= -2%.
    for h in out["new_highs"]:
        assert h["pct_from_high"] >= -2.01


@pytest.mark.live
def test_new_highs_lows_with_filter_live():
    out = _call("new_highs_lows", {
        "market": "america",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e10}],
        "limit": 200,
    })
    assert "new_highs_count" in out
    assert out["sample"] > 0


# --- volume_leaders (offline math + live data) ---------------------------

def test_volume_leaders_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "volume_leaders" in tools


def test_compute_volume_leaders_basic():
    rows = [
        # High rvol, up move.
        {"name": "A", "close": 50.0, "change": 3.5, "rvol": None, "relative_volume_10d_calc": 4.2, "volume": 1_000_000, "sector": "Tech"},
        # Medium rvol, down move.
        {"name": "B", "close": 20.0, "change": -1.2, "relative_volume_10d_calc": 2.1, "volume": 500_000, "sector": "Energy"},
        # Below threshold (1.0 < 1.5), should be excluded.
        {"name": "C", "close": 30.0, "change": 0.5, "relative_volume_10d_calc": 1.0, "volume": 300_000, "sector": "Tech"},
        # At threshold exactly, up.
        {"name": "D", "close": 10.0, "change": 0.1, "relative_volume_10d_calc": 1.5, "volume": 200_000, "sector": "Finance"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    assert result["sample"] == 4
    assert result["count"] == 3         # A, B, D qualify; C excluded
    assert result["min_rvol"] == 1.5
    names = [r["name"] for r in result["leaders"]]
    assert "A" in names and "B" in names and "D" in names
    assert "C" not in names
    bd = result["by_direction"]
    assert bd["up"] == 2    # A and D
    assert bd["down"] == 1  # B
    assert bd["flat"] == 0
    assert bd["pct_up"] == pytest.approx(2 / 3 * 100, abs=0.2)
    assert bd["pct_down"] == pytest.approx(1 / 3 * 100, abs=0.2)


def test_compute_volume_leaders_sorted_by_rvol_desc():
    rows = [
        {"name": "Low",  "change": 1.0, "relative_volume_10d_calc": 2.0, "sector": "A"},
        {"name": "High", "change": 1.0, "relative_volume_10d_calc": 5.0, "sector": "B"},
        {"name": "Mid",  "change": 1.0, "relative_volume_10d_calc": 3.0, "sector": "C"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    rvols = [r["rvol"] for r in result["leaders"]]
    assert rvols == sorted(rvols, reverse=True)
    assert result["leaders"][0]["name"] == "High"


def test_compute_volume_leaders_empty():
    result = _compute_volume_leaders([])
    assert result["sample"] == 0
    assert result["count"] == 0
    assert result["leaders"] == []
    assert result["by_direction"] == {}
    assert result["by_sector"] == []


def test_compute_volume_leaders_none_below_threshold():
    rows = [
        {"name": "X", "change": 1.0, "relative_volume_10d_calc": 0.8, "sector": "Tech"},
        {"name": "Y", "change": -1.0, "relative_volume_10d_calc": 1.2, "sector": "Energy"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=2.0)
    assert result["count"] == 0
    assert result["leaders"] == []
    bd = result["by_direction"]
    assert bd["pct_up"] is None
    assert bd["pct_down"] is None


def test_compute_volume_leaders_missing_rvol_field():
    # Rows without relative_volume_10d_calc are silently skipped.
    rows = [
        {"name": "A", "change": 2.0, "sector": "Tech"},
        {"name": "B", "change": -1.0, "relative_volume_10d_calc": None, "sector": "Energy"},
        {"name": "C", "change": 1.0, "relative_volume_10d_calc": 3.0, "sector": "Finance"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    assert result["sample"] == 3
    assert result["count"] == 1
    assert result["leaders"][0]["name"] == "C"


def test_compute_volume_leaders_missing_change_is_flat():
    # Rows without change are classified as flat, not dropped.
    rows = [
        {"name": "A", "relative_volume_10d_calc": 2.5, "sector": "Tech"},
        {"name": "B", "relative_volume_10d_calc": 2.0, "change": None, "sector": "Energy"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    assert result["count"] == 2
    assert all(r["direction"] == "flat" for r in result["leaders"])
    assert result["by_direction"]["flat"] == 2
    assert result["by_direction"]["up"] == 0
    assert result["by_direction"]["down"] == 0


def test_compute_volume_leaders_sector_breakdown():
    rows = [
        {"name": "A1", "change": 1.0, "relative_volume_10d_calc": 3.0, "sector": "Tech"},
        {"name": "A2", "change": 2.0, "relative_volume_10d_calc": 2.5, "sector": "Tech"},
        {"name": "B1", "change": -1.0, "relative_volume_10d_calc": 4.0, "sector": "Energy"},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    by_sec = {s["sector"]: s for s in result["by_sector"]}
    assert "Tech" in by_sec and "Energy" in by_sec
    assert by_sec["Tech"]["count"] == 2
    assert by_sec["Tech"]["up"] == 2
    assert by_sec["Tech"]["down"] == 0
    assert by_sec["Energy"]["count"] == 1
    assert by_sec["Energy"]["down"] == 1
    assert by_sec["Tech"]["avg_rvol"] == pytest.approx(2.75, abs=0.01)
    # Sorted by count desc: Tech (2) before Energy (1).
    assert result["by_sector"][0]["sector"] == "Tech"


def test_compute_volume_leaders_no_sector_field():
    # Rows without a sector land in "Unknown".
    rows = [
        {"name": "X", "change": 1.0, "relative_volume_10d_calc": 2.0},
        {"name": "Y", "change": -1.0, "relative_volume_10d_calc": 3.0, "sector": None},
    ]
    result = _compute_volume_leaders(rows, min_rvol=1.5)
    assert result["count"] == 2
    by_sec = {s["sector"]: s for s in result["by_sector"]}
    assert "Unknown" in by_sec
    assert by_sec["Unknown"]["count"] == 2


@pytest.mark.live
def test_volume_leaders_live():
    out = _call("volume_leaders", {"market": "america", "limit": 300, "min_rvol": 1.5})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 300
    assert "count" in out
    assert "by_direction" in out
    assert "by_sector" in out
    assert isinstance(out["leaders"], list)
    # Every leader must have rvol >= min_rvol.
    for r in out["leaders"]:
        assert r["rvol"] >= 1.5
    # Leaders sorted rvol desc.
    rvols = [r["rvol"] for r in out["leaders"]]
    assert rvols == sorted(rvols, reverse=True)
    # Direction values are the allowed set.
    for r in out["leaders"]:
        assert r["direction"] in {"up", "down", "flat"}


@pytest.mark.live
def test_volume_leaders_with_filter_live():
    out = _call("volume_leaders", {
        "market": "america",
        "min_rvol": 2.0,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 200,
        "top": 20,
    })
    assert "count" in out
    assert len(out["leaders"]) <= 20


# --- momentum_consistency (offline math + live data) -------------------------

def test_momentum_consistency_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "momentum_consistency" in tools


def test_compute_momentum_consistency_basic():
    rows = [
        # Strong bull: all 5 timeframes positive.
        {"name": "A", "close": 100, "change": 2.0, "sector": "Tech",
         "Perf.W": 3.0, "Perf.1M": 8.0, "Perf.3M": 15.0, "Perf.YTD": 30.0,
         "market_cap_basic": 1e12},
        # Mixed: 3 of 5 positive (1d, 1W, 1M positive; 3M, YTD negative).
        {"name": "B", "close": 50, "change": 1.0, "sector": "Energy",
         "Perf.W": 1.5, "Perf.1M": 2.0, "Perf.3M": -3.0, "Perf.YTD": -5.0,
         "market_cap_basic": 5e10},
        # Weak bull: only 1d positive, rest negative.
        {"name": "C", "close": 30, "change": 0.5, "sector": "Finance",
         "Perf.W": -1.0, "Perf.1M": -2.0, "Perf.3M": -5.0, "Perf.YTD": -8.0,
         "market_cap_basic": 2e10},
    ]
    result = _compute_momentum_consistency(rows, direction="bull")
    names = [r["name"] for r in result]
    # A should lead (score 1.0), then B (3 of 5 aligned), then C (1 of 5).
    assert names[0] == "A"
    assert names[-1] == "C"
    # A has consistency_score == 1.0 (all timeframes aligned).
    assert result[0]["consistency_score"] == pytest.approx(1.0)
    # Score is in [0, 1] for all rows.
    for r in result:
        assert 0.0 <= r["consistency_score"] <= 1.0
    # A has all 5 timeframes aligned.
    assert result[0]["timeframes_aligned"] == 5
    assert result[0]["positive_tf"] == ["1d", "1W", "1M", "3M", "YTD"]
    assert result[0]["negative_tf"] == []


def test_compute_momentum_consistency_empty():
    result = _compute_momentum_consistency([])
    assert result == []


def test_compute_momentum_consistency_bear_direction():
    rows = [
        # Bear: all 5 negative.
        {"name": "X", "close": 10, "change": -3.0, "sector": "Tech",
         "Perf.W": -4.0, "Perf.1M": -8.0, "Perf.3M": -12.0, "Perf.YTD": -20.0},
        # Only 1d negative.
        {"name": "Y", "close": 20, "change": -0.5, "sector": "Energy",
         "Perf.W": 1.0, "Perf.1M": 3.0, "Perf.3M": 5.0, "Perf.YTD": 10.0},
    ]
    result = _compute_momentum_consistency(rows, direction="bear")
    # X (all 5 negative) should score 1.0 and lead.
    assert result[0]["name"] == "X"
    assert result[0]["consistency_score"] == pytest.approx(1.0)
    assert result[0]["timeframes_aligned"] == 5
    # Y has only 1d negative.
    y = next(r for r in result if r["name"] == "Y")
    assert y["timeframes_aligned"] == 1
    # Y's score = 0.15 (only the 1d weight).
    assert y["consistency_score"] == pytest.approx(0.15, abs=1e-4)


def test_compute_momentum_consistency_missing_fields():
    # Rows missing some perf fields: weights are re-normalized over available data.
    rows = [
        # Only 1d and 1W present, both positive.
        {"name": "A", "close": 50, "change": 1.0, "Perf.W": 2.0},
        # Only 1d present, positive.
        {"name": "B", "close": 30, "change": 0.5},
    ]
    result = _compute_momentum_consistency(rows, direction="bull")
    a = next(r for r in result if r["name"] == "A")
    b = next(r for r in result if r["name"] == "B")
    # A: 1d (0.15) + 1W (0.20) both aligned, total available = 0.35 -> score = 1.0.
    assert a["consistency_score"] == pytest.approx(1.0)
    assert a["timeframes_total"] == 2
    # B: only 1d available and aligned -> score = 1.0 (1 aligned / 1 available).
    assert b["consistency_score"] == pytest.approx(1.0)
    assert b["timeframes_total"] == 1


def test_compute_momentum_consistency_no_data():
    # A row with no performance data at all gets score None.
    rows = [
        {"name": "Z", "close": 10},
        {"name": "A", "close": 20, "change": 1.0},
    ]
    result = _compute_momentum_consistency(rows, direction="bull")
    # Both lack full data, but "A" has 1d (change). "Z" has none at all.
    z = next(r for r in result if r["name"] == "Z")
    assert z["consistency_score"] is None
    # None-score rows sort to the end.
    assert result[-1]["name"] == "Z"


def test_compute_momentum_consistency_sorted_desc():
    rows = [
        {"name": "Lo",  "change": -1.0, "Perf.W": -2.0, "Perf.1M": -3.0, "Perf.3M": -4.0, "Perf.YTD": -5.0},
        {"name": "Hi",  "change":  2.0, "Perf.W":  3.0, "Perf.1M":  4.0, "Perf.3M":  5.0, "Perf.YTD":  6.0},
        {"name": "Mid", "change":  1.0, "Perf.W":  1.5, "Perf.1M": -1.0, "Perf.3M": -2.0, "Perf.YTD": -3.0},
    ]
    result = _compute_momentum_consistency(rows, direction="bull")
    scores = [r["consistency_score"] for r in result if r["consistency_score"] is not None]
    assert scores == sorted(scores, reverse=True)
    assert result[0]["name"] == "Hi"


def test_compute_momentum_consistency_score_bounds():
    # consistency_score must always be in [0, 1].
    rows = [
        {"name": str(i), "change": v, "Perf.W": v, "Perf.1M": v, "Perf.3M": v, "Perf.YTD": v}
        for i, v in enumerate([-10.0, -1.0, 0.0, 1.0, 10.0])
    ]
    for direction in ("bull", "bear"):
        result = _compute_momentum_consistency(rows, direction=direction)
        for r in result:
            if r["consistency_score"] is not None:
                assert 0.0 <= r["consistency_score"] <= 1.0


def test_compute_momentum_consistency_zero_change_not_aligned():
    # Exactly zero (flat) should not count as bull or bear aligned.
    rows = [{"name": "Flat", "change": 0.0, "Perf.W": 0.0, "Perf.1M": 0.0,
             "Perf.3M": 0.0, "Perf.YTD": 0.0}]
    bull = _compute_momentum_consistency(rows, direction="bull")
    bear = _compute_momentum_consistency(rows, direction="bear")
    assert bull[0]["consistency_score"] == pytest.approx(0.0)
    assert bear[0]["consistency_score"] == pytest.approx(0.0)
    assert bull[0]["timeframes_aligned"] == 0
    assert bear[0]["timeframes_aligned"] == 0


@pytest.mark.live
def test_momentum_consistency_live():
    out = _call("momentum_consistency", {"market": "america", "limit": 200, "top": 20})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 200
    assert out["direction"] == "bull"
    top = out["top"]
    assert isinstance(top, list)
    assert len(top) <= 20
    # Scores are in [0, 1] and sorted descending.
    scores = [r["consistency_score"] for r in top if r["consistency_score"] is not None]
    for s in scores:
        assert 0.0 <= s <= 1.0
    assert scores == sorted(scores, reverse=True)
    # Each row has the required keys.
    for r in top:
        assert "name" in r
        assert "consistency_score" in r
        assert "timeframes_aligned" in r
        assert "positive_tf" in r


@pytest.mark.live
def test_momentum_consistency_bear_live():
    out = _call("momentum_consistency", {
        "market": "america",
        "direction": "bear",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 200,
        "top": 10,
    })
    assert out["direction"] == "bear"
    assert len(out["top"]) <= 10


# --- relative_strength_leaders (offline math + live data) -----------------

def test_relative_strength_leaders_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "relative_strength_leaders" in tools


def test_compute_relative_strength_basic():
    # Tech sector average change = 1.0 ((3 + -1) / 2).
    # Energy sector average change = -2.0 (single stock).
    # Expected: TechHero (excess_1d = 3-1=+2) leads, TechLaggard (excess_1d=-1-1=-2) trails.
    rows = [
        {
            "name": "TechHero",   "close": 100, "change": 3.0,
            "sector": "Tech",     "market_cap_basic": 1e12,
            "Perf.1M": 10.0,      "Perf.YTD": 30.0,
        },
        {
            "name": "TechLaggard","close": 50,  "change": -1.0,
            "sector": "Tech",     "market_cap_basic": 5e10,
            "Perf.1M": -2.0,      "Perf.YTD": -5.0,
        },
        {
            "name": "EnergyName", "close": 30,  "change": -2.0,
            "sector": "Energy",   "market_cap_basic": 2e11,
            "Perf.1M": -3.0,      "Perf.YTD": -8.0,
        },
    ]
    result = _compute_relative_strength(rows)

    assert len(result) == 3
    by_name = {r["name"]: r for r in result}

    # TechHero: sector Tech avg change=1.0, excess_1d = 3.0 - 1.0 = 2.0.
    assert by_name["TechHero"]["excess_1d"] == pytest.approx(2.0)
    assert by_name["TechLaggard"]["excess_1d"] == pytest.approx(-2.0)
    # EnergyName is the only stock in Energy, excess_1d = -2.0 - (-2.0) = 0.0.
    assert by_name["EnergyName"]["excess_1d"] == pytest.approx(0.0)

    # TechHero should lead (highest composite excess).
    assert result[0]["name"] == "TechHero"

    # rs_score in [0, 100] for all rows.
    for r in result:
        if r["rs_score"] is not None:
            assert 0.0 <= r["rs_score"] <= 100.0


def test_compute_relative_strength_empty():
    result = _compute_relative_strength([])
    assert result == []


def test_compute_relative_strength_sector_avg_accuracy():
    # Two stocks in the same sector. Each stock's sector_avg_change = (5+1)/2 = 3.0.
    rows = [
        {"name": "A", "close": 10, "change": 5.0, "sector": "X", "Perf.1M": 10.0, "Perf.YTD": 20.0},
        {"name": "B", "close": 20, "change": 1.0, "sector": "X", "Perf.1M": 2.0,  "Perf.YTD": 5.0},
    ]
    result = _compute_relative_strength(rows)
    by_name = {r["name"]: r for r in result}

    # sector_avg_change should be (5+1)/2 = 3.0 for both.
    assert by_name["A"]["sector_avg_change"] == pytest.approx(3.0)
    assert by_name["B"]["sector_avg_change"] == pytest.approx(3.0)

    # A excess_1d = 5 - 3 = 2.0; B excess_1d = 1 - 3 = -2.0.
    assert by_name["A"]["excess_1d"] == pytest.approx(2.0)
    assert by_name["B"]["excess_1d"] == pytest.approx(-2.0)

    # A should score higher than B.
    assert by_name["A"]["rs_score"] > by_name["B"]["rs_score"]


def test_compute_relative_strength_single_stock_per_sector():
    # When a stock is the only one in its sector, sector_avg = its own value,
    # so every excess is 0.0 and rs_score lands in the middle (50).
    rows = [
        {"name": "Solo", "close": 100, "change": 5.0, "sector": "Unique",
         "Perf.1M": 10.0, "Perf.YTD": 20.0},
    ]
    result = _compute_relative_strength(rows)
    assert len(result) == 1
    r = result[0]
    assert r["excess_1d"] == pytest.approx(0.0)
    assert r["excess_1m"] == pytest.approx(0.0)
    assert r["excess_ytd"] == pytest.approx(0.0)
    # Percentile rank of the single value against itself = 50.
    assert r["rs_score"] == pytest.approx(50.0)


def test_compute_relative_strength_missing_change_field():
    # Rows without change: excess_1d is None; the 1M and YTD timeframes still score.
    rows = [
        {"name": "NoChange",  "close": 10, "sector": "Tech",
         "Perf.1M": 5.0, "Perf.YTD": 15.0},
        {"name": "HasChange", "close": 20, "change": 2.0, "sector": "Tech",
         "Perf.1M": 3.0, "Perf.YTD": 10.0},
    ]
    result = _compute_relative_strength(rows)
    by_name = {r["name"]: r for r in result}

    # excess_1d should be None because change is missing.
    assert by_name["NoChange"]["excess_1d"] is None
    # rs_score should still be computed (from 1M and YTD excess).
    assert by_name["NoChange"]["rs_score"] is not None


def test_compute_relative_strength_no_data_at_all():
    # A row with no numeric fields at all gets rs_score None and sorts last.
    rows = [
        {"name": "Ghost", "close": None, "sector": "X"},
        {"name": "Normal", "close": 10, "change": 2.0, "sector": "X", "Perf.1M": 4.0},
    ]
    result = _compute_relative_strength(rows)
    by_name = {r["name"]: r for r in result}

    assert by_name["Ghost"]["rs_score"] is None
    # None-score row sorts to the end.
    assert result[-1]["name"] == "Ghost"


def test_compute_relative_strength_score_bounds():
    # rs_score must always be in [0, 100] regardless of the data shape.
    rows = [
        {"name": str(i), "close": 10, "change": float(v),
         "sector": "X", "Perf.1M": float(v) * 2, "Perf.YTD": float(v) * 3}
        for i, v in enumerate([-20, -5, 0, 5, 20])
    ]
    result = _compute_relative_strength(rows)
    for r in result:
        if r["rs_score"] is not None:
            assert 0.0 <= r["rs_score"] <= 100.0


def test_compute_relative_strength_sorted_desc():
    # Result must be sorted descending by rs_score (None last).
    rows = [
        {"name": "A", "close": 10, "change": 5.0, "sector": "S", "Perf.1M": 10.0, "Perf.YTD": 20.0},
        {"name": "B", "close": 10, "change": 2.0, "sector": "S", "Perf.1M": 4.0,  "Perf.YTD": 8.0},
        {"name": "C", "close": 10, "change": -3.0,"sector": "S", "Perf.1M": -5.0, "Perf.YTD": -10.0},
        {"name": "D", "close": None, "sector": "S"},  # no data, should sort last
    ]
    result = _compute_relative_strength(rows)
    scores = [r["rs_score"] for r in result]
    non_null = [s for s in scores if s is not None]
    assert non_null == sorted(non_null, reverse=True)
    assert result[-1]["name"] == "D"


def test_compute_relative_strength_unknown_sector():
    # Rows without a sector field land in "Unknown" and are treated as one group.
    rows = [
        {"name": "X", "close": 10, "change": 3.0, "Perf.1M": 6.0, "Perf.YTD": 12.0},
        {"name": "Y", "close": 20, "change": -1.0, "sector": None, "Perf.1M": -2.0},
    ]
    result = _compute_relative_strength(rows)
    sectors = {r["sector"] for r in result}
    assert sectors == {"Unknown"}
    # Both rows scored (same "Unknown" sector, one outperforms the other).
    assert all(r["rs_score"] is not None for r in result)


@pytest.mark.live
def test_relative_strength_leaders_live():
    out = _call("relative_strength_leaders", {"market": "america", "limit": 300, "top": 20})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 300
    top = out["top"]
    assert isinstance(top, list)
    assert len(top) <= 20
    # rs_score in [0, 100] and sorted descending.
    scores = [r["rs_score"] for r in top if r["rs_score"] is not None]
    for s in scores:
        assert 0.0 <= s <= 100.0
    assert scores == sorted(scores, reverse=True)
    # Each row has required keys.
    for r in top:
        assert "name" in r
        assert "rs_score" in r
        assert "excess_1d" in r
        assert "sector" in r
        assert "sector_avg_change" in r


@pytest.mark.live
def test_relative_strength_leaders_with_filter_live():
    out = _call("relative_strength_leaders", {
        "market": "america",
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 200,
        "top": 10,
    })
    assert "top" in out
    assert out["sample"] > 0
    assert len(out["top"]) <= 10


# --- ema_stack_scan (offline math + live data) ---------------------------

def test_ema_stack_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "ema_stack_scan" in tools


def test_compute_ema_stack_full_bull():
    # All four conditions True: price > EMA8 > EMA21 > SMA50 > SMA200.
    row = {
        "name": "A", "close": 200.0, "change": 2.0, "sector": "Tech",
        "EMA8": 190.0, "EMA21": 175.0, "SMA50": 150.0, "SMA200": 120.0,
        "RSI": 65.0, "market_cap_basic": 1e12,
    }
    result = _compute_ema_stack([row])
    r = result[0]
    assert r["stack_score"] == 4
    assert r["ma_alignment"] == "full_bull"
    assert r["conditions_available"] == 4
    assert r["price_vs_ema8"] is True
    assert r["ema8_vs_ema21"] is True
    assert r["ema21_vs_sma50"] is True
    assert r["sma50_vs_sma200"] is True
    assert r["rsi"] == pytest.approx(65.0)


def test_compute_ema_stack_full_bear():
    # All four conditions False: price < EMA8 < EMA21 < SMA50 < SMA200.
    row = {
        "name": "B", "close": 80.0, "change": -2.0,
        "EMA8": 90.0, "EMA21": 100.0, "SMA50": 115.0, "SMA200": 130.0,
    }
    result = _compute_ema_stack([row])
    r = result[0]
    assert r["stack_score"] == 0
    assert r["ma_alignment"] == "full_bear"
    assert r["conditions_available"] == 4
    assert r["price_vs_ema8"] is False
    assert r["ema8_vs_ema21"] is False
    assert r["ema21_vs_sma50"] is False
    assert r["sma50_vs_sma200"] is False


def test_compute_ema_stack_partial():
    # close(110) > EMA8(105): True
    # EMA8(105) > EMA21(100): True
    # EMA21(100) > SMA50(120): False   <- EMA21 below SMA50
    # SMA50(120) > SMA200(130): False  <- death-cross stack
    row = {
        "name": "C", "close": 110.0,
        "EMA8": 105.0, "EMA21": 100.0, "SMA50": 120.0, "SMA200": 130.0,
    }
    result = _compute_ema_stack([row])
    r = result[0]
    assert r["stack_score"] == 2
    assert r["ma_alignment"] == "partial_2"
    assert r["conditions_available"] == 4
    assert r["price_vs_ema8"] is True
    assert r["ema8_vs_ema21"] is True
    assert r["ema21_vs_sma50"] is False
    assert r["sma50_vs_sma200"] is False


def test_compute_ema_stack_empty():
    assert _compute_ema_stack([]) == []


def test_compute_ema_stack_no_ma_fields():
    # Row with no MA data at all: score None, alignment "unknown".
    row = {"name": "X", "close": 50.0, "change": 0.5}
    result = _compute_ema_stack([row])
    r = result[0]
    assert r["stack_score"] is None
    assert r["ma_alignment"] == "unknown"
    assert r["conditions_available"] == 0
    assert r["price_vs_ema8"] is None
    assert r["ema8_vs_ema21"] is None


def test_compute_ema_stack_partial_missing_fields():
    # Only EMA8 available: only the first condition can be evaluated.
    row = {"name": "Y", "close": 100.0, "EMA8": 90.0}
    # close(100) > EMA8(90): True -> score 1, partial_1
    result = _compute_ema_stack([row])
    r = result[0]
    assert r["stack_score"] == 1
    assert r["ma_alignment"] == "partial_1"
    assert r["conditions_available"] == 1
    assert r["price_vs_ema8"] is True
    assert r["ema8_vs_ema21"] is None
    assert r["ema21_vs_sma50"] is None
    assert r["sma50_vs_sma200"] is None


def test_compute_ema_stack_sorted_desc_none_last():
    rows = [
        # score None (no MA data)
        {"name": "None",  "close": 50.0},
        # score 4 (full bull)
        {"name": "Bull",  "close": 200.0, "EMA8": 190.0, "EMA21": 175.0, "SMA50": 150.0, "SMA200": 120.0},
        # score 2 (partial)
        {"name": "Mid",   "close": 110.0, "EMA8": 105.0, "EMA21": 100.0, "SMA50": 120.0, "SMA200": 130.0},
        # score 0 (full bear)
        {"name": "Bear",  "close": 80.0,  "EMA8": 90.0,  "EMA21": 100.0, "SMA50": 115.0, "SMA200": 130.0},
    ]
    result = _compute_ema_stack(rows)
    names = [r["name"] for r in result]
    # Full bull leads, then partial, then full bear, then None.
    assert names[0] == "Bull"
    assert names[-1] == "None"
    # Scores are non-increasing (ignoring None at end).
    scored = [r["stack_score"] for r in result if r["stack_score"] is not None]
    assert scored == sorted(scored, reverse=True)


def test_compute_ema_stack_distribution_counts():
    rows = [
        # score 4
        {"name": "A", "close": 200.0, "EMA8": 190.0, "EMA21": 175.0, "SMA50": 150.0, "SMA200": 120.0},
        {"name": "B", "close": 200.0, "EMA8": 190.0, "EMA21": 175.0, "SMA50": 150.0, "SMA200": 120.0},
        # score 2
        {"name": "C", "close": 110.0, "EMA8": 105.0, "EMA21": 100.0, "SMA50": 120.0, "SMA200": 130.0},
        # score 0
        {"name": "D", "close": 80.0,  "EMA8": 90.0,  "EMA21": 100.0, "SMA50": 115.0, "SMA200": 130.0},
        # score None
        {"name": "E", "close": 50.0},
    ]
    result = _compute_ema_stack(rows)
    scores = [r["stack_score"] for r in result]
    assert scores.count(4) == 2
    assert scores.count(2) == 1
    assert scores.count(0) == 1
    assert scores.count(None) == 1


@pytest.mark.live
def test_ema_stack_scan_live():
    out = _call("ema_stack_scan", {"market": "america", "limit": 300, "top": 20})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 300
    assert "distribution" in out
    dist = out["distribution"]
    assert set(dist.keys()) >= {"0", "1", "2", "3", "4", "none"}
    assert "pct_full_bull" in out
    assert "avg_stack_score" in out
    top = out["top"]
    assert isinstance(top, list)
    assert len(top) <= 20
    # All top entries must have required keys.
    for r in top:
        assert "stack_score" in r
        assert "ma_alignment" in r
        assert r["ma_alignment"] in {
            "full_bull", "full_bear", "partial_0", "partial_1",
            "partial_2", "partial_3", "unknown",
        }
    # Top list is sorted descending by stack_score.
    scores = [r["stack_score"] for r in top if r["stack_score"] is not None]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.live
def test_ema_stack_scan_min_stack_live():
    out = _call("ema_stack_scan", {
        "market": "america",
        "min_stack": 4,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 300,
        "top": 30,
    })
    assert "top" in out
    # Every returned stock should be at full bull stack score 4.
    for r in out["top"]:
        assert r["stack_score"] == 4
        assert r["ma_alignment"] == "full_bull"


# --- earnings_radar (offline math + live data) ---------------------------

def test_earnings_radar_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "earnings_radar" in tools


def test_compute_earnings_radar_basic():
    rows = [
        # Earnings today.
        {"name": "A", "close": 100.0, "change": 1.0, "sector": "Tech",
         "days_to_earnings": 0, "RSI": 60.0, "ATRP": 2.5, "Perf.1M": 5.0,
         "market_cap_basic": 1e11},
        # Earnings in 3 days (this_week).
        {"name": "B", "close": 50.0, "change": -0.5, "sector": "Energy",
         "days_to_earnings": 3, "RSI": 45.0, "ATRP": 3.0, "Perf.1M": -2.0,
         "market_cap_basic": 5e10},
        # Earnings in 7 days (later, right at max_days boundary).
        {"name": "C", "close": 30.0, "change": 0.2, "sector": "Tech",
         "days_to_earnings": 7, "RSI": 55.0, "ATRP": 1.8, "Perf.1M": 3.0,
         "market_cap_basic": 2e10},
        # Beyond max_days=7, should be excluded.
        {"name": "D", "close": 20.0, "change": 0.0, "sector": "Finance",
         "days_to_earnings": 10, "RSI": 50.0, "ATRP": 1.5, "Perf.1M": 0.0,
         "market_cap_basic": 1e10},
    ]
    result = _compute_earnings_radar(rows, max_days=7)
    assert result["count"] == 3
    names = [s["name"] for s in result["stocks"]]
    assert "A" in names and "B" in names and "C" in names
    assert "D" not in names
    assert result["by_bucket"]["today"] == 1
    assert result["by_bucket"]["this_week"] == 1
    assert result["by_bucket"]["later"] == 1


def test_compute_earnings_radar_empty():
    result = _compute_earnings_radar([])
    assert result["count"] == 0
    assert result["stocks"] == []
    assert result["by_sector"] == []
    assert result["by_bucket"] == {"today": 0, "this_week": 0, "later": 0}


def test_compute_earnings_radar_missing_days_field():
    # Rows without days_to_earnings or with None are silently skipped.
    rows = [
        {"name": "A", "close": 100.0, "sector": "Tech"},
        {"name": "B", "close": 50.0, "days_to_earnings": None, "sector": "Energy"},
        {"name": "C", "close": 30.0, "days_to_earnings": 2, "sector": "Finance"},
    ]
    result = _compute_earnings_radar(rows, max_days=7)
    assert result["count"] == 1
    assert result["stocks"][0]["name"] == "C"


def test_compute_earnings_radar_sorted_asc():
    rows = [
        {"name": "Late",  "days_to_earnings": 5, "sector": "A"},
        {"name": "Early", "days_to_earnings": 1, "sector": "B"},
        {"name": "Today", "days_to_earnings": 0, "sector": "C"},
    ]
    result = _compute_earnings_radar(rows, max_days=7)
    names = [s["name"] for s in result["stocks"]]
    assert names[0] == "Today"
    assert names[-1] == "Late"
    dtes = [s["days_to_earnings"] for s in result["stocks"]]
    assert dtes == sorted(dtes)


def test_compute_earnings_radar_max_days_filter():
    # max_days=3 excludes rows with days_to_earnings > 3.
    rows = [
        {"name": "A", "days_to_earnings": 2, "sector": "Tech"},
        {"name": "B", "days_to_earnings": 4, "sector": "Energy"},
        {"name": "C", "days_to_earnings": 0, "sector": "Finance"},
    ]
    result = _compute_earnings_radar(rows, max_days=3)
    assert result["count"] == 2
    names = [s["name"] for s in result["stocks"]]
    assert "B" not in names
    assert "A" in names and "C" in names


def test_compute_earnings_radar_bucket_classification():
    rows = [
        {"name": "Today",    "days_to_earnings": 0,  "sector": "S"},
        {"name": "Tomorrow", "days_to_earnings": 1,  "sector": "S"},
        {"name": "FriDay",   "days_to_earnings": 5,  "sector": "S"},
        {"name": "Next",     "days_to_earnings": 6,  "sector": "S"},
        {"name": "Beyond",   "days_to_earnings": 14, "sector": "S"},
    ]
    result = _compute_earnings_radar(rows, max_days=14)
    by_name = {s["name"]: s for s in result["stocks"]}
    assert by_name["Today"]["bucket"] == "today"
    assert by_name["Tomorrow"]["bucket"] == "this_week"
    assert by_name["FriDay"]["bucket"] == "this_week"
    assert by_name["Next"]["bucket"] == "later"
    assert by_name["Beyond"]["bucket"] == "later"
    assert result["by_bucket"]["today"] == 1
    assert result["by_bucket"]["this_week"] == 2
    assert result["by_bucket"]["later"] == 2


def test_days_to_earnings_does_not_depend_on_the_host_timezone():
    """The day count must come from the market's clock, not the server's.

    earnings_release_next_date is a real UTC instant (the 1787774400 below is
    NVDA's, 20:00 UTC, the 4pm ET after-close slot). Converting it with the
    host's local timezone made the answer depend on where the process ran: the
    same row read 24 days out from New York and 25 from Tokyo, so "reports
    today" silently became "reports tomorrow" past a certain longitude.
    """
    import os
    import time as _time

    ts = 1787774400  # 2026-08-26 20:00 UTC
    today = dt.date(2026, 8, 2)
    seen = set()
    original = os.environ.get("TZ")
    try:
        for tz in ("UTC", "America/New_York", "Asia/Tokyo", "Australia/Sydney"):
            os.environ["TZ"] = tz
            if hasattr(_time, "tzset"):
                _time.tzset()
            seen.add(_days_to_earnings(ts, today))
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        if hasattr(_time, "tzset"):
            _time.tzset()

    assert len(seen) == 1, f"day count varies with the host timezone: {seen}"
    assert seen == {24}


def test_days_to_earnings_rejects_non_numeric():
    assert _days_to_earnings(None) is None
    assert _days_to_earnings("2026-08-26") is None
    assert _days_to_earnings(True) is None  # bool is an int subclass


def test_compute_earnings_radar_sector_breakdown():
    rows = [
        {"name": "T1", "days_to_earnings": 0, "sector": "Tech"},
        {"name": "T2", "days_to_earnings": 1, "sector": "Tech"},
        {"name": "E1", "days_to_earnings": 3, "sector": "Energy"},
    ]
    result = _compute_earnings_radar(rows, max_days=7)
    by_sec = {s["sector"]: s for s in result["by_sector"]}
    assert "Tech" in by_sec and "Energy" in by_sec
    assert by_sec["Tech"]["count"] == 2
    assert by_sec["Energy"]["count"] == 1
    assert by_sec["Tech"]["today"] == 1
    assert by_sec["Tech"]["this_week"] == 1
    # Sorted by count desc: Tech (2) before Energy (1).
    assert result["by_sector"][0]["sector"] == "Tech"


def test_compute_earnings_radar_missing_atrp_field():
    # Rows without ATRP, RSI, or perf_1m are still included; those keys are None.
    rows = [{"name": "A", "close": 100.0, "days_to_earnings": 1, "sector": "Tech"}]
    result = _compute_earnings_radar(rows, max_days=7)
    assert result["count"] == 1
    r = result["stocks"][0]
    assert r["atrp"] is None
    assert r["rsi"] is None
    assert r["perf_1m"] is None
    assert r["name"] == "A"


@pytest.mark.live
def test_earnings_radar_live():
    out = _call("earnings_radar", {"market": "america", "horizon": 7, "limit": 500, "top": 30})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert "count" in out
    assert "by_bucket" in out
    assert set(out["by_bucket"].keys()) == {"today", "this_week", "later"}
    assert "by_sector" in out
    assert isinstance(out["stocks"], list)
    assert len(out["stocks"]) <= 30
    # Stocks sorted by days_to_earnings ascending.
    dtes = [s["days_to_earnings"] for s in out["stocks"]]
    assert dtes == sorted(dtes)
    # All stocks have the required fields.
    for s in out["stocks"]:
        assert "name" in s
        assert "days_to_earnings" in s
        assert s["bucket"] in {"today", "this_week", "later"}
        assert 0 <= s["days_to_earnings"] <= 7


@pytest.mark.live
def test_earnings_radar_with_filter_live():
    out = _call("earnings_radar", {
        "market": "america",
        "horizon": 14,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 300,
        "top": 20,
    })
    assert "count" in out
    assert out["horizon_days"] == 14
    assert len(out["stocks"]) <= 20


# --- gap_scanner (offline math + live data) ------------------------------

def test_gap_scanner_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "gap_scanner" in tools


def test_compute_gap_scanner_basic():
    # gap=10%: open=110, prev_close=100. gap=−5%: open=95, prev_close=100.
    rows = [
        {"name": "Up",   "open": 110.0, "close": 107.0, "gap": 10.0,  "change": 5.0,  "sector": "Tech"},
        {"name": "Down", "open":  95.0, "close":  96.0, "gap": -5.0,  "change": -2.0, "sector": "Energy"},
        {"name": "Flat", "open": 100.0, "close": 100.5, "gap":  0.3,  "change": 0.5,  "sector": "Finance"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    assert result["count"] == 2             # Flat excluded (0.3% < 1.0%)
    assert result["gap_up_count"] == 1
    assert result["gap_down_count"] == 1
    names = {r["name"] for r in result["gap_up"] + result["gap_down"]}
    assert "Up" in names
    assert "Down" in names
    assert "Flat" not in names


def test_compute_gap_scanner_empty():
    result = _compute_gap_scanner([])
    assert result["count"] == 0
    assert result["gap_up"] == []
    assert result["gap_down"] == []
    assert result["by_sector"] == []


def test_compute_gap_scanner_below_threshold():
    # All gaps are below the 2% threshold; none should qualify.
    rows = [
        {"name": "A", "open": 101.0, "close": 100.5, "gap": 0.8, "sector": "Tech"},
        {"name": "B", "open":  99.5, "close": 100.0, "gap": -0.5, "sector": "Energy"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=2.0)
    assert result["count"] == 0
    assert result["gap_up"] == []
    assert result["gap_down"] == []


def test_compute_gap_scanner_fill_pct_gap_up():
    # open=110, gap=10% => prev_close = 110/1.1 = 100.0
    # Case A: close=110 (no fill)  => (110-110)/(110-100)*100 = 0%
    # Case B: close=105 (50% fill) => (110-105)/(110-100)*100 = 50%
    # Case C: close=100 (full fill)=> (110-100)/(110-100)*100 = 100%
    # Case D: close=95 (overshoot) => (110-95)/(110-100)*100 = 150%
    rows = [
        {"name": "NoFill",  "open": 110.0, "close": 110.0, "gap": 10.0, "sector": "S"},
        {"name": "HalfFill","open": 110.0, "close": 105.0, "gap": 10.0, "sector": "S"},
        {"name": "FullFill","open": 110.0, "close": 100.0, "gap": 10.0, "sector": "S"},
        {"name": "Overshoot","open": 110.0, "close":  95.0, "gap": 10.0, "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    by_name = {r["name"]: r for r in result["gap_up"]}
    assert by_name["NoFill"]["gap_fill_pct"] == pytest.approx(0.0)
    assert by_name["HalfFill"]["gap_fill_pct"] == pytest.approx(50.0)
    assert by_name["FullFill"]["gap_fill_pct"] == pytest.approx(100.0)
    assert by_name["Overshoot"]["gap_fill_pct"] == pytest.approx(150.0)


def test_compute_gap_scanner_fill_pct_gap_down():
    # open=90, gap=-10% => prev_close = 90/0.9 = 100.0
    # Case A: close=90 (no fill)   => (90-90)/(90-100)*100 = 0%
    # Case B: close=95 (50% fill)  => (90-95)/(90-100)*100 = 50%
    # Case C: close=100 (full fill)=> (90-100)/(90-100)*100 = 100%
    # Case D: close=105 (overshoot)=> (90-105)/(90-100)*100 = 150%
    rows = [
        {"name": "NoFill",   "open": 90.0, "close":  90.0, "gap": -10.0, "sector": "S"},
        {"name": "HalfFill", "open": 90.0, "close":  95.0, "gap": -10.0, "sector": "S"},
        {"name": "FullFill", "open": 90.0, "close": 100.0, "gap": -10.0, "sector": "S"},
        {"name": "Overshoot","open": 90.0, "close": 105.0, "gap": -10.0, "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    by_name = {r["name"]: r for r in result["gap_down"]}
    assert by_name["NoFill"]["gap_fill_pct"] == pytest.approx(0.0)
    assert by_name["HalfFill"]["gap_fill_pct"] == pytest.approx(50.0)
    assert by_name["FullFill"]["gap_fill_pct"] == pytest.approx(100.0)
    assert by_name["Overshoot"]["gap_fill_pct"] == pytest.approx(150.0)


def test_compute_gap_scanner_is_holding_and_filled_flags():
    # open=110, gap=10% => prev_close=100
    rows = [
        # fill=0%: is_holding=True, is_filled=False
        {"name": "Hold",   "open": 110.0, "close": 110.0, "gap": 10.0, "sector": "S"},
        # fill=24%: is_holding=True (< 25), is_filled=False
        {"name": "Almost", "open": 110.0, "close": 107.6, "gap": 10.0, "sector": "S"},
        # fill=26%: is_holding=False (>= 25), is_filled=False
        {"name": "Moving", "open": 110.0, "close": 107.4, "gap": 10.0, "sector": "S"},
        # fill=100%: is_holding=False, is_filled=True
        {"name": "Filled", "open": 110.0, "close": 100.0, "gap": 10.0, "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    by_name = {r["name"]: r for r in result["gap_up"]}
    assert by_name["Hold"]["is_holding"] is True
    assert by_name["Hold"]["is_filled"] is False
    assert by_name["Almost"]["is_holding"] is True
    assert by_name["Moving"]["is_holding"] is False
    assert by_name["Moving"]["is_filled"] is False
    assert by_name["Filled"]["is_filled"] is True
    assert by_name["Filled"]["is_holding"] is False


def test_compute_gap_scanner_sorted_by_abs_gap():
    # Results should be ordered by |gap| descending, mixing gap_up and gap_down.
    rows = [
        {"name": "Small",    "open": 102.0, "close": 102.0, "gap":  2.0, "sector": "S"},
        {"name": "Biggest",  "open": 115.0, "close": 115.0, "gap": 15.0, "sector": "S"},
        {"name": "NegMid",   "open":  93.0, "close":  93.0, "gap": -7.0, "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    # Combined qualified list is sorted by |gap| desc.
    all_rows = result["gap_up"] + result["gap_down"]
    abs_gaps = [abs(r["gap"]) for r in all_rows]
    # Biggest (15%) should appear first in gap_up.
    assert result["gap_up"][0]["name"] == "Biggest"
    # The gap_down list has only NegMid (7%), gap_up has Biggest (15%) and Small (2%).
    assert abs(result["gap_up"][0]["gap"]) >= abs(result["gap_up"][-1]["gap"])


def test_compute_gap_scanner_missing_gap_field():
    # Rows without a numeric `gap` field are silently skipped.
    rows = [
        {"name": "NoGap",   "open": 100.0, "close": 100.0, "sector": "S"},
        {"name": "NullGap", "open": 100.0, "close": 100.0, "gap": None, "sector": "S"},
        {"name": "HasGap",  "open": 110.0, "close": 110.0, "gap": 5.0,  "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    assert result["count"] == 1
    assert result["gap_up"][0]["name"] == "HasGap"


@pytest.mark.live
def test_broad_america_tools_return_no_otc_by_default():
    """The guard has to actually reach the rows, not just the query.

    Measured unguarded: top_movers 10/10 OTC on both lists, gap_scanner 50/50
    on both. Those two rank on change and gap, which a sub-penny shell wins by
    construction, so they are the sharpest test that the guard is live.
    """
    from mcp_server.scanline.models import ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    resp = run_screen(ScreenRequest(
        market="america", columns=["name", "exchange"],
        filters=[{"field": "exchange", "op": "==", "value": "OTC"}], limit=1000,
    ))
    assert not resp["meta"].get("error"), resp["meta"]["error"]
    otc = {r["name"] for r in resp["rows"]}
    assert otc, "could not build an OTC ticker set, the check would be vacuous"

    lists = {
        "top_movers.gainers": _call("top_movers")["gainers"],
        "top_movers.losers": _call("top_movers")["losers"],
        "gap_scanner.gap_up": _call("gap_scanner")["gap_up"],
        "gap_scanner.gap_down": _call("gap_scanner")["gap_down"],
    }
    for label, rows in lists.items():
        assert rows, f"{label} came back empty"
        hits = [r["name"] for r in rows if r["name"] in otc]
        assert not hits, f"{label} returned OTC rows despite the guard: {hits[:5]}"


@pytest.mark.live
def test_include_otc_actually_lifts_the_guard():
    # The escape hatch has to work, otherwise the guard is a silent cap rather
    # than a default. OTC dominates the change ranking, so lifting it shows up
    # immediately.
    guarded = _call("top_movers")["gainers"]
    lifted = _call("top_movers", {"include_otc": True})["gainers"]
    assert guarded and lifted
    assert [r["name"] for r in guarded] != [r["name"] for r in lifted]
    assert guarded[0]["change"] < lifted[0]["change"]


@pytest.mark.live
def test_a_trivially_true_filter_does_not_change_the_universe():
    """Query.where() ASSIGNS query['filter'], it does not append.

    stocks('america') ships is_primary == true in that slot, so adding any
    condition of our own used to wipe it out and readmit every non-primary
    listing: close > -1e12 took america from 7,648 rows to 13,374. Every preset
    and every filtered tool was therefore scanning a different and much dirtier
    universe than the unfiltered default view, which is also where a good share
    of the OTC junk came from.
    """
    from mcp_server.scanline.models import ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    def count(filters):
        r = run_screen(ScreenRequest(market="america", columns=["name"],
                                     filters=filters, limit=1))
        assert not r["meta"].get("error"), r["meta"]["error"]
        return r["count"]

    bare = count([])
    trivial = count([{"field": "close", "op": ">", "value": -1e12}])
    assert trivial == bare, (
        f"a trivially true filter changed the universe from {bare} to {trivial}, "
        f"so the market's own constraints are being overwritten again"
    )


@pytest.mark.live
def test_match_any_keeps_the_market_constraints_too():
    # where2() assigns query['filter2'], which carries the instrument type
    # constraints, so the match="any" path needs the same composition.
    from mcp_server.scanline.models import ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    def count(filters, match="all"):
        r = run_screen(ScreenRequest(market="america", columns=["name"],
                                     filters=filters, match=match, limit=1))
        assert not r["meta"].get("error"), r["meta"]["error"]
        return r["count"]

    lo = [{"field": "RSI", "op": "<", "value": 30}]
    hi = [{"field": "RSI", "op": ">", "value": 70}]
    both = count(lo + hi, match="any")
    # Disjoint conditions, so the OR is exactly the sum, and all three are
    # bounded by the same primary-listing universe.
    assert both == count(lo) + count(hi)
    assert both <= count([])


@pytest.mark.live
def test_gap_scanner_samples_both_tails():
    """gap_scanner must sample both ends of the gap distribution.

    It used to run one query sorted by gap descending and keep the top `limit`
    rows, which is the largest POSITIVE gaps and nothing else. On america that
    window ran from +334900% down to +5.9% with no negative in it, so gap_down
    came back empty and gap_down_count was 0 on a session where 210 of 1000
    sampled rows had gapped down 1% or more. The tool reported success, and
    "nothing gapped down today" was a confident false statement.

    The US market always has gaps on both sides once the session has data, so
    an empty side here means the sampling is one-eyed again.
    """
    out = _call("gap_scanner", {"market": "america", "limit": 400})
    assert "error" not in out, out
    assert out["gap_up_count"] > 0, "no gap ups sampled at all"
    assert out["gap_down_count"] > 0, (
        "gap_down is empty: the sample is one-sided again, so the tool is "
        "reporting that nothing gapped down"
    )
    assert all(r["gap"] > 0 for r in out["gap_up"])
    assert all(r["gap"] < 0 for r in out["gap_down"])

    names = [r["name"] for r in out["gap_up"]] + [r["name"] for r in out["gap_down"]]
    assert len(names) == len(set(names)), "a row was counted on both sides"


def test_compute_gap_scanner_missing_open_or_close():
    # When open or close is missing, gap_fill_pct and flags are None/False.
    rows = [
        {"name": "NoOpen",  "close": 105.0, "gap": 5.0,  "sector": "S"},
        {"name": "NoClose", "open": 110.0,  "gap": 10.0, "sector": "S"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    assert result["count"] == 2
    by_name = {r["name"]: r for r in result["gap_up"]}
    assert by_name["NoOpen"]["gap_fill_pct"] is None
    assert by_name["NoOpen"]["is_holding"] is False
    assert by_name["NoClose"]["gap_fill_pct"] is None


def test_compute_gap_scanner_sector_breakdown():
    rows = [
        {"name": "T1", "open": 110.0, "close": 109.0, "gap": 5.0,  "sector": "Tech"},
        {"name": "T2", "open": 112.0, "close": 111.0, "gap": 3.0,  "sector": "Tech"},
        {"name": "E1", "open":  94.0, "close":  95.0, "gap": -4.0, "sector": "Energy"},
    ]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    by_sec = {s["sector"]: s for s in result["by_sector"]}
    assert "Tech" in by_sec and "Energy" in by_sec
    assert by_sec["Tech"]["count"] == 2
    assert by_sec["Tech"]["gap_up"] == 2
    assert by_sec["Tech"]["gap_down"] == 0
    assert by_sec["Energy"]["count"] == 1
    assert by_sec["Energy"]["gap_down"] == 1
    # Sorted by count desc: Tech before Energy.
    assert result["by_sector"][0]["sector"] == "Tech"


def test_compute_gap_scanner_prev_close_accuracy():
    # Verify back-calculation: gap=10%, open=110 => prev_close=100 exactly.
    rows = [{"name": "A", "open": 110.0, "close": 110.0, "gap": 10.0, "sector": "S"}]
    result = _compute_gap_scanner(rows, min_gap_pct=1.0)
    r = result["gap_up"][0]
    assert r["prev_close"] == pytest.approx(100.0, abs=1e-4)
    assert r["gap_fill_pct"] == pytest.approx(0.0)


@pytest.mark.live
def test_gap_scanner_live():
    out = _call("gap_scanner", {"market": "america", "limit": 300, "min_gap_pct": 0.5})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert out["sample"] <= 300
    assert "count" in out
    assert "gap_up_count" in out
    assert "gap_down_count" in out
    assert out["gap_up_count"] + out["gap_down_count"] == out["count"]
    assert isinstance(out["gap_up"], list)
    assert isinstance(out["gap_down"], list)
    # Every returned gap must be at or above the threshold.
    for r in out["gap_up"] + out["gap_down"]:
        assert abs(r["gap"]) >= 0.5
    # gap_up entries have positive gap.
    for r in out["gap_up"]:
        assert r["gap"] > 0
    # gap_down entries have negative gap.
    for r in out["gap_down"]:
        assert r["gap"] < 0
    # gap_fill_pct is a float when open and close are available.
    filled = [r for r in out["gap_up"] + out["gap_down"] if r["gap_fill_pct"] is not None]
    for r in filled:
        assert isinstance(r["gap_fill_pct"], float)


@pytest.mark.live
def test_gap_scanner_with_filter_live():
    out = _call("gap_scanner", {
        "market": "america",
        "min_gap_pct": 1.0,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 200,
        "top": 20,
    })
    assert "gap_up_count" in out
    assert len(out["gap_up"]) <= 20
    assert len(out["gap_down"]) <= 20


# --- dividend_screen (offline math + payout helper + live data) ----------

def test_dividend_screen_is_registered():
    tools, _, _ = _list()
    assert PREFIX + "dividend_screen" in tools


def test_payout_health_bands():
    # Ideal range: exact boundaries.
    assert _payout_health(20.0) == pytest.approx(1.0)
    assert _payout_health(50.0) == pytest.approx(1.0)
    assert _payout_health(75.0) == pytest.approx(1.0)
    # Low payout ramp: 0% -> 0.4, 20% -> 1.0.
    assert _payout_health(0.0) == pytest.approx(0.4)
    assert _payout_health(10.0) == pytest.approx(0.7, abs=1e-6)
    # High payout ramp: 75% -> 1.0, 100% -> 0.0.
    assert _payout_health(100.0) == pytest.approx(0.0)
    # Beyond bounds.
    assert _payout_health(-1.0) == pytest.approx(0.0)
    assert _payout_health(101.0) == pytest.approx(0.0)
    # Missing data.
    assert _payout_health(None) == pytest.approx(0.5)


def test_compute_dividend_quality_basic():
    # A: high yield, Aristocrat, healthy payout -> should lead.
    # B: medium yield, Achiever, ok payout -> middle.
    # C: low yield, Payer, high payout -> trails.
    rows = [
        {
            "name": "A", "close": 100.0, "change": 1.0, "sector": "Consumer",
            "market_cap_basic": 5e10, "dividends_yield_current": 4.0,
            "continuous_dividend_payout": 30, "continuous_dividend_growth": 28,
            "payout_ratio": 50.0, "return_on_equity": 20.0, "Perf.1M": 3.0,
        },
        {
            "name": "B", "close": 50.0, "change": -0.5, "sector": "Utilities",
            "market_cap_basic": 2e10, "dividends_yield_current": 3.0,
            "continuous_dividend_payout": 15, "continuous_dividend_growth": 12,
            "payout_ratio": 60.0, "return_on_equity": 12.0, "Perf.1M": 1.0,
        },
        {
            "name": "C", "close": 20.0, "change": -1.0, "sector": "Energy",
            "market_cap_basic": 5e9, "dividends_yield_current": 1.5,
            "continuous_dividend_payout": 5, "continuous_dividend_growth": 0,
            "payout_ratio": 90.0, "return_on_equity": 5.0, "Perf.1M": -2.0,
        },
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)

    assert result["count"] == 3
    names = [s["name"] for s in result["stocks"]]
    # A (highest quality: yield=4%, 28yr growth, 50% payout) leads.
    assert names[0] == "A"
    # C (lowest: 1.5% yield, no growth, 90% payout) trails.
    assert names[-1] == "C"
    # Categories.
    by_name = {s["name"]: s for s in result["stocks"]}
    assert by_name["A"]["category"] == "Aristocrat"
    assert by_name["B"]["category"] == "Achiever"
    assert by_name["C"]["category"] == "Payer"
    # avg_yield is the mean of all three yields.
    assert result["avg_yield"] == pytest.approx((4.0 + 3.0 + 1.5) / 3.0, abs=0.01)


def test_compute_dividend_quality_empty():
    result = _compute_dividend_quality([])
    assert result["count"] == 0
    assert result["stocks"] == []
    assert result["by_sector"] == []
    assert result["avg_yield"] is None
    assert result["by_category"] == {"Aristocrat": 0, "Achiever": 0, "Grower": 0, "Payer": 0}


def test_compute_dividend_quality_min_yield_filter():
    # Only rows with div_yield >= min_yield qualify.
    rows = [
        {"name": "Hi", "dividends_yield_current": 3.0, "continuous_dividend_growth": 5,
         "payout_ratio": 40.0, "sector": "Tech"},
        {"name": "Lo", "dividends_yield_current": 0.5, "continuous_dividend_growth": 2,
         "payout_ratio": 30.0, "sector": "Tech"},
    ]
    result = _compute_dividend_quality(rows, min_yield=2.0)
    assert result["count"] == 1
    assert result["stocks"][0]["name"] == "Hi"


def test_compute_dividend_quality_categories():
    rows = [
        {"name": "Arist",  "dividends_yield_current": 2.0, "continuous_dividend_growth": 25, "sector": "S"},
        {"name": "Achiev", "dividends_yield_current": 2.0, "continuous_dividend_growth": 10, "sector": "S"},
        {"name": "Grower", "dividends_yield_current": 2.0, "continuous_dividend_growth": 5,  "sector": "S"},
        {"name": "Payer",  "dividends_yield_current": 2.0, "continuous_dividend_growth": 0,  "sector": "S"},
        {"name": "NoDat",  "dividends_yield_current": 2.0,                                   "sector": "S"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    by_name = {s["name"]: s for s in result["stocks"]}
    assert by_name["Arist"]["category"] == "Aristocrat"
    assert by_name["Achiev"]["category"] == "Achiever"
    assert by_name["Grower"]["category"] == "Grower"
    assert by_name["Payer"]["category"] == "Payer"
    assert by_name["NoDat"]["category"] == "Payer"
    cats = result["by_category"]
    assert cats["Aristocrat"] == 1
    assert cats["Achiever"] == 1
    assert cats["Grower"] == 1
    assert cats["Payer"] == 2


def test_compute_dividend_quality_missing_yield_skipped():
    rows = [
        {"name": "NoYield", "sector": "S"},
        {"name": "NullYield", "dividends_yield_current": None, "sector": "S"},
        {"name": "HasYield", "dividends_yield_current": 2.5, "continuous_dividend_growth": 3,
         "payout_ratio": 45.0, "sector": "S"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    assert result["count"] == 1
    assert result["stocks"][0]["name"] == "HasYield"


def test_compute_dividend_quality_score_bounds():
    rows = [
        {"name": str(i), "dividends_yield_current": float(y),
         "continuous_dividend_growth": i * 5, "payout_ratio": 30.0 + i * 10, "sector": "S"}
        for i, y in enumerate([1.5, 2.5, 4.0, 6.0, 8.0])
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    for s in result["stocks"]:
        assert s["dq_score"] is not None
        assert 0.0 <= s["dq_score"] <= 100.0


def test_compute_dividend_quality_sorted_desc():
    rows = [
        {"name": "Low",  "dividends_yield_current": 1.5, "continuous_dividend_growth": 0,
         "payout_ratio": 90.0, "sector": "S"},
        {"name": "High", "dividends_yield_current": 5.0, "continuous_dividend_growth": 30,
         "payout_ratio": 45.0, "sector": "S"},
        {"name": "Mid",  "dividends_yield_current": 3.0, "continuous_dividend_growth": 10,
         "payout_ratio": 55.0, "sector": "S"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    scores = [s["dq_score"] for s in result["stocks"]]
    assert scores == sorted(scores, reverse=True)
    assert result["stocks"][0]["name"] == "High"


def test_compute_dividend_quality_sector_breakdown():
    rows = [
        {"name": "U1", "dividends_yield_current": 4.0, "continuous_dividend_growth": 20,
         "payout_ratio": 65.0, "sector": "Utilities"},
        {"name": "U2", "dividends_yield_current": 3.5, "continuous_dividend_growth": 8,
         "payout_ratio": 60.0, "sector": "Utilities"},
        {"name": "C1", "dividends_yield_current": 2.0, "continuous_dividend_growth": 5,
         "payout_ratio": 40.0, "sector": "Consumer"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    by_sec = {s["sector"]: s for s in result["by_sector"]}

    assert "Utilities" in by_sec and "Consumer" in by_sec
    assert by_sec["Utilities"]["count"] == 2
    assert by_sec["Consumer"]["count"] == 1
    # avg_yield for Utilities = (4.0 + 3.5) / 2 = 3.75.
    assert by_sec["Utilities"]["avg_yield"] == pytest.approx(3.75, abs=0.01)
    # Sorted by count desc: Utilities (2) before Consumer (1).
    assert result["by_sector"][0]["sector"] == "Utilities"
    # U1 has 20 years growing -> Achiever (not Aristocrat since 20 < 25).
    assert by_sec["Utilities"]["achievers"] >= 1


def test_compute_dividend_quality_by_category_counts():
    rows = [
        {"name": "A1", "dividends_yield_current": 3.0, "continuous_dividend_growth": 26, "sector": "S"},
        {"name": "A2", "dividends_yield_current": 2.5, "continuous_dividend_growth": 30, "sector": "S"},
        {"name": "Ac", "dividends_yield_current": 2.0, "continuous_dividend_growth": 15, "sector": "S"},
        {"name": "Gr", "dividends_yield_current": 1.5, "continuous_dividend_growth": 3,  "sector": "S"},
        {"name": "Pa", "dividends_yield_current": 4.0, "continuous_dividend_growth": 0,  "sector": "S"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    cats = result["by_category"]
    assert cats["Aristocrat"] == 2
    assert cats["Achiever"] == 1
    assert cats["Grower"] == 1
    assert cats["Payer"] == 1
    assert sum(cats.values()) == 5


def test_compute_dividend_quality_payout_extremes():
    # Extreme payout (>100%) gets 0.0, which reduces dq_score.
    # Healthy payout (50%) gets 1.0, which raises dq_score.
    rows = [
        {"name": "Healthy",  "dividends_yield_current": 3.0, "continuous_dividend_growth": 5,
         "payout_ratio": 50.0, "sector": "S"},
        {"name": "Extreme",  "dividends_yield_current": 3.0, "continuous_dividend_growth": 5,
         "payout_ratio": 200.0, "sector": "S"},
    ]
    result = _compute_dividend_quality(rows, min_yield=1.0)
    by_name = {s["name"]: s for s in result["stocks"]}
    # Healthy payout should score higher.
    assert by_name["Healthy"]["dq_score"] > by_name["Extreme"]["dq_score"]


@pytest.mark.live
def test_dividend_screen_live():
    out = _call("dividend_screen", {"market": "america", "min_yield": 1.5, "limit": 300, "top": 30})
    assert out.get("market") == "america"
    assert out.get("universe", 0) > 0
    assert "count" in out
    assert "avg_yield" in out
    assert "by_category" in out
    assert set(out["by_category"].keys()) == {"Aristocrat", "Achiever", "Grower", "Payer"}
    assert "by_sector" in out
    stocks = out["stocks"]
    assert isinstance(stocks, list)
    assert len(stocks) <= 30
    # All returned stocks must have div_yield >= min_yield.
    for s in stocks:
        assert s["div_yield"] >= 1.5
    # dq_score in [0, 100] for all rows.
    scores = [s["dq_score"] for s in stocks if s["dq_score"] is not None]
    for sc in scores:
        assert 0.0 <= sc <= 100.0
    # Sorted descending by dq_score.
    assert scores == sorted(scores, reverse=True)
    # Required keys present.
    for s in stocks:
        assert "name" in s
        assert "div_yield" in s
        assert "years_growing" in s
        assert "category" in s
        assert s["category"] in {"Aristocrat", "Achiever", "Grower", "Payer"}


@pytest.mark.live
def test_dividend_screen_aristocrats_live():
    out = _call("dividend_screen", {
        "market": "america",
        "min_yield": 2.0,
        "min_years_growing": 25,
        "filters": [{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        "limit": 500,
        "top": 30,
    })
    assert "count" in out
    # Every returned stock must have at least 25 years of growth.
    for s in out["stocks"]:
        assert s["years_growing"] >= 25
        assert s["category"] == "Aristocrat"
