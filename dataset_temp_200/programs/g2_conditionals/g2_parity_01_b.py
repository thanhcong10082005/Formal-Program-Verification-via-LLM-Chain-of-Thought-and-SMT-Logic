# g2_parity_01_b
# group: g2_conditionals | family: parity
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_branch
# note: the claim ignores the odd branch

def f(n: int) -> int:
    if n % 2 == 0:
        x = 1
    else:
        x = 0
    assert x == 1
    return x
