"""Service wrapper over the tradingview-screener package.

Builds a Query from a structured request, translates op strings to column
operators, runs it, and returns json-safe records. All upstream calls are
wrapped so a failure becomes a clean ScreenerError, never a 500 stacktrace.
"""

from __future__ import annotations

import math
from typing import Any

from tradingview_screener import (
    Query,
    bond,
    cfd,
    col,
    crypto,
    forex,
    futures,
    stocks,
)

import requests

from . import realtime
from .fields import default_columns, field_type, validate_field

# Each market maps to the package helper that configures the correct upstream
# scan context. set_markets('crypto') returns 0 rows for several markets, so we
# start from these pre-configured Query builders instead.
_MARKET_QUERY = {
    "america": lambda: stocks("america"),
    "crypto": crypto,
    "forex": forex,
    "futures": futures,
    "bond": bond,
    "cfd": cfd,
}


def _base_query(market: str) -> Query:
    """Return a fresh Query already scoped to the given market."""
    factory = _MARKET_QUERY.get(market)
    if factory is None:
        raise ScreenerError(f"Unknown market: {market}", "unknown_market")
    return factory()

# Markets the screener can target.
MARKETS: list[dict] = [
    {"id": "america", "label": "US Stocks", "kind": "stocks"},
    {"id": "crypto", "label": "Crypto", "kind": "crypto"},
    {"id": "forex", "label": "Forex", "kind": "forex"},
    {"id": "futures", "label": "Futures", "kind": "futures"},
    {"id": "bond", "label": "Bonds", "kind": "bond"},
    {"id": "cfd", "label": "CFD", "kind": "cfd"},
]

_MARKET_IDS = {m["id"] for m in MARKETS}


# Error codes that mean the caller's request was bad (HTTP 400). Everything
# else is an upstream failure (HTTP 502).
VALIDATION_CODES = frozenset({
    "invalid_request", "unknown_market", "unknown_field", "bad_value",
    "bad_operator", "unknown_sort_field",
})


