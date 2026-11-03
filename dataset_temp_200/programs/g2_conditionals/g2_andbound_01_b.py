# g2_andbound_01_b
# group: g2_conditionals | family: and_bound
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_branch
# precondition: none
# note: the claim ignores the out-of-range branch

def f(n: int) -> int:
    if n > 0 and n < 10:
        x = 1
    else:
        x = 0
    assert x == 1
    return x
