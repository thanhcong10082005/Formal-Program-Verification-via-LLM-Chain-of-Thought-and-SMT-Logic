# g1_accum_02_b
# group: g1_arithmetic | family: accumulate
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_constant
# note: the claim forgets the added constant

def f(n: int) -> int:
    x = 0
    x = x + n
    x = x + 6
    assert x == n
    return x
