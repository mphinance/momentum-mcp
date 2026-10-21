"""SCANLINE screening tools, ported into momentum-mcp.

This is the MCP tool layer of https://github.com/mphinance/scanline, carried
over with its screen engine (pipeline, analytics, fields, presets) as the
`mcp_server.scanline` subpackage. It gives the momentum server the full
TradingView screener: 6 markets, 1000+ fields, cross-field filters, sandboxed
computed columns, factor scoring, 47 preset scans and a dozen market studies.

Every tool, prompt and resource here is registered through
`register_scanline_tools(mcp)` with a `scan_` prefix, the same way the
TickerTrace tools carry `etf_`. The prefix is not cosmetic. The server already
has `get_fundamentals`, `analyze_technicals`, `analyze_breadth` and friends
over yfinance, and several of these tools answer the same question from
TradingView data instead. A model picking between `fundamentals` and
`get_fundamentals` by name would have no way to tell which source it was
getting; `scan_fundamentals` says so.

Design notes carried over from scanline:
  - One unified `scan_screen` tool (the backend spans 6 markets) plus preset
    and factor-preset runners, rather than a tool per asset class.
  - `scan_search_fields` makes the 1000+ field universe discoverable to the
    model.
  - Cross-field filters work out of the box: a filter value may be another
    field id, e.g. {"field":"SMA50","op":"crosses_above","value":"SMA200"}.
  - A short TTL cache in front of the engine softens TradingView's rate-limit
    cliff under bursty agent use, and `scan_server_stats` reports on it.

No TradingView account needed. Data is live and delayed. Real-time mode is
opt-in through SCANLINE_TV_SESSIONID, see realtime.py.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Annotated, Any, Callable

from pydantic import Field

from .cache import TTLCache
from .fields import CURATED_IDS, FIELDS, field_index, validate_field
from .models import (
    MAX_LIMIT,
    Computed,
    Factor,
    FactorWeight,
    Filter,
    ScreenRequest,
    SortKey,
    Stat,
)
from .pipeline import request_key, run_screen
from .presets import FACTOR_PRESETS, NO_OTC, PRESETS
from .screener import MARKETS

# Registration is deferred: tools, prompts and resources collect here at import
# time and `register_scanline_tools` mounts them on a server with the prefix.
# Keeping the functions at module level (rather than closures inside the
# register function) keeps them importable for tests, exactly as upstream.
_TOOLS: list[Callable[..., Any]] = []
_PROMPTS: list[Callable[..., Any]] = []
_RESOURCES: list[tuple[str, Callable[..., Any]]] = []


def _tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    _TOOLS.append(fn)
    return fn


def _prompt(fn: Callable[..., Any]) -> Callable[..., Any]:
    _PROMPTS.append(fn)
    return fn


def _resource(uri: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _RESOURCES.append((uri, fn))
        return fn
    return deco


# Row-count parameters are bounded identically on every tool and the HTTP API.
Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Field(ge=0)]

# Resilience + a tiny bit of self-awareness for the stats tool.
_cache = TTLCache(ttl_seconds=20)
_STATS = {"calls": 0, "cache_hits": 0, "errors": 0, "started": time.time()}

# Operator reference, kept in lockstep with scanline/screener._apply_op. The
# `value` column notes when a value may be another field id (cross-field).
OPERATORS: list[dict] = [
    {"op": ">", "value": "number or field id", "desc": "greater than"},
    {"op": ">=", "value": "number or field id", "desc": "greater than or equal"},
    {"op": "<", "value": "number or field id", "desc": "less than"},
    {"op": "<=", "value": "number or field id", "desc": "less than or equal"},
    {"op": "==", "value": "number, string, or field id", "desc": "equal"},
    {"op": "!=", "value": "number, string, or field id", "desc": "not equal"},
    {"op": "between", "value": "[low, high]", "desc": "inclusive range"},
    {"op": "not_between", "value": "[low, high]", "desc": "outside a range"},
    {"op": "above_pct", "value": "[field id, ratio]", "desc": "close >= field * ratio, e.g. close above_pct [price_52_week_high, 0.95] is within 5% of the high"},
    {"op": "below_pct", "value": "[field id, ratio]", "desc": "close <= field * ratio, e.g. close below_pct [price_52_week_low, 1.05] is within 5% of the low"},
    {"op": "crosses", "value": "number or field id", "desc": "crossed in either direction this bar"},
    {"op": "crosses_above", "value": "number or field id", "desc": "crossed up through (golden-cross style)"},
    {"op": "crosses_below", "value": "number or field id", "desc": "crossed down through (death-cross style)"},
    {"op": "isin", "value": "[v1, v2, ...]", "desc": "value is one of"},
    {"op": "not_in", "value": "[v1, v2, ...]", "desc": "value is none of"},
    {"op": "in_day_range", "value": "[low, high]", "desc": "today's range overlaps"},
    {"op": "in_week_range", "value": "[low, high]", "desc": "this week's range overlaps"},
    {"op": "in_month_range", "value": "[low, high]", "desc": "this month's range overlaps"},
    {"op": "like", "value": "substring", "desc": "text contains"},
    {"op": "not_like", "value": "substring", "desc": "text does not contain"},
    {"op": "empty", "value": "(none)", "desc": "field has no value"},
    {"op": "not_empty", "value": "(none)", "desc": "field has a value"},
]


# Friendly chart intervals mapped to TradingView interval codes. The values are
# exactly what the TradingView chart URL and the advanced-chart widget accept.
_INTERVALS: dict[str, str] = {
    "1m": "1", "1": "1", "1min": "1",
    "5m": "5", "5": "5",
    "15m": "15", "15": "15",
    "30m": "30", "30": "30",
    "1h": "60", "60": "60", "hourly": "60",
    "2h": "120", "120": "120",
    "4h": "240", "240": "240",
    "1d": "D", "d": "D", "day": "D", "daily": "D",
    "1w": "W", "w": "W", "week": "W", "weekly": "W",
    "1mo": "M", "m": "M", "month": "M", "monthly": "M",
}


def normalize_interval(value: str) -> str:
    """Map a friendly interval ("4h", "daily", "1W") to a TradingView code."""
    return _INTERVALS.get(str(value).strip().lower(), "D")


# TradingView's Recommend.* gauges are floats in [-1, 1]. These are the same
# bands the TradingView UI uses for its Strong Buy / Buy / Neutral / ... label.
def rating_label(score: float | None) -> str:
    """Map a Recommend.* score to its TradingView text rating."""
    if score is None:
        return "Unknown"
    if score >= 0.5:
        return "Strong Buy"
    if score >= 0.1:
        return "Buy"
    if score > -0.1:
        return "Neutral"
    if score > -0.5:
        return "Sell"
    return "Strong Sell"


def _resolve_row(symbol: str, market: str, columns: list[str]) -> tuple[dict | None, str | None]:
    """Fetch one row by ticker symbol (case-insensitive on `name`).

    Returns (row, error). Callers must treat those as different outcomes.

    run_screen never raises. On an upstream failure it returns no rows plus
    meta.error, so reading only the rows turns a dead or rate limited data
    source into "no such symbol", which is a confident false statement about a
    ticker that is perfectly real. Every other run_screen caller in this module
    reads meta.error. This one did not, and it backs the analyze and
    technical_rating tools, the two used to look at a single name in depth.
    """
    cols = [c for c in columns if validate_field(c)] or ["name"]
    req = ScreenRequest(
        market=market,
        filters=[Filter(field="name", op="==", value=symbol.upper())],
        columns=cols,
        limit=1,
    )
    resp = run_screen(req)
    err = (resp.get("meta") or {}).get("error")
    if err:
        _STATS["errors"] += 1
        return None, err
    rows = resp.get("rows") or []
    return (rows[0] if rows else None), None


def _unavailable(ticker: str, err: str) -> dict:
    """The response for "we could not check", as distinct from "it does not exist"."""
    return {
        "error": f"Data source unavailable, so '{ticker.upper()}' could not be checked.",
        "detail": err,
        "symbol_exists": "unknown",
        "note": ("This is NOT a statement that the symbol does not exist. The screen "
                 "itself failed. Retry, or confirm the name from another source before "
                 "concluding anything about it."),
    }


# ----------------------------------------------------------------------------
# OTC guard for the market study tools
#
# presets.py guards the preset scans. The tools ran unguarded, and the two that
# rank by percentage move were made entirely of the junk the preset guard exists
# to remove. Measured 2026-08-02 against the america universe:
#
#   top_movers   gainers 10/10 OTC, losers 10/10 OTC
#   gap_scanner  gap_up 50/50 OTC, gap_down 50/50 OTC
#
# That is not a coincidence of the sample. A sub-penny shell that ticks one
# hundredth of a cent posts a four figure percentage move, so anything sorting
# on change or gap descending returns those rows and only those rows. The top
# gap up was MFDB at +334900%.
#
# Tools that rank on volume, relative strength, EMA stacking or a factor score
# measured 0% OTC even unguarded, and so did the breadth percentages: those
# tools sample in the market's default market-cap order, which does not reach
# the shells at ordinary limits. They are guarded anyway, for three reasons that
# are smaller but real. Their reported `universe` counted rows they would never
# have sampled (7,648 rather than 5,182). Raising `limit`, or passing filters
# that reorder the sample, walks straight into the same rows top_movers was
# built entirely from. And a factor blend is z-scored ACROSS the returned set,
# so one absurd reading moves the mean and standard deviation and therefore
# every other row's score, whether or not the shell itself surfaces.
#
# Same shape as the preset guard, deliberately: exchange exclusion only, no
# market cap and no share price floor, so a $2 NASDAQ small cap still comes
# back. Lift it per call with include_otc=True.
# ----------------------------------------------------------------------------
def _otc_guard(
    market: str, filters: list[dict] | None, include_otc: bool
) -> tuple[list[dict], dict | None]:
    """Return (filters, guard_block) with the OTC exclusion applied.

    Only for america. `exchange` carries US venue names, so the condition is
    meaningless in crypto, forex, futures, bond and cfd, and is skipped there.

    A caller who filters on `exchange` themselves is left alone. They have
    already said what they want from the venue and we should not second guess
    it.

    Every tool using this passes match "all", so the appended condition
    RESTRICTS the result. It would WIDEN it under match "any", which is why
    `scan_screen` is deliberately NOT guarded: it is the raw escape hatch, it takes
    a caller-supplied match, and its filters are entirely the caller's.
    """
    raw = [dict(f) for f in (filters or [])]
    if market != "america":
        return raw, None
    if include_otc:
        return raw, {
            "active": False,
            "note": "OTC guard lifted by include_otc=True. Expect sub-penny shells "
                    "whose percentage moves are a fraction of a cent expressed as "
                    "a percentage.",
        }
    if any(f.get("field") == "exchange" for f in raw):
        return raw, {
            "active": False,
            "note": "OTC guard skipped: the caller supplied their own exchange filter.",
        }
    return [*raw, dict(NO_OTC)], {
        "active": True,
        "excluded_venue": "OTC",
        "note": "OTC excluded as a data quality filter. No market cap or share "
                "price floor is applied, so listed small caps still come back. "
                "Pass include_otc=True to scan the full venue.",
    }


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _render_table(columns: list[str], rows: list[dict], max_rows: int = 50) -> str:
    """Render rows as an aligned monospace table, token-efficient for the model."""
    if not rows:
        return "(no rows)"
    cols = columns or list(rows[0].keys())
    shown = rows[:max_rows]

    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.4g}" if abs(v) < 1e6 else f"{v:,.0f}"
        return str(v)

    widths = {c: len(c) for c in cols}
    table_cells = []
    for r in shown:
        row_cells = {c: cell(r.get(c)) for c in cols}
        for c in cols:
            widths[c] = max(widths[c], len(row_cells[c]))
        table_cells.append(row_cells)

    header = "  ".join(c.ljust(widths[c]) for c in cols)
    sep = "  ".join("-" * widths[c] for c in cols)
    body = "\n".join(
        "  ".join(rc[c].ljust(widths[c]) for c in cols) for rc in table_cells
    )
    extra = f"\n... {len(rows) - max_rows} more rows" if len(rows) > max_rows else ""
    return f"{header}\n{sep}\n{body}{extra}"


def _shape(resp: dict, table_rows: int = 50) -> dict:
    """Add a rendered table and a one-line summary to a pipeline response."""
    meta = resp.get("meta", {})
    if meta.get("error"):
        _STATS["errors"] += 1
        return {
            "error": meta["error"],
            "error_code": meta.get("error_code"),
            "detail": meta.get("detail"),
            "market": meta.get("market"),
            "count": 0,
            "rows": [],
        }
    rows, columns = resp["rows"], resp["columns"]
    extra: dict = {}
    if meta.get("warnings"):
        extra["warnings"] = meta["warnings"]
    return {
        **extra,
        "mode": meta.get("mode", "delayed"),
        "count": resp["count"],
        "returned": len(rows),
        "market": meta.get("market"),
        "ms": meta.get("ms"),
        "columns": columns,
        "rows": rows,
        "table": _render_table(columns, rows, max_rows=table_rows),
    }


def _run(req: ScreenRequest, table_rows: int = 50) -> dict:
    """Run a screen through the cache and shape it for the model."""
    _STATS["calls"] += 1
    cached = _cache.get(request_key(req))
    if cached is not None:
        _STATS["cache_hits"] += 1
        return _shape(cached, table_rows)
    resp = run_screen(req)
    if resp["meta"].get("error") is None:
        # Re-derive the key: a rejected cookie mid-run changes the mode.
        _cache.set(request_key(req), resp)
    return _shape(resp, table_rows)


# ----------------------------------------------------------------------------
# Discovery tools
# ----------------------------------------------------------------------------
@_tool
def list_markets() -> list[dict]:
    """List the markets the screener can target.

    Returns each market's id (use as the `market` arg elsewhere), a label, and
    its kind. Covers US stocks, crypto, forex, futures, bonds, and CFDs.
    """
    return MARKETS


@_tool
def search_fields(query: str = "", group: str = "", limit: Limit = 40) -> dict:
    """Search the TradingView field universe (1000+ fields) by id, label, or group.

    This is how you discover what you can filter, sort, and select on. The
    curated set (price, performance, valuation, profitability, dividends,
    technicals, moving averages, oscillators, volume, volatility) leads; every
    other queryable base field is reachable too.

    query: case-insensitive substring matched against field id and label.
    group: optional exact group filter, e.g. "Oscillators", "Valuation".
    Returns {count, fields:[{id,label,group,type,unit,curated}]}.
    """
    q = query.strip().lower()
    g = group.strip().lower()
    out: list[dict] = []
    for f in FIELDS:
        if g and f["group"].lower() != g:
            continue
        if q and q not in f["id"].lower() and q not in f["label"].lower():
            continue
        out.append({**f, "curated": f["id"] in CURATED_IDS})
        if len(out) >= max(1, limit):
            break
    return {"count": len(out), "fields": out}


@_tool
def list_operators() -> list[dict]:
    """List the filter operators, what value each expects, and what it means.

    Note the cross-field operators: a value may be another field id, so
    {"field":"SMA50","op":"crosses_above","value":"SMA200"} is a golden cross
    and {"field":"close","op":">","value":"VWAP"} is price over VWAP.
    """
    return OPERATORS


@_tool
def list_presets(group: str = "") -> list[dict]:
    """List the one-click preset scans (id, name, description, market, group).

    47 presets across general scans and the SIGNALS / MOMENTUM / TREND /
    MULTI-TIMEFRAME signal groups. Pass `group` to filter, e.g. "Momentum".
    Run one with `scan_run_preset`.
    """
    g = group.strip().lower()
    out = []
    for p in PRESETS:
        if g and p.get("group", "").lower() != g:
            continue
        entry = {
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "market": p["market"],
            "group": p.get("group", "General"),
        }
        if p.get("unavailable"):
            entry["unavailable"] = p["unavailable"]
        if p.get("otc_guarded"):
            entry["otc_guarded"] = True
        out.append(entry)
    return out


@_tool
def list_factor_presets() -> list[dict]:
    """List the composite factor-scoring presets (Momentum, Value, Quality, ...).

    Each is a weighted, direction-aware blend of fields. Run one with
    `scan_run_factor_preset` to rank a market by that factor.
    """
    return [
        {"id": fp["id"], "name": fp["name"], "weights": fp["weights"]}
        for fp in FACTOR_PRESETS
    ]


# ----------------------------------------------------------------------------
# The core screen tool
# ----------------------------------------------------------------------------
@_tool
def screen(
    market: str = "america",
    filters: list[dict] | None = None,
    columns: list[str] | None = None,
    match: str = "all",
    sort: list[dict] | None = None,
    computed: list[dict] | None = None,
    stats: list[dict] | None = None,
    factor: list[dict] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    """Run a live market screen and return the matching rows.

    This is the full engine the web app uses, including the analytics layer that
    makes this more than a filter.

    market:  one of scan_list_markets() ids (america, crypto, forex, futures, bond, cfd).
    filters: list of {"field","op","value"}. See scan_list_operators(). Values may be
             numbers, strings, lists, or another field id (cross-field). Example:
             [{"field":"market_cap_basic","op":">","value":1e9},
              {"field":"RSI","op":"<","value":30}]
    columns: field ids to select (see scan_search_fields). Defaults to a sensible set.
    match:   "all" (AND) or "any" (OR) across filters.
    sort:    list of {"field","dir"} with dir "asc"|"desc". First key is applied
             server-side; you may sort by a computed/stat/factor column too.
    computed: derived columns via a sandboxed expression engine, e.g.
             [{"id":"dollar_vol","expr":"close*volume"},
              {"id":"range_pct","expr":"(high-low)/close*100"}].
             Operators + - * / % ** and abs min max round sqrt log ln floor ceil.
    stats:   in-result statistics computed across the returned set, e.g.
             [{"fn":"zscore","field":"change"},{"fn":"pctrank","field":"volume"}].
             fn is one of zscore | pctrank | rank | norm | madzscore | winsor | decile.
             Column name is "fn(field)". madzscore uses median+MAD (outlier-resistant).
             winsor clips at 5th/95th pct then normalizes to [0,1] (factor scoring).
             decile assigns each row to a decile 1-10 (1=lowest, 10=highest).
    factor:  composite factor score: list of {"field","weight","dir"} with dir
             "high"|"low". Adds a "factor_score" column and ranks by it when no
             explicit sort is given.
    limit:   max rows (default 50). offset: skip N rows for paging.

    Returns {count, returned, market, ms, columns, rows, table}. `count` is the
    full universe size matching the filters; `rows` is the page you asked for.
    """
    factor_model = None
    if factor:
        factor_model = Factor(weights=[FactorWeight(**w) for w in factor])
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in (filters or [])],
        columns=columns or [],
        match=match,
        sort=[SortKey(**s) for s in (sort or [])],
        computed=[Computed(**c) for c in (computed or [])],
        stats=[Stat(**s) for s in (stats or [])],
        factor=factor_model,
        limit=limit,
        offset=offset,
    )
    return _run(req, table_rows=min(limit, 50))


@_tool
def run_preset(preset_id: str, limit: Limit = 50, include_otc: bool = False) -> dict:
    """Run a named preset scan (see list_presets) and return its rows.

    The preset supplies the market, filters, columns, and sort. `limit` caps
    the rows returned.

    Most signal presets exclude OTC by default, because an indicator reading on a
    sub-penny shell is computed from noise rather than from a trend: those rows
    carry "changes" like +9,900 percent, which is a $0.0001 tick expressed as a
    percentage. This is a data quality filter only. There is NO market cap or
    share price floor, so genuine small and cheap listed names still come back.

    Set include_otc=True to lift it and scan the full venue, deliberately. When
    the guard is active the response carries an `otc_guard` block reporting how
    many rows it removed, so nothing is dropped silently.
    """
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if preset is None:
        return {"error": f"Unknown preset: {preset_id}. See scan_list_presets()."}
    if preset.get("unavailable"):
        # Refuse up front rather than running a screen that can only return
        # nothing. An empty result reads as "no matches today", which is a very
        # different claim from "this data does not exist".
        _STATS["errors"] += 1
        return {
            "error": f"Preset '{preset_id}' is unavailable on this data source.",
            "reason": preset["unavailable"],
            "count": 0,
            "rows": [],
        }

    filters = list(preset.get("filters", []))
    guarded = bool(preset.get("otc_guarded"))
    if guarded and include_otc:
        filters = [f for f in filters if f.get("field") != "exchange"]

    req = ScreenRequest(
        market=preset["market"],
        filters=[Filter(**f) for f in filters],
        columns=preset.get("columns", []),
        match=preset.get("match", "all"),
        sort=[SortKey(**s) for s in preset.get("sort", [])],
        limit=limit,
    )
    out = _run(req, table_rows=min(limit, 50))

    if guarded and not include_otc and "error" not in out:
        # One cheap limit=1 probe: run_screen returns the FULL match count
        # regardless of limit, so this costs one small query and turns a silent
        # exclusion into a reported one.
        probe = ScreenRequest(
            market=preset["market"],
            filters=[Filter(**f) for f in filters if f.get("field") != "exchange"],
            columns=["name"],
            match=preset.get("match", "all"),
            limit=1,
        )
        unfiltered = run_screen(probe)
        total = unfiltered["count"] if not unfiltered["meta"].get("error") else None
        out["otc_guard"] = {
            "active": True,
            "matches_excluding_otc": out.get("count"),
            "matches_including_otc": total,
            "excluded": (total - out["count"]) if isinstance(total, int) else "unknown",
            "note": "OTC excluded as a data quality filter. "
                    "No market cap or price floor is applied. "
                    "Pass include_otc=True to scan the full venue.",
        }
    elif guarded and include_otc and "error" not in out:
        out["otc_guard"] = {
            "active": False,
            "note": "OTC guard lifted by include_otc=True. Expect sub-penny shells "
                    "whose indicator values are computed from noise.",
        }
    return out


@_tool
def run_factor_preset(
    factor_preset_id: str,
    market: str = "america",
    filters: list[dict] | None = None,
    columns: list[str] | None = None,
    limit: Limit = 50,
    include_otc: bool = False,
) -> dict:
    """Rank a market by a named composite factor (see list_factor_presets).

    Applies the factor's weighted, direction-aware z-score blend, adds a
    "factor_score" column, and ranks by it. Narrow the universe first with
    `filters` (recommended, e.g. a market-cap floor) so the ranking is over a
    meaningful set.

    On america, OTC is excluded by default. The blend is z-scored ACROSS the
    returned rows, so this is not only about which names surface: a shell with
    an absurd reading moves the mean and the standard deviation, and therefore
    every other row's score. Pass include_otc=True to scan the full venue.
    """
    fp = next((f for f in FACTOR_PRESETS if f["id"] == factor_preset_id), None)
    if fp is None:
        return {"error": f"Unknown factor preset: {factor_preset_id}. See scan_list_factor_presets()."}
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=columns or [],
        factor=Factor(weights=[FactorWeight(**w) for w in fp["weights"]]),
        limit=limit,
    )
    out = _run(req, table_rows=min(limit, 50))
    if guard and "error" not in out:
        out["otc_guard"] = guard
    return out


@_tool
def lookup_symbol(ticker: str, market: str = "america", columns: list[str] | None = None) -> dict:
    """Fetch one symbol's row by ticker (e.g. "NVDA", "BTCUSD").

    Matches case-insensitively on the `name` field. Returns the single row with
    the requested columns (or a broad default set), or an error if not found.
    """
    cols = columns or [
        "name", "description", "close", "change", "volume", "market_cap_basic",
        "RSI", "Perf.1M", "Perf.YTD", "price_earnings_ttm", "sector",
    ]
    cols = [c for c in cols if c in field_index]
    req = ScreenRequest(
        market=market,
        filters=[Filter(field="name", op="==", value=ticker.upper())],
        columns=cols,
        limit=1,
    )
    out = _run(req, table_rows=1)
    if out.get("rows"):
        return {"symbol": ticker.upper(), "market": market, "row": out["rows"][0], "table": out["table"]}
    if out.get("error"):
        return out
    return {"error": f"No symbol '{ticker.upper()}' found in market '{market}'."}


@_tool
def server_stats() -> dict:
    """Report screener MCP server health: calls served, cache hit rate, errors.

    A self-monitoring tool, handy for confirming the cache is doing its job and
    that upstream calls are healthy.
    """
    uptime = time.time() - _STATS["started"]
    calls = _STATS["calls"]
    return {
        "uptime_seconds": int(uptime),
        "screen_calls": calls,
        "cache_hits": _STATS["cache_hits"],
        "cache_hit_rate": round(_STATS["cache_hits"] / calls, 3) if calls else 0.0,
        "errors": _STATS["errors"],
        "cache_ttl_seconds": _cache.ttl,
        "fields_indexed": len(FIELDS),
        "presets": len(PRESETS),
        "factor_presets": len(FACTOR_PRESETS),
        "markets": len(MARKETS),
    }


# ----------------------------------------------------------------------------
# Symbol intelligence: search, compare, rate, and read a chart
# ----------------------------------------------------------------------------
@_tool
def search_symbols(query: str, market: str = "america", limit: Limit = 20) -> dict:
    """Find symbols by ticker or company name (substring, case-insensitive).

    Resolves "apple" or "nvda" to real rows so you can hand a ticker to the
    other tools. Matches the `name` and `description` fields. Returns
    {count, symbols:[{ticker,name,description,close,change,sector,market_cap_basic}]}.
    """
    q = query.strip()
    if not q:
        return {"count": 0, "symbols": []}
    cols = ["name", "description", "close", "change", "sector", "market_cap_basic"]
    cols = [c for c in cols if c in field_index]
    req = ScreenRequest(
        market=market,
        filters=[
            Filter(field="name", op="like", value=q),
            Filter(field="description", op="like", value=q),
        ],
        match="any",
        columns=cols,
        sort=[SortKey(field="market_cap_basic", dir="desc")] if "market_cap_basic" in field_index else [],
        limit=limit,
    )
    out = _run(req, table_rows=limit)
    if out.get("error"):
        return out
    return {"count": out["count"], "symbols": out["rows"], "table": out.get("table")}


@_tool
def compare(tickers: list[str], columns: list[str] | None = None, market: str = "america") -> dict:
    """Put several symbols side by side on the same columns.

    tickers: e.g. ["NVDA","AMD","AVGO"]. columns defaults to a broad price +
    performance + valuation set. Returns the aligned rows and a rendered table.
    """
    syms = [t.upper() for t in (tickers or []) if t]
    if not syms:
        return {"error": "Pass at least one ticker."}
    cols = columns or [
        "name", "close", "change", "Perf.1M", "Perf.YTD", "RSI",
        "market_cap_basic", "price_earnings_ttm", "Recommend.All",
    ]
    bad = [c for c in cols if not validate_field(c)]
    if bad:
        return {"error": "Unknown column: " + ", ".join(f"'{c}'" for c in bad[:5]) + "."}
    syms = list(dict.fromkeys(syms))
    if len(syms) > MAX_LIMIT:
        return {"error": f"Compare at most {MAX_LIMIT} tickers."}
    req = ScreenRequest(
        market=market,
        filters=[Filter(field="name", op="isin", value=syms)],
        columns=cols,
        limit=max(len(syms), 1),
    )
    out = _run(req, table_rows=len(syms))
    if out.get("error"):
        return out
    found = {str(r.get("name", "")).upper() for r in out.get("rows", [])}
    out["not_found"] = [t for t in syms if t not in found]
    return out


@_tool
def technical_rating(ticker: str, market: str = "america", timeframes: list[str] | None = None) -> dict:
    """TradingView's own technical rating gauge for a symbol.

    Returns the overall, moving-average, and oscillator ratings (Strong Buy /
    Buy / Neutral / Sell / Strong Sell), optionally across several timeframes.

    timeframes: any of "1m","5m","15m","1h","4h","1d","1w","1mo" (default "1d").
    """
    tfs = timeframes or ["1d"]
    unknown = [t for t in tfs if str(t).strip().lower() not in _INTERVALS]
    if unknown:
        return {
            "error": "Unknown timeframe: " + ", ".join(f"'{t}'" for t in unknown) + ".",
            "valid_timeframes": ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1mo"],
        }
    base = ["Recommend.All", "Recommend.MA", "Recommend.Other"]
    # Build the suffixed column set. Daily uses the bare field; others get |code.
    wanted = ["name", "description", "close", "change"]
    tf_cols: dict[str, dict] = {}
    for tf in tfs:
        code = normalize_interval(tf)
        # The scanner spells weekly and monthly |1W and |1M, unlike chart URLs.
        code = {"W": "1W", "M": "1M"}.get(code, code)
        suffix = "" if code == "D" else f"|{code}"
        tf_cols[tf] = {b: f"{b}{suffix}" for b in base}
        wanted += list(tf_cols[tf].values())
    row, err = _resolve_row(ticker, market, wanted)
    if err:
        return _unavailable(ticker, err)
    if row is None:
        return {"error": f"No symbol '{ticker.upper()}' in market '{market}'."}

    ratings = {}
    for tf, cols in tf_cols.items():
        ratings[tf] = {
            "overall": rating_label(row.get(cols["Recommend.All"])),
            "moving_averages": rating_label(row.get(cols["Recommend.MA"])),
            "oscillators": rating_label(row.get(cols["Recommend.Other"])),
            "score": row.get(cols["Recommend.All"]),
        }
    return {
        "ticker": row.get("name", ticker.upper()),
        "description": row.get("description"),
        "close": row.get("close"),
        "change": row.get("change"),
        "ratings": ratings,
    }


# Multi-timeframe ladder. Each entry is (label, suffix-code). Daily is the bare
# field; the others carry the |code suffix the scanner accepts. All probed live.
_TIMEFRAMES = [("1h", "60"), ("4h", "240"), ("1d", "D"), ("1w", "1W"), ("1m", "1M")]


def _tf_field(base: str, code: str) -> str:
    """RSI, "D" -> "RSI"; RSI, "240" -> "RSI|240"."""
    return base if code == "D" else f"{base}|{code}"


# Columns the chart-read pulls. Daily-centric trend/range/rating fields, plus the
# RSI and MACD across every timeframe so the read is multi-timeframe in one shot,
# no chart swapping. Fields that do not populate for a market simply drop out.
_READ_COLUMNS = [
    "name", "description", "close", "change", "sector",
    "RSI", "MACD.macd", "MACD.signal", "Stoch.K", "Stoch.D",
    "ADX", "ADX+DI", "ADX-DI", "AO", "CCI20", "W.R",
    "EMA8", "EMA21", "SMA20", "SMA50", "SMA200",
    "price_52_week_high", "price_52_week_low",
    "Perf.W", "Perf.1M", "Perf.YTD", "relative_volume_10d_calc", "Volatility.D",
    "Recommend.All", "Recommend.MA", "Recommend.Other",
] + [
    _tf_field(b, code)
    for _lbl, code in _TIMEFRAMES
    for b in ("RSI", "MACD.macd", "MACD.signal")
]


@_tool
def analyze(ticker: str, market: str = "america") -> dict:
    """Read a symbol's chart: a structured, multi-timeframe technical analysis.

    Composes TradingView's own fields into trend, momentum, range, and rating
    reads plus a list of plain-language signals, so an agent can narrate "what
    the chart is saying" without eyeballing it. The read is multi-timeframe in a
    single call: RSI and MACD bias on the 1h, 4h, 1d, 1w, and 1m at once, with an
    alignment verdict, so nobody has to swap timeframes. Pure TradingView data.

    Returns {ticker, close, change, trend, momentum, multi_timeframe, range,
    rating, performance, signals[], summary}.
    """
    row, err = _resolve_row(ticker, market, _READ_COLUMNS)
    if err:
        return _unavailable(ticker, err)
    if row is None:
        return {"error": f"No symbol '{ticker.upper()}' in market '{market}'."}

    g = row.get
    signals: list[str] = []

    def above(a, b):
        return g(a) is not None and g(b) is not None and g(a) > g(b)

    # ---- Trend: price versus the moving-average stack.
    close = g("close")
    ma_pos = {}
    for ma in ("SMA20", "SMA50", "SMA200"):
        if close is not None and g(ma) is not None:
            ma_pos[ma] = "above" if close > g(ma) else "below"
    if ma_pos and all(v == "above" for v in ma_pos.values()):
        signals.append("Price above all major moving averages")
    elif ma_pos and all(v == "below" for v in ma_pos.values()):
        signals.append("Price below all major moving averages")
    golden = above("SMA50", "SMA200")
    if g("SMA50") is not None and g("SMA200") is not None:
        signals.append("SMA50 above SMA200 (golden-cross stack)" if golden
                       else "SMA50 below SMA200 (death-cross stack)")
    adx = g("ADX")
    if adx is not None:
        di_bull = above("ADX+DI", "ADX-DI")
        strength = "strong" if adx >= 25 else "weak"
        direction = "up" if di_bull else "down"
        trend_label = f"{strength} {direction}trend (ADX {adx:.0f})"
        if adx >= 25:
            signals.append(f"ADX {adx:.0f}: strong { 'up' if di_bull else 'down' }trend")
    else:
        trend_label = None
    ema_fast = "EMA8 above EMA21 (fast bull)" if above("EMA8", "EMA21") else None
    if ema_fast:
        signals.append(ema_fast)

    trend = {
        "vs_moving_averages": ma_pos,
        "golden_cross_stack": golden if (g("SMA50") is not None and g("SMA200") is not None) else None,
        "ema_fast_bull": above("EMA8", "EMA21") if (g("EMA8") is not None and g("EMA21") is not None) else None,
        "adx": adx,
        "label": trend_label,
    }

    # ---- Momentum: RSI / MACD / Stochastic.
    rsi = g("RSI")
    rsi_state = None
    if rsi is not None:
        if rsi >= 70:
            rsi_state = "overbought"; signals.append(f"RSI {rsi:.0f}: overbought")
        elif rsi <= 30:
            rsi_state = "oversold"; signals.append(f"RSI {rsi:.0f}: oversold")
        elif rsi >= 50:
            rsi_state = "bullish"
        else:
            rsi_state = "bearish"
    macd_bull = above("MACD.macd", "MACD.signal")
    if g("MACD.macd") is not None and g("MACD.signal") is not None:
        signals.append("MACD above signal (bullish)" if macd_bull else "MACD below signal (bearish)")
    stoch_bull = above("Stoch.K", "Stoch.D")
    momentum = {
        "rsi": rsi,
        "rsi_state": rsi_state,
        "rsi_weekly": g("RSI|1W"),
        "macd_bullish": macd_bull if (g("MACD.macd") is not None and g("MACD.signal") is not None) else None,
        "stochastic_bullish": stoch_bull if (g("Stoch.K") is not None and g("Stoch.D") is not None) else None,
    }

    # ---- Range: position within the 52-week band.
    hi, lo = g("price_52_week_high"), g("price_52_week_low")
    rng = {}
    if close is not None and hi is not None and lo is not None and hi > lo:
        pct_from_high = (close - hi) / hi * 100.0
        pct_off_low = (close - lo) / lo * 100.0
        pos = (close - lo) / (hi - lo) * 100.0
        rng = {
            "high_52w": hi, "low_52w": lo,
            "pct_from_high": round(pct_from_high, 2),
            "pct_off_low": round(pct_off_low, 2),
            "position_in_range": round(pos, 1),
        }
        if pct_from_high >= -3:
            signals.append("Within 3% of the 52-week high")
        if pct_off_low <= 3:
            signals.append("Within 3% of the 52-week low")

    # ---- Multi-timeframe: RSI + MACD bias on every timeframe at once.
    timeframes: dict[str, dict] = {}
    bull_tfs, bear_tfs = [], []
    for lbl, code in _TIMEFRAMES:
        tf_rsi = g(_tf_field("RSI", code))
        tf_macd = g(_tf_field("MACD.macd", code))
        tf_sig = g(_tf_field("MACD.signal", code))
        if tf_rsi is None and tf_macd is None:
            continue
        rsi_bull = tf_rsi is not None and tf_rsi > 50
        macd_bull = (tf_macd is not None and tf_sig is not None and tf_macd > tf_sig)
        if tf_rsi is not None and tf_macd is not None and tf_sig is not None:
            bias = "bull" if (rsi_bull and macd_bull) else "bear" if (not rsi_bull and not macd_bull) else "mixed"
        elif tf_rsi is not None:
            bias = "bull" if rsi_bull else "bear"
        else:
            bias = "bull" if macd_bull else "bear"
        if bias == "bull":
            bull_tfs.append(lbl)
        elif bias == "bear":
            bear_tfs.append(lbl)
        timeframes[lbl] = {
            "rsi": tf_rsi,
            "macd_bullish": macd_bull if (tf_macd is not None and tf_sig is not None) else None,
            "bias": bias,
        }

    graded = [tf for tf in timeframes.values() if tf["bias"] in ("bull", "bear")]
    if graded and all(tf["bias"] == "bull" for tf in graded):
        alignment = "fully aligned bullish"
        signals.append(f"Aligned bullish across all timeframes ({', '.join(bull_tfs)})")
    elif graded and all(tf["bias"] == "bear" for tf in graded):
        alignment = "fully aligned bearish"
        signals.append(f"Aligned bearish across all timeframes ({', '.join(bear_tfs)})")
    elif graded:
        alignment = f"mixed ({len(bull_tfs)} bull, {len(bear_tfs)} bear)"
    else:
        alignment = "unknown"
    multi_timeframe = {"alignment": alignment, "bullish": bull_tfs, "bearish": bear_tfs, "by_timeframe": timeframes}

    rating = {
        "overall": rating_label(g("Recommend.All")),
        "moving_averages": rating_label(g("Recommend.MA")),
        "oscillators": rating_label(g("Recommend.Other")),
        "score": g("Recommend.All"),
    }

    performance = {k: g(v) for k, v in (("week", "Perf.W"), ("month", "Perf.1M"), ("ytd", "Perf.YTD")) if g(v) is not None}

    summary = (
        f"{row.get('name', ticker.upper())} at {close}: {rating['overall']} overall. "
        f"{trend_label or 'trend n/a'}; "
        f"RSI {('%.0f' % rsi) if rsi is not None else 'n/a'}"
        f"{f' ({rsi_state})' if rsi_state else ''}; "
        f"MACD {'bullish' if macd_bull else 'bearish'}; "
        f"timeframes {alignment}."
    )

    return {
        "ticker": row.get("name", ticker.upper()),
        "description": row.get("description"),
        "market": market,
        "close": close,
        "change": row.get("change"),
        "trend": trend,
        "momentum": momentum,
        "multi_timeframe": multi_timeframe,
        "range": rng,
        "rating": rating,
        "performance": performance,
        "signals": signals,
        "summary": summary,
    }


# Fields fundamentals() reads. The catalog (backend/fields.py) already carries all of these.
# analyze() never touched them because it is deliberately technicals-only, so this is
# composition, not new data.
#
# Three catalog fields that validate but are DEAD (null on every US row, per the same
# discovery already made for the growth factor preset and the dividend_aristocrats preset in
# presets.py, and re-confirmed here by sampling 300 large-cap rows): `revenue_growth_ttm_yoy` /
# `revenue_growth_fq_yoy` / `earnings_per_share_diluted_growth_percent_ttm_yoy` (0/300),
# `dividend_yield_recent` (0/300), `payout_ratio` (0/300), `days_to_earnings` (0/300, per
# `_days_to_earnings`'s own docstring below). Do not reintroduce them, they validate against
# `validate_field()` so nothing catches the mistake except sampling real rows.
_FUNDAMENTALS_COLUMNS = [
    "name", "description", "sector", "industry", "number_of_employees",
    "market_cap_basic",
    "price_earnings_ttm", "price_earnings_growth_ttm", "price_sales_current",
    "price_book_fq", "price_free_cash_flow_ttm", "enterprise_value_ebitda_ttm",
    "enterprise_value_to_revenue_ttm",
    "earnings_per_share_basic_ttm", "earnings_per_share_diluted_ttm",
    "earnings_per_share_forecast_next_fq",
    "gross_margin", "operating_margin", "net_margin", "free_cash_flow_margin_ttm",
    "return_on_equity", "return_on_assets", "return_on_invested_capital",
    "total_revenue", "net_income",
    "total_revenue_yoy_growth_ttm", "total_revenue_yoy_growth_fq",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "total_debt", "total_equity_fq", "debt_to_equity", "current_ratio", "quick_ratio",
    "cash_n_short_term_invest_fq", "net_debt",
    "dividends_yield", "dividend_payout_ratio_ttm", "continuous_dividend_growth",
    "earnings_release_next_date",
]


@_tool
def fundamentals(ticker: str, market: str = "america") -> dict:
    """Read a symbol's fundamentals: valuation, profitability, growth, leverage, dividends.

    The counterpart to `analyze()`, which is deliberately technicals-only. This is the
    fundamentals half of the same symbol, composed from TradingView's own reported financials
    (same data `scan_screen`/`scan_compare` can already pull field-by-field, packaged here into a single
    narratable read plus plain-language flags).

    A missing field (TradingView carries no fundamentals for some ETFs, SPACs, and non-US
    listings) is left out of its section rather than shown as zero. A `0.0` P/E and a
    genuinely absent one must never look the same.

    Returns {ticker, sector, industry, valuation, profitability, growth, leverage, dividends,
    earnings, signals[], summary}.
    """
    row, err = _resolve_row(ticker, market, _FUNDAMENTALS_COLUMNS)
    if err:
        return _unavailable(ticker, err)
    if row is None:
        return {"error": f"No symbol '{ticker.upper()}' in market '{market}'."}

    g = row.get
    signals: list[str] = []

    def section(*keys: str) -> dict:
        return {k: g(k) for k in keys if g(k) is not None}

    valuation = section(
        "market_cap_basic", "price_earnings_ttm", "price_earnings_growth_ttm",
        "price_sales_current", "price_book_fq", "price_free_cash_flow_ttm",
        "enterprise_value_ebitda_ttm", "enterprise_value_to_revenue_ttm",
        "earnings_per_share_basic_ttm", "earnings_per_share_diluted_ttm",
        "earnings_per_share_forecast_next_fq",
    )
    profitability = section(
        "gross_margin", "operating_margin", "net_margin", "free_cash_flow_margin_ttm",
        "return_on_equity", "return_on_assets", "return_on_invested_capital",
    )
    growth = section(
        "total_revenue", "net_income", "total_revenue_yoy_growth_ttm",
        "total_revenue_yoy_growth_fq", "earnings_per_share_diluted_yoy_growth_ttm",
    )
    leverage = section(
        "total_debt", "total_equity_fq", "debt_to_equity", "current_ratio", "quick_ratio",
        "cash_n_short_term_invest_fq", "net_debt",
    )
    dividends = section("dividends_yield", "dividend_payout_ratio_ttm", "continuous_dividend_growth")
    # days_to_earnings is dead on TradingView's side (see _days_to_earnings's docstring).
    # Derive it the same way the rest of this module already does, from the populated
    # earnings_release_next_date timestamp.
    dte = _days_to_earnings(g("earnings_release_next_date"))
    earnings = section("earnings_release_next_date")
    if dte is not None:
        earnings["days_to_earnings"] = dte

    # ---- Plain-language flags. Each one only fires when every field it needs is present.
    eps_ttm = g("earnings_per_share_basic_ttm")
    if eps_ttm is not None and eps_ttm < 0:
        signals.append("Unprofitable on a TTM EPS basis")

    pe = g("price_earnings_ttm")
    peg = g("price_earnings_growth_ttm")
    if pe is not None and pe > 0 and peg is not None:
        if 0 < peg < 1:
            signals.append(f"PEG {peg:.2f}: growth looks cheap relative to the P/E")
        elif peg > 2:
            signals.append(f"PEG {peg:.2f}: expensive relative to growth")

    de = g("debt_to_equity")
    if de is not None and de > 2:
        signals.append(f"Debt/Equity {de:.1f}: highly levered")

    cr = g("current_ratio")
    if cr is not None and cr < 1:
        signals.append(f"Current ratio {cr:.2f}: current liabilities exceed current assets")

    roe = g("return_on_equity")
    if roe is not None and roe < 0:
        signals.append("Negative ROE")

    rev_ttm_yoy = g("total_revenue_yoy_growth_ttm")
    rev_fq_yoy = g("total_revenue_yoy_growth_fq")
    if rev_ttm_yoy is not None and rev_fq_yoy is not None:
        if rev_fq_yoy < rev_ttm_yoy - 5:
            signals.append(
                f"Revenue growth decelerating: {rev_fq_yoy:.1f}% latest quarter vs "
                f"{rev_ttm_yoy:.1f}% TTM"
            )
        elif rev_fq_yoy > rev_ttm_yoy + 5:
            signals.append(
                f"Revenue growth accelerating: {rev_fq_yoy:.1f}% latest quarter vs "
                f"{rev_ttm_yoy:.1f}% TTM"
            )

    if dte is not None and 0 <= dte <= 7:
        signals.append(f"Reports earnings in {dte} day{'s' if dte != 1 else ''}")

    div_yield = g("dividends_yield")
    payout = g("dividend_payout_ratio_ttm")
    if div_yield is not None and div_yield > 0 and payout is not None and payout > 100:
        signals.append(f"Payout ratio {payout:.0f}%: dividend exceeds earnings")

    summary_bits = []
    if pe is not None:
        summary_bits.append(f"P/E {pe:.1f}")
    if rev_ttm_yoy is not None:
        summary_bits.append(f"Rev growth {rev_ttm_yoy:.1f}% YoY")
    net_margin = g("net_margin")
    if net_margin is not None:
        summary_bits.append(f"Net margin {net_margin:.1f}%")
    if dte is not None:
        summary_bits.append(f"earnings in {dte}d")
    summary = (
        f"{row.get('name', ticker.upper())}: " + (", ".join(summary_bits) if summary_bits else "no fundamentals data reported")
    )

    return {
        "ticker": row.get("name", ticker.upper()),
        "description": row.get("description"),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "employees": row.get("number_of_employees"),
        "market": market,
        "valuation": valuation,
        "profitability": profitability,
        "growth": growth,
        "leverage": leverage,
        "dividends": dividends,
        "earnings": earnings,
        "signals": signals,
        "summary": summary,
    }


@_tool
def chart(ticker: str, market: str = "america", interval: str = "1d", theme: str = "dark") -> dict:
    """Get a live TradingView chart for a symbol: deep link + embeddable widget.

    Resolves the symbol to its exchange-prefixed ticker, then returns a chart
    URL plus a ready-to-paste TradingView Advanced Chart widget (config + HTML).
    Pure TradingView. interval accepts "1m".."1mo" friendly forms; theme is
    "dark" or "light".

    Returns {symbol, interval, chart_url, widget:{script,config}, embed_html}.
    """
    # chart only needs a symbol string for the URL, so a failed lookup is not fatal
    # here. It falls back to the raw ticker and the widget still renders.
    row, _err = _resolve_row(ticker, market, ["name", "description"])
    full = (row or {}).get("ticker") or ticker.upper()
    code = normalize_interval(interval)
    chart_url = f"https://www.tradingview.com/chart/?symbol={full}&interval={code}"
    config = {
        "symbol": full,
        "interval": code,
        "theme": "dark" if theme != "light" else "light",
        "style": "1",
        "autosize": True,
        "studies": ["RSI@tv-basicstudies", "MASimple@tv-basicstudies"],
        "allow_symbol_change": True,
    }
    script = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
    import json as _json
    embed_html = (
        '<div class="tradingview-widget-container">'
        '<div class="tradingview-widget-container__widget"></div>'
        f'<script type="text/javascript" src="{script}" async>'
        f"{_json.dumps(config)}"
        "</script></div>"
    )
    return {
        "symbol": full,
        "description": (row or {}).get("description"),
        "interval": code,
        "chart_url": chart_url,
        "widget": {"type": "advanced-chart", "script": script, "config": config},
        "embed_html": embed_html,
    }


@_tool
def sector_breakdown(
    market: str = "america",
    filters: list[dict] | None = None,
    limit: Limit = 500,
    include_otc: bool = False,
) -> dict:
    """Aggregate a screen by sector: count, average change, and total market cap.

    A bird's-eye read of where the money and the moves are right now. Aggregates
    over the returned set (apply `filters` to scope it, e.g. a market-cap floor).
    Returns sectors sorted by total market cap.

    On america, OTC is excluded by default as a data quality filter, since a
    third of the universe is sub-penny shells whose changes are a fraction of a
    cent expressed as a percentage. No market cap or price floor is applied.
    Pass include_otc=True to scan the full venue. The response carries an
    `otc_guard` block so the exclusion is never silent.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=["name", "sector", "change", "market_cap_basic"],
        limit=limit,
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}
    buckets: dict[str, dict] = {}
    for r in resp["rows"]:
        sec = r.get("sector") or "Unknown"
        b = buckets.setdefault(sec, {"count": 0, "changes": [], "mcap": 0.0})
        b["count"] += 1
        if isinstance(r.get("change"), (int, float)):
            b["changes"].append(r["change"])
        if isinstance(r.get("market_cap_basic"), (int, float)):
            b["mcap"] += r["market_cap_basic"]
    sectors = []
    for sec, b in buckets.items():
        changes = b["changes"]
        sectors.append({
            "sector": sec,
            "count": b["count"],
            "avg_change": round(sum(changes) / len(changes), 3) if changes else None,
            "total_market_cap": round(b["mcap"], 0),
        })
    sectors.sort(key=lambda s: s["total_market_cap"], reverse=True)
    out = {"sampled": len(resp["rows"]), "universe": resp["count"], "sectors": sectors}
    if guard:
        out["otc_guard"] = guard
    return out


