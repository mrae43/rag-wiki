# Docs Convention

This file defines the purpose, audience, ownership, and linking rules for every
documentation file in this project. Read this before creating a new doc file or
deciding where to put information.

---

## The core rule

> **One file owns each piece of information. Everyone else links to it.**

If the same fact appears in two files, one of them is wrong. The fix is always
to remove the copy and add a link to the owner — never to keep both in sync
manually.

---

## File map

### `README.md`
| | |
|---|---|
| **Audience** | A human encountering the project for the first time (reviewer, enterprise buyer, new contributor) |
| **Purpose** | Sell the project. Answer: *what is this, why does it exist, how do I run it* |
| **Tone** | Public-facing, persuasive, accessible without prior context |
| **Owns** | Project tagline and one-paragraph description · Feature list · Architecture diagram (visual overview) · Why self-hosted · Supported LLM providers · Quickstart (clone → configure → run) · Local development (without Docker) · Deployment options · Roadmap · Design influences · Contributing entry point |
| **Does NOT own** | Architecture constraints (AGENTS.md owns) · Domain terminology (CONTEXT.md owns) · Coding conventions (coding-standards.md owns) · Tool/library choices (tech-stack.md owns) · Decision rationale (ADRs own) |
| **Links to** | CONTEXT.md · AGENTS.md · docs/coding-standards.md · docs/adr/ (index only) |

---

### `AGENTS.md`
| | |
|---|---|
| **Audience** | A coding agent (Claude Code, OpenCode, Codex, etc.) actively working inside the repo |
| **Purpose** | Prevent the agent from making decisions that violate locked-in architecture. Answer: *what must I not break, what do I read before touching X, what do I do when stuck* |
| **Tone** | Operational, directive, assumes the agent already knows what the project is |
| **Owns** | Pre-task reading order · Architecture constraints (the "never do X" rules, with ADR refs) · ADR index table · Package layout with full paths · Quality command order and pass criteria · Not-yet-decided items with explicit "stop and ask" instruction |
| **Does NOT own** | Project description (README owns) · Design influences (README owns) · Quickstart (README owns) · Local development setup (README.md owns) · Full coding conventions (coding-standards.md owns) · Full tool detail (tech-stack.md owns) · Decision rationale (ADRs own) · Domain definitions (CONTEXT.md owns) |
| **Links to** | CONTEXT.md · docs/coding-standards.md · docs/tech-stack.md · docs/adr/ (full index inline) · README.md (local development) |

---

### `CONTEXT.md`
| | |
|---|---|
| **Audience** | Anyone — human or agent — who needs to use domain terminology correctly |
| **Purpose** | Define the vocabulary of this project. Terms used in code, ADRs, docs, and conversation must match definitions here exactly |
| **Tone** | Neutral, precise, definitional |
| **Owns** | Every domain term used across the project (Source, Chunk, Entity, Relation, Wiki, Source of Truth, etc.) · What each term does and does NOT include (scope boundary per definition) |
| **Does NOT own** | Decisions (ADRs own) · Conventions (coding-standards.md owns) · Tool choices (tech-stack.md owns) · Project description (README owns) |
| **Links to** | Nothing — it is a leaf node. Other files link to it, it does not link outward |
| **Update rule** | Add a term when it first appears in code or an ADR and isn't already defined here. Never delete a term without checking all references first |

---

### `docs/adr/NNNN-slug.md`
| | |
|---|---|
| **Audience** | Anyone (human or agent) who needs to understand *why* a decision was made before changing something that depends on it |
| **Purpose** | Record a single architectural decision: what was decided, what alternatives were considered, why this option was chosen, and what the consequences are |
| **Tone** | Analytical, permanent (an ADR is never edited once Accepted — it is superseded by a new ADR) |
| **Owns** | The decision itself · Context that motivated it · Alternatives considered · Rationale · Consequences (including explicit migration paths for provisional decisions) |
| **Does NOT own** | Implementation detail (code owns) · Domain definitions (CONTEXT.md owns) · Tool choices below the decision level (tech-stack.md owns) |
| **Links to** | CONTEXT.md terms when used · Related ADRs when a decision depends on or constrains another |
| **Naming** | `NNNN-short-slug.md` where NNNN is zero-padded sequential (e.g. `0011-auth-rbac.md`). Slug uses hyphens, lowercase, describes the decision not the topic |
| **Status values** | `Accepted` · `Superseded by ADR-NNNN` · `Deprecated` · `Proposed` (for decisions under discussion) |
| **Update rule** | Never edit an Accepted ADR's Decision or Rationale sections. To change a decision, write a new ADR with status `Accepted` and mark the old one `Superseded by ADR-NNNN` |

