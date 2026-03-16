"""
tests/test_workflow.py — Workflow-level unit tests.

These tests exercise the workflow's orchestration logic, retry behaviour,
and failure-handling paths WITHOUT making real LLM calls.

Strategy:
  - Patch `with_retry` to call the underlying fn once
  - Patch each agent's `.run()` method to return a fabricated RunResponse
    whose `.content` is the expected Pydantic model
  - Test both happy paths and hard/soft failure modes

Run:
    python -m pytest tests/test_workflow.py -v
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set a dummy API key so imports don't fail
import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

from app.models.state import (
    AggregateStats,
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
    WorkflowStatus,
)
from app.workflows.news_aggregator import (
    NewsAggregatorWorkflow,
    ObservabilityTracker,
    parse_time_period,
    with_retry,
)


# ---------------------------------------------------------------------------
# Fixtures — fabricated agent outputs
# ---------------------------------------------------------------------------

TODAY = date.today()

def _make_research_plan() -> ResearchPlan:
    return ResearchPlan(
        company_name="Apple Inc",
        ticker="AAPL",
        aliases=["Apple"],
        queries=[
            SearchQuery(query="Apple news", category="general"),
            SearchQuery(query="AAPL earnings", category="earnings"),
        ],
        start_date=TODAY - timedelta(days=90),
        end_date=TODAY,
    )


def _make_raw_article_list() -> RawArticleList:
    return RawArticleList(
        articles=[
            RawArticle(
                headline="Apple Reports Record Q1 Revenue",
                publisher="Reuters",
                timestamp_raw="2024-02-01",
                url="https://reuters.com/apple-q1",
                body_text="Apple revenue rose 8% to $119bn...",
                content_status=ContentStatus.AVAILABLE,
                query_source="AAPL earnings",
            ),
            RawArticle(
                headline="Apple Faces EU Antitrust Probe",
                publisher="FT",
                timestamp_raw="10 days ago",
                url="https://ft.com/apple-eu",
                body_text="The European Commission launched an investigation...",
                content_status=ContentStatus.AVAILABLE,
                query_source="Apple news",
            ),
            RawArticle(
                headline="Apple Paywalled Story",
                publisher="WSJ",
                timestamp_raw="2024-02-15",
                url="https://wsj.com/apple-story",
                body_text=None,
                content_status=ContentStatus.UNAVAILABLE,
                query_source="Apple news",
            ),
        ],
        total_queries_executed=2,
    )


def _make_enriched_article_list() -> EnrichedArticleList:
    articles = [
        EnrichedArticle(
            headline="Apple Reports Record Q1 Revenue",
            publisher="Reuters",
            timestamp_raw="2024-02-01",
            url="https://reuters.com/apple-q1",
            body_text="Apple revenue rose 8% to $119bn...",
            content_status=ContentStatus.AVAILABLE,
            query_source="AAPL earnings",
            normalized_date=TODAY - timedelta(days=30),
            sentiment=SentimentLabel.POSITIVE,
            sentiment_confidence=0.92,
            sentiment_rationale="Revenue 8% growth reported",
            event_types=[EventType.EARNINGS],
        ),
        EnrichedArticle(
            headline="Apple Faces EU Antitrust Probe",
            publisher="FT",
            timestamp_raw="10 days ago",
            url="https://ft.com/apple-eu",
            body_text="The European Commission launched an investigation...",
            content_status=ContentStatus.AVAILABLE,
            query_source="Apple news",
            normalized_date=TODAY - timedelta(days=10),
            sentiment=SentimentLabel.NEGATIVE,
            sentiment_confidence=0.85,
            sentiment_rationale="Regulatory risk from EU probe",
            event_types=[EventType.LEGAL_REGULATORY],
        ),
    ]
    return EnrichedArticleList(articles=articles, discarded_count=1)


def _make_final_report() -> FinalReport:
    return FinalReport(
        company_name="Apple Inc",
        analysis_period="Jan 1, 2024 – Mar 31, 2024",
        generated_at="2024-03-31T12:00:00Z",
        aggregate_stats=AggregateStats(
            total_articles=2,
            sentiment_breakdown={"positive": 1, "negative": 1, "neutral": 0},
            event_type_breakdown={"earnings": 1, "legal_regulatory": 1},
            content_coverage_pct=100.0,
            date_range_actual="2024-01-15 to 2024-03-20",
        ),
        executive_summary="Apple had a mixed quarter with strong earnings but regulatory headwinds.",
        key_events="Apple reported 8% revenue growth while facing EU scrutiny.",
        sentiment_analysis="Sentiment was slightly negative overall due to regulatory risks.",
        notable_headlines="1. Apple Q1 Revenue Record — Revenue rose 8%...",
        status=WorkflowStatus.SUCCESS,
    )


def _mock_run(content):
    """Return a MagicMock that mimics agent.run() returning a structured response."""
    mock = MagicMock()
    mock.content = content
    mock.metrics = {"input_tokens": 100, "output_tokens": 200}
    return mock


# ---------------------------------------------------------------------------
# Test: Happy path
# ---------------------------------------------------------------------------

class TestNewsAggregatorWorkflow:

    def _collect(self, workflow: NewsAggregatorWorkflow, **kwargs) -> list:
        """Drain the workflow generator into a list."""
        return list(workflow.run(**kwargs))

    @patch("app.workflows.news_aggregator.with_retry")
    def test_happy_path(self, mock_retry):
        """Full pipeline runs and final RunResponse contains FinalReport."""
        plan = _make_research_plan()
        raw = _make_raw_article_list()
        enriched = _make_enriched_article_list()
        report = _make_final_report()

        # Each with_retry call should pass through to the function and return mocks
        call_count = 0
        expected_returns = [
            _mock_run(plan),
            _mock_run(raw),
            _mock_run(enriched),
            _mock_run(report),
        ]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            result = expected_returns[call_count]
            call_count += 1
            return result

        mock_retry.side_effect = side_effect

        workflow = NewsAggregatorWorkflow()
        responses = self._collect(
            workflow, company_name="Apple Inc", time_period="last 3 months"
        )

        # Should have progress messages + final report
        assert len(responses) > 1
        final_content = responses[-1].content
        assert isinstance(final_content, FinalReport)
        assert final_content.company_name == "Apple Inc"

    def test_empty_company_name_returns_error(self):
        """Empty company name should yield an error response immediately."""
        workflow = NewsAggregatorWorkflow()
        responses = self._collect(workflow, company_name="", time_period="last 3 months")
        assert len(responses) == 1
        assert "ERROR" in responses[0].content

    def test_invalid_time_period_returns_error(self):
        """Unparseable time period should yield an error response immediately."""
        workflow = NewsAggregatorWorkflow()
        responses = self._collect(
            workflow, company_name="Apple", time_period="from a while ago"
        )
        assert len(responses) == 1
        assert "ERROR" in responses[0].content

    @patch("app.workflows.news_aggregator.with_retry")
    def test_zero_articles_returns_fatal(self, mock_retry):
        """Browser returns 0 articles → FATAL response, pipeline aborts."""
        plan = _make_research_plan()
        empty_raw = RawArticleList(articles=[], total_queries_executed=2)

        call_count = 0
        returns = [_mock_run(plan), _mock_run(empty_raw)]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]
            call_count += 1
            return r

        mock_retry.side_effect = side_effect

        workflow = NewsAggregatorWorkflow()
        responses = self._collect(
            workflow, company_name="Apple", time_period="last 3 months"
        )
        last = responses[-1].content
        assert "FATAL" in last or "0 articles" in last

    @patch("app.workflows.news_aggregator.with_retry")
    def test_planner_failure_aborts(self, mock_retry):
        """Planner raising an exception should abort the pipeline."""
        mock_retry.side_effect = RuntimeError("LLM timeout")

        workflow = NewsAggregatorWorkflow()
        responses = self._collect(
            workflow, company_name="Apple", time_period="last 3 months"
        )
        last = responses[-1].content
        assert "FATAL" in last

    @patch("app.workflows.news_aggregator.with_retry")
    def test_low_article_count_degraded(self, mock_retry):
        """1 article after extraction triggers 'degraded' mode, not hard failure."""
        plan = _make_research_plan()
        raw = _make_raw_article_list()
        # Only 1 article after extraction — below MIN_ARTICLES_FOR_FULL_REPORT
        sparse = EnrichedArticleList(
            articles=[_make_enriched_article_list().articles[0]],
            discarded_count=2,
        )
        report = _make_final_report()
        report.status = WorkflowStatus.DEGRADED

        call_count = 0
        returns = [_mock_run(plan), _mock_run(raw), _mock_run(sparse), _mock_run(report)]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]
            call_count += 1
            return r

        mock_retry.side_effect = side_effect

        workflow = NewsAggregatorWorkflow()
        responses = self._collect(
            workflow, company_name="Apple", time_period="last 3 months"
        )
        final = responses[-1].content
        # Pipeline should complete (not abort)
        assert isinstance(final, FinalReport)
        # Warnings should mention low article count
        assert any("article" in w.lower() for w in final.warnings)


# ---------------------------------------------------------------------------
# Test: Utility functions
# ---------------------------------------------------------------------------

class TestWithRetry:
    def test_succeeds_on_first_try(self):
        fn = MagicMock(return_value=42)
        result = with_retry(fn, max_attempts=3, base_delay=0)
        assert result == 42
        assert fn.call_count == 1

    def test_retries_on_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[ValueError("fail"), ValueError("fail"), 99])
        result = with_retry(fn, max_attempts=3, base_delay=0)
        assert result == 99
        assert fn.call_count == 3

    def test_raises_after_all_attempts_exhausted(self):
        fn = MagicMock(side_effect=RuntimeError("always fails"))
        with pytest.raises(RuntimeError, match="always fails"):
            with_retry(fn, max_attempts=2, base_delay=0)
        assert fn.call_count == 2


class TestObservabilityTracker:
    def test_records_agent_lifecycle(self, tmp_path):
        tracker = ObservabilityTracker("Apple", "run-001")
        tracker.start_agent("planner")
        tracker.end_agent("planner", success=True, tokens={"input": 100, "output": 50})
        tracker.add_warning("Low article count")

        data = tracker.to_dict()
        assert data["company"] == "Apple"
        assert data["agent_success"]["planner"] is True
        assert len(data["warnings"]) == 1

    def test_save_creates_file(self, tmp_path):
        tracker = ObservabilityTracker("Apple", "run-002")
        path = tracker.save(directory=str(tmp_path))
        assert Path(path).exists()
