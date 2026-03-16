"""
planner.py — Planner Agent

Responsibility: Given a company name and resolved date range, produce a structured
ResearchPlan that tells the Browser Agent exactly which queries to run and how many
results to collect per query.

Tools: None — pure LLM reasoning.
Output: ResearchPlan (Pydantic)
"""

from agno.agent import Agent
from agno.models.anthropic import Claude

from app.models.state import ResearchPlan

PLANNER_INSTRUCTIONS = """\
You are a research planning specialist for a news aggregation system.

Your job is to receive a company name and date range, then produce a structured
research plan for collecting news coverage from DuckDuckGo News.

## Your tasks

1. **Canonical name**: Identify the company's full legal/canonical name.
2. **Ticker**: Provide the stock ticker if the company is publicly traded (null otherwise).
3. **Aliases**: List common alternate names, abbreviations, or subsidiary names that
   may appear in headlines (e.g. "TSMC" for "Taiwan Semiconductor Manufacturing Company").
4. **Search queries**: Generate exactly 5-6 diverse queries using the following categories.
   Each query should be a realistic news search string (not a URL):
   - `general`: broad company news
   - `earnings`: financial results, revenue, profit, guidance
   - `leadership`: CEO, executive changes, board decisions
   - `legal_regulatory`: lawsuits, fines, government investigations, antitrust
   - `product`: new products, launches, recalls, partnerships
   - `partnership`: acquisitions, mergers, joint ventures, deals
5. **max_articles_per_query**: Set to 4 (do not change this value).

## Important rules

- Keep queries concise and naturally readable (2–6 words), not boolean search syntax.
- Include the ticker or canonical short name in at least 3 of the queries.
- Include the date year in the `earnings` query to improve result relevance.
- Respond ONLY with the structured ResearchPlan output — no preamble or explanation.
"""


def create_planner_agent() -> Agent:
    """Return a configured Planner Agent instance."""
    return Agent(
        name="Planner Agent",
        description="Translates a company name and date range into a structured research plan.",
        model=Claude(id="claude-sonnet-4-5"),
        instructions=PLANNER_INSTRUCTIONS,
        output_schema=ResearchPlan,
        # No tools — pure reasoning
        markdown=False,
        debug_mode=False,
    )
