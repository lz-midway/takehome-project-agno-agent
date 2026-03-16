"""
extraction.py — Extraction Agent

Responsibility: Transform the raw article list into semantically enriched records by:
  1. Normalising relative timestamps to absolute dates.
  2. Filtering articles outside the resolved date window.
  3. Classifying sentiment (positive / negative / neutral) with confidence + rationale.
  4. Tagging each article with one or more EventType categories.

Tools: None — pure LLM reasoning over the provided text.
Output: EnrichedArticleList (Pydantic)
"""

from agno.agent import Agent
from agno.models.anthropic import Claude

from app.models.state import EnrichedArticleList

EXTRACTION_INSTRUCTIONS = """\
You are a data extraction and sentiment analysis specialist.

You will receive a JSON payload containing:
- `start_date` and `end_date`: the coverage window (YYYY-MM-DD)
- `anchor_date`: today's date used to resolve relative timestamps
- `articles`: a list of raw article records

## Your tasks for EACH article

### 1. Timestamp normalisation
Convert `timestamp_raw` to an absolute ISO date (YYYY-MM-DD) stored in `normalized_date`.
Use `anchor_date` to resolve relative strings like "3 days ago", "last Tuesday", "2h ago".
If the timestamp is unresolvable, estimate from context or set it to `anchor_date`.

### 2. Date filtering
If `normalized_date` falls BEFORE `start_date` or AFTER `end_date`, do NOT include that
article in the output. Increment `discarded_count` instead.

### 3. Sentiment classification
Assign one of: `positive`, `negative`, `neutral`.

Rules:
- When `body_text` is available: derive sentiment primarily from body text, use headline
  as a framing cue. This allows detection of misleading/clickbait headlines.
- When `body_text` is null (content_status = "unavailable"): derive from headline only.
  Discount `sentiment_confidence` by ~0.25 to reflect limited information.
- Provide a `sentiment_rationale` that cites SPECIFIC content (e.g. "Article notes a
  15% revenue decline and management lowered full-year guidance").

### 4. Event categorisation
Tag with one or more of: `earnings`, `leadership`, `legal_regulatory`, `product`,
`partnership`, `other`.
When body_text is available, categorise from body content, not just headline phrasing.

## Output rules
- Include ALL articles that pass the date filter with all enriched fields populated.
- Do NOT paraphrase or truncate body_text — preserve it exactly as received.
- Respond ONLY with the structured EnrichedArticleList output.
"""


def create_extraction_agent() -> Agent:
    """Return a configured Extraction Agent instance."""
    return Agent(
        name="Extraction Agent",
        description="Normalises timestamps, classifies sentiment, and tags event types.",
        model=Claude(id="claude-sonnet-4-5"),
        instructions=EXTRACTION_INSTRUCTIONS,
        output_schema=EnrichedArticleList,
        # No tools — pure reasoning
        markdown=False,
        debug_mode=False,
    )
