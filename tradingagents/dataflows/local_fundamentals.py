"""Local fundamentals data engine — SEC EDGAR scraper + local cache.

Replaces Alpha Vantage for all fundamental data needs. Uses SEC EDGAR's
public XBRL/HTML filings to extract financial data without any API keys.

Architecture (per Synapse's recommendation):
1. Ingest: Scrape SEC EDGAR filings (free, public, no API key)
2. Process: Parse XBRL/HTML for key financial line items
3. Store: Cache locally (quarterly updates — fundamentals are slow-moving)
4. Query: Calculate derived metrics by joining with TradeStation prices

Key features:
- Zero API keys required
- Quarterly cache refresh (fundamentals update quarterly)
- CIK lookup for ticker → SEC identifier mapping
- XBRL/HTML parsing for 10-K (annual) and 10-Q (quarterly) filings
- Derived metrics: P/E, P/B, Debt/Equity, ROE, FCF, revenue growth, etc.
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

_CACHE_DIR = Path(os.environ.get(
    "TRADINGAGENTS_CACHE_DIR",
    os.path.join(os.path.expanduser("~"), ".tradingagents", "cache", "fundamentals")
))
CACHE_TTL_HOURS = 48  # Refresh cached data every 48 hours (fundamentals update quarterly)
SEC_BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_EDGAR_FULL_URL = "https://www.sec.gov/Archives/edgar/data"
REQUEST_DELAY = 0.3  # Seconds between SEC requests (be polite)

# ─── CIK Lookup Cache ────────────────────────────────────────────────────────

# Common tickers → CIK mapping (SEC central index key)
# This is a static lookup — CIKs don't change frequently.
# We supplement with live SEC lookup for unknown tickers.
CIK_LOOKUP = {
    "AAPL": "320193",    # Apple
    "MSFT": "789019",    # Microsoft
    "GOOGL": "1652044",  # Alphabet (Google)
    "GOOG": "1652044",
    "AMZN": "1018724",   # Amazon
    "NVDA": "1045810",   # NVIDIA
    "META": "1326801",   # Meta (Facebook)
    "TSLA": "1318605",   # Tesla
    "BRK.B": "1067983",  # Berkshire Hathaway
    "JPM": "19617",      # JPMorgan
    "JNJ": "200406",     # Johnson & Johnson
    "V": "1403161",      # Visa
    "PG": "80424",       # Procter & Gamble
    "UNH": "731766",     # UnitedHealth
    "HD": "1011219",     # Home Depot
    "MA": "1141391",     # Mastercard
    "DIS": "1744489",    # Disney
    "BAC": "70858",      # Bank of America
    "ADBE": "1065280",   # Adobe
    "NFLX": "1065280",   # Netflix
    "CRM": "1108524",    # Salesforce
    "PYPL": "1633917",   # PayPal
    "INTC": "50863",     # Intel
    "CMCSA": "1298912",  # Comcast
    "PFE": "78003",      # Pfizer
    "KO": "21344",       # Coca-Cola
    "PEP": "77476",      # PepsiCo
    "COST": "1022078",   # Costco
    "ABBV": "1551152",   # AbbVie
    "TMO": "29699",      # Thermo Fisher
    "AVGO": "1730168",   # Broadcom
    "MDT": "1035085",    # Medtronic
    "MRK": "310158",     # Merck
    "CSCO": "858877",    # Cisco
    "ACN": "1103116",    # Accenture
    "LIN": "1170971",    # Linde
    "TXN": "943819",     # Texas Instruments
    "QCOM": "804328",    # Qualcomm
    "NEE": "1200511",    # NextEra Energy
    "AMGN": "318452",    # Amgen
    "HON": "40545",      # Honeywell
    "UPS": "1090727",    # UPS
    "LOW": "1001250",    # Lowe's
    "SBUX": "829224",    # Starbucks
    "GILD": "814114",    # Gilead
    "AMD": "2488",       # Advanced Micro Devices
    "SPY": None,         # ETF — skip fundamentals
    "QQQ": None,         # ETF — skip fundamentals
}


# ─── XBRL Tag Mappings ──────────────────────────────────────────────────────

# Maps our internal field names to SEC XBRL/HTML tags.
# SEC filings use XBRL taxonomy — these are the most common tag references.
XBRL_TAGS = {
    "revenue": [
        "us-gaap_Revenue",
        "us-gaap_SalesRevenueNet",
        "us-gaap_SalesRevenueGoodsNet",
        "dei_EntityRegistrantName",  # fallback: company name
    ],
    "net_income": [
        "us-gaap_NetIncomeLoss",
        "us-gaap_NetIncome",
    ],
    "eps": [
        "us-gaap_EarningsPerShareDiluted",
        "us-gaap_EarningsPerShareBasic",
    ],
    "total_assets": [
        "us-gaap_Assets",
        "us-gaap_AssetsNoncurrent",
        "us-gaap_AssetsCurrent",
    ],
    "total_liabilities": [
        "us-gaap_Liabilities",
        "us-gaap_LiabilitiesAndStockholdersEquity",
    ],
    "total_equity": [
        "us-gaap_StockholdersEquity",
        "us-gaap_StockholdersEquityIncludingNoncontrollingInterests",
    ],
    "operating_cash_flow": [
        "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "us-gaap_NetCashProvidedByUsedInOperatingActivities",
    ],
    "free_cash_flow": [
        "us-gaap_FreeCashFlow",
    ],
    "debt": [
        "us-gaap_LongTermDebt",
        "us-gaap_DebtCurrent",
        "us-gaap_LongTermDebtCurrent",
    ],
    "current_assets": [
        "us-gaap_AssetsCurrent",
    ],
    "current_liabilities": [
        "us-gaap_LiabilitiesCurrent",
    ],
    "gross_profit": [
        "us-gaap_GrossProfit",
        "us-gaap_NetSalesRevenue",
    ],
    "operating_income": [
        "us-gaap_OperatingIncomeLoss",
        "us-gaap_IncomeFromContinuingOperationsBeforeDedistributionsAndTaxExpenses",
    ],
    "research_development": [
        "us-gaap_ResearchAndDevelopmentExpense",
    ],
    "operating_expenses": [
        "us-gaap_OperatingExpenses",
    ],
    "capex": [
        "us-gaap_PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap_PaymentsForProceedsFromPurchaseOfMaturityOfInvestments",
    ],
    "shares_outstanding": [
        "us-gaap_WeightedAverageNumberOfShareOutstandingBasic",
        "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "retained_earnings": [
        "us-gaap_RetainedEarningsAccumulatedDeficit",
    ],
    "cash_and_equivalents": [
        "us-gaap_CashAndCashEquivalentsAtCarryingValue",
        "us-gaap_Cash",
    ],
}


# ─── HTML Fallback Mappings ─────────────────────────────────────────────────
# When XBRL parsing fails (many filings don't have XBRL), use HTML text matching.
HTML_PATTERNS = {
    "revenue": [
        r"(?:Total\s+)?(?:Net\s+)?(?:Sales\s+)?(?:Revenue|Revenues)\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Total\s+Revenue\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Net\s+Revenue\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "net_income": [
        r"(?:Net\s+)?(?:Income\s+)?(?:Loss)\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Net\s+Income\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Net\s+Earnings\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "eps": [
        r"(?:Basic\s+)?(?:Diluted\s+)?EPS?\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Earnings\s+Per\s+Share\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "total_assets": [
        r"Total\s+Assets\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "total_liabilities": [
        r"Total\s+Liabilities\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "total_equity": [
        r"Total\s+Stockholders?\s+Equity\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Total\s+Equity\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "operating_cash_flow": [
        r"Net\s+Cash\s+.*?(?:Operating|from\s+operations)\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Cash\s+from\s+Operations\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "free_cash_flow": [
        r"Free\s+Cash\s+Flow\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "debt": [
        r"Total\s+Debt\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Long\s+Term\s+Debt\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Total\s+Borrowings\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "current_assets": [
        r"Total\s+Current\s+Assets\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "current_liabilities": [
        r"Total\s+Current\s+Liabilities\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "gross_profit": [
        r"Gross\s+Profit\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "operating_income": [
        r"Operating\s+Income\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Operating\s+Earnings\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "research_development": [
        r"Research\s+and\s+Development\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"R\&D\s+Expense\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "operating_expenses": [
        r"Total\s+Operating\s+Expenses\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Operating\s+Expenses\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "capex": [
        r"Capital\s+Expenditure.*?(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        r"Payments\s+to\s+Acquire\s+Property\s+Plant.*?\s*[\$]?(-?[\d,\.]+)",
        r"Capital\s+Expenditures\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
    ],
    "shares_outstanding": [
        r"(?:Weighted\s+)?(?:Average\s+)?Shares?\s+(?:Outstanding|Issued)\s*(?:\(|:)\s*([\d,\.]+)",
        r"Shares?\s+Outstanding\s*(?:\(|:)\s*([\d,\.]+)",
    ],
    "retained_earnings": [
        r"Retained\s+Earnings\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
    "cash_and_equivalents": [
        r"Cash\s+and\s+Cash\s+Equivalents\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        r"Total\s+Cash\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
    ],
}


# ─── Helper Functions ────────────────────────────────────────────────────────

def _format_number(value) -> Optional[float]:
    """Parse a number string from SEC filings, handling K/M/B suffixes and parentheses for negatives."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # Remove dollar signs and commas
    s = s.replace("$", "").replace(",", "").replace("(", "").replace(")", "")
    # Handle negative in parentheses: (1,234.56) → -1234.56
    s = s.strip()
    # Try direct parse
    try:
        return float(s)
    except ValueError:
        pass
    # Handle K/M/B suffixes
    suffix = s[-1].upper()
    try:
        num = float(s[:-1])
    except ValueError:
        return None
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    if suffix in multipliers:
        return num * multipliers[suffix]
    return None


