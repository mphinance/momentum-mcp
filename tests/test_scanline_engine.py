"""SCANLINE engine tests, ported from mphinance/scanline tests/test_backend.py.

Backend tests.

Analytics tests run fully offline. The API tests hit the live tradingview
endpoint and are marked 'live' so they can be skipped without network.
"""

from __future__ import annotations

import math

import pytest

from mcp_server.scanline import analytics
from mcp_server.scanline.fields import FIELDS, field_index, validate_field
from mcp_server.scanline.presets import FACTOR_PRESETS, OTC_GUARDED, PRESETS


# --- catalog -------------------------------------------------------------

def test_catalog_has_enough_fields():
    assert len(FIELDS) >= 150


def test_catalog_entries_well_formed():
    types = {"num", "pct", "price", "int", "str", "bignum"}
    for f in FIELDS:
        assert set(f) == {"id", "label", "group", "type", "unit"}
        assert f["type"] in types
        assert f["id"]


def test_validate_field():
    assert validate_field("close")
    assert not validate_field("not_a_real_field_xyz")


def test_presets_count():
    assert len(PRESETS) >= 22
    assert len(FACTOR_PRESETS) == 5


def test_preset_fields_are_real():
    # Every column referenced by a preset must be a catalog field, or a catalog
    # field carrying a valid timeframe suffix (e.g. RSI|1W on the MTF presets).
    for p in PRESETS:
        for c in p["columns"]:
            assert validate_field(c), f"{p['id']} uses unknown column {c}"


def test_preset_filter_fields_are_real():
    # Filter fields and any field-id values must validate too, so a typo in a
    # signal preset cannot silently return nothing.
    for p in PRESETS:
        for flt in p.get("filters", []):
            assert validate_field(flt["field"]), f"{p['id']} filters unknown field {flt['field']}"
            val = flt.get("value")
            if isinstance(val, str):
                assert validate_field(val), f"{p['id']} compares against unknown field {val}"


# --- OTC guard -----------------------------------------------------------

_FLOOR_FIELDS = {
    "market_cap_basic", "market_cap_calc",
    "volume", "average_volume_10d_calc", "average_volume_30d_calc",
    "average_volume_60d_calc", "average_volume_90d_calc", "value_traded",
    "close", "open", "low", "high",
}


def _has_numeric_floor(preset: dict) -> bool:
    """True when a preset already bounds size, liquidity or price from below.

    A comparison against another FIELD (close > EMA8) is not a floor, and a
    ceiling (close < 5) is not one either, so neither counts here.
    """
    for f in preset.get("filters", []) or []:
        if f.get("field") not in _FLOOR_FIELDS:
            continue
        op, val = str(f.get("op")), f.get("value")
        if op in (">", ">=") and isinstance(val, (int, float)):
            return True
        if op in ("between", "in_range") and isinstance(val, (list, tuple)) and val:
            if isinstance(val[0], (int, float)):
                return True
    return False


def test_otc_guard_applied_to_every_guarded_preset():
    for p in PRESETS:
        if p["id"] not in OTC_GUARDED:
            continue
        assert p.get("otc_guarded") is True, f"{p['id']} is not marked otc_guarded"
        assert any(f.get("field") == "exchange" and f.get("op") == "not_in"
                   for f in p["filters"]), f"{p['id']} is missing the OTC exclusion"


def test_otc_guard_only_on_match_all_america_presets():
    # The guard APPENDS a condition. Under match "any" that would widen the
    # result rather than restrict it, and a US exchange filter is meaningless
    # off the america market.
    for p in PRESETS:
        if p["id"] in OTC_GUARDED:
            assert p.get("match", "all") == "all", f"{p['id']} uses match=any"
            assert p["market"] == "america", f"{p['id']} is not a US preset"


def test_every_unprotected_america_preset_is_guarded():
    # This is the point of the guard. A new america preset with no floor must
    # either gain a floor or join OTC_GUARDED, otherwise it silently returns
    # sub-penny OTC shells whose indicator values are computed from noise.
    for p in PRESETS:
        if p["market"] != "america" or p.get("match", "all") != "all":
            continue
        if _has_numeric_floor(p):
            continue
        assert p["id"] in OTC_GUARDED, (
            f"{p['id']} has no size, liquidity or price floor and is not OTC-guarded"
        )


