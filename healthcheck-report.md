====================================================================
  CODEBASE HEALTH CHECK — FastAPI / Pydantic v2 / PostgreSQL
====================================================================

  Overall score : 82/100  ✅ PASSING
  Total findings: 26  (0 critical, 2 high)

  Project structure            █████████████████░░░  88/100
  Error handling               ████████████████████ 100/100
  Type safety                  ████████████████████ 100/100
  Documentation                ███████░░░░░░░░░░░░░  35/100
  Tests                        ████████████████████ 100/100
  Security                     ████████████████████ 100/100
  Performance                  ████████████████░░░░  80/100
  Scalability                  ████████████████████ 100/100
  Consistency                  ████████░░░░░░░░░░░░  40/100

--------------------------------------------------------------------

  ——— PROJECT STRUCTURE ————————————————————————————————————————————

  🟡 [MEDIUM  ] /home/mrae43/repo-git/rag-wiki
     Issue : Expected directory 'migrations/' not found at project root
     Fix   : Create 'migrations/' following standard layout conventions

  ✅ Error handling — no findings

  ✅ Type safety — no findings

  ——— DOCUMENTATION ————————————————————————————————————————————————

  🔵 [LOW     ] rag_wiki/settings.py:8
     Issue : Public class `Settings` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/settings.py:76
     Issue : Public function `get_settings` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/parser.py:19
     Issue : Public function `parse_document` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/schemas.py:9
     Issue : Public class `ChunkType` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/schemas.py:15
     Issue : Public class `BaseChunk` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/schemas.py:23
     Issue : Public class `TextChunk` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/schemas.py:28
     Issue : Public class `TableChunk` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/schemas.py:34
     Issue : Public class `ImageChunk` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/chunking.py:10
     Issue : Public function `count_tokens` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/chunking.py:19
     Issue : Public function `split_by_sections` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/parsers/pdf.py:130
     Issue : Public function `parse_pdf` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/parsers/simple.py:61
     Issue : Public function `parse_simple` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  🔵 [LOW     ] rag_wiki/ingest/parsers/unstructured.py:9
     Issue : Public function `parse_unstructured` has no docstring
     Fix   : Add a one-line docstring describing what it does, its params, and return value

  ✅ Tests — no findings

  ✅ Security — no findings

  ——— PERFORMANCE ——————————————————————————————————————————————————

  🟠 [HIGH    ] rag_wiki/wiki/synthesis.py:53
     Issue : Potential N+1: DB call inside a loop at line 53
     Fix   : Use selectinload() or joinedload() on the relationship, or batch with a single WHERE IN query

  🟠 [HIGH    ] rag_wiki/graph/resolution.py:138
     Issue : Potential N+1: DB call inside a loop at line 138
     Fix   : Use selectinload() or joinedload() on the relationship, or batch with a single WHERE IN query

  ✅ Scalability — no findings

  ——— CONSISTENCY ——————————————————————————————————————————————————

  🔵 [LOW     ] tests/ingest/test_pipeline.py:13
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] tests/ingest/test_parsing.py:5
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] tests/ingest/conftest.py:16
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] tests/api/routes/test_source.py:9
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/providers/openai.py:14
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/wiki/synthesis.py:15
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/ingest/parser.py:5
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/ingest/pipeline.py:14
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/graph/resolution.py:20
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

  🔵 [LOW     ] rag_wiki/api/routes/source.py:13
     Issue : Import ordering: stdlib import appears after third-party imports
     Fix   : Use ruff with `I` rules enabled to auto-sort: stdlib → third-party → local

====================================================================
  ✅  Health check PASSED (score ≥ 75, no critical findings)
====================================================================