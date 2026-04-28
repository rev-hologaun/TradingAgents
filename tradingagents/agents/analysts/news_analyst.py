from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past week. "
            "Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. "
            "Use the available tools: get_news(query, start_date, end_date) for company-specific or targeted news searches, "
            "and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. "
            "Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """

**CRITICAL TOOL USAGE RULES — READ CAREFULLY:**

1. Call `get_news` ONCE with the company ticker and a 7-day lookback (start_date = curr_date minus 7 days, end_date = curr_date).
2. IMMEDIATELY call `get_global_news` in the SAME turn alongside `get_news` — do NOT wait for get_news results.
3. If `get_news` returns empty results, "No news found", or any empty/missing response, DO NOT retry `get_news` with wider date ranges. Immediately use `get_global_news` for macro context.
4. NEVER retry `get_news` with escalating date ranges (7d → 14d → 90d → 1y). This wastes turns and delays your report.
5. After getting results from both tools, synthesize a single comprehensive report from whatever data is available.
6. Your goal is to produce ONE report, not to exhaustively search for news. If no news is found, state that clearly and proceed with macro analysis.
"""
            + """
Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
