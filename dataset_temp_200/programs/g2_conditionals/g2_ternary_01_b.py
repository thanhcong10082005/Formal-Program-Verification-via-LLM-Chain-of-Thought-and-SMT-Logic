# g2_ternary_01_b
# group: g2_conditionals | family: ternary
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_branch
# note: the claim ignores the negative branch

def f(n: int) -> int:
    x = 5 if n > 0 else -1
    assert x >= 0
    return x
