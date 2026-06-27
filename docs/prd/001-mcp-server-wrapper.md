# PRD-001: MCP Server Wrapper for RAG Wiki

## Problem Statement

The RAG Wiki system has a rich query API, entity graph, and wiki pages, but
users can only access it through the FastAPI web API or the `rag-wiki` CLI.
There is no way to query the knowledge graph from within a user's existing
knowledge-management workflow — specifically from Obsidian, where knowledge
workers already browse, link, and organize their notes.

The Model Context Protocol (MCP) is the emerging standard for connecting AI
applications to external tools. By wrapping the query pipeline as an MCP server,
users can query the knowledge graph directly from an Obsidian chat prompt
without switching context.

## Solution

A thin MCP server wrapper built with Python and FastMCP that sits between
MCP hosts (Obsidian, ChatForest, Claude Desktop) and the existing FastAPI
backend. The wrapper exposes two tools — `query_knowledge_graph` and
`retrieve_context` — that proxy calls to `POST /api/v1/queries` on the
existing backend.

The server runs in stdio mode by default (for Obsidian), with an opt-in
Streamable HTTP mode for remote clients. A single env var switches between
transports. The wrapper is stateless and contains zero retrieval logic — all
intelligence stays in the existing backend.

## User Stories

1. As an Obsidian user, I want to ask a natural-language question about my
   knowledge graph from within Obsidian, so that I can reference and explore
   my wiki without leaving my note-taking environment.

2. As an Obsidian user, I want the knowledge graph query tool to appear
   automatically in my chat plugin's tool list, so that I don't need to
   manually configure tool schemas.

3. As an Obsidian user, I want to retrieve raw graph context without a
   synthesised answer, so that I can reason over the underlying entities,
   relations, and chunks myself.

4. As an Obsidian user, I want a clear error message if the backend is
   unreachable, so that I know to start the server rather than wondering why
   the tool returned nothing.

5. As an Obsidian user, I want the tool to time out gracefully rather than
   hanging indefinitely, so that I can retry with a narrower question.

6. As a developer, I want to test the MCP tools without running a subprocess
   or a real backend, so that I can iterate quickly with deterministic
   results.

7. As a developer, I want to validate the MCP server with MCP Inspector
   before wiring it to Obsidian, so that I catch schema and error-handling
   issues in a debuggable UI.

8. As a developer, I want the MCP server to be invocable via `uv run rag-wiki
   mcp serve`, so that the Obsidian config is a single documented command.

9. As an operator, I want to switch the MCP server from stdio to HTTP mode
   with an environment variable, so that I can connect remote MCP hosts
   without code changes.

10. As a future developer, I want tool descriptions written as LLM-facing
    prompts, so that the AI understands when and how to use each tool without
    trial and error.

## Implementation Decisions

### Architecture: Thin HTTP proxy wrapper

The MCP server does not import or call any retrieval logic. It is a
stateless HTTP proxy that maps MCP tool calls to `POST /api/v1/queries` on
the existing FastAPI backend. The `httpx.AsyncClient` is shared across tool
calls for connection pooling, with explicit timeouts (5s connect, 30s read).

### Framework: FastMCP (Python)

FastMCP provides `FastMCP` class with `@mcp.tool` decoration, automatic JSON
Schema generation from Python type annotations, and `mcp.run()` supporting
both stdio and Streamable HTTP transport. Pinned as `fastmcp>=3.4,<4` — minor
version breaking changes are accepted for ecosystem alignment.

### Transport: Dual (stdio + Streamable HTTP)

- **stdio** — default. All Obsidian MCP plugins use stdio via subprocess
  spawning (`command + args`).
- **Streamable HTTP** — opt-in via `RAG_WIKI_MCP_TRANSPORT=http`. Requires
  an explicit port (`RAG_WIKI_MCP_PORT`). Uses FastMCP's built-in
  `mcp.run(transport="http")` — not SSE, which was superseded in the
  2025-03-26 protocol revision.

### Tool surface (Phase 1)

**`query_knowledge_graph`**
- Returns a synthesised natural-language answer from the knowledge graph.
- Maps to `POST /api/v1/queries` with `generate_answer=true`.
- Accepts: `query` (required), `query_type`, `seed_entity_ids`, `max_context_tokens`.

**`retrieve_context`**
- Returns raw `RetrievalResult` as structured JSON — seeds, subgraph edges,
  scored chunks, wiki page snapshot. No answer synthesis.
- Maps to `POST /api/v1/queries` with `generate_answer=false`.
- Accepts: `query` (required), `query_type`, `seed_entity_ids`, `max_context_tokens`.

### Entrypoint: `rag-wiki mcp serve`

Subcommand of the existing Typer CLI. CLI flags map directly to environment
variables (`RAG_WIKI_MCP_TRANSPORT`, `RAG_WIKI_MCP_HOST`,
`RAG_WIKI_MCP_PORT`). The Obsidian config entry is:

```json
{
  "command": "uv",
  "args": ["run", "rag-wiki", "mcp", "serve"],
  "env": { "RAG_WIKI_API_URL": "http://localhost:8000" }
}
```

### Factory pattern for testability

