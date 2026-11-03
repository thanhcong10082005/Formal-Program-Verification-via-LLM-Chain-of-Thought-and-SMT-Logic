# g2_min2_01_b
# group: g2_conditionals | family: min2
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# precondition: none
# note: the claim is inverted for min

def f(n: int) -> int:
    if n < 5:
        x = n
    else:
        x = 5
    assert x >= 5
    return x