def _compute_breadth(rows: list[dict]) -> dict:
    """Aggregate a list of screened rows into market breadth indicators.

    Pure function over row data. Returns counts, ratios, moving-average
    positioning, and RSI distribution so callers can test it offline.
    """
    n = len(rows)
    if not n:
        return {"sample": 0}

    adv = sum(1 for r in rows if isinstance(r.get("change"), (int, float)) and r["change"] > 0)
    dec = sum(1 for r in rows if isinstance(r.get("change"), (int, float)) and r["change"] < 0)
    unc = sum(1 for r in rows if isinstance(r.get("change"), (int, float)) and r["change"] == 0)
    ad_ratio = round(adv / dec, 3) if dec else None

    changes = [r["change"] for r in rows if isinstance(r.get("change"), (int, float))]
    avg_change = round(sum(changes) / len(changes), 3) if changes else None

    sma50_rows = [r for r in rows if isinstance(r.get("close"), (int, float)) and isinstance(r.get("SMA50"), (int, float))]
    pct_above_sma50 = (
        round(sum(1 for r in sma50_rows if r["close"] > r["SMA50"]) / len(sma50_rows) * 100, 1)
        if sma50_rows else None
    )

    sma200_rows = [r for r in rows if isinstance(r.get("close"), (int, float)) and isinstance(r.get("SMA200"), (int, float))]
    pct_above_sma200 = (
        round(sum(1 for r in sma200_rows if r["close"] > r["SMA200"]) / len(sma200_rows) * 100, 1)
        if sma200_rows else None
    )

    rsi_vals = [r["RSI"] for r in rows if isinstance(r.get("RSI"), (int, float))]
    rsi_n = len(rsi_vals)
    if rsi_n:
        avg_rsi = round(sum(rsi_vals) / rsi_n, 1)
        pct_overbought = round(sum(1 for v in rsi_vals if v >= 70) / rsi_n * 100, 1)
        pct_oversold = round(sum(1 for v in rsi_vals if v <= 30) / rsi_n * 100, 1)
        pct_rsi_neutral = round(sum(1 for v in rsi_vals if 30 < v < 70) / rsi_n * 100, 1)
    else:
        avg_rsi = pct_overbought = pct_oversold = pct_rsi_neutral = None

    return {
        "sample": n,
        "advancers": adv,
        "decliners": dec,
        "unchanged": unc,
        "ad_ratio": ad_ratio,
        "pct_advancers": round(adv / n * 100, 1),
        "pct_decliners": round(dec / n * 100, 1),
        "avg_change": avg_change,
        "pct_above_sma50": pct_above_sma50,
        "pct_above_sma200": pct_above_sma200,
        "avg_rsi": avg_rsi,
        "pct_rsi_overbought": pct_overbought,
        "pct_rsi_oversold": pct_oversold,
        "pct_rsi_neutral": pct_rsi_neutral,
    }


