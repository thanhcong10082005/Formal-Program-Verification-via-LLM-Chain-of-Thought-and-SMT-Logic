# Gemini QF-LIA Simple-Claim Baseline Smoke Report

Generated: 2026-10-16  
Model: `gemini-3.5-flash-lite`  
Baseline: `UNANCHORED`

## Executive summary

The live Gemini smoke test reached the simple-claim baseline end to end. A
successful run produced a verified QF-LIA claim with deterministic step `1`,
empty anchors, and replay disabled.

The model output was not completely stable across repeated calls. The latest
metric-bearing run returned one valid claim and one malformed empty expression;
the malformed claim was correctly classified as `TRANSLATION_ERROR` instead
of being accepted by the verifier.

## Test scope

The live request used only synthetic, non-repository code:

```python
def f(x: int):
    assert x + 1 >= x
    return x + 1
```

No API key, repository source, dataset content, or ground-truth data was
included in this report or printed by the test.

## Latest metric-bearing smoke run

| Metric | Result |
|---|---:|
| Claims generated | 2 |
| Verified | 1 |
| Counterexamples | 0 |
| Unsupported | 0 |
| Translation errors | 1 |
| Unknown/timeout | 0 |
| Ordered steps | `[1, 2]` |
| Anchors | Empty |
| Replay | Disabled |
| Pipeline elapsed time | 7.98 s |
| Reported input tokens | 0 |
| Reported output tokens | 0 |
| Reported estimated cost | `$0.000000` |

The Gemini response did not provide usage metadata, so the zero token and cost
values are only the adapter's reported values; they should not be interpreted
as a billing guarantee.

## Clean smoke result

A preceding post-fix synthetic run returned:

```text
claims= 1
steps= [1]
statuses= ['VERIFIED']
anchors_empty= True
replay_disabled= True
```

This confirms the intended baseline behavior: each claim receives a
deterministic 1-based step, no AST anchors are created, and counterexamples
are not replayed.

## Compatibility fix applied

Gemini Developer API rejected the original Pydantic-generated schema because
it contained `additionalProperties`. The simple-claim request now uses a
provider-compatible bounded recursive expression schema in
[`src/astverifier/llm.py`](../src/astverifier/llm.py), while strict runtime
validation remains handled by the Pydantic model and QF-LIA whitelist
translator.

## Dataset-backed multi-program run

The approved dataset run covered 39 programs:

- 18 SV-COMP core loop programs: all `loop-simple` and `loop-invariants`
  files;
- 21 preserved CRUXEval Python samples.

The pipeline's QF-LIA subset gate rejected 37 programs before any Gemini
request. These samples contained malformed C-to-Python translations or
unsupported strings, lists, dictionaries, calls, methods, and loop forms. Two
programs passed the gate and reached Gemini:

| Dataset program | Claims | Verified | Counterexample | Translation error | Time |
|---|---:|---:|---:|---:|---:|
| `loop-invariants/eq2` | 1 | 0 | 1 | 0 | 7.83 s |
| `CRUXEval sample_51` | 1 | 0 | 0 | 1 | 4.14 s |
| **Total** | **2** | **0** | **1** | **1** | **12.00 s** |

After the compatibility change, every processed program has one explicit
program-level result:

| Overall status | Count |
|---|---:|
| Verified | 0 |
| Counterexample | 1 |
| Unsupported subset | 22 |
| Translation error | 16 |
| Unknown/timeout | 0 |
| **Total program results** | **39** |

Across all 39 processed programs, including subset rejections, the reported
token totals were 727 input and 103 output tokens. The estimated total cost at
the adapter's configured prices was `$0.005180`; reported usage metadata was
zero for rejected programs because they never reached Gemini. The aggregate
unsupported-or-translation-error rate over program results was `38/39 = 97.4%`.
Among the two programs that actually reached Gemini, one returned a
counterexample and one returned a translation error (`1/2 = 50%` translation
error rate).

For the 18 SV-COMP programs with available ground truth, all 18 now produce a
result record, but only one passed the subset gate. The remaining 17 are
explicit unsupported/translation-error results. The stored labels are all
`VERIFIED`, while no result was `VERIFIED`, giving accuracy `0.0`, recall
`0.0`, and F1 `0.0`. This is a subset-coverage diagnostic, not a
representative benchmark score.

The generated per-program results and summary are available at
[`results/UNANCHORED`](../results/UNANCHORED) and
[`dataset_smoke_summary.json`](../results/UNANCHORED/dataset_smoke_summary.json).

## Local verification

```text
25 passed in 1.18s
python -m compileall -q src tools tests  -> passed
git diff --check                         -> passed
```

## Interpretation and limitations

- The smoke test validates request formation, structured JSON decoding,
  QF-LIA translation, Z3 classification, step ordering, empty anchors, and
  disabled replay.
- The repeated malformed expression demonstrates ordinary LLM-output
  variability; it is surfaced as a translation error rather than hidden.
- The dataset run is primarily a subset-coverage diagnostic: most preserved
  SV-COMP and CRUXEval files are not valid Python/QF-LIA inputs for this
  pipeline.
- The reported ground-truth metrics are based on only one accepted labeled
  SV-COMP program and should not be treated as benchmark scores.
- Replay was disabled by design, so CRVR is not applicable.
