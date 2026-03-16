"""
browser.py — Browser Agent

Responsibility: Execute every search query in the ResearchPlan, collect article
metadata (headline, publisher, timestamp, URL) from DuckDuckGo News, then follow
each unique URL to extract the article body via Newspaper4k.

Tools: DuckDuckGoTools (news search) + Newspaper4kTools (article body extraction).
Output: RawArticleList (Pydantic)

This is the only agent in the pipeline that is allowed to call external tools.
"""

from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.newspaper4k import Newspaper4kTools

from app.models.state import RawArticleList

BROWSER_INSTRUCTIONS = """\
You are a news research agent specialised in collecting raw article data.

You will receive a JSON research plan. Your job is to:

## Step 1 — Search for articles
For EACH query in the plan's `queries` list:
- Call `duckduckgo_news` with the query string.
- Collect up to `max_articles_per_query` results.
- Record: headline, publisher, timestamp, url, query_source (the query string used).

After all queries, deduplicate articles by URL — keep only the first occurrence.

## Step 2 — Fetch article body text
For EACH unique URL collected:
- Call `read_article` (Newspaper4k) with the URL.
- If it returns body text: set content_status = "available", populate body_text.
- If it fails (paywall, error, empty): set content_status = "unavailable", body_text = null.
  Do NOT discard the article — headline + metadata are still valuable signal.

## Output rules
- Return ALL collected articles (including unavailable ones).
- Set `total_queries_executed` to the number of queries you actually ran.
- Respond ONLY with the structured RawArticleList output — no preamble.
- If a search returns 0 results for a query, skip it and continue to the next.
- Do not fabricate articles. Only include articles you actually retrieved via tools.
"""


def create_browser_agent() -> Agent:
    """Return a configured Browser Agent instance."""
    return Agent(
        name="Browser Agent",
        description="Searches DuckDuckGo News and extracts article body text.",
        model=Claude(id="claude-sonnet-4-5"),
        instructions=BROWSER_INSTRUCTIONS,
        tools=[
            DuckDuckGoTools(news=True, search=False),
            Newspaper4kTools(),
        ],
        output_schema=RawArticleList,
        # Allow enough tool calls for N queries × M articles
        tool_call_limit=60,
        markdown=False,
        debug_mode=False,
    )
