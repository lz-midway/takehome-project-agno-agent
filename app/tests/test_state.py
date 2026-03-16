"""
tests/test_state.py — Unit tests for Pydantic models and date parsing.

Run:
    python -m pytest tests/test_state.py -v
"""

import sys
from datetime import date
from pathlib import Path

import pytest

from app.models.state import (
    AggregateStats,
    BrowserInput,
    CompilerInput,
    ContentStatus,
    EnrichedArticle,
    EnrichedArticleList,
    ErrorEvent,
    EventType,
    ExtractionInput,
    FinalReport,
    PlannerInput,
    ProgressEvent,
    RawArticle,
    RawArticleList,
    ReportEvent,
    ResearchPlan,
    SearchQuery,
    SentimentLabel,
    WorkflowInput,
    WorkflowStatus,
)
from app.workflows.news_aggregator import parse_time_period


# ---------------------------------------------------------------------------
# parse_time_period
# ---------------------------------------------------------------------------

class TestParseTimePeriod:
    ANCHOR = date(2024, 6, 15)

    def test_last_n_days(self):
        start, end = parse_time_period("last 7 days", anchor=self.ANCHOR)
        assert end == self.ANCHOR
        assert (end - start).days == 7

    def test_last_n_weeks(self):
        start, end = parse_time_period("last 2 weeks", anchor=self.ANCHOR)
        assert (end - start).days == 14

    def test_last_n_months(self):
        start, end = parse_time_period("last 3 months", anchor=self.ANCHOR)
        assert (end - start).days == 90

    def test_last_n_years(self):
        start, end = parse_time_period("last 1 year", anchor=self.ANCHOR)
        assert (end - start).days == 365

    def test_past_synonym(self):
        s1, _ = parse_time_period("last 30 days", anchor=self.ANCHOR)
        s2, _ = parse_time_period("past 30 days", anchor=self.ANCHOR)
        assert s1 == s2

    def test_iso_range(self):
        start, end = parse_time_period("2024-01-01 to 2024-03-31")
        assert start == date(2024, 1, 1)
        assert end   == date(2024, 3, 31)

    def test_quarter_q1(self):
        start, end = parse_time_period("Q1 2024")
        assert start == date(2024, 1, 1)
        assert end   == date(2024, 3, 31)

    def test_quarter_q4(self):
        start, end = parse_time_period("Q4 2023")
        assert start == date(2023, 10, 1)
        assert end   == date(2023, 12, 31)

    def test_case_insensitive(self):
        start, end = parse_time_period("Last 3 Months", anchor=self.ANCHOR)
        assert end == self.ANCHOR

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_time_period("sometime recently")

    def test_inverted_iso_raises(self):
        with pytest.raises(ValueError, match="after end_date"):
            parse_time_period("2024-06-01 to 2024-01-01")


# ---------------------------------------------------------------------------
# Typed agent input models
# ---------------------------------------------------------------------------

