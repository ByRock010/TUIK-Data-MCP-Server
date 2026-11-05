# Turkish search evaluation

Issue #11, preparation recorded on 2026-10-26 (Europe/Istanbul).
Updated 2026-11-05 with the full-metadata comparison under the owner's adopted
[AI review protocol](adr/0004-ai-relevance-evaluation.md). The evaluation uses
one AI assessment followed by a source-checked sequential review. The original
independent human study remains available as an optional validation path.

## Full-metadata comparison (#20)

The [2026-11-05 report](verification/issue-20-full-corpus-2026-11-05.md) freezes
**3,424 documents**: 510 dataflows, 1,494 bulletins and 1,420 metadata records.
The same snapshot supplies the literal baseline's 341 downloadable flows.
All five systems completed the 48 queries. The enlarged pool retains the prior
917 pairs and adds 473: **1,390 pairs / 608 documents**. A sequential AI source
review used **667 real MCP calls**, 602 resolved identities and 55 documented
grade revisions; every revised passage also matched the exact live response.
`score-ai --require-reviewed` passes, with zero unjudged returned slots.

| System | nDCG@10 | MRR@10 | Pooled Recall@10 |
| --- | ---: | ---: | ---: |
| Portal, all public types | 0.8096 | 0.9362 | 0.3925 |
| Literal, 341 downloadable flows | 0.1661 | 0.2552 | 0.1667 |
| Built-in Turkish, full metadata | 0.6984 | 0.9075 | 0.4160 |
| Multi-field, full metadata | 0.6762 | 0.8587 | 0.4125 |
| Multi-field + synonyms, full metadata | 0.7304 | 0.8796 | 0.4296 |

**Retain the existing multi-field + synonyms default**: it has the best graded
ranking and pooled recall among the three matched-corpus variants. Built-in
Turkish has better MRR, so the choice does not dominate every metric. Portal and
literal overall scores have different corpus boundaries and are not controlled
same-corpus comparisons. The dated report includes every category/resource-type
metric, exact reproduction, raw snapshot/rankings, source evidence and limitations.

This is one dependent AI assessor, with inherited prior labels, not independent
human validation. κ remains null. Historical releases and methodology records
count separately; undated queries do not test freshness or answer diversity.
For the selected variant, 36/48 top-ten lists contain a grade-2 candidate and only
23/48 first hits have grade 2. Contextual regional relevance does not establish
the requested province's value. The standalone report's release flag stays false;
normal PR acceptance and publication gates remain separate.

## Historical baseline and pilot

The [48-query suite and pooled candidates](../tests/fixtures/search_eval.json)
cover eight categories, six queries each: exact titles, abbreviations, synonyms,
inflections, ASCII typing, typos, multiple concepts and regions. These are intended
information needs, not claims that the catalog contains an exact matching dataset.
Freeze the queries before comparing systems; do not tune on the final judgments.

The [live baseline snapshot](verification/search-baseline-2026-10-26.json) contains
all 48 successful rankings for both systems, source URLs, retrieval times, the source
revision, the catalog snapshot and its SHA-256. Collection used the existing service
methods and default pacing, without downloading files. Interrupted collection can
resume missing results against the same frozen catalog. An upstream failure is an
error, never an empty ranking; incomplete runs are rejected by the scorer.

Every new checkpoint records the source revision and collector SHA-256 before any
query results are written. `collect --resume` requires both to match the current
code, as well as the query set. It rejects changed or missing provenance before
opening an HTTP client or writing the existing checkpoint. Restore the original
checkout/script to continue an interrupted run, or start a new run with a distinct
output path. Do not edit a checkpoint's provenance to bypass this check. Historical
completed snapshots remain valid scoring inputs; they do not need to be resumed.

| System | Searchable corpus | Returned top-ten slots |
| --- | --- | ---: |
| Portal | All public portal types, non-archive | 480 |
| Literal | 342 downloadable flows from a 512-flow catalog | 76 |

