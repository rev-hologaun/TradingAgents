"""Local fundamentals data engine — SEC EDGAR APIs + local cache.

Replaces Alpha Vantage for all fundamental data needs. Uses SEC EDGAR's
official JSON APIs to extract financial data without any API keys.

Architecture (per Synapse's recommendation):
1. Ingest: SEC EDGAR Submissions API → structured JSON (no scraping)
2. Process: Parse XBRL/HTML filing content for key financial line items
3. Store: Cache locally (24h for API responses, 48h for parsed fundamentals)
4. Query: Calculate derived metrics by joining with TradeStation prices

Key features:
- Zero API keys required
- 24h API cache, 48h fundamentals cache
- Official SEC EDGAR JSON APIs (Submissions API + XBRL Company Facts)
- XBRL/HTML parsing for 10-K (annual) and 10-Q (quarterly) filings
- Derived metrics: P/E, P/B, Debt/Equity, ROE, FCF, revenue growth, etc.
- Proper rate limiting (1s between SEC requests)
- User-Agent header required by SEC

Modules:
- sec_edgar_client.py: SEC EDGAR API client (Submissions, XBRL, caching)
- This file: Integration layer — fetches, parses, calculates metrics, caches
"""

import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ─── Import SEC EDGAR client ─────────────────────────────────────────────────

from tradingagents.dataflows.sec_edgar_client import (
    SecEdgarClient,
    _format_number as _sec_format_number,
    SEC_CACHE_TTL_HOURS,
    XBRL_CONCEPT_MAP,
)

# ─── Configuration ───────────────────────────────────────────────────────────

_CACHE_DIR = Path(os.environ.get(
    "TRADINGAGENTS_CACHE_DIR",
    os.path.join(os.path.expanduser("~"), ".tradingagents", "cache", "fundamentals")
))
CACHE_TTL_HOURS = 48  # Refresh cached fundamentals every 48 hours
REQUEST_DELAY = 1.0   # Rate limit for SEC requests (delegated to SecEdgarClient)

# ETF symbols to skip
ETF_SYMBOLS = {"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO"}

# ─── Module-level SEC client (shared session, rate limiting) ─────────────────

_client = None


def _get_sec_client() -> SecEdgarClient:
    """Get or create the module-level SEC EDGAR client."""
    global _client
    if _client is None:
        _client = SecEdgarClient(cache_dir=_CACHE_DIR, request_delay=REQUEST_DELAY)
    return _client


# ─── Helper Functions ────────────────────────────────────────────────────────

def _format_number(value) -> Optional[float]:
    """Parse a number string from SEC filings.

    Delegates to sec_edgar_client._format_number for consistency.
    Kept here for backward compatibility.
    """
    return _sec_format_number(value)


def _calculate_metrics(raw: Dict[str, Any], price: Optional[float] = None) -> Dict[str, Any]:
    """Calculate derived financial metrics from raw parsed values.

    Args:
        raw: Dict of field_name → numeric_value (from parsed filing)
        price: Current stock price for ratio calculations

    Returns:
        Dict of metric_name → numeric_value
    """
    metrics = {}

    # Parse raw values
    revenue = _format_number(raw.get("revenue"))
    net_income = _format_number(raw.get("net_income"))
    eps_diluted = _format_number(raw.get("eps_diluted") or raw.get("eps"))
    total_assets = _format_number(raw.get("total_assets"))
    total_liabilities = _format_number(raw.get("total_liabilities"))
    total_equity = _format_number(raw.get("total_equity"))
    operating_cf = _format_number(raw.get("operating_cash_flow"))
    capex = _format_number(raw.get("capex"))
    debt = _format_number(raw.get("long_term_debt") or raw.get("debt"))
    gross_profit = _format_number(raw.get("gross_profit"))
    operating_income = _format_number(raw.get("operating_income"))
    current_assets = _format_number(raw.get("total_current_assets") or raw.get("current_assets"))
    current_liabilities = _format_number(raw.get("total_current_liabilities") or raw.get("current_liabilities"))
    shares = _format_number(raw.get("shares_outstanding"))

    # ── Profitability ratios ──
    if total_equity and total_equity != 0:
        metrics["roe"] = net_income / total_equity if net_income else None  # Return on Equity

    if total_assets and total_assets != 0:
        metrics["roa"] = net_income / total_assets if net_income else None  # Return on Assets

    if gross_profit and revenue and revenue != 0:
        metrics["gross_margin"] = gross_profit / revenue

    if operating_income and revenue and revenue != 0:
        metrics["operating_margin"] = operating_income / revenue

    if net_income and revenue and revenue != 0:
        metrics["net_margin"] = net_income / revenue

    # ── Solvency ratios ──
    if debt and total_equity and total_equity != 0:
        metrics["debt_to_equity"] = debt / total_equity

    if current_assets and current_liabilities and current_liabilities != 0:
        metrics["current_ratio"] = current_assets / current_liabilities

    # ── Cash flow ──
    if operating_cf is not None and capex is not None:
        metrics["free_cash_flow"] = operating_cf - capex

    # ── Valuation ratios ──
    if price and eps_diluted and eps_diluted != 0:
        metrics["pe_ratio"] = price / eps_diluted

    if price and total_equity and shares and shares > 0:
        book_per_share = total_equity / shares
        if book_per_share > 0:
            metrics["pb_ratio"] = price / book_per_share

    # Rough market cap estimate
    if eps_diluted and shares and shares > 0:
        metrics["market_cap_estimate"] = abs(eps_diluted) * shares

    return metrics


