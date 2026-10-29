# Contributing

This project uses topic branches into `develop`, then `develop` into `master`.
Nobody commits directly to `master` or `develop`;
every change arrives through a pull request that passes CI and gets one review.

## Branches

| Branch | Purpose | Created from | Merges into |
| --- | --- | --- | --- |
| `master` | Released code, promoted only from `develop` | — | — |
| `develop` | Integration branch; the next release in progress | `master` initially | `master` |
| `feature/<issue>-<slug>` | New tools or capabilities | `develop` | `develop` |
| `fix/<issue>-<slug>`, `bugfix/<issue>-<slug>`, `hotfix/<issue>-<slug>` | Bug fixes, including urgent fixes | `develop` | `develop` |
| `docs/<slug>`, `chore/<slug>`, `refactor/<slug>` | Docs, tooling, cleanup | `develop` | `develop` |

Examples: `feature/12-xlsx-ingestion`, `fix/18-csv-semicolon-delimiter`,
`chore/prepare-0.2.0`, `hotfix/18-parser-crash`.

The required `Branch policy` check accepts topic prefixes `feature`, `fix`,
`bugfix`, `hotfix`, `docs`, `chore`, `refactor`, `test`, `perf`, `build`, `ci`
and `revert`, followed by `/` and a nonempty slug without spaces or nested `/`.
Issue numbers are encouraged but not mandatory. Fork topic PRs into `develop`
are allowed; a fork branch named `develop` cannot promote into `master`.
Git does not record where a branch was originally created: create it from
`develop` as below and keep it up to date with `develop` before merging.

## Day-to-day workflow

1. Open (or pick) an issue and assign yourself.
2. Branch from an up-to-date `develop`:

   ```bash
   git switch develop && git pull
   git switch -c feature/12-xlsx-ingestion
   ```

