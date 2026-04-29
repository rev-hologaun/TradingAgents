import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.4",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "tradestation",
        "technical_indicators": "tradestation",
        "fundamental_data": "local_fundamentals",  # SEC EDGAR scraper — no API key needed
        "news_data": "rss",
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "tradestation",  # Override category default
    },
}

# Local/vLLM configuration variant.
#
# Use this when running TradingAgents against a local vLLM endpoint
# (e.g. Qwen3.6-35B-A3B on gojira's RTX 6000) with TradeStation data
# and RSS news feeds.
#
# Usage:
#   from tradingagents.default_config import DEFAULT_CONFIG_LOCAL
#   ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG_LOCAL)
#
# Note: This config requires:
#   - OpenClaw runtime (for TradeStation MCP tools)
#   - feedparser library (for RSS news): pip install feedparser
#   - vLLM endpoint accessible at backend_url
#   - SEC EDGAR access for fundamentals (free, no API key needed)
#
# All data sources are now self-contained: TradeStation (OHLCV/quotes),
# local_fundamentals (SEC EDGAR), RSS (news). No external API keys required.
DEFAULT_CONFIG_LOCAL = {
    **DEFAULT_CONFIG,
    "llm_provider": "openai",
    "backend_url": "http://192.168.50.144:8036/v1",
    "deep_think_llm": "Qwen/Qwen3.6-35B-A3B-FP8",
    "quick_think_llm": "Qwen/Qwen3.6-35B-A3B-FP8",
    "data_vendors": {
        "core_stock_apis": "tradestation",
        "technical_indicators": "tradestation",
        "fundamental_data": "local_fundamentals",  # SEC EDGAR scraper — no API key needed
        "news_data": "rss",
    },
}

# Gemma configuration variant.
#
# Use this when running TradingAgents against the Gemma-4-26B endpoint
# (cloud19/gemma-4-26B-A4B-it-heretic-FP8-Static) with TradeStation data
# and RSS news feeds.
#
# Usage:
#   from tradingagents.default_config import DEFAULT_CONFIG_GEMMA
#   ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG_GEMMA)
#
# Note: This config requires:
#   - OpenClaw runtime (for TradeStation MCP tools)
#   - feedparser library (for RSS news): pip install feedparser
#   - Gemma endpoint accessible at backend_url
#   - SEC EDGAR access for fundamentals (free, no API key needed)
#
# All data sources are now self-contained: TradeStation (OHLCV/quotes),
# local_fundamentals (SEC EDGAR), RSS (news). No external API keys required.
DEFAULT_CONFIG_GEMMA = {
    **DEFAULT_CONFIG,
    "llm_provider": "openai",
    "backend_url": "http://192.168.50.144:8040/v1",
    "deep_think_llm": "cloud19/gemma-4-26B-A4B-it-heretic-FP8-Static",
    "quick_think_llm": "cloud19/gemma-4-26B-A4B-it-heretic-FP8-Static",
    "data_vendors": {
        "core_stock_apis": "tradestation",
        "technical_indicators": "tradestation",
        "fundamental_data": "local_fundamentals",  # SEC EDGAR scraper — no API key needed
        "news_data": "rss",
    },
}
