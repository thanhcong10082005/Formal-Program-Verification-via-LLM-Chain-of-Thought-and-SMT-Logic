"""
replay.py - Counterexample Replay on real CPython (Phase 3 + Phase 1 extension).

Runs f(**inputs) in an isolated subprocess with a hard timeout and, when a
target is supplied, traces that line and evaluates the claim there.
NEVER trusts LLM output. Only runs the trusted Python function with
values copied from the Z3 counterexample.

Phase 1 extension: also captures failing_variable_state and failing_iteration_count
for use by the Retrospective Feedback Loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ReplayResult:
    success: bool
    output_repr: str
    exit_code: int
    error: Optional[str]
    elapsed_ms: float
    # Phase 1 extension: extra fields for the Feedback Loop
    failing_variable_state: Optional[Dict[str, int]] = None
    failing_iteration_count: int = 0
    trace_log: List[str] = field(default_factory=list)


def _build_replay_script(
    source: str,
    func_name: str,
    inputs: Dict[str, int],
    target_line: Optional[int] = None,
    claim_expression: Optional[str] = None,
    capture_state: bool = False,
) -> str:
    # Filter inputs to int only; raise if anything else sneaks in.
    safe_inputs = {k: int(v) for k, v in inputs.items()}
    payload = json.dumps(safe_inputs)
    target_payload = json.dumps(target_line)
    claim_payload = json.dumps(claim_expression)
    capture_flag = "True" if capture_state else "False"
    return textwrap.dedent(
        f"""\
        import json, sys
        inputs = json.loads({payload!r})
        target_line = json.loads({target_payload!r})
        claim_expression = json.loads({claim_payload!r})
        capture_state = {capture_flag}
        trace_state = {{
            "reached": False,
            "claim_false": False,
            "claim_error": None,
            "failing_state": {{}},
            "iteration": 0,
            "log": [],
        }}

        def trace(frame, event, arg):
            if (
                event == "line"
                and frame.f_code.co_name == {func_name!r}
            ):
                # Approximate iteration count: count line visits in the loop body
                # by tracking same-line revisits.
                if frame.f_lineno == target_line and target_line is not None:
                    trace_state["reached"] = True
                    if claim_expression:
                        try:
                            ok = bool(
                                eval(claim_expression, frame.f_globals, frame.f_locals)
                            )
                            if not ok:
                                trace_state["claim_false"] = True
                                if capture_state:
                                    # Capture integer/float variables in locals
                                    captured = {{}}
                                    for k, v in frame.f_locals.items():
                                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                                            try:
                                                captured[k] = int(v)
                                            except Exception:
                                                pass
                                    trace_state["failing_state"] = captured
                        except Exception as exc:
                            trace_state["claim_error"] = repr(exc)
                # Log every line for offline inspection
                if capture_state and len(trace_state["log"]) < 200:
                    trace_state["log"].append(str(frame.f_lineno))
            return trace

        try:
            src = {source!r}
        except Exception as e:
            print("REPRO_SRC_FAIL:", e, file=sys.stderr)
            sys.exit(2)
        ns = {{}}
        try:
            exec(src, ns)
        except Exception as e:
            print("REPRO_IMPORT_FAIL:", e, file=sys.stderr)
            sys.exit(3)
        fn = ns.get({func_name!r})
        if fn is None:
            print("REPRO_FUNC_MISSING", file=sys.stderr)
            sys.exit(4)
        sys.settrace(trace)
        out = None
        call_error = None
        try:
            out = fn(**inputs)
        except BaseException as e:
            call_error = repr(e)
        finally:
            sys.settrace(None)

        # Count iterations: number of times target_line was visited minus 1
        # (a single visit is not a "loop iteration").
        if target_line is not None:
            count = sum(1 for ln in trace_state["log"] if ln == str(target_line))
            trace_state["iteration"] = max(0, count - 1)

        targeted = target_line is not None or claim_expression is not None
        success = (
            (trace_state["reached"] and trace_state["claim_false"])
            if targeted
            else call_error is None
        )
        if success:
            print("REPRO_RESULT:", json.dumps({{
                "success": True,
                "output": repr(out),
                "failing_state": trace_state["failing_state"],
                "iteration": trace_state["iteration"],
                "log": trace_state["log"][:50],
            }}))
            sys.exit(0)
        if targeted and not trace_state["reached"]:
            error = "target line was not reached"
        elif targeted and trace_state["claim_error"]:
            error = "claim evaluation failed: " + trace_state["claim_error"]
        elif targeted:
            error = "target was reached but the claim was not false"
        else:
            error = call_error or "function did not return normally"
        print("REPRO_RESULT:", json.dumps({{
            "success": False,
            "output": repr(out),
            "error": error,
            "failing_state": trace_state["failing_state"],
            "iteration": trace_state["iteration"],
            "log": trace_state["log"][:50],
        }}))
        sys.exit(1)
        """
    )


def replay_counterexample(
    source: str,
    func_name: str,
    inputs: Dict[str, int],
    timeout: float = 2.0,
    *,
    target_line: Optional[int] = None,
    claim_expression: Optional[str] = None,
    capture_state: bool = False,
) -> ReplayResult:
    """Replay inputs and, when supplied, check a claim at its target line.

    When ``capture_state`` is True, the returned ReplayResult includes
    ``failing_variable_state``, ``failing_iteration_count`` and ``trace_log``
    for use by the Retrospective Feedback Loop.
    """
    script = _build_replay_script(
        source,
        func_name,
        inputs,
        target_line=target_line,
        claim_expression=claim_expression,
        capture_state=capture_state,
    )
    t0 = time.perf_counter()
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(script)
            tmp_path = fh.name
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        marker = "REPRO_RESULT:"
        if marker in stdout:
            payload = stdout.split(marker, 1)[1].strip()
            try:
                result = json.loads(payload)
            except json.JSONDecodeError:
                result = {}
            failing_state = result.get("failing_state") or None
            iter_count = int(result.get("iteration", 0) or 0)
            trace_log = list(result.get("log", []))
            if result.get("success") is True:
                return ReplayResult(
                    success=True,
                    output_repr=str(result.get("output", "")),
                    exit_code=proc.returncode,
                    error=None,
                    elapsed_ms=elapsed_ms,
                    failing_variable_state=failing_state,
                    failing_iteration_count=iter_count,
                    trace_log=trace_log,
                )
            return ReplayResult(
                success=False,
                output_repr=str(result.get("output", stdout)),
                exit_code=proc.returncode,
                error=result.get("error") or stderr or "replay did not reproduce the target violation",
                elapsed_ms=elapsed_ms,
                failing_variable_state=failing_state,
                failing_iteration_count=iter_count,
                trace_log=trace_log,
            )
        return ReplayResult(
            success=False,
            output_repr=stdout,
            exit_code=proc.returncode,
            error=stderr or f"exit={proc.returncode}",
            elapsed_ms=elapsed_ms,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return ReplayResult(
            success=False,
            output_repr="",
            exit_code=-1,
            error=f"timeout after {timeout}s",
            elapsed_ms=elapsed_ms,
        )
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return ReplayResult(
            success=False,
            output_repr="",
            exit_code=-2,
            error=f"host error: {e!r}",
            elapsed_ms=elapsed_ms,
        )
    finally:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


__all__ = ["ReplayResult", "replay_counterexample"]