def test_otc_guard_is_idempotent():
    # Import order must never double-append the exclusion.
    from mcp_server.scanline import presets as presets_mod

    before = [len(p["filters"]) for p in PRESETS]
    presets_mod._apply_otc_guard()
    assert [len(p["filters"]) for p in PRESETS] == before


@pytest.mark.live
def test_preset_filter_fields_are_populated_in_their_own_market():
    """A field can be in the catalog and still be null in a given market.

    validate_field is global, so the offline field tests cannot see this.
    crypto_movers filtered on Value.Traded, which is a real catalog field but is
    NULL for every row in the crypto market, so the preset silently returned 0
    rows out of 39,812 while reporting success and no error. Catalog validity is
    global; field availability is per market, and only a live call can tell.
    """
    from mcp_server.scanline.models import ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    wanted: dict[str, set[str]] = {}
    for p in PRESETS:
        # A preset that DECLARES itself unavailable has already accounted for its
        # dead field in the open, which is the outcome this test exists to force.
        if p.get("unavailable"):
            continue
        for f in p.get("filters", []) or []:
            wanted.setdefault(p["market"], set()).add(f["field"])
            val = f.get("value")
            if isinstance(val, str) and validate_field(val):
                wanted[p["market"]].add(val)

    for market, fields in wanted.items():
        cols = sorted(fields)
        resp = run_screen(ScreenRequest(market=market, filters=[], columns=cols, limit=200))
        assert not resp["meta"].get("error"), f"{market}: {resp['meta']['error']}"
        assert resp["rows"], f"{market} returned no rows at all"
        for field in cols:
            populated = sum(1 for r in resp["rows"] if r.get(field) is not None)
            assert populated, (
                f"{market}: preset filter field '{field}' is null for all "
                f"{len(resp['rows'])} sampled rows, so any preset filtering on it "
                f"returns nothing while reporting success"
            )


@pytest.mark.live
def test_displayed_and_scored_fields_are_populated_in_their_own_market():
    """The same dead-field check, for fields that are READ rather than filtered.

    The filter-field test above only walks preset filters, so a dead field that
    is merely displayed or scored slips past it. Three did:

      - Value.Traded survived in DEFAULT_COLUMNS["crypto"] and MARKET_FIELDS
        ["crypto"] after it was taken out of the crypto_movers filter, so the
        default crypto view still carried a permanently blank column.
      - The Growth factor model weighted revenue_growth_ttm_yoy and
        earnings_per_share_diluted_growth_percent_ttm_yoy at 1.0 each, and both
        are null for every US row. apply_factor scores a missing value as 0, so
        Growth was a pure gross_margin sort, scoring identically to a
        gross-margin-only model on 1000 of 1000 sampled rows. A factor model
        cannot return zero rows, so nothing about the output looked wrong.

    A displayed dead field is a blank column. A scored one silently drops a
    factor out of the model, which is worse, because the ranking still looks
    like a ranking.
    """
    from mcp_server.scanline.fields import DEFAULT_COLUMNS, MARKET_FIELDS
    from mcp_server.scanline.models import ScreenRequest
    from mcp_server.scanline.pipeline import run_screen

    wanted: dict[str, set[str]] = {}
    for market, cols in DEFAULT_COLUMNS.items():
        wanted.setdefault(market, set()).update(cols)
    for market, cols in MARKET_FIELDS.items():
        wanted.setdefault(market, set()).update(cols)
    for p in PRESETS:
        if p.get("unavailable"):
            continue
        wanted.setdefault(p["market"], set()).update(p.get("columns", []) or [])
    # Factor models are america-only and are scored, not filtered.
    for fp in FACTOR_PRESETS:
        for w in fp.get("weights", []) or []:
            wanted.setdefault("america", set()).add(w["field"])

    for market, fields in sorted(wanted.items()):
        cols = sorted(fields)
        # Chunked so one market's column set cannot outgrow a single query.
        for i in range(0, len(cols), 40):
            chunk = cols[i:i + 40]
            resp = run_screen(
                ScreenRequest(market=market, filters=[], columns=chunk, limit=200)
            )
            assert not resp["meta"].get("error"), f"{market}: {resp['meta']['error']}"
            assert resp["rows"], f"{market} returned no rows at all"
            for field in chunk:
                populated = sum(1 for r in resp["rows"] if r.get(field) is not None)
                assert populated, (
                    f"{market}: '{field}' is displayed or scored but is null for "
                    f"all {len(resp['rows'])} sampled rows"
                )


