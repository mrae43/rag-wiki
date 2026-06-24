"""Tests for the hybrid query planner (LLM + keyword fallback + LRU cache)."""

from __future__ import annotations

import uuid

import pytest

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.planner.base import QueryType
from rag_wiki.planner.exceptions import PlannerClassificationError
from rag_wiki.planner.query import QueryPlanner
from rag_wiki.providers.base import (
    CompletionRequest,
    CompletionResponse,
)
from rag_wiki.settings import Settings

_JSON_FACTUAL = (
    '{"type": "factual_lookup", "confidence": 0.95, "rationale": "what is query"}'
)
_JSON_RELATIONSHIP = (
    '{"type": "relationship_query", "confidence": 0.9, "rationale": "relates to"}'
)


class _FakeLLMProvider:
    """Test double that returns configurable JSON or raises errors."""

    def __init__(
        self,
        response_json: str | None = None,
        raise_error: bool = False,
    ) -> None:
        self.response_json = response_json
        self.raise_error = raise_error
        self.call_count = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.call_count += 1
        if self.raise_error:
            raise LLMProviderError("fake provider error")
        fallback = '{"type": "factual_lookup", "confidence": 0.95, "rationale": "test"}'
        content = self.response_json or fallback
        return CompletionResponse(content=content)

    async def caption_image(
        self,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> str:
        return "fake-caption"


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/test",
        planner_confidence_minimum=0.5,
        planner_confidence_low=0.5,
    )


@pytest.fixture()
def planner(settings: Settings) -> QueryPlanner:
    return QueryPlanner(settings=settings)


@pytest.fixture()
def query_id() -> uuid.UUID:
    return uuid.uuid4()


class TestQueryPlannerLLM:
    """LLM classification path tests."""

    @pytest.fixture()
    def llm_provider(self) -> _FakeLLMProvider:
        return _FakeLLMProvider()

    @pytest.fixture()
    def llm_planner(
        self, settings: Settings, llm_provider: _FakeLLMProvider
    ) -> QueryPlanner:
        return QueryPlanner(settings=settings, chat_provider=llm_provider)

    async def test_llm_returns_factual_lookup(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
        query_id: uuid.UUID,
    ) -> None:
        llm_provider.response_json = _JSON_FACTUAL
        plan = await llm_planner.classify_query("What is RAG?", query_id=query_id)
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP
        assert plan.confidence == 0.95
        assert plan.classification_source == "llm"

    async def test_llm_returns_relationship_query(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
        query_id: uuid.UUID,
    ) -> None:
        llm_provider.response_json = _JSON_RELATIONSHIP
        plan = await llm_planner.classify_query(
            "How does X relate to Y?", query_id=query_id
        )
        assert plan.classified_type == QueryType.RELATIONSHIP_QUERY
        assert plan.classification_source == "llm"

    async def test_llm_returns_summarization(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
        query_id: uuid.UUID,
    ) -> None:
        llm_provider.response_json = (
            '{"type": "summarization", "confidence": 0.9, "rationale": "summarize"}'
        )
        plan = await llm_planner.classify_query("Summarize the wiki", query_id=query_id)
        assert plan.classified_type == QueryType.SUMMARIZATION
        assert plan.classification_source == "llm"

    async def test_llm_returns_comparison(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
        query_id: uuid.UUID,
    ) -> None:
        llm_provider.response_json = (
            '{"type": "comparison", "confidence": 0.9, "rationale": "compare"}'
        )
        plan = await llm_planner.classify_query("Compare X and Y", query_id=query_id)
        assert plan.classified_type == QueryType.COMPARISON
        assert plan.classification_source == "llm"

    async def test_llm_timeout_falls_back_to_rules(
        self,
        settings: Settings,
        query_id: uuid.UUID,
    ) -> None:
        provider = _FakeLLMProvider(raise_error=True)
        planner = QueryPlanner(settings=settings, chat_provider=provider)
        plan = await planner.classify_query("What is RAG?", query_id=query_id)
        assert plan.classification_source == "rule"
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP

    async def test_llm_invalid_json_falls_back_to_rules(
        self,
        settings: Settings,
        query_id: uuid.UUID,
    ) -> None:
        provider = _FakeLLMProvider(response_json="not valid json")
        planner = QueryPlanner(settings=settings, chat_provider=provider)
        plan = await planner.classify_query("What is RAG?", query_id=query_id)
        assert plan.classification_source == "rule"

    async def test_llm_invalid_type_falls_back_to_rules(
        self,
        settings: Settings,
        query_id: uuid.UUID,
    ) -> None:
        bad_json = '{"type": "invalid_type", "confidence": 0.9, "rationale": "bad"}'
        provider = _FakeLLMProvider(response_json=bad_json)
        planner = QueryPlanner(settings=settings, chat_provider=provider)
        plan = await planner.classify_query("What is RAG?", query_id=query_id)
        assert plan.classification_source == "rule"


