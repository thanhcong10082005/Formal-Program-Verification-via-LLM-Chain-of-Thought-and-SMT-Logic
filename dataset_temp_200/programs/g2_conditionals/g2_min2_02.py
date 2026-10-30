# g2_min2_02
# group: g2_conditionals | family: min2
# expected_verdict: VERIFIED | bug_type: none
# note: min(n, c) is at most c

def f(n: int) -> int:
    if n < 7:
        x = n
    else:
        x = 7
    assert x <= 7
    return x
