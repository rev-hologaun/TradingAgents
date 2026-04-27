"""SEC EDGAR API client — official JSON APIs for financial data.

Uses SEC's official EDGAR APIs (no API keys needed) to fetch:
- Company filings (10-K, 10-Q, 8-K, etc.) via Submissions API
- Structured financial data via XBRL Company Facts API (PRIMARY)
- Filing documents via /Archives/ endpoint (FALLBACK)

Architecture:
1. CIK lookup — static mapping + SEC ticker-to-CIK endpoint
2. Fetch XBRL facts — Company Facts API returns structured JSON (PRIMARY)
3. Fetch filings — Submissions API for filing metadata
4. Fallback — download HTML/TXT filing documents via /Archives/ endpoint
5. Parse financial data — XBRL tags first, HTML regex fallback
6. Cache — local JSON files, 24-hour TTL

References:
- Submissions API: https://www.sec.gov/files/qa_filingdata497_2018.pdf
- XBRL Company Facts: https://www.sec.gov/edgar/searchedgar/webxbrl.htm
- EDGAR Filing structure: https://www.sec.gov/cgi-bin/browse-edgar
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
SEC_CACHE_TTL_HOURS = 24  # 24-hour cache for SEC data
SEC_REQUEST_DELAY = 1.0  # Minimum seconds between SEC API calls (rate limit)

# SEC API endpoints
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_FILING_URL = "https://www.sec.gov/Archives/edgar/data"

# Required User-Agent header (SEC blocks requests without it)
SEC_HEADERS = {
    "User-Agent": "TradingAgents/1.0 (contact: admin@tradingagents.local)"
}

# ─── CIK Mapping ─────────────────────────────────────────────────────────────

# Ticker → CIK (zero-padded 10-digit SEC Central Index Key)
CIK_MAP = {
    "AAPL": "0000320193",    # Apple Inc.
    "MSFT": "0000789019",    # Microsoft Corp.
    "GOOGL": "0001652044",   # Alphabet Inc.
    "GOOG": "0001652044",    # Alphabet Inc. (Class A)
    "AMZN": "0001018724",    # Amazon.com Inc.
    "TSLA": "0001318605",    # Tesla Inc.
    "NVDA": "0001045810",    # NVIDIA Corp.
    "META": "0001326801",    # Meta Platforms Inc.
    "BRK-B": "0001067983",   # Berkshire Hathaway Inc.
    "BRK.B": "0001067983",
    "JPM": "0000019617",     # JPMorgan Chase & Co.
    "AMD": "0000002488",     # Advanced Micro Devices Inc.
    "JNJ": "0000200406",     # Johnson & Johnson
    "V": "0001171921",       # Visa Inc.
    "PG": "0000080424",      # Procter & Gamble Co.
    "UNH": "0000731766",     # UnitedHealth Group Inc.
    "HD": "0000354950",      # Home Depot Inc.
    "MA": "0001141391",      # Mastercard Inc.
    "DIS": "0001744489",     # Walt Disney Co.
    "BAC": "0000070858",     # Bank of America Corp.
    "ADBE": "0000796343",    # Adobe Inc.
    "NFLX": "0001065280",    # Netflix Inc.
    "CRM": "0001108524",     # Salesforce Inc.
    "PYPL": "0001633917",    # PayPal Holdings Inc.
    "INTC": "0000050863",    # Intel Corp.
    "CMCSA": "0001298912",   # Comcast Corp.
    "PFE": "0000078003",     # Pfizer Inc.
    "KO": "0000021344",      # Coca-Cola Co.
    "PEP": "0000077476",     # PepsiCo Inc.
    "COST": "0001022078",    # Costco Wholesale Corp.
    "ABBV": "0001551152",    # AbbVie Inc.
    "TMO": "000029699",      # Thermo Fisher Scientific Inc.
    "AVGO": "0001730168",    # Broadcom Inc.
    "MDT": "0001035085",     # Medtronic PLC
    "MRK": "0000310158",     # Merck & Co. Inc.
    "CSCO": "0000858877",    # Cisco Systems Inc.
    "ACN": "0001103116",     # Accenture PLC
    "LIN": "0001170971",     # Linde PLC
    "TXN": "0000943819",     # Texas Instruments Inc.
    "QCOM": "0000804328",    # Qualcomm Inc.
    "NEE": "0001200511",     # NextEra Energy Inc.
    "AMGN": "0000318452",    # Amgen Inc.
    "HON": "000040545",      # Honeywell Intl.
    "UPS": "0001090727",     # United Parcel Service Inc.
    "LOW": "0001001250",     # Lowe's Cos. Inc.
    "SBUX": "0000829224",    # Starbucks Corp.
    "GILD": "0000814114",    # Gilead Sciences Inc.
    "SPY": None,             # ETF — skip
    "QQQ": None,             # ETF — skip
    "IWM": None,             # ETF — skip
    "DIA": None,             # ETF — skip
    "VTI": None,             # ETF — skip
    "VOO": None,             # ETF — skip
}


# ─── XBRL Concept Mappings ──────────────────────────────────────────────────

# Maps internal field names to SEC XBRL concept names (us-gaap taxonomy).
# The XBRL Company Facts API returns data organized by taxonomy → concept → unit → facts.
# Each fact has: val (value in units), filed (filing date), end (report period), form (10-K/10-Q).
# We filter by form type, sort by end date, and take the latest value.
XBRL_CONCEPT_MAP = {
    # Income Statement
    "revenue": [
        "us-gaap_Revenue",
        "us-gaap_SalesRevenueNet",
        "us-gaap_SalesRevenueGoodsAndServicesNet",
    ],
    "cost_of_revenue": ["us-gaap_CostOfRevenue"],
    "gross_profit": ["us-gaap_GrossProfit"],
    "operating_expenses": ["us-gaap_OperatingExpenses"],
    "research_development": ["us-gaap_ResearchAndDevelopmentExpense"],
    "operating_income": ["us-gaap_OperatingIncomeLoss"],
    "interest_expense": ["us-gaap_InterestExpense"],
    "other_income_expense": ["us-gaap_NonOperatingIncomeExpense"],
    "income_before_tax": [
        "us-gaap_IncomeFromContinuingOperationsBeforeDedistributionsAndTaxExpenses",
        "us-gaap_IncomeFromContinuingOperationsBeforeTax",
    ],
    "income_tax_expense": ["us-gaap_IncomeTaxExpenseBenefit"],
    "net_income": ["us-gaap_NetIncomeLoss", "us-gaap_NetIncome"],
    "eps_basic": ["us-gaap_EarningsPerShareBasic"],
    "eps_diluted": ["us-gaap_EarningsPerShareDiluted"],
    # Balance Sheet
    "cash_and_equivalents": ["us-gaap_CashAndCashEquivalentsAtCarryingValue", "us-gaap_Cash"],
    "short_term_investments": ["us-gaap_ShortTermInvestments"],
    "accounts_receivable": ["us-gaap_AccountsReceivableNetCurrent"],
    "inventory": ["us-gaap_InventoryNet"],
    "total_current_assets": ["us-gaap_AssetsCurrent"],
    "property_plant_equipment": ["us-gaap_PropertyPlantAndEquipmentNet"],
    "goodwill": ["us-gaap_Goodwill"],
    "total_assets": ["us-gaap_Assets"],
    "accounts_payable": ["us-gaap_AccountsPayableCurrent"],
    "total_current_liabilities": ["us-gaap_LiabilitiesCurrent"],
    "long_term_debt": ["us-gaap_LongTermDebt"],
    "total_liabilities": ["us-gaap_Liabilities"],
    "total_equity": ["us-gaap_StockholdersEquity", "us-gaap_StockholdersEquityIncludingNoncontrollingInterests"],
    # Cash Flow
    "operating_cash_flow": [
        "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "us-gaap_NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": ["us-gaap_PaymentsToAcquirePropertyPlantAndEquipment"],
    "investing_cash_flow": [
        "us-gaap_NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
        "us-gaap_NetCashProvidedByUsedInInvestingActivities",
    ],
    "financing_cash_flow": [
        "us-gaap_NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
        "us-gaap_NetCashProvidedByUsedInFinancingActivities",
    ],
    "shares_outstanding": [
        "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",
        "us-gaap_SharesOutstanding",
    ],
    # Additional fields for completeness
    "retained_earnings": ["us-gaap_RetainedEarningsAccumulatedDeficit"],
    "current_assets": ["us-gaap_AssetsCurrent"],
    "current_liabilities": ["us-gaap_LiabilitiesCurrent"],
    "debt": ["us-gaap_LongTermDebt"],
}


# ─── Financial Field Mappings (HTML regex fallback) ──────────────────────────

# Maps internal field names to patterns found in SEC filing HTML/TXT
# Priority order: XBRL tags > HTML regex patterns
FINANCIAL_FIELDS = {
    # Income Statement
    "revenue": {
        "xbrl_tags": ["us-gaap_Revenue", "us-gaap_SalesRevenueNet", "us-gaap_SalesRevenueGoodsAndServicesNet"],
        "html_patterns": [
            r"(?:Total\s+)?(?:Net\s+)?(?:Sales\s+)?(?:Revenue|Revenues)\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Total\s+Revenue\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Net\s+Revenue\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "cost_of_revenue": {
        "xbrl_tags": ["us-gaap_CostOfRevenue"],
        "html_patterns": [
            r"Cost\s+of\s+(?:Revenue|Sales)\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Cost\s+of\s+Goods\s+Sold\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "gross_profit": {
        "xbrl_tags": ["us-gaap_GrossProfit"],
        "html_patterns": [
            r"Gross\s+Profit\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "operating_expenses": {
        "xbrl_tags": ["us-gaap_OperatingExpenses"],
        "html_patterns": [
            r"Total\s+Operating\s+Expenses\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Operating\s+Expenses\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "research_development": {
        "xbrl_tags": ["us-gaap_ResearchAndDevelopmentExpense"],
        "html_patterns": [
            r"Research\s+and\s+Development\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"R\&D\s+Expense\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "operating_income": {
        "xbrl_tags": ["us-gaap_OperatingIncomeLoss"],
        "html_patterns": [
            r"Operating\s+Income\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Operating\s+Earnings\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "interest_expense": {
        "xbrl_tags": ["us-gaap_InterestExpense"],
        "html_patterns": [
            r"Interest\s+Expense\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "other_income_expense": {
        "xbrl_tags": ["us-gaap_NonOperatingIncomeExpense"],
        "html_patterns": [
            r"(?:Other\s+Income\s*\(\s*Expense\)|Non\s+Operating\s+Income\s*\(Expense\))\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "income_before_tax": {
        "xbrl_tags": ["us-gaap_IncomeFromContinuingOperationsBeforeDedistributionsAndTaxExpenses"],
        "html_patterns": [
            r"Income\s+before\s+income\s+taxes\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Income\s+from\s+continuing\s+operations\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "income_tax_expense": {
        "xbrl_tags": ["us-gaap_IncomeTaxExpenseBenefit"],
        "html_patterns": [
            r"(?:Provision\s+for\s+)?Income\s+Tax\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "net_income": {
        "xbrl_tags": ["us-gaap_NetIncomeLoss", "us-gaap_NetIncome"],
        "html_patterns": [
            r"(?:Net\s+)?(?:Income\s+)?(?:Loss)\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Net\s+Income\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Net\s+Earnings\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "eps_basic": {
        "xbrl_tags": ["us-gaap_EarningsPerShareBasic"],
        "html_patterns": [
            r"Basic\s+earnings\s+per\s+share\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "eps_diluted": {
        "xbrl_tags": ["us-gaap_EarningsPerShareDiluted"],
        "html_patterns": [
            r"Diluted\s+earnings\s+per\s+share\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    # Balance Sheet
    "cash_and_equivalents": {
        "xbrl_tags": ["us-gaap_CashAndCashEquivalentsAtCarryingValue", "us-gaap_Cash"],
        "html_patterns": [
            r"Cash\s+and\s+Cash\s+Equivalents\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Total\s+Cash\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "short_term_investments": {
        "xbrl_tags": ["us-gaap_ShortTermInvestments"],
        "html_patterns": [
            r"Short\s+Term\s+Investments\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Marketable\s+Securities\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "accounts_receivable": {
        "xbrl_tags": ["us-gaap_AccountsReceivableNetCurrent"],
        "html_patterns": [
            r"(?:Trade\s+)?Accounts\s+Receivable\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "inventory": {
        "xbrl_tags": ["us-gaap_InventoryNet"],
        "html_patterns": [
            r"Inventory\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "total_current_assets": {
        "xbrl_tags": ["us-gaap_AssetsCurrent"],
        "html_patterns": [
            r"Total\s+Current\s+Assets\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "property_plant_equipment": {
        "xbrl_tags": ["us-gaap_PropertyPlantAndEquipmentNet"],
        "html_patterns": [
            r"Property\s+and\s+Equipment\s+net\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Net\s+Property\s+and\s+Equipment\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "goodwill": {
        "xbrl_tags": ["us-gaap_Goodwill"],
        "html_patterns": [
            r"Goodwill\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "total_assets": {
        "xbrl_tags": ["us-gaap_Assets"],
        "html_patterns": [
            r"Total\s+Assets\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "accounts_payable": {
        "xbrl_tags": ["us-gaap_AccountsPayableCurrent"],
        "html_patterns": [
            r"Accounts\s+Payable\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "total_current_liabilities": {
        "xbrl_tags": ["us-gaap_LiabilitiesCurrent"],
        "html_patterns": [
            r"Total\s+Current\s+Liabilities\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "long_term_debt": {
        "xbrl_tags": ["us-gaap_LongTermDebt"],
        "html_patterns": [
            r"Long\s+Term\s+Debt\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Long\s+term\s+debt\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "total_liabilities": {
        "xbrl_tags": ["us-gaap_Liabilities"],
        "html_patterns": [
            r"Total\s+Liabilities\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    "total_equity": {
        "xbrl_tags": ["us-gaap_StockholdersEquity", "us-gaap_StockholdersEquityIncludingNoncontrollingInterests"],
        "html_patterns": [
            r"Total\s+Stockholders?\s+Equity\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
            r"Total\s+Equity\s*(?:\(|:)\s*[\$]?([\d,\.]+)",
        ],
    },
    # Cash Flow
    "operating_cash_flow": {
        "xbrl_tags": ["us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "us-gaap_NetCashProvidedByUsedInOperatingActivities"],
        "html_patterns": [
            r"Net\s+Cash\s+.*?(?:Provided\s+by|Used\s+in|from)\s+Operating\s+Activities\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Cash\s+from\s+Operations\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "capex": {
        "xbrl_tags": ["us-gaap_PaymentsToAcquirePropertyPlantAndEquipment"],
        "html_patterns": [
            r"Capital\s+Expenditures?\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Payments\s+to\s+Acquire\s+Property\s+Plant\s+and\s+Equipment\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
            r"Purchase\s+of\s+Property\s+and\s+Equipment\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "investing_cash_flow": {
        "xbrl_tags": ["us-gaap_NetCashProvidedByUsedInInvestingActivitiesContinuingOperations", "us-gaap_NetCashProvidedByUsedInInvestingActivities"],
        "html_patterns": [
            r"Net\s+Cash\s+.*?(?:Provided\s+by|Used\s+in)\s+Investing\s+Activities\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "financing_cash_flow": {
        "xbrl_tags": ["us-gaap_NetCashProvidedByUsedInFinancingActivitiesContinuingOperations", "us-gaap_NetCashProvidedByUsedInFinancingActivities"],
        "html_patterns": [
            r"Net\s+Cash\s+.*?(?:Provided\s+by|Used\s+in)\s+Financing\s+Activities\s*(?:\(|:)\s*[\$]?(-?[\d,\.]+)",
        ],
    },
    "shares_outstanding": {
        "xbrl_tags": ["us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding"],
        "html_patterns": [
            r"(?:Weighted\s+)?(?:Average\s+)?Shares?\s+(?:Outstanding|Issued)\s*(?:\(|:)\s*([\d,\.]+)",
        ],
    },
}


# ─── SEC API Client ──────────────────────────────────────────────────────────

class SecEdgarClient:
    """Client for SEC EDGAR JSON APIs.

    Uses official SEC endpoints with proper rate limiting and caching.
    """

    def __init__(self, cache_dir: Optional[Path] = None, request_delay: float = SEC_REQUEST_DELAY):
        self.cache_dir = cache_dir or _CACHE_DIR
        self.request_delay = request_delay
        self._last_request_time = 0.0
        self._session = requests.Session()
        self._session.headers.update(SEC_HEADERS)

    def _rate_limit(self):
        """Enforce minimum delay between SEC API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, timeout: int = 30) -> Optional[requests.Response]:
        """Make a rate-limited GET request to SEC API."""
        self._rate_limit()
        try:
            resp = self._session.get(url, timeout=timeout)
            if resp.status_code == 429:
                logger.warning(f"SEC API rate limited (429). Waiting 5s...")
                time.sleep(5)
                self._rate_limit()
                resp = self._session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp
            else:
                logger.debug(f"SEC API returned status {resp.status_code} for {url}")
                return None
        except requests.RequestException as e:
            logger.debug(f"SEC API request failed for {url}: {e}")
            return None

    # ─── CIK Lookup ────────────────────────────────────────────────────

    def get_cik(self, ticker: str) -> Optional[str]:
        """Get CIK code for a ticker symbol.

        Uses static mapping first, then falls back to SEC's ticker-to-CIK
        mapping endpoint.

        Args:
            ticker: Ticker symbol (e.g., "AAPL")

        Returns:
            Zero-padded 10-digit CIK string, or None if not found.
        """
        ticker = ticker.upper().strip()

        # Static lookup
        if ticker in CIK_MAP:
            cik = CIK_MAP[ticker]
            if cik is None:
                return None  # ETF or unsupported
            return cik

        # Try SEC's ticker-to-CIK mapping
        try:
            self._rate_limit()
            resp = self._session.get(
                "https://www.sec.gov/cgi-bin/ticker-lookups",
                params={"ticker": ticker},
                timeout=15,
            )
            if resp.status_code == 200:
                match = re.search(r'CIK[=:]\s*(\d+)', resp.text)
                if match:
                    cik = match.group(1).zfill(10)
                    CIK_MAP[ticker] = cik
                    return cik
        except Exception as e:
            logger.debug(f"SEC ticker lookup failed for {ticker}: {e}")

        return None

    # ─── Filings API ───────────────────────────────────────────────────

    def get_filings(self, cik: str, forms: Optional[List[str]] = None,
                    limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent SEC filings for a company.

        Uses the Submissions API which returns up to 1000 most recent filings.

        Args:
            cik: CIK code (with or without zero-padding)
            forms: List of form types to filter (e.g., ["10-K", "10-Q"]).
                   If None, returns all forms.
            limit: Maximum number of filings to return.

        Returns:
            List of filing dicts with keys: form, filingDate, reportDate,
            accessionNumber, documentUrl, financialReportUrl.
        """
        cik = cik.zfill(10)
        cache_key = f"filings_{cik}_{forms}_{limit}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        url = f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json"
        resp = self._get(url)
        if not resp:
            return []

        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from SEC submissions API for CIK {cik}")
            return []

        filings_list = data.get("filings", {}).get("recent", {})

        # Extract filing data
        # The SEC Submissions API returns these keys (may vary by company):
        # accessionNumber, form, filingDate, reportDate, primaryDocument, isXBRL,
        # isInlineXBRL, core_type, size, acceptanceDateTime, etc.
        accessions = filings_list.get("accessionNumber", [])
        forms_list = filings_list.get("form", [])
        filing_dates = filings_list.get("filingDate", [])
        report_dates = filings_list.get("reportDate", [])
        primary_docs = filings_list.get("primaryDocument", [])
        has_xbrl = filings_list.get("isXBRL", [])
        has_inline_xbrl = filings_list.get("isInlineXBRL", [])

        filings = []
        for i in range(len(accessions)):
            form = forms_list[i].upper().replace("-", "") if i < len(forms_list) else ""

            # Filter by form type if specified
            if forms:
                # Normalize: "10-K" → "10K", "10-Q" → "10Q"
                form_normalized = form.replace("-", "")
                if not any(f.replace("-", "").upper() == form_normalized for f in forms):
                    continue

            accession = accessions[i].replace("-", "") if i < len(accessions) else ""
            filing_date = filing_dates[i] if i < len(filing_dates) else ""
            report_date = report_dates[i] if i < len(report_dates) else ""

            # Build document URL using primaryDocument from the API
            # Format: https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/PRIMARY_DOCUMENT
            # Example: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm
            accession_padded = accession.zfill(12)
            primary_doc = primary_docs[i] if i < len(primary_docs) else f"{accession}.htm"
            document_url = f"{SEC_FILING_URL}/{cik}/{accession_padded}/{primary_doc}"

            filings.append({
                "form": form,
                "filingDate": filing_date,
                "reportDate": report_date,
                "accessionNumber": accession,
                "primaryDocument": primary_doc,
                "documentUrl": document_url,
                "isXBRL": bool(has_xbrl[i]) if i < len(has_xbrl) else False,
                "isInlineXBRL": bool(has_inline_xbrl[i]) if i < len(has_inline_xbrl) else False,
            })

        # Sort by filing date (most recent first) and limit
        filings.sort(key=lambda x: x["filingDate"], reverse=True)
        result = filings[:limit]

        # Cache
        self._save_cache(cache_key, result)

        return result

    def get_10k_filings(self, cik: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent 10-K (annual) filings."""
        return self.get_filings(cik, forms=["10-K"], limit=limit)

    def get_10q_filings(self, cik: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent 10-Q (quarterly) filings."""
        return self.get_filings(cik, forms=["10-Q"], limit=limit)

    # ─── Filing Content ────────────────────────────────────────────────

    def get_filing_content(self, cik: str, accession: str,
                           primary_document: Optional[str] = None,
                           as_text: bool = True) -> Optional[str]:
        """Download the content of a specific SEC filing.

        Uses the SEC EDGAR filing URL structure:
        https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/PRIMARY_DOCUMENT

        Args:
            cik: CIK code
            accession: Accession number (without dashes)
            primary_document: Primary document filename from the Submissions API
                             (e.g., "aapl-20250927.htm"). If None, falls back
                             to constructing URL from accession.
            as_text: If True and content is HTML, extract text. If False, return raw HTML.

        Returns:
            Filing content as string, or None on failure.
        """
        cik = cik.zfill(10)
        accession = accession.replace("-", "")
        accession_padded = accession.zfill(12)

        # Build URL using primaryDocument from the Submissions API
        # Format: https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/PRIMARY_DOCUMENT
        # Example: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm
        if primary_document:
            url = f"{SEC_FILING_URL}/{cik}/{accession_padded}/{primary_document}"
        else:
            # Fallback: try both .htm and .txt
            url = f"{SEC_FILING_URL}/{cik}/{accession_padded}/{accession}.htm"

        resp = self._get(url)
        if not resp:
            return None

        # If requesting text but got HTML, extract text content
        if as_text and "text/html" in resp.headers.get("Content-Type", ""):
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove script/style tags for cleaner text
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text()

        return resp.text

    # ─── Filing Parsing ────────────────────────────────────────────────

    def parse_filing_content(self, content: str) -> Dict[str, Any]:
        """Parse financial data from a SEC filing's text content.

        Tries XBRL parsing first, falls back to HTML regex patterns.

        Args:
            content: Filing content (text or HTML)

        Returns:
            Dict mapping field names to numeric values.
        """
        result = {}

        # Try XBRL parsing first
        if "<us-gaap" in content or "<dei>" in content:
            xbrl_result = self._parse_xbrl(content)
            if xbrl_result:
                result.update(xbrl_result)

        # Fall back to HTML/text regex parsing
        if not result:
            result = self._parse_html_content(content)

        return result

    def _parse_xbrl(self, content: str) -> Dict[str, Any]:
        """Parse XBRL-tagged content from SEC filing."""
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
        for field, config in FINANCIAL_FIELDS.items():
            for tag in config.get("xbrl_tags", []):
                elem = root.find(f".//{ns}{tag}")
                if elem is not None and elem.text:
                    parsed = _format_number(elem.text.strip())
                    if parsed is not None:
                        result[field] = parsed
                        break

        return result

    def _parse_html_content(self, content: str) -> Dict[str, Any]:
        """Parse financial data from HTML/text filing content using regex."""
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text()

        result = {}
        for field, config in FINANCIAL_FIELDS.items():
            for pattern in config.get("html_patterns", []):
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    parsed = _format_number(match.group(1))
                    if parsed is not None:
                        result[field] = parsed
                        break

        return result

    # ─── XBRL Company Facts API (PRIMARY DATA SOURCE) ──────────────────

    def get_xbrl_facts(self, cik: str, form_types: Optional[List[str]] = None,
                       as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """Fetch XBRL-tagged financial facts via the SEC Company Facts API.

        This is the PRIMARY data source for fundamental analysis. Returns
        structured JSON with all XBRL-tagged financial line items, organized
        by taxonomy → concept → unit → period.

        API docs: https://www.sec.gov/edgar/searchedgar/webxbrl.htm
        Endpoint: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json

        Response structure:
        {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [{"val": 12345, "unit": "USD",
                                     "end": "2025-09-27", "form": "10-K",
                                     "accesionNumber": "...",
                                     "filed": "2025-11-15"}]
                        }
                    }
                }
            }
        }

        Args:
            cik: CIK code (zero-padded)
            form_types: Filter by form types (e.g., ["10-K", "10-Q"]).
                        Defaults to ["10-K"] (annual only).
            as_of_date: Optional date to filter to. If provided, returns the
                        most recent filing on or before this date.

        Returns:
            Dict mapping field_name → numeric_value (e.g., {"net_income": 12345.0}).
            Only fields with valid data are included.
        """
        if form_types is None:
            form_types = ["10-K"]

        cik = cik.zfill(10)
        cache_key = f"xbrl_{cik}_{form_types}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        resp = self._get(url)
        if not resp:
            return {}

        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from XBRL Company Facts API for CIK {cik}")
            return {}

        # Parse the structured XBRL data
        facts_data = data.get("facts", {})
        result = {}

        for field_name, xbrl_concepts in XBRL_CONCEPT_MAP.items():
            for concept in xbrl_concepts:
                # Navigate: taxonomy → concept → units → facts
                taxonomy_data = facts_data.get(concept, {})
                units_data = taxonomy_data.get("units", {})
                usd_units = units_data.get("USD", [])
                shares_units = units_data.get("shares", [])

                # Determine which unit list to use
                # Most fields use USD; shares_outstanding uses shares
                if field_name == "shares_outstanding":
                    fact_list = shares_units if shares_units else usd_units
                else:
                    fact_list = usd_units

                if not fact_list:
                    continue

                # Filter by form type
                filtered = [
                    f for f in fact_list
                    if any(ft.replace("-", "").upper() == f.get("form", "").replace("-", "")
                           for ft in form_types)
                ]

                if not filtered:
                    continue

                # Sort by report date (most recent first)
                filtered.sort(key=lambda x: x.get("end", ""), reverse=True)

                # Apply as_of_date filter if specified
                if as_of_date:
                    filtered = [f for f in filtered if f.get("end", "") <= as_of_date]
                    if not filtered:
                        continue
                    # Re-sort after filtering
                    filtered.sort(key=lambda x: x.get("end", ""), reverse=True)

                # Take the most recent value
                latest = filtered[0]
                val = latest.get("val")
                if val is not None:
                    result[field_name] = float(val)
                    break  # Found a value for this field

        # Cache the parsed result
        self._save_cache(cache_key, result)
        return result

    def get_company_facts(self, cik: str) -> Optional[Dict[str, Any]]:
        """Get raw XBRL Company Facts API response for debugging.

        DEPRECATED: Use get_xbrl_facts() instead for parsed financial data.
        This returns the raw API response structure.

        Args:
            cik: CIK code

        Returns:
            Raw API response dict, or None on failure.
        """
        cache_key = f"facts_raw_{cik}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        resp = self._get(url)
        if not resp:
            return None

        try:
            data = resp.json()
            self._save_cache(cache_key, data)
            return data
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from XBRL Company Facts API for CIK {cik}")
            return None

    # ─── Caching ───────────────────────────────────────────────────────

    def _get_cache_key(self, key: str) -> str:
        """Generate cache filename from key."""
        return f"sec_{key}.json"

    def _load_cache(self, key: str) -> Optional[Any]:
        """Load cached data if fresh."""
        cache_file = self.cache_dir / self._get_cache_key(key)
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r") as f:
                data = json.load(f)

            cached_time = data.get("cached_at", 0)
            if time.time() - cached_time < SEC_CACHE_TTL_HOURS * 3600:
                return data.get("data")
            return None
        except Exception as e:
            logger.debug(f"Cache load failed for {key}: {e}")
            return None

    def _save_cache(self, key: str, data: Any):
        """Save data to cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / self._get_cache_key(key)

        try:
            with open(cache_file, "w") as f:
                json.dump({
                    "data": data,
                    "cached_at": time.time(),
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"Cache save failed for {key}: {e}")


# ─── Convenience Functions ───────────────────────────────────────────────────

# Module-level client instance (reuses session, respects rate limits)
_client = None

def _get_client() -> SecEdgarClient:
    """Get or create the module-level SEC client."""
    global _client
    if _client is None:
        _client = SecEdgarClient()
    return _client


def get_cik(ticker: str) -> Optional[str]:
    """Get CIK code for a ticker."""
    return _get_client().get_cik(ticker)


def get_filings(ticker: str, forms: Optional[List[str]] = None,
                limit: int = 10) -> List[Dict[str, Any]]:
    """Get SEC filings for a ticker.

    Args:
        ticker: Ticker symbol
        forms: Form types to filter (e.g., ["10-K", "10-Q"])
        limit: Max filings to return

    Returns:
        List of filing dicts.
    """
    cik = _get_client().get_cik(ticker)
    if not cik:
        return []
    return _get_client().get_filings(cik, forms=form, limit=limit)


def get_10k_filings(ticker: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Get recent 10-K filings for a ticker."""
    cik = _get_client().get_cik(ticker)
    if not cik:
        return []
    return _get_client().get_10k_filings(cik, limit=limit)


def get_10q_filings(ticker: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent 10-Q filings for a ticker."""
    cik = _get_client().get_cik(ticker)
    if not cik:
        return []
    return _get_client().get_10q_filings(cik, limit=limit)


def parse_filing(ticker: str, form: str = "10-K", limit: int = 1) -> List[Dict[str, Any]]:
    """Fetch and parse financial data from a ticker's SEC filings.

    Args:
        ticker: Ticker symbol
        form: Form type to fetch ("10-K" or "10-Q")
        limit: Number of recent filings to parse

    Returns:
        List of parsed financial data dicts, one per filing.
    """
    cik = _get_client().get_cik(ticker)
    if not cik:
        return []

    filings = _get_client().get_filings(cik, forms=[form], limit=limit)
    results = []

    for filing in filings:
        content = _get_client().get_filing_content(cik, filing["accessionNumber"])
        if content:
            parsed = _get_client().parse_filing_content(content)
            parsed["filing_date"] = filing["filingDate"]
            parsed["report_date"] = filing["reportDate"]
            parsed["form"] = filing["form"]
            parsed["ticker"] = ticker
            results.append(parsed)

    return results


# ─── Utility Functions ───────────────────────────────────────────────────────

def _format_number(value) -> Optional[float]:
    """Parse a number string, handling K/M/B suffixes and parentheses for negatives.

    Args:
        value: String, int, or float to parse.

    Returns:
        Float value, or None if parsing fails.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None

    # Remove dollar signs, commas, and parentheses
    s = s.replace("$", "").replace(",", "")

    # Handle negative in parentheses: (1,234.56) → -1234.56
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # Try direct parse
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        pass

    # Handle K/M/B suffixes
    if s and s[-1].upper() in "KMBT":
        try:
            num = float(s[:-1])
            multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
            result = num * multipliers[s[-1].upper()]
            return -result if negative else result
        except ValueError:
            pass

    return None


def get_company_name(cik: str) -> Optional[str]:
    """Get company name from SEC filings data.

    Args:
        cik: CIK code

    Returns:
        Company name string, or None.
    """
    resp = _get_client()._get(f"{SEC_SUBMISSIONS_URL}/CIK{cik.zfill(10)}.json")
    if not resp:
        return None
    try:
        data = resp.json()
        return data.get("name", "")
    except (json.JSONDecodeError, KeyError):
        return None
