# g1_minmax_01_b
# group: g1_arithmetic | family: min_max
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# precondition: none
# note: the claim is inverted for min

def f(n: int) -> int:
    x = min(n, 4)
    assert x >= 4
    return x
