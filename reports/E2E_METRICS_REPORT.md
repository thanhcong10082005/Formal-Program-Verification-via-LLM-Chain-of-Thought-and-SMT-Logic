# Temporary End-to-End Metrics Report

_Regenerated from the Gemini E2E run on 12 October 2026 using
`gemini-3.5-flash-lite`._

## Executive Summary

The full dataset-backed E2E run completed all 188 persisted records: 94 for
`AST_ANCHORED` and 94 for `UNANCHORED`. Gemini generation succeeded for both
independent calls on all 94 source programs, with no provider failures. The
run used the economical `gemini-3.5-flash-lite` model and completed in about
338.9 seconds.

`AST_ANCHORED` requested Python claims with source anchors, reachability, and
replay. `UNANCHORED` is Baseline A Direct Formalization: it requested
SMT-LIB2 QF-LIA formulas and checked only their negations. The claim pools are
independent and are not paired identical claims.

## Run Scope

| Item | Value |
|---|---:|
| Source programs | 94 |
| Baseline records | 188 |
| Subset-accepted programs | 12 per baseline |
| Subset-rejected programs | 82 per baseline |
| Successful Gemini generations | 188 (94 anchored + 94 direct) |
| Gemini provider failures | 0 |
| Model | `gemini-3.5-flash-lite` |
| AST claims | 58 |
| Direct Formalization claims | 40 |
| Full-run elapsed time | 338.9 seconds |

Subset-rejected programs remain represented in both result directories. Their
generated token usage is persisted, but they do not contribute claim verdicts.

## Metric Comparison

| Metric | `AST_ANCHORED` | `UNANCHORED` |
|---|---:|---:|
| False Discovery Rate | **0.062** | 0.000 |
| Accuracy (`VERIFIED` positive) | **0.298** | 0.053 |
| Precision (`VERIFIED`) | **0.938** | 1.000 |
| Recall (`VERIFIED`) | **0.278** | 0.027 |
| F1 (`VERIFIED`) | **0.429** | 0.053 |
| Total input tokens | 42,524 | 44,199 |
| Total output tokens | 20,138 | 11,397 |
| Average input tokens / program | 452.4 | 470.2 |
| Average output tokens / program | 214.2 | 121.2 |
| Average recorded cost / program | $0.005475 | $0.004170 |
| Hallucination Rate | **0.017** | 0.000 |
| Counterexample Replay Validity | **0.176** | n/a |

Costs use the repository defaults of $0.005 per 1K input tokens and $0.015
per 1K output tokens. Hallucination Rate counts unsupported and translation
errors over claims. Direct counterexamples have no executable target location,
so CRVR is not applicable to `UNANCHORED`.

## Ground-Truth Matrix

`VERIFIED` is treated as the positive class. The curated ground truth matches
57 anchored claims and 38 direct-formalization claims.

| Baseline | TP | FP | FN | TN | FDR |
|---|---:|---:|---:|---:|---:|
| `AST_ANCHORED` | 15 | 1 | 39 | 2 | **0.062** |
| `UNANCHORED` | 1 | 0 | 36 | 1 | 0.000 |

## Verdict Distribution

| Status | `AST_ANCHORED` | `UNANCHORED` |
|---|---:|---:|
| `REJECTED` | 82 | 82 |
| `VERIFIED` | 16 | 1 |
| `COUNTEREXAMPLE` | 17 | 39 |
| `UNKNOWN_TIMEOUT` | 24 | 0 |
| `UNSUPPORTED` | 1 | 0 |
| `NO_CLAIMS` | 1 | 0 |

The anchored run exposes bounded-coverage gaps as `UNKNOWN_TIMEOUT`. Its one
unsupported claim contributes to the 0.017 hallucination rate. Direct
formalization produced 39 SMT-LIB2 counterexamples and one verified formula;
its direct results have empty anchor fields and no replay verdict.

## Interpretation and Limits

- The two baselines were generated independently with different prompts and
  schemas, so the comparison is not a paired claim-level causal experiment.
- Only 12 of 94 programs passed the current QF-LIA subset gate; rejected
  programs are included for coverage accounting but have no claim verdicts.
- `AST_ANCHORED` counterexamples can be replayed at grounded targets. Direct
  counterexamples are Z3 models for `not C` and are intentionally not replayed.
- These metrics evaluate the persisted prototype run; they are not a proof of
  complete soundness for arbitrary Python programs.

## Validation

The implementation regression suite passed:

```text
PYTHONPATH=src pytest -q tests
16 passed
```

Compilation and whitespace validation passed:

```text
python -m compileall -q src tools tests
git diff --check
```

## Artifacts

- Per-program results: `results/AST_ANCHORED/` and `results/UNANCHORED/`
- Aggregated metrics: `results/summary.md`
- This temporary report: `reports/E2E_METRICS_REPORT.md`
