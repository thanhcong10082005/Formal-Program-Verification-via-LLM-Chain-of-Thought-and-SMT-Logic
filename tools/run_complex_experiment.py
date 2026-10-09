"""
run_complex_experiment.py - Run AST_ANCHORED vs UNANCHORED on complex programs:
1. Dead Code / Unreachable Path (Vacuous Truth defense)
2. LLM Hallucination Stress-test (Anchor Gate defense)
3. Complex Loops & Multi-branching (MBPP Advanced with Gemini 3.5 Flash)
"""
import os
import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from astverifier.pipeline import run_pipeline
from astverifier.binding import Claim, bind_llm_claims
from astverifier.llm import generate_cot_and_claims, _get_api_key
from astverifier.classify import StatusEnum

GEMINI_KEY = _get_api_key()

OUT_DIR = PROJECT_ROOT / "results" / "complex_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# 1. TEST PROGRAMS DEFINITION
# -----------------------------------------------------------------------------
TEST_SUITE = [
    # --- Group A: Dead Code / Unreachable Path (Vacuous Truth) ---
    {
        "id": "A1_dead_loop_eq2",
        "category": "Dead Code / Vacuous Truth",
        "desc": "Loop with while 0 condition (dead loop from SV-COMP)",
        "code": (
            "def f(w, x, y, z):\n"
            "    while 0:\n"
            "        y += 1\n"
            "        z += 1\n"
            "    assert y == z\n"
            "    return\n"
        ),
        "use_llm": False,
        "inject_claims": []
    },
    {
        "id": "A2_dead_branch_loopv1",
        "category": "Dead Code / Vacuous Truth",
        "desc": "Loop with if 0 dead branch (SV-COMP loopv1)",
        "code": (
            "def f(i, j, n):\n"
            "    while i < n:\n"
            "        if 0:\n"
            "            i = i + 6\n"
            "        else:\n"
            "            i = i + 3\n"
            "    assert (i % 3) == 0\n"
            "    return\n"
        ),
        "use_llm": False,
        "inject_claims": []
    },
    {
        "id": "A3_unreachable_contradiction",
        "category": "Dead Code / Vacuous Truth",
        "desc": "Unreachable guard (x > 10 and x < 5) enclosing an assertion",
        "code": (
            "def check_guard(x):\n"
            "    res = 0\n"
            "    if x > 10 and x < 5:\n"
            "        res = 999\n"
            "        assert res == 999\n"
            "    return res\n"
        ),
        "use_llm": False,
        "inject_claims": []
    },

    # --- Group B: LLM Hallucination Stress-Test (Anchor Gate Defense) ---
    {
        "id": "B1_hallucination_line_overflow",
        "category": "LLM Hallucination",
        "desc": "LLM hallucinates line number 42 on a 6-line function",
        "code": (
            "def clamp(val, low, high):\n"
            "    if val < low:\n"
            "        return low\n"
            "    if val > high:\n"
            "        return high\n"
            "    return val\n"
        ),
        "use_llm": False,
        "inject_claims": [
            # Line 0 or 42 beyond file boundaries
            {"id": "halluc_line_0", "line": 0, "expression": "val >= low and val <= high"}
        ]
    },
    {
        "id": "B2_hallucination_syntax_error",
        "category": "LLM Hallucination",
        "desc": "LLM outputs non-Python / invalid math syntax (e.g. 2x == y)",
        "code": (
            "def double_val(x):\n"
            "    y = 2 * x\n"
            "    return y\n"
        ),
        "use_llm": False,
        "inject_claims": [
            {"id": "halluc_syntax", "line": 3, "expression": "2x == y"}
        ]
    },

    # --- Group C: Complex Multi-branch & Nested Loops (MBPP Advanced + Gemini) ---
    {
        "id": "C1_mbpp_448_cal_sum_perrin",
        "category": "Complex Arithmetic & Loops",
        "desc": "Perrin numbers sum (19 lines, 3 if branches, 4-variable state rotation in while loop)",
        "code": (
            "def cal_sum(n):\n"
            "    a = 3\n"
            "    b = 0\n"
            "    c = 2\n"
            "    if (n == 0):\n"
            "        return 3\n"
            "    if (n == 1):\n"
            "        return 3\n"
            "    if (n == 2):\n"
            "        return 5\n"
            "    sum = 5\n"
            "    while (n > 2):\n"
            "        d = a + b\n"
            "        sum = sum + d\n"
            "        a = b\n"
            "        b = c\n"
            "        c = d\n"
            "        n = n - 1\n"
            "    return sum\n"
        ),
        "use_llm": True
    },
    {
        "id": "C2_mbpp_711_product_equal",
        "category": "Complex Arithmetic & Loops",
        "desc": "Product equal at even/odd digits (16 lines, while loop with inner break, floor div & mod)",
        "code": (
            "def product_Equal(n):\n"
            "    if n < 10:\n"
            "        return False\n"
            "    prodOdd = 1\n"
            "    prodEven = 1\n"
            "    while n > 0:\n"
            "        digit = n % 10\n"
            "        prodOdd = prodOdd * digit\n"
            "        n = n // 10\n"
            "        if n == 0:\n"
            "            break\n"
            "        digit = n % 10\n"
            "        prodEven = prodEven * digit\n"
            "        n = n // 10\n"
            "    if prodOdd == prodEven:\n"
            "        return True\n"
            "    return False\n"
        ),
        "use_llm": True
    },
    {
        "id": "C3_mbpp_683_sum_square",
        "category": "Complex Arithmetic & Loops",
        "desc": "Sum of two squares representation (Nested while loops)",
        "code": (
            "def sum_Square(n):\n"
            "    i = 1\n"
            "    while i * i <= n:\n"
            "        j = 1\n"
            "        while (j * j <= n):\n"
            "            if (i * i + j * j == n):\n"
            "                return True\n"
            "            j = j + 1\n"
            "        i = i + 1\n"
            "    return False\n"
        ),
        "use_llm": True
    }
]

