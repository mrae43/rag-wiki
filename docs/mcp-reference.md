# MCP Reference — Source of Truth
### Converting a Graph-RAG "Second Brain" System into an MCP Server

> **Scope of this document:** This is the single authoritative reference for migrating the existing graph-RAG knowledge system (POST `/api/v1/queries`) into a Model Context Protocol server. It covers theory, qualification criteria, implementation patterns, and practitioner best practices — optimised for the Obsidian-first, stdio+HTTP dual-transport target architecture.

---

## Table of Contents

1. [What Is MCP?](#1-what-is-mcp)
2. [Does This Project Qualify as an MCP Server?](#2-does-this-project-qualify-as-an-mcp-server)
3. [Additional Context — MCP Concepts to Internalise](#3-additional-context--mcp-concepts-to-internalise)
4. [Architecture for This System](#4-architecture-for-this-system)
5. [Best Practices from Practitioners](#5-best-practices-from-practitioners)
6. [Do's and Don'ts](#6-dos-and-donts)
7. [Implementation Checklist](#7-implementation-checklist)

---

## 1. What Is MCP?

**Model Context Protocol (MCP)** is an open, language-agnostic protocol that standardises how AI applications (hosts/clients) connect to external capabilities (servers). It replaces ad-hoc, point-to-point API integrations with a single, discoverable interface — analogous to what USB did for hardware peripherals.

### Core concept

```
MCP Host (e.g. Obsidian plugin, Claude, VS Code)
        ↓  discovers & calls
MCP Server  (your system)
        ↓  wraps & calls
Your existing backend (POST /api/v1/queries, pgvector, graph DB)
```

The host never needs to know the internal shape of your system. It only speaks MCP.

### Transport layer

| Transport | When to use |
|---|---|
| **stdio** | Local process spawned by the host. Required by Obsidian MCP plugins (`mcp-obsidian`). Simplest to implement. |
| **HTTP + SSE** | Remote or networked clients. Enables streaming responses and future web/mobile clients. |
| **WebSocket** | Bidirectional real-time use cases. Not required for initial target. |

### Protocol primitives

MCP exposes three primitive types that hosts can discover and call at runtime:

| Primitive | Description | Maps to your system |
|---|---|---|
| **Tool** | A callable function the LLM can invoke. Returns structured data. | `query_knowledge_graph`, `get_entity`, `compare_entities` |
| **Resource** | A readable data object (like a file or DB record). URI-addressable. | A wiki page, a graph node, a chunk |
| **Prompt** | A reusable prompt template the host can inject into its context. | Query templates per `query_type` |

### How MCP works at runtime

1. Host spawns or connects to the MCP server.
2. Host calls `tools/list` — server responds with all available tool schemas.
3. LLM decides which tool to call based on user input and tool descriptions.
4. Host sends `tools/call` with arguments.
5. Server executes and returns `content` (text, JSON, image, etc.).
6. LLM synthesises the final answer from the tool result.

---

## 2. Does This Project Qualify as an MCP Server?

### Qualification criteria

The following metrics determine whether a system is a strong MCP server candidate. This project scores on every dimension.

| Criterion | Explanation | This System |
|---|---|---|
| **Exposes callable capabilities** | The system can do something discrete and repeatable when invoked | ✅ POST `/api/v1/queries` is a well-defined operation |
| **Returns structured or semi-structured output** | MCP tools return JSON-serialisable content | ✅ `QueryResponse` is fully structured (seeds, subgraph, chunks, answer) |
| **Has domain-specific retrieval logic** | The system's value is in *how* it retrieves, not just what it stores | ✅ Hybrid retrieval: embedding → pgvector → graph traversal → chunk scoring |
| **Accepts typed inputs** | Inputs can be described as a JSON Schema | ✅ `query`, `query_type`, `generate_answer`, `seed_entity_ids`, `max_context_tokens` |
| **Has independent capabilities that map to tools** | Multiple distinct operations the host can choose between | ✅ Factual lookup, relationship query, summarisation, comparison — all distinct |
| **Manages its own state/storage** | The server owns data the host doesn't have | ✅ pgvector embeddings, graph edges, wiki pages, entity store |
| **Can be invoked without knowing internals** | A host can call it with just a query string | ✅ `generate_answer=True` with a plain `query` is sufficient |
| **Slow or expensive computation the host shouldn't repeat** | Justifies externalising as a server | ✅ Graph traversal, embedding cosine search, LLM synthesis — all expensive |

### RAG pipeline classification

This system is a **Graph-RAG pipeline**, which is one of the canonical MCP server archetypes:

> Expose the retrieval backend as an MCP resource and the query endpoint as an MCP tool.

The fact that retrieval is hybrid (vector + graph) makes the tool *more* valuable as an MCP server, not less — the host gets sophisticated context it could never build itself.

---

## 3. Additional Context — MCP Concepts to Internalise

### 3.1 Tool schema is your contract

Every tool you expose must have a JSON Schema input definition. The schema is what the LLM reads to decide when and how to call your tool. A vague schema produces vague calls.

```json
{
  "name": "query_knowledge_graph",
  "description": "Search the second-brain knowledge graph using a natural language query. Returns structured context including entity seeds, subgraph relationships, wiki excerpts, and a synthesised answer. Use for factual lookups, relationship exploration, summarisation, and entity comparisons.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The natural language question or search query"
      },
      "query_type": {
        "type": "string",
        "enum": ["factual_lookup", "relationship_query", "summarization", "comparison"],
        "description": "Optional. Force a specific retrieval strategy. Omit to let the system auto-classify."
      },
      "generate_answer": {
        "type": "boolean",
        "default": true,
        "description": "Whether to synthesise a final LLM answer. Set false to return raw retrieval context only."
      },
      "seed_entity_ids": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Optional. Anchor retrieval to specific entity IDs for precision lookups."
      },
      "max_context_tokens": {
        "type": "integer",
        "description": "Optional. Cap the token budget for context assembly."
      }
    },
    "required": ["query"]
  }
}
```

### 3.2 Resources vs. Tools — know the difference

| | Tool | Resource |
|---|---|---|
| **Invocation** | LLM decides to call it (agentic) | Host or user explicitly fetches it |
| **Side effects** | Allowed | Read-only by convention |
| **Use case** | Executing retrieval, triggering actions | Exposing a specific entity, wiki page, or node |
| **URI** | Not applicable | `knowledge://entity/{id}`, `knowledge://wiki/{slug}` |

For this system: expose `query_knowledge_graph` as a **tool** and individual entities/wiki pages as **resources**. Resources let Obsidian users pin specific graph nodes into context manually.

### 3.3 Prompt templates

MCP supports pre-defined prompt templates that host applications can inject. For a second-brain system, useful templates include:

- `summarise_topic` — a prompt that wraps a topic name and instructs the system to retrieve and summarise
- `find_connections` — prompts the system to explore relationships from a seed entity
- `compare_concepts` — triggers a comparison query between two named entities

These become reusable slash-commands or quick actions in Obsidian.

### 3.4 Content types in responses

MCP tool responses return an array of `content` blocks. Use the right type:

| Content type | When to use |
|---|---|
| `text` | Plain answer, markdown-formatted output |
| `resource` | Referencing a URI in your resource space |
| `image` | Graph visualisations (future) |

For now, return `text` with the synthesised answer and optionally embed structured JSON for the full `RetrievalResult` as a second `text` block (clients that want raw context can parse it).

### 3.5 The `stdio` process model

When Obsidian spawns your MCP server via stdio:

- Your process reads JSON-RPC messages from `stdin` and writes responses to `stdout`.
- `stderr` is for logs only — never write JSON-RPC to stderr.
- The process must stay alive for the duration of the session. Handle `SIGTERM` gracefully.
- Startup must be fast (< 2 seconds) — Obsidian's plugin will time out otherwise.

Since your real logic lives in the HTTP API, the stdio process is a **thin wrapper** that proxies calls. This is the correct architecture.

---

## 4. Architecture for This System

### Dual-transport wrapper

```
Obsidian (mcp-obsidian plugin)
        ↓ stdio (JSON-RPC over stdin/stdout)
┌─────────────────────────────────┐
│   MCP Server Process            │
│   (thin wrapper — Node/Python)  │
│                                 │
│  ┌──────────┐  ┌─────────────┐  │
│  │  stdio   │  │  HTTP+SSE   │  │  ← Both transports, one server
│  │ handler  │  │   handler   │  │
│  └────┬─────┘  └──────┬──────┘  │
│       └────────┬───────┘         │
│           HTTP client            │
└───────────────┬──────────────────┘
                ↓ POST /api/v1/queries
        Your existing FastAPI/Flask backend
                ↓
        pgvector + graph DB + LLM
```

**Why this is correct:**
- The MCP wrapper is stateless — no business logic lives in it.
- Your existing backend stays unchanged.
- stdio satisfies Obsidian now; HTTP+SSE satisfies future clients for free.
- If the backend changes, only the HTTP call inside the wrapper needs updating.

### Tool surface (initial)

| Tool name | Maps to | Notes |
|---|---|---|
| `query_knowledge_graph` | POST `/api/v1/queries` with `generate_answer: true` | Primary tool — 80% of calls |
| `retrieve_context` | POST `/api/v1/queries` with `generate_answer: false` | Returns raw retrieval for host to synthesise |
| `compare_entities` | POST `/api/v1/queries` with `query_type: "comparison"` and two seed IDs | Explicit comparison flow |
| `get_entity_context` | POST `/api/v1/queries` with `seed_entity_ids` set | Anchor-first lookup |

Start with `query_knowledge_graph` only. Add others after validating the integration.

### Resource surface (initial)

| URI pattern | Returns |
|---|---|
| `knowledge://entity/{id}` | Entity metadata, edges, associated chunks |
| `knowledge://wiki/{slug}` | Raw wiki page content |
| `knowledge://query/{hash}` | Cached result of a previous query |

Resources are optional for the Obsidian MVP — implement tools first.

---

## 5. Best Practices from Practitioners

### 5.1 Tool descriptions are prompt engineering

The `description` field of every tool and parameter is the only thing the LLM reads when deciding to call your tool. Treat it as a prompt, not a comment.

- Be specific about *when* to use the tool vs. when not to.
- Describe the output shape so the LLM knows what it will receive.
- Mention limitations (e.g. "limited to knowledge in the graph — does not access the internet").

### 5.2 Fail loudly, not silently

MCP tools should return structured errors that the LLM can understand and relay to the user. Never swallow exceptions and return an empty result.

```json
{
  "content": [{
    "type": "text",
    "text": "ERROR: Query failed — entity ID 'xyz' not found in the knowledge graph. Try a broader query without seed_entity_ids."
  }],
  "isError": true
}
```

The `isError: true` flag tells the host this is a failure, not an answer.

### 5.3 Keep the wrapper truly thin

The MCP server process should contain zero retrieval logic. Its only jobs are:

1. Validate and deserialise the incoming MCP request.
2. Map MCP tool arguments to your HTTP API request body.
3. Call the HTTP API.
4. Map the HTTP response to an MCP content block.
5. Return.

Any logic beyond this makes the wrapper harder to maintain and test independently.

### 5.4 Version your tools

Name tools with a stable identifier you can version if needed (`query_knowledge_graph_v2`). Hosts cache tool lists — if you change a tool's schema, bump the name or version to force re-discovery.

### 5.5 Timeout and retry strategy

Obsidian's stdio plugin will hang if your tool call doesn't return. Set an explicit timeout on your HTTP call inside the wrapper (recommended: 30s for answer synthesis, 10s for context-only retrieval). Return an error content block on timeout rather than letting the process stall.

### 5.6 Streaming for long answers

For `generate_answer: true` calls where LLM synthesis takes > 5 seconds, the HTTP+SSE transport can stream partial results. The stdio transport does not support streaming in most Obsidian plugin implementations — return the complete result at once for stdio.

### 5.7 Structured output alongside prose

Return both the synthesised answer and the raw `RetrievalResult` as separate content blocks. Advanced Obsidian users and future clients can use the raw retrieval data (seeds, subgraph, chunks) to build richer UIs.

```json
{
  "content": [
    { "type": "text", "text": "The synthesised answer goes here..." },
    { "type": "text", "text": "{\"seeds\": [...], \"subgraph\": [...], \"chunks\": [...]}" }
  ]
}
```

### 5.8 Log to stderr, nothing else

In stdio mode, only JSON-RPC messages go to stdout. All debug logging, request tracing, and error messages go to stderr. Mixing them breaks the protocol. Use a structured logger (e.g. `structlog`, `winston`) configured to write to stderr.

### 5.9 Graceful shutdown

Register a `SIGTERM` handler. When Obsidian closes the plugin, it sends SIGTERM to the stdio process. Clean up any open connections to your HTTP backend before exiting. Unclean shutdowns cause the Obsidian plugin to hang on restart.

### 5.10 Test with MCP Inspector before connecting to Obsidian

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is a CLI tool that lets you test your server's tool discovery and tool calls before wiring it to a real host. It surfaces schema errors and response format issues immediately.

```bash
npx @modelcontextprotocol/inspector python mcp_server.py
# or
npx @modelcontextprotocol/inspector node mcp_server.js
```

---

## 6. Do's and Don'ts

### Do's ✅

**Protocol and transport**
- Build both stdio and HTTP+SSE transports from day one — they share the same tool handler logic.
- Keep stdio as the primary transport for Obsidian; treat HTTP+SSE as a bonus output.
- Use JSON Schema `required` arrays to enforce that `query` is always provided.
- Return `isError: true` with a helpful message on every failure path.
- Write all logs to `stderr` in stdio mode.
- Handle `SIGTERM` gracefully in the stdio process.

**Tool design**
- Write tool descriptions as LLM-facing prompts — be explicit and specific.
- Expose `query_type` as an optional parameter so advanced users can override auto-classification.
- Include example values in parameter descriptions where possible.
- Start with one core tool (`query_knowledge_graph`) and expand after validating the integration.
- Return both synthesised answer and raw retrieval context in the response.

**Architecture**
- Keep the MCP wrapper stateless and logic-free — all intelligence stays in the existing backend.
- Set explicit HTTP timeouts inside the wrapper (30s answer, 10s context-only).
- Test with MCP Inspector before connecting to Obsidian.
- Pin the MCP SDK version in your dependencies — the protocol is still evolving.

**Obsidian-specific**
- Ensure the stdio process starts in < 2 seconds.
- Document the exact command Obsidian should use to spawn the process (including any env vars for the backend URL).
- Provide a sample `mcp-config.json` for Obsidian plugin configuration.

---

### Don'ts ❌

**Protocol and transport**
- Never write anything other than JSON-RPC to stdout in stdio mode.
- Never block stdin — always process messages asynchronously.
- Don't implement WebSocket until HTTP+SSE is validated with real clients.
- Don't assume the host will cache tool lists indefinitely — re-expose all tools on every `tools/list` call.

**Tool design**
- Don't expose too many tools at once — start with 1–2. LLMs get confused by large, similar-sounding tool sets.
- Don't put business logic (graph traversal, embedding, ranking) inside the MCP server process.
- Don't return raw Python/stack tracebacks as tool responses — the LLM cannot use them.
- Don't use generic tool names like `search` or `query` — be domain-specific (`query_knowledge_graph`).
- Don't silently return empty results when the backend fails — always surface the error.

**Architecture**
- Don't duplicate retrieval logic between the MCP wrapper and the existing HTTP backend.
- Don't embed API keys or secrets in the tool schema or response — use environment variables in the wrapper process.
- Don't skip the MCP Inspector testing step — schema errors are invisible until a host tries to call your tool.

**Obsidian-specific**
- Don't make the stdio process depend on a GUI or interactive terminal — it runs headlessly.
- Don't use relative paths in the Obsidian spawn command — always use absolute paths or `npx`/`uvx` launchers.

---

## 7. Implementation Checklist

Use this as a step-by-step execution plan.

### Phase 1 — MCP wrapper (stdio, Obsidian MVP)

- [ ] Choose implementation language for wrapper (Python with `mcp` SDK or Node.js with `@modelcontextprotocol/sdk`)
- [ ] Install MCP SDK and pin version in `requirements.txt` / `package.json`
- [ ] Implement `query_knowledge_graph` tool with full JSON Schema
- [ ] Implement HTTP client call to `POST /api/v1/queries` inside the tool handler
- [ ] Map `QueryResponse` to MCP content blocks (text answer + raw JSON)
- [ ] Implement error handling with `isError: true` responses
- [ ] Configure `stderr` logging with a structured logger
- [ ] Register `SIGTERM` handler for graceful shutdown
- [ ] Test with MCP Inspector — verify `tools/list` and `tools/call`
- [ ] Write `mcp-config.json` for Obsidian plugin configuration
- [ ] Document the startup command with all required env vars

### Phase 2 — HTTP+SSE transport

- [ ] Add HTTP+SSE transport alongside existing stdio handler
- [ ] Expose server on configurable port (default: 3000)
- [ ] Test HTTP transport with MCP Inspector (`--transport http`)
- [ ] Validate that both transports use the same tool handler logic

### Phase 3 — Tool expansion

- [ ] Add `retrieve_context` tool (`generate_answer: false`)
- [ ] Add `compare_entities` tool (`query_type: "comparison"`)
- [ ] Add `get_entity_context` tool (seed-first lookup)
- [ ] Update tool descriptions and test each with MCP Inspector

### Phase 4 — Resources (optional, post-MVP)

- [ ] Implement `resources/list` handler
- [ ] Implement `resources/read` handler for `knowledge://entity/{id}`
- [ ] Implement `resources/read` handler for `knowledge://wiki/{slug}`
- [ ] Test resource fetching from Obsidian plugin

### Phase 5 — Prompt templates (optional)

- [ ] Define `summarise_topic` prompt template
- [ ] Define `find_connections` prompt template
- [ ] Define `compare_concepts` prompt template
- [ ] Expose via `prompts/list` and `prompts/get` handlers

---

## Reference links

| Resource | URL |
|---|---|
| MCP specification | https://spec.modelcontextprotocol.io |
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk |
| MCP TypeScript SDK | https://github.com/modelcontextprotocol/typescript-sdk |
| MCP Inspector (testing tool) | https://github.com/modelcontextprotocol/inspector |
| Obsidian MCP plugin (`mcp-obsidian`) | https://github.com/MarkusSagen/mcp-obsidian |
| MCP server examples | https://github.com/modelcontextprotocol/servers |

---

*Last updated: June 2026 — scoped to graph-RAG second-brain system with Obsidian as primary MCP host.*