# g1_mulconst_02
# group: g1_arithmetic | family: mul_const
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: postcondition of x = 5 * n

def f(n: int) -> int:
    x = n * 5
    assert x == 5 * n
    return x
