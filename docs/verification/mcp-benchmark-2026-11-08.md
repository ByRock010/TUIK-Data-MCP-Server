# TÜİK MCP vs web vs closed-book benchmark — 2026-11-08

One model answered the same 10 difficult TÜİK questions under three conditions.
The model was `claude-opus-5-5` through headless Claude Code (`claude -p`, stream-json). Each answer was graded against ground truth
taken independently from TÜİK's raw CSV/Excel files and bulletin texts.
**This is a small, single-run comparison graded by a model of the same family;
it shows direction, not statistical significance.**

## Result

| | TÜİK MCP | Web | Closed-book |
| --- | ---: | ---: | ---: |
| Tools available | 9 TÜİK MCP tools only | WebSearch + WebFetch only | none |
| Score (correct 1, partial 0.5) | **10 / 10** | 8 / 10 | 2 / 10 |
| Correct / partial / no answer / wrong or broken | 10 / 0 / 0 / 0 | 6 / 4 / 0 / 0 | 1 / 2 / 3 / 4 |
| Median session time | **32 s** | 73 s | 20 s |
| Median session cost | $0.39 | $0.37 | $0.05 |
| Total cost (10 sessions) | $4.45 | $4.11 | $3.21 |
| Tool calls (mean per session) | 4.4, 0 errors | 9.0 | 0 |

- **MCP vs web:** the web condition found most headline bulletin figures through
  news sites. Only 11 of its 52 page fetches were on `tuik.gov.tr`. It cannot see
  revised series:
  - P02: it named February 2026 (15.8%) as the youth-unemployment peak; the
    current series says November 2025 (15.9%).
  - P03: it gave June 2026 as 7.6%; the current series says 7.7%.
  - P10: it gave 2023 GDP growth as 5.1%; the current value is 5.0%.

  MCP answered all three from TÜİK's current CSV and Excel files.
- **Grounding:** every number in the MCP answers either appears in that session's
  tool output or is the model's own labelled calculation or rounding. The
  automatic check below flags the rest, and each one was reviewed by hand.
- **Closed-book failure modes:**
  - Wrong figures from memory: P09 gives 185,742 divorces in 2024; the 2024
    bulletin says 187,343, later revised to 188,963.
  - In 2 of 10 sessions (P02, P05), with no tools available, the model wrote fake
    tool-call markup as text, with invented files and system messages.
  - P05 then ran for 14 minutes, cost $2.63 and ended in an API safety refusal
    with no answer.

## Setup

