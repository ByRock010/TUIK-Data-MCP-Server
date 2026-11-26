# Sector benchmark and current capability baseline

[Issue #85](https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/85) extends the
[existing ten-question comparison](../mcp-benchmark-2026-11-08.md). The
[protocol](protocol.md) and [24 tasks with answer rubrics](tasks.json) were frozen
before the current capability check. The November 8 archive anchors questions
with historical revision cutoffs. The other questions name fixed official
release IDs and dates so future publications cannot change the grading target.

| Persona | Supported paths | Unsupported requests | Held out |
| --- | ---: | --- | --- |
| Finance/research | 5 | F06 editable XLSX | F06 |
| Manufacturing/export | 5 | M06 editable PPTX | M06 |
| Local government | 5 | L05 monthly provincial unemployment | L06 |
| Academic/journalism | 5 | A06 editable DOCX | A06 |
| Total | 20 | 4 | 4 |

The [baseline output](baseline.json) records nine discovered MCP tools on
develop `b7bdb25`. All 24 tasks have their stated source or fallback path
mapped to discovered tools. The capability audit uses the November 8 run,
frozen official releases, repository tests for CSV/XLS/PDF and cursor paths,
and a fresh stdio artifact run. The output's `expected_support` field copies
the frozen task classifications. A supported task has a path using the current
tools, plus client-side arithmetic when the rubric calls for it. The measured
checks cover tool discovery, route availability and the F05 artifact probe.

F05 exercised the actual MCP tool on a recorded official series. The PNG,
three plotted CSV values, original source bytes and manifest were fetched via
`resources/read`. The source hash and chronology matched. The figure was
visually checked for Turkish title, source, ticks and label clipping. The PNG
is static and the independently retrieved CSV is editable with a spreadsheet
application. The three Office requests require exporters absent from this
baseline. The monthly province request requires statistics that the cited
annual and monthly releases do not establish.

| Client | Observed availability | Execution evidence |
| --- | --- | --- |
| Python MCP SDK 1.30.0 / Python 3.14.0 | Local `ClientSession` | Fresh stdio tool list and fixture-backed F05 artifact retrieval. Cold process, then inspected source reused by chart. No search index. No model/provider. Calls limited to discovery, inspection, charting and artifact reads. |
| Claude Code 2.1.158 | Installed CLI version verified via `claude --version` | Current scenario run not scored in this client. Prior model/provider and permissions are in the November 8 report. |
| VS Code 1.147.0 | Installed editor version verified via `code --version` | Editor MCP configuration is supported. Connection, Copilot availability and setup timing were not tested. [Editor documentation](https://code.visualstudio.com/docs/agents/reference/mcp-configuration). |

## Reproduce and extend

```sh
python docs/verification/issue-85/baseline.py > /tmp/sector-capabilities.json
python docs/verification/issue-85/baseline.py --prompts development > /tmp/sector-development.json
python docs/verification/issue-85/baseline.py --prompts held_out > /tmp/sector-held-out.json
```

Pass either prompt file to the existing harness with `--prompts`,
`--today 23.11.2026`, `--timeout 120` and `--mcp-workers 1`. Preserve
`run-meta.json`, per-task stream JSON and the existing summaries.
Before collecting scored answers, apply the budget flag and sidecar fields
specified in the protocol. For artifact tasks, verify that the comparison
client can read the generated MCP resource in its permitted workspace.
Use known read-only capabilities and check that any exported path belongs to
the current session. The folder for a scored run must contain no rubrics.
A comparison across clients additionally needs separate setup and provider
records. No scored model sample or measured success interval is claimed here.