---

### `docs/coding-standards.md`
| | |
|---|---|
| **Audience** | Anyone writing Python code in this repo — human or agent |
| **Purpose** | Define how code must be written: docstrings, error handling, typing, logging, database access, testing, configuration, git hygiene |
| **Tone** | Prescriptive, detailed, with examples |
| **Owns** | Docstring format (module, class, function) · Exception hierarchy rules · Typing conventions · Formatting/linting rules (ruff config) · Async conventions · Logging conventions (structlog, log levels, no secrets) · Database conventions (Alembic, no SELECT *, explicit transactions) · Testing conventions (file structure, naming, FakeLLMProvider, error path coverage) · Configuration conventions (pydantic-settings, .env.example) · Git conventions (commit format, one logical change per commit) |
| **Does NOT own** | Which tools are used (tech-stack.md owns) · Why architectural decisions were made (ADRs own) · Domain terms (CONTEXT.md owns) |
| **Links to** | docs/tech-stack.md for tool version references · docs/adr/ when a convention directly implements an ADR consequence |

---

### `docs/tech-stack.md`
| | |
|---|---|
| **Audience** | Anyone adding a dependency, setting up a new environment, or evaluating the project's technology choices |
| **Purpose** | Record the concrete libraries and tools chosen, tied back to the ADRs that motivated them. Not a justification — a record |
| **Tone** | Matter-of-fact, terse |
| **Owns** | Library names and version pins · Which ADR motivated each tool choice · Optional vs required dependencies · Deployment artifact types (Docker, Helm) · Tools not yet decided |
| **Does NOT own** | Why an architectural decision was made (ADRs own) · How to use a library in code (coding-standards.md or code itself owns) · Project description (README owns) |
| **Links to** | docs/adr/ for the decision that motivated each tool |

---

### `docs/docs-convention.md` ← this file
| | |
|---|---|
| **Audience** | Anyone creating or editing documentation |
| **Purpose** | Define the ownership, audience, and linking rules for every doc file. Prevent duplication. Provide a checklist for where new content belongs |
| **Tone** | Prescriptive |
| **Owns** | The file map · The deduplication rule · The "where does this go" checklist · Naming and status conventions for ADRs |
| **Links to** | All doc files (as the index of the documentation system itself) |

---

## Where does this go? — decision checklist

When you have new information to document, work through this in order:

1. **Is it a new domain term?** → `CONTEXT.md`
2. **Is it an architectural decision with trade-offs and rationale?** → new ADR in `docs/adr/`
3. **Is it a rule about how to write code?** → `docs/coding-standards.md`
4. **Is it a library/tool choice (not a decision, just a record)?** → `docs/tech-stack.md`
5. **Is it for a human discovering the project (feature, quickstart, why)?** → `README.md`
6. **Is it an operational rule for an agent mid-task?** → `AGENTS.md`
7. **Is it a rule about where documentation goes?** → this file

If it fits more than one, pick the file whose audience is most likely to need it,
and add a link from the other file.

---

## Linking rules

- **Always link by relative path**, not absolute URL — the repo may be hosted anywhere.
- **Link to the specific file**, not the folder, unless you mean "browse this folder".
- **One-way ownership**: if file A owns a piece of information and file B needs to
  reference it, B links to A. Never copy the content into B.
- **CONTEXT.md is a leaf node** — it links to nothing. All other files may link to it.
- **ADRs link to each other** when a decision depends on another, using the ADR number
  (e.g. `ADR-0005`), not the filename, so references survive renames.

---

## What to do when a doc needs updating

| Situation | Action |
|---|---|
| A new domain term appears in code or an ADR | Add to `CONTEXT.md` |
| An architectural decision changes | Write a new ADR; mark the old one `Superseded by ADR-NNNN` |
| A coding convention changes | Edit `docs/coding-standards.md`; check if any inline examples in `AGENTS.md` need updating |
| A new library is added | Add to `docs/tech-stack.md` with the ADR or reason |
| A feature is shipped | Update the roadmap table in `README.md` (✅ Done) |
| A not-yet-decided item gets decided | New ADR → update `AGENTS.md` constraints → update `docs/tech-stack.md` if a tool is involved → remove from "Not yet decided" sections |
| A new doc file is needed | Add it to the file map in this file first; if it doesn't fit the checklist above, question whether it's needed |