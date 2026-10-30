# g1_abs_01
# group: g1_arithmetic | family: absolute
# expected_verdict: VERIFIED | bug_type: none
# note: an absolute value is non-negative

def f(n: int) -> int:
    x = abs(n - 3)
    assert x >= 0
    return x
