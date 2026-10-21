"""Curated field catalog for the screener.

Every entry uses a real tradingview-screener field id, smoke-tested live.
FIELDS is the master list the frontend renders. Each dict has:
  id    real tradingview field id
  label human friendly name
  group one of the GROUPS below
  type  one of {"num","pct","price","int","str","bignum"}
  unit  display unit like "$", "%", "x", or ""
"""

from __future__ import annotations

GROUPS = [
    "Identity",
    "Price",
    "Performance",
    "Valuation",
    "Profitability",
    "Dividends",
    "Balance Sheet",
    "Income",
    "Technicals",
    "MovingAverages",
    "Oscillators",
    "Volume",
    "Volatility",
    "Other",
]


def _f(id_: str, label: str, group: str, type_: str, unit: str = "") -> dict:
    """Build a field catalog entry."""
    return {"id": id_, "label": label, "group": group, "type": type_, "unit": unit}


# The catalog. Grouped for readability, flattened into FIELDS at the end.

_IDENTITY = [
    _f("name", "Ticker", "Identity", "str"),
    _f("description", "Company", "Identity", "str"),
    _f("sector", "Sector", "Identity", "str"),
    _f("industry", "Industry", "Identity", "str"),
    _f("country", "Country", "Identity", "str"),
    _f("exchange", "Exchange", "Identity", "str"),
    _f("type", "Type", "Identity", "str"),
    _f("subtype", "Subtype", "Identity", "str"),
    _f("currency", "Currency", "Identity", "str"),
    _f("number_of_employees", "Employees", "Identity", "int"),
    _f("fundamental_currency_code", "Reporting Currency", "Identity", "str"),
]

_PRICE = [
    _f("close", "Price", "Price", "price", "$"),
    _f("open", "Open", "Price", "price", "$"),
    _f("high", "High", "Price", "price", "$"),
    _f("low", "Low", "Price", "price", "$"),
    _f("change", "Change %", "Price", "pct", "%"),
    _f("change_abs", "Change", "Price", "price", "$"),
    _f("change|1W", "Change 1W %", "Price", "pct", "%"),
    _f("change|1M", "Change 1M %", "Price", "pct", "%"),
    _f("gap", "Gap %", "Price", "pct", "%"),
    _f("premarket_change", "Premarket Chg %", "Price", "pct", "%"),
    _f("postmarket_change", "Postmarket Chg %", "Price", "pct", "%"),
    _f("premarket_close", "Premarket Price", "Price", "price", "$"),
    _f("postmarket_close", "Postmarket Price", "Price", "price", "$"),
    _f("VWAP", "VWAP", "Price", "price", "$"),
    _f("price_52_week_high", "52W High", "Price", "price", "$"),
    _f("price_52_week_low", "52W Low", "Price", "price", "$"),
    _f("High.1M", "High 1M", "Price", "price", "$"),
    _f("Low.1M", "Low 1M", "Price", "price", "$"),
    _f("High.3M", "High 3M", "Price", "price", "$"),
    _f("Low.3M", "Low 3M", "Price", "price", "$"),
    _f("High.6M", "High 6M", "Price", "price", "$"),
    _f("Low.6M", "Low 6M", "Price", "price", "$"),
    _f("High.All", "All-Time High", "Price", "price", "$"),
    _f("Low.All", "All-Time Low", "Price", "price", "$"),
    _f("Pivot.M.Classic.Middle", "Pivot M Classic", "Price", "price", "$"),
    _f("Pivot.M.Classic.R1", "Pivot R1", "Price", "price", "$"),
    _f("Pivot.M.Classic.S1", "Pivot S1", "Price", "price", "$"),
]

_PERFORMANCE = [
    _f("Perf.W", "Perf Week %", "Performance", "pct", "%"),
    _f("Perf.1M", "Perf 1M %", "Performance", "pct", "%"),
    _f("Perf.3M", "Perf 3M %", "Performance", "pct", "%"),
    _f("Perf.6M", "Perf 6M %", "Performance", "pct", "%"),
    _f("Perf.Y", "Perf Year %", "Performance", "pct", "%"),
    _f("Perf.YTD", "Perf YTD %", "Performance", "pct", "%"),
    _f("Perf.5Y", "Perf 5Y %", "Performance", "pct", "%"),
    _f("Perf.10Y", "Perf 10Y %", "Performance", "pct", "%"),
    _f("Perf.All", "Perf All %", "Performance", "pct", "%"),
    _f("Volatility.D", "Volatility Day", "Performance", "pct", "%"),
    _f("beta_1_year", "Beta 1Y", "Performance", "num", "x"),
    _f("beta_3_year", "Beta 3Y", "Performance", "num", "x"),
    _f("beta_5_year", "Beta 5Y", "Performance", "num", "x"),
]