@_tool
def market_breadth(
    market: str = "america",
    filters: list[dict] | None = None,
    limit: Limit = 500,
    include_otc: bool = False,
) -> dict:
    """Market breadth indicators: advancers/decliners, % above key MAs, RSI distribution.

    A single-call health read of the market (or a filtered slice of it). Useful
    for quickly answering "is the market broadly advancing or declining?" and
    "how stretched is sentiment right now?" before drilling into individual names.

    market:  one of scan_list_markets() ids.
    filters: optional extra filters, e.g. a market-cap floor to focus on a tier.
    limit:   rows to sample (default 500; the full scan is often 7000+ stocks).
    include_otc: scan OTC too (default False). Breadth is a PERCENTAGE over the
                 universe, so this matters more here than in a top-N list: a
                 third of america is sub-penny shells, and letting them vote
                 moves every ratio this tool reports.

    Returns {market, universe, sample, advancers, decliners, unchanged, ad_ratio,
    pct_advancers, pct_decliners, avg_change, pct_above_sma50, pct_above_sma200,
    avg_rsi, pct_rsi_overbought, pct_rsi_oversold, pct_rsi_neutral, otc_guard}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=["name", "close", "change", "RSI", "SMA50", "SMA200"],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}
    breadth = _compute_breadth(resp["rows"])
    breadth["universe"] = resp["count"]
    breadth["market"] = market
    if guard:
        breadth["otc_guard"] = guard
    return breadth


def _sect_avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _sect_norm(sectors: list[dict], key: str) -> dict[str, float]:
    """Normalize a sector metric to [0, 1] across all sectors that have it."""
    pairs = [(s["sector"], s[key]) for s in sectors if s.get(key) is not None]
    if len(pairs) < 2:
        return {}
    nums = [v for _, v in pairs]
    lo, hi = min(nums), max(nums)
    span = hi - lo
    if span == 0:
        return {sec: 0.0 for sec, _ in pairs}
    return {sec: (v - lo) / span for sec, v in pairs}


def _compute_sector_rotation(rows: list[dict]) -> list[dict]:
    """Aggregate rows by sector into multi-timeframe momentum metrics.

    Pure function over row data. Each sector dict carries:
    sector, count, avg_change, avg_perf_1m, avg_perf_ytd, avg_rsi,
    total_market_cap, momentum_score.

    momentum_score is a weighted normalized blend:
      avg_change 50%, avg_perf_1m 30%, avg_perf_ytd 20%.
    Sorted descending by momentum_score.
    """
    buckets: dict[str, dict] = {}
    for r in rows:
        sec = r.get("sector") or "Unknown"
        b = buckets.setdefault(
            sec,
            {"changes": [], "perf_1m": [], "perf_ytd": [], "rsi": [], "mcap": 0.0, "count": 0},
        )
        b["count"] += 1
        for field, slot in (
            ("change", "changes"),
            ("Perf.1M", "perf_1m"),
            ("Perf.YTD", "perf_ytd"),
            ("RSI", "rsi"),
        ):
            v = r.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                b[slot].append(float(v))
        mcap = r.get("market_cap_basic")
        if isinstance(mcap, (int, float)):
            b["mcap"] += float(mcap)

    sectors: list[dict] = []
    for sec, b in buckets.items():
        sectors.append({
            "sector": sec,
            "count": b["count"],
            "avg_change": _sect_avg(b["changes"]),
            "avg_perf_1m": _sect_avg(b["perf_1m"]),
            "avg_perf_ytd": _sect_avg(b["perf_ytd"]),
            "avg_rsi": round(sum(b["rsi"]) / len(b["rsi"]), 1) if b["rsi"] else None,
            "total_market_cap": round(b["mcap"], 0),
            "momentum_score": None,
        })

    weights = [("avg_change", 0.5), ("avg_perf_1m", 0.3), ("avg_perf_ytd", 0.2)]
    norm_maps = {key: _sect_norm(sectors, key) for key, _ in weights}

    for s in sectors:
        total_w, score = 0.0, 0.0
        for key, w in weights:
            nm = norm_maps[key]
            if s["sector"] in nm:
                score += w * nm[s["sector"]]
                total_w += w
        s["momentum_score"] = round(score / total_w, 4) if total_w > 0 else None

    sectors.sort(key=lambda s: (s["momentum_score"] is None, -(s["momentum_score"] or 0.0)))
    return sectors


@_tool
def sector_rotation(
    market: str = "america",
    filters: list[dict] | None = None,
    limit: Limit = 1000,
    include_otc: bool = False,
) -> dict:
    """Rank sectors by multi-timeframe momentum: a sector rotation dashboard.

    Aggregates the screened universe by sector and computes per-sector:
      avg_change:    average 1-day change % (short-term momentum)
      avg_perf_1m:  average 1-month performance (medium momentum)
      avg_perf_ytd: average YTD performance (longer-term trend)
      avg_rsi:      average RSI (sentiment)
      total_market_cap: total market cap of sampled stocks in the sector
      momentum_score: weighted normalized blend (50% change, 30% 1M, 20% YTD)
                       scaled 0-1 so sectors can be compared at a glance.

    Sectors are returned sorted descending by momentum_score, so sector[0] has
    the strongest momentum right now. Use this to decide which sectors to hunt in
    before drilling into individual names with `scan_screen` or `scan_analyze`.

    market:  one of scan_list_markets() ids.
    filters: optional extra filters (e.g. a market-cap floor).
    limit:   stocks to sample (default 1000; spread across all sectors).
    include_otc: scan OTC too (default False). Sector averages are computed over
                 the sample, so shells drag whichever sector they land in.

    Returns {market, sampled, universe, sectors:[...], otc_guard}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=["name", "sector", "change", "Perf.1M", "Perf.YTD", "RSI", "market_cap_basic"],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}
    sectors = _compute_sector_rotation(resp["rows"])
    out = {
        "market": market,
        "sampled": len(resp["rows"]),
        "universe": resp["count"],
        "sectors": sectors,
    }
    if guard:
        out["otc_guard"] = guard
    return out


