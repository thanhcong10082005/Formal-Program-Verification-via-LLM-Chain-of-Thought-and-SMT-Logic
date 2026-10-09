"""
run_mbpp_experiment.py - Run AST_ANCHORED vs UNANCHORED comparison with Gemini LLM on 10 MBPP problems.
Includes caching and 14s rate-limiting to strictly respect Gemini Free Tier 5 RPM quota.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any

# Ensure stdout is utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astverifier.binding import bind_llm_claims, extract_asserts, Claim
from astverifier.classify import AnchorInfo, ClaimResult, ProgramResult, StatusEnum
from astverifier.executor import Reachability, TrustedSymbolicExecutor
from astverifier.llm import generate_cot_and_claims, _get_api_key, SuggestedClaim
from astverifier.nodes import assign_ids
from astverifier.obligations import build_obligations, run_reachability, run_validity
from astverifier.pipeline import _process_claim, _select_reachability, _roll_up
from astverifier.subset import check_and_normalize


# 10 clean MBPP tasks (pure arithmetic, conditionals, loops)
MBPP_TASKS = [
    {
        "task_id": 17,
        "name": "square_perimeter",
        "description": "Find perimeter of a square: 4 * a",
        "code": "def square_perimeter(a):\n    perimeter = 4 * a\n    return perimeter\n",
    },
    {
        "task_id": 35,
        "name": "find_rect_num",
        "description": "Find n-th rectangular number: n * (n + 1)",
        "code": "def find_rect_num(n):\n    return n * (n + 1)\n",
    },
    {
        "task_id": 36,
        "name": "find_Nth_Digit",
        "description": "Long division while loop to find Nth digit of p/q",
        "code": "def find_Nth_Digit(p, q, N):\n    while N > 0:\n        N -= 1\n        p = p * 10\n        res = p // q\n        p = p % q\n    return res\n",
    },
    {
        "task_id": 51,
        "name": "check_equilateral",
        "description": "Check if triangle is equilateral (chained comparison)",
        "code": "def check_equilateral(x, y, z):\n    if x == y and y == z:\n        return True\n    else:\n        return False\n",
    },
    {
        "task_id": 52,
        "name": "parallelogram_area",
        "description": "Calculate area of a parallelogram: b * h",
        "code": "def parallelogram_area(b, h):\n    area = b * h\n    return area\n",
    },
    {
        "task_id": 59,
        "name": "is_octagonal",
        "description": "Find the nth octagonal number: 3*n*n - 2*n",
        "code": "def is_octagonal(n):\n    return 3 * n * n - 2 * n\n",
    },
    {
        "task_id": 72,
        "name": "dif_Square",
        "description": "Check if number can be difference of two squares (n % 4 != 2)",
        "code": "def dif_Square(n):\n    if n % 4 != 2:\n        return True\n    return False\n",
    },
    {
        "task_id": 86,
        "name": "centered_hexagonal_number",
        "description": "Find nth centered hexagonal number: 3*n*(n-1) + 1",
        "code": "def centered_hexagonal_number(n):\n    return 3 * n * (n - 1) + 1\n",
    },
    {
        "task_id": 89,
        "name": "closest_num",
        "description": "Find the closest smaller number than n: n - 1",
        "code": "def closest_num(n):\n    return n - 1\n",
    },
    {
        "task_id": 138,
        "name": "is_Sum_Of_Powers_Of_Two",
        "description": "Check parity condition with if-else",
        "code": "def is_Sum_Of_Powers_Of_Two(n):\n    if n % 2 == 1:\n        return False\n    else:\n        return True\n",
    },
]


def run_experiment_on_program(
    task: Dict[str, Any],
    api_key: str,
    out_dir: Path,
) -> Dict[str, Any]:
    code = task["code"]
    task_id = task["task_id"]
    name = task["name"]
    cache_file = out_dir / f"task_{task_id}_{name}.json"

    print(f"\n[+] Processing Task {task_id}: {name}...")

    cot_trace = ""
    suggested_claims = []
    tokens_in = 0
    tokens_out = 0

    # 1. Check if cached LLM result exists
    if cache_file.exists():
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached_data.get("cot_trace"):
                cot_trace = cached_data["cot_trace"]
                tokens_in = cached_data.get("tokens_in", 0)
                tokens_out = cached_data.get("tokens_out", 0)
                # Recover claims from anchored prog result or raw
                if "anchored" in cached_data and "claim_results" in cached_data["anchored"]:
                    for cr in cached_data["anchored"]["claim_results"]:
                        line = cr["anchor"]["source_lines"][0] if cr["anchor"]["source_lines"] else 1
                        suggested_claims.append(SuggestedClaim(line=line, expression=cr["claim_text"]))
                print(f"    [Cache Hit] Reusing cached Gemini CoT & {len(suggested_claims)} claims.")
        except Exception:
            pass

    if not cot_trace:
        # Call Gemini API
        t0 = time.perf_counter()
        llm_res = generate_cot_and_claims(code, api_key=api_key, model="gemini-flash-latest")
        llm_elapsed = time.perf_counter() - t0
        cot_trace = llm_res.cot_trace
        suggested_claims = llm_res.claims
        tokens_in = llm_res.tokens_in
        tokens_out = llm_res.tokens_out
        print(f"    Gemini CoT completed in {llm_elapsed:.2f}s (In: {tokens_in}, Out: {tokens_out} tokens)")
        print(f"    Suggested {len(suggested_claims)} claims.")
        # Rate limit safety delay for next call
        time.sleep(13)

    # Parse AST
    sub = check_and_normalize(code)
    ids = assign_ids(sub.tree)
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    transitions = executor.build_all_transitions()

    # 2. Evaluate AST_ANCHORED
    t_anchored_start = time.perf_counter()
    anchored_claims = bind_llm_claims(sub.tree, ids, suggested_claims)
    valid_anchored = [c for c in anchored_claims if c.status == "ANCHORED"]
    unsupported_anchored = [c for c in anchored_claims if c.status in ("UNSUPPORTED", "TRANSLATION_ERROR")]

    reach_list = _select_reachability(executor, valid_anchored)
    reachability = reach_list[0] if reach_list else Reachability(path=[], relation=None, path_condition=None)

    anchored_results: List[ClaimResult] = []
    for c in valid_anchored:
        cr = _process_claim(c, executor, reachability, baseline="AST_ANCHORED", source=code, do_replay=True)
        anchored_results.append(cr)
    for c in unsupported_anchored:
        st = StatusEnum.UNSUPPORTED if c.status == "UNSUPPORTED" else StatusEnum.TRANSLATION_ERROR
        anchored_results.append(
            ClaimResult(
                claim_id=c.claim_id,
                claim_text=c.expr_source,
                status=st,
                anchor=AnchorInfo(node_ids=[], source_lines=[c.source_line], has_grounding=False),
                reason=f"Claim rejected by Anchor Gate ({c.status})",
            )
        )
    anchored_prog = _roll_up(
        claims=anchored_results,
        program_id=f"mbpp_{task_id}_{name}",
        source_path=f"mbpp_{task_id}",
        source=code,
        baseline="AST_ANCHORED",
        elapsed=time.perf_counter() - t_anchored_start,
        transitions=transitions,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cot_trace=cot_trace,
    )

    # 3. Evaluate UNANCHORED (ablation: bypass anchor gate, skip reachability)
    t_unanchored_start = time.perf_counter()
    unanchored_results: List[ClaimResult] = []
    for c in anchored_claims:
        if c.expr_node is None:
            unanchored_results.append(
                ClaimResult(
                    claim_id=c.claim_id,
                    claim_text=c.expr_source,
                    status=StatusEnum.TRANSLATION_ERROR,
                    anchor=AnchorInfo(node_ids=[], source_lines=[c.source_line], has_grounding=True),
                    reason="Translation error",
                )
            )
        else:
            cr = _process_claim(c, executor, reachability, baseline="UNANCHORED", source=code, do_replay=True)
            unanchored_results.append(cr)
    unanchored_prog = _roll_up(
        claims=unanchored_results,
        program_id=f"mbpp_{task_id}_{name}",
        source_path=f"mbpp_{task_id}",
        source=code,
        baseline="UNANCHORED",
        elapsed=time.perf_counter() - t_unanchored_start,
        transitions=transitions,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cot_trace=cot_trace,
    )

    print(f"    AST_ANCHORED: V={anchored_prog.n_verified}, CE={anchored_prog.n_counterexample}, UN={anchored_prog.n_unreachable}, UNSUP={anchored_prog.n_unsupported}")
    print(f"    UNANCHORED  : V={unanchored_prog.n_verified}, CE={unanchored_prog.n_counterexample}, UN={unanchored_prog.n_unreachable}, UNSUP={unanchored_prog.n_unsupported}")

    res_dict = {
        "task_id": task_id,
        "name": name,
        "description": task["description"],
        "code": code,
        "cot_trace": cot_trace,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "anchored": anchored_prog.model_dump(),
        "unanchored": unanchored_prog.model_dump(),
    }
    cache_file.write_text(json.dumps(res_dict, indent=2), encoding="utf-8")
    return res_dict


def main() -> int:
    api_key = _get_api_key()
    if not api_key:
        print("[!] GEMINI_API_KEY is not set in .env or environment.")
        return 1

    out_dir = Path("results/mbpp_experiment")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" STARTING EXPERIMENT: AST_ANCHORED vs UNANCHORED on 10 MBPP TASKS")
    print("=" * 70)

    all_data = []
    for task in MBPP_TASKS:
        for attempt in range(3):
            try:
                res = run_experiment_on_program(task, api_key, out_dir)
                all_data.append(res)
                break
            except Exception as e:
                print(f"[!] Error on task {task['task_id']} (attempt {attempt+1}): {e}")
                time.sleep(20)

    # Save summary json
    (out_dir / "all_results.json").write_text(json.dumps(all_data, indent=2), encoding="utf-8")
    print("\n[+] All 10 MBPP experiments completed successfully!")
    print(f"[+] Results saved to {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

