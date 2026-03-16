"""
main.py — Entry point for the News Aggregator system.

Usage:
    python main.py                           # uses demo defaults
    python main.py "Apple" "last 3 months"
    python main.py "TSMC" "2024-01-01 to 2024-06-30"
    python main.py "Nvidia" "Q1 2024"
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment setup — must happen before any Agno / Anthropic imports
# ---------------------------------------------------------------------------

load_dotenv()

# Agno's Anthropic integration reads ANTHROPIC_API_KEY.
# The project stores it as CLAUDE_API_KEY in .env — bridge the gap here.
_claude_key = os.environ.get("CLAUDE_API_KEY")
if _claude_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = _claude_key

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(
        "ERROR: No API key found. Set CLAUDE_API_KEY (or ANTHROPIC_API_KEY) "
        "in your .env file.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow import (after env is set)
# ---------------------------------------------------------------------------

from app.models.state import FinalReport, WorkflowStatus  # noqa: E402
from app.workflows.news_aggregator import NewsAggregatorWorkflow  # noqa: E402

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEMO_COMPANY = "Apple Inc"
DEMO_PERIOD = "last 3 months"


def run_aggregator(company_name: str, time_period: str) -> FinalReport | None:
    """Run the workflow and return the FinalReport, or None on hard failure."""
    workflow = NewsAggregatorWorkflow()

    final_report: FinalReport | None = None

    print(f"\n{'=' * 60}")
    print(f"  News Aggregator")
    print(f"  Company : {company_name}")
    print(f"  Period  : {time_period}")
    print(f"{'=' * 60}\n")

    for response in workflow.run(company_name=company_name, time_period=time_period):
        content = response.content

        # Progress messages are plain strings
        if isinstance(content, str):
            print(content)

        # The final response carries the structured FinalReport
        elif isinstance(content, FinalReport):
            final_report = content

    if final_report is None:
        logger.error("Workflow ended without producing a FinalReport.")
        return None

    # Pretty-print the report to stdout
    _print_report(final_report)
    return final_report


def _print_report(report: FinalReport) -> None:
    """Render the FinalReport to stdout in a human-readable format."""
    sep = "─" * 60
    status_icon = {"success": "✅", "degraded": "⚠️", "error": "❌"}.get(
        report.status.value, "?"
    )

    print(f"\n{sep}")
    print(f"  {status_icon}  FINAL REPORT — {report.company_name.upper()}")
    print(f"  Period: {report.analysis_period}")
    print(f"  Generated: {report.generated_at}")
    print(sep)

    s = report.aggregate_stats
    print(f"\n📊 AGGREGATE STATISTICS")
    print(f"  Total articles  : {s.total_articles}")
    print(f"  Sentiment       : {s.sentiment_breakdown}")
    print(f"  Event types     : {s.event_type_breakdown}")
    print(f"  Body coverage   : {s.content_coverage_pct:.1f}%")
    print(f"  Actual range    : {s.date_range_actual}")

    if report.warnings:
        print(f"\n⚠️  WARNINGS")
        for w in report.warnings:
            print(f"  • {w}")

    print(f"\n📝 EXECUTIVE SUMMARY\n{report.executive_summary}")
    print(f"\n📅 KEY EVENTS\n{report.key_events}")
    print(f"\n💬 SENTIMENT ANALYSIS\n{report.sentiment_analysis}")
    print(f"\n🗞️  NOTABLE HEADLINES\n{report.notable_headlines}")
    print(f"\n{sep}\n")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    if len(args) == 0:
        company = DEMO_COMPANY
        period = DEMO_PERIOD
        logger.info(f"No arguments provided — using demo defaults: {company!r}, {period!r}")
    elif len(args) == 2:
        company, period = args[0], args[1]
    else:
        print(
            "Usage: python main.py [<company_name> <time_period>]\n"
            "Examples:\n"
            "  python main.py\n"
            '  python main.py "Apple" "last 3 months"\n'
            '  python main.py "TSMC" "2024-01-01 to 2024-06-30"',
            file=sys.stderr,
        )
        sys.exit(1)

    result = run_aggregator(company, period)
    sys.exit(0 if result and result.status != WorkflowStatus.ERROR else 1)
