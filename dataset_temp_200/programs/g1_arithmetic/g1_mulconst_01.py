# g1_mulconst_01
# group: g1_arithmetic | family: mul_const
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of x = 2 * n

def f(n: int) -> int:
    x = n * 2
    assert x == 2 * n
    return x
