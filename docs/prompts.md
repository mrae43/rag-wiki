# Prompt Rulebook

Every LLM system instruction used by the system lives in
`rag_wiki/prompts/` unless noted below.  Static prompts are in
`constants.py`; dynamic (Jinja2) prompts are in `templates/`.

---

## 1. Extraction Prompt

| Attribute | Value |
|---|---|
| **Role** | Entity and relation extraction engine |
| **Purpose** | Extract structured entities and relations from a single text chunk |
| **Location** | `rag_wiki/prompts/constants.py` — `EXTRACTION_PROMPT` |
| **Model env var** | `LLM_MODEL_EXTRACTION` (default: `gpt-4o-mini`) |
| **Output type** | Tool call — `extract_entities_and_relations` |
| **Trigger** | Per-chunk during ingestion pipeline (`ingest/pipeline.py`) |
| **Constraints** | Returns JSON via tool call, never free-text |
| **Stop / escalation** | Missing tool call → `ExtractionError`; chunk skipped, job continues |

---

## 2. Resolution Prompt

| Attribute | Value |
|---|---|
| **Role** | Entity resolution engine |
| **Purpose** | Decide whether an extracted entity merges into an existing entity or creates a new one |
| **Location** | `rag_wiki/prompts/templates/resolution.j2` |
| **Model env var** | `LLM_MODEL_RESOLUTION` (default: `gpt-4o`) |
| **Output type** | Tool call — `merge_decision` |
| **Trigger** | During entity resolution (`graph/resolution.py`), for each candidate with vector neighbours above distance threshold |
| **Constraints** | Receives chunk context + candidate details + existing candidates block; returns merge/new decision with optional merged_into_id |
| **Stop / escalation** | Missing/invalid tool call → `ExtractionError`; candidate skipped |

---

## 3. Caption Prompt

| Attribute | Value |
|---|---|
| **Role** | Image captioning assistant |
| **Purpose** | Generate a text caption for an image (used in caption-to-text pipeline) |
| **Location** | `rag_wiki/prompts/constants.py` — `CAPTION_PROMPT` |
| **Model env var** | `LLM_MODEL_CAPTION` (default: `gpt-4o-mini`) |
| **Output type** | Free-text (the caption string) |
| **Trigger** | Per-image during ingestion pipeline, before chunking |
| **Constraints** | Single static string `"Describe this image."`; no variables |
| **Stop / escalation** | LLM error → `LLMProviderError`; image skipped |
| **§4.3 note** | Minimal prompt — exempt from full harness engineering requirements (single-purpose, no variables) |

---

## 4. Query System Prompt

| Attribute | Value |
|---|---|
| **Role** | Helpful research assistant |
| **Purpose** | Synthesise a natural-language answer from retrieved context |
| **Location** | `rag_wiki/prompts/constants.py` — `QUERY_SYSTEM_PROMPT` |
| **Model env var** | `LLM_MODEL_QUERY` (default: `gpt-4o`) |
| **Output type** | Free-text (the answer) |
| **Trigger** | `POST /queries` when `generate_answer=true` (`api/routes/query.py`) |
| **Constraints** | Must use only retrieved context; user message is `"Question: {query}\n\nContext:\n{context}"` (inline in `query.py`, not templated) |
| **Stop / escalation** | LLM error → `RetrievalError`; endpoint returns `retrieval` result without answer |

---

## 5. Entity Synthesis Prompt

| Attribute | Value |
|---|---|
| **Role** | Knowledge wiki curator |
| **Purpose** | Generate or update an entity wiki page from its graph context |
| **Location** | `rag_wiki/prompts/templates/synthesize_entity.j2` |
| **Model env var** | `LLM_MODEL_WIKI_SYNTHESIS` (default: `gpt-4o`) |
| **Output type** | Free-text (markdown wiki page) |
| **Trigger** | Worker processes `synthesize_entity` job (`wiki/synthesis.py`) |
| **Constraints** | Receives entity metadata, edges, known entities, source chunks, existing page content (if update). No citation fabrication; chunks-only evidence |
| **Stop / escalation** | `LLMProviderError` → skip (no page written); entity data already in graph |

---

## 6. Source Summary Prompt

| Attribute | Value |
|---|---|
| **Role** | Knowledge wiki curator |
| **Purpose** | Generate a source-level wiki page summarising an ingested document |
| **Location** | `rag_wiki/prompts/templates/synthesize_source_summary.j2` |
| **Model env var** | `LLM_MODEL_WIKI_SYNTHESIS` (default: `gpt-4o`) |
| **Output type** | Free-text (markdown wiki page) |
| **Trigger** | Worker processes `synthesize_source_summary` job (`wiki/synthesis.py`) |
| **Constraints** | Receives source metadata, chunks, touched entities, source relations, ingest history |
| **Stop / escalation** | `LLMProviderError` → skip (no page written) |

---

## 7. Retrieval Instruction (non-prompt)

| Attribute | Value |
|---|---|
| **Purpose** | Token-budget-only string used in context assembly; never sent to LLM |
| **Location** | `rag_wiki/retrieval/context.py` — `_RETRIEVAL_INSTRUCTION` |
| **Value** | `"Use the provided context — including entity metadata, graph relations, wiki pages, and source chunks — to answer the user's question accurately."` |
| **Notes** | Counted in `RETRIEVAL_INSTRUCTION_BUDGET_TOKENS` for slot budgeting only |
