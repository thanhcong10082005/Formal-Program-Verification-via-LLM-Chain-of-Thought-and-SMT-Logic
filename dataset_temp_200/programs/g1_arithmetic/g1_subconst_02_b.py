# g1_subconst_02_b
# group: g1_arithmetic | family: sub_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_sign_error
# note: sign error in the claim

def f(n: int) -> int:
    x = n - 4
    assert x == n + 4
    return x
