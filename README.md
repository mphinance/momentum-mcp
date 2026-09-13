<p align="center">
  <h1 align="center">⚡ momentum-mcp</h1>
  <p align="center">
    <strong>Give your AI agent a Bloomberg terminal.</strong>
    <br/>
    A Model Context Protocol server for quantitative trading analysis.
  </p>
</p>

<p align="center">
  <a href="https://github.com/mphinance/momentum-mcp/stargazers"><img src="https://img.shields.io/github/stars/mphinance/momentum-mcp?style=for-the-badge&color=00e5ff" alt="Stars"></a>
  <a href="#tools"><img src="https://img.shields.io/badge/MCP_Tools-73-ff007f?style=for-the-badge" alt="73 Tools"></a>
  <a href="#connect-your-client"><img src="https://img.shields.io/badge/Works_With-Claude_%7C_Cursor_%7C_Windsurf-8a2be2?style=for-the-badge" alt="Works with Claude, Cursor, Windsurf"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-39ff14?style=for-the-badge" alt="Apache-2.0 License"></a>
</p>

<p align="center">
  <a href="#tools">Tools</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="#connect-your-client">Connect Your Client</a> •
  <a href="#tech-stack">Tech Stack</a> •
  <a href="#license">License</a>
</p>

---

> This toolkit shares its lineage with [Vesper](https://github.com/mphinance/trading-agent), a
> LangGraph trading agent for Webull — its `mcp_server/` is a separate, credential-heavier
> superset (adds a live-account owner mode and, eventually, an order path). This repo stays the
> standalone version: point any MCP host at it, no broker anywhere near it. It's also part of the
> [mphinance AI trading stack](https://github.com/mphinance/alpha-skills) — if you want packaged
> Claude/Gemini Skills (screeners, regime detection, backtesting playbooks) that can call these
> tools instead of a paid API, see [alpha-skills](https://github.com/mphinance/alpha-skills); if
> you want to act on what these tools tell you, see
> [awesome-broker-mcp](https://github.com/mphinance/awesome-broker-mcp) for which broker an AI
> can actually trade through.

## Changelog

**September 13, 2026 — SEC EDGAR + TickerTrace, and a Docs Catch-Up**
- **21 new tools, both credential-free:** `get_sec_filings`, `get_sec_financials`,
  `get_shares_outstanding`, `get_stakes_held` (SEC EDGAR — no API key, just a `SEC_USER_AGENT`
  contact string) and 17 `etf_*` tools (TickerTrace — daily institutional ETF holdings across 71
  funds, deliberately open with no key at all). Neither needs a TraderDaddy Pro subscription.
- Tools table and Claude Desktop instructions were still describing 6-35 tools; corrected to the
  actual 73 now registered in `mcp_server/server.py`, grouped by category.
- Fixed a License section that said MIT while the committed `LICENSE` file is Apache-2.0 — the
  README now matches the file.

**April 26, 2026 — The "Constellation" Update**
- **Massive 35-Tool Expansion:** Merged in the complete Phase 2 toolset from the internal workspace.
- **Options (VoPR™ Engine):** Added `analyze_options_setup`, `find_best_to_sell`, `find_best_to_buy`, and `sweep_setups` for intelligent options grading.
- **Institutional Flow:** Native TraderDaddy integration for real-time unusual activity, Gamma Exposure (GEX), and sector rotation.
- **Backtesting Suite:** 6 technical presets with walk-forward validation and multi-ticker sweeping via `backtest_strategy` and `sweep_strategy`.
- **Macro & Environment:** Added `detect_macro_regime`, `analyze_breadth`, `detect_bubble_risk`, and `get_market_environment`.
- **Knowledge Base:** Added `search_knowledge` RAG tool to search across 139 trading books.

## What Is This?

**momentum-mcp** turns any MCP-compatible AI assistant into a quantitative trading analyst. Instead of copy-pasting tickers into Yahoo Finance and screenshotting charts, your AI agent has access to 73 institutional-grade tools to:

- 🔍 **Screen the entire market** in seconds — find overbought stocks, unusual volume spikes, new 52-week highs
- 📊 **Pull clean OHLCV data** for any ticker, any timeframe — ready for analysis, no CSV wrangling
- 📈 **Compute technical indicators** — RSI, MACD with plain-English interpretation, not just raw numbers
- 🕯️ **Generate professional candlestick charts** — dark-themed with stacked EMA overlays (8/21/34/55/89), volume panels, publication-ready PNGs
- 📰 **Aggregate financial news** from multiple RSS sources in real-time
- 📄 **Extract full article text** from any URL — your agent reads the actual article, not just the headline

All of this happens through the [Model Context Protocol](https://modelcontextprotocol.io/), so your AI assistant calls these tools natively — no API keys, no REST endpoints, no configuration hell.

### Example Chart Output

`generate_chart("NVDA", period="6mo")` → candlestick + volume + stacked EMAs:

![NVDA 6-month chart with EMA overlays](docs/chart_example.png)

## Tools

73 tools across twelve groups:

| Group | Tools |
|---|---|
| **Screening & Data** | `run_stock_screen`, `run_custom_screen`, `get_historical_data`, `get_fundamentals` |
| **Technicals & Charts** | `analyze_technicals` (24 indicators), `get_tv_analysis` (26-indicator TradingView consensus), `generate_chart` |
| **Options — VoPR™ Engine** | `analyze_options_setup`, `find_best_to_sell`, `find_best_to_buy`, `sweep_setups`, `calculate_position_size` |
| **Institutional Flow & Gamma** | `get_market_pulse`, `get_market_stats`, `get_put_call_ratios`, `get_sector_flow`, `get_unusual_activity`, `get_signals`, `get_gex_overview`, `get_earnings_calendar`, `get_earnings_flow`, `get_politician_trades`, `get_alpha_signals` |
| **Pattern Screeners** | `screen_vcp`, `screen_pead`, `screen_canslim`, `analyze_pair` |
| **Market Structure & Regime** | `detect_market_top`, `detect_ftd`, `get_exposure_recommendation`, `get_market_environment`, `detect_macro_regime`, `analyze_breadth`, `analyze_uptrend_participation`, `detect_themes`, `analyze_recent_gap`, `detect_bubble_risk`, `get_momentum_pulse` |
| **Scenario & Modeling** | `analyze_scenario`, `model_price_distribution` |
| **Backtesting** | `backtest_strategy`, `save_strategy`, `list_strategies`, `get_learned_patterns`, `sweep_strategy`, `walk_forward_test` |
| **Knowledge & Journal** | `search_knowledge` (139-book RAG), `log_conviction`, `get_track_record`, `generate_alpha_card` |
| **News** | `fetch_ticker_news`, `extract_article_text` |
| **SEC EDGAR** (no API key — see below) | `get_sec_filings`, `get_sec_financials`, `get_shares_outstanding`, `get_stakes_held` |
| **Institutional ETF Flow** (no API key at all) | `etf_briefing`, `etf_signals`, `etf_institutional_flow`, `etf_institutional_trend`, `etf_holdings_changes`, `etf_divergences`, `etf_layering_patterns`, `etf_sector_flow`, `etf_stock_activity`, `etf_fund_detail`, `etf_list_funds`, `etf_list_tickers`, `etf_income_overview`, `etf_income_fund_detail`, `etf_options_listings`, `etf_signal_performance`, `etf_global_stats` |

**SEC EDGAR needs one free env var**, no subscription: SEC's `User-Agent` header IS the
credential, and it just has to identify you.

```bash
export SEC_USER_AGENT="Your Project Name (contact: you@example.com)"
```

Without it, `get_sec_filings` and friends return a clean `{"error": ...}` naming the missing
variable rather than crashing. The 17 `etf_*` tools need nothing at all —
[api.tickertrace.pro](https://api.tickertrace.pro) is deliberately open, no key or account.

## Quickstart

```bash
git clone https://github.com/mphinance/momentum-mcp.git
cd momentum-mcp
python3 -m venv .venv
source .venv/bin/activate
# Three-step install: pandas-ta hard-pins numba==0.61.2 which doesn't support
# Python 3.14, so we install it separately and override numba.
pip install fastmcp tradingview-screener yfinance pandas mplfinance matplotlib feedparser trafilatura scipy tradingview-ta chromadb requests httpx
pip install pandas-ta --no-deps
pip install 'numba>=0.62'
```

> **Why three steps?** `pandas-ta==0.4.71b0` declares `numba==0.61.2` as a hard dependency, but that `numba` version only supports Python `<3.14`. Installing `pandas-ta` with `--no-deps` skips the pin, and `numba>=0.62` (which has Python 3.14 wheels) is fully compatible at runtime — verified working.

Test that everything works:

```bash
python -c "from mcp_server.server import mcp; print('✓ Ready')"
```

Run the server:

```bash
python -m mcp_server.server
```

## Connect Your Client

### Claude Desktop

The most popular MCP client. Add to your `claude_desktop_config.json` (located at `~/Library/Application Support/Claude/` on macOS or `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "momentum": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/momentum-mcp",
      "env": {
        "VIRTUAL_ENV": "/absolute/path/to/momentum-mcp/.venv",
        "PATH": "/absolute/path/to/momentum-mcp/.venv/bin:$PATH"
      }
    }
  }
}
```

Restart Claude Desktop. You'll see the 🔨 tools icon — click it to verify all 73 tools are loaded.

---

### Cursor

Add to `.cursor/mcp.json` in your project root (or global config at `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "momentum": {
      "command": "/absolute/path/to/momentum-mcp/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/momentum-mcp"
    }
  }
}
```

The tools will be available in Cursor's Agent mode. Ask it to "screen for overbought stocks" or "chart NVDA over the last 6 months."

---

### VS Code + GitHub Copilot

MCP is generally available in GitHub Copilot (VS Code 1.86+). Add to your `.vscode/mcp.json`:

```json
{
  "servers": {
    "momentum": {
      "command": "/absolute/path/to/momentum-mcp/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/momentum-mcp"
    }
  }
}
```

Enable Agent mode in the Copilot Chat panel — momentum tools will appear in the tool picker.

---

### Windsurf

Add to your Windsurf MCP config at `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "momentum": {
      "command": "/absolute/path/to/momentum-mcp/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/momentum-mcp"
    }
  }
}
```

Cascade will automatically discover the tools. Use them in any Windsurf chat.

---

### Cline (VS Code Extension)

Open Cline's MCP settings (gear icon → MCP Servers → "Edit MCP Settings") and add:

```json
{
  "mcpServers": {
    "momentum": {
      "command": "/absolute/path/to/momentum-mcp/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/momentum-mcp"
    }
  }
}
```

Cline will list the tools in its server panel. Enable them and they're ready to use.

---

### Claude Code (CLI)

Add the server directly from the terminal:

```bash
claude mcp add momentum \
  /absolute/path/to/momentum-mcp/.venv/bin/python \
  -m mcp_server.server \
  --cwd /absolute/path/to/momentum-mcp
```

Verify it's connected:

```bash
claude mcp list
```

---

### Any Other MCP Client

momentum-mcp uses the **stdio** transport (the MCP default). Any client that supports stdio can connect by running:

```bash
/path/to/.venv/bin/python -m mcp_server.server
```

That's it. No HTTP server, no ports, no auth — just stdin/stdout.

## Project Structure

```
momentum-mcp/
├── requirements.txt         # Pinned dependencies
├── README.md
└── mcp_server/
    ├── __init__.py
    ├── server.py            # FastMCP entry point — registers all 73 tools
    ├── screener.py          # TradingView stock scanner
    ├── data.py              # yfinance OHLCV with async wrapper
    ├── technicals.py        # pandas-ta indicator suite
    ├── charts.py            # mplfinance candlestick + volume charts
    ├── news.py              # feedparser RSS + trafilatura extraction
    ├── edgar.py             # SEC EDGAR client — no API key, User-Agent only
    ├── edgar_tools.py        # get_sec_filings/financials/shares_outstanding/stakes_held
    ├── tickertrace.py       # api.tickertrace.pro client — no key, no account
    ├── tickertrace_tools.py # 17 etf_* institutional ETF holdings tools
    └── ...                  # options, backtest, screeners, macro/regime, knowledge base
```

## Tech Stack

| Library | Role |
|---|---|
| [FastMCP](https://gofastmcp.com/) | MCP server framework (v3.x) |
| [tradingview-screener](https://pypi.org/project/tradingview-screener/) | Stock screening via TradingView's API |
| [yfinance](https://pypi.org/project/yfinance/) | Yahoo Finance OHLCV data |
| [pandas-ta](https://pypi.org/project/pandas-ta/) | 130+ technical indicators |
| [mplfinance](https://pypi.org/project/mplfinance/) | Financial chart rendering |
| [feedparser](https://pypi.org/project/feedparser/) | RSS/Atom feed parsing |
| [trafilatura](https://pypi.org/project/trafilatura/) | Web article text extraction |
| [requests](https://pypi.org/project/requests/) | SEC EDGAR client (no API key, just a User-Agent) |
| [httpx](https://pypi.org/project/httpx/) | TickerTrace client (api.tickertrace.pro, no key) |

## Example Prompts

Once connected, try asking your AI assistant:

> "Screen the market for stocks with high relative volume today"

> "Get me 6 months of daily data for NVDA and analyze the technicals"

> "Generate a candlestick chart for AAPL over the past year"

> "What's the latest news on TSLA? Pull the full text of the most interesting article."

> "Find oversold stocks, then analyze the technicals on the top 3 results"

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