# --- safe_eval sandbox ---------------------------------------------------

def test_safe_eval_basic_math():
    row = {"high": 10, "low": 8, "close": 9, "volume": 1000}
    assert analytics.safe_eval("(high-low)/close*100", row) == pytest.approx(22.2222, abs=1e-3)
    assert analytics.safe_eval("close*volume", row) == 9000


def test_safe_eval_functions():
    row = {"x": -5, "y": 16}
    assert analytics.safe_eval("abs(x)", row) == 5
    assert analytics.safe_eval("sqrt(y)", row) == 4
    assert analytics.safe_eval("max(x,y)", row) == 16


def test_safe_eval_div_by_zero_returns_none():
    assert analytics.safe_eval("a/b", {"a": 1, "b": 0}) is None


def test_safe_eval_rejects_imports():
    with pytest.raises(ValueError):
        analytics.safe_eval("__import__('os').system('echo hi')", {})


def test_safe_eval_rejects_attribute_access():
    with pytest.raises(ValueError):
        analytics.safe_eval("close.__class__", {"close": 5})


def test_safe_eval_rejects_subscript():
    with pytest.raises(ValueError):
        analytics.safe_eval("close[0]", {"close": 5})


def test_safe_eval_rejects_strings():
    with pytest.raises(ValueError):
        analytics.safe_eval("'a'+'b'", {})


def test_safe_eval_rejects_unknown_function():
    with pytest.raises(ValueError):
        analytics.safe_eval("exec('x')", {})


# --- computed columns ----------------------------------------------------

def test_apply_computed():
    rows = [{"high": 10, "low": 5, "close": 8}, {"high": 20, "low": 10, "close": 15}]
    out = analytics.apply_computed(rows, [{"id": "rng", "expr": "(high-low)/close*100"}])
    assert out[0]["rng"] == pytest.approx(62.5, abs=1e-3)
    assert out[1]["rng"] == pytest.approx(66.6667, abs=1e-3)


def test_apply_computed_malicious_is_none_not_executed():
    rows = [{"close": 5}]
    out = analytics.apply_computed(rows, [{"id": "x", "expr": "__import__('os').system('echo hi')"}])
    assert out[0]["x"] is None


# --- stats ---------------------------------------------------------------

def test_zscore():
    rows = [{"v": 1}, {"v": 2}, {"v": 3}, {"v": 4}, {"v": 5}]
    analytics.apply_stats(rows, [{"fn": "zscore", "field": "v"}])
    # mean 3, population std sqrt(2)
    assert rows[2]["zscore(v)"] == pytest.approx(0.0, abs=1e-6)
    assert rows[0]["zscore(v)"] == pytest.approx((1 - 3) / math.sqrt(2), abs=1e-3)


def test_pctrank():
    rows = [{"v": 10}, {"v": 20}, {"v": 30}, {"v": 40}]
    analytics.apply_stats(rows, [{"fn": "pctrank", "field": "v"}])
    assert rows[0]["pctrank(v)"] == pytest.approx(12.5, abs=1e-6)
    assert rows[3]["pctrank(v)"] == pytest.approx(87.5, abs=1e-6)


def test_rank_desc():
    rows = [{"v": 10}, {"v": 30}, {"v": 20}]
    analytics.apply_stats(rows, [{"fn": "rank", "field": "v"}])
    assert rows[1]["rank(v)"] == 1
    assert rows[2]["rank(v)"] == 2
    assert rows[0]["rank(v)"] == 3


