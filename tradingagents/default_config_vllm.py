"""vLLM configuration variant for TradingAgents.

Extends DEFAULT_CONFIG with settings for running against a local vLLM
endpoint (e.g. Qwen3.6-35B-A3B on gojira's RTX 6000).

Usage:
    from tradingagents.default_config_vllm import DEFAULT_CONFIG_VLLM as DEFAULT_CONFIG

    ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG_VLLM)
"""

from .default_config import DEFAULT_CONFIG

DEFAULT_CONFIG_VLLM = {
    **DEFAULT_CONFIG,
    # LLM settings — use "openai" provider since vLLM is OpenAI-compatible
    "llm_provider": "openai",
    "backend_url": "http://192.168.50.144:8036/v1",
    "deep_think_llm": "Qwen/Qwen3.6-35B-A3B",
    "quick_think_llm": "Qwen/Qwen3.6-35B-A3B",
    # Data vendor configuration — use TradeStation + RSS
    "data_vendors": {
        "core_stock_apis": "tradestation",
        "technical_indicators": "tradestation",
        "fundamental_data": "local_fundamentals",  # SEC EDGAR scraper — no API key needed
        "news_data": "rss",
    },
    # No API keys needed for local vLLM, but set a dummy key to satisfy
    # langchain-openai's requirement for api_key
    "api_key": "dummy",
}
