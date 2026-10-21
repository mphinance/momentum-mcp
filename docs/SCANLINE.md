# SCANLINE tools reference

The `scan_*` tools are the MCP layer of [mphinance/scanline](https://github.com/mphinance/scanline),
a live TradingView market screener with a quant analytics layer. The engine
(`mcp_server/scanline/`: pipeline, analytics, fields, presets, screener) is
vendored here verbatim and `mcp_server/scanline/tools.py` registers it on the
momentum server through `register_scanline_tools(mcp)`, the same way the
TickerTrace tools mount with `etf_`.

Why the prefix: the momentum server already answers "fundamentals", "breadth"
and "sector flow" from yfinance and TraderDaddy. These tools answer the same
questions from TradingView's scanner, and a model choosing by name needs to see
which source it is getting. `scan_fundamentals` and `get_fundamentals` are
different data. Both are useful. Neither should masquerade as the other.

Pure TradingView data, no account needed (data is live and delayed). Read-only.
Nothing here places an order or touches a broker.

## What it adds over `run_stock_screen`

`run_stock_screen` and `run_custom_screen` are the original 22-preset screener
over the same `tradingview-screener` package. The `scan_*` engine is the full
thing:

- **Six markets**, not one: `america`, `crypto`, `forex`, `futures`, `bond`, `cfd`.
- **The whole field universe**, 1000+ ids, searchable with `scan_search_fields`
  and validated on every request. A typo returns `{error, error_code, detail}`
  naming the offender, never a silent empty result.
- **Cross-field filters**: a filter value may be another field id, so
  `{"field":"SMA50","op":"crosses_above","value":"SMA200"}` is a golden cross.
- **Computed columns** from a sandboxed expression engine (`close * volume`,
  `(close - SMA200) / ATR`), **cross-sectional stats** (`zscore`, `pctrank`,
  `rank`, `norm`) and a **direction-aware factor model** with five presets
  (Momentum, Value, Quality, Growth, Low-Vol).
- **47 preset scans** including signal, momentum, trend and multi-timeframe
  groups, each built from real field ids.
- **Market studies** that reduce a broad screen to a structured read instead of
  rows: breadth, sector rotation, new highs and lows, volume leaders, gap
  scanner with fill tracking, earnings radar, dividend quality.
- **Multi-timeframe symbol reads**: `scan_analyze` reports RSI and MACD bias on
  the 1h, 4h, 1d, 1w and 1mo at once with an alignment verdict.

## Tools (28)

Every tool also carries a full docstring, which is what an MCP client actually
reads when choosing between them. The market-study tools below carry the most
detailed ones.

### Screening

- **`scan_screen(market, filters, columns, match, sort, computed, stats, factor, limit, offset)`**
  The full engine. `filters` is a list of `{field, op, value}`; `value` may be a
  number, a list, or another field id (cross-field, so
  `{"field":"SMA50","op":"crosses_above","value":"SMA200"}` is a golden cross).
  `computed` adds derived columns from a sandboxed expression engine, `stats`
  adds `zscore`/`pctrank`/`rank`/`norm` across the result, `factor` is a weighted
  direction-aware composite score. Returns `{count, returned, market, ms,
  columns, rows, table}`.
- **`scan_run_preset(preset_id, limit, include_otc)`** Run one of the 47 named preset
  scans. The 25 signal/momentum/trend/MTF presets exclude OTC by default, because
  an indicator reading on a sub-penny shell is computed from noise: those rows
  carry "changes" like +9,900 percent, which is a $0.0001 tick expressed as a
  percentage. It is a data quality filter only, with NO market cap or share price
  floor, so genuine small and cheap listed names still come back. When the guard
  is on, the response carries an `otc_guard` block reporting how many rows it
  removed, so nothing is dropped silently. Pass `include_otc=True` to lift it.
- **`scan_run_factor_preset(factor_preset_id, market, filters, columns, limit)`** Rank
  a market by a named factor (Momentum, Value, Quality, Growth, Low-Vol).
- **`scan_lookup_symbol(ticker, market, columns)`** One row by exact ticker.

### Discovery

- **`scan_list_markets()`** The six markets (america, crypto, forex, futures, bond, cfd).
- **`scan_search_fields(query, group, limit)`** Search the 1000+ field universe.
- **`scan_list_operators()`** The filter operators and what value each expects.
- **`scan_list_presets(group)`** The preset scans, optionally by group.
- **`scan_list_factor_presets()`** The composite factor presets.
- **`scan_server_stats()`** Server health: calls served, cache hit rate, errors.

### Symbol intelligence

- **`scan_analyze(ticker, market)`** Read a symbol's chart into structured technical
  analysis: trend, momentum, range, rating, and plain-language signals. It is
  **multi-timeframe in one call**, RSI and MACD bias on the 1h, 4h, 1d, 1w, and
  1m at once with an alignment verdict, so nobody has to swap timeframes.
- **`scan_technical_rating(ticker, market, timeframes)`** TradingView's own gauge
  (overall, moving averages, oscillators) as Strong Buy ... Strong Sell, across
  the timeframes you ask for.
- **`scan_compare(tickers, columns, market)`** Several symbols side by side.
- **`scan_search_symbols(query, market, limit)`** Resolve "apple" or "nvda" to rows.
- **`scan_chart(ticker, market, interval, theme)`** A live TradingView chart deep link
  plus a ready-to-embed Advanced Chart widget config.
- **`scan_sector_breakdown(market, filters, limit)`** Aggregate a screen by sector:
  count, average change, total market cap.

### Market studies

Each of these runs one broad screen and reduces it to a structured read, rather
than handing back rows. All take an optional `filters` list you can narrow with,
and all take `include_otc` (see below).

- **`scan_top_movers(market, n, filters, columns, include_otc)`** The top N gainers and
  top N losers in one call.
- **`scan_volume_leaders(market, min_rvol, filters, limit, top, include_otc)`** Names
  trading on unusual volume right now, classified by price direction.
- **`scan_new_highs_lows(market, filters, threshold, limit, include_otc)`** Stocks at or
  near their 52 week highs and lows. A classic breadth gauge.
- **`scan_market_breadth(market, filters, limit, include_otc)`** Advancers and
  decliners, percent above key moving averages, and the RSI distribution.
- **`scan_sector_rotation(market, filters, limit, include_otc)`** Sectors ranked by
  multi-timeframe momentum. A rotation dashboard.
- **`scan_momentum_consistency(market, direction, min_aligned, filters, limit, top, include_otc)`**
  Ranks names by how consistently returns align across five timeframes.
- **`scan_relative_strength_leaders(market, filters, limit, top, include_otc)`** Names
  outperforming their own sector peers, not just the index.
- **`scan_ema_stack_scan(market, min_stack, filters, limit, top, include_otc)`** Ranks by
  EMA and SMA stack alignment. A bull-stack breadth indicator.
- **`scan_gap_scanner(market, min_gap_pct, filters, limit, top, include_otc)`** Names
  that gapped at the open, with intraday fill progress tracked. Samples both
  tails, so gap downs come back as well as gap ups.
- **`scan_earnings_radar(market, horizon, filters, limit, top, include_otc)`** Names
  reporting earnings within the next N days. Company earnings only. This is NOT an
  economic calendar and carries no macro releases.
- **`scan_dividend_screen(market, min_yield, min_years_growing, filters, limit, top, include_otc)`**
  Dividend payers ranked by a composite Dividend Quality Score.

### The OTC guard

On `market="america"` every tool above, plus `scan_run_preset` and
`scan_run_factor_preset`, excludes OTC by default.

It is a data quality filter, not a view on company size. A sub-penny shell that
ticks one hundredth of a cent posts a four figure percentage move, so anything
ranking on change or gap returns those rows and only those rows. Measured
unguarded, `scan_top_movers` came back 10 of 10 OTC on both its gainers and its
losers, and `scan_gap_scanner` 50 of 50 on both sides. The top gain was +334900%,
which is a $0.0001 tick rendered as a percentage.

There is **no market cap and no share price floor**, so a $2 NASDAQ small cap is
still a legitimate result and still comes back. It is a single
`exchange not_in ["OTC"]`, deliberately not an allow list of the other venues,
which would silently drop one TradingView adds later. There are five today:
OTC, NYSE, NASDAQ, AMEX and CBOE.

Nothing is dropped silently: the response carries an `otc_guard` block saying
whether the guard was active. Pass `include_otc=True` to lift it per call. The
guard also stands aside entirely if you supply your own `exchange` filter, and
never applies off `america`, where the venue names are meaningless.

`scan_screen` is deliberately **not** guarded. It is the raw escape hatch, its filters
are entirely yours, and it accepts `match="any"`, where an appended condition
would widen the result rather than restrict it.

## Prompts (4)

Canned workflows the client can launch:

- **`scan_momentum_breakouts(market)`** Find and read the strongest breakouts now.
- **`scan_oversold_quality(market)`** Oversold names that are still high quality.
- **`scan_rank_by_factor(factor, market)`** Rank a market by a factor and read the leaders.
- **`scan_read_symbol(ticker)`** A full plain-language chart read for one symbol.

## Resources (3)

- **`screener://fields`** The full field catalog.
- **`screener://presets`** Every preset and factor preset.
- **`screener://operators`** The operator reference.

## Notes

- A short TTL cache (256 entries max, expired entries swept on write) sits in
  front of the engine to soften TradingView's rate-limit behavior under bursty
  agent use. `scan_server_stats` reports the hit rate. Keys include the data mode,
  never a cookie.
- `limit` is 1 to 1000 on every tool. Unknown filter, column, sort or stat
  fields, bad operators and bad values return `{error, error_code, detail}`
  naming the offender instead of an empty result. Successful screens carry
  `mode` (`delayed`, `realtime`, `cookie_rejected`) and, when relevant,
  `warnings` (for example a factor score withheld on empty columns).
- `scan_technical_rating` rejects unknown timeframes, `scan_compare` returns `not_found`
  for tickers that did not resolve and rejects unknown columns, and
  `scan_earnings_radar` requires an integer `horizon` of 1 to 365.
- Real-time data is opt-in through the server's environment: set
  `SCANLINE_TV_SESSIONID` (and optionally `SCANLINE_TV_SESSIONID_SIGN`) to a
  TradingView `sessionid` cookie. The cookie never reaches a response body, a log
  line or a cache key; successful screens report `mode` so you can tell which feed
  you got. After TradingView rejects a cookie the server stops sending it for ten
  minutes, then tries again.