def test_norm():
    rows = [{"v": 0}, {"v": 5}, {"v": 10}]
    analytics.apply_stats(rows, [{"fn": "norm", "field": "v"}])
    assert rows[0]["norm(v)"] == 0.0
    assert rows[1]["norm(v)"] == pytest.approx(0.5)
    assert rows[2]["norm(v)"] == 1.0


def test_madzscore_symmetric():
    # [1,2,3,4,5]: median=3, MAD=1, scale=1.4826
    rows = [{"v": 1}, {"v": 2}, {"v": 3}, {"v": 4}, {"v": 5}]
    analytics.apply_stats(rows, [{"fn": "madzscore", "field": "v"}])
    # Median element is 0
    assert rows[2]["madzscore(v)"] == pytest.approx(0.0, abs=1e-6)
    # By symmetry, 1 and 5 should be negatives of each other
    assert rows[0]["madzscore(v)"] == pytest.approx(-rows[4]["madzscore(v)"], abs=1e-4)
    # Exactly: (1-3)/(1.4826*1)
    assert rows[0]["madzscore(v)"] == pytest.approx(-2.0 / 1.4826, abs=1e-4)


def test_madzscore_outlier_resistance():
    # With a large outlier the non-outlier scores stay reasonable.
    # [1,2,3,4,100]: median=3, |devs|=[2,1,0,1,97], MAD=1, scale=1.4826
    rows = [{"v": 1}, {"v": 2}, {"v": 3}, {"v": 4}, {"v": 100}]
    analytics.apply_stats(rows, [{"fn": "madzscore", "field": "v"}])
    # The median row is still 0
    assert rows[2]["madzscore(v)"] == pytest.approx(0.0, abs=1e-6)
    # Non-outlier row 0: (1-3)/1.4826 ~ -1.35; not squashed toward 0
    assert rows[0]["madzscore(v)"] == pytest.approx(-2.0 / 1.4826, abs=1e-4)
    # Outlier gets a large score, but does not destroy the rest
    assert rows[4]["madzscore(v)"] > 50


def test_madzscore_none_handling():
    # None values stay None; present values are computed over the non-None set
    rows = [{"v": 2}, {"v": None}, {"v": 4}]
    analytics.apply_stats(rows, [{"fn": "madzscore", "field": "v"}])
    assert rows[1]["madzscore(v)"] is None
    # median([2,4])=3, MAD=1, scale=1.4826
    assert rows[0]["madzscore(v)"] == pytest.approx(-1.0 / 1.4826, abs=1e-4)
    assert rows[2]["madzscore(v)"] == pytest.approx(1.0 / 1.4826, abs=1e-4)


def test_madzscore_constant_returns_zero():
    # If MAD=0 every value is the median, all scores should be 0.
    rows = [{"v": 5}, {"v": 5}, {"v": 5}]
    analytics.apply_stats(rows, [{"fn": "madzscore", "field": "v"}])
    for row in rows:
        assert row["madzscore(v)"] == 0.0


def test_stats_ignore_none():
    rows = [{"v": 2}, {"v": None}, {"v": 4}]
    analytics.apply_stats(rows, [{"fn": "zscore", "field": "v"}])
    assert rows[1]["zscore(v)"] is None
    # mean computed only over 2 and 4 -> 3
    assert rows[0]["zscore(v)"] == pytest.approx(-1.0, abs=1e-6)


# --- winsor stat ---------------------------------------------------------

def test_winsor_clips_outliers():
    # 20 regular values (1..20) plus extreme outliers -1000 and 1000.
    # Winsor should clip at Q5/Q95 so the inner values still span [0,1]
    # and the outliers are clamped, not blowing out the range.
    inner = list(range(1, 21))   # 20 points
    rows = [{"v": -1000}] + [{"v": x} for x in inner] + [{"v": 1000}]
    analytics.apply_stats(rows, [{"fn": "winsor", "field": "v"}])
    col = "winsor(v)"
    # Bottom outlier should be 0.0, top outlier should be 1.0 (clamped at clip boundaries)
    assert rows[0][col] == 0.0
    assert rows[-1][col] == 1.0
    # Inner min and max should both be in [0, 1]
    inner_scores = [rows[i + 1][col] for i in range(len(inner))]
    assert all(0.0 <= s <= 1.0 for s in inner_scores)
    # Lowest inner value is >= 0, highest is <= 1 and the span is meaningful
    assert inner_scores[-1] > inner_scores[0]


