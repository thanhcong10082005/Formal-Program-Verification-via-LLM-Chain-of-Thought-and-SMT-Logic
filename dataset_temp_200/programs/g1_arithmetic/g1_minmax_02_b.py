# g1_minmax_02_b
# group: g1_arithmetic | family: min_max
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# precondition: none
# note: the claim is inverted for min

def f(n: int) -> int:
    x = min(n, 6)
    assert x >= 6
    return x
