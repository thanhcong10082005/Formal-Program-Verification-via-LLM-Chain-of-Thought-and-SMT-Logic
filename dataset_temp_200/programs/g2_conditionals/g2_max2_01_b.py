# g2_max2_01_b
# group: g2_conditionals | family: max2
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# note: the claim is inverted for max

def f(n: int) -> int:
    if n > 5:
        x = n
    else:
        x = 5
    assert x <= 5
    return x
