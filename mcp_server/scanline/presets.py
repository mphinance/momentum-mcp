"""One-click preset scans and factor-scoring presets.

PRESETS use real field ids and the op strings scanline/screener.py understands.
FACTOR_PRESETS feed scanline/analytics.apply_factor.
"""

from __future__ import annotations

# Column sets reused across several presets.
_CORE = ["name", "description", "close", "change", "volume", "market_cap_basic"]
_TECH = ["name", "close", "change", "RSI", "volume", "relative_volume_10d_calc"]
_FUND = ["name", "description", "close", "market_cap_basic", "price_earnings_ttm", "return_on_equity"]


PRESETS: list[dict] = [
    {
        "id": "top_gainers",
        "name": "Top Gainers",
        "description": "Biggest percentage movers up today, liquid names only.",
        "market": "america",
        "columns": _CORE + ["relative_volume_10d_calc"],
        "filters": [
            {"field": "market_cap_basic", "op": ">", "value": 1e8},
            {"field": "volume", "op": ">", "value": 200000},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "top_losers",
        "name": "Top Losers",
        "description": "Biggest percentage movers down today, liquid names only.",
        "market": "america",
        "columns": _CORE + ["relative_volume_10d_calc"],
        "filters": [
            {"field": "market_cap_basic", "op": ">", "value": 1e8},
            {"field": "volume", "op": ">", "value": 200000},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    {
        "id": "unusual_volume",
        "name": "Unusual Volume",
        "description": "Relative volume well above the 10-day average.",
        "market": "america",
        "columns": _CORE + ["relative_volume_10d_calc", "average_volume_10d_calc"],
        "filters": [
            {"field": "relative_volume_10d_calc", "op": ">", "value": 3},
            {"field": "market_cap_basic", "op": ">", "value": 1e8},
        ],
        "match": "all",
        "sort": [{"field": "relative_volume_10d_calc", "dir": "desc"}],
    },
    {
        "id": "rsi_oversold",
        "name": "RSI Oversold",
        "description": "RSI under 30, potential mean-reversion candidates.",
        "market": "america",
        "columns": _TECH,
        "filters": [
            {"field": "RSI", "op": "<", "value": 30},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "RSI", "dir": "asc"}],
    },
    {
        "id": "rsi_overbought",
        "name": "RSI Overbought",
        "description": "RSI above 70, stretched to the upside.",
        "market": "america",
        "columns": _TECH,
        "filters": [
            {"field": "RSI", "op": ">", "value": 70},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "RSI", "dir": "desc"}],
    },
    {
        "id": "high_52w",
        "name": "52-Week Highs",
        "description": "Trading at or very near the 52-week high.",
        "market": "america",
        "columns": _CORE + ["price_52_week_high", "Perf.Y"],
        "filters": [
            {"field": "close", "op": "above_pct", "value": ["price_52_week_high", 0.98]},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "Perf.Y", "dir": "desc"}],
    },
    {
        "id": "low_52w",
        "name": "52-Week Lows",
        "description": "Trading at or very near the 52-week low.",
        "market": "america",
        "columns": _CORE + ["price_52_week_low", "Perf.Y"],
        "filters": [
            {"field": "close", "op": "below_pct", "value": ["price_52_week_low", 1.02]},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "Perf.Y", "dir": "asc"}],
    },
    {
        "id": "gap_ups",
        "name": "Gap Ups",
        "description": "Opened with a sizable gap higher on volume.",
        "market": "america",
        "columns": _CORE + ["gap", "relative_volume_10d_calc"],
        "filters": [
            {"field": "gap", "op": ">", "value": 3},
            {"field": "market_cap_basic", "op": ">", "value": 1e8},
        ],
        "match": "all",
        "sort": [{"field": "gap", "dir": "desc"}],
    },
    {
        "id": "golden_cross",
        "name": "Golden Cross",
        "description": "Price above SMA50 and SMA50 above SMA200, uptrend stack.",
        "market": "america",
        "columns": _CORE + ["SMA50", "SMA200"],
        "filters": [
            {"field": "close", "op": ">", "value": "SMA50"},
            {"field": "SMA50", "op": ">", "value": "SMA200"},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "Perf.3M", "dir": "desc"}],
    },
    {
        "id": "death_cross",
        "name": "Death Cross",
        "description": "Price below SMA50 and SMA50 below SMA200, downtrend stack.",
        "market": "america",
        "columns": _CORE + ["SMA50", "SMA200"],
        "filters": [
            {"field": "close", "op": "<", "value": "SMA50"},
            {"field": "SMA50", "op": "<", "value": "SMA200"},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "Perf.3M", "dir": "asc"}],
    },
    {
        "id": "high_short_interest",
        "name": "High Short Interest",
        "description": "Heavily shorted as a percent of float, squeeze watch.",
        "market": "america",
        # UNAVAILABLE, and unlike the other dead-field cases there is no substitute.
        # short_interest, short_interest_percent_of_float and
        # short_interest_prev_month_pct all validate against the catalog and are
        # ALL null on every sampled US row, so this preset returned 0 rows while
        # reporting success. TradingView does not carry short interest on the
        # no-account delayed tier. Kept rather than deleted so the gap stays
        # visible and nobody re-adds it, and run_preset refuses it up front with
        # this reason instead of running a screen that can only come back empty.
        "unavailable": ("Short interest is not carried on TradingView's no-account "
                        "delayed tier. All three short interest fields are null for "
                        "every row, so this scan can only ever return nothing."),
        "columns": _CORE + ["short_interest_percent_of_float", "relative_volume_10d_calc"],
        "filters": [
            {"field": "short_interest_percent_of_float", "op": ">", "value": 15},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "short_interest_percent_of_float", "dir": "desc"}],
    },
    {
        "id": "dividend_aristocrats",
        "name": "Dividend Aristocrats",
        "description": "Solid yield on a large cap, with the dividend still growing.",
        "market": "america",
        # payout_ratio was the second filter here and it is NULL for every US row.
        # It validates against the catalog, so the field tests passed while the
        # preset returned 0 rows instead of the 988 it should have. Replaced with
        # dividend PER SHARE growth, which is populated (161 of 200 sampled) and is
        # closer to what "aristocrat" means anyway: a rising dividend, not merely a
        # sustainable one.
        "columns": _FUND + ["dividends_yield_current",
                            "dps_common_stock_prim_issue_yoy_growth_fy"],
        "filters": [
            {"field": "dividends_yield_current", "op": ">", "value": 3},
            {"field": "dps_common_stock_prim_issue_yoy_growth_fy", "op": ">", "value": 0},
            {"field": "market_cap_basic", "op": ">", "value": 2e9},
        ],
        "match": "all",
        "sort": [{"field": "dividends_yield_current", "dir": "desc"}],
    },
    {
        "id": "mega_cap_movers",
        "name": "Mega Cap Movers",
        "description": "The largest companies making the biggest moves today.",
        "market": "america",
        "columns": _CORE + ["Perf.W"],
        "filters": [
            {"field": "market_cap_basic", "op": ">", "value": 1e11},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "small_cap_momentum",
        "name": "Small Cap Momentum",
        "description": "Small caps with strong recent momentum and volume.",
        "market": "america",
        "columns": _CORE + ["Perf.1M", "relative_volume_10d_calc"],
        "filters": [
            {"field": "market_cap_basic", "op": "between", "value": [3e8, 2e9]},
            {"field": "Perf.1M", "op": ">", "value": 15},
            {"field": "relative_volume_10d_calc", "op": ">", "value": 1.5},
        ],
        "match": "all",
        "sort": [{"field": "Perf.1M", "dir": "desc"}],
    },
    {
        "id": "value_plays",
        "name": "Value Plays",
        "description": "Low P/E with positive return on equity.",
        "market": "america",
        "columns": _FUND + ["price_book_fq", "debt_to_equity"],
        "filters": [
            {"field": "price_earnings_ttm", "op": "between", "value": [0, 15]},
            {"field": "return_on_equity", "op": ">", "value": 10},
            {"field": "market_cap_basic", "op": ">", "value": 1e9},
        ],
        "match": "all",
        "sort": [{"field": "price_earnings_ttm", "dir": "asc"}],
    },
    {
        "id": "breakouts",
        "name": "Breakouts",
        "description": "Near the 52-week high on elevated relative volume.",
        "market": "america",
        "columns": _CORE + ["price_52_week_high", "relative_volume_10d_calc"],
        "filters": [
            {"field": "close", "op": "above_pct", "value": ["price_52_week_high", 0.95]},
            {"field": "relative_volume_10d_calc", "op": ">", "value": 2},
            {"field": "market_cap_basic", "op": ">", "value": 3e8},
        ],
        "match": "all",
        "sort": [{"field": "relative_volume_10d_calc", "dir": "desc"}],
    },
    {
        "id": "high_beta",
        "name": "High Beta",
        "description": "Most volatile names relative to the market.",
        "market": "america",
        "columns": _CORE + ["beta_1_year", "Volatility.D"],
        "filters": [
            {"field": "beta_1_year", "op": ">", "value": 2},
            {"field": "market_cap_basic", "op": ">", "value": 1e9},
        ],
        "match": "all",
        "sort": [{"field": "beta_1_year", "dir": "desc"}],
    },
    {
        "id": "low_volatility",
        "name": "Low Volatility",
        "description": "Large caps with the calmest daily ranges.",
        "market": "america",
        "columns": _CORE + ["beta_1_year", "Volatility.D"],
        "filters": [
            {"field": "beta_1_year", "op": "between", "value": [0, 0.8]},
            {"field": "market_cap_basic", "op": ">", "value": 1e10},
        ],
        "match": "all",
        "sort": [{"field": "Volatility.D", "dir": "asc"}],
    },
    {
        "id": "oversold_megacaps",
        "name": "Oversold MegaCaps",
        "description": "Giant companies with RSI in oversold territory.",
        "market": "america",
        "columns": _CORE + ["RSI", "Perf.1M"],
        "filters": [
            {"field": "RSI", "op": "<", "value": 40},
            {"field": "market_cap_basic", "op": ">", "value": 5e10},
        ],
        "match": "all",
        "sort": [{"field": "RSI", "dir": "asc"}],
    },
    {
        "id": "crypto_movers",
        "name": "Crypto Movers",
        "description": "Biggest movers among liquid crypto, by 24h change.",
        "market": "crypto",
        # Value.Traded was both the filter and a column here, and it is NULL for
        # every row in the crypto market. It validates (it is a real catalog
        # field), so the field tests passed while the preset silently returned
        # 0 of 39,812 coins. Catalog validity is global, field availability is
        # per market. market_cap_calc is populated here and is also the right
        # gate: filtering on volume instead returns the crypto version of the
        # OTC problem, POLONIEX meme coins priced at $0.00 showing +2,848 percent.
        "columns": ["name", "close", "change", "volume", "market_cap_calc"],
        "filters": [
            {"field": "market_cap_calc", "op": ">", "value": 1e8},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "most_active",
        "name": "Most Active",
        "description": "Highest dollar volume traded today.",
        "market": "america",
        "columns": _CORE + ["Value.Traded", "relative_volume_10d_calc"],
        "filters": [
            {"field": "market_cap_basic", "op": ">", "value": 1e8},
        ],
        "match": "all",
        "sort": [{"field": "volume", "dir": "desc"}],
    },
    {
        "id": "earnings_quality",
        "name": "Earnings Quality",
        "description": "High return on equity with healthy margins.",
        "market": "america",
        "columns": _FUND + ["net_margin", "operating_margin"],
        "filters": [
            {"field": "return_on_equity", "op": ">", "value": 20},
            {"field": "net_margin", "op": ">", "value": 10},
            {"field": "market_cap_basic", "op": ">", "value": 2e9},
        ],
        "match": "all",
        "sort": [{"field": "return_on_equity", "dir": "desc"}],
    },
    # ---- SIGNALS group (Wave 5). Trend, cross, and breakout signals built on
    # moving averages and relative volume. Each carries group "Signals" so the
    # panel renders them under their own header.
    {
        "id": "signal_golden_cross",
        "name": "Golden Cross",
        "description": "SMA50 crossing above SMA200, a classic bullish trend signal.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "volume", "SMA50", "SMA200"],
        "filters": [
            {"field": "SMA50", "op": "crosses_above", "value": "SMA200"},
        ],
        "match": "all",
        "sort": [{"field": "volume", "dir": "desc"}],
    },
    {
        "id": "signal_death_cross",
        "name": "Death Cross",
        "description": "SMA50 crossing below SMA200, a classic bearish trend signal.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "volume", "SMA50", "SMA200"],
        "filters": [
            {"field": "SMA50", "op": "crosses_below", "value": "SMA200"},
        ],
        "match": "all",
        "sort": [{"field": "volume", "dir": "desc"}],
    },
    {
        "id": "signal_stacked_ema_ribbon",
        "name": "Stacked EMA Ribbon",
        "description": "Price above a fully stacked Fibonacci EMA ribbon of 8, 21, 34, 55, and 89 periods.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "EMA8", "EMA21", "EMA34", "EMA55", "EMA89"],
        "filters": [
            {"field": "close", "op": ">", "value": "EMA8"},
            {"field": "EMA8", "op": ">", "value": "EMA21"},
            {"field": "EMA21", "op": ">", "value": "EMA34"},
            {"field": "EMA34", "op": ">", "value": "EMA55"},
            {"field": "EMA55", "op": ">", "value": "EMA89"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "signal_above_all_mas",
        "name": "Above All MAs",
        "description": "Price trading above its SMA20, SMA50, and SMA200, a broad uptrend.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "SMA20", "SMA50", "SMA200"],
        "filters": [
            {"field": "close", "op": ">", "value": "SMA20"},
            {"field": "close", "op": ">", "value": "SMA50"},
            {"field": "close", "op": ">", "value": "SMA200"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "signal_gap_and_go",
        "name": "Gap and Go",
        "description": "Gapping up over 2 percent on elevated relative volume with a green day.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "gap", "relative_volume_10d_calc"],
        "filters": [
            {"field": "gap", "op": ">", "value": 2},
            {"field": "relative_volume_10d_calc", "op": ">", "value": 1.5},
            {"field": "change", "op": ">", "value": 0},
        ],
        "match": "all",
        "sort": [{"field": "gap", "dir": "desc"}],
    },
    {
        "id": "signal_volume_breakout",
        "name": "Volume Breakout",
        "description": "Trading above SMA50 and up over 2 percent on double its average relative volume.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "volume", "relative_volume_10d_calc", "SMA50"],
        "filters": [
            {"field": "relative_volume_10d_calc", "op": ">", "value": 2},
            {"field": "change", "op": ">", "value": 2},
            {"field": "close", "op": ">", "value": "SMA50"},
        ],
        "match": "all",
        "sort": [{"field": "relative_volume_10d_calc", "dir": "desc"}],
    },
    {
        "id": "signal_below_all_mas",
        "name": "Below All MAs",
        "description": "Price trading below its SMA20, SMA50, and SMA200, a broad downtrend.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "SMA20", "SMA50", "SMA200"],
        "filters": [
            {"field": "close", "op": "<", "value": "SMA20"},
            {"field": "close", "op": "<", "value": "SMA50"},
            {"field": "close", "op": "<", "value": "SMA200"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    {
        "id": "signal_ema_ribbon_bear",
        "name": "EMA Ribbon Bear",
        "description": "Price below a fully inverted Fibonacci EMA ribbon of 8, 21, 34, 55, and 89 periods.",
        "market": "america",
        "group": "Signals",
        "columns": ["name", "close", "change", "EMA8", "EMA21", "EMA34", "EMA55", "EMA89"],
        "filters": [
            {"field": "close", "op": "<", "value": "EMA8"},
            {"field": "EMA8", "op": "<", "value": "EMA21"},
            {"field": "EMA21", "op": "<", "value": "EMA34"},
            {"field": "EMA34", "op": "<", "value": "EMA55"},
            {"field": "EMA55", "op": "<", "value": "EMA89"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    # --- MOMENTUM group. Oscillator crosses and flips, all probed live. ---
    {
        "id": "mom_macd_bull_cross",
        "name": "MACD Bull Cross",
        "description": "MACD line just crossed up through its signal line. A fresh momentum flip.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "MACD.macd", "MACD.signal", "MACD.hist"],
        "filters": [{"field": "MACD.macd", "op": "crosses_above", "value": "MACD.signal"}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mom_macd_bear_cross",
        "name": "MACD Bear Cross",
        "description": "MACD line just crossed down through its signal line.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "MACD.macd", "MACD.signal", "MACD.hist"],
        "filters": [{"field": "MACD.macd", "op": "crosses_below", "value": "MACD.signal"}],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    {
        "id": "mom_stoch_bull_oversold",
        "name": "Stochastic Bull Cross",
        "description": "Stochastic K crossing up over D from oversold, under 30. A bounce setup.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "Stoch.K", "Stoch.D"],
        "filters": [
            {"field": "Stoch.K", "op": "crosses_above", "value": "Stoch.D"},
            {"field": "Stoch.K", "op": "<", "value": 30},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mom_stoch_bear_overbought",
        "name": "Stochastic Bear Cross",
        "description": "Stochastic K crossing down under D from overbought, over 70.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "Stoch.K", "Stoch.D"],
        "filters": [
            {"field": "Stoch.K", "op": "crosses_below", "value": "Stoch.D"},
            {"field": "Stoch.K", "op": ">", "value": 70},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    {
        "id": "mom_stochrsi_bull",
        "name": "Stoch RSI Bull Cross",
        "description": "Stochastic RSI K crossing up over D. An early momentum tell.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "Stoch.RSI.K", "Stoch.RSI.D"],
        "filters": [{"field": "Stoch.RSI.K", "op": "crosses_above", "value": "Stoch.RSI.D"}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mom_rsi_reclaim_50",
        "name": "RSI Reclaims 50",
        "description": "RSI just crossed back above 50, momentum turning bullish.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "RSI"],
        "filters": [{"field": "RSI", "op": "crosses_above", "value": 50}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mom_ao_zero_cross",
        "name": "Awesome Osc Zero Cross",
        "description": "Awesome Oscillator just crossed above zero, momentum shifting up.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "AO", "Mom"],
        "filters": [{"field": "AO", "op": "crosses_above", "value": 0}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mom_cci_reversal",
        "name": "CCI Oversold Reversal",
        "description": "CCI crossing back up through minus 100 from oversold.",
        "market": "america",
        "group": "Momentum",
        "columns": ["name", "close", "change", "CCI20"],
        "filters": [{"field": "CCI20", "op": "crosses_above", "value": -100}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    # --- TREND group. Trend, VWAP, and rating signals. ---
    {
        "id": "trend_vwap_reclaim",
        "name": "VWAP Reclaim",
        "description": "Price just crossed back above its VWAP. An intraday strength flip.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "VWAP", "volume"],
        "filters": [{"field": "close", "op": "crosses_above", "value": "VWAP"}],
        "match": "all",
        "sort": [{"field": "volume", "dir": "desc"}],
    },
    {
        "id": "trend_ema_8_21_bull",
        "name": "EMA 8/21 Bull Cross",
        "description": "Fast EMA8 crossing up over EMA21. The quick ribbon flip.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "EMA8", "EMA21"],
        "filters": [{"field": "EMA8", "op": "crosses_above", "value": "EMA21"}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "trend_adx_strong_bull",
        "name": "ADX Strong Uptrend",
        "description": "ADX over 25 with plus DI above minus DI. A strong, directional uptrend.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "ADX", "ADX+DI", "ADX-DI"],
        "filters": [
            {"field": "ADX", "op": ">", "value": 25},
            {"field": "ADX+DI", "op": ">", "value": "ADX-DI"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "trend_sar_bull",
        "name": "Parabolic SAR Bull",
        "description": "Price trading above its Parabolic SAR, trend flipped up.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "P.SAR"],
        "filters": [{"field": "close", "op": ">", "value": "P.SAR"}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "trend_williams_oversold",
        "name": "Williams Percent R Oversold",
        "description": "Williams Percent R under minus 80, deeply oversold.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "W.R"],
        "filters": [{"field": "W.R", "op": "<", "value": -80}],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "trend_strong_buy_rating",
        "name": "TradingView Strong Buy",
        "description": "TradingView technical rating in strong buy territory, over 0.5.",
        "market": "america",
        "group": "Trend",
        "columns": ["name", "close", "change", "Recommend.All", "Recommend.MA", "Recommend.Other"],
        "filters": [{"field": "Recommend.All", "op": ">", "value": 0.5}],
        "match": "all",
        "sort": [{"field": "Recommend.All", "dir": "desc"}],
    },
    # --- MULTI-TIMEFRAME group. Alignment across daily, weekly, monthly. ---
    {
        "id": "mtf_rsi_triple_bull",
        "name": "Triple Screen Bull",
        "description": "RSI above 50 on daily, weekly, and monthly all at once. Trend aligned up.",
        "market": "america",
        "group": "Multi-Timeframe",
        "columns": ["name", "close", "change", "RSI", "RSI|1W", "RSI|1M"],
        "filters": [
            {"field": "RSI", "op": ">", "value": 50},
            {"field": "RSI|1W", "op": ">", "value": 50},
            {"field": "RSI|1M", "op": ">", "value": 50},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
    {
        "id": "mtf_rsi_triple_bear",
        "name": "Triple Screen Bear",
        "description": "RSI under 50 on daily, weekly, and monthly all at once. Trend aligned down.",
        "market": "america",
        "group": "Multi-Timeframe",
        "columns": ["name", "close", "change", "RSI", "RSI|1W", "RSI|1M"],
        "filters": [
            {"field": "RSI", "op": "<", "value": 50},
            {"field": "RSI|1W", "op": "<", "value": 50},
            {"field": "RSI|1M", "op": "<", "value": 50},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "asc"}],
    },
    {
        "id": "mtf_macd_daily_weekly",
        "name": "MACD Daily and Weekly Bull",
        "description": "MACD above its signal on both the daily and the weekly. Momentum aligned up.",
        "market": "america",
        "group": "Multi-Timeframe",
        "columns": ["name", "close", "change", "MACD.macd", "MACD.signal", "MACD.macd|1W", "MACD.signal|1W"],
        "filters": [
            {"field": "MACD.macd", "op": ">", "value": "MACD.signal"},
            {"field": "MACD.macd|1W", "op": ">", "value": "MACD.signal|1W"},
        ],
        "match": "all",
        "sort": [{"field": "change", "dir": "desc"}],
    },
]


FACTOR_PRESETS: list[dict] = [
    {
        "id": "momentum",
        "name": "Momentum",
        "weights": [
            {"field": "Perf.1M", "weight": 1.0, "dir": "high"},
            {"field": "Perf.3M", "weight": 1.0, "dir": "high"},
            {"field": "Perf.6M", "weight": 0.5, "dir": "high"},
            {"field": "relative_volume_10d_calc", "weight": 0.5, "dir": "high"},
        ],
    },
    {
        "id": "value",
        "name": "Value",
        "weights": [
            {"field": "price_earnings_ttm", "weight": 1.0, "dir": "low"},
            {"field": "price_book_fq", "weight": 1.0, "dir": "low"},
            {"field": "price_sales_current", "weight": 0.5, "dir": "low"},
            {"field": "dividends_yield_current", "weight": 0.5, "dir": "high"},
        ],
    },
    {
        "id": "quality",
        "name": "Quality",
        "weights": [
            {"field": "return_on_equity", "weight": 1.0, "dir": "high"},
            {"field": "return_on_invested_capital", "weight": 1.0, "dir": "high"},
            {"field": "net_margin", "weight": 0.5, "dir": "high"},
            {"field": "debt_to_equity", "weight": 0.5, "dir": "low"},
        ],
    },
    {
        "id": "growth",
        # Same dead-field class as the preset fixes above, and the worst case of
        # it, because a factor model cannot come back empty. revenue_growth_ttm_yoy
        # and earnings_per_share_diluted_growth_percent_ttm_yoy are real catalog
        # fields and are null for every US row (0 of 1000 sampled at market cap
        # > $1B). apply_factor scores a missing value as 0, so both weight-1.0
        # terms contributed nothing and "Growth" was a pure gross_margin sort:
        # scores were identical to a gross-margin-only model on 1000 of 1000 rows.
        # It ranked, it reported success, and it was not measuring growth.
        # Replaced with the populated TTM YoY analogues of the same two measures.
        "name": "Growth",
        "weights": [
            {"field": "total_revenue_yoy_growth_ttm", "weight": 1.0, "dir": "high"},
            {"field": "earnings_per_share_diluted_yoy_growth_ttm", "weight": 1.0, "dir": "high"},
            {"field": "gross_margin", "weight": 0.5, "dir": "high"},
        ],
    },
    {
        "id": "low_vol",
        "name": "Low-Vol",
        "weights": [
            {"field": "beta_1_year", "weight": 1.0, "dir": "low"},
            {"field": "Volatility.D", "weight": 1.0, "dir": "low"},
            {"field": "Volatility.M", "weight": 0.5, "dir": "low"},
        ],
    },
]


# ---------------------------------------------------------------------------
# OTC guard
#
# Excluding OTC is a DATA QUALITY decision, not a view on company size.
#
# Measured 2026-08-02: trend_adx_strong_bull returned 2,423 matches, and all 25
# of the top rows were OTC, 24 of them under $1, every one with a blank company
# name, carrying "changes" like +9,900 percent. That figure is a $0.0001 tick
# rendered as a percentage, not a move. An ADX or RSI reading on a name like that
# is computed from noise, so the scan is not finding risky opportunities, it is
# finding arithmetic. Excluding the venue drops the count to 1,262 and the top
# rows become real listed names (FCUV, REPL, TCX).
#
# Only four exchange values exist in market=america: OTC, NYSE, NASDAQ and AMEX.
# So a single not_in ["OTC"] is exact. Deliberately NOT an allow list of the
# other three, which would silently drop a venue TradingView adds later. And
# deliberately no market cap or share price floor: a $2 NASDAQ small cap is a
# legitimate result and stays in.
#
# Only presets with no existing floor are guarded. small_cap_momentum already
# bounds market cap at $300M and is left alone. crypto_movers is a crypto market
# preset where a US exchange filter would be meaningless.
#
# Lift it per call with run_preset(..., include_otc=True).
# ---------------------------------------------------------------------------
NO_OTC: dict = {"field": "exchange", "op": "not_in", "value": ["OTC"]}

OTC_GUARDED: set[str] = {
    "signal_golden_cross",
    "signal_death_cross",
    "signal_stacked_ema_ribbon",
    "signal_above_all_mas",
    "signal_gap_and_go",
    "signal_volume_breakout",
    "signal_below_all_mas",
    "signal_ema_ribbon_bear",
    "mom_macd_bull_cross",
    "mom_macd_bear_cross",
    "mom_stoch_bull_oversold",
    "mom_stoch_bear_overbought",
    "mom_stochrsi_bull",
    "mom_rsi_reclaim_50",
    "mom_ao_zero_cross",
    "mom_cci_reversal",
    "trend_vwap_reclaim",
    "trend_ema_8_21_bull",
    "trend_adx_strong_bull",
    "trend_sar_bull",
    "trend_williams_oversold",
    "trend_strong_buy_rating",
    "mtf_rsi_triple_bull",
    "mtf_rsi_triple_bear",
    "mtf_macd_daily_weekly",
}


def _apply_otc_guard() -> None:
    """Append NO_OTC to every guarded preset, once, at import.

    Every guarded preset uses match "all", so the added condition RESTRICTS the
    result. It would WIDEN it under match "any", which is why the set above is
    explicit rather than computed: a preset added later with match "any" must not
    pick this up by accident. The test suite asserts the set stays correct.
    """
    for p in PRESETS:
        if p["id"] not in OTC_GUARDED:
            continue
        if any(f.get("field") == "exchange" for f in p.get("filters", []) or []):
            continue
        p["filters"] = [*p.get("filters", []), dict(NO_OTC)]
        p["otc_guarded"] = True


_apply_otc_guard()
