# Open Knowledge Format (OKF) — Reference Document

> **Purpose of this document:** Source-of-truth reference on Google Cloud's Open Knowledge Format (OKF), structured with the 5W+1H framework, intended to guide decisions about integrating OKF into an application system.
>
> **Status:** OKF is at v0.1 (draft/early stage). Details below reflect public information as of July 2026 and should be re-verified against the official spec before production use.
> **Primary sources:** Google Cloud Blog announcement; `GoogleCloudPlatform/knowledge-catalog` GitHub repo (`okf/SPEC.md`); Google Cloud Tech announcement thread.

---

## 1. WHAT is OKF?

OKF (Open Knowledge Format) is an **open, vendor-neutral specification** for representing organizational knowledge — metadata, context, and curated insight about data and systems — in a format that both humans and AI agents/LLMs can read, write, and traverse.

Key characteristics:
- **It's a format, not a platform or service.** OKF doesn't require any proprietary account, SDK, database, or runtime.
- **A bundle is just a directory of Markdown files.** Each file represents one "concept" (e.g., a table, dataset, metric, API, runbook, playbook, or business process).
- **Each concept file** = a small YAML front-matter block (structured fields) + a Markdown body (free-form content).
- **Concepts link to each other** via ordinary Markdown links, turning the folder structure into a knowledge graph rather than a flat list of files.
- **Two reserved filenames** carry special meaning at any level of the hierarchy:
  - `index.md` — an optional directory listing, enabling "progressive disclosure" (agents/humans see what's available before opening files).
  - `log.md` — an optional chronological changelog for that part of the bundle.
- **Distribution formats:** a Git repository (recommended, for history/attribution/diffs), a tarball/zip archive, or a subdirectory inside a larger repo.
- **Formally, only one field is required in front matter** (`type`), though Google's own reference parser expects four (`type`, `title`, `description`, `timestamp`) — an inconsistency worth noting since the spec is still v0.1.

**What OKF explicitly does NOT try to do:**
- It does not define a fixed taxonomy of concept types (producers define their own).
- It does not prescribe storage, serving, or query infrastructure.
- It does not replace domain-specific schemas like Avro, Protobuf, or OpenAPI — it references them rather than subsuming them.

**Core terminology:**
| Term | Meaning |
|---|---|
| Knowledge Bundle | A self-contained, hierarchical collection of knowledge documents (the unit of distribution) |
| Concept | A single unit of knowledge, represented as one Markdown document |
| Concept ID | The file's path within the bundle, with the `.md` suffix removed |
| Producer | The person or system that creates/maintains the knowledge (e.g., a data team, a documentation pipeline) |
| Consumer | The system that uses the knowledge (e.g., an AI agent, LLM, search index, visualizer) |

Example bundle structure:
```
sales/
├── index.md
├── datasets/
│   ├── index.md
│   └── orders_db.md
├── tables/
│   ├── index.md
│   ├── orders.md
│   └── customers.md
└── metrics/
    ├── index.md
    └── weekly_active_users.md
```

Example concept file (`orders.md`):
```markdown
---
type: BigQuery Table
title: Orders
description: One row per completed customer order.
resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders
tags: [sales, revenue]
timestamp: 2026-05-28T14:30:00Z
---
# Schema
| Column     | Type   | Description                      |
|------------|--------|-----------------------------------|
| order_id   | STRING | Globally unique order identifier |
```

---

## 2. WHO created and uses OKF?

- **Creator:** Google Cloud's **Data Cloud team** (announced via the Google Cloud Blog and Google Cloud Tech's official channels).
- **Producers** (who write OKF bundles): documentation teams, data/analytics teams, enrichment pipelines/agents, or any system/person maintaining internal knowledge (table schemas, metric definitions, runbooks, policies, etc.).
- **Consumers** (who read/use OKF bundles): AI agents, LLMs, search indexes, visualizers, and Google Cloud's **Knowledge Catalog** (formerly Dataplex, rebranded as an "always-on context engine" for AI agents), which was updated to natively ingest OKF.
- **Community:** The spec, reference implementations, and sample bundles are open on GitHub (`GoogleCloudPlatform/knowledge-catalog`), and Google explicitly invites external contributions, alternative implementations, and adoption beyond Google's own products. Third parties (e.g., independent developers) have already built adjacent tools, such as a WordPress plugin that converts site content into OKF bundles.

---

## 3. WHEN was OKF introduced?

- Publicly announced around **June 12, 2026**, via the Google Cloud Blog and a companion thread from the Google Cloud Tech account.
- Currently at **version 0.1** — explicitly described by Google as "a starting point, not a finished standard," expected to evolve through community and internal use.
- Conceptually, OKF formalizes the **"LLM-wiki" pattern**, an idea popularized in an April 2026 gist by Andrej Karpathy describing markdown-based wikis that AI agents read, update, and maintain autonomously.

---

## 4. WHERE does OKF apply / where is it hosted?

