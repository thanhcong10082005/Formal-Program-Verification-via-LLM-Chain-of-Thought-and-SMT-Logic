"""Shared claim model and claim-pool loader.

Every method -- the Core Proposed Method and every Baseline -- consumes the
*same* claims, so that no method can be advantaged by extracting or wording
claims better than another.

The :class:`Claim` dataclass here is the canonical definition. The Core
Proposed Method's ``core.binding.Claim`` is a *different* type (it carries an
``ast.expr`` node and an anchor gate status); this module deliberately keeps
the two apart so that ``core/`` keeps ownership of its own binding logic,
while baselines and the shared tooling talk about the pool-level claim.

Run with::

    python -c "from astverifier.shared.claims import load_claim_pool; \\
        pool = load_claim_pool('data/bench_v1/claim_pool.json')"
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# The verdict vocabulary shared by the ground truth and the baselines.
VERDICT_VERIFIED = "VERIFIED"
VERDICT_COUNTEREXAMPLE = "COUNTEREXAMPLE"
VERDICT_UNREACHABLE = "UNREACHABLE"

#: The kinds a claim may have.
KIND_POSTCONDITION = "postcondition"
KIND_PRECONDITION = "precondition"
KIND_INVARIANT = "invariant"


@dataclass(frozen=True)
class Claim:
    """One verification claim, anchored to a source line.

    Attributes:
        claim_id: Stable identifier, unique inside a pool.
        program_id: Path of the program the claim belongs to, relative to the
            dataset root (for example ``loops/p00019.py``).
        text: The Python boolean expression, as source text.
        source_line: 1-based line the claim lives on.
        kind: One of ``postcondition``, ``precondition``, ``invariant``.
        ast_anchor: The stable :class:`~astverifier.shared.anchors.NodeId`
            text form, or ``None`` when the claim is not anchored.
        precondition: The path condition under which the claim is executed,
            or ``None``.
        provenance: Where the claim came from, e.g. ``assert@L9``.
        expected_verdict: The ground-truth verdict, when known.
        gt_confidence: The ground-truth confidence, when known.
    """

    claim_id: str
    program_id: str
    text: str
    source_line: int
    kind: str = KIND_POSTCONDITION
    ast_anchor: str | None = None
    precondition: str | None = None
    provenance: str = ""
    expected_verdict: str | None = None
    gt_confidence: str | None = None

    @property
    def is_anchored(self) -> bool:
        """True iff the claim carries an AST anchor."""
        return bool(self.ast_anchor)


def _claim_from_dict(payload: dict) -> Claim:
    """Build a :class:`Claim` from one claim-pool entry."""
    return Claim(
        claim_id=str(payload.get("claim_id", "")),
        program_id=str(payload.get("program_id", "")),
        text=str(payload.get("text", "")),
        source_line=int(payload.get("source_line", 0) or 0),
        kind=str(payload.get("kind", KIND_POSTCONDITION)),
        ast_anchor=payload.get("ast_anchor") or None,
        precondition=payload.get("precondition") or None,
        provenance=str(payload.get("provenance", "")),
        expected_verdict=payload.get("expected_verdict") or None,
        gt_confidence=payload.get("gt_confidence") or None,
    )


def load_claim_pool(path: str | Path) -> dict[str, list[Claim]]:
    """Load a claim pool and group its claims by program.

    Args:
        path: Path to ``claim_pool.json``.

    Returns:
        A mapping ``program_id -> list[Claim]``. Programs without claims are
        absent from the mapping.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload.get("claims", payload if isinstance(payload, list) else [])
    grouped: dict[str, list[Claim]] = {}
    for entry in entries:
        claim = _claim_from_dict(entry)
        grouped.setdefault(claim.program_id, []).append(claim)
    return grouped


def load_ground_truth(path: str | Path) -> dict[str, str]:
    """Load ``program_id -> expected_verdict`` from a manifest.

    The manifest produced by Phase 0.5 stores one entry per program with an
    ``expected_verdict`` field; programs without a claim carry no verdict.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    programs = payload.get("programs", payload if isinstance(payload, list) else [])
    truth: dict[str, str] = {}
    for program in programs:
        verdict = program.get("expected_verdict")
        if verdict:
            truth[program["path"]] = verdict
    return truth


__all__ = [
    "Claim",
    "KIND_INVARIANT",
    "KIND_POSTCONDITION",
    "KIND_PRECONDITION",
    "VERDICT_COUNTEREXAMPLE",
    "VERDICT_UNREACHABLE",
    "VERDICT_VERIFIED",
    "load_claim_pool",
    "load_ground_truth",
]