_VALUATION = [
    _f("market_cap_basic", "Market Cap", "Valuation", "bignum", "$"),
    _f("market_cap_calc", "Market Cap (calc)", "Valuation", "bignum", "$"),
    _f("price_earnings_ttm", "P/E", "Valuation", "num", "x"),
    _f("price_earnings_growth_ttm", "PEG", "Valuation", "num", "x"),
    _f("price_sales_current", "P/S", "Valuation", "num", "x"),
    _f("price_book_fq", "P/B", "Valuation", "num", "x"),
    _f("price_book_ratio", "Price/Book", "Valuation", "num", "x"),
    _f("price_free_cash_flow_ttm", "P/FCF", "Valuation", "num", "x"),
    _f("price_cash_flow_ttm", "P/CF", "Valuation", "num", "x"),
    _f("enterprise_value_current", "Enterprise Value", "Valuation", "bignum", "$"),
    _f("enterprise_value_ebitda_ttm", "EV/EBITDA", "Valuation", "num", "x"),
    _f("enterprise_value_to_revenue_ttm", "EV/Revenue", "Valuation", "num", "x"),
    _f("earnings_per_share_basic_ttm", "EPS Basic TTM", "Valuation", "num", "$"),
    _f("earnings_per_share_diluted_ttm", "EPS Diluted TTM", "Valuation", "num", "$"),
    _f("earnings_per_share_forecast_next_fq", "EPS Next Q Est", "Valuation", "num", "$"),
    _f("book_value_per_share_fq", "Book Value / Share", "Valuation", "num", "$"),
]

_PROFITABILITY = [
    _f("gross_margin", "Gross Margin %", "Profitability", "pct", "%"),
    _f("operating_margin", "Operating Margin %", "Profitability", "pct", "%"),
    _f("net_margin", "Net Margin %", "Profitability", "pct", "%"),
    _f("pre_tax_margin", "Pre-Tax Margin %", "Profitability", "pct", "%"),
    _f("free_cash_flow_margin_ttm", "FCF Margin %", "Profitability", "pct", "%"),
    _f("return_on_equity", "ROE %", "Profitability", "pct", "%"),
    _f("return_on_assets", "ROA %", "Profitability", "pct", "%"),
    _f("return_on_invested_capital", "ROIC %", "Profitability", "pct", "%"),
    _f("research_and_dev_ratio_ttm", "R&D / Revenue %", "Profitability", "pct", "%"),
    _f("ebitda", "EBITDA", "Profitability", "bignum", "$"),
    _f("gross_profit", "Gross Profit", "Profitability", "bignum", "$"),
    _f("operating_income", "Operating Income", "Profitability", "bignum", "$"),
]

_DIVIDENDS = [
    _f("dividends_yield_current", "Dividend Yield %", "Dividends", "pct", "%"),
    _f("dividends_yield", "Div Yield (TTM) %", "Dividends", "pct", "%"),
    _f("dps_common_stock_prim_issue_fy", "DPS FY", "Dividends", "num", "$"),
    _f("dividends_per_share_fq", "DPS FQ", "Dividends", "num", "$"),
    _f("payout_ratio", "Payout Ratio %", "Dividends", "pct", "%"),
    _f("dividends_paid", "Dividends Paid", "Dividends", "bignum", "$"),
    _f("continuous_dividend_payout", "Yrs Div Paid", "Dividends", "int"),
    _f("continuous_dividend_growth", "Yrs Div Growth", "Dividends", "int"),
]