# -----------------------------------------------------------------------------
# 2. RUN EXPERIMENT
# -----------------------------------------------------------------------------
def run_benchmark():
    print("=" * 75)
    print(" STARTING EXPERIMENT: AST_ANCHORED vs UNANCHORED ON COMPLEX SUITE")
    print("=" * 75)

    all_results = []

    for item in TEST_SUITE:
        p_id = item["id"]
        category = item["category"]
        desc = item["desc"]
        code = item["code"]
        use_llm = item.get("use_llm", False)
        cache_file = OUT_DIR / f"{p_id}.json"

        print(f"\n[+] Processing: {p_id} ({category})")
        print(f"    Desc: {desc}")

        cot_trace = ""
        tokens_in = 0
        tokens_out = 0
        raw_claims_data = []

        if cache_file.exists():
            print("    [Cache Hit] Reusing existing results.")
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            all_results.append(cached_data)
            continue

        if use_llm:
            print("    Calling Gemini 3.5 Flash for CoT & Invariants...")
            t0 = time.time()
            llm_res = generate_cot_and_claims(code, api_key=GEMINI_KEY, model="gemini-3.5-flash")
            dur = time.time() - t0
            cot_trace = llm_res.cot_trace
            tokens_in = llm_res.tokens_in
            tokens_out = llm_res.tokens_out
            raw_claims_data = llm_res.claims
            print(f"    Gemini CoT completed in {dur:.2f}s (In: {tokens_in}, Out: {tokens_out} tokens)")
            print(f"    Suggested {len(raw_claims_data)} claims.")
            time.sleep(13)  # Respect free tier rate limit
        else:
            # Synthetic / injected claims
            raw_claims_data = item.get("inject_claims", [])

        # Run AST_ANCHORED
        # For injected claims, we pass them as if extracted / generated
        ast_result = run_pipeline_with_claims(code, raw_claims_data, baseline="AST_ANCHORED", cot_trace=cot_trace)
        un_result = run_pipeline_with_claims(code, raw_claims_data, baseline="UNANCHORED", cot_trace=cot_trace)

        res_entry = {
            "id": p_id,
            "category": category,
            "description": desc,
            "code": code,
            "cot_trace": cot_trace,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "ast_anchored": ast_result.model_dump(),
            "unanchored": un_result.model_dump()
        }

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(res_entry, f, indent=2)

        print(f"    AST_ANCHORED: V={ast_result.n_verified}, CE={ast_result.n_counterexample}, UN={ast_result.n_unreachable}, UNSUP={ast_result.n_unsupported + ast_result.n_translation_error}")
        print(f"    UNANCHORED  : V={un_result.n_verified}, CE={un_result.n_counterexample}, UN={un_result.n_unreachable}, UNSUP={un_result.n_unsupported + un_result.n_translation_error}")

        all_results.append(res_entry)

    with open(OUT_DIR / "all_complex_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print("\n[+] Done! All results saved to", OUT_DIR)


def run_pipeline_with_claims(code: str, raw_claims: list, baseline: str, cot_trace: str = ""):
    from astverifier.subset import check_and_normalize
    from astverifier.nodes import assign_ids
    from astverifier.executor import TrustedSymbolicExecutor
    from astverifier.binding import extract_asserts, bind_llm_claims, Claim
    from astverifier.classify import make_empty_program_result, StatusEnum, AnchorInfo, ClaimResult
    from astverifier.pipeline import _select_reachability, _process_claim, _roll_up
    import time

    start = time.perf_counter()
    sub = check_and_normalize(code)
    if not sub.accepted:
        res = make_empty_program_result(program_id="<test>", baseline=baseline, rejected=True, reason=sub.rejected_reason, source_text=code)
        res.elapsed_seconds = time.perf_counter() - start
        return res

    ids = assign_ids(sub.tree)
    executor = TrustedSymbolicExecutor(sub.tree, ids)
    transitions = executor.build_all_transitions()
    static_claims = extract_asserts(sub.tree, ids)
    from astverifier.llm import SuggestedClaim
    suggested_objs = []
    for c in raw_claims:
        if isinstance(c, SuggestedClaim):
            suggested_objs.append(c)
        elif isinstance(c, dict):
            suggested_objs.append(SuggestedClaim(line=c.get("line", 0), expression=c.get("expression", "")))
        else:
            suggested_objs.append(c)

    llm_claims = bind_llm_claims(sub.tree, ids, suggested_objs)

    all_claims = static_claims + llm_claims
    unsupported_claims = []

    if baseline == "AST_ANCHORED":
        claims = [c for c in all_claims if c.status == "ANCHORED"]
        unsupported_claims = [c for c in all_claims if c.status in ("UNSUPPORTED", "TRANSLATION_ERROR")]
    else:
        # UNANCHORED skips anchor gate
        claims = all_claims

    reach_list = _select_reachability(executor, claims)
    import z3
    if not reach_list:
        reachability = executor.build_reachability(None) if hasattr(executor, 'build_reachability') else None
    else:
        reachability = reach_list[0]

    claim_results = []
    for c in claims:
        cr = _process_claim(c, executor, reachability, baseline=baseline, source=code, do_replay=True)
        claim_results.append(cr)

    for c in unsupported_claims:
        st = StatusEnum.UNSUPPORTED if c.status == "UNSUPPORTED" else StatusEnum.TRANSLATION_ERROR
        claim_results.append(
            ClaimResult(
                claim_id=c.claim_id,
                claim_text=c.expr_source,
                status=st,
                anchor=AnchorInfo(node_ids=[], source_lines=[c.source_line], has_grounding=False),
                reason=f"Claim rejected by Anchor Gate ({c.status})",
            )
        )

    return _roll_up(
        claims=claim_results,
        program_id="<test>",
        source_path="<test>",
        source=code,
        baseline=baseline,
        elapsed=time.perf_counter() - start,
        transitions=transitions,
        cot_trace=cot_trace,
    )


if __name__ == "__main__":
    run_benchmark()
