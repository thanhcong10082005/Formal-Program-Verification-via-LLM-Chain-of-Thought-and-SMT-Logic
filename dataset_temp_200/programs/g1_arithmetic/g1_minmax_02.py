# g1_minmax_02
# group: g1_arithmetic | family: min_max
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: min(n, c) never exceeds c

def f(n: int) -> int:
    x = min(n, 6)
    assert x <= 6
    return x