# ─── Cache Functions ─────────────────────────────────────────────────────────

def _get_cache_key(ticker: str, filing_type: str = "annual") -> str:
    """Generate a cache key for a ticker."""
    return f"{ticker.upper()}_{filing_type}"


def _load_cache(ticker: str, filing_type: str = "annual") -> Optional[Dict]:
    """Load cached fundamentals data if it exists and is fresh."""
    cache_file = _CACHE_DIR / f"{_get_cache_key(ticker, filing_type)}.json"
    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r") as f:
            data = json.load(f)

        cached_time = data.get("cached_at", 0)
        if time.time() - cached_time < CACHE_TTL_HOURS * 3600:
            logger.debug(f"Cache hit for {ticker} ({filing_type})")
            return data.get("data")
        else:
            logger.debug(f"Cache expired for {ticker} ({filing_type})")
            return None
    except Exception as e:
        logger.debug(f"Cache load failed for {ticker}: {e}")
        return None


def _save_cache(ticker: str, filing_type: str, data: Dict):
    """Save fundamentals data to cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{_get_cache_key(ticker, filing_type)}.json"

    try:
        cache_data = {
            "data": data,
            "cached_at": time.time(),
            "ticker": ticker,
            "filing_type": filing_type,
        }
        with open(cache_file, "w") as f:
            json.dump(cache_data, f, indent=2)
        logger.debug(f"Cache saved for {ticker} ({filing_type})")
    except Exception as e:
        logger.debug(f"Cache save failed for {ticker}: {e}")


# ─── Price Fetching ──────────────────────────────────────────────────────────

def _get_current_price(ticker: str) -> Optional[float]:
    """Get the current stock price for ratio calculations.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Current price as float, or None on failure.
    """
    try:
        from tradingagents.dataflows.tradestation_stock import get_quote
        quote = get_quote(ticker)
        if quote:
            return quote.get("last") or quote.get("close")
    except Exception as e:
        logger.debug(f"Could not fetch price for {ticker}: {e}")
    return None


# ─── Main Fetch Functions ────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch fundamental data for a ticker from SEC EDGAR.

    PRIMARY DATA SOURCE: XBRL Company Facts API (data.sec.gov/api/xbrl/companyfacts/)
    Returns structured JSON with all XBRL-tagged financial line items.

    FALLBACK: /Archives/ document parsing (HTML regex) if XBRL data is incomplete.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        curr_date: Current date string (YYYY-MM-DD format). Defaults to today.
        look_back_days: How far back to look for filings. Defaults to 365 days.

    Returns:
        Dict with financial data and derived metrics:
        {
            "ticker": str,
            "cik": str,
            "filing_date": str,
            "report_date": str,
            "filing_type": str,
            "data_source": "xbrl_company_facts" | "document_parsing_fallback",
            "raw": Dict[str, float],  # Parsed financial line items
            "metrics": Dict[str, float],  # Derived ratios
        }
    """
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")

    ticker = ticker.upper().strip()

    # Skip ETFs and non-equity symbols
    if ticker in ETF_SYMBOLS:
        return {
            "error": f"{ticker} is an ETF — fundamentals not applicable",
            "is_etf": True,
        }

    # Check cache first
    cached = _load_cache(ticker, "annual")
    if cached:
        return cached

    client = _get_sec_client()

    # Get current price for ratio calculations
    price = _get_current_price(ticker)

    # Look up CIK
    cik = client.get_cik(ticker)
    if not cik:
        return {
            "error": f"Could not find SEC filing data for {ticker}",
            "ticker": ticker,
        }

    # ── PRIMARY: XBRL Company Facts API ────────────────────────────────
    logger.info(f"Fetching XBRL Company Facts for {ticker} (CIK: {cik})")
    xbrl_raw = client.get_xbrl_facts(cik, form_types=["10-K"])
    xbrl_field_count = len(xbrl_raw)
    logger.info(f"XBRL returned {xbrl_field_count} fields for {ticker}")

    if xbrl_field_count >= 5:
        # XBRL returned sufficient data — use it as primary source
        logger.info(f"Using XBRL data as primary source for {ticker} ({xbrl_field_count} fields)")

        # Get filing metadata from Submissions API for context
        filings = client.get_10k_filings(cik, limit=1)
        latest = filings[0] if filings else {}

        raw = {k: _format_number(v) for k, v in xbrl_raw.items()}
        metrics = _calculate_metrics(raw, price)

        result = {
            "ticker": ticker,
            "cik": cik,
            "filing_date": latest.get("filingDate", ""),
            "report_date": latest.get("reportDate", ""),
            "filing_type": latest.get("form", "10-K"),
            "data_source": "xbrl_company_facts",
            "raw": raw,
            "metrics": metrics,
        }

        # Cache the result
        _save_cache(ticker, "annual", result)
        return result

    # ── FALLBACK: Document parsing via /Archives/ endpoint ─────────────
    # XBRL data was insufficient — fall back to parsing filing documents
    logger.warning(f"XBRL returned only {xbrl_field_count} fields for {ticker}, falling back to document parsing")

    filings = client.get_10k_filings(cik, limit=5)
    if not filings:
        filings = client.get_10q_filings(cik, limit=10)

    if not filings:
        return {
            "error": f"No SEC filings found for {ticker} (CIK: {cik})",
            "ticker": ticker,
            "cik": cik,
        }

    latest = filings[0]
    content = client.get_filing_content(cik, latest["accessionNumber"])

    if not content:
        return {
            "error": f"Could not download filing for {ticker} (XBRL insufficient + /Archives/ failed). "
                     f"CIK: {cik}, Filing: {latest.get('filingDate', '')}",
            "ticker": ticker,
            "cik": cik,
            "filing_date": latest.get("filingDate", ""),
            "xbrl_fields_found": xbrl_field_count,
        }

    # Parse filing content (XBRL tags in HTML first, then regex fallback)
    raw = client.parse_filing_content(content)
    raw = {k: _format_number(v) for k, v in raw.items()}

    # Merge XBRL data on top of document parsing (XBRL values override)
    for k, v in xbrl_raw.items():
        if k not in raw or raw[k] is None:
            raw[k] = _format_number(v)

    metrics = _calculate_metrics(raw, price)

    result = {
        "ticker": ticker,
        "cik": cik,
        "filing_date": latest.get("filingDate", ""),
        "report_date": latest.get("reportDate", ""),
        "filing_type": latest.get("form", "10-K"),
        "data_source": "document_parsing_fallback",
        "raw": raw,
        "metrics": metrics,
    }

    # Cache the result
    _save_cache(ticker, "annual", result)

    return result


