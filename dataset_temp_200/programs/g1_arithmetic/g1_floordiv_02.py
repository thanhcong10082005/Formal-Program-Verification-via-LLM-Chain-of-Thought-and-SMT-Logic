# g1_floordiv_02
# group: g1_arithmetic | family: floor_div
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the floor quotient of a non-negative value is non-negative

def f(n: int) -> int:
    x = n // 4
    assert x >= 0
    return x
