"""
rag_wiki.planner
----------------
Planner subsystem: plan-forms ingest and retrieval decisions before tool
invocation.

The ingest planner (rule-based) classifies source documents by MIME type
and density. The query planner (LLM + rule fallback) classifies queries
into one of four v1 query types. Both produce an inspectable, logged
plan before any downstream tools run.
"""

from rag_wiki.planner.base import (
    ParserType as ParserType,
)
from rag_wiki.planner.base import (
    PDFParserMode as PDFParserMode,
)
from rag_wiki.planner.base import (
    QueryPlan as QueryPlan,
)
from rag_wiki.planner.base import (
    QueryType as QueryType,
)
from rag_wiki.planner.base import (
    SourcePlan as SourcePlan,
)
from rag_wiki.planner.exceptions import (
    PlannerClassificationError as PlannerClassificationError,
)
from rag_wiki.planner.exceptions import (
    PlannerError as PlannerError,
)
from rag_wiki.planner.ingest import (
    IngestPlanner as IngestPlanner,
)
from rag_wiki.planner.query import (
    QueryPlanner as QueryPlanner,
)

__all__ = [
    "IngestPlanner",
    "PDFParserMode",
    "ParserType",
    "PlannerClassificationError",
    "PlannerError",
    "QueryPlan",
    "QueryPlanner",
    "QueryType",
    "SourcePlan",
]
