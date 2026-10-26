# Turkish lexical search design

Issue #13. Token design accepted for the #14 schema foundation on 2026-10-26;
ranking defaults and synonym enablement remain subject to #11/#20 human evaluation.
No MCP search routing changes in this increment. See [ADR 0002](adr/0002-opensearch.md).

## Evidence and reproduction

The [captured OpenSearch 3.3.2 report](verification/search-analysis-2026-10-26.json)
records 38 difficult forms through seven analyzers, including full token positions,
offsets, graph lengths, the exact index definition, engine/Lucene version and cleanup
acknowledgment. Fourteen small controlled retrieval probes compare six variants on
nine example titles. These are linguistic checks, not the independent relevance
judgments required by #11. They establish observed behavior, not relevance superiority.

```bash
docker compose up -d --wait
.venv/bin/python scripts/inspect_search_analysis.py \
  --url http://localhost:9200 --output /tmp/search-analysis.json
.venv/bin/pytest tests/test_search_analysis.py -q
```

The probe creates a unique `tuik-analysis-probe-<uuid>` index, writes only its own
controlled documents and deletes that exact index in `finally`, including on analyze
failure. It never changes `tuik-resources`, aliases, existing indexes or TÜİK sources.
Cleanup failure makes the command fail and is recorded in the output. Offline tests
check the captured linguistic findings and failure/cleanup behavior without a server.

## Fields and analyzer chains

The reusable settings live in
[`search/analysis.json`](../src/tuik_mcp/search/analysis.json); the reviewed candidate
rules live in [`search/synonyms.txt`](../src/tuik_mcp/search/synonyms.txt).
`tuik_mcp.search.analysis_settings()` loads both using package resources and returns
fresh settings, without importing the optional OpenSearch client or accessing a server.
The synonym text is embedded into the settings, avoiding container-side file mounts.
#14 will combine these settings with the document schema and actual mapping.

| Field | Index analyzer | Query analyzer | Purpose |
| --- | --- | --- | --- |
| `title` | `tuik_tr_index` | `tuik_tr_search` (or index analyzer for the no-synonym variant) | Original Turkish stems, highest title weight |
| `title.ascii` | `tuik_ascii_index` | `tuik_ascii_search` (or index analyzer for no synonyms) | ASCII typing, without stemming |
| `title.keyword` | keyword | exact term | Exact values/sorting; preserve the original title |
| `title.suggest` | defer to #14/#18 | prefix-specific | Optional autocomplete, separate from general retrieval |
| `description`, `body` | `tuik_tr_index` | `tuik_tr_search` | Natural-language text, lower weight than the title |
| `theme`, `subtheme`, `kind`, IDs | keyword | exact filters | Filter/facet identity, not synonym-expanded text |
| optional `title_en` | built-in English | built-in English | English text only, separate from Turkish |

Both paths use a character filter converting curly/modifier/full-width apostrophes
to ASCII, then the standard tokenizer. The Turkish index chain is `apostrophe` →
Turkish lowercase → Turkish Snowball stemmer. Its search chain inserts
`synonym_graph` **before** the stemmer. The ASCII chain is `apostrophe` → Turkish
lowercase → `asciifolding`; its search version then adds the graph filter. The rules
are parsed through the filters preceding each graph, so the same reviewed file also
recognizes ASCII `TUFE` and `YI-UFE` without an independently drifting rule list.

We intentionally omit stop-word removal in the custom chains: short statistical
phrases and multiword synonyms should retain all positions. The built-in `turkish`
analyzer (which has stop-word removal) remains a measured baseline. This is a design
choice for evaluation, not a proven quality advantage.

