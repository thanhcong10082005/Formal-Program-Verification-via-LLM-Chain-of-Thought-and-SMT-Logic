"""
run_complex_experiment.py - Run AST_ANCHORED vs UNANCHORED on complex programs:
1. Dead Code / Unreachable Path (Vacuous Truth defense)
2. LLM Hallucination Stress-test (Anchor Gate defense)
3. Complex Loops & Multi-branching (MBPP Advanced with Gemini 3.5 Flash)

The UNANCHORED branch uses the structured simple claim-list contract.
"""
import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from astverifier.pipeline import run_pipeline
from astverifier.llm import (
    _get_api_key,
    generate_anchored_formalization,
    generate_simple_claims,
)

GEMINI_KEY = _get_api_key()

OUT_DIR = PROJECT_ROOT / "results" / "complex_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_VERSION = 5

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
        "inject_simple_claims": []
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
        "inject_simple_claims": []
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
        "inject_simple_claims": []
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
        "inject_anchored_specs": [
            # Line 42 is beyond the source and must fail the anchor gate.
            {"line": 42, "formula": "(and (>= val low) (<= val high))", "variables": ["val", "low", "high"]}
        ],
        "inject_simple_claims": [
            {
                "expression": {
                    "kind": "op",
                    "op": "and",
                    "args": [
                        {"kind": "op", "op": "ge", "args": [
                            {"kind": "var", "name": "val"},
                            {"kind": "var", "name": "low"},
                        ]},
                        {"kind": "op", "op": "le", "args": [
                            {"kind": "var", "name": "val"},
                            {"kind": "var", "name": "high"},
                        ]},
                    ],
                },
                "rationale": "The clamp output lies between its bounds.",
            }
        ],
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
        "inject_anchored_specs": [
            {"line": 3, "formula": "(= (* x y) 0)", "variables": ["x", "y"]}
        ],
        "inject_simple_claims": [
            {
                "expression": {
                    "kind": "op",
                    "op": "eq",
                    "args": [
                        {"kind": "op", "op": "mul", "args": [
                            {"kind": "var", "name": "x"},
                            {"kind": "var", "name": "y"},
                        ]},
                        {"kind": "int", "value": 0},
                    ],
                },
                "rationale": "Intentional nonlinear simple-claim rejection.",
            }
        ],
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

        tokens_in = 0
        tokens_out = 0
        raw_anchored_constraints = []
        raw_anchored_specs = []
        raw_simple_claims = None

        if cache_file.exists():
            print("    [Cache Hit] Reusing existing results.")
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            if cached_data.get("generation_version") == CACHE_VERSION:
                all_results.append(cached_data)
                continue
            print("    [Stale Cache] Regenerating independent baseline inputs.")

        if use_llm:
            print("    Calling independent Gemini prompts for both baselines...")
            t0 = time.time()
            anchored_llm = generate_anchored_formalization(
                code,
                api_key=GEMINI_KEY,
                model="gemini-3.5-flash",
            )
            anchored_dur = time.time() - t0
            raw_anchored_constraints = anchored_llm.constraints
            raw_anchored_specs = anchored_llm.specifications
            print(
                f"    AST_ANCHORED Gemini completed in {anchored_dur:.2f}s "
                f"(In: {anchored_llm.tokens_in}, Out: {anchored_llm.tokens_out}; "
                f"{len(raw_anchored_constraints)} constraints, "
                f"{len(raw_anchored_specs)} anchored specifications)"
            )
            time.sleep(13)  # Respect free tier rate limit
            t0 = time.time()
            simple_llm = generate_simple_claims(
                code,
                api_key=GEMINI_KEY,
                model="gemini-3.5-flash",
            )
            simple_dur = time.time() - t0
            raw_simple_claims = simple_llm.claims
            print(
                f"    UNANCHORED simple claims completed in {simple_dur:.2f}s "
                f"(In: {simple_llm.tokens_in}, Out: {simple_llm.tokens_out}; "
                f"{len(raw_simple_claims)} claims)"
            )
        else:
            # Synthetic / injected structured simple claims
            raw_anchored_constraints = item.get("inject_anchored_constraints", [])
            raw_anchored_specs = item.get("inject_anchored_specs", [])
            raw_simple_claims = item.get("inject_simple_claims", [])
            anchored_llm = None
            simple_llm = None

        ast_result = run_pipeline_with_claims(
            code,
            baseline="AST_ANCHORED",
            anchored_constraints=raw_anchored_constraints,
            anchored_specs=raw_anchored_specs,
            tokens_in=anchored_llm.tokens_in if anchored_llm else 0,
            tokens_out=anchored_llm.tokens_out if anchored_llm else 0,
        )
        un_result = run_pipeline_with_claims(
            code,
            baseline="UNANCHORED",
            simple_claims=raw_simple_claims,
            tokens_in=simple_llm.tokens_in if simple_llm else 0,
            tokens_out=simple_llm.tokens_out if simple_llm else 0,
        )

        res_entry = {
            "generation_version": CACHE_VERSION,
            "id": p_id,
            "category": category,
            "description": desc,
            "code": code,
            "ast_generation": {
                "model": anchored_llm.model_name if anchored_llm else "deterministic",
                "tokens_in": anchored_llm.tokens_in if anchored_llm else 0,
                "tokens_out": anchored_llm.tokens_out if anchored_llm else 0,
                "constraint_count": len(raw_anchored_constraints or []),
                "specification_count": len(raw_anchored_specs or []),
            },
            "simple_generation": {
                "model": simple_llm.model_name if simple_llm else "deterministic",
                "tokens_in": simple_llm.tokens_in if simple_llm else 0,
                "tokens_out": simple_llm.tokens_out if simple_llm else 0,
                "claim_count": len(raw_simple_claims or []),
            },
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


def run_pipeline_with_claims(
    code: str,
    baseline: str,
    anchored_constraints: list | None = None,
    anchored_specs: list | None = None,
    simple_claims: list | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
):
    """Run one baseline through the public pipeline with its own input pool."""
    kwargs = {}
    if baseline == "AST_ANCHORED":
        kwargs.update(
            anchored_constraints=anchored_constraints,
            anchored_specifications=anchored_specs,
        )
    elif simple_claims is not None:
        kwargs.update(simple_claims=simple_claims)
    kwargs.update(
        program_id="<test>",
        source_path="<test>",
        baseline=baseline,
        do_replay=True,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    return run_pipeline(code, **kwargs)


if __name__ == "__main__":
    run_benchmark()
