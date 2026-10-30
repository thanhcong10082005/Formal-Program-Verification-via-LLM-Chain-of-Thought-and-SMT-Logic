# g1_mulconst_02_b
# group: g1_arithmetic | family: mul_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_coefficient
# note: the claim forgets the factor

def f(n: int) -> int:
    x = n * 5
    assert x == 5 * n + 5
    return x