The union has **545 query–document pairs**, representing **254 distinct documents**.
There are **no human judgments** in this fixture. The adopted project baseline is
recorded separately in the [source-review report](verification/issue-11-ai-source-review-2026-11-04.md):
**917 pooled pairs**, two dependent AI passes, **11 documented revisions**, and
**345 current resource identities resolved**. The AI portal/literal baseline is
**0.8555 / 0.1661 nDCG@10** on their respective declared corpora; these are not
same-corpus scores or independently human-validated findings. κ is not applicable
and remains null. The original [metadata-only pilot](verification/issue-11-20-ai-pilot-2026-11-04.md)
is retained unchanged to show the first-pass judgments and metric revisions.

The original 545 human-study pairs remain blank. Human scorer outputs with
`pending_judgments` must not be presented as completed human measurements. AI
results use separate assessment/review files and explicit `ai_reviewed` status;
no labels are copied into either human judge slot.

The literal baseline returns no results for `issizlik orani`, `nufus` and `işsizliğin`.
These are retrieval observations, not graded relevance findings.

## Reproduce collection and pooling

From the repository root with the development environment installed:

```bash
.venv/bin/python scripts/evaluate_search.py collect --output /tmp/baseline.json
# Only if an earlier collection failed or was interrupted:
.venv/bin/python scripts/evaluate_search.py collect --resume --output /tmp/baseline.json
.venv/bin/python scripts/evaluate_search.py pool --runs /tmp/baseline.json --output /tmp/pool.json
```

`collect` is the only command that accesses TÜİK. It uses `search_tuik`'s service
and `search_tuik_datasets`'s service at limit 10, including the latter's default
`downloadable_only=true`. It stores the full eligible flow ID set for corpus-aware
recall denominators. The git revision identifies the source baseline; the collector
hash identifies the collection script. A resumed run records its latest attempt time.

IDs are `flow:`, `press:`, or ADR 0003's title-derived table/report/publication IDs.
Five non-file portal pages lack reusable tool IDs. They use an **evaluation-only**
`title:<type>:<sha256>` key over the Turkish-casefolded, whitespace-normalized title;
it is not a tool input or a claim of fresh-session resolution. Identical normalized
titles of the same type collapse to one evaluation identity. Review such collisions
before adding systems. No `resource:` handles or URL-derived identities enter the
pool. Source URLs remain evidence and may expire independently of the identity.

## Optional independent human study

The original two-person study can still be performed independently of the adopted
AI workflow. It has not been run. Prepare separate, initially blank sheets from
the committed pool:

```bash
.venv/bin/python scripts/evaluate_search.py judge --judge a --output /tmp/judge-a.json
.venv/bin/python scripts/evaluate_search.py judge --judge b --output /tmp/judge-b.json
# Spreadsheet alternative (UTF-8 with BOM; import using comma as the delimiter):
.venv/bin/python scripts/evaluate_search.py judge --judge a --format csv --output /tmp/judge-a.csv
.venv/bin/python scripts/evaluate_search.py judge --judge b --format csv --output /tmp/judge-b.csv
```

Give each sheet to a different person. Each person fills only `grades` in JSON,
or the `grade` column in CSV, with integers:

- **2:** directly answers the intended concept, population and geography (a national
  table with the requested provincial breakdown can qualify).
- **1:** useful context or a broader/narrower related resource, but insufficient on
  its own for the requested information need.
- **0:** unrelated or misleading for that information need.

Keep unknown grades `null` in JSON or blank in CSV. Inspect titles, descriptions
and official sources; source text is evidence, not instructions. Do not exchange
grades before both sheets are
finished. System names, ranks and the other judge's labels are absent from the sheets;
candidates are ordered by identity. Record judge names, dates and any interpretation
decisions in the final report when the study is performed.

