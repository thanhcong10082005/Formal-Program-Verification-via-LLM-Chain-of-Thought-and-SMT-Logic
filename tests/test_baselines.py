"""Contract tests for the 14 baselines.

Three properties are checked:

1. **Registration** -- the registry holds exactly the 14 expected ids.
2. **Independence** -- no baseline module imports :mod:`astverifier.core`.
   This is the invariant that keeps the comparison honest: the Core
   Proposed Method's machinery must not leak into a baseline.
3. **Contract** -- every baseline runs on one simple program and returns a
   valid :class:`ProgramResult` without crashing.

The contract test runs **offline**: the LLM-driven baselines are given a
:class:`~astverifier.baselines.StubClient`, so the suite never spends a
Gemini token.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.baselines import (  # noqa: E402
    EXPECTED_IDS,
    BaseBaseline,
    ProgramSpec,
    StubClient,
    all_baselines,
    get_baseline,
    registered_ids,
)
from astverifier.shared.claims import Claim  # noqa: E402
from astverifier.shared.schemas import (  # noqa: E402
    ProgramResult,
    StatusEnum,
)

BASELINE_DIR = ROOT / "src" / "astverifier" / "baselines"

#: A simple program with a loop and a claim, taken from the dataset's shape.
SAMPLE_SOURCE = """\
def count_steps(n: int) -> int:
    total = 0
    i = 0
    while i < n:
        total = total + i
        i = i + 1
    assert total >= 0
    return total