3. Commit in small steps using [Conventional Commits](https://www.conventionalcommits.org/):
   `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`, `refactor: ...`, `chore: ...`.
4. Before pushing, run the same checks CI runs:

   ```bash
   .venv/bin/ruff check src tests scripts
   .venv/bin/ruff format --check src tests scripts
   .venv/bin/pytest
   ```

   If you touched a TÜİK adapter, also run `.venv/bin/pytest -m live`.
5. Push and open a PR into `develop`. Fill in the template, link the issue
   (`Closes #12`), and add an entry under `[Unreleased]` in `CHANGELOG.md`.
6. Merge after CI is green and one teammate approves. Use **squash merge** so each
   feature lands on `develop` as one Conventional Commit. Delete the branch afterwards.

## Issue closing on develop

The `Close issues after develop merge` workflow closes completed issues after
an approved PR is merged into `develop`. Keep the repository default branch
`master`; branch protection and rulesets do not change GitHub's built-in closing
keyword target. This workflow supplies the additional develop behavior.

Use a standalone closing directive in the **PR description** only when all of
that issue's acceptance criteria are complete:

```text
Closes #12
```

`Fixes #12` and `Resolves #12` are also accepted (including the other standard
English keyword forms, case-insensitively). For partial work use `Refs #12`.
An issue with outstanding GitHub-hosted acceptance runs must remain a reference
until those runs are complete. Each issue still has its own branch and PR into `develop`.

The parser accepts multiple references separated by commas/semicolons or on
separate directive lines. Same-repository qualified references and GitHub issue
URLs are supported; references to other repositories are ignored. Ordinary
mentions, PR titles, branch names and commit messages do not trigger this
workflow. Examples inside fenced/indented code, inline code, HTML code blocks,
HTML comments and blockquotes are ignored. Put the actual directive outside the
code block, as the PR template does.

On a non-forced push to `develop`, GitHub's associated-PR API is checked against
the exact pushed SHA. The canonical PR must be merged, target this repository's
`develop` branch and have that exact `merge_commit_sha`. Only then does the
workflow close its explicitly referenced open issues as `completed`. It does not
close PRs, already-closed issues or missing issue numbers. Retries are idempotent.
A direct commit without a matching merged PR does not close anything. This also
supports PRs merged from forks: the workflow runs from the trusted develop push,
with the base repository's token, rather than executing an unmerged PR head.

No PAT or additional secret is needed. The job requests `contents: read`,
`pull-requests: read` and `issues: write`; it neither changes the default branch
nor weakens merge/review rules. Closing uses the Issues API and creates no bot
comments. The Actions log identifies the PR, source SHA and each issue it closes.
If closing fails, inspect the workflow run and correct the directive/permission
before retrying. A missing issue logs a warning and is skipped.

Activation: merge the automation PR into `develop` through the normal CI/review
process. Its `push` trigger works from `develop`; it does not need a release to
`master` or a default-branch workflow_dispatch bootstrap. Earlier PR merges are
not backfilled. GitHub's native `Closes` behavior on `master` continues to work.

Offline automation checks (also run in CI):

```bash
node --test .github/scripts/close-develop-issues.test.cjs
```

See [GitHub's native issue-closing behavior](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
and [push event workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push).

## Releasing

For v0.2.0, use the issue-by-issue readiness and evidence checklist in
[`docs/RELEASE_0.2.0.md`](docs/RELEASE_0.2.0.md). Keep #10 and its tracking epic #23
open until the reviewed promotion and tag are complete; a preparation PR uses
`Refs #10`, not an early issue-closing directive. The old `release/0.2.0` instruction
in #10 predates the enforced topic → `develop` → `master` branch policy below.

1. `git switch -c chore/prepare-0.2.0 develop`
2. Bump `version` in `pyproject.toml`, move `[Unreleased]` entries in
   `CHANGELOG.md` under `[0.2.0] - YYYY-MM-DD`, and refresh `docs/VERIFICATION.md`
   with a live run if needed. Open a PR into `develop` and merge after CI and review.
3. Open a PR `develop → master`. Merge with a **merge commit** (not squash),
   so `master` and `develop` keep a shared history.
4. Tag the merge commit on `master` and push the tag:

   ```bash
   git switch master && git pull
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

5. Continue new work from `develop`. Release preparation is already in `develop`;
   there is no `master → develop` PR in this workflow.

Hotfixes branch from `develop`, merge into `develop`, and then promote through
`develop → master`, bumping the patch version when appropriate.

## Versioning

[Semantic Versioning](https://semver.org/). While the project is `0.x`, a minor
bump (`0.2.0`) may change tool signatures; patch bumps (`0.1.1`) only fix bugs.
Tool names and parameters are the public API, so call out any change to them in
the changelog.

## Repository settings (admin)

The active GitHub branch rulesets for `master` and `develop` are stored as
API/import payloads in [`.github/rulesets/`](.github/rulesets/). Changes to these
files alone do not update GitHub; an administrator must apply the payload with
the repository rulesets API or import it in Settings → Rules → Rulesets.

- Require a pull request before merging, with at least 1 approval; dismiss stale
  approvals when reviewable changes are pushed and resolve review conversations
- Require `Lint and test (Python 3.12)`, `Lint and test (Python 3.13)`,
  `Docker build`, and `Branch policy`, all from the GitHub Actions app
- Require topic branches to be up to date with `develop` before merging
- Allow only merge commits for `develop → master`, preserving shared history;
  `master` does not require `develop` to include its previous release merge commit
- Block force pushes and deletions
- No bypass actors, including administrators

Bootstrap: the branch-policy workflow is introduced by its own topic PR into
`develop`. That PR runs the new required check and still needs one teammate's
approval. Existing PRs without the workflow must update from `develop` after
the setup PR merges. A subsequent `develop → master` PR carries the workflow
to `master` and runs the same check. Do not release unrelated code just to install
the workflow. Review changes to the CI workflow and branch-policy script carefully:
like the other CI jobs, this check runs from the PR revision with read-only access.

The check validates source/target names; the rulesets enforce PRs, reviews and CI.
It does not prove a branch's historical creation point. No signed-commit or linear
history requirement is enabled. Weekly live TÜİK checks remain optional.

Offline branch-policy regression checks (also run in CI):

```bash
node --test .github/scripts/branch-policy.test.cjs
```

See [GitHub's ruleset documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Live upstream contract monitoring

`Live TÜİK contracts` runs weekly on Monday at 06:17 UTC (09:17 Istanbul),
separately from CI. It runs `pytest -m live -k 'not test_live_mcp_workflow'`
and the MCP stdio smoke script (the full MCP workflow runs only once),
sequentially with the existing HTTP pacing; it never crawls files. Keep this
workflow out of required branch checks: an upstream outage must not block PRs.
The `tuik-live-contracts-<run>-<attempt>` artifact contains JSON, JUnit and logs,
retained for 30 days, including smoke failures. An install failure is also reported.
Failures open or refresh one issue labeled `bug` and `area: http`; a previously
closed monitoring issue is reopened on recurrence. Success does not close issues.

GitHub requires the workflow to exist on the default branch (`master`) for
**both** schedule and `workflow_dispatch`. Until the next release includes it,
use the equivalent local commands below. For pre-release GitHub acceptance,
maintainers can use a separate, disposable branch with a push trigger restricted
to that branch, run the normal and injected-failure paths, and then delete the
branch. Keep that temporary trigger out of the production PR. This is a manually
initiated push run, not a `workflow_dispatch` run; it does not activate scheduling.
The completed #28 exercise is recorded in
[`docs/verification/issue-28-2026-10-18.md`](docs/verification/issue-28-2026-10-18.md).
Once present on `master`, maintainers
can select `develop` or a feature branch from Actions → Live TÜİK contracts →
Run workflow, or run:

```bash
gh workflow run live-contracts.yml --ref develop
# Once on the default branch, exercise reporting on a branch without TÜİK traffic:
gh workflow run live-contracts.yml --ref chore/28-live-contract-checks -f exercise_failure=true
```

Do not record a successful GitHub manual run until it has actually executed. The
failure exercise intentionally makes the run fail and creates/reopens the same
monitoring issue; inspect it and close it as an exercise once verified. The offline
reporter tests exercise first failure, repeat failure and reopening without any
GitHub write.

When a real check fails:

1. Read the linked run's artifacts. Distinguish timeout/outage/throttling from a
   changed JSON schema, missing resource or unexpected file signature.
2. Retry once manually with default pacing; do not raise request rates or crawl
   all files. Use the JSON's timestamp and error and the test logs for diagnosis.
3. For a contract change, capture a reduced official fixture, add an offline
   regression test, fix the adapter on a `fix/<issue>-<slug>` branch and rerun live.
4. Confirm a successful live run, link it in the monitoring issue, then close the
   issue manually. Keep transient outages visible until recovery is verified.

Local equivalents (no issue creation):

```bash
.venv/bin/pytest -m live -q --junitxml=/tmp/tuik-live-tests.xml
.venv/bin/python scripts/live_smoke.py --output /tmp/tuik-live-report.json
node --test .github/scripts/report-live-contracts.test.cjs
```

The smoke script exits nonzero on failure and writes a structured error JSON as
well as a stderr traceback. Source text and exception logs remain untrusted data.
See [GitHub's manual workflow prerequisites](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
