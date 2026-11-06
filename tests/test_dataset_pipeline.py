"""Phase 0.5.5 -- Tests for the dataset pipeline.

The tests check the four properties the benchmark depends on:

1. **Schema conformity** -- every label is one of the four allowed values,
   and every counterexample carries a reproducible witness.
2. **Reproducibility** -- a counterexample's witness still fails the claim
   on real CPython, and the pipeline is idempotent.
3. **Sample quality** -- the sample has exactly 1,000 programs, covers
   every claim form, has balanced verdicts and no structural duplicates.
4. **Claim-pool integrity** -- every claim in the pool matches an ``assert``
   in its source file and resolves to a valid AST anchor.

The tests run against the artefacts in ``data/bench_v1``. If those files
are missing, the tests skip rather than fail, so the suite stays usable
before the pipeline has been run.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.hashing import fingerprint  # noqa: E402
from astverifier.shared.oracle import replay_witness  # noqa: E402
from astverifier.shared.structure import analyze  # noqa: E402

DATA = ROOT / "data" / "bench_v1"
DATASET = ROOT.parent / "Dataset" / "filtered"

MANIFEST = DATA / "manifest.json"
SAMPLE = DATA / "sample_1000.txt"
REPORT = DATA / "sampling_report.json"
POOL = DATA / "claim_pool.json"

VALID_VERDICTS = {"VERIFIED", "COUNTEREXAMPLE", "UNREACHABLE", "UNKNOWN"}
VALID_CONFIDENCE = {"high", "medium", "low"}

# The sample size the plan fixes.
SAMPLE_SIZE = 1000

# The number of distinct claim forms the dataset has.
#
# The dataset holds 36 distinct claim *texts*, but only 34 can be
# represented in the sample: two of them --
# ``not val < 0 or _contract_result == -val`` and
# ``not val >= 0 or _contract_result == val`` -- live exclusively in
# ``conditionals/p00008.py``, which asserts two different expressions and
# therefore cannot be scored against a single ground-truth verdict. The
# plan drops that program on purpose (Phase 0.5.3, rule 3.1).
N_CLAIM_FORMS = 36
N_REPRESENTABLE_CLAIM_FORMS = 34


def _load(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} has not been generated yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _programs() -> list[dict]:
    manifest = _load(MANIFEST)
    return manifest["programs"]


def _claim_programs() -> list[dict]:
    return [p for p in _programs() if p.get("claims")]


def _sample_paths() -> list[str]:
    if not SAMPLE.exists():
        pytest.skip("sample_1000.txt has not been generated yet")
    return [
        line.strip()
        for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# 1. Ground-truth labels
# ---------------------------------------------------------------------------


def test_gt_labels_are_valid():
    """Every claim-carrying program has a valid label."""
    programs = _claim_programs()
    assert programs, "the manifest has no claim-carrying programs"

    for entry in programs:
        verdict = entry.get("expected_verdict")
        assert verdict in VALID_VERDICTS, f"{entry['path']}: {verdict!r}"
        assert entry.get("gt_confidence") in VALID_CONFIDENCE, entry["path"]
        assert entry.get("gt_source"), entry["path"]


def test_every_claim_program_is_labeled():
    """No claim-carrying program is left unlabeled."""
    unlabeled = [
        e["path"]
        for e in _claim_programs()
        if not e.get("expected_verdict")
    ]
    assert not unlabeled, f"{len(unlabeled)} programs are unlabeled"


def test_unknown_rate_is_within_budget():
    """The unknown rate stays under 10%."""
    programs = _claim_programs()
    unknown = sum(1 for e in programs if e["expected_verdict"] == "UNKNOWN")
    assert unknown / len(programs) <= 0.10, f"unknown rate {unknown/len(programs):.2%}"


def test_ambiguous_rate_is_within_budget():
    """The ambiguous rate stays under 25%."""
    programs = _claim_programs()
    ambiguous = sum(1 for e in programs if e.get("ambiguous"))
    assert (
        ambiguous / len(programs) <= 0.25
    ), f"ambiguous rate {ambiguous/len(programs):.2%}"


def test_counterexamples_carry_a_witness():
    """Every counterexample carries the input that produced it."""
    for entry in _claim_programs():
        if entry["expected_verdict"] != "COUNTEREXAMPLE":
            continue
        assert entry.get("counterexample_input"), entry["path"]


def test_counterexamples_replay():
    """Replaying a counterexample's witness fails the claim again."""
    counterexamples = [
        e
        for e in _claim_programs()
        if e["expected_verdict"] == "COUNTEREXAMPLE"
    ]
    if not counterexamples:
        pytest.skip("no counterexample in the manifest")

    # A representative subset keeps the test fast; every label is still
    # checked by ``test_all_counterexamples_replay`` when it is enabled.
    for entry in counterexamples[:60]:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        func = entry.get("function_name") or "main"
        confirmed = replay_witness(
            source, func, entry["counterexample_input"]
        )
        assert confirmed, f"{entry['path']}: the witness no longer fails"


