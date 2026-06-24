"""
rag_wiki.planner.query
--------------------
Hybrid query planner that classifies queries using LLM + keyword fallback.

The ``QueryPlanner`` class uses an LLM as the primary classifier. If the LLM
call fails (timeout, parse error), it falls back to keyword/regex rules. An
LRU cache avoids redundant LLM calls for identical queries within the same
process.
"""

from __future__ import annotations

import json
import re
import uuid

import structlog

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.planner.base import QueryPlan, QueryType
from rag_wiki.planner.exceptions import PlannerClassificationError
from rag_wiki.prompts.constants import QUERY_CLASSIFICATION_PROMPT
from rag_wiki.providers.base import ChatProvider, CompletionRequest, Message
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)

_KEYWORD_RULES: list[tuple[re.Pattern[str], QueryType, float]] = [
    (re.compile(r"(?i)\b(what is|define|who is)"), QueryType.FACTUAL_LOOKUP, 0.9),
    (
        re.compile(r"(?i)\b(how\b.*\brelate|connect|relationship)"),
        QueryType.RELATIONSHIP_QUERY,
        0.9,
    ),
    (
        re.compile(r"(?i)\b(summar|overview|give me an overview)"),
        QueryType.SUMMARIZATION,
        0.9,
    ),
    (re.compile(r"(?i)\b(compare|difference|versus|vs)"), QueryType.COMPARISON, 0.9),
]

_DEFAULT_QUERY_TYPE = QueryType.FACTUAL_LOOKUP
_DEFAULT_KEYWORD_CONFIDENCE = 0.3

_RETRIEVAL_DEPTH_MAP: dict[QueryType, tuple[str, int, str]] = {
    QueryType.FACTUAL_LOOKUP: ("shallow", 1, "top-3 seeds found"),
    QueryType.RELATIONSHIP_QUERY: ("deep", 2, "graph depth 2 reached"),
    QueryType.SUMMARIZATION: ("deep", 5, "top-10 chunks aggregated"),
    QueryType.COMPARISON: ("shallow", 0, "all entities resolved"),
}

_CACHE_MAXSIZE = 128


class QueryPlanner:
    """Classifies user queries into one of four v1 query types.

    Classification order:
    1. Explicit override (if ``explicit_type`` is provided)
    2. LRU cache hit (return cached result with new query_id)
    3. LLM classification (primary path)
    4. Keyword/regex fallback (if LLM fails)

    The LRU cache is process-local, keyed by ``query.lower().strip()``,
    with a maximum of 128 entries.
    """

    def __init__(
        self,
        settings: Settings,
        chat_provider: ChatProvider | None = None,
    ) -> None:
        self._settings = settings
        self._chat_provider = chat_provider
        self._cache: dict[str, tuple[str, float, str, str | None]] = {}

    async def classify_query(
        self,
        query: str,
        query_id: uuid.UUID | None = None,
        explicit_type: QueryType | None = None,
    ) -> QueryPlan:
        """Classify the query and return a fully populated ``QueryPlan``.

        Args:
            query: The raw user query text.
            query_id: Optional UUID for the plan. Generated if not provided.
            explicit_type: If set, skip classification and use this type.

        Returns:
            A ``QueryPlan`` with the classification decision.

        Raises:
            PlannerClassificationError: If confidence is below
                ``planner_confidence_minimum`` and no explicit override was given.
        """
        qid = query_id or uuid.uuid4()

        if explicit_type is not None:
            return self._build_plan(
                query_id=qid,
                query=query,
                classified_type=explicit_type,
                confidence=1.0,
                classification_source="explicit",
                rationale=f"explicit type override: {explicit_type.value}",
            )

        cache_key = query.lower().strip()
        if cache_key in self._cache:
            type_str, confidence, rationale, model = self._cache[cache_key]
            plan = self._build_plan(
                query_id=qid,
                query=query,
                classified_type=QueryType(type_str),
                confidence=confidence,
                classification_source="llm (cached)",
                rationale=rationale,
                model_used=model,
            )
            self._check_confidence(plan, explicit_type)
            return plan

        if self._chat_provider is not None:
            try:
                result = await self._llm_classify(query)
                self._cache_put(cache_key, result)
                type_str, confidence, rationale, model = result
                plan = self._build_plan(
                    query_id=qid,
                    query=query,
                    classified_type=QueryType(type_str),
                    confidence=confidence,
                    classification_source="llm",
                    rationale=rationale,
                    model_used=model,
                )
                self._check_confidence(plan, explicit_type)
                return plan
            except (
                LLMProviderError,
                json.JSONDecodeError,
                ValueError,
                KeyError,
                TimeoutError,
            ) as exc:
                logger.warning(
                    "LLM query classification failed, falling back to rules",
                    error=str(exc),
                )

        qtype, confidence, rationale = self._keyword_classify(query)
        plan = self._build_plan(
            query_id=qid,
            query=query,
            classified_type=qtype,
            confidence=confidence,
            classification_source="rule",
            rationale=rationale,
        )
        self._check_confidence(plan, explicit_type)
        return plan

    def _check_confidence(
        self,
        plan: QueryPlan,
        explicit_type: QueryType | None,
    ) -> None:
        if (
            plan.confidence < self._settings.planner_confidence_minimum
            and explicit_type is None
        ):
            raise PlannerClassificationError(
                f"Confidence {plan.confidence} below minimum threshold "
                f"{self._settings.planner_confidence_minimum}. "
                f"Plan: {plan.rationale}"
            )

    async def _llm_classify(
        self,
        query: str,
    ) -> tuple[str, float, str, str | None]:
        """Call the LLM for query classification.

        Returns:
            Tuple of (type_str, confidence, rationale, model).
        """
        assert self._chat_provider is not None
        response = await self._chat_provider.complete(
            CompletionRequest(
                system=QUERY_CLASSIFICATION_PROMPT,
                messages=[Message(role="user", content=query)],
                model=self._settings.llm_model_query_classification,
            )
        )
        content = (response.content or "").strip()
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) >= 2 else ""
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        result = json.loads(content)
        type_str = str(result["type"])
        QueryType(type_str)
        confidence = float(result["confidence"])
        rationale = str(result.get("rationale", ""))
        model = self._settings.llm_model_query_classification
        return type_str, confidence, rationale, model

    def _keyword_classify(self, query: str) -> tuple[QueryType, float, str]:
        """Classify query using keyword/regex rules."""
        for pattern, qtype, conf in _KEYWORD_RULES:
            if pattern.search(query):
                return qtype, conf, f"keyword match: {pattern.pattern}"
        return (
            _DEFAULT_QUERY_TYPE,
            _DEFAULT_KEYWORD_CONFIDENCE,
            "no keyword match, defaulting to factual_lookup",
        )

    def _cache_put(self, key: str, value: tuple[str, float, str, str | None]) -> None:
        if len(self._cache) >= _CACHE_MAXSIZE:
            self._cache.clear()
        self._cache[key] = value

    def _build_plan(
        self,
        query_id: uuid.UUID,
        query: str,
        classified_type: QueryType,
        confidence: float,
        classification_source: str,
        rationale: str,
        model_used: str | None = None,
    ) -> QueryPlan:
        depth, seed_count, termination = _RETRIEVAL_DEPTH_MAP[classified_type]
        return QueryPlan(
            query_id=query_id,
            raw_query=query,
            classified_type=classified_type,
            retrieval_depth=depth,
            seed_count=seed_count,
            termination_condition=termination,
            confidence=confidence,
            classification_source=classification_source,
            model_used=model_used,
            rationale=rationale,
            planner_version=self._settings.planner_version,
        )
