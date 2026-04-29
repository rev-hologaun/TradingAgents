from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG_LOCAL

from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Local vLLM + TradeStation + RSS configuration
config = DEFAULT_CONFIG_LOCAL.copy()
config["max_debate_rounds"] = 1

# Initialize with local config (vLLM on gojira, TradeStation data, RSS news)
ta = TradingAgentsGraph(debug=True, config=config)

# Prompt for symbol and date
symbol = input("Enter symbol (default TSLA): ").strip() or "TSLA"
date_input = input("Enter date YYYY-MM-DD (default today): ").strip() or datetime.now().strftime("%Y-%m-%d")

# forward propagate
_, decision = ta.propagate(symbol, date_input)
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
