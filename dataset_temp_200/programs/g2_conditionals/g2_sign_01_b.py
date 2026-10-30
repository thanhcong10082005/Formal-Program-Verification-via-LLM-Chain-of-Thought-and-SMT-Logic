# g2_sign_01_b
# group: g2_conditionals | family: sign
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_branch
# note: the claim ignores the negative branch

def f(n: int) -> int:
    if n == 0:
        x = 0
    elif n > 0:
        x = 1
    else:
        x = -1
    assert x >= 0
    return x
