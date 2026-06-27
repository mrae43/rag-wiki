# ADR-0016: MCP server wrapper for LLM-host integration

## Status

Accepted

## Context

The system exposes a FastAPI query endpoint (`POST /api/v1/queries`) and
read-only entity/wiki-page browsing routes. Users interact with the knowledge
graph through the API directly or via a future Obsidian-based interface.

The Model Context Protocol (MCP) is the emerging standard for connecting AI
applications (hosts like Obsidian, Claude Desktop, VS Code) to external
capabilities. Converting the query pipeline into an MCP server lets users query
the knowledge graph from within their existing knowledge-management workflow
without switching to a separate web UI.

The goal is an Obsidian-first MCP server where the primary interaction is
querying the knowledge graph from within the Obsidian vault using a natural
language prompt. The server must be robust enough for daily personal use and
extensible to other MCP hosts.

Key design questions:

1. Where does the MCP server live in the codebase?
2. What language and SDK?
3. stdio, HTTP, or both?
4. How does the MCP server call the existing backend?
5. What tools to expose in the initial surface?
6. How to handle errors, logging, and lifecycle?

## Decision

### 1. Package location: `rag_wiki/mcp/`

A new module inside the existing `rag_wiki` package:

```
rag_wiki/mcp/
  __init__.py        # exports create_mcp_server()
  server.py          # FastMCP instance factory
  tools.py           # register_tools() — tool registration, backend proxy
  transport.py       # run() entrypoint, logging config, transport dispatch
  errors.py          # backend_error_message() — maps httpx exceptions to messages
```

Lives in the existing package so it shares `settings.py`, `structlog` config,
and existing tooling. No separate project or venv.

### 2. SDK: Python with FastMCP

Use `fastmcp>=3.4,<4` as a core dependency (not optional). FastMCP provides the
`FastMCP` class with `@mcp.tool` decoration, `mcp.run(transport="stdio")` and
`mcp.run(transport="http")`, and automatic JSON Schema generation from Python
type annotations. It is actively maintained and supports Streamable HTTP.

FastMCP permits breaking changes within minor versions; the version range is
a deliberate acceptance of that risk given that this is an application, not a
library.

### 3. Dual transport: stdio (default) + Streamable HTTP

| Transport | Use case | Config |
|-----------|----------|--------|
| `stdio` | Obsidian (every MCP Obsidian plugin uses stdio) | default |
| `http` | Remote clients, ChatForest, future web/mobile | `RAG_WIKI_MCP_TRANSPORT=http` |

Because FastMCP's SSE transport is superseded by Streamable HTTP (2025-03-26
protocol revision), the HTTP transport uses `mcp.run(transport="http")` with
path `/mcp`, not SSE.

Mandatory port check: if `transport="http"` and no port is set, the runner
raises `ValueError` with a clear message. No default port for HTTP.

### 4. Backend coupling: thin HTTP proxy

The MCP tool handlers do not import or call the retrieval pipeline directly.
Instead they make HTTP requests to the existing FastAPI backend via a shared
`httpx.AsyncClient`. The client is constructed once in `create_mcp_server()`
and injected into the tool registration function, enabling connection pooling
and testability via `httpx.MockTransport`.

Two timeouts: `connect=5.0` (fast fail if backend is down), `read=30.0`
(covers LLM synthesis). Never use a default `None` timeout.

### 5. Tool surface: query + retrieve (Phase 1)

Two tools, both wrapping `POST /api/v1/queries`:

- **`query_knowledge_graph`** — sets `generate_answer=true`. Returns a
  synthesised natural-language answer. Primary tool.
- **`retrieve_context`** — sets `generate_answer=false`. Returns raw
  `RetrievalResult` as structured JSON. For hosts that want to reason over
  the context themselves.

Both accept `query`, optional `query_type`, `seed_entity_ids`, and
`max_context_tokens`. Tool descriptions are written as LLM-facing prompts
per MCP best practices.

### 6. Tools entrypoint: `rag-wiki mcp serve`

The MCP server is a subcommand of the existing Typer CLI:

```bash
uv run rag-wiki mcp serve
```

CLI flags mirror environment variables (`RAG_WIKI_MCP_TRANSPORT`,
`RAG_WIKI_MCP_HOST`, `RAG_WIKI_MCP_PORT`), so the Obsidian config is:

```json
{
  "command": "uv",
  "args": ["run", "rag-wiki", "mcp", "serve"],
  "env": { "RAG_WIKI_API_URL": "http://localhost:8000" }
}
```

### 7. Factory pattern: `create_mcp_server()`

A factory function in `server.py` that accepts optional `Settings` and
`httpx.AsyncClient` parameters, constructs a `FastMCP` instance, calls
`register_tools(mcp, client)` from `tools.py`, and returns the instance.