def fetch_balance_sheet(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch balance sheet data for a ticker."""
    result = fetch_fundamentals(ticker, curr_date, look_back_days)
    if "error" in result:
        return result

    raw = result.get("raw", {})
    return {
        "ticker": ticker,
        "filing_date": result.get("filing_date"),
        "report_date": result.get("report_date"),
        "total_assets": _format_number(raw.get("total_assets")),
        "total_liabilities": _format_number(raw.get("total_liabilities")),
        "total_equity": _format_number(raw.get("total_equity")),
        "current_assets": _format_number(raw.get("total_current_assets") or raw.get("current_assets")),
        "current_liabilities": _format_number(raw.get("total_current_liabilities") or raw.get("current_liabilities")),
        "debt": _format_number(raw.get("long_term_debt") or raw.get("debt")),
        "cash_and_equivalents": _format_number(raw.get("cash_and_equivalents")),
        "retained_earnings": _format_number(raw.get("retained_earnings")),
        "accounts_receivable": _format_number(raw.get("accounts_receivable")),
        "inventory": _format_number(raw.get("inventory")),
        "property_plant_equipment": _format_number(raw.get("property_plant_equipment")),
        "goodwill": _format_number(raw.get("goodwill")),
        "accounts_payable": _format_number(raw.get("accounts_payable")),
    }


def fetch_income_statement(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch income statement data for a ticker."""
    result = fetch_fundamentals(ticker, curr_date, look_back_days)
    if "error" in result:
        return result

    raw = result.get("raw", {})
    return {
        "ticker": ticker,
        "filing_date": result.get("filing_date"),
        "report_date": result.get("report_date"),
        "revenue": _format_number(raw.get("revenue")),
        "cost_of_revenue": _format_number(raw.get("cost_of_revenue")),
        "gross_profit": _format_number(raw.get("gross_profit")),
        "operating_expenses": _format_number(raw.get("operating_expenses")),
        "research_development": _format_number(raw.get("research_development")),
        "operating_income": _format_number(raw.get("operating_income")),
        "interest_expense": _format_number(raw.get("interest_expense")),
        "other_income_expense": _format_number(raw.get("other_income_expense")),
        "income_before_tax": _format_number(raw.get("income_before_tax")),
        "income_tax_expense": _format_number(raw.get("income_tax_expense")),
        "net_income": _format_number(raw.get("net_income")),
        "eps_basic": _format_number(raw.get("eps_basic")),
        "eps_diluted": _format_number(raw.get("eps_diluted")),
        "shares_outstanding": _format_number(raw.get("shares_outstanding")),
    }


def fetch_cashflow(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch cash flow data for a ticker."""
    result = fetch_fundamentals(ticker, curr_date, look_back_days)
    if "error" in result:
        return result

    raw = result.get("raw", {})
    return {
        "ticker": ticker,
        "filing_date": result.get("filing_date"),
        "report_date": result.get("report_date"),
        "operating_cash_flow": _format_number(raw.get("operating_cash_flow")),
        "investing_cash_flow": _format_number(raw.get("investing_cash_flow")),
        "financing_cash_flow": _format_number(raw.get("financing_cash_flow")),
        "capex": _format_number(raw.get("capex")),
        "free_cash_flow": _format_number(raw.get("operating_cash_flow")) - _format_number(raw.get("capex")),
    }


# ─── Public API (replaces Alpha Vantage) ─────────────────────────────────────

def get_fundamentals(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Main entry point — returns comprehensive fundamental data.

    This is the function that replaces Alpha Vantage's get_fundamentals.
    Uses SEC EDGAR JSON APIs (no scraping, no API keys).

    Args:
        ticker: Stock ticker symbol
        curr_date: Current date (YYYY-MM-DD). Defaults to today.
        look_back_days: How far back to look for filings.

    Returns:
        Dict with ticker, cik, filing info, raw financials, and derived metrics.
    """
    return fetch_fundamentals(ticker, curr_date, look_back_days)


def get_balance_sheet(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Returns balance sheet data."""
    return fetch_balance_sheet(ticker, curr_date, look_back_days)


def get_income_statement(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Returns income statement data."""
    return fetch_income_statement(ticker, curr_date, look_back_days)


def get_cashflow(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Returns cash flow data."""
    return fetch_cashflow(ticker, curr_date, look_back_days)


# ─── Convenience: Get all three at once ──────────────────────────────────────

def get_all_financials(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch income statement, balance sheet, and cash flow in one call.

    Args:
        ticker: Stock ticker symbol
        curr_date: Current date (YYYY-MM-DD). Defaults to today.
        look_back_days: How far back to look for filings.

    Returns:
        Dict with keys: income_statement, balance_sheet, cash_flow, metrics
    """
    fundamentals = fetch_fundamentals(ticker, curr_date, look_back_days)
    if "error" in fundamentals:
        return fundamentals

    return {
        "ticker": fundamentals["ticker"],
        "filing_date": fundamentals["filing_date"],
        "report_date": fundamentals["report_date"],
        "filing_type": fundamentals["filing_type"],
        "income_statement": fetch_income_statement(ticker, curr_date, look_back_days),
        "balance_sheet": fetch_balance_sheet(ticker, curr_date, look_back_days),
        "cash_flow": fetch_cashflow(ticker, curr_date, look_back_days),
        "metrics": fundamentals.get("metrics", {}),
    }