def _compute_new_highs_lows(rows: list[dict], threshold: float = 0.02) -> dict:
    """Identify stocks at or near their 52-week highs and lows.

    threshold: fractional tolerance for "near" the extreme.
    0.02 = within 2% of the 52-week high/low counts as a new high/low.

    Returns counts, ratio, difference, and the actual stock lists.
    """
    n = len(rows)
    if not n:
        return {"sample": 0, "new_highs": [], "new_lows": []}

    new_highs: list[dict] = []
    new_lows: list[dict] = []

    for row in rows:
        close = row.get("close")
        high_52w = row.get("price_52_week_high")
        low_52w = row.get("price_52_week_low")

        if close is not None and high_52w is not None and high_52w > 0:
            pct_from_high = (close - high_52w) / high_52w
            if pct_from_high >= -threshold:
                new_highs.append({
                    "name": row.get("name"),
                    "close": close,
                    "high_52w": high_52w,
                    "pct_from_high": round(pct_from_high * 100, 2),
                    "change": row.get("change"),
                    "sector": row.get("sector"),
                })

        if close is not None and low_52w is not None and low_52w > 0:
            pct_from_low = (close - low_52w) / low_52w
            if pct_from_low <= threshold:
                new_lows.append({
                    "name": row.get("name"),
                    "close": close,
                    "low_52w": low_52w,
                    "pct_from_low": round(pct_from_low * 100, 2),
                    "change": row.get("change"),
                    "sector": row.get("sector"),
                })

    nh = len(new_highs)
    nl = len(new_lows)
    new_highs.sort(key=lambda x: x.get("pct_from_high") or 0, reverse=True)
    new_lows.sort(key=lambda x: x.get("pct_from_low") or 0)

    return {
        "sample": n,
        "new_highs_count": nh,
        "new_lows_count": nl,
        "nh_nl_ratio": round(nh / nl, 3) if nl else None,
        "nh_nl_diff": nh - nl,
        "pct_new_highs": round(nh / n * 100, 1),
        "pct_new_lows": round(nl / n * 100, 1),
        "new_highs": new_highs,
        "new_lows": new_lows,
    }


