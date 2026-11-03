# g2_clamp_01_b
# group: g2_conditionals | family: clamp
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_bound
# precondition: none
# note: the claim uses the wrong clamp bound

def f(n: int) -> int:
    if n < 0:
        x = 0
    elif n > 10:
        x = 10
    else:
        x = n
    assert x <= 0
    return x
