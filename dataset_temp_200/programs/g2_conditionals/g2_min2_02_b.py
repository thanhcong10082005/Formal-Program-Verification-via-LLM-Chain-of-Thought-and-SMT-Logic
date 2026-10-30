# g2_min2_02_b
# group: g2_conditionals | family: min2
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# note: the claim is inverted for min

def f(n: int) -> int:
    if n < 7:
        x = n
    else:
        x = 7
    assert x >= 7
    return x