def test_all_counterexamples_replay():
    """Every counterexample's witness fails the claim again."""
    counterexamples = [
        e
        for e in _claim_programs()
        if e["expected_verdict"] == "COUNTEREXAMPLE"
    ]
    failures = []
    for entry in counterexamples:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        func = entry.get("function_name") or "main"
        if not replay_witness(source, func, entry["counterexample_input"]):
            failures.append(entry["path"])
    assert not failures, f"{len(failures)} counterexamples did not replay"


# ---------------------------------------------------------------------------
# 2. Preconditions
# ---------------------------------------------------------------------------


def test_precondition_parses():
    """At least 99% of the preconditions parse as Python expressions."""
    programs = _claim_programs()
    with_pre = [e for e in programs if e.get("precondition")]
    if not with_pre:
        pytest.skip("no precondition in the manifest")

    good = 0
    for entry in with_pre:
        try:
            ast.parse(entry["precondition"], mode="eval")
            good += 1
        except SyntaxError:
            pass
    assert good / len(with_pre) >= 0.99, f"{good}/{len(with_pre)} parse"


def test_precondition_matches_guard():
    """The precondition matches the program's actual guard conditions.

    The check is mechanical: the precondition must be the conjunction of the
    conditions under which the assert is reached, computed independently
    from the structural analysis.
    """
    from astverifier.shared.preconditions import extract

    programs = _claim_programs()
    guarded = [e for e in programs if e.get("precondition_guarded")]
    if not guarded:
        pytest.skip("no guarded program in the manifest")

    mismatches = []
    for entry in guarded:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        outcome = extract(source)
        if outcome.precondition != entry.get("precondition"):
            mismatches.append(entry["path"])
    assert not mismatches, f"{len(mismatches)} preconditions disagree"


def test_precondition_covers_guarded_programs():
    """Every program whose assert sits in a branch has a precondition."""
    programs = _claim_programs()
    missing = []
    for entry in programs:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        shape = analyze(source)
        if not shape.sites:
            continue
        if shape.sites[0].in_branch is None:
            continue
        if not entry.get("precondition"):
            missing.append(entry["path"])
    assert not missing, f"{len(missing)} guarded programs have no precondition"


# ---------------------------------------------------------------------------
# 3. The stratified sample
# ---------------------------------------------------------------------------


def test_sample_is_exactly_1000():
    """The sample has exactly 1,000 programs."""
    paths = _sample_paths()
    assert len(paths) == SAMPLE_SIZE, f"{len(paths)} programs"
    assert len(set(paths)) == SAMPLE_SIZE, "the sample contains duplicates"


def test_sample_covers_all_claims():
    """The sample covers every representable claim form.

    Thirty-four of the dataset's 36 claim texts are representable; the
    other two live only in the multi-claim program that Phase 0.5.3
    drops. The sampling report names the ones it could not represent, so
    the gap is documented rather than silent.
    """
    paths = set(_sample_paths())
    by_path = {e["path"]: e for e in _programs()}
    claims = {
        by_path[path]["claims"][0]
        for path in paths
        if path in by_path and by_path[path].get("claims")
    }
    assert len(claims) == N_REPRESENTABLE_CLAIM_FORMS, (
        f"{len(claims)}/{N_REPRESENTABLE_CLAIM_FORMS} claim forms"
    )


def test_claim_forms_not_representable_are_documented():
    """The claim texts the sample cannot cover are named in the report."""
    report = _load(REPORT)
    assert report["n_distinct_claim_texts_in_dataset"] == N_CLAIM_FORMS
    missing = report["claim_texts_not_representable"]
    assert len(missing) == N_CLAIM_FORMS - N_REPRESENTABLE_CLAIM_FORMS
    assert missing == [
        "not val < 0 or _contract_result == -val",
        "not val >= 0 or _contract_result == val",
    ]


def test_sample_verdict_balanced():
    """Each verdict class holds between 40% and 60% of the sample."""
    paths = set(_sample_paths())
    by_path = {e["path"]: e for e in _programs()}
    counts = Counter(
        by_path[path]["expected_verdict"]
        for path in paths
        if path in by_path
    )
    total = sum(counts.values())
    assert total == SAMPLE_SIZE
    for verdict, count in counts.items():
        share = count / total
        assert 0.40 <= share <= 0.60, f"{verdict}: {share:.2%}"


