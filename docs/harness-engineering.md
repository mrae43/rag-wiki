# How to Design an AI Agent Harness
> A practical step-by-step blueprint for building reliable AI agent systems

---

## What is an Agent Harness?

An **agent harness** is the system around an AI agent that defines its job, instructions, workflow, tools, memory, checks, and safety rules. It turns a raw model into a reliable working agent.

> **Core idea:** Good harnesses reduce confusion, control tool use, and make agent work repeatable.

---

## Design Principles (Summary)

| Principle | Description |
|---|---|
| **Clear job** | Define one clear task and measurable outcome |
| **Controlled tools** | Give the right tools with clear rules and limits |
| **Focused context** | Use only relevant information and remove the rest |
| **Strong checks** | Evaluate results and catch problems before delivery |
| **Slow improvement** | Make small changes and learn from real execution |

---

## The 16-Step Blueprint

---

### Step 1 — Define the Job

- Start with one clear task.
- Do not begin with a general agent.
- Write exactly what the agent must do.

**Goal:** Make the job narrow enough to test.

**Evaluation checklist:**
- [ ] Is there exactly one primary task defined?
- [ ] Is the task specific enough to write a test case for?
- [ ] Does the definition avoid vague or multi-purpose scope?

---

### Step 2 — Choose the Model

- Pick the model that matches the work.
- Use stronger models for reasoning, coding, and planning.
- Use cheaper models for simple extraction or formatting.

**Goal:** Do not waste a powerful model on small work.

**Evaluation checklist:**
- [ ] Is the model tier justified by the task complexity?
- [ ] Has a cost/capability trade-off been considered?
- [ ] Is the model selection documented and revisitable?

---

### Step 3 — Write System Instructions

- Define role, boundaries, and expected behavior.
- Tell the agent what to do and what to avoid.
- Add stop conditions and help-seeking rules.

**Goal:** Give the agent a rulebook before tools.

**Evaluation checklist:**
- [ ] Is the agent's role explicitly stated?
- [ ] Are prohibited actions or out-of-scope topics defined?
- [ ] Are stop conditions and escalation paths specified?
- [ ] Are help-seeking triggers defined (e.g., when to ask the user)?

---

### Step 4 — Design the Workflow

- Break the task into stages.
- Example flow: receive request → plan → act → check → return.
- A harness should guide the path, not just the outcome.

**Goal:** Turn messy work into a repeatable flow.

**Evaluation checklist:**
- [ ] Are the workflow stages explicitly defined?
- [ ] Is there a defined entry point and exit condition?
- [ ] Does the flow enforce ordering where required?
- [ ] Is the workflow documented separately from system instructions?

---

### Step 5 — Add Tools

- Give only the tools needed.
- Examples: search, browser, database, code, email, APIs.
- Too many tools create confusion.

**Goal:** Keep tool access tight and useful.

**Evaluation checklist:**
- [ ] Is each tool justified by a specific workflow step?
- [ ] Are unnecessary or redundant tools removed?
- [ ] Is the total number of tools minimal?

---

### Step 6 — Add Memory

- Use **short-term memory** for the current task.
- Use **long-term memory** for stable facts and preferences.
- Use **retrieval memory** for documents and knowledge bases.

**Goal:** Help the agent continue work without overload.

**Evaluation checklist:**
- [ ] Is the appropriate memory type used for each data category?
- [ ] Is long-term memory scoped to genuinely stable data?
- [ ] Is retrieval memory used instead of stuffing docs into context?

---

### Step 7 — Control Context

- Select only the most relevant information.
- Rank context by importance.
- Remove weak or outdated material.

**Goal:** Keep the model focused.

**Evaluation checklist:**
- [ ] Is context filtered before being passed to the model?
- [ ] Is there a defined priority/ranking strategy for context selection?
- [ ] Is stale or irrelevant context actively pruned?

---

### Step 8 — Add Planning

- Let the agent make a small plan before acting.
- Plan should include: steps, tool choices, expected result, and finish condition.

**Goal:** Prevent random tool use.

**Evaluation checklist:**
- [ ] Does the agent produce a plan before taking actions?
- [ ] Does the plan include expected output and a termination condition?
- [ ] Is the plan visible/logged for debugging?