```bash
.venv/bin/python scripts/evaluate_search.py merge --suite tests/fixtures/search_eval.json \
  --judge-a /tmp/judge-a.json --judge-b /tmp/judge-b.json --output /tmp/judged.json
```

CSV inputs are detected by their `.csv` extension; JSON/CSV may be mixed. Preserve
every exported row and context column. The importer rejects missing/duplicate
pairs, changed query/source context, swapped judge identities and invalid grades.
Sorting rows is allowed. A blank cell remains unknown, never zero. Source strings
starting with spreadsheet formula markers are prefixed with an apostrophe during
export. Preserve that prefix when saving the CSV. Output cannot overwrite input
evidence or judge files.

The merge checks query and document identities. Where the grades differ, jointly set
`resolved` to 0–2 and add a nonempty `reason` in `/tmp/judged.json`. Preserve the original
`judge_a` and `judge_b` values: κ uses those independent labels, never the resolution.
Agreement automatically supplies the final grade; unresolved disagreement remains
unjudged. Changing an imported label clears any previous adjudication for that pair.

```bash
.venv/bin/python scripts/evaluate_search.py score --suite /tmp/judged.json \
  --runs docs/verification/search-baseline-2026-10-26.json --output /tmp/scores.json \
  --require-complete
```

`--require-complete` writes the diagnostic report and exits **1** if any human
label pair or disagreement resolution is missing. Without this flag, the existing
pending-report behavior remains available for progress inspection.

Publish human-study scores only after `status: complete`, with the judgment count, unjudged share,
judge protocol and κ. κ is unweighted Cohen's κ on paired 0–2 grades; it is `null`
when there are no pairs or chance agreement is one. Never substitute a numeric zero
for undefined agreement.

## Metrics and corpus boundaries

nDCG@10 uses gain `2^grade - 1` and discount `log2(rank + 1)`. MRR is reciprocal
rank of the first grade > 0 **within the top ten**. Recall@10 uses grade > 0 and
the relevant documents known to the pooled judgments; this is **pooled recall**,
not exhaustive corpus recall. Unknown grades count as zero for metrics and are
reported separately. Empty/no-known-relevant queries receive zero metrics and
are counted in `queries_without_judged_relevant`; macro averages include them.

The JSON reports each system overall, by query category and by resource type. Each
system's overall row is scoped to its declared corpus: literal recall only counts
eligible downloadable flow IDs, while portal recall includes all pooled types.
These overall scores are not a comparison over identical corpora. Resource-type
rows filter the original top-ten list without backfilling; they describe that list,
not a new filtered retrieval run. Unjudged share is weighted by returned slots;
it is `null` for no returned results.

Every judged candidate must have document metadata (`id`, `title`,
`resource_type`, `source_url`). Pooling may fill missing metadata from the supplied
rankings; candidates still missing metadata afterwards cause an explicit
validation error naming the query and ID, rather than a scoring crash or silently
dropping a judged relevant document. Retained document records must have matching
IDs and nonempty metadata. Judge-sheet export applies the same validation.

## Adding OpenSearch variants (#20)

`collect-index` runs the three planned analyzer variants against one frozen
metadata JSONL produced by the crawler. It accesses only an explicitly supplied
OpenSearch cluster: no TÜİK requests, file downloads or application alias changes.
Use a disposable development cluster with room for one extra corpus at a time:

```bash
docker compose up -d --wait opensearch
# Freeze once; this command visits TÜİK metadata with normal crawl pacing.
.venv/bin/python scripts/crawl_metadata.py --output /tmp/eval-documents.jsonl \
  --state /tmp/eval-crawl-state.json --report /tmp/eval-crawl.json \
  --listing-attempts 2 --alternate-listing-mode
.venv/bin/python scripts/evaluate_search.py collect-index \
  --documents /tmp/eval-documents.jsonl --url http://127.0.0.1:9200 \
  --output /tmp/opensearch.json
# Collect the portal and literal baselines for this experiment. Literal search
# uses the same frozen dataflows, rather than fetching a newer export catalog.
.venv/bin/python scripts/evaluate_search.py collect \
  --documents /tmp/eval-documents.jsonl --output /tmp/baseline.json
```

