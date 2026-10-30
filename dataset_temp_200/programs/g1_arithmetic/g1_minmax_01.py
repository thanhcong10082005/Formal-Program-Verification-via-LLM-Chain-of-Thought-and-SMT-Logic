# g1_minmax_01
# group: g1_arithmetic | family: min_max
# expected_verdict: VERIFIED | bug_type: none
# note: min(n, c) never exceeds c

def f(n: int) -> int:
    x = min(n, 4)
    assert x <= 4
    return x
