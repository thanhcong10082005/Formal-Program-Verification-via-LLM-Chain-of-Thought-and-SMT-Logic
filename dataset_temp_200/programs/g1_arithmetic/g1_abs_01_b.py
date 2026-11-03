# g1_abs_01_b
# group: g1_arithmetic | family: absolute
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_strict_bound
# precondition: none
# note: the claim excludes the zero case

def f(n: int) -> int:
    x = abs(n - 3)
    assert x > 0
    return x
