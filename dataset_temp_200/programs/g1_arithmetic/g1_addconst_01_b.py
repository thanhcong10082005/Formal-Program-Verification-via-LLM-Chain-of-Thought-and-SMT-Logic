# g1_addconst_01_b
# group: g1_arithmetic | family: add_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: none
# note: off-by-one in the claim

def f(n: int) -> int:
    x = n + 3
    assert x == n + 3 + 1
    return x