class TestPlannerInput:
    def test_valid(self):
        inp = PlannerInput(
            company_name="Apple Inc",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert inp.company_name == "Apple Inc"
        assert isinstance(inp.start_date, date)

    def test_serialises_to_json(self):
        inp  = PlannerInput(company_name="TSMC", start_date=date(2024,1,1), end_date=date(2024,3,31))
        data = inp.model_dump()
        assert data["company_name"] == "TSMC"


class TestBrowserInput:
    def test_contains_plan(self):
        plan = ResearchPlan(
            company_name="Apple",
            queries=[SearchQuery(query="Apple news", category="general")],
            start_date=date(2024,1,1),
            end_date=date(2024,3,31),
        )
        inp  = BrowserInput(plan=plan)
        data = inp.model_dump()
        assert data["plan"]["company_name"] == "Apple"


class TestExtractionInput:
    def test_contains_articles(self):
        article = RawArticle(
            headline="h", publisher="p", timestamp_raw="t",
            url="u", query_source="q",
        )
        inp  = ExtractionInput(
            start_date=date(2024,1,1), end_date=date(2024,3,31),
            anchor_date=date(2024,3,31), articles=[article],
        )
        assert len(inp.articles) == 1
        assert inp.anchor_date == date(2024,3,31)


class TestCompilerInput:
    def test_fields_present(self):
        inp = CompilerInput(
            company_name="Apple",
            analysis_period="Jan – Mar 2024",
            generated_at="2024-03-31T00:00:00Z",
            articles=[],
        )
        assert inp.company_name == "Apple"
        assert inp.articles == []


# ---------------------------------------------------------------------------
# Pipeline data models
# ---------------------------------------------------------------------------

class TestWorkflowInput:
    def test_valid(self):
        wi = WorkflowInput(
            company_name="Apple Inc",
            time_period="last 3 months",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert isinstance(wi.start_date, date)


class TestResearchPlan:
    def test_defaults(self):
        plan = ResearchPlan(
            company_name="Anthropic",
            queries=[SearchQuery(query="Anthropic AI news", category="general")],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert plan.ticker is None
        assert plan.max_articles_per_query == 4


class TestRawArticle:
    def test_default_content_status(self):
        a = RawArticle(headline="h", publisher="p", timestamp_raw="t", url="u", query_source="q")
        assert a.content_status == ContentStatus.AVAILABLE
        assert a.body_text is None

    def test_unavailable(self):
        a = RawArticle(
            headline="h", publisher="p", timestamp_raw="t", url="u",
            query_source="q", content_status=ContentStatus.UNAVAILABLE,
        )
        assert a.content_status == ContentStatus.UNAVAILABLE


class TestEnrichedArticle:
    def test_confidence_bounds(self):
        a = EnrichedArticle(
            headline="h", publisher="p", timestamp_raw="t", url="u",
            body_text="Revenue grew 20% year-over-year.",
            content_status=ContentStatus.AVAILABLE, query_source="q",
            normalized_date=date(2024,3,1),
            sentiment=SentimentLabel.POSITIVE,
            sentiment_confidence=0.9,
            sentiment_rationale="Strong revenue growth",
            event_types=[EventType.EARNINGS],
        )
        assert 0.0 <= a.sentiment_confidence <= 1.0

    def test_invalid_confidence_rejected(self):
        with pytest.raises(Exception):
            EnrichedArticle(
                headline="h", publisher="p", timestamp_raw="t", url="u",
                body_text=None,
                content_status=ContentStatus.AVAILABLE, query_source="q",
                normalized_date=date(2024,1,1),
                sentiment=SentimentLabel.NEUTRAL,
                sentiment_confidence=1.5,  # > 1.0 → invalid
                sentiment_rationale="r",
                event_types=[],
            )


# ---------------------------------------------------------------------------
# WorkflowEvent models
# ---------------------------------------------------------------------------

class TestWorkflowEvents:
    def test_progress_event(self):
        e = ProgressEvent(stage=2, message="Running Browser…")
        assert e.kind == "progress"
        assert e.stage == 2

    def test_error_event_defaults_fatal(self):
        e = ErrorEvent(stage=1, message="Planner failed")
        assert e.fatal is True
        assert e.kind == "error"

    def test_error_event_non_fatal(self):
        e = ErrorEvent(stage=3, message="Low article count", fatal=False)
        assert e.fatal is False

    def test_report_event(self):
        report = FinalReport(
            company_name="Apple",
            analysis_period="Q1 2024",
            generated_at="2024-03-31T00:00:00Z",
            aggregate_stats=AggregateStats(
                total_articles=5,
                sentiment_breakdown={"positive": 3, "negative": 1, "neutral": 1},
                event_type_breakdown={"earnings": 2},
                content_coverage_pct=80.0,
                date_range_actual="2024-01-10 to 2024-03-25",
            ),
            executive_summary="Good quarter.",
            key_events="Revenue up.",
            sentiment_analysis="Mostly positive.",
            notable_headlines="1. Record revenue.",
        )
        e = ReportEvent(report=report, report_path="data/x.json", obs_path="data/logs/x.json")
        assert e.kind == "report"
        assert e.report.status == WorkflowStatus.SUCCESS
