"""
main.py — CLI entry point for the News Aggregator system.

Usage:
    python main.py                           # demo defaults
    python main.py "Apple" "last 3 months"
    python main.py "TSMC" "2024-01-01 to 2024-06-30"
    python main.py "Nvidia" "Q1 2024"
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_claude_key = os.environ.get("CLAUDE_API_KEY")
if _claude_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = _claude_key

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: Set CLAUDE_API_KEY in your .env file.", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from app.models.state import ErrorEvent, FinalReport, ProgressEvent, ReportEvent, WorkflowStatus
from app.workflows.news_aggregator import NewsAggregatorWorkflow

DEMO_COMPANY = "Apple Inc"
DEMO_PERIOD  = "last 3 months"


def run_aggregator(company_name: str, time_period: str) -> FinalReport | None:
    """Run the workflow and return the FinalReport, or None on hard failure."""
    workflow = NewsAggregatorWorkflow()
    message  = f"{company_name} | {time_period}"

    print(f"\n{'=' * 60}")
    print(f"  News Aggregator")
    print(f"  Company : {company_name}")
    print(f"  Period  : {time_period}")
    print(f"{'=' * 60}\n")

    final_report: FinalReport | None = None

    for event in workflow.run(message=message):
        if isinstance(event, ProgressEvent):
            print(event.message)

        elif isinstance(event, ErrorEvent):
            prefix = "FATAL" if event.fatal else "WARNING"
            print(f"{prefix} [stage {event.stage}]: {event.message}")
            if event.fatal:
                return None

        elif isinstance(event, ReportEvent):
            final_report = event.report
            _print_report(final_report)

    return final_report


def _print_report(report: FinalReport) -> None:
    sep         = "─" * 60
    status_icon = {"success": "✅", "degraded": "⚠️", "error": "❌"}.get(report.status.value, "?")

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


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 0:
        company, period = DEMO_COMPANY, DEMO_PERIOD
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
