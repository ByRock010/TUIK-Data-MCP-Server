# ADR 0004: Explicit AI relevance evaluation for project acceptance

Date: 2026-11-04 (Europe/Istanbul)

## Decision

Use a disclosed **single-AI assessment followed by a sequential source review**
for the project's relevance baseline and subsequent comparison. The project
owner requested that Codex perform the review to avoid the manual workload of
the original two-person study. This changes the assessment method for #11/#20;
it does not represent an AI session as a person or two independent assessors.

The original human-study procedure remains available as an optional, stronger
validation path. Its empty fixture and judge-sheet commands remain intact.
Independent human validation has **not** been performed.

## Protocol

1. Freeze the queries, corpora, exact retrieval configurations, rankings and stable
   source identities. Pool the union of every system's top ten candidates.
2. One AI assessor grades each query–document pair 0–2 using the published rubric.
   Record assessor identity/type, scope, limitations, source hashes and per-query
   rationale. Retain this first assessment unchanged.
3. Run the actual MCP server. Replay the queries through the live portal, resolve
   pooled resources, and inspect/download a bounded selection of tables where
   geography or demographic scope is uncertain. Preserve every response/error,
   source URL, retrieval time and snapshot identity. New live search rankings are
   diagnostic evidence; they do not replace frozen rankings in the experiment.
4. The same AI revisits the complete pool in a second pass using the recorded
   evidence. Preserve final grades, query rationales, source-check references and
   every changed grade with its original value and reason. Unavailable/currently
   unresolved sources retain explicitly identified archival evidence; an access
   error alone does not make a historical result irrelevant.
5. `score-ai --require-reviewed` must validate the complete grade pool, original
   assessment hash, finished source-evidence hash, source references and revision
   log before reporting `ai_reviewed`. Publish nDCG@10, MRR@10 and pooled Recall@10
   with category/corpus boundaries and the recorded limitations.

## Interpretation and limits

Both passes come from **one dependent AI assessor** with knowledge of the system
design and earlier judgments. Revisions measure self-correction under additional
evidence, not inter-rater agreement. Cohen's κ and human agreement are **not
applicable** to this method; their report fields remain null with zero human
pairs. They must never be filled using copied AI labels or repeated sessions.

Results support project engineering decisions under the disclosed AI protocol.
They must not be described as independently human-validated research evidence
in a thesis or release claim. A future independent human study can test the AI
grades using initially blank forms, without seeing the AI labels first.

The existing 342-flow OpenSearch pilot still omits the full bulletin/metadata
corpus. Completing the AI baseline does not finish #20, select a winning default
for the full corpus, or release v0.3.0. Full-corpus collection/review, final index
acceptance, normal PR review/CI and publication steps remain required. For that
reason `release_gate_satisfied` stays false in standalone AI reports.

## Implementation evidence — 2026-11-05

The [full-metadata comparison](../verification/issue-20-full-corpus-2026-11-05.md)
now applies this protocol to 3,424 documents and 1,390 pooled pairs. It retains
the original pilot/review files, records all new labels and source-based revisions,
and passes `score-ai --require-reviewed`. The synonym default decision and MRR
tradeoff are documented. This completes the comparison evidence for #20; the
standalone release flag remains false and publication still follows #29/#24.