- **Hosting/spec location:** The specification (`SPEC.md`), reference implementations, and sample bundles live in the public GitHub repo `GoogleCloudPlatform/knowledge-catalog`.
- **Where it's used in practice:**
  - Enterprise/organizational knowledge management — data catalogs, internal wikis, documentation of tables, APIs, metrics, and business processes.
  - Google Cloud's **Knowledge Catalog** product, which now ingests OKF bundles natively and serves them to AI agents.
  - Reference implementations target **BigQuery** specifically (an enrichment agent that walks a BigQuery dataset and drafts a concept document per table/view).
  - Beyond Google's original enterprise-data use case, early adopters have repurposed OKF for website/content structuring (e.g., turning blog/CMS content into OKF bundles for agent consumption) — though this is described by third parties as a "repurposing," not the original intended use.
- **Storage location:** Anywhere a directory of Markdown files can live — a Git repo, a tarball/zip, or a subdirectory within a larger codebase. No specific cloud or database is required.

---

## 5. WHY was OKF created?

Google identifies a recurring problem in building AI agents: **agents are only as capable as the context they're given**, but organizational knowledge is typically fragmented across:
- Data catalogs (each vendor with its own API/schema)
- Wikis and internal documentation
- Code comments and docstrings
- The heads of senior engineers

This fragmentation means every team building an AI agent re-solves the same "context assembly" problem from scratch, and every catalog vendor reinvents its own data model — none of it portable across products or organizations.

OKF's stated goals:
1. **Producer/consumer independence** — decouple who authors knowledge from who consumes it (a human-written bundle can be read by an agent; an agent-generated bundle can be browsed by a human via a visualizer; one LLM's output can be another LLM's input).
2. **Format, not platform** — avoid vendor lock-in; the format should never require a proprietary account or SDK.
3. **Standardize the minimum viable contract** — define just enough (folder structure, front matter, reserved filenames) to make knowledge interoperable, without dictating a taxonomy, storage layer, or query engine.

Independent commentary frames this distinction precisely: OKF v0.1 solves **structural interoperability** (a shared container format) but largely leaves **semantic interoperability** (shared meaning/taxonomy across organizations) to future convention.

---

## 6. HOW does OKF work (mechanically)?

1. **Author a concept:** Create a `.md` file. Its file path (minus `.md`) becomes its permanent Concept ID.
2. **Add front matter:** Include a YAML block with structured fields — commonly `type` (required), plus `title`, `description`, `resource`, `tags`, `timestamp`.
3. **Write the body:** Everything else — schemas, tables, explanations — goes in normal Markdown below the front matter.
4. **Link concepts:** Reference other concepts using standard Markdown links (e.g., `[Orders Table](../tables/orders.md)`). This turns the bundle into a navigable graph, not just a folder hierarchy.
5. **Organize into a bundle:** Group concept files into directories however makes sense for the domain (OKF is directory-structure-agnostic).
6. **Add optional navigation aids:**
   - `index.md` for a browsable directory listing (progressive disclosure).
   - `log.md` for a chronological history of changes.
7. **Distribute the bundle:** As a Git repo (preferred, for versioning/attribution), a zip/tarball, or a subdirectory in an existing repo.
8. **Consume the bundle:** Any tool that can read Markdown + YAML can parse it — no special runtime needed. Google published a minimal Python example (using `pathlib`, `re`, and `yaml`) that walks a bundle, loads front matter, and extracts the markdown-link graph.
9. **Tolerance for incompleteness:** Consumers must tolerate broken links — a link to a concept that doesn't exist yet is treated as "not-yet-written knowledge," not as an error.

**Reference implementations shipped by Google:**
- An **enrichment agent** (built on Google's Agent Development Kit + Gemini) that scans a BigQuery dataset, drafts one OKF concept per table/view, then runs a second LLM pass to enrich each concept with authoritative documentation.
- A **static HTML visualizer** that renders any OKF bundle as a self-contained, interactive page.

---

## Comparisons to related concepts (for context)

| Compared to | Key difference |
|---|---|
| **Obsidian** | Obsidian already stores notes as portable local Markdown; the difference is *purpose* — OKF targets machine/agent consumption of organizational knowledge, not personal note-taking. |
| **Notion** | Notion knowledge is platform-trapped (even with markdown export); OKF is filesystem-native from the start. |
| **AGENTS.md** (OpenAI, Aug 2025) | Similar "plain markdown convention" philosophy, but AGENTS.md tells a coding agent how to behave in a repo; OKF describes knowledge/context itself. |
| **llms.txt** | Another emerging "markdown for agents" convention; narrower in scope than OKF's bundle/graph model. |

---

## Open questions / things to verify before adopting in your app system

- [ ] Confirm current spec version (v0.1 as of this writing) — check GitHub for updates before implementation.
- [ ] Decide on a required front-matter field set for your own bundles (spec says only `type` is required, but Google's own parser expects `type`, `title`, `description`, `timestamp`).
- [ ] Determine how your app will handle broken/forward-referenced links (spec says consumers must tolerate them).
- [ ] Evaluate whether your knowledge domain needs a custom `type` taxonomy (OKF deliberately leaves this undefined).
- [ ] If using Google Cloud, check current Knowledge Catalog ingestion behavior/APIs (verify via `docs.claude.com`-style official docs, since this is a fast-moving external product).

---

*Compiled from Google Cloud Blog, Google Cloud Tech, and the `GoogleCloudPlatform/knowledge-catalog` GitHub repository (`okf/SPEC.md`), plus third-party analysis, as of July 2026.*