_BALANCE = [
    _f("total_assets", "Total Assets", "Balance Sheet", "bignum", "$"),
    _f("total_debt", "Total Debt", "Balance Sheet", "bignum", "$"),
    _f("total_liabilities_fq", "Total Liabilities", "Balance Sheet", "bignum", "$"),
    _f("total_equity_fq", "Total Equity", "Balance Sheet", "bignum", "$"),
    _f("cash_n_short_term_invest_fq", "Cash & ST Invest", "Balance Sheet", "bignum", "$"),
    _f("debt_to_equity", "Debt / Equity", "Balance Sheet", "num", "x"),
    _f("current_ratio", "Current Ratio", "Balance Sheet", "num", "x"),
    _f("quick_ratio", "Quick Ratio", "Balance Sheet", "num", "x"),
    _f("long_term_debt_to_assets_fq", "LT Debt / Assets", "Balance Sheet", "num", "x"),
    _f("net_debt", "Net Debt", "Balance Sheet", "bignum", "$"),
    _f("total_current_assets", "Total Current Assets", "Balance Sheet", "bignum", "$"),
    _f("goodwill", "Goodwill", "Balance Sheet", "bignum", "$"),
]

_INCOME = [
    _f("total_revenue", "Revenue TTM", "Income", "bignum", "$"),
    _f("revenue_per_share_ttm", "Revenue / Share", "Income", "num", "$"),
    _f("net_income", "Net Income", "Income", "bignum", "$"),
    _f("revenue_growth_ttm_yoy", "Revenue Growth YoY %", "Income", "pct", "%"),
    _f("earnings_per_share_diluted_growth_percent_ttm_yoy", "EPS Growth YoY %", "Income", "pct", "%"),
    _f("revenue_growth_fq_yoy", "Revenue Growth FQ %", "Income", "pct", "%"),
    _f("gross_profit_ttm", "Gross Profit TTM", "Income", "bignum", "$"),
    _f("total_revenue_yoy_growth_fy", "Revenue Growth FY %", "Income", "pct", "%"),
]

_TECHNICALS = [
    _f("Recommend.All", "Tech Rating", "Technicals", "num"),
    _f("Recommend.MA", "MA Rating", "Technicals", "num"),
    _f("Recommend.Other", "Oscillator Rating", "Technicals", "num"),
    _f("recommendation_mark", "Analyst Mark", "Technicals", "num"),
    _f("ATR", "ATR", "Technicals", "num"),
    _f("ADX", "ADX", "Technicals", "num"),
    _f("ADX+DI", "ADX +DI", "Technicals", "num"),
    _f("ADX-DI", "ADX -DI", "Technicals", "num"),
    _f("Aroon.Up", "Aroon Up", "Technicals", "num"),
    _f("Aroon.Down", "Aroon Down", "Technicals", "num"),
    _f("Ichimoku.BLine", "Ichimoku Base", "Technicals", "num"),
    _f("P.SAR", "Parabolic SAR", "Technicals", "num"),
]

_MOVINGAVG = [
    # Full standard + Fibonacci period set. All probed live and confirmed to
    # return data, so the EMA ribbon signals (8/21/34/55/89) display correctly.
    _f("SMA5", "SMA 5", "MovingAverages", "price", "$"),
    _f("SMA8", "SMA 8", "MovingAverages", "price", "$"),
    _f("SMA10", "SMA 10", "MovingAverages", "price", "$"),
    _f("SMA13", "SMA 13", "MovingAverages", "price", "$"),
    _f("SMA20", "SMA 20", "MovingAverages", "price", "$"),
    _f("SMA21", "SMA 21", "MovingAverages", "price", "$"),
    _f("SMA30", "SMA 30", "MovingAverages", "price", "$"),
    _f("SMA34", "SMA 34", "MovingAverages", "price", "$"),
    _f("SMA50", "SMA 50", "MovingAverages", "price", "$"),
    _f("SMA55", "SMA 55", "MovingAverages", "price", "$"),
    _f("SMA89", "SMA 89", "MovingAverages", "price", "$"),
    _f("SMA100", "SMA 100", "MovingAverages", "price", "$"),
    _f("SMA200", "SMA 200", "MovingAverages", "price", "$"),
    _f("EMA5", "EMA 5", "MovingAverages", "price", "$"),
    _f("EMA8", "EMA 8", "MovingAverages", "price", "$"),
    _f("EMA10", "EMA 10", "MovingAverages", "price", "$"),
    _f("EMA13", "EMA 13", "MovingAverages", "price", "$"),
    _f("EMA20", "EMA 20", "MovingAverages", "price", "$"),
    _f("EMA21", "EMA 21", "MovingAverages", "price", "$"),
    _f("EMA30", "EMA 30", "MovingAverages", "price", "$"),
    _f("EMA34", "EMA 34", "MovingAverages", "price", "$"),
    _f("EMA50", "EMA 50", "MovingAverages", "price", "$"),
    _f("EMA55", "EMA 55", "MovingAverages", "price", "$"),
    _f("EMA89", "EMA 89", "MovingAverages", "price", "$"),
    _f("EMA100", "EMA 100", "MovingAverages", "price", "$"),
    _f("EMA200", "EMA 200", "MovingAverages", "price", "$"),
    _f("BB.upper", "Bollinger Upper", "MovingAverages", "price", "$"),
    _f("BB.lower", "Bollinger Lower", "MovingAverages", "price", "$"),
    _f("BB.basis", "Bollinger Basis", "MovingAverages", "price", "$"),
    _f("HullMA9", "Hull MA 9", "MovingAverages", "price", "$"),
    _f("VWMA", "VWMA", "MovingAverages", "price", "$"),
]

