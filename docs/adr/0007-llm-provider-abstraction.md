# ADR-0007: Thin custom LLM provider abstraction

## Status
Accepted

## Context
The pipeline makes LLM calls at multiple stages: image/table captioning
(ADR-0003), entity/relation extraction, embedding generation, wiki page
synthesis, and query answering. How these calls are made affects which LLMs/
endpoints a deployment can use.

For enterprise customers (ADR-0004), data-sovereignty requirements often mean
"must use our Azure OpenAI tenant" or "must use an on-prem model via vLLM/Ollama"
— the same sovereignty rationale that motivated single-tenant deployment extends
to the LLM layer.

Options considered:

1. **Hardcode to one provider** (e.g. call the Anthropic or OpenAI API directly
   throughout the codebase) — simplest, but a customer needing a different
   provider/endpoint requires a code fork.
2. **Thin custom abstraction** — a small interface (e.g. `complete()`,
   `embed()`, `caption_image()`) with built-in implementations for
   OpenAI-compatible endpoints (covers OpenAI, Azure OpenAI, vLLM, Ollama) and
   Anthropic, selected/configured per deployment via environment variables.
3. **Adopt an existing framework's abstraction wholesale** (LiteLLM, LangChain)
   as the LLM layer used throughout the codebase.

## Decision
Build a **thin custom abstraction** (option 2): a small protocol/interface
defining the LLM operations the system needs, with first-class implementations
for OpenAI-compatible endpoints and Anthropic, configured per deployment.
Libraries like LiteLLM may be used *underneath* a given implementation as an
optimization, but the rest of the codebase depends on this project's own
interface, not on LiteLLM's or LangChain's types directly.

## Rationale
- **Directly serves the enterprise "bring your own model" requirement** —
  switching providers/endpoints is a configuration change, not a code change.
- **Portfolio value**: demonstrates deliberate interface design (a small,
  domain-specific abstraction) rather than either reinventing everything or
  depending on a large, opinionated framework for something this focused.
- **Avoids lock-in to a third-party abstraction's design decisions** — if
  LiteLLM/LangChain's interfaces change or don't fit a new use case, only the
  implementation behind this project's interface needs to change.

## Consequences
- The interface must be designed to cover all current LLM call sites
  (captioning, extraction, embedding, wiki synthesis, query answering) from the
  start, even if only one implementation exists initially.
- Each deployment configures its LLM provider(s) via environment variables/config
  (e.g. base URL, API key, model names per operation) — this configuration
  surface needs documenting as part of the deployment artifacts (ADR-0004).
- Different operations may reasonably use different models (e.g. a cheap/fast
  model for captioning, a stronger model for wiki synthesis) — the interface
  should support per-operation model selection, not assume one model for
  everything.