@_tool
def new_highs_lows(
    market: str = "america",
    filters: list[dict] | None = None,
    threshold: float = 0.02,
    limit: Limit = 500,
    include_otc: bool = False,
) -> dict:
    """Stocks at or near their 52-week highs and lows: a classic breadth gauge.

    The NH/NL ratio (new highs / new lows) and the NH-NL difference are leading
    indicators of internal market health. A rising new-high count and a positive
    NH-NL difference signal broad participation in an advance. A rising new-low
    count signals deterioration under the surface even when indices hold up.

    market:    one of scan_list_markets() ids.
    filters:   optional filters to scope the universe (e.g. a market-cap floor).
    threshold: fractional distance from the 52w extreme to count as "at" the
               level. Default 0.02 = within 2%. Use 0.05 for "near highs/lows".
    limit:     rows to sample (default 500).
    include_otc: scan OTC too (default False). This is a breadth gauge reported
               as counts and percentages, so shells vote on the reading.

    Returns {market, universe, sample, new_highs_count, new_lows_count,
    nh_nl_ratio, nh_nl_diff, pct_new_highs, pct_new_lows,
    new_highs:[{name,close,high_52w,pct_from_high,change,sector}],
    new_lows:[{name,close,low_52w,pct_from_low,change,sector}], otc_guard}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=["name", "close", "change", "price_52_week_high", "price_52_week_low", "sector"],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}
    result = _compute_new_highs_lows(resp["rows"], threshold=threshold)
    result["universe"] = resp["count"]
    result["market"] = market
    if guard:
        result["otc_guard"] = guard
    return result


def _compute_volume_leaders(rows: list[dict], min_rvol: float = 1.5) -> dict:
    """Identify stocks with unusual volume and classify the activity direction.

    Pure function over row data. Each row must carry relative_volume_10d_calc
    and optionally change, close, volume, sector. Rows without a numeric rvol,
    or with rvol below min_rvol, are excluded.

    Returns sample, count (rows that qualified), by_direction breakdown
    (up/down/flat counts and pct), by_sector aggregation sorted by count,
    and the qualified leaders sorted descending by rvol.
    """
    n = len(rows)
    if not n:
        return {"sample": 0, "count": 0, "leaders": [], "by_direction": {}, "by_sector": []}

    leaders: list[dict] = []
    for row in rows:
        rvol = row.get("relative_volume_10d_calc")
        if not isinstance(rvol, (int, float)) or isinstance(rvol, bool):
            continue
        if rvol < min_rvol:
            continue
        change = row.get("change")
        if isinstance(change, (int, float)) and not isinstance(change, bool):
            direction = "up" if change > 0 else ("down" if change < 0 else "flat")
        else:
            direction = "flat"
        leaders.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": change,
            "rvol": round(float(rvol), 2),
            "volume": row.get("volume"),
            "sector": row.get("sector"),
            "direction": direction,
        })

    leaders.sort(key=lambda x: -(x["rvol"] or 0))

    total = len(leaders)
    up = sum(1 for r in leaders if r["direction"] == "up")
    down = sum(1 for r in leaders if r["direction"] == "down")
    flat = total - up - down

    by_direction = {
        "up": up,
        "down": down,
        "flat": flat,
        "pct_up": round(up / total * 100, 1) if total else None,
        "pct_down": round(down / total * 100, 1) if total else None,
    }

    sector_buckets: dict[str, dict] = {}
    for r in leaders:
        sec = r.get("sector") or "Unknown"
        b = sector_buckets.setdefault(sec, {"count": 0, "rvols": [], "up": 0, "down": 0})
        b["count"] += 1
        b["rvols"].append(r["rvol"])
        if r["direction"] == "up":
            b["up"] += 1
        elif r["direction"] == "down":
            b["down"] += 1

    by_sector = sorted([
        {
            "sector": sec,
            "count": b["count"],
            "avg_rvol": round(sum(b["rvols"]) / len(b["rvols"]), 2) if b["rvols"] else None,
            "up": b["up"],
            "down": b["down"],
        }
        for sec, b in sector_buckets.items()
    ], key=lambda s: -s["count"])

    return {
        "sample": n,
        "count": total,
        "min_rvol": min_rvol,
        "by_direction": by_direction,
        "by_sector": by_sector,
        "leaders": leaders,
    }


@_tool
def volume_leaders(
    market: str = "america",
    min_rvol: float = 1.5,
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Stocks with unusual volume right now, classified by price direction.

    Finds every stock trading at or above min_rvol times its 10-day average
    volume, then classifies each as up/down/flat. The direction breakdown reveals
    whether the surge is buying pressure, selling pressure, or mixed. A sector
    breakdown shows which sectors have concentrated unusual activity. The leaders
    list is sorted by relative volume descending so the most unusual names lead.

    market:   one of scan_list_markets() ids (america, crypto, forex, ...).
    min_rvol: minimum relative-volume multiple (default 1.5 = 50% above normal).
              Use 2.0 for clear surges, 3.0 for extreme spikes.
    filters:  optional extra filters (e.g. a market-cap floor).
    limit:    rows to sample (default 500). Raise to 1000 for broader coverage.
    top:      max leaders to return (default 50).
    include_otc: scan OTC too (default False).

    Returns {market, universe, sample, count, min_rvol, by_direction,
    by_sector:[{sector,count,avg_rvol,up,down}],
    leaders:[{name,close,change,rvol,volume,sector,direction}], otc_guard}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=["name", "close", "change", "volume", "relative_volume_10d_calc", "sector"],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}
    result = _compute_volume_leaders(resp["rows"], min_rvol=min_rvol)
    result["leaders"] = result["leaders"][:top]
    result["universe"] = resp["count"]
    result["market"] = market
    if guard:
        result["otc_guard"] = guard
    return result


@_tool
def top_movers(
    market: str = "america",
    n: int = 10,
    filters: list[dict] | None = None,
    columns: list[str] | None = None,
    include_otc: bool = False,
) -> dict:
    """Get the top N gainers and top N losers in any market right now.

    The fastest entry point for momentum, gap-fill, or reversal ideas. Two
    separate ranked lists in one call: stocks sorted by change% descending
    (gainers) and ascending (losers), both filtered the same way.

    On america, OTC is excluded by default. This tool ranks purely on change%,
    which is the metric a sub-penny shell wins by construction: a $0.0001 tick
    is a four figure percentage move. Measured unguarded, both lists came back
    10 of 10 OTC, so the tool was not reporting the day's movers, it was
    reporting rounding. No market cap or price floor is applied, so listed small
    caps still surface. Pass include_otc=True to scan the full venue.

    market:  one of scan_list_markets() ids (america, crypto, forex, ...).
    n:       movers to show on each side, 1-50 (default 10).
    filters: optional extra filters applied to both lists, e.g. a market_cap
             floor: [{"field":"market_cap_basic","op":">","value":1e9}].
    columns: fields to include. Defaults to name, description, close, change,
             volume, market_cap_basic, sector.
    include_otc: scan OTC too (default False).

    Returns {market, universe, gainers:[rows], losers:[rows],
    gainers_table, losers_table, otc_guard}.
    """
    default_cols = ["name", "description", "close", "change", "volume", "market_cap_basic", "sector"]
    cols = [c for c in (columns or default_cols) if validate_field(c)]
    # Ensure `change` is always present so the sort and table are meaningful.
    if "change" not in cols:
        cols.insert(1, "change")
    n_capped = max(1, min(n, 50))
    guarded, guard = _otc_guard(market, filters, include_otc)
    base_filters = [Filter(**f) for f in guarded]

    gainers_req = ScreenRequest(
        market=market,
        filters=base_filters,
        columns=cols,
        sort=[SortKey(field="change", dir="desc")],
        limit=n_capped,
    )
    losers_req = ScreenRequest(
        market=market,
        filters=base_filters,
        columns=cols,
        sort=[SortKey(field="change", dir="asc")],
        limit=n_capped,
    )

    gainers_out = _run(gainers_req, table_rows=n_capped)
    if gainers_out.get("error"):
        return gainers_out
    losers_out = _run(losers_req, table_rows=n_capped)
    if losers_out.get("error"):
        return losers_out

    out = {
        "market": market,
        "universe": gainers_out.get("count", 0),
        "gainers": gainers_out.get("rows", []),
        "losers": losers_out.get("rows", []),
        "gainers_table": gainers_out.get("table", ""),
        "losers_table": losers_out.get("table", ""),
    }
    if guard:
        out["otc_guard"] = guard
    return out


# Five timeframes checked for momentum consistency.
# Weights sum to 1.0; the 1-day reading is down-weighted as daily noise.
_MC_TIMEFRAMES = [
    ("1d",  "change",   0.15),
    ("1W",  "Perf.W",   0.20),
    ("1M",  "Perf.1M",  0.25),
    ("3M",  "Perf.3M",  0.20),
    ("YTD", "Perf.YTD", 0.20),
]


def _compute_momentum_consistency(rows: list[dict], direction: str = "bull") -> list[dict]:
    """Score each row by how consistently its returns align with `direction`.

    direction: "bull" scores positive timeframes; "bear" scores negative ones.

    Each of the five timeframes (1d, 1W, 1M, 3M, YTD) contributes its weight
    (15/20/25/20/20%) when the return aligns with the direction. Timeframes
    without data are skipped and the remaining weights are re-normalized, so
    the score is always in [0, 1] regardless of how many fields are present.
    A score of 1.0 means every available timeframe aligns; 0.0 means none do.

    Rows with no timeframe data at all get score None and sort to the end.
    Returns the list sorted descending by consistency_score.
    """
    results: list[dict] = []
    for row in rows:
        pos_tfs: list[str] = []
        neg_tfs: list[str] = []
        total_w = 0.0
        aligned_w = 0.0
        for label, field, weight in _MC_TIMEFRAMES:
            v = row.get(field)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            total_w += weight
            if v > 0:
                pos_tfs.append(label)
                if direction == "bull":
                    aligned_w += weight
            elif v < 0:
                neg_tfs.append(label)
                if direction == "bear":
                    aligned_w += weight

        score = round(aligned_w / total_w, 4) if total_w > 0 else None
        aligned_count = len(pos_tfs) if direction == "bull" else len(neg_tfs)
        results.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "sector": row.get("sector"),
            "market_cap_basic": row.get("market_cap_basic"),
            "consistency_score": score,
            "timeframes_aligned": aligned_count,
            "timeframes_total": len(pos_tfs) + len(neg_tfs),
            "positive_tf": pos_tfs,
            "negative_tf": neg_tfs,
            "perf_1w": row.get("Perf.W"),
            "perf_1m": row.get("Perf.1M"),
            "perf_3m": row.get("Perf.3M"),
            "perf_ytd": row.get("Perf.YTD"),
        })

    results.sort(key=lambda x: (x["consistency_score"] is None, -(x["consistency_score"] or 0.0)))
    return results


@_tool
def momentum_consistency(
    market: str = "america",
    direction: str = "bull",
    min_aligned: int = 0,
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Rank stocks by how consistently their returns align across five time frames.

    A stock up on the day might be noise. A stock up on the day, the week, the
    month, the quarter, AND year-to-date is a genuine momentum leader. This tool
    computes a consistency_score in [0, 1] for every stock: 1.0 means all five
    timeframes (1d, 1W, 1M, 3M, YTD) align; 0.0 means none do.

    The timeframes are weighted (1d 15%, 1W 20%, 1M 25%, 3M 20%, YTD 20%) so
    longer-duration alignment contributes more to the score. Timeframes without
    data are skipped and weights are re-normalized, so the score stays in [0,1].

    Use direction="bear" to find consistent downtrends (laggards, short ideas).
    Use min_aligned to filter for a minimum number of timeframes aligning.

    market:     one of scan_list_markets() ids.
    direction:  "bull" (positive returns dominate) or "bear" (negative).
    min_aligned: minimum number of timeframes that must align (0 = no filter).
    filters:    optional extra filters (e.g. a market-cap floor).
    limit:      rows to sample (default 500; more = better coverage).
    top:        max stocks to return (default 50).

    Returns {market, universe, sample, direction, top:[{name, close, change,
    sector, market_cap_basic, consistency_score, timeframes_aligned,
    timeframes_total, positive_tf, negative_tf, perf_1w, perf_1m,
    perf_3m, perf_ytd}]}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=[
            "name", "close", "change", "sector", "market_cap_basic",
            "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD",
        ],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}

    scored = _compute_momentum_consistency(resp["rows"], direction=direction)

    if min_aligned > 0:
        scored = [s for s in scored if s["timeframes_aligned"] >= min_aligned]

    top_rows = scored[:max(1, top)]
    out = {
        "market": market,
        "universe": resp["count"],
        "sample": len(resp["rows"]),
        "direction": direction,
        "top": top_rows,
    }
    if guard:
        out["otc_guard"] = guard
    return out


# Timeframe config for the relative-strength calculation.
# label -> TradingView field name
_RS_FIELDS = [
    ("1d",  "change",   0.5),
    ("1M",  "Perf.1M",  0.3),
    ("YTD", "Perf.YTD", 0.2),
]


def _compute_relative_strength(rows: list[dict]) -> list[dict]:
    """Score each row by its excess return vs its sector average.

    For three timeframes (1d, 1M, YTD), compute the per-sector mean return,
    then each row's excess = row_value - sector_mean. The composite raw score
    is a weighted sum of excess returns (50% 1d, 30% 1M, 20% YTD) re-normalized
    over whichever timeframes have data for that row. The final rs_score is the
    percentile rank (0-100) of the raw composite across all rows in the pull.

    A score of 99 means the stock is in the top 1% of its sector-relative excess
    return in the screened universe. A score of 1 means it is in the bottom 1%.
    Rows with no numeric data at all get rs_score None and sort to the end.
    Returns the list sorted descending by rs_score.
    """
    if not rows:
        return []

    # Build per-sector value buckets for each timeframe.
    sec_buckets: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        sec = row.get("sector") or "Unknown"
        if sec not in sec_buckets:
            sec_buckets[sec] = {label: [] for label, _, _ in _RS_FIELDS}
        for label, field, _ in _RS_FIELDS:
            v = row.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                sec_buckets[sec][label].append(float(v))

    # Compute sector averages.
    sec_avgs: dict[str, dict[str, float | None]] = {}
    for sec, buckets in sec_buckets.items():
        sec_avgs[sec] = {}
        for label, _, _ in _RS_FIELDS:
            vals = buckets[label]
            sec_avgs[sec][label] = sum(vals) / len(vals) if vals else None

    # Compute per-row excess returns and raw weighted score.
    raw_scores: list[float | None] = []
    results: list[dict] = []
    for row in rows:
        sec = row.get("sector") or "Unknown"
        avgs = sec_avgs.get(sec, {})

        excess: dict[str, float | None] = {}
        weighted_sum = 0.0
        total_w = 0.0

        for label, field, weight in _RS_FIELDS:
            v = row.get(field)
            sec_avg = avgs.get(label)
            if (
                isinstance(v, (int, float)) and not isinstance(v, bool)
                and sec_avg is not None
            ):
                exc = float(v) - sec_avg
                excess[label] = round(exc, 4)
                weighted_sum += weight * exc
                total_w += weight
            else:
                excess[label] = None

        raw = (weighted_sum / total_w) if total_w > 0 else None
        raw_scores.append(raw)

        results.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "sector": sec,
            "market_cap_basic": row.get("market_cap_basic"),
            "rs_score": None,
            "excess_1d": excess.get("1d"),
            "excess_1m": excess.get("1M"),
            "excess_ytd": excess.get("YTD"),
            "sector_avg_change": avgs.get("1d"),
            "perf_1m": row.get("Perf.1M"),
            "perf_ytd": row.get("Perf.YTD"),
        })

    # Compute percentile rank of the raw composite (0-100).
    present = [v for v in raw_scores if v is not None]
    n_present = len(present)

    for res, raw in zip(results, raw_scores):
        if raw is None or n_present == 0:
            res["rs_score"] = None
        else:
            below = sum(1 for x in present if x < raw)
            equal = sum(1 for x in present if x == raw)
            res["rs_score"] = round((below + 0.5 * equal) / n_present * 100.0, 1)

    results.sort(key=lambda x: (x["rs_score"] is None, -(x["rs_score"] or 0.0)))
    return results


@_tool
def relative_strength_leaders(
    market: str = "america",
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Find stocks outperforming their sector peers: sector-relative strength leaders.

    Raw change% ranks every stock together across the entire market. This tool
    re-ranks by sector-relative excess return: how much is each stock beating
    (or lagging) its own sector's average? A stock up 3% in a sector down 2% is
    outperforming by 5 points and surfaces near the top. A stock up 1% in a sector
    up 4% is actually lagging and scores lower.

    The composite rs_score (0-100, percentile rank) weights excess returns across
    three timeframes: 50% 1-day, 30% 1-month, 20% YTD. This finds stocks with
    sustained leadership within their sector, not just today's noise.

    Use this to:
    - Find sector leaders before they show up on the raw gainers list.
    - Identify stocks holding up best during sector-wide drawdowns.
    - Cross-reference with `scan_sector_rotation` to double down on the hot sector's
      strongest names.

    market:  one of scan_list_markets() ids.
    filters: optional extra filters (e.g. a market-cap floor).
    limit:   rows to sample (default 500; more coverage = more reliable sector avgs).
    top:     max stocks to return (default 50).
    include_otc: scan OTC too (default False).

    Returns {market, universe, sample, top:[{name, close, change, sector,
    market_cap_basic, rs_score, excess_1d, excess_1m, excess_ytd,
    sector_avg_change, perf_1m, perf_ytd}]}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=[
            "name", "close", "change", "sector", "market_cap_basic",
            "Perf.1M", "Perf.YTD",
        ],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}

    scored = _compute_relative_strength(resp["rows"])
    top_rows = scored[:max(1, top)]
    out = {
        "market": market,
        "universe": resp["count"],
        "sample": len(resp["rows"]),
        "top": top_rows,
    }
    if guard:
        out["otc_guard"] = guard
    return out


# Four MA-alignment conditions that define the EMA stack.
# Each entry is (condition_name, fast_field, slow_field).
_STACK_CHECKS = [
    ("price_vs_ema8",   "close", "EMA8"),
    ("ema8_vs_ema21",   "EMA8",  "EMA21"),
    ("ema21_vs_sma50",  "EMA21", "SMA50"),
    ("sma50_vs_sma200", "SMA50", "SMA200"),
]


def _compute_ema_stack(rows: list[dict]) -> list[dict]:
    """Score each row by how many EMA/SMA bull-alignment conditions hold.

    Four conditions are checked in order from fastest to slowest MA:
      1. close  > EMA8   (price above fast EMA: near-term strength)
      2. EMA8   > EMA21  (fast EMAs in bull order: medium momentum)
      3. EMA21  > SMA50  (EMA above medium SMA: trend confirmed)
      4. SMA50  > SMA200 (golden-cross stack: long-term trend aligned)

    stack_score is the count of conditions that evaluate True (0-4).
    Conditions where one or both MA fields are missing are skipped (not
    counted as False). stack_score is None only when no conditions could
    be evaluated at all.

    ma_alignment labels:
      "full_bull"   score == 4 (all four checked and True)
      "full_bear"   score == 0 and all four were evaluated (all False)
      "partial_N"   N conditions True, fewer than four True or four False
      "unknown"     no MA data available for any condition

    Returns the list sorted descending by stack_score (None values last).
    """
    results = []
    for row in rows:
        true_count = 0
        eval_count = 0
        checks: dict[str, bool | None] = {}
        for cname, fast_f, slow_f in _STACK_CHECKS:
            fast = row.get(fast_f)
            slow = row.get(slow_f)
            if (
                isinstance(fast, (int, float)) and not isinstance(fast, bool)
                and isinstance(slow, (int, float)) and not isinstance(slow, bool)
            ):
                bull = float(fast) > float(slow)
                checks[cname] = bull
                eval_count += 1
                if bull:
                    true_count += 1
            else:
                checks[cname] = None

        if eval_count == 0:
            stack_score = None
            ma_alignment = "unknown"
        elif true_count == 4:
            stack_score = 4
            ma_alignment = "full_bull"
        elif true_count == 0 and eval_count == 4:
            stack_score = 0
            ma_alignment = "full_bear"
        else:
            stack_score = true_count
            ma_alignment = f"partial_{true_count}"

        results.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "sector": row.get("sector"),
            "market_cap_basic": row.get("market_cap_basic"),
            "rsi": row.get("RSI"),
            "stack_score": stack_score,
            "ma_alignment": ma_alignment,
            "conditions_available": eval_count,
            **checks,
        })

    results.sort(key=lambda x: (x["stack_score"] is None, -(x["stack_score"] or 0)))
    return results


@_tool
def ema_stack_scan(
    market: str = "america",
    min_stack: int = 0,
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Rank stocks by EMA/SMA stack alignment: a bull-stack breadth indicator.

    Checks four moving-average alignment conditions for each stock:
      1. Price  above EMA8   (near-term momentum)
      2. EMA8   above EMA21  (fast EMAs in bull order)
      3. EMA21  above SMA50  (medium-term trend aligned)
      4. SMA50  above SMA200 (golden-cross long-term stack)

    stack_score is the count of conditions that hold (0-4). A score of 4
    is a full bull stack: price is above every MA and each MA is above the
    next slower one. A score of 0 is a full bear stack.

    The distribution doubles as a market breadth read: when most stocks are
    at 3-4 the broad trend is healthy; a slide to mostly 0-1 signals broad
    internal deterioration. pct_full_bull is a single summary number.

    Use min_stack=4 to screen for stocks in the cleanest uptrend; combine
    with a `filters` market-cap floor to keep the list investable.

    market:    one of scan_list_markets() ids.
    min_stack: return only stocks at or above this score (0 = no filter;
               3 = bull-bias; 4 = full bull stack only).
    filters:   optional extra filters (e.g. a market-cap floor).
    limit:     rows to sample (default 500).
    top:       max stocks to return (default 50).
    include_otc: scan OTC too (default False).

    Returns {market, universe, sample,
    distribution:{0:n, 1:n, 2:n, 3:n, 4:n, none:n},
    avg_stack_score, pct_full_bull, pct_full_bear,
    top:[{name, close, change, sector, market_cap_basic, rsi,
    stack_score, ma_alignment, conditions_available,
    price_vs_ema8, ema8_vs_ema21, ema21_vs_sma50, sma50_vs_sma200}]}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    req = ScreenRequest(
        market=market,
        filters=[Filter(**f) for f in guarded],
        columns=[
            "name", "close", "change", "sector", "market_cap_basic",
            "RSI", "EMA8", "EMA21", "SMA50", "SMA200",
        ],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}

    scored = _compute_ema_stack(resp["rows"])

    # Build distribution and summary stats.
    dist: dict[str | int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, "none": 0}
    score_sum = 0.0
    n_scored = 0
    for s in scored:
        sc = s["stack_score"]
        if sc is None:
            dist["none"] += 1
        else:
            dist[sc] += 1
            score_sum += sc
            n_scored += 1

    pct_full_bull = round(dist[4] / n_scored * 100, 1) if n_scored else None
    pct_full_bear = round(dist[0] / n_scored * 100, 1) if n_scored else None
    avg_stack = round(score_sum / n_scored, 2) if n_scored else None

    if min_stack > 0:
        filtered = [s for s in scored if s["stack_score"] is not None and s["stack_score"] >= min_stack]
    else:
        filtered = scored

    out = {
        "market": market,
        "universe": resp["count"],
        "sample": len(resp["rows"]),
        "distribution": dist,
        "avg_stack_score": avg_stack,
        "pct_full_bull": pct_full_bull,
        "pct_full_bear": pct_full_bear,
        "top": filtered[:max(1, top)],
    }
    if guard:
        out["otc_guard"] = guard
    return out


def _market_tz() -> dt.tzinfo | None:
    """US market timezone, or None to fall back to the host's local time.

    earnings_release_next_date is a real UTC instant (NVDA reads 20:00 UTC, the
    4pm ET after-close slot), so turning it into a CALENDAR DAY needs a fixed
    reference. Using the host's timezone made "reports today" depend on where the
    server happened to run: the same row buckets one day later from Tokyo than
    from New York. The market's own clock is the honest anchor for a US equities
    scan, so both the query window and the day count use it.

    Returns None when the tz database is unavailable (a bare Windows install
    without `tzdata`), which restores the previous local-time behaviour rather
    than failing the scan.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/New_York")
    except Exception:
        return None


