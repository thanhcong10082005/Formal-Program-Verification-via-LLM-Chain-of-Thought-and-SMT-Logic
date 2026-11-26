"""Slow integration tests over the 1,000-program dataset and Z3/Spacer prover.

These tests are separated from ``test_dataset_pipeline.py`` and tagged with
``@pytest.mark.slow`` so day-to-day development can run ``pytest -m "not slow"``
in a few seconds while full verification (``pytest`` or ``pytest -m slow``)
still exercises every counterexample replay, idempotency check, and Spacer
soundness invariant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.oracle import replay_witness  # noqa: E402
from astverifier.shared.structure import analyze  # noqa: E402

DATA = ROOT / "dataset"
DATASET = ROOT / "dataset"
MANIFEST = DATA / "manifest.json"

pytestmark = pytest.mark.slow


def _load(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} has not been generated yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _programs() -> list[dict]:
    manifest = _load(MANIFEST)
    return manifest["programs"]


def _claim_programs() -> list[dict]:
    return [p for p in _programs() if p.get("claims")]


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


def test_verified_programs_are_not_contradicted_by_reachability():
    """The exact reachability query agrees with the manifest's verdict."""
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
            continue
        result = prove_reachability(source, entry["claims"][0], tag="t")
        if result.verdict == VERDICT_UNREACHABLE:
            wrong.append(entry["path"])
    assert not wrong, f"{len(wrong)} reachable programs are unreachable"
