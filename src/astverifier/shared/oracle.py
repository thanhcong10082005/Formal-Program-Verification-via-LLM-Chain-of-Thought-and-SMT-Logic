"""Tier 1 of the ground truth: the CPython execution oracle.

A finite oracle can only ever *falsify* a claim. It can never prove one.
This module therefore reports one of three observations, and never claims that
a claim is true:

* :data:`OBS_COUNTEREXAMPLE` -- some input made the assert fail.
* :data:`OBS_UNREACHABLE` -- the assert never executed on any input tried.
* :data:`OBS_NO_VIOLATION` -- the assert held on every input tried.

:class:`~astverifier.shared.prover.prove` turns the third observation into
a proof (or an explicit inconclusive verdict).

Execution happens in a **separate process** (:mod:`astverifier.shared.oracle_runner`)
because some programs in the dataset do not terminate -- for example
``while x < y: x = x + x`` with ``x = 0``. A hard timeout is the only
reliable way to stop them, and Windows has no ``SIGALRM``.

Every input tried is recorded in :attr:`OracleOutcome.tried`, so the oracle is
fully replayable.
"""

from __future__ import annotations

import ast
import itertools
import json
import random
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

OBS_COUNTEREXAMPLE = "COUNTEREXAMPLE"
OBS_UNREACHABLE = "UNREACHABLE"
OBS_NO_VIOLATION = "NO_VIOLATION"
OBS_ERROR = "ERROR"
OBS_TIMEOUT = "TIMEOUT"

# Boundary values dominate the observed violations, so they come first.
#
# The order matters: a program that loops ``while x != 0: x = x - 1``
# terminates for ``x >= 0`` but never for ``x < 0``. The *safe* values
# come first so that a non-terminating input is met late, after the
# program has already been explored on the inputs that do terminate.
EDGE_VALUES: tuple[int, ...] = (
    0, 1, -1, 2, -2, 3, 5, 8, 13, 21, 34, 55, 89,
)

SEED = 20261105
RANDOM_SAMPLES = 6
MAX_GRID = 4096

# The per-input execution budget, in seconds.
DEFAULT_INPUT_TIMEOUT = 2.0
# The whole-program budget, in seconds.
DEFAULT_PROGRAM_TIMEOUT = 30.0

# A program whose loop bound is large needs a much smaller input grid: one
# run costs O(bound) and the grid is the product of the parameter domains.
BIG_BOUND_THRESHOLD = 200
BIG_BOUND_GRID = 64


@dataclass
class OracleOutcome:
    """The result of running one program over the input grid."""

    observation: str
    witness: dict[str, int] | None = None
    inputs_tried: int = 0
    assert_reached: bool = False
    error: str | None = None
    tried: list[dict[str, int]] = field(default_factory=list)


def rewrite_asserts(source: str) -> str:
    """Replace every ``assert EXPR`` with ``_chk(EXPR)``.

    The rewrite keeps the line structure intact, so reported line numbers stay
    valid.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    targets = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    for lineno in targets:
        original = lines[lineno - 1]
        marker = "assert "
        index = original.index(marker)
        lines[lineno - 1] = (
            original[:index] + "_chk(" + original[index + len(marker):].rstrip() + ")"
        )
    return "\n".join(lines) + "\n"


def build_input_grid(
    params: list[str],
    *,
    seed: int = SEED,
    random_samples: int = RANDOM_SAMPLES,
    max_grid: int = MAX_GRID,
) -> list[dict[str, int]]:
    """Build a deterministic list of input dictionaries.

    The grid is the cartesian product of :data:`EDGE_VALUES` (capped so wide
    signatures stay bounded) followed by seeded random tuples. The seed is
    fixed, so two runs explore exactly the same inputs.

    The grid is a *search heuristic*, not a proof: it only has to be rich
    enough to find the violations that exist. Spacer, not the grid, decides
    what is actually true.
    """
    if not params:
        return [{}]

    rng = random.Random(seed)

    # A wide signature cannot be covered by a product of edge values, so
    # the edge values are capped harder as the arity grows.
    if len(params) <= 2:
        axes = [list(EDGE_VALUES) for _ in params]
    elif len(params) == 3:
        axes = [list(EDGE_VALUES[:8]), list(EDGE_VALUES[:8]), list(EDGE_VALUES[:6])]
    elif len(params) == 4:
        axes = [list(EDGE_VALUES[:5]) for _ in params]
    else:
        axes = [list(EDGE_VALUES[:4]) for _ in params]

    size = 1
    for axis in axes:
        size *= len(axis)
    if size > max_grid:
        # Fall back to a *diagonal* grid: instead of the full product,
        # walk the edge values in lockstep. Every individual edge value
        # still appears in every position, and the grid stays small.
        depth = max(len(axis) for axis in axes)
        axes = [list(EDGE_VALUES[: min(len(axis), depth)]) for axis in axes]
        size = 1
        for axis in axes:
            size *= len(axis)
        if size > max_grid:
            axes = [list(EDGE_VALUES[:4]) for _ in params]

    candidates: list[dict[str, int]] = []
    for combo in itertools.product(*axes):
        candidates.append(dict(zip(params, combo, strict=True)))

    # The uniform tuples come first, then the *diagonal* ones -- the
    # tuples in which every parameter takes the same value. The diagonal
    # is where most of the dataset's violations live (a program that
    # claims ``x >= y`` is broken exactly when ``x < y``), and it is
    # cheap to reach: one run per value instead of one per tuple.
    width = len(params)
    if width > 1:
        depth = max(len(axis) for axis in axes)
        for value in EDGE_VALUES[:depth]:
            candidates.append(dict.fromkeys(params, value))

    for _ in range(random_samples):
        candidates.append({p: rng.randint(-20, 20) for p in params})

    seen: set[tuple[int, ...]] = set()
    out: list[dict[str, int]] = []
    for candidate in candidates:
        key = tuple(candidate[p] for p in params)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _loop_bound(source: str) -> int:
    """Return the largest constant bound found in a loop test.

    A program with a large bound needs a small input grid, because one run
    costs O(bound) and the grid is the product of the parameter domains.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    largest = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, int):
                largest = max(largest, abs(sub.value))
    return largest