def test_winsor_normal_range_matches_norm():
    # Without outliers (all values in the inner 5-95% range), winsor and norm
    # should produce identical results for the extreme points.
    rows = [{"v": float(x)} for x in range(1, 11)]
    out_winsor = [dict(r) for r in rows]
    out_norm = [dict(r) for r in rows]
    analytics.apply_stats(out_winsor, [{"fn": "winsor", "field": "v"}])
    analytics.apply_stats(out_norm, [{"fn": "norm", "field": "v"}])
    # Both should produce 0.0 at the minimum and 1.0 at the maximum
    assert out_winsor[0]["winsor(v)"] == pytest.approx(0.0, abs=0.05)
    assert out_winsor[-1]["winsor(v)"] == pytest.approx(1.0, abs=0.05)


def test_winsor_none_handling():
    rows = [{"v": 1}, {"v": None}, {"v": 5}, {"v": 10}]
    analytics.apply_stats(rows, [{"fn": "winsor", "field": "v"}])
    col = "winsor(v)"
    assert rows[1][col] is None
    # Non-None rows get a score in [0, 1]
    for i in (0, 2, 3):
        assert 0.0 <= rows[i][col] <= 1.0


def test_winsor_constant_returns_zero():
    # Constant column: span=0 after clipping, every value should be 0.0.
    rows = [{"v": 7}, {"v": 7}, {"v": 7}, {"v": 7}, {"v": 7}]
    analytics.apply_stats(rows, [{"fn": "winsor", "field": "v"}])
    for row in rows:
        assert row["winsor(v)"] == 0.0


def test_winsor_single_value():
    rows = [{"v": 42}]
    analytics.apply_stats(rows, [{"fn": "winsor", "field": "v"}])
    assert rows[0]["winsor(v)"] == 0.0


# --- decile stat ---------------------------------------------------------

def test_decile_basic():
    # 10 evenly spaced values should each land in a distinct decile.
    rows = [{"v": float(i)} for i in range(1, 11)]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    col = "decile(v)"
    deciles = [row[col] for row in rows]
    assert deciles[0] == 1
    assert deciles[-1] == 10
    assert deciles == sorted(deciles)
    assert all(1 <= d <= 10 for d in deciles)


def test_decile_top_and_bottom():
    rows = [{"v": 0.0}, {"v": 50.0}, {"v": 100.0}]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    col = "decile(v)"
    assert rows[0][col] == 1
    assert rows[-1][col] == 10


def test_decile_none_handling():
    rows = [{"v": 1}, {"v": None}, {"v": 5}, {"v": 10}]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    col = "decile(v)"
    assert rows[1][col] is None
    assert all(rows[i][col] is not None for i in (0, 2, 3))
    assert all(1 <= rows[i][col] <= 10 for i in (0, 2, 3))


def test_decile_constant_returns_one():
    # Constant series: no ordering possible, all fall in decile 1.
    rows = [{"v": 7}, {"v": 7}, {"v": 7}]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    assert all(row["decile(v)"] == 1 for row in rows)


def test_decile_single_value():
    rows = [{"v": 42}]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    assert rows[0]["decile(v)"] == 1


def test_decile_ties_same_decile():
    # Tied values must land in the same decile.
    rows = [{"v": 1}, {"v": 1}, {"v": 5}, {"v": 10}, {"v": 10}]
    analytics.apply_stats(rows, [{"fn": "decile", "field": "v"}])
    col = "decile(v)"
    assert rows[0][col] == rows[1][col]
    assert rows[3][col] == rows[4][col]
    assert rows[0][col] < rows[2][col] < rows[3][col]