def test_sample_deduped():
    """No two sampled programs share an AST-normalized hash."""
    paths = _sample_paths()
    hashes = []
    for path in paths:
        source = (DATASET / path).read_text(encoding="utf-8")
        hashes.append(fingerprint(source).ast_hash)
    duplicates = [h for h, n in Counter(hashes).items() if n > 1]
    assert not duplicates, f"{len(duplicates)} duplicate AST hashes"


def test_sample_paths_exist():
    """Every sampled path exists in the dataset."""
    for path in _sample_paths():
        assert (DATASET / path).exists(), path


def test_sampling_report_is_complete():
    """The report records the counts, the reasons and the distribution."""
    report = _load(REPORT)
    assert report["selected_size"] == SAMPLE_SIZE
    assert report["n_claim_groups_covered"] == N_REPRESENTABLE_CLAIM_FORMS
    assert report["n_dropped"], "the report lists no dropped programs"
    assert report["by_verdict"]
    assert report["claim_groups"]


def test_sampling_is_deterministic():
    """Running the selector again yields the same sample."""
    paths = _sample_paths()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "select_stratified_sample.py"),
            "--manifest",
            str(MANIFEST),
            "--dataset",
            str(DATASET),
            "--out-dir",
            str(ROOT / "data" / "bench_v1" / "_determinism_check"),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    check = ROOT / "data" / "bench_v1" / "_determinism_check" / "sample_1000.txt"
    try:
        again = [
            line.strip()
            for line in check.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert again == paths, "the sample is not deterministic"
    finally:
        for leftover in check.parent.glob("*"):
            leftover.unlink()
        check.parent.rmdir()


# ---------------------------------------------------------------------------
# 4. The shared claim pool
# ---------------------------------------------------------------------------


def test_claim_pool_covers_the_sample():
    """Every sampled program contributes at least one claim."""
    pool = _load(POOL)
    covered = {c["program_id"] for c in pool["claims"]}
    assert covered == set(_sample_paths())
    assert pool["generator"]["tokens"] == 0


def test_claim_matches_assert():
    """Every claim in the pool matches an ``assert`` in its source file."""
    pool = _load(POOL)
    mismatches = []
    for claim in pool["claims"]:
        source = (DATASET / claim["program_id"]).read_text(encoding="utf-8")
        tree = ast.parse(source)
        texts = {
            ast.unparse(node.test)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        }
        if claim["text"] not in texts:
            mismatches.append(claim["claim_id"])
    assert not mismatches, f"{len(mismatches)} claims do not match an assert"


def test_claim_anchor_resolves():
    """Every claim's AST anchor resolves to its assert node."""
    from astverifier.shared.anchors import assign_ids

    pool = _load(POOL)
    broken = []
    for claim in pool["claims"]:
        source = (DATASET / claim["program_id"]).read_text(encoding="utf-8")
        tree = ast.parse(source)
        ids = assign_ids(tree)
        strings = {node_id.as_string() for node_id in ids.values()}
        if claim["ast_anchor"] not in strings:
            broken.append(claim["claim_id"])
    assert not broken, f"{len(broken)} anchors do not resolve"


def test_claim_pool_has_preconditions():
    """Every claim carries a precondition field, even when it is null."""
    pool = _load(POOL)
    for claim in pool["claims"]:
        assert "precondition" in claim, claim["claim_id"]


def test_claim_pool_carries_ground_truth():
    """Every claim carries the ground-truth verdict."""
    pool = _load(POOL)
    for claim in pool["claims"]:
        assert claim["expected_verdict"] in VALID_VERDICTS, claim["claim_id"]


# ---------------------------------------------------------------------------
# 5. Idempotency
# ---------------------------------------------------------------------------


def test_pipeline_idempotent():
    """Re-running the labelling does not change any label."""
    if not MANIFEST.exists():
        pytest.skip("the manifest has not been generated yet")

    before = {
        e["path"]: (
            e.get("expected_verdict"),
            e.get("gt_confidence"),
            e.get("gt_source"),
        )
        for e in _programs()
        if e.get("expected_verdict")
    }
    if not before:
        pytest.skip("nothing has been labelled yet")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "build_ground_truth.py"),
            "--dataset",
            str(DATASET),
            "--out",
            str(MANIFEST),
            "--workers",
            "2",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr

    after = {
        e["path"]: (
            e.get("expected_verdict"),
            e.get("gt_confidence"),
            e.get("gt_source"),
        )
        for e in _programs()
        if e.get("expected_verdict")
    }
    assert after == before, "the labels changed on a re-run"