class TestQueryPlannerExplicitOverride:
    """Explicit type override tests."""

    async def test_explicit_type_override(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Some query",
            query_id=query_id,
            explicit_type=QueryType.SUMMARIZATION,
        )
        assert plan.classified_type == QueryType.SUMMARIZATION
        assert plan.confidence == 1.0
        assert plan.classification_source == "explicit"
        assert "explicit type override" in plan.rationale

    async def test_explicit_override_skips_llm(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        provider = _FakeLLMProvider()
        planner = QueryPlanner(settings=settings, chat_provider=provider)
        plan = await planner.classify_query(
            "Some query",
            query_id=query_id,
            explicit_type=QueryType.COMPARISON,
        )
        assert provider.call_count == 0
        assert plan.classified_type == QueryType.COMPARISON
        assert plan.classification_source == "explicit"


class TestQueryPlannerKeywordFallback:
    """Keyword/regex fallback tests."""

    async def test_keyword_factual_lookup(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What is the capital of France?", query_id=query_id
        )
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP
        assert plan.confidence == 0.9
        assert plan.classification_source == "rule"

    async def test_keyword_factual_lookup_define(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Define machine learning", query_id=query_id
        )
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP

    async def test_keyword_factual_lookup_who_is(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query("Who is Ada Lovelace?", query_id=query_id)
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP

    async def test_keyword_relationship_query(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "How does RAG relate to LLMs?", query_id=query_id
        )
        assert plan.classified_type == QueryType.RELATIONSHIP_QUERY
        assert plan.confidence == 0.9

    async def test_keyword_relationship_query_connect(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What connects quantum computing and AI?", query_id=query_id
        )
        assert plan.classified_type == QueryType.RELATIONSHIP_QUERY

    async def test_keyword_summarization(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Summarize the history of Rome", query_id=query_id
        )
        assert plan.classified_type == QueryType.SUMMARIZATION
        assert plan.confidence == 0.9

    async def test_keyword_comparison(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Compare Python and Rust", query_id=query_id
        )
        assert plan.classified_type == QueryType.COMPARISON
        assert plan.confidence == 0.9

    async def test_keyword_comparison_vs(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Python vs Rust performance", query_id=query_id
        )
        assert plan.classified_type == QueryType.COMPARISON

    async def test_keyword_default_factual(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        low_minimum = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.1,
        )
        planner = QueryPlanner(settings=low_minimum)
        plan = await planner.classify_query(
            "random text without keywords", query_id=query_id
        )
        assert plan.classified_type == QueryType.FACTUAL_LOOKUP
        assert plan.confidence == 0.3
        assert plan.classification_source == "rule"


class TestQueryPlannerCache:
    """LRU cache behavior tests."""

    @pytest.fixture()
    def llm_provider(self) -> _FakeLLMProvider:
        return _FakeLLMProvider()

    @pytest.fixture()
    def llm_planner(
        self, settings: Settings, llm_provider: _FakeLLMProvider
    ) -> QueryPlanner:
        return QueryPlanner(settings=settings, chat_provider=llm_provider)

    async def test_lru_cache_hit(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
    ) -> None:
        llm_provider.response_json = (
            '{"type": "summarization", "confidence": 0.9, "rationale": "test"}'
        )
        plan1 = await llm_planner.classify_query("Summarize this")
        plan2 = await llm_planner.classify_query("Summarize this")
        assert llm_provider.call_count == 1
        assert plan1.classified_type == QueryType.SUMMARIZATION
        assert plan2.classified_type == QueryType.SUMMARIZATION
        assert plan1.query_id != plan2.query_id
        assert plan2.classification_source == "llm (cached)"

    async def test_lru_cache_miss(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
    ) -> None:
        llm_provider.response_json = (
            '{"type": "factual_lookup", "confidence": 0.95, "rationale": "test"}'
        )
        plan1 = await llm_planner.classify_query("What is X?")
        llm_provider.response_json = (
            '{"type": "summarization", "confidence": 0.9, "rationale": "test"}'
        )
        plan2 = await llm_planner.classify_query("Summarize Y")
        assert llm_provider.call_count == 2
        assert plan1.classified_type == QueryType.FACTUAL_LOOKUP
        assert plan2.classified_type == QueryType.SUMMARIZATION

    async def test_cache_key_is_case_insensitive(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
    ) -> None:
        llm_provider.response_json = (
            '{"type": "factual_lookup", "confidence": 0.95, "rationale": "test"}'
        )
        await llm_planner.classify_query("What is X?")
        await llm_planner.classify_query("  what is x?  ")
        assert llm_provider.call_count == 1

    async def test_cache_not_used_for_explicit_override(
        self,
        llm_planner: QueryPlanner,
        llm_provider: _FakeLLMProvider,
    ) -> None:
        """Explicit overrides never hit the cache."""
        llm_provider.response_json = (
            '{"type": "factual_lookup", "confidence": 0.95, "rationale": "test"}'
        )
        await llm_planner.classify_query(
            "Some query", explicit_type=QueryType.SUMMARIZATION
        )
        assert llm_provider.call_count == 0

    async def test_rule_results_not_cached(
        self, settings: Settings, llm_provider: _FakeLLMProvider
    ) -> None:
        """Keyword fallback results are not cached - LLM is tried again next time."""
        planner = QueryPlanner(settings=settings, chat_provider=llm_provider)
        llm_provider.raise_error = True
        plan1 = await planner.classify_query("What is X?")
        assert plan1.classification_source == "rule"
        assert llm_provider.call_count == 1

        llm_provider.raise_error = False
        llm_provider.response_json = (
            '{"type": "summarization", "confidence": 0.9, "rationale": "test"}'
        )
        plan2 = await planner.classify_query("What is X?")
        assert plan2.classification_source == "llm"
        assert llm_provider.call_count == 2

    async def test_cache_eviction_removes_oldest(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        cache_size_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.1,
            planner_confidence_low=0.1,
            planner_confidence_high=1.0,
        )
        provider = _FakeLLMProvider(
            response_json=(
                '{"type": "factual_lookup", "confidence": 0.9, '
                '"rationale": "test"}'
            )
        )
        planner = QueryPlanner(settings=cache_size_settings, chat_provider=provider)
        # Fill the cache with queries 0..127 (128 = CACHE_MAXSIZE).
        for i in range(128):
            await planner.classify_query(f"query {i}")
        # Insert one more to trigger eviction of the oldest (query 0).
        await planner.classify_query("query evictor")
        provider.response_json = (
            '{"type": "comparison", "confidence": 0.9, "rationale": "test"}'
        )
        plan = await planner.classify_query("query 0")
        # Cache miss means LLM was called again — classified_type from LLM.
        assert plan.classified_type == QueryType.COMPARISON
        assert plan.classification_source == "llm"


class TestQueryPlannerConfidence:
    """Confidence threshold tests."""

    async def test_confidence_below_minimum_raises_error(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        high_minimum = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.8,
        )
        low_conf = (
            '{"type": "factual_lookup", "confidence": 0.6,'
            ' "rationale": "low confidence"}'
        )
        provider = _FakeLLMProvider(response_json=low_conf)
        planner = QueryPlanner(settings=high_minimum, chat_provider=provider)
        with pytest.raises(PlannerClassificationError) as exc_info:
            await planner.classify_query("Some query", query_id=query_id)
        assert "Confidence 0.6" in str(exc_info.value)

    async def test_confidence_below_minimum_from_keyword_default(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        """Default keyword confidence 0.3 is below default minimum 0.5."""
        planner = QueryPlanner(settings=settings)
        with pytest.raises(PlannerClassificationError) as exc_info:
            await planner.classify_query(
                "random text without keywords", query_id=query_id
            )
        assert "Confidence 0.3" in str(exc_info.value)

    async def test_explicit_override_bypasses_confidence_check(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Some query",
            query_id=query_id,
            explicit_type=QueryType.SUMMARIZATION,
        )
        assert plan.confidence == 1.0

    async def test_confidence_with_explicit_override_is_always_1(
        self, settings: Settings, query_id: uuid.UUID
    ) -> None:
        high_minimum = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.99,
        )
        planner = QueryPlanner(settings=high_minimum)
        plan = await planner.classify_query(
            "Any query",
            query_id=query_id,
            explicit_type=QueryType.COMPARISON,
        )
        assert plan.confidence == 1.0

    async def test_confidence_in_mid_range_escalates_depth(
        self, query_id: uuid.UUID
    ) -> None:
        """Mid-range confidence escalates retrieval depth to 'deep'."""
        mid_range_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.3,
            planner_confidence_low=0.5,
            planner_confidence_high=0.8,
        )
        json_ = (
            '{"type": "factual_lookup", "confidence": 0.6,'
            ' "rationale": "mid confidence"}'
        )
        provider = _FakeLLMProvider(response_json=json_)
        planner = QueryPlanner(settings=mid_range_settings, chat_provider=provider)
        plan = await planner.classify_query("Some query", query_id=query_id)
        assert plan.retrieval_depth == "deep"
        assert plan.seed_count >= 3
        assert "escalated" in plan.termination_condition

    async def test_confidence_above_high_does_not_escalate(
        self, query_id: uuid.UUID
    ) -> None:
        """Confidence >= high threshold does not escalate."""
        above_high_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.3,
            planner_confidence_low=0.5,
            planner_confidence_high=0.8,
        )
        json_ = (
            '{"type": "factual_lookup", "confidence": 0.9,'
            ' "rationale": "high confidence"}'
        )
        provider = _FakeLLMProvider(response_json=json_)
        planner = QueryPlanner(settings=above_high_settings, chat_provider=provider)
        plan = await planner.classify_query("Some query", query_id=query_id)
        assert plan.retrieval_depth == "shallow"
        assert plan.seed_count == 1

    async def test_confidence_below_low_does_not_escalate(
        self, query_id: uuid.UUID
    ) -> None:
        """Confidence below low threshold does not escalate (but passes minimum)."""
        below_low_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/test",
            planner_confidence_minimum=0.3,
            planner_confidence_low=0.5,
            planner_confidence_high=0.8,
        )
        json_ = (
            '{"type": "factual_lookup", "confidence": 0.4,'
            ' "rationale": "low confidence"}'
        )
        provider = _FakeLLMProvider(response_json=json_)
        planner = QueryPlanner(settings=below_low_settings, chat_provider=provider)
        plan = await planner.classify_query("Some query", query_id=query_id)
        assert plan.retrieval_depth == "shallow"
        assert plan.seed_count == 1


