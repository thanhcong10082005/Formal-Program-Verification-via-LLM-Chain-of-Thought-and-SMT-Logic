# g1_twostep_01_b
# group: g1_arithmetic | family: two_step
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: none
# note: off-by-one in the claim

def f(n: int) -> int:
    x = n + 3
    x = x - 1
    assert x == n + 3 - 1 - 1
    return x