Turkish lowercase precedes folding. It maps `İ` to `i` and `I` to `ı`; only the ASCII
field later merges `ı` into `i`. Stemming folded text changes the stemmer's vowel
context. In the captured report, `işsizliğin` and `işsizlik` both become `işsizlik`
in the Turkish path; the unsafe fold-then-stem probe produces `issizligi` versus
`issizlik`. Snowball stems are retrieval tokens, not guaranteed dictionary lemmas.
The [built-in Turkish analyzer documentation](https://docs.opensearch.org/latest/analyzers/language-analyzers/turkish/)
describes the reference components; all claimed token outputs above were also
measured on the pinned Compose version, rather than assumed from newer docs.

## Synonym direction and graph behavior

Equivalent rules cover TÜFE, ÜFE, Yİ-ÜFE, GSYH, HİA, ADNKS and NACE with their full
names. **ÜFE and Yİ-ÜFE are separate rules**: a generic producer-price term must not
silently become the domestic series. Standard tokenization splits `Yİ-ÜFE` into
`yi`, `üfe`; the rule is written as `yi üfe`, and the graph output is recorded.

Broad concepts expand one way: enflasyon, hayat pahalılığı, milli gelir and dış
ticaret. Keep the original phrase as an alternative. Write full target phrases on
the right-hand side: graph expansion is not recursive, so `enflasyon => tüfe`
would not subsequently expand to a title containing only Tüketici Fiyat Endeksi.
`ihracat` does not expand back to dış ticaret, and a CPI title does not expand back
to enflasyon. The ambiguous `büyüme` is excluded pending human evaluation.

Graphs appear only at search time. They preserve alternative multiword paths;
the probe stores `positionLength` evidence. Rules are strict (`lenient: false`)
and no stop/edge-ngram filter precedes them. The ordering follows the
[official graph-filter constraints](https://docs.opensearch.org/latest/analyzers/token-filters/synonym-graph/).
Synonym changes are versioned with a rebuilt index initially; hot reload is not
claimed by this design.

## What the controlled retrieval probes show

| Input | Observed behavior on the controlled titles |
| --- | --- |
| `işsizliğin` | Built-in Turkish and the original-text Turkish field find İşsizlik Oranı |
| `issizlik` | Fold-only and multi-field paths find İşsizlik Oranı; built-in Turkish misses it |
| `TÜFE`, `TUFE`, `Yİ-ÜFE` | Search-time synonyms find the corresponding full title; plain multi-field misses abbreviations absent from the title |
| `enflasyon` | Synonyms find CPI and domestic PPI titles; plain variants miss them |
| `İstanbul'un`, `İstanbul’un` | Both apostrophe forms find İstanbul Nüfusu |
| `nufusun` | `AUTO` fuzzy fold finds population titles; fold-only and index-time edge n-grams miss |
| `nüfusun` | Snowball yields `nüfu` while canonical `nüfus` stays `nüfus`; plain Turkish/multi-field misses, and the separate fuzzy fold clause finds population titles |
| `issizligin` | All six tested variants miss the canonical unemployment title |
| `enflsyon` | All tested variants miss CPI/PPI: typo matching does not recursively trigger the enflasyon synonym |

Index-time [edge n-grams](https://docs.opensearch.org/latest/analyzers/token-filters/edge-ngram/)
support prefixes, not arbitrary suffix removal: a query longer than the indexed
canonical word is not one of its prefixes. Reserve them for optional autocomplete;
do not advertise them as a solution for ASCII-typed Turkish inflections.

## Query design handoff to #17

Compare a plain `multi_match` with `best_fields` over `title^4`, `title.ascii`,
`description^2` and `body`, then the search-time graph variant. Those weights are
starting hypotheses for #20, not measured defaults. Use exact keyword filters for
kind/theme/downloadable/date, separately from the text query. Retrieve highlights
and preserve each document's source URL and index timestamp in #18.

Fuzziness is an optional lower-weight ASCII clause, bounded to `AUTO`,
`prefix_length: 1`, `max_expansions: 50`; it is not paired with `cross_fields`.
The probe records the exact query DSL. Fuzziness fixes some edits but misses
`issizligin` and cannot make an absent abbreviation appear in the indexed text.
Do not apply unrestricted spelling/diacritic substitutions or claim complete
Turkish morphology. More advanced deasciification needs its own evaluated design.

## ICU, morphology and remaining research

ICU is optional and not used by the accepted chains. Its folding filter requires
an additional plugin and performs broad normalization; any experiment must retain
Turkish lowercase before ICU folding and compare on the same relevance pool.
See [official ICU documentation](https://docs.opensearch.org/latest/analyzers/token-filters/icu-folding/).
No ICU benchmark or installation is claimed here.

[Zemberek](https://github.com/ahmetaa/zemberek-nlp) provides Turkish morphology in a
Java toolkit. It is a feasibility option for an external preprocessing step or a
custom analyzer plugin, adding deployment/version costs. It is not installed,
benchmarked or required for this lexical milestone.

The #11 preparation PR supplies the 48-query pooled judging workflow. Complete two
independent human judgments and κ, then #20 evaluates baseline, built-in Turkish,
multi-field and multi-field+synonym variants with one enlarged judged pool. Only
that evidence chooses relevance defaults. #13 supplies the reproducible token
contract that unblocks #14; it does not close #11 or #20.
