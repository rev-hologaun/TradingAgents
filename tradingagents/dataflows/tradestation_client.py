"""Standalone TradeStation REST API client.

Uses the OAuth2 credentials from ~/projects/tfresh2/ to authenticate
and make REST API calls to TradeStation (sim or live).

Handles:
- Token loading and automatic refresh via refresh_token grant
- Rate limiting (TradeStation: 300 req/min on SIM, 120 req/min on LIVE)
- All data endpoints needed by TradingAgents: quotes, bars, symbol details,
  positions, orders, option chain, market depth
"""

import configparser
import json
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TFRESH2_DIR = Path(os.path.expanduser("~/projects/tfresh2"))
CONFIG_PATH = TFRESH2_DIR / "config.ini"
TOKEN_PATH = TFRESH2_DIR / "token.json"


class TradeStationClient:
    """Standalone TradeStation REST API client.

    Reads credentials from ~/projects/tfresh2/config.ini and token.json.
    Auto-refreshes access tokens using the refresh_token grant.
    """

    # Rate limits: SIM = 300/min, LIVE = 120/min
    RATE_LIMIT_PER_MIN = 300  # default to SIM; overridden from config
    RATE_LIMIT_WINDOW = 60  # seconds

    def __init__(
        self,
        config_path: str = None,
        token_path: str = None,
    ):
        self.config_path = config_path or str(CONFIG_PATH)
        self.token_path = token_path or str(TOKEN_PATH)

        # Config
        self.client_id: str = ""
        self.client_secret: str = ""
        self.redirect_uri: str = ""
        self.api_base_url: str = "https://sim-api.tradestation.com"
        self.environment: str = "SIM"
        self.auth_base: str = "https://signin.tradestation.com/v3"
        self.token_url: str = "https://signin.tradestation.com/oauth/token"
        self.scope: str = "openid profile MarketData ReadAccount Trade offline_access"

        # Token state
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.expires_at: float = 0
        self._lock = threading.Lock()

        # Rate limiting
        self._request_timestamps: List[float] = []

        # Load config and token
        self._load_config()
        self._load_token()

    # ------------------------------------------------------------------
    # Config / Token loading
    # ------------------------------------------------------------------

    def _load_config(self):
        """Load TradeStation config from config.ini."""
        try:
            cp = configparser.ConfigParser()
            cp.read(self.config_path)
            if "tradestation" in cp:
                self.client_id = cp["tradestation"].get("client_id", self.client_id)
                self.client_secret = cp["tradestation"].get("client_secret", self.client_secret)
                self.redirect_uri = cp["tradestation"].get("redirect_uri", self.redirect_uri)
                self.api_base_url = cp["tradestation"].get("api_base_url", self.api_base_url)
                self.environment = cp["tradestation"].get("environment", "SIM").upper()
                self.auth_base = cp["tradestation"].get("auth_base", self.auth_base)
                self.token_url = cp["tradestation"].get("token_url", self.token_url)
                self.scope = cp["tradestation"].get("scope", self.scope)
                # Set rate limit based on environment
                if self.environment == "LIVE":
                    self.RATE_LIMIT_PER_MIN = 120
        except Exception as e:
            print(f"[TradeStation] Warning: Could not load config from {self.config_path}: {e}")

    def _load_token(self) -> bool:
        """Load token from token.json. Returns True if loaded successfully."""
        try:
            with open(self.token_path, "r") as f:
                token_data = json.load(f)

            self.access_token = token_data.get("access_token", "")
            self.refresh_token = token_data.get("refresh_token", "")
            self.expires_at = token_data.get("expires_at", 0)

            if self.access_token:
                return True
        except Exception as e:
            print(f"[TradeStation] Warning: Could not load token from {self.token_path}: {e}")

        return False

    def _save_token(self, token_data: Dict[str, Any]):
        """Persist token data to token.json."""
        try:
            with open(self.token_path, "w") as f:
                json.dump(token_data, f, indent=2)
        except Exception as e:
            print(f"[TradeStation] Error saving token: {e}")

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def is_token_valid(self) -> bool:
        """Check if the current access token is still valid (with 5-min safety margin)."""
        if not self.access_token:
            return False
        return time.time() < (self.expires_at - 300)

    def refresh_token_if_needed(self) -> bool:
        """Refresh the access token using the refresh_token grant if needed."""
        if self.is_token_valid():
            return True

        if not self.refresh_token:
            print("[TradeStation] No refresh token available. Full auth required.")
            return False

        return self._perform_token_refresh()

    def _perform_token_refresh(self) -> bool:
        """Perform OAuth2 refresh_token grant flow."""
        import requests

        try:
            resp = requests.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": self.scope,
                },
                timeout=30,
            )
            resp.raise_for_status()
            token_data = resp.json()

            with self._lock:
                self.access_token = token_data.get("access_token", "")
                if "refresh_token" in token_data:
                    self.refresh_token = token_data["refresh_token"]
                self.expires_at = time.time() + token_data.get("expires_in", 1200)

            self._save_token(token_data)
            print(f"[TradeStation] Token refreshed at {datetime.now().strftime('%H:%M:%S')}")
            return True

        except Exception as e:
            print(f"[TradeStation] Token refresh failed: {e}")
            return False

    def _ensure_token(self) -> bool:
        """Ensure we have a valid token, refreshing if necessary."""
        with self._lock:
            return self.refresh_token_if_needed()

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self):
        """Enforce rate limiting. Sleeps if we'd exceed the limit."""
        now = time.time()
        window_start = now - self.RATE_LIMIT_WINDOW

        with self._lock:
            # Remove timestamps outside the window
            self._request_timestamps = [
                ts for ts in self._request_timestamps if ts > window_start
            ]

            if len(self._request_timestamps) >= self.RATE_LIMIT_PER_MIN:
                # Calculate how long to wait
                oldest_in_window = self._request_timestamps[0]
                wait_time = oldest_in_window + self.RATE_LIMIT_WINDOW - now
                if wait_time > 0:
                    print(f"[TradeStation] Rate limit hit, waiting {wait_time:.1f}s")
                    time.sleep(wait_time)
                    self._request_timestamps = []

            self._request_timestamps.append(time.time())

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get_headers(self) -> Dict[str, str]:
        """Build request headers with authorization."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        return headers

    def _request(self, method: str, endpoint: str, params: Dict = None, **kwargs) -> Optional[Dict]:
        """Make an authenticated request to the TradeStation API."""
        import requests

        if not self._ensure_token():
            return None

        self._check_rate_limit()

        url = f"{self.api_base_url}{endpoint}"
        headers = self._get_headers()

        try:
            resp = getattr(requests, method)(
                url, headers=headers, params=params, timeout=30, **kwargs
            )

            if resp.status_code == 401:
                # Token expired mid-request — refresh and retry once
                if self._perform_token_refresh():
                    headers = self._get_headers()
                    resp = getattr(requests, method)(
                        url, headers=headers, params=params, timeout=30, **kwargs
                    )

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as e:
            print(f"[TradeStation] HTTP error: {e} (status={e.response.status_code})")
            if e.response is not None:
                print(f"[TradeStation] Response: {e.response.text[:500]}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[TradeStation] Request error: {e}")
            return None

    def get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """GET request to TradeStation API."""
        return self._request("get", endpoint, params=params)

    def post(self, endpoint: str, json_data: Dict = None) -> Optional[Dict]:
        """POST request to TradeStation API."""
        return self._request("post", endpoint, json_data=json_data)

    # ------------------------------------------------------------------
    # Market Data Endpoints
    # ------------------------------------------------------------------

    def get_quotes(self, symbols: List[str]) -> Optional[Dict]:
        """Get current quotes for one or more symbols.

        Endpoint: GET /v3/marketdata/quotes/{symbols}
        Symbols are passed as a path segment, comma-separated.
        Query params are NOT supported for quotes.

        Args:
            symbols: List of ticker symbols (e.g. ["AAPL", "MSFT"])

        Returns:
            Dict with Quotes array, or None on failure
        """
        return self.get(f"/marketdata/quotes/{','.join(symbols)}")

    def get_bars(
        self,
        symbol: str,
        interval: int = 1,
        unit: str = "Daily",
        start_date: str = None,
        end_date: str = None,
        bars_back: int = None,
    ) -> Optional[Dict]:
        """Get historical OHLCV bars.

        Endpoint: GET /v3/marketdata/barcharts/{symbol}

        Args:
            symbol: Ticker symbol
            interval: Bar interval (1-1440 for minutes, 1 for day/week/month)
            unit: Time unit — Minute, Daily, Weekly, Monthly (default: Daily)
            start_date: Start date (ISO format, e.g. "2024-01-01")
            end_date: End date (ISO format)
            bars_back: Number of bars to fetch (mutually exclusive with dates)

        Returns:
            Dict with Bars array, or None on failure
        """
        params = {"interval": str(interval), "unit": unit}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if bars_back:
            params["barsBack"] = str(bars_back)

        return self.get(f"/marketdata/barcharts/{symbol}", params=params)

    def get_symbol_details(self, symbols: List[str]) -> Optional[Dict]:
        """Get symbol metadata.

        Endpoint: GET /v3/marketdata/symbols/{symbol}
        Symbols are passed as a path segment, comma-separated.
        Query params are NOT supported.

        Args:
            symbols: List of ticker symbols

        Returns:
            Dict with Symbols array, or None on failure
        """
        return self.get(f"/marketdata/symbols/{','.join(symbols)}")

    def get_market_depth(self, symbol: str, levels: int = 20) -> Optional[Dict]:
        """Get market depth (order book) for a symbol.

        NOTE: This endpoint is NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            symbol: Ticker symbol
            levels: Number of price levels (1-50)

        Returns:
            Dict with bid/ask depth data, or None on failure
        """
        return self.get(f"/marketdata/stream/marketdepth/aggregates/{symbol}", params={"maxlevels": str(levels)})

    def get_options_chain(
        self,
        symbol: str,
        expiration: str = None,
        strike_proximity: int = 5,
        option_type: str = "all",
    ) -> Optional[Dict]:
        """Get option chain for a symbol.

        NOTE: Options endpoints are NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            symbol: Underlying ticker symbol
            expiration: Expiration date (YYYY-MM-DD). None = next expiration
            strike_proximity: Number of strikes above/below center
            option_type: all, call, put

        Returns:
            Dict with option chain data, or None on failure
        """
        params = {"strikeProximity": str(strike_proximity), "optionType": option_type}
        if expiration:
            params["expiration"] = expiration
        return self.get(f"/marketdata/options/chains/{symbol}", params=params)

    def get_option_expirations(self, symbol: str) -> Optional[Dict]:
        """Get available option expiration dates for a symbol.

        NOTE: Options endpoints are NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            symbol: Underlying ticker symbol

        Returns:
            Dict with expiration dates, or None on failure
        """
        return self.get(f"/marketdata/options/expirations/{symbol}")

    def get_option_strikes(self, symbol: str, expiration: str = None) -> Optional[Dict]:
        """Get available option strike prices for a symbol.

        NOTE: Options endpoints are NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            symbol: Underlying ticker symbol
            expiration: Expiration date (YYYY-MM-DD). None = next expiration

        Returns:
            Dict with strike prices, or None on failure
        """
        params = {"spreadType": "Single", "strikeInterval": "1"}
        if expiration:
            params["expiration"] = expiration
        return self.get(f"/marketdata/options/strikes/{symbol}", params=params)

    # ------------------------------------------------------------------
    # Account Endpoints
    # ------------------------------------------------------------------

    def get_positions(self, account_id: str = None, symbol_filter: str = None) -> Optional[Dict]:
        """Get current positions.

        Endpoint: GET /v3/brokerage/accounts/{account_id}/positions
        Account ID is REQUIRED (path segment, not query param).
        Use comma-separated IDs for multiple accounts: "ACC1,ACC2"

        Args:
            account_id: Account ID (required). Comma-separated for multiple.
            symbol_filter: Optional symbol filter (e.g. "MSFT *" for options)

        Returns:
            Dict with Positions array, or None on failure
        """
        if not account_id:
            print("[TradeStation] Account ID is required for positions")
            return None
        params = {}
        if symbol_filter:
            params["symbol"] = symbol_filter
        return self.get(f"/brokerage/accounts/{account_id}/positions", params=params)

    def get_orders(
        self,
        account_id: str = None,
        status: str = None,
    ) -> Optional[Dict]:
        """Get orders.

        Endpoint: GET /v3/brokerage/accounts/{account_id}/orders
        Account ID is REQUIRED (path segment, not query param).

        NOTE: Date filters (start_date, end_date) are NOT supported via query params.
        Use get_historical_orders() for historical order data.

        Args:
            account_id: Account ID (required). Comma-separated for multiple.
            status: Order status filter (open, filled, cancelled, etc.)

        Returns:
            Dict with Orders array, or None on failure
        """
        if not account_id:
            print("[TradeStation] Account ID is required for orders")
            return None
        params = {"pageSize": "600"}
        if status:
            params["status"] = status
        return self.get(f"/brokerage/accounts/{account_id}/orders", params=params)

    def get_account_balances(self, account_id: str = None) -> Optional[Dict]:
        """Get account balances.

        Endpoint: GET /v3/brokerage/accounts/{account_id}/balances
        Account ID is REQUIRED (path segment, not query param).
        Use comma-separated IDs for multiple accounts.

        Args:
            account_id: Account ID (required). Comma-separated for multiple.

        Returns:
            Dict with Balances array, or None on failure
        """
        if not account_id:
            print("[TradeStation] Account ID is required for balances")
            return None
        return self.get(f"/brokerage/accounts/{account_id}/balances")

    def get_account_list(self) -> Optional[Dict]:
        """Get list of accounts.

        Endpoint: GET /v3/brokerage/accounts

        Returns:
            Dict with Accounts array, or None on failure
        """
        return self.get("/brokerage/accounts")

    # ------------------------------------------------------------------
    # Trading Endpoints
    # ------------------------------------------------------------------

    def place_order(
        self,
        account_id: str,
        symbol: str,
        quantity: int,
        side: str,
        order_type: str,
        limit_price: float = None,
        stop_price: float = None,
        duration: str = "day",
    ) -> Optional[Dict]:
        """Place a new order.

        Endpoint: POST /brokerage/orderexecution/accounts/{account_id}/orders
        NOTE: Order execution is NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            account_id: Account ID
            symbol: Ticker symbol
            quantity: Number of shares
            side: BUY or SELL
            order_type: market, limit, stop, stop_limit
            limit_price: Limit price (required for limit/stop_limit orders)
            stop_price: Stop price (required for stop/stop_limit orders)
            duration: day, gtc, opg, clo, ioc, fok

        Returns:
            Dict with order confirmation, or None on failure
        """
        order = {
            "symbol": symbol,
            "quantity": str(quantity),
            "side": side,
            "orderType": order_type,
            "duration": duration,
        }
        if limit_price is not None:
            order["limitPrice"] = str(limit_price)
        if stop_price is not None:
            order["stopPrice"] = str(stop_price)

        return self.post(
            f"/brokerage/orderexecution/accounts/{account_id}/orders",
            json_data=order,
        )

    def cancel_order(self, order_id: str) -> Optional[Dict]:
        """Cancel an open order.

        Endpoint: POST /brokerage/orderexecution/orders/{order_id}/cancel
        NOTE: Order execution is NOT available in the SIM environment (404).
        Available in LIVE only.

        Args:
            order_id: Order ID to cancel

        Returns:
            Dict with cancellation result, or None on failure
        """
        return self.post(f"/brokerage/orderexecution/orders/{order_id}/cancel")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get client status summary.

        Returns:
            Dict with client status information
        """
        return {
            "environment": self.environment,
            "api_base_url": self.api_base_url,
            "token_valid": self.is_token_valid(),
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "expires_at": datetime.fromtimestamp(self.expires_at).strftime("%Y-%m-%d %H:%M:%S") if self.expires_at else None,
            "rate_limit_per_min": self.RATE_LIMIT_PER_MIN,
        }

    def __repr__(self):
        status = self.get_status()
        return (
            f"<TradeStationClient env={self.environment} "
            f"token_valid={status['token_valid']} "
            f"api={self.api_base_url}>"
        )


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_client_instance: Optional[TradeStationClient] = None
_client_lock = threading.Lock()


def get_client(
    config_path: str = None,
    token_path: str = None,
) -> TradeStationClient:
    """Get (or create) the singleton TradeStation client.

    Args:
        config_path: Path to config.ini (defaults to ~/projects/tfresh2/config.ini)
        token_path: Path to token.json (defaults to ~/projects/tfresh2/token.json)

    Returns:
        TradeStationClient singleton
    """
    global _client_instance

    with _client_lock:
        if _client_instance is None:
            _client_instance = TradeStationClient(
                config_path=config_path,
                token_path=token_path,
            )
        return _client_instance


def reset_client():
    """Reset the singleton client (useful for testing or config changes)."""
    global _client_instance
    with _client_lock:
        _client_instance = None
