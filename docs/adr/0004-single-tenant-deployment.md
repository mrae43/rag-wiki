# ADR-0004: Single-tenant, per-customer deployment model

## Status
Accepted

## Context
The project is intended both as a portfolio piece and as a potentially sellable
system for enterprise customers. "Enterprise" could mean several different things
architecturally:

1. **Multi-tenant SaaS** — one deployment serves many customer organizations, with
   tenant isolation (RLS, per-tenant schemas, or per-tenant databases). Requires
   building tenant management, isolation guarantees, and billing as core features.
2. **Single-tenant, deployable per customer** — each customer runs their own
   instance (e.g. Docker Compose / Helm chart) on their own infrastructure or
   cloud account. "Enterprise-grade" refers to the quality bar (auth, RBAC, audit
   logging, observability, security, documentation), not to multi-tenancy.
3. **Quality bar only** — same single-org tool, built to a higher engineering
   standard, without committing to any specific deployment/sales model.

## Decision
Adopt **single-tenant, deployable per customer** (option 2). Each deployment
serves one organization. No multi-tenant data isolation is built in.

## Rationale
- **Aligns with the system's nature**: a knowledge wiki built from an
  organization's internal documents is sensitive data. Many enterprise buyers of
  this type of tool prefer it run on their own infrastructure/cloud account for
  data sovereignty — single-tenant-per-deployment is a legitimate and common
  sales model (not a downgrade from "real enterprise").
- **Preserves ADR-0001's scale assumptions**: per-deployment data volumes remain
  in the range ADR-0001 was designed for (relational graph tables, recursive
  CTEs). Multi-tenant SaaS would require revisiting that decision (tenant-scoped
  indexes, RLS overhead, much larger aggregate data volumes).
- **Avoids a large, separate engineering effort**: tenant isolation, cross-tenant
  security guarantees, and SaaS billing/ops are substantial scope additions that
  would distract from the core RAG-Anything + LLM Wiki synthesis that is the
  point of this project.
- **Still demands real "enterprise" features**: auth, RBAC, audit logging,
  config-driven secrets/LLM provider settings, and clean Docker/Helm-based
  deployment remain in scope — these are what "enterprise-grade" actually buys,
  independent of tenancy model.

## Consequences
- No `tenant_id` columns or RLS policies are needed in the schema.
- Auth/RBAC (next ADR) is scoped to "users within one organization's deployment",
  not cross-tenant access control.
- If multi-tenant SaaS becomes a goal later, it would likely require a new ADR
  revisiting the schema (adding tenant scoping) — deliberately deferred.
- Deployment artifacts (Docker Compose for local/dev, Helm chart for production)
  become a real deliverable, not an afterthought, since "give the customer
  something they can run" is the actual product.
