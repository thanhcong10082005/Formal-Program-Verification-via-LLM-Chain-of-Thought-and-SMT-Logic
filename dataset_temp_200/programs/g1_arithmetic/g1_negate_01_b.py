# g1_negate_01_b
# group: g1_arithmetic | family: negate
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_sign_error
# note: sign error in the claim

def f(n: int) -> int:
    x = -n
    x = x + 4
    assert x == n + 4
    return x
