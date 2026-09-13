"""SEC EDGAR client — filings sweep, XBRL financials, shares outstanding, and
AS-FILER 13D/13G stakes (a company's own stakes held in OTHER issuers).

Ported from a friend's discount-bloomberg repo. Vesper had zero fundamentals/filings
tooling before this: TDPro and TickerTrace are flow and options data, not SEC filings.

No API key. SEC's User-Agent header IS the credential and must identify a real contact;
a missing one returns a bare HTTP 403 from sec.gov that reads like a bug here rather than
a missing setting, so `_get()` raises a clear RuntimeError instead. SEC asks for <=10
req/s; `_throttle()` enforces ~9/s process-wide.

Public functions, all DataFrames or dicts carrying `source_url` so every number is
citable back to the primary filing:

    cik_for(ticker)                     -> zero-padded CIK
    filings(ticker, months, forms)      -> filing index (the recent-events sweep)
    document(url)                       -> raw filing text
    financials(ticker, periods, annual) -> multi-period XBRL incl. operating cash flow
    shares_outstanding(ticker)          -> cover-page share count (basic + diluted)
    stakes_held(ticker)                 -> AS-FILER 13D/13G: stakes in OTHER companies

Why stakes_held exists: an aggregator will tell you who owns a company, not what a
company itself owns. A 13D/13G is indexed under both the filer and the subject, so
finding "companies we own" requires parsing each document and keeping only the ones
where the reporting person is the ticker being asked about, not the issuer.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

DATA_URL = "https://data.sec.gov"
WWW_URL = "https://www.sec.gov"

MIN_INTERVAL = 0.11  # ~9 req/s, just under SEC's 10/s ceiling
_last_call = 0.0

# In-memory, process-lifetime cache. Filed documents are immutable, so "doc" is cached
# forever within a run; submissions/tickers/facts get a real TTL since new filings
# appear. No disk persistence: Vesper's other data clients (td.py) cache the same way,
# and SEC filings are always re-fetchable, so there is nothing worth persisting across
# process restarts.
_CACHE: dict[str, tuple[float, Any]] = {}
TTL_SEC: dict[str, float | None] = {
    "tickers": 30 * 24 * 3600.0,
    "submissions": 3600.0,       # new filings appear; 13D/A is time-sensitive
    "facts": 7 * 24 * 3600.0,    # financials only change quarterly
    "doc": None,                 # immutable once accepted
}

# XBRL tags, in preference order. SEC filers don't all use the same tag.
TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    # THE accruals metric: net_income >> operating_cash_flow means accrual-driven earnings.
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
}


# --------------------------------------------------------------------------- http


def _throttle() -> None:
    global _last_call
    delta = time.monotonic() - _last_call
    if delta < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - delta)
    _last_call = time.monotonic()


def _get(url: str, kind: str, as_json: bool = True) -> Any:
    """Throttled, UA-declared, TTL-cached GET. `kind` selects the cache policy.

    This is the one seam every function below goes through, and the one tests
    monkeypatch — see tests/test_edgar.py.
    """
    ttl = TTL_SEC.get(kind, 3600.0)
    cached = _CACHE.get(url)
    if cached is not None:
        ts, data = cached
        if ttl is None or (time.monotonic() - ts) < ttl:
            return data

    ua = os.environ.get("SEC_USER_AGENT", "")
    if not ua:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. SEC EDGAR has no API key -- the User-Agent "
            "header IS the credential, and it must identify you with a real contact "
            "address. Add a line to .env like:\n"
            "    SEC_USER_AGENT=Your Project Name (contact: you@example.com)\n"
            "Without it sec.gov returns HTTP 403 for every request."
        )

    _throttle()
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
    r.raise_for_status()
    data = r.json() if as_json else r.text
    _CACHE[url] = (time.monotonic(), data)
    return data


# --------------------------------------------------------------------- ticker/CIK


def cik_for(ticker: str) -> str:
    """Ticker -> zero-padded 10-digit CIK."""
    data = _get(f"{WWW_URL}/files/company_tickers.json", "tickers")
    t = ticker.upper()
    for row in data.values():
        if str(row.get("ticker", "")).upper() == t:
            return str(row["cik_str"]).zfill(10)
    raise ValueError(f"No CIK found for ticker {ticker!r}")


def company_name_for(ticker: str) -> str:
    """Ticker -> registered company name. "" rather than raising: this is for
    display and matching, never for correctness.
    """
    try:
        data = _get(f"{WWW_URL}/files/company_tickers.json", "tickers")
    except Exception:
        return ""
    t = ticker.upper()
    for row in data.values():
        if str(row.get("ticker", "")).upper() == t:
            return str(row.get("title", ""))
    return ""


def _submissions(ticker: str) -> dict:
    cik = cik_for(ticker)
    return _get(f"{DATA_URL}/submissions/CIK{cik}.json", "submissions")


# ------------------------------------------------------------------------ filings


def filings(
    ticker: str,
    months: int = 12,
    forms: list[str] | None = None,
) -> pd.DataFrame:
    """Filing index for any ticker: the recent-events sweep.

    forms: optional filter, e.g. ["8-K", "10-Q"]. Matching is case-insensitive prefix.
    """
    sub = _submissions(ticker)
    recent = sub["filings"]["recent"]
    df = pd.DataFrame(
        {
            "filing_date": recent["filingDate"],
            "form": recent["form"],
            "report_date": recent.get("reportDate"),
            "accession": recent["accessionNumber"],
            "primary_doc": recent["primaryDocument"],
            "items": recent.get("items"),
        }
    )
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    cik_nozero = str(int(sub["cik"]))
    df["source_url"] = [
        f"{WWW_URL}/Archives/edgar/data/{cik_nozero}/{a.replace('-', '')}/{d}"
        # Both columns come from the same DataFrame, so a length mismatch is
        # impossible -- and if EDGAR ever changed that, silently truncating would
        # pair accession numbers with the wrong documents.
        for a, d in zip(df["accession"], df["primary_doc"], strict=True)
    ]
    if months:
        df = df[df["filing_date"] >= datetime.now() - timedelta(days=30 * months)]
    if forms:
        pat = "|".join(re.escape(f) for f in forms)
        df = df[df["form"].str.contains(pat, case=False, na=False)]
    return df.sort_values("filing_date", ascending=False).reset_index(drop=True)


def document(url: str) -> str:
    """Raw text of a filed document (immutable, cached forever within the process)."""
    return _get(url, "doc", as_json=False)


# --------------------------------------------------------------------- financials


def _facts(ticker: str) -> dict:
    cik = cik_for(ticker)
    return _get(f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik}.json", "facts")


def financials(
    ticker: str,
    periods: int = 8,
    annual: bool = True,
) -> pd.DataFrame:
    """Multi-period financials straight from XBRL. Wide: one row per period.

    Includes `operating_cash_flow` alongside `net_income` so the accruals check is
    computable without leaving primary sources: `accrual_gap` = net_income minus
    operating_cash_flow, positive means reported profit exceeds cash generated.
    `annual=False` returns quarterly (10-Q) periods instead.
    """
    facts = _facts(ticker).get("facts", {})
    want_forms = ("10-K",) if annual else ("10-Q",)
    rows: list[dict] = []

    for metric, candidates in TAGS.items():
        for taxonomy in ("us-gaap", "ifrs-full"):
            block = facts.get(taxonomy, {})
            picked = next((c for c in candidates if c in block), None)
            if not picked:
                continue
            for unit_rows in block[picked]["units"].values():
                for f in unit_rows:
                    if f.get("form") not in want_forms:
                        continue
                    rows.append(
                        {
                            "end": f["end"],
                            "start": f.get("start"),
                            "fy": f.get("fy"),
                            "fp": f.get("fp"),
                            "form": f["form"],
                            "metric": metric,
                            "value": f["val"],
                            "accession": f.get("accn"),
                            "xbrl_tag": picked,
                        }
                    )
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Keep the latest-filed value for each (period, metric): restatements supersede.
    df = df.sort_values("accession").drop_duplicates(["end", "metric"], keep="last")
    wide = df.pivot(index="end", columns="metric", values="value").sort_index(ascending=False)
    wide.index = pd.to_datetime(wide.index)
    wide = wide.head(periods)

    if {"net_income", "operating_cash_flow"} <= set(wide.columns):
        wide["accrual_gap"] = wide["net_income"] - wide["operating_cash_flow"]

    cik = cik_for(ticker)
    wide.attrs["source_url"] = f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik}.json"
    return wide


def shares_outstanding(ticker: str) -> dict:
    """Cover-page share count: `dei:EntityCommonStockSharesOutstanding` IS the
    10-Q/10-K cover page number, not an aggregator's derived figure.
    """
    facts = _facts(ticker).get("facts", {})
    out: dict = {"basic_cover_page": None, "diluted_weighted_avg": None, "as_of": None}

    dei = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if dei:
        rows = [f for u in dei["units"].values() for f in u]
        latest = max(rows, key=lambda f: f["end"])
        out["basic_cover_page"] = latest["val"]
        out["as_of"] = latest["end"]
        out["form"] = latest.get("form")
        out["accession"] = latest.get("accn")

    gaap = facts.get("us-gaap", {})
    for tag in (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ):
        if tag in gaap:
            rows = [f for u in gaap[tag]["units"].values() for f in u]
            latest = max(rows, key=lambda f: f["end"])
            val, basic = latest["val"], out["basic_cover_page"]
            # SCALE SANITY CHECK. Filers sometimes switch to reporting share counts in
            # THOUSANDS without changing the `shares` unit tag. Silently returning the
            # wrong one would poison every per-share number downstream, so validate
            # against the cover-page count and say so instead.
            if basic:
                ratio = val / basic
                if 0.3 <= ratio <= 3:
                    out["diluted_weighted_avg"] = val
                elif 0.3 <= ratio * 1000 <= 3:
                    out["diluted_weighted_avg"] = val * 1000
                    out["diluted_scale_warning"] = (
                        f"filer reported {tag} in THOUSANDS ({val:,.0f}); "
                        f"scaled x1000 to reconcile with the cover-page count"
                    )
                else:
                    out["diluted_weighted_avg"] = None
                    out["diluted_scale_warning"] = (
                        f"{tag}={val:,.0f} is implausible vs cover-page basic "
                        f"{basic:,.0f} (ratio {ratio:.4g}), REJECTED, verify manually"
                    )
            else:
                out["diluted_weighted_avg"] = val
            out["diluted_period_end"] = latest["end"]
            break

    cik = cik_for(ticker)
    out["source_url"] = f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik}.json"
    return out


# ------------------------------------------------------------------- as-filer 13D/G

_TAG = re.compile(r"<(\w+)>([^<>]{1,120})</\1>")

# Tag names for the numeric ownership fields, in preference order. SEC's electronic
# Schedule 13D/G XML (schema X0202, effective ~2023) uses `aggregateAmountOwned`,
# `percentOfClass` and `dateOfEvent` -- confirmed live against a real filing
# (GameStop's 13D/A on its eBay stake). Older or amended filings have been seen
# using different names, so the second candidate in each list is kept as a
# fallback rather than assumed dead.
_SHARE_TAGS = ("aggregateamountowned", "reportingpersonbeneficiallyownedaggregatenumberofshares")
_PERCENT_TAGS = ("percentofclass", "classpercent")
_EVENT_DATE_TAGS = ("dateofevent", "eventdaterequiresfilingthisstatement")


def _first(vals: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in vals:
            return vals[k]
    return None


def stakes_held(ticker: str, months: int = 24) -> pd.DataFrame:
    """AS-FILER 13D/13G: stakes this company holds in OTHER public companies.

    EDGAR indexes a 13D/G under BOTH the filer and the subject, so each document
    must be parsed and kept only when the reporting person is the ticker being
    asked about, filed BY it, rather than the issuer, filed ABOUT it.

    Discriminates as-filer from as-subject by CIK, never by name: the same feed
    contains "funds that own us" and "companies we own", and name matching fails
    immediately on cases like ticker KOPN vs entity "KOPIN CORPORATION", which
    share no substring. CIK is exact.
    """
    idx = filings(ticker, months=months, forms=["SC 13D", "SC 13G", "SCHEDULE 13"])
    if idx.empty:
        return pd.DataFrame()

    our_cik = int(cik_for(ticker))
    rows: list[dict] = []
    for _, r in idx.iterrows():
        # EDGAR's primaryDocument for these is the XSL-rendered view (styled HTML,
        # not data). The machine-readable XML lives at the accession root.
        acc = str(r["accession"]).replace("-", "")
        raw_url = f"{WWW_URL}/Archives/edgar/data/{our_cik}/{acc}/primary_doc.xml"
        try:
            xml = document(raw_url)
        except Exception:
            continue
        vals: dict[str, str] = {}
        for m in _TAG.finditer(xml):
            vals.setdefault(m.group(1).lower(), m.group(2).strip())

        reporter = vals.get("reportingpersonname", "")
        issuer = vals.get("issuername", "")
        issuer_cik = vals.get("issuercik")
        if issuer_cik is None:
            continue
        # AS-FILER means the issuer is someone ELSE.
        if int(issuer_cik) == our_cik:
            continue
        rows.append(
            {
                "filing_date": r["filing_date"],
                "form": r["form"],
                "reporting_person": reporter,
                "issuer": issuer,
                "issuer_cik": issuer_cik,
                "cusip": vals.get("issuercusipnumber"),
                "shares": _num(_first(vals, _SHARE_TAGS)),
                "percent_of_class": _num(_first(vals, _PERCENT_TAGS)),
                "event_date": _first(vals, _EVENT_DATE_TAGS),
                "source_url": raw_url,
            }
        )
    return pd.DataFrame(rows)


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