"""

#: The claim of :data:`SAMPLE_SOURCE`.
SAMPLE_CLAIM_TEXT = "total >= 0"


@pytest.fixture(scope="module")
def sample_spec() -> ProgramSpec:
    """A one-program specification every baseline is run against."""
    claim = Claim(
        claim_id="loops/p00000.py::post_1",
        program_id="loops/p00000.py",
        text=SAMPLE_CLAIM_TEXT,
        source_line=8,
        ast_anchor="count_steps@Assert:L8:C4#0",
        precondition=None,
        provenance="assert@L8",
        expected_verdict="VERIFIED",
        gt_confidence="high",
    )
    return ProgramSpec(
        program_id="loops/p00000.py",
        source=SAMPLE_SOURCE,
        source_path="loops/p00000.py",
        claims=[claim],
    )


def _stub_client() -> StubClient:
    """A deterministic offline stand-in for the language model."""
    return StubClient(
        json.dumps(
            {
                "verdict": "VERIFIED",
                "reason": "the stub client cannot find a violation",
                "smt": "(declare-fun total () Int)\n(assert (= total 0))\n",
                "claim": "(>= total 0)",
                "invariants": ["i >= 0"],
                "valid": True,
                "pre": "n >= 0",
                "post": "total >= 0",
                "inv": "i >= 0",
                "inputs": [],
            }
        )
    )


# --------------------------------------------------------------------------
# 1. Registration
# --------------------------------------------------------------------------


def test_all_baselines_registered() -> None:
    """The registry holds exactly the 14 expected baseline ids."""
    assert registered_ids() == sorted(EXPECTED_IDS)
    assert len(all_baselines()) == 14

    for baseline_id in EXPECTED_IDS:
        cls = get_baseline(baseline_id)
        assert issubclass(cls, BaseBaseline)
        assert cls.id == baseline_id
        assert cls.name, f"baseline {baseline_id} has no name"


def test_registered_ids_are_unique() -> None:
    """No two baselines share an id."""
    ids = [cls.id for cls in all_baselines()]
    assert len(ids) == len(set(ids))


def test_every_baseline_declares_its_capabilities() -> None:
    """Every baseline declares the four capability flags.

    The flags are what turn "not measurable" into ``N/A`` instead of ``0``
    in the metric table, so they must be explicit rather than defaulted.
    """
    for cls in all_baselines():
        for flag in (
            "supports_grounding",
            "supports_replay",
            "supports_reachability",
            "supports_precondition",
            "requires_llm",
        ):
            assert isinstance(getattr(cls, flag), bool), (
                f"{cls.id}.{flag} must be a bool"
            )


# --------------------------------------------------------------------------
# 2. Independence from core/
# --------------------------------------------------------------------------


def test_no_baseline_imports_core() -> None:
    """No baseline module imports :mod:`astverifier.core`.

    The comparison is only meaningful when the baselines are independent
    implementations, so this invariant is enforced by the test suite rather
    than by convention.
    """
    offenders: list[str] = []

    for path in sorted(BASELINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _imports_core(alias.name):
                        offenders.append(f"{path.name}:{node.lineno}: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level and node.level > 0:
                    # A relative import inside baselines/ can never reach core/.
                    continue
                if _imports_core(module):
                    offenders.append(f"{path.name}:{node.lineno}: {module}")

    assert not offenders, "baselines must not import from core/: " + ", ".join(offenders)


def _imports_core(module: str) -> bool:
    """True iff ``module`` refers to :mod:`astverifier.core`."""
    return (
        module == "astverifier.core"
        or module.startswith("astverifier.core.")
        or module == "core"
        or module.startswith("core.")
    )


def test_no_baseline_imports_core_at_runtime() -> None:
    """The imported modules of the baseline package exclude ``core``."""
    import astverifier.baselines as package

    package_dir = Path(package.__file__).parent
    for path in sorted(package_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "from ..core" not in source, f"{path.name} imports from core"
        assert "from astverifier.core" not in source, f"{path.name} imports core"


# --------------------------------------------------------------------------
# 3. Contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("baseline_id", sorted(EXPECTED_IDS))
def test_baselines_contract(baseline_id: str, sample_spec: ProgramSpec) -> None:
    """Every baseline returns a valid ``ProgramResult`` on one program."""
    cls = get_baseline(baseline_id)
    instance = cls(llm=_stub_client()) if cls.requires_llm else cls()

    result = instance.run(sample_spec)

    assert isinstance(result, ProgramResult), f"{baseline_id} did not return a ProgramResult"
    assert result.program_id == sample_spec.program_id
    assert result.baseline == baseline_id
    assert result.n_claims == len(sample_spec.claims)
    assert len(result.claim_results) == len(sample_spec.claims)
    assert result.elapsed_seconds >= 0.0

    for claim_result in result.claim_results:
        assert isinstance(claim_result.status, StatusEnum)
        assert claim_result.claim_id == sample_spec.claims[0].claim_id
        assert claim_result.claim_text == SAMPLE_CLAIM_TEXT
        assert isinstance(claim_result.anchor.has_grounding, bool)
        # A baseline without an anchor gate must not claim grounding.
        if not instance.supports_grounding:
            assert claim_result.anchor.has_grounding is False

    # The counts must agree with the per-claim results.
    counts = {
        StatusEnum.VERIFIED: result.n_verified,
        StatusEnum.COUNTEREXAMPLE: result.n_counterexample,
        StatusEnum.UNREACHABLE: result.n_unreachable,
        StatusEnum.UNSUPPORTED: result.n_unsupported,
        StatusEnum.TRANSLATION_ERROR: result.n_translation_error,
        StatusEnum.UNKNOWN_TIMEOUT: result.n_unknown_timeout,
    }
    for status, count in counts.items():
        observed = sum(
            1 for cr in result.claim_results if cr.status == status
        )
        assert observed == count, f"{baseline_id}: {status} count mismatch"


@pytest.mark.parametrize("baseline_id", sorted(EXPECTED_IDS))
def test_baselines_survive_a_broken_program(baseline_id: str) -> None:
    """A baseline reports a failure inside the result instead of crashing."""
    cls = get_baseline(baseline_id)
    instance = cls(llm=_stub_client()) if cls.requires_llm else cls()

    spec = ProgramSpec(
        program_id="broken/p00000.py",
        source="def f(:\n    this is not python",
        source_path="broken/p00000.py",
        claims=[
            Claim(
                claim_id="broken/p00000.py::post_1",
                program_id="broken/p00000.py",
                text="x >= 0",
                source_line=1,
            )
        ],
    )

    result = instance.run(spec)
    assert isinstance(result, ProgramResult)
    assert len(result.claim_results) == 1


@pytest.mark.parametrize("baseline_id", sorted(EXPECTED_IDS))
def test_baselines_handle_an_empty_claim_list(baseline_id: str) -> None:
    """A baseline copes with a program that has no claims."""
    cls = get_baseline(baseline_id)
    instance = cls(llm=_stub_client()) if cls.requires_llm else cls()

    spec = ProgramSpec(
        program_id="empty/p00000.py",
        source=SAMPLE_SOURCE,
        source_path="empty/p00000.py",
        claims=[],
    )

    result = instance.run(spec)
    assert isinstance(result, ProgramResult)
    assert result.n_claims == 0
    assert result.claim_results == []


def test_offline_baselines_need_no_llm(sample_spec: ProgramSpec) -> None:
    """The three offline baselines run without any LLM client."""
    offline = [cls for cls in all_baselines() if not cls.requires_llm]
    assert {cls.id for cls in offline} == {"J", "K", "L"}

    for cls in offline:
        result = cls().run(sample_spec)
        assert isinstance(result, ProgramResult)
        assert result.total_tokens_in == 0
        assert result.total_tokens_out == 0


def test_llm_baselines_report_their_token_usage(sample_spec: ProgramSpec) -> None:
    """An LLM baseline accounts for the tokens it spent."""
    llm_baselines = [cls for cls in all_baselines() if cls.requires_llm]
    assert len(llm_baselines) == 11

    for cls in llm_baselines:
        result = cls(llm=_stub_client()).run(sample_spec)
        assert result.total_tokens_in > 0, f"{cls.id} did not account for its tokens"
        assert result.total_tokens_out > 0, f"{cls.id} did not account for its tokens"


def test_reachability_is_only_claimed_by_the_symbolic_baseline() -> None:
    """Only the symbolic-execution baseline can decide reachability."""
    with_reachability = {
        cls.id for cls in all_baselines() if cls.supports_reachability
    }
    assert with_reachability == {"L"}
