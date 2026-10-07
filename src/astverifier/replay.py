"""
replay.py - Counterexample Replay on real CPython (Phase 3).

Runs f(**inputs) in an isolated subprocess with a hard timeout.
NEVER trusts LLM output. Only runs the trusted Python function with
values copied from the Z3 counterexample.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ReplayResult:
    success: bool
    output_repr: str
    exit_code: int
    error: Optional[str]
    elapsed_ms: float


def _build_replay_script(source: str, func_name: str, inputs: Dict[str, int]) -> str:
    # Filter inputs to int only; raise if anything else sneaks in.
    safe_inputs = {k: int(v) for k, v in inputs.items()}
    payload = json.dumps(safe_inputs)
    return textwrap.dedent(
        f"""\
        import json, sys
        inputs = json.loads({payload!r})
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
        try:
            out = fn(**inputs)
            print("REPRO_OK:", repr(out))
            sys.exit(0)
        except Exception as e:
            print("REPRO_EXCEPTION:", repr(e), file=sys.stderr)
            sys.exit(5)
        """
    )


def replay_counterexample(
    source: str,
    func_name: str,
    inputs: Dict[str, int],
    timeout: float = 2.0,
) -> ReplayResult:
    """Run the function with `inputs` in a subprocess; report the result."""
    script = _build_replay_script(source, func_name, inputs)
    t0 = time.perf_counter()
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
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode == 0 and stdout.startswith("REPRO_OK:"):
            return ReplayResult(
                success=True,
                output_repr=stdout[len("REPRO_OK:"):].strip(),
                exit_code=proc.returncode,
                error=None,
                elapsed_ms=elapsed_ms,
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


__all__ = ["ReplayResult", "replay_counterexample"]