# ---------------------------------------------------------------------------
# 6. The prover's own soundness
# ---------------------------------------------------------------------------


def test_prover_agrees_with_execution():
    """The prover never proves a claim that execution contradicts."""
    from astverifier.shared.oracle import observe
    from astverifier.shared.prover import prove

    programs = _claim_programs()[:40]
    if not programs:
        pytest.skip("no programs to check")

    for entry in programs:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        func = entry.get("function_name") or "main"
        params = analyze(source).params

        proof = prove(source, entry["claims"][0], tag="t")
        if proof.verdict != "VERIFIED":
            continue
        outcome = observe(source, func, list(params))
        assert outcome.observation != "COUNTEREXAMPLE", (
            f"{entry['path']}: the prover says VERIFIED but execution found a "
            f"counterexample"
        )


def test_prover_never_guesses():
    """The prover only ever returns one of its four verdicts."""
    from astverifier.shared.prover import (
        VERDICT_COUNTEREXAMPLE,
        VERDICT_INCONCLUSIVE,
        VERDICT_UNREACHABLE,
        VERDICT_VERIFIED,
        prove,
    )

    allowed = {
        VERDICT_VERIFIED,
        VERDICT_COUNTEREXAMPLE,
        VERDICT_UNREACHABLE,
        VERDICT_INCONCLUSIVE,
    }
    for entry in _claim_programs()[:40]:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        proof = prove(source, entry["claims"][0], tag="t")
        assert proof.verdict in allowed, proof.verdict


# ---------------------------------------------------------------------------
# 7. The exact reachability decision
# ---------------------------------------------------------------------------


def test_reachability_matches_the_unreachable_label():
    """The exact reachability query agrees with the UNREACHABLE label.

    The labelling pipeline may only write ``UNREACHABLE`` when the exact
    Datalog reachability query confirms it. This test re-runs that query
    on a sample of the manifest's UNREACHABLE programs and checks that
    none of them is in fact reachable.
    """
    from astverifier.shared.prover import (
        VERDICT_REACHABLE,
        prove_reachability,
    )

    unreachable = [
        e for e in _claim_programs() if e["expected_verdict"] == "UNREACHABLE"
    ]
    if not unreachable:
        pytest.skip("no unreachable program in the manifest")

    wrong = []
    for entry in unreachable[:30]:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        result = prove_reachability(source, entry["claims"][0], tag="t")
        if result.verdict == VERDICT_REACHABLE:
            wrong.append(entry["path"])
    assert not wrong, f"{len(wrong)} unreachable programs are reachable"


def test_reachability_verdict_is_exact():
    """The reachability query never reports a verdict it cannot back."""
    from astverifier.shared.prover import (
        VERDICT_INCONCLUSIVE,
        VERDICT_REACHABLE,
        VERDICT_UNREACHABLE,
        prove_reachability,
    )

    allowed = {VERDICT_REACHABLE, VERDICT_UNREACHABLE, VERDICT_INCONCLUSIVE}
    for entry in _claim_programs()[:30]:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        result = prove_reachability(source, entry["claims"][0], tag="t")
        assert result.verdict in allowed, result.verdict


def test_verified_programs_are_not_contradicted_by_reachability():
    """The exact reachability query agrees with the manifest's verdict.

    The execution oracle and the exact query must not contradict each
    other. A program the oracle *reached* -- one whose assert actually
    ran -- is necessarily reachable, so the query must not call it
    unreachable.

    Note that the converse does not hold: a program whose assert never
    runs is labelled ``UNREACHABLE``, but a program that *is* reachable
    can still be ``VERIFIED`` (the claim holds) or ``COUNTEREXAMPLE``.
    """
    from astverifier.shared.oracle import observe
    from astverifier.shared.prover import (
        VERDICT_UNREACHABLE,
        prove_reachability,
    )

    candidates = [
        e
        for e in _claim_programs()
        if e["expected_verdict"] in ("VERIFIED", "COUNTEREXAMPLE")
    ]
    if not candidates:
        pytest.skip("no verified program in the manifest")

    wrong = []
    for entry in candidates[:30]:
        source = (DATASET / entry["path"]).read_text(encoding="utf-8")
        func = entry.get("function_name") or "main"
        params = analyze(source).params
        outcome = observe(source, func, list(params))
        if not outcome.assert_reached:
            # The assert never ran, so the oracle says nothing about
            # reachability.
            continue
        result = prove_reachability(source, entry["claims"][0], tag="t")
        if result.verdict == VERDICT_UNREACHABLE:
            wrong.append(entry["path"])
    assert not wrong, f"{len(wrong)} reachable programs are unreachable"