class ScreenerError(Exception):
    """Clean, user-facing error from the screener service.

    `code` is a short stable machine string; `detail` an optional one-liner.
    Neither ever carries raw library text or credentials.
    """

    def __init__(self, message: str, code: str = "invalid_request", detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail

    @property
    def is_validation(self) -> bool:
        return self.code in VALIDATION_CODES


def _upstream_error(exc: BaseException) -> ScreenerError:
    """Map any upstream exception to a short human message.

    Deliberately never uses str(exc): library text is long, leaks URLs and can
    carry request internals.
    """
    if isinstance(exc, requests.exceptions.Timeout):
        return ScreenerError("TradingView took too long to respond. Try again shortly.",
                             "upstream_timeout", "Timeout")
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ScreenerError("Could not reach TradingView. Check the network and try again.",
                             "upstream_unreachable", "ConnectionError")
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            return ScreenerError("TradingView is rate limiting requests. Wait a moment and retry.",
                                 "upstream_rate_limited", "HTTP 429")
        return ScreenerError("TradingView rejected the query.", "upstream_error",
                             f"HTTP {status}" if status else "HTTPError")
    return ScreenerError("The TradingView query failed.", "upstream_error", type(exc).__name__)


# Operators that take a single comparison value.
def _apply_op(c, op: str, value: Any):
    """Translate an op string plus value into a column condition."""
    if op == ">":
        return c > value
    if op == ">=":
        return c >= value
    if op == "<":
        return c < value
    if op == "<=":
        return c <= value
    if op in ("==", "="):
        return c == value
    if op == "!=":
        return c != value
    if op == "between":
        a, b = value
        return c.between(a, b)
    if op == "not_between":
        a, b = value
        return c.not_between(a, b)
    if op in ("above_pct", "below_pct"):
        # The library wants (other_field, ratio): close >= field * ratio for
        # above_pct, close <= field * ratio for below_pct. The pair shape is
        # validated upstream of here.
        return getattr(c, op)(*value)
    if op == "crosses":
        return c.crosses(value)
    if op == "crosses_above":
        return c.crosses_above(value)
    if op == "crosses_below":
        return c.crosses_below(value)
    if op == "isin":
        return c.isin(value)
    if op == "not_in":
        return c.not_in(value)
    if op == "in_day_range":
        a, b = value
        return c.in_day_range(a, b)
    if op == "in_week_range":
        a, b = value
        return c.in_week_range(a, b)
    if op == "in_month_range":
        a, b = value
        return c.in_month_range(a, b)
    if op == "like":
        return c.like(value)
    if op == "not_like":
        return c.not_like(value)
    if op == "empty":
        return c.empty()
    if op == "not_empty":
        return c.not_empty()
    raise ScreenerError(f"Unsupported operator: {op}", "bad_operator")


_SCALAR_OPS = {">", ">=", "<", "<=", "==", "=", "!=", "crosses", "crosses_above", "crosses_below"}
_PAIR_OPS = {"between", "not_between", "in_day_range", "in_week_range", "in_month_range",
             "above_pct", "below_pct"}
_LIST_OPS = {"isin", "not_in"}
_TEXT_OPS = {"like", "not_like"}
_NO_VALUE_OPS = {"empty", "not_empty"}
KNOWN_OPS = _SCALAR_OPS | _PAIR_OPS | _LIST_OPS | _TEXT_OPS | _NO_VALUE_OPS


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (
        isinstance(v, float) and (math.isnan(v) or math.isinf(v))
    )


def _coerce_operand(field: str, v: Any) -> Any:
    """Validate one comparison operand and return it (numeric strings become
    floats). A string is accepted when the field is text-typed or when it is
    itself a real field id (cross-field compare)."""
    if _is_number(v):
        return v
    if isinstance(v, str):
        if field_type(field) == "str":
            return v
        if validate_field(v):
            return v
        try:
            f = float(v)
        except ValueError:
            f = None
        if f is not None and _is_number(f):
            return f
        raise ScreenerError(
            f"Filter on '{field}': value '{v[:40]}' is not a number or a known field id.",
            "bad_value")
    raise ScreenerError(f"Filter on '{field}': value must be a number or a field id.", "bad_value")


def _check_filter_value(field: str, op: str, value: Any) -> Any:
    """Validate (and lightly coerce) a filter value for its operator."""
    if op in _NO_VALUE_OPS:
        return value
    if op in _TEXT_OPS:
        if not isinstance(value, str) or not value:
            raise ScreenerError(f"Filter on '{field}': '{op}' needs a non-empty text value.",
                                "bad_value")
        return value
    if op in _LIST_OPS:
        if not isinstance(value, (list, tuple)) or not value:
            raise ScreenerError(f"Filter on '{field}': '{op}' needs a non-empty list.", "bad_value")
        if field_type(field) == "str":
            return list(value)
        return [_coerce_operand(field, v) for v in value]
    if op in _PAIR_OPS:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ScreenerError(f"Filter on '{field}': '{op}' needs a [a, b] pair.", "bad_value")
        return [_coerce_operand(field, v) for v in value]
    return _coerce_operand(field, value)


def _build_conditions(filters: list[dict]):
    """Turn filter dicts into a list of column conditions, validating each."""
    conds = []
    for filt in filters or []:
        field = filt.get("field")
        op = filt.get("op", ">")
        value = filt.get("value")
        if not field:
            raise ScreenerError("Filter missing field", "unknown_field")
        if not validate_field(field):
            raise ScreenerError(f"Unknown filter field '{field}'.", "unknown_field")
        if op not in KNOWN_OPS:
            raise ScreenerError(f"Unsupported operator '{op}' on field '{field}'.", "bad_operator")
        value = _check_filter_value(field, op, value)
        conds.append(_apply_op(col(field), op, value))
    return conds


def _json_safe(value: Any) -> Any:
    """Replace NaN/inf with None so the payload serializes cleanly."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


def _fetch(q: Query) -> tuple[int, Any]:
    """Run the query, with the session cookie when real-time mode is on.

    If TradingView refuses the cookie (401/403) we flag it rejected and retry
    once without it, so the screener degrades to delayed data instead of
    failing. Every exception raised on the cookie path is re-raised `from None`
    so no request object (which carries the Cookie header) rides along.
    """
    cookies = realtime.active_cookies()
    if cookies:
        try:
            return q.get_scanner_data(cookies=cookies)
        except requests.exceptions.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in (401, 403):
                raise _upstream_error(exc) from None
            realtime.mark_rejected()
        except Exception as exc:  # noqa: BLE001
            raise _upstream_error(exc) from None
    return q.get_scanner_data()


def run_query(
    market: str = "america",
    columns: list[str] | None = None,
    filters: list[dict] | None = None,
    match: str = "all",
    sort: list[dict] | None = None,
    limit: int = 150,
    offset: int = 0,
) -> dict:
    """Run a screener query and return {count, rows, columns}.

    'ticker' and 'name' are always selected. Sorting is applied server-side
    from the first sort key. Raises ScreenerError on any failure.
    """
    if market not in _MARKET_IDS:
        raise ScreenerError(f"Unknown market: {market}", "unknown_market")

    columns = list(columns) if columns else default_columns(market)

    # An unknown column is an error, never silently dropped.
    bad = [c for c in columns if not validate_field(c)]
    if bad:
        raise ScreenerError(
            "Unknown column" + ("s" if len(bad) > 1 else "") + ": "
            + ", ".join(f"'{c}'" for c in bad[:5]) + ".", "unknown_field")
    if match not in ("all", "any"):
        raise ScreenerError(f"Bad match '{match}': use 'all' or 'any'.", "invalid_request")
    for key in sort or []:
        if key.get("dir", "desc") not in ("asc", "desc"):
            raise ScreenerError(f"Bad sort dir '{key.get('dir')}': use 'asc' or 'desc'.",
                                "invalid_request")
        f = key.get("field")
        if not f or not validate_field(f):
            raise ScreenerError(f"Unknown sort field '{f}'.", "unknown_sort_field")

    # Always include name; ticker is added automatically by the package.
    select_cols: list[str] = []
    for c in ["name", *columns]:
        if c not in select_cols:
            select_cols.append(c)

    try:
        conds = _build_conditions(filters)
    except ScreenerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ScreenerError("A filter value has the wrong shape for its operator.",
                            "bad_value", type(exc).__name__) from None

    try:
        q = _base_query(market).select(*select_cols)

        # The market helpers come pre-loaded with the constraints that DEFINE
        # the market: stocks('america') carries is_primary == true in `filter`,
        # plus the instrument type constraints in `filter2`. Both Query.where()
        # and Query.where2() ASSIGN rather than append:
        #
        #     def where(self, *expressions):
        #         self.query['filter'] = list(expressions)
        #
        # so adding any condition of our own silently wiped them out. A single
        # trivially true filter (close > -1e12) took america from 7,648 rows to
        # 13,374 by readmitting every non-primary listing, which means every
        # preset and every filtered tool has been scanning a different, much
        # dirtier universe than the unfiltered default view. Compose with the
        # market's own constraints instead of replacing them.
        base_filter = list(q.query.get("filter") or [])
        base_filter2 = q.query.get("filter2")

        if conds:
            if match == "any" and len(conds) > 1:
                from tradingview_screener import Or

                combined = Or(*conds)
                # filter2 is {"operator": "and", "operands": [{"operation":...}]}
                # and Or() returns exactly that operand shape, so the market's
                # type constraints survive alongside ours.
                if isinstance(base_filter2, dict) and base_filter2.get("operator") == "and":
                    base_filter2.setdefault("operands", []).append(combined)
                else:
                    q = q.where2(combined)
                if base_filter:
                    q = q.where(*base_filter)
            else:
                q = q.where(*base_filter, *conds)

        if sort:
            first = sort[0]
            field = first.get("field")
            ascending = first.get("dir", "desc") != "desc"
            q = q.order_by(field, ascending=ascending)

        q = q.offset(max(0, offset)).limit(max(1, limit))

        count, df = _fetch(q)
    except ScreenerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _upstream_error(exc) from None

    # DataFrame -> json-safe records.
    records: list[dict] = []
    df_cols = list(df.columns)
    for raw in df.to_dict("records"):
        records.append({k: _json_safe(v) for k, v in raw.items()})

    return {"count": int(count), "rows": records, "columns": df_cols,
            "mode": realtime.mode()}
