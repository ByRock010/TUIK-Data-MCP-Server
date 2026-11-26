# Sector and artifact benchmark protocol

Frozen 2026-11-23 for [#85](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/85).
The [task matrix](tasks.json) defines 24 Turkish prompts, six per persona.
Each rubric records expected content, unit, period and the official source.
Archive keys refer to the independently assembled ground truth in the
[8 November evidence](../mcp-benchmark-2026-11-08.json). Cutoff-labelled
questions retain that vintage. The other references are official releases
available by 23 November. Keep observed data and release dates distinct.

## Scoring and budgets

An answer passes only if each required value, sign, unit, period, geography,
revision model and official citation agrees. Derived results must show their
inputs and operation. For a real artifact, inspect that the file opens, the
data agree, requested elements remain editable, and the layout checks pass.
Mark numeric/source divergence and presenting unsupported results as critical
errors. Treat missing details, timeouts and unavailable files on supported
tasks as failures. For unsupported tasks, score explicit restraint and a
correctly labelled alternative separately.

The four frozen held-out prompts include one supported workflow (L06) and
three unsupported requests (F06, M06 and A06) that test refusal and fallback
behavior. Use only the 20 development tasks for tuning. Report held-out results
separately and interpret them primarily as a check of how the model handles
unsupported requests. The IDs and answers are public for reproducibility. This
is a fixed no-tuning holdout, with no claim of secrecy or independence after
publication.

For a candidate model run, use one fresh session per assigned task and condition,
one in-flight MCP session, 120 seconds and USD 0.75 per run. Each task gets one
attempt. Record timeout, budget stop, refusal and any provider error. Compare
model rankings only within a fixed client build, model/provider and corpus
snapshot. Cap a 24-task × 3-condition run at USD 54 per client. Stop a run if a
critical numeric/source error is confirmed, and publish how many tasks were
unrun. A separately reviewed rerun uses a new run ID and reports both attempts.

Pre-registered gates are zero critical numeric or source errors, at least
90% success among the 20 supported tasks (at least 18/20), and all four
unsupported requests disclosed (4/4). For each condition, show the supported
success count overall (out of 20), in development (out of 19) and in the
holdout (out of 1). Apply the 18/20 gate to the combined supported count.
Report unsupported disclosures separately (out of 1 in development and out of
3 in the holdout). An incomplete run cannot pass. The denominators stay fixed
even if runs time out. Report two-sided 95% Wilson intervals per
supported/unsupported scope, condition and split. Treat the intervals as
descriptive because several tasks share sources.
Keep source correctness, model behavior, client setup time and artifact
usability as separate observations.

For the reproducible capability baseline, discover the installed server's
nine tools with a cold stdio session and run its existing scenario/verifier
checks. Match each matrix route to actual tools and known source snapshots.
Check PNG/CSV artifact storage, numeric values and resource delivery with the
existing #88 test cases. Record each task's expected support from the frozen
matrix alongside the discovered tools and F05 probe result. Model task success,
setup time and visual usability require their own run.

## Runner and review

Export only ID and prompt text from the development or held-out split to the
existing `scripts/mcp_benchmark.py` runner. Preserve the current condition
configs, event streams and summary, with an empty per-run work directory.
Pass the frozen date and server executable. For a scored run, extend
`run_one` to pass Claude Code's `--max-budget-usd` and use its existing per-run
timeout. Refuse conditions whose permissions or budget enforcement cannot
be verified. Add client, provider/model ID, server SHA, fixture and search-index
hash, cold/warm state, permissions, and artifact IDs/hashes as sidecar fields.
Keep rubrics out of model-visible inputs. Use precise numeric checks and source
locators to assist grading. Treat model-assisted judgments as provisional.

Recruit at least one external reviewer per persona: finance analyst,
manufacturing/export analyst, municipal data analyst, and researcher or
journalist. Ask each to check their six tasks, report setup friction, and
blind-review anonymized answers and artifact layouts before seeing automated
scores. A second reviewer adjudicates source/number disagreements. Record
declines and missing roles. Review slots are currently unfilled (0/4);
the source and repository checks are not independent sector-user review.
