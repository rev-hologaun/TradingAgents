from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# vLLM Local Configuration (uncomment to use local vLLM endpoint)
# =============================================================================
# To run against a local vLLM endpoint (e.g. Qwen3.6-35B-A3B on gojira):
#
#   1. Uncomment the import below
#   2. Comment out the DEFAULT_CONFIG import above
#   3. Ensure the vLLM endpoint is running and accessible
#   4. Ensure feedparser is installed: pip install feedparser
#
# from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM as DEFAULT_CONFIG
# =============================================================================

# Create a custom config
config = DEFAULT_CONFIG.copy()
config["deep_think_llm"] = "gpt-5.4-mini"  # Use a different model
config["quick_think_llm"] = "gpt-5.4-mini"  # Use a different model
config["max_debate_rounds"] = 1  # Increase debate rounds

# Configure data vendors (default uses yfinance, no extra API keys needed)
config["data_vendors"] = {
    "core_stock_apis": "yfinance",           # Options: alpha_vantage, yfinance
    "technical_indicators": "yfinance",      # Options: alpha_vantage, yfinance
    "fundamental_data": "yfinance",          # Options: alpha_vantage, yfinance
    "news_data": "yfinance",                 # Options: alpha_vantage, yfinance
}

# Initialize with custom config
ta = TradingAgentsGraph(debug=True, config=config)

# forward propagate
_, decision = ta.propagate("NVDA", "2024-05-10")
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
