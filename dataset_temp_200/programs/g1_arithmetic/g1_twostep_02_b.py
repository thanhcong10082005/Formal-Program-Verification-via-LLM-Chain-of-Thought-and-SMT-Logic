# g1_twostep_02_b
# group: g1_arithmetic | family: two_step
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    x = n + 5
    x = x - 2
    assert x == n + 5 - 2 - 1
    return x