def _market_today() -> dt.date:
    """Today's date on the market's clock (see _market_tz)."""
    return dt.datetime.now(_market_tz()).date()


def _days_to_earnings(ts: Any, today: dt.date | None = None) -> int | None:
    """Whole days from today to an earnings timestamp, or None.

    TradingView exposes a `days_to_earnings` field and it validates against the
    catalog, but it is NULL for every US row, so filtering on it returned nothing
    while reporting success. `earnings_release_next_date` is populated (150 of 150
    sampled) and is a unix timestamp, so the number is derived from that instead.

    Compared as DATES rather than elapsed seconds, so an announcement later today
    is 0 and not a fraction that floors to the wrong bucket. Both sides of that
    subtraction resolve in MARKET time, not the host's. See _market_tz.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    try:
        when = dt.datetime.fromtimestamp(float(ts), _market_tz()).date()
    except (OverflowError, OSError, ValueError):
        return None
    return (when - (today or _market_today())).days


def _compute_earnings_radar(rows: list[dict], max_days: int = 7) -> dict:
    """Identify stocks with upcoming earnings and group them by timing bucket.

    Keeps rows whose derived days-to-earnings is in [0, max_days]. Returns a
    sector breakdown and the individual stock list sorted by days_to_earnings
    ascending (earnings today first).

    Timing buckets:
      "today"     days_to_earnings == 0
      "this_week" 1 <= days_to_earnings <= 5
      "later"     6 <= days_to_earnings <= max_days

    Each stock entry carries: name, close, change, sector, market_cap_basic,
    days_to_earnings, bucket, rsi, atrp (ATR % as proxy for expected range),
    perf_1m.
    Rows with no usable earnings date are silently skipped.
    """
    stocks: list[dict] = []
    for row in rows:
        # Prefer a literal days_to_earnings when a row actually carries one, and
        # derive from the timestamp otherwise. TradingView leaves the literal null
        # on every US row today, but accepting both keeps this function working on
        # either shape rather than tying it to one provider quirk.
        dte = row.get("days_to_earnings")
        if not isinstance(dte, (int, float)) or isinstance(dte, bool):
            dte = _days_to_earnings(row.get("earnings_release_next_date"))
        if dte is None:
            continue
        dte_int = int(dte)
        if not (0 <= dte_int <= max_days):
            continue

        if dte_int == 0:
            bucket = "today"
        elif dte_int <= 5:
            bucket = "this_week"
        else:
            bucket = "later"

        stocks.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "sector": row.get("sector") or "Unknown",
            "market_cap_basic": row.get("market_cap_basic"),
            "days_to_earnings": dte_int,
            "bucket": bucket,
            "rsi": row.get("RSI"),
            "atrp": row.get("ATRP"),
            "perf_1m": row.get("Perf.1M"),
        })

    stocks.sort(key=lambda x: (x["days_to_earnings"], x.get("name") or ""))

    by_bucket: dict[str, int] = {"today": 0, "this_week": 0, "later": 0}
    sec_buckets: dict[str, dict] = {}
    for s in stocks:
        by_bucket[s["bucket"]] += 1
        sec = s["sector"]
        b = sec_buckets.setdefault(
            sec, {"count": 0, "today": 0, "this_week": 0, "later": 0}
        )
        b["count"] += 1
        b[s["bucket"]] += 1

    by_sector = sorted(
        [{"sector": sec, **b} for sec, b in sec_buckets.items()],
        key=lambda s: -s["count"],
    )

    return {
        "count": len(stocks),
        "by_bucket": by_bucket,
        "by_sector": by_sector,
        "stocks": stocks,
    }


@_tool
def earnings_radar(
    market: str = "america",
    horizon: int = 7,
    filters: list[dict] | None = None,
    limit: Limit = 1000,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Find stocks with earnings announcements in the next N days.

    Earnings are the biggest single-day catalyst. This tool builds a forward
    radar of names about to report, grouped by timing bucket (today, this_week,
    later) and sector, with the context a trader needs before the bell:
    RSI, ATR% (a proxy for the expected intraday range), 1-month performance,
    and market cap.

    Use it the morning before the market opens to build an earnings watch-list.
    Pair with sector_rotation to see which sectors have the most catalysts
    coming, or with analyze to drill into the setups you care about most.

    market:  one of scan_list_markets() ids (america, crypto, ...).
    horizon: days ahead to look for earnings (default 7 = one week). Use 1 for
             today-only, 14 for a two-week forward radar.
    filters: optional extra filters (e.g. market_cap_basic > 1e9 to skip micro-
             caps, or RSI < 40 for oversold pre-earnings ideas).
    limit:   rows to pull from the market (default 1000). Raise to 1000 if you
             want broader small-cap coverage.
    top:     max stocks to return in the stocks list (default 50).

    Returns {market, universe, sample, horizon_days, count,
    by_bucket:{today,this_week,later},
    by_sector:[{sector,count,today,this_week,later}],
    stocks:[{name,close,change,sector,market_cap_basic,days_to_earnings,
    bucket,rsi,atrp,perf_1m}]}.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 365:
        return {"error": "horizon must be a whole number of days between 1 and 365.",
                "error_code": "invalid_request"}
    guarded, guard = _otc_guard(market, filters, include_otc)
    base_filters = [Filter(**f) for f in guarded]
    # Window on earnings_release_next_date, not days_to_earnings: the latter
    # validates against the catalog but is null for every US row, so the old
    # filter matched nothing and the tool reported an empty universe with no
    # error. Bounds run from the start of today to the end of the horizon day.
    _tz = _market_tz()
    _today = _market_today()
    _start = int(dt.datetime.combine(_today, dt.time.min, tzinfo=_tz).timestamp())
    _end = int(dt.datetime.combine(_today + dt.timedelta(days=max(1, horizon)),
                                   dt.time.max, tzinfo=_tz).timestamp())
    base_filters.append(
        Filter(field="earnings_release_next_date", op="between", value=[_start, _end])
    )
    req = ScreenRequest(
        market=market,
        filters=base_filters,
        columns=[
            "name", "close", "change", "sector", "market_cap_basic",
            "earnings_release_next_date", "RSI", "ATRP", "Perf.1M",
        ],
        sort=[SortKey(field="earnings_release_next_date", dir="asc")],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}

    result = _compute_earnings_radar(resp["rows"], max_days=horizon)
    result["stocks"] = result["stocks"][:max(1, top)]
    result["universe"] = resp["count"]
    result["market"] = market
    result["horizon_days"] = horizon
    result["sample"] = len(resp["rows"])
    if guard:
        result["otc_guard"] = guard
    return result


def _compute_gap_scanner(rows: list[dict], min_gap_pct: float = 1.0) -> dict:
    """Identify stocks that opened with a significant gap and track fill progress.

    Uses the `gap` field (open vs yesterday's close %) together with `open` and
    `close` to compute how much of the gap has been filled during the session.

    gap_fill_pct formula:
        (open - close) / (open - prev_close) * 100
    where prev_close = open / (1 + gap/100).

    At the open (close == open): fill = 0%.
    When price retraces to yesterday's close (close == prev_close): fill = 100%.
    When price overshoots past yesterday's close: fill > 100%.

    is_holding: fill < 25 (gap largely intact, "gap and go" territory).
    is_filled:  fill >= 100 (price retraced through the opening gap level).

    Rows without a numeric `gap` field or whose |gap| < min_gap_pct are skipped.
    Returns both sides sorted by absolute gap size descending.
    """
    if not rows:
        return {
            "count": 0,
            "gap_up_count": 0,
            "gap_down_count": 0,
            "by_sector": [],
            "gap_up": [],
            "gap_down": [],
        }

    qualified: list[dict] = []
    for row in rows:
        gap = row.get("gap")
        if not isinstance(gap, (int, float)) or isinstance(gap, bool):
            continue
        if abs(gap) < min_gap_pct:
            continue

        open_price = row.get("open")
        close = row.get("close")

        prev_close: float | None = None
        gap_fill_pct: float | None = None
        is_holding = False
        is_filled = False

        if (
            isinstance(open_price, (int, float)) and not isinstance(open_price, bool)
            and isinstance(close, (int, float)) and not isinstance(close, bool)
        ):
            divisor = 1.0 + gap / 100.0
            if abs(divisor) > 0.001:
                prev_close = open_price / divisor
                gap_span = open_price - prev_close
                if abs(gap_span) > 1e-9:
                    gap_fill_pct = round((open_price - close) / gap_span * 100.0, 1)
                else:
                    gap_fill_pct = 0.0
                is_holding = gap_fill_pct < 25.0
                is_filled = gap_fill_pct >= 100.0

        gap_type = "gap_up" if gap > 0 else "gap_down"
        qualified.append({
            "name": row.get("name"),
            "close": close,
            "open": open_price,
            "change": row.get("change"),
            "sector": row.get("sector") or "Unknown",
            "gap": round(float(gap), 2),
            "gap_type": gap_type,
            "prev_close": round(prev_close, 4) if prev_close is not None else None,
            "gap_fill_pct": gap_fill_pct,
            "is_holding": is_holding,
            "is_filled": is_filled,
            "volume": row.get("volume"),
            "rvol": row.get("relative_volume_10d_calc"),
        })

    qualified.sort(key=lambda x: -abs(x["gap"]))

    gap_up = [q for q in qualified if q["gap_type"] == "gap_up"]
    gap_down = [q for q in qualified if q["gap_type"] == "gap_down"]

    sec_buckets: dict[str, dict] = {}
    for r in qualified:
        sec = r["sector"]
        b = sec_buckets.setdefault(sec, {"count": 0, "gap_up": 0, "gap_down": 0})
        b["count"] += 1
        if r["gap_type"] == "gap_up":
            b["gap_up"] += 1
        else:
            b["gap_down"] += 1

    by_sector = sorted(
        [{"sector": sec, **b} for sec, b in sec_buckets.items()],
        key=lambda s: -s["count"],
    )

    return {
        "count": len(qualified),
        "gap_up_count": len(gap_up),
        "gap_down_count": len(gap_down),
        "by_sector": by_sector,
        "gap_up": gap_up,
        "gap_down": gap_down,
    }


@_tool
def gap_scanner(
    market: str = "america",
    min_gap_pct: float = 1.0,
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Find stocks that opened with a significant gap and track intraday fill progress.

    A gap occurs when a stock opens above (gap up) or below (gap down) the
    previous session's close. Gaps are high-conviction setups: when the gap
    holds, it signals momentum continuation ("gap and go"); when it fills, it
    signals mean reversion back to the prior close.

    For each qualifying gap this tool reports:
      gap:          the opening gap % (positive = gap up, negative = gap down)
      prev_close:   yesterday's close, back-calculated from open and gap%
      gap_fill_pct: how much of the opening gap the current price has retraced.
                    0% = price still at the open; 100% = fully filled back to
                    yesterday's close; >100% = price overshot past the gap level.
      is_holding:   True when gap_fill_pct < 25 (gap still largely intact)
      is_filled:    True when gap_fill_pct >= 100 (full gap fill occurred)
      rvol:         relative volume (conviction read on the gap)

    market:      one of scan_list_markets() ids.
    min_gap_pct: minimum absolute gap % to qualify (default 1.0). Use 2.0
                 for larger, cleaner gaps; 0.5 to catch small gaps too.
    filters:     optional extra filters (e.g. a market-cap floor).
    limit:       rows to sample (default 500).
    top:         max stocks to return per side, gap_up and gap_down (default 50).

    Returns {market, universe, sample, min_gap_pct, count,
    gap_up_count, gap_down_count,
    by_sector:[{sector, count, gap_up, gap_down}],
    gap_up:[{name, close, open, change, sector, gap, prev_close, gap_fill_pct,
    is_holding, is_filled, volume, rvol}],
    gap_down:[same shape]}.
    """
    # BOTH tails, one query each. A single query sorted by gap descending takes
    # the largest POSITIVE gaps and nothing else: measured on america, the top
    # 500 window ran from +334900% down to +5.9% with not one negative in it, so
    # gap_down was always empty and gap_down_count always 0. That is a confident
    # "nothing gapped down today" on a session where 210 of 1000 sampled rows had
    # gapped down 1% or more. The limit is split so the total sampled still
    # matches what `limit` advertises.
    guarded, guard = _otc_guard(market, filters, include_otc)
    base = [Filter(**f) for f in guarded]
    cols = [
        "name", "close", "open", "change", "sector",
        "gap", "volume", "relative_volume_10d_calc",
    ]
    half = max(1, min(limit, MAX_LIMIT) // 2)

    rows: list[dict] = []
    seen: set = set()
    universe = 0
    for direction in ("desc", "asc"):
        resp = run_screen(ScreenRequest(
            market=market,
            filters=base,
            columns=cols,
            sort=[SortKey(field="gap", dir=direction)],
            limit=half,
        ))
        if resp["meta"].get("error"):
            _STATS["errors"] += 1
            return {"error": resp["meta"]["error"]}
        universe = resp["count"]
        for row in resp["rows"]:
            key = row.get("ticker") or row.get("name")
            if key in seen:
                # A market with fewer gaps than the limit returns overlapping
                # windows. Counting a row twice would inflate both sides.
                continue
            seen.add(key)
            rows.append(row)

    result = _compute_gap_scanner(rows, min_gap_pct=min_gap_pct)
    result["gap_up"] = result["gap_up"][:max(1, top)]
    result["gap_down"] = result["gap_down"][:max(1, top)]
    result["universe"] = universe
    result["market"] = market
    result["sample"] = len(rows)
    result["min_gap_pct"] = min_gap_pct
    if guard:
        result["otc_guard"] = guard
    return result


def _payout_health(pr: float | None) -> float:
    """Map a payout ratio % to a health score in [0, 1].

    20-75%: 1.0 (ideal: returns value without over-straining the business)
    <20%: ramps from 0.4 at 0% to 1.0 at 20% (conservative, but ok)
    >75%: ramps from 1.0 at 75% to 0.0 at 100% (elevated cut risk)
    Outside [0, 100] or missing: 0.0 / 0.5 respectively (neutral for missing).
    """
    if pr is None or not isinstance(pr, (int, float)) or isinstance(pr, bool):
        return 0.5
    if pr < 0 or pr > 100:
        return 0.0
    if 20.0 <= pr <= 75.0:
        return 1.0
    if pr < 20.0:
        return 0.4 + 0.6 * pr / 20.0
    return max(0.0, 1.0 - (pr - 75.0) / 25.0)


def _compute_dividend_quality(rows: list[dict], min_yield: float = 1.0) -> dict:
    """Rank dividend-paying rows by a composite Dividend Quality Score (dq_score).

    Filters to rows with dividends_yield_current >= min_yield. For each qualifying
    row a raw score is computed from three components:
      yield_norm:   min(yield_pct / 10.0, 1.0)  [10%+ maps to 1.0]
      growth_norm:  min(years_growing / 50.0, 1.0) [50+ years maps to 1.0]
      payout_norm:  _payout_health(payout_ratio)   [piecewise, 1.0 at 20-75%]

    Weights: yield 30%, growth streak 40%, payout health 30%.

    raw = 0.30 * yield_norm + 0.40 * growth_norm + 0.30 * payout_norm

    dq_score is the percentile rank (0-100) of raw within the qualifying set.
    A score of 99 beats 99% of the sample on the combined quality-yield composite.

    Category labels:
      "Aristocrat"  years_growing >= 25
      "Achiever"    years_growing >= 10
      "Grower"      years_growing >= 1
      "Payer"       years_growing == 0 or missing

    Returns {count, avg_yield, by_category, by_sector, stocks}.
    None-yield rows are silently skipped; rows below min_yield are excluded.
    """
    qualified: list[dict] = []
    for row in rows:
        yield_pct = row.get("dividends_yield_current")
        if not isinstance(yield_pct, (int, float)) or isinstance(yield_pct, bool):
            continue
        if yield_pct < min_yield:
            continue

        yg_raw = row.get("continuous_dividend_growth")
        yg = int(yg_raw) if isinstance(yg_raw, (int, float)) and not isinstance(yg_raw, bool) else 0

        if yg >= 25:
            category = "Aristocrat"
        elif yg >= 10:
            category = "Achiever"
        elif yg >= 1:
            category = "Grower"
        else:
            category = "Payer"

        yp_raw = row.get("continuous_dividend_payout")
        pr = row.get("payout_ratio")

        qualified.append({
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "sector": row.get("sector") or "Unknown",
            "market_cap_basic": row.get("market_cap_basic"),
            "div_yield": round(float(yield_pct), 3),
            "years_paying": int(yp_raw) if isinstance(yp_raw, (int, float)) and not isinstance(yp_raw, bool) else None,
            "years_growing": yg,
            "payout_ratio": pr,
            "roe": row.get("return_on_equity"),
            "category": category,
            "dq_score": None,
            "perf_1m": row.get("Perf.1M"),
        })

    if not qualified:
        return {
            "count": 0,
            "avg_yield": None,
            "by_category": {"Aristocrat": 0, "Achiever": 0, "Grower": 0, "Payer": 0},
            "by_sector": [],
            "stocks": [],
        }

    # Compute raw composite score for each row.
    raw_scores: list[float] = []
    for q in qualified:
        yield_norm = min(1.0, q["div_yield"] / 10.0)
        growth_norm = min(1.0, q["years_growing"] / 50.0)
        payout_norm = _payout_health(q["payout_ratio"])
        raw_scores.append(0.30 * yield_norm + 0.40 * growth_norm + 0.30 * payout_norm)

    # Percentile rank the raw composite to get dq_score (0-100).
    n = len(raw_scores)
    for i, q in enumerate(qualified):
        raw = raw_scores[i]
        below = sum(1 for r in raw_scores if r < raw)
        equal = sum(1 for r in raw_scores if r == raw)
        q["dq_score"] = round((below + 0.5 * equal) / n * 100.0, 1)

    qualified.sort(key=lambda x: -(x["dq_score"] or 0.0))

    # Summary stats.
    all_yields = [q["div_yield"] for q in qualified]
    avg_yield = round(sum(all_yields) / len(all_yields), 2) if all_yields else None

    # by_category counts.
    by_category: dict[str, int] = {"Aristocrat": 0, "Achiever": 0, "Grower": 0, "Payer": 0}
    for q in qualified:
        by_category[q["category"]] += 1

    # by_sector aggregation.
    sec_buckets: dict[str, dict] = {}
    for q in qualified:
        sec = q["sector"]
        b = sec_buckets.setdefault(
            sec, {"count": 0, "yields": [], "aristocrats": 0, "achievers": 0}
        )
        b["count"] += 1
        b["yields"].append(q["div_yield"])
        if q["category"] == "Aristocrat":
            b["aristocrats"] += 1
        elif q["category"] == "Achiever":
            b["achievers"] += 1

    by_sector = sorted(
        [
            {
                "sector": sec,
                "count": b["count"],
                "avg_yield": round(sum(b["yields"]) / len(b["yields"]), 2),
                "aristocrats": b["aristocrats"],
                "achievers": b["achievers"],
            }
            for sec, b in sec_buckets.items()
        ],
        key=lambda s: -s["count"],
    )

    return {
        "count": len(qualified),
        "avg_yield": avg_yield,
        "by_category": by_category,
        "by_sector": by_sector,
        "stocks": qualified,
    }


@_tool
def dividend_screen(
    market: str = "america",
    min_yield: float = 1.0,
    min_years_growing: int = 0,
    filters: list[dict] | None = None,
    limit: Limit = 500,
    top: int = 50,
    include_otc: bool = False,
) -> dict:
    """Find high-quality dividend-paying stocks ranked by a Dividend Quality Score.

    Surfaces income ideas with a composite score (dq_score, 0-100) that weights
    three things simultaneously: how high the yield is, how long the dividend has
    grown consecutively (quality and reliability signal), and whether the payout
    ratio is in a healthy range (not so low it risks being cut, not so high it
    strains the balance sheet).

    Score components:
      yield component   30%: yield up to 10% maps to full credit
      growth streak     40%: consecutive years of dividend growth / 50 (capped)
      payout health     30%: ideal 20-75%, penalized outside that band
    dq_score is the percentile rank (0-100) of the raw composite in the sample.

    Category labels (based on consecutive years of dividend growth):
      Aristocrat   25+ consecutive years of growth (S&P Dividend Aristocrat tier)
      Achiever     10-24 years
      Grower       1-9 years
      Payer        paying dividends but no confirmed growth streak

    market:          one of scan_list_markets() ids (america works best; fundmental data
                     is sparse for crypto/forex/futures).
    min_yield:       minimum dividend yield % to qualify (default 1.0).
    min_years_growing: return only stocks with at least this many consecutive years
                     of dividend growth (0 = all payers; 10 = Achievers+; 25 = Aristocrats).
    filters:         optional extra filters (e.g. market_cap_basic > 1e9).
    limit:           rows to sample (default 500).
    top:             max stocks to return (default 50).
    include_otc:     scan OTC too (default False).

    Returns {market, universe, sample, min_yield, count, avg_yield,
    by_category:{Aristocrat, Achiever, Grower, Payer},
    by_sector:[{sector, count, avg_yield, aristocrats, achievers}],
    stocks:[{name, close, change, sector, market_cap_basic, div_yield,
    years_paying, years_growing, payout_ratio, roe, category, dq_score, perf_1m}]}.
    """
    guarded, guard = _otc_guard(market, filters, include_otc)
    base_filters = [Filter(**f) for f in guarded]
    base_filters.append(
        Filter(field="dividends_yield_current", op=">", value=max(0.0, min_yield))
    )
    req = ScreenRequest(
        market=market,
        filters=base_filters,
        columns=[
            "name", "close", "change", "sector", "market_cap_basic",
            "dividends_yield_current", "continuous_dividend_payout",
            "continuous_dividend_growth", "payout_ratio",
            "return_on_equity", "Perf.1M",
        ],
        sort=[SortKey(field="dividends_yield_current", dir="desc")],
        limit=max(1, min(limit, MAX_LIMIT)),
    )
    resp = run_screen(req)
    if resp["meta"].get("error"):
        _STATS["errors"] += 1
        return {"error": resp["meta"]["error"]}

    result = _compute_dividend_quality(resp["rows"], min_yield=min_yield)

    if min_years_growing > 0:
        result["stocks"] = [
            s for s in result["stocks"] if s["years_growing"] >= min_years_growing
        ]

    result["stocks"] = result["stocks"][:max(1, top)]
    result["universe"] = resp["count"]
    result["market"] = market
    result["min_yield"] = min_yield
    result["sample"] = len(resp["rows"])
    if guard:
        result["otc_guard"] = guard
    return result


# ----------------------------------------------------------------------------
# Prompts: canned, modern screening workflows the model can launch
# ----------------------------------------------------------------------------
@_prompt
def momentum_breakouts(market: str = "america") -> str:
    """Find and read the strongest momentum breakouts right now."""
    return (
        f"Use the scan_* tools on the {market} market. Run the "
        "`signal_volume_breakout` and `signal_stacked_ema_ribbon` presets via "
        "scan_run_preset, then for the top 3 names call `scan_analyze` to read each chart. "
        "Summarize which look like the cleanest breakouts and why, citing the "
        "rating, trend, and the 52-week position."
    )


@_prompt
def oversold_quality(market: str = "america") -> str:
    """Hunt for oversold names that are still high quality."""
    return (
        "Screen for oversold-but-quality candidates: run a `scan_screen` on "
        f"{market} with RSI < 35 and return_on_equity > 15 and market_cap_basic "
        "> 2e9, ranked by RSI ascending. Then `scan_analyze` the top few and flag any "
        "where the weekly RSI is still bullish (a pullback in an uptrend)."
    )


@_prompt
def rank_by_factor(factor: str = "momentum", market: str = "america") -> str:
    """Rank a market by a composite factor and read the leaders."""
    return (
        f"Call scan_run_factor_preset with factor_preset_id='{factor}' on the {market} "
        "market and a market_cap_basic > 1e10 filter. Take the top 5 by "
        "factor_score, then `scan_analyze` each and tell me which leader has the "
        "strongest technical confirmation."
    )


@_prompt
def read_symbol(ticker: str) -> str:
    """Give a full plain-language chart read for one symbol."""
    return (
        f"Call `scan_analyze` for {ticker}, then `scan_technical_rating` across the 1d, 1w, "
        "and 1mo timeframes, and `scan_chart` for a link. Write a tight chart read: "
        "trend, momentum, key levels, multi-timeframe rating, and a final stance."
    )


# ----------------------------------------------------------------------------
# Resources: catalogs the model can read wholesale
# ----------------------------------------------------------------------------
@_resource("screener://fields")
def fields_resource() -> dict:
    """The full field catalog (id, label, group, type, unit)."""
    return {"count": len(FIELDS), "fields": FIELDS}


@_resource("screener://presets")
def presets_resource() -> dict:
    """Every preset scan and factor preset, in full."""
    return {"presets": PRESETS, "factor_presets": FACTOR_PRESETS}


@_resource("screener://operators")
def operators_resource() -> list[dict]:
    """The filter operator reference."""
    return OPERATORS


# ----------------------------------------------------------------------------
# Mounting onto the momentum server
# ----------------------------------------------------------------------------
PREFIX = "scan_"


def tool_names(prefix: str = PREFIX) -> list[str]:
    """The registered tool names, prefixed, in registration order."""
    return [prefix + fn.__name__ for fn in _TOOLS]


def prompt_names(prefix: str = PREFIX) -> list[str]:
    """The registered prompt names, prefixed, in registration order."""
    return [prefix + fn.__name__ for fn in _PROMPTS]


def resource_uris() -> list[str]:
    """The registered resource URIs, in registration order."""
    return [uri for uri, _ in _RESOURCES]


def register_scanline_tools(mcp: Any, prefix: str = PREFIX) -> list[str]:
    """Register every scanline tool, prompt and resource on `mcp`; return tool names.

    `mcp` is any FastMCP-shaped server: it needs `tool(name=...)`,
    `prompt(name=...)` and `resource(uri)` decorator factories, which both the
    legacy `mcp.server.fastmcp.FastMCP` the momentum server is built on and the
    standalone `fastmcp` package provide with the same signatures.
    """
    names: list[str] = []
    for fn in _TOOLS:
        name = prefix + fn.__name__
        mcp.tool(name=name)(fn)
        names.append(name)
    for fn in _PROMPTS:
        mcp.prompt(name=prefix + fn.__name__)(fn)
    for uri, fn in _RESOURCES:
        mcp.resource(uri)(fn)
    return names
