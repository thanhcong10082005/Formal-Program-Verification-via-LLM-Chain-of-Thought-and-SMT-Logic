"""The sandboxed runner for the tier-1 execution oracle.

Run as::

    python -m astverifier.shared.oracle_runner

The runner reads a JSON specification from stdin and writes a JSON result to
stdout::

    {"observation": "...", "witness": {...}, "inputs_tried": 12, ...}

Why a separate process? Two reasons:

* **Termination.** Some programs in the dataset never terminate -- for
  example ``while x < y: x = x + x`` with ``x = 0``. A hard timeout is the
  only reliable way to stop them, and Windows has no ``SIGALRM``.
* **Isolation.** The program under test runs with its own globals; a crash
  cannot take the labeling pipeline down with it.

Within the runner, each input is executed on its own daemon thread with a
watchdog. A thread that overruns is abandoned (the interpreter cannot kill a
thread safely), and the runner reports ``TIMEOUT`` rather than guessing.
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import traceback
from typing import Any

OBS_COUNTEREXAMPLE = "COUNTEREXAMPLE"
OBS_UNREACHABLE = "UNREACHABLE"
OBS_NO_VIOLATION = "NO_VIOLATION"
OBS_ERROR = "ERROR"
OBS_TIMEOUT = "TIMEOUT"

# How many inputs may time out before the program is given up on. A
# program that does not terminate on a handful of inputs is still worth
# exploring on the others.
MAX_INPUT_TIMEOUTS = 8


class _ClaimFailedError(Exception):
    """Raised when the rewritten claim evaluates to false."""


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


def _run_one(function: Any, arguments: dict[str, int], state: dict) -> str:
    """Execute one input; returns ``"ok"``, ``"violation"`` or ``"error"``."""
    try:
        function(**arguments)
    except _ClaimFailedError:
        return "violation"
    except BaseException:  # noqa: BLE001 - any runtime error is not a violation
        return "error"
    return "ok"


def execute(spec: dict) -> dict:
    """Run the program described by ``spec`` and report what happened."""
    source = spec["source"]
    func_name = spec.get("function_name") or "main"
    grid = spec.get("grid") or [{}]
    per_input_timeout = float(spec.get("per_input_timeout", 2.0))

    state = {"reached": False}

    def _chk(value: object) -> None:
        state["reached"] = True
        if not value:
            raise _ClaimFailedError

    namespace: dict = {"_chk": _chk}
    try:
        exec(compile(rewrite_asserts(source), "<oracle>", "exec"), namespace)  # noqa: S102
    except Exception as exc:
        return {
            "observation": OBS_ERROR,
            "error": f"exec: {exc}",
            "inputs_tried": 0,
            "assert_reached": False,
        }

    function = namespace.get(func_name)
    if function is None or not callable(function):
        return {
            "observation": OBS_ERROR,
            "error": f"no function {func_name!r}",
            "inputs_tried": 0,
            "assert_reached": False,
        }

    tried = 0
    saw_error = False
    n_timeouts = 0

    for arguments in grid:
        tried += 1
        outcome = {"result": "error"}
        finished = threading.Event()
        # The worker closes over the loop variables, so they are bound
        # explicitly: without this, the worker would see the *last* value
        # of ``arguments`` when it finally runs.
        payload = arguments

        def worker(
            payload: dict[str, int] = payload,
            outcome: dict[str, str] = outcome,
            finished: threading.Event = finished,
        ) -> None:
            outcome["result"] = _run_one(function, payload, state)
            finished.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if not finished.wait(per_input_timeout):
            # The input did not finish in time. The program may not
            # terminate on it -- but that says nothing about the *other*
            # inputs, so the runner skips this one and carries on. Only
            # when the timeouts add up to the whole budget does the
            # program give up and report ``TIMEOUT``.
            n_timeouts += 1
            if n_timeouts >= MAX_INPUT_TIMEOUTS:
                return {
                    "observation": OBS_TIMEOUT,
                    "error": (
                        f"{n_timeouts} inputs exceeded {per_input_timeout}s"
                    ),
                    "inputs_tried": tried,
                    "assert_reached": state["reached"],
                    "witness": None,
                }
            continue
        if outcome["result"] == "violation":
            return {
                "observation": OBS_COUNTEREXAMPLE,
                "witness": dict(arguments),
                "inputs_tried": tried,
                "assert_reached": True,
                "error": None,
            }
        if outcome["result"] == "error":
            saw_error = True

    if n_timeouts:
        # Some inputs did not terminate. The program may or may not reach
        # its claim on the others, so the oracle refuses to label it.
        return {
            "observation": OBS_TIMEOUT,
            "error": f"{n_timeouts} inputs exceeded {per_input_timeout}s",
            "inputs_tried": tried,
            "assert_reached": state["reached"],
            "witness": None,
        }

    if not state["reached"]:
        observation = OBS_UNREACHABLE
    else:
        observation = OBS_NO_VIOLATION

    return {
        "observation": observation,
        "witness": None,
        "inputs_tried": tried,
        "assert_reached": state["reached"],
        "error": "runtime error on some inputs" if saw_error else None,
    }


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError) as exc:
        json.dump(
            {"observation": OBS_ERROR, "error": f"bad spec: {exc}"}, sys.stdout
        )
        return 1
    try:
        result = execute(spec)
    except BaseException:  # noqa: BLE001 - the runner must never crash silently
        result = {
            "observation": OBS_ERROR,
            "error": traceback.format_exc(limit=3),
            "inputs_tried": 0,
            "assert_reached": False,
        }
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
