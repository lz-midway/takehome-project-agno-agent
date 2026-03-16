"""
tests/test_workflow.py — Workflow-level unit tests.

All LLM calls are mocked. Tests assert on typed WorkflowEvent objects
(ProgressEvent, ErrorEvent, ReportEvent) — no string matching.

Run:
    python -m pytest tests/test_workflow.py -v
"""

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

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
    WorkflowStatus,
)
from app.workflows.news_aggregator import (
    NewsAggregatorWorkflow,
    ObservabilityTracker,
    parse_time_period,
    with_retry,
)

TODAY = date.today()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
                headline="Paywalled Story",
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
    return EnrichedArticleList(
        articles=[
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
                sentiment_rationale="8% revenue growth reported",
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
                sentiment_rationale="EU regulatory risk",
                event_types=[EventType.LEGAL_REGULATORY],
            ),
        ],
        discarded_count=1,
    )


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
        executive_summary="Apple had a mixed quarter.",
        key_events="Apple reported 8% revenue growth while facing EU scrutiny.",
        sentiment_analysis="Slightly negative due to regulatory risks.",
        notable_headlines="1. Apple Q1 Revenue Record — Revenue rose 8%.",
        status=WorkflowStatus.SUCCESS,
    )


def _mock_run(content):
    mock = MagicMock()
    mock.content = content
    mock.metrics = {"input_tokens": 100, "output_tokens": 200}
    return mock


# ---------------------------------------------------------------------------
# Helper — drain the workflow generator
# ---------------------------------------------------------------------------

def _make_workflow() -> NewsAggregatorWorkflow:
    """Return a workflow with mocked agents and no DB (safe for unit tests)."""
    wf = NewsAggregatorWorkflow()
    # Pre-inject mock agents so _ensure_agents() is a no-op
    wf.planner_agent    = MagicMock()
    wf.browser_agent    = MagicMock()
    wf.extraction_agent = MagicMock()
    wf.compiler_agent   = MagicMock()
    return wf


