"""
demo_gemini_verify.py - End-to-end Demonstration of Formal Verification using Gemini LLM.

Workflow:
1. Takes Python source code.
2. Sends to Google Gemini API using the selected baseline's own prompt.
   - AST_ANCHORED returns a CoT trace and line-anchored Python claims.
   - UNANCHORED returns direct SMT-LIB2 specifications and brief rationales.
3. AST_ANCHORED binds claims to syntax NodeIds (rejecting hallucinations).
4. Z3 checks the selected baseline's formal obligation.
5. AST_ANCHORED may replay counterexamples on CPython; direct results do not.
6. Prints detailed trace, verdicts, and token consumption statistics.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astverifier.pipeline import run_pipeline
from astverifier.llm import _get_api_key
from astverifier.metrics import PRICE_IN_PER_1K, PRICE_OUT_PER_1K


SAMPLE_CODE = '''def sum_loop(n: int):
    # Pre-condition: n can be any integer
    s = 0
    i = 0
    while i < n:
        s = s + i
        i = i + 1
    return s
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Python code with Gemini LLM + Z3 SMT")
    parser.add_argument("--file", "-f", type=str, help="Path to Python file to verify")
    parser.add_argument("--key", "-k", type=str, help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--model", "-m", type=str, default="gemini-3.5-flash", help="Gemini model (default: gemini-3.5-flash)")
    parser.add_argument("--baseline", "-b", choices=["AST_ANCHORED", "UNANCHORED"], default="AST_ANCHORED")
    args = parser.parse_args()

    api_key = _get_api_key(args.key)
    if not api_key:
        print("[!] GEMINI_API_KEY is not set.")
        print("    You can pass it with --key YOUR_KEY or set $env:GEMINI_API_KEY='YOUR_KEY'")
        print("    You can get a free key at: https://aistudio.google.com/apikey\n")
        entered = input("Enter your Gemini API key (press Enter to cancel): ").strip()
        if not entered:
            print("Canceled.")
            return 1
        api_key = entered

    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"[Error] File not found: {args.file}")
            return 1
        source = p.read_text(encoding="utf-8")
        prog_id = p.stem
    else:
        source = SAMPLE_CODE
        prog_id = "sample_sum_loop"

    print("=" * 70)
    print(" PROGRAM SOURCE CODE:")
    print("=" * 70)
    print(source.strip())
    print("=" * 70)
    if args.baseline == "AST_ANCHORED":
        call_label = "Chain-of-Thought & anchored claims"
    else:
        call_label = "Direct Formalization SMT-LIB2 specifications"
    print(f"[*] Calling Gemini ({args.model}) for {call_label}...")

    try:
        res = run_pipeline(
            source,
            program_id=prog_id,
            baseline=args.baseline,
            use_llm=True,
            gemini_api_key=api_key,
            gemini_model=args.model,
            do_replay=True,
        )
    except Exception as e:
        print(f"[Error] Pipeline execution failed: {e}")
        return 1

    print("\n" + "=" * 70)
    if args.baseline == "AST_ANCHORED":
        print(" [CoT] GEMINI CHAIN-OF-THOUGHT REASONING TRACE:")
        print("=" * 70)
        print(res.cot_trace or "(No CoT trace returned)")
    else:
        print(" [Direct Formalization] SMT-LIB2 specifications")
        print("=" * 70)
        print("The direct baseline uses brief per-specification rationales; no CoT is requested.")

    print("\n" + "=" * 70)
    label = "AST-Anchored + Z3" if args.baseline == "AST_ANCHORED" else "Direct Formalization + Z3"
    print(f" [SMT] FORMAL VERIFICATION RESULTS ({label}):")
    print("=" * 70)
    print(f"Total claims evaluated: {res.n_claims}")
    print(f"  * Verified (Always Holds):    {res.n_verified}")
    print(f"  * Counterexample (Violated):  {res.n_counterexample}")
    print(f"  * Unreachable (Dead code):    {res.n_unreachable}")
    print(f"  * Unsupported / Hallucinated: {res.n_unsupported}")
    print(f"  * Translation Error:          {res.n_translation_error}")
    print(f"  * Timeout / Unknown:          {res.n_unknown_timeout}")
    print("-" * 70)

    for idx, cr in enumerate(res.claim_results, start=1):
        print(f"[{idx}] Claim: '{cr.claim_text}' (Line: {cr.anchor.source_lines})")
        print(f"    Status: {cr.status.value}")
        if args.baseline == "AST_ANCHORED":
            anchor_label = cr.anchor.node_ids[0] if cr.anchor.node_ids else "None (Hallucinated)"
        else:
            anchor_label = "None (direct baseline)"
        print(f"    Anchor: {anchor_label}")
        print(f"    Reason: {cr.reason}")
        if cr.explanation:
            print(f"    Explanation: {cr.explanation}")
        if cr.counterexample:
            print(f"    Counterexample Model: {cr.counterexample.z3_model}")
            print(f"    CPython Replay Reproduced: {cr.counterexample.replay_verdict}")
        print()

    # Token & cost accounting
    cost = (res.total_tokens_in * PRICE_IN_PER_1K + res.total_tokens_out * PRICE_OUT_PER_1K) / 1000
    print("=" * 70)
    print(" [Metrics] TOKEN USAGE & PERFORMANCE:")
    print("=" * 70)
    print(f"Tokens in  : {res.total_tokens_in}")
    print(f"Tokens out : {res.total_tokens_out}")
    print(f"Est. cost  : ${cost:.6f} USD")
    print(f"Total time : {res.elapsed_seconds:.2f}s")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
