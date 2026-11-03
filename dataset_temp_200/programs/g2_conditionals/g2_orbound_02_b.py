# g2_orbound_02_b
# group: g2_conditionals | family: or_bound
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_branch
# precondition: none
# note: the claim ignores the inside-range branch

def f(n: int) -> int:
    if n < 0 or n > 20:
        x = 1
    else:
        x = 0
    assert x == 1
    return x
