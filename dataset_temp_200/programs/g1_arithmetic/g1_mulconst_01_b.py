# g1_mulconst_01_b
# group: g1_arithmetic | family: mul_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_coefficient
# note: the claim forgets the factor

def f(n: int) -> int:
    x = n * 2
    assert x == 2 * n + 2
    return x
