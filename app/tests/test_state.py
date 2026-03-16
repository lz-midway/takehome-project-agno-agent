"""
tests/test_state.py — Unit tests for deterministic, non-LLM logic.

Tests:
  1. parse_time_period() — date resolution across all supported formats
  2. Pydantic model validation — field constraints and enum coercion
  3. Edge cases — boundary inputs, invalid formats

Run:
    python -m pytest tests/test_state.py -v
"""

import sys
from datetime import date
from pathlib import Path

import pytest

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.state import (
    ContentStatus,
    EnrichedArticle,
    EnrichedArticleList,
    EventType,
    FinalReport,
    RawArticle,
    RawArticleList,
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
        assert end == self.ANCHOR
        assert (end - start).days == 14

    def test_last_n_months(self):
        start, end = parse_time_period("last 3 months", anchor=self.ANCHOR)
        assert end == self.ANCHOR
        assert (end - start).days == 90

    def test_last_n_years(self):
        start, end = parse_time_period("last 1 year", anchor=self.ANCHOR)
        assert end == self.ANCHOR
        assert (end - start).days == 365

    def test_past_synonym(self):
        start1, _ = parse_time_period("last 30 days", anchor=self.ANCHOR)
        start2, _ = parse_time_period("past 30 days", anchor=self.ANCHOR)
        assert start1 == start2

    def test_iso_range(self):
        start, end = parse_time_period("2024-01-01 to 2024-03-31")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 3, 31)

    def test_quarter_q1(self):
        start, end = parse_time_period("Q1 2024")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 3, 31)

    def test_quarter_q4(self):
        start, end = parse_time_period("Q4 2023")
        assert start == date(2023, 10, 1)
        assert end == date(2023, 12, 31)

    def test_plural_month(self):
        # "months" (plural) should also work
        start, end = parse_time_period("last 6 months", anchor=self.ANCHOR)
        assert end == self.ANCHOR

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_time_period("sometime recently")

    def test_iso_range_inverted_raises(self):
        with pytest.raises(ValueError, match="after end_date"):
            parse_time_period("2024-06-01 to 2024-01-01")

    def test_case_insensitive(self):
        # Input may be mixed case
        start, end = parse_time_period("Last 3 Months", anchor=self.ANCHOR)
        assert end == self.ANCHOR


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------

class TestWorkflowInput:
    def test_valid(self):
        wi = WorkflowInput(
            company_name="Apple Inc",
            time_period="last 3 months",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert wi.company_name == "Apple Inc"

    def test_date_types(self):
        wi = WorkflowInput(
            company_name="TSMC",
            time_period="Q1 2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert isinstance(wi.start_date, date)
        assert isinstance(wi.end_date, date)


class TestResearchPlan:
    def test_valid_plan(self):
        plan = ResearchPlan(
            company_name="Apple Inc",
            ticker="AAPL",
            aliases=["Apple"],
            queries=[
                SearchQuery(query="Apple earnings Q1 2024", category="earnings"),
                SearchQuery(query="Apple CEO Tim Cook", category="leadership"),
            ],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert len(plan.queries) == 2
        assert plan.max_articles_per_query == 4  # default

    def test_optional_ticker(self):
        plan = ResearchPlan(
            company_name="Anthropic",
            queries=[SearchQuery(query="Anthropic AI news", category="general")],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        assert plan.ticker is None


class TestRawArticle:
    def test_default_content_status(self):
        article = RawArticle(
            headline="Apple reports record revenue",
            publisher="Reuters",
            timestamp_raw="3 days ago",
            url="https://reuters.com/article/1",
            query_source="Apple earnings",
        )
        assert article.content_status == ContentStatus.AVAILABLE
        assert article.body_text is None

    def test_unavailable_article(self):
        article = RawArticle(
            headline="Paywalled story",
            publisher="WSJ",
            timestamp_raw="2024-03-10",
            url="https://wsj.com/article/2",
            body_text=None,
            content_status=ContentStatus.UNAVAILABLE,
            query_source="Apple news",
        )
        assert article.content_status == ContentStatus.UNAVAILABLE


class TestEnrichedArticle:
    def test_sentiment_confidence_bounds(self):
        article = EnrichedArticle(
            headline="Good news",
            publisher="Bloomberg",
            timestamp_raw="2024-03-01",
            url="https://bloomberg.com/1",
            body_text="Revenue grew 20% year-over-year.",
            content_status=ContentStatus.AVAILABLE,
            query_source="Apple earnings",
            normalized_date=date(2024, 3, 1),
            sentiment=SentimentLabel.POSITIVE,
            sentiment_confidence=0.9,
            sentiment_rationale="Article highlights 20% revenue growth.",
            event_types=[EventType.EARNINGS],
        )
        assert 0.0 <= article.sentiment_confidence <= 1.0

    def test_invalid_confidence_rejected(self):
        with pytest.raises(Exception):
            EnrichedArticle(
                headline="h",
                publisher="p",
                timestamp_raw="t",
                url="u",
                content_status=ContentStatus.AVAILABLE,
                query_source="q",
                normalized_date=date(2024, 1, 1),
                sentiment=SentimentLabel.NEUTRAL,
                sentiment_confidence=1.5,  # > 1.0 → invalid
                sentiment_rationale="r",
                event_types=[],
            )


class TestRawArticleList:
    def test_empty_list(self):
        ral = RawArticleList()
        assert ral.articles == []
        assert ral.total_queries_executed == 0

    def test_populated(self):
        ral = RawArticleList(
            articles=[
                RawArticle(
                    headline="h",
                    publisher="p",
                    timestamp_raw="t",
                    url="u",
                    query_source="q",
                )
            ],
            total_queries_executed=1,
        )
        assert len(ral.articles) == 1


class TestWorkflowStatus:
    def test_degraded_flag(self):
        assert WorkflowStatus.DEGRADED == "degraded"

    def test_string_coercion(self):
        assert WorkflowStatus("success") == WorkflowStatus.SUCCESS