This enables testing without subprocess or network — tests inject a mock
HTTP transport and call tools via FastMCP's in-memory dispatch.

### 8. Error handling: catch-all in the proxy layer

`_call_backend()` in `tools.py` catches all `httpx` exceptions and delegates
to `backend_error_message()` in `errors.py` for human-readable messages.
The MCP response uses `isError: true` for all failure paths.

`errors.py` imports nothing from the project — only from `httpx`.
`backend_error_message(exc, api_url)` is independently testable with no
fixtures or config.

### 9. Logging: structlog to stderr

There is no central `configure_logging()` in the codebase. `transport.py`
calls `structlog.configure()` with `PrintLoggerFactory(file=sys.stderr)`
and a non-color `ConsoleRenderer` before `mcp.run()`. Only JSON-RPC goes
to stdout.

### 10. Graceful shutdown: none in Phase 1

FastMCP's stdio loop exits cleanly on pipe EOF when Obsidian closes the
plugin. Python's default SIGTERM behaviour is sufficient for a stateless
proxy with no long-lived transactions. Revisit if an app-lifetime httpx
client is introduced.

### 11. Resources and prompt templates: deferred to Phase 2

Designed and documented in `docs/mcp-reference.md` but not implemented:

- **Resource URIs:** `knowledge://entity/{id}`, `knowledge://wiki/{slug}`,
  `knowledge://graph/recent`
- **Prompt templates:** `summarise_topic`, `find_connections`, `compare_concepts`

These require dedicated backend read endpoints (not shoehorned through the
query API) and are deferred until Phase 2 validates the tool-first approach.

### 12. Testing pattern

| Test file | What it tests | How |
|-----------|---------------|-----|
| `tests/mcp/test_tools.py` | Tool handler behaviour | `httpx.MockTransport` + FastMCP in-memory dispatch |
| `tests/mcp/test_errors.py` | `backend_error_message()` for each exception type | Unit tests, zero project imports |
| `tests/mcp/test_server.py` | `create_mcp_server()` factory wiring | No mock, just construction and tool listing |
| `tests/mcp/conftest.py` | `fake_backend` handler, mock client fixture | Reusable across all test files |

Validation gate before Obsidian wiring: MCP Inspector

```bash
npx @modelcontextprotocol/inspector uv run rag-wiki mcp serve
```

Three checks: `tools/list` returns correct schemas, a live call returns an
answer, a call with the backend stopped returns a clean error message (not a
stack trace).

## Rationale

- **Python + FastMCP** over TypeScript: the entire codebase is Python; sharing
  settings, logging, and deployment patterns outweighs any ecosystem advantage
  TypeScript might offer. FastMCP's `mcp.run()` handles both transports with
  one call.
- **Dual transport from day one**: stdio is mandatory for Obsidian; Streamable
  HTTP enables future clients (ChatForest, remote access). Both share the same
  tool handlers via FastMCP. A single env var switches between them.
- **HTTP proxy over in-process call**: the wrapper must be stateless and
  logic-free. Calling the existing HTTP API keeps the MCP server thin,
  testable, and independently deployable. The FastAPI backend already handles
  auth, rate-limiting, and connection pooling.
- **Factory pattern over global instance**: injectable dependencies make
  testing trivial — no subprocess, no network, no env vars in tests. FastMCP's
  in-memory transport handles tool dispatch so tests stay fast and isolated.
- **Tool descriptions as prompt engineering**: the LLM decides which tool to
  call based solely on the `description` field. Writing them as clear,
  specific prompts is not documentation — it's core correctness.
- **Deferred resources/templates**: tools cover 80%+ of real usage. Resources
  and prompt templates are additive; shipping them now risks confusing the
  tool surface before real usage reveals the right design. Documenting them
  now prevents unknown-unknowns.

## Consequences

- `rag_wiki/mcp/` is added as a new module with five files.
- `pyproject.toml` gains `fastmcp>=3.4,<4` as a core dependency.
- `rag_wiki/cli.py` gains a `mcp serve` subcommand and imports from
  `rag_wiki.mcp.transport`.
- `rag_wiki/settings.py` gains four new settings:
  `mcp_transport`, `mcp_api_url`, `mcp_host`, `mcp_port`.
- `docs/mcp-reference.md` is updated to reflect Streamable HTTP (not SSE),
  the Phase 2 resource/template designs, and the Inspector validation steps.
- The Obsidian user must have `uv` on their `PATH` and the FastAPI backend
  running independently.
- All MCP tools are unavailable if the FastAPI backend is unreachable —
  documented in tool descriptions so the LLM can warn the user.
- The MCP server and FastAPI server are separate processes. In production
  this may mean two container images, two deployments, or running both behind
  a shared reverse proxy.
