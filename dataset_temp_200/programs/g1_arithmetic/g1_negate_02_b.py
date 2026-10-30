# g1_negate_02_b
# group: g1_arithmetic | family: negate
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_sign_error
# note: sign error in the claim

def f(n: int) -> int:
    x = -n
    x = x + 6
    assert x == n + 6
    return x