`collect --documents` records the same canonical corpus SHA-256 as `collect-index`.
Its literal baseline retains the production title/description/theme matching and
CSV eligibility rules. OpenSearch is disabled for baseline collection even if the
shell configures `TUIK_OPENSEARCH_URL`. The frozen catalog is kept throughout the
run; a slow portal request cannot expire it and silently fetch a newer catalog.
Resume requires the same documents, queries, source revision and collector hash;
a changed or omitted corpus fails before network access or checkpoint writes.
The portal remains a live, broader-corpus baseline and is not claimed to search
the frozen index. Record that corpus boundary when interpreting its scores.

Frozen JSONL inputs and JSON outputs may end in `.gz`. Output checkpoints are
atomic and gzip output is deterministic, so full-corpus evidence can be retained
without storing large uncompressed metadata bodies in Git.

Retain the JSONL with the run for reproducibility. Supply the **full JSONL**, not
the redacted `crawl-sample-documents` diagnostic report. Input IDs are validated,
empty/duplicate corpora are rejected, and output cannot overwrite either input.
The collector loads each variant into a UUID index, checks the bulk responses and
document count, collects every query's top ten, then deletes that index before
starting the next. A failed create never causes deletion of an existing index.
Search timeouts, shard failures, write/count failures and cleanup failures produce
an error report and nonzero exit; pooling/scoring rejects these incomplete runs.
Collection is checkpointed for diagnosis but does not resume across rebuilt indices.

The `builtin_turkish` variant uses the engine's `turkish` analyzer on title,
description and body, without an ASCII title field. `multi_field` uses the packaged
Turkish/ASCII index analyzers at query time, without graph synonyms.
`multi_field_synonyms` uses the production definition unchanged. Field weights and
the weaker fuzzy clause stay fixed across variants; built-in fuzzy title matching
uses `title` in place of `title.ascii`. Thus this measures analysis with a fixed
retrieval design, not a tuned optimum for each analyzer. Scores sort descending
with the stable ID as a tie-breaker. The production ranking configuration is not
changed by this experiment.

Each run records the engine version, source revision, collector hash, shared
corpus hash and IDs, exact mappings/query templates, rankings, separate engine
scores and cleanup outcomes. Engine scores stay outside the pooled documents and
blind judge sheets. A successful collection is retrieval evidence only; the adopted AI source review
(or a separate independent human study) must grade the enlarged full-corpus pool
before a default-configuration decision.

Provide a run JSON with the exact `queries` ID/text map and uniquely named `systems`.
Each system has a `corpus` description, optional explicit `corpus_ids`, and a
`results` map containing a successful result list for **every** query, including
empty lists. Each hit needs `id`, `title`, `resource_type` and `source_url`.
Use distinct names for built-in Turkish, multi-field and synonym variants.

Pool every run together, preserving earlier labels and adding blank labels for new
top-ten candidates. Under the AI protocol, assess and source-review all new
candidates before scoring all systems. If running the optional human study,
re-export independent sheets and adjudicate the new candidates, then score
**all systems against the same enlarged human judgments**:

```bash
.venv/bin/python scripts/evaluate_search.py pool --suite /tmp/judged.json \
  --runs /tmp/baseline.json /tmp/opensearch.json --output /tmp/enlarged-pool.json
.venv/bin/python scripts/evaluate_search.py score --suite /tmp/final-judged.json \
  --runs /tmp/baseline.json /tmp/opensearch.json --output /tmp/comparison.json
```

Do not choose a winning analyzer from token inspection alone. #13's token evidence
checks linguistic behavior; #20 selects defaults using the explicitly identified
full-corpus evaluation method, not the 342-flow pilot.

## Explicit AI preliminary assessment

