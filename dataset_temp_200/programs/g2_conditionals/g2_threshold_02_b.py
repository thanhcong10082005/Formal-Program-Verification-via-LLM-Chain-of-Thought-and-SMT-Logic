# g2_threshold_02_b
# group: g2_conditionals | family: threshold
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# note: the claim is inverted for the threshold flag

def f(n: int) -> int:
    if n > 20:
        x = 1
    else:
        x = 0
    assert x <= 0
    return x