def _parse_xbrl_element(element, tags: List[str]) -> Optional[str]:
    """Try to find a value in XBRL XML by tag name."""
    if element is None:
        return None
    text = element.get_text(strip=True) if hasattr(element, 'get_text') else str(element)
    if not text:
        return None
    return text


def _parse_xbrl_file(content: str) -> Dict[str, str]:
    """Parse XBRL content from a SEC filing and extract key financial values."""
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(content)
    except Exception:
        return {}

    # Handle namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    result = {}
    for tag in XBRL_TAGS.get("revenue", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["revenue"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("net_income", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["net_income"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("total_assets", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["total_assets"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("total_liabilities", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["total_liabilities"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("total_equity", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["total_equity"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("operating_cash_flow", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["operating_cash_flow"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("debt", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["debt"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("eps", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["eps"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("gross_profit", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["gross_profit"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("operating_income", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["operating_income"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("research_development", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["research_development"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("capex", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["capex"] = _parse_xbrl_element(elem, [tag])
            break

    for tag in XBRL_TAGS.get("shares_outstanding", []):
        elem = root.find(f".//{ns}{tag}")
        if elem is not None:
            result["shares_outstanding"] = _parse_xbrl_element(elem, [tag])
            break

    return result


def _parse_html_filing(content: str) -> Dict[str, str]:
    """Parse HTML filing content using regex patterns as fallback."""
    result = {}
    soup = BeautifulSoup(content, "html.parser")
    # Get text content
    text = soup.get_text()

    for field, patterns in HTML_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                result[field] = match.group(1)
                break

    return result


def _lookup_cik(ticker: str) -> Optional[str]:
    """Look up the CIK number for a ticker symbol."""
    ticker = ticker.upper().strip()

    # Check static lookup first
    if ticker in CIK_LOOKUP:
        return CIK_LOOKUP[ticker]

    # Try SEC EDGAR company search
    try:
        params = {
            "company": ticker,
            "type": "10-K",
            "dateb": "",
            "owner": "include",
            "count": 1,
            "action": "companycompany"
        }
        resp = requests.get(SEC_BASE_URL, params=params, timeout=10, headers={
            "User-Agent": "TradingAgents/1.0 (local; no API key needed)"
        })
        if resp.status_code == 200:
            # Parse CIK from search results
            match = re.search(r'href.*?CIK=([0-9]+)', resp.text)
            if match:
                cik = match.group(1).zfill(10)
                CIK_LOOKUP[ticker] = cik
                return cik
    except Exception as e:
        logger.debug(f"SEC CIK lookup failed for {ticker}: {e}")

    return None


def _get_filings_urls(cik: str, filing_type: str = "10-K") -> List[Dict]:
    """Get list of filing URLs for a given CIK and filing type."""
    # SEC EDGAR full-text search
    url = f"https://efts.sec.gov/LATEST/search-index?q={ticker}"
    # SEC index API (preferred, returns JSON)
    url = f"{SEC_EDGAR_FULL_URL}/{cik}/{filing_type}.idx"

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "TradingAgents/1.0 (local; no API key needed)"
        })
        if resp.status_code == 200:
            filings = []
            for line in resp.text.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3:
                    accession = parts[0]
                    # Format: ACCESSIONNUMBER COMPANY_NAME FILING_DATE.FILING_TYPE
                    filing_date_str = parts[1]
                    try:
                        filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
                    except ValueError:
                        continue
                    filings.append({
                        "accession": accession,
                        "date": filing_date,
                        "company": parts[2].replace("_", " "),
                    })
            return sorted(filings, key=lambda x: x["date"], reverse=True)
    except Exception as e:
        logger.debug(f"Failed to fetch {filing_type} index for CIK {cik}: {e}")

    return []


def _get_filing_content(cik: str, accession: str, ticker: str = None) -> Optional[str]:
    """Download and return the content of a specific SEC filing."""
    clean_accession = accession.replace("-", "")
    ticker_lower = (ticker or "company").lower()

    # Try multiple URL formats that SEC EDGAR uses
    urls = [
        f"{SEC_EDGAR_FULL_URL}/{cik.zfill(10)}/{clean_accession}/{ticker_lower}-{clean_accession}.txt",
        f"{SEC_EDGAR_FULL_URL}/{cik.zfill(10)}/{clean_accession}/{ticker_lower}{clean_accession}.txt",
        f"{SEC_EDGAR_FULL_URL}/{cik.zfill(10)}/{clean_accession}/{clean_accession}.txt",
        f"{SEC_EDGAR_FULL_URL}/{cik.zfill(10)}/{clean_accession}/{clean_accession}.htm",
    ]

    for url in urls:
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "TradingAgents/1.0 (local; no API key needed)"
            })
            if resp.status_code == 200 and resp.text.strip():
                time.sleep(REQUEST_DELAY)
                return resp.text
        except Exception:
            continue

    return None


def _calculate_metrics(raw: Dict[str, str], price: Optional[float] = None) -> Dict[str, Any]:
    """Calculate derived financial metrics from raw parsed values."""
    metrics = {}

    # Parse raw values
    revenue = _format_number(raw.get("revenue"))
    net_income = _format_number(raw.get("net_income"))
    eps = _format_number(raw.get("eps"))
    total_assets = _format_number(raw.get("total_assets"))
    total_liabilities = _format_number(raw.get("total_liabilities"))
    total_equity = _format_number(raw.get("total_equity"))
    operating_cf = _format_number(raw.get("operating_cash_flow"))
    debt = _format_number(raw.get("debt"))
    gross_profit = _format_number(raw.get("gross_profit"))
    operating_income = _format_number(raw.get("operating_income"))
    capex = _format_number(raw.get("capex"))
    current_assets = _format_number(raw.get("current_assets"))
    current_liabilities = _format_number(raw.get("current_liabilities"))
    shares = _format_number(raw.get("shares_outstanding"))

    # Derived metrics
    if revenue and total_equity and total_equity != 0:
        metrics["roe"] = net_income / total_equity if net_income else None  # ROE

    if revenue and total_assets and total_assets != 0:
        metrics["roa"] = net_income / total_assets if net_income else None  # ROA

    if debt and total_equity and total_equity != 0:
        metrics["debt_to_equity"] = debt / total_equity

    if current_assets and current_liabilities and current_liabilities != 0:
        metrics["current_ratio"] = current_assets / current_liabilities

    if gross_profit and revenue and revenue != 0:
        metrics["gross_margin"] = gross_profit / revenue

    if operating_income and revenue and revenue != 0:
        metrics["operating_margin"] = operating_income / revenue

    if net_income and revenue and revenue != 0:
        metrics["net_margin"] = net_income / revenue

    if operating_cf and capex:
        metrics["free_cash_flow"] = operating_cf - capex

    if price and eps and eps != 0:
        metrics["pe_ratio"] = price / eps

    if price and total_equity and shares and shares > 0 and total_assets and total_assets > 0:
        book_per_share = total_equity / shares
        if book_per_share > 0:
            metrics["pb_ratio"] = price / book_per_share

    if eps and shares and shares > 0:
        metrics["market_cap"] = eps * shares  # Rough estimate

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

        # Check TTL
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


# ─── Main Fetch Functions ────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Fetch fundamental data for a ticker from SEC EDGAR.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        curr_date: Current date string (YYYY-MM-DD format). Defaults to today.
        look_back_days: How far back to look for filings. Defaults to 365 days.

    Returns:
        Dict with financial data and derived metrics.
    """
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")

    ticker = ticker.upper().strip()

    # Skip ETFs and non-equity symbols
    if ticker in ("SPY", "QQQ", "IWM", "DIA", "VTI", "VOO"):
        return {
            "error": f"{ticker} is an ETF — fundamentals not applicable",
            "is_etf": True,
        }

    # Check cache first
    cached = _load_cache(ticker, "annual")
    if cached:
        return cached

    # Get current price for ratio calculations
    price = None
    try:
        from tradingagents.dataflows.tradestation_stock import get_quote
        quote = get_quote(ticker)
        if quote:
            price = quote.get("last") or quote.get("close")
    except Exception as e:
        logger.debug(f"Could not fetch price for {ticker}: {e}")

    # Look up CIK
    cik = _lookup_cik(ticker)
    if not cik:
        return {
            "error": f"Could not find SEC filing data for {ticker}",
            "ticker": ticker,
        }

    # Get latest 10-K filing
    filings = _get_filings_urls(cik, "10-K")
    if not filings:
        # Try 10-Q as fallback
        filings = _get_filings_urls(cik, "10-Q")

    if not filings:
        return {
            "error": f"No SEC filings found for {ticker} (CIK: {cik})",
            "ticker": ticker,
            "cik": cik,
        }

    # Fetch the most recent filing
    latest = filings[0]
    content = _get_filing_content(cik, latest["accession"], ticker)

    if not content:
        return {
            "error": f"Could not download filing for {ticker}",
            "ticker": ticker,
            "cik": cik,
            "filing_date": latest["date"].strftime("%Y-%m-%d"),
        }

    # Parse filing
    raw = {}

    # Try XBRL first
    if "<us-gaap" in content or "<dei>" in content:
        raw = _parse_xbrl_file(content)

    # Fall back to HTML parsing
    if not raw:
        raw = _parse_html_filing(content)

    # Calculate derived metrics
    metrics = _calculate_metrics(raw, price)

    result = {
        "ticker": ticker,
        "cik": cik,
        "filing_date": latest["date"].strftime("%Y-%m-%d"),
        "filing_type": "10-K" if "10-K" in str(filings[0]) else "10-Q",
        "raw": {k: _format_number(v) for k, v in raw.items()},
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
        "total_assets": _format_number(raw.get("total_assets")),
        "total_liabilities": _format_number(raw.get("total_liabilities")),
        "total_equity": _format_number(raw.get("total_equity")),
        "current_assets": _format_number(raw.get("current_assets")),
        "current_liabilities": _format_number(raw.get("current_liabilities")),
        "debt": _format_number(raw.get("debt")),
        "cash_and_equivalents": _format_number(raw.get("cash_and_equivalents")),
        "retained_earnings": _format_number(raw.get("retained_earnings")),
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
        "revenue": _format_number(raw.get("revenue")),
        "gross_profit": _format_number(raw.get("gross_profit")),
        "operating_income": _format_number(raw.get("operating_income")),
        "net_income": _format_number(raw.get("net_income")),
        "eps": _format_number(raw.get("eps")),
        "research_development": _format_number(raw.get("research_development")),
        "operating_expenses": _format_number(raw.get("operating_expenses")),
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
        "operating_cash_flow": _format_number(raw.get("operating_cash_flow")),
        "free_cash_flow": _format_number(raw.get("free_cash_flow")),
        "capex": _format_number(raw.get("capex")),
    }


def get_fundamentals(ticker: str, curr_date: str = None, look_back_days: int = 365) -> Dict[str, Any]:
    """Main entry point — returns comprehensive fundamental data.

    This is the function that replaces Alpha Vantage's get_fundamentals.
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
