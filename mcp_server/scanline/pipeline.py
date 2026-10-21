"""The shared screen pipeline.

One pure function, run_screen, that takes a ScreenRequest and returns the
shaped response dict. Both the HTTP API (scanline/app.py) and the MCP server
(scanline/mcp_server.py) call this so a screen behaves identically no matter
who drives it. Caching lives in the callers, not here.
"""

from __future__ import annotations

import math
import time

from . import analytics, realtime
from .cache import make_key
from .fields import default_columns, validate_field
from .models import ScreenRequest
from .screener import ScreenerError, run_query

# Below this share of non-null cells across the weighted columns, a factor
# score is noise (growth/value/quality on crypto or forex are all null), so it
# is withheld rather than ranked.
MIN_FACTOR_COVERAGE = 0.20

_STAT_PREFIXES = ("zscore(", "pctrank(", "rank(", "norm(", "madzscore(", "winsor(", "decile(")


def request_key(req: ScreenRequest) -> str:
    """Cache key for a request. Includes the data MODE (never the cookie) so
    delayed and real-time results cannot be served for one another."""
    return make_key({"mode": realtime.mode(), "req": req.model_dump()})


def _round_floats(rows: list[dict]) -> list[dict]:
    """Round floats to a reasonable display precision, drop NaN/inf."""
    for row in rows:
        for k, v in row.items():
            if isinstance(v, float):
                if math.isnan(v) or math.isinf(v):
                    row[k] = None
                else:
                    row[k] = round(v, 6)
    return rows


def _query_columns(req: ScreenRequest) -> list[str]:
    """Requested columns plus any base fields stats/factor/sort need to read."""
    base_columns = list(req.columns) or default_columns(req.market)
    needed = set(base_columns)
    for s in req.stats:
        needed.add(s.field)
    if req.factor:
        for w in req.factor.weights:
            needed.add(w.field)
    # Sort on a base (non-derived) field needs that column present too.
    # Virtual stat columns must NOT be added to the query; they are computed
    # after the query returns. All stat fn names must be listed here.
    derived = _derived_ids(req)
    for sk in req.sort:
        if sk.field not in derived:
            needed.add(sk.field)
    return list(dict.fromkeys([*base_columns, *needed]))


def _derived_ids(req: ScreenRequest) -> set[str]:
    """Column ids this request itself creates after the query returns."""
    ids = {c.id for c in req.computed}
    ids |= {f"{s.fn}({s.field})" for s in req.stats}
    if req.factor and req.factor.weights:
        ids.add("factor_score")
    return ids


def _validate_request(req: ScreenRequest) -> None:
    """Catalog-check everything the screener layer does not see."""
    derived = _derived_ids(req)
    for s in req.stats:
        if not validate_field(s.field):
            raise ScreenerError(f"Unknown stat field '{s.field}'.", "unknown_field")
    if req.factor:
        for w in req.factor.weights:
            if not validate_field(w.field):
                raise ScreenerError(f"Unknown factor field '{w.field}'.", "unknown_field")
    for sk in req.sort:
        if sk.field in derived or validate_field(sk.field):
            continue
        raise ScreenerError(f"Unknown sort field '{sk.field}'.", "unknown_sort_field")


def _error_response(req: ScreenRequest, started: float, exc: ScreenerError) -> dict:
    return {
        "count": 0,
        "rows": [],
        "columns": [],
        "meta": {
            "cached": False,
            "ms": int((time.time() - started) * 1000),
            "market": req.market,
            "error": str(exc),
            "error_code": exc.code,
            "detail": exc.detail,
            "mode": realtime.mode(),
            "warnings": [],
        },
    }


def _factor_coverage(rows: list[dict], weights: list[dict]) -> tuple[float, list[str]]:
    """Weight-averaged share of non-null cells, plus the weighted fields that
    are mostly empty."""
    if not rows or not weights:
        return 1.0, []
    total = 0.0
    acc = 0.0
    sparse: list[str] = []
    for w in weights:
        field = w["field"]
        cov = sum(1 for r in rows if r.get(field) is not None) / len(rows)
        wt = abs(float(w.get("weight", 1))) or 1.0
        total += wt
        acc += wt * cov
        if cov < MIN_FACTOR_COVERAGE:
            sparse.append(field)
    return (acc / total if total else 1.0), sparse


def run_screen(req: ScreenRequest) -> dict:
    """Run the full screen pipeline and return the shaped response dict.

    Shape: {count, rows, columns, meta:{cached, ms, market, error}}. A query
    failure becomes a clean error response, never a raised exception, so every
    caller gets the same contract.
    """
    started = time.time()

    try:
        _validate_request(req)
        derived = _derived_ids(req)
        result = run_query(
            market=req.market,
            columns=_query_columns(req),
            filters=[f.model_dump() for f in req.filters],
            match=req.match,
            # Derived columns only exist after the query; they are sorted below.
            sort=[s.model_dump() for s in req.sort if s.field not in derived] or None,
            limit=req.limit,
            offset=req.offset,
        )
    except ScreenerError as exc:
        return _error_response(req, started, exc)

    warnings: list[str] = []

    rows = result["rows"]

    # Analytics pipeline over the returned rows.
    if req.computed:
        rows = analytics.apply_computed(rows, [c.model_dump() for c in req.computed])
    if req.stats:
        rows = analytics.apply_stats(rows, [s.model_dump() for s in req.stats])

    factor_requested = bool(req.factor and req.factor.weights)
    if factor_requested:
        weights = [w.model_dump() for w in req.factor.weights]
        rows = analytics.apply_factor(rows, weights)
        coverage, sparse = _factor_coverage(rows, weights)
        if rows and coverage < MIN_FACTOR_COVERAGE:
            for r in rows:
                r["factor_score"] = None
            warnings.append(
                f"factor_score withheld: only {coverage:.0%} of the weighted columns "
                f"have data in this market ({', '.join(sparse) or 'all'}). "
                "Pick factors that populate here."
            )
        else:
            if sparse:
                warnings.append(
                    "Sparse factor columns (under 20% filled), treated as neutral: "
                    + ", ".join(sparse) + "."
                )
            # Re-sort by factor_score when the caller gave no explicit sort.
            if not req.sort:
                rows.sort(key=lambda r: r.get("factor_score") or float("-inf"), reverse=True)

    # Explicit sort whose lead key is a derived column: apply it locally.
    if req.sort and req.sort[0].field in derived:
        for sk in reversed(req.sort):
            present = [r for r in rows if r.get(sk.field) is not None]
            absent = [r for r in rows if r.get(sk.field) is None]
            present.sort(key=lambda r, f=sk.field: r[f], reverse=(sk.dir == "desc"))
            rows = present + absent

    rows = _round_floats(rows)

    # Final ordered column list: query columns, then computed, stats, factor.
    ordered: list[str] = []
    for c in result["columns"]:
        if c not in ordered:
            ordered.append(c)
    for c in req.computed:
        if c.id not in ordered:
            ordered.append(c.id)
    for s in req.stats:
        name = f"{s.fn}({s.field})"
        if name not in ordered:
            ordered.append(name)
    if factor_requested and "factor_score" not in ordered:
        ordered.append("factor_score")

    return {
        "count": result["count"],
        "rows": rows,
        "columns": ordered,
        "meta": {
            "cached": False,
            "ms": int((time.time() - started) * 1000),
            "market": req.market,
            "error": None,
            "error_code": None,
            "detail": None,
            "mode": result.get("mode", realtime.mode()),
            "warnings": warnings,
        },
    }