`create_mcp_server(settings, http_client)` accepts optional `Settings` and
`httpx.AsyncClient` parameters. Tests inject a mock HTTP transport via
`httpx.MockTransport` and call tools through FastMCP's in-memory dispatch —
no subprocess, no network, no env vars.

### Error handling

All httpx exceptions (`ConnectError`, `TimeoutException`, `HTTPStatusError`,
`RequestError`) are caught in the `_call_backend` proxy function and mapped to
human-readable messages by `backend_error_message()`. MCP responses use
`isError: true` for all failure paths.

### Logging

`structlog` configured to write to `stderr` with non-color `ConsoleRenderer`.
Only JSON-RPC messages go to `stdout` in stdio mode.

### Module structure

```
rag_wiki/mcp/
  __init__.py        # exports create_mcp_server()
  server.py          # create_mcp_server() factory — FastMCP instance, register_tools()
  tools.py           # register_tools(mcp, client), _call_backend(), @mcp.tool handlers
  transport.py       # run() entrypoint, structlog stderr config, transport dispatch
  errors.py          # backend_error_message(exc, api_url) -> str

tests/mcp/
  __init__.py
  conftest.py        # fake_backend handler, mock httpx.AsyncClient fixture
  test_tools.py      # tool handler tests via FastMCP in-memory dispatch
  test_errors.py     # backend_error_message for each httpx exception type
  test_server.py     # create_mcp_server() wiring test
```

### Modified modules

- `rag_wiki/cli.py` — add `mcp_app` typer group with `serve` command
- `rag_wiki/settings.py` — add `mcp_transport`, `mcp_api_url`, `mcp_host`,
  `mcp_port` (default `None` — must be set explicitly for HTTP)
- `pyproject.toml` — add `fastmcp>=3.4,<4` to `dependencies`
- `docs/mcp-reference.md` — update SSE references to Streamable HTTP, add
  Phase 2 resource/template designs

### Settings

```python
mcp_transport: Literal["stdio", "http"] = "stdio"
mcp_api_url: AnyHttpUrl = "http://localhost:8000"  # backend to proxy to
mcp_host: str = "127.0.0.1"
mcp_port: int | None = None  # no default — must be set explicitly for HTTP
```

### Deferred (Phase 2)

- **Resources:** `knowledge://entity/{id}`, `knowledge://wiki/{slug}`,
  `knowledge://graph/recent`
- **Prompt templates:** `summarise_topic`, `find_connections`, `compare_concepts`

URI scheme and template designs are documented in `docs/mcp-reference.md` but
not implemented. These require dedicated GET endpoints on the backend, not
shoehorned through the query API.

## Testing Decisions

### What makes a good test

- Tests should exercise the **external contract** (tool schemas, error
  messages, backend proxy behaviour), not internal implementation details.
- Tool handler tests use **`httpx.MockTransport`** to simulate backend
  responses — no real HTTP calls, no running FastAPI server.
- Error handler tests call `backend_error_message()` directly with each
  httpx exception type — no fixtures, no project imports, no env vars.
- Factory tests verify that `create_mcp_server()` wires tools and settings
  correctly — no mock needed, just construction and tool listing.

### Modules tested

| Test file | What it tests | Prior art |
|-----------|---------------|-----------|
| `tests/mcp/test_tools.py` | Tool handler behaviour (happy path, backend down, timeout, 422, 500) | Uses `httpx.MockTransport` pattern standard for async HTTP testing |
| `tests/mcp/test_errors.py` | `backend_error_message()` for each httpx exception | Pure unit tests — no prior art needed, trivially isolated |
| `tests/mcp/test_server.py` | `create_mcp_server()` factory wiring | Similar to `test_create_app` pattern in FastAPI projects |

### Manual validation

Before connecting to Obsidian, run MCP Inspector and verify three things:

```bash
npx @modelcontextprotocol/inspector uv run rag-wiki mcp serve
```

1. `tools/list` returns both tools with correct parameter schemas
2. `tools/call` with a live backend returns a text content block with an answer
3. `tools/call` with the backend stopped returns a clean error message, not a
   stack trace

## Out of Scope

- **Resources and prompt templates** — deferred to Phase 2 (documented in
  `docs/mcp-reference.md`).
- **Auth/RBAC** — not needed for the stdio personal-use path. If HTTP mode
  is deployed in multi-user scenarios, auth is a separate concern.
- **Multi-model support** — the MCP server does not select or configure LLM
  models; that remains the backend's responsibility (ADR-0007).
- **Caching layer** — no per-query caching or result idempotency in the MCP
  wrapper. The backend may add its own caching later.
- **Observability** — no metrics, tracing, or health checks in the MCP wrapper.
  Logging to stderr is sufficient for Phase 1.

## Further Notes

- The MCP server depends on `uv` being on the user's `PATH` for the Obsidian
  spawn command. Document this explicitly in the README.
- The FastAPI backend must be running independently. The MCP server returns
  a clear error if the backend is unreachable.
- No explicit SIGTERM handler is needed in Phase 1 — FastMCP's stdio loop
  exits cleanly on pipe EOF, and Python's default SIGTERM behaviour is
  sufficient for a stateless proxy.
- `errors.py` intentionally imports nothing from the RAG Wiki project — only
  from `httpx`. This keeps it independently testable.