---

### Step 9 — Set Tool Rules

- Define when each tool should be used.
- Define input format, output expectations, and failure handling.

**Goal:** Tools should feel controlled, not random.

**Evaluation checklist:**
- [ ] Does each tool have a defined trigger condition?
- [ ] Are input schemas documented for each tool?
- [ ] Is failure/fallback behavior defined per tool?

---

### Step 10 — Add Evaluation

- Check the result before returning it.
- Review accuracy, completeness, format, safety, and user fit.

**Goal:** The agent should not trust its first answer.

**Evaluation checklist:**
- [ ] Is there a self-check step after task completion?
- [ ] Does evaluation cover accuracy, format, and safety?
- [ ] Is the evaluation step part of the workflow (not optional)?

---

### Step 11 — Handle Errors

- Plan for weak sources, failed tools, broken code, or unclear tasks.
- Tell the agent how to recover and retry.

**Goal:** Recover instead of stopping blindly.

**Evaluation checklist:**
- [ ] Are common failure modes enumerated and handled?
- [ ] Is there a retry strategy with limits?
- [ ] Does the agent report errors rather than silently failing?

---

### Step 12 — Require Human Approval

- Keep risky actions behind approval.
- Examples of risky actions: sending emails, making payments, deleting files, posting publicly.

**Goal:** Agent prepares work; humans approve sensitive actions.

**Evaluation checklist:**
- [ ] Is a list of "sensitive" or "irreversible" actions defined?
- [ ] Are those actions gated by human confirmation?
- [ ] Is there a clear UI or notification path for approval requests?

---

### Step 13 — Add Logs

- Save the request, plan, tools used, decisions, errors, and final output.
- Logs make debugging and improvement easier.

**Goal:** Make the harness observable.

**Evaluation checklist:**
- [ ] Are all major decision points logged?
- [ ] Are tool calls and their outputs captured?
- [ ] Are errors and retries recorded with context?
- [ ] Are logs accessible and structured for review?

---

### Step 14 — Test with Real Tasks

- Test messy inputs, unclear requests, long context, missing files, and tool failures.
- Do not test only perfect examples.

**Goal:** See how the harness behaves in real work.

**Evaluation checklist:**
- [ ] Has the system been tested with malformed or ambiguous inputs?
- [ ] Have edge cases (missing files, tool timeouts) been tested?
- [ ] Is there a regression test suite covering known failure modes?

---

### Step 15 — Improve the Harness

- Tune one part at a time.
- Fix instructions, tool choices, memory, context, checks, and output format.

**Goal:** Improve slowly instead of rebuilding everything.

**Evaluation checklist:**
- [ ] Are changes made incrementally, one component at a time?
- [ ] Is each change tracked against a measurable outcome?
- [ ] Is there a changelog or versioning system for the harness?

---

## Master Evaluation Scorecard

Use this to assess an existing harness end-to-end.

| # | Component | Key Question | Pass? |
|---|---|---|---|
| 1 | Job Definition | Is there one clear, testable task? | |
| 2 | Model Selection | Is the model tier appropriate for the task? | |
| 3 | System Instructions | Are role, scope, stop conditions, and help-seeking defined? | |
| 4 | Workflow Design | Is the flow staged, documented, and repeatable? | |
| 5 | Tools | Are tools minimal, justified, and scoped? | |
| 6 | Memory | Are the right memory types used for each data category? | |
| 7 | Context Control | Is context filtered, ranked, and pruned? | |
| 8 | Planning | Does the agent plan before acting? | |
| 9 | Tool Rules | Does each tool have triggers, schemas, and failure handling? | |
| 10 | Evaluation | Does the agent check its own output before returning? | |
| 11 | Error Handling | Are failures handled with recovery, not silent stops? | |
| 12 | Human Approval | Are risky/irreversible actions gated by a human? | |
| 13 | Logging | Are decisions, tools, errors, and outputs logged? | |
| 14 | Real-world Testing | Has the system been tested on messy, imperfect inputs? | |
| 15 | Iteration Process | Are improvements made incrementally with tracking? | |

---

*Source: "How to Design an AI Agent Harness" infographic — transcribed and expanded for use as a system evaluation reference.*