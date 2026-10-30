# g2_clamp_02_b
# group: g2_conditionals | family: clamp
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_bound
# note: the claim uses the wrong clamp bound

def f(n: int) -> int:
    if n < 2:
        x = 2
    elif n > 8:
        x = 8
    else:
        x = n
    assert x <= 2
    return x