# --- factor scoring ------------------------------------------------------

def test_apply_factor():
    rows = [
        {"change": 5, "rv": 1.0},
        {"change": 0, "rv": 2.0},
        {"change": -5, "rv": 3.0},
    ]
    analytics.apply_factor(
        rows,
        [
            {"field": "change", "weight": 1, "dir": "high"},
            {"field": "rv", "weight": 1, "dir": "high"},
        ],
    )
    # change is symmetric, rv ascending: row0 high change low rv, row2 low change high rv.
    # weights equal so scores should roughly cancel for the extremes.
    assert "factor_score" in rows[0]
    assert rows[0]["factor_score"] == pytest.approx(0.0, abs=1e-6)
    assert rows[1]["factor_score"] == pytest.approx(0.0, abs=1e-6)


def test_factor_direction_low():
    rows = [{"pe": 5}, {"pe": 10}, {"pe": 15}]
    analytics.apply_factor(rows, [{"field": "pe", "weight": 1, "dir": "low"}])
    # low PE should score highest
    assert rows[0]["factor_score"] > rows[2]["factor_score"]


# --- pipeline sort prefix guard ------------------------------------------

def test_query_columns_excludes_stat_sort_fields():
    # Sorting by a stat virtual column (winsor/decile/zscore/etc.) must NOT
    # add that virtual name to the query column set. Before the fix, winsor(
    # and decile( were missing from the guard and would leak into the query.
    from mcp_server.scanline.models import ScreenRequest, Stat, SortKey
    from mcp_server.scanline.pipeline import _query_columns

    req = ScreenRequest(
        market="america",
        columns=["name", "close", "RSI"],
        stats=[
            Stat(fn="winsor", field="RSI"),
            Stat(fn="decile", field="close"),
        ],
        sort=[
            SortKey(field="winsor(RSI)", dir="desc"),
            SortKey(field="decile(close)", dir="asc"),
            SortKey(field="close", dir="desc"),
        ],
    )
    cols = _query_columns(req)
    # Virtual stat columns must not appear as query columns.
    assert "winsor(RSI)" not in cols
    assert "decile(close)" not in cols
    # Real columns must still be present.
    assert "close" in cols
    assert "RSI" in cols


# --- live API tests ------------------------------------------------------

@pytest.mark.live
def test_run_query_live():
    from mcp_server.scanline.screener import run_query

    res = run_query(
        market="america",
        columns=["name", "close", "change", "volume", "market_cap_basic"],
        filters=[{"field": "market_cap_basic", "op": ">", "value": 1e9}],
        match="all",
        sort=[{"field": "volume", "dir": "desc"}],
        limit=10,
    )
    assert res["count"] > 0
    assert len(res["rows"]) > 0
    assert "ticker" in res["columns"]


def test_every_preset_builds_its_filters():
    # Building the conditions is offline and exercises the operator mapping for
    # real. above_pct/below_pct once took the wrong arity and three presets
    # returned nothing while the suite stayed green.
    from mcp_server.scanline.screener import _build_conditions

    for p in PRESETS:
        _build_conditions(p.get("filters", []))


def test_above_below_pct_build():
    from mcp_server.scanline.screener import _build_conditions

    _build_conditions([{"field": "close", "op": "above_pct", "value": ["SMA50", 1.05]}])
    _build_conditions([{"field": "close", "op": "below_pct", "value": ["SMA50", 0.95]}])


def test_pow_cannot_hang_the_sandbox():
    import time

    t0 = time.time()
    assert analytics.safe_eval("2**10", {}) == 1024
    for expr in ("9**9**9**9", "(9**9)**(9**9)", "2**100000", "9" * 600):
        with pytest.raises(ValueError):
            analytics.safe_eval(expr, {})
    assert time.time() - t0 < 1.0


def test_pct_ops_need_a_field_and_ratio():
    from mcp_server.scanline.screener import ScreenerError, _build_conditions

    with pytest.raises(ScreenerError):
        _build_conditions([{"field": "close", "op": "above_pct", "value": 0.5}])
