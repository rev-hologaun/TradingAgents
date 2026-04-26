"""Tests for TradeStation data vendor implementation.

These tests use mock data to verify the TradeStation vendor functions
work correctly without requiring the OpenClaw runtime.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestTradeStationStock(unittest.TestCase):
    """Tests for tradestation_stock.py"""

    def setUp(self):
        """Mock the MCP tools to avoid runtime dependency."""
        self.mock_bars = {
            "Bars": [
                {
                    "TimeStamp": "2024-12-01T21:00:00Z",
                    "Open": 185.00,
                    "High": 187.50,
                    "Low": 184.00,
                    "Close": 186.75,
                    "TotalVolume": 50000000,
                },
                {
                    "TimeStamp": "2024-12-02T21:00:00Z",
                    "Open": 186.00,
                    "High": 188.00,
                    "Low": 185.50,
                    "Close": 187.25,
                    "TotalVolume": 48000000,
                },
            ]
        }
        self.mock_quotes = {
            "Quotes": [
                {
                    "Symbol": "AAPL",
                    "Last": 187.25,
                    "Bid": 187.20,
                    "Ask": 187.30,
                    "BidSize": 100,
                    "AskSize": 200,
                    "Volume": 48000000,
                    "Open": 186.00,
                    "High": 188.00,
                    "Low": 185.50,
                    "NetChange": 0.50,
                    "NetChangePct": 0.27,
                }
            ]
        }
        self.mock_details = {
            "Symbols": [
                {
                    "Name": "Apple Inc.",
                    "Description": "Apple Inc.",
                    "Exchange": "NASDAQ",
                    "Category": "Stock",
                }
            ]
        }

    @patch("tradingagents.dataflows.tradestation_stock._get_symbol_details")
    @patch("tradingagents.dataflows.tradestation_stock._get_bars")
    @patch("tradingagents.dataflows.tradestation_stock._get_quote_snapshots")
    def test_get_stock_data(self, mock_quotes, mock_bars, mock_details):
        """Test that get_stock_data returns formatted OHLCV data."""
        mock_bars.return_value = self.mock_bars
        mock_quotes.return_value = self.mock_quotes
        mock_details.return_value = self.mock_details

        from tradingagents.dataflows.tradestation_stock import get_stock_data

        result = get_stock_data("AAPL", "2024-12-01", "2024-12-02")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)
        self.assertIn("Stock data for", result)
        self.assertIn("186.75", result)
        self.assertIn("187.25", result)
        self.assertIn("Current Quote", result)
        self.assertIn("Last: 187.25", result)

    @patch("tradingagents.dataflows.tradestation_stock._get_bars")
    @patch("tradingagents.dataflows.tradestation_stock._get_quote_snapshots")
    def test_get_stock_data_empty_bars(self, mock_quotes, mock_bars):
        """Test get_stock_data with no bar data."""
        mock_bars.return_value = {"Bars": []}
        mock_quotes.return_value = {"Quotes": []}

        from tradingagents.dataflows.tradestation_stock import get_stock_data

        result = get_stock_data("AAPL", "2024-12-01", "2024-12-02")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)
        self.assertIn("Total records: 0", result)

    def test_get_insider_transactions(self):
        """Test that get_insider_transactions returns a placeholder."""
        from tradingagents.dataflows.tradestation_stock import get_insider_transactions

        result = get_insider_transactions("AAPL")

        self.assertIn("Insider transaction data not available", result)
        self.assertIn("TradeStation", result)

    @patch("tradingagents.dataflows.tradestation_stock._get_symbol_details")
    @patch("tradingagents.dataflows.tradestation_stock._get_bars")
    @patch("tradingagents.dataflows.tradestation_stock._get_quote_snapshots")
    def test_get_stock_data_handles_missing_details(self, mock_quotes, mock_bars, mock_details):
        """Test get_stock_data when symbol details lookup fails."""
        mock_bars.return_value = self.mock_bars
        mock_quotes.return_value = self.mock_quotes
        mock_details.side_effect = RuntimeError("Tool not available")

        from tradingagents.dataflows.tradestation_stock import get_stock_data

        result = get_stock_data("AAPL", "2024-12-01", "2024-12-02")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)
        self.assertIn("186.75", result)


class TestTradeStationIndicators(unittest.TestCase):
    """Tests for tradestation_indicators.py"""

    def setUp(self):
        """Mock the MCP tools."""
        self.mock_bars = {
            "Bars": [
                {
                    "TimeStamp": f"2024-11-{i:02d}T21:00:00Z",
                    "Open": 180.0 + i * 0.5,
                    "High": 182.0 + i * 0.5,
                    "Low": 179.0 + i * 0.5,
                    "Close": 181.0 + i * 0.5,
                    "TotalVolume": 50000000 + i * 100000,
                }
                for i in range(1, 31)
            ]
        }

    @patch("tradingagents.dataflows.tradestation_indicators._get_bars")
    def test_get_indicators(self, mock_bars):
        """Test that get_indicators returns formatted indicator data."""
        mock_bars.return_value = self.mock_bars

        from tradingagents.dataflows.tradestation_indicators import get_indicators

        result = get_indicators("AAPL", "2024-11-01", "2024-11-30")

        self.assertIsInstance(result, str)
        self.assertIn("Technical Indicators", result)
        self.assertIn("AAPL", result)
        # Check that some indicators are present
        self.assertIn("RSI", result)
        self.assertIn("MACD", result)
        self.assertIn("Bollinger", result)

    @patch("tradingagents.dataflows.tradestation_indicators._get_bars")
    def test_get_indicators_empty_bars(self, mock_bars):
        """Test get_indicators with no bar data."""
        mock_bars.return_value = {"Bars": []}

        from tradingagents.dataflows.tradestation_indicators import get_indicators

        result = get_indicators("AAPL", "2024-11-01", "2024-11-30")

        self.assertIn("No bar data available", result)

    @patch("tradingagents.dataflows.tradestation_indicators._get_bars")
    def test_get_indicator_specific(self, mock_bars):
        """Test get_indicator for a specific indicator."""
        mock_bars.return_value = self.mock_bars

        from tradingagents.dataflows.tradestation_indicators import get_indicator

        result = get_indicator("AAPL", "rsi", "2024-11-30", look_back_days=30)

        self.assertIsInstance(result, str)
        self.assertIn("RSI", result)
        self.assertIn("2024-11", result)

    def test_get_indicator_invalid(self):
        """Test get_indicator with an invalid indicator name."""
        from tradingagents.dataflows.tradestation_indicators import get_indicator

        result = get_indicator("AAPL", "INVALID_INDICATOR", "2024-11-30")

        self.assertIn("not supported", result)


class TestTradeStationFundamentals(unittest.TestCase):
    """Tests for tradestation_fundamentals.py"""

    @patch("tradingagents.dataflows.tradestation_fundamentals._get_symbol_details")
    def test_get_fundamentals(self, mock_details):
        """Test that get_fundamentals returns limited data with notes."""
        mock_details.return_value = {
            "Symbols": [
                {
                    "Name": "AAPL",
                    "Description": "Apple Inc.",
                    "Exchange": "NASDAQ",
                    "Category": "Stock",
                }
            ]
        }

        from tradingagents.dataflows.tradestation_fundamentals import get_fundamentals

        result = get_fundamentals("AAPL")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)
        self.assertIn("TradeStation", result)
        self.assertIn("limited", result.lower())

    @patch("tradingagents.dataflows.tradestation_fundamentals._get_symbol_details")
    def test_get_fundamentals_no_details(self, mock_details):
        """Test get_fundamentals when details lookup fails."""
        mock_details.side_effect = RuntimeError("Tool not available")

        from tradingagents.dataflows.tradestation_fundamentals import get_fundamentals

        result = get_fundamentals("AAPL")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)

    def test_get_balance_sheet(self):
        """Test that get_balance_sheet returns a limitation note."""
        from tradingagents.dataflows.tradestation_fundamentals import get_balance_sheet

        result = get_balance_sheet("AAPL")

        self.assertIn("TradeStation", result)
        self.assertIn("balance sheet", result.lower())
        self.assertIn("yfinance", result)

    def test_get_cashflow(self):
        """Test that get_cashflow returns a limitation note."""
        from tradingagents.dataflows.tradestation_fundamentals import get_cashflow

        result = get_cashflow("AAPL")

        self.assertIn("TradeStation", result)
        self.assertIn("cash flow", result.lower())

    def test_get_income_statement(self):
        """Test that get_income_statement returns a limitation note."""
        from tradingagents.dataflows.tradestation_fundamentals import get_income_statement

        result = get_income_statement("AAPL")

        self.assertIn("TradeStation", result)
        self.assertIn("income statement", result.lower())


class TestVllmConfig(unittest.TestCase):
    """Tests for the vLLM configuration."""

    def test_default_config_vllm_exists(self):
        """Test that DEFAULT_CONFIG_VLLM can be imported."""
        from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM

        self.assertIsInstance(DEFAULT_CONFIG_VLLM, dict)

    def test_vllm_config_has_backend_url(self):
        """Test that vLLM config has the correct backend URL."""
        from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM

        self.assertEqual(
            DEFAULT_CONFIG_VLLM["backend_url"],
            "http://192.168.50.144:8036/v1",
        )

    def test_vllm_config_has_correct_llm(self):
        """Test that vLLM config has the correct model names."""
        from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM

        self.assertEqual(
            DEFAULT_CONFIG_VLLM["deep_think_llm"],
            "Qwen/Qwen3.6-35B-A3B",
        )
        self.assertEqual(
            DEFAULT_CONFIG_VLLM["quick_think_llm"],
            "Qwen/Qwen3.6-35B-A3B",
        )

    def test_vllm_config_data_vendors(self):
        """Test that vLLM config has the correct data vendors."""
        from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM

        vendors = DEFAULT_CONFIG_VLLM["data_vendors"]
        self.assertEqual(vendors["core_stock_apis"], "tradestation")
        self.assertEqual(vendors["technical_indicators"], "tradestation")
        self.assertEqual(vendors["fundamental_data"], "yfinance")
        self.assertEqual(vendors["news_data"], "rss")

    def test_default_config_local_exists(self):
        """Test that DEFAULT_CONFIG_LOCAL can be imported."""
        from tradingagents.default_config import DEFAULT_CONFIG_LOCAL

        self.assertIsInstance(DEFAULT_CONFIG_LOCAL, dict)

    def test_default_config_local_matches_vllm(self):
        """Test that DEFAULT_CONFIG_LOCAL matches DEFAULT_CONFIG_VLLM settings."""
        from tradingagents.default_config import DEFAULT_CONFIG_LOCAL
        from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM

        self.assertEqual(
            DEFAULT_CONFIG_LOCAL["backend_url"],
            DEFAULT_CONFIG_VLLM["backend_url"],
        )
        self.assertEqual(
            DEFAULT_CONFIG_LOCAL["deep_think_llm"],
            DEFAULT_CONFIG_VLLM["deep_think_llm"],
        )
        self.assertEqual(
            DEFAULT_CONFIG_LOCAL["data_vendors"],
            DEFAULT_CONFIG_VLLM["data_vendors"],
        )


class TestInterfaceVendorRouting(unittest.TestCase):
    """Tests for vendor routing in interface.py."""

    def test_vendor_list_includes_new_vendors(self):
        """Test that VENDOR_LIST includes tradestation and rss."""
        from tradingagents.dataflows.interface import VENDOR_LIST

        self.assertIn("tradestation", VENDOR_LIST)
        self.assertIn("rss", VENDOR_LIST)

    def test_vendor_methods_include_new_implementations(self):
        """Test that VENDOR_METHODS includes new vendor implementations."""
        from tradingagents.dataflows.interface import VENDOR_METHODS

        # Check get_stock_data has tradestation
        self.assertIn("tradestation", VENDOR_METHODS["get_stock_data"])

        # Check get_indicators has tradestation
        self.assertIn("tradestation", VENDOR_METHODS["get_indicators"])

        # Check get_news has rss
        self.assertIn("rss", VENDOR_METHODS["get_news"])

        # Check get_global_news has rss
        self.assertIn("rss", VENDOR_METHODS["get_global_news"])

        # Check fundamentals have tradestation
        self.assertIn("tradestation", VENDOR_METHODS["get_fundamentals"])
        self.assertIn("tradestation", VENDOR_METHODS["get_balance_sheet"])
        self.assertIn("tradestation", VENDOR_METHODS["get_cashflow"])
        self.assertIn("tradestation", VENDOR_METHODS["get_income_statement"])

        # Check insider transactions have tradestation
        self.assertIn("tradestation", VENDOR_METHODS["get_insider_transactions"])

    def test_route_to_vendor_tradestation(self):
        """Test that route_to_vendor can route to tradestation."""
        from tradingagents.dataflows.interface import route_to_vendor, get_vendor, get_category_for_method
        from tradingagents.dataflows.config import set_config

        # Temporarily set tradestation as the vendor
        original_config = {
            "data_vendors": {
                "core_stock_apis": "tradestation",
                "technical_indicators": "tradestation",
                "fundamental_data": "yfinance",
                "news_data": "yfinance",
            }
        }
        set_config(original_config)

        # Verify the vendor resolves to tradestation
        category = get_category_for_method("get_stock_data")
        vendor = get_vendor(category, "get_stock_data")
        self.assertEqual(vendor, "tradestation")

    def test_route_to_vendor_rss(self):
        """Test that route_to_vendor can route to rss."""
        from tradingagents.dataflows.interface import get_vendor, get_category_for_method
        from tradingagents.dataflows.config import set_config

        # Temporarily set rss as the vendor
        original_config = {
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "technical_indicators": "yfinance",
                "fundamental_data": "yfinance",
                "news_data": "rss",
            }
        }
        set_config(original_config)

        # Verify the vendor resolves to rss
        category = get_category_for_method("get_news")
        vendor = get_vendor(category, "get_news")
        self.assertEqual(vendor, "rss")


if __name__ == "__main__":
    unittest.main()
