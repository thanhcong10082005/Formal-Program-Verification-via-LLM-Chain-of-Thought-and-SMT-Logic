# g1_floordiv_02_b
# group: g1_arithmetic | family: floor_div
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_strict_bound
# note: the claim excludes the zero quotient

def f(n: int) -> int:
    x = n // 4
    assert x > 0
    return x