_OSCILLATORS = [
    _f("RSI", "RSI 14", "Oscillators", "num"),
    _f("RSI7", "RSI 7", "Oscillators", "num"),
    _f("Stoch.K", "Stochastic %K", "Oscillators", "num"),
    _f("Stoch.D", "Stochastic %D", "Oscillators", "num"),
    _f("Stoch.RSI.K", "Stoch RSI %K", "Oscillators", "num"),
    _f("Stoch.RSI.D", "Stoch RSI %D", "Oscillators", "num"),
    _f("CCI20", "CCI 20", "Oscillators", "num"),
    _f("MACD.macd", "MACD", "Oscillators", "num"),
    _f("MACD.signal", "MACD Signal", "Oscillators", "num"),
    _f("MACD.hist", "MACD Hist", "Oscillators", "num"),
    _f("Mom", "Momentum", "Oscillators", "num"),
    _f("AO", "Awesome Osc", "Oscillators", "num"),
    _f("W.R", "Williams %R", "Oscillators", "num"),
    _f("UO", "Ultimate Osc", "Oscillators", "num"),
    _f("ROC", "Rate of Change", "Oscillators", "num"),
]

_VOLUME = [
    _f("volume", "Volume", "Volume", "bignum"),
    _f("average_volume_10d_calc", "Avg Vol 10D", "Volume", "bignum"),
    _f("average_volume_30d_calc", "Avg Vol 30D", "Volume", "bignum"),
    _f("average_volume_60d_calc", "Avg Vol 60D", "Volume", "bignum"),
    _f("average_volume_90d_calc", "Avg Vol 90D", "Volume", "bignum"),
    _f("relative_volume_10d_calc", "Rel Volume", "Volume", "num", "x"),
    _f("Value.Traded", "Dollar Volume", "Volume", "bignum", "$"),
    _f("volume_change", "Volume Change %", "Volume", "pct", "%"),
    _f("float_shares_outstanding_current", "Float Shares", "Volume", "bignum"),
    _f("total_shares_outstanding_current", "Shares Outstanding", "Volume", "bignum"),
    _f("float_shares_percent_current", "Float %", "Volume", "pct", "%"),
    _f("short_interest_prev_month_pct", "Short Interest %", "Volume", "pct", "%"),
    _f("short_interest_percent_of_float", "Short % of Float", "Volume", "pct", "%"),
    _f("short_interest", "Short Interest", "Volume", "bignum"),
]

_VOLATILITY = [
    _f("Volatility.W", "Volatility Week", "Volatility", "pct", "%"),
    _f("Volatility.M", "Volatility Month", "Volatility", "pct", "%"),
    _f("ATRP", "ATR %", "Volatility", "pct", "%"),
    _f("average_true_range", "Average True Range", "Volatility", "num"),
]

_OTHER = [
    _f("earnings_release_next_date", "Next Earnings", "Other", "int"),
    _f("earnings_release_date", "Last Earnings", "Other", "int"),
    _f("days_to_earnings", "Days to Earnings", "Other", "int"),
    _f("dividends_ex_date_upcoming", "Next Ex-Div", "Other", "int"),
    _f("indexes", "Index Membership", "Other", "str"),
    _f("is_primary", "Primary Listing", "Other", "str"),
    _f("active_symbol", "Active", "Other", "str"),
]

