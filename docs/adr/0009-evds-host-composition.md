# ADR 0009 · TÜİK and EVDS in a finance briefing

**Status** · Proposed for review. **Evidence recorded** · 2026-11-28, Europe/Istanbul.
**Tracking** · [#109](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/109).

## Decision

Keep provider access with the host and its official tools. Ship the bounded
offline evidence replay as research. Keep the core TÜİK server independent
of an EVDS account, API key, adapter and query contract.

The demonstrated benefit is narrow. A finance analyst can compare the
direction and scale of general consumer prices and the month-end indicative
USD buying rate in the same quarter, with a separate audit trail for each
measure. TÜİK-only evidence answers the price side and has one source to
configure. The composed path adds three exchange-rate points and their source
dates. It requires inspecting a second series, choosing a daily-to-monthly
sampling rule, and keeping two sets of release metadata. One quarterly
example supports the descriptive comparison. Evidence for recurring operator
demand or a dedicated production integration is still pending.

| Option | Setup and maintenance | Result for the task |
| --- | --- | --- |
| TÜİK-only report | One provider and the existing reviewed observation recipe | Headline CPI for three months. |
| Host composition | Independently obtain the second series, record the day selection and replay two typed inputs | Both series and their normalization, with source IDs and vintages visible. |
| Optional server adapter | Own credential storage, EVDS failures, schema/version mapping, licensing checks and cross-provider tests | The same comparison with a different integration path. Defer until repeat use and authenticated samples justify it. |

TCMB announced its own EVDS-MCP on 26 October 2026. The provider documents
`TP.DK.USD.A.YTL` as its USD buying series. The recorded run used the
same publisher's public dated XML for the corresponding USD ForexBuying
observations. Authenticated EVDS/MCP parity, permissions and latency remain
unverified. The documented provider path for a credentialed comparison is
an operator-configured official EVDS connection. Keep its credentials outside
conversations, fixtures and logs. [TCMB EVDS-MCP announcement](https://www.tcmb.gov.tr/wps/wcm/connect/8aa08135-1a98-40dc-a800-df22608c9088/DUY2026-46.pdf?MOD=AJPERES) · [EVDS exchange-rate catalogue](https://evds3.tcmb.gov.tr/tumSeriler/2501/bie_dkdovytl) · [Official EVDS series/API guide](https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=4) · [TCMB exchange-rate publication](https://www.tcmb.gov.tr/kurlar/kurlar_en.html).

## Recorded comparison

Select Türkiye's headline CPI level, original monthly observations,
2025=100. Pair it with TCMB's indicative foreign-exchange USD buying rate
in TRY per USD, sampled on the last published business day for each matched
month. The recorded dates are 30 January, 27 February and 31 March. This
sampling is a **recorded selection** in the pilot. A host making a new
capture must establish the last published day from the provider calendar
and available records. The replay checks that every cited date belongs to
its matched month and that its URL identifies that date's official XML.

In the briefing, normalize each level independently to January 2026=100
using `100 × observed value / its own January value`. Compare the resulting
movements descriptively. The source CPI base remains 2025=100. The original
exchange rate remains a nominal TRY-per-USD level. Each derived point
links to both the current and baseline source observations.

| Month | TÜİK CPI (2025=100) | TCMB USD buying (TRY per USD) | CPI (Jan=100) | USD buying (Jan=100) |
| --- | ---: | ---: | ---: | ---: |
| 2026-01 | 115.73 | 43.3414 | 100.0000 | 100.0000 |
| 2026-02 | 119.16 | 43.8000 | 102.9638 | 101.0581 |
| 2026-03 | 121.47 | 44.3961 | 104.9598 | 102.4335 |

By March both series were above their January levels. CPI was up 4.9598%
and the indicative USD buying rate 2.4335% over the selected interval.
The report uses two nominal movements. A purchasing-power or real
exchange-rate analysis would require its own comparison definition and
additional evidence.

## Reproduction and provenance

With the repository's Python environment:

```sh
python docs/verification/issue-109/compose.py > /tmp/tuik-evds-evidence.json
python -m pytest -q tests/test_evds_pilot.py
```

The [input fixture](../verification/issue-109/2026-q1.json) contains the
six public values, provider IDs, original decimal strings, source locators,
retrieval times and SHA-256 hashes of the upstream responses. TÜİK values use
the reviewed `cpi-main-groups` recipe on `DF_TUFE_SDMX_TT01+V1.0`, exact
filters `Endeks-Değişim=Endeks` and `Sınıflama Düzeyi=TÜFE`, and source
records 254–256. TCMB values use the `ForexBuying` element of the USD
record in each linked daily XML. The stored observations are data excerpts.
The response hashes allow comparison to a fresh download while recognizing
that live exports can change. The replay preserves each source record and
its vintage independently. When two vintages claim one series and month,
the input must select one explicitly before generating another briefing.

Replay accepts two fixed series profiles, at most 12 consecutive matched
months, and up to 32 KiB of local JSON. It rejects different frequencies,
currencies, bases, adjustments and source channels. It also rejects unknown
URLs, duplicate IDs or periods, missing months and ambiguous or nonpositive
values. Replay performs zero network calls; source URLs are provenance.
Raw input text is never executed or echoed in validation errors.

TÜİK is the original CPI publisher. TCMB is the original rate publisher and
operates EVDS, the reference catalogue for the rate ID. The dated XML values
were obtained directly from TCMB; the export records no redistributor. The
public terms permit publication of these credited excerpts in this research
sample. The TCMB website terms require written permission for commercial
use of website data. The separate EVDS terms require attribution and identify
translated text as unofficial. [TÜİK legal notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari) · [TCMB public website terms](https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Bottom%2BMenu/Diger) · [TCMB EVDS terms](https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=18).
