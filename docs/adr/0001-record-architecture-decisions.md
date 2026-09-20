# ADR 0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

ValidSim is a fast-moving MVP built by a two-founder team against an 8-week
sprint plan. Architectural choices — the scorecard weighting, the job-queue
backend, the storage abstraction, the licensing split — carry long-term
consequences (compliance, cost, moat, hiring) but are currently explained only
in scattered vault notes and code docstrings. New contributors and future
reviewers cannot easily recover *why* a decision was made, what was rejected,
or whether a choice is still valid. We need a lightweight, version-controlled
record that lives next to the code.

## Decision

We will record significant architectural decisions as **Architecture Decision
Records (ADRs)** in `docs/adr/`, following the Nygard format
(Michael Nygard, "Documenting Architecture Decisions", 2011).

Each ADR is a single Markdown file named `NNNN-<kebab-title>.md` (zero-padded,
append-only sequence) with these sections:

1. **Title** — the decision, as a few words.
2. **Status** — one of `Proposed`, `Accepted`, `Deprecated`, or `Superseded by
   ADR-NNNN`.
3. **Context** — the forces at play: the problem, constraints, and assumptions
   that make a decision necessary.
4. **Decision** — the active voice statement of what we will do, and the key
   parameters chosen.
5. **Consequences** — the resulting trade-offs, both positive and negative, and
   any follow-up work.

Guidelines:

- **One decision per record.** An ADR captures a single, hard-to-reverse choice.
- **Decisions are immutable once accepted.** When a choice changes, write a new
  ADR that `Supersedes` the old one rather than editing history in place.
- **ADRs justify, they don't spec.** Detailed how-to content belongs in
  `docs/*.md` and the vault; an ADR links to those.
- **Low ceremony.** An ADR is added in the same PR that introduces the change it
  documents, reviewed like any other code.

## Consequences

**Positive**

- The rationale behind non-obvious choices (e.g. *why not Celery*) is captured
  at decision time, when it is fresh, instead of being reconstructed later.
- Contributors gain a fast way to understand the system's load-bearing decisions
  and to see which are still open (`Proposed`) versus settled (`Accepted`).
- The append-only, supersede-don't-edit convention preserves an audit trail that
  mirrors the immutable-run ethos the product itself sells
  ([[IP Strategy]], [[Product Principles]] #5).

**Negative / trade-offs**

- A documentation burden: every meaningful decision now warrants a short record,
  and stale ADRs must be explicitly superseded to avoid misleading readers.
- ADRs can drift from the code if not maintained; the continuous-build culture
  ([[README]]) should treat broken/contradicted ADRs like broken tests.

**Follow-up**

- Link this directory from the vault Home and `docs/` index so the engineering
  notes ([[Solution Architecture]], [[Tech Stack]]) cross-reference the records.
- Consider a `template.md` and an `index.md` (MADR-style table of contents) as
  the series grows.

## References

- [[Solution Architecture]] — "Architectural decisions (and their consequences)".
- [[Product Principles]] · [[Tech Stack]] · [[IP Strategy]].
- Michael Nygard, *Documenting Architecture Decisions*,
  https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