- **Prompts:** 10 Turkish questions written to be hard in different ways: very
  recent bulletins (after the model's June 2026 knowledge cutoff), multi-step
  table queries, a 261-month series, a methodology PDF asked in ASCII spelling,
  an unpublished statistic (monthly İstanbul unemployment), classification
  metadata, a paraphrase, and revised GDP growth.
- **Same everything else:** one system prompt for all conditions ("answer in
  Turkish, give period and source, state what you cannot verify, do not invent
  values, today is 8 November 2026"). Sessions ran from an empty directory with
  `--strict-mcp-config`.
- **MCP server:** this repository at `fix/hybrid-no-lexical-match` @ `2243c2e`
  ([PR #81](https://github.com/ByRock010/TUIK-Data-MCP-Server/pull/81)).
  - Search used a local OpenSearch 3.3.2 index: the frozen 3,424-document corpus
    of 2026-11-05 plus 16 content documents, with multilingual-e5-small vectors.
    All MCP searches were answered by OpenSearch (hybrid ranking, lexical for
    dataset and file-content searches).
  - Data files and bulletins were downloaded live from TÜİK.
- **Grading:** correct = matches ground truth; partial = some values outdated or
  missing; no answer = declined without inventing values; wrong = a stated value
  or claim contradicts ground truth; broken = no usable answer. For revised
  series, the current published series is ground truth.

## Per question

| ID | Challenge | Ground truth (abridged) | MCP | Web | Closed |
| --- | --- | --- | --- | --- | --- |
| P01 | Latest month, three breakdowns | Sep 2026 SA unemployment 8.4 / 6.9 / 11.3 %, +0.5 / +0.2 / +1.1 pt | correct | correct | no answer |
| P02 | Multi-step query + mean | Youth (15-24) Oct 2025–Sep 2026: max Nov 2025 15.9, min Jun 2026 12.9, mean 14.5 | correct | partial: first-release values, wrong peak month | broken: fake tool calls |
| P03 | 261-month series | Max 14.1 (Aug, Sep 2019; Jul 2020, tied), min 7.7 (Jun 2026) | correct | partial: 7.6, missed ties | wrong: "consistent series starts in 2014" |
| P04 | Newest bulletin + trap | Oct 2026 CPI 29.77 % y/y, 2.58 % m/m; highest group education 48.86 % (not housing 40.24 %) | correct | correct | no answer |
| P05 | Methodology PDF, ASCII spelling | CAPI + CATI; ~60k households per quarter, ~235k per year | correct (PDF pages 4–5) | correct (microdata document, p. 7) | broken: fake tool calls, API refusal |
| P06 | Unpublished statistic | No monthly provincial unemployment; İstanbul (TR10) is annual only | correct | correct | correct |
| P07 | Classification + metadata | HİA uses NACE Rev.2.1 (from Jan 2026); 9 NACE versions on the server | correct | partial: said Rev.2, 4 of 9 versions | partial: said Rev.2, 4 versions from memory |
| P08 | Latest trade bulletin | Sep 2026 exports $25.967bn, imports $31.130bn, deficit $5.163bn, cover 83.4 % | correct | correct | no answer |
| P09 | Paraphrase, near cutoff | 2025: 193,793 divorces, 2.26 ‰ (2024 revised 188,963) | correct | correct | wrong: 2024 from memory |
| P10 | Revisions | 2023 5.0 %, 2024 3.5 % (r), 2025 3.7 %; first releases 4.5 / 3.2 / 3.6 | correct | partial: 2023 as 5.1 % | partial: first releases only |

Full prompts, ground-truth sources, grading notes (in Turkish) and every final
answer are in [the evidence JSON](mcp-benchmark-2026-11-08.json).

## Tool use

| ID | MCP call sequence | Time | Cost | Numbers traced |
| --- | --- | ---: | ---: | ---: |
| P01 | search_tuik → get_tuik_resource | 27 s | $0.35 | 14/16 |
| P02 | search_tuik → get_tuik_resource → inspect_tuik_data → get_tuik_data | 31 s | $0.53 | 13/16 |
| P03 | search_tuik_datasets ×2 → inspect_tuik_data → get_tuik_data ×2 | 34 s | $0.30 | 7/8 |
| P04 | search_tuik → get_tuik_resource → inspect_tuik_data → get_tuik_data | 32 s | $0.51 | 63/63 |
| P05 | search_tuik (document_chunk, metadata) → read_tuik_document ×2 | 33 s | $0.22 | 2/2 |
| P06 | search_tuik ×3, get_tuik_resource ×2 → inspect_tuik_data → get_tuik_data | 33 s | $0.73 | 15/15 |
| P07 | search_tuik → search_tuik_classifications → search_tuik → get_tuik_resource | 39 s | $0.42 | 11/11 |
| P08 | search_tuik → get_tuik_resource | 23 s | $0.28 | 16/28 |
| P09 | search_tuik → get_tuik_resource → read_tuik_document → get_tuik_data | 28 s | $0.27 | 11/15 |
| P10 | 4 searches → get_tuik_resource ×2 → inspect_tuik_data → get_tuik_data | 39 s | $0.85 | 16/16 |

"Numbers traced" counts decimal and four-digit-plus numbers in the answer, after
removing years and dates, that occur verbatim (or in a TÜİK spelling such as
`2 967` or `25 milyar 967 milyon`) in the session's tool output. A number must
occur whole: a match inside a longer number does not count. Every untraced
MCP number was checked by hand:

- **Calculations:** ratios, means and differences, such as 2,967 / 35,412 = 8.38 %
  and the 12-month mean of 14.525.
- **Roundings:** traced values written shorter, such as 23 milyar 506 milyon → 23.51
  and 31 milyar 130 milyon → 31.13.
- **One spelling the check misses:** "8,0" where the source wrote "8".

No MCP tool call returned an error. The PDF passage search (`kind="document_chunk"`)
found the labour methodology page directly (P05). Indexed bulletin search
retrieved first-release bulletins from 2023–2025 for the revision questions
(P09, P10).

The web condition read mostly secondary sources: alomaliye.com, news sites and
bank research. In P07 the TÜİK classification server it tried timed out three
times.

## Limitations

- Each prompt ran once per condition. Agent runs vary; 10 prompts is a small sample.
- The grader is the same model family as the system under test. Ground truth
  was extracted from TÜİK files before reading the answers, but there is no
  independent human review.
- The closed-book condition was run inside Claude Code with all tools disabled.
  Its fake-tool-call failures (P02, P05) are partly a harness effect: the agent
  is prompted as a tool-using agent. A plain chat model without that harness
  may behave differently.
- The questions deliberately include data published after the model's knowledge
  cutoff, so part of the closed-book gap is expected by design.
- The MCP search index was frozen three days before the run. Questions about
  bulletins published after 2026-11-05 would have needed live search or a
  refreshed index.

## Reproduce

Needs an authenticated Claude Code CLI. The MCP condition also needs the search
index and network access to TÜİK.

```bash
docker compose up -d opensearch
TUIK_OPENSEARCH_URL=http://localhost:9200 .venv/bin/python scripts/mcp_benchmark.py run \
  --prompts prompts.json --out /tmp/tuik-bench --today "8 Kasım 2026"
.venv/bin/python scripts/mcp_benchmark.py summarize --out /tmp/tuik-bench \
  --output /tmp/tuik-bench/summary.json
```

`prompts.json` is the `prompts` array of the evidence JSON. Grading is manual,
against the `ground_truth` entries.

## Files

- [`mcp-benchmark-2026-11-08.json`](mcp-benchmark-2026-11-08.json): run setup,
  prompts, ground truth with sources, grades with notes, and per-run summaries
  (tool calls with inputs, cost, duration, traced numbers, final answer).
- [`mcp-benchmark-runs-2026-11-08.jsonl.gz`](mcp-benchmark-runs-2026-11-08.jsonl.gz):
  all 915 stream-json events of the 30 sessions, including full tool results.
  Init events are reduced to model, tools and MCP status, and local paths and
  the user name are masked.