# The hand curated, nicely labelled and grouped fields. These lead the catalog.
_CURATED: list[dict] = (
    _IDENTITY
    + _PRICE
    + _PERFORMANCE
    + _VALUATION
    + _PROFITABILITY
    + _DIVIDENDS
    + _BALANCE
    + _INCOME
    + _TECHNICALS
    + _MOVINGAVG
    + _OSCILLATORS
    + _VOLUME
    + _VOLATILITY
    + _OTHER
)

# The complete field universe, generated from TradingView's live metainfo by
# scanline/gen_fields.py (committed as fields_all.json, no runtime network call).
# Curated entries win on id, so common fields keep their friendly labels and the
# remaining ~850 base fields are still searchable in the column picker.
import json as _json
from pathlib import Path as _Path

_curated_ids = {f["id"] for f in _CURATED}
_curated_groups = {f["group"] for f in _CURATED}
try:
    _all = _json.loads((_Path(__file__).parent / "fields_all.json").read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    _all = []
# Bucket non curated fields the catalog already names under a "More: <group>"
# label so they do not crowd the curated groups but stay grouped and findable.
_extra: list[dict] = []
for _f in _all:
    if _f["id"] in _curated_ids:
        continue
    g = _f["group"]
    _f = dict(_f)
    _f["group"] = g if g not in _curated_groups else f"More {g}"
    _extra.append(_f)

FIELDS: list[dict] = _CURATED + _extra

# Fast lookup id -> meta. Covers the full universe, so any real field validates.
field_index: dict[str, dict] = {f["id"]: f for f in FIELDS}

# Curated subset (ids) for callers that want only the friendly set.
CURATED_IDS: set[str] = _curated_ids


# Timeframe suffixes the upstream scanner accepts on price and technical fields,
# e.g. "RSI|1W", "close|60". All probed live and confirmed to return data.
TIMEFRAME_SUFFIXES: set[str] = {"1", "5", "15", "30", "60", "120", "240", "1W", "1M"}


def validate_field(field_id: str) -> bool:
    """Return True when field_id is a catalog field, or a catalog field carrying
    a known timeframe suffix like "RSI|1W"."""
    if field_id in field_index:
        return True
    if "|" in field_id:
        base, _, suffix = field_id.partition("|")
        return base in field_index and suffix in TIMEFRAME_SUFFIXES
    return False


def field_type(field_id: str) -> str | None:
    """Catalog type of a field (num, pct, price, int, str, bignum), or None when
    the id is unknown. A timeframe suffix is stripped first."""
    base = field_id.partition("|")[0] if "|" in field_id else field_id
    entry = field_index.get(base)
    return entry["type"] if entry else None


# Sensible default columns per market kind. Stocks get fundamentals,
# crypto and forex get the price-and-volume subset that actually populates.
DEFAULT_COLUMNS: dict[str, list[str]] = {
    "america": [
        "name", "description", "close", "change", "volume",
        "market_cap_basic", "price_earnings_ttm", "sector",
    ],
    # Value.Traded is a real catalog field but is null for every crypto row, so
    # it was a permanently blank column on the default crypto view. Same reason
    # it came out of the crypto_movers preset.
    "crypto": ["name", "close", "change", "volume", "market_cap_calc"],
    "forex": ["name", "close", "change", "high", "low"],
    "futures": ["name", "close", "change", "volume", "high", "low"],
    "bond": ["name", "close", "change", "high", "low"],
    "cfd": ["name", "close", "change", "high", "low"],
}

# Field ids that only make sense (or only populate) for certain markets.
# Frontend can use this to grey out irrelevant fields when the market switches.
MARKET_FIELDS: dict[str, list[str]] = {
    "crypto": [
        "name", "close", "open", "high", "low", "change", "change_abs",
        "volume", "market_cap_calc", "Perf.W", "Perf.1M",
        "Perf.YTD", "RSI", "SMA50", "SMA200", "Volatility.D",
    ],
    "forex": [
        "name", "close", "open", "high", "low", "change", "change_abs",
        "RSI", "SMA50", "SMA200", "ATR", "Volatility.D",
    ],
}


def default_columns(market: str) -> list[str]:
    """Default column set for a market, falling back to america."""
    return DEFAULT_COLUMNS.get(market, DEFAULT_COLUMNS["america"])
