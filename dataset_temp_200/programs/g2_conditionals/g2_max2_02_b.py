# g2_max2_02_b
# group: g2_conditionals | family: max2
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# precondition: none
# note: the claim is inverted for max

def f(n: int) -> int:
    if n > 7:
        x = n
    else:
        x = 7
    assert x <= 7
    return x