def _collect(workflow: NewsAggregatorWorkflow, message: str) -> list:
    return list(workflow.run(message=message))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestNewsAggregatorWorkflow:

    @patch("app.workflows.news_aggregator.NewsAggregatorWorkflow._save_report", return_value="data/test_report.json")
    @patch("app.workflows.news_aggregator.ObservabilityTracker.save", return_value="data/logs/test.json")
    @patch("app.workflows.news_aggregator.with_retry")
    def test_happy_path_yields_report_event(self, mock_retry, mock_obs_save, mock_save_report):
        """Full pipeline completes and last event is a ReportEvent."""
        plan     = _make_research_plan()
        raw      = _make_raw_article_list()
        enriched = _make_enriched_article_list()
        report   = _make_final_report()

        call_count = 0
        returns = [_mock_run(plan), _mock_run(raw), _mock_run(enriched), _mock_run(report)]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]; call_count += 1
            return r

        mock_retry.side_effect = side_effect

        events = _collect(_make_workflow(), "Apple Inc | last 3 months")

        report_events = [e for e in events if isinstance(e, ReportEvent)]
        assert len(report_events) == 1
        assert report_events[0].report.company_name == "Apple Inc"

    @patch("app.workflows.news_aggregator.NewsAggregatorWorkflow._save_report", return_value="data/test_report.json")
    @patch("app.workflows.news_aggregator.ObservabilityTracker.save", return_value="data/logs/test.json")
    @patch("app.workflows.news_aggregator.with_retry")
    def test_happy_path_no_fatal_errors(self, mock_retry, mock_obs_save, mock_save_report):
        """No fatal ErrorEvents on success path."""
        call_count = 0
        returns = [
            _mock_run(_make_research_plan()),
            _mock_run(_make_raw_article_list()),
            _mock_run(_make_enriched_article_list()),
            _mock_run(_make_final_report()),
        ]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]; call_count += 1
            return r

        mock_retry.side_effect = side_effect

        events = _collect(_make_workflow(), "Apple Inc | last 3 months")
        fatal_errors = [e for e in events if isinstance(e, ErrorEvent) and e.fatal]
        assert fatal_errors == []

    @patch("app.workflows.news_aggregator.NewsAggregatorWorkflow._save_report", return_value="data/test_report.json")
    @patch("app.workflows.news_aggregator.ObservabilityTracker.save", return_value="data/logs/test.json")
    @patch("app.workflows.news_aggregator.with_retry")
    def test_progress_events_have_correct_stages(self, mock_retry, mock_obs_save, mock_save_report):
        """ProgressEvents are emitted for stages 0–4."""
        call_count = 0
        returns = [
            _mock_run(_make_research_plan()),
            _mock_run(_make_raw_article_list()),
            _mock_run(_make_enriched_article_list()),
            _mock_run(_make_final_report()),
        ]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]; call_count += 1
            return r

        mock_retry.side_effect = side_effect

        events = _collect(_make_workflow(), "Apple | last 3 months")
        stages = {e.stage for e in events if isinstance(e, ProgressEvent)}
        assert stages == {0, 1, 2, 3, 4}

    # ── Hard failure modes ─────────────────────────────────────────────

    def test_no_time_period_yields_fatal_error_event(self):
        """Message with no time period yields a fatal ErrorEvent at stage 0."""
        events = _collect(_make_workflow(), "just a company name")
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].fatal is True
        assert events[0].stage == 0

    def test_bad_time_period_yields_fatal_error_event(self):
        """Unresolvable time period after parsing yields a fatal ErrorEvent."""
        events = _collect(_make_workflow(), "Apple | from a while ago")
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].fatal is True
        assert events[0].stage == 0

    @patch("app.workflows.news_aggregator.with_retry")
    def test_zero_articles_yields_fatal_error_at_stage_2(self, mock_retry):
        """Browser returns 0 articles → fatal ErrorEvent at stage 2."""
        call_count = 0
        returns = [
            _mock_run(_make_research_plan()),
            _mock_run(RawArticleList(articles=[], total_queries_executed=2)),
        ]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]; call_count += 1
            return r

        mock_retry.side_effect = side_effect

        events = _collect(_make_workflow(), "Apple | last 3 months")
        fatal_errors = [e for e in events if isinstance(e, ErrorEvent) and e.fatal]
        assert len(fatal_errors) == 1
        assert fatal_errors[0].stage == 2

    @patch("app.workflows.news_aggregator.with_retry")
    def test_planner_failure_yields_fatal_error_at_stage_1(self, mock_retry):
        """Planner exception → fatal ErrorEvent at stage 1."""
        mock_retry.side_effect = RuntimeError("LLM timeout")

        events = _collect(_make_workflow(), "Apple | last 3 months")
        fatal_errors = [e for e in events if isinstance(e, ErrorEvent) and e.fatal]
        assert len(fatal_errors) == 1
        assert fatal_errors[0].stage == 1

    # ── Soft failure modes ─────────────────────────────────────────────

    @patch("app.workflows.news_aggregator.NewsAggregatorWorkflow._save_report", return_value="data/test_report.json")
    @patch("app.workflows.news_aggregator.ObservabilityTracker.save", return_value="data/logs/test.json")
    @patch("app.workflows.news_aggregator.with_retry")
    def test_low_article_count_yields_non_fatal_error_and_report(self, mock_retry, mock_obs_save, mock_save_report):
        """1 article after extraction → non-fatal ErrorEvent + ReportEvent (degraded)."""
        sparse = EnrichedArticleList(
            articles=[_make_enriched_article_list().articles[0]],
            discarded_count=2,
        )
        report = _make_final_report()
        report.status = WorkflowStatus.DEGRADED

        call_count = 0
        returns = [
            _mock_run(_make_research_plan()),
            _mock_run(_make_raw_article_list()),
            _mock_run(sparse),
            _mock_run(report),
        ]

        def side_effect(fn, **kwargs):
            nonlocal call_count
            r = returns[call_count]; call_count += 1
            return r

        mock_retry.side_effect = side_effect

        events = _collect(_make_workflow(), "Apple | last 3 months")

        soft_errors  = [e for e in events if isinstance(e, ErrorEvent) and not e.fatal]
        report_events = [e for e in events if isinstance(e, ReportEvent)]

        assert len(soft_errors) == 1
        assert soft_errors[0].stage == 3
        assert len(report_events) == 1  # pipeline still completes


# ---------------------------------------------------------------------------
# Typed inputs — verify model_dump_json called for each agent
# ---------------------------------------------------------------------------

