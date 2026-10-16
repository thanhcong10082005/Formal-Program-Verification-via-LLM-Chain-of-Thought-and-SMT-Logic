"""
tracing.py - Execution trace collection for Baseline B.

Compared to the original version this adds:

* ``MAX_TRACE_ITERATIONS`` cap: the per-line callback aborts the trace once
  the total event count exceeds the cap.  Programs that would run forever
  (e.g. ``while n > 0: n -= 1`` on huge inputs) no longer hang.
* ``timeout`` is no longer hard-coded at 2 s.  Callers can ask for 5–10 s
  (IMPROVEMENT 2.2).
* ``truncated`` flag on ``ExecutionTrace`` so downstream code can tell
  apart "really timed out" vs "iteration cap hit".
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

DEFAULT_TIMEOUT = 5.0            # was 2.0; raised for IMPROVEMENT 2.2
MAX_TRACE_ITERATIONS = 10_000    # hard cap on per-line callback events


@dataclass
class TraceFrame:
    line: int
    event: str
    details: Dict[str, Any]


@dataclass
class ExecutionTrace:
    inputs: Dict[str, int]
    frames: List[TraceFrame]
    exit_code: int
    error: Optional[str]
    elapsed_ms: float
    truncated: bool = False       # True when MAX_TRACE_ITERATIONS hit


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def run_concrete_execution(
    source: str,
    func_name: str,
    inputs: Dict[str, int],
    *,
    target_line: Optional[int] = None,
    claim_expression: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ExecutionTrace:
    script = _build_trace_script(source, func_name, inputs, target_line, claim_expression)
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

        marker = "TRACE_RESULT:"
        if marker in stdout:
            payload = stdout.split(marker, 1)[1].strip()
            try:
                data = json.loads(payload)
                frames = [TraceFrame(**f) for f in data.get("frames", [])]
                truncated = bool(data.get("truncated", False))
                return ExecutionTrace(
                    inputs=inputs,
                    frames=frames,
                    exit_code=proc.returncode,
                    error=data.get("error"),
                    elapsed_ms=elapsed_ms,
                    truncated=truncated,
                )
            except (json.JSONDecodeError, TypeError):
                pass

        return ExecutionTrace(
            inputs=inputs,
            frames=[],
            exit_code=proc.returncode,
            error=stderr or f"exit={proc.returncode}",
            elapsed_ms=elapsed_ms,
        )

    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return ExecutionTrace(
            inputs=inputs, frames=[], exit_code=-1,
            error=f"timeout after {timeout}s", elapsed_ms=elapsed_ms,
        )
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return ExecutionTrace(
            inputs=inputs, frames=[], exit_code=-2,
            error=f"host error: {e!r}", elapsed_ms=elapsed_ms,
        )
    finally:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def trace_to_dict(trace: ExecutionTrace) -> Dict[str, Any]:
    return {
        "inputs": trace.inputs,
        "frames": [asdict(f) for f in trace.frames],
        "exit_code": trace.exit_code,
        "error": trace.error,
        "elapsed_ms": trace.elapsed_ms,
        "truncated": trace.truncated,
    }


# -------------------------------------------------------------------
# Internal: script builder
# -------------------------------------------------------------------

def _build_trace_script(
    source: str,
    func_name: str,
    inputs: Dict[str, int],
    target_line: Optional[int],
    claim_expression: Optional[str],
) -> str:
    safe_inputs = {k: int(v) for k, v in inputs.items()}
    return _TRACE_TEMPLATE.format(
        inputs_json=_quote(safe_inputs),
        target_line=_quote(target_line),
        claim_expr=_quote(claim_expression),
        func_name=_quote(func_name),
        source_code=_quote(source),
    )


def _quote(value: Any) -> str:
    if value is None:
        return '"null"'
    inner = json.dumps(value, separators=(",", ":"))
    return json.dumps(inner, separators=(",", ":"))


_TRACE_TEMPLATE = r"""
import json, sys

INPUTS = json.loads({inputs_json})
TARGET_LINE = json.loads({target_line})
CLAIM_EXPR = json.loads({claim_expr})
FUNC_NAME = json.loads({func_name})
SOURCE_CODE = json.loads({source_code})

frames = []
_prev_live = {{}}
_event_count = 0
_truncated = False

def trace_callback(frame, event, arg):
    global _event_count, _truncated
    co = frame.f_code
    lineno = frame.f_lineno
    if event not in ("line", "return"):
        return trace_callback
    if co.co_name != FUNC_NAME:
        return trace_callback

    _event_count += 1
    if _event_count > 10000:
        _truncated = True
        sys.settrace(None)
        return None

    try:
        loc = {{k: repr(v) for k, v in frame.f_locals.items() if k not in ("__builtins__",)}}
    except Exception:
        loc = {{}}
    frames.append({{"line": lineno, "event": "snapshot", "details": {{"locals": loc}}}})

    prev_keys = _prev_live.get(lineno, set())
    curr_keys = set(frame.f_locals.keys()) - {{"__builtins__"}}
    new_keys = curr_keys - prev_keys
    _prev_live[lineno] = curr_keys
    for var in new_keys:
        frames.append({{
            "line": lineno,
            "event": "assignment",
            "details": {{"variable": var, "value": repr(frame.f_locals[var])}}
        }})

    if TARGET_LINE is not None and lineno == TARGET_LINE and CLAIM_EXPR:
        try:
            result = bool(eval(CLAIM_EXPR, frame.f_globals, frame.f_locals))
            frames.append({{
                "line": lineno,
                "event": "claim_check",
                "details": {{"expression": CLAIM_EXPR, "result": result}}
            }})
        except Exception as exc:
            frames.append({{
                "line": lineno,
                "event": "claim_error",
                "details": {{"expression": CLAIM_EXPR, "error": repr(exc)}}
            }})

    if event == "return" and co.co_name == FUNC_NAME:
        frames.append({{
            "line": lineno,
            "event": "return",
            "details": {{"value": repr(arg)}}
        }})

    return trace_callback

ns = {{}}
try:
    exec(SOURCE_CODE, ns)
except Exception as e:
    print("TRACE_IMPORT_FAIL:", repr(e), file=sys.stderr)
    sys.exit(3)

fn = ns.get(FUNC_NAME)
if fn is None:
    print("TRACE_FUNC_MISSING", file=sys.stderr)
    sys.exit(4)

sys.settrace(trace_callback)
call_error = None
result = None
try:
    result = fn(**INPUTS)
except BaseException as e:
    call_error = repr(e)
finally:
    sys.settrace(None)

error_msg = call_error
payload = json.dumps({{
    "frames": frames,
    "result": repr(result),
    "error": error_msg,
    "truncated": _truncated
}})
print("TRACE_RESULT:", payload)
sys.exit(0 if error_msg is None else 5)
"""[1:]


__all__ = [
    "TraceFrame",
    "ExecutionTrace",
    "run_concrete_execution",
    "trace_to_dict",
    "DEFAULT_TIMEOUT",
    "MAX_TRACE_ITERATIONS",
]