class TestQueryPlannerPlanProperties:
    """QueryPlan structure and invariants."""

    async def test_plan_has_query_id(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What is RAG?", query_id=query_id, explicit_type=QueryType.FACTUAL_LOOKUP
        )
        assert plan.query_id == query_id

    async def test_plan_generates_query_id(self, planner: QueryPlanner) -> None:
        plan = await planner.classify_query(
            "What is RAG?", explicit_type=QueryType.FACTUAL_LOOKUP
        )
        assert isinstance(plan.query_id, uuid.UUID)

    async def test_plan_has_retrieval_depth(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What is RAG?", query_id=query_id, explicit_type=QueryType.FACTUAL_LOOKUP
        )
        assert plan.retrieval_depth == "shallow"
        assert plan.seed_count == 1

    async def test_plan_deep_retrieval(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "Summarize the wiki",
            query_id=query_id,
            explicit_type=QueryType.SUMMARIZATION,
        )
        assert plan.retrieval_depth == "deep"
        assert plan.seed_count == 5

    async def test_plan_has_version(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What is RAG?", query_id=query_id, explicit_type=QueryType.FACTUAL_LOOKUP
        )
        assert plan.planner_version == "1.0.0"

    async def test_plan_raw_query(
        self, planner: QueryPlanner, query_id: uuid.UUID
    ) -> None:
        plan = await planner.classify_query(
            "What is RAG?", query_id=query_id, explicit_type=QueryType.FACTUAL_LOOKUP
        )
        assert plan.raw_query == "What is RAG?"
