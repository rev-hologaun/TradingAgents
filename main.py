from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG_LOCAL, DEFAULT_CONFIG_GEMMA

from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Prompt for symbol and date
symbol = input("Enter symbol (default TSLA): ").strip() or "TSLA"
date_input = input("Enter date YYYY-MM-DD (default today): ").strip() or datetime.now().strftime("%Y-%m-%d")

# Model selection
print()
print("Select model:")
print("  1) Qwen/Qwen3.6-35B-A3B-FP8 (default)")
print("  2) cloud19/gemma-4-26B-A4B-it-heretic-FP8-Static")
model_choice = input("Choice [1/2, default 1]: ").strip()

if model_choice == "2":
    config = DEFAULT_CONFIG_GEMMA.copy()
else:
    config = DEFAULT_CONFIG_LOCAL.copy()

config["max_debate_rounds"] = 1

# Initialize with selected config
ta = TradingAgentsGraph(debug=True, config=config)

# Forward propagate
_, decision = ta.propagate(symbol, date_input)
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
