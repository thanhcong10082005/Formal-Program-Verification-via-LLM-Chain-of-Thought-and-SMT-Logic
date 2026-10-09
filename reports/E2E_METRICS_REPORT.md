# End-to-End Metrics Report

_Regenerated from the deterministic E2E run on 2026-10-10._

## Executive Summary

The dataset-backed refresh regenerated 94 programs for each baseline,
producing 188 fresh `ProgramResult` records. It ran deterministically from
source assertions, so no LLM tokens are included. When LLM mode is enabled,
`AST_ANCHORED` and the compatibility-named `UNANCHORED` baseline use separate
calls: the former requests anchored Python claims, while the latter requests
Baseline A Direct Formalization in SMT-LIB2.

The refreshed AST-grounded baseline produced 8 counterexamples and 9 bounded
coverage unknowns among accepted programs. Direct Formalization produced 17
SMT-LIB2 counterexamples. Direct models have no program replay or anchors;
these results are not paired claims and do not support claim-level causal
attribution between the baselines.

## Run Scope

| Item | Value |
|---|---:|
| Source programs | 94 |
| Baseline records | 188 |
| Subset-accepted programs | 12 |
| Subset-rejected programs | 82 |
| Successful Gemini generations | 0 (deterministic refresh) |
| Gemini provider failures | 0 |
| Claims per baseline | 17 |
| Requested model for optional LLM run | `gemini-3.5-flash` |

LLM generation was not requested for this refresh. Subset-rejected programs
remain represented in both result directories.

## Metric Comparison

| Metric | `AST_ANCHORED` | `UNANCHORED` |
|---|---:|---:|
| False Discovery Rate | n/a | n/a |
| Accuracy (`VERIFIED` positive) | **0.059** | 0.059 |
| Precision (`VERIFIED`) | n/a | n/a |
| Recall (`VERIFIED`) | 0.000 | 0.000 |
| F1 (`VERIFIED`) | 0.000 | 0.000 |
| Average input tokens / program | 0.0 | 0.0 |
| Average output tokens / program | 0.0 | 0.0 |
| Average recorded cost / program | $0.000000 | $0.000000 |
| Hallucination Rate | 0.000 | 0.000 |
| Counterexample Replay Validity | **0.250** | n/a |

The refreshed token totals are zero because the run was deterministic. In an
LLM run, input/output totals are recorded independently for the anchored and
direct calls; they may differ because the prompts and output schemas differ.

Accuracy, precision, recall, and F1 use `VERIFIED` as the positive class. All
other statuses, including `COUNTEREXAMPLE` and `UNKNOWN_TIMEOUT`, are treated
as negative. This is a deliberately simple claim-level classification view;
the status distribution and bounded-coverage caveats remain essential.

## Verdict Distribution

| Status | `AST_ANCHORED` | `UNANCHORED` |
|---|---:|---:|
| `REJECTED` | 82 | 82 |
| `VERIFIED` | 0 | 0 |
| `COUNTEREXAMPLE` | 8 | 17 |
| `UNKNOWN_TIMEOUT` | 9 | 0 |

`AST_ANCHORED` reports `UNKNOWN_TIMEOUT` when bounded loop/control-flow
coverage is incomplete. `UNANCHORED` is Baseline A Direct Formalization: its
counterexamples are SMT-LIB2 models for `not C`, with empty anchor fields and
no replay verdict; CRVR is therefore not applicable.

## Ground-Truth Matrix

The curated ground truth contains 17 claims:

| Baseline | TP | FP | FN | TN | FDR |
|---|---:|---:|---:|---:|---:|
| `AST_ANCHORED` | 0 | 0 | 16 | 1 | n/a |
| `UNANCHORED` | 0 | 0 | 16 | 1 | n/a |

Both baselines have accuracy `1 / 17 = 0.059` and no positive `VERIFIED`
prediction. The deterministic result is an implementation refresh, not an LLM
comparison.

## Validation

The repository regression suite passed:

```text
PYTHONPATH=src pytest -q tests
16 passed
```

Compilation passed with `python -m compileall -q src tools tests`.

## Artifacts

- Per-program results: `results/AST_ANCHORED/` and `results/UNANCHORED/`
- Aggregated report: `results/summary.md`
- This report: `reports/E2E_METRICS_REPORT.md`
