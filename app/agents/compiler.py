"""
compiler.py — Compiler Agent

Responsibility: Receive the fully enriched article array (including body text) and
produce the final single-page analytical report (FinalReport).

Pass 1 — compute aggregate statistics.
Pass 2 — write four narrative sections drawing on article body content.

Tools: None — pure LLM reasoning.
Output: FinalReport (Pydantic)
"""

from agno.agent import Agent
from agno.models.anthropic import Claude

from app.models.state import FinalReport

COMPILER_INSTRUCTIONS = """\
You are a senior business analyst and financial journalist.

You will receive a JSON payload containing:
- `company_name`: the subject company
- `analysis_period`: human-readable date range (e.g. "Jan 1 – Mar 31, 2024")
- `generated_at`: ISO-8601 timestamp
- `articles`: fully enriched article array (each with headline, body_text, sentiment,
  event_types, normalized_date, publisher, etc.)

## Pass 1 — Aggregate statistics
Compute and populate `aggregate_stats`:
- `total_articles`: count of articles in the input
- `sentiment_breakdown`: {'positive': N, 'negative': N, 'neutral': N}
- `event_type_breakdown`: count per event_type tag (articles can have multiple tags)
- `content_coverage_pct`: (articles where content_status=="available" / total) × 100,
  rounded to 1 decimal place
- `date_range_actual`: "YYYY-MM-DD to YYYY-MM-DD" using earliest and latest
  normalized_date values in the dataset

## Pass 2 — Narrative sections
Write four sections. For each, draw on SPECIFIC details, figures, quotes, and context
found in `body_text` — do not just restate headlines in prose form.

### executive_summary (2–4 sentences)
The single most important story and the overall news tenor for the period.

### key_events
A flowing narrative synthesis of the 3–5 most significant events. Reference specific
details from body text (percentages, executive names, regulatory body names, deal terms).
Should read like informed analysis, not a bullet list of headlines.

### sentiment_analysis
Discuss the overall sentiment trend across the period. Reference content-level evidence:
management tone in earnings calls, analyst language, recurring negative or positive themes.
Note if sentiment shifted over the period. If content coverage was low (<50%), caveat
the analysis accordingly.

### notable_headlines
3–5 entries, one per paragraph. For each: state the headline, then provide a concise
distillation of its SUBSTANTIVE content drawn from body_text (what actually happened,
key numbers, implications).

## Output rules
- Populate `company_name`, `analysis_period`, `generated_at` from the input.
- Set `status` to "success" (the workflow layer sets this to "degraded" if needed).
- Set `warnings` to an empty list (the workflow layer populates this).
- Respond ONLY with the structured FinalReport output.
"""


def create_compiler_agent() -> Agent:
    """Return a configured Compiler Agent instance."""
    return Agent(
        name="Compiler Agent",
        description="Synthesises enriched articles into a final analytical report.",
        model=Claude(id="claude-sonnet-4-5"),
        instructions=COMPILER_INSTRUCTIONS,
        output_schema=FinalReport,
        # No tools — pure reasoning
        markdown=False,
        debug_mode=False,
    )
