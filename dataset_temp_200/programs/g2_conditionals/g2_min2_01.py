# g2_min2_01
# group: g2_conditionals | family: min2
# expected_verdict: VERIFIED | bug_type: none
# note: min(n, c) is at most c

def f(n: int) -> int:
    if n < 5:
        x = n
    else:
        x = 5
    assert x <= 5
    return x