def _runner_env() -> dict[str, str]:
    """Build the environment for the sandbox subprocess.

    The package lives under ``src/``, which is not necessarily on the
    interpreter's path, so it is added explicitly.
    """
    import os

    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    parts = [package_root] if not existing else [package_root, existing]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _runner_command() -> list[str]:
    """Return the command that starts the sandbox.

    The runner is launched as a *script* rather than with ``-m``: importing
    the ``astverifier`` package pulls in Z3 and Pydantic, which costs more
    than a second per program. The runner itself only needs the standard
    library.
    """
    runner = Path(__file__).resolve().parent / "oracle_runner.py"
    return [sys.executable, "-S", str(runner)]


def replay_witness(
    source: str,
    func_name: str,
    witness: dict[str, int],
    *,
    timeout: float = DEFAULT_INPUT_TIMEOUT,
) -> bool:
    """Replay one input and report whether the claim really fails.

    The prover's model is an under-approximation of the reachable states,
    so a candidate counterexample only counts once real CPython confirms
    it. The replay runs in the sandbox, because the witness may drive the
    program into a non-terminating loop.
    """
    spec = {
        "source": source,
        "function_name": func_name,
        "grid": [dict(witness)],
        "per_input_timeout": timeout,
    }
    try:
        completed = subprocess.run(
            _runner_command(),
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            timeout=timeout + 10.0,
            cwd=str(Path(__file__).resolve().parents[3]),
            env=_runner_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False

    stdout = completed.stdout.strip()
    if not stdout:
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return payload.get("observation") == OBS_COUNTEREXAMPLE


def observe(
    source: str,
    func_name: str,
    params: list[str],
    *,
    grid: list[dict[str, int]] | None = None,
    input_timeout: float = DEFAULT_INPUT_TIMEOUT,
    program_timeout: float = DEFAULT_PROGRAM_TIMEOUT,
) -> OracleOutcome:
    """Execute ``source`` over the input grid in a sandbox and report.

    The program runs in a separate interpreter, so a non-terminating program
    is stopped by a hard timeout instead of hanging the pipeline.
    """
    if grid is None:
        # A program with a large loop bound needs a smaller grid: one run
        # costs O(bound).
        bound = _loop_bound(source)
        if bound >= BIG_BOUND_THRESHOLD:
            grid = build_input_grid(
                params, random_samples=2, max_grid=BIG_BOUND_GRID
            )
        else:
            grid = build_input_grid(params)

    spec = {
        "source": source,
        "function_name": func_name,
        "grid": grid,
        "per_input_timeout": input_timeout,
    }

    try:
        completed = subprocess.run(
            _runner_command(),
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            timeout=program_timeout,
            cwd=str(Path(__file__).resolve().parents[3]),
            env=_runner_env(),
        )
    except subprocess.TimeoutExpired:
        return OracleOutcome(
            observation=OBS_TIMEOUT,
            error=f"program budget of {program_timeout}s exhausted",
            inputs_tried=0,
        )
    except OSError as exc:
        return OracleOutcome(observation=OBS_ERROR, error=f"sandbox: {exc}")

    stdout = completed.stdout.strip()
    if not stdout:
        return OracleOutcome(
            observation=OBS_ERROR,
            error=f"sandbox produced no output (exit={completed.returncode})",
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return OracleOutcome(
            observation=OBS_ERROR,
            error=f"sandbox output is not JSON: {exc}",
        )

    observation = payload.get("observation", OBS_ERROR)
    witness = payload.get("witness")
    return OracleOutcome(
        observation=observation,
        witness=witness,
        inputs_tried=int(payload.get("inputs_tried", 0) or 0),
        assert_reached=bool(payload.get("assert_reached", False)),
        error=payload.get("error"),
    )


__all__ = [
    "DEFAULT_INPUT_TIMEOUT",
    "DEFAULT_PROGRAM_TIMEOUT",
    "EDGE_VALUES",
    "OBS_COUNTEREXAMPLE",
    "OBS_ERROR",
    "OBS_NO_VIOLATION",
    "OBS_TIMEOUT",
    "OBS_UNREACHABLE",
    "OracleOutcome",
    "build_input_grid",
    "observe",
    "rewrite_asserts",
]
