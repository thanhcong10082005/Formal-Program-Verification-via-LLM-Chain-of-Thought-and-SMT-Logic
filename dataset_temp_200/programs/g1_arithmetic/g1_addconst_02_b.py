# g1_addconst_02_b
# group: g1_arithmetic | family: add_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: none
# note: off-by-one in the claim

def f(n: int) -> int:
    x = n + 7
    assert x == n + 7 + 1
    return x