class TestTypedAgentInputs:

    def test_planner_input_serialises(self):
        inp = PlannerInput(
            company_name="Apple",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        data = inp.model_dump()
        assert data["company_name"] == "Apple"
        assert isinstance(data["start_date"], date)

    def test_browser_input_contains_plan(self):
        plan = _make_research_plan()
        inp  = BrowserInput(plan=plan)
        data = inp.model_dump()
        assert data["plan"]["company_name"] == "Apple Inc"
        assert len(data["plan"]["queries"]) == 2

    def test_extraction_input_contains_articles(self):
        raw = _make_raw_article_list()
        inp = ExtractionInput(
            start_date=TODAY - timedelta(days=90),
            end_date=TODAY,
            anchor_date=TODAY,
            articles=raw.articles,
        )
        data = inp.model_dump()
        assert len(data["articles"]) == 3
        assert data["anchor_date"] == TODAY

    def test_compiler_input_contains_enriched_articles(self):
        enriched = _make_enriched_article_list()
        inp = CompilerInput(
            company_name="Apple Inc",
            analysis_period="Jan 1 – Mar 31, 2024",
            generated_at="2024-03-31T00:00:00Z",
            articles=enriched.articles,
        )
        data = inp.model_dump()
        assert len(data["articles"]) == 2
        assert data["articles"][0]["sentiment"] == "positive"


# ---------------------------------------------------------------------------
# WorkflowEvent discriminated union
# ---------------------------------------------------------------------------

class TestWorkflowEvents:

    def test_progress_event_kind(self):
        e = ProgressEvent(stage=1, message="Running Planner…")
        assert e.kind == "progress"
        assert e.stage == 1

    def test_error_event_fatal_default(self):
        e = ErrorEvent(stage=2, message="Something went wrong")
        assert e.fatal is True
        assert e.kind == "error"

    def test_error_event_non_fatal(self):
        e = ErrorEvent(stage=3, message="Low article count", fatal=False)
        assert e.fatal is False

    def test_report_event_carries_report(self):
        report = _make_final_report()
        e = ReportEvent(report=report, report_path="data/x.json", obs_path="data/logs/x.json")
        assert e.kind == "report"
        assert e.report.company_name == "Apple Inc"
        assert e.report_path == "data/x.json"


# ---------------------------------------------------------------------------
# parse_time_period + _parse_user_message
# ---------------------------------------------------------------------------

class TestParseTimePeriod:
    ANCHOR = date(2024, 6, 15)

    def test_last_n_months(self):
        start, end = parse_time_period("last 3 months", anchor=self.ANCHOR)
        assert end == self.ANCHOR
        assert (end - start).days == 90

    def test_iso_range(self):
        start, end = parse_time_period("2024-01-01 to 2024-03-31")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 3, 31)

    def test_quarter(self):
        start, end = parse_time_period("Q1 2024")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 3, 31)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_time_period("sometime recently")

    def test_inverted_iso_raises(self):
        with pytest.raises(ValueError, match="after end_date"):
            parse_time_period("2024-06-01 to 2024-01-01")


class TestParseUserMessage:

    def test_pipe_separator(self):
        company, period = NewsAggregatorWorkflow._parse_user_message("Apple Inc | last 3 months")
        assert company == "Apple Inc"
        assert period == "last 3 months"

    def test_analyze_prefix_stripped(self):
        company, _ = NewsAggregatorWorkflow._parse_user_message("Analyze Nvidia for the last 6 months")
        assert company.lower() == "nvidia"

    def test_for_connector_stripped(self):
        # "for" without "the" should also be stripped
        company, period = NewsAggregatorWorkflow._parse_user_message("Apple for last 3 months")
        assert company.lower() == "apple"
        assert "last 3 months" in period

    def test_comma_separator(self):
        company, period = NewsAggregatorWorkflow._parse_user_message("TSMC news, Q1 2024")
        assert "TSMC" in company
        assert "Q1 2024" in period

    def test_iso_range(self):
        company, period = NewsAggregatorWorkflow._parse_user_message("Microsoft | 2024-01-01 to 2024-06-30")
        assert "Microsoft" in company
        assert period == "2024-01-01 to 2024-06-30"

    def test_no_time_period_raises(self):
        with pytest.raises(ValueError, match="time period"):
            NewsAggregatorWorkflow._parse_user_message("just a company name")


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------

class TestWithRetry:

    def test_succeeds_first_try(self):
        fn = MagicMock(return_value=42)
        assert with_retry(fn, max_attempts=3, base_delay=0) == 42
        assert fn.call_count == 1

    def test_retries_then_succeeds(self):
        fn = MagicMock(side_effect=[ValueError("fail"), ValueError("fail"), 99])
        assert with_retry(fn, max_attempts=3, base_delay=0) == 99
        assert fn.call_count == 3

    def test_raises_after_all_attempts(self):
        fn = MagicMock(side_effect=RuntimeError("always fails"))
        with pytest.raises(RuntimeError, match="always fails"):
            with_retry(fn, max_attempts=2, base_delay=0)
        assert fn.call_count == 2


# ---------------------------------------------------------------------------
# ObservabilityTracker
# ---------------------------------------------------------------------------

class TestObservabilityTracker:

    def test_records_agent_lifecycle(self):
        t = ObservabilityTracker("Apple", "run-001")
        t.start_agent("planner")
        t.end_agent("planner", success=True, tokens={"input": 100})
        t.add_warning("Low article count")
        data = t.to_dict()
        assert data["agent_success"]["planner"] is True
        assert len(data["warnings"]) == 1

    def test_save_creates_file(self, tmp_path):
        t = ObservabilityTracker("Apple", "run-002")
        path = t.save(directory=str(tmp_path))
        assert (tmp_path / path.split("/")[-1]).exists()