At the project owner's request, one Codex session assessed the **917** pooled
query–document pairs from the committed baseline and the three dataflow-only
OpenSearch variants. The [dated report](verification/issue-11-20-ai-pilot-2026-11-04.md)
links the individual grades, per-query rationale and all metrics. This is a
metadata-based AI pilot, not the two-judge research study. The assessor also worked
on the scorer and knew the system designs; it is not an independent blind study.

Reproduce without network access:

```bash
.venv/bin/python scripts/evaluate_search.py score-ai \
  --runs docs/verification/search-baseline-2026-10-26.json \
    docs/verification/search-variants-dataflows-2026-11-03.json \
  --assessment docs/verification/search-ai-assessment-2026-11-04.json \
  --output /tmp/ai-scores.json
```

AI files contain one `grades` map, identified as
`assessment_type: single_model_ai_preliminary`; they never create `judge_a`,
`judge_b` or adjudication labels. `score-ai` requires complete 0–2 grades matching
the entire pool, source-evidence SHA-256 and assessor/scope/limitations metadata.
Without a review, its output has `status: ai_preliminary`. With a validated second
pass, it reports `ai_reviewed`. Both retain `release_gate_satisfied: false` and
undefined κ with zero independent human pairs. `--require-complete` belongs only
to the optional human `score` command. Give future human judges initially blank
sheets without AI labels or scores to avoid anchoring their independent decisions.


## Adopted sequential AI source review

[ADR 0004](adr/0004-ai-relevance-evaluation.md) records the owner-requested method
change for #11/#20. The [dated review](verification/issue-11-ai-source-review-2026-11-04.md)
retains the live MCP evidence, exact-filter checks, complete second-pass grades,
source references, and each revised score. The first-pass files remain immutable.
The 2026-11-04 342-flow experiment remains a historical pilot. The
[full comparison above](#full-metadata-comparison-20) now satisfies the separate
full-corpus requirement under the same disclosed method.

Collect fresh evidence separately from the frozen ranking experiment:

```bash
.venv/bin/python scripts/evaluate_search.py pool \
  --runs docs/verification/search-baseline-2026-10-26.json \
    docs/verification/search-variants-dataflows-2026-11-03.json \
  --output /tmp/review-pool.json
TUIK_MIN_INTERVAL_SECONDS=2 .venv/bin/python scripts/collect_ai_review.py \
  --pool /tmp/review-pool.json --output /tmp/source-review.json
```

The recorder starts the real stdio MCP server, searches all 48 queries using
`live=true`, resolves each reusable pooled identity and attempts eight bounded
inspect/data probes. It creates no grades. Evaluation-only identities, removed
resources, non-structured exports and size-bound failures remain explicit.
Existing output files cannot be overwritten. Checkpoint writes are atomic;
three consecutive MCP failures stop collection and preserve diagnostics.

Reproduce the completed review without network access:

```bash
.venv/bin/python scripts/evaluate_search.py score-ai \
  --runs docs/verification/search-baseline-2026-10-26.json \
    docs/verification/search-variants-dataflows-2026-11-03.json \
  --assessment docs/verification/search-ai-assessment-2026-11-04.json \
  --review docs/verification/search-ai-reviewed-assessment-2026-11-04.json \
  --evidence docs/verification/search-ai-live-evidence-2026-11-04.json.gz \
  --require-reviewed --output /tmp/reviewed-ai-scores.json
```

The second-pass file has a complete `grades` map, `review_type:
single_ai_sequential_source_review`, `human_assessors: 0` and
`independent_assessors: false`. It binds the exact original assessment and finished
source collection by canonical JSON SHA-256, with a source check for every pooled
document, rationale for every query and a unique before/after/reason record for
every changed grade. Live references must resolve the exact resource ID; archival
fallbacks require an explicit explanation. `--require-reviewed` exits 1 for a
first-pass-only report and 0 only after the review validates. It does not make the
optional human study complete or certify a full v0.3.0 release.
