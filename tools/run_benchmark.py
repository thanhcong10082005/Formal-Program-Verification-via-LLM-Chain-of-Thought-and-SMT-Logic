"""
run_benchmark.py - Run the framework over SV-COMP loops + a CRUXEval subset,
collect ProgramResults for both baselines, and emit per-program JSON files.

The default run is deterministic and uses source assertions. Pass
``--use-llm`` to make the claim-generation step call Gemini explicitly.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.classify import ProgramResult
from astverifier.llm import LLMResult, generate_cot_and_claims
from astverifier.pipeline import run_pipeline


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = (PROJECT_ROOT.parent / "Dataset") if (PROJECT_ROOT.parent / "Dataset").exists() else (PROJECT_ROOT / "Dataset")
PY_LOOPS_ROOT = DATASET_ROOT / "sv-benchmarks-loops-py"
CRUXEVAL_JSONL = DATASET_ROOT / "cruxeval-main" / "data" / "cruxeval.jsonl"
RESULT_ROOT = PROJECT_ROOT / "results"

BASELINES = ("AST_ANCHORED", "UNANCHORED")


# --- Ground-truth labelling -------------------------------------------------

# By reading the .c originals we manually tagged each program as expecting:
#   "VERIFIED"        : the property holds (no reachable counterexample)
#   "COUNTEREXAMPLE"  : the property fails (a violating model exists)
# When unlabelled, we record "" and skip FDR computation for that program.
GROUND_TRUTH: dict[str, str] = {
    # loop-simple
    "loop-simple/nested_1": "VERIFIED",          # a ends at 6 after the for loop
    "loop-simple/nested_1b": "VERIFIED",
    "loop-simple/nested_2": "VERIFIED",
    "loop-simple/nested_3": "VERIFIED",
    "loop-simple/nested_4": "VERIFIED",
    "loop-simple/nested_5": "VERIFIED",
    "loop-simple/nested_6": "VERIFIED",
    "loop-simple/deep-nested": "VERIFIED",
    # loop-invariants (mostly VERIFIED; const.c trivially satisfies)
    "loop-invariants/const": "VERIFIED",
    "loop-invariants/eq1": "VERIFIED",
    "loop-invariants/eq2": "VERIFIED",
    "loop-invariants/even": "VERIFIED",
    "loop-invariants/odd": "VERIFIED",
    "loop-invariants/mod4": "VERIFIED",
    "loop-invariants/bin-suffix-5": "VERIFIED",
    "loop-invariants/linear-inequality-inv-a": "VERIFIED",
    "loop-invariants/linear-inequality-inv-b": "VERIFIED",
    "loop-invariants/linear-inequality-inv-c": "VERIFIED",
    # loops-crafted-1: a few are COUNTEREXAMPLE
    "loops-crafted-1/iftelse": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de20": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de31": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de32": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de41": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de42": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de51": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de52": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de61": "COUNTEREXAMPLE",
    "loops-crafted-1/in-de62": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv1": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv2": "COUNTEREXAMPLE",
    "loops-crafted-1/loopv3": "COUNTEREXAMPLE",
    "loops-crafted-1/mono-crafted_1": "VERIFIED",
    "loops-crafted-1/mono-crafted_3": "VERIFIED",
    "loops-crafted-1/mono-crafted_6": "VERIFIED",
    "loops-crafted-1/mono-crafted_7": "VERIFIED",
    "loops-crafted-1/mono-crafted_8": "VERIFIED",
    "loops-crafted-1/mono-crafted_9": "VERIFIED",
    "loops-crafted-1/mono-crafted_10": "VERIFIED",
    "loops-crafted-1/mono-crafted_11": "VERIFIED",
    "loops-crafted-1/mono-crafted_12": "VERIFIED",
    "loops-crafted-1/mono-crafted_13": "VERIFIED",
    "loops-crafted-1/mono-crafted_14": "VERIFIED",
    "loops-crafted-1/Mono1_1-1": "VERIFIED",
    "loops-crafted-1/Mono1_1-2": "VERIFIED",
    "loops-crafted-1/Mono3_1": "VERIFIED",
    "loops-crafted-1/Mono4_1": "VERIFIED",
    "loops-crafted-1/Mono5_1": "VERIFIED",
    "loops-crafted-1/Mono6_1": "VERIFIED",
    "loops-crafted-1/nested3-1": "VERIFIED",
    "loops-crafted-1/nested3-1_abstracted": "VERIFIED",
    "loops-crafted-1/nested3-2": "VERIFIED",
    "loops-crafted-1/nested3-2_abstracted": "VERIFIED",
    "loops-crafted-1/nested5-1": "VERIFIED",
    "loops-crafted-1/nested5-2": "VERIFIED",
    "loops-crafted-1/nested_delay_nd": "VERIFIED",
    "loops-crafted-1/nested_delay_notd2": "VERIFIED",
    "loops-crafted-1/net_reset": "VERIFIED",
    "loops-crafted-1/sumt2": "VERIFIED",
    "loops-crafted-1/sumt3": "VERIFIED",
    "loops-crafted-1/sumt4": "VERIFIED",
    "loops-crafted-1/sumt5": "VERIFIED",
    "loops-crafted-1/sumt6": "VERIFIED",
    "loops-crafted-1/sumt7": "VERIFIED",
    "loops-crafted-1/sumt8": "VERIFIED",
    "loops-crafted-1/sumt9": "VERIFIED",
    "loops-crafted-1/sum_by_3": "VERIFIED",
    "loops-crafted-1/sum_by_3_abstracted": "VERIFIED",
    "loops-crafted-1/sum_natnum": "VERIFIED",
    "loops-crafted-1/theatreSquare": "VERIFIED",
    "loops-crafted-1/vnew1": "VERIFIED",
    "loops-crafted-1/vnew2": "VERIFIED",
    "loops-crafted-1/watermelon": "VERIFIED",
}


def _collect_svcomp_sources() -> List[Path]:
    out: List[Path] = []
    for task in ("loop-simple", "loops-crafted-1", "loop-invariants"):
        d = PY_LOOPS_ROOT / task
        if not d.exists():
            continue
        for f in sorted(d.glob("*.py")):
            out.append(f)
    return out


def _cruxeval_python_subset(limit: int = 200) -> List[Path]:
    """Convert a slice of CRUXEval JSONL into .py files using a tiny
    adapter that wraps the JSON-defined `def f(...)` as-is, and rejects
    samples whose input/output literals contain non-int/bool/None values.
    """
    out: List[Path] = []
    target_dir = RESULT_ROOT / "_cruxeval_py"
    if not CRUXEVAL_JSONL.exists():
        if target_dir.exists():
            return sorted(target_dir.glob("*.py"))[:limit]
        return out
    target_dir.mkdir(parents=True, exist_ok=True)
    cnt = 0
    with CRUXEVAL_JSONL.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            code: str = rec.get("code", "")
            inp: str = rec.get("input", "")
            sid: str = rec.get("id", "?")
            # Parse the function's parameter list by introspecting AST
            try:
                import ast as _ast

                tree = _ast.parse(code)
            except Exception:
                continue
            params: list[str] = []
            for stmt in tree.body:
                if isinstance(stmt, _ast.FunctionDef):
                    for a in stmt.args.args:
                        params.append(getattr(a, "arg", None) or getattr(a, "name", None))
            if not params:
                continue
            # Build an example call dict: parse the input literal safely
            args_list = _parse_inputs(inp)
            if args_list is None:
                continue
            if len(args_list) != len(params):
                continue
            # Only keep if ALL args are int/bool/None
            if not all(isinstance(a, (int, bool, type(None))) for a in args_list):
                continue
            # Reject samples that contain nested FunctionDef or unsupported constructs
            has_nested = any(
                isinstance(n, _ast.FunctionDef) and n is not stmt
                for n in _ast.walk(tree)
            )
            if has_nested:
                continue
            out_path = target_dir / f"{sid}.py"
            # Find the first `def f(...)` line, drop everything before it, then
            # strip the leading `def f(...):` and re-emit it ourselves.
            lines = code.splitlines()
            start = 0
            for i, ln in enumerate(lines):
                if ln.lstrip().startswith("def "):
                    start = i
                    break
            else:
                start = 0
            body_lines = lines[start + 1 :]
            # Dedent: remove up to one leading 4-space level
            from textwrap import dedent as _dedent
            body_text = _dedent("\n".join(body_lines))
            wrapped = (
                f"def f({', '.join(params)}):\n"
                + "\n".join("    " + ln if ln.strip() else ln for ln in body_text.splitlines())
                + "\n"
            )
            out_path.write_text(wrapped, encoding="utf-8")
            out.append(out_path)
            cnt += 1
            if cnt >= limit:
                break
    return out


def _parse_inputs(s: str) -> list | None:
    """Parse a CRUXEval input literal. Only int/bool/None are accepted."""
    import ast as _ast

    try:
        node = _ast.parse(s, mode="eval").body
    except Exception:
        return None
    if isinstance(node, _ast.Tuple):
        out = []
        for elt in node.elts:
            v = _literal_value(elt)
            if v is _UNSET:
                return None
            out.append(v)
        return out
    v = _literal_value(node)
    if v is _UNSET:
        return None
    return [v]


_UNSET = object()


def _literal_value(node) -> object:
    """Return the literal value of a Constant, or _UNSET."""
    import ast as _ast

    if isinstance(node, _ast.Constant):
        return node.value
    if isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.USub):
        v = _literal_value(node.operand)
        if isinstance(v, int):
            return -v
        if isinstance(v, bool):
            return -v  # -True == -1
    return _UNSET


def _run_one(
    source_path: Path,
    baseline: str,
    *,
    use_llm: bool = False,
    llm_result: LLMResult | None = None,
    llm_error_message: str | None = None,
) -> ProgramResult:
    source = source_path.read_text(encoding="utf-8")
    program_id = source_path.stem
    try:
        relative = str(source_path.relative_to(DATASET_ROOT)).replace("\\", "/")
        program_id = relative.replace("/", "__").replace(".py", "")
    except ValueError:
        # File is outside the dataset root (e.g. CRUXEval temp dir)
        program_id = f"cruxeval__{source_path.stem}"
    if llm_result is not None or llm_error_message is not None:
        return run_pipeline(
            source,
            program_id=program_id,
            source_path=str(source_path),
            baseline=baseline,
            do_replay=True,
            suggested_claims=llm_result.claims if llm_result is not None else [],
            tokens_in=llm_result.tokens_in if llm_result is not None else 0,
            tokens_out=llm_result.tokens_out if llm_result is not None else 0,
            cot_trace=llm_result.cot_trace if llm_result is not None else "",
            llm_error_message=llm_error_message,
        )
    return run_pipeline(
        source,
        program_id=program_id,
        source_path=str(source_path),
        baseline=baseline,
        do_replay=True,
        use_llm=use_llm,
    )


def main(argv: List[str]) -> int:
    use_llm = "--use-llm" in argv[1:]
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    sources = _collect_svcomp_sources()
    print(f"Collected {len(sources)} SV-COMP Python sources")
    print("Claim generation:", "Gemini LLM" if use_llm else "deterministic source assertions")
    crux = _cruxeval_python_subset(limit=300)
    print(f"Collected {len(crux)} CRUXEval samples (int/bool/None only)")

    all_sources = sources + crux
    grand_total = len(all_sources) * len(BASELINES)
    done = 0
    t0 = time.perf_counter()

    for src_path in all_sources:
        generated: LLMResult | None = None
        generation_error: str | None = None
        if use_llm:
            try:
                generated = generate_cot_and_claims(
                    src_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                generation_error = str(exc)
                print(f"  [llm error] {src_path.name}: {generation_error}")
        for baseline in BASELINES:
            t1 = time.perf_counter()
            try:
                result = _run_one(
                    src_path,
                    baseline,
                    use_llm=False,
                    llm_result=generated,
                    llm_error_message=generation_error,
                )
            except Exception as e:
                print(f"  [error] {src_path.name} {baseline}: {e}")
                continue
            elapsed = time.perf_counter() - t1
            # Persist
            out_dir = RESULT_ROOT / baseline
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                rel = str(src_path.relative_to(DATASET_ROOT)).replace("\\", "/").replace("/", "__").replace(".py", ".json")
            except ValueError:
                rel = f"cruxeval__{src_path.stem}.json"
            out_path = out_dir / rel
            # pydantic model_dump
            out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            done += 1
            if done % 10 == 0 or done == grand_total:
                print(
                    f"  [{done}/{grand_total}] {src_path.name} {baseline} "
                    f"({elapsed:.2f}s) status: V={result.n_verified} "
                    f"CE={result.n_counterexample} UN={result.n_unreachable} "
                    f"UNS={result.n_unsupported} TE={result.n_translation_error} "
                    f"TO={result.n_unknown_timeout} rejected={result.rejected_by_subset}"
                )

    print(f"\nDone in {time.perf_counter() - t0:.1f}s ({done} